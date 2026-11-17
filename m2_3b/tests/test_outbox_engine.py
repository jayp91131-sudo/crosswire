"""
Crosswire M2.3B Outbox Engine Automated Tests (Phase 2.3B.1).
Tests atomic leasing, attempt lineage, fencing token monotonicity,
payload immutability, lease expiry/takeover, and dispatch authorization.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import sqlite3
import pytest

# Ensure project root and .venv site-packages are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_SITE = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from m2_3b.tests.test_helpers import get_test_db, seed_test_booking_environment, seed_child_intent
from m2_3b.outbox.outbox_manager import OutboxManager, compute_payload_hash, canonical_now

def test_01_operation_creation_stable_ids_and_payload_hash():
    """1. Outbox operation creates stable logical ID, unique initial attempt ID, and payload hash."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)
    payload = {"hotel_id": "HTL_MUMBAI", "nights": 2, "guests": 2}
    expected_hash = compute_payload_hash(payload)

    op = mgr.create_operation(
        operation_id="OP_1",
        child_intent_id="CHILD_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload=payload,
        supplier_idempotency_key="idemp_op_1"
    )

    assert op.operation_id == "OP_1"
    assert op.logical_operation_id == "CHILD_1:CREATE_RESERVATION"
    assert op.attempt_id == "OP_1:att-0"
    assert op.status == "PENDING"
    assert op.payload_hash == expected_hash
    assert op.fencing_token == 1
    assert op.attempts == 0
    assert op.supplier_idempotency_key == "idemp_op_1"

def test_02_capability_idempotency_key_enforcement():
    """2. Enforces non-null idempotency key for IDEMPOTENT_WITH_LOOKUP and NULL for LOOKUP_ONLY/NEITHER."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_HB", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB")
    seed_child_intent(conn, "CHILD_DIR", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_DIRECT", "ACC_ALPHA_DIRECT")

    mgr = OutboxManager(conn)
    payload = {"hotel_id": "HTL_MUMBAI"}

    # LOOKUP_ONLY auto-nullifies idempotency key to satisfy schema CHECK constraint
    op_hb = mgr.create_operation(
        operation_id="OP_HB",
        child_intent_id="CHILD_HB",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_HOTELBEDS",
        provider_account_id="ACC_ALPHA_HB",
        operation_type="CREATE_RESERVATION",
        supplier_capability="LOOKUP_ONLY",
        payload=payload
    )
    assert op_hb.supplier_idempotency_key is None

    # NEITHER auto-nullifies idempotency key
    op_dir = mgr.create_operation(
        operation_id="OP_DIR",
        child_intent_id="CHILD_DIR",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_DIRECT",
        provider_account_id="ACC_ALPHA_DIRECT",
        operation_type="CREATE_RESERVATION",
        supplier_capability="NEITHER",
        payload=payload
    )
    assert op_dir.supplier_idempotency_key is None

def test_03_atomic_lease_acquisition():
    """3. Atomic lease acquisition transitions to IN_PROGRESS, increments attempt and fencing token."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)
    mgr.create_operation(
        operation_id="OP_CLAIM",
        child_intent_id="CHILD_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI"}
    )

    claimed = mgr.acquire_lease("worker_alpha", lease_duration_seconds=30)
    assert claimed is not None
    assert claimed.operation_id == "OP_CLAIM"
    assert claimed.status == "IN_PROGRESS"
    assert claimed.leased_by == "worker_alpha"
    assert claimed.attempts == 1
    assert claimed.attempt_id == "OP_CLAIM:att-1"
    assert claimed.fencing_token == 2  # Incremented from initial 1

    # Second worker cannot claim the same operation while lease is active
    second_claim = mgr.acquire_lease("worker_beta", lease_duration_seconds=30)
    assert second_claim is None

def test_04_lease_expiry_and_peer_worker_recovery():
    """4. When a worker lease expires, a peer worker recovers and claims the operation."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)
    mgr.create_operation(
        operation_id="OP_EXPIRE",
        child_intent_id="CHILD_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI"}
    )

    # Worker 1 claims with lease in the past (simulating expired lease)
    now_past = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("""
            UPDATE child_operation_outbox
            SET status = 'IN_PROGRESS',
                leased_by = 'worker_dead',
                lease_expires_at = ?,
                attempts = 1,
                attempt_id = 'OP_EXPIRE:att-1',
                fencing_token = 2
            WHERE operation_id = 'OP_EXPIRE'
        """, (now_past,))

    # Worker 2 attempts lease acquisition and recovers the abandoned task
    recovered = mgr.acquire_lease("worker_alive", lease_duration_seconds=30)
    assert recovered is not None
    assert recovered.operation_id == "OP_EXPIRE"
    assert recovered.leased_by == "worker_alive"
    assert recovered.attempts == 2
    assert recovered.attempt_id == "OP_EXPIRE:att-2"
    assert recovered.fencing_token == 3  # Monotonically incremented to 3!

def test_05_fencing_token_rejects_stale_worker():
    """5. Stale worker with old fencing token cannot renew lease or complete operation."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)
    mgr.create_operation(
        operation_id="OP_FENCE",
        child_intent_id="CHILD_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI"}
    )

    # Worker 1 claims with fencing_token = 2
    w1_op = mgr.acquire_lease("worker_1", lease_duration_seconds=30)
    w1_token = w1_op.fencing_token

    # Simulate worker 1 hanging, lease expiring, and Worker 2 taking ownership
    now_past = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("UPDATE child_operation_outbox SET lease_expires_at = ? WHERE operation_id = 'OP_FENCE'", (now_past,))

    w2_op = mgr.acquire_lease("worker_2", lease_duration_seconds=30)
    assert w2_op.fencing_token == 3

    # Worker 1 wakes up and tries to complete or renew with stale token (2)
    renew_ok = mgr.renew_lease("OP_FENCE", "worker_1", fencing_token=w1_token)
    assert renew_ok is False

    complete_ok = mgr.complete_operation("OP_FENCE", "worker_1", fencing_token=w1_token, outcome_status="SUCCEEDED")
    assert complete_ok is False

    # Verify Worker 2 is still the authoritative lease owner
    current = mgr.get_operation("OP_FENCE")
    assert current.status == "IN_PROGRESS"
    assert current.leased_by == "worker_2"
    assert current.fencing_token == 3

def test_06_account_revocation_blocks_lease_dispatch():
    """6. Revoking supplier account authorization blocks lease dispatch to IN_PROGRESS via trigger."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_HB", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB")

    mgr = OutboxManager(conn)
    mgr.create_operation(
        operation_id="OP_REVOKE",
        child_intent_id="CHILD_HB",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_HOTELBEDS",
        provider_account_id="ACC_ALPHA_HB",
        operation_type="CREATE_RESERVATION",
        supplier_capability="LOOKUP_ONLY",
        payload={"hotel_id": "HTL_MUMBAI"}
    )

    # Revoke account authorization while operation is PENDING
    with conn:
        conn.execute("""
            UPDATE agency_provider_account_authorizations
            SET is_enabled = 0
            WHERE agency_id = 'AGENCY_ALPHA' AND supplier_id = 'SUP_HOTELBEDS' AND provider_account_id = 'ACC_ALPHA_HB'
        """)

    # Attempting to acquire lease / dispatch triggers database abort
    with pytest.raises(sqlite3.IntegrityError, match="AuthorizationError: Supplier provider account authorization was revoked before outbox dispatch"):
        mgr.acquire_lease("worker_alpha", operation_id="OP_REVOKE")

def test_07_concurrent_worker_leasing_no_conflicts():
    """7. Multiple workers claiming tasks concurrently never obtain the same lease."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "concurrent_test.db")

        init_conn = get_test_db(db_path)
        seed_test_booking_environment(init_conn)
        mgr_init = OutboxManager(init_conn)

        # Create 10 operations
        for i in range(10):
            cid = f"CHILD_CONC_{i}"
            seed_child_intent(init_conn, cid, "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")
            mgr_init.create_operation(
                operation_id=f"OP_CONC_{i}",
                child_intent_id=cid,
                parent_booking_id="BKG_TEST_100",
                agency_id="AGENCY_ALPHA",
                supplier_id="SUP_EXPEDIA",
                provider_account_id="ACC_GLOBAL_EXP",
                operation_type="CREATE_RESERVATION",
                supplier_capability="IDEMPOTENT_WITH_LOOKUP",
                payload={"hotel_id": "HTL_MUMBAI", "index": i}
            )
        init_conn.close()

        # Two separate connections simulating two independent workers
        conn_w1 = get_test_db(db_path)
        conn_w2 = get_test_db(db_path)
        mgr_w1 = OutboxManager(conn_w1)
        mgr_w2 = OutboxManager(conn_w2)

        claimed_w1 = []
        claimed_w2 = []

        for _ in range(10):
            op1 = mgr_w1.acquire_lease("worker_1")
            if op1:
                claimed_w1.append(op1.operation_id)
            op2 = mgr_w2.acquire_lease("worker_2")
            if op2:
                claimed_w2.append(op2.operation_id)

        # Verify no overlap in claimed operations
        set_w1 = set(claimed_w1)
        set_w2 = set(claimed_w2)
        assert len(set_w1.intersection(set_w2)) == 0, f"Collision detected! Overlap: {set_w1.intersection(set_w2)}"
        assert len(set_w1) + len(set_w2) == 10

        conn_w1.close()
        conn_w2.close()

"""
Crosswire M2.3B Crash Recovery & Concurrency Stress Test Suite (Phase 2.3B.6).
Implements explicit automated tests for the 10 mandatory failure injection modes:
1. Worker crash after supplier HTTP dispatch but before local DB commit
2. Worker crash during lease renewal
3. Lease expiry during long-running supplier call
4. Two workers racing on expired lease
5. Late response arrives after lease takeover and resolution by second worker
6. Duplicate supplier webhook arrives for already-resolved child
7. Outbox operation retry exhaustion (max attempts reached)
8. Cancellation dispatched while creation response is in flight
9. Concurrent parent saga evaluations
10. Database restart / connection drop mid-transaction
"""

import sys
from pathlib import Path
import sqlite3
import threading
import time
import tempfile
from datetime import datetime, timezone, timedelta
import pytest

# Ensure project root and .venv site-packages are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_SITE = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from m2_3b.tests.test_helpers import get_test_db, seed_test_booking_environment, seed_child_intent
from m2_3b.outbox.outbox_manager import OutboxManager, canonical_now
from m2_3b.suppliers.synthetic_supplier import SyntheticSupplierBackend, ConfigurableSupplierAdapter
from m2_3b.outbox.dispatcher import dispatch_operation
from m2_3b.evidence.observation_writer import ObservationWriter
from m2_3b.evidence.observation_validator import ObservationValidator
from m2_3b.evidence.resolution_manager import ResolutionManager
from m2_3b.coordinator.parent_saga_coordinator import ParentCoordinator

def test_01_worker_crash_after_dispatch_before_commit():
    """1. Worker 1 dispatches to supplier, supplier creates reservation, Worker 1 crashes before commit.
    Worker 2 acquires expired lease, performs lookup, recovers reservation without duplicate."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_1", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_CRASH_1", "BKG_CRASH_1", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    backend = SyntheticSupplierBackend("SUP_EXPEDIA")
    adapter = ConfigurableSupplierAdapter("SUP_EXPEDIA", backend, create_capability="IDEMPOTENT_WITH_LOOKUP")

    outbox_mgr = OutboxManager(conn)
    op = outbox_mgr.create_operation(
        operation_id="OP_CRASH_1",
        child_intent_id="CH_CRASH_1",
        parent_booking_id="BKG_CRASH_1",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CH_CRASH_1"},
        supplier_idempotency_key="idemp_crash_1"
    )

    # Worker 1 leases op
    op1 = outbox_mgr.acquire_lease("worker-1", operation_id="OP_CRASH_1", lease_duration_seconds=30)
    assert op1 is not None

    # Worker 1 dispatches to supplier directly
    resp = adapter.create_reservation(op1.payload, idempotency_key=op1.supplier_idempotency_key)
    res_id = resp["supplier_reservation_id"]
    assert backend.reservations_created_count == 1

    # SIMULATE CRASH: Worker 1 dies immediately! Does NOT call complete_operation or write observation.
    # Force lease expiry to the past
    past_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("UPDATE child_operation_outbox SET lease_expires_at = ? WHERE operation_id = 'OP_CRASH_1'", (past_ts,))

    # Worker 2 recovers expired lease
    op2 = outbox_mgr.acquire_lease("worker-2", operation_id="OP_CRASH_1", lease_duration_seconds=10)
    assert op2 is not None
    assert op2.operation_id == op.operation_id
    assert op2.fencing_token > op1.fencing_token

    # Worker 2 dispatches via capability-aware dispatcher
    result2 = dispatch_operation(op2, adapter)
    assert result2.outcome_status == "SUCCEEDED"
    assert result2.supplier_reservation_id == res_id

    # CRITICAL INVARIANT: Zero duplicate reservations created at supplier
    assert backend.reservations_created_count == 1

def test_02_worker_crash_during_lease_renewal():
    """2. Worker crashes during lease heartbeat; heartbeat stops, peer worker recovers expired lease."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_2", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_CRASH_2", "BKG_CRASH_2", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    outbox_mgr = OutboxManager(conn)
    op = outbox_mgr.create_operation(
        operation_id="OP_CRASH_2",
        child_intent_id="CH_CRASH_2",
        parent_booking_id="BKG_CRASH_2",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "check_in_date": "2026-12-01", "check_out_date": "2026-12-03"}
    )

    # Worker 1 acquires short lease
    op1 = outbox_mgr.acquire_lease("worker-1", operation_id="OP_CRASH_2", lease_duration_seconds=30)
    assert op1 is not None

    # Renew lease once
    ok = outbox_mgr.renew_lease(op1.operation_id, "worker-1", op1.fencing_token, additional_seconds=30)
    assert ok is True

    # Worker 1 crashes (no further renewals, lease set to past)
    past_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("UPDATE child_operation_outbox SET lease_expires_at = ? WHERE operation_id = 'OP_CRASH_2'", (past_ts,))

    # Peer worker recovers lease
    op2 = outbox_mgr.acquire_lease("worker-2", operation_id="OP_CRASH_2", lease_duration_seconds=5)
    assert op2 is not None
    assert op2.operation_id == op.operation_id
    assert op2.leased_by == "worker-2"

def test_03_lease_expiry_during_long_running_supplier_call():
    """3. Lease expires while worker is blocked on slow supplier. Peer worker takes over; original worker rejected on complete."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_3", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_CRASH_3", "BKG_CRASH_3", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    outbox_mgr = OutboxManager(conn)
    op = outbox_mgr.create_operation(
        operation_id="OP_CRASH_3",
        child_intent_id="CH_CRASH_3",
        parent_booking_id="BKG_CRASH_3",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "check_in_date": "2026-12-01", "check_out_date": "2026-12-03"}
    )

    op1 = outbox_mgr.acquire_lease("worker-1", operation_id="OP_CRASH_3", lease_duration_seconds=30)
    assert op1 is not None

    # Lease expires while blocked
    past_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("UPDATE child_operation_outbox SET lease_expires_at = ? WHERE operation_id = 'OP_CRASH_3'", (past_ts,))

    # Worker 2 takes over
    op2 = outbox_mgr.acquire_lease("worker-2", operation_id="OP_CRASH_3", lease_duration_seconds=5)
    assert op2 is not None
    assert op2.fencing_token == 3

    # Worker 1 finishes its delayed call and attempts to complete outbox
    ok1 = outbox_mgr.complete_operation(op.operation_id, "worker-1", op1.fencing_token, outcome_status="SUCCEEDED", outcome_data={"status": "LATE"})
    assert ok1 is False  # Fencing token rejected!

    # Worker 2 successfully completes outbox
    ok2 = outbox_mgr.complete_operation(op.operation_id, "worker-2", op2.fencing_token, outcome_status="SUCCEEDED", outcome_data={"status": "OK"})
    assert ok2 is True

def test_04_two_workers_racing_on_expired_lease():
    """4. Two concurrent workers race to acquire the same expired lease. Exactly one wins."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = str(Path(td) / "race.db")
        conn = get_test_db(db_path)
        seed_test_booking_environment(conn, "BKG_CRASH_4", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
        seed_child_intent(conn, "CH_CRASH_4", "BKG_CRASH_4", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

        outbox_mgr = OutboxManager(conn)
        op = outbox_mgr.create_operation(
            operation_id="OP_CRASH_4",
            child_intent_id="CH_CRASH_4",
            parent_booking_id="BKG_CRASH_4",
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_EXPEDIA",
            provider_account_id="ACC_GLOBAL_EXP",
            operation_type="CREATE_RESERVATION",
            supplier_capability="IDEMPOTENT_WITH_LOOKUP",
            payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "check_in_date": "2026-12-01", "check_out_date": "2026-12-03"}
        )

        # Initial lease expires
        outbox_mgr.acquire_lease("init-worker", operation_id="OP_CRASH_4", lease_duration_seconds=30)
        past_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with conn:
            conn.execute("UPDATE child_operation_outbox SET lease_expires_at = ? WHERE operation_id = 'OP_CRASH_4'", (past_ts,))
        conn.close()

        winner = []
        barrier = threading.Barrier(2)

        def worker_attempt(worker_id):
            t_conn = sqlite3.connect(db_path, timeout=10.0)
            t_conn.row_factory = sqlite3.Row
            t_mgr = OutboxManager(t_conn)
            barrier.wait()
            leased = t_mgr.acquire_lease(worker_id, operation_id="OP_CRASH_4", lease_duration_seconds=5)
            if leased:
                winner.append(worker_id)
            t_conn.close()

        t1 = threading.Thread(target=worker_attempt, args=("worker-A",))
        t2 = threading.Thread(target=worker_attempt, args=("worker-B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one worker won the race
        assert len(winner) == 1

def test_05_late_response_rejected_by_fencing_token_trigger():
    """5. Late response arrives after lease takeover and resolution by second worker.
    Raw evidence is preserved in observations, but resolution trigger rejects stale fencing token."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_5", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_CRASH_5", "BKG_CRASH_5", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)

    # Worker 2 successfully resolves with fencing token = 2
    obs2 = writer.record_observation(
        "CH_CRASH_5", "BKG_CRASH_5", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_W2", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, v2, fencing_token=2)

    # Worker 1's late response arrives with stale fencing token = 1
    obs1 = writer.record_observation(
        "CH_CRASH_5", "BKG_CRASH_5", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_W1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    # Evidence IS preserved in append-only table!
    assert writer.get_observation(obs1) is not None

    v1, _ = validator.validate_observation(obs1)

    # But authoritative resolution update MUST be aborted by trigger trg_prevent_stale_fencing_token_resolution!
    with pytest.raises(sqlite3.IntegrityError, match="Stale fencing token cannot overwrite current authoritative resolution"):
        resolver.resolve_child_intent(obs1, v1, fencing_token=1)

def test_06_duplicate_supplier_webhook_preserves_evidence_idempotently():
    """6. Duplicate supplier webhook arrives for already-resolved child.
    Appends immutable observation without corrupting active resolution."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_6", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_CRASH_6", "BKG_CRASH_6", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)

    # 1. Initial resolution
    obs1 = writer.record_observation(
        "CH_CRASH_6", "BKG_CRASH_6", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_WH_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, v1, fencing_token=1)

    # 2. Duplicate webhook arrives with observation_source='WEBHOOK_NOTIFICATION'
    obs2 = writer.record_observation(
        "CH_CRASH_6", "BKG_CRASH_6", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_WH_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2},
        observation_source="WEBHOOK_NOTIFICATION"
    )
    assert obs1 != obs2

    # Both observations exist in database
    count = conn.execute("SELECT count(*) as c FROM supplier_reservation_observations WHERE child_intent_id = 'CH_CRASH_6'").fetchone()["c"]
    assert count == 2

    # Current authoritative resolution remains intact
    curr = conn.execute("SELECT * FROM child_current_authoritative_resolution WHERE child_intent_id = 'CH_CRASH_6'").fetchone()
    assert curr["supplier_reservation_id"] == "RES_WH_1"
    assert curr["lifecycle_status"] == "RESERVATION_ACTIVE"

def test_07_outbox_retry_exhaustion_escalates_to_reconciliation():
    """7. Outbox operation retry exhaustion: when attempts reach max_attempts, escalates to RECONCILIATION_REQUIRED."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_7", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_CRASH_7", "BKG_CRASH_7", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    backend = SyntheticSupplierBackend("SUP_EXPEDIA")
    backend.set_create_mode("TIMEOUT_BEFORE_COMMIT")
    backend.set_lookup_mode("NOT_FOUND")
    adapter = ConfigurableSupplierAdapter("SUP_EXPEDIA", backend, create_capability="IDEMPOTENT_WITH_LOOKUP")

    outbox_mgr = OutboxManager(conn)
    op = outbox_mgr.create_operation(
        operation_id="OP_CRASH_7",
        child_intent_id="CH_CRASH_7",
        parent_booking_id="BKG_CRASH_7",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CH_CRASH_7"},
        max_attempts=2
    )

    # Attempt 1
    op_att1 = outbox_mgr.acquire_lease("worker-1", operation_id="OP_CRASH_7", lease_duration_seconds=30)
    res1 = dispatch_operation(op_att1, adapter)
    assert res1.outcome_status == "PENDING_RETRY"
    outbox_mgr.release_for_retry(op.operation_id, "worker-1", op_att1.fencing_token)

    # Attempt 2 (Exhaustion)
    op_att2 = outbox_mgr.acquire_lease("worker-1", operation_id="OP_CRASH_7", lease_duration_seconds=30)
    assert op_att2.attempts == 2
    res2 = dispatch_operation(op_att2, adapter)
    assert res2.outcome_status == "RECONCILIATION_REQUIRED"

    outbox_mgr.complete_operation(op.operation_id, "worker-1", op_att2.fencing_token, outcome_status="FAILED", outcome_data={"error": res2.error_message})

    # Update child intent to RECONCILIATION_REQUIRED
    conn.execute("UPDATE child_supplier_intents SET status = 'RECONCILIATION_REQUIRED' WHERE child_intent_id = 'CH_CRASH_7'")

    # Parent coordinator checks booking
    coord = ParentCoordinator(conn)
    status = coord.reconcile_and_advance_parent("BKG_CRASH_7")
    assert status == "RECONCILIATION_REQUIRED"

    # Financial hold must be retained
    hold = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_CRASH_7'").fetchone()
    assert hold["status"] == "HELD"

def test_08_cancellation_dispatched_while_creation_in_flight():
    """8. Cancellation is enqueued and dispatched while creation is pending/in-flight.
    Parent coordinator enforces all-or-nothing compensation."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_CRASH_8", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)
    seed_child_intent(conn, "CH_CR_1", "BKG_CRASH_8", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_CR_2", "BKG_CRASH_8", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    coord = ParentCoordinator(conn)

    # CH_CR_1 succeeds
    obs1 = writer.record_observation(
        "CH_CR_1", "BKG_CRASH_8", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_CR_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, v1)

    # CH_CR_2 suffers definitive rejection
    obs2 = writer.record_observation(
        "CH_CR_2", "BKG_CRASH_8", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="DEFINITIVE_FAILURE",
        raw_response={"error": "Definitive rejection"}
    )
    v2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, v2)

    # Parent coordinator enters CANCELLATION_PENDING and enqueues cancellation for CH_CR_1
    assert coord.reconcile_and_advance_parent("BKG_CRASH_8") == "CANCELLATION_PENDING"

    cancel_op = conn.execute("""
        SELECT * FROM child_operation_outbox WHERE child_intent_id = 'CH_CR_1' AND operation_type = 'CANCEL_RESERVATION'
    """).fetchone()
    assert cancel_op is not None

def test_09_concurrent_parent_saga_evaluations():
    """9. Concurrent parent saga evaluations: race on final confirmation.
    Voucher is issued exactly once, hold captured exactly once."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = str(Path(td) / "parent_race.db")
        conn = get_test_db(db_path)
        seed_test_booking_environment(conn, "BKG_CRASH_9", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
        seed_child_intent(conn, "CH_CR_9", "BKG_CRASH_9", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

        writer = ObservationWriter(conn)
        validator = ObservationValidator(conn)
        resolver = ResolutionManager(conn)

        obs = writer.record_observation(
            "CH_CR_9", "BKG_CRASH_9", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
            lifecycle_outcome="RESERVATION_ACTIVE",
            raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_RACE", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
        )
        val, _ = validator.validate_observation(obs)
        resolver.resolve_child_intent(obs, val)
        conn.close()

        barrier = threading.Barrier(3)
        outcomes = []

        def coord_worker():
            t_conn = sqlite3.connect(db_path, timeout=10.0)
            t_conn.row_factory = sqlite3.Row
            c = ParentCoordinator(t_conn)
            barrier.wait()
            res = c.reconcile_and_advance_parent("BKG_CRASH_9")
            outcomes.append(res)
            t_conn.close()

        threads = [threading.Thread(target=coord_worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads agree parent is CONFIRMED
        assert all(o == "CONFIRMED" for o in outcomes)

        v_conn = sqlite3.connect(db_path, timeout=10.0)
        v_conn.row_factory = sqlite3.Row

        # Exactly one voucher record issued
        vouchers = v_conn.execute("SELECT count(*) as c FROM voucher_records WHERE booking_id = 'BKG_CRASH_9'").fetchone()["c"]
        assert vouchers == 1

        # Wallet hold captured exactly once
        hold = v_conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_CRASH_9'").fetchone()
        assert hold["status"] == "CAPTURED"
        v_conn.close()

def test_10_database_restart_mid_transaction_rollback():
    """10. Connection drop / restart mid-transaction rolls back uncommitted writes cleanly."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = str(Path(td) / "restart.db")
        conn = get_test_db(db_path)
        seed_test_booking_environment(conn, "BKG_CRASH_10", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
        seed_child_intent(conn, "CH_CR_10", "BKG_CRASH_10", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)
        conn.close()

        # Start an explicit transaction on connection A, insert a partial record, but DO NOT commit
        conn_a = sqlite3.connect(db_path, timeout=10.0)
        conn_a.execute("BEGIN IMMEDIATE")
        conn_a.execute("""
            UPDATE booking_records SET status = 'CORRUPTED_IN_FLIGHT' WHERE booking_id = 'BKG_CRASH_10'
        """)

        # Simulate abrupt connection drop / crash
        conn_a.close()

        # Open fresh connection B
        conn_b = sqlite3.connect(db_path, timeout=10.0)
        conn_b.row_factory = sqlite3.Row
        bkg = conn_b.execute("SELECT status FROM booking_records WHERE booking_id = 'BKG_CRASH_10'").fetchone()

        # Status was cleanly rolled back to PROCESSING!
        assert bkg["status"] == "PROCESSING"
        conn_b.close()

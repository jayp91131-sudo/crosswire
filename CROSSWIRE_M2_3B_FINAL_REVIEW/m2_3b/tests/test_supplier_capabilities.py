"""
Crosswire M2.3B Supplier Capability Automated Tests (Phase 2.3B.2).
Tests IDEMPOTENT_WITH_LOOKUP, LOOKUP_ONLY, and NEITHER capabilities,
proving prevention of duplicate bookings and safe escalation of ambiguous outcomes.
"""

import sys
from pathlib import Path
import pytest

# Ensure project root and .venv site-packages are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_SITE = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from m2_3b.tests.test_helpers import get_test_db, seed_test_booking_environment, seed_child_intent
from m2_3b.outbox.outbox_manager import OutboxManager
from m2_3b.suppliers.synthetic_supplier import SyntheticSupplierBackend, ConfigurableSupplierAdapter
from m2_3b.outbox.dispatcher import dispatch_operation

def test_01_idempotent_with_lookup_clean_success():
    """1. IDEMPOTENT_WITH_LOOKUP executes clean reservation on first attempt."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_EXP_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)
    op = mgr.create_operation(
        operation_id="OP_EXP_1",
        child_intent_id="CHILD_EXP_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CHILD_EXP_1"}
    )
    leased_op = mgr.acquire_lease("worker_1", operation_id="OP_EXP_1")

    backend = SyntheticSupplierBackend("SUP_EXPEDIA")
    adapter = ConfigurableSupplierAdapter("SUP_EXPEDIA", backend, create_capability="IDEMPOTENT_WITH_LOOKUP")

    res = dispatch_operation(leased_op, adapter)
    assert res.outcome_status == "SUCCEEDED"
    assert res.lifecycle_outcome == "RESERVATION_ACTIVE"
    assert res.supplier_reservation_id is not None
    assert backend.reservations_created_count == 1

def test_02_idempotent_with_lookup_safe_replay_prevents_duplicate():
    """2. IDEMPOTENT_WITH_LOOKUP reuses identical idempotency key on retry, preventing double booking."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_EXP_2", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)
    op = mgr.create_operation(
        operation_id="OP_EXP_2",
        child_intent_id="CHILD_EXP_2",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CHILD_EXP_2"},
        supplier_idempotency_key="idemp_key_fixed_123"
    )

    backend = SyntheticSupplierBackend("SUP_EXPEDIA")
    adapter = ConfigurableSupplierAdapter("SUP_EXPEDIA", backend, create_capability="IDEMPOTENT_WITH_LOOKUP")

    # Simulate timeout AFTER commit on attempt 1
    backend.set_create_mode("TIMEOUT_AFTER_COMMIT")
    leased_op_1 = mgr.acquire_lease("worker_1", operation_id="OP_EXP_2")

    res1 = dispatch_operation(leased_op_1, adapter)
    # Because backend committed the reservation, lookup recovered it or signaled retryable
    # Notice backend created 1 reservation
    assert backend.reservations_created_count == 1

    # Worker 1 releases for retry
    mgr.release_for_retry("OP_EXP_2", "worker_1", leased_op_1.fencing_token)

    # Worker 2 acquires retry attempt (with identical supplier_idempotency_key)
    backend.set_create_mode("SUCCESS")
    leased_op_2 = mgr.acquire_lease("worker_2", operation_id="OP_EXP_2")
    assert leased_op_2.supplier_idempotency_key == "idemp_key_fixed_123"

    res2 = dispatch_operation(leased_op_2, adapter)
    assert res2.outcome_status == "SUCCEEDED"
    assert res2.lifecycle_outcome == "RESERVATION_ACTIVE"
    # Idempotent replay detected -> zero additional reservation created!
    assert res2.raw_payload.get("is_idempotent_replay") is True
    assert backend.reservations_created_count == 1  # EXACTLY 1, NO DOUBLE BOOKING!

def test_03_lookup_only_never_blind_retries():
    """3. LOOKUP_ONLY on ambiguous drop uses lookup; NEVER blindly executes create_reservation."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_HB_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB")

    mgr = OutboxManager(conn)
    op = mgr.create_operation(
        operation_id="OP_HB_1",
        child_intent_id="CHILD_HB_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_HOTELBEDS",
        provider_account_id="ACC_ALPHA_HB",
        operation_type="CREATE_RESERVATION",
        supplier_capability="LOOKUP_ONLY",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CHILD_HB_1"}
    )
    leased_op = mgr.acquire_lease("worker_1", operation_id="OP_HB_1")
    assert leased_op.supplier_idempotency_key is None

    backend = SyntheticSupplierBackend("SUP_HOTELBEDS")
    adapter = ConfigurableSupplierAdapter("SUP_HOTELBEDS", backend, create_capability="LOOKUP_ONLY")

    # Simulate timeout AFTER commit (ambiguous drop)
    backend.set_create_mode("TIMEOUT_AFTER_COMMIT")

    res = dispatch_operation(leased_op, adapter)

    # Dispatcher caught timeout and executed lookup instead of blind retry
    assert res.outcome_status == "SUCCEEDED"
    assert res.lifecycle_outcome == "RESERVATION_ACTIVE"
    assert res.supplier_reservation_id is not None
    # Proof: create was called once, lookup was called once, NO duplicate created!
    create_calls = [c for c in backend.call_log if c["action"] == "CREATE"]
    lookup_calls = [c for c in backend.call_log if c["action"] == "LOOKUP"]
    assert len(create_calls) == 1
    assert len(lookup_calls) == 1
    assert backend.reservations_created_count == 1

def test_04_lookup_only_unresolved_escalates_to_reconciliation():
    """4. LOOKUP_ONLY escalates to RECONCILIATION_REQUIRED when lookup cannot resolve state."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_HB_2", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB")

    mgr = OutboxManager(conn)
    op = mgr.create_operation(
        operation_id="OP_HB_2",
        child_intent_id="CHILD_HB_2",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_HOTELBEDS",
        provider_account_id="ACC_ALPHA_HB",
        operation_type="CREATE_RESERVATION",
        supplier_capability="LOOKUP_ONLY",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CHILD_HB_2"}
    )
    leased_op = mgr.acquire_lease("worker_1", operation_id="OP_HB_2")

    backend = SyntheticSupplierBackend("SUP_HOTELBEDS")
    adapter = ConfigurableSupplierAdapter("SUP_HOTELBEDS", backend, create_capability="LOOKUP_ONLY")

    # Simulate ambiguous drop on CREATE and timeout on LOOKUP
    backend.set_create_mode("TIMEOUT_AFTER_COMMIT")
    backend.set_lookup_mode("TIMEOUT")

    res = dispatch_operation(leased_op, adapter)

    # Escalates safely without blind retry
    assert res.outcome_status == "RECONCILIATION_REQUIRED"
    assert res.lifecycle_outcome == "AMBIGUOUS"

def test_05_neither_capability_immediate_reconciliation():
    """5. NEITHER capability immediately escalates to RECONCILIATION_REQUIRED on ambiguous drops."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_DIR_1", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_DIRECT", "ACC_ALPHA_DIRECT")

    mgr = OutboxManager(conn)
    op = mgr.create_operation(
        operation_id="OP_DIR_1",
        child_intent_id="CHILD_DIR_1",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_DIRECT",
        provider_account_id="ACC_ALPHA_DIRECT",
        operation_type="CREATE_RESERVATION",
        supplier_capability="NEITHER",
        payload={"hotel_id": "HTL_MUMBAI", "occupancy": 2, "child_intent_id": "CHILD_DIR_1"}
    )
    leased_op = mgr.acquire_lease("worker_1", operation_id="OP_DIR_1")

    backend = SyntheticSupplierBackend("SUP_DIRECT")
    adapter = ConfigurableSupplierAdapter("SUP_DIRECT", backend, create_capability="NEITHER")

    # Simulate ambiguous drop
    backend.set_create_mode("TIMEOUT_AFTER_COMMIT")

    res = dispatch_operation(leased_op, adapter)

    # NEITHER cannot retry, cannot lookup -> immediate reconciliation required!
    assert res.outcome_status == "RECONCILIATION_REQUIRED"
    assert res.lifecycle_outcome == "AMBIGUOUS"
    # Verify lookup was never attempted
    lookup_calls = [c for c in backend.call_log if c["action"] == "LOOKUP"]
    assert len(lookup_calls) == 0

def test_06_independent_create_and_cancel_capabilities():
    """6. CREATE and CANCEL capabilities are independently configured and executed."""
    conn = get_test_db()
    seed_test_booking_environment(conn)
    seed_child_intent(conn, "CHILD_INDEP", "BKG_TEST_100", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP")

    mgr = OutboxManager(conn)

    # Adapter configured with IDEMPOTENT_WITH_LOOKUP for CREATE, but LOOKUP_ONLY for CANCEL
    backend = SyntheticSupplierBackend("SUP_EXPEDIA")
    adapter = ConfigurableSupplierAdapter(
        "SUP_EXPEDIA",
        backend,
        create_capability="IDEMPOTENT_WITH_LOOKUP",
        cancel_capability="LOOKUP_ONLY"
    )

    # 1. Create reservation successfully
    op_create = mgr.create_operation(
        operation_id="OP_INDEP_CREATE",
        child_intent_id="CHILD_INDEP",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CREATE_RESERVATION",
        supplier_capability="IDEMPOTENT_WITH_LOOKUP",
        payload={"hotel_id": "HTL_MUMBAI", "child_intent_id": "CHILD_INDEP"}
    )
    leased_create = mgr.acquire_lease("worker_1", operation_id="OP_INDEP_CREATE")
    res_create = dispatch_operation(leased_create, adapter)
    assert res_create.outcome_status == "SUCCEEDED"
    res_id = res_create.supplier_reservation_id

    # 2. Cancel reservation with LOOKUP_ONLY capability
    op_cancel = mgr.create_operation(
        operation_id="OP_INDEP_CANCEL",
        child_intent_id="CHILD_INDEP",
        parent_booking_id="BKG_TEST_100",
        agency_id="AGENCY_ALPHA",
        supplier_id="SUP_EXPEDIA",
        provider_account_id="ACC_GLOBAL_EXP",
        operation_type="CANCEL_RESERVATION",
        supplier_capability="LOOKUP_ONLY",
        payload={"supplier_reservation_id": res_id}
    )
    leased_cancel = mgr.acquire_lease("worker_1", operation_id="OP_INDEP_CANCEL")
    assert leased_cancel.supplier_idempotency_key is None

    # Simulate timeout on cancel after commit
    backend.set_cancel_mode("TIMEOUT_AFTER_COMMIT")
    res_cancel = dispatch_operation(leased_cancel, adapter)

    # Cancellation confirmed via lookup
    assert res_cancel.outcome_status == "SUCCEEDED"
    assert res_cancel.lifecycle_outcome == "RESERVATION_CANCELLED"
    assert backend.reservations[res_id]["status"] == "CANCELLED"

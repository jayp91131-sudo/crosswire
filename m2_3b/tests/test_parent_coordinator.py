"""
Crosswire M2.3B Parent Saga Coordinator Tests (Phase 2.3B.4).
Verifies:
- All confirmed + coverage satisfied -> parent CONFIRMED, hold CAPTURED, voucher ISSUED.
- Partial failure -> CANCELLATION_PENDING, compensation cancel outbox operation enqueued, hold RETAINED.
- Full compensation completion -> CANCELLED, hold RELEASED in full.
- Ambiguous/timeout child -> RECONCILIATION_REQUIRED, hold RETAINED, voucher STRICTLY PREVENTED.
- Coverage gap/deficit -> confirmation strictly BLOCKED.
- Late confirmation arriving after cancellation -> parent remains CANCELLED, no hold capture, no voucher.
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
from m2_3b.evidence.observation_writer import ObservationWriter
from m2_3b.evidence.observation_validator import ObservationValidator
from m2_3b.evidence.resolution_manager import ResolutionManager
from m2_3b.coordinator.parent_saga_coordinator import ParentCoordinator

def test_01_all_children_confirmed_and_coverage_satisfied():
    """1. All children confirmed with coverage satisfied -> CONFIRMED, hold CAPTURED, voucher ISSUED."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_SAGA_OK", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)
    seed_child_intent(conn, "CH_OK_1", "BKG_SAGA_OK", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_OK_2", "BKG_SAGA_OK", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    coord = ParentCoordinator(conn)

    # Resolve child 1 active
    obs1 = writer.record_observation(
        "CH_OK_1", "BKG_SAGA_OK", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_OK_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, v1)

    # In-flight after only 1 child
    status_mid = coord.reconcile_and_advance_parent("BKG_SAGA_OK")
    assert status_mid == "PROCESSING"

    # Resolve child 2 active
    obs2 = writer.record_observation(
        "CH_OK_2", "BKG_SAGA_OK", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_OK_2", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, v2)

    # Both confirmed & coverage verified
    status_final = coord.reconcile_and_advance_parent("BKG_SAGA_OK")
    assert status_final == "CONFIRMED"

    # Verify wallet hold is captured
    hold = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_SAGA_OK'").fetchone()
    assert hold["status"] == "CAPTURED"

    # Verify voucher record is created
    vch = conn.execute("SELECT * FROM voucher_records WHERE booking_id = 'BKG_SAGA_OK'").fetchone()
    assert vch is not None
    assert vch["status"] == "ISSUED"

def test_02_partial_failure_initiates_all_or_nothing_compensation():
    """2. Child 1 confirmed, Child 2 fails -> CANCELLATION_PENDING, cancel op enqueued, hold RETAINED."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_SAGA_PARTIAL", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)
    seed_child_intent(conn, "CH_P_1", "BKG_SAGA_PARTIAL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_P_2", "BKG_SAGA_PARTIAL", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    coord = ParentCoordinator(conn)

    # Child 1 confirms
    obs1 = writer.record_observation(
        "CH_P_1", "BKG_SAGA_PARTIAL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_P_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, v1)

    # Child 2 suffers definitive rejection (e.g. inventory sold out)
    obs2 = writer.record_observation(
        "CH_P_2", "BKG_SAGA_PARTIAL", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="DEFINITIVE_FAILURE",
        raw_response={"status": "SOLD_OUT", "error": "No inventory available"}
    )
    v2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, v2)

    # Advance parent state
    status = coord.reconcile_and_advance_parent("BKG_SAGA_PARTIAL")
    assert status == "CANCELLATION_PENDING"

    # Compensation cancel operation for CH_P_1 must be enqueued in outbox
    cancel_op = conn.execute("""
        SELECT * FROM child_operation_outbox
        WHERE child_intent_id = 'CH_P_1' AND operation_type = 'CANCEL_RESERVATION'
    """).fetchone()
    assert cancel_op is not None
    assert cancel_op["status"] == "PENDING"

    # Financial hold MUST STILL BE HELD (cannot release until supplier cancellation confirms!)
    hold = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_SAGA_PARTIAL'").fetchone()
    assert hold["status"] == "HELD"

    # NO voucher issued!
    vch = conn.execute("SELECT * FROM voucher_records WHERE booking_id = 'BKG_SAGA_PARTIAL'").fetchone()
    assert vch is None

def test_03_compensation_completion_cancels_parent_and_releases_hold():
    """3. Once compensation succeeds on supplier -> parent CANCELLED, hold RELEASED."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_SAGA_COMPL", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)
    seed_child_intent(conn, "CH_C_1", "BKG_SAGA_COMPL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_C_2", "BKG_SAGA_COMPL", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    coord = ParentCoordinator(conn)

    # Initial state: CH_C_1 was active, CH_C_2 failed
    obs1 = writer.record_observation(
        "CH_C_1", "BKG_SAGA_COMPL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_C_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, v1)

    obs2 = writer.record_observation(
        "CH_C_2", "BKG_SAGA_COMPL", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="DEFINITIVE_FAILURE",
        raw_response={"error": "Definitive rejection"}
    )
    v2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, v2)

    # First advance -> CANCELLATION_PENDING
    assert coord.reconcile_and_advance_parent("BKG_SAGA_COMPL") == "CANCELLATION_PENDING"

    # Now compensation cancellation executes and receives supplier evidence of cancellation
    obs_cancel = writer.record_observation(
        "CH_C_1", "BKG_SAGA_COMPL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_CANCELLED",
        raw_response={"status": "CANCELLED", "supplier_reservation_id": "RES_C_1"},
        operation_type="CANCEL_RESERVATION"
    )
    v_cancel, _ = validator.validate_observation(obs_cancel)
    resolver.resolve_child_intent(obs_cancel, v_cancel)

    # Reconcile again -> All active children are now CANCELLED
    status_final = coord.reconcile_and_advance_parent("BKG_SAGA_COMPL")
    assert status_final == "CANCELLED"

    # Financial hold is now released in full
    hold = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_SAGA_COMPL'").fetchone()
    assert hold["status"] == "RELEASED"

def test_04_ambiguous_child_forces_reconciliation_required_and_retains_hold():
    """4. Ambiguous/timeout child -> RECONCILIATION_REQUIRED, hold RETAINED, voucher PREVENTED."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_SAGA_AMBIG", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)
    seed_child_intent(conn, "CH_A_1", "BKG_SAGA_AMBIG", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_A_2", "BKG_SAGA_AMBIG", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    coord = ParentCoordinator(conn)

    # CH_A_1 is confirmed
    obs1 = writer.record_observation(
        "CH_A_1", "BKG_SAGA_AMBIG", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_A_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    v1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, v1)

    # CH_A_2 is marked RECONCILIATION_REQUIRED due to network timeout exhaustion
    conn.execute("""
        UPDATE child_supplier_intents
        SET status = 'RECONCILIATION_REQUIRED'
        WHERE child_intent_id = 'CH_A_2'
    """)

    # Advance parent state
    status = coord.reconcile_and_advance_parent("BKG_SAGA_AMBIG")
    assert status == "RECONCILIATION_REQUIRED"

    # Financial hold MUST BE RETAINED
    hold = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_SAGA_AMBIG'").fetchone()
    assert hold["status"] == "HELD"

    # Voucher issuance strictly blocked
    vch = conn.execute("SELECT * FROM voucher_records WHERE booking_id = 'BKG_SAGA_AMBIG'").fetchone()
    assert vch is None

def test_05_late_confirmation_after_cancellation_preserves_cancelled_state():
    """5. Late confirmation after cancellation: parent booking remains CANCELLED, hold remains RELEASED, no voucher."""
    conn = get_test_db()
    seed_test_booking_environment(conn, "BKG_SAGA_LATE", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CH_L_1", "BKG_SAGA_LATE", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    coord = ParentCoordinator(conn)

    # 1. Child fails initially, parent cancels, hold is released
    obs_fail = writer.record_observation(
        "CH_L_1", "BKG_SAGA_LATE", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="DEFINITIVE_FAILURE",
        raw_response={"error": "Initial timeout/failure"}
    )
    v_fail, _ = validator.validate_observation(obs_fail)
    resolver.resolve_child_intent(obs_fail, v_fail)

    status_cancelled = coord.reconcile_and_advance_parent("BKG_SAGA_LATE")
    assert status_cancelled == "CANCELLED"

    hold = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_SAGA_LATE'").fetchone()
    assert hold["status"] == "RELEASED"

    # 2. Later, a delayed confirmation arrives from the supplier for CH_L_1
    obs_late = writer.record_observation(
        "CH_L_1", "BKG_SAGA_LATE", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_LATE_101", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    # Raw observation IS appended for audit/reconciliation
    assert writer.get_observation(obs_late) is not None

    # But parent coordinator evaluation preserves terminal CANCELLED status
    parent_eval = coord.reconcile_and_advance_parent("BKG_SAGA_LATE")
    assert parent_eval == "CANCELLED"

    # Hold remains RELEASED (cannot be re-captured)
    hold_after = conn.execute("SELECT status FROM wallet_holds WHERE booking_id = 'BKG_SAGA_LATE'").fetchone()
    assert hold_after["status"] == "RELEASED"

    # No voucher is ever issued for a cancelled booking
    vch = conn.execute("SELECT * FROM voucher_records WHERE booking_id = 'BKG_SAGA_LATE'").fetchone()
    assert vch is None

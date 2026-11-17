"""
Crosswire M2.3B Coverage and Occupancy Oracle Automated Tests (Phase 2.3B.4).
Validates continuous stay coverage, property match, room slots, intra-slot non-overlap, and guest occupancy
strictly against actual supplier reservation evidence (not merely child intent status flags).
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
from m2_3b.evidence.coverage_oracle import CoverageOracle

def test_01_full_coverage_and_occupancy_satisfied():
    """1. Full stay coverage and occupancy verified across 2 child rooms."""
    conn = get_test_db()
    # Parent requires HTL_MUMBAI, 2026-12-01 to 2026-12-03 (2 nights), 2 rooms, 4 adults
    seed_test_booking_environment(conn, "BKG_COV_1", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)

    seed_child_intent(conn, "CHILD_R1", "BKG_COV_1", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CHILD_R2", "BKG_COV_1", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    oracle = CoverageOracle(conn)

    # Room 1 observation & resolution
    obs1 = writer.record_observation(
        "CHILD_R1", "BKG_COV_1", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_EXP_101", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val1, status1 = validator.validate_observation(obs1)
    assert status1 == "VALIDATED"
    resolver.resolve_child_intent(obs1, val1)

    # Before Room 2 resolves -> coverage is partial (only 1 room confirmed)
    res_partial = oracle.evaluate_parent_coverage("BKG_COV_1")
    assert res_partial.is_satisfied is False
    assert "room deficit" in res_partial.reason

    # Room 2 observation & resolution
    obs2 = writer.record_observation(
        "CHILD_R2", "BKG_COV_1", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_HB_102", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val2, status2 = validator.validate_observation(obs2)
    assert status2 == "VALIDATED"
    resolver.resolve_child_intent(obs2, val2)

    # Now both rooms are active and verified -> 100% coverage satisfied
    res_full = oracle.evaluate_parent_coverage("BKG_COV_1")
    assert res_full.is_satisfied is True
    assert res_full.nights_covered == 2
    assert len(res_full.verified_reservations) == 2

def test_02_gap_in_dates_fails_coverage():
    """2. Date gap in reservations fails coverage oracle."""
    conn = get_test_db()
    # Parent stay: Dec 1 to Dec 4 (3 nights)
    seed_test_booking_environment(conn, "BKG_COV_GAP", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-04", room_count=1, adults=2)
    seed_child_intent(conn, "CHILD_GAP", "BKG_COV_GAP", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    oracle = CoverageOracle(conn)

    # Reservation only covers Dec 1 to Dec 3 (leaves night of Dec 3 -> Dec 4 missing!)
    obs = writer.record_observation(
        "CHILD_GAP", "BKG_COV_GAP", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_GAP_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val, _ = validator.validate_observation(obs)
    resolver.resolve_child_intent(obs, val)

    res = oracle.evaluate_parent_coverage("BKG_COV_GAP")
    assert res.is_satisfied is False
    assert res.nights_covered == 2  # Only 2 out of 3 nights covered
    assert "room deficit" in res.reason

def test_03_property_mismatch_fails_coverage():
    """3. Confirmed reservation for different hotel property fails coverage oracle."""
    conn = get_test_db()
    # Parent specifies HTL_MUMBAI
    seed_test_booking_environment(conn, "BKG_COV_HTL", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    seed_child_intent(conn, "CHILD_HTL", "BKG_COV_HTL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    writer = ObservationWriter(conn)
    oracle = CoverageOracle(conn)

    # Observation is confirmed for HTL_DELHI instead of HTL_MUMBAI
    obs = writer.record_observation(
        "CHILD_HTL", "BKG_COV_HTL", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_DELHI", "hotel_id": "HTL_DELHI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    # Validator rejects it
    validator = ObservationValidator(conn)
    val, val_status = validator.validate_observation(obs)
    assert val_status == "REJECTED"

    # Coverage oracle evaluates: no active valid resolution for HTL_MUMBAI exists
    res = oracle.evaluate_parent_coverage("BKG_COV_HTL")
    assert res.is_satisfied is False
    assert len(res.verified_reservations) == 0

def test_04_occupancy_deficit_fails_coverage():
    """4. Occupancy deficit across confirmed reservations fails coverage oracle."""
    conn = get_test_db()
    # Parent requires 4 adults
    seed_test_booking_environment(conn, "BKG_COV_OCC", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=4)
    # Child room requested with capacity 2 (e.g. standard room)
    seed_child_intent(conn, "CHILD_OCC", "BKG_COV_OCC", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    oracle = CoverageOracle(conn)

    # Reservation confirmed for 1 room with occupancy=2 (matching child request, so validated)
    obs = writer.record_observation(
        "CHILD_OCC", "BKG_COV_OCC", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_OCC_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val, val_status = validator.validate_observation(obs)
    assert val_status == "VALIDATED"
    resolver.resolve_child_intent(obs, val)

    # Parent requires 4 adults, but only 2 adults covered -> coverage oracle flags deficit
    res = oracle.evaluate_parent_coverage("BKG_COV_OCC")
    assert res.is_satisfied is False
    assert "occupancy deficit" in res.reason

def test_05_split_stay_continuity_across_suppliers():
    """5. Split-stay continuity: Night 1 via Expedia, Night 2 via Hotelbeds at same hotel form zero-gap coverage."""
    conn = get_test_db()
    # Parent stay: Dec 1 to Dec 3 (2 nights), 1 room, 2 adults
    seed_test_booking_environment(conn, "BKG_COV_SPLIT", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)
    
    # Segment 1: Dec 1 -> Dec 2 (Expedia)
    seed_child_intent(conn, "CH_SPLIT_1", "BKG_COV_SPLIT", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-02", occupancy=2, room_slot_index=0)
    # Segment 2: Dec 2 -> Dec 3 (Hotelbeds)
    seed_child_intent(conn, "CH_SPLIT_2", "BKG_COV_SPLIT", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-02", "2026-12-03", occupancy=2, room_slot_index=0)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    oracle = CoverageOracle(conn)

    # Confirm Segment 1
    obs1 = writer.record_observation(
        "CH_SPLIT_1", "BKG_COV_SPLIT", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_SPLIT_EXP", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-02", "occupancy": 2}
    )
    val1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, val1)

    # Confirm Segment 2
    obs2 = writer.record_observation(
        "CH_SPLIT_2", "BKG_COV_SPLIT", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_SPLIT_HB", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-02", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, val2)

    # Verify coverage oracle sees continuous zero-gap coverage across both nights
    res = oracle.evaluate_parent_coverage("BKG_COV_SPLIT")
    assert res.is_satisfied is True
    assert res.nights_covered == 2
    assert len(res.verified_reservations) == 2
    assert len(res.intra_slot_overlaps) == 0

def test_06_intra_slot_overlap_rejection():
    """6. Intra-slot overlap rejection: two reservations covering the same room slot for overlapping dates are rejected."""
    conn = get_test_db()
    # Parent stay: Dec 1 to Dec 3 (2 nights), 1 room, 2 adults
    seed_test_booking_environment(conn, "BKG_COV_OVERLAP", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=1, adults=2)

    # Two child intents both assigned to room_slot_index=0 with overlapping dates (Dec 1 -> Dec 3)
    seed_child_intent(conn, "CH_OV_1", "BKG_COV_OVERLAP", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_OV_2", "BKG_COV_OVERLAP", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    oracle = CoverageOracle(conn)

    # Confirm both reservations
    obs1 = writer.record_observation(
        "CH_OV_1", "BKG_COV_OVERLAP", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_OV_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, val1)

    obs2 = writer.record_observation(
        "CH_OV_2", "BKG_COV_OVERLAP", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_OV_2", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 2}
    )
    val2, _ = validator.validate_observation(obs2)
    resolver.resolve_child_intent(obs2, val2)

    # Oracle MUST detect intra-slot overlap conflict on slot 0 and fail satisfaction!
    res = oracle.evaluate_parent_coverage("BKG_COV_OVERLAP")
    assert res.is_satisfied is False
    assert len(res.intra_slot_overlaps) > 0
    assert "Intra-slot overlap" in res.reason

def test_07_per_room_occupancy_deficit_rejection():
    """7. Per-room occupancy: if a specific room slot fails its specified guest capacity, oracle rejects."""
    conn = get_test_db()
    # Parent requires 2 rooms, total 4 adults
    seed_test_booking_environment(conn, "BKG_COV_PER_ROOM", "AGENCY_ALPHA", "HTL_MUMBAI", "2026-12-01", "2026-12-03", room_count=2, adults=4)

    # Room slot 0 requires 2 adults, Room slot 1 requires 2 adults
    seed_child_intent(conn, "CH_PR_1", "BKG_COV_PER_ROOM", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=0)
    seed_child_intent(conn, "CH_PR_2", "BKG_COV_PER_ROOM", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB", "HTL_MUMBAI", "2026-12-01", "2026-12-03", occupancy=2, room_slot_index=1)

    writer = ObservationWriter(conn)
    validator = ObservationValidator(conn)
    resolver = ResolutionManager(conn)
    oracle = CoverageOracle(conn)

    # Room slot 0 confirms for 3 guests (exceeds requirement)
    obs1 = writer.record_observation(
        "CH_PR_1", "BKG_COV_PER_ROOM", "AGENCY_ALPHA", "SUP_EXPEDIA", "ACC_GLOBAL_EXP",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_PR_1", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 3}
    )
    val1, _ = validator.validate_observation(obs1)
    resolver.resolve_child_intent(obs1, val1)

    # Room slot 1 confirms for only 1 guest (required 2)
    # In child segment spec, guest_capacity was 2. If supplier returns 1, validator REJECTS it.
    obs2 = writer.record_observation(
        "CH_PR_2", "BKG_COV_PER_ROOM", "AGENCY_ALPHA", "SUP_HOTELBEDS", "ACC_ALPHA_HB",
        lifecycle_outcome="RESERVATION_ACTIVE",
        raw_response={"status": "CONFIRMED", "supplier_reservation_id": "RES_PR_2", "hotel_id": "HTL_MUMBAI", "check_in_date": "2026-12-01", "check_out_date": "2026-12-03", "occupancy": 1}
    )
    val2, val2_status = validator.validate_observation(obs2)
    assert val2_status == "REJECTED"

    # Total occupancy would be 3 + 1 = 4 in aggregate, but room 1 has a deficit.
    # Because room 2 was rejected, it is not resolved active, so oracle rejects.
    res = oracle.evaluate_parent_coverage("BKG_COV_PER_ROOM")
    assert res.is_satisfied is False

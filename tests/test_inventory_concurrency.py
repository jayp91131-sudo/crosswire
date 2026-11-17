"""
Milestone 2.2 — Test Suite: Hotel Room Inventory Concurrency & Overbooking (Scenario B).
Verifies:
1. Vulnerable mode TOCTOU race: two simultaneous booking requests for the last remaining room
   both succeed, causing inventory overbooking and emitting finding CW-SEC-M2-202.
2. Fixed mode atomic enforcement: exactly one booking succeeds (HTTP 200), the competing request
   fails with RoomSoldOutError (HTTP 409), capacity is never exceeded.
3. Invariants 6.6 (capacity constraint) and 6.7 (non-negative inventory units).
4. Cancellation compensation: cancelling a reservation restores inventory back to available.
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from target_platform.supplier.app import supplier_app
from target_platform.supplier.database import get_supplier_db
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.models import (
    SupplierBookingRequest,
    RoomSoldOutError,
    SupplierMode,
)
from engine.invariant_engine import get_invariant_engine
from engine.ground_truth_oracle import get_ground_truth_oracle


@pytest.fixture(autouse=True)
def setup_teardown():
    s_db = get_supplier_db()
    s_db.reset()
    s_service = get_supplier_service()
    s_service.set_mode(SupplierMode.IDEMPOTENT)
    s_service.set_inventory_mode("ATOMIC")
    s_service.set_inventory_check_enabled(True)
    yield
    s_db.reset()
    s_service.set_inventory_mode("ATOMIC")
    s_service.set_inventory_check_enabled(False)


@pytest.mark.asyncio
async def test_vulnerable_mode_last_room_overbooking_toctou_race():
    """
    Scenario B (Vulnerable):
    Hotel 'htl_mumbai_grand', room 'deluxe_suite' has capacity=1, available=1.
    Two competing requests from Agency Alpha and Agency Beta arrive simultaneously.
    Vulnerable mode executes non-atomic check-then-reserve with an interleaving window.
    Both requests succeed, creating 2 confirmed reservations for 1 physical room.
    Invariant 6.6 flags CW-SEC-M2-202 (CRITICAL).
    """
    s_db = get_supplier_db()
    s_service = get_supplier_service()
    s_service.set_inventory_mode("VULNERABLE")
    s_service.set_inventory_check_enabled(True)
    invariant_engine = get_invariant_engine()

    hotel_id = "htl_mumbai_grand"
    room_type = "deluxe_suite"

    # Verify initial inventory capacity=1, available=1
    inv_initial = s_db.get_inventory(hotel_id, room_type)
    assert inv_initial.capacity == 1
    assert inv_initial.available_units == 1
    assert inv_initial.allocated_units == 0

    req_alpha = SupplierBookingRequest(
        intent_id="INTENT-INV-VULN-ALPHA",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Guest Alpha",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        correlation_id="corr-inv-vuln-alpha",
        idempotency_key="IDEMP-INV-VULN-A",
    )

    req_beta = SupplierBookingRequest(
        intent_id="INTENT-INV-VULN-BETA",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Guest Beta",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        correlation_id="corr-inv-vuln-beta",
        idempotency_key="IDEMP-INV-VULN-B",
    )

    # Execute both simultaneously
    results = await asyncio.gather(
        s_service.create_reservation(req_alpha),
        s_service.create_reservation(req_beta),
        return_exceptions=True,
    )

    # In vulnerable mode, both reservations succeeded
    assert not isinstance(results[0], Exception)
    assert not isinstance(results[1], Exception)
    assert results[0].supplier_reservation_id != results[1].supplier_reservation_id

    # Ground truth: 2 reservations exist for capacity=1
    inv_after = s_db.get_inventory(hotel_id, room_type)
    assert inv_after.allocated_units == 2  # Overallocated!
    assert inv_after.available_units == -1  # Negative available inventory!

    # Invariant 6.6 MUST fail and flag CW-SEC-M2-202
    inv_check = invariant_engine.check_inventory_capacity(hotel_id, room_type)
    assert not inv_check.passed
    assert "CW-SEC-M2-202" in inv_check.evidence["finding_id"]
    assert inv_check.evidence["violation_detected"] is True
    assert inv_check.severity.value == "CRITICAL"


@pytest.mark.asyncio
async def test_fixed_mode_prevents_last_room_overbooking_atomic_reservation():
    """
    Scenario B (Fixed):
    Hotel 'htl_mumbai_grand', room 'deluxe_suite' has capacity=1, available=1.
    Two competing requests arrive simultaneously.
    Fixed mode executes atomic conditional UPDATE: WHERE available_units >= 1.
    Exactly one succeeds; the second is rejected with RoomSoldOutError (HTTP 409).
    Invariants 6.6 and 6.7 pass with clean audit evidence.
    """
    s_db = get_supplier_db()
    s_service = get_supplier_service()
    s_service.set_inventory_mode("ATOMIC")
    s_service.set_inventory_check_enabled(True)
    invariant_engine = get_invariant_engine()

    hotel_id = "htl_mumbai_grand"
    room_type = "deluxe_suite"

    req_alpha = SupplierBookingRequest(
        intent_id="INTENT-INV-FIX-ALPHA",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Guest Alpha",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        correlation_id="corr-inv-fix-alpha",
        idempotency_key="IDEMP-INV-FIX-A",
    )

    req_beta = SupplierBookingRequest(
        intent_id="INTENT-INV-FIX-BETA",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Guest Beta",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        correlation_id="corr-inv-fix-beta",
        idempotency_key="IDEMP-INV-FIX-B",
    )

    results = await asyncio.gather(
        s_service.create_reservation(req_alpha),
        s_service.create_reservation(req_beta),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
    assert isinstance(failures[0], RoomSoldOutError)

    inv_after = s_db.get_inventory(hotel_id, room_type)
    assert inv_after.allocated_units == 1
    assert inv_after.available_units == 0

    # Invariants 6.6 and 6.7 pass
    inv_6_6 = invariant_engine.check_inventory_capacity(hotel_id, room_type)
    assert inv_6_6.passed

    inv_6_7 = invariant_engine.check_inventory_non_negative(hotel_id, room_type)
    assert inv_6_7.passed


@pytest.mark.asyncio
async def test_cancellation_restores_inventory_units():
    """
    Tests that cancelling a confirmed supplier reservation restores the available room units.
    """
    s_db = get_supplier_db()
    s_service = get_supplier_service()
    s_service.set_inventory_mode("ATOMIC")
    s_service.set_inventory_check_enabled(True)

    hotel_id = "htl_mumbai_grand"
    room_type = "deluxe_suite"

    req = SupplierBookingRequest(
        intent_id="INTENT-INV-CANCEL-TEST",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Guest Cancel",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        correlation_id="corr-inv-cancel",
        idempotency_key="IDEMP-INV-CANCEL",
    )

    res = await s_service.create_reservation(req)
    inv1 = s_db.get_inventory(hotel_id, room_type)
    assert inv1.allocated_units == 1
    assert inv1.available_units == 0

    # Cancel reservation
    cancelled = await s_service.cancel_reservation(res.supplier_reservation_id, reason="Customer cancellation")
    assert cancelled is True

    inv2 = s_db.get_inventory(hotel_id, room_type)
    assert inv2.allocated_units == 0
    assert inv2.available_units == 1  # Restored!

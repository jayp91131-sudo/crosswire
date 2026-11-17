"""
Concurrency Test for Supplier Cancellation Atomicity (CW-CON-M2-211).
Verifies that multiple concurrent cancellation requests for the same supplier reservation
execute under atomic BEGIN IMMEDIATE locking:
- Exactly ONE cancellation succeeds (returns True and restores inventory).
- All duplicate concurrent cancellations return False (no-op).
- Hotel inventory available_units increases by exactly 1, never 2+.
"""

import pytest
import concurrent.futures
from datetime import datetime
from target_platform.supplier.database import get_supplier_db
from target_platform.models import SupplierReservation


@pytest.fixture(autouse=True)
def reset_supplier():
    db = get_supplier_db()
    db.reset()
    yield
    db.reset()


def test_concurrent_supplier_cancellations():
    db = get_supplier_db()

    # 1. Reserve inventory and save a reservation
    ok = db.reserve_inventory_fixed("htl_mumbai_grand", "standard_room")
    assert ok is True

    res = SupplierReservation(
        supplier_reservation_id="S-CANCEL-RACE-001",
        intent_id="intent_cancel_race",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        guest_name="Test Guest",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    db.save_reservation(res)
    res_id = res.supplier_reservation_id

    # Check inventory before cancellation
    inv_before = db.get_inventory("htl_mumbai_grand", "standard_room")
    avail_before = inv_before.available_units
    alloc_before = inv_before.allocated_units

    # 2. Fire 10 concurrent threads attempting to cancel the exact same reservation
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(db.cancel_reservation, res_id) for _ in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # 3. Assert atomicity
    true_count = sum(1 for r in results if r is True)
    false_count = sum(1 for r in results if r is False)

    assert true_count == 1, f"Expected exactly 1 successful cancellation, got {true_count}"
    assert false_count == 9, f"Expected 9 failed duplicate cancellations, got {false_count}"

    # 4. Verify inventory was restored by exactly 1
    inv_after = db.get_inventory("htl_mumbai_grand", "standard_room")
    assert inv_after.available_units == avail_before + 1
    assert inv_after.allocated_units == alloc_before - 1

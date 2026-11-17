"""
Milestone 2.2 Final Recovery Hardening — Supplier Cancellation Audit Suite.
Verifies supplier cancellation atomicity under high concurrency:
1. Cancellation concurrent with new booking claiming the last room.
2. Mass duplicate cancellations on the same reservation.
3. Transactional rollback integrity when an error is injected during cancellation.
"""

import pytest
import sqlite3
import concurrent.futures
from datetime import datetime
from unittest.mock import patch

from target_platform.supplier.database import get_supplier_db
from target_platform.models import SupplierReservation
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
def reset_supplier():
    db = get_supplier_db()
    db.reset()
    yield
    db.reset()


def test_cancellation_concurrent_with_new_booking_last_room():
    """
    Scenario:
    - Standard room starts with capacity=10, 9 allocated, 1 available.
    - An existing confirmed reservation (res_to_cancel) is active.
    - Concurrently:
      - 5 threads attempt to cancel res_to_cancel.
      - 5 threads attempt to reserve the remaining room.
    - Under BEGIN IMMEDIATE atomic transactions:
      - Inventory available_units must NEVER become negative.
      - Total active reservations must strictly equal allocated_units.
      - capacity == available_units + allocated_units.
    """
    sup_db = get_supplier_db()
    hotel_id = "htl_mumbai_grand"
    room_type = "standard_room"

    # Allocate 9 rooms, leaving exactly 1 available
    for i in range(9):
        ok = sup_db.reserve_inventory_fixed(hotel_id, room_type)
        assert ok is True
        res = SupplierReservation(
            supplier_reservation_id=f"SUP-EXISTING-{i}",
            intent_id=f"intent_existing_{i}",
            hotel_id=hotel_id,
            room_type=room_type,
            guest_name=f"Existing Guest {i}",
            amount=5000.0,
            amount_minor=500000,
            currency="INR",
            status="CONFIRMED",
            created_at=datetime.utcnow(),
        )
        sup_db.save_reservation(res)

    inv_mid = sup_db.get_inventory(hotel_id, room_type)
    assert inv_mid.available_units == 1
    assert inv_mid.allocated_units == 9

    target_res_id = "SUP-EXISTING-0"

    cancel_results = []
    book_results = []

    def cancel_task():
        return sup_db.cancel_reservation(target_res_id)

    def book_task(idx):
        reserved = sup_db.reserve_inventory_fixed(hotel_id, room_type)
        if reserved:
            new_res = SupplierReservation(
                supplier_reservation_id=f"SUP-CONCUR-BOOK-{idx}",
                intent_id=f"intent_concur_{idx}",
                hotel_id=hotel_id,
                room_type=room_type,
                guest_name=f"Racer {idx}",
                amount=5000.0,
                amount_minor=500000,
                currency="INR",
                status="CONFIRMED",
                created_at=datetime.utcnow(),
            )
            sup_db.save_reservation(new_res)
            return True
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        c_futs = [executor.submit(cancel_task) for _ in range(5)]
        b_futs = [executor.submit(book_task, i) for i in range(5)]

        for f in concurrent.futures.as_completed(c_futs):
            cancel_results.append(f.result())
        for f in concurrent.futures.as_completed(b_futs):
            book_results.append(f.result())

    # Exactly 1 cancellation succeeded
    assert sum(1 for r in cancel_results if r is True) == 1

    # Bookings: total successful new bookings must be <= 2 (1 initially available + 1 restored from cancel)
    successful_bookings = sum(1 for r in book_results if r is True)
    assert successful_bookings <= 2

    # Verify inventory integrity
    inv_final = sup_db.get_inventory(hotel_id, room_type)
    assert inv_final.available_units >= 0
    assert inv_final.capacity == inv_final.available_units + inv_final.allocated_units

    # Invariants pass
    engine = get_invariant_engine()
    assert engine.check_inventory_non_negative(hotel_id, room_type).passed
    assert engine.check_inventory_allocation_conservation(hotel_id, room_type).passed
    assert engine.check_cancellation_inventory_conservation(hotel_id, room_type).passed


def test_mass_duplicate_cancellations():
    """
    25 concurrent threads attempting to cancel the exact same supplier reservation.
    Guarantees:
    - Exactly 1 thread succeeds (returns True).
    - 24 threads return False.
    - available_units increments by exactly 1.
    """
    sup_db = get_supplier_db()
    hotel_id = "htl_mumbai_grand"
    room_type = "standard_room"

    ok = sup_db.reserve_inventory_fixed(hotel_id, room_type)
    assert ok is True

    res = SupplierReservation(
        supplier_reservation_id="SUP-MASS-CANCEL-001",
        intent_id="intent_mass_cancel",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Mass Guest",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    sup_db.save_reservation(res)

    inv_before = sup_db.get_inventory(hotel_id, room_type)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futs = [executor.submit(sup_db.cancel_reservation, "SUP-MASS-CANCEL-001") for _ in range(25)]
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())

    assert sum(1 for r in results if r is True) == 1
    assert sum(1 for r in results if r is False) == 24

    inv_after = sup_db.get_inventory(hotel_id, room_type)
    assert inv_after.available_units == inv_before.available_units + 1
    assert inv_after.allocated_units == inv_before.allocated_units - 1


def test_injected_error_during_cancellation_rolls_back_cleanly():
    """
    Simulates a failure (disk failure, lock conflict) inside cancel_reservation
    after updating reservation status but before committing inventory restoration.
    The entire transaction must ROLLBACK:
    - Reservation status remains CONFIRMED.
    - Inventory available_units and allocated_units are unchanged.
    """
    sup_db = get_supplier_db()
    hotel_id = "htl_mumbai_grand"
    room_type = "standard_room"

    ok = sup_db.reserve_inventory_fixed(hotel_id, room_type)
    assert ok is True

    res = SupplierReservation(
        supplier_reservation_id="SUP-ROLLBACK-001",
        intent_id="intent_rollback",
        hotel_id=hotel_id,
        room_type=room_type,
        guest_name="Rollback Guest",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    sup_db.save_reservation(res)

    inv_before = sup_db.get_inventory(hotel_id, room_type)

    orig_connect = sqlite3.connect

    class FaultyConnProxy:
        def __init__(self, target):
            super().__setattr__("_target", target)

        def execute(self, sql, params=()):
            if "UPDATE hotel_inventory" in sql:
                raise sqlite3.OperationalError("Simulated disk I/O error during inventory restoration")
            return self._target.execute(sql, params)

        def commit(self):
            return self._target.commit()

        def rollback(self):
            return self._target.rollback()

        def close(self):
            return self._target.close()

        def __getattr__(self, name):
            return getattr(self._target, name)

        def __setattr__(self, name, value):
            setattr(self._target, name, value)

    def faulty_connect(*args, **kwargs):
        real_conn = orig_connect(*args, **kwargs)
        return FaultyConnProxy(real_conn)

    with patch("target_platform.supplier.database.sqlite3.connect", side_effect=faulty_connect):
        with pytest.raises(sqlite3.OperationalError):
            sup_db.cancel_reservation("SUP-ROLLBACK-001")

    # State must be rolled back completely
    res_after = sup_db.get_reservation("SUP-ROLLBACK-001")
    assert res_after.status == "CONFIRMED"

    inv_after = sup_db.get_inventory(hotel_id, room_type)
    assert inv_after.available_units == inv_before.available_units
    assert inv_after.allocated_units == inv_before.allocated_units

    # Invariants pass
    engine = get_invariant_engine()
    assert engine.check_inventory_allocation_conservation(hotel_id, room_type).passed

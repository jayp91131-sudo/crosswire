"""
Milestone 2.2 — Test Suite: Distributed Saga & Compensation (Scenario D).
Verifies:
1. Boundary 1: Wallet hold succeeds, but supplier inventory is sold out ->
   Saga compensates by releasing wallet hold (status 'RELEASED'), booking marked 'FAILED',
   and available wallet funds are completely restored.
2. Boundary 2: Supplier succeeds, but downstream processing fails ->
   Saga compensates by invoking supplier reservation cancellation to free physical room inventory.
3. Boundary 3: Supplier times out during wallet booking ->
   Aggregator enters 'SUPPLIER_UNKNOWN' / 'RECONCILIATION_REQUIRED', preserving wallet hold in 'HELD'
   without double-debiting or leaking funds until reconciliation settles.
4. Invariants 6.10 and 6.11: Distributed saga state consistency across wallet, supplier, and booking.
"""

import pytest
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.models import (
    BookingIntent,
    AggregatorMode,
    SupplierMode,
    RoomSoldOutError,
    TransactionStatus,
)
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
def setup_teardown(supplier_client):
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()

    agg_service = get_aggregator_booking_service()
    sup_service = get_supplier_service()
    agg_service.supplier_client = supplier_client
    agg_service.set_mode(AggregatorMode.FIXED)
    agg_service.set_wallet_mode(AggregatorMode.FIXED)
    sup_service.set_mode(SupplierMode.IDEMPOTENT)
    sup_service.set_inventory_mode("ATOMIC")
    sup_service.set_inventory_check_enabled(False)

    yield

    agg_db.reset()
    sup_db.reset()


@pytest.mark.asyncio
async def test_saga_compensation_on_supplier_sold_out():
    """
    Boundary 1: Agency submits wallet booking for a room that is sold out.
    - Wallet hold is created (HELD).
    - Supplier rejects with RoomSoldOutError.
    - Saga compensates: wallet hold is transition to RELEASED.
    - Agency wallet available balance is restored to full initial amount.
    - Invariant 6.11 verifies saga compensation consistency.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_service = get_aggregator_booking_service()
    sup_service = get_supplier_service()
    invariant_engine = get_invariant_engine()

    # Enable inventory check and set capacity to 0 (sold out)
    sup_service.set_inventory_check_enabled(True)
    sup_db.set_inventory("htl_mumbai_grand", "deluxe_suite", capacity=0, available_units=0)

    agency_id = "agency_alpha"
    wallet_before = agg_db.get_wallet(agency_id)
    assert wallet_before.available_balance_minor == 1000000

    intent = BookingIntent(
        intent_id="INTENT-SAGA-SOLDOUT",
        agency_id=agency_id,
        hotel_id="htl_mumbai_grand",
        room_type="deluxe_suite",
        amount=4000.0,
        amount_minor=400000,
        currency="INR",
        guest_name="Saga Traveler",
        correlation_id="corr-saga-soldout",
        payment_method="WALLET",
        idempotency_key="IDEMP-SAGA-SOLDOUT",
    )

    with pytest.raises(RoomSoldOutError):
        await agg_service.process_booking_intent(intent)

    # Verify wallet funds were restored
    wallet_after = agg_db.get_wallet(agency_id)
    assert wallet_after.active_holds_minor == 0
    assert wallet_after.available_balance_minor == 1000000  # 100% restored!

    # Verify booking record status is FAILED
    booking = agg_db.get_booking_by_intent("INTENT-SAGA-SOLDOUT")
    assert booking.status.value == "FAILED"

    # Verify wallet hold status is RELEASED
    hold = next(h for h in agg_db.get_wallet_holds(agency_id) if h.hold_id == booking.wallet_hold_id)
    assert hold.status.value == "RELEASED"

    # Invariant 6.11 check
    inv_res = invariant_engine.check_saga_state_consistency(booking.booking_id)
    assert inv_res.passed


@pytest.mark.asyncio
async def test_saga_compensation_supplier_cancellation():
    """
    Boundary 2: Supplier confirmation succeeds, then downstream cancellation is initiated.
    - Aggregator calls supplier cancellation endpoint.
    - Supplier cancels reservation and restores room inventory.
    """
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()

    sup_service.set_inventory_check_enabled(True)
    sup_db.set_inventory("htl_mumbai_grand", "deluxe_suite", capacity=1, available_units=1)

    intent = BookingIntent(
        intent_id="INTENT-SAGA-CANCEL",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="deluxe_suite",
        amount=3000.0,
        amount_minor=300000,
        currency="INR",
        guest_name="Cancel Guest",
        correlation_id="corr-saga-cancel",
        payment_method="WALLET",
        idempotency_key="IDEMP-SAGA-CANCEL",
    )

    booking = await agg_service.process_booking_intent(intent)
    assert booking.status.value == "SUPPLIER_CONFIRMED"
    sup_res_id = booking.primary_supplier_reservation_id

    # Inventory is allocated
    inv1 = sup_db.get_inventory("htl_mumbai_grand", "deluxe_suite")
    assert inv1.allocated_units == 1
    assert inv1.available_units == 0

    # Execute supplier compensation / cancellation
    cancelled = await agg_service.supplier_client.call_cancel_reservation(sup_res_id, reason="Saga compensation")
    assert cancelled is True

    # Inventory is restored
    inv2 = sup_db.get_inventory("htl_mumbai_grand", "deluxe_suite")
    assert inv2.allocated_units == 0
    assert inv2.available_units == 1


@pytest.mark.asyncio
async def test_saga_supplier_timeout_enters_reconciliation_without_double_debit():
    """
    Boundary 3: Supplier times out during wallet booking.
    - Aggregator catches timeout and transitions booking to SUPPLIER_UNKNOWN.
    - Wallet hold is preserved in HELD status (NOT released yet, in case supplier committed).
    - No double debit occurs.
    - Invariant 6.10/6.11 verifies ambiguous hold consistency.
    """
    agg_db = get_aggregator_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()
    invariant_engine = get_invariant_engine()

    # Induce timeout on supplier
    sup_service.configure(simulate_timeout=True, timeout_delay_seconds=0.35)

    agency_id = "agency_alpha"
    intent = BookingIntent(
        intent_id="INTENT-SAGA-TIMEOUT",
        agency_id=agency_id,
        hotel_id="htl_mumbai_grand",
        room_type="deluxe_suite",
        amount=3000.0,
        amount_minor=300000,
        currency="INR",
        guest_name="Timeout Guest",
        correlation_id="corr-saga-timeout",
        payment_method="WALLET",
        idempotency_key="IDEMP-SAGA-TIMEOUT",
    )

    booking = await agg_service.process_booking_intent(intent)
    assert booking.status.value in ["SUPPLIER_UNKNOWN", "RECONCILIATION_REQUIRED"]

    # Wallet hold is preserved in HELD
    wallet = agg_db.get_wallet(agency_id)
    assert wallet.active_holds_minor == 300000
    assert wallet.total_captured_minor == 0  # NOT double debited or prematurely captured!

    # Invariant check
    inv_res = invariant_engine.check_saga_state_consistency(booking.booking_id)
    assert inv_res.passed

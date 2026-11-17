"""
Test Suite for Reliable Saga Compensation and Crash Recovery (CW-ARC-M2-208).
Verifies:
1. When supplier reservation succeeds but wallet capture fails, Aggregator reliably
   compensates by canceling the supplier reservation, releasing the wallet hold,
   and recording a COMPENSATED saga recovery record.
2. If supplier cancellation also fails or times out, Aggregator marks RECONCILIATION_REQUIRED
   in both the booking claim and saga_recovery_records table.
3. Ambiguous-capture check: if the hold was authoritatively captured, Aggregator does NOT
   compensate or cancel the supplier reservation.
"""

import pytest
from unittest.mock import patch
from datetime import datetime
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.models import (
    BookingIntent,
    AggregatorMode,
    SupplierMode,
    TransactionStatus,
    BookingClaimStatus,
    WalletHoldStatus,
)
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
def setup_teardown(supplier_client):
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()

    agg_service = get_aggregator_booking_service()
    agg_service.supplier_client = supplier_client
    agg_service.set_mode(AggregatorMode.FIXED)
    agg_service.set_wallet_mode(AggregatorMode.FIXED)

    yield

    agg_db.reset()
    sup_db.reset()


@pytest.mark.asyncio
async def test_saga_compensation_on_wallet_capture_failure():
    """
    Scenario: Supplier reservation is successfully confirmed.
    Wallet capture encounters an unexpected database/system failure.
    Aggregator must:
    1. Detect capture failure.
    2. Perform ambiguous capture check (confirm hold is not captured).
    3. Call supplier cancellation.
    4. Release the wallet hold.
    5. Record saga recovery with status 'COMPENSATED'.
    6. Transition booking to FAILED.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    service = get_aggregator_booking_service()

    intent = BookingIntent(
        intent_id="intent_saga_cap_fail",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Saga Guest",
        correlation_id="corr_saga_1",
        payment_method="WALLET",
        idempotency_key="idemp_saga_001",
    )

    # Intercept capture_wallet_hold to simulate a database crash/failure during capture
    orig_capture = agg_db.capture_wallet_hold

    def mock_capture_fail(hold_id):
        raise RuntimeError("Simulated database deadlock during wallet capture")

    with patch.object(agg_db, "capture_wallet_hold", side_effect=mock_capture_fail):
        booking = await service.process_booking_intent(intent)

    # 1. Booking must be FAILED
    assert booking.status == TransactionStatus.FAILED
    assert "cancelled via saga compensation" in booking.error_message

    # 2. Wallet hold must be RELEASED
    hold = agg_db.get_wallet_hold(booking.wallet_hold_id)
    assert hold.status == WalletHoldStatus.RELEASED

    # 3. Supplier reservation must be CANCELLED in supplier DB
    sup_res = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(sup_res) == 1
    assert sup_res[0].status == "CANCELLED"

    # 4. Saga recovery record must be persisted
    records = agg_db.get_saga_recovery_records(booking.booking_id)
    assert len(records) >= 1
    assert records[0].status == "COMPENSATED"
    assert records[0].target_supplier_reservation_id == sup_res[0].supplier_reservation_id

    # 5. Claim status must be FAILED
    claim = agg_db.get_booking_claim("agency_alpha", "idemp_saga_001")
    assert claim.status == BookingClaimStatus.FAILED

    # 6. Verify Invariant 6.11
    engine = get_invariant_engine()
    res_6_11 = engine.check_saga_state_consistency(booking.booking_id)
    assert res_6_11.passed is True


@pytest.mark.asyncio
async def test_saga_cancellation_failure_enters_reconciliation_required():
    """
    Scenario: Supplier reservation is confirmed. Wallet capture fails,
    AND supplier cancellation fails/times out.
    Aggregator must:
    1. Enter SUPPLIER_UNKNOWN / RECONCILIATION_REQUIRED.
    2. Persist saga recovery record with status 'RECONCILIATION_REQUIRED'.
    3. Update claim status to RECONCILIATION_REQUIRED.
    """
    agg_db = get_aggregator_db()
    service = get_aggregator_booking_service()

    intent = BookingIntent(
        intent_id="intent_saga_cancel_fail",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Saga Cancel Fail Guest",
        correlation_id="corr_saga_2",
        payment_method="WALLET",
        idempotency_key="idemp_saga_002",
    )

    def mock_capture_fail(hold_id):
        raise RuntimeError("Simulated wallet capture crash")

    async def mock_cancel_fail(res_id, reason=None):
        raise RuntimeError("Supplier network partitioned during cancellation")

    with patch.object(agg_db, "capture_wallet_hold", side_effect=mock_capture_fail), \
         patch.object(service.supplier_client, "call_cancel_reservation", side_effect=mock_cancel_fail):
        booking = await service.process_booking_intent(intent)

    # 1. Booking must be SUPPLIER_UNKNOWN
    assert booking.status == TransactionStatus.SUPPLIER_UNKNOWN
    assert "Manual reconciliation required" in booking.error_message

    # 2. Saga recovery record must have status RECONCILIATION_REQUIRED
    records = agg_db.get_saga_recovery_records(booking.booking_id)
    assert len(records) >= 1
    assert records[0].status == "RECONCILIATION_REQUIRED"

    # 3. Claim status must be RECONCILIATION_REQUIRED
    claim = agg_db.get_booking_claim("agency_alpha", "idemp_saga_002")
    assert claim.status == BookingClaimStatus.RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_ambiguous_capture_check_prevents_unnecessary_cancellation():
    """
    Scenario: capture_wallet_hold raises an exception, BUT the hold was
    actually committed as CAPTURED in SQLite (e.g. timeout after commit).
    Aggregator inspects the authoritative DB state, sees CAPTURED,
    and therefore does NOT cancel the supplier reservation.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    service = get_aggregator_booking_service()

    intent = BookingIntent(
        intent_id="intent_saga_ambig_cap",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Ambig Capture Guest",
        correlation_id="corr_saga_3",
        payment_method="WALLET",
        idempotency_key="idemp_saga_003",
    )

    real_capture = agg_db.capture_wallet_hold

    def mock_capture_succeeds_then_raises(hold_id):
        # Perform real capture first
        real_capture(hold_id)
        # Then raise an exception simulating socket disconnect right after commit
        raise ConnectionResetError("Socket reset immediately after capture commit")

    with patch.object(agg_db, "capture_wallet_hold", side_effect=mock_capture_succeeds_then_raises):
        booking = await service.process_booking_intent(intent)

    # Booking must be confirmed (NOT cancelled)
    assert booking.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert booking.primary_supplier_reservation_id is not None

    # Supplier reservation must still be CONFIRMED
    sup_res = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(sup_res) == 1
    assert sup_res[0].status == "CONFIRMED"

    # Wallet hold must be CAPTURED
    hold = agg_db.get_wallet_hold(booking.wallet_hold_id)
    assert hold.status == WalletHoldStatus.CAPTURED

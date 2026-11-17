"""
Test Suite for Wallet Refund Reconciliation (CW-FIN-M2-209).
Verifies:
1. Wallet booking refund credits agency_wallets.total_refunded_minor and creates
   a traceable REFUND transaction in wallet_transactions.
2. Gross lifetime captures in total_captured_minor are retained and not reduced.
3. Available balance formula reconciles:
   available = funded + total_refunded - total_captured - active_holds.
4. Card booking refund does NOT credit or mutate agency wallet.
5. Invariant 6.5 (WALLET_REFUND_CONSERVATION) validates that total_refunded_minor <= total_captured_minor.
"""

import pytest
import asyncio
from datetime import datetime
from target_platform.aggregator.database import get_aggregator_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.models import (
    BookingIntent,
    RefundRequest,
    AggregatorMode,
    TransactionStatus,
    WalletTransactionType,
)
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
def reset_all(supplier_client):
    db = get_aggregator_db()
    db.reset()
    service = get_aggregator_booking_service()
    service.supplier_client = supplier_client
    service.set_wallet_mode(AggregatorMode.FIXED)
    service.set_mode(AggregatorMode.FIXED)
    yield
    db.reset()


@pytest.mark.asyncio
async def test_wallet_booking_refund_credits_wallet():
    db = get_aggregator_db()
    service = get_aggregator_booking_service()
    service.set_wallet_mode(AggregatorMode.FIXED)
    service.set_mode(AggregatorMode.FIXED)

    # 1. Check initial wallet state (seeded at 1,000,000 minor units / ₹10,000)
    w_initial = db.get_wallet("agency_alpha")
    assert w_initial.funded_balance_minor == 1000000
    assert w_initial.total_captured_minor == 0
    assert w_initial.total_refunded_minor == 0
    assert w_initial.available_balance_minor == 1000000

    # 2. Book via WALLET (amount: ₹5,000 / 500,000 minor units)
    intent = BookingIntent(
        intent_id="intent_wallet_rfd_test",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Wallet Guest",
        correlation_id="corr_w_rfd_1",
        payment_method="WALLET",
        cancellation_penalty_rate=0.20,
    )
    booking = await service.process_booking_intent(intent)
    assert booking.status == TransactionStatus.SUPPLIER_CONFIRMED

    # Check post-booking wallet: 500,000 captured
    w_booked = db.get_wallet("agency_alpha")
    assert w_booked.total_captured_minor == 500000
    assert w_booked.active_holds_minor == 0
    assert w_booked.total_refunded_minor == 0
    assert w_booked.available_balance_minor == 500000  # 1,000,000 - 500,000

    # 3. Cancel booking (20% penalty = ₹1,000 / 100,000 minor; refundable entitlement = ₹4,000 / 400,000 minor)
    from target_platform.models import CancellationRequest
    cancel_resp = await service.cancel_booking(
        booking.booking_id, "agency_alpha", CancellationRequest(reason="Customer requested")
    )
    assert cancel_resp.status == TransactionStatus.CANCELLED
    assert cancel_resp.refundable_entitlement_minor == 400000

    # 4. Refund booking (₹4,000 / 400,000 minor units)
    refund_req = RefundRequest(
        amount=4000.0,
        amount_minor=400000,
        currency="INR",
        reason="Full entitlement refund",
        idempotency_key="rfd_key_wallet_001",
    )
    refund_resp = await service.refund_booking(booking.booking_id, "agency_alpha", refund_req)
    assert refund_resp.status == TransactionStatus.REFUNDED

    # 5. Verify Wallet Integrity & Accounting
    w_refunded = db.get_wallet("agency_alpha")
    # Gross capture MUST remain 500,000
    assert w_refunded.total_captured_minor == 500000
    # Total refunded must be credited to 400,000
    assert w_refunded.total_refunded_minor == 400000
    # Available balance = funded (1,000,000) + refunded (400,000) - captured (500,000) - holds (0) = 900,000
    assert w_refunded.available_balance_minor == 900000

    # 6. Verify traceable REFUND transaction in wallet_transactions
    txs = db.get_wallet_transactions("agency_alpha")
    rfd_txs = [t for t in txs if t.transaction_type == WalletTransactionType.REFUND]
    assert len(rfd_txs) == 1
    assert rfd_txs[0].amount_minor == 400000
    assert rfd_txs[0].balance_after_minor == 900000

    # 7. Verify Invariant 6.5 (WALLET_REFUND_CONSERVATION)
    engine = get_invariant_engine()
    res_6_5 = engine.check_wallet_refund_conservation("agency_alpha")
    assert res_6_5.passed is True


@pytest.mark.asyncio
async def test_card_booking_refund_does_not_mutate_wallet():
    db = get_aggregator_db()
    service = get_aggregator_booking_service()
    service.set_wallet_mode(AggregatorMode.FIXED)
    service.set_mode(AggregatorMode.FIXED)

    w_before = db.get_wallet("agency_alpha")
    init_avail = w_before.available_balance_minor

    # 1. Book via CARD
    intent = BookingIntent(
        intent_id="intent_card_rfd_test",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=3000.0,
        amount_minor=300000,
        currency="INR",
        guest_name="Card Guest",
        correlation_id="corr_card_rfd_1",
        payment_method="CARD",
        cancellation_penalty_rate=0.20,
    )
    booking = await service.process_booking_intent(intent)
    assert booking.status == TransactionStatus.SUPPLIER_CONFIRMED

    # 2. Cancel and Refund
    from target_platform.models import CancellationRequest
    await service.cancel_booking(booking.booking_id, "agency_alpha", CancellationRequest(reason="Change of plans"))

    refund_req = RefundRequest(
        amount=2400.0,
        amount_minor=240000,
        currency="INR",
        reason="Entitlement refund for card",
        idempotency_key="rfd_key_card_001",
    )
    refund_resp = await service.refund_booking(booking.booking_id, "agency_alpha", refund_req)
    assert refund_resp.status == TransactionStatus.REFUNDED

    # 3. Verify agency wallet was NEVER touched for card refund
    w_after = db.get_wallet("agency_alpha")
    assert w_after.available_balance_minor == init_avail
    assert w_after.total_refunded_minor == 0
    assert w_after.total_captured_minor == 0

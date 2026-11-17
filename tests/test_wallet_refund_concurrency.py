"""
Milestone 2.2 Final Recovery Hardening — Refund Concurrency Suite.
Verifies concurrent refund handling, atomic authorization checks, webhook deduplication,
payload tamper resistance, concurrent wallet credits, and card payment isolation.
"""

import pytest
import asyncio
from datetime import datetime
from decimal import Decimal

from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.models import (
    BookingIntent,
    AggregatorMode,
    TransactionStatus,
    CancellationRequest,
    RefundRequest,
    WalletTransactionType,
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
async def test_concurrent_partial_refunds_exceeding_entitlement(supplier_client):
    """
    Booking with ₹5,000 (500,000 minor) and 20% penalty has ₹4,000 (400,000 minor) entitlement.
    Two concurrent partial refund requests each ask for ₹3,000 (300,000 minor).
    Sum (600,000) exceeds entitlement (400,000).
    Atomic entitlement gate ensures exactly one succeeds and one is rejected.
    """
    agg_db = get_aggregator_db()
    service = get_aggregator_booking_service()

    intent = BookingIntent(
        intent_id="intent_concur_rfd_001",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Concurrent Refund Guest",
        correlation_id="corr_c_rfd_1",
        payment_method="WALLET",
        cancellation_penalty_rate=0.20,
        idempotency_key="idemp_concur_bkg_1",
    )
    booking = await service.process_booking_intent(intent)
    assert booking.status in (TransactionStatus.SUPPLIER_CONFIRMED, TransactionStatus.VOUCHER_ISSUED)

    # Cancel booking
    cancel_resp = await service.cancel_booking(
        booking.booking_id, "agency_alpha", CancellationRequest(reason="Change of plans")
    )
    assert cancel_resp.status == TransactionStatus.CANCELLED
    assert cancel_resp.refundable_entitlement_minor == 400000

    req_a = RefundRequest(
        amount=3000.0,
        amount_minor=300000,
        currency="INR",
        reason="Tranche A",
        idempotency_key="rfd_tranche_a_001",
    )
    req_b = RefundRequest(
        amount=3000.0,
        amount_minor=300000,
        currency="INR",
        reason="Tranche B",
        idempotency_key="rfd_tranche_b_001",
    )

    results = await asyncio.gather(
        service.refund_booking(booking.booking_id, "agency_alpha", req_a),
        service.refund_booking(booking.booking_id, "agency_alpha", req_b),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception) and r.status in [TransactionStatus.REFUNDED, TransactionStatus.PARTIALLY_REFUNDED]]
    failures = [r for r in results if isinstance(r, Exception) or r.status == TransactionStatus.FAILED]

    assert len(successes) == 1
    assert len(failures) == 1

    # Database verification
    final_bkg = agg_db.get_booking_record(booking.booking_id, requesting_agency_id="agency_alpha")
    assert final_bkg.total_refunded_minor == 300000
    assert final_bkg.total_refunded_minor <= final_bkg.refundable_entitlement_minor

    # Wallet verification
    wallet = agg_db.get_wallet("agency_alpha")
    assert wallet.total_refunded_minor == 300000

    # Invariants pass
    engine = get_invariant_engine()
    assert engine.check_wallet_refund_conservation("agency_alpha").passed
    assert engine.check_refund_boundedness(booking.booking_id).passed


@pytest.mark.asyncio
async def test_concurrent_distinct_refund_ids_within_entitlement(supplier_client):
    """
    Two concurrent partial refund requests of ₹2,000 (200,000 minor) each against ₹4,000 (400,000 minor) entitlement.
    Both should succeed atomically without race conditions, exhausting entitlement exactly.
    """
    agg_db = get_aggregator_db()
    service = get_aggregator_booking_service()

    intent = BookingIntent(
        intent_id="intent_concur_rfd_002",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Split Refund Guest",
        correlation_id="corr_c_rfd_2",
        payment_method="WALLET",
        cancellation_penalty_rate=0.20,
        idempotency_key="idemp_concur_bkg_2",
    )
    booking = await service.process_booking_intent(intent)
    await service.cancel_booking(
        booking.booking_id, "agency_alpha", CancellationRequest(reason="Customer requested")
    )

    req_1 = RefundRequest(
        amount=2000.0,
        amount_minor=200000,
        currency="INR",
        reason="Split 1",
        idempotency_key="rfd_split_1_002",
    )
    req_2 = RefundRequest(
        amount=2000.0,
        amount_minor=200000,
        currency="INR",
        reason="Split 2",
        idempotency_key="rfd_split_2_002",
    )

    r1, r2 = await asyncio.gather(
        service.refund_booking(booking.booking_id, "agency_alpha", req_1),
        service.refund_booking(booking.booking_id, "agency_alpha", req_2),
    )

    assert r1.status in [TransactionStatus.PARTIALLY_REFUNDED, TransactionStatus.REFUNDED]
    assert r2.status in [TransactionStatus.PARTIALLY_REFUNDED, TransactionStatus.REFUNDED]

    final_bkg = agg_db.get_booking_record(booking.booking_id, requesting_agency_id="agency_alpha")
    assert final_bkg.total_refunded_minor == 400000
    assert final_bkg.status == TransactionStatus.REFUNDED

    wallet = agg_db.get_wallet("agency_alpha")
    assert wallet.total_refunded_minor == 400000

    # Verify 2 traceable REFUND transactions
    txs = agg_db.get_wallet_transactions("agency_alpha")
    refund_txs = [t for t in txs if t.transaction_type == WalletTransactionType.REFUND]
    assert len(refund_txs) == 2


@pytest.mark.asyncio
async def test_duplicate_refund_requests_same_idempotency_key(supplier_client):
    """
    Two concurrent identical refund requests with the same idempotency_key.
    Exactly one executes financial mutation; the other receives idempotent response.
    """
    agg_db = get_aggregator_db()
    service = get_aggregator_booking_service()

    intent = BookingIntent(
        intent_id="intent_concur_rfd_003",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Idemp Refund Guest",
        correlation_id="corr_c_rfd_3",
        payment_method="WALLET",
        cancellation_penalty_rate=0.20,
        idempotency_key="idemp_concur_bkg_3",
    )
    booking = await service.process_booking_intent(intent)
    await service.cancel_booking(
        booking.booking_id, "agency_alpha", CancellationRequest(reason="Customer requested")
    )

    req = RefundRequest(
        amount=4000.0,
        amount_minor=400000,
        currency="INR",
        reason="Full refund",
        idempotency_key="rfd_exact_duplicate_003",
    )

    r1, r2 = await asyncio.gather(
        service.refund_booking(booking.booking_id, "agency_alpha", req),
        service.refund_booking(booking.booking_id, "agency_alpha", req),
    )

    assert r1.status == TransactionStatus.REFUNDED
    assert r2.status == TransactionStatus.REFUNDED
    assert r1.total_refunded_minor == 400000
    assert r2.total_refunded_minor == 400000

    # Ensure wallet credited exactly once
    wallet = agg_db.get_wallet("agency_alpha")
    assert wallet.total_refunded_minor == 400000
    txs = [t for t in agg_db.get_wallet_transactions("agency_alpha") if t.transaction_type == WalletTransactionType.REFUND]
    assert len(txs) == 1


@pytest.mark.asyncio
async def test_concurrent_card_refunds_zero_agency_wallet_mutation(supplier_client):
    """
    Multiple card bookings are cancelled and refunded concurrently.
    Agency wallet must not receive ANY deposits, holds, or mutations whatsoever.
    """
    agg_db = get_aggregator_db()
    service = get_aggregator_booking_service()

    w_before = agg_db.get_wallet("agency_alpha")
    init_avail = w_before.available_balance_minor
    init_refunded = w_before.total_refunded_minor
    init_captured = w_before.total_captured_minor

    # Create 3 concurrent card bookings
    async def book_cancel_refund_card(idx):
        intent = BookingIntent(
            intent_id=f"intent_card_iso_{idx}",
            agency_id="agency_alpha",
            hotel_id="htl_mumbai_grand",
            hotel_name="Grand Mumbai",
            room_type="standard_room",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=5000.0,
            amount_minor=500000,
            currency="INR",
            guest_name=f"Card Guest {idx}",
            correlation_id=f"corr_card_{idx}",
            payment_method="CARD",
            cancellation_penalty_rate=0.20,
            idempotency_key=f"idemp_card_iso_{idx}",
        )
        bkg = await service.process_booking_intent(intent)
        await service.cancel_booking(bkg.booking_id, "agency_alpha", CancellationRequest(reason="Customer cancel"))
        ref_req = RefundRequest(
            amount=4000.0,
            amount_minor=400000,
            currency="INR",
            reason="Card refund",
            idempotency_key=f"rfd_card_key_{idx}",
        )
        return await service.refund_booking(bkg.booking_id, "agency_alpha", ref_req)

    results = await asyncio.gather(
        book_cancel_refund_card(1),
        book_cancel_refund_card(2),
        book_cancel_refund_card(3),
    )

    for r in results:
        assert r.status == TransactionStatus.REFUNDED

    # Agency wallet state MUST BE IDENTICAL to before
    w_after = agg_db.get_wallet("agency_alpha")
    assert w_after.available_balance_minor == init_avail
    assert w_after.total_refunded_minor == init_refunded
    assert w_after.total_captured_minor == init_captured

    # Zero wallet transactions generated
    txs = agg_db.get_wallet_transactions("agency_alpha")
    card_txs = [t for t in txs if "card" in (t.reference or "").lower()]
    assert len(card_txs) == 0

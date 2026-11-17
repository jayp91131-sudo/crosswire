"""
Milestone 1 Completion Verification Suite:
Testing Priorities 1 through 6:
1. Successful booking and payment.
2. Payment failure.
3. Valid cancellation.
4. Full and partial refunds.
5. Refund exceeding eligible amount.
6. Duplicate refund operation (idempotent replay and payload conflict).
7. Agency isolation (cancellation, refunds, and financial ledger).
8. Concurrent financial operations (database uniqueness & ledger deduplication).
9. Existing timeout vulnerable mode with payment lifecycle.
10. Existing timeout fixed mode with payment lifecycle.
"""

import asyncio
import pytest
import httpx
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    BookingIntent,
    QuoteRequest,
    HoldRequest,
    TransactionStatus,
    AggregatorMode,
    SupplierMode,
    LedgerEntryType,
    LedgerDirection,
)
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.supplier.supplier_service import get_supplier_service
from engine.invariant_engine import get_invariant_engine


@pytest.mark.asyncio
async def test_successful_booking_and_payment(agency_client: AgencyClient):
    """
    Priority 1 & 2:
    Tests the full positive lifecycle:
    SEARCH/QUOTE -> HOLD -> BOOKING -> PAYMENT -> SUPPLIER CONFIRMATION -> VOUCHER.
    Verifies:
    1. Quote and hold persistence.
    2. Payment captured and ledger debited.
    3. Supplier reservation confirmed.
    4. Voucher issued.
    """
    agg_db = get_aggregator_db()

    # 1. Quote
    quote_req = QuoteRequest(
        hotel_id="htl_mumbai_grand",
        room_type="deluxe_suite",
        check_in="2026-12-15",
        check_out="2026-12-18",
        guest_name="Aarav Sharma",
        amount=15000.0,
        currency="INR",
    )
    quote = await agency_client.create_quote(quote_req)
    assert quote.status == TransactionStatus.QUOTED
    assert quote.amount == 15000.0

    # 2. Hold
    hold = await agency_client.create_hold(HoldRequest(quote_id=quote.quote_id, hold_duration_seconds=300))
    assert hold.status == TransactionStatus.HELD

    # 3. Booking Intent
    intent = agency_client.create_synthetic_intent(
        intent_id="INTENT-LIFE-01",
        amount=15000.0,
        cancellation_penalty_rate=0.20,
    )
    resp = await agency_client.submit_booking(intent)
    assert resp.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert resp.primary_supplier_reservation_id is not None
    assert resp.payment_id is not None

    # 4. Check Booking Record & Voucher
    booking = await agency_client.get_booking_status(resp.booking_id)
    assert booking.payment_id == resp.payment_id
    assert booking.amount == 15000.0
    assert booking.cancellation_penalty == 3000.0
    assert booking.refundable_entitlement == 12000.0

    # Issue Voucher
    voucher_booking = await agency_client.issue_voucher(resp.booking_id)
    assert voucher_booking.status == TransactionStatus.VOUCHER_ISSUED
    assert voucher_booking.voucher_id == f"VOUCHER-{resp.booking_id}"

    # 5. Check Financial Ledger
    ledger = await agency_client.get_ledger(resp.booking_id)
    assert len(ledger) >= 1
    capture_entry = next(e for e in ledger if e.entry_type == LedgerEntryType.PAYMENT_CAPTURE)
    assert capture_entry.direction == LedgerDirection.DEBIT
    assert capture_entry.amount == 15000.0


@pytest.mark.asyncio
async def test_payment_failure_halts_booking_without_supplier_commitment(agency_client: AgencyClient):
    """
    Priority 1 & 6.2:
    When payment authorization fails:
    1. Booking transitions to PAYMENT_FAILED.
    2. Supplier is NEVER invoked (0 reservations created).
    3. Invariant: Voucher cannot be issued.
    """
    sup_db = get_supplier_db()
    intent = agency_client.create_synthetic_intent(
        intent_id="INTENT-PAY-FAIL-01",
        simulate_payment_failure=True,
    )

    resp = await agency_client.submit_booking(intent)
    assert resp.status == TransactionStatus.PAYMENT_FAILED
    assert resp.primary_supplier_reservation_id is None

    # Supplier database must have 0 reservations for this intent
    sup_reservations = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(sup_reservations) == 0

    # Attempting to issue a voucher must be rejected with 400
    with pytest.raises(RuntimeError) as exc_info:
        await agency_client.issue_voucher(resp.booking_id)
    assert "400" in str(exc_info.value)


@pytest.mark.asyncio
async def test_valid_cancellation(agency_client: AgencyClient):
    """
    Priority 2 & 4:
    Valid booking cancellation:
    1. Moves status to CANCELLED.
    2. Cancellation penalty (20% of 15,000 = 3,000 INR) is posted to ledger under PENALTY.
    3. Refundable entitlement (12,000 INR) is explicitly computed.
    """
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-CANCEL-01", amount=15000.0)
    init_res = await agency_client.submit_booking(intent)

    cancel_res = await agency_client.cancel_booking(init_res.booking_id, reason="Customer schedule changed")
    assert cancel_res.status == TransactionStatus.CANCELLED
    assert cancel_res.penalty_amount == 3000.0
    assert cancel_res.refundable_entitlement == 12000.0

    # Verify ledger entry for penalty
    ledger = await agency_client.get_ledger(init_res.booking_id)
    penalty_entry = next((e for e in ledger if e.entry_type == LedgerEntryType.PENALTY), None)
    assert penalty_entry is not None
    assert penalty_entry.amount == 3000.0


@pytest.mark.asyncio
async def test_full_and_partial_refunds(agency_client: AgencyClient):
    """
    Priority 4 & 6.4:
    Tests bounded partial refunds and progression to full refund:
    1. Booking 15,000 INR with 3,000 INR penalty -> 12,000 INR refundable entitlement.
    2. Partial refund 1: 5,000 INR -> Status PARTIALLY_REFUNDED, remaining 7,000 INR.
    3. Partial refund 2: 7,000 INR -> Status REFUNDED, remaining 0 INR.
    4. Cumulative refunds == refundable entitlement.
    """
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-REFUND-01", amount=15000.0)
    init_res = await agency_client.submit_booking(intent)
    await agency_client.cancel_booking(init_res.booking_id)

    # 1. Partial refund 1: 5,000 INR
    ref_1 = await agency_client.refund_booking(init_res.booking_id, amount=5000.0, reason="Partial refund 1")
    assert ref_1.status == TransactionStatus.PARTIALLY_REFUNDED
    assert ref_1.amount == 5000.0
    assert ref_1.total_refunded == 5000.0
    assert ref_1.remaining_entitlement == 7000.0

    # 2. Partial refund 2: 7,000 INR (exhausts entitlement)
    ref_2 = await agency_client.refund_booking(init_res.booking_id, amount=7000.0, reason="Final balance refund")
    assert ref_2.status == TransactionStatus.REFUNDED
    assert ref_2.amount == 7000.0
    assert ref_2.total_refunded == 12000.0
    assert ref_2.remaining_entitlement == 0.0

    # 3. Verify ledger entries
    ledger = await agency_client.get_ledger(init_res.booking_id)
    refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 2
    assert sum(e.amount for e in refund_entries) == 12000.0


@pytest.mark.asyncio
async def test_refund_exceeding_eligible_amount_rejected(agency_client: AgencyClient):
    """
    Priority 2, 4 & 6.5 (Invariant 5.5):
    cumulative_refunds(B, c) <= refundable_entitlement(B, c).
    When an agency requests a refund of 15,000 INR where entitlement is 12,000 INR:
    Request is rejected (HTTP 400 / ExcessiveRefundError).
    """
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-EXCESS-01", amount=15000.0)
    init_res = await agency_client.submit_booking(intent)
    await agency_client.cancel_booking(init_res.booking_id)

    with pytest.raises(RuntimeError) as exc_info:
        await agency_client.refund_booking(init_res.booking_id, amount=15000.0)
    assert "400" in str(exc_info.value)
    assert "exceeds" in str(exc_info.value).lower()

    # Verify no refund was recorded
    refunds = await agency_client.get_refunds(init_res.booking_id)
    assert len(refunds) == 0


@pytest.mark.asyncio
async def test_duplicate_refund_operation_idempotency_and_conflict(agency_client: AgencyClient):
    """
    Priority 5 & 6.6:
    1. Replaying an identical refund request with the same idempotency key returns the original result
       without posting a second financial effect (count == 1).
    2. Reusing the same idempotency key with a different amount produces HTTP 409 Conflict.
    """
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-IDEMP-RFD-01", amount=15000.0)
    init_res = await agency_client.submit_booking(intent)
    await agency_client.cancel_booking(init_res.booking_id)

    key = "IDEMP-REFUND-KEY-999"

    # Call 1: Original refund
    res_1 = await agency_client.refund_booking(init_res.booking_id, amount=4000.0, idempotency_key=key)
    assert res_1.amount == 4000.0

    # Call 2: Exact replay with identical key and amount
    res_2 = await agency_client.refund_booking(init_res.booking_id, amount=4000.0, idempotency_key=key)
    assert res_2.refund_id == res_1.refund_id
    assert res_2.amount == 4000.0

    # Check ledger: exactly 1 refund entry posted
    ledger = await agency_client.get_ledger(init_res.booking_id)
    refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 1

    # Call 3: Key reuse with mismatched payload/amount -> Must return 409 Conflict
    with pytest.raises(RuntimeError) as exc_info:
        await agency_client.refund_booking(init_res.booking_id, amount=6000.0, idempotency_key=key)
    assert "409" in str(exc_info.value)


@pytest.mark.asyncio
async def test_agency_isolation(agency_client: AgencyClient, aggregator_transport, supplier_client):
    """
    Priority 5 & 6.7:
    Cross-agency isolation across booking, cancellation, refund, and ledger:
    Agency Beta cannot cancel, refund, or view Agency Alpha's booking or financial ledger.
    """
    # 1. Agency Alpha creates and confirms a booking
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-ISOLATE-01")
    init_res = await agency_client.submit_booking(intent)
    booking_id = init_res.booking_id

    # 2. Client for Agency Beta
    beta_client = AgencyClient(
        agency_id="agency_beta",
        api_key="secret_beta_key_88",
        aggregator_base_url="http://travel-aggregator",
        transport=aggregator_transport,
    )

    # Beta attempts to read Alpha's booking -> 404
    with pytest.raises(RuntimeError) as exc:
        await beta_client.get_booking_status(booking_id)
    assert "404" in str(exc.value)

    # Beta attempts to cancel Alpha's booking -> 404
    with pytest.raises(RuntimeError) as exc:
        await beta_client.cancel_booking(booking_id)
    assert "404" in str(exc.value)

    # Beta attempts to refund Alpha's booking -> 404
    with pytest.raises(RuntimeError) as exc:
        await beta_client.refund_booking(booking_id, amount=1000.0)
    assert "404" in str(exc.value)

    # Beta attempts to inspect Alpha's ledger -> 404
    with pytest.raises(RuntimeError) as exc:
        await beta_client.get_ledger(booking_id)
    assert "404" in str(exc.value)


@pytest.mark.asyncio
async def test_concurrent_financial_operations(agency_client: AgencyClient):
    """
    Priority 5 & 6.8:
    Concurrent race condition: 5 parallel refund operations race with the exact same idempotency key.
    Database-level uniqueness and transaction handling must guarantee:
    1. Exactly 1 refund record persisted in SQLite.
    2. Exactly 1 ledger entry posted.
    3. All concurrent coroutines resolve safely without IntegrityError crashes.
    """
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-CONCUR-FIN-01", amount=15000.0)
    init_res = await agency_client.submit_booking(intent)
    await agency_client.cancel_booking(init_res.booking_id)

    key = "IDEMP-CONCUR-FIN-KEY-555"

    results = await asyncio.gather(
        agency_client.refund_booking(init_res.booking_id, amount=3000.0, idempotency_key=key),
        agency_client.refund_booking(init_res.booking_id, amount=3000.0, idempotency_key=key),
        agency_client.refund_booking(init_res.booking_id, amount=3000.0, idempotency_key=key),
        agency_client.refund_booking(init_res.booking_id, amount=3000.0, idempotency_key=key),
        agency_client.refund_booking(init_res.booking_id, amount=3000.0, idempotency_key=key),
    )

    # All callers must receive the exact same refund ID
    first_id = results[0].refund_id
    for r in results:
        assert r.refund_id == first_id

    # Ledger check: exactly 1 refund entry was posted
    ledger = await agency_client.get_ledger(init_res.booking_id)
    refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 1
    assert refund_entries[0].amount == 3000.0


@pytest.mark.asyncio
async def test_existing_timeout_vulnerable_mode_with_payment(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    Priority 6.9:
    Verifies that vulnerable blind retry behavior under HTTP timeout remains intact
    while executing the full payment lifecycle:
    1. Payment is captured and recorded.
    2. Supplier commit-then-timeout occurs.
    3. Aggregator blindly retries -> creates 2 distinct supplier reservations.
    4. Invariant Engine detects violation.
    """
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()
    inv_engine = get_invariant_engine()

    agg_service.set_mode(AggregatorMode.VULNERABLE)
    sup_service.configure(
        mode=SupplierMode.VULNERABLE,
        simulate_timeout=True,
        timeout_delay_seconds=0.35,
    )

    response = await agency_client.submit_booking(synthetic_intent)
    assert response.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert response.retry_count == 1
    assert response.payment_id is not None

    # Check duplicate supplier reservations
    reservations = sup_db.get_reservations_by_intent(synthetic_intent.intent_id)
    assert len(reservations) == 2

    # Invariant Oracle must detect violation
    finding = inv_engine.check_booking_intent_uniqueness(synthetic_intent.intent_id)
    assert finding.passed is False
    assert "CWE-675" in finding.cwe


@pytest.mark.asyncio
async def test_existing_timeout_fixed_mode_with_payment(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    Priority 6.10:
    Verifies that fixed idempotent recovery behavior under HTTP timeout remains intact
    while executing the full payment lifecycle:
    1. Payment is captured.
    2. Supplier commit-then-timeout occurs.
    3. Aggregator recovers existing reservation via reference lookup or idempotency key.
    4. Exactly 1 supplier reservation exists.
    5. Invariant Engine passes cleanly.
    """
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()
    inv_engine = get_invariant_engine()

    agg_service.set_mode(AggregatorMode.FIXED)
    sup_service.configure(
        mode=SupplierMode.IDEMPOTENT,
        simulate_timeout=True,
        timeout_delay_seconds=0.35,
    )

    response = await agency_client.submit_booking(synthetic_intent)
    assert response.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert response.payment_id is not None

    reservations = sup_db.get_reservations_by_intent(synthetic_intent.intent_id)
    assert len(reservations) == 1

    # Invariant Oracle must pass
    finding = inv_engine.check_booking_intent_uniqueness(synthetic_intent.intent_id)
    assert finding.passed is True

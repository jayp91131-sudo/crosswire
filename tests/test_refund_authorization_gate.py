"""
Crosswire 2.0 - Final Refund-Authorization Security Gate Test Suite.

Verifies:
1. Distinct webhook events referencing the same refund instruction cannot cause
   cumulative financial credits exceeding the authorized amount.
2. Concurrent delivery of distinct events against the same refund authorization
   atomically succeeds exactly once and rejects the exceeding delivery.
3. Legitimate partial refund sequence: sequential tranches transition state from
   AUTHORIZED -> PARTIALLY_REFUNDED -> COMPLETED and reject subsequent deliveries.
4. Multiple distinct refund instructions against the same booking cannot exceed
   the total booking refundable entitlement.
5. Concurrent delivery of distinct refund instructions exceeding entitlement
   atomically accepts one and rejects the other.
6. Booking state eligibility matrix:
   - CANCELLED: Eligible.
   - PARTIALLY_REFUNDED: Eligible.
   - SUPPLIER_UNKNOWN: Eligible (when payment captured).
   - RECONCILIATION_REQUIRED: Eligible (when payment captured).
   - FAILED: Eligible (when payment captured).
   - PAYMENT_FAILED: Ineligible (Rejected HTTP 400).
   - Uncaptured payment: Ineligible (Rejected HTTP 400).
7. Localhost TCP socket concurrent delivery over real network sockets.
8. Invariant Engine detects vulnerable cumulative over-crediting with structured evidence.
"""

import asyncio
import subprocess
import sys
import time
from datetime import datetime
import httpx
import pytest

from target_platform.agency_client import AgencyClient
from target_platform.aggregator.database import get_aggregator_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.payment.payment_service import get_payment_service
from target_platform.models import (
    TransactionStatus,
    PaymentStatus,
    AggregatorMode,
    WebhookPayload,
    LedgerEntryType,
    LedgerDirection,
    get_provider_registry,
)
from engine.invariant_engine import get_invariant_engine


async def create_booking_with_entitlement(
    agency_client: AgencyClient,
    intent_id: str,
    total_amount: float = 12500.0,
    penalty_rate: float = 0.20,
):
    """
    Creates a confirmed and cancelled booking with a known entitlement.
    Default: 12,500 total, 20% penalty = 2,500 penalty, 10,000 entitlement (1,000,000 minor).
    """
    intent = agency_client.create_synthetic_intent(
        intent_id=intent_id,
        amount=total_amount,
        cancellation_penalty_rate=penalty_rate,
    )
    init_res = await agency_client.submit_booking(intent)
    assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED

    cancel_res = await agency_client.cancel_booking(init_res.booking_id)
    assert cancel_res.status == TransactionStatus.CANCELLED

    return init_res.booking_id, cancel_res.refundable_entitlement_minor


# ---------------------------------------------------------------------------
# Scenario 1: Distinct Events Referencing Same Refund ID (Sequential)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_distinct_events_same_refund_id_sequential(agency_client: AgencyClient):
    """
    Scenario 1:
    Create a booking with ₹10,000 entitlement (1,000,000 minor).
    Authorize ONE refund instruction 'RFD-GATE-01' for ₹10,000.
    Deliver distinct webhook events evt_101 (₹7,000) and evt_102 (₹7,000) referencing RFD-GATE-01.
    Verify:
    - First event succeeds (HTTP 200).
    - Second event is rejected (HTTP 400).
    - Cumulative ledger credits do not exceed authorized ₹10,000 (strictly ₹7,000).
    """
    booking_id, entitlement_minor = await create_booking_with_entitlement(
        agency_client, "INTENT-GATE-01", total_amount=12500.0
    )
    assert entitlement_minor == 1000000  # ₹10,000

    rfd_id = "RFD-GATE-01"
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-01",
    )

    pay_service = get_payment_service()
    agg_db = get_aggregator_db()
    inv_engine = get_invariant_engine()

    # Event 101: ₹7,000 against ₹10,000 authorized
    payload_101 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-01",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_gate_101",
    )
    resp_101 = await agency_client.deliver_webhook(payload_101)
    assert resp_101.status_code == 200
    assert resp_101.json()["status"] == "PROCESSED"

    # Event 102: ₹7,000 against same refund ID (remaining is only ₹3,000)
    payload_102 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-01",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_gate_102",
    )
    resp_102 = await agency_client.deliver_webhook(payload_102)
    assert resp_102.status_code == 400
    assert "exceeds authorized" in resp_102.text.lower()

    # Verification: Ground Truth Oracle
    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 700000

    rfd = agg_db.get_refund_record(rfd_id)
    assert rfd.authorized_amount_minor == 1000000
    assert rfd.processed_amount_minor == 700000
    assert rfd.status == "PARTIALLY_REFUNDED"

    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_credits = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND and e.direction == LedgerDirection.CREDIT]
    assert len(refund_credits) == 1
    assert refund_credits[0].amount_minor == 700000

    # Crosswire Invariants
    inv_bounds = inv_engine.check_refund_boundedness(booking_id)
    assert inv_bounds.passed is True

    inv_cons = inv_engine.check_financial_consistency(booking_id)
    assert inv_cons.passed is True


# ---------------------------------------------------------------------------
# Scenario 2: Concurrent Deliveries of Distinct Events against Same Refund ID
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_distinct_events_same_refund_id_concurrent(agency_client: AgencyClient):
    """
    Scenario 2:
    Deliver two distinct events concurrently for ₹7,000 each against the same ₹10,000 authorization.
    Verify:
    - Exactly one succeeds (200 OK).
    - Exactly one fails (400 Bad Request).
    - Ledger records exactly one credit (₹7,000).
    - Invariants 5.5 and 5.6 hold.
    """
    booking_id, entitlement_minor = await create_booking_with_entitlement(
        agency_client, "INTENT-GATE-02", total_amount=12500.0
    )
    assert entitlement_minor == 1000000

    rfd_id = "RFD-GATE-02"
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-02",
    )

    pay_service = get_payment_service()
    agg_db = get_aggregator_db()
    inv_engine = get_invariant_engine()

    p_a = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-02",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_conc_same_A",
    )
    p_b = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-02",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_conc_same_B",
    )

    r_a, r_b = await asyncio.gather(
        agency_client.deliver_webhook(p_a),
        agency_client.deliver_webhook(p_b),
    )

    statuses = sorted([r_a.status_code, r_b.status_code])
    assert statuses == [200, 400], f"Expected exactly [200, 400], got {statuses}"

    # Database Verification
    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 700000

    rfd = agg_db.get_refund_record(rfd_id)
    assert rfd.processed_amount_minor == 700000

    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_credits = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND and e.direction == LedgerDirection.CREDIT]
    assert len(refund_credits) == 1
    assert refund_credits[0].amount_minor == 700000

    # Invariants
    assert inv_engine.check_refund_boundedness(booking_id).passed is True
    assert inv_engine.check_financial_consistency(booking_id).passed is True


# ---------------------------------------------------------------------------
# Scenario 3: Legitimate Partial Refund Sequence (Tranches & Exhaustion)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legitimate_partial_refund_tranches_and_exhaustion(agency_client: AgencyClient):
    """
    Scenario 3:
    Authorize refund RFD-201 for ₹10,000.
    Deliver evt_201 (₹3,000), evt_202 (₹4,000), evt_203 (₹3,000) sequentially.
    Verify:
    - All three succeed (200 OK).
    - Ledger has exactly three credits totalling ₹10,000.
    - Status transitions AUTHORIZED -> PARTIALLY_REFUNDED -> COMPLETED.
    - Subsequent delivery of evt_204 (₹1,000) is rejected (HTTP 400).
    """
    booking_id, entitlement_minor = await create_booking_with_entitlement(
        agency_client, "INTENT-GATE-03", total_amount=12500.0
    )
    assert entitlement_minor == 1000000

    rfd_id = "RFD-GATE-03"
    auth_rec = await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-03",
    )
    assert auth_rec.status == "AUTHORIZED"
    assert auth_rec.authorized_amount_minor == 1000000
    assert auth_rec.processed_amount_minor == 0

    pay_service = get_payment_service()
    agg_db = get_aggregator_db()
    inv_engine = get_invariant_engine()

    # Tranche 1: ₹3,000
    p1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-03",
        refund_id=rfd_id,
        amount_minor=300000,
        currency="INR",
        event_id="evt_tranche_201",
    )
    r1 = await agency_client.deliver_webhook(p1)
    assert r1.status_code == 200

    rfd_after_1 = agg_db.get_refund_record(rfd_id)
    assert rfd_after_1.status == "PARTIALLY_REFUNDED"
    assert rfd_after_1.processed_amount_minor == 300000
    assert inv_engine.check_financial_consistency(booking_id).passed is True

    # Tranche 2: ₹4,000
    p2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-03",
        refund_id=rfd_id,
        amount_minor=400000,
        currency="INR",
        event_id="evt_tranche_202",
    )
    r2 = await agency_client.deliver_webhook(p2)
    assert r2.status_code == 200

    rfd_after_2 = agg_db.get_refund_record(rfd_id)
    assert rfd_after_2.status == "PARTIALLY_REFUNDED"
    assert rfd_after_2.processed_amount_minor == 700000
    assert inv_engine.check_financial_consistency(booking_id).passed is True

    # Tranche 3: ₹3,000 (completes the ₹10,000 authorization)
    p3 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-03",
        refund_id=rfd_id,
        amount_minor=300000,
        currency="INR",
        event_id="evt_tranche_203",
    )
    r3 = await agency_client.deliver_webhook(p3)
    assert r3.status_code == 200

    rfd_after_3 = agg_db.get_refund_record(rfd_id)
    assert rfd_after_3.status == "COMPLETED"
    assert rfd_after_3.processed_amount_minor == 1000000

    booking_after_3 = agg_db.get_booking_record(booking_id)
    assert booking_after_3.status == TransactionStatus.REFUNDED
    assert booking_after_3.total_refunded_minor == 1000000

    # Tranche 4: ₹1,000 (Exceeds completed authorization)
    p4 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-03",
        refund_id=rfd_id,
        amount_minor=100000,
        currency="INR",
        event_id="evt_tranche_204",
    )
    r4 = await agency_client.deliver_webhook(p4)
    assert r4.status_code == 400

    # Verification: Exactly 3 ledger entries, strictly ₹10,000
    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_credits = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND and e.direction == LedgerDirection.CREDIT]
    assert len(refund_credits) == 3
    assert sum(e.amount_minor for e in refund_credits) == 1000000

    # Final Invariants
    assert inv_engine.check_refund_boundedness(booking_id).passed is True
    assert inv_engine.check_financial_consistency(booking_id).passed is True


# ---------------------------------------------------------------------------
# Scenario 4: Distinct Refund IDs within and Exceeding Entitlement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_distinct_refund_ids_within_and_exceeding_entitlement(agency_client: AgencyClient):
    """
    Scenario 4:
    Create booking with ₹15,000 entitlement (1,500,000 minor).
    Authorize RFD-A for ₹8,000 and RFD-B for ₹6,000 (total ₹14,000 <= ₹15,000).
    Deliver both: both succeed. Total credited = ₹14,000.
    Then authorize RFD-C for ₹2,000.
    Deliver RFD-C: rejected (HTTP 400) because 14,000 + 2,000 = 16,000 > 15,000 available.
    """
    booking_id, entitlement_minor = await create_booking_with_entitlement(
        agency_client, "INTENT-GATE-04", total_amount=18750.0
    )
    assert entitlement_minor == 1500000  # ₹15,000

    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-DIST-A",
        amount_minor=800000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-04",
    )
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-DIST-B",
        amount_minor=600000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-04",
    )

    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    pa = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-04",
        refund_id="RFD-DIST-A",
        amount_minor=800000,
        currency="INR",
        event_id="evt_dist_A",
    )
    pb = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-04",
        refund_id="RFD-DIST-B",
        amount_minor=600000,
        currency="INR",
        event_id="evt_dist_B",
    )

    ra = await agency_client.deliver_webhook(pa)
    rb = await agency_client.deliver_webhook(pb)
    assert ra.status_code == 200
    assert rb.status_code == 200

    b_after_ab = agg_db.get_booking_record(booking_id)
    assert b_after_ab.total_refunded_minor == 1400000

    # Now attempt RFD-C for ₹2,000 against remaining ₹1,000
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-DIST-C",
        amount_minor=200000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-04",
    )
    pc = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-04",
        refund_id="RFD-DIST-C",
        amount_minor=200000,
        currency="INR",
        event_id="evt_dist_C",
    )
    rc = await agency_client.deliver_webhook(pc)
    assert rc.status_code == 400
    assert "exceeds remaining refundable entitlement" in rc.text.lower()

    b_final = agg_db.get_booking_record(booking_id)
    assert b_final.total_refunded_minor == 1400000


# ---------------------------------------------------------------------------
# Scenario 5: Concurrent Distinct Refund IDs Exceeding Entitlement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_distinct_refund_ids_exceeding_entitlement(agency_client: AgencyClient):
    """
    Scenario 5:
    Booking with ₹15,000 entitlement (1,500,000 minor).
    Authorize RFD-CONC-1 for ₹10,000 and RFD-CONC-2 for ₹10,000.
    Concurrently deliver webhooks for both.
    Verify:
    - Exactly one succeeds (200 OK).
    - Exactly one is rejected (400 Bad Request).
    - Total credited is strictly ₹10,000 <= ₹15,000.
    """
    booking_id, entitlement_minor = await create_booking_with_entitlement(
        agency_client, "INTENT-GATE-05", total_amount=18750.0
    )
    assert entitlement_minor == 1500000

    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-CONC-1",
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-05",
    )
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-CONC-2",
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-05",
    )

    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    p1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-05",
        refund_id="RFD-CONC-1",
        amount_minor=1000000,
        currency="INR",
        event_id="evt_conc_dist_1",
    )
    p2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-05",
        refund_id="RFD-CONC-2",
        amount_minor=1000000,
        currency="INR",
        event_id="evt_conc_dist_2",
    )

    r1, r2 = await asyncio.gather(
        agency_client.deliver_webhook(p1),
        agency_client.deliver_webhook(p2),
    )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 400], f"Expected exactly [200, 400], got {statuses}"

    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 1000000


# ---------------------------------------------------------------------------
# Scenario 6: Booking-State Eligibility Audit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "booking_status,should_succeed,expected_err",
    [
        (TransactionStatus.CANCELLED.value, True, None),
        (TransactionStatus.PARTIALLY_REFUNDED.value, True, None),
        (TransactionStatus.SUPPLIER_UNKNOWN.value, True, None),
        (TransactionStatus.RECONCILIATION_REQUIRED.value, True, None),
        (TransactionStatus.FAILED.value, True, None),
        (TransactionStatus.PAYMENT_FAILED.value, False, "not eligible for refund"),
    ],
)
async def test_booking_state_eligibility(
    agency_client: AgencyClient,
    booking_status: str,
    should_succeed: bool,
    expected_err: str,
):
    """
    Scenario 6:
    Tests webhook refund processing across all defined booking statuses.
    Ensures CANCELLED, PARTIALLY_REFUNDED, SUPPLIER_UNKNOWN, RECONCILIATION_REQUIRED,
    and FAILED are eligible, while PAYMENT_FAILED is strictly rejected.
    """
    intent_id = f"INTENT-STATE-{booking_status}"
    booking_id, _ = await create_booking_with_entitlement(
        agency_client, intent_id, total_amount=12500.0
    )
    agg_db = get_aggregator_db()

    # Manually transition booking record to target state for testing
    with agg_db._connection() as conn:
        conn.execute(
            "UPDATE booking_records SET status = ? WHERE booking_id = ?",
            (booking_status, booking_id),
        )

    rfd_id = f"RFD-STATE-{booking_status}"
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=500000,
        currency="INR",
        payment_id=f"PAY-{intent_id}",
    )

    pay_service = get_payment_service()
    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id=f"PAY-{intent_id}",
        refund_id=rfd_id,
        amount_minor=500000,
        currency="INR",
        event_id=f"evt_state_{booking_status}",
    )

    resp = await agency_client.deliver_webhook(payload)
    if should_succeed:
        assert resp.status_code == 200
        assert resp.json()["status"] == "PROCESSED"
    else:
        assert resp.status_code == 400
        if expected_err:
            assert expected_err in resp.text.lower()


@pytest.mark.asyncio
async def test_uncaptured_payment_rejected(agency_client: AgencyClient):
    """
    Scenario 6b:
    If a booking has an uncaptured payment transaction (e.g. status == FAILED),
    refunding must be rejected with HTTP 400 (PaymentMismatchError).
    """
    booking_id, _ = await create_booking_with_entitlement(
        agency_client, "INTENT-UNCAPTURED-PAY", total_amount=12500.0
    )
    agg_db = get_aggregator_db()

    # Mark payment as FAILED
    with agg_db._connection() as conn:
        conn.execute(
            "UPDATE payment_transactions SET status = 'FAILED' WHERE payment_id = 'PAY-INTENT-UNCAPTURED-PAY'"
        )

    rfd_id = "RFD-UNCAPTURED-PAY"
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=500000,
        currency="INR",
        payment_id="PAY-INTENT-UNCAPTURED-PAY",
    )

    pay_service = get_payment_service()
    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-UNCAPTURED-PAY",
        refund_id=rfd_id,
        amount_minor=500000,
        currency="INR",
        event_id="evt_uncaptured_pay",
    )

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "not captured" in resp.text.lower()


# ---------------------------------------------------------------------------
# Scenario 7: Localhost TCP Socket Concurrency
# ---------------------------------------------------------------------------
TCP_GATE_PORT = 8013
TCP_GATE_URL = f"http://127.0.0.1:{TCP_GATE_PORT}"


@pytest.fixture(scope="module")
def tcp_aggregator_server_8013():
    """Starts Uvicorn on localhost port 8013 for dedicated security gate TCP concurrency tests."""
    base_cmd = [sys.executable, "-m", "uvicorn"]
    proc = subprocess.Popen(
        base_cmd + [
            "target_platform.aggregator.app:aggregator_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(TCP_GATE_PORT),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 10.0
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{TCP_GATE_URL}/", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.1)

    if not ready:
        proc.kill()
        pytest.fail(f"TCP server on port {TCP_GATE_PORT} failed to start")

    yield TCP_GATE_URL

    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_tcp_socket_concurrent_distinct_events(tcp_aggregator_server_8013):
    """
    Scenario 7:
    Fires two distinct webhook deliveries concurrently over localhost TCP sockets (port 8013)
    against the same refund authorization.
    Verifies exactly one 200 OK and exactly one 400 Bad Request.
    """
    from target_platform.models import BookingIntent, AggregatorBookingRecord, PaymentTransactionRecord

    agg_db = get_aggregator_db()
    agg_db.reset()
    pay_service = get_payment_service()

    booking_id = "TBOH-TCP-GATE-01"
    intent_id = "INTENT-TCP-GATE-01"

    # Seed intent & cancelled booking with captured payment
    intent = BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Synthetic Hotel Mumbai",
        room_type="deluxe_suite",
        check_in="2026-12-15",
        check_out="2026-12-18",
        amount=12500.0,
        amount_minor=1250000,
        currency="INR",
        guest_name="Aarav Sharma",
        correlation_id="corr_tcp_gate_01",
        cancellation_penalty_rate=0.20,
    )
    agg_db.save_booking_intent(intent)

    booking = AggregatorBookingRecord(
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.CANCELLED,
        amount=12500.0,
        amount_minor=1250000,
        currency="INR",
        cancellation_penalty=2500.0,
        cancellation_penalty_minor=250000,
        refundable_entitlement=10000.0,
        refundable_entitlement_minor=1000000,
        total_refunded=0.0,
        total_refunded_minor=0,
        correlation_id="corr_tcp_gate_01",
        payment_id="PAY-INTENT-TCP-GATE-01",
    )
    agg_db.save_booking_record(booking)

    pay_tx = PaymentTransactionRecord(
        payment_id="PAY-INTENT-TCP-GATE-01",
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        amount=12500.0,
        amount_minor=1250000,
        currency="INR",
        status=PaymentStatus.CAPTURED,
        transaction_reference="TXN-PAY-TCP-GATE-01",
        idempotency_key="PAY-AUTH-TCP-GATE-01",
        provider="mock_pay_provider",
    )
    agg_db.save_payment_transaction(pay_tx)

    rfd_id = "RFD-TCP-GATE-01"
    agg_db.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-TCP-GATE-01",
    )

    # Generate two distinct signed webhooks for ₹7,000 each against the ₹10,000 authorization
    p1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-TCP-GATE-01",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_tcp_gate_A",
    )
    p2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-TCP-GATE-01",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_tcp_gate_B",
    )

    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }

    async with httpx.AsyncClient(base_url=tcp_aggregator_server_8013, headers=headers) as client:
        async def send(payload):
            return await client.post(
                "/api/v1/webhooks/payment",
                content=payload.model_dump_json(),
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": payload.signature,
                },
            )

        r1, r2 = await asyncio.gather(send(p1), send(p2))
        statuses = sorted([r1.status_code, r2.status_code])
        assert statuses == [200, 400], f"Expected exactly [200, 400], got {statuses}"

    # Database Verification
    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 700000


# ---------------------------------------------------------------------------
# Scenario 8: Invariant Engine Detection of Vulnerable Over-Crediting
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vulnerable_mode_allows_cumulative_overcrediting_and_invariant_catches_it(
    agency_client: AgencyClient,
):
    """
    Scenario 8:
    In vulnerable mode, distinct events referencing the same refund ID both succeed,
    causing cumulative refunds to exceed the refundable entitlement.
    Crosswire's Invariant Engine must detect the violation and return structured audit evidence.
    """
    service = get_aggregator_booking_service()
    service.set_webhook_mode(AggregatorMode.VULNERABLE)

    booking_id, entitlement_minor = await create_booking_with_entitlement(
        agency_client, "INTENT-GATE-VULN", total_amount=12500.0
    )
    assert entitlement_minor == 1000000

    rfd_id = "RFD-GATE-VULN"
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=1000000,
        currency="INR",
        payment_id="PAY-INTENT-GATE-VULN",
    )

    pay_service = get_payment_service()
    inv_engine = get_invariant_engine()

    p1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-VULN",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_vuln_gate_1",
    )
    p2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-GATE-VULN",
        refund_id=rfd_id,
        amount_minor=700000,
        currency="INR",
        event_id="evt_vuln_gate_2",
    )

    r1 = await agency_client.deliver_webhook(p1)
    r2 = await agency_client.deliver_webhook(p2)
    assert r1.status_code == 200
    assert r2.status_code == 200  # Both succeed in vulnerable mode!

    # Verify Invariant Engine detects failure
    res_bounds = inv_engine.check_refund_boundedness(booking_id)
    assert res_bounds.passed is False
    assert res_bounds.evidence["total_refunded_minor"] == 1400000
    assert res_bounds.evidence["excess_minor"] == 400000  # 14,000 - 10,000 = 4,000 INR excess
    assert "remediation" in res_bounds.model_dump()

    # Restore fixed mode
    service.set_webhook_mode(AggregatorMode.FIXED)

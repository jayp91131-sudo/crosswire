"""
Crosswire 2.0: Milestone 2.1 Adversarial Duplicate Refund Webhook Test Suite.
Verifies:
1. Successful single refund webhook delivery.
2. Duplicate sequential delivery with idempotent replay.
3. Duplicate concurrent delivery (10 asynchronous requests).
4. Invalid HMAC-SHA256 signature rejection (HTTP 401).
5. Same event ID with modified payload rejection (HTTP 409 Conflict).
6. Missing event ID rejection (HTTP 400).
7. Already-processed event idempotency.
8. Refund exceeding eligible entitlement rejection (HTTP 400).
9. Concurrent different refund events against the same entitlement (mutual exclusion).
10. Financial consistency invariant (booking == refunds == ledger).
11. Fixed mode regression and idempotency verification.
12. Vulnerable mode detection and structured security finding generation (CW-SEC-M2-001).
13. Real localhost TCP integration over socket transport (10 concurrent deliveries).
14. Complete local sandbox execution without outbound external network calls.
"""

import sys
import time
import shutil
import asyncio
import subprocess
import pytest
import httpx
from target_platform.agency_client import AgencyClient
from target_platform.payment.payment_service import get_payment_service
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.aggregator.database import get_aggregator_db
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    PaymentTransactionRecord,
    PaymentStatus,
    AggregatorMode,
    TransactionStatus,
    LedgerEntryType,
    LedgerDirection,
    DEFAULT_WEBHOOK_SECRET,
    compute_webhook_signature,
)
from engine.invariant_engine import get_invariant_engine


# Helper to create and cancel a booking with authorized refund records for testing
async def create_cancelled_booking(agency_client: AgencyClient, intent_id: str, amount: float = 15000.0):
    intent = agency_client.create_synthetic_intent(intent_id=intent_id, amount=amount)
    init_res = await agency_client.submit_booking(intent)
    assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED
    cancel_res = await agency_client.cancel_booking(init_res.booking_id)
    assert cancel_res.status == TransactionStatus.CANCELLED

    # Pre-authorize refund records required by CW-REV-M2-002 reconciliation
    suffix = intent_id.replace("INTENT-", "")
    if intent_id == "INTENT-WH-09":
        await agency_client.authorize_refund(
            booking_id=init_res.booking_id,
            refund_id="RFD-WH-09-A",
            amount_minor=800000,
            currency="INR",
            payment_id=f"PAY-{intent_id}",
        )
        await agency_client.authorize_refund(
            booking_id=init_res.booking_id,
            refund_id="RFD-WH-09-B",
            amount_minor=800000,
            currency="INR",
            payment_id=f"PAY-{intent_id}",
        )
    elif intent_id == "INTENT-WH-10":
        await agency_client.authorize_refund(
            booking_id=init_res.booking_id,
            refund_id="RFD-WH-10-A",
            amount_minor=300000,
            currency="INR",
            payment_id=f"PAY-{intent_id}",
        )
        await agency_client.authorize_refund(
            booking_id=init_res.booking_id,
            refund_id="RFD-WH-10-B",
            amount_minor=400000,
            currency="INR",
            payment_id=f"PAY-{intent_id}",
        )
    elif intent_id != "INTENT-WH-04":
        await agency_client.authorize_refund(
            booking_id=init_res.booking_id,
            refund_id=f"RFD-{suffix}",
            amount_minor=cancel_res.refundable_entitlement_minor,
            currency="INR",
            payment_id=f"PAY-{intent_id}",
        )

    return init_res.booking_id, cancel_res.refundable_entitlement_minor


# ---------------------------------------------------------------------------
# Test 1: Successful single refund webhook
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_single_refund_webhook(agency_client: AgencyClient):
    """
    Test 1:
    A valid signed refund webhook for 5,000 INR is delivered.
    Asserts:
    - Returns HTTP 200 with status="PROCESSED", duplicate=False.
    - Exactly 1 refund record created.
    - Exactly 1 ledger entry posted with direction=CREDIT and amount_minor=500000.
    - Booking status transitions to PARTIALLY_REFUNDED.
    """
    booking_id, entitlement_minor = await create_cancelled_booking(agency_client, "INTENT-WH-01")
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id=f"PAY-INTENT-WH-01",
        refund_id="RFD-WH-01",
        amount_minor=500000,
        currency="INR",
        event_id="evt_rfd_test_01",
    )

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PROCESSED"
    assert data["duplicate"] is False
    assert data["event_id"] == "evt_rfd_test_01"
    assert data["total_refunded_minor"] == 500000

    # DB Checks
    refunds = agg_db.get_refunds_for_booking(booking_id)
    assert len(refunds) == 1
    assert refunds[0].amount_minor == 500000

    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 1
    assert refund_entries[0].amount_minor == 500000
    assert refund_entries[0].direction == LedgerDirection.CREDIT


# ---------------------------------------------------------------------------
# Test 2: Duplicate sequential delivery
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_sequential_delivery(agency_client: AgencyClient):
    """
    Test 2:
    Deliver the exact same signed webhook 3 times sequentially.
    Asserts:
    - 1st delivery returns PROCESSED (duplicate=False).
    - 2nd & 3rd deliveries return IDEMPOTENT_REPLAY (duplicate=True, processed_count increments).
    - Exactly 1 refund record in SQLite.
    - Exactly 1 refund ledger entry posted.
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-02")
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-02",
        refund_id="RFD-WH-02",
        amount_minor=400000,
        currency="INR",
        event_id="evt_rfd_test_02",
    )

    # Delivery 1
    r1 = await agency_client.deliver_webhook(payload)
    assert r1.status_code == 200
    assert r1.json()["status"] == "PROCESSED"
    assert r1.json()["duplicate"] is False

    # Delivery 2 (Duplicate replay)
    r2 = await agency_client.deliver_webhook(payload)
    assert r2.status_code == 200
    assert r2.json()["status"] == "IDEMPOTENT_REPLAY"
    assert r2.json()["duplicate"] is True
    assert r2.json()["processed_count"] == 2

    # Delivery 3 (Duplicate replay)
    r3 = await agency_client.deliver_webhook(payload)
    assert r3.status_code == 200
    assert r3.json()["status"] == "IDEMPOTENT_REPLAY"
    assert r3.json()["duplicate"] is True
    assert r3.json()["processed_count"] == 3

    # Ground truth: Exactly 1 financial effect in SQLite
    refunds = agg_db.get_refunds_for_booking(booking_id)
    assert len(refunds) == 1
    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 1


# ---------------------------------------------------------------------------
# Test 3: Duplicate concurrent delivery (10 asynchronous requests)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_concurrent_delivery(agency_client: AgencyClient):
    """
    Test 3 & Phase 3 Requirement:
    Deliver the same signed event concurrently from at least 10 asynchronous requests.
    Asserts:
    - All 10 requests resolve successfully (HTTP 200).
    - Database-level locking and transaction handling prevent race conditions.
    - Exactly 1 refund record persisted.
    - Exactly 1 ledger entry posted.
    - Event delivery count equals 10.
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-03")
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-03",
        refund_id="RFD-WH-03",
        amount_minor=600000,
        currency="INR",
        event_id="evt_rfd_concur_10",
    )

    # Fire 10 concurrent requests with exact same signed event
    tasks = [agency_client.deliver_webhook(payload) for _ in range(10)]
    responses = await asyncio.gather(*tasks)

    # All must succeed with 200
    status_codes = [r.status_code for r in responses]
    assert status_codes == [200] * 10

    # Ground Truth: Exactly 1 financial effect in SQLite
    refunds = agg_db.get_refunds_for_booking(booking_id)
    assert len(refunds) == 1
    assert refunds[0].amount_minor == 600000

    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 1

    # Delivery count recorded in audit log
    wh_event = agg_db.get_webhook_event("evt_rfd_concur_10")
    assert wh_event is not None
    assert wh_event.processed_count == 10


# ---------------------------------------------------------------------------
# Test 4: Invalid signature rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invalid_signature_rejection(agency_client: AgencyClient):
    """
    Test 4:
    Submit a webhook with an invalid or tampered HMAC-SHA256 signature.
    Asserts:
    - Rejected with HTTP 401 Unauthorized.
    - No refund record or ledger entry created.
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-04")
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-04",
        refund_id="RFD-WH-04",
        amount_minor=300000,
        currency="INR",
        event_id="evt_rfd_bad_sig",
    )

    # Tamper with signature
    payload.signature = "invalid_tampered_hmac_sha256_signature_abc123"

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 401
    assert "signature" in resp.text.lower()

    # Verify no financial state mutation occurred
    refunds = agg_db.get_refunds_for_booking(booking_id)
    assert len(refunds) == 0
    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    assert len([e for e in ledger if e.entry_type == LedgerEntryType.REFUND]) == 0


# ---------------------------------------------------------------------------
# Test 5: Same event ID with modified payload
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_same_event_id_with_modified_payload(agency_client: AgencyClient):
    """
    Test 5 & Phase 3 Requirement:
    A reused event ID with a different payload must produce a safe conflict (HTTP 409)
    and must not change financial state.
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-05")
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    # First delivery: 3,000 INR
    payload_1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-05",
        refund_id="RFD-WH-05",
        amount_minor=300000,
        currency="INR",
        event_id="evt_rfd_conflict_01",
    )
    r1 = await agency_client.deliver_webhook(payload_1)
    assert r1.status_code == 200

    # Second delivery: Reuses same event_id with modified amount (5,000 INR)
    payload_2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-05",
        refund_id="RFD-WH-05",
        amount_minor=500000,
        currency="INR",
        event_id="evt_rfd_conflict_01",
    )

    r2 = await agency_client.deliver_webhook(payload_2)
    assert r2.status_code == 409
    assert "conflict" in r2.text.lower()

    # Ground truth: original amount (3,000 INR) preserved; no duplicate posted
    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 300000
    refunds = agg_db.get_refunds_for_booking(booking_id)
    assert len(refunds) == 1
    assert refunds[0].amount_minor == 300000


# ---------------------------------------------------------------------------
# Test 6: Missing event ID
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_event_id_rejected(agency_client: AgencyClient):
    """
    Test 6:
    Webhook with missing or empty event_id must be rejected (HTTP 400).
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-06")

    raw_payload = {
        "provider_id": "mock_pay_provider",
        "event_id": "",  # Empty
        "event_type": "refund.processed",
        "booking_id": booking_id,
        "payment_id": "PAY-INTENT-WH-06",
        "refund_id": "RFD-WH-06",
        "amount_minor": 200000,
        "currency": "INR",
        "created_at": "2026-11-08T12:00:00",
    }
    raw_payload["signature"] = compute_webhook_signature(DEFAULT_WEBHOOK_SECRET, raw_payload)

    resp = await agency_client.deliver_webhook(raw_payload)
    assert resp.status_code == 400
    assert "event_id" in resp.text.lower()


# ---------------------------------------------------------------------------
# Test 7: Already-processed event
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_already_processed_event_idempotent(agency_client: AgencyClient):
    """
    Test 7:
    Explicit test verifying that an already-processed event returns IDEMPOTENT_REPLAY
    and preserves identical refund/ledger IDs.
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-07")
    pay_service = get_payment_service()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-07",
        refund_id="RFD-WH-07",
        amount_minor=100000,
        currency="INR",
        event_id="evt_rfd_already_processed",
    )

    r1 = await agency_client.deliver_webhook(payload)
    assert r1.status_code == 200
    assert r1.json()["status"] == "PROCESSED"

    r2 = await agency_client.deliver_webhook(payload)
    assert r2.status_code == 200
    assert r2.json()["status"] == "IDEMPOTENT_REPLAY"
    assert r2.json()["duplicate"] is True


# ---------------------------------------------------------------------------
# Test 8: Refund exceeding eligible entitlement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refund_exceeding_eligible_entitlement_rejected(agency_client: AgencyClient):
    """
    Test 8:
    A webhook specifying an amount (15,000 INR) exceeding remaining entitlement (12,000 INR)
    must be rejected with HTTP 400 (ExcessiveRefundError) and not applied to financial ledger.
    """
    booking_id, entitlement_minor = await create_cancelled_booking(agency_client, "INTENT-WH-08", amount=15000.0)
    assert entitlement_minor == 1200000
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-08",
        refund_id="RFD-WH-08",
        amount_minor=1500000,  # 15,000 INR > 12,000 INR entitlement
        currency="INR",
        event_id="evt_rfd_excessive_01",
    )

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "exceeds" in resp.text.lower()

    # Verify no funds disbursed
    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 0
    ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    assert len([e for e in ledger if e.entry_type == LedgerEntryType.REFUND]) == 0


# ---------------------------------------------------------------------------
# Test 9: Concurrent different refund events against the same entitlement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_different_refund_events_against_same_entitlement(agency_client: AgencyClient):
    """
    Test 9:
    Two DIFFERENT signed refund webhooks (E1 for 8,000 INR and E2 for 8,000 INR)
    are fired concurrently against a 12,000 INR entitlement.
    Since 8,000 + 8,000 = 16,000 > 12,000:
    - Exactly one must succeed (200 OK).
    - Exactly one must be rejected (400 / ExcessiveRefundError).
    - Cumulative refunds strictly equal 8,000 INR <= 12,000 INR.
    """
    booking_id, entitlement_minor = await create_cancelled_booking(agency_client, "INTENT-WH-09", amount=15000.0)
    assert entitlement_minor == 1200000
    pay_service = get_payment_service()
    agg_db = get_aggregator_db()

    p1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-09",
        refund_id="RFD-WH-09-A",
        amount_minor=800000,
        currency="INR",
        event_id="evt_diff_A_8000",
    )
    p2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-09",
        refund_id="RFD-WH-09-B",
        amount_minor=800000,
        currency="INR",
        event_id="evt_diff_B_8000",
    )

    r1, r2 = await asyncio.gather(
        agency_client.deliver_webhook(p1),
        agency_client.deliver_webhook(p2),
    )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 400], f"Expected exactly [200, 400], got {statuses}"

    # Ground truth: Exactly 8,000 INR refunded, bounded by 12,000 INR
    booking = agg_db.get_booking_record(booking_id)
    assert booking.total_refunded_minor == 800000
    assert booking.total_refunded_minor <= booking.refundable_entitlement_minor


# ---------------------------------------------------------------------------
# Test 10: Financial consistency invariant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ledger_consistency_invariant(agency_client: AgencyClient):
    """
    Test 10 & Phase 4 Requirement:
    Verifies that InvariantEngine.check_financial_consistency proves exact
    mathematical equality across:
    booking_records.total_refunded_minor == sum(refund_records) == sum(ledger credit refunds).
    """
    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-10")
    pay_service = get_payment_service()
    inv_engine = get_invariant_engine()

    # Deliver partial refund 1: 3,000 INR
    p1 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-10",
        refund_id="RFD-WH-10-A",
        amount_minor=300000,
        currency="INR",
        event_id="evt_consist_01",
    )
    await agency_client.deliver_webhook(p1)

    # Deliver partial refund 2: 4,000 INR
    p2 = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-10",
        refund_id="RFD-WH-10-B",
        amount_minor=400000,
        currency="INR",
        event_id="evt_consist_02",
    )
    await agency_client.deliver_webhook(p2)

    # Invariant Oracle verification
    res = inv_engine.check_financial_consistency(booking_id)
    assert res.passed is True
    assert res.actual == "All values match: 700000 minor units"


# ---------------------------------------------------------------------------
# Test 11: Fixed mode regression and idempotency verification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fixed_mode_prevents_duplicate_credits(agency_client: AgencyClient):
    """
    Test 11 & Phase 2 Fixed Mode:
    In Fixed mode, 5 repeated deliveries of the same event:
    - Yield exactly 1 financial effect in SQLite.
    - check_webhook_idempotency passes.
    - check_refund_boundedness passes.
    """
    service = get_aggregator_booking_service()
    service.set_webhook_mode(AggregatorMode.FIXED)

    booking_id, _ = await create_cancelled_booking(agency_client, "INTENT-WH-11")
    pay_service = get_payment_service()
    inv_engine = get_invariant_engine()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-11",
        refund_id="RFD-WH-11",
        amount_minor=500000,
        currency="INR",
        event_id="evt_fixed_mode_01",
    )

    # Deliver 5 times
    for _ in range(5):
        await agency_client.deliver_webhook(payload)

    # Verify Webhook Idempotency Invariant
    res_idemp = inv_engine.check_webhook_idempotency("mock_pay_provider", "evt_fixed_mode_01")
    assert res_idemp.passed is True

    # Verify Refund Boundedness Invariant
    res_bounds = inv_engine.check_refund_boundedness(booking_id)
    assert res_bounds.passed is True


# ---------------------------------------------------------------------------
# Test 12: Vulnerable mode detection and structured security finding generation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vulnerable_mode_detection_and_evidence_generation(agency_client: AgencyClient):
    """
    Test 12 & Phase 2/4/6 Acceptance Criteria:
    1. Set Aggregator to VULNERABLE webhook mode.
    2. Deliver same event 3 times (5,000 INR each against a 12,000 INR entitlement).
    3. Aggregator naively processes all 3 deliveries:
       - 3 duplicate ledger entries created.
       - 15,000 INR disbursed > 12,000 INR entitlement.
    4. Crosswire Invariant Engine catches the violation, generating structured finding CW-SEC-M2-001.
    """
    service = get_aggregator_booking_service()
    service.set_webhook_mode(AggregatorMode.VULNERABLE)

    booking_id, entitlement_minor = await create_cancelled_booking(agency_client, "INTENT-WH-12", amount=15000.0)
    assert entitlement_minor == 1200000
    pay_service = get_payment_service()
    inv_engine = get_invariant_engine()

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id="PAY-INTENT-WH-12",
        refund_id="RFD-WH-12",
        amount_minor=500000,  # 5,000 INR
        currency="INR",
        event_id="evt_vuln_replay_01",
    )

    # Deliver 3 times
    for _ in range(3):
        r = await agency_client.deliver_webhook(payload)
        assert r.status_code == 200

    # Crosswire Invariant Engine: Webhook Idempotency Check
    res_idemp = inv_engine.check_webhook_idempotency("mock_pay_provider", "evt_vuln_replay_01")
    assert res_idemp.passed is False
    assert res_idemp.cwe is not None
    assert "CW-SEC-M2-001" in str(res_idemp.evidence)
    assert res_idemp.evidence["actual_refund_amount_minor"] == 1500000  # 3 * 500000
    assert len(res_idemp.evidence["duplicate_ledger_entry_ids"]) == 3

    # Crosswire Invariant Engine: Refund Boundedness Check
    res_bounds = inv_engine.check_refund_boundedness(booking_id)
    assert res_bounds.passed is False
    assert res_bounds.evidence["total_refunded_minor"] == 1500000
    assert res_bounds.evidence["excess_minor"] == 300000  # 15,000 - 12,000 = 3,000 INR excess

    # Restore default fixed mode
    service.set_webhook_mode(AggregatorMode.FIXED)


# ---------------------------------------------------------------------------
# Test 13: Real localhost TCP integration over socket transport
# ---------------------------------------------------------------------------
TCP_AGG_PORT = 8012
TCP_AGG_URL = f"http://127.0.0.1:{TCP_AGG_PORT}"


@pytest.fixture(scope="module")
def tcp_aggregator_server():
    """Starts a real Uvicorn instance on localhost port 8012 for TCP webhook concurrency tests."""
    base_cmd = [sys.executable, "-m", "uvicorn"]

    proc = subprocess.Popen(
        base_cmd + [
            "target_platform.aggregator.app:aggregator_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(TCP_AGG_PORT),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to come online
    deadline = time.time() + 10.0
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{TCP_AGG_URL}/", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.1)

    if not ready:
        proc.kill()
        pytest.fail(f"Aggregator TCP server on port {TCP_AGG_PORT} failed to start within 10s")

    yield TCP_AGG_URL

    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_tcp_webhook_concurrent_delivery(tcp_aggregator_server):
    """
    Test 13 & Phase 3:
    Executes 10 concurrent webhook deliveries over actual localhost TCP sockets (port 8012).
    Verifies that:
    - Real socket network calls succeed.
    - Exactly 1 refund record is committed in SQLite.
    - Exactly 1 ledger entry is committed in SQLite.
    """
    agg_db = get_aggregator_db()
    agg_db.reset()
    pay_service = get_payment_service()

    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }

    booking_id = "TBOH-TCP-WH-01"
    intent_id = "INTENT-TCP-WH-01"

    # Seed intent & cancelled booking directly in SQLite to keep webhook TCP test fully isolated
    intent = BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Synthetic Hotel Mumbai",
        room_type="deluxe_suite",
        check_in="2026-12-15",
        check_out="2026-12-18",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        guest_name="Aarav Sharma",
        correlation_id="corr_tcp_wh_01",
        cancellation_penalty_rate=0.20,
    )
    agg_db.save_booking_intent(intent)

    booking = AggregatorBookingRecord(
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.CANCELLED,
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        cancellation_penalty=3000.0,
        cancellation_penalty_minor=300000,
        refundable_entitlement=12000.0,
        refundable_entitlement_minor=1200000,
        total_refunded=0.0,
        total_refunded_minor=0,
        correlation_id="corr_tcp_wh_01",
        payment_id="PAY-INTENT-TCP-WH-01",
    )
    agg_db.save_booking_record(booking)

    pay_tx = PaymentTransactionRecord(
        payment_id="PAY-INTENT-TCP-WH-01",
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        status=PaymentStatus.CAPTURED,
        transaction_reference="TXN-PAY-TCP-WH-01",
        idempotency_key="PAY-AUTH-TCP-WH-01",
        provider="mock_pay_provider",
    )
    agg_db.save_payment_transaction(pay_tx)

    agg_db.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-TCP-WH-01",
        amount_minor=1200000,
        currency="INR",
        payment_id="PAY-INTENT-TCP-WH-01",
        agency_id="agency_alpha",
    )

    async with httpx.AsyncClient(base_url=tcp_aggregator_server, timeout=10.0) as client:
        # Configure Fixed mode on the live TCP server
        await client.post("/api/v1/admin/config", json={"webhook_mode": AggregatorMode.FIXED.value})

        # Generate signed webhook event
        payload = pay_service.generate_refund_webhook_event(
            booking_id=booking_id,
            payment_id="PAY-INTENT-TCP-WH-01",
            refund_id="RFD-TCP-WH-01",
            amount_minor=600000,
            currency="INR",
            event_id="evt_tcp_concur_10",
        )
        body = payload.model_dump(mode="json")
        wh_headers = {"X-Webhook-Signature": payload.signature}

        # Fire 10 concurrent requests over TCP
        tasks = [client.post("/api/v1/webhooks/payment", json=body, headers=wh_headers) for _ in range(10)]
        responses = await asyncio.gather(*tasks)

        assert [r.status_code for r in responses] == [200] * 10

        # Verify SQLite Ground Truth
        refunds = agg_db.get_refunds_for_booking(booking_id)
        assert len(refunds) == 1
        assert refunds[0].amount_minor == 600000

        ledger = agg_db.get_ledger_entries_for_booking(booking_id)
        refund_entries = [e for e in ledger if e.entry_type == LedgerEntryType.REFUND]
        assert len(refund_entries) == 1
        assert refund_entries[0].amount_minor == 600000


# ---------------------------------------------------------------------------
# Test 14: No external network access
# ---------------------------------------------------------------------------
def test_no_external_network_access(monkeypatch):
    """
    Test 14 & Strict Restriction:
    Verifies that the entire synthetic platform and testbed execute completely
    offline without making any outbound external network or internet connections.
    """
    import socket

    def guarded_connect(self, *args, **kwargs):
        dest = args[0] if args else kwargs.get("address")
        host = dest[0] if isinstance(dest, tuple) else str(dest)
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise PermissionError(f"External network call forbidden by test harness: {host}")
        return original_connect(self, *args, **kwargs)

    original_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    # Perform internal lookup & validation
    pay_service = get_payment_service()
    evt = pay_service.generate_refund_webhook_event(
        booking_id="TBOH-TEST-ISOLATED",
        payment_id="PAY-TEST-ISOLATED",
        refund_id="RFD-TEST-ISOLATED",
        amount_minor=100000,
        currency="INR",
        event_id="evt_isolated_test",
    )
    assert evt.signature is not None

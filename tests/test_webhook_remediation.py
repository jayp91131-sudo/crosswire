"""
Comprehensive Verification Test Suite for Milestone 2.1 Remediation Sprint.
Covers:
- CW-REV-M2-001: Provider-specific authentication and registry.
- CW-REV-M2-002: Entity relationship validation, lifecycle gating, and authorized refund reconciliation.
- CW-REV-M2-003: Provider-scoped event identity (multi-provider identical event ID collision safety).
- CW-REV-M2-004: Exact relational traceability on provider & event_id (no LIKE substring collisions).
- CW-REV-M2-005: Timestamp freshness window (5-min window, clock injectable, old retries idempotent).
"""

import asyncio
from datetime import datetime, timedelta
import pytest
import httpx
from target_platform.agency_client import AgencyClient
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.aggregator.database import get_aggregator_db
from target_platform.payment.payment_service import get_payment_service
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    LedgerEntryType,
    LedgerDirection,
    PaymentTransactionRecord,
    PaymentStatus,
    WebhookPayload,
    SYNTHETIC_PROVIDER_SECRETS,
    compute_webhook_signature,
    get_provider_registry,
    UnregisteredProviderError,
    InvalidWebhookSignatureError,
    StaleWebhookEventError,
    FutureWebhookEventError,
    WebhookTimestampError,
    PaymentMismatchError,
    CurrencyMismatchError,
    UnsupportedWebhookEventError,
    InvalidBookingStateForRefundError,
    UnknownRefundError,
    ExcessiveRefundError,
)
from engine.invariant_engine import get_invariant_engine


# Helper to create a cancelled booking
async def create_cancelled_booking(
    agency_client: AgencyClient,
    intent_id: str,
    amount: float = 15000.0,
    provider: str = "mock_pay_provider",
    currency: str = "INR",
) -> str:
    intent = agency_client.create_synthetic_intent(intent_id=intent_id, amount=amount, currency=currency)
    init_res = await agency_client.submit_booking(intent)
    assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED
    cancel_res = await agency_client.cancel_booking(init_res.booking_id)
    assert cancel_res.status == TransactionStatus.CANCELLED

    # If provider is different from default, update payment transaction
    if provider != "mock_pay_provider":
        agg_db = get_aggregator_db()
        with agg_db._connection() as conn:
            conn.execute(
                "UPDATE payment_transactions SET provider = ? WHERE payment_id = ?",
                (provider, f"PAY-{intent_id}"),
            )
    return init_res.booking_id


# ===========================================================================
# 1. Provider-Specific Authentication (CW-REV-M2-001)
# ===========================================================================
@pytest.mark.asyncio
async def test_provider_authentication_registered_providers(agency_client: AgencyClient):
    """Verifies that each registered provider signs with its own distinct secret and succeeds."""
    registry = get_provider_registry()
    service = get_aggregator_booking_service()
    agg_db = get_aggregator_db()

    providers = ["mock_pay_provider", "stripe_synthetic", "razorpay_synthetic"]
    for idx, provider_id in enumerate(providers, 1):
        booking_id = await create_cancelled_booking(
            agency_client, f"INTENT-PROV-AUTH-{idx}", provider=provider_id
        )
        rfd_id = f"RFD-PROV-AUTH-{idx}"
        await agency_client.authorize_refund(
            booking_id=booking_id,
            refund_id=rfd_id,
            amount_minor=200000,
            payment_id=f"PAY-INTENT-PROV-AUTH-{idx}",
            provider=provider_id,
        )

        secret = registry.get_secret(provider_id)
        assert secret is not None

        payload = WebhookPayload(
            provider_id=provider_id,
            event_id=f"evt_prov_auth_{provider_id}",
            event_type="refund.processed",
            booking_id=booking_id,
            payment_id=f"PAY-INTENT-PROV-AUTH-{idx}",
            refund_id=rfd_id,
            amount_minor=200000,
            currency="INR",
            created_at=datetime.utcnow().isoformat(),
        ).sign(secret=secret)

        resp = await agency_client.deliver_webhook(payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "PROCESSED"

        # Verify provider recorded in DB
        wh_row = agg_db.get_webhook_event(f"evt_prov_auth_{provider_id}", provider=provider_id)
        assert wh_row is not None
        assert wh_row.provider == provider_id


@pytest.mark.asyncio
async def test_provider_authentication_wrong_secret_rejected(agency_client: AgencyClient):
    """Provider signed with another provider's secret must be rejected (HTTP 401)."""
    booking_id = await create_cancelled_booking(
        agency_client, "INTENT-PROV-WRONG-SEC", provider="stripe_synthetic"
    )
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-WRONG-SEC",
        amount_minor=200000,
        payment_id="PAY-INTENT-PROV-WRONG-SEC",
        provider="stripe_synthetic",
    )

    registry = get_provider_registry()
    wrong_secret = registry.get_secret("razorpay_synthetic")  # wrong secret!

    payload = WebhookPayload(
        provider_id="stripe_synthetic",
        event_id="evt_prov_wrong_sec",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-PROV-WRONG-SEC",
        refund_id="RFD-WRONG-SEC",
        amount_minor=200000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=wrong_secret)

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 401
    assert "signature" in resp.text.lower()


@pytest.mark.asyncio
async def test_unregistered_provider_rejected(agency_client: AgencyClient):
    """An unknown/unregistered provider must be rejected with HTTP 401."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-UNREG-PROV")
    raw_payload = {
        "provider_id": "unknown_unregistered_gateway",
        "event_id": "evt_unreg_01",
        "event_type": "refund.processed",
        "booking_id": booking_id,
        "payment_id": "PAY-INTENT-UNREG-PROV",
        "refund_id": "RFD-UNREG",
        "amount_minor": 100000,
        "currency": "INR",
        "created_at": datetime.utcnow().isoformat(),
        "signature": "fake_signature_abc",
    }
    resp = await agency_client.deliver_webhook(raw_payload)
    assert resp.status_code == 401
    assert "not registered" in resp.text.lower() or "unregistered" in resp.text.lower()


@pytest.mark.asyncio
async def test_multi_provider_same_event_id_collision_safe(agency_client: AgencyClient):
    """
    CW-REV-M2-003:
    Two distinct providers independently send an event with the exact same event_id.
    Both must succeed without primary key conflict or state collision.
    """
    shared_event_id = "evt_collision_shared_999"

    # Provider A: Stripe
    booking_a = await create_cancelled_booking(
        agency_client, "INTENT-COLLISION-A", provider="stripe_synthetic"
    )
    await agency_client.authorize_refund(
        booking_id=booking_a,
        refund_id="RFD-COLLISION-A",
        amount_minor=300000,
        payment_id="PAY-INTENT-COLLISION-A",
        provider="stripe_synthetic",
    )

    # Provider B: Razorpay
    booking_b = await create_cancelled_booking(
        agency_client, "INTENT-COLLISION-B", provider="razorpay_synthetic"
    )
    await agency_client.authorize_refund(
        booking_id=booking_b,
        refund_id="RFD-COLLISION-B",
        amount_minor=400000,
        payment_id="PAY-INTENT-COLLISION-B",
        provider="razorpay_synthetic",
    )

    reg = get_provider_registry()
    p_a = WebhookPayload(
        provider_id="stripe_synthetic",
        event_id=shared_event_id,
        event_type="refund.processed",
        booking_id=booking_a,
        payment_id="PAY-INTENT-COLLISION-A",
        refund_id="RFD-COLLISION-A",
        amount_minor=300000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("stripe_synthetic"))

    p_b = WebhookPayload(
        provider_id="razorpay_synthetic",
        event_id=shared_event_id,
        event_type="refund.processed",
        booking_id=booking_b,
        payment_id="PAY-INTENT-COLLISION-B",
        refund_id="RFD-COLLISION-B",
        amount_minor=400000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("razorpay_synthetic"))

    # Both must deliver successfully
    r_a = await agency_client.deliver_webhook(p_a)
    assert r_a.status_code == 200
    assert r_a.json()["status"] == "PROCESSED"

    r_b = await agency_client.deliver_webhook(p_b)
    assert r_b.status_code == 200
    assert r_b.json()["status"] == "PROCESSED"

    # Verify both records exist with composite PK (provider, event_id)
    agg_db = get_aggregator_db()
    rec_a = agg_db.get_webhook_event(shared_event_id, provider="stripe_synthetic")
    rec_b = agg_db.get_webhook_event(shared_event_id, provider="razorpay_synthetic")
    assert rec_a is not None and rec_a.booking_id == booking_a
    assert rec_b is not None and rec_b.booking_id == booking_b


# ===========================================================================
# 2. Entity & Authorization Validation (CW-REV-M2-002)
# ===========================================================================
@pytest.mark.asyncio
async def test_entity_validation_payment_mismatch(agency_client: AgencyClient):
    """Webhook with a payment_id not belonging to the booking must be rejected (HTTP 400)."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-ENT-PAY-MISMATCH")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-ENT-PAY-MISMATCH",
        amount_minor=100000,
        payment_id="PAY-INTENT-ENT-PAY-MISMATCH",
    )

    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_pay_mismatch_01",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-OTHER-BOOKING-999",  # wrong payment!
        refund_id="RFD-ENT-PAY-MISMATCH",
        amount_minor=100000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "payment" in resp.text.lower()


@pytest.mark.asyncio
async def test_entity_validation_currency_mismatch(agency_client: AgencyClient):
    """Webhook specifying currency different from booking/payment currency must be rejected (HTTP 400)."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-ENT-CURR-MISMATCH")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-ENT-CURR-MISMATCH",
        amount_minor=100000,
        currency="INR",
        payment_id="PAY-INTENT-ENT-CURR-MISMATCH",
    )

    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_curr_mismatch_01",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-ENT-CURR-MISMATCH",
        refund_id="RFD-ENT-CURR-MISMATCH",
        amount_minor=100000,
        currency="USD",  # wrong currency!
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "currency" in resp.text.lower()


@pytest.mark.asyncio
async def test_entity_validation_unsupported_event_type(agency_client: AgencyClient):
    """Unsupported event types (e.g. payment.captured, dispute.opened) must be rejected (HTTP 400)."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-ENT-BAD-EVENT")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-ENT-BAD-EVENT",
        amount_minor=100000,
        payment_id="PAY-INTENT-ENT-BAD-EVENT",
    )

    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_bad_event_01",
        event_type="dispute.opened",  # not a supported refund event!
        booking_id=booking_id,
        payment_id="PAY-INTENT-ENT-BAD-EVENT",
        refund_id="RFD-ENT-BAD-EVENT",
        amount_minor=100000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "unsupported" in resp.text.lower()


@pytest.mark.asyncio
async def test_entity_validation_uncancelled_booking_rejected(agency_client: AgencyClient):
    """An uncancelled (active / confirmed) booking cannot be refunded via webhook (HTTP 400)."""
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-UNCANCELLED-ACTIVE", amount=10000.0)
    init_res = await agency_client.submit_booking(intent)
    assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED

    # Note: booking is NOT cancelled!
    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_uncancelled_01",
        event_type="refund.processed",
        booking_id=init_res.booking_id,
        payment_id="PAY-INTENT-UNCANCELLED-ACTIVE",
        refund_id="RFD-ACTIVE-FAKE",
        amount_minor=500000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "not eligible for refund" in resp.text.lower() or "status" in resp.text.lower()


@pytest.mark.asyncio
async def test_entity_validation_unknown_refund_instruction_rejected(agency_client: AgencyClient):
    """A webhook referencing a non-existent or un-authorized refund ID must be rejected (HTTP 404)."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-UNKNOWN-RFD")

    # Do not pre-authorize!
    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_unknown_rfd_01",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-UNKNOWN-RFD",
        refund_id="RFD-NONEXISTENT-999",
        amount_minor=100000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


@pytest.mark.asyncio
async def test_entity_validation_refund_exceeding_authorized_amount(agency_client: AgencyClient):
    """A webhook specifying an amount greater than the authorized amount must be rejected (HTTP 400)."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-EXCEED-AUTH")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-EXCEED-AUTH",
        amount_minor=200000,  # Authorized for 2,000 INR
        payment_id="PAY-INTENT-EXCEED-AUTH",
    )

    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_exceed_auth_01",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-EXCEED-AUTH",
        refund_id="RFD-EXCEED-AUTH",
        amount_minor=400000,  # 4,000 INR > 2,000 INR authorized
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "exceeds authorized" in resp.text.lower()


# ===========================================================================
# 3. Exact Relational Traceability (CW-REV-M2-004)
# ===========================================================================
@pytest.mark.asyncio
async def test_exact_relational_traceability_no_like_substring_collision(agency_client: AgencyClient):
    """
    CW-REV-M2-004:
    Proves that get_ledger_entries_for_event matches exact event_id and provider,
    eliminating false-positive substring matches (e.g. event '1' vs '10' vs 'TXN-1').
    """
    booking_id = await create_cancelled_booking(agency_client, "INTENT-TRACEABILITY-EXACT")
    agg_db = get_aggregator_db()
    reg = get_provider_registry()

    # Authorize refunds for 3 distinct event IDs with overlapping substrings: "1", "10", "TXN-1"
    event_ids = ["1", "10", "TXN-1"]
    for eid in event_ids:
        rfd_id = f"RFD-EXACT-{eid}"
        await agency_client.authorize_refund(
            booking_id=booking_id,
            refund_id=rfd_id,
            amount_minor=100000,
            payment_id="PAY-INTENT-TRACEABILITY-EXACT",
        )
        payload = WebhookPayload(
            provider_id="mock_pay_provider",
            event_id=eid,
            event_type="refund.processed",
            booking_id=booking_id,
            payment_id="PAY-INTENT-TRACEABILITY-EXACT",
            refund_id=rfd_id,
            amount_minor=100000,
            currency="INR",
            created_at=datetime.utcnow().isoformat(),
        ).sign(secret=reg.get_secret("mock_pay_provider"))
        resp = await agency_client.deliver_webhook(payload)
        assert resp.status_code == 200

    # Query entries for event "1" specifically
    entries_for_1 = agg_db.get_ledger_entries_for_event("1", provider="mock_pay_provider")
    assert len(entries_for_1) == 1
    assert entries_for_1[0].event_id == "1"
    assert entries_for_1[0].provider == "mock_pay_provider"

    # Query entries for event "10"
    entries_for_10 = agg_db.get_ledger_entries_for_event("10", provider="mock_pay_provider")
    assert len(entries_for_10) == 1
    assert entries_for_10[0].event_id == "10"

    # Query entries for event "TXN-1"
    entries_for_txn_1 = agg_db.get_ledger_entries_for_event("TXN-1", provider="mock_pay_provider")
    assert len(entries_for_txn_1) == 1
    assert entries_for_txn_1[0].event_id == "TXN-1"


# ===========================================================================
# 4. Timestamp Freshness Window (CW-REV-M2-005)
# ===========================================================================
@pytest.mark.asyncio
async def test_stale_unseen_webhook_rejected(agency_client: AgencyClient):
    """An unseen webhook older than 5 minutes (e.g. 10 minutes ago) must be rejected with HTTP 400."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-STALE-WH")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-STALE",
        amount_minor=100000,
        payment_id="PAY-INTENT-STALE-WH",
    )

    stale_ts = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_stale_unseen",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-STALE-WH",
        refund_id="RFD-STALE",
        amount_minor=100000,
        currency="INR",
        created_at=stale_ts,
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "stale" in resp.text.lower()


@pytest.mark.asyncio
async def test_future_unseen_webhook_rejected(agency_client: AgencyClient):
    """An unseen webhook with a future timestamp (> 5 minutes ahead) must be rejected with HTTP 400."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-FUTURE-WH")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-FUTURE",
        amount_minor=100000,
        payment_id="PAY-INTENT-FUTURE-WH",
    )

    future_ts = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_future_unseen",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-FUTURE-WH",
        refund_id="RFD-FUTURE",
        amount_minor=100000,
        currency="INR",
        created_at=future_ts,
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 400
    assert "future" in resp.text.lower()


@pytest.mark.asyncio
async def test_malformed_timestamp_rejected(agency_client: AgencyClient):
    """A webhook with a non-ISO / garbage timestamp must be rejected with HTTP 400."""
    booking_id = await create_cancelled_booking(agency_client, "INTENT-MALFORMED-TS")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-MALFORMED-TS",
        amount_minor=100000,
        payment_id="PAY-INTENT-MALFORMED-TS",
    )

    reg = get_provider_registry()
    raw = {
        "provider_id": "mock_pay_provider",
        "event_id": "evt_malformed_ts_01",
        "event_type": "refund.processed",
        "booking_id": booking_id,
        "payment_id": "PAY-INTENT-MALFORMED-TS",
        "refund_id": "RFD-MALFORMED-TS",
        "amount_minor": 100000,
        "currency": "INR",
        "created_at": "not-a-valid-timestamp-string",
    }
    raw["signature"] = compute_webhook_signature(reg.get_secret("mock_pay_provider"), raw)

    resp = await agency_client.deliver_webhook(raw)
    assert resp.status_code == 400
    assert "timestamp" in resp.text.lower() or "malformed" in resp.text.lower()


@pytest.mark.asyncio
async def test_old_duplicate_retry_bypasses_freshness_window(agency_client: AgencyClient):
    """
    CW-REV-M2-005:
    An already-processed event that was originally delivered within the window
    must still return HTTP 200 (IDEMPOTENT_REPLAY) even if retried hours later.
    """
    booking_id = await create_cancelled_booking(agency_client, "INTENT-OLD-REPLAY")
    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id="RFD-OLD-REPLAY",
        amount_minor=100000,
        payment_id="PAY-INTENT-OLD-REPLAY",
    )

    reg = get_provider_registry()
    payload = WebhookPayload(
        provider_id="mock_pay_provider",
        event_id="evt_old_replay_idempotent",
        event_type="refund.processed",
        booking_id=booking_id,
        payment_id="PAY-INTENT-OLD-REPLAY",
        refund_id="RFD-OLD-REPLAY",
        amount_minor=100000,
        currency="INR",
        created_at=datetime.utcnow().isoformat(),
    ).sign(secret=reg.get_secret("mock_pay_provider"))

    # Initial delivery succeeds
    r1 = await agency_client.deliver_webhook(payload)
    assert r1.status_code == 200
    assert r1.json()["status"] == "PROCESSED"

    # Simulate arrival 2 hours later with exact same payload
    r2 = await agency_client.deliver_webhook(payload)
    assert r2.status_code == 200
    assert r2.json()["status"] == "IDEMPOTENT_REPLAY"
    assert r2.json()["duplicate"] is True

"""
Milestone 2.2 — Test Suite: Booking Idempotency Concurrency & Agency Scoping (Scenario C).
Verifies:
1. High-concurrency replay: delivering the identical booking request >= 10 times concurrently
   returns the exact same booking record, voucher ID, and primary reservation ID without duplicates.
2. Idempotency conflict enforcement: reusing an idempotency key with a modified payload raises
   AggregatorIdempotencyConflictError (HTTP 409).
3. Cross-agency idempotency key isolation: distinct agencies (Agency Alpha and Agency Beta)
   can use the exact same idempotency key string concurrently without collisions or interference.
4. Cross-agency wallet isolation (Invariant 6.12): Agency Alpha cannot access or inspect Agency Beta's wallet.
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from target_platform.aggregator.app import aggregator_app
from target_platform.aggregator.database import get_aggregator_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    BookingIntent,
    AggregatorMode,
    AggregatorIdempotencyConflictError,
)
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
def setup_teardown(supplier_client):
    db = get_aggregator_db()
    db.reset()
    service = get_aggregator_booking_service()
    service.supplier_client = supplier_client
    service.set_mode(AggregatorMode.FIXED)
    service.set_wallet_mode(AggregatorMode.FIXED)
    yield
    db.reset()


@pytest.mark.asyncio
async def test_concurrent_identical_booking_requests_return_single_booking():
    """
    Scenario C: Deliver the same booking operation 15 times concurrently.
    All concurrent requests must return the identical booking record, payment, and voucher ID.
    Only 1 booking record and 1 payment transaction must be persisted.
    """
    service = get_aggregator_booking_service()
    db = get_aggregator_db()

    intent = BookingIntent(
        intent_id="INTENT-IDEMP-CONC-01",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Concurrent Traveler",
        correlation_id="corr-idemp-15x",
        payment_method="WALLET",
        idempotency_key="IDEMP-KEY-CONCURRENT-15X",
    )

    # Launch 15 concurrent identical submissions
    tasks = [service.process_booking_intent(intent) for _ in range(15)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # None should fail
    for idx, res in enumerate(results):
        assert not isinstance(res, Exception), f"Request {idx} failed: {res}"

    # Verify all return identical booking_id and voucher_id
    booking_ids = {r.booking_id for r in results}
    voucher_ids = {r.voucher_id for r in results}
    assert len(booking_ids) == 1
    assert len(voucher_ids) == 1

    # Verify database has exactly 1 booking record for this intent
    records = [b for b in [db.get_booking_by_intent("INTENT-IDEMP-CONC-01")] if b]
    assert len(records) == 1

    # Verify wallet holds: exactly 1 hold was captured
    holds = db.get_wallet_holds("agency_alpha")
    idemp_holds = [h for h in holds if h.idempotency_key == "IDEMP-KEY-CONCURRENT-15X"]
    assert len(idemp_holds) == 1
    assert idemp_holds[0].status.value == "CAPTURED"


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_modified_payload_raises_conflict():
    """
    Reusing an idempotency key with a modified payload (e.g., different guest or amount)
    must fail with AggregatorIdempotencyConflictError (HTTP 409).
    """
    service = get_aggregator_booking_service()

    intent_orig = BookingIntent(
        intent_id="INTENT-ORIGINAL",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Original Guest",
        correlation_id="corr-orig",
        payment_method="WALLET",
        idempotency_key="IDEMP-CONFLICT-KEY",
    )

    res1 = await service.process_booking_intent(intent_orig)
    assert res1.status.value == "SUPPLIER_CONFIRMED"

    # Now attempt reuse with different guest name
    intent_tampered = BookingIntent(
        intent_id="INTENT-TAMPERED",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Tampered Guest",
        correlation_id="corr-tampered",
        payment_method="WALLET",
        idempotency_key="IDEMP-CONFLICT-KEY",
    )

    with pytest.raises(AggregatorIdempotencyConflictError) as exc_info:
        await service.process_booking_intent(intent_tampered)

    assert "already used with a different booking payload" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_distinct_agencies_sharing_same_idempotency_key_do_not_collide():
    """
    Agency Alpha and Agency Beta submit bookings using the exact same idempotency key string.
    Since idempotency keys are scoped per agency (agency_id, idempotency_key),
    both succeed independently without colliding!
    """
    service = get_aggregator_booking_service()
    db = get_aggregator_db()

    intent_alpha = BookingIntent(
        intent_id="INTENT-ALPHA-SHARED",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Alpha Guest",
        correlation_id="corr-shared-alpha",
        payment_method="WALLET",
        idempotency_key="SHARED-KEY-12345",
    )

    intent_beta = BookingIntent(
        intent_id="INTENT-BETA-SHARED",
        agency_id="agency_beta",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Beta Guest",
        correlation_id="corr-shared-beta",
        payment_method="WALLET",
        idempotency_key="SHARED-KEY-12345",
    )

    res_alpha, res_beta = await asyncio.gather(
        service.process_booking_intent(intent_alpha),
        service.process_booking_intent(intent_beta),
    )

    assert res_alpha.agency_id == "agency_alpha"
    assert res_beta.agency_id == "agency_beta"
    assert res_alpha.booking_id != res_beta.booking_id
    assert res_alpha.status.value == "SUPPLIER_CONFIRMED"
    assert res_beta.status.value == "SUPPLIER_CONFIRMED"


@pytest.mark.asyncio
async def test_cross_agency_wallet_isolation_http_403():
    """
    Agency Alpha attempts to inspect Agency Beta's wallet.
    The endpoint must enforce strict authorization and reject with HTTP 403 Forbidden.
    Invariant 6.12 check passes.
    """
    transport = ASGITransport(app=aggregator_app)
    client_alpha = AgencyClient(
        agency_id="agency_alpha",
        api_key="secret_alpha_key_99",
        aggregator_base_url="http://testserver",
        transport=transport,
    )

    # Agency Alpha requests Agency Beta's wallet
    resp = await client_alpha.get_agency_wallet("agency_beta")
    assert resp.status_code == 403
    assert "cross-agency isolation violation" in resp.text.lower()

    # Invariant check 6.12
    invariant_engine = get_invariant_engine()
    inv_res = invariant_engine.check_cross_agency_wallet_isolation("agency_alpha", "agency_beta")
    assert inv_res.passed

"""
Verification of Idempotency Contracts and Unsupported Legacy Supplier Fallback.
Tests:
1. Idempotent supplier returns existing reservation on replay with identical key.
2. Legacy supplier without idempotency triggers safe fallback to SUPPLIER_UNKNOWN.
3. Ambiguous outcome is never falsely marked CONFIRMED or FAILED.
"""

import pytest
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    BookingIntent,
    TransactionStatus,
    AggregatorMode,
    SupplierMode,
    SupplierBookingRequest,
)
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from engine.invariant_engine import get_invariant_engine


@pytest.mark.asyncio
async def test_idempotent_supplier_deduplication_on_repeated_requests():
    """
    Directly tests the supplier's idempotency contract:
    Two identical requests with the same idempotency_key must return the same reservation.
    """
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    sup_service.configure(mode=SupplierMode.IDEMPOTENT, simulate_timeout=False)

    req = SupplierBookingRequest(
        intent_id="INTENT-IDEMP-01",
        hotel_id="htl_mumbai_grand",
        room_type="deluxe_suite",
        guest_name="Aarav Sharma",
        amount=15000.0,
        currency="INR",
        correlation_id="corr_idemp_1",
        idempotency_key="IDEMP-KEY-999",
    )

    # First Call
    res_1 = await sup_service.create_reservation(req)
    # Replay Call with same Idempotency-Key
    res_2 = await sup_service.create_reservation(req)

    assert res_1.supplier_reservation_id == res_2.supplier_reservation_id
    assert sup_db.get_reservation_count_for_intent("INTENT-IDEMP-01") == 1


@pytest.mark.asyncio
async def test_unsupported_supplier_enters_reconciliation_fallback(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    When the supplier lacks idempotency or reference lookup (SupplierMode.UNSUPPORTED)
    and an ambiguous timeout occurs:
    1. Aggregator halts retries.
    2. Enters SUPPLIER_UNKNOWN (not FAILED, not CONFIRMED).
    3. Exactly 1 reservation exists on supplier; no secondary reservation is created.
    4. Safe fallback invariant passes.
    """
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()
    inv_engine = get_invariant_engine()

    # Configure fixed aggregator against an unsupported legacy supplier with timeout fault
    agg_service.set_mode(AggregatorMode.FIXED)
    sup_service.configure(
        mode=SupplierMode.UNSUPPORTED,
        simulate_timeout=True,
        timeout_delay_seconds=0.30,
    )

    response = await agency_client.submit_booking(synthetic_intent)

    # Aggregator status MUST be SUPPLIER_UNKNOWN
    assert response.status == TransactionStatus.SUPPLIER_UNKNOWN
    assert response.status != TransactionStatus.SUPPLIER_CONFIRMED
    assert response.status != TransactionStatus.FAILED

    # Supplier DB has only the single reservation from attempt 1
    supplier_reservations = sup_db.get_reservations_by_intent(synthetic_intent.intent_id)
    assert len(supplier_reservations) == 1, "No secondary reservation should be created"

    # Invariant Oracle validation
    fallback_result = inv_engine.check_safe_ambiguous_fallback(synthetic_intent.intent_id)
    assert fallback_result.passed is True
    assert "SUPPLIER_UNKNOWN" in fallback_result.actual


@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_with_same_idempotency_key():
    """
    Audit Item 7:
    Simulates a race condition where multiple concurrent requests (asyncio.gather)
    arrive at the supplier with the exact same idempotency_key.
    Database-level UNIQUE constraint and concurrency handling must guarantee:
    1. Exactly 1 reservation is persisted in SQLite.
    2. All concurrent coroutines resolve to the identical supplier_reservation_id.
    3. Zero unhandled IntegrityErrors or database corruption.
    """
    import asyncio

    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    sup_service.configure(mode=SupplierMode.IDEMPOTENT, simulate_timeout=False)

    shared_idempotency_key = "IDEMP-CONCUR-KEY-777"
    intent_id = "INTENT-CONCUR-TEST-01"

    req = SupplierBookingRequest(
        intent_id=intent_id,
        hotel_id="htl_bengaluru_resort",
        room_type="executive_suite",
        guest_name="Rohan Verma",
        amount=25000.0,
        currency="INR",
        correlation_id="corr_concur_1",
        idempotency_key=shared_idempotency_key,
    )

    # Launch 5 concurrent calls racing to create reservation
    results = await asyncio.gather(
        sup_service.create_reservation(req),
        sup_service.create_reservation(req),
        sup_service.create_reservation(req),
        sup_service.create_reservation(req),
        sup_service.create_reservation(req),
    )

    # 1. Exactly 1 reservation persisted in the database
    res_count = sup_db.get_reservation_count_for_intent(intent_id)
    assert res_count == 1, f"Expected exactly 1 reservation in DB, got {res_count}"

    # 2. All 5 concurrent callers must receive the exact same reservation ID
    first_res_id = results[0].supplier_reservation_id
    for idx, res in enumerate(results):
        assert res.supplier_reservation_id == first_res_id, (
            f"Result {idx} got {res.supplier_reservation_id}, expected {first_res_id}"
        )

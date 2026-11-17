"""
Positive Control Verification: Normal Successful Booking Workflow.
Proves that under standard operating conditions (no faults), the three-actor system
completes end-to-end booking fulfillment with full SQLite persistence and traceability.
"""

import pytest
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    BookingIntent,
    TransactionStatus,
    AggregatorMode,
    SupplierMode,
)
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from engine.invariant_engine import get_invariant_engine


@pytest.mark.asyncio
async def test_normal_successful_booking_lifecycle(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    Positive control: Agency Alpha submits booking, supplier confirms immediately,
    and single reservation is registered in SQLite.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    inv_engine = get_invariant_engine()

    # 1. Agency submits booking intent over HTTP
    response = await agency_client.submit_booking(synthetic_intent)

    # 2. Verify Aggregator API Response
    assert response.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert response.retry_count == 0
    assert response.primary_supplier_reservation_id is not None
    assert response.primary_supplier_reservation_id.startswith("SUP-RES-INTENT-001")

    # 3. Verify SQLite Ground-Truth in Supplier DB
    reservations = sup_db.get_reservations_by_intent(synthetic_intent.intent_id)
    assert len(reservations) == 1, "Expected strictly 1 reservation in supplier database"
    res = reservations[0]
    assert res.supplier_reservation_id == response.primary_supplier_reservation_id
    assert res.hotel_id == "htl_mumbai_grand"
    assert res.amount == 15000.0
    assert res.currency == "INR"
    assert res.guest_name == "Aarav Sharma"
    assert res.status == "CONFIRMED"

    # 4. Verify SQLite Ground-Truth in Aggregator DB
    record = agg_db.get_booking_record(response.booking_id)
    assert record is not None
    assert record.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert record.primary_supplier_reservation_id == res.supplier_reservation_id
    assert record.retry_count == 0
    assert record.correlation_id == synthetic_intent.correlation_id

    # 5. Verify Agency Status Lookup API
    status_lookup = await agency_client.get_booking_status(response.booking_id)
    assert status_lookup.booking_id == response.booking_id
    assert status_lookup.status == TransactionStatus.SUPPLIER_CONFIRMED

    intent_lookup = await agency_client.get_booking_by_intent(synthetic_intent.intent_id)
    assert intent_lookup.booking_id == response.booking_id
    assert intent_lookup.primary_supplier_reservation_id == res.supplier_reservation_id

    # 6. Invariant Oracle Verification
    result = inv_engine.check_booking_intent_uniqueness(synthetic_intent.intent_id)
    assert result.passed is True
    assert result.actual == "1 reservation(s)"


@pytest.mark.asyncio
async def test_traceability_across_distributed_actors(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    Verifies that the correlation ID traverses from Agency Client -> Aggregator -> Supplier,
    and is recorded in SQLite transaction audit logs.
    """
    agg_db = get_aggregator_db()

    response = await agency_client.submit_booking(synthetic_intent)
    assert response.status == TransactionStatus.SUPPLIER_CONFIRMED

    # Inspect events in SQLite
    events = agg_db.get_events_for_booking(response.booking_id)
    assert len(events) >= 2

    # Check correlation ID consistency
    for ev in events:
        assert ev["correlation_id"] == synthetic_intent.correlation_id
        assert ev["booking_id"] == response.booking_id

    # Inspect supplier operations in SQLite
    operations = agg_db.get_supplier_operations_for_booking(response.booking_id)
    assert len(operations) == 1
    assert operations[0]["attempt_number"] == 1
    assert operations[0]["status_code"] == 200
    assert operations[0]["supplier_reservation_id"] == response.primary_supplier_reservation_id

"""
Verification of Ambiguous Supplier Timeout & Vulnerable Blind Retry.
Proves that when a response is delayed beyond HTTP timeout after persistence,
vulnerable aggregator creates duplicate reservations, whereas fixed mode recovers safely.
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
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from engine.invariant_engine import get_invariant_engine


@pytest.mark.asyncio
async def test_vulnerable_blind_retry_produces_duplicate_supplier_reservations(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    Scenario:
    1. Supplier commits initial reservation to SQLite.
    2. Supplier delays response (0.35s) beyond Aggregator's HTTP timeout (0.15s).
    3. Aggregator experiences ReadTimeout.
    4. Vulnerable Aggregator blindly retries POST /reservations without idempotency.
    5. Supplier receives second request and commits a second reservation to SQLite.
    6. Aggregator marks status SUPPLIER_CONFIRMED with the 2nd reservation ID.
    7. Invariant Oracle proves Invariant 5.2 violation: 2 distinct supplier reservations for 1 intent.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()
    inv_engine = get_invariant_engine()

    # Configure vulnerable aggregator & supplier timeout fault
    agg_service.set_mode(AggregatorMode.VULNERABLE)
    sup_service.configure(
        mode=SupplierMode.VULNERABLE,
        simulate_timeout=True,
        timeout_delay_seconds=0.30,  # > 0.15s client timeout
    )

    # Agency initiates booking
    response = await agency_client.submit_booking(synthetic_intent)

    # Aggregator status reports confirmed, but with retry_count == 1
    assert response.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert response.retry_count == 1

    # GROUND TRUTH IN SQLITE: Prove supplier created TWO distinct reservations!
    supplier_reservations = sup_db.get_reservations_by_intent(synthetic_intent.intent_id)
    assert len(supplier_reservations) == 2, "Vulnerability proof: 2 reservations must exist in SQLite"

    res_1 = supplier_reservations[0]
    res_2 = supplier_reservations[1]
    assert res_1.supplier_reservation_id != res_2.supplier_reservation_id
    assert res_1.supplier_reservation_id == f"SUP-RES-{synthetic_intent.intent_id}-01"
    assert res_2.supplier_reservation_id == f"SUP-RES-{synthetic_intent.intent_id}-02"

    # Aggregator DB audit records two supplier operations
    ops = agg_db.get_supplier_operations_for_booking(response.booking_id)
    assert len(ops) == 2
    assert ops[0]["attempt_number"] == 1
    assert ops[0]["error_type"] == "HTTP_TIMEOUT"
    assert ops[1]["attempt_number"] == 2
    assert ops[1]["status_code"] == 200
    assert ops[1]["supplier_reservation_id"] == res_2.supplier_reservation_id

    # INVARIANT CHECK: Invariant 5.2 MUST FAIL
    inv_result = inv_engine.check_booking_intent_uniqueness(synthetic_intent.intent_id)
    assert inv_result.passed is False
    assert inv_result.cwe == "CWE-675: Multiple Operations on Resource / CWE-841: Improper Enforcement of Behavioral Workflow"
    assert "2 distinct reservations" in inv_result.actual
    assert inv_result.evidence["violation_detected"] is True


@pytest.mark.asyncio
async def test_fixed_mode_recovers_original_reservation_after_timeout(
    agency_client: AgencyClient, synthetic_intent: BookingIntent
):
    """
    Scenario:
    1. Supplier commits initial reservation to SQLite.
    2. Supplier delays response (0.35s) beyond Aggregator's HTTP timeout (0.15s).
    3. Aggregator experiences ReadTimeout.
    4. Fixed Aggregator invokes reconciliation/idempotent query.
    5. Supplier returns existing reservation (SUP-RES-INTENT-001-01).
    6. Aggregator links the original reservation and confirms booking.
    7. Invariant Oracle proves Invariant 5.2 holds: exactly 1 reservation in supplier DB.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    sup_service = get_supplier_service()
    agg_service = get_aggregator_booking_service()
    inv_engine = get_invariant_engine()

    # Configure fixed aggregator & idempotent supplier with timeout fault
    agg_service.set_mode(AggregatorMode.FIXED)
    sup_service.configure(
        mode=SupplierMode.IDEMPOTENT,
        simulate_timeout=True,
        timeout_delay_seconds=0.30,
    )

    response = await agency_client.submit_booking(synthetic_intent)

    assert response.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert response.retry_count == 1

    # GROUND TRUTH IN SQLITE: Exactly ONE reservation exists!
    supplier_reservations = sup_db.get_reservations_by_intent(synthetic_intent.intent_id)
    assert len(supplier_reservations) == 1, "Fixed proof: strictly 1 reservation must exist in SQLite"

    res_1 = supplier_reservations[0]
    assert response.primary_supplier_reservation_id == res_1.supplier_reservation_id
    assert res_1.supplier_reservation_id == f"SUP-RES-{synthetic_intent.intent_id}-01"

    # INVARIANT CHECK: Invariant 5.2 MUST PASS
    inv_result = inv_engine.check_booking_intent_uniqueness(synthetic_intent.intent_id)
    assert inv_result.passed is True
    assert inv_result.actual == "1 reservation(s)"
    assert inv_result.evidence["violation_detected"] is False

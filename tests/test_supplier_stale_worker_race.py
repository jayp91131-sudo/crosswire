"""
Test suite for Supplier Capability Boundary & Stale Worker Race
Specification: MASTER_PLAN_V2.md Section 6 (Scenario 1) & Release Gate Remediation Task 1 & 3

Tests all three capability modes:
A. IDEMPOTENT_WITH_LOOKUP
   - Worker A pauses before or during dispatch.
   - Worker B takes over expired claim.
   - Both workers may reach supplier with same stable key and identical payload.
   - Upstream supplier deduplicates (request count = 2, reservation count = 1).
   - Conflicting payload with same key is rejected with 409 Conflict.
   - Stale Worker A is rejected by database fencing token (AggregatorFencingError).

B. LOOKUP_ONLY
   - Worker A pauses before dispatch. Worker B takes over.
   - Negative lookup leads to SUPPLIER_UNKNOWN / RECONCILIATION_REQUIRED, never a blind redispatch.
   - Worker A resumes and dispatches: exactly 1 supplier reservation created.
   - Request accepted by supplier but not yet visible to lookup: Worker B does not redispatch.
   - Positive lookup: Worker B adopts existing reservation without redispatch.

C. NEITHER
   - Worker A pauses before dispatch. Worker B takes over.
   - Neither lookup nor idempotency supported: Worker B halts immediately in RECONCILIATION_REQUIRED.
   - Worker A never resumes: booking remains in reconciliation, 0 reservations created.
   - Invalid, unknown, or missing capability configuration fails closed to NEITHER.
"""

import pytest
import asyncio
from datetime import datetime, timedelta

from target_platform.models import (
    BookingIntent,
    BookingClaimStatus,
    AggregatorMode,
    SupplierMode,
    SupplierCapability,
    SupplierBookingRequest,
    SupplierReservation,
    AggregatorBookingRecord,
    AggregatorFencingError,
    AggregatorIdempotencyConflictError,
    TransactionStatus,
)
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.booking_service import AggregatorBookingService
from target_platform.aggregator.supplier_client import SupplierClient, AggregatorSupplierTimeoutError
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.supplier.app import supplier_app
import httpx
from httpx import ASGITransport


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport):
        self.transport = transport
        self.post_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/supplier/v1/reservations" in str(request.url) and not str(request.url).endswith("/cancel"):
            self.post_count += 1
        return await self.transport.handle_async_request(request)


@pytest.fixture
def clean_dbs():
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()
    sup_svc = get_supplier_service()
    sup_svc.set_mode(SupplierMode.IDEMPOTENT)
    yield agg_db, sup_db
    agg_db.reset()
    sup_db.reset()
    sup_svc.set_mode(SupplierMode.VULNERABLE)


@pytest.fixture
def supplier_transport():
    return ASGITransport(app=supplier_app)


@pytest.fixture
def supplier_client(supplier_transport):
    return SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=supplier_transport,
        capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP,
    )


def _make_intent(intent_id: str = "INTENT-RACE-001") -> BookingIntent:
    return BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="hotel_taj",
        hotel_name="Taj Mahal Palace",
        room_type="ocean_view",
        check_in="2026-12-10",
        check_out="2026-12-15",
        amount=25000.0,
        amount_minor=2500000,
        currency="INR",
        guest_name="Ratan Tata",
        idempotency_key=f"idemp_{intent_id}",
        correlation_id=f"corr_{intent_id}",
        payment_method="WALLET",
    )


# =========================================================================
# Mode A: IDEMPOTENT_WITH_LOOKUP Tests
# =========================================================================

@pytest.mark.asyncio
async def test_mode_a_stale_worker_external_supplier_race(clean_dbs, supplier_client):
    """
    Mode A: IDEMPOTENT_WITH_LOOKUP
    Worker A pauses immediately before external supplier dispatch.
    Worker B recovers the expired claim and dispatches.
    Worker A resumes and dispatches late request with identical stable key.
    Supplier deduplicates: request count = 2, reservation count = 1.
    Worker A's stale database write is rejected with AggregatorFencingError.
    """
    agg_db, sup_db = clean_dbs
    intent = _make_intent("INTENT-STALE-DISPATCH-001")
    agg_db.save_booking_intent(intent)

    # 1. Worker A acquires claim with fencing_token = 1
    worker_a_svc = AggregatorBookingService(supplier_client=supplier_client)
    worker_a_id = worker_a_svc.owner_token
    acquired_a, claim_a = agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_a",
        operation_id="op_a",
        booking_id=f"TBOH-{intent.intent_id}",
        owner_token=worker_a_id,
        lease_duration_seconds=0.1,  # short lease for test
    )
    assert acquired_a is True
    assert claim_a.fencing_token == 1

    # Worker A updates claim to SUPPLIER_REQUEST_PENDING
    agg_db.update_claim_status(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_fencing_token=1,
        expected_owner_token=worker_a_id,
        lease_duration_seconds=0.1,
    )

    # 2. Worker A prepares request but is PAUSED before dispatch
    stable_supplier_idemp_key = f"IDEMP-TXN-{intent.intent_id}"
    req_a = SupplierBookingRequest(
        intent_id=intent.intent_id,
        hotel_id=intent.hotel_id,
        room_type=intent.room_type,
        guest_name=intent.guest_name,
        amount=intent.amount,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        correlation_id=intent.correlation_id,
        idempotency_key=stable_supplier_idemp_key,
    )

    # 3. Worker A's lease expires
    await asyncio.sleep(0.15)

    # 4. Worker B arrives, finds lease expired, and steals claim
    worker_b_svc = AggregatorBookingService(supplier_client=supplier_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    assert stolen is True
    assert claim_b.fencing_token == 2

    # Worker B executes recovery: queries supplier (returns None), dispatches with stable key
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )
    assert rec_b.status == TransactionStatus.SUPPLIER_CONFIRMED

    # Check supplier state after Worker B: exactly 1 reservation exists
    reservations_after_b = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(reservations_after_b) == 1
    res_b_id = reservations_after_b[0].supplier_reservation_id

    # 5. Worker A RESUMES and dispatches its delayed request to the supplier!
    res_a = await supplier_client.call_create_reservation(req_a)

    # The supplier recognized the stable idempotency key and returned the existing reservation!
    assert res_a.supplier_reservation_id == res_b_id

    # Check supplier database: still EXACTLY 1 reservation exists (no duplication!)
    reservations_after_a = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(reservations_after_a) == 1

    # 6. Worker A tries to update database with its stale fencing_token = 1
    with pytest.raises(AggregatorFencingError) as exc_info:
        agg_db.update_claim_status(
            agency_id=intent.agency_id,
            idempotency_key=intent.idempotency_key,
            status=BookingClaimStatus.COMPLETED,
            expected_fencing_token=1,  # Worker A's stale token
            expected_owner_token=worker_a_id,
        )
    assert "fencing token mismatch" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_mode_a_supplier_idempotency_payload_conflict_rejected(clean_dbs, supplier_client):
    """
    Mode A: IDEMPOTENT_WITH_LOOKUP
    Verifies that reusing the same supplier idempotency key with a modified payload
    is rejected with 409 Conflict (AggregatorIdempotencyConflictError).
    """
    idemp_key = "IDEMP-CONFLICT-TEST-001"
    req1 = SupplierBookingRequest(
        intent_id="INTENT-ORIGINAL",
        hotel_id="hotel_taj",
        room_type="ocean_view",
        guest_name="Guest One",
        amount=100.0,
        amount_minor=10000,
        currency="INR",
        correlation_id="corr-1",
        idempotency_key=idemp_key,
    )
    res1 = await supplier_client.call_create_reservation(req1)
    assert res1.supplier_reservation_id is not None

    # Modify payload (amount changed) under identical idempotency key
    req2 = SupplierBookingRequest(
        intent_id="INTENT-ORIGINAL",
        hotel_id="hotel_taj",
        room_type="ocean_view",
        guest_name="Guest One",
        amount=999.0,
        amount_minor=99900,
        currency="INR",
        correlation_id="corr-2",
        idempotency_key=idemp_key,
    )

    with pytest.raises(AggregatorIdempotencyConflictError) as exc_info:
        await supplier_client.call_create_reservation(req2)
    assert "idempotency conflict" in str(exc_info.value).lower()


# =========================================================================
# Mode B: LOOKUP_ONLY Tests
# =========================================================================

@pytest.mark.asyncio
async def test_mode_b_lookup_only_negative_lookup_halts_recovery(clean_dbs, supplier_transport):
    """
    Mode B: LOOKUP_ONLY
    Worker A pauses before dispatch.
    Worker B recovers expired claim.
    Negative lookup on non-idempotent provider halts recovery in SUPPLIER_UNKNOWN / RECONCILIATION_REQUIRED.
    Worker B DOES NOT dispatch a new request.
    Worker A resumes and dispatches: exactly 1 supplier request and 1 reservation.
    """
    agg_db, sup_db = clean_dbs
    sup_svc = get_supplier_service()
    sup_svc.set_mode(SupplierMode.VULNERABLE)  # Non-idempotent supplier!

    intent = _make_intent("INTENT-LOOKUP-ONLY-001")
    agg_db.save_booking_intent(intent)

    lookup_client = SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=supplier_transport,
        capability=SupplierCapability.LOOKUP_ONLY,
    )
    assert lookup_client.supports_lookup is True
    assert lookup_client.supports_idempotency is False

    # 1. Worker A acquires claim with short lease
    worker_a_svc = AggregatorBookingService(supplier_client=lookup_client)
    acquired_a, claim_a = agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_b1",
        operation_id="op_b1",
        booking_id=f"TBOH-{intent.intent_id}",
        owner_token=worker_a_svc.owner_token,
        lease_duration_seconds=0.1,
    )
    agg_db.update_claim_status(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_fencing_token=1,
        expected_owner_token=worker_a_svc.owner_token,
        lease_duration_seconds=0.1,
    )

    # 2. Worker A pauses. Lease expires.
    await asyncio.sleep(0.15)

    # 3. Worker B reclaims expired lease
    worker_b_svc = AggregatorBookingService(supplier_client=lookup_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    assert stolen is True

    # 4. Worker B attempts recovery: lookup returns None (404).
    # MUST NOT redispatch because supports_idempotency is False!
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )
    assert rec_b.status == TransactionStatus.SUPPLIER_UNKNOWN
    claim_after_b = agg_db.get_booking_claim(intent.agency_id, intent.idempotency_key)
    assert claim_after_b.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    # Supplier-side reservations must be ZERO so far
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 0

    # 5. Worker A resumes and performs the original dispatch
    req_a = SupplierBookingRequest(
        intent_id=intent.intent_id,
        hotel_id=intent.hotel_id,
        room_type=intent.room_type,
        guest_name=intent.guest_name,
        amount=intent.amount,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        correlation_id=intent.correlation_id,
    )
    res_a = await lookup_client.call_create_reservation(req_a)
    assert res_a.status == "CONFIRMED"

    # Exactly 1 supplier reservation exists in the supplier database!
    sup_reservations = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(sup_reservations) == 1


@pytest.mark.asyncio
async def test_mode_b_lookup_only_in_flight_accepted_not_visible_no_duplicate(clean_dbs, supplier_transport):
    """
    Mode B: LOOKUP_ONLY
    Worker A dispatches to supplier. Supplier accepts and commits reservation.
    However, the reservation is not yet indexed / visible to lookup (simulated 404).
    Worker B recovers expired claim.
    Worker B MUST NOT blindly redispatch; halts in RECONCILIATION_REQUIRED.
    Total reservations on supplier = exactly 1 (no duplicate creation).
    """
    agg_db, sup_db = clean_dbs
    sup_svc = get_supplier_service()
    sup_svc.set_mode(SupplierMode.VULNERABLE)

    intent = _make_intent("INTENT-LOOKUP-INFLIGHT-001")
    agg_db.save_booking_intent(intent)

    lookup_client = SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=supplier_transport,
        capability=SupplierCapability.LOOKUP_ONLY,
    )

    # 1. Worker A dispatches to supplier
    req_a = SupplierBookingRequest(
        intent_id=intent.intent_id,
        hotel_id=intent.hotel_id,
        room_type=intent.room_type,
        guest_name=intent.guest_name,
        amount=intent.amount,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        correlation_id=intent.correlation_id,
    )
    res_a = await lookup_client.call_create_reservation(req_a)
    assert res_a.status == "CONFIRMED"
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1

    # 2. Worker A crashed before saving record. Claim expired.
    acquired_a, claim_a = agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_inflight",
        operation_id="op_inflight",
        booking_id=f"TBOH-{intent.intent_id}",
        owner_token="worker_a",
        lease_duration_seconds=0.1,
    )
    await asyncio.sleep(0.15)

    # 3. Worker B steals claim
    worker_b_svc = AggregatorBookingService(supplier_client=lookup_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    assert stolen is True

    # Temporarily simulate lookup not yet indexing this reservation (returns None)
    orig_lookup = sup_svc.lookup_by_reference
    sup_svc.lookup_by_reference = lambda ref: None

    try:
        rec_b = await worker_b_svc.recover_external_dispatch(
            intent=intent,
            claim=claim_b,
            owner_token=worker_b_svc.owner_token,
            fencing_token=claim_b.fencing_token,
            mode=AggregatorMode.FIXED,
        )
        assert rec_b.status == TransactionStatus.SUPPLIER_UNKNOWN
        # Crucial: Supplier MUST NOT have received a redispatch creating a 2nd reservation!
        assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1
    finally:
        sup_svc.lookup_by_reference = orig_lookup


@pytest.mark.asyncio
async def test_mode_b_lookup_only_positive_lookup_adopts_reservation(clean_dbs, supplier_transport):
    """
    Mode B: LOOKUP_ONLY
    Worker A dispatched, reservation is committed and visible on supplier.
    Worker B recovers expired claim.
    Positive lookup adopts existing reservation; no duplicate dispatch.
    """
    agg_db, sup_db = clean_dbs
    sup_svc = get_supplier_service()
    sup_svc.set_mode(SupplierMode.VULNERABLE)

    intent = _make_intent("INTENT-LOOKUP-ADOPT-001")
    agg_db.save_booking_intent(intent)

    lookup_client = SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=supplier_transport,
        capability=SupplierCapability.LOOKUP_ONLY,
    )

    # Worker A dispatched and committed reservation on supplier
    req_a = SupplierBookingRequest(
        intent_id=intent.intent_id,
        hotel_id=intent.hotel_id,
        room_type=intent.room_type,
        guest_name=intent.guest_name,
        amount=intent.amount,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        correlation_id=intent.correlation_id,
    )
    res_a = await lookup_client.call_create_reservation(req_a)

    # Claim expires
    agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_adopt",
        operation_id="op_adopt",
        booking_id=f"TBOH-{intent.intent_id}",
        owner_token="worker_a",
        lease_duration_seconds=0.1,
    )
    await asyncio.sleep(0.15)

    # Worker B recovers
    worker_b_svc = AggregatorBookingService(supplier_client=lookup_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )

    # Adopted successfully!
    assert rec_b.status == TransactionStatus.SUPPLIER_CONFIRMED
    assert rec_b.primary_supplier_reservation_id == res_a.supplier_reservation_id
    # Exactly 1 reservation in supplier DB
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1


# =========================================================================
# Mode C: NEITHER Tests
# =========================================================================

@pytest.mark.asyncio
async def test_mode_c_neither_worker_b_takes_over_and_does_not_dispatch(clean_dbs, supplier_transport):
    """
    Mode C: NEITHER
    Worker A pauses before dispatch.
    Worker B takes over expired claim.
    Neither lookup nor idempotency supported: Worker B MUST NOT dispatch.
    Worker A resumes and performs at most the original dispatch (1 reservation).
    """
    agg_db, sup_db = clean_dbs
    sup_svc = get_supplier_service()
    sup_svc.set_mode(SupplierMode.VULNERABLE)

    intent = _make_intent("INTENT-NEITHER-001")
    agg_db.save_booking_intent(intent)

    neither_client = SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=supplier_transport,
        capability=SupplierCapability.NEITHER,
    )
    assert neither_client.supports_lookup is False
    assert neither_client.supports_idempotency is False

    # Worker A acquires claim with short lease
    worker_a_svc = AggregatorBookingService(supplier_client=neither_client)
    agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_c1",
        operation_id="op_c1",
        booking_id=f"TBOH-{intent.intent_id}",
        owner_token=worker_a_svc.owner_token,
        lease_duration_seconds=0.1,
    )
    agg_db.update_claim_status(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_fencing_token=1,
        expected_owner_token=worker_a_svc.owner_token,
        lease_duration_seconds=0.1,
    )

    await asyncio.sleep(0.15)

    # Worker B recovers expired claim
    worker_b_svc = AggregatorBookingService(supplier_client=neither_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    assert stolen is True

    # Worker B runs recovery: MUST NOT dispatch
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )
    assert rec_b.status == TransactionStatus.SUPPLIER_UNKNOWN
    claim_after_b = agg_db.get_booking_claim(intent.agency_id, intent.idempotency_key)
    assert claim_after_b.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 0

    # Worker A resumes and performs original dispatch
    req_a = SupplierBookingRequest(
        intent_id=intent.intent_id,
        hotel_id=intent.hotel_id,
        room_type=intent.room_type,
        guest_name=intent.guest_name,
        amount=intent.amount,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        correlation_id=intent.correlation_id,
    )
    res_a = await neither_client.call_create_reservation(req_a)
    assert res_a.status == "CONFIRMED"

    # Exactly 1 reservation created on supplier
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1


@pytest.mark.asyncio
async def test_mode_c_neither_worker_a_never_resumes_remains_in_reconciliation(clean_dbs, supplier_transport):
    """
    Mode C: NEITHER
    Worker A crashes and never resumes.
    Worker B recovers expired claim.
    Booking remains in reconciliation rather than being retried automatically.
    Zero supplier reservations created.
    """
    agg_db, sup_db = clean_dbs
    intent = _make_intent("INTENT-NEITHER-DEAD-001")
    agg_db.save_booking_intent(intent)

    neither_client = SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=supplier_transport,
        capability=SupplierCapability.NEITHER,
    )

    # Worker A acquired claim, marked SUPPLIER_REQUEST_PENDING, then crashed
    agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_c2",
        operation_id="op_c2",
        booking_id=f"TBOH-{intent.intent_id}",
        owner_token="dead_worker_a",
        lease_duration_seconds=0.1,
    )
    agg_db.update_claim_status(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_fencing_token=1,
        expected_owner_token="dead_worker_a",
        lease_duration_seconds=0.1,
    )

    await asyncio.sleep(0.15)

    # Worker B recovers
    worker_b_svc = AggregatorBookingService(supplier_client=neither_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )

    assert rec_b.status == TransactionStatus.SUPPLIER_UNKNOWN
    claim_final = agg_db.get_booking_claim(intent.agency_id, intent.idempotency_key)
    assert claim_final.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    # Zero reservations created!
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 0


def test_supplier_client_fails_closed_on_invalid_or_missing_capability():
    """
    Verifies that unknown, missing, or invalid capability configuration
    fails closed to NEITHER.
    """
    # 1. Missing capability argument defaults to NEITHER
    client_default = SupplierClient()
    assert client_default.capability == SupplierCapability.NEITHER
    assert client_default.supports_idempotency is False
    assert client_default.supports_lookup is False

    # 2. None capability fails closed to NEITHER
    client_none = SupplierClient(capability=None)
    assert client_none.capability == SupplierCapability.NEITHER
    assert client_none.supports_idempotency is False
    assert client_none.supports_lookup is False

    # 3. Invalid string fails closed to NEITHER
    client_invalid = SupplierClient(capability="UNSUPPORTED_SUPER_CAPABILITY")
    assert client_invalid.capability == SupplierCapability.NEITHER
    assert client_invalid.supports_idempotency is False
    assert client_invalid.supports_lookup is False

    # 4. Explicit valid capabilities work
    client_idemp = SupplierClient(capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP)
    assert client_idemp.supports_idempotency is True
    assert client_idemp.supports_lookup is True

    client_lookup = SupplierClient(capability=SupplierCapability.LOOKUP_ONLY)
    assert client_lookup.supports_idempotency is False
    assert client_lookup.supports_lookup is True


@pytest.mark.asyncio
async def test_mode_c_neither_ambiguous_commit_lost_response_recovery(clean_dbs, supplier_transport):
    """
    In-Process ASGI HTTP Integration Test — Stale-Worker Delayed Continuation and Fencing Rejection.
    (Note: For real localhost TCP process death, see test_tcp_neither_commit_timeout_process_death_recovery).

    1. Worker A acquires claim with fencing token F=1 and transitions to SUPPLIER_REQUEST_PENDING.
    2. Worker A dispatches an HTTP booking POST over an in-process ASGI transport (CountingTransport wrapping ASGI application).
    3. Mock supplier commits 1 reservation to SQLite, then simulates lost response/read timeout.
    4. Worker A catches the HTTP read timeout and is delayed / paused before recording aggregator confirmation.
       (Worker A remains alive but delayed; it is NOT terminated).
    5. Lease expires during Worker A's pause.
    6. Worker B recovers the expired claim with fencing token F=2.
    7. Its adapter declares NEITHER (no lookup, no idempotency).
    8. Worker B halts immediately in SUPPLIER_UNKNOWN / RECONCILIATION_REQUIRED without redispatching.
    9. Assertions verified:
       - In-process ASGI HTTP booking POST count = 1.
       - Supplier reservation count = 1.
       - Worker B makes zero additional booking POST requests.
       - Worker B's recovery path does not claim supplier confirmation without evidence.
       - Delayed Worker A unpauses/resumes and attempts a stale database commit with F=1.
       - Aggregator rejects Worker A's stale write with AggregatorFencingError (token 1 < 2).
       - Final claim owner is Worker B, fencing token is 2, status is RECONCILIATION_REQUIRED.
    """
    agg_db, sup_db = clean_dbs
    sup_svc = get_supplier_service()
    sup_svc.set_mode(SupplierMode.VULNERABLE)

    intent = _make_intent("INTENT-NEITHER-HTTP-AMBIG-001")
    agg_db.save_booking_intent(intent)

    # Counting transport interceptor to track actual HTTP POST calls to /supplier/v1/reservations
    counting_transport = CountingTransport(supplier_transport)

    neither_client_a = SupplierClient(
        base_url="http://mock-supplier",
        timeout=0.05,
        transport=counting_transport,
        capability=SupplierCapability.NEITHER,
    )
    assert neither_client_a.supports_lookup is False
    assert neither_client_a.supports_idempotency is False

    # 1. Worker A acquires claim with fencing_token = 1
    worker_a_svc = AggregatorBookingService(supplier_client=neither_client_a)
    booking_id = f"TBOH-{intent.intent_id}"
    acquired_a, claim_a = agg_db.acquire_booking_claim(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        payload_hash="hash_neither_http_ambig",
        operation_id="op_neither_http_ambig_1",
        booking_id=booking_id,
        owner_token=worker_a_svc.owner_token,
        lease_duration_seconds=0.1,  # Short lease
    )
    assert acquired_a is True
    assert claim_a.fencing_token == 1

    # Save initial pending booking record
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent.intent_id,
            agency_id=intent.agency_id,
            status=TransactionStatus.BOOKING_PENDING,
            amount=intent.amount,
            amount_minor=intent.amount_minor,
            currency=intent.currency,
            payment_method="WALLET",
            correlation_id=intent.correlation_id,
        )
    )

    # Worker A transitions claim to SUPPLIER_REQUEST_PENDING
    agg_db.update_claim_status(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_fencing_token=1,
        expected_owner_token=worker_a_svc.owner_token,
        lease_duration_seconds=0.1,
    )

    # 2. Worker A sends genuine HTTP booking request to mock supplier
    # Mock supplier commits reservation to SQLite FIRST, then delays to induce client timeout/dropped response
    sup_svc.simulate_timeout = True
    sup_svc.timeout_delay_seconds = 0.3

    req_a = SupplierBookingRequest(
        intent_id=intent.intent_id,
        hotel_id=intent.hotel_id,
        room_type=intent.room_type,
        guest_name=intent.guest_name,
        amount=intent.amount,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        correlation_id=intent.correlation_id,
    )

    with pytest.raises(AggregatorSupplierTimeoutError):
        await neither_client_a.call_create_reservation(req_a, timeout_override=0.05)

    sup_svc.simulate_timeout = False

    # Assertions on genuine HTTP dispatch:
    # 1 HTTP POST was delivered and processed by mock supplier
    assert counting_transport.post_count == 1
    # 1 reservation was committed by mock supplier (NO direct DB insertion!)
    sup_reservations = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(sup_reservations) == 1
    assert sup_reservations[0].status == "CONFIRMED"

    # Worker A crashes/terminates before aggregator confirmation.

    # 3. Worker A's lease expires
    await asyncio.sleep(0.15)

    # 4. Worker B takes over the expired claim with fencing token F=2
    neither_client_b = SupplierClient(
        base_url="http://mock-supplier",
        timeout=1.0,
        transport=counting_transport,
        capability=SupplierCapability.NEITHER,
    )
    worker_b_svc = AggregatorBookingService(supplier_client=neither_client_b)

    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    assert stolen is True
    assert claim_b.fencing_token == 2
    assert claim_b.owner_token == worker_b_svc.owner_token

    # 5. Worker B executes recover_external_dispatch with NEITHER capability
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )

    # Assertions on Worker B recovery:
    # Zero additional HTTP POST requests made by Worker B
    assert counting_transport.post_count == 1
    # Supplier reservation count remains strictly 1
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1
    # Recovery path does not claim supplier confirmation without evidence
    assert rec_b.status == TransactionStatus.SUPPLIER_UNKNOWN
    claim_after_recovery = agg_db.get_booking_claim(intent.agency_id, intent.idempotency_key)
    assert claim_after_recovery.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    assert claim_after_recovery.owner_token == worker_b_svc.owner_token
    assert claim_after_recovery.fencing_token == 2

    # 6. Stale Worker A resumes and tries to commit using stale token F=1
    with pytest.raises(AggregatorFencingError) as exc_info:
        agg_db.update_claim_status(
            agency_id=intent.agency_id,
            idempotency_key=intent.idempotency_key,
            status=BookingClaimStatus.COMPLETED,
            expected_fencing_token=1,
            expected_owner_token=worker_a_svc.owner_token,
        )
    assert "Stale worker fenced out" in str(exc_info.value)

    # 7. Final state verification distinguishing supplier commit from aggregator state commit
    claim_final = agg_db.get_booking_claim(intent.agency_id, intent.idempotency_key)
    assert claim_final.owner_token == worker_b_svc.owner_token
    assert claim_final.fencing_token == 2
    assert claim_final.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    # Supplier-side: 1 committed reservation
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1
    # Aggregator-side: booking remains unconfirmed (SUPPLIER_UNKNOWN)
    booking_final = agg_db.get_booking_record(booking_id)
    assert booking_final.status == TransactionStatus.SUPPLIER_UNKNOWN
    assert claim_final.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    assert len(sup_db.get_reservations_by_intent(intent.intent_id)) == 1

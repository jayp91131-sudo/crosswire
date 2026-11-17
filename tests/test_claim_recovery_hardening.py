"""
Milestone 2.2 Final Recovery Hardening — Claim Recovery Test Suite.
Verifies safe multi-process claim recovery, fencing token fencing, and adoption
of orphaned supplier reservations without duplicate bookings.
"""

import pytest
import asyncio
import uuid
import time
import hashlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.booking_service import (
    AggregatorBookingService,
    get_aggregator_booking_service,
)
from target_platform.models import (
    BookingIntent,
    AggregatorMode,
    SupplierMode,
    TransactionStatus,
    BookingClaimStatus,
    WalletHoldStatus,
    AggregatorFencingError,
    SupplierReservation,
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


def _compute_hash(intent: BookingIntent) -> str:
    amount_minor = intent.amount_minor or int(intent.amount * 100)
    payload_str = f"{intent.hotel_id}|{intent.room_type}|{intent.check_in}|{intent.check_out}|{amount_minor}|{intent.currency}|{intent.guest_name}"
    return hashlib.sha256(payload_str.encode()).hexdigest()


@pytest.mark.asyncio
async def test_worker_crashes_immediately_after_claim_acquisition(supplier_client):
    """
    Worker 1 acquires claim (CLAIMED) with 1s lease, but crashes before dispatching supplier request.
    Worker 2 arrives after lease expiration:
    - Worker 2 steals the expired pre-dispatch claim.
    - Increments fencing token.
    - Dispatches to supplier.
    - Successfully confirms booking.
    - Exactly 1 supplier reservation created.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()

    agency_id = "agency_alpha"
    idempotency_key = "idemp_crash_pre_dispatch_001"
    intent_id = "intent_crash_001"
    booking_id = f"TBOH-{intent_id}"

    intent = BookingIntent(
        intent_id=intent_id,
        agency_id=agency_id,
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Crash Recovered Guest",
        correlation_id="corr_crash_1",
        payment_method="WALLET",
        idempotency_key=idempotency_key,
    )
    p_hash = _compute_hash(intent)

    # Worker 1 acquires claim with very short lease (1 second)
    w1_token = "worker_1_pid1001"
    acquired, claim = agg_db.acquire_booking_claim(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        operation_id=f"op_{intent_id}",
        booking_id=booking_id,
        owner_token=w1_token,
        correlation_id=intent.correlation_id,
        lease_duration_seconds=1.0,
    )
    assert acquired is True
    assert claim.owner_token == w1_token
    assert claim.fencing_token == 1
    assert claim.status == BookingClaimStatus.CLAIMED

    # Worker 1 crashes (simulated by doing nothing and letting lease expire)
    time.sleep(1.2)

    # Worker 2 creates booking service instance
    w2_service = AggregatorBookingService(supplier_client=supplier_client)
    w2_service.set_mode(AggregatorMode.FIXED)
    w2_service.set_wallet_mode(AggregatorMode.FIXED)
    w2_token = w2_service.owner_token

    booking = await w2_service.process_booking_intent(intent)

    assert booking.status in (TransactionStatus.SUPPLIER_CONFIRMED, TransactionStatus.VOUCHER_ISSUED)
    assert booking.voucher_id is not None

    # Check database claim state
    final_claim = agg_db.get_booking_claim(agency_id, idempotency_key)
    assert final_claim.status == BookingClaimStatus.COMPLETED
    assert final_claim.owner_token == w2_token
    assert final_claim.fencing_token >= 2  # Fencing token was incremented

    # Exactly 1 supplier reservation
    reservations = sup_db.get_reservations_by_intent(intent_id)
    assert len(reservations) == 1

    # Invariants hold
    engine = get_invariant_engine()
    assert engine.check_wallet_available_balance(agency_id).passed
    assert engine.check_wallet_holds_bounded(agency_id).passed
    assert engine.check_wallet_total_accounting(agency_id).passed
    assert engine.check_inventory_capacity("htl_mumbai_grand", "standard_room").passed


@pytest.mark.asyncio
async def test_worker_crashes_after_supplier_dispatch_with_lost_response(supplier_client):
    """
    Worker 1 dispatches to supplier (SUPPLIER_REQUEST_PENDING), supplier creates reservation,
    but response is lost and Worker 1 crashes.
    Worker 2 arrives after lease expiration:
    - Worker 2 adopts the claim via reclaim_expired_claim_for_external_recovery.
    - Worker 2 reconciles with supplier via reference (intent_id).
    - Supplier reports existing reservation.
    - Worker 2 adopts reservation (SUPPLIER_RESERVATION_ADOPTED), captures wallet hold.
    - Booking is CONFIRMED with the existing reservation.
    - No duplicate supplier reservation created.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()

    agency_id = "agency_alpha"
    idempotency_key = "idemp_crash_mid_dispatch_002"
    intent_id = "intent_mid_dispatch_002"
    booking_id = f"TBOH-{intent_id}"

    intent = BookingIntent(
        intent_id=intent_id,
        agency_id=agency_id,
        hotel_id="htl_mumbai_grand",
        hotel_name="Grand Mumbai",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Orphan Guest",
        correlation_id="corr_lost_ack_2",
        payment_method="WALLET",
        idempotency_key=idempotency_key,
    )
    p_hash = _compute_hash(intent)

    w1_token = "worker_1_lost_ack"
    acquired, w1_claim = agg_db.acquire_booking_claim(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        operation_id=f"op_{intent_id}",
        booking_id=booking_id,
        owner_token=w1_token,
        correlation_id=intent.correlation_id,
        lease_duration_seconds=1.0,
    )
    assert acquired is True

    # Worker 1 creates wallet hold
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=500000,
        currency="INR",
        idempotency_key=f"hold_{intent_id}",
    )

    # Worker 1 transitions claim to SUPPLIER_REQUEST_PENDING with 1s lease
    agg_db.update_claim_status(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_owner_token=w1_token,
        expected_fencing_token=w1_claim.fencing_token,
        lease_duration_seconds=1.0,
    )

    # Supplier actually processed and committed reservation
    sup_res = SupplierReservation(
        supplier_reservation_id=f"SUP-{intent_id}",
        intent_id=intent_id,
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        guest_name="Orphan Guest",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    sup_db.save_reservation(sup_res)

    # Worker 1 crashes, lease expires
    time.sleep(1.2)

    # Worker 2 starts
    w2_service = AggregatorBookingService(supplier_client=supplier_client)
    w2_service.set_mode(AggregatorMode.FIXED)
    w2_service.set_wallet_mode(AggregatorMode.FIXED)

    booking = await w2_service.process_booking_intent(intent)

    assert booking.status in (TransactionStatus.SUPPLIER_CONFIRMED, TransactionStatus.VOUCHER_ISSUED)
    assert booking.primary_supplier_reservation_id == sup_res.supplier_reservation_id

    # Check supplier database: strictly ONE reservation exists for this intent
    all_res = sup_db.get_reservations_by_intent(intent_id)
    assert len(all_res) == 1

    # Check wallet hold: must be CAPTURED
    db_hold = agg_db.get_wallet_hold(hold.hold_id)
    assert db_hold.status == WalletHoldStatus.CAPTURED

    # Invariants pass
    engine = get_invariant_engine()
    assert engine.check_wallet_available_balance(agency_id).passed
    assert engine.check_wallet_holds_bounded(agency_id).passed
    assert engine.check_wallet_total_accounting(agency_id).passed
    assert engine.check_inventory_capacity("htl_mumbai_grand", "standard_room").passed


@pytest.mark.asyncio
async def test_stale_zombie_worker_blocked_by_fencing_token(supplier_client):
    """
    Worker 1 acquires claim, gets paused (GC pause or network freeze).
    Lease expires, Worker 2 recovers claim and increments fencing token to 2.
    Worker 1 wakes up and attempts to update claim status -> AggregatorFencingError raised.
    """
    agg_db = get_aggregator_db()

    agency_id = "agency_alpha"
    idempotency_key = "idemp_fencing_zombie_003"
    p_hash = "dummy_hash_003"
    op_id = "op_zombie_003"
    bkg_id = "bkg_zombie_003"

    w1_token = "worker_1_zombie"
    acquired, w1_claim = agg_db.acquire_booking_claim(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        operation_id=op_id,
        booking_id=bkg_id,
        owner_token=w1_token,
        lease_duration_seconds=1.0,
    )
    assert acquired is True
    assert w1_claim.fencing_token == 1

    time.sleep(1.2)  # Lease expires

    w2_token = "worker_2_fencing"
    # Worker 2 reclaims pre-dispatch claim
    acquired2, w2_claim = agg_db.acquire_booking_claim(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        operation_id=op_id,
        booking_id=bkg_id,
        owner_token=w2_token,
        lease_duration_seconds=10.0,
    )
    assert acquired2 is True
    assert w2_claim.owner_token == w2_token
    assert w2_claim.fencing_token == 2

    # Worker 1 wakes up from pause and tries to mutate claim with stale token
    with pytest.raises(AggregatorFencingError) as exc_info:
        agg_db.update_claim_status(
            agency_id=agency_id,
            idempotency_key=idempotency_key,
            status=BookingClaimStatus.SUPPLIER_CONFIRMED,
            expected_owner_token=w1_token,
            expected_fencing_token=w1_claim.fencing_token,
        )

    assert "Stale worker fenced out" in str(exc_info.value)

    # Claim remains untouched by zombie worker
    current_claim = agg_db.get_booking_claim(agency_id, idempotency_key)
    assert current_claim.owner_token == w2_token
    assert current_claim.fencing_token == 2


@pytest.mark.asyncio
async def test_two_workers_concurrent_claim_recovery(supplier_client):
    """
    Worker 1 crashed leaving an expired claim in SUPPLIER_REQUEST_PENDING.
    Workers 2 and 3 both try to reclaim the expired claim concurrently.
    Exactly ONE worker succeeds in acquiring external recovery ownership;
    the other receives (False, claim) and must wait for the winner.
    """
    agg_db = get_aggregator_db()

    agency_id = "agency_alpha"
    idempotency_key = "idemp_race_reclaim_004"
    p_hash = "dummy_hash_004"
    op_id = "op_race_004"
    bkg_id = "bkg_race_004"

    # Expired claim
    acquired, w1_claim = agg_db.acquire_booking_claim(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        operation_id=op_id,
        booking_id=bkg_id,
        owner_token="worker_1_dead",
        lease_duration_seconds=1.0,
    )
    assert acquired is True
    agg_db.update_claim_status(
        agency_id=agency_id,
        idempotency_key=idempotency_key,
        status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
        expected_owner_token="worker_1_dead",
        expected_fencing_token=w1_claim.fencing_token,
        lease_duration_seconds=1.0,
    )

    time.sleep(1.2)  # Expired

    # Concurrent reclaim attempts
    def try_reclaim(worker_id):
        return agg_db.reclaim_expired_claim_for_external_recovery(
            agency_id=agency_id,
            idempotency_key=idempotency_key,
            new_owner_token=worker_id,
            lease_duration_seconds=10.0,
        )

    # Run both simultaneously
    loop = asyncio.get_event_loop()
    r2, r3 = await asyncio.gather(
        loop.run_in_executor(None, try_reclaim, "worker_2_concurrent"),
        loop.run_in_executor(None, try_reclaim, "worker_3_concurrent"),
    )

    # Exactly one succeeded
    successes = [claim for (acq, claim) in [r2, r3] if acq]
    failures = [claim for (acq, claim) in [r2, r3] if not acq]
    assert len(successes) == 1
    assert len(failures) == 1

    winner = successes[0]
    assert winner.fencing_token == 2
    assert winner.owner_token in ["worker_2_concurrent", "worker_3_concurrent"]

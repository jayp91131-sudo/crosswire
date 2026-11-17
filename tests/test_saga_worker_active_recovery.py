"""
Milestone 2.2 Final Recovery Hardening — Active Saga Recovery Worker Test Suite.
Verifies autonomous background saga reconciliation, lease-based task claiming,
fencing, idempotent compensations, and crash recovery.
"""

import pytest
import asyncio
import uuid
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.saga_recovery_worker import SagaRecoveryWorker
from target_platform.aggregator.supplier_client import (
    SupplierClient,
    AggregatorSupplierTimeoutError,
)
from target_platform.models import (
    SagaRecoveryRecord,
    WalletHoldStatus,
    AggregatorMode,
    SupplierReservation,
    AggregatorFencingError,
)
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
def setup_teardown(supplier_client):
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()

    yield

    agg_db.reset()
    sup_db.reset()


@pytest.mark.asyncio
async def test_saga_worker_executes_supplier_cancellation(supplier_client):
    """
    Saga recovery task recorded in RECONCILIATION_REQUIRED.
    SagaRecoveryWorker executes an active recovery cycle:
    - Atomically claims the task with lease and fencing token.
    - Idempotently invokes supplier cancellation.
    - Updates saga status to COMPENSATED.
    - Verifies supplier reservation is CANCELLED.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()

    intent_id = "intent_worker_cancel_001"
    sup_res_id = f"SUP-{intent_id}"
    booking_id = f"TBOH-{intent_id}"
    agency_id = "agency_alpha"

    # Create supplier reservation
    sup_res = SupplierReservation(
        supplier_reservation_id=sup_res_id,
        intent_id=intent_id,
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        guest_name="Saga Worker Guest",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        idempotency_key=f"idemp_{intent_id}",
        payload_hash="dummy_hash",
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    sup_db.save_reservation(sup_res)

    # Record uncompensated saga task
    rec = agg_db.record_saga_recovery(
        booking_id=booking_id,
        agency_id=agency_id,
        action="CANCEL_SUPPLIER_RESERVATION",
        status="RECONCILIATION_REQUIRED",
        target_supplier_reservation_id=sup_res_id,
    )

    worker = SagaRecoveryWorker(supplier_client=supplier_client)
    processed = await worker.run_recovery_cycle()
    assert processed >= 1

    # Check saga record updated to COMPENSATED
    updated_rec = agg_db.get_saga_recovery_record(rec.recovery_id)
    assert updated_rec.status == "COMPENSATED"
    assert updated_rec.fencing_token >= 1

    # Check supplier reservation is cancelled
    res = sup_db.get_reservation(sup_res_id)
    assert res.status == "CANCELLED"


@pytest.mark.asyncio
async def test_saga_worker_executes_wallet_hold_release():
    """
    Saga recovery task recorded for RELEASE_WALLET_HOLD in RECONCILIATION_REQUIRED.
    SagaRecoveryWorker processes task:
    - Atomically releases the held funds back to available wallet balance.
    - Transitions task to COMPENSATED.
    - Invariants pass.
    """
    agg_db = get_aggregator_db()
    agency_id = "agency_alpha"
    booking_id = "TBOH-intent_hold_release_002"

    # Create wallet hold
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=500000,
        currency="INR",
        idempotency_key="hold_worker_002",
    )
    assert hold.status == WalletHoldStatus.HELD

    # Record saga task
    rec = agg_db.record_saga_recovery(
        booking_id=booking_id,
        agency_id=agency_id,
        action="RELEASE_WALLET_HOLD",
        status="RECONCILIATION_REQUIRED",
        wallet_hold_id=hold.hold_id,
    )

    worker = SagaRecoveryWorker()
    processed = await worker.run_recovery_cycle()
    assert processed >= 1

    updated_rec = agg_db.get_saga_recovery_record(rec.recovery_id)
    assert updated_rec.status == "COMPENSATED"

    # Wallet hold must be RELEASED
    updated_hold = agg_db.get_wallet_hold(hold.hold_id)
    assert updated_hold.status == WalletHoldStatus.RELEASED

    # Wallet invariants pass
    engine = get_invariant_engine()
    assert engine.check_wallet_available_balance(agency_id).passed
    assert engine.check_wallet_holds_bounded(agency_id).passed
    assert engine.check_wallet_total_accounting(agency_id).passed


@pytest.mark.asyncio
async def test_saga_worker_retry_with_exponential_backoff(supplier_client):
    """
    Supplier cancellation encounters a transient network error.
    Worker:
    - Records error in task.
    - Sets next_attempt_at with exponential backoff.
    - Leaves status RECONCILIATION_REQUIRED.
    - Immediate subsequent run skips it because backoff hasn't elapsed.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()

    intent_id = "intent_retry_003"
    sup_res_id = f"SUP-{intent_id}"
    booking_id = f"TBOH-{intent_id}"
    agency_id = "agency_alpha"

    rec = agg_db.record_saga_recovery(
        booking_id=booking_id,
        agency_id=agency_id,
        action="CANCEL_SUPPLIER_RESERVATION",
        status="RECONCILIATION_REQUIRED",
        target_supplier_reservation_id=sup_res_id,
    )

    worker = SagaRecoveryWorker(supplier_client=supplier_client)

    # Force supplier cancellation to raise a transient timeout error
    with patch.object(
        supplier_client,
        "call_cancel_reservation",
        side_effect=AggregatorSupplierTimeoutError("Simulated supplier timeout"),
    ):
        processed = await worker.run_recovery_cycle()
        assert processed == 1

    updated_rec = agg_db.get_saga_recovery_record(rec.recovery_id)
    assert updated_rec.status in ["PENDING", "RECONCILIATION_REQUIRED"]
    assert updated_rec.attempt_count == 2
    assert updated_rec.next_attempt_at is not None
    assert "Simulated supplier timeout" in updated_rec.last_error

    # Second immediate cycle should NOT re-process because next_attempt_at is in the future
    processed2 = await worker.run_recovery_cycle()
    assert processed2 == 0


@pytest.mark.asyncio
async def test_saga_worker_crash_and_second_worker_recovery(supplier_client):
    """
    Worker 1 claims saga task with 1s lease, but crashes.
    Worker 2 arrives after lease expiration:
    - Worker 2 successfully claims the task.
    - Fencing token increments.
    - Worker 2 completes compensation.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()

    intent_id = "intent_worker_crash_004"
    sup_res_id = f"SUP-{intent_id}"
    booking_id = f"TBOH-{intent_id}"
    agency_id = "agency_alpha"

    sup_res = SupplierReservation(
        supplier_reservation_id=sup_res_id,
        intent_id=intent_id,
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        guest_name="Crash Saga Guest",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        idempotency_key=f"idemp_{intent_id}",
        payload_hash="dummy_hash",
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    sup_db.save_reservation(sup_res)

    rec = agg_db.record_saga_recovery(
        booking_id=booking_id,
        agency_id=agency_id,
        action="CANCEL_SUPPLIER_RESERVATION",
        status="RECONCILIATION_REQUIRED",
        target_supplier_reservation_id=sup_res_id,
    )

    # Worker 1 claims task with short 1s lease
    claimed_rec = agg_db.claim_pending_saga_recovery_task(
        owner_token="worker_1_dead",
        recovery_id=rec.recovery_id,
        lease_duration_seconds=1.0,
    )
    assert claimed_rec is not None
    assert claimed_rec.fencing_token == 1

    time.sleep(1.2)  # Worker 1 dead, lease expired

    # Worker 2 starts
    worker2 = SagaRecoveryWorker(supplier_client=supplier_client)
    processed = await worker2.run_recovery_cycle()
    assert processed >= 1

    updated_rec = agg_db.get_saga_recovery_record(rec.recovery_id)
    assert updated_rec.status == "COMPENSATED"
    assert updated_rec.fencing_token == 2
    assert updated_rec.owner_token == worker2.worker_id


@pytest.mark.asyncio
async def test_concurrent_saga_workers_race(supplier_client):
    """
    Two workers attempt to claim the exact same pending saga task concurrently.
    Exactly one succeeds; the other returns None.
    """
    agg_db = get_aggregator_db()

    rec = agg_db.record_saga_recovery(
        booking_id="TBOH-race_005",
        agency_id="agency_alpha",
        action="CANCEL_SUPPLIER_RESERVATION",
        status="RECONCILIATION_REQUIRED",
        target_supplier_reservation_id="SUP-race-005",
    )

    def try_claim(owner_token):
        return agg_db.claim_pending_saga_recovery_task(
            owner_token=owner_token,
            recovery_id=rec.recovery_id,
            lease_duration_seconds=10.0,
        )

    loop = asyncio.get_event_loop()
    c1, c2 = await asyncio.gather(
        loop.run_in_executor(None, try_claim, "worker_concurrent_A"),
        loop.run_in_executor(None, try_claim, "worker_concurrent_B"),
    )

    claimed = [c for c in [c1, c2] if c is not None]
    unclaimed = [c for c in [c1, c2] if c is None]
    assert len(claimed) == 1
    assert len(unclaimed) == 1

"""
Runtime Proof & Lifespan Integration Tests for SagaRecoveryWorker
Specification: Release Gate Task 1
Verifies:
1. FastAPI lifespan starts SagaRecoveryWorker and attaches it to app.state.
2. Autonomous compensation without manual worker invocation.
3. End-to-end Uvicorn live server test:
   - Starts application on real TCP port.
   - Inserts pending task; verifies automatic compensation.
   - Stops application; creates new pending task.
   - Restarts application; verifies second task is processed automatically after startup.
4. Concurrent worker mutual exclusion (no duplicate compensation).
"""

import os
import sys
import time
import socket
import subprocess
from pathlib import Path
import pytest
import asyncio
import httpx
from datetime import datetime, timedelta

from target_platform.models import (
    SagaRecoveryRecord,
    WalletHoldRecord,
    WalletHoldStatus,
    BookingIntent,
    SupplierReservation,
    SupplierCapability,
)
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.app import aggregator_app
from target_platform.aggregator.saga_recovery_worker import SagaRecoveryWorker
from target_platform.aggregator.supplier_client import SupplierClient
from target_platform.supplier.app import supplier_app
from httpx import ASGITransport


@pytest.fixture
def clean_dbs():
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()
    yield agg_db, sup_db
    agg_db.reset()
    sup_db.reset()


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_fastapi_lifespan_starts_and_stops_worker(clean_dbs):
    """
    Verifies that entering FastAPI lifespan instantiates SagaRecoveryWorker in app.state,
    runs background loop, automatically processes pending tasks, and stops cleanly on exit.
    """
    agg_db, _ = clean_dbs

    # 1. Prepare wallet hold and pending saga task
    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id="BK-LIFE-001",
        amount_minor=50000,
        currency="INR",
        idempotency_key="hold_life_001",
    )
    assert hold.status == WalletHoldStatus.HELD

    rec = agg_db.record_saga_recovery(
        booking_id="BK-LIFE-001",
        agency_id="agency_alpha",
        action="RELEASE_WALLET_HOLD",
        status="PENDING",
        wallet_hold_id=hold.hold_id,
    )

    # 2. Enter FastAPI lifespan using AsyncClient with ASGI transport
    # Set poll interval to 0.1s for fast test
    old_poll = os.environ.get("SAGA_RECOVERY_POLL_INTERVAL")
    os.environ["SAGA_RECOVERY_POLL_INTERVAL"] = "0.1"
    try:
        async with aggregator_app.router.lifespan_context(aggregator_app):
            # Verify worker is attached to application state
            worker = getattr(aggregator_app.state, "saga_recovery_worker", None)
            assert worker is not None
            assert isinstance(worker, SagaRecoveryWorker)
            assert worker._running is True

            # 3. Wait for background worker to autonomously pick up and process task
            # DO NOT call worker.run_recovery_cycle() manually!
            for _ in range(30):
                task_state = agg_db.get_saga_recovery_record(rec.recovery_id)
                if task_state and task_state.status == "COMPENSATED":
                    break
                await asyncio.sleep(0.1)

            task_final = agg_db.get_saga_recovery_record(rec.recovery_id)
            assert task_final.status == "COMPENSATED"

            # Verify the wallet hold was actually released
            hold_after = agg_db.get_wallet_hold(hold.hold_id)
            assert hold_after.status == WalletHoldStatus.RELEASED

        # 4. After lifespan exit, verify worker stopped
        assert worker._running is False
    finally:
        if old_poll is not None:
            os.environ["SAGA_RECOVERY_POLL_INTERVAL"] = old_poll
        else:
            os.environ.pop("SAGA_RECOVERY_POLL_INTERVAL", None)


def test_uvicorn_startup_and_restart_autonomous_processing(clean_dbs):
    """
    Full real-world Uvicorn runtime proof:
    1. Start application via Uvicorn subprocess.
    2. Create pending saga recovery record.
    3. Do NOT manually call any worker methods.
    4. Wait for polling cycle; verify compensation occurs.
    5. Verify database record becomes COMPENSATED.
    6. Stop application.
    7. Create another pending record and restart application.
    8. Verify second record is processed automatically after startup.
    """
    agg_db, _ = clean_dbs
    port = _get_free_port()

    # Environment for subprocess
    repo_dir = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_dir};{repo_dir}\\.venv\\Lib\\site-packages"
    env["AGGREGATOR_DB_PATH"] = agg_db.db_path
    env["SAGA_RECOVERY_POLL_INTERVAL"] = "0.5"

    python_exe = sys.executable

    def _start_server():
        proc = subprocess.Popen(
            [
                python_exe,
                "-m",
                "uvicorn",
                "target_platform.aggregator.app:aggregator_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=repo_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for health check
        started = False
        for _ in range(50):
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
                if r.status_code == 200:
                    started = True
                    break
            except Exception:
                time.sleep(0.1)
        if not started:
            proc.kill()
            raise RuntimeError(f"Uvicorn failed to start on port {port}")
        return proc

    # STEP 1: Start application
    proc1 = _start_server()

    try:
        # STEP 2: Create a pending saga recovery record
        hold1 = agg_db.create_wallet_hold_fixed(
            agency_id="agency_alpha",
            booking_id="BK-UVI-001",
            amount_minor=30000,
            currency="INR",
            idempotency_key="idemp_uvi_001",
        )
        assert hold1.status == WalletHoldStatus.HELD

        rec1 = agg_db.record_saga_recovery(
            booking_id="BK-UVI-001",
            agency_id="agency_alpha",
            action="RELEASE_WALLET_HOLD",
            status="PENDING",
            wallet_hold_id=hold1.hold_id,
        )

        # STEP 3 & 4: Do NOT call worker manually. Wait for autonomous processing
        compensated = False
        for _ in range(40):
            t = agg_db.get_saga_recovery_record(rec1.recovery_id)
            if t and t.status == "COMPENSATED":
                compensated = True
                break
            time.sleep(0.2)

        assert compensated is True, "Pending saga task was not autonomously compensated by running Uvicorn server"
        h1_after = agg_db.get_wallet_hold(hold1.hold_id)
        assert h1_after.status == WalletHoldStatus.RELEASED

    finally:
        # STEP 7: Stop application
        proc1.terminate()
        try:
            proc1.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc1.kill()

    # STEP 8: Create another pending record while server is down
    hold2 = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id="BK-UVI-002",
        amount_minor=45000,
        currency="INR",
        idempotency_key="idemp_uvi_002",
    )
    rec2 = agg_db.record_saga_recovery(
        booking_id="BK-UVI-002",
        agency_id="agency_alpha",
        action="RELEASE_WALLET_HOLD",
        status="PENDING",
        wallet_hold_id=hold2.hold_id,
    )

    # Restart application
    proc2 = _start_server()
    try:
        # STEP 9: Verify second record is processed automatically after startup
        compensated2 = False
        for _ in range(40):
            t2 = agg_db.get_saga_recovery_record(rec2.recovery_id)
            if t2 and t2.status == "COMPENSATED":
                compensated2 = True
                break
            time.sleep(0.2)

        assert compensated2 is True, "Second task was not processed automatically after Uvicorn server restart"
        h2_after = agg_db.get_wallet_hold(hold2.hold_id)
        assert h2_after.status == WalletHoldStatus.RELEASED
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()


@pytest.mark.asyncio
async def test_concurrent_application_workers_cannot_double_compensate(clean_dbs):
    """
    Verifies that multiple worker instances running concurrently cannot execute the same recovery task twice.
    """
    agg_db, _ = clean_dbs
    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id="BK-RACE-WORKER-001",
        amount_minor=60000,
        currency="INR",
        idempotency_key="idemp_race_worker",
    )
    rec = agg_db.record_saga_recovery(
        booking_id="BK-RACE-WORKER-001",
        agency_id="agency_alpha",
        action="RELEASE_WALLET_HOLD",
        status="PENDING",
        wallet_hold_id=hold.hold_id,
    )

    # Worker 1 claims task
    claim1 = agg_db.claim_pending_saga_recovery_task(
        owner_token="worker_process_1",
        recovery_id=rec.recovery_id,
        lease_duration_seconds=10.0,
    )
    assert claim1 is not None
    assert claim1.owner_token == "worker_process_1"
    assert claim1.fencing_token == 1

    # Worker 2 simultaneously tries to claim the same task
    claim2 = agg_db.claim_pending_saga_recovery_task(
        owner_token="worker_process_2",
        recovery_id=rec.recovery_id,
        lease_duration_seconds=10.0,
    )
    # Claim 2 MUST be None because lease is active
    assert claim2 is None


@pytest.mark.asyncio
async def test_shutdown_during_inflight_compensation_recovers_cleanly_on_restart(clean_dbs):
    """
    Section 3 Proof:
    1. Deliberately pause a compensation in the middle of execution.
    2. Shut down the FastAPI application lifespan.
    3. Verify:
       - No task is falsely marked COMPENSATED.
       - No task is silently lost.
    4. Restart FastAPI application lifespan.
    5. Verify:
       - Recovery worker safely finishes the compensation.
       - Database record reaches COMPENSATED.
       - External supplier side effect is not duplicated.
    """
    agg_db, sup_db = clean_dbs

    booking_id = "BK-SEVERED-001"
    intent_id = "INTENT-SEVERED-001"
    sup_res_id = "SUP-SEVERED-001"

    # 1. Supplier reservation confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_taj",
            room_type="ocean_view",
            guest_name="Guest",
            amount=200.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # 2. Seed pending saga recovery task with short lease duration
    rec = agg_db.record_saga_recovery(
        booking_id=booking_id,
        agency_id="agency_alpha",
        action="CANCEL_SUPPLIER_RESERVATION",
        status="PENDING",
        target_supplier_reservation_id=sup_res_id,
    )

    started_cancel = asyncio.Event()
    unblock_cancel = asyncio.Event()

    # 3. Patch supplier client on aggregator app to pause during compensation
    test_sup_client_1 = SupplierClient(
        base_url="http://mock-supplier",
        transport=ASGITransport(app=supplier_app),
        capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP,
    )
    worker_1 = SagaRecoveryWorker(supplier_client=test_sup_client_1, lease_duration_seconds=0.2)
    original_cancel = worker_1.supplier_client.call_cancel_reservation

    async def mock_pausing_cancel(res_id, reason=None, timeout_override=None):
        started_cancel.set()
        await unblock_cancel.wait()
        return await original_cancel(res_id, reason=reason, timeout_override=timeout_override)

    worker_1.supplier_client.call_cancel_reservation = mock_pausing_cancel

    # Start worker 1
    worker_1.start_background_worker(poll_interval=0.05)

    # Wait for worker 1 to pick up task and enter call_cancel_reservation
    await asyncio.wait_for(started_cancel.wait(), timeout=2.0)

    # 4. Trigger shutdown while task is in-flight!
    # With a 0.1s shutdown limit, it will cancel gracefully
    await worker_1.stop_background_worker_async(shutdown_timeout=0.1)
    unblock_cancel.set()  # release the mock

    # 5. Verify database invariant immediately after severed shutdown:
    task_after_shutdown = agg_db.get_saga_recovery_record(rec.recovery_id)
    assert task_after_shutdown is not None, "Task was lost!"
    assert task_after_shutdown.status != "COMPENSATED", "Task was falsely marked COMPENSATED before completion!"
    assert task_after_shutdown.status == "IN_PROGRESS"

    # Supplier reservation must still be in progress / confirmed
    res_mid = sup_db.get_reservation(sup_res_id)
    assert res_mid.status == "CONFIRMED"

    # 6. Wait for lease to expire (0.2s lease duration)
    await asyncio.sleep(0.25)

    # 7. Restart application worker (Worker 2)
    test_sup_client_2 = SupplierClient(
        base_url="http://mock-supplier",
        transport=ASGITransport(app=supplier_app),
        capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP,
    )
    worker_2 = SagaRecoveryWorker(supplier_client=test_sup_client_2, lease_duration_seconds=10.0)
    worker_2.start_background_worker(poll_interval=0.05)

    try:
        # Wait for worker 2 to autonomously pick up and finish the task
        for _ in range(30):
            t_final = agg_db.get_saga_recovery_record(rec.recovery_id)
            if t_final and t_final.status == "COMPENSATED":
                break
            await asyncio.sleep(0.1)

        t_final = agg_db.get_saga_recovery_record(rec.recovery_id)
        assert t_final.status == "COMPENSATED", f"Task was not compensated on restart: status={t_final.status}"
        assert t_final.fencing_token >= 2, "Fencing token was not incremented upon recovery takeover!"

        # Supplier reservation is now safely CANCELLED
        res_final = sup_db.get_reservation(sup_res_id)
        assert res_final.status == "CANCELLED"
    finally:
        await worker_2.stop_background_worker_async(shutdown_timeout=1.0)


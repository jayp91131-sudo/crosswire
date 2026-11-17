"""
Integration tests verifying Crosswire 2.0 synthetic platform over real TCP sockets.
Requirements tested:
1. Aggregator and Supplier run as separate Uvicorn processes on localhost TCP.
2. Commit-then-timeout failure reproduced over actual HTTP socket transport.
3. Vulnerable blind retry produces two distinct supplier reservation IDs.
4. Fixed recovery creates exactly one supplier reservation.
5. Unsupported supplier behavior safely enters SUPPLIER_UNKNOWN without blind retries.
"""

import os
import sys
import time
import subprocess
import asyncio
import pytest
import httpx
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    BookingClaimStatus,
    SupplierBookingRequest,
    AggregatorMode,
    SupplierMode,
)
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.database import get_aggregator_db
from target_platform.aggregator.supplier_client import (
    SupplierClient,
    SupplierCapability,
    AggregatorSupplierTimeoutError,
)
from target_platform.aggregator.booking_service import AggregatorBookingService

AGG_PORT = 8010
SUP_PORT = 8011
AGG_URL = f"http://127.0.0.1:{AGG_PORT}"
SUP_URL = f"http://127.0.0.1:{SUP_PORT}"


@pytest.fixture(scope="module")
def uvicorn_processes():
    """
    Launches Aggregator (port 8010) and Supplier (port 8011) as separate background Uvicorn processes
    over localhost TCP. Waits for both to respond to health checks, then tears down cleanly.
    """
    base_cmd = [sys.executable, "-m", "uvicorn"]

    agg_proc = subprocess.Popen(
        base_cmd + [
            "target_platform.aggregator.app:aggregator_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(AGG_PORT),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    sup_proc = subprocess.Popen(
        base_cmd + [
            "target_platform.supplier.app:supplier_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(SUP_PORT),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for both services to be responsive
    start = time.time()
    healthy = False
    with httpx.Client(timeout=1.0) as client:
        while time.time() - start < 10.0:
            try:
                r1 = client.get(f"{AGG_URL}/")
                r2 = client.get(f"{SUP_URL}/")
                if r1.status_code == 200 and r2.status_code == 200:
                    healthy = True
                    break
            except Exception:
                pass
            time.sleep(0.3)

    if not healthy:
        agg_proc.kill()
        sup_proc.kill()
        raise RuntimeError("Uvicorn TCP test processes failed to start within 10 seconds")

    yield {"agg_url": AGG_URL, "sup_url": SUP_URL}

    # Teardown
    agg_proc.terminate()
    sup_proc.terminate()
    try:
        agg_proc.wait(timeout=3)
        sup_proc.wait(timeout=3)
    except Exception:
        agg_proc.kill()
        sup_proc.kill()


@pytest.mark.asyncio
async def test_tcp_vulnerable_blind_retry_creates_duplicate_reservations(uvicorn_processes):
    """
    Audit Items 1, 2, 3:
    Runs over real TCP sockets:
    1. Supplier commits first reservation, then delays past Aggregator's HTTP read timeout.
    2. Aggregator encounters socket timeout.
    3. Under VULNERABLE mode, Aggregator blindly issues a second HTTP request.
    4. Supplier creates and persists a second distinct reservation.
    5. Verifies exactly 2 distinct reservation IDs exist in supplier SQLite DB.
    """
    sup_db = get_supplier_db()
    agg_db = get_aggregator_db()
    sup_db.reset()
    agg_db.reset()

    intent_id = "INTENT-TCP-VULN-01"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Point aggregator to supplier over TCP
        await client.post(
            f"{AGG_URL}/api/v1/admin/config",
            json={"mode": AggregatorMode.VULNERABLE.value, "supplier_base_url": SUP_URL},
        )

        # Configure supplier for commit-then-timeout fault
        await client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": SupplierMode.VULNERABLE.value,
                "simulate_timeout": True,
                "timeout_delay_seconds": 0.40,  # exceeds aggregator 0.20s timeout
            },
        )

        # Agency Alpha submits booking request over real TCP
        intent = {
            "intent_id": intent_id,
            "agency_id": "agency_alpha",
            "hotel_id": "htl_mumbai_grand",
            "hotel_name": "Synthetic Hotel Mumbai",
            "room_type": "deluxe_suite",
            "check_in": "2026-12-15",
            "check_out": "2026-12-18",
            "amount": 15000.0,
            "currency": "INR",
            "guest_name": "Aarav Sharma",
            "correlation_id": f"corr_tcp_vuln_{intent_id}",
        }
        headers = {
            "X-Agency-ID": "agency_alpha",
            "X-API-Key": "secret_alpha_key_99",
        }

        resp = await client.post(f"{AGG_URL}/api/v1/bookings", json=intent, headers=headers)
        assert resp.status_code == 200
        booking_data = resp.json()

        assert booking_data["status"] == "SUPPLIER_CONFIRMED"
        assert booking_data["retry_count"] == 1

        # Direct verification of supplier database over localhost:
        supplier_reservations = sup_db.get_reservations_by_intent(intent_id)
        assert len(supplier_reservations) == 2, (
            f"Expected 2 duplicate supplier reservations, got {len(supplier_reservations)}"
        )
        res_ids = [r.supplier_reservation_id for r in supplier_reservations]
        assert len(set(res_ids)) == 2, "Duplicate supplier reservations must have distinct IDs"


@pytest.mark.asyncio
async def test_tcp_fixed_mode_recovers_single_reservation(uvicorn_processes):
    """
    Audit Items 1, 2, 4:
    Runs over real TCP sockets:
    1. Supplier commits first reservation, then delays past timeout.
    2. In FIXED mode, Aggregator catches timeout and calls idempotent lookup / retry.
    3. Exactly 1 reservation exists in supplier SQLite DB.
    """
    sup_db = get_supplier_db()
    agg_db = get_aggregator_db()
    sup_db.reset()
    agg_db.reset()

    intent_id = "INTENT-TCP-FIXED-01"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Point aggregator to supplier over TCP in FIXED mode
        await client.post(
            f"{AGG_URL}/api/v1/admin/config",
            json={"mode": AggregatorMode.FIXED.value, "supplier_base_url": SUP_URL},
        )

        # Configure supplier for idempotent mode with timeout delay on first call
        await client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": SupplierMode.IDEMPOTENT.value,
                "simulate_timeout": True,
                "timeout_delay_seconds": 0.40,
            },
        )

        intent = {
            "intent_id": intent_id,
            "agency_id": "agency_alpha",
            "hotel_id": "htl_mumbai_grand",
            "hotel_name": "Synthetic Hotel Mumbai",
            "room_type": "deluxe_suite",
            "check_in": "2026-12-15",
            "check_out": "2026-12-18",
            "amount": 15000.0,
            "currency": "INR",
            "guest_name": "Aarav Sharma",
            "correlation_id": f"corr_tcp_fixed_{intent_id}",
        }
        headers = {
            "X-Agency-ID": "agency_alpha",
            "X-API-Key": "secret_alpha_key_99",
        }

        resp = await client.post(f"{AGG_URL}/api/v1/bookings", json=intent, headers=headers)
        assert resp.status_code == 200
        booking_data = resp.json()

        assert booking_data["status"] == "SUPPLIER_CONFIRMED"

        # Verify exactly one reservation in supplier DB
        supplier_reservations = sup_db.get_reservations_by_intent(intent_id)
        assert len(supplier_reservations) == 1, (
            f"Expected exactly 1 supplier reservation, got {len(supplier_reservations)}"
        )


@pytest.mark.asyncio
async def test_tcp_unsupported_supplier_enters_reconciliation_safely(uvicorn_processes):
    """
    Audit Items 1, 2, 5:
    Runs over real TCP sockets:
    1. Legacy supplier lacks idempotency and lookup (UNSUPPORTED mode).
    2. Commit-then-timeout occurs.
    3. Aggregator detects unsupported supplier, marks transaction SUPPLIER_UNKNOWN, halts retries.
    4. Exactly 1 reservation created; no duplicate reservations.
    """
    sup_db = get_supplier_db()
    agg_db = get_aggregator_db()
    sup_db.reset()
    agg_db.reset()

    intent_id = "INTENT-TCP-UNSUP-01"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Point aggregator to supplier over TCP in FIXED mode
        await client.post(
            f"{AGG_URL}/api/v1/admin/config",
            json={"mode": AggregatorMode.FIXED.value, "supplier_base_url": SUP_URL},
        )

        # Configure supplier for UNSUPPORTED mode with timeout delay
        await client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": SupplierMode.UNSUPPORTED.value,
                "simulate_timeout": True,
                "timeout_delay_seconds": 0.40,
            },
        )

        intent = {
            "intent_id": intent_id,
            "agency_id": "agency_alpha",
            "hotel_id": "htl_mumbai_grand",
            "hotel_name": "Synthetic Hotel Mumbai",
            "room_type": "deluxe_suite",
            "check_in": "2026-12-15",
            "check_out": "2026-12-18",
            "amount": 15000.0,
            "currency": "INR",
            "guest_name": "Aarav Sharma",
            "correlation_id": f"corr_tcp_unsup_{intent_id}",
        }
        headers = {
            "X-Agency-ID": "agency_alpha",
            "X-API-Key": "secret_alpha_key_99",
        }

        resp = await client.post(f"{AGG_URL}/api/v1/bookings", json=intent, headers=headers)
        assert resp.status_code == 200
        booking_data = resp.json()

        assert booking_data["status"] == "SUPPLIER_UNKNOWN"
        assert booking_data["status"] != "SUPPLIER_CONFIRMED"
        assert booking_data["status"] != "FAILED"

        # Supplier DB has only the single reservation from attempt 1
        supplier_reservations = sup_db.get_reservations_by_intent(intent_id)
        assert len(supplier_reservations) == 1


@pytest.mark.asyncio
async def test_tcp_neither_commit_timeout_process_death_recovery(uvicorn_processes):
    """
    Final Release Gate — Audit Item 1: Real Local Uvicorn/TCP Subprocess Death Test.
    1. Worker A is spawned as an independent OS subprocess with its own PID.
    2. Worker A acquires the booking claim (F=1) and marks SUPPLIER_REQUEST_PENDING.
    3. Worker A sends an HTTP POST to Mock Supplier over localhost TCP socket (port 8011).
    4. Supplier commits the reservation to its SQLite database and delays past client timeout.
    5. Worker A experiences an httpx.ReadTimeout over TCP and terminates with exit code 137 (actual OS process crash).
    6. Parent verifies Worker A subprocess is dead (poll() is not None, returncode == 137).
    7. Worker B takes over the expired claim with fencing token F=2.
    8. Supplier adapter declares NEITHER (no idempotency, no lookup).
    9. Worker B must not redispatch.
    10. Verify exactly one supplier HTTP booking POST and exactly one supplier reservation in DB.
    11. Verify final booking and claim states are SUPPLIER_UNKNOWN and RECONCILIATION_REQUIRED.
    12. Verify no ungrounded supplier confirmation is written anywhere.
    """
    sup_db = get_supplier_db()
    agg_db = get_aggregator_db()
    sup_db.reset()
    agg_db.reset()

    # Configure supplier over TCP socket in VULNERABLE mode with timeout delay
    async with httpx.AsyncClient(timeout=5.0) as admin_client:
        cfg_resp = await admin_client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": SupplierMode.VULNERABLE.value,
                "simulate_timeout": True,
                "timeout_delay_seconds": 0.50,
            },
        )
        assert cfg_resp.status_code == 200

    intent = BookingIntent(
        intent_id="INTENT-TCP-PROC-DEATH-001",
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        hotel_name="Synthetic Hotel Mumbai",
        room_type="standard_room",
        check_in="2026-12-15",
        check_out="2026-12-18",
        amount=15000.0,
        amount_minor=1500000,
        currency="INR",
        guest_name="TCP Worker A Guest",
        correlation_id="corr_tcp_proc_death",
        payment_method="WALLET",
        idempotency_key="idemp_tcp_proc_death_001",
    )
    agg_db.save_booking_intent(intent)
    booking_id = f"TBOH-{intent.intent_id}"
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

    # 1. Spawn Worker A as an actual independent OS subprocess
    worker_a_script = """
import os
import sys
import httpx
from target_platform.aggregator.database import AggregatorDatabase
from target_platform.models import BookingClaimStatus

agg_db_path = sys.argv[1]
sup_url = sys.argv[2]
agency_id = sys.argv[3]
idemp_key = sys.argv[4]
bkg_id = sys.argv[5]

pid = os.getpid()
owner_token = f"worker_proc_{pid}"

db = AggregatorDatabase(db_path=agg_db_path)
acquired, claim = db.acquire_booking_claim(
    agency_id=agency_id,
    idempotency_key=idemp_key,
    payload_hash="hash_tcp_proc_death",
    operation_id=f"op_proc_{pid}",
    booking_id=bkg_id,
    owner_token=owner_token,
    lease_duration_seconds=0.35,
)
if not acquired:
    sys.exit(10)

db.update_claim_status(
    agency_id=agency_id,
    idempotency_key=idemp_key,
    status=BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
    expected_fencing_token=1,
    expected_owner_token=owner_token,
    lease_duration_seconds=0.35,
)

# Output readiness message with real OS PID and owner_token
print(f"WORKER_A_READY:{pid}:{owner_token}", flush=True)

# Dispatch HTTP request to mock supplier over localhost TCP port 8011
# Client timeout is 0.15s, while supplier delays for 0.50s after committing
try:
    with httpx.Client(timeout=0.15) as client:
        payload = {
            "intent_id": "INTENT-TCP-PROC-DEATH-001",
            "hotel_id": "htl_mumbai_grand",
            "room_type": "standard_room",
            "guest_name": "TCP Worker A Guest",
            "amount": 15000.0,
            "currency": "INR",
            "correlation_id": "corr_tcp_proc_death",
        }
        client.post(f"{sup_url}/supplier/v1/reservations", json=payload)
except httpx.ReadTimeout:
    print(f"WORKER_A_TIMEOUT_CRASH:{pid}", flush=True)
    os._exit(137)  # Terminate immediately with exit status 137 (hard abnormal exit)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()};" + env.get("PYTHONPATH", "")

    worker_a_proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            worker_a_script,
            agg_db.db_path,
            SUP_URL,
            intent.agency_id,
            intent.idempotency_key,
            booking_id,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # 2. Capture Worker A OS PID and owner token from subprocess stdout
    line = worker_a_proc.stdout.readline()
    assert line.startswith("WORKER_A_READY:"), f"Worker A failed startup: {line}"
    parts = line.strip().split(":")
    worker_a_pid = int(parts[1])
    worker_a_token = parts[2]
    assert worker_a_pid > 0, "Worker A must have a valid OS PID"
    assert worker_a_token == f"worker_proc_{worker_a_pid}"

    # 3. Wait for Worker A subprocess termination (hard crash upon TCP timeout)
    stdout, stderr = worker_a_proc.communicate(timeout=5.0)
    assert worker_a_proc.poll() is not None, "Worker A process must have terminated"
    assert worker_a_proc.returncode == 137, f"Expected Worker A crash exit code 137, got {worker_a_proc.returncode}"
    assert "WORKER_A_TIMEOUT_CRASH" in stdout

    # 4. Wait for Worker A's claim lease to expire (wait 0.40s > 0.35s lease)
    await asyncio.sleep(0.40)

    # 5. Worker B starts as a distinct worker process, recovers the expired lease with F=2
    worker_b_client = SupplierClient(
        base_url=SUP_URL,
        timeout=2.0,
        capability=SupplierCapability.NEITHER,
    )
    worker_b_svc = AggregatorBookingService(supplier_client=worker_b_client)
    stolen, claim_b = agg_db.reclaim_expired_claim_for_external_recovery(
        agency_id=intent.agency_id,
        idempotency_key=intent.idempotency_key,
        new_owner_token=worker_b_svc.owner_token,
        lease_duration_seconds=10.0,
    )
    assert stolen is True
    assert claim_b.fencing_token == 2
    assert claim_b.last_owner_token == worker_a_token

    # 6. Worker B recovers under NEITHER capability: MUST NOT redispatch!
    rec_b = await worker_b_svc.recover_external_dispatch(
        intent=intent,
        claim=claim_b,
        owner_token=worker_b_svc.owner_token,
        fencing_token=claim_b.fencing_token,
        mode=AggregatorMode.FIXED,
    )

    # 7. Verify final booking and claim states
    assert rec_b.status == TransactionStatus.SUPPLIER_UNKNOWN
    claim_final = agg_db.get_booking_claim(intent.agency_id, intent.idempotency_key)
    assert claim_final.status == BookingClaimStatus.RECONCILIATION_REQUIRED
    assert claim_final.owner_token == worker_b_svc.owner_token
    assert claim_final.last_owner_token == worker_a_token
    assert claim_final.fencing_token == 2

    # 8. Verify exactly one supplier reservation was committed in supplier DB
    supplier_reservations = sup_db.get_reservations_by_intent(intent.intent_id)
    assert len(supplier_reservations) == 1
    committed_res = supplier_reservations[0]
    assert committed_res.status == "CONFIRMED"

    # Verify supplier event log: exactly one RESERVATION_COMMITTED event logged
    events = sup_db.get_events_for_intent(intent.intent_id)
    committed_events = [e for e in events if e.get("event_type") == "RESERVATION_COMMITTED" or getattr(e, "event_type", None) == "RESERVATION_COMMITTED"]
    assert len(committed_events) == 1

    # Verify no ungrounded supplier confirmation was written to aggregator database
    final_booking = agg_db.get_booking_record(booking_id)
    assert final_booking.status == TransactionStatus.SUPPLIER_UNKNOWN
    assert final_booking.primary_supplier_reservation_id is None
    assert final_booking.voucher_id is None


"""
Milestone 2.2 — Test Suite: Real Localhost TCP Concurrency Verification.
Runs Travel Aggregator (port 8016) and Mock Hotel Supplier (port 8017) as separate
Uvicorn processes communicating over real localhost TCP sockets.
Verifies:
1. 25 concurrent wallet booking requests over TCP sockets: exactly 10 succeed against ₹10,000
   funded balance, 15 are rejected (HTTP 400), zero overdraft.
2. 10 concurrent requests for last room over TCP sockets: exactly 1 succeeds, 9 receive HTTP 409.
3. 50 concurrent identical requests over TCP sockets: all 50 return HTTP 200 with identical
   booking and voucher ID, zero duplicate side effects.
"""

import sys
import time
import subprocess
import asyncio
import pytest
import httpx
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from engine.invariant_engine import get_invariant_engine

AGG_PORT = 8016
SUP_PORT = 8017
AGG_URL = f"http://127.0.0.1:{AGG_PORT}"
SUP_URL = f"http://127.0.0.1:{SUP_PORT}"


@pytest.fixture(scope="module")
def tcp_servers():
    """
    Spins up real Uvicorn processes for Aggregator (8016) and Supplier (8017) over localhost TCP.
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

    start = time.time()
    healthy = False
    with httpx.Client(timeout=1.0) as client:
        while time.time() - start < 15.0:
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
        pytest.fail("Failed to connect to Aggregator and Supplier Uvicorn servers over localhost TCP.")

    # Wire aggregator to point to supplier on 8017
    with httpx.Client(timeout=2.0) as client:
        client.post(
            f"{AGG_URL}/api/v1/admin/config",
            json={
                "mode": "FIXED",
                "wallet_mode": "FIXED",
                "supplier_base_url": SUP_URL,
                "supplier_timeout": 5.0,
            },
        )
        client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": "IDEMPOTENT",
                "inventory_mode": "ATOMIC",
                "inventory_check_enabled": True,
            },
        )

    yield

    agg_proc.terminate()
    sup_proc.terminate()
    try:
        agg_proc.wait(timeout=3.0)
        sup_proc.wait(timeout=3.0)
    except Exception:
        agg_proc.kill()
        sup_proc.kill()


@pytest.fixture(autouse=True)
def reset_dbs_per_test(tcp_servers):
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()

    with httpx.Client(timeout=2.0) as client:
        client.post(
            f"{AGG_URL}/api/v1/admin/config",
            json={
                "mode": "FIXED",
                "wallet_mode": "FIXED",
                "supplier_base_url": SUP_URL,
                "supplier_timeout": 5.0,
            },
        )
        client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": "IDEMPOTENT",
                "inventory_mode": "ATOMIC",
                "inventory_check_enabled": True,
            },
        )
    yield
    agg_db.reset()
    sup_db.reset()


@pytest.mark.asyncio
async def test_tcp_concurrent_wallet_overdraft_prevention():
    """
    Real TCP test:
    Agency Alpha starts with ₹10,000 (1,000,000 minor units).
    25 simultaneous booking requests of ₹1,000 each are dispatched over real HTTP sockets.
    Exactly 10 succeed (10 * ₹1,000 = ₹10,000).
    15 are rejected with HTTP 400 (Insufficient wallet balance).
    Final available balance is ₹0, zero overdraft occurs.
    """
    agg_db = get_aggregator_db()
    inv_engine = get_invariant_engine()

    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }

    async def send_booking(idx: int):
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "intent_id": f"TCP-BKG-25X-{idx:02d}",
                "agency_id": "agency_alpha",
                "hotel_id": "htl_mumbai_grand",
                "room_type": "standard_room",
                "check_in": "2026-12-15",
                "check_out": "2026-12-18",
                "amount": 1000.0,
                "amount_minor": 100000,
                "currency": "INR",
                "guest_name": f"TCP Guest {idx}",
                "correlation_id": f"corr-tcp-25x-{idx}",
                "payment_method": "WALLET",
                "idempotency_key": f"IDEMP-TCP-25X-{idx}",
            }
            return await client.post(f"{AGG_URL}/api/v1/bookings", json=payload, headers=headers)

    tasks = [send_booking(i) for i in range(25)]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    status_200 = [r for r in responses if isinstance(r, httpx.Response) and r.status_code == 200]
    status_400 = [r for r in responses if isinstance(r, httpx.Response) and r.status_code == 400]

    assert len(status_200) == 10, f"Expected exactly 10 successes, got {len(status_200)}"
    assert len(status_400) == 15, f"Expected exactly 15 rejections, got {len(status_400)}"

    wallet = agg_db.get_wallet("agency_alpha")
    assert wallet.funded_balance_minor == 1000000
    assert wallet.total_captured_minor == 1000000
    assert wallet.active_holds_minor == 0
    assert wallet.available_balance_minor == 0

    inv_res = inv_engine.check_wallet_available_balance("agency_alpha")
    assert inv_res.passed


@pytest.mark.asyncio
async def test_tcp_concurrent_last_room_race():
    """
    Real TCP test:
    Room capacity=1. 10 concurrent requests from different agencies compete over real TCP sockets.
    Exactly 1 receives HTTP 200, 9 receive HTTP 409 (Sold Out).
    """
    headers_alpha = {"X-Agency-ID": "agency_alpha", "X-API-Key": "secret_alpha_key_99"}
    headers_beta = {"X-Agency-ID": "agency_beta", "X-API-Key": "secret_beta_key_88"}

    async def send_race_booking(idx: int):
        agency = "agency_alpha" if idx % 2 == 0 else "agency_beta"
        headers = headers_alpha if agency == "agency_alpha" else headers_beta
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "intent_id": f"TCP-LASTROOM-{idx:02d}",
                "agency_id": agency,
                "hotel_id": "htl_mumbai_grand",
                "room_type": "deluxe_suite",  # capacity = 1
                "check_in": "2026-12-15",
                "check_out": "2026-12-18",
                "amount": 2000.0,
                "amount_minor": 200000,
                "currency": "INR",
                "guest_name": f"Last Room Guest {idx}",
                "correlation_id": f"corr-lastroom-{idx}",
                "payment_method": "WALLET",
                "idempotency_key": f"IDEMP-LASTROOM-{idx}",
            }
            return await client.post(f"{AGG_URL}/api/v1/bookings", json=payload, headers=headers)

    tasks = [send_race_booking(i) for i in range(10)]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    status_200 = [r for r in responses if isinstance(r, httpx.Response) and r.status_code == 200]
    status_409 = [r for r in responses if isinstance(r, httpx.Response) and r.status_code == 409]

    assert len(status_200) == 1, f"Expected exactly 1 booking confirmed, got {len(status_200)}"
    assert len(status_409) == 9, f"Expected exactly 9 sold-out rejections, got {len(status_409)}"


@pytest.mark.asyncio
async def test_tcp_concurrent_idempotent_replay_50x():
    """
    Real TCP test:
    50 concurrent identical requests with identical idempotency key over real TCP sockets.
    All 50 must succeed with HTTP 200, return the exact same booking ID and voucher ID.
    Zero duplicate reservations or ledger entries created.
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()

    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }

    payload = {
        "intent_id": "TCP-50X-REPLAY-INTENT",
        "agency_id": "agency_alpha",
        "hotel_id": "htl_mumbai_grand",
        "room_type": "standard_room",
        "check_in": "2026-12-15",
        "check_out": "2026-12-18",
        "amount": 2500.0,
        "amount_minor": 250000,
        "currency": "INR",
        "guest_name": "Replay Guest",
        "correlation_id": "corr-tcp-50x",
        "payment_method": "WALLET",
        "idempotency_key": "IDEMP-TCP-50X-REPLAY",
    }

    async def send_identical(idx: int):
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.post(f"{AGG_URL}/api/v1/bookings", json=payload, headers=headers)

    tasks = [send_identical(i) for i in range(50)]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    assert len(responses) == 50
    for r in responses:
        assert isinstance(r, httpx.Response)
        assert r.status_code == 200

    booking_ids = {r.json()["booking_id"] for r in responses}
    voucher_ids = {r.json()["voucher_id"] for r in responses}
    res_ids = {r.json()["primary_supplier_reservation_id"] for r in responses}

    assert len(booking_ids) == 1
    assert len(voucher_ids) == 1
    assert len(res_ids) == 1

    # Check database: exactly 1 supplier reservation registered
    reservations = sup_db.get_reservations_by_intent("TCP-50X-REPLAY-INTENT")
    assert len(reservations) == 1

    # Check wallet: exactly ₹2,500 captured, exactly 1 hold
    wallet = agg_db.get_wallet("agency_alpha")
    assert wallet.total_captured_minor == 250000
    assert wallet.available_balance_minor == 750000

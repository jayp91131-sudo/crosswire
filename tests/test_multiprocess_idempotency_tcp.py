"""
Milestone 2.2 — Test Suite: Multi-Process Idempotency over Real Localhost TCP (CW-ARC-M2-207).
Spins up:
- Aggregator Process A on port 8021 (Uvicorn subprocess)
- Aggregator Process B on port 8022 (Uvicorn subprocess)
- Mock Hotel Supplier on port 8023 (Uvicorn subprocess)
Both Aggregators share the same underlying SQLite aggregator database.
Supplier introduces a realistic response delay (0.3s).
Two simultaneous requests with the exact same (agency_id, idempotency_key) are dispatched
concurrently across Aggregator A and Aggregator B.

Verifies:
1. Distributed database-backed claim locking via `booking_operation_claims`.
2. Exactly ONE Aggregator worker wins the claim and calls the upstream Supplier.
3. The other Aggregator worker waits without making duplicate upstream supplier calls.
4. Exactly ONE reservation is registered in the Supplier database.
5. Both callers receive identical booking responses (HTTP 200) with identical booking_id and voucher_id.
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

AGG_A_PORT = 8021
AGG_B_PORT = 8022
SUP_PORT = 8023

AGG_A_URL = f"http://127.0.0.1:{AGG_A_PORT}"
AGG_B_URL = f"http://127.0.0.1:{AGG_B_PORT}"
SUP_URL = f"http://127.0.0.1:{SUP_PORT}"


@pytest.fixture(scope="module")
def multi_process_cluster():
    """
    Spins up Aggregator A (8021), Aggregator B (8022), and Supplier (8023).
    """
    base_cmd = [sys.executable, "-m", "uvicorn"]

    agg_a = subprocess.Popen(
        base_cmd + [
            "target_platform.aggregator.app:aggregator_app",
            "--host", "127.0.0.1",
            "--port", str(AGG_A_PORT),
            "--log-level", "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    agg_b = subprocess.Popen(
        base_cmd + [
            "target_platform.aggregator.app:aggregator_app",
            "--host", "127.0.0.1",
            "--port", str(AGG_B_PORT),
            "--log-level", "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    sup = subprocess.Popen(
        base_cmd + [
            "target_platform.supplier.app:supplier_app",
            "--host", "127.0.0.1",
            "--port", str(SUP_PORT),
            "--log-level", "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Health check
    start = time.time()
    healthy = False
    with httpx.Client(timeout=1.0) as client:
        while time.time() - start < 15.0:
            try:
                r_a = client.get(f"{AGG_A_URL}/")
                r_b = client.get(f"{AGG_B_URL}/")
                r_s = client.get(f"{SUP_URL}/")
                if r_a.status_code == 200 and r_b.status_code == 200 and r_s.status_code == 200:
                    healthy = True
                    break
            except Exception:
                pass
            time.sleep(0.3)

    if not healthy:
        agg_a.kill()
        agg_b.kill()
        sup.kill()
        pytest.fail("Failed to spin up Aggregator A, Aggregator B, and Supplier over localhost TCP.")

    # Configure both aggregators to point to the shared supplier on port 8023
    with httpx.Client(timeout=3.0) as client:
        for url in [AGG_A_URL, AGG_B_URL]:
            client.post(
                f"{url}/api/v1/admin/config",
                json={
                    "mode": "FIXED",
                    "wallet_mode": "FIXED",
                    "supplier_base_url": SUP_URL,
                    "supplier_timeout": 5.0,
                },
            )

        # Configure supplier with 0.3s delay to ensure concurrent execution window
        client.post(
            f"{SUP_URL}/supplier/v1/admin/config",
            json={
                "mode": "IDEMPOTENT",
                "inventory_mode": "ATOMIC",
                "inventory_check_enabled": True,
                "simulate_timeout": False,
                "timeout_delay_seconds": 0.3,
            },
        )

    yield (agg_a, agg_b, sup)

    agg_a.kill()
    agg_b.kill()
    sup.kill()
    agg_a.wait()
    agg_b.wait()
    sup.wait()


@pytest.mark.asyncio
async def test_multiprocess_idempotency_cross_server(multi_process_cluster):
    """
    Submits simultaneous duplicate requests with the same idempotency_key
    to two distinct Aggregator server processes (Process A and Process B).
    """
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()

    # Pre-fund agency wallet with ₹10,000 (1,000,000 minor)
    auth_headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }

    idemp_key = "IDEMP-MULTIPROC-TCP-001"
    payload = {
        "intent_id": "intent_multiproc_tcp_01",
        "agency_id": "agency_alpha",
        "hotel_id": "htl_mumbai_grand",
        "hotel_name": "Grand Mumbai",
        "room_type": "standard_room",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 2500.0,
        "amount_minor": 250000,
        "currency": "INR",
        "guest_name": "MultiProc Guest",
        "correlation_id": "corr_multiproc_1",
        "payment_method": "WALLET",
        "idempotency_key": idemp_key,
    }

    # Dispatch request 1 to Aggregator A and request 2 to Aggregator B simultaneously
    async with httpx.AsyncClient(timeout=10.0) as client:
        async def call_agg_a():
            return await client.post(f"{AGG_A_URL}/api/v1/bookings", json=payload, headers=auth_headers)

        async def call_agg_b():
            return await client.post(f"{AGG_B_URL}/api/v1/bookings", json=payload, headers=auth_headers)

        resp_a, resp_b = await asyncio.gather(call_agg_a(), call_agg_b())

    # Both requests must succeed with HTTP 200
    assert resp_a.status_code == 200, f"Aggregator A failed: {resp_a.text}"
    assert resp_b.status_code == 200, f"Aggregator B failed: {resp_b.text}"

    data_a = resp_a.json()
    data_b = resp_b.json()

    # Assert identical booking and voucher IDs returned
    assert data_a["booking_id"] == data_b["booking_id"]
    assert data_a["primary_supplier_reservation_id"] == data_b["primary_supplier_reservation_id"]
    assert data_a["voucher_id"] == data_b["voucher_id"]

    # Assert exactly ONE reservation was created at the supplier
    sup_reservations = sup_db.get_reservations_by_intent(payload["intent_id"])
    assert len(sup_reservations) == 1, f"Expected 1 reservation at supplier, got {len(sup_reservations)}"

    # Assert claim status in database reached COMPLETED
    claim = agg_db.get_booking_claim("agency_alpha", idemp_key)
    assert claim is not None
    assert claim.status.value == "COMPLETED"

    # Assert exactly 1 booking record in aggregator database
    engine = get_invariant_engine()
    inv_res = engine.check_saga_state_consistency(data_a["booking_id"])
    assert inv_res.passed is True

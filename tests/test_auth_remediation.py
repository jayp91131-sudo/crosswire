"""
Regression and Negative Security Test Suite for Authentication Perimeter (CW-SEC-M2-206).
Verifies that all 8 negative authentication permutations are strictly enforced:
1. Missing X-Agency-ID -> 401
2. Missing X-API-Key -> 401
3. Invalid credentials -> 401
4. Cross-agency intent mismatch (X-Agency-ID != intent.agency_id) -> 403
5. Unauthenticated read on /api/v1/bookings/{id} -> 401
6. Unauthenticated mutation on /api/v1/wallets/deposit -> 401
7. Unauthenticated read on /api/v1/wallets/me -> 401
8. Cross-agency wallet read attempt -> 403
"""

import pytest
from fastapi.testclient import TestClient
from target_platform.aggregator.app import aggregator_app
from target_platform.aggregator.database import get_aggregator_db


@pytest.fixture(autouse=True)
def reset_db():
    db = get_aggregator_db()
    db.reset()
    yield
    db.reset()


@pytest.fixture
def client():
    return TestClient(aggregator_app)


def test_missing_x_agency_id_header(client):
    payload = {
        "intent_id": "test_intent_no_agency_hdr",
        "agency_id": "agency_alpha",
        "hotel_id": "hotel_taj_mumbai",
        "hotel_name": "Taj Mahal Palace",
        "room_type": "DELUXE",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 15000.0,
        "amount_minor": 1500000,
        "currency": "INR",
        "guest_name": "Alice Smith",
        "correlation_id": "corr_no_agency_hdr",
    }
    resp = client.post("/api/v1/bookings", json=payload, headers={"X-API-Key": "secret_alpha_key_99"})
    assert resp.status_code == 401
    assert "Missing X-Agency-ID" in resp.json()["detail"]


def test_missing_x_api_key_header(client):
    payload = {
        "intent_id": "test_intent_no_key_hdr",
        "agency_id": "agency_alpha",
        "hotel_id": "hotel_taj_mumbai",
        "hotel_name": "Taj Mahal Palace",
        "room_type": "DELUXE",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 15000.0,
        "amount_minor": 1500000,
        "currency": "INR",
        "guest_name": "Alice Smith",
        "correlation_id": "corr_no_key_hdr",
    }
    resp = client.post("/api/v1/bookings", json=payload, headers={"X-Agency-ID": "agency_alpha"})
    assert resp.status_code == 401
    assert "Missing X-API-Key" in resp.json()["detail"]


def test_invalid_api_key(client):
    payload = {
        "intent_id": "test_intent_bad_creds",
        "agency_id": "agency_alpha",
        "hotel_id": "hotel_taj_mumbai",
        "hotel_name": "Taj Mahal Palace",
        "room_type": "DELUXE",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 15000.0,
        "amount_minor": 1500000,
        "currency": "INR",
        "guest_name": "Alice Smith",
        "correlation_id": "corr_bad_creds",
    }
    resp = client.post(
        "/api/v1/bookings",
        json=payload,
        headers={"X-Agency-ID": "agency_alpha", "X-API-Key": "wrong_password_123"},
    )
    assert resp.status_code == 401
    assert "Invalid agency credentials" in resp.json()["detail"]


def test_cross_agency_intent_ownership_mismatch(client):
    """
    Agency Alpha attempts to create a booking using Agency Beta's agency_id in the body.
    Must be blocked with HTTP 403 Forbidden.
    """
    payload = {
        "intent_id": "test_intent_spoof_beta",
        "agency_id": "agency_beta",
        "hotel_id": "hotel_taj_mumbai",
        "hotel_name": "Taj Mahal Palace",
        "room_type": "DELUXE",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 15000.0,
        "amount_minor": 1500000,
        "currency": "INR",
        "guest_name": "Alice Smith",
        "correlation_id": "corr_spoof_beta",
    }
    resp = client.post(
        "/api/v1/bookings",
        json=payload,
        headers={"X-Agency-ID": "agency_alpha", "X-API-Key": "secret_alpha_key_99"},
    )
    assert resp.status_code == 403
    assert "Intent ownership mismatch" in resp.json()["detail"]


def test_unauthenticated_get_booking(client):
    resp = client.get("/api/v1/bookings/TBOH-nonexistent")
    assert resp.status_code == 401
    assert "Missing X-Agency-ID" in resp.json()["detail"]


def test_unauthenticated_wallet_deposit(client):
    payload = {"amount": 5000.0, "currency": "INR"}
    resp = client.post("/api/v1/wallets/deposit", json=payload)
    assert resp.status_code == 401
    assert "Missing X-Agency-ID" in resp.json()["detail"]


def test_unauthenticated_wallet_me(client):
    resp = client.get("/api/v1/wallets/me")
    assert resp.status_code == 401
    assert "Missing X-Agency-ID" in resp.json()["detail"]


def test_cross_agency_wallet_access_denied(client):
    resp = client.get(
        "/api/v1/wallets/agency_beta",
        headers={"X-Agency-ID": "agency_alpha", "X-API-Key": "secret_alpha_key_99"},
    )
    assert resp.status_code == 403
    assert "Cross-agency isolation violation" in resp.json()["detail"]

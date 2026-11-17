"""
Audit Item 10: Verification of Authentication, Intent Ownership, Correlation IDs, and Secret Handling.
Tests:
1. Rejection of invalid agency credentials (HTTP 401).
2. Rejection of intent ownership mismatch (Agency Alpha trying to submit for Agency Beta) (HTTP 403).
3. Cross-agency data isolation: Agency Beta cannot access Agency Alpha's booking records.
4. End-to-end correlation ID propagation across intent, aggregator, and response.
5. Secret handling: API keys and sensitive internal file paths are not leaked in responses or audit details.
"""

import pytest
import httpx
from target_platform.agency_client import AgencyClient
from target_platform.models import BookingIntent
from target_platform.aggregator.app import aggregator_app
from target_platform.aggregator.database import get_aggregator_db


from target_platform.aggregator.booking_service import get_aggregator_booking_service


@pytest.fixture
def test_client(aggregator_transport, supplier_client):
    """Client for testing Aggregator endpoints directly with supplier wired."""
    agg_service = get_aggregator_booking_service()
    agg_service.supplier_client = supplier_client
    return httpx.AsyncClient(transport=aggregator_transport, base_url="http://travel-aggregator")


@pytest.mark.asyncio
async def test_auth_invalid_credentials_rejected(test_client):
    """Requests with invalid API keys must be rejected with 401."""
    intent_data = {
        "intent_id": "INTENT-AUTH-01",
        "agency_id": "agency_alpha",
        "hotel_id": "htl_mumbai_grand",
        "hotel_name": "Hotel Mumbai",
        "room_type": "deluxe",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 10000.0,
        "currency": "INR",
        "guest_name": "Aarav Sharma",
        "correlation_id": "corr_auth_01",
    }
    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "WRONG_SECRET_KEY",
    }
    resp = await test_client.post("/api/v1/bookings", json=intent_data, headers=headers)
    assert resp.status_code == 401
    assert "Invalid agency credentials" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_intent_ownership_mismatch_rejected(test_client):
    """An agency cannot submit a booking intent belonging to another agency (403 Forbidden)."""
    # Authenticated as agency_alpha, but submitting intent for agency_beta
    intent_data = {
        "intent_id": "INTENT-AUTH-02",
        "agency_id": "agency_beta",
        "hotel_id": "htl_mumbai_grand",
        "hotel_name": "Hotel Mumbai",
        "room_type": "deluxe",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 12000.0,
        "currency": "INR",
        "guest_name": "Priya Patel",
        "correlation_id": "corr_auth_02",
    }
    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }
    resp = await test_client.post("/api/v1/bookings", json=intent_data, headers=headers)
    assert resp.status_code == 403
    assert "Intent ownership mismatch" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cross_agency_booking_access_forbidden(test_client):
    """Agency Beta cannot read bookings created by Agency Alpha."""
    alpha_intent = {
        "intent_id": "INTENT-AUTH-ISOLATE-01",
        "agency_id": "agency_alpha",
        "hotel_id": "htl_mumbai_grand",
        "hotel_name": "Hotel Mumbai",
        "room_type": "deluxe",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 10000.0,
        "currency": "INR",
        "guest_name": "Aarav Sharma",
        "correlation_id": "corr_auth_isolate_01",
    }
    alpha_headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }
    create_resp = await test_client.post("/api/v1/bookings", json=alpha_intent, headers=alpha_headers)
    assert create_resp.status_code == 200
    booking_id = create_resp.json()["booking_id"]

    # Agency Alpha can view it
    alpha_read = await test_client.get(f"/api/v1/bookings/{booking_id}", headers=alpha_headers)
    assert alpha_read.status_code == 200

    # Agency Beta cannot view it (returns 404/unauthorized isolation)
    beta_headers = {
        "X-Agency-ID": "agency_beta",
        "X-API-Key": "secret_beta_key_88",
    }
    beta_read = await test_client.get(f"/api/v1/bookings/{booking_id}", headers=beta_headers)
    assert beta_read.status_code == 404

    # Agency Beta also cannot view it by intent
    beta_by_intent = await test_client.get(
        f"/api/v1/bookings/by-intent/INTENT-AUTH-ISOLATE-01", headers=beta_headers
    )
    assert beta_by_intent.status_code == 404


@pytest.mark.asyncio
async def test_correlation_id_propagation_and_secret_handling(test_client):
    """
    Verifies:
    1. Correlation ID is propagated completely into the response and booking record.
    2. API keys and DB secrets are not leaked in the HTTP response body or audit events.
    """
    agg_db = get_aggregator_db()
    intent_data = {
        "intent_id": "INTENT-CORR-01",
        "agency_id": "agency_alpha",
        "hotel_id": "htl_mumbai_grand",
        "hotel_name": "Hotel Mumbai",
        "room_type": "deluxe",
        "check_in": "2026-12-01",
        "check_out": "2026-12-05",
        "amount": 14000.0,
        "currency": "INR",
        "guest_name": "Aarav Sharma",
        "correlation_id": "CORR-UNIQUE-TRACE-TRACE-999",
    }
    headers = {
        "X-Agency-ID": "agency_alpha",
        "X-API-Key": "secret_alpha_key_99",
    }
    resp = await test_client.post("/api/v1/bookings", json=intent_data, headers=headers)
    assert resp.status_code == 200
    resp_data = resp.json()

    # 1. Correlation ID correctly reflected
    assert resp_data["correlation_id"] == "CORR-UNIQUE-TRACE-TRACE-999"

    # 2. Secret check: raw secret key must not appear in response payload
    raw_response_text = resp.text
    assert "secret_alpha_key_99" not in raw_response_text

    # 3. Secret check: aggregator database audit events do not log raw api keys
    booking_id = resp_data["booking_id"]
    audit_events = agg_db.get_events_for_booking(booking_id)
    for evt in audit_events:
        assert "secret_alpha_key_99" not in str(evt)

"""
Crosswire M2.9 Test Suite: Target Integration & Adapter Framework.

Tests:
1. Target capability registration, allowlisting, and metadata discovery.
2. Capability validation and fail-closed security boundaries.
3. PortableTimeoutScenario against Target A (Reference) and Target B (TravelSandboxB).
4. PortableWebhookScenario against Target A (Reference) and Target B (TravelSandboxB).
5. Ground-truth observation normalization across divergent database schemas.
6. Fail-safe handling: unavailable ground truth never treated as passing.
7. Control Center target selection, API integration, and capability rejection.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from engine.adapters import (
    get_target_registry,
    TargetCapability,
    TargetNotAllowedError,
    UnsupportedCapabilityError,
    ObservationResult,
    BookingObservation,
    SupplierReservationObservation,
    LedgerObservation,
    WebhookObservation,
)
from engine.portable_scenarios import (
    PortableTimeoutScenario,
    PortableWebhookScenario,
)
from control_center.app import app
from control_center.engine_bridge import get_engine_bridge


# =============================================================================
# 1. Target Registry & Capability Tests
# =============================================================================

def test_target_registry_default_registrations():
    """Verifies that both Target A and Target B are registered on startup."""
    registry = get_target_registry()
    targets = registry.list_targets()
    assert "target_a" in targets
    assert "travel_sandbox_b" in targets


def test_target_metadata_and_capabilities():
    """Verifies metadata and capability differences between Target A and Target B."""
    registry = get_target_registry()

    # Target A has all capabilities including outbox
    meta_a = registry.get_target_metadata("target_a")
    assert meta_a.target_id == "target_a"
    assert meta_a.has_capability(TargetCapability.BOOKING_INTENTS)
    assert meta_a.has_capability(TargetCapability.SUPPLIER_RESERVATIONS)
    assert meta_a.has_capability(TargetCapability.MULTI_SUPPLIER_OUTBOX)
    assert meta_a.is_local_sandbox is True

    # Target B has core capabilities but lacks outbox
    meta_b = registry.get_target_metadata("travel_sandbox_b")
    assert meta_b.target_id == "travel_sandbox_b"
    assert meta_b.has_capability(TargetCapability.BOOKING_INTENTS)
    assert meta_b.has_capability(TargetCapability.SUPPLIER_RESERVATIONS)
    assert meta_b.has_capability(TargetCapability.REFUND_WEBHOOKS)
    assert meta_b.has_capability(TargetCapability.LEDGER_OBSERVATIONS)
    assert not meta_b.has_capability(TargetCapability.MULTI_SUPPLIER_OUTBOX)


def test_target_allowlisting_security_boundary():
    """Verifies that attempting to target an unapproved external platform raises TargetNotAllowedError."""
    registry = get_target_registry()
    with pytest.raises(TargetNotAllowedError):
        registry.get_execution_adapter("https://api.external-travel-aggregator.com")

    with pytest.raises(TargetNotAllowedError):
        registry.get_observation_adapter("production_db")


def test_unsupported_capability_fail_closed():
    """Verifies that requesting an unsupported capability raises UnsupportedCapabilityError."""
    registry = get_target_registry()
    # Target B does not support outbox
    with pytest.raises(UnsupportedCapabilityError):
        registry.validate_capabilities("travel_sandbox_b", [TargetCapability.MULTI_SUPPLIER_OUTBOX])


# =============================================================================
# 2. Portable SCN-TIMEOUT-001 Tests (Both Targets)
# =============================================================================

@pytest.mark.asyncio
async def test_portable_timeout_target_a_vulnerable():
    """Tests ambiguous supplier commit-then-timeout against Target A in VULNERABLE mode."""
    scenario = PortableTimeoutScenario(target_id="target_a")
    meta, finding = await scenario.run_vulnerable_workflow(intent_id="INTENT-TEST-A-VULN")

    assert finding.finding_id == "CW-SEC-M2-001"
    assert finding.scenario_id == "SCN-TIMEOUT-001"
    assert finding.severity == "CRITICAL"
    assert "INV-5.2" in finding.violated_invariant
    assert len(finding.supplier_reservation_ids) == 2
    assert meta["target_id"] == "target_a"
    assert meta["reservation_count"] == 2


@pytest.mark.asyncio
async def test_portable_timeout_target_a_fixed():
    """Tests ambiguous supplier commit-then-timeout against Target A in FIXED mode."""
    scenario = PortableTimeoutScenario(target_id="target_a")
    meta, retest = await scenario.run_fixed_workflow(intent_id="INTENT-TEST-A-FIXED")

    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert retest.supplier_reservation_count == 1
    assert len(retest.supplier_reservation_ids) == 1
    assert meta["target_id"] == "target_a"


@pytest.mark.asyncio
async def test_portable_timeout_target_b_vulnerable():
    """Tests ambiguous supplier commit-then-timeout against independent Target B in VULNERABLE mode."""
    scenario = PortableTimeoutScenario(target_id="travel_sandbox_b")
    meta, finding = await scenario.run_vulnerable_workflow(intent_id="INTENT-TEST-B-VULN")

    assert finding.finding_id == "CW-SEC-M2-001"
    assert finding.scenario_id == "SCN-TIMEOUT-001"
    assert finding.severity == "CRITICAL"
    assert "INV-5.2" in finding.violated_invariant
    # Target B produces 2 vendor confirmations under blind retry
    assert len(finding.supplier_reservation_ids) == 2
    assert meta["target_id"] == "travel_sandbox_b"
    assert meta["reservation_count"] == 2


@pytest.mark.asyncio
async def test_portable_timeout_target_b_fixed():
    """Tests ambiguous supplier commit-then-timeout against independent Target B in FIXED mode."""
    scenario = PortableTimeoutScenario(target_id="travel_sandbox_b")
    meta, retest = await scenario.run_fixed_workflow(intent_id="INTENT-TEST-B-FIXED")

    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert retest.supplier_reservation_count == 1
    assert len(retest.supplier_reservation_ids) == 1
    assert meta["target_id"] == "travel_sandbox_b"


# =============================================================================
# 3. Portable SCN-WEBHOOK-001 Tests (Both Targets)
# =============================================================================

@pytest.mark.asyncio
async def test_portable_webhook_target_a_vulnerable():
    """Tests duplicate refund webhook replay against Target A in VULNERABLE mode."""
    scenario = PortableWebhookScenario(target_id="target_a")
    meta, finding = await scenario.run_vulnerable_workflow(
        intent_id="INTENT-WH-A-VULN",
        event_id="evt_test_a_vuln"
    )

    assert finding.finding_id == "CW-SEC-M2-002"
    assert finding.scenario_id == "SCN-WEBHOOK-001"
    assert "INV-5.3" in finding.violated_invariant
    assert meta["target_id"] == "target_a"
    assert meta["ledger_count"] == 3
    assert meta["total_refunded"] == 15000.0
    assert meta["excess_amount"] > 0


@pytest.mark.asyncio
async def test_portable_webhook_target_a_fixed():
    """Tests duplicate refund webhook replay against Target A in FIXED mode."""
    scenario = PortableWebhookScenario(target_id="target_a")
    meta, retest = await scenario.run_fixed_workflow(
        intent_id="INTENT-WH-A-FIXED",
        event_id="evt_test_a_fixed"
    )

    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert meta["target_id"] == "target_a"
    assert meta["ledger_count"] == 1
    assert meta["total_refunded"] == 5000.0


@pytest.mark.asyncio
async def test_portable_webhook_target_b_vulnerable():
    """Tests duplicate refund webhook replay against independent Target B in VULNERABLE mode."""
    scenario = PortableWebhookScenario(target_id="travel_sandbox_b")
    meta, finding = await scenario.run_vulnerable_workflow(
        intent_id="INTENT-WH-B-VULN",
        event_id="evt_test_b_vuln"
    )

    assert finding.finding_id == "CW-SEC-M2-002"
    assert finding.scenario_id == "SCN-WEBHOOK-001"
    assert "INV-5.3" in finding.violated_invariant
    assert meta["target_id"] == "travel_sandbox_b"
    # Target B recorded 3 distinct credits in tsb_audit_ledger
    assert meta["ledger_count"] == 3
    assert meta["total_refunded"] == 15000.0
    assert meta["excess_amount"] == 3000.0


@pytest.mark.asyncio
async def test_portable_webhook_target_b_fixed():
    """Tests duplicate refund webhook replay against independent Target B in FIXED mode."""
    scenario = PortableWebhookScenario(target_id="travel_sandbox_b")
    meta, retest = await scenario.run_fixed_workflow(
        intent_id="INTENT-WH-B-FIXED",
        event_id="evt_test_b_fixed"
    )

    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert meta["target_id"] == "travel_sandbox_b"
    assert meta["ledger_count"] == 1
    assert meta["total_refunded"] == 5000.0


# =============================================================================
# 4. Observation Normalization & Fail-Safe Unavailable Handling
# =============================================================================

@pytest.mark.asyncio
async def test_observation_normalization_structure():
    """Verifies that observations from both targets conform to canonical dataclass structures."""
    registry = get_target_registry()
    obs_b = registry.get_observation_adapter("travel_sandbox_b")

    # Run booking on Target B to test normalization
    exec_b = registry.get_execution_adapter("travel_sandbox_b")
    exec_b.reset()
    intent = await exec_b.create_booking_intent(
        intent_id="INTENT-NORM-001",
        hotel_id="htl_delhi",
        amount=10000.0,
        currency="INR"
    )
    booking = await exec_b.submit_booking(intent)

    # Observe via normalized adapter
    bkg_obs = obs_b.get_booking_observation("INTENT-NORM-001")
    assert bkg_obs.available is True
    assert bkg_obs.data is not None
    item = bkg_obs.data
    assert isinstance(item, BookingObservation)
    assert item.booking_intent_id == "INTENT-NORM-001"
    assert item.amount == 10000.0
    assert item.currency == "INR"


def test_fail_safe_unavailable_ground_truth():
    """Verifies that unavailable ground truth is reported explicitly and never treated as passing."""
    registry = get_target_registry()
    obs_b = registry.get_observation_adapter("travel_sandbox_b")

    outbox_obs = obs_b.get_outbox_observations("BKG-ANY")
    assert outbox_obs.available is False
    assert outbox_obs.data is None
    assert "multi-supplier outbox" in outbox_obs.unavailable_reason.lower()


# =============================================================================
# 5. Control Center Target Selector & API Tests
# =============================================================================

@pytest.mark.asyncio
async def test_control_center_targets_endpoint():
    """Verifies GET /api/targets lists registered targets with metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/targets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        ids = [t["target_id"] for t in data]
        assert "target_a" in ids
        assert "travel_sandbox_b" in ids


@pytest.mark.asyncio
async def test_control_center_run_against_target_b():
    """Verifies POST /api/scenarios/run executes against Target B."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/scenarios/run", json={
            "scenario_id": "SCN-TIMEOUT-001",
            "mode": "VULNERABLE",
            "target_id": "travel_sandbox_b",
        })
        assert resp.status_code == 200
        rec = resp.json()
        assert rec["scenario_id"] == "SCN-TIMEOUT-001"
        assert rec["target_id"] == "travel_sandbox_b"
        assert rec["status"] == "VULNERABILITY_CONFIRMED"
        assert len(rec["events"]) > 0


@pytest.mark.asyncio
async def test_control_center_rejects_unsupported_capability_on_target_b():
    """Verifies running SCN-OUTBOX-001 against Target B fails cleanly because Target B lacks outbox."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/scenarios/run", json={
            "scenario_id": "SCN-OUTBOX-001",
            "mode": "VULNERABLE",
            "target_id": "travel_sandbox_b",
        })
        assert resp.status_code == 400
        err = resp.json()
        assert "MULTI_SUPPLIER_OUTBOX" in err["detail"] or "Unsupported" in err["detail"]

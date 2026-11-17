"""Crosswire 2.0: Tests for Benchmarking Harness and Safety & Operational Hardening.

Verifies:
1. Benchmarking harness evaluation across Crosswire 2.0 and baseline security/testing tools.
2. Target allowlisting and blocking of unapproved targets.
3. Concurrency ceiling and rate-limiting enforcement.
4. Synthetic credential quarantine (rejecting live production tokens).
5. Emergency Stop / Kill Switch mechanics.
"""

import pytest
import time
from engine.benchmarking import BenchmarkHarness
from engine.safety import (
    get_safety_manager,
    EmergencyStopActiveError,
    TargetNotAllowedError,
    ScopeViolationError,
    RateLimitExceededError,
    LiveCredentialDetectedError,
)


@pytest.fixture(autouse=True)
def clean_safety_state():
    manager = get_safety_manager()
    manager.reset_emergency_stop()
    manager._active_executions_count = 0
    manager._request_timestamps.clear()
    yield
    manager.reset_emergency_stop()
    manager._active_executions_count = 0
    manager._request_timestamps.clear()


def test_benchmark_harness_comprehensive_evaluation():
    """Verifies that the BenchmarkHarness compares all baseline tools across 10 scenarios."""
    harness = BenchmarkHarness()
    results = harness.run_comprehensive_benchmark()

    assert results["total_scenarios_evaluated"] == 10
    assert len(results["tool_metrics"]) == 7

    # Verify tool entries
    names = [t["tool_name"] for t in results["tool_metrics"]]
    assert "Crosswire 2.0" in names
    assert "OWASP ZAP" in names
    assert "Nuclei" in names
    assert "Postman / Newman" in names
    assert "Toxiproxy" in names
    assert "MockCard" in names
    assert "Conventional Custom Integration Tests" in names

    # Verify that fresh harness starts with zero records and unrun metrics
    assert len(harness.raw_records) == 0
    cw_metric = next(t for t in results["tool_metrics"] if t["tool_name"] == "Crosswire 2.0")
    assert cw_metric["execution_status"] == "NOT_RUN"
    assert cw_metric["true_positives"] is None
    assert cw_metric["false_positives"] is None
    assert cw_metric["false_negatives"] is None
    assert cw_metric["precision"] is None
    assert cw_metric["recall"] is None
    assert cw_metric["f1_score"] is None

    # Verify coverage matrix: all unrun tools report NOT_RUN (or NOT_APPLICABLE for Toxiproxy)
    matrix = results["scenario_coverage_matrix"]
    assert len(matrix) == 10
    for sm in matrix:
        assert sm["tested_tools"]["Crosswire 2.0"] == "NOT_RUN"
        assert sm["tested_tools"]["OWASP ZAP"] == "NOT_RUN"
        assert sm["tested_tools"]["Toxiproxy"] == "NOT_APPLICABLE"


def test_safety_target_allowlisting():
    """Verifies that SafetyManager permits synthetic targets and blocks unauthorized targets."""
    manager = get_safety_manager()

    # Allowed targets
    manager.validate_target_id("target_a")
    manager.validate_target_id("travel_sandbox_b")

    # Unauthorized target
    with pytest.raises(TargetNotAllowedError):
        manager.validate_target_id("production_live_travel_api")


def test_safety_url_safety():
    """Verifies that only loopback / sandboxed endpoints are allowed."""
    manager = get_safety_manager()

    # Allowed local URLs
    manager.validate_url_safety("http://127.0.0.1:8000/api/v1/book")
    manager.validate_url_safety("http://localhost:9000/webhook")
    manager.validate_url_safety("http://travel-aggregator/internal")

    # Blocked public internet URLs
    with pytest.raises(ScopeViolationError):
        manager.validate_url_safety("https://api.expedia.com/v3/hotels")

    with pytest.raises(ScopeViolationError):
        manager.validate_url_safety("https://api.stripe.com/v1/charges")


def test_safety_synthetic_credential_filter():
    """Verifies that live production keys/tokens are rejected immediately."""
    manager = get_safety_manager()

    # Synthetic keys allowed
    manager.validate_synthetic_credentials("agency_alpha", "test_key_synth_123")
    manager.validate_synthetic_credentials("agency_victim", "mock_secret_456")

    # Live Stripe key pattern blocked
    with pytest.raises(LiveCredentialDetectedError):
        manager.validate_synthetic_credentials("agency_real", "sk_live_" + "test_blocked_token")

    # Live AWS key pattern blocked
    with pytest.raises(LiveCredentialDetectedError):
        manager.validate_synthetic_credentials("agency_real", "AKIAIOSFODNN7EXAMPLE")


def test_safety_rate_limiter():
    """Verifies that exceeding the 100 req/s rate limit triggers RateLimitExceededError."""
    manager = get_safety_manager()

    # Dispatch 100 requests (allowed)
    for _ in range(manager.MAX_REQUESTS_PER_SECOND):
        manager.check_rate_limit()

    # 101st request in the same second window should be rejected
    with pytest.raises(RateLimitExceededError):
        manager.check_rate_limit()


def test_safety_concurrency_ceiling():
    """Verifies that exceeding 5 concurrent scenario execution slots raises ScopeViolationError."""
    manager = get_safety_manager()

    for _ in range(manager.MAX_CONCURRENT_SCENARIOS):
        manager.acquire_execution_slot()

    # 6th concurrent slot must fail
    with pytest.raises(ScopeViolationError):
        manager.acquire_execution_slot()


def test_safety_emergency_stop_lifecycle():
    """Verifies that triggering the kill switch halts all test dispatch and resumes upon reset."""
    manager = get_safety_manager()

    assert not manager.is_emergency_stopped
    manager.assert_can_execute("target_a")

    # Trigger kill switch
    manager.trigger_emergency_stop(reason="Operator detected abnormal anomaly")
    assert manager.is_emergency_stopped

    with pytest.raises(EmergencyStopActiveError):
        manager.assert_can_execute("target_a")

    # Reset
    manager.reset_emergency_stop()
    assert not manager.is_emergency_stopped
    manager.assert_can_execute("target_a")

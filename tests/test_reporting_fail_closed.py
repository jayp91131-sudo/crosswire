"""Crosswire 2.0: Regression Tests for Fail-Closed Forensic Reporting and Full-Dossier Integrity.

Verifies:
1. Empty execution trace => INCONCLUSIVE.
2. Ground truth marked available but invariant results missing => INCONCLUSIVE.
3. Partial invariant results => INCONCLUSIVE.
4. Confirmed finding => FAIL.
5. All required invariants explicitly pass with verified ground-truth observations => PASS.
6. Missing or contradictory database observations cannot generate PASS.
7. Full-dossier SHA-256 evidence integrity across event trace, observations, finding, and retest evidence.
"""

import pytest
from engine.reporting import ForensicReportingEngine, ForensicReport
from engine.event_collector import CanonicalEvent
from m2_3c.findings import SecurityFinding, RetestResult


@pytest.fixture
def sample_event():
    return CanonicalEvent(
        event_id="EVT-TEST-001",
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        sequence_number=1,
        occurred_at_utc="2026-11-16T12:00:00Z",
        actor="AGGREGATOR",
        event_type="DISPATCH",
        event_status="COMMITTED",
        title="Supplier Dispatch",
        detail="Dispatching booking request to supplier",
        intent_id="INTENT-FC-001",
        metadata={"booking_id": "BKG-001", "agency_id": "agency_alpha", "amount": 15000.0, "currency": "INR"}
    )


@pytest.fixture
def sample_finding():
    return SecurityFinding(
        finding_id="CW-SEC-M2-101",
        scenario_id="SCN-TIMEOUT-001",
        title="Supplier Commit-Then-Timeout Duplicate Reservation",
        severity="CRITICAL",
        cwe="CWE-662",
        violated_invariant="INV-5.2: Booking Intent Uniqueness",
        logical_intent_id="INTENT-FC-001",
        supplier_reservation_ids=["RES-1", "RES-2"],
        expected_outcome="Single supplier reservation",
        actual_outcome="Two duplicate supplier reservations",
        technical_description="Aggregator retry created duplicate supplier reservation.",
        evidence={"reservation_ids": ["RES-1", "RES-2"], "count": 2},
        remediation_guidance="Use stable idempotency key across all retries.",
    )


def test_rule_1_empty_events_yields_inconclusive():
    """Rule 1: Empty events + no finding + no retest => INCONCLUSIVE."""
    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[],
        finding=None,
        retest=None,
        ground_truth_available=True,
    )
    assert report.verdict == "INCONCLUSIVE"
    assert "Execution trace is empty" in report.actual_state


def test_rule_2_ground_truth_available_but_invariant_results_missing_yields_inconclusive(sample_event):
    """Rule 2: Ground truth marked available but invariant results missing => INCONCLUSIVE."""
    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations={"reservations_count": 1},
        invariant_evaluations=None,  # Missing!
    )
    assert report.verdict == "INCONCLUSIVE"
    assert "Explicit invariant evaluation results missing" in report.actual_state


def test_rule_3_partial_invariant_results_yields_inconclusive(sample_event):
    """Rule 3: Partial invariant results against required invariants => INCONCLUSIVE."""
    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-WEBHOOK-001",
        scenario_name="Webhook Replay",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations={"ledger_count": 1},
        required_invariants=["INV-5.3: Webhook Idempotency", "INV-5.5: Refund Boundedness"],
        invariant_evaluations={"INV-5.3: Webhook Idempotency": True},  # Missing INV-5.5!
    )
    assert report.verdict == "INCONCLUSIVE"
    assert "Partial invariant evaluations" in report.actual_state


def test_rule_4_confirmed_finding_yields_fail(sample_event, sample_finding):
    """Rule 4: Confirmed finding => FAIL."""
    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=sample_finding,
        ground_truth_available=True,
    )
    assert report.verdict == "FAIL"
    assert report.actual_state == sample_finding.actual_outcome
    assert report.violated_invariant == "INV-5.2: Booking Intent Uniqueness"


def test_rule_5_all_required_invariants_pass_with_evidence_yields_pass(sample_event):
    """Rule 5: All required invariants explicitly pass with sufficient evidence => PASS."""
    obs = {"reservations_count": 1, "reservation_ids": ["RES-001"], "status": "CONFIRMED"}
    evals = {"INV-5.2: Booking Intent Uniqueness": True}

    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations=obs,
        invariant_evaluations=evals,
        required_invariants=["INV-5.2: Booking Intent Uniqueness"],
    )
    assert report.verdict == "PASS"
    assert "All required invariants explicitly passed" in report.actual_state
    assert report.verify_integrity() is True


def test_rule_6_missing_or_contradictory_observations_cannot_yield_pass(sample_event):
    """Rule 6: Missing or contradictory database observations cannot generate PASS."""
    evals = {"INV-5.2: Booking Intent Uniqueness": True}

    # Case A: Missing observations
    report_missing = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations={},  # Empty/missing!
        invariant_evaluations=evals,
    )
    assert report_missing.verdict == "INCONCLUSIVE"
    assert "Missing database observations" in report_missing.actual_state

    # Case B: Contradictory observations
    report_contradictory = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations={"reservations_count": 1, "contradictory": True},
        invariant_evaluations=evals,
    )
    assert report_contradictory.verdict == "INCONCLUSIVE"
    assert "Contradictory database observations" in report_contradictory.actual_state


def test_rule_7_full_dossier_tamper_detection(sample_event, sample_finding):
    """Rule 7: Mutating event data, observation data, finding evidence, or retest outcome fails integrity."""
    obs = {"reservations_count": 2, "reservation_ids": ["RES-1", "RES-2"]}
    evals = {"INV-5.2: Booking Intent Uniqueness": False}
    retest = RetestResult(
        retest_id="RET-001",
        timestamp="2026-11-16T12:05:00Z",
        fixed_mode_passed=True,
        supplier_reservation_count=1,
        supplier_reservation_ids=["RES-1"],
        invariant_passed=True,
        summary="Retest verified successfully",
    )

    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=sample_finding,
        retest=retest,
        ground_truth_available=True,
        ground_truth_observations=obs,
        invariant_evaluations=evals,
    )

    # Initial state must be valid
    assert report.verify_integrity() is True

    # Tamper 1: Mutate event trace
    report.runtime_event_trace[0]["detail"] = "Tampered detail"
    assert report.verify_integrity() is False
    report.runtime_event_trace[0]["detail"] = sample_event.detail  # Revert
    assert report.verify_integrity() is True

    # Tamper 2: Mutate ground truth observations
    report.ground_truth_observations["reservations_count"] = 999
    assert report.verify_integrity() is False
    report.ground_truth_observations["reservations_count"] = 2  # Revert
    assert report.verify_integrity() is True

    # Tamper 3: Mutate finding evidence
    report.finding_evidence["count"] = 0
    assert report.verify_integrity() is False
    report.finding_evidence["count"] = 2  # Revert
    assert report.verify_integrity() is True

    # Tamper 4: Mutate retest outcome
    report.retest_outcome["retest_status"] = "TAMPERED"
    assert report.verify_integrity() is False
    report.retest_outcome["retest_status"] = "PASS"  # Revert
    assert report.verify_integrity() is True

    # Tamper 5: Mutate verdict (e.g. attempting to whitewash FAIL -> PASS)
    report.verdict = "PASS"
    assert report.verify_integrity() is False
    report.verdict = "FAIL"  # Revert
    assert report.verify_integrity() is True

    # Tamper 6: Mutate affected transaction amount
    report.affected_transaction["amount"] = 0.0
    assert report.verify_integrity() is False
    report.affected_transaction["amount"] = 15000.0  # Revert
    assert report.verify_integrity() is True

    # Tamper 7: Mutate expected or actual state
    report.expected_state = "Tampered expectations"
    assert report.verify_integrity() is False
    report.expected_state = sample_finding.expected_outcome  # Revert
    assert report.verify_integrity() is True

    # Tamper 8: Mutate required invariants
    report.required_invariants.append("INV-TAMPER: Injected")
    assert report.verify_integrity() is False


def test_rule_8_omitted_or_empty_required_invariants_yields_inconclusive(sample_event):
    """Rule 8: Unknown scenario with no registered or passed required invariants cannot PASS."""
    obs = {"reservations_count": 1, "status": "CONFIRMED"}
    evals = {"INV-CUSTOM-01": True}

    # Case A: Unknown scenario ID and no required_invariants supplied
    report_unknown = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-UNKNOWN",
        scenario_id="SCN-UNKNOWN-999",
        scenario_name="Unknown Scenario",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations=obs,
        invariant_evaluations=evals,
        required_invariants=None,
    )
    assert report_unknown.verdict == "INCONCLUSIVE"
    assert "No required invariants defined or supplied" in report_unknown.actual_state

    # Case B: Explicitly passing empty list for required_invariants
    report_empty_req = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-EMPTY-REQ",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations=obs,
        invariant_evaluations=evals,
        required_invariants=[],
    )
    assert report_empty_req.verdict == "INCONCLUSIVE"
    assert "No required invariants defined or supplied" in report_empty_req.actual_state


def test_rule_9_placeholder_observation_yields_inconclusive(sample_event):
    """Rule 9: Non-empty event trace, passing invariant evaluations, but only placeholder observation => INCONCLUSIVE."""
    evals = {"INV-5.2: Booking Intent Uniqueness": True}
    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-FC-PLACEHOLDER",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        target_id="target_a",
        target_name="Target A",
        events=[sample_event],
        finding=None,
        ground_truth_available=True,
        ground_truth_observations={"status": "CAPTURED"},  # Placeholder observation only!
        invariant_evaluations=evals,
        required_invariants=["INV-5.2: Booking Intent Uniqueness"],
    )
    assert report.verdict == "INCONCLUSIVE"
    assert "Insufficient ground-truth observations" in report.actual_state



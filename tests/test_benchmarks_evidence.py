"""Crosswire 2.0: Unit and Regression Tests for Dynamic Benchmark Metrics & Evidence Honesty.

Verifies:
1. Fresh BenchmarkHarness starts with zero raw records and strictly returns None for all quantitative metrics.
2. Where raw execution evidence is absent, tools are marked NOT_RUN (or NOT_COMPARABLE) and cannot produce numerical claims.
3. Scenario matrix marks tools without run records as NOT_RUN (never inferred as MISSED).
4. Benchmark metrics are computed dynamically only when genuine RawBenchmarkRunRecord instances are recorded.
5. Changing or clearing raw run records dynamically changes the derived results.
"""

import pytest
from engine.benchmarking import (
    BenchmarkHarness,
    RawBenchmarkRunRecord,
    ExecutionStatus,
    get_iso_now,
)


def test_fresh_harness_starts_with_zero_records_and_none_metrics():
    """Verifies that a new harness starts with zero records and all tools are NOT_RUN with None metrics."""
    harness = BenchmarkHarness()
    assert len(harness.raw_records) == 0

    results = harness.run_comprehensive_benchmark()
    assert results["total_scenarios_evaluated"] == 10
    assert len(results["tool_metrics"]) == 7

    for tm in results["tool_metrics"]:
        tname = tm["tool_name"]
        expected_status = (
            ExecutionStatus.NOT_COMPARABLE.value
            if tname == "Toxiproxy"
            else ExecutionStatus.NOT_RUN.value
        )
        assert tm["execution_status"] == expected_status
        assert tm["true_positives"] is None
        assert tm["false_positives"] is None
        assert tm["false_negatives"] is None
        assert tm["precision"] is None
        assert tm["recall"] is None
        assert tm["f1_score"] is None
        assert tm["detection_time_ms"] is None
        assert tm["reproducibility_pct"] is None
        assert tm["evidence_quality_score"] is None
        assert tm["negative_control_passed"] is None

    # Verify coverage matrix: all tools without runs are NOT_RUN (or NOT_APPLICABLE)
    for sm in results["scenario_coverage_matrix"]:
        for tname, cov in sm["tested_tools"].items():
            if tname == "Toxiproxy":
                assert cov == "NOT_APPLICABLE"
            else:
                assert cov == "NOT_RUN"  # Never inferred as MISSED!


def test_metrics_computed_dynamically_from_recorded_runs():
    """Verifies that metrics are derived exclusively and dynamically when run records are added."""
    harness = BenchmarkHarness()
    now = get_iso_now()

    # Record 10 positive control runs for Crosswire 2.0
    for sc_id, _, _ in harness.SCENARIOS:
        harness.record_run(
            RawBenchmarkRunRecord(
                tool_name="Crosswire 2.0",
                scenario_id=sc_id,
                run_id=f"CW-POS-{sc_id}",
                executed_at=now,
                is_positive_control=True,
                detected_vulnerability=True,
                duration_ms=210.0,
                evidence_collected=True,
                reproduced=True,
            )
        )

    results = harness.run_comprehensive_benchmark()
    cw = next(m for m in results["tool_metrics"] if m["tool_name"] == "Crosswire 2.0")

    assert cw["execution_status"] == ExecutionStatus.EXECUTED_AND_VERIFIED.value
    assert cw["true_positives"] == 10
    assert cw["false_positives"] == 0
    assert cw["false_negatives"] == 0
    assert cw["precision"] == 1.0
    assert cw["recall"] == 1.0
    assert cw["f1_score"] == 1.0
    assert cw["detection_time_ms"] == 210.0
    assert cw["reproducibility_pct"] == 100.0
    assert cw["evidence_quality_score"] == 5.0

    # Verify scenario coverage matrix reports DETECTED for Crosswire 2.0
    for sm in results["scenario_coverage_matrix"]:
        assert sm["tested_tools"]["Crosswire 2.0"] == "DETECTED"
        assert sm["tested_tools"]["OWASP ZAP"] == "NOT_RUN"


def test_clearing_records_restores_none_metrics():
    """Verifies that clearing records restores all empirical metrics to None."""
    harness = BenchmarkHarness()
    now = get_iso_now()

    harness.record_run(
        RawBenchmarkRunRecord(
            tool_name="Crosswire 2.0",
            scenario_id="SCN-TIMEOUT-001",
            run_id="RUN-1",
            executed_at=now,
            is_positive_control=True,
            detected_vulnerability=True,
            duration_ms=150.0,
            evidence_collected=True,
            reproduced=True,
        )
    )
    # Clear records
    harness.clear_records("Crosswire 2.0")

    results = harness.run_comprehensive_benchmark()
    cw = next(m for m in results["tool_metrics"] if m["tool_name"] == "Crosswire 2.0")

    assert cw["execution_status"] == ExecutionStatus.NOT_RUN.value
    assert cw["true_positives"] is None
    assert cw["false_positives"] is None
    assert cw["precision"] is None
    assert cw["detection_time_ms"] is None


def test_dynamic_recalculation_on_new_runs():
    """Verifies that recording new runs dynamically recalculates TP, FP, FN, precision, recall, and F1."""
    harness = BenchmarkHarness()
    now = get_iso_now()

    # Record 2 positive runs: 1 detected (100ms), 1 missed (200ms)
    harness.record_run(
        RawBenchmarkRunRecord(
            tool_name="Crosswire 2.0",
            scenario_id="SCN-TIMEOUT-001",
            run_id="RUN-1",
            executed_at=now,
            is_positive_control=True,
            detected_vulnerability=True,
            duration_ms=100.0,
            evidence_collected=True,
            reproduced=True,
        )
    )
    harness.record_run(
        RawBenchmarkRunRecord(
            tool_name="Crosswire 2.0",
            scenario_id="SCN-WEBHOOK-001",
            run_id="RUN-2",
            executed_at=now,
            is_positive_control=True,
            detected_vulnerability=False,  # Missed!
            duration_ms=200.0,
            evidence_collected=False,
            reproduced=False,
        )
    )
    # Record 1 negative run: detected=True (false alarm)
    harness.record_run(
        RawBenchmarkRunRecord(
            tool_name="Crosswire 2.0",
            scenario_id="SCN-TIMEOUT-001",
            run_id="RUN-3",
            executed_at=now,
            is_positive_control=False,
            detected_vulnerability=True,  # False positive!
            duration_ms=300.0,
            evidence_collected=False,
            reproduced=True,
        )
    )

    results = harness.run_comprehensive_benchmark()
    cw = next(m for m in results["tool_metrics"] if m["tool_name"] == "Crosswire 2.0")

    assert cw["execution_status"] == ExecutionStatus.EXECUTED_AND_VERIFIED.value
    assert cw["true_positives"] == 1
    assert cw["false_negatives"] == 1
    assert cw["false_positives"] == 1
    # Precision = 1 / (1 + 1) = 0.5
    assert cw["precision"] == 0.5
    # Recall = 1 / (1 + 1) = 0.5
    assert cw["recall"] == 0.5
    # F1 = 2 * 0.5 * 0.5 / 1.0 = 0.5
    assert cw["f1_score"] == 0.5
    # Latency: mean duration of detected runs: RUN-1 (100ms) and RUN-3 (300ms) -> 200.0ms
    assert cw["detection_time_ms"] == 200.0
    # Reproducibility = 2 / 3 = 66.7%
    assert cw["reproducibility_pct"] == 66.7
    # Negative control failed because FP == 1
    assert cw["negative_control_passed"] is False

    # Scenario matrix check: SCN-TIMEOUT-001 is DETECTED, SCN-WEBHOOK-001 is MISSED, others are NOT_RUN
    matrix = {sm["scenario_id"]: sm["tested_tools"]["Crosswire 2.0"] for sm in results["scenario_coverage_matrix"]}
    assert matrix["SCN-TIMEOUT-001"] == "DETECTED"
    assert matrix["SCN-WEBHOOK-001"] == "MISSED"
    assert matrix["SCN-WALLET-001"] == "NOT_RUN"

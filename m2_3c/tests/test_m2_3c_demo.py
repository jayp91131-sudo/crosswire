"""
Crosswire Milestone 2.3C Test Suite: End-to-End Integration Verification.
Verifies:
1. Primary scenario vulnerable workflow produces Invariant 5.2 violation and structured finding CW-SEC-M2-001.
2. Primary scenario fixed workflow reconciles via idempotency and passes Invariant 5.2.
3. Retest result is attached to the primary security finding with proof of fix.
4. Secondary scenario vulnerable workflow produces Invariant 5.3 & 5.4 violations and structured finding CW-SEC-M2-002.
5. Secondary scenario fixed workflow deduplicates repeated webhook deliveries and passes financial consistency.
6. M2.3A relational schema + M2.3B outbox multi-supplier coordination completes under simulated commit-then-timeout faults.
7. Coverage Oracle and Parent Saga Coordinator advance parent booking to CONFIRMED.
8. Vulnerable outbox blind retry produces duplicate supplier reservations in synthetic backend.
9. End-to-end one-command demo runner executes cleanly and exports artifacts.
"""

import pytest
import asyncio
from pathlib import Path

from m2_3c.primary_scenario import PrimaryTimeoutScenario
from m2_3c.secondary_scenario import SecondaryWebhookScenario
from m2_3c.outbox_integration import OutboxEngineDemonstration
from m2_3c.demo import M23CDemoRunner
from m2_3c.findings import SecurityFinding, RetestResult


@pytest.mark.asyncio
async def test_primary_scenario_vulnerable_workflow():
    """Verifies that vulnerable aggregator produces duplicate supplier reservations under commit-then-timeout."""
    primary = PrimaryTimeoutScenario()
    meta, finding = await primary.run_vulnerable_workflow("INTENT-TEST-VULN-001")

    assert meta["workflow"] == "VULNERABLE"
    assert meta["supplier_reservation_count"] == 2
    assert len(meta["supplier_reservation_ids"]) == 2
    assert meta["invariant_5_2_passed"] is False

    assert isinstance(finding, SecurityFinding)
    assert finding.finding_id == "CW-SEC-M2-001"
    assert finding.scenario_id == "SCN-TIMEOUT-001"
    assert finding.severity == "HIGH"
    assert "CWE-675" in finding.cwe
    assert finding.violated_invariant == "INV-5.2: Booking Intent Uniqueness"
    assert finding.logical_intent_id == "INTENT-TEST-VULN-001"
    assert len(finding.supplier_reservation_ids) == 2


@pytest.mark.asyncio
async def test_primary_scenario_fixed_workflow_and_retest():
    """Verifies that fixed aggregator reconciles via idempotency and verifies retest."""
    primary = PrimaryTimeoutScenario()
    _, finding = await primary.run_vulnerable_workflow("INTENT-TEST-VULN-002")
    meta, retest = await primary.run_fixed_workflow("INTENT-TEST-FIXED-002", finding=finding)

    assert meta["workflow"] == "FIXED"
    assert meta["supplier_reservation_count"] == 1
    assert meta["invariant_5_2_passed"] is True

    assert isinstance(retest, RetestResult)
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert retest.supplier_reservation_count == 1

    # Finding now carries retest proof
    assert finding.retest_result is not None
    assert finding.retest_result.fixed_mode_passed is True


@pytest.mark.asyncio
async def test_secondary_scenario_vulnerable_workflow():
    """Verifies that vulnerable webhook processing creates duplicate ledger entries and financial overflow."""
    secondary = SecondaryWebhookScenario()
    meta, finding = await secondary.run_vulnerable_workflow(
        intent_id="INTENT-TEST-WH-VULN",
        event_id="evt_test_wh_vuln"
    )

    assert meta["workflow"] == "VULNERABLE"
    assert meta["deliveries"] == 3
    assert meta["total_refunded_inr"] == 15000.0  # 3 * 5,000 INR
    assert meta["excess_inr"] == 3000.0          # 15,000 - 12,000
    assert meta["inv_5_3_passed"] is False
    assert meta["inv_5_4_passed"] is False
    assert meta["inv_5_5_passed"] is False

    assert isinstance(finding, SecurityFinding)
    assert finding.finding_id == "CW-SEC-M2-002"
    assert finding.severity == "CRITICAL"
    assert "CWE-675" in finding.cwe
    assert finding.evidence["duplicate_ledger_entries"] is not None
    assert len(finding.evidence["duplicate_ledger_entries"]) == 3


@pytest.mark.asyncio
async def test_secondary_scenario_fixed_workflow_and_retest():
    """Verifies that fixed webhook processing deduplicates deliveries idempotently."""
    secondary = SecondaryWebhookScenario()
    _, finding = await secondary.run_vulnerable_workflow(
        intent_id="INTENT-TEST-WH-VULN-2",
        event_id="evt_test_wh_vuln_2"
    )
    meta, retest = await secondary.run_fixed_workflow(
        intent_id="INTENT-TEST-WH-FIXED-2",
        event_id="evt_test_wh_fixed_2",
        finding=finding
    )

    assert meta["workflow"] == "FIXED"
    assert meta["deliveries"] == 3
    assert meta["total_refunded_inr"] == 5000.0
    assert meta["excess_inr"] == 0.0
    assert meta["inv_5_3_passed"] is True
    assert meta["inv_5_4_passed"] is True
    assert meta["inv_5_5_passed"] is True

    assert isinstance(retest, RetestResult)
    assert retest.fixed_mode_passed is True
    assert finding.retest_result is not None


def test_outbox_multi_supplier_coordination():
    """Verifies M2.3A schema and M2.3B outbox execution under commit-then-timeout faults."""
    demo = OutboxEngineDemonstration()
    res = demo.run_multi_supplier_coordination_demo("BKG_TEST_OUTBOX_001")

    assert res["booking_id"] == "BKG_TEST_OUTBOX_001"
    assert res["parent_status"] == "CONFIRMED"
    assert res["wallet_hold_status"] == "CAPTURED"
    assert res["coverage_satisfied"] is True
    assert res["room_1"]["status"] == "RESERVATION_ACTIVE"
    assert res["room_1"]["capability"] == "IDEMPOTENT_WITH_LOOKUP"
    assert res["room_2"]["status"] == "RESERVATION_ACTIVE"
    assert res["room_2"]["capability"] == "LOOKUP_ONLY"


def test_outbox_vulnerable_blind_retry_contrast():
    """Verifies that outbox blind retry produces duplicate reservations in supplier backend."""
    demo = OutboxEngineDemonstration()
    res = demo.run_vulnerable_outbox_blind_retry_demo("BKG_TEST_OUTBOX_VULN")

    assert res["duplicate_detected"] is True
    assert res["total_reservations_created"] == 2
    assert len(res["supplier_reservation_ids"]) == 2


@pytest.mark.asyncio
async def test_m2_3c_demo_runner_execution(tmp_path):
    """Verifies that the full end-to-end demonstration runner executes and exports reports."""
    runner = M23CDemoRunner(output_dir=tmp_path)
    exit_code = await runner.run_all()

    assert exit_code == 0
    assert len(runner.findings) == 2
    assert len(runner.execution_records) >= 6

    # Verify JSON artifacts written
    findings_file = tmp_path / "M2_3C_FINDINGS.json"
    report_file = tmp_path / "M2_3C_DEMO_EXECUTION_REPORT.json"

    assert findings_file.exists()
    assert report_file.exists()
    assert findings_file.stat().st_size > 0
    assert report_file.stat().st_size > 0

"""Test Suite for Complete Portable Transaction Scenarios Pack.

Covers all 10 Crosswire security scenarios across vulnerable and fixed modes:
1. SCN-TIMEOUT-001: Ambiguous Supplier Commit-Then-Timeout (INV-5.2)
2. SCN-WEBHOOK-001: Duplicate Refund Webhook Replay (INV-5.3 & INV-5.5)
3. SCN-WALLET-001: Wallet Concurrency & Double-Spend (INV-5.1: Wallet Balance & Hold Bounds)
4. SCN-RACE-001: Cancellation vs Delayed Supplier Confirmation (INV-5.4 & INV-5.6)
5. SCN-BOLA-001: Cross-Agency BOLA/IDOR Boundary (INV-AUTH-01)
6. SCN-PRIV-001: Sub-Agent Privilege Escalation (INV-AUTH-02)
7. SCN-TAMPER-001: Fare and Currency Tampering (INV-PRICE-01)
8. SCN-SKIP-001: Workflow Step-Skipping / Unentitled Voucher (INV-5.4)
9. SCN-ABUSE-001: Cancel-Rebook-Refund Abuse (INV-5.5)
"""

import pytest
import asyncio

from engine.portable_scenarios import (
    PortableTimeoutScenario,
    PortableWebhookScenario,
    PortableOutboxScenario,
    PortableWalletScenario,
    PortableRaceScenario,
    PortableBolaScenario,
    PortablePrivilegeEscalationScenario,
    PortableTamperScenario,
    PortableSkipScenario,
    PortableAbuseScenario,
)
from engine.event_collector import RuntimeEventCollector


@pytest.fixture(autouse=True)
def clean_test_collector():
    collector = RuntimeEventCollector("TEST-ALL-SCENARIOS", "SCN-SUITE")
    with collector:
        yield collector


@pytest.mark.asyncio
async def test_wallet_concurrency_vulnerable_and_fixed():
    scenario = PortableWalletScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-201"
    assert ev_vuln["is_overdrawn"] is True
    assert ev_vuln["invariant_passed"] is False

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["succeeded_holds_count"] == 1


@pytest.mark.asyncio
async def test_cancellation_race_vulnerable_and_fixed():
    scenario = PortableRaceScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-202"
    assert ev_vuln["has_contradictory_state"] is True
    assert ev_vuln["invariant_passed"] is False

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["voucher_id"] is None


@pytest.mark.asyncio
async def test_bola_idor_vulnerable_and_fixed():
    scenario = PortableBolaScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-203"
    assert ev_vuln["invariant_passed"] is False
    assert ev_vuln["exfiltrated_amount"] == 15000.0

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["access_blocked"] is True


@pytest.mark.asyncio
async def test_privilege_escalation_vulnerable_and_fixed():
    scenario = PortablePrivilegeEscalationScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-204"
    assert ev_vuln["invariant_passed"] is False
    assert ev_vuln["injected_amount"] == 50000.0

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["access_blocked"] is True
    assert ev_fixed["balance_preserved"] is True


@pytest.mark.asyncio
async def test_fare_tampering_vulnerable_and_fixed():
    scenario = PortableTamperScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-205"
    assert ev_vuln["tampered_amount"] == 15.0
    assert ev_vuln["revenue_loss"] == 14985.0
    assert ev_vuln["invariant_passed"] is False

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["tamper_rejected"] is True


@pytest.mark.asyncio
async def test_step_skipping_vulnerable_and_fixed():
    scenario = PortableSkipScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-206"
    assert ev_vuln["payment_satisfied"] is False
    assert ev_vuln["invariant_passed"] is False
    assert ev_vuln["voucher_id"] is not None

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["issuance_rejected"] is True
    assert ev_fixed["voucher_issued"] is False


@pytest.mark.asyncio
async def test_refund_abuse_vulnerable_and_fixed():
    scenario = PortableAbuseScenario("target_a")
    ev_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-207"
    assert ev_vuln["cumulative_refunded"] == 27000.0
    assert ev_vuln["excess_refund_drained"] == 12000.0
    assert ev_vuln["invariant_passed"] is False

    ev_fixed, retest = await scenario.run_fixed_retest()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert ev_fixed["retest_status"] == "PASS"
    assert ev_fixed["excess_rejected"] is True


@pytest.mark.asyncio
async def test_timeout_scenario_portable():
    scenario = PortableTimeoutScenario("target_a")
    meta_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-001"
    assert meta_vuln["supplier_reservation_count"] == 2
    assert meta_vuln["invariant_5_2_passed"] is False

    meta_fixed, retest = await scenario.run_fixed_workflow()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert meta_fixed["supplier_reservation_count"] == 1


@pytest.mark.asyncio
async def test_webhook_scenario_portable():
    scenario = PortableWebhookScenario("target_a")
    meta_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-002"
    assert meta_vuln["ledger_count"] == 3
    assert meta_vuln["total_refunded"] == 15000.0
    assert meta_vuln["inv_5_3_passed"] is False

    meta_fixed, retest = await scenario.run_fixed_workflow()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert meta_fixed["ledger_count"] == 1
    assert meta_fixed["total_refunded"] == 5000.0
    assert meta_fixed["inv_5_3_passed"] is True
    assert meta_fixed["inv_5_5_passed"] is True


@pytest.mark.asyncio
async def test_outbox_scenario_portable():
    scenario = PortableOutboxScenario("target_a")
    meta_vuln, finding = await scenario.run_vulnerable_workflow()
    assert finding.finding_id == "CW-SEC-M2-003"
    assert finding.violated_invariant == "Parent Stay Coverage & Monotonic Fencing Tokens"

    meta_fixed, retest = await scenario.run_fixed_workflow()
    assert retest.fixed_mode_passed is True
    assert retest.invariant_passed is True
    assert meta_fixed["parent_status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_positive_control_legitimate_cancellation_and_partial_refund():
    """Positive control: Verifies valid partial refund under legitimate cancellation penalty satisfies INV-5.5."""
    from target_platform.aggregator.database import get_aggregator_db
    scenario = PortableAbuseScenario("target_a")

    # When a legitimate booking is cancelled, 12,000 INR refund is issued (15,000 INR captured - 3,000 INR penalty)
    db = get_aggregator_db()
    intent = await scenario.exec_adapter.create_booking_intent(
        intent_id="INTENT-POS-CTRL-01",
        hotel_id="htl_mumbai_grand",
        amount=15000.0,
        currency="INR"
    )
    bkg = await scenario.exec_adapter.submit_booking(intent)
    cancel = await scenario.exec_adapter.cancel_booking(bkg.booking_id)

    # Entitlement is 12,000 INR (1,200,000 minor)
    refundable_entitlement = cancel["refundable_entitlement"]
    assert refundable_entitlement == 12000.0

    refunds = db.get_refunds_for_booking(bkg.booking_id)
    total_refunded = sum(r.amount for r in refunds)

    # Assert INV-5.5 is satisfied (total_refunded <= refundable_entitlement)
    assert total_refunded == 12000.0
    assert total_refunded <= refundable_entitlement
    # No false alarm, legitimate partial refund passes cleanly


def test_forensic_report_sha256_provenance_and_integrity():
    """Verifies that ForensicReport calculates deterministic SHA-256 evidence digests and verifies integrity."""
    from engine.reporting import ForensicReportingEngine, ForensicReport
    from engine.event_collector import CanonicalEvent
    from m2_3c.findings import SecurityFinding

    events = [
        CanonicalEvent(
            event_id="EVT-001",
            execution_id="EXEC-TEST-001",
            scenario_id="SCN-TIMEOUT-001",
            sequence_number=1,
            occurred_at_utc="2026-11-16T12:00:00Z",
            actor="AGGREGATOR",
            event_type="DISPATCH",
            event_status="COMMITTED",
            title="Dispatch Booking",
            detail="Aggregator dispatched booking request",
            intent_id="INTENT-001",
        )
    ]
    finding = SecurityFinding(
        finding_id="CW-SEC-M2-101",
        scenario_id="SCN-TIMEOUT-001",
        title="Supplier Commit-Then-Timeout Duplicate Reservation",
        severity="CRITICAL",
        cwe="CWE-662",
        violated_invariant="INV-5.2: Booking Intent Uniqueness",
        logical_intent_id="INTENT-001",
        supplier_reservation_ids=["RES-1", "RES-2"],
        expected_outcome="Single supplier reservation",
        actual_outcome="Two duplicate supplier reservations",
        technical_description="Aggregator retry created duplicate supplier reservation.",
        evidence={"reservation_ids": ["RES-1", "RES-2"], "count": 2},
        remediation_guidance="Use stable idempotency key across all retries.",
    )

    report = ForensicReportingEngine.build_report(
        execution_id="EXEC-TEST-001",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Commit-Then-Timeout",
        target_id="target_a",
        target_name="Target A Platform",
        events=events,
        finding=finding,
    )

    assert report.verdict == "FAIL"
    assert report.evidence_sha256 != ""
    assert len(report.evidence_sha256) == 64
    assert report.verify_integrity() is True

    # Tamper test
    report.evidence_bundle["tampered_key"] = "unauthorized_modification"
    assert report.verify_integrity() is False

    # Inconclusive test when ground truth is missing
    inconclusive_report = ForensicReportingEngine.build_report(
        execution_id="EXEC-TEST-002",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Commit-Then-Timeout",
        target_id="target_a",
        target_name="Target A Platform",
        events=events,
        finding=None,
        ground_truth_available=False,
        unavailable_reason="Database connection dropped",
    )
    assert inconclusive_report.verdict == "INCONCLUSIVE"



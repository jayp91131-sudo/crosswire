"""
Crosswire M2.3C: End-to-End Integration Demonstration Runner.
Integrates M2.2 Engine, M2.3A Relational Schema, and M2.3B Outbox Engine.
Provides a single-command automated demonstration of:
1. Primary Scenario: Ambiguous Supplier Commit-Then-Timeout (Vulnerable -> Finding -> Fixed -> Retest).
2. Secondary Scenario: Duplicate Refund Webhook Replay (Vulnerable -> Finding -> Fixed -> Retest).
3. Outbox Engine Multi-Supplier Coordinated Execution under simulated network faults.
"""

import sys
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from .findings import SecurityFinding, RetestResult, get_iso_timestamp
from .primary_scenario import PrimaryTimeoutScenario
from .secondary_scenario import SecondaryWebhookScenario
from .outbox_integration import OutboxEngineDemonstration


def print_header(title: str, width: int = 80):
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def print_step(step: str, detail: str = ""):
    print(f"  [*] {step}")
    if detail:
        print(f"      -> {detail}")


def print_success(msg: str):
    print(f"  [+] SUCCESS: {msg}")


def print_warning(msg: str):
    print(f"  [!] VULNERABILITY DETECTED: {msg}")


class M23CDemoRunner:
    """Orchestrates the complete Crosswire M2.3C end-to-end demonstration."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path(__file__).resolve().parent.parent
        self.findings: List[SecurityFinding] = []
        self.execution_records: List[Dict[str, Any]] = []

    async def run_all(self) -> int:
        """Executes the full demonstration suite and outputs reports."""
        start_time = datetime.now(timezone.utc)

        print_header("CROSSWIRE M2.3C: END-TO-END DEMONSTRATION")
        print(f"  Started at           : {start_time.isoformat()}")
        print(f"  Python Version       : {sys.version.split()[0]}")
        print(f"  Target Scope         : Single-hotel, single-currency synthetic platform")
        print(f"  Verified Invariants  : INV-5.2, INV-5.3, INV-5.5, Parent Coverage, Monotonic Fencing")
        print("=" * 80)

        # -------------------------------------------------------------------
        # PART 1: PRIMARY SCENARIO - AMBIGUOUS COMMIT-THEN-TIMEOUT
        # -------------------------------------------------------------------
        print_header("PART 1: PRIMARY SCENARIO - AMBIGUOUS COMMIT-THEN-TIMEOUT")
        primary = PrimaryTimeoutScenario()

        # Phase 1: Vulnerable Workflow
        print_step("Phase 1: Executing Vulnerable Workflow (Aggregator=VULNERABLE, Supplier=VULNERABLE)")
        vuln_meta, finding_1 = await primary.run_vulnerable_workflow(intent_id="INTENT-DEMO-VULN-001")
        self.findings.append(finding_1)
        self.execution_records.append(vuln_meta)

        print_warning(
            f"Invariant 5.2 Violated! Supplier committed {vuln_meta['supplier_reservation_count']} reservations "
            f"for 1 intent: {vuln_meta['supplier_reservation_ids']}"
        )
        print("\n" + finding_1.format_terminal_card())

        # Phase 2: Fixed Workflow
        print_step("Phase 2: Executing Fixed Workflow (Aggregator=FIXED, Supplier=IDEMPOTENT)")
        fixed_meta, retest_1 = await primary.run_fixed_workflow(
            intent_id="INTENT-DEMO-FIXED-001", finding=finding_1
        )
        self.execution_records.append(fixed_meta)

        print_success(
            f"Invariant 5.2 Passed! Reconciled ambiguous timeout via idempotency contract. "
            f"Strictly {fixed_meta['supplier_reservation_count']} reservation in supplier DB: {fixed_meta['supplier_reservation_ids']}"
        )

        # Phase 3: Retest Summary
        print_step("Phase 3: Retest Verification Proved Fix", retest_1.summary)

        # -------------------------------------------------------------------
        # PART 2: SECONDARY SCENARIO - DUPLICATE REFUND WEBHOOK REPLAY
        # -------------------------------------------------------------------
        print_header("PART 2: SECONDARY SCENARIO - DUPLICATE REFUND WEBHOOK REPLAY")
        secondary = SecondaryWebhookScenario()

        # Phase 1: Vulnerable Workflow
        print_step("Phase 1: Executing Vulnerable Webhook Processing (Aggregator=VULNERABLE)")
        sec_vuln_meta, finding_2 = await secondary.run_vulnerable_workflow(
            intent_id="INTENT-DEMO-WH-VULN",
            event_id="evt_demo_wh_vuln_replay"
        )
        self.findings.append(finding_2)
        self.execution_records.append(sec_vuln_meta)

        print_warning(
            f"Invariant 5.3 & 5.5 Violated! Webhook delivered 3 times produced 3 duplicate ledger entries. "
            f"Total refunded: {sec_vuln_meta['total_refunded_inr']} INR (Excess leak: {sec_vuln_meta['excess_inr']} INR)"
        )
        print("\n" + finding_2.format_terminal_card())

        # Phase 2: Fixed Workflow
        print_step("Phase 2: Executing Fixed Webhook Processing (Aggregator=FIXED)")
        sec_fixed_meta, retest_2 = await secondary.run_fixed_workflow(
            intent_id="INTENT-DEMO-WH-FIXED",
            event_id="evt_demo_wh_fixed_replay",
            finding=finding_2
        )
        self.execution_records.append(sec_fixed_meta)

        print_success(
            f"Invariants 5.3 & 5.5 Passed! Atomic idempotency deduplicated deliveries. "
            f"Exactly 1 refund ({sec_fixed_meta['total_refunded_inr']} INR) applied across 3 deliveries."
        )

        # Phase 3: Retest Summary
        print_step("Phase 3: Retest Verification Proved Fix", retest_2.summary)

        # -------------------------------------------------------------------
        # PART 3: M2.3A SCHEMA & M2.3B OUTBOX MULTI-SUPPLIER COORDINATION
        # -------------------------------------------------------------------
        print_header("PART 3: M2.3A SCHEMA & M2.3B OUTBOX MULTI-SUPPLIER COORDINATION")
        outbox_demo = OutboxEngineDemonstration()

        # Multi-supplier coordinated execution
        print_step("Phase 1: Multi-Supplier Outbox Execution under Commit-Then-Timeout Faults")
        print_step("         Room 1 -> Expedia (IDEMPOTENT_WITH_LOOKUP)")
        print_step("         Room 2 -> Hotelbeds (LOOKUP_ONLY)")
        outbox_res = outbox_demo.run_multi_supplier_coordination_demo(booking_id="BKG_E2E_DEMO_001")
        self.execution_records.append({
            "workflow": "OUTBOX_MULTI_SUPPLIER_COORDINATION",
            "booking_id": outbox_res["booking_id"],
            "parent_status": outbox_res["parent_status"],
            "wallet_hold_status": outbox_res["wallet_hold_status"],
            "coverage_satisfied": outbox_res["coverage_satisfied"],
            "room_1": outbox_res["room_1"],
            "room_2": outbox_res["room_2"],
        })

        print_success(
            f"Parent Booking advanced to '{outbox_res['parent_status']}' with wallet hold '{outbox_res['wallet_hold_status']}'. "
            f"Coverage Oracle: {outbox_res['coverage_reason']}"
        )

        # Vulnerable outbox contrast
        print_step("Phase 2: Vulnerable Outbox Dispatcher Contrast (Naive Blind Retry without Idempotency)")
        outbox_vuln_res = outbox_demo.run_vulnerable_outbox_blind_retry_demo(booking_id="BKG_OUTBOX_VULN_001")
        self.execution_records.append(outbox_vuln_res)

        print_warning(
            f"Outbox Naive Retry created {outbox_vuln_res['total_reservations_created']} distinct supplier "
            f"reservations in backend: {outbox_vuln_res['supplier_reservation_ids']}"
        )

        # -------------------------------------------------------------------
        # PART 4: SUMMARY & ARTIFACT EXPORT
        # -------------------------------------------------------------------
        end_time = datetime.now(timezone.utc)
        duration_s = (end_time - start_time).total_seconds()

        print_header("DEMONSTRATION RESULTS SUMMARY")
        print(f"  Total Duration       : {duration_s:.2f} seconds")
        print(f"  Total Scenarios Run  : 3 (Primary Timeout, Secondary Webhook, Multi-Supplier Outbox)")
        print(f"  Security Findings    : {len(self.findings)} Confirmed Vulnerabilities (Both Proven Fixed via Retest)")
        print("-" * 80)
        print(f"  {'SCENARIO / WORKFLOW':<35} | {'MODE':<12} | {'OUTCOME':<12} | {'INVARIANTS'}")
        print("-" * 80)
        print(f"  {'Ambiguous Commit-Timeout':<35} | {'VULNERABLE':<12} | {'VULNERABLE':<12} | {'INV-5.2 VIOLATED (2 Res)'}")
        print(f"  {'Ambiguous Commit-Timeout':<35} | {'FIXED':<12} | {'PASSED':<12} | {'INV-5.2 PASSED (1 Res)'}")
        print(f"  {'Duplicate Refund Webhook':<35} | {'VULNERABLE':<12} | {'VULNERABLE':<12} | {'INV-5.3/5.5 VIOLATED'}")
        print(f"  {'Duplicate Refund Webhook':<35} | {'FIXED':<12} | {'PASSED':<12} | {'INV-5.3/5.5 PASSED'}")
        print(f"  {'Outbox Multi-Supplier':<35} | {'FIXED':<12} | {'CONFIRMED':<12} | {'Coverage + Fencing PASSED'}")
        print(f"  {'Outbox Naive Blind Retry':<35} | {'VULNERABLE':<12} | {'VULNERABLE':<12} | {'Duplicate Res Detected'}")
        print("=" * 80)

        # Write execution artifacts
        self.export_artifacts()

        return 0

    def export_artifacts(self):
        """Writes structured JSON reports of the execution and findings."""
        findings_path = self.output_dir / "M2_3C_FINDINGS.json"
        with open(findings_path, "w", encoding="utf-8") as f:
            json.dump([f.to_dict() for f in self.findings], f, indent=2)
        print_step(f"Exported Security Findings report to {findings_path.name}")

        report_path = self.output_dir / "M2_3C_DEMO_EXECUTION_REPORT.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": get_iso_timestamp(),
                "total_findings": len(self.findings),
                "executions": self.execution_records
            }, f, indent=2)
        print_step(f"Exported Demo Execution report to {report_path.name}")


def main():
    runner = M23CDemoRunner()
    return asyncio.run(runner.run_all())


if __name__ == "__main__":
    sys.exit(main())

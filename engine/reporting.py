"""Crosswire 2.0: Forensic Reporting and Evidence Provenance Engine.

Specification: MASTER_PLAN_V2.md Section 6 & Section 6 of Release Mission
Guarantees:
1. Complete forensic dossier for every executed transaction scenario.
2. Contains affected transaction, violated invariant, expected vs actual state,
   runtime event trace, evidence references, root cause, reproduction commands,
   remediation guidance, and retest verification.
3. Strict fail-safe rule: Unavailable evidence produces an INCONCLUSIVE result,
   never a false PASS.
"""

import json
import hashlib
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from engine.event_collector import CanonicalEvent
from m2_3c.findings import SecurityFinding, RetestResult


def get_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ForensicReport:
    """Standardized Crosswire 2.0 Forensic Security Dossier."""
    report_id: str
    execution_id: str
    scenario_id: str
    scenario_name: str
    target_id: str
    target_name: str
    generated_at_utc: str
    verdict: str  # FAIL (Vulnerability Detected), PASS (Invariants Upheld), INCONCLUSIVE (Ground Truth Missing / Incomplete)
    affected_transaction: Dict[str, Any]
    violated_invariant: Optional[str] = None
    expected_state: Optional[str] = None
    actual_state: Optional[str] = None
    root_cause: Optional[str] = None
    cwe: Optional[str] = None
    severity: Optional[str] = None
    required_invariants: List[str] = field(default_factory=list)
    runtime_events_count: int = 0
    runtime_event_trace: List[Dict[str, Any]] = field(default_factory=list)
    ground_truth_observations: Dict[str, Any] = field(default_factory=dict)
    invariant_evaluations: Dict[str, bool] = field(default_factory=dict)
    finding_evidence: Dict[str, Any] = field(default_factory=dict)
    evidence_bundle: Dict[str, Any] = field(default_factory=dict)
    evidence_sha256: str = ""
    reproduction_instructions: Dict[str, Any] = field(default_factory=dict)
    remediation_guidance: Optional[str] = None
    retest_outcome: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def get_canonical_evidence_bundle(self) -> Dict[str, Any]:
        """Constructs the canonical bundle encompassing all material report fields for full-dossier tamper detection."""
        return {
            "report_id": self.report_id,
            "execution_id": self.execution_id,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "generated_at_utc": self.generated_at_utc,
            "verdict": self.verdict,
            "affected_transaction": self.affected_transaction,
            "violated_invariant": self.violated_invariant,
            "expected_state": self.expected_state,
            "actual_state": self.actual_state,
            "root_cause": self.root_cause,
            "cwe": self.cwe,
            "severity": self.severity,
            "required_invariants": self.required_invariants,
            "invariant_evaluations": self.invariant_evaluations,
            "runtime_events_count": self.runtime_events_count,
            "runtime_event_trace": self.runtime_event_trace,
            "ground_truth_observations": self.ground_truth_observations,
            "finding_evidence": self.finding_evidence,
            "evidence_bundle": self.evidence_bundle,
            "reproduction_instructions": self.reproduction_instructions,
            "remediation_guidance": self.remediation_guidance,
            "retest_outcome": self.retest_outcome,
        }

    def verify_integrity(self) -> bool:
        """Verifies that the canonical evidence bundle matches the recorded SHA-256 evidence digest."""
        canonical_bundle = self.get_canonical_evidence_bundle()
        bundle_json = json.dumps(canonical_bundle, sort_keys=True).encode()
        expected_hash = hashlib.sha256(bundle_json).hexdigest()
        return self.evidence_sha256 == expected_hash

    def generate_markdown(self) -> str:
        """Generates an editorial Swiss/Forensic Markdown dossier."""
        lines = [
            f"# CROSSWIRE FORENSIC DOSSIER — {self.scenario_id}",
            f"**Report ID:** `{self.report_id}` | **Generated:** `{self.generated_at_utc}`",
            f"**Execution ID:** `{self.execution_id}` | **Target:** {self.target_name} (`{self.target_id}`)",
            "",
            "## 1. EXECUTIVE VERDICT & IMPACT",
            f"- **Verdict:** **`{self.verdict}`**",
            f"- **Severity:** **`{self.severity or 'INFO'}`**",
            f"- **CWE:** {self.cwe or 'N/A'}",
            f"- **Violated Invariant:** {self.violated_invariant or 'None'}",
            f"- **Evidence SHA-256 Digest:** `{self.evidence_sha256}` ({'VERIFIED' if self.verify_integrity() else 'MISMATCH'})",
            "",
            "## 2. TRANSACTION CONTEXT",
            f"- **Intent ID:** `{self.affected_transaction.get('intent_id', 'N/A')}`",
            f"- **Booking ID:** `{self.affected_transaction.get('booking_id', 'N/A')}`",
            f"- **Agency / Tenant:** `{self.affected_transaction.get('agency_id', 'N/A')}`",
            f"- **Amount / Currency:** {self.affected_transaction.get('amount', 0):,.2f} {self.affected_transaction.get('currency', 'INR')}",
            "",
            "## 3. STATE INTEGRITY AUDIT",
            "### Expected State",
            f"> {self.expected_state or 'All transactions and financial balances uphold defined business-logic invariants.'}",
            "",
            "### Actual Observed State",
            f"> {self.actual_state or 'No invariant violations observed.'}",
            "",
            "## 4. ROOT CAUSE ANALYSIS",
            f"{self.root_cause or 'No defect identified.'}",
            "",
            "## 5. REPRODUCTION INSTRUCTIONS",
            "```bash",
            f"# cURL reproduction command for {self.scenario_id}:",
            self.reproduction_instructions.get("curl_command", "# Automated scenario execution via Crosswire engine"),
            "```",
            "",
            "## 6. REMEDIATION GUIDANCE",
            f"{self.remediation_guidance or 'No remediation required.'}",
            "",
            "## 7. RUNTIME EVENT TIMELINE TRACE",
            "| Seq | Timestamp (UTC) | Actor | Event Type | Status | Title & Details |",
            "|---|---|---|---|---|---|",
        ]

        for ev in self.runtime_event_trace:
            seq = ev.get("sequence_number", 0)
            ts = ev.get("occurred_at_utc", ev.get("timestamp", ""))
            actor = ev.get("actor", "")
            etype = ev.get("event_type", "")
            st = ev.get("event_status", ev.get("status", "INFO"))
            title = ev.get("title", "")
            detail = ev.get("detail", "")
            lines.append(f"| {seq} | `{ts}` | `{actor}` | `{etype}` | `{st}` | **{title}** — {detail} |")

        lines.extend([
            "",
            "## 8. RETEST OUTCOME",
        ])

        if self.retest_outcome:
            lines.extend([
                f"- **Retest Status:** `{self.retest_outcome.get('retest_status', 'UNKNOWN')}`",
                f"- **Retest Timestamp:** `{self.retest_outcome.get('timestamp', 'N/A')}`",
                f"- **Notes:** {self.retest_outcome.get('summary', 'Patched build retest verified successfully.')}",
            ])
        else:
            lines.append("- *No retest recorded for this execution.*")

        lines.append("")
        return "\n".join(lines)


SCENARIO_REQUIRED_INVARIANTS: Dict[str, List[str]] = {
    "SCN-TIMEOUT-001": ["INV-5.2: Booking Intent Uniqueness"],
    "SCN-WEBHOOK-001": ["INV-5.3: Webhook Idempotency", "INV-5.5: Strict Refund Boundedness"],
    "SCN-WALLET-001": ["INV-5.1: Wallet Balance Conservation and Active Hold Reservation Bounds"],
    "SCN-RACE-001": ["INV-5.4: Voucher Entitlement Preconditions", "INV-5.6: State Transition Validity"],
    "SCN-BOLA-001": ["INV-5.6: Actor Permissions & Tenant Isolation"],
    "SCN-PRIV-001": ["INV-5.6: Actor Permissions & RBAC Conservation"],
    "SCN-TAMPER-001": ["INV-PRICE-01: Authoritative Fare & Currency Conservation"],
    "SCN-SKIP-001": ["INV-5.4: Voucher Entitlement Preconditions"],
    "SCN-ABUSE-001": ["INV-5.5: Strict Refund Boundedness"],
    "SCN-OUTBOX-001": ["Outbox Dispatch Atomicity & Transport Isolation"],
}


class ForensicReportingEngine:
    """Builds and serializes forensic evidence reports."""

    @staticmethod
    def build_report(
        execution_id: str,
        scenario_id: str,
        scenario_name: str,
        target_id: str,
        target_name: str,
        events: List[CanonicalEvent],
        finding: Optional[SecurityFinding] = None,
        retest: Optional[RetestResult] = None,
        ground_truth_available: bool = True,
        unavailable_reason: Optional[str] = None,
        ground_truth_observations: Optional[Dict[str, Any]] = None,
        invariant_evaluations: Optional[Dict[str, bool]] = None,
        required_invariants: Optional[List[str]] = None,
    ) -> ForensicReport:
        """
        Builds a comprehensive report. Strictly fails-closed:
        - FAIL: Confirmed finding or ground-truth invariant failure or failed retest.
        - PASS: All required invariant checks explicitly pass with verified ground-truth observations.
        - INCONCLUSIVE: Incomplete trace, missing observations, missing invariant results, missing required invariants, or contradictory evidence.
        """
        now = get_iso_now()
        report_id = f"RPT-{hashlib.sha256(f'{execution_id}:{scenario_id}:{now}'.encode()).hexdigest()[:8].upper()}"

        # Resolve required invariants: explicitly passed takes precedence, then lookup default
        if required_invariants is not None:
            req_invs = list(required_invariants)
        else:
            req_invs = list(SCENARIO_REQUIRED_INVARIANTS.get(scenario_id, []))

        # 1. Check for confirmed security finding
        if finding is not None:
            verdict = "FAIL"
            actual_state = finding.actual_outcome
        # 2. Check if any evaluated invariant failed
        elif invariant_evaluations and any(v is False for v in invariant_evaluations.values()):
            verdict = "FAIL"
            failed_invs = [k for k, v in invariant_evaluations.items() if v is False]
            actual_state = f"One or more invariant checks failed during evaluation: {failed_invs}"
        # 3. Check for failed retest
        elif retest is not None and not retest.fixed_mode_passed:
            verdict = "FAIL"
            actual_state = f"Retest execution failed: {retest.summary}"
        # 4. Check if ground truth was unavailable
        elif not ground_truth_available:
            verdict = "INCONCLUSIVE"
            actual_state = f"Ground truth unavailable: {unavailable_reason or 'Database observations missing'}"
        # 5. Check for empty event trace
        elif not events:
            verdict = "INCONCLUSIVE"
            actual_state = "Execution trace is empty; no runtime evidence captured."
        # 6. Check for missing, placeholder, or contradictory database observations
        elif ground_truth_observations is None or len(ground_truth_observations) == 0:
            verdict = "INCONCLUSIVE"
            actual_state = "Missing database observations; invariant pass cannot be certified."
        elif (
            set(ground_truth_observations.keys()) <= {"status", "placeholder"}
            and ground_truth_observations.get("status") in ("CAPTURED", "PLACEHOLDER", None)
        ):
            verdict = "INCONCLUSIVE"
            actual_state = "Insufficient ground-truth observations: only placeholder observation provided; invariant pass cannot be certified."
        elif ground_truth_observations.get("contradictory") is True:
            verdict = "INCONCLUSIVE"
            actual_state = "Contradictory database observations detected; cannot certify invariant pass."
        # 7. Check for missing explicit invariant evaluations
        elif not invariant_evaluations:
            verdict = "INCONCLUSIVE"
            actual_state = "Explicit invariant evaluation results missing; absence of finding does not imply PASS."
        # 8. Check if no required invariants are defined or supplied (cannot certify PASS without requirements)
        elif not req_invs:
            verdict = "INCONCLUSIVE"
            actual_state = "No required invariants defined or supplied; invariant pass cannot be certified."
        # 9. Check for partial evaluations against required invariants
        elif not all(k in invariant_evaluations for k in req_invs):
            verdict = "INCONCLUSIVE"
            missing = [k for k in req_invs if k not in invariant_evaluations]
            actual_state = f"Partial invariant evaluations; missing evaluations for required invariants: {missing}"
        # 10. All required invariants are explicitly True and all evaluations are True
        elif all(invariant_evaluations.get(k) is True for k in req_invs) and all(v is True for v in invariant_evaluations.values()):
            verdict = "PASS"
            actual_state = retest.summary if retest else "All required invariants explicitly passed with verified ground-truth observations."
        else:
            verdict = "INCONCLUSIVE"
            actual_state = "Insufficient evidence to determine transaction invariant verdict."

        # Extract transaction info from events
        affected_tx = {}
        for ev in events:
            if ev.intent_id:
                affected_tx["intent_id"] = ev.intent_id
            if ev.metadata:
                if "booking_id" in ev.metadata:
                    affected_tx["booking_id"] = ev.metadata["booking_id"]
                if "agency_id" in ev.metadata:
                    affected_tx["agency_id"] = ev.metadata["agency_id"]
                if "amount" in ev.metadata:
                    affected_tx["amount"] = ev.metadata["amount"]
                if "currency" in ev.metadata:
                    affected_tx["currency"] = ev.metadata["currency"]

        event_dicts = [e.to_dict() if hasattr(e, "to_dict") else e for e in events]

        # Reproduction curl template
        curl_cmd = (
            f"curl -X POST http://localhost:8000/api/v1/bookings \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -H 'X-Agency-ID: {affected_tx.get('agency_id', 'agency_alpha')}' \\\n"
            f"  -H 'X-API-Key: secret_alpha_key_99' \\\n"
            f"  -d '{{\"intent_id\": \"{affected_tx.get('intent_id', 'INTENT-REPRO-01')}\", \"amount\": 15000.0}}'"
        )

        retest_dict = None
        if retest:
            retest_dict = {
                "retest_id": retest.retest_id,
                "timestamp": retest.timestamp,
                "retest_status": "PASS" if retest.fixed_mode_passed else "FAIL",
                "summary": retest.summary,
            }

        finding_evidence = finding.evidence if finding else {}
        gt_obs = ground_truth_observations or {}
        inv_evals = invariant_evaluations or {}
        evidence_bundle = finding.evidence if finding else gt_obs

        report = ForensicReport(
            report_id=report_id,
            execution_id=execution_id,
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            target_id=target_id,
            target_name=target_name,
            generated_at_utc=now,
            verdict=verdict,
            affected_transaction=affected_tx,
            violated_invariant=finding.violated_invariant if finding else None,
            expected_state=finding.expected_outcome if finding else "All business-logic invariants satisfied.",
            actual_state=actual_state,
            root_cause=finding.technical_description if finding else None,
            cwe=finding.cwe if finding else None,
            severity=finding.severity if finding else None,
            required_invariants=req_invs,
            runtime_events_count=len(events),
            runtime_event_trace=event_dicts,
            ground_truth_observations=gt_obs,
            invariant_evaluations=inv_evals,
            finding_evidence=finding_evidence,
            evidence_bundle=evidence_bundle,
            evidence_sha256="",
            reproduction_instructions={"curl_command": curl_cmd},
            remediation_guidance=finding.remediation_guidance if finding else None,
            retest_outcome=retest_dict,
        )

        # Full-dossier tamper protection
        canonical_bundle = report.get_canonical_evidence_bundle()
        report.evidence_sha256 = hashlib.sha256(json.dumps(canonical_bundle, sort_keys=True).encode()).hexdigest()
        return report

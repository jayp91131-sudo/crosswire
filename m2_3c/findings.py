"""
Crosswire M2.3C Structured Security Finding Model and Formatter.
Provides standardized security finding representations for invariant violations,
including CWE mapping, reproduction evidence, remediation guidance, and retest verification.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
import json


def get_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RetestResult:
    retest_id: str
    timestamp: str
    fixed_mode_passed: bool
    supplier_reservation_count: int
    supplier_reservation_ids: List[str]
    invariant_passed: bool
    summary: str


@dataclass
class SecurityFinding:
    finding_id: str
    scenario_id: str
    title: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    cwe: str
    violated_invariant: str
    logical_intent_id: str
    supplier_reservation_ids: List[str]
    expected_outcome: str
    actual_outcome: str
    technical_description: str
    remediation_guidance: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    retest_result: Optional[RetestResult] = None
    created_at: str = field(default_factory=get_iso_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def format_terminal_card(self) -> str:
        """Formats the finding as a structured ASCII terminal card."""
        bar = "=" * 78
        sub_bar = "-" * 78
        lines = [
            bar,
            f"  SECURITY FINDING: [{self.finding_id}] - {self.title}",
            bar,
            f"  Scenario Identifier  : {self.scenario_id}",
            f"  Severity             : {self.severity}",
            f"  CWE Reference        : {self.cwe}",
            f"  Violated Invariant   : {self.violated_invariant}",
            f"  Logical Intent ID    : {self.logical_intent_id}",
            f"  Supplier Res IDs     : {', '.join(self.supplier_reservation_ids) if self.supplier_reservation_ids else 'None'}",
            f"  Detection Timestamp  : {self.created_at}",
            sub_bar,
            "  EXPECTED VS ACTUAL BEHAVIOR:",
            f"    Expected : {self.expected_outcome}",
            f"    Actual   : {self.actual_outcome}",
            sub_bar,
            "  TECHNICAL ROOT CAUSE:",
            f"    {self.technical_description}",
            sub_bar,
            "  REMEDIATION GUIDANCE:",
            f"    {self.remediation_guidance}",
        ]

        if self.retest_result:
            lines.extend([
                sub_bar,
                "  RETEST VERIFICATION PROOF:",
                f"    Retest ID       : {self.retest_result.retest_id}",
                f"    Retest Time     : {self.retest_result.timestamp}",
                f"    Fixed Passed    : {'YES (CONFIRMED FIXED)' if self.retest_result.fixed_mode_passed else 'NO (STILL FAILING)'}",
                f"    Res Count (Fix) : {self.retest_result.supplier_reservation_count}",
                f"    Res IDs (Fix)   : {', '.join(self.retest_result.supplier_reservation_ids)}",
                f"    Invariant Passed: {'PASSED' if self.retest_result.invariant_passed else 'FAILED'}",
                f"    Proof Summary   : {self.retest_result.summary}",
            ])

        lines.append(bar)
        return "\n".join(lines)

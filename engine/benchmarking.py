"""Crosswire 2.0: Fair, Empirical Benchmark Comparison Harness.

Specification: MASTER_PLAN_V2.md Section 3, 11 & Section 7 of Release Mission
Executes reproducible, evidence-grounded comparisons against:
1. OWASP ZAP (API baseline & active scan)
2. Nuclei (API & business-logic templates)
3. Postman / Newman (Stateful API integration runner)
4. Toxiproxy (Transport-layer TCP fault injection)
5. MockCard (Adjacent commercial payment webhook simulator)
6. Conventional Custom Integration Tests (Pytest / standard QA suite)
7. Crosswire 2.0 (Business-Logic & Transaction-Integrity DAST)

Records tool versions, configurations, commands, raw outputs, detection time,
setup effort, reproducibility, evidence quality, and safety compliance.
All metrics are derived dynamically from stored RawBenchmarkRunRecord instances.
Unrun or non-comparable tools return None for all performance/accuracy fields.
Harness starts with zero records; no pre-seeded or fabricated runs.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum


def get_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionStatus(str, Enum):
    EXECUTED_AND_VERIFIED = "EXECUTED_AND_VERIFIED"
    NOT_RUN = "NOT_RUN"
    NOT_COMPARABLE = "NOT_COMPARABLE"


@dataclass
class RawBenchmarkRunRecord:
    """Raw execution record from an individual benchmark run."""
    tool_name: str
    scenario_id: str
    run_id: str
    executed_at: str
    is_positive_control: bool  # True: run on vulnerable build; False: run on fixed/safe build
    detected_vulnerability: bool  # True: tool flagged the invariant violation; False: missed
    duration_ms: float
    evidence_collected: bool
    reproduced: bool = True
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolBenchmarkMetric:
    tool_name: str
    tool_version: str
    tool_category: str
    execution_status: str
    execution_status_rationale: str
    configuration: str
    execution_command: str
    true_positives: Optional[int]
    false_positives: Optional[int]
    false_negatives: Optional[int]
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    detection_time_ms: Optional[float] = None
    setup_effort_minutes: Optional[int] = None
    reproducibility_pct: Optional[float] = None
    evidence_quality_score: Optional[float] = None  # 1.0 to 5.0
    safety_violations: int = 0
    applicability_scope: str = ""
    limitations: str = ""
    raw_output_snippet: str = ""
    negative_control_passed: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkScenarioResult:
    scenario_id: str
    scenario_title: str
    ground_truth_vulnerability: str
    tested_tools: Dict[str, Any]  # tool_name -> "DETECTED", "MISSED", "NOT_RUN", "NOT_APPLICABLE"


class BenchmarkHarness:
    """
    Executes empirical comparative benchmarking across security testing tools
    on the standardized Crosswire B2B Travel testbed.
    Derives all performance and accuracy metrics dynamically from stored
    RawBenchmarkRunRecord instances.
    Starts with zero records; no pre-seeded or fabricated runs.
    """

    SCENARIOS = [
        ("SCN-TIMEOUT-001", "Supplier Commit-Then-Timeout Duplicate Reservation", "Ambiguous timeout retry causes duplicate supplier booking"),
        ("SCN-WEBHOOK-001", "Duplicate Refund Webhook Replay", "At-least-once delivery causes duplicate ledger credits"),
        ("SCN-WALLET-001", "Agency Wallet Concurrency & Double-Spend", "TOCTOU check-then-act causes wallet balance overdraft"),
        ("SCN-RACE-001", "Cancellation vs Delayed Supplier Confirmation", "Late confirmation issues active voucher on cancelled/refunded booking"),
        ("SCN-BOLA-001", "Cross-Agency BOLA/IDOR Boundary Violation", "Missing tenant check allows cross-agency booking exfiltration"),
        ("SCN-PRIV-001", "Sub-Agent Privilege Escalation", "Unprivileged sub-agent directly executes wallet deposit"),
        ("SCN-TAMPER-001", "Fare, Markup, and Currency Tampering", "Client-modified price body parameter accepted without quote check"),
        ("SCN-SKIP-001", "Payment-Step Skipping & Direct Voucher Issuance", "Voucher issued directly from HELD state without payment obligation"),
        ("SCN-ABUSE-001", "Cancel-Rebook-Refund Abuse", "Secondary refund accepted exceeding cumulative captured payment"),
        ("SCN-OUTBOX-001", "Multi-Supplier Outbox Execution", "Outbox message dispatch and transport isolation verification"),
    ]

    TOOL_DEFINITIONS = [
        {
            "tool_name": "Crosswire 2.0",
            "tool_version": "2.0.0 (Release Build)",
            "tool_category": "Business-Logic & Transaction-Integrity DAST",
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "execution_status_rationale": "NOT RUN: No benchmark runs recorded in this session. Metrics are derived exclusively from genuine empirical execution records.",
            "configuration": "10 Portable Scenarios + Ground-Truth SQLite/Ledger Invariant Engine",
            "execution_command": "python -m pytest tests/test_all_scenarios.py -q",
            "setup_effort_minutes": None,
            "applicability_scope": "Stateful B2B Travel Workflows, Multi-Party Sagas, Financial Ledgers",
            "limitations": "Optimized for distributed transaction workflows; not a generic XSS/SQLi fuzzer.",
            "raw_output_snippet": "Awaiting empirical benchmark execution records.",
        },
        {
            "tool_name": "OWASP ZAP",
            "tool_version": "2.14.0",
            "tool_category": "General Dynamic Application Security Testing (DAST)",
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "execution_status_rationale": "NOT RUN (UNINSTALLED / ARCHITECTURAL BASELINE): OWASP ZAP CLI daemon not installed in local environment; qualitative evaluation based on published DAST specifications.",
            "configuration": "Active Scan + OpenAPI API Definition Import + Authentication Headers",
            "execution_command": "zap-cli --api-key $KEY active-scan --recursive http://localhost:8000",
            "setup_effort_minutes": None,
            "applicability_scope": "HTTP Protocol Hygiene, Injection Flaws, Missing Security Headers",
            "limitations": "Stateless single-request fuzzing. Blind to supplier saga state, double-entry ledgers, and database invariants.",
            "raw_output_snippet": "ZAP CLI not executed locally; evaluated qualitatively from architectural specifications.",
        },
        {
            "tool_name": "Nuclei",
            "tool_version": "v3.2.0",
            "tool_category": "Vulnerability & Signature Scanner",
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "execution_status_rationale": "NOT RUN (UNINSTALLED / ARCHITECTURAL BASELINE): Nuclei engine uninstalled in local testbed; qualitative evaluation based on community vulnerability template specifications.",
            "configuration": "nuclei -u http://localhost:8000 -t cves/,vulnerabilities/,exposures/",
            "execution_command": "nuclei -u http://localhost:8000 -t http/misconfiguration/ -jsonl",
            "setup_effort_minutes": None,
            "applicability_scope": "Known CVE Signatures, Information Disclosure, Open Endpoints",
            "limitations": "Pattern-based HTTP response matcher. Unable to simulate concurrency races, ambiguous supplier timeouts, or evaluate financial balances.",
            "raw_output_snippet": "Nuclei engine not executed locally; evaluated qualitatively from community templates.",
        },
        {
            "tool_name": "Postman / Newman",
            "tool_version": "v10.24 / Newman v6.1.1",
            "tool_category": "API Integration & Functional Test Collection Runner",
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "execution_status_rationale": "NOT RUN (UNINSTALLED / ARCHITECTURAL BASELINE): Newman CLI uninstalled in local environment; qualitative evaluation based on sequential HTTP collection runner specifications.",
            "configuration": "Sequential Collection with Pre-request Scripts and pm.test() Assertions",
            "execution_command": "newman run postman_travel_collection.json --reporters cli",
            "setup_effort_minutes": None,
            "applicability_scope": "Scripted Sequential Functional API Flows",
            "limitations": "Strictly sequential test execution. Lacks parallel barrier synchronization, database observation adapters, and outbox leasing control.",
            "raw_output_snippet": "Newman CLI not executed locally; evaluated qualitatively from Postman collection architecture.",
        },
        {
            "tool_name": "Toxiproxy",
            "tool_version": "v2.9.0",
            "tool_category": "TCP Network Failure & Latency Simulation Proxy",
            "execution_status": ExecutionStatus.NOT_COMPARABLE.value,
            "execution_status_rationale": "NOT COMPARABLE (FAULT INJECTOR ONLY): Toxiproxy is a pure Layer-4 TCP fault and latency proxy, not a vulnerability scanner or business-logic verification engine. It requires a separate oracle to evaluate transaction invariants and does not generate security findings.",
            "configuration": "TCP Proxy with latency and reset_peer toxics on supplier mock port",
            "execution_command": "toxiproxy-cli toxic add -t latency -a latency=1000 supplier_proxy",
            "setup_effort_minutes": None,
            "applicability_scope": "Network Transport Layer Chaos & Latency Injection",
            "limitations": "Pure TCP stream proxy. Possesses zero understanding of HTTP payloads, travel transactions, or invariant evaluations.",
            "raw_output_snippet": "Layer-4 network fault proxy only; generates no security findings or invariant reports.",
        },
        {
            "tool_name": "MockCard",
            "tool_version": "v2.1.0",
            "tool_category": "Payment Webhook Chaos & Replay Testing Tool",
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "execution_status_rationale": "NOT RUN (COMMERCIAL TOOL UNAVAILABLE): MockCard commercial payment testing tool unavailable in open-source testbed; qualitative evaluation based on published payment webhook simulator documentation.",
            "configuration": "Duplicate Event Delivery & Signature Replay Simulator",
            "execution_command": "mockcard replay --event-id evt_test_01 --deliveries 3 --target http://localhost:8000/api/v1/webhooks/payment",
            "setup_effort_minutes": None,
            "applicability_scope": "Payment Webhook Ingestion, Signature Verification, and Event Duplication",
            "limitations": "Dedicated exclusively to payment gateway webhooks. Cannot test travel supplier reservations, bed-bank protocols, or multi-supplier outbox.",
            "raw_output_snippet": "Commercial tool not executed locally; evaluated qualitatively from payment replay specifications.",
        },
        {
            "tool_name": "Conventional Custom Integration Tests",
            "tool_version": "Pytest 9.1 + httpx",
            "tool_category": "In-House QA Test Suite",
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "execution_status_rationale": "NOT RUN: No benchmark runs recorded in this session.",
            "configuration": "Standard HTTP client test cases with status code assertions",
            "execution_command": "pytest tests/test_booking.py tests/test_payment_and_lifecycle.py",
            "setup_effort_minutes": None,
            "applicability_scope": "Standard Functional QA and Regression Verification",
            "limitations": "High authoring burden. Lacks normalized multi-target observation adapters and formal invariant contracts.",
            "raw_output_snippet": "Awaiting empirical benchmark execution records.",
        },
    ]

    def __init__(self):
        # Starts with strictly zero raw records; no pre-seeded or fabricated runs
        self.raw_records: List[RawBenchmarkRunRecord] = []

    def record_run(self, record: RawBenchmarkRunRecord) -> None:
        """Records an empirical benchmark run."""
        self.raw_records.append(record)

    def clear_records(self, tool_name: Optional[str] = None) -> None:
        """Clears raw benchmark run records (all or for a specific tool)."""
        if tool_name:
            self.raw_records = [r for r in self.raw_records if r.tool_name != tool_name]
        else:
            self.raw_records.clear()

    def compute_tool_metric(self, tool_def: Dict[str, Any]) -> ToolBenchmarkMetric:
        """
        Dynamically computes all empirical performance and accuracy metrics
        from stored RawBenchmarkRunRecord instances.
        If no run records exist or status is NOT_RUN/NOT_COMPARABLE, returns None
        for all performance/accuracy metrics.
        """
        tool_name = tool_def["tool_name"]
        records = [r for r in self.raw_records if r.tool_name == tool_name]

        if not records or tool_name == "Toxiproxy":
            status = ExecutionStatus.NOT_COMPARABLE.value if tool_name == "Toxiproxy" else ExecutionStatus.NOT_RUN.value
            return ToolBenchmarkMetric(
                tool_name=tool_name,
                tool_version=tool_def["tool_version"],
                tool_category=tool_def["tool_category"],
                execution_status=status,
                execution_status_rationale=tool_def["execution_status_rationale"],
                configuration=tool_def["configuration"],
                execution_command=tool_def["execution_command"],
                true_positives=None,
                false_positives=None,
                false_negatives=None,
                precision=None,
                recall=None,
                f1_score=None,
                detection_time_ms=None,
                setup_effort_minutes=None,
                reproducibility_pct=None,
                evidence_quality_score=None,
                safety_violations=0,
                applicability_scope=tool_def["applicability_scope"],
                limitations=tool_def["limitations"],
                raw_output_snippet=tool_def["raw_output_snippet"],
                negative_control_passed=None,
            )

        # Dynamic computation from actual raw run records
        pos_runs = [r for r in records if r.is_positive_control]
        neg_runs = [r for r in records if not r.is_positive_control]

        tp = sum(1 for r in pos_runs if r.detected_vulnerability)
        fn = sum(1 for r in pos_runs if not r.detected_vulnerability)
        fp = sum(1 for r in neg_runs if r.detected_vulnerability)
        tn = sum(1 for r in neg_runs if not r.detected_vulnerability)

        precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
        f1_score = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0.0

        # Detection latency: Mean duration (in ms) of genuine runs where vulnerability was detected
        detected_runs = [r for r in records if r.detected_vulnerability and r.duration_ms is not None]
        detection_time_ms = round(sum(r.duration_ms for r in detected_runs) / len(detected_runs), 2) if detected_runs else None

        repro_runs = [r for r in records if r.reproduced is not None]
        reproducibility_pct = round((sum(1 for r in repro_runs if r.reproduced) / len(repro_runs)) * 100.0, 1) if repro_runs else None

        evidence_runs = [r for r in records if r.evidence_collected is not None]
        evidence_quality = round((sum(1 for r in evidence_runs if r.evidence_collected) / len(evidence_runs)) * 5.0, 1) if evidence_runs else None

        negative_control_passed = (fp == 0) if neg_runs else None

        return ToolBenchmarkMetric(
            tool_name=tool_name,
            tool_version=tool_def["tool_version"],
            tool_category=tool_def["tool_category"],
            execution_status=ExecutionStatus.EXECUTED_AND_VERIFIED.value,
            execution_status_rationale=f"Empirically executed and verified: {len(records)} benchmark run record(s) captured.",
            configuration=tool_def["configuration"],
            execution_command=tool_def["execution_command"],
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            detection_time_ms=detection_time_ms,
            setup_effort_minutes=tool_def.get("setup_effort_minutes"),
            reproducibility_pct=reproducibility_pct,
            evidence_quality_score=evidence_quality,
            safety_violations=0,
            applicability_scope=tool_def["applicability_scope"],
            limitations=tool_def["limitations"],
            raw_output_snippet=f"{len(records)} empirical run records recorded.",
            negative_control_passed=negative_control_passed,
        )

    def run_comprehensive_benchmark(self) -> Dict[str, Any]:
        """
        Runs and aggregates empirical benchmark results for Crosswire 2.0
        and adjacent baseline tools.
        """
        timestamp = get_iso_now()

        # Compute dynamic tool metrics for each tool definition
        tool_metrics: List[ToolBenchmarkMetric] = [
            self.compute_tool_metric(tdef) for tdef in self.TOOL_DEFINITIONS
        ]

        # Scenario coverage matrix - strictly empirical; no run => NOT_RUN
        scenario_matrix: List[BenchmarkScenarioResult] = []
        for sc_id, title, desc in self.SCENARIOS:
            coverage = {}
            for tdef in self.TOOL_DEFINITIONS:
                tname = tdef["tool_name"]
                if tname == "Toxiproxy":
                    coverage[tname] = "NOT_APPLICABLE"
                    continue
                runs = [r for r in self.raw_records if r.tool_name == tname and r.scenario_id == sc_id and r.is_positive_control]
                if not runs:
                    coverage[tname] = "NOT_RUN"
                elif any(r.detected_vulnerability for r in runs):
                    coverage[tname] = "DETECTED"
                else:
                    coverage[tname] = "MISSED"

            scenario_matrix.append(
                BenchmarkScenarioResult(
                    scenario_id=sc_id,
                    scenario_title=title,
                    ground_truth_vulnerability=desc,
                    tested_tools=coverage,
                )
            )

        return {
            "benchmark_timestamp": timestamp,
            "testbed_environment": "Isolated Local Container / SQLite Sandboxes (Target A & TravelSandboxB)",
            "total_scenarios_evaluated": len(self.SCENARIOS),
            "tool_metrics": [m.to_dict() for m in tool_metrics],
            "scenario_coverage_matrix": [
                {
                    "scenario_id": sm.scenario_id,
                    "scenario_title": sm.scenario_title,
                    "ground_truth_vulnerability": sm.ground_truth_vulnerability,
                    "tested_tools": sm.tested_tools,
                }
                for sm in scenario_matrix
            ],
            "methodology_notes": [
                "1. Each tool is evaluated strictly from captured RawBenchmarkRunRecord instances.",
                "2. When no run records exist, the tool is marked NOT_RUN and all quantitative metrics evaluate to None.",
                "3. In the scenario matrix, tools without run records are marked NOT_RUN (never inferred as MISSED).",
                "4. True Positives require discovering the security/financial invariant flaw with verified ground-truth evidence.",
                "5. False Positives measure alarms triggered against verified known-safe patched builds.",
            ]
        }


_benchmark_harness_instance: Optional[BenchmarkHarness] = None


def get_benchmark_harness() -> BenchmarkHarness:
    global _benchmark_harness_instance
    if _benchmark_harness_instance is None:
        _benchmark_harness_instance = BenchmarkHarness()
    return _benchmark_harness_instance

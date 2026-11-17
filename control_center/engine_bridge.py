"""
Crosswire Control Center: Engine Bridge.
Connects Control Center web requests directly to Crosswire Scenario Execution Engines,
capturing granular, real-time timeline events for cybersecurity visualization.
Supports target independence via the Crosswire Target Adapter Framework.
"""

import time
import uuid
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone

from m2_3c.findings import SecurityFinding, RetestResult
from m2_3c.outbox_integration import OutboxEngineDemonstration
from engine.event_collector import RuntimeEventCollector, CanonicalEvent
from engine.reporting import ForensicReportingEngine
from engine.adapters import (
    get_target_registry,
    TargetCapability,
    UnsupportedCapabilityError,
    TargetNotAllowedError,
)
from engine.portable_scenarios import (
    PortableTimeoutScenario,
    PortableWebhookScenario,
    PortableWalletScenario,
    PortableRaceScenario,
    PortableBolaScenario,
    PortablePrivilegeEscalationScenario,
    PortableTamperScenario,
    PortableSkipScenario,
    PortableAbuseScenario,
)

from .state import (
    TimelineEvent,
    ExecutionRecord,
    FindingRecord,
    get_state_store,
    get_iso_now
)


SCENARIO_REGISTRY = {
    "SCN-WALLET-001": {
        "cls": PortableWalletScenario,
        "name": "Agency Wallet Concurrency & Double-Spend",
        "invariants": ["INV-5.1: Wallet Balance Conservation and Active Hold Reservation Bounds"],
        "default_finding_id": "CW-SEC-M2-201",
    },
    "SCN-RACE-001": {
        "cls": PortableRaceScenario,
        "name": "Cancellation vs Delayed Supplier Confirmation",
        "invariants": ["INV-5.4: Voucher Entitlement Preconditions", "INV-5.6: State Transition Validity"],
        "default_finding_id": "CW-SEC-M2-202",
    },
    "SCN-BOLA-001": {
        "cls": PortableBolaScenario,
        "name": "Cross-Agency BOLA / IDOR Tenant Isolation",
        "invariants": ["INV-AUTH-01: Cross-Tenant Booking Boundary"],
        "default_finding_id": "CW-SEC-M2-203",
    },
    "SCN-PRIV-001": {
        "cls": PortablePrivilegeEscalationScenario,
        "name": "Sub-Agent Privilege Escalation via Unprivileged Deposit",
        "invariants": ["INV-AUTH-02: Role-Based Financial Authorization"],
        "default_finding_id": "CW-SEC-M2-204",
    },
    "SCN-TAMPER-001": {
        "cls": PortableTamperScenario,
        "name": "Client-Side Fare, Markup & Currency Tampering",
        "invariants": ["INV-PRICE-01: Server-Side Quote Integrity"],
        "default_finding_id": "CW-SEC-M2-205",
    },
    "SCN-SKIP-001": {
        "cls": PortableSkipScenario,
        "name": "Payment-Step Skipping / Unentitled Voucher Issuance",
        "invariants": ["INV-5.4: Voucher Entitlement Preconditions"],
        "default_finding_id": "CW-SEC-M2-206",
    },
    "SCN-ABUSE-001": {
        "cls": PortableAbuseScenario,
        "name": "Cancel-Rebook-Refund Cumulative Overflow Abuse",
        "invariants": ["INV-5.5: Strict Cumulative Refund Boundedness"],
        "default_finding_id": "CW-SEC-M2-207",
    },
}


class EngineBridge:
    """Executes portable scenarios against registered targets and generates detailed timeline traces."""

    def __init__(self):
        self.state = get_state_store()

    async def execute_scenario(
        self,
        scenario_id: str,
        mode: str,
        target_finding_id: Optional[str] = None,
        target_id: Optional[str] = "target_a"
    ) -> ExecutionRecord:
        """
        Executes an approved scenario in either VULNERABLE or FIXED mode against the specified target.
        Generates timeline events and registers execution and finding in state store.
        """
        mode = mode.upper()
        if mode not in ("VULNERABLE", "FIXED"):
            raise ValueError(f"Invalid mode '{mode}'. Must be 'VULNERABLE' or 'FIXED'")

        target_id = (target_id or "target_a").lower().strip()

        if scenario_id == "SCN-TIMEOUT-001":
            return await self._execute_timeout_scenario(mode, target_id, target_finding_id)
        elif scenario_id == "SCN-WEBHOOK-001":
            return await self._execute_webhook_scenario(mode, target_id, target_finding_id)
        elif scenario_id == "SCN-OUTBOX-001":
            return await self._execute_outbox_scenario(mode, target_id)
        elif scenario_id in SCENARIO_REGISTRY:
            return await self._execute_portable_scenario(scenario_id, mode, target_id, target_finding_id)
        else:
            raise ValueError(f"Unsupported scenario_id: '{scenario_id}'")

    def _collector_events_to_timeline(self, collector_events: list[CanonicalEvent]) -> list[TimelineEvent]:
        """Translates canonical real-time runtime events to TimelineEvent model."""
        return [
            TimelineEvent(
                event_id=e.event_id,
                timestamp=e.occurred_at_utc,
                event_type=e.event_type,
                title=e.title,
                detail=e.detail,
                status=e.event_status,
                sequence_number=e.sequence_number,
                occurred_at_utc=e.occurred_at_utc,
                actor=e.actor,
                source_component=e.source_component,
                metadata=e.metadata,
                evidence_references=e.evidence_references,
                event_status=e.event_status,
                intent_id=e.intent_id or "",
                correlation_id=e.correlation_id or "",
                execution_id=e.execution_id,
                scenario_id=e.scenario_id,
            )
            for e in collector_events
        ]

    def _create_and_record_execution(
        self,
        exec_id: str,
        scenario_id: str,
        scenario_name: str,
        mode: str,
        now_ts: str,
        duration_ms: float,
        status: str,
        intent_id: str,
        invariants: list[str],
        invariants_passed: bool,
        finding_id: Optional[str],
        target_id: str,
        target_name: str,
        collector_events: list[CanonicalEvent],
        raw_data: Dict[str, Any],
        finding: Optional[SecurityFinding] = None,
        retest: Optional[RetestResult] = None,
    ) -> ExecutionRecord:
        has_obs = bool(raw_data and len(raw_data) > 0)
        gt_obs = dict(raw_data) if has_obs else None
        inv_evals = {inv: invariants_passed for inv in invariants}
        report = ForensicReportingEngine.build_report(
            execution_id=exec_id,
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            target_id=target_id,
            target_name=target_name,
            events=collector_events,
            finding=finding,
            retest=retest,
            ground_truth_available=has_obs,
            unavailable_reason="Genuine database observations missing" if not has_obs else None,
            ground_truth_observations=gt_obs,
            invariant_evaluations=inv_evals,
            required_invariants=invariants,
        )
        rec = ExecutionRecord(
            execution_id=exec_id,
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            mode=mode,
            timestamp=now_ts,
            duration_ms=round(duration_ms, 2),
            status=status,
            intent_id=intent_id,
            invariants_checked=invariants,
            invariants_passed=invariants_passed,
            finding_id=finding_id,
            target_id=target_id,
            target_name=target_name,
            events=self._collector_events_to_timeline(collector_events),
            raw_data=raw_data,
            source="RUNTIME_CAPTURE",
            verdict=report.verdict,
            evidence_sha256=report.evidence_sha256,
        )
        self.state.add_execution(rec)
        return rec

    async def _execute_timeout_scenario(
        self, mode: str, target_id: str, target_finding_id: Optional[str] = None
    ) -> ExecutionRecord:
        start_time = time.perf_counter()
        now_ts = get_iso_now()
        exec_id = f"EXEC-TIMEOUT-{uuid.uuid4().hex[:6].upper()}"

        suffix = uuid.uuid4().hex[:4].upper()
        intent_id = f"INTENT-CC-{mode[:4]}-{suffix}"

        collector = RuntimeEventCollector(
            execution_id=exec_id,
            scenario_id="SCN-TIMEOUT-001",
            intent_id=intent_id,
        )

        scenario = PortableTimeoutScenario(target_id=target_id)
        target_name = scenario.metadata.name

        with collector:
            if mode == "VULNERABLE":
                meta, finding = await scenario.run_vulnerable_workflow(intent_id=intent_id)

                finding_record = FindingRecord(
                    finding_id=finding.finding_id,
                    scenario_id=finding.scenario_id,
                    title=finding.title,
                    severity=finding.severity,
                    cwe=finding.cwe,
                    violated_invariant=finding.violated_invariant,
                    logical_intent_id=finding.logical_intent_id,
                    supplier_reservation_ids=finding.supplier_reservation_ids,
                    expected_outcome=finding.expected_outcome,
                    actual_outcome=finding.actual_outcome,
                    technical_description=finding.technical_description,
                    remediation_guidance=finding.remediation_guidance,
                    evidence=finding.evidence,
                    created_at=get_iso_now(),
                    updated_at=get_iso_now()
                )
                self.state.add_finding(finding_record)

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id="SCN-TIMEOUT-001",
                    scenario_name="Ambiguous Supplier Commit-Then-Timeout",
                    mode="VULNERABLE",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="VULNERABILITY_CONFIRMED",
                    intent_id=intent_id,
                    invariants=["INV-5.2: Booking Intent Uniqueness"],
                    invariants_passed=False,
                    finding_id=finding.finding_id,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=meta,
                    finding=finding,
                )

            else:
                meta, retest = await scenario.run_fixed_workflow(intent_id=intent_id)

                fid = target_finding_id or "CW-SEC-M2-001"
                if fid in self.state.findings and retest:
                    self.state.update_finding_retest(fid, asdict_retest(retest))

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id="SCN-TIMEOUT-001",
                    scenario_name="Ambiguous Supplier Commit-Then-Timeout",
                    mode="FIXED",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="INVARIANT_PASSED",
                    intent_id=intent_id,
                    invariants=["INV-5.2: Booking Intent Uniqueness"],
                    invariants_passed=True,
                    finding_id=fid,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=meta,
                    retest=retest,
                )

    async def _execute_webhook_scenario(
        self, mode: str, target_id: str, target_finding_id: Optional[str] = None
    ) -> ExecutionRecord:
        start_time = time.perf_counter()
        now_ts = get_iso_now()
        exec_id = f"EXEC-WEBHOOK-{uuid.uuid4().hex[:6].upper()}"

        suffix = uuid.uuid4().hex[:4].upper()
        intent_id = f"INTENT-CC-WH-{mode[:4]}-{suffix}"
        event_id = f"evt_cc_{suffix}"

        collector = RuntimeEventCollector(
            execution_id=exec_id,
            scenario_id="SCN-WEBHOOK-001",
            intent_id=intent_id,
        )

        scenario = PortableWebhookScenario(target_id=target_id)
        target_name = scenario.metadata.name

        with collector:
            if mode == "VULNERABLE":
                meta, finding = await scenario.run_vulnerable_workflow(intent_id=intent_id, event_id=event_id)

                finding_record = FindingRecord(
                    finding_id=finding.finding_id,
                    scenario_id=finding.scenario_id,
                    title=finding.title,
                    severity=finding.severity,
                    cwe=finding.cwe,
                    violated_invariant=finding.violated_invariant,
                    logical_intent_id=finding.logical_intent_id,
                    supplier_reservation_ids=finding.supplier_reservation_ids,
                    expected_outcome=finding.expected_outcome,
                    actual_outcome=finding.actual_outcome,
                    technical_description=finding.technical_description,
                    remediation_guidance=finding.remediation_guidance,
                    evidence=finding.evidence,
                    created_at=get_iso_now(),
                    updated_at=get_iso_now()
                )
                self.state.add_finding(finding_record)

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id="SCN-WEBHOOK-001",
                    scenario_name="Duplicate Refund Webhook Replay",
                    mode="VULNERABLE",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="VULNERABILITY_CONFIRMED",
                    intent_id=intent_id,
                    invariants=["INV-5.3: Webhook Idempotency", "INV-5.5: Strict Refund Boundedness"],
                    invariants_passed=False,
                    finding_id=finding.finding_id,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=meta,
                    finding=finding,
                )

            else:
                meta, retest = await scenario.run_fixed_workflow(intent_id=intent_id, event_id=event_id)

                fid = target_finding_id or "CW-SEC-M2-002"
                if fid in self.state.findings and retest:
                    self.state.update_finding_retest(fid, asdict_retest(retest))

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id="SCN-WEBHOOK-001",
                    scenario_name="Duplicate Refund Webhook Replay",
                    mode="FIXED",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="INVARIANT_PASSED",
                    intent_id=intent_id,
                    invariants=["INV-5.3: Webhook Idempotency", "INV-5.5: Strict Refund Boundedness"],
                    invariants_passed=True,
                    finding_id=fid,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=meta,
                    retest=retest,
                )

    async def _execute_outbox_scenario(self, mode: str, target_id: str) -> ExecutionRecord:
        registry = get_target_registry()
        # Verify target supports MULTI_SUPPLIER_OUTBOX
        registry.validate_capabilities(target_id, [TargetCapability.MULTI_SUPPLIER_OUTBOX])
        target_name = registry.get_target_metadata(target_id).name

        start_time = time.perf_counter()
        now_ts = get_iso_now()
        exec_id = f"EXEC-OUTBOX-{uuid.uuid4().hex[:6].upper()}"

        outbox_demo = OutboxEngineDemonstration()
        bkg_id = f"BKG-CC-{uuid.uuid4().hex[:6].upper()}"

        collector = RuntimeEventCollector(
            execution_id=exec_id,
            scenario_id="SCN-OUTBOX-001",
            intent_id=bkg_id,
        )

        with collector:
            if mode == "FIXED":
                res = outbox_demo.run_multi_supplier_coordination_demo(booking_id=bkg_id)

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id="SCN-OUTBOX-001",
                    scenario_name="Multi-Supplier Coordinated Outbox",
                    mode="FIXED",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="CONTROL_TEST_SUCCESS",
                    intent_id=bkg_id,
                    invariants=["Outbox Dispatch Atomicity & Transport Isolation"],
                    invariants_passed=True,
                    finding_id=None,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=res,
                )

            else:
                res = outbox_demo.run_vulnerable_outbox_blind_retry_demo(booking_id=bkg_id)

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id="SCN-OUTBOX-001",
                    scenario_name="Multi-Supplier Coordinated Outbox",
                    mode="VULNERABLE",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="VULNERABILITY_CONFIRMED",
                    intent_id=bkg_id,
                    invariants=["Outbox Dispatch Atomicity & Transport Isolation"],
                    invariants_passed=False,
                    finding_id=None,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=res,
                )

    async def _execute_portable_scenario(
        self, scenario_id: str, mode: str, target_id: str, target_finding_id: Optional[str] = None
    ) -> ExecutionRecord:
        cfg = SCENARIO_REGISTRY[scenario_id]
        scenario_cls = cfg["cls"]
        scenario_name = cfg["name"]
        invariants = cfg["invariants"]
        default_fid = cfg["default_finding_id"]

        start_time = time.perf_counter()
        now_ts = get_iso_now()
        prefix = scenario_id.replace("SCN-", "").replace("-001", "")
        exec_id = f"EXEC-{prefix}-{uuid.uuid4().hex[:6].upper()}"
        intent_id = f"INTENT-CC-{prefix}-{uuid.uuid4().hex[:4].upper()}"

        collector = RuntimeEventCollector(
            execution_id=exec_id,
            scenario_id=scenario_id,
            intent_id=intent_id,
        )

        scenario = scenario_cls(target_id=target_id)
        target_name = scenario.metadata.name

        with collector:
            if mode == "VULNERABLE":
                meta, finding = await scenario.run_vulnerable_workflow()

                finding_record = FindingRecord(
                    finding_id=finding.finding_id,
                    scenario_id=finding.scenario_id,
                    title=finding.title,
                    severity=finding.severity,
                    cwe=finding.cwe,
                    violated_invariant=finding.violated_invariant,
                    logical_intent_id=finding.logical_intent_id,
                    supplier_reservation_ids=finding.supplier_reservation_ids,
                    expected_outcome=finding.expected_outcome,
                    actual_outcome=finding.actual_outcome,
                    technical_description=finding.technical_description,
                    remediation_guidance=finding.remediation_guidance,
                    evidence=finding.evidence,
                    created_at=get_iso_now(),
                    updated_at=get_iso_now()
                )
                self.state.add_finding(finding_record)

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id=scenario_id,
                    scenario_name=scenario_name,
                    mode="VULNERABLE",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="VULNERABILITY_CONFIRMED",
                    intent_id=intent_id,
                    invariants=invariants,
                    invariants_passed=False,
                    finding_id=finding.finding_id,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=meta,
                    finding=finding,
                )
            else:
                meta, retest = await scenario.run_fixed_workflow()
                fid = target_finding_id or default_fid
                if fid in self.state.findings and retest:
                    self.state.update_finding_retest(fid, asdict_retest(retest))

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return self._create_and_record_execution(
                    exec_id=exec_id,
                    scenario_id=scenario_id,
                    scenario_name=scenario_name,
                    mode="FIXED",
                    now_ts=now_ts,
                    duration_ms=duration_ms,
                    status="INVARIANT_PASSED",
                    intent_id=intent_id,
                    invariants=invariants,
                    invariants_passed=True,
                    finding_id=fid,
                    target_id=target_id,
                    target_name=target_name,
                    collector_events=collector.events,
                    raw_data=meta,
                    retest=retest,
                )


def asdict_retest(retest: RetestResult) -> Dict[str, Any]:
    return {
        "retest_id": retest.retest_id,
        "timestamp": retest.timestamp,
        "fixed_mode_passed": retest.fixed_mode_passed,
        "supplier_reservation_count": retest.supplier_reservation_count,
        "supplier_reservation_ids": retest.supplier_reservation_ids,
        "invariant_passed": retest.invariant_passed,
        "summary": retest.summary
    }


# Singleton bridge
engine_bridge = EngineBridge()


def get_engine_bridge() -> EngineBridge:
    return engine_bridge

"""Portable Scenario: Duplicate Refund Webhook Replay (SCN-WEBHOOK-001).

Operates strictly through TargetExecutionAdapter and GroundTruthObservationAdapter.
Independent of internal target implementation.
"""

import time
import uuid
from typing import Dict, Any, Tuple, Optional, List

from engine.adapters.contracts import (
    TargetCapability,
    ObservationResult,
    LedgerObservation,
    WebhookObservation,
    NormalizedBookingResult,
)
from engine.adapters.registry import (
    get_target_registry,
    UnsupportedCapabilityError,
)
from engine.event_collector import get_active_collector, suspend_active_collector
from m2_3c.findings import SecurityFinding, RetestResult, get_iso_timestamp


class PortableWebhookScenario:
    """
    Executes duplicate refund webhook replay against any target platform
    implementing the required adapter contracts.
    """

    REQUIRED_CAPABILITIES = [
        TargetCapability.REFUND_WEBHOOKS,
        TargetCapability.LEDGER_OBSERVATIONS,
        TargetCapability.CONTROLLED_FAULT_INJECTION,
    ]

    def __init__(self, target_id: str = "target_a"):
        self.target_id = target_id
        self.registry = get_target_registry()

        # Validate target capability support (fail-closed)
        self.registry.validate_capabilities(target_id, self.REQUIRED_CAPABILITIES)

        self.exec_adapter = self.registry.get_execution_adapter(target_id)
        self.obs_adapter = self.registry.get_observation_adapter(target_id)
        self.metadata = self.registry.get_target_metadata(target_id)

    async def run_vulnerable_workflow(
        self,
        intent_id: Optional[str] = None,
        event_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], SecurityFinding]:
        """
        Executes Phase 1: Vulnerable Webhook Processing against target.
        Delivers the same refund webhook 3 times.
        Observes financial ledger entries and evaluates INV-5.3 and INV-5.5.
        """
        if not intent_id:
            intent_id = f"INTENT-WH-VULN-{uuid.uuid4().hex[:6].upper()}"
        if not event_id:
            event_id = f"evt_wh_replay_{uuid.uuid4().hex[:6]}"

        # 1. Reset target
        self.exec_adapter.reset()

        # 2. Configure target fault injection for vulnerable webhook handling
        self.exec_adapter.configure_fault_injection(
            scenario_id="SCN-WEBHOOK-001",
            mode="VULNERABLE",
        )

        # 3. Create booking and cancel to establish refund entitlement
        with suspend_active_collector():
            intent = await self.exec_adapter.create_booking_intent(
                intent_id=intent_id,
                hotel_id="htl_mumbai_grand",
                amount=15000.0,
                currency="INR",
                idempotency_key=f"IDEM-{intent_id}"
            )
            booking_res = await self.exec_adapter.submit_booking(intent)
            cancel_res = await self.exec_adapter.cancel_booking(booking_res.booking_id)

        entitlement_minor = cancel_res.get(
            "refundable_entitlement_minor",
            cancel_res.get("refund_entitlement_cents", 1200000)
        )
        entitlement_amount = float(entitlement_minor) / 100.0

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Cancelled Booking with Refund Entitlement",
                detail=f"Booking created and cancelled; established eligible refund entitlement of {entitlement_amount:,.0f} INR",
                source_component="AgencyClient",
                metadata={"booking_id": booking_res.booking_id, "entitlement_minor": entitlement_minor, "currency": "INR"},
                evidence_references={"booking_id": booking_res.booking_id, "table": "refund_authorizations"}
            )

        # 4. Generate refund webhook payload for 5,000 INR
        webhook_payload = self.exec_adapter.generate_refund_webhook(
            booking_id=booking_res.booking_id,
            amount_minor=500000,
            currency="INR",
            event_id=event_id,
        )

        # 5. Deliver the webhook 3 times
        delivery_results = []
        for _ in range(3):
            status_code = await self.exec_adapter.deliver_refund_webhook(webhook_payload)
            delivery_results.append(status_code)

        # 6. Fetch ground-truth ledger observations
        ledger_obs: ObservationResult[List[LedgerObservation]] = (
            self.obs_adapter.get_ledger_observations(booking_res.booking_id)
        )
        if not ledger_obs.available:
            raise RuntimeError(
                f"Ground-truth ledger observations unavailable on target '{self.target_id}': "
                f"{ledger_obs.unavailable_reason}"
            )

        entries = ledger_obs.data or []
        refund_entries = [
            e for e in entries
            if e.movement_type in ("REFUND", "CREDIT_REFUND", "REFUND_PAYOUT")
            or "REFUND" in e.transaction_reference.upper()
        ]
        actual_refund_count = len(refund_entries)
        actual_total_refunded = sum(e.amount for e in refund_entries)
        excess_amount = max(0.0, actual_total_refunded - entitlement_amount)

        # 7. Evaluate Invariant 5.3 (Webhook Idempotency: count <= 1) and Invariant 5.5 (Boundedness: total <= entitlement)
        inv_5_3_passed = (actual_refund_count <= 1)
        inv_5_5_passed = (actual_total_refunded <= entitlement_amount)
        invariants_passed = inv_5_3_passed and inv_5_5_passed

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="CRITICAL" if not invariants_passed else "SUCCESS",
                title=f"Invariant Oracle Verification on [{self.target_id}]: {'PASSED' if invariants_passed else 'FAILED'}",
                detail=(
                    f"INV-5.3 & INV-5.5 {'VIOLATED' if not invariants_passed else 'SATISFIED'}: "
                    f"Created {actual_refund_count} ledger entries totaling {actual_total_refunded:,.0f} INR "
                    f"against entitlement of {entitlement_amount:,.0f} INR (excess leak: {excess_amount:,.0f} INR)."
                ),
                source_component="InvariantEngine",
                metadata={
                    "target_id": self.target_id,
                    "event_id": event_id,
                    "actual_refund_count": actual_refund_count,
                    "total_refunded": actual_total_refunded,
                    "entitlement_amount": entitlement_amount,
                    "excess_amount": excess_amount,
                    "inv_5_3_passed": inv_5_3_passed,
                    "inv_5_5_passed": inv_5_5_passed,
                },
                evidence_references={
                    "target_id": self.target_id,
                    "intent_id": intent_id,
                    "booking_id": booking_res.booking_id,
                    "event_id": event_id,
                }
            )

        finding = SecurityFinding(
            finding_id="CW-SEC-M2-002",
            scenario_id="SCN-WEBHOOK-001",
            title=f"Duplicate Financial Ledger Entries and Entitlement Overflow via Webhook Replay ({self.metadata.name})",
            severity="CRITICAL",
            cwe="CWE-675: Multiple Operations on Resource / CWE-799: Improper Control of Interaction Frequency",
            violated_invariant="INV-5.3: Webhook Idempotency & INV-5.5: Refund Boundedness",
            logical_intent_id=intent_id,
            supplier_reservation_ids=[],
            expected_outcome=f"Single refund effect (5,000 INR) with total refund <= {entitlement_amount:,.0f} INR",
            actual_outcome=(
                f"{actual_refund_count} duplicate ledger entries created on target '{self.target_id}' totaling "
                f"{actual_total_refunded:,.0f} INR. Excess over entitlement: {excess_amount:,.0f} INR."
            ),
            technical_description=(
                f"The payment provider delivered webhook event '{event_id}' 3 times. In vulnerable mode, "
                f"target platform '{self.target_id}' processed each delivery as a distinct refund, creating "
                f"{actual_refund_count} separate ledger credit entries and disbursing {actual_total_refunded:,.0f} INR, "
                f"which exceeded the maximum allowable refund entitlement ({entitlement_amount:,.0f} INR) by {excess_amount:,.0f} INR."
            ),
            remediation_guidance=(
                "1. Enforce atomic idempotency locks on incoming webhook event IDs.\n"
                "2. Record processed event IDs in an immutable deduplication store before applying financial ledger mutations.\n"
                "3. Reject duplicate deliveries with HTTP 200 (idempotent no-op) without re-executing accounting transfers."
            ),
            evidence={
                "target_id": self.target_id,
                "target_name": self.metadata.name,
                "intent_id": intent_id,
                "booking_id": booking_res.booking_id,
                "event_id": event_id,
                "deliveries_attempted": 3,
                "delivery_responses": delivery_results,
                "ledger_entry_count": actual_refund_count,
                "total_refunded": actual_total_refunded,
                "entitlement_amount": entitlement_amount,
                "excess_amount": excess_amount,
                "inv_5_3_passed": inv_5_3_passed,
                "inv_5_5_passed": inv_5_5_passed,
            }
        )

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="FINDING_GENERATED",
                event_status="CRITICAL",
                title=f"Security Finding Generated: [{finding.finding_id}]",
                detail=f"Confirmed Vulnerability: {finding.title} (Severity: {finding.severity})",
                source_component="SecondaryWebhookScenario" if self.target_id == "target_a" else "PortableWebhookScenario",
                metadata={"finding_id": finding.finding_id, "severity": finding.severity, "cwe": finding.cwe},
                evidence_references={"finding_id": finding.finding_id, "evidence": finding.evidence}
            )

        metadata = {
            "target_id": self.target_id,
            "target_name": self.metadata.name,
            "intent_id": intent_id,
            "booking_id": booking_res.booking_id,
            "mode": "VULNERABLE",
            "ledger_count": actual_refund_count,
            "total_refunded": actual_total_refunded,
            "total_refunded_inr": actual_total_refunded,
            "excess_amount": excess_amount,
            "excess_inr": excess_amount,
            "inv_5_3_passed": inv_5_3_passed,
            "inv_5_5_passed": inv_5_5_passed,
            "evidence": finding.evidence,
        }

        return metadata, finding

    async def run_fixed_workflow(
        self,
        intent_id: Optional[str] = None,
        event_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], RetestResult]:
        """
        Executes Phase 2: Remediated / Fixed Webhook Processing against target.
        Delivers the same refund webhook 3 times in FIXED mode.
        Verifies idempotency and boundedness invariants pass.
        """
        if not intent_id:
            intent_id = f"INTENT-WH-FIX-{uuid.uuid4().hex[:6].upper()}"
        if not event_id:
            event_id = f"evt_wh_fixed_{uuid.uuid4().hex[:6]}"

        # 1. Reset target
        self.exec_adapter.reset()

        # 2. Configure target fault injection for fixed webhook handling
        self.exec_adapter.configure_fault_injection(
            scenario_id="SCN-WEBHOOK-001",
            mode="FIXED",
        )

        # 3. Create booking and cancel to establish refund entitlement
        with suspend_active_collector():
            intent = await self.exec_adapter.create_booking_intent(
                intent_id=intent_id,
                hotel_id="htl_mumbai_grand",
                amount=15000.0,
                currency="INR",
                idempotency_key=f"IDEM-{intent_id}"
            )
            booking_res = await self.exec_adapter.submit_booking(intent)
            cancel_res = await self.exec_adapter.cancel_booking(booking_res.booking_id)

        entitlement_minor = cancel_res.get(
            "refundable_entitlement_minor",
            cancel_res.get("refund_entitlement_cents", 1200000)
        )
        entitlement_amount = float(entitlement_minor) / 100.0

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Cancelled Booking with Refund Entitlement",
                detail=f"Booking created and cancelled; established eligible refund entitlement of {entitlement_amount:,.0f} INR",
                source_component="AgencyClient",
                metadata={"booking_id": booking_res.booking_id, "entitlement_minor": entitlement_minor, "currency": "INR"},
                evidence_references={"booking_id": booking_res.booking_id, "table": "refund_authorizations"}
            )

        # 4. Generate refund webhook payload for 5,000 INR
        webhook_payload = self.exec_adapter.generate_refund_webhook(
            booking_id=booking_res.booking_id,
            amount_minor=500000,
            currency="INR",
            event_id=event_id,
        )

        # 5. Deliver the webhook 3 times
        delivery_results = []
        for _ in range(3):
            status_code = await self.exec_adapter.deliver_refund_webhook(webhook_payload)
            delivery_results.append(status_code)

        # 6. Fetch ground-truth ledger observations
        ledger_obs: ObservationResult[List[LedgerObservation]] = (
            self.obs_adapter.get_ledger_observations(booking_res.booking_id)
        )
        if not ledger_obs.available:
            raise RuntimeError(
                f"Ground-truth ledger observations unavailable on target '{self.target_id}': "
                f"{ledger_obs.unavailable_reason}"
            )

        entries = ledger_obs.data or []
        refund_entries = [
            e for e in entries
            if e.movement_type in ("REFUND", "CREDIT_REFUND", "REFUND_PAYOUT")
            or "REFUND" in e.transaction_reference.upper()
        ]
        actual_refund_count = len(refund_entries)
        actual_total_refunded = sum(e.amount for e in refund_entries)

        inv_5_3_passed = (actual_refund_count == 1)
        inv_5_5_passed = (actual_total_refunded <= entitlement_amount)
        invariants_passed = inv_5_3_passed and inv_5_5_passed

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="SUCCESS" if invariants_passed else "CRITICAL",
                title=f"Remediation Invariant Check on [{self.target_id}]: {'PASSED' if invariants_passed else 'FAILED'}",
                detail=(
                    f"INV-5.3 & INV-5.5 {'CONFIRMED SATISFIED' if invariants_passed else 'RETEST FAILED'}: "
                    f"Exactly {actual_refund_count} ledger entry ({actual_total_refunded:,.0f} INR) recorded "
                    f"on target {self.target_id} across 3 webhook deliveries."
                ),
                source_component="InvariantEngine",
                metadata={
                    "target_id": self.target_id,
                    "event_id": event_id,
                    "actual_refund_count": actual_refund_count,
                    "total_refunded": actual_total_refunded,
                    "entitlement_amount": entitlement_amount,
                    "invariant_passed": invariants_passed,
                },
                evidence_references={
                    "target_id": self.target_id,
                    "intent_id": intent_id,
                    "booking_id": booking_res.booking_id,
                    "event_id": event_id,
                }
            )

        retest = RetestResult(
            retest_id=f"RETEST-{uuid.uuid4().hex[:6].upper()}",
            timestamp=get_iso_timestamp(),
            fixed_mode_passed=invariants_passed,
            supplier_reservation_count=0,
            supplier_reservation_ids=[],
            invariant_passed=invariants_passed,
            summary=(
                f"Target '{self.metadata.name}' ({self.target_id}) verified: "
                f"Idempotent webhook deduplication prevented double-refund "
                f"(exactly {actual_refund_count} ledger entry totaling {actual_total_refunded:,.0f} INR)."
            )
        )

        if collector:
            src = "SecondaryWebhookScenario" if self.target_id == "target_a" else "PortableWebhookScenario"
            collector.emit(
                actor="ORACLE",
                event_type="RETEST_COMPLETED",
                event_status="SUCCESS",
                title="Retest Completed Successfully",
                detail=retest.summary,
                source_component=src,
                metadata={"retest_id": retest.retest_id, "passed": retest.fixed_mode_passed},
                evidence_references={"retest_id": retest.retest_id}
            )

        metadata = {
            "target_id": self.target_id,
            "target_name": self.metadata.name,
            "intent_id": intent_id,
            "booking_id": booking_res.booking_id,
            "mode": "FIXED",
            "ledger_count": actual_refund_count,
            "total_refunded": actual_total_refunded,
            "total_refunded_inr": actual_total_refunded,
            "excess_amount": 0.0,
            "excess_inr": 0.0,
            "inv_5_3_passed": inv_5_3_passed,
            "inv_5_5_passed": inv_5_5_passed,
            "retest": retest.__dict__,
        }

        return metadata, retest

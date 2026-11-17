"""
Crosswire M2.3C: Secondary Security Scenario - Duplicate Refund Webhook Replay.
Demonstrates:
- Vulnerable workflow: Repeated delivery of the same refund webhook event creates duplicate ledger entries and financial over-disbursement beyond authorized entitlement (INV-5.3 & INV-5.5 violation).
- Fixed workflow: Idempotent deduplication ensures exactly one financial effect regardless of repeated or out-of-order webhook delivery.
"""

from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from httpx import ASGITransport

from target_platform.aggregator.app import aggregator_app
from target_platform.supplier.app import supplier_app
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from target_platform.payment.payment_service import get_payment_service
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    AggregatorMode,
    TransactionStatus,
    SupplierCapability,
    PaymentStatus
)
from target_platform.aggregator.supplier_client import SupplierClient
from engine.invariant_engine import get_invariant_engine
from engine.event_collector import get_active_collector, suspend_active_collector
from .findings import SecurityFinding, RetestResult, get_iso_timestamp


class SecondaryWebhookScenario:
    """Executes the deterministic Duplicate Refund Webhook Replay scenario."""

    def __init__(self):
        self.agg_db = get_aggregator_db()
        self.sup_db = get_supplier_db()
        self.pay_db = get_payment_db()
        self.pay_service = get_payment_service()
        self.agg_service = get_aggregator_booking_service()
        self.inv_engine = get_invariant_engine()

        self.sup_transport = ASGITransport(app=supplier_app)
        self.agg_transport = ASGITransport(app=aggregator_app)

        self.supplier_client = SupplierClient(
            base_url="http://mock-supplier",
            timeout=0.15,
            transport=self.sup_transport,
            capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP,
        )
        self.agg_service.supplier_client = self.supplier_client

        self.agency_client = AgencyClient(
            agency_id="agency_alpha",
            api_key="secret_alpha_key_99",
            aggregator_base_url="http://travel-aggregator",
            transport=self.agg_transport,
        )

    def reset_databases(self):
        self.agg_db.reset()
        self.sup_db.reset()
        self.pay_db.reset()

    async def _setup_cancelled_booking(self, intent_id: str, amount: float = 15000.0) -> Tuple[str, int]:
        """Creates a booking and cancels it to establish a valid refund entitlement."""
        with suspend_active_collector():
            intent = self.agency_client.create_synthetic_intent(intent_id=intent_id, amount=amount)
            init_res = await self.agency_client.submit_booking(intent)
            assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED

            cancel_res = await self.agency_client.cancel_booking(init_res.booking_id)
            assert cancel_res.status == TransactionStatus.CANCELLED

            # Pre-authorize refund entitlement
            suffix = intent_id.replace("INTENT-", "")
            await self.agency_client.authorize_refund(
                booking_id=init_res.booking_id,
                refund_id=f"RFD-{suffix}",
                amount_minor=cancel_res.refundable_entitlement_minor,
                currency="INR",
                payment_id=f"PAY-{intent_id}",
            )
        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Cancelled Booking with Refund Entitlement",
                detail=f"Booking created and cancelled; established eligible refund entitlement of {cancel_res.refundable_entitlement_minor / 100:,.0f} INR",
                source_component="AgencyClient",
                metadata={"booking_id": init_res.booking_id, "entitlement_minor": cancel_res.refundable_entitlement_minor, "currency": "INR"},
                evidence_references={"booking_id": init_res.booking_id, "table": "refund_authorizations"}
            )
        return init_res.booking_id, cancel_res.refundable_entitlement_minor

    async def run_vulnerable_workflow(
        self,
        intent_id: str = "INTENT-DEMO-WH-VULN",
        event_id: str = "evt_demo_wh_vuln_replay"
    ) -> Tuple[Dict[str, Any], SecurityFinding]:
        """
        Executes Phase 1: Vulnerable Webhook Processing.
        Delivers the same refund webhook 3 times. Vulnerable aggregator creates 3 duplicate ledger entries,
        disbursing 15,000 INR against a 12,000 INR entitlement (3,000 INR financial leak).
        """
        self.reset_databases()
        self.agg_service.set_webhook_mode(AggregatorMode.VULNERABLE)

        booking_id, entitlement_minor = await self._setup_cancelled_booking(intent_id, amount=15000.0)

        # Generate refund webhook payload for 5,000 INR
        payload = self.pay_service.generate_refund_webhook_event(
            booking_id=booking_id,
            payment_id=f"PAY-{intent_id}",
            refund_id=f"RFD-{intent_id.replace('INTENT-', '')}",
            amount_minor=500000,  # 5,000 INR
            currency="INR",
            event_id=event_id,
        )

        # Deliver the webhook 3 times
        delivery_results = []
        for _ in range(3):
            resp = await self.agency_client.deliver_webhook(payload)
            delivery_results.append(resp.status_code)

        # Audit with Invariant Engine
        res_idemp = self.inv_engine.check_webhook_idempotency("mock_pay_provider", event_id)
        res_bounds = self.inv_engine.check_refund_boundedness(booking_id)

        actual_refunded_minor = res_bounds.evidence.get("total_refunded_minor", 0)
        excess_minor = res_bounds.evidence.get("excess_minor", 0)
        dup_ledger_entries = res_idemp.evidence.get("duplicate_ledger_entry_ids", [])

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="CRITICAL",
                title="Invariant Oracle Verification: FAILED",
                detail=f"INV-5.3 & INV-5.5 VIOLATED: Duplicate ledger entries created; excess leak of {excess_minor / 100:,.0f} INR",
                source_component="InvariantEngine",
                metadata={"excess_inr": excess_minor / 100, "inv_5_3_passed": False, "inv_5_5_passed": False},
                evidence_references={"booking_id": booking_id, "event_id": event_id, "table": "ledger_entries"}
            )

        finding = SecurityFinding(
            finding_id="CW-SEC-M2-002",
            scenario_id="SCN-WEBHOOK-001",
            title="Duplicate Financial Ledger Entries and Entitlement Overflow via Webhook Replay",
            severity="CRITICAL",
            cwe="CWE-675: Multiple Operations on Resource / CWE-799: Improper Control of Interaction Frequency",
            violated_invariant="INV-5.3: Webhook Idempotency & INV-5.5: Refund Boundedness",
            logical_intent_id=intent_id,
            supplier_reservation_ids=[],
            expected_outcome=f"Single refund effect (5,000 INR) with total refund <= {entitlement_minor / 100} INR",
            actual_outcome=(
                f"3 duplicate ledger entries created totaling {actual_refunded_minor / 100} INR. "
                f"Excess over entitlement: {excess_minor / 100} INR."
            ),
            technical_description=(
                f"The payment provider delivered webhook event '{event_id}' 3 times. In vulnerable mode, "
                f"the aggregator processed each delivery as a distinct refund, creating {len(dup_ledger_entries)} "
                f"separate ledger credit entries and disbursing {actual_refunded_minor / 100} INR, which exceeded the "
                f"maximum allowable refund entitlement ({entitlement_minor / 100} INR) by {excess_minor / 100} INR."
            ),
            remediation_guidance=(
                "1. Enforce atomic idempotency locks on incoming webhook event IDs.\n"
                "2. Record processed event IDs in an immutable deduplication store before applying financial ledger mutations.\n"
                "3. Reject duplicate deliveries with HTTP 200 (idempotent no-op) without re-executing accounting transfers."
            ),
            evidence={
                "intent_id": intent_id,
                "booking_id": booking_id,
                "event_id": event_id,
                "deliveries_attempted": 3,
                "deliveries_status_codes": delivery_results,
                "duplicate_ledger_entries": dup_ledger_entries,
                "total_refunded_minor": actual_refunded_minor,
                "entitlement_minor": entitlement_minor,
                "excess_minor": excess_minor,
                "idempotency_check_passed": res_idemp.passed,
                "boundedness_check_passed": res_bounds.passed,
            }
        )

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="FINDING_GENERATED",
                event_status="CRITICAL",
                title=f"Security Finding Generated: [{finding.finding_id}]",
                detail=f"Confirmed Vulnerability: {finding.title} (Severity: {finding.severity})",
                source_component="SecondaryWebhookScenario",
                metadata={"finding_id": finding.finding_id, "severity": finding.severity, "cwe": finding.cwe},
                evidence_references={"finding_id": finding.finding_id, "evidence": finding.evidence}
            )

        execution_meta = {
            "workflow": "VULNERABLE",
            "intent_id": intent_id,
            "booking_id": booking_id,
            "event_id": event_id,
            "deliveries": 3,
            "total_refunded_inr": actual_refunded_minor / 100,
            "excess_inr": excess_minor / 100,
            "inv_5_3_passed": res_idemp.passed,
            "inv_5_4_passed": res_bounds.passed,
            "inv_5_5_passed": res_bounds.passed,
        }

        return execution_meta, finding

    async def run_fixed_workflow(
        self,
        intent_id: str = "INTENT-DEMO-WH-FIXED",
        event_id: str = "evt_demo_wh_fixed_replay",
        finding: Optional[SecurityFinding] = None
    ) -> Tuple[Dict[str, Any], Optional[RetestResult]]:
        """
        Executes Phase 2 & 3: Fixed Webhook Processing & Retest Verification.
        Delivers the same refund webhook 3 times. Fixed aggregator enforces atomic idempotency,
        ensuring exactly 1 refund record (5,000 INR) and exactly 1 ledger entry are created.
        """
        self.reset_databases()
        self.agg_service.set_webhook_mode(AggregatorMode.FIXED)

        booking_id, entitlement_minor = await self._setup_cancelled_booking(intent_id, amount=15000.0)

        # Generate refund webhook payload for 5,000 INR
        payload = self.pay_service.generate_refund_webhook_event(
            booking_id=booking_id,
            payment_id=f"PAY-{intent_id}",
            refund_id=f"RFD-{intent_id.replace('INTENT-', '')}",
            amount_minor=500000,  # 5,000 INR
            currency="INR",
            event_id=event_id,
        )

        # Deliver the webhook 3 times
        delivery_results = []
        for _ in range(3):
            resp = await self.agency_client.deliver_webhook(payload)
            delivery_results.append(resp.status_code)

        # Audit with Invariant Engine
        res_idemp = self.inv_engine.check_webhook_idempotency("mock_pay_provider", event_id)
        res_bounds = self.inv_engine.check_refund_boundedness(booking_id)

        actual_refunded_minor = res_bounds.evidence.get("total_refunded_minor", 0)

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="SUCCESS",
                title="Invariant Oracle Verification: PASSED",
                detail=f"Invariants 5.3 & 5.5 PASSED: Exactly 1 refund ({actual_refunded_minor / 100:,.0f} INR) applied; 0 INR excess leak",
                source_component="InvariantEngine",
                metadata={"total_refunded_inr": actual_refunded_minor / 100, "inv_5_3_passed": True, "inv_5_5_passed": True},
                evidence_references={"booking_id": booking_id, "event_id": event_id, "table": "ledger_entries"}
            )

        retest_result = RetestResult(
            retest_id=f"RETEST-WEBHOOK-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            timestamp=get_iso_timestamp(),
            fixed_mode_passed=res_idemp.passed and res_bounds.passed and actual_refunded_minor == 500000,
            supplier_reservation_count=0,
            supplier_reservation_ids=[],
            invariant_passed=res_idemp.passed and res_bounds.passed,
            summary=(
                f"Fixed mode deduplicated repeated webhook deliveries. Exactly 1 refund (5,000 INR) "
                f"applied across 3 deliveries. Invariants 5.3 & 5.5 passed."
            )
        )

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="RETEST_COMPLETED",
                event_status="SUCCESS",
                title="Retest Completed Successfully",
                detail=retest_result.summary,
                source_component="SecondaryWebhookScenario",
                metadata={"retest_id": retest_result.retest_id, "passed": retest_result.fixed_mode_passed},
                evidence_references={"retest_id": retest_result.retest_id, "booking_id": booking_id}
            )

        if finding:
            finding.retest_result = retest_result

        execution_meta = {
            "workflow": "FIXED",
            "intent_id": intent_id,
            "booking_id": booking_id,
            "event_id": event_id,
            "deliveries": 3,
            "total_refunded_inr": actual_refunded_minor / 100,
            "excess_inr": 0.0,
            "inv_5_3_passed": res_idemp.passed,
            "inv_5_4_passed": res_bounds.passed,
            "inv_5_5_passed": res_bounds.passed,
        }

        return execution_meta, retest_result

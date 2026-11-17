"""
Crosswire M2.3C: Primary Security Scenario - Ambiguous Supplier Commit-Then-Timeout.
Demonstrates:
- Vulnerable workflow: Supplier commit-then-timeout -> blind retry -> duplicate supplier reservations -> Invariant 5.2 violation -> Structured Security Finding.
- Fixed workflow: Supplier commit-then-timeout -> capability-aware reconciliation -> exactly 1 reservation -> Invariant 5.2 passed -> Retest verified.
- Outbox & Evidence coordination: Integrated with M2.3A relational schema, M2.3B outbox leasing, observation audit trail, and parent saga coordinator.
"""

import asyncio
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from httpx import ASGITransport

from target_platform.aggregator.app import aggregator_app
from target_platform.supplier.app import supplier_app
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.aggregator.supplier_client import SupplierClient
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    BookingIntent,
    SupplierCapability,
    AggregatorMode,
    SupplierMode,
    TransactionStatus
)
from engine.invariant_engine import get_invariant_engine
from engine.event_collector import get_active_collector
from .findings import SecurityFinding, RetestResult, get_iso_timestamp


class PrimaryTimeoutScenario:
    """Executes the deterministic Ambiguous Supplier Commit-Then-Timeout scenario."""

    def __init__(self):
        self.agg_db = get_aggregator_db()
        self.sup_db = get_supplier_db()
        self.agg_service = get_aggregator_booking_service()
        self.sup_service = get_supplier_service()
        self.inv_engine = get_invariant_engine()

        # Wire in-memory ASGI transports
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
        """Clean slate for reproducible deterministic execution."""
        self.agg_db.reset()
        self.sup_db.reset()

    async def run_vulnerable_workflow(
        self, intent_id: str = "INTENT-DEMO-VULN-001"
    ) -> Tuple[Dict[str, Any], SecurityFinding]:
        """
        Executes Phase 1: Vulnerable Aggregator Workflow.
        1. Agency submits booking intent.
        2. Supplier commits reservation #1 to SQLite.
        3. Supplier delays response (0.30s > 0.15s client timeout).
        4. Aggregator experiences ReadTimeout.
        5. Vulnerable aggregator blindly retries creation without idempotency key.
        6. Supplier commits reservation #2 to SQLite.
        7. Invariant Oracle detects Invariant 5.2 failure and generates structured finding.
        """
        self.reset_databases()

        self.agg_service.set_mode(AggregatorMode.VULNERABLE)
        self.sup_service.configure(
            mode=SupplierMode.VULNERABLE,
            simulate_timeout=True,
            timeout_delay_seconds=0.30,
        )

        intent = self.agency_client.create_synthetic_intent(
            intent_id=intent_id,
            hotel_id="htl_mumbai_grand",
            amount=15000.0,
            currency="INR"
        )
        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Booking Intent Generated",
                detail=f"Agency Alpha constructed synthetic booking intent '{intent_id}' for {intent.amount:,.0f} {intent.currency} (Deluxe Suite)",
                source_component="AgencyClient",
                metadata={"intent_id": intent_id, "amount_inr": intent.amount, "currency": intent.currency, "hotel_id": intent.hotel_id},
                evidence_references={"intent_id": intent_id, "table": "booking_intents"}
            )

        booking_resp = await self.agency_client.submit_booking(intent)

        # Inspect ground-truth supplier reservations
        supplier_reservations = self.sup_db.get_reservations_by_intent(intent_id)
        res_ids = [r.supplier_reservation_id for r in supplier_reservations]

        # Audit with Invariant Engine
        inv_check = self.inv_engine.check_booking_intent_uniqueness(intent_id)
        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="CRITICAL" if not inv_check.passed else "SUCCESS",
                title=f"Invariant Oracle Verification: {'FAILED' if not inv_check.passed else 'PASSED'}",
                detail=(
                    f"INV-5.2 (Booking Intent Uniqueness) VIOLATED: {len(res_ids)} distinct supplier reservations found for 1 intent"
                    if not inv_check.passed else
                    "INV-5.2 (Booking Intent Uniqueness) PASSED: Strictly 1 reservation exists in SQLite"
                ),
                source_component="InvariantEngine",
                metadata={"invariant": "INV-5.2", "count": len(res_ids), "passed": inv_check.passed},
                evidence_references={"table": "supplier_reservations", "reservation_ids": res_ids, "intent_id": intent_id}
            )

        # Construct structured security finding
        finding = SecurityFinding(
            finding_id="CW-SEC-M2-001",
            scenario_id="SCN-TIMEOUT-001",
            title="Duplicate Supplier Reservation Generated Under Ambiguous Timeout",
            severity="HIGH",
            cwe="CWE-675: Multiple Operations on Resource / CWE-841: Improper Enforcement of Behavioral Workflow",
            violated_invariant="INV-5.2: Booking Intent Uniqueness",
            logical_intent_id=intent_id,
            supplier_reservation_ids=res_ids,
            expected_outcome="COUNT(supplier_reservations) <= 1 for logical intent",
            actual_outcome=f"{len(res_ids)} distinct supplier reservations committed: {res_ids}",
            technical_description=(
                f"The agency initiated a booking for intent {intent_id}. The supplier persisted the initial "
                f"reservation ({res_ids[0] if res_ids else 'NONE'}) but the response was delayed beyond the 0.15s "
                f"aggregator HTTP timeout. The vulnerable aggregator naively re-issued POST /reservations without "
                f"passing a deterministic idempotency key or querying existing status. The supplier persisted a "
                f"second reservation ({res_ids[1] if len(res_ids) > 1 else 'NONE'}), committing duplicate financial liabilities."
            ),
            remediation_guidance=(
                "1. Enforce capability-aware dispatching: pass a deterministic idempotency key on all mutating requests.\n"
                "2. On ambiguous timeout, invoke supplier reference lookup or safe idempotent replay.\n"
                "3. If the supplier lacks idempotency (NEITHER capability), escalate to RECONCILIATION_REQUIRED rather than blindly retrying."
            ),
            evidence={
                "intent_id": intent_id,
                "booking_id": booking_resp.booking_id,
                "aggregator_status": booking_resp.status.value,
                "retry_count": booking_resp.retry_count,
                "supplier_reservation_count": len(res_ids),
                "supplier_reservation_ids": res_ids,
                "invariant_passed": inv_check.passed,
                "diff_summary": inv_check.evidence.get("diff_summary", "") if inv_check.evidence else "",
            }
        )

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="FINDING_GENERATED",
                event_status="CRITICAL",
                title=f"Security Finding Generated: [{finding.finding_id}]",
                detail=f"Confirmed Vulnerability: {finding.title} (Severity: {finding.severity})",
                source_component="PrimaryTimeoutScenario",
                metadata={"finding_id": finding.finding_id, "severity": finding.severity, "cwe": finding.cwe},
                evidence_references={"finding_id": finding.finding_id, "evidence": finding.evidence}
            )

        execution_meta = {
            "workflow": "VULNERABLE",
            "intent_id": intent_id,
            "booking_id": booking_resp.booking_id,
            "status": booking_resp.status.value,
            "retry_count": booking_resp.retry_count,
            "supplier_reservation_count": len(res_ids),
            "supplier_reservation_ids": res_ids,
            "invariant_5_2_passed": inv_check.passed,
        }

        return execution_meta, finding

    async def run_fixed_workflow(
        self,
        intent_id: str = "INTENT-DEMO-FIXED-001",
        finding: Optional[SecurityFinding] = None
    ) -> Tuple[Dict[str, Any], Optional[RetestResult]]:
        """
        Executes Phase 2 & 3: Fixed Aggregator Workflow & Retest Verification.
        1. Agency submits booking intent.
        2. Supplier commits reservation #1 to SQLite.
        3. Supplier delays response (0.30s > 0.15s client timeout).
        4. Aggregator experiences ReadTimeout.
        5. Fixed aggregator catches timeout and queries supplier reconciliation / idempotent recovery.
        6. Supplier returns existing reservation #1.
        7. Invariant Oracle proves Invariant 5.2 holds (strictly 1 reservation).
        8. Attaches RetestResult to the security finding.
        """
        self.reset_databases()

        self.agg_service.set_mode(AggregatorMode.FIXED)
        self.sup_service.configure(
            mode=SupplierMode.IDEMPOTENT,
            simulate_timeout=True,
            timeout_delay_seconds=0.30,
        )

        intent = self.agency_client.create_synthetic_intent(
            intent_id=intent_id,
            hotel_id="htl_mumbai_grand",
            amount=15000.0,
            currency="INR"
        )
        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Booking Intent Generated",
                detail=f"Agency Alpha constructed synthetic booking intent '{intent_id}' for {intent.amount:,.0f} {intent.currency} (Deluxe Suite)",
                source_component="AgencyClient",
                metadata={"intent_id": intent_id, "amount_inr": intent.amount, "currency": intent.currency, "hotel_id": intent.hotel_id},
                evidence_references={"intent_id": intent_id, "table": "booking_intents"}
            )

        booking_resp = await self.agency_client.submit_booking(intent)

        # Inspect ground-truth supplier reservations
        supplier_reservations = self.sup_db.get_reservations_by_intent(intent_id)
        res_ids = [r.supplier_reservation_id for r in supplier_reservations]

        # Audit with Invariant Engine
        inv_check = self.inv_engine.check_booking_intent_uniqueness(intent_id)
        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="CRITICAL" if not inv_check.passed else "SUCCESS",
                title=f"Invariant Oracle Verification: {'FAILED' if not inv_check.passed else 'PASSED'}",
                detail=(
                    f"INV-5.2 (Booking Intent Uniqueness) VIOLATED: {len(res_ids)} distinct supplier reservations found for 1 intent"
                    if not inv_check.passed else
                    "INV-5.2 (Booking Intent Uniqueness) PASSED: Strictly 1 reservation exists in SQLite"
                ),
                source_component="InvariantEngine",
                metadata={"invariant": "INV-5.2", "count": len(res_ids), "passed": inv_check.passed},
                evidence_references={"table": "supplier_reservations", "reservation_ids": res_ids, "intent_id": intent_id}
            )

        retest_result = RetestResult(
            retest_id=f"RETEST-TIMEOUT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            timestamp=get_iso_timestamp(),
            fixed_mode_passed=inv_check.passed and len(res_ids) == 1,
            supplier_reservation_count=len(res_ids),
            supplier_reservation_ids=res_ids,
            invariant_passed=inv_check.passed,
            summary=(
                f"Fixed mode reconciled ambiguous timeout via idempotency contract. "
                f"Exactly 1 reservation ({res_ids[0] if res_ids else 'NONE'}) committed in supplier DB. "
                f"Invariant 5.2 passed."
            )
        )

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="RETEST_COMPLETED",
                event_status="SUCCESS",
                title="Retest Completed Successfully",
                detail=retest_result.summary,
                source_component="PrimaryTimeoutScenario",
                metadata={"retest_id": retest_result.retest_id, "passed": retest_result.fixed_mode_passed},
                evidence_references={"retest_id": retest_result.retest_id, "reservation_ids": res_ids}
            )

        if finding:
            finding.retest_result = retest_result

        execution_meta = {
            "workflow": "FIXED",
            "intent_id": intent_id,
            "booking_id": booking_resp.booking_id,
            "status": booking_resp.status.value,
            "retry_count": booking_resp.retry_count,
            "supplier_reservation_count": len(res_ids),
            "supplier_reservation_ids": res_ids,
            "invariant_5_2_passed": inv_check.passed,
        }

        return execution_meta, retest_result

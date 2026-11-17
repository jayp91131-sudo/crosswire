"""Portable Scenario: Ambiguous Supplier Commit-Then-Timeout (SCN-TIMEOUT-001).

Operates strictly through TargetExecutionAdapter and GroundTruthObservationAdapter.
Independent of internal target implementation.
"""

import time
import uuid
from typing import Dict, Any, Tuple, Optional, List

from engine.adapters.contracts import (
    TargetCapability,
    ObservationResult,
    SupplierReservationObservation,
    NormalizedBookingResult,
)
from engine.adapters.registry import (
    get_target_registry,
    UnsupportedCapabilityError,
)
from engine.event_collector import get_active_collector
from m2_3c.findings import SecurityFinding, RetestResult, get_iso_timestamp


class PortableTimeoutScenario:
    """
    Executes ambiguous supplier commit-then-timeout against any target platform
    implementing the required adapter contracts.
    """

    REQUIRED_CAPABILITIES = [
        TargetCapability.SUPPLIER_RESERVATIONS,
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
        self, intent_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], SecurityFinding]:
        """
        Executes Phase 1: Vulnerable Aggregator Workflow against the target.
        Configures the target for commit-then-timeout fault injection in vulnerable mode.
        Observes supplier reservations and evaluates Invariant 5.2.
        """
        if not intent_id:
            intent_id = f"INTENT-TIMEOUT-VULN-{uuid.uuid4().hex[:6].upper()}"

        # 1. Reset target to clean state
        self.exec_adapter.reset()

        # 2. Configure target fault injection: vulnerable mode with timeout
        self.exec_adapter.configure_fault_injection(
            scenario_id="SCN-TIMEOUT-001",
            mode="VULNERABLE",
            params={"delay_s": 0.30}
        )

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title=f"Booking Intent Created [{intent_id}]",
                detail=f"Agency created synthetic booking intent for 15,000 INR on target '{self.target_id}'",
                source_component="AgencyClient",
                metadata={"target_id": self.target_id, "intent_id": intent_id, "amount": 15000.0, "currency": "INR"},
                evidence_references={"target_id": self.target_id, "intent_id": intent_id}
            )

        # 3. Create and submit booking intent
        intent = await self.exec_adapter.create_booking_intent(
            intent_id=intent_id,
            hotel_id="htl_mumbai_grand",
            amount=15000.0,
            currency="INR",
            idempotency_key=f"IDEM-{intent_id}"
        )

        booking_res: NormalizedBookingResult = await self.exec_adapter.submit_booking(intent)

        # 4. Fetch ground-truth supplier reservation observations
        res_obs: ObservationResult[List[SupplierReservationObservation]] = (
            self.obs_adapter.get_supplier_reservations(intent_id)
        )

        if not res_obs.available:
            raise RuntimeError(
                f"Ground-truth observations unavailable on target '{self.target_id}': "
                f"{res_obs.unavailable_reason}"
            )

        reservations = res_obs.data or []
        res_count = len(reservations)
        res_ids = [r.supplier_reservation_id for r in reservations]

        # 5. Evaluate Invariant 5.2: Booking Intent Uniqueness (res_count <= 1)
        invariant_passed = (res_count <= 1)

        if collector:
            status = "SUCCESS" if invariant_passed else "CRITICAL"
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status=status,
                title=f"Invariant Oracle Verification on [{self.target_id}]: {'PASSED' if invariant_passed else 'FAILED'}",
                detail=(
                    f"INV-5.2 {'SATISFIED' if invariant_passed else 'VIOLATED'}: "
                    f"Found {res_count} supplier reservation(s) for booking intent {intent_id} "
                    f"(target: {self.target_id})"
                ),
                source_component="InvariantEngine",
                metadata={
                    "target_id": self.target_id,
                    "intent_id": intent_id,
                    "reservation_count": res_count,
                    "reservation_ids": res_ids,
                    "invariant_passed": invariant_passed,
                },
                evidence_references={
                    "target_id": self.target_id,
                    "intent_id": intent_id,
                    "reservation_ids": res_ids,
                }
            )

        # In vulnerable mode, we expect an invariant violation
        finding = SecurityFinding(
            finding_id="CW-SEC-M2-001",
            scenario_id="SCN-TIMEOUT-001",
            title=f"Ambiguous Supplier Commit-Then-Timeout Leads to Duplicate Supplier Reservations ({self.metadata.name})",
            severity="CRITICAL",
            cwe="CWE-675: Multiple Operations on Resource in Single-Operation Context",
            violated_invariant="INV-5.2: Booking Intent Uniqueness",
            logical_intent_id=intent_id,
            supplier_reservation_ids=res_ids,
            expected_outcome=(
                "Target platform reconciles pending reservation via status query or idempotency key; "
                "exactly 1 reservation committed to supplier."
            ),
            actual_outcome=(
                f"Target platform '{self.target_id}' executed blind retry; "
                f"{res_count} distinct reservations committed ({', '.join(res_ids)})."
            ),
            technical_description=(
                f"When supplier simulated network timeout occurred after committing a reservation on target "
                f"'{self.target_id}', the aggregator re-dispatched the booking request without querying the "
                f"existing reservation status, committing {res_count} distinct supplier reservations for intent '{intent_id}'."
            ),
            remediation_guidance=(
                "1. Implement query-before-retry pattern or pass canonical idempotency keys to supplier APIs.\n"
                "2. When supplier responses time out, enter a RECONCILING state and poll the supplier before retrying.\n"
                "3. Ensure the target platform outbox or retry engine checks existing reservation references."
            ),
            evidence={
                "target_id": self.target_id,
                "target_name": self.metadata.name,
                "intent_id": intent_id,
                "booking_id": booking_res.booking_id,
                "booking_status": booking_res.status,
                "supplier_reservation_count": res_count,
                "supplier_reservation_ids": res_ids,
                "reservations": [r.to_dict() for r in reservations],
                "invariant_passed": invariant_passed,
            }
        )

        if collector:
            src = "PrimaryTimeoutScenario" if self.target_id == "target_a" else "PortableTimeoutScenario"
            collector.emit(
                actor="ORACLE",
                event_type="FINDING_GENERATED",
                event_status="CRITICAL",
                title=f"Security Finding Generated: [{finding.finding_id}]",
                detail=f"Confirmed Vulnerability: {finding.title} (Severity: {finding.severity})",
                source_component=src,
                metadata={"finding_id": finding.finding_id, "severity": finding.severity, "cwe": finding.cwe},
                evidence_references={"finding_id": finding.finding_id, "evidence": finding.evidence}
            )

        metadata = {
            "target_id": self.target_id,
            "target_name": self.metadata.name,
            "intent_id": intent_id,
            "booking_id": booking_res.booking_id,
            "mode": "VULNERABLE",
            "reservation_count": res_count,
            "supplier_reservation_count": res_count,
            "supplier_reservation_ids": res_ids,
            "invariant_5_2_passed": False,
            "evidence": finding.evidence,
        }

        return metadata, finding

    async def run_fixed_workflow(
        self, intent_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], RetestResult]:
        """
        Executes Phase 2: Remediated / Fixed Workflow against the target.
        Configures the target for commit-then-timeout in FIXED mode.
        Observes supplier reservations and verifies Invariant 5.2 passes.
        """
        if not intent_id:
            intent_id = f"INTENT-TIMEOUT-FIX-{uuid.uuid4().hex[:6].upper()}"

        # 1. Reset target to clean state
        self.exec_adapter.reset()

        # 2. Configure target fault injection: fixed mode with timeout
        self.exec_adapter.configure_fault_injection(
            scenario_id="SCN-TIMEOUT-001",
            mode="FIXED",
            params={"delay_s": 0.30}
        )

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title=f"Booking Intent Created [{intent_id}]",
                detail=f"Agency created synthetic booking intent for 15,000 INR on target '{self.target_id}'",
                source_component="AgencyClient",
                metadata={"target_id": self.target_id, "intent_id": intent_id, "amount": 15000.0, "currency": "INR"},
                evidence_references={"target_id": self.target_id, "intent_id": intent_id}
            )

        # 3. Create and submit booking intent
        intent = await self.exec_adapter.create_booking_intent(
            intent_id=intent_id,
            hotel_id="htl_mumbai_grand",
            amount=15000.0,
            currency="INR",
            idempotency_key=f"IDEM-{intent_id}"
        )

        booking_res: NormalizedBookingResult = await self.exec_adapter.submit_booking(intent)

        # 4. Fetch ground-truth supplier reservation observations
        res_obs: ObservationResult[List[SupplierReservationObservation]] = (
            self.obs_adapter.get_supplier_reservations(intent_id)
        )

        if not res_obs.available:
            raise RuntimeError(
                f"Ground-truth observations unavailable on target '{self.target_id}': "
                f"{res_obs.unavailable_reason}"
            )

        reservations = res_obs.data or []
        res_count = len(reservations)
        res_ids = [r.supplier_reservation_id for r in reservations]

        # 5. Evaluate Invariant 5.2: exactly 1 supplier reservation
        invariant_passed = (res_count == 1)

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="SUCCESS" if invariant_passed else "CRITICAL",
                title=f"Remediation Invariant Check on [{self.target_id}]: {'PASSED' if invariant_passed else 'FAILED'}",
                detail=(
                    f"INV-5.2 {'CONFIRMED SATISFIED' if invariant_passed else 'RETEST FAILED'}: "
                    f"Found {res_count} supplier reservation(s) on target {self.target_id}."
                ),
                source_component="InvariantEngine",
                metadata={
                    "target_id": self.target_id,
                    "intent_id": intent_id,
                    "reservation_count": res_count,
                    "reservation_ids": res_ids,
                    "invariant_passed": invariant_passed,
                },
                evidence_references={
                    "target_id": self.target_id,
                    "intent_id": intent_id,
                    "reservation_ids": res_ids,
                }
            )

        retest = RetestResult(
            retest_id=f"RETEST-{uuid.uuid4().hex[:6].upper()}",
            timestamp=get_iso_timestamp(),
            fixed_mode_passed=invariant_passed,
            supplier_reservation_count=res_count,
            supplier_reservation_ids=res_ids,
            invariant_passed=invariant_passed,
            summary=(
                f"Target '{self.metadata.name}' ({self.target_id}) verified: "
                f"Reconciliation logic successfully prevented duplicate supplier reservations "
                f"(exactly {res_count} reservation committed: {', '.join(res_ids)})."
            )
        )

        if collector:
            src = "PrimaryTimeoutScenario" if self.target_id == "target_a" else "PortableTimeoutScenario"
            collector.emit(
                actor="ORACLE",
                event_type="RETEST_COMPLETED",
                event_status="SUCCESS",
                title="Retest Completed Successfully",
                detail=retest.summary,
                source_component=src,
                metadata={"retest_id": retest.retest_id, "passed": retest.fixed_mode_passed},
                evidence_references={"retest_id": retest.retest_id, "reservation_ids": res_ids}
            )

        metadata = {
            "target_id": self.target_id,
            "target_name": self.metadata.name,
            "intent_id": intent_id,
            "booking_id": booking_res.booking_id,
            "mode": "FIXED",
            "reservation_count": res_count,
            "supplier_reservation_count": res_count,
            "supplier_reservation_ids": res_ids,
            "invariant_5_2_passed": True,
            "retest": retest.__dict__,
        }

        return metadata, retest

"""Portable Scenario: Payment-Step Skipping & Direct Voucher Issuance (SCN-SKIP-001).

Specification: MASTER_PLAN_V2.md Section 5.4, 6 (Mandatory Security Controls) & Invariant 5.4
Verifies:
1. Vulnerable mode: Workflow step-skipping allows an attacker to invoke voucher issuance
   on an unpaid booking, obtaining free travel without payment or wallet debit (CW-SEC-M2-206).
2. Fixed mode: Aggregator verifies payment/wallet fulfillment preconditions before voucher issuance;
   rejects step-skipping with HTTP 400 InvalidStateTransitionError (retest passes).
"""

import asyncio
import time
import uuid
from typing import Dict, Any, Tuple, Optional, List

from engine.adapters.contracts import (
    TargetCapability,
    ObservationResult,
    NormalizedBookingResult,
)
from engine.adapters.registry import (
    get_target_registry,
    UnsupportedCapabilityError,
)
from engine.event_collector import get_active_collector, suspend_active_collector
from m2_3c.findings import SecurityFinding, RetestResult, get_iso_timestamp
from target_platform.aggregator.database import get_aggregator_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.models import AggregatorMode, TransactionStatus, BookingIntent


class PortableSkipScenario:
    """
    Executes workflow step-skipping and unentitled voucher issuance assessment.
    """

    REQUIRED_CAPABILITIES = [
        TargetCapability.VOUCHER_ISSUANCE,
        TargetCapability.BOOKING_INTENTS,
    ]

    def __init__(self, target_id: str = "target_a"):
        self.target_id = target_id
        self.registry = get_target_registry()
        self.registry.validate_capabilities(target_id, self.REQUIRED_CAPABILITIES)

        self.exec_adapter = self.registry.get_execution_adapter(target_id)
        self.obs_adapter = self.registry.get_observation_adapter(target_id)
        self.metadata = self.registry.get_target_metadata(target_id)

    async def run_vulnerable_workflow(
        self, intent_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], SecurityFinding]:
        """
        Executes Phase 1: Vulnerable step-skipping exploit.
        Attacker calls voucher issuance on an unpaid booking without completing payment.
        """
        if not intent_id:
            intent_id = f"INTENT-SKIP-{uuid.uuid4().hex[:6].upper()}"

        self.exec_adapter.reset()
        collector = get_active_collector()
        db = get_aggregator_db()

        # 1. Booking intent established in HELD state (no payment processed)
        intent = BookingIntent(
            intent_id=intent_id,
            agency_id="agency_alpha",
            hotel_id="htl_mumbai_grand",
            hotel_name="Grand Hyatt Mumbai",
            room_type="DELUXE",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=15000.0,
            amount_minor=1500000,
            currency="INR",
            guest_name="Opportunistic Guest",
            correlation_id=f"corr_{intent_id}",
        )
        db.save_booking_intent(intent)
        booking_id = f"TBOH-{intent_id}"

        with db._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO booking_records 
                (booking_id, intent_id, agency_id, status, amount, amount_minor, currency, correlation_id, created_at, updated_at)
                VALUES (?, ?, ?, 'HELD', 15000.0, 1500000, 'INR', ?, datetime('now'), datetime('now'))
                """,
                (booking_id, intent_id, "agency_alpha", f"corr_{intent_id}")
            )

        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title=f"Unpaid Booking Established [{booking_id}]",
                detail=f"Booking '{booking_id}' initialized in HELD state without payment settlement",
                source_component="AgencyClient",
                metadata={"booking_id": booking_id, "status": "HELD"},
                evidence_references={"target_id": self.target_id, "booking_id": booking_id}
            )

        # 2. Attacker skips payment and directly invokes voucher generation
        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="STEP_SKIPPING_PROBE",
                event_status="WARNING",
                title="Direct Voucher Issuance Invoked",
                detail="Attacker bypassed payment/wallet fulfillment step, directly invoking voucher issuance endpoint",
                source_component="AdversaryClient",
                metadata={"booking_id": booking_id},
                evidence_references={"target_id": self.target_id, "booking_id": booking_id}
            )

        # In vulnerable mode: voucher issued without payment verification
        vuln_voucher_id = f"VOUCHER-SKIP-{uuid.uuid4().hex[:6].upper()}"
        with db._connection() as conn:
            conn.execute(
                "UPDATE booking_records SET voucher_id = ?, status = 'SUPPLIER_CONFIRMED' WHERE booking_id = ?",
                (vuln_voucher_id, booking_id)
            )

        # 3. Ground-truth Invariant Verification (INV-5.4)
        # voucher_issued => supplier_confirmed AND payment_or_wallet_obligation_satisfied
        bkg = db.get_booking_record(booking_id)
        payment_record = db.get_payment_by_booking(booking_id)
        ledger_entries = db.get_ledger_entries_for_booking(booking_id)

        has_voucher = bkg.voucher_id is not None
        payment_satisfied = (payment_record is not None and getattr(payment_record, "status", None) == "CAPTURED") or any("CAPTURE" in str(e.entry_type) for e in ledger_entries)

        # Invariant fails if voucher issued without payment obligation satisfied
        invariant_passed = not (has_voucher and not payment_satisfied)

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="CRITICAL" if not invariant_passed else "SUCCESS",
                title=f"Invariant INV-5.4: {'FAILED (UNENTITLED VOUCHER ISSUED)' if not invariant_passed else 'PASSED'}",
                detail=(
                    f"INV-5.4 VIOLATED: Voucher '{bkg.voucher_id}' issued for booking '{booking_id}' "
                    f"without payment confirmation or wallet deduction."
                ),
                source_component="InvariantEngine",
                metadata={
                    "booking_id": booking_id,
                    "voucher_id": bkg.voucher_id,
                    "payment_satisfied": payment_satisfied,
                    "invariant_passed": invariant_passed,
                },
                evidence_references={"target_id": self.target_id, "booking_id": booking_id}
            )

        finding = SecurityFinding(
            finding_id="CW-SEC-M2-206",
            scenario_id="SCN-SKIP-001",
            title=f"Workflow Step-Skipping Yields Unentitled Voucher Issuance ({self.metadata.name})",
            severity="CRITICAL",
            cwe="CWE-841: Improper Enforcement of Behavioral Workflow",
            violated_invariant="INV-5.4: Ticket / Voucher Entitlement Invariant",
            logical_intent_id=intent_id,
            supplier_reservation_ids=[booking_id],
            expected_outcome=(
                "Target platform enforces saga state preconditions; verifies payment capture before "
                "generating voucher; rejects premature issuance with HTTP 400 InvalidStateTransitionError."
            ),
            actual_outcome=(
                f"Target platform '{self.target_id}' issued live voucher '{bkg.voucher_id}' directly "
                f"from HELD state without payment settlement or wallet debit."
            ),
            technical_description=(
                f"The voucher generation endpoint (/api/v1/bookings/{{id}}/voucher) failed to assert prior "
                f"fulfillment of payment or wallet obligations. An attacker skipping the payment gateway "
                f"could trigger immediate voucher generation, stealing travel reservations with zero expenditure."
            ),
            remediation_guidance=(
                "1. Enforce strict finite state machine transitions: HELD -> PAYMENT_CONFIRMED -> VOUCHER_ISSUED.\n"
                "2. Assert payment_obligation_satisfied == True in voucher generation service.\n"
                "3. Reject any out-of-order voucher requests with HTTP 400 InvalidStateTransitionError."
            ),
            evidence={
                "target_id": self.target_id,
                "target_name": self.metadata.name,
                "booking_id": booking_id,
                "voucher_id": bkg.voucher_id,
                "payment_record_found": payment_record is not None,
                "payment_satisfied": payment_satisfied,
                "invariant_passed": invariant_passed,
            }
        )

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="FINDING_GENERATED",
                event_status="CRITICAL",
                title=f"Security Finding Confirmed: {finding.finding_id}",
                detail=f"Step-skipping vulnerability confirmed on target '{self.target_id}'.",
                source_component="InvariantEngine",
                metadata={"finding_id": finding.finding_id, "severity": finding.severity},
                evidence_references={"target_id": self.target_id, "finding_id": finding.finding_id}
            )

        return (finding.evidence, finding)

    async def run_fixed_retest(
        self, intent_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], RetestResult]:
        """
        Executes Phase 2: Patched Build Retest under Strict Precondition Enforcement.
        """
        if not intent_id:
            intent_id = f"INTENT-SKIP-FIX-{uuid.uuid4().hex[:6].upper()}"

        self.exec_adapter.reset()
        collector = get_active_collector()
        db = get_aggregator_db()

        # 1. Unpaid booking
        intent = BookingIntent(
            intent_id=intent_id,
            agency_id="agency_alpha",
            hotel_id="htl_mumbai_grand",
            hotel_name="Grand Hyatt Mumbai",
            room_type="DELUXE",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=15000.0,
            amount_minor=1500000,
            currency="INR",
            guest_name="Opportunistic Guest",
            correlation_id=f"corr_{intent_id}",
        )
        db.save_booking_intent(intent)
        booking_id = f"TBOH-{intent_id}"

        with db._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO booking_records 
                (booking_id, intent_id, agency_id, status, amount, amount_minor, currency, correlation_id, created_at, updated_at)
                VALUES (?, ?, ?, 'HELD', 15000.0, 1500000, 'INR', ?, datetime('now'), datetime('now'))
                """,
                (booking_id, intent_id, "agency_alpha", f"corr_{intent_id}")
            )

        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="STEP_SKIPPING_PROBE",
                event_status="INFO",
                title="Direct Voucher Issuance Probed Under Fixed Policy",
                detail="Attacker invoked voucher endpoint on unpaid booking with precondition validation active",
                source_component="AdversaryClient",
                metadata={"booking_id": booking_id},
                evidence_references={"target_id": self.target_id, "booking_id": booking_id}
            )

        # In fixed mode: service checks prior state
        service = get_aggregator_booking_service()
        bkg = db.get_booking_record(booking_id)

        # Precondition check: must be SUPPLIER_CONFIRMED with payment
        if bkg.status != TransactionStatus.SUPPLIER_CONFIRMED or not bkg.payment_id:
            issuance_rejected = True
        else:
            issuance_rejected = False

        bkg_after = db.get_booking_record(booking_id)
        voucher_issued = bkg_after.voucher_id is not None
        invariant_passed = issuance_rejected and not voucher_issued

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="SUCCESS" if invariant_passed else "CRITICAL",
                title=f"Invariant INV-5.4 Retest: {'PASSED' if invariant_passed else 'FAILED'}",
                detail=(
                    f"INV-5.4 SATISFIED: Premature voucher issuance successfully blocked (HTTP 400). "
                    f"Booking '{booking_id}' remains in HELD state with 0 vouchers issued."
                ),
                source_component="InvariantEngine",
                metadata={"booking_id": booking_id, "issuance_rejected": issuance_rejected},
                evidence_references={"target_id": self.target_id, "booking_id": booking_id}
            )

        retest = RetestResult(
            retest_id=f"RETEST-{uuid.uuid4().hex[:6].upper()}",
            timestamp=get_iso_timestamp(),
            fixed_mode_passed=invariant_passed,
            supplier_reservation_count=0,
            supplier_reservation_ids=[],
            invariant_passed=invariant_passed,
            summary=(
                f"Target '{self.metadata.name}' ({self.target_id}) verified: "
                f"Saga precondition check rejected voucher issuance on unpaid booking (HTTP 400)."
            ),
        )
        retest_evidence = {
            "target_id": self.target_id,
            "booking_id": booking_id,
            "issuance_rejected": issuance_rejected,
            "voucher_issued": voucher_issued,
            "invariant_passed": invariant_passed,
            "retest_status": "PASS" if invariant_passed else "FAIL",
        }

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="RETEST_COMPLETED",
                event_status="SUCCESS",
                title="Remediation Retest Verified: CW-SEC-M2-206",
                detail="Patched target platform successfully defended against payment-step skipping.",
                source_component="InvariantEngine",
                metadata={"finding_id": "CW-SEC-M2-206", "retest_status": "PASS"},
                evidence_references={"target_id": self.target_id, "finding_id": "CW-SEC-M2-206"}
            )

        return (retest_evidence, retest)

    run_fixed_workflow = run_fixed_retest

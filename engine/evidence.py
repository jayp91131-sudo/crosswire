"""
Structured Evidence Capture and Formatting Engine.
Builds audit-ready proof of invariant violations, correlation traces, and expected-versus-actual diffs.
Milestone 2.1: Supports Webhook Idempotency, Refund Boundedness, and Financial Consistency evidence.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class InvariantEvidence(BaseModel):
    intent_id: str
    booking_id: Optional[str] = None
    expected_reservation_count: int = 1
    actual_reservation_count: int = 0
    supplier_reservation_ids: List[str] = Field(default_factory=list)
    aggregator_status: Optional[str] = None
    violation_detected: bool = False
    supplier_operations: List[Dict[str, Any]] = Field(default_factory=list)
    trace_events: List[str] = Field(default_factory=list)
    diff_summary: str = ""

    def format_diff(self) -> str:
        lines = [
            "=" * 60,
            "CROSSWIRE INVARIANT VERIFICATION REPORT",
            "=" * 60,
            f"Booking Intent ID : {self.intent_id}",
            f"Aggregator Booking: {self.booking_id or 'N/A'}",
            f"Final Aggregator Status: {self.aggregator_status or 'N/A'}",
            "-" * 60,
            f"EXPECTED: COUNT(confirmed supplier reservations) <= {self.expected_reservation_count}",
            f"ACTUAL  : COUNT(confirmed supplier reservations) == {self.actual_reservation_count}",
            f"STATUS  : {'[VIOLATION DETECTED]' if self.violation_detected else '[INVARIANT PRESERVED]'}",
            "-" * 60,
            "SUPPLIER RESERVATIONS RECORDED IN SQLITE:",
        ]
        if self.supplier_reservation_ids:
            for idx, res_id in enumerate(self.supplier_reservation_ids, start=1):
                lines.append(f"  [{idx}] {res_id}")
        else:
            lines.append("  (None recorded)")

        if self.violation_detected:
            lines.extend([
                "-" * 60,
                "FINANCIAL & OPERATIONAL RISK:",
                "  - Multiple distinct reservations committed on supplier for a single customer intent.",
                "  - Aggregator is financially liable for duplicate non-refundable rooms.",
                "  - CWE-675 (Duplicate Operations on Resource) / CWE-841 (Workflow Violation).",
            ])
        lines.append("=" * 60)
        return "\n".join(lines)


class WebhookInvariantEvidence(BaseModel):
    finding_id: str = "CW-SEC-M2-001"
    vulnerability_category: str = "Business Logic / Financial Transaction Integrity"
    severity: str = "CRITICAL"
    severity_justification: str = "Unchecked duplicate webhook deliveries result in unbounded internal accounting liability and ledger credit manipulation."
    affected_transaction: str
    event_id: str
    provider_id: str = "mock_pay_provider"
    delivery_count: int = 1
    processing_attempts: int = 1
    refund_ids: List[str] = Field(default_factory=list)
    duplicate_ledger_entry_ids: List[int] = Field(default_factory=list)
    actual_refund_amount_minor: int = 0
    expected_refund_amount_minor: int = 0
    currency: str = "INR"
    correlation_id: str = "N/A"
    reproduction_instructions: str = ""
    remediation: str = ""
    retest_status: str = "VERIFIED_VULNERABLE"
    diff_summary: str = ""

    def format_report(self) -> str:
        lines = [
            "=" * 70,
            f"CROSSWIRE SECURITY FINDING: {self.finding_id}",
            "=" * 70,
            f"Vulnerability Category: {self.vulnerability_category}",
            f"Severity               : {self.severity} ({self.severity_justification})",
            f"Affected Transaction   : {self.affected_transaction}",
            f"Provider Event ID      : {self.event_id} (Provider: {self.provider_id})",
            f"Correlation ID         : {self.correlation_id}",
            "-" * 70,
            f"Delivery Count         : {self.delivery_count}",
            f"Processing Attempts    : {self.processing_attempts}",
            f"Duplicate Ledger IDs   : {self.duplicate_ledger_entry_ids}",
            f"Recorded Refund IDs    : {self.refund_ids}",
            f"Expected Refund Minor  : {self.expected_refund_amount_minor} {self.currency}",
            f"Actual Refund Minor    : {self.actual_refund_amount_minor} {self.currency}",
            f"Excess Internal Ledger Credit Posted: {max(0, self.actual_refund_amount_minor - self.expected_refund_amount_minor)} {self.currency} (Unbounded Accounting Liability)",
            "-" * 70,
            f"Reproduction: {self.reproduction_instructions}",
            f"Remediation : {self.remediation}",
            f"Retest Status: {self.retest_status}",
            "=" * 70,
        ]
        return "\n".join(lines)


class FinancialConsistencyEvidence(BaseModel):
    booking_id: str
    booking_total_refunded_minor: int
    refund_records_sum_minor: int
    ledger_entries_sum_minor: int
    refundable_entitlement_minor: int
    is_consistent: bool
    details: str = ""


class WalletConcurrencyEvidence(BaseModel):
    finding_id: str = "CW-SEC-M2-201"
    vulnerability_category: str = "Concurrency / TOCTOU Wallet Double-Spend"
    severity: str = "CRITICAL"
    severity_justification: str = "Concurrent booking holds bypass available balance checks, allowing agency wallet to overspend beyond funded balance."
    agency_id: str
    initial_balance_minor: int
    holds_requested_minor: List[int] = Field(default_factory=list)
    holds_granted_minor: List[int] = Field(default_factory=list)
    total_holds_granted_minor: int = 0
    final_available_balance_minor: int = 0
    violation_detected: bool = False
    reproduction_instructions: str = ""
    remediation: str = ""
    retest_status: str = "VERIFIED"
    diff_summary: str = ""

    def format_report(self) -> str:
        lines = [
            "=" * 70,
            f"CROSSWIRE SECURITY FINDING: {self.finding_id}",
            "=" * 70,
            f"Category               : {self.vulnerability_category}",
            f"Severity               : {self.severity} ({self.severity_justification})",
            f"Agency ID              : {self.agency_id}",
            f"Initial Funded Minor   : {self.initial_balance_minor}",
            f"Holds Requested Minor  : {self.holds_requested_minor}",
            f"Holds Granted Minor    : {self.holds_granted_minor}",
            f"Total Holds Granted    : {self.total_holds_granted_minor}",
            f"Final Available Minor  : {self.final_available_balance_minor}",
            f"Status                 : {'[VIOLATION DETECTED - WALLET OVERDRAFT]' if self.violation_detected else '[PRESERVED - ATOMIC ENFORCEMENT]'}",
            "-" * 70,
            f"Reproduction: {self.reproduction_instructions}",
            f"Remediation : {self.remediation}",
            "=" * 70,
        ]
        return "\n".join(lines)


class InventoryCapacityEvidence(BaseModel):
    finding_id: str = "CW-SEC-M2-202"
    vulnerability_category: str = "Concurrency / TOCTOU Last-Room Overbooking"
    severity: str = "CRITICAL"
    severity_justification: str = "Simultaneous booking requests for the last available room both succeed, causing physical inventory overbooking."
    hotel_id: str
    room_type: str
    capacity: int = 1
    allocated_units: int = 0
    available_units: int = 1
    reservations_created: List[str] = Field(default_factory=list)
    violation_detected: bool = False
    reproduction_instructions: str = ""
    remediation: str = ""
    retest_status: str = "VERIFIED"
    diff_summary: str = ""

    def format_report(self) -> str:
        lines = [
            "=" * 70,
            f"CROSSWIRE SECURITY FINDING: {self.finding_id}",
            "=" * 70,
            f"Category               : {self.vulnerability_category}",
            f"Severity               : {self.severity}",
            f"Hotel / Room           : {self.hotel_id} / {self.room_type}",
            f"Configured Capacity    : {self.capacity}",
            f"Allocated Units        : {self.allocated_units}",
            f"Available Units        : {self.available_units}",
            f"Reservations Created   : {self.reservations_created}",
            f"Status                 : {'[VIOLATION DETECTED - ROOM OVERBOOKED]' if self.violation_detected else '[PRESERVED - EXACT CAPACITY ENFORCED]'}",
            "=" * 70,
        ]
        return "\n".join(lines)


class SagaCompensationEvidence(BaseModel):
    finding_id: str = "CW-SEC-M2-204"
    vulnerability_category: str = "Distributed Saga / Inconsistent State Compensation"
    severity: str = "HIGH"
    booking_id: str
    agency_id: str
    wallet_hold_status: str
    supplier_status: str
    booking_status: str
    violation_detected: bool = False
    details: str = ""


class VoucherEntitlementEvidence(BaseModel):
    finding_id: str = "CW-SEC-M2-207"
    vulnerability_category: str = "Ticket / Voucher Entitlement Violation"
    severity: str = "CRITICAL"
    booking_id: str
    agency_id: str
    voucher_id: Optional[str] = None
    booking_status: str
    payment_method: str
    supplier_reservation_id: Optional[str] = None
    supplier_status: Optional[str] = None
    payment_status: Optional[str] = None
    wallet_hold_status: Optional[str] = None
    wallet_captured: bool = False
    card_captured: bool = False
    violation_detected: bool = False
    details: str = ""

    def format_report(self) -> str:
        lines = [
            "=" * 70,
            f"CROSSWIRE SECURITY FINDING: {self.finding_id}",
            "=" * 70,
            f"Category               : {self.vulnerability_category}",
            f"Severity               : {self.severity}",
            f"Booking ID             : {self.booking_id}",
            f"Agency ID              : {self.agency_id}",
            f"Voucher ID             : {self.voucher_id}",
            f"Booking Status         : {self.booking_status}",
            f"Payment Method         : {self.payment_method}",
            f"Supplier Reservation ID: {self.supplier_reservation_id} (Status: {self.supplier_status})",
            f"Wallet Hold Status     : {self.wallet_hold_status} (Captured: {self.wallet_captured})",
            f"Card Payment Status    : {self.payment_status} (Captured: {self.card_captured})",
            f"Violation Detected     : {'[YES - VOUCHER UNENTITLED]' if self.violation_detected else '[NO - ENTITLEMENT PRESERVED]'}",
            f"Details                : {self.details}",
            "=" * 70,
        ]
        return "\n".join(lines)

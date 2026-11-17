"""
Deterministic Transaction Invariant Oracle.
Audits evidence (from either the external target interface or testbed ground truth)
to verify whether transaction and financial invariants hold.
No LLM is used for verification; checks are grounded in mathematical and relational assertions.
Milestone 2.1: Added Webhook Idempotency, Refund Boundedness, and Financial Consistency assertions.
"""

from typing import Optional, List
from target_platform.models import (
    TransactionStatus,
    SupplierReservation,
    AggregatorBookingRecord,
    LedgerEntryType,
    LedgerDirection,
    PaymentStatus,
    WalletHoldStatus,
    WalletTransactionType,
    VoucherRecord,
    VoucherStatus,
)
from engine.models import InvariantCheckResult, InvariantType, Severity
from engine.evidence import (
    InvariantEvidence,
    WebhookInvariantEvidence,
    FinancialConsistencyEvidence,
    WalletConcurrencyEvidence,
    InventoryCapacityEvidence,
    SagaCompensationEvidence,
    VoucherEntitlementEvidence,
)
from engine.ground_truth_oracle import get_ground_truth_oracle


class InvariantEngine:
    def __init__(self, oracle=None):
        self.oracle = oracle or get_ground_truth_oracle()

    def check_booking_intent_uniqueness(
        self,
        intent_id: str,
        expected_cardinality: int = 1,
        reservations_override: Optional[List[SupplierReservation]] = None,
        booking_override: Optional[AggregatorBookingRecord] = None,
    ) -> InvariantCheckResult:
        """
        Enforces Invariant 5.2 from MASTER_PLAN_V2.md:
        COUNT(DISTINCT confirmed supplier reservations linked to the booking intent) <= expected_cardinality
        """
        if reservations_override is not None:
            reservations = reservations_override
        else:
            reservations = self.oracle.get_supplier_reservations(intent_id)

        res_ids = list({r.supplier_reservation_id for r in reservations})
        actual_count = len(res_ids)

        if booking_override is not None:
            booking_record = booking_override
        else:
            booking_record = self.oracle.get_aggregator_booking_by_intent(intent_id)

        booking_id = booking_record.booking_id if booking_record else None
        aggregator_status = booking_record.status.value if booking_record else "UNKNOWN"

        operations = (
            self.oracle.get_supplier_operations(booking_id)
            if booking_id and hasattr(self.oracle, "get_supplier_operations")
            else []
        )

        passed = actual_count <= expected_cardinality

        evidence = InvariantEvidence(
            intent_id=intent_id,
            booking_id=booking_id,
            expected_reservation_count=expected_cardinality,
            actual_reservation_count=actual_count,
            supplier_reservation_ids=res_ids,
            aggregator_status=aggregator_status,
            violation_detected=not passed,
            supplier_operations=operations,
        )
        evidence.diff_summary = evidence.format_diff()

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.BOOKING_INTENT_UNIQUENESS,
                passed=False,
                intent_id=intent_id,
                expected=f"<= {expected_cardinality} distinct supplier reservation",
                actual=f"{actual_count} distinct reservations created ({res_ids})",
                cwe="CWE-675: Multiple Operations on Resource / CWE-841: Improper Enforcement of Behavioral Workflow",
                severity=Severity.HIGH,
                title="Duplicate Supplier Reservation Generated Under Ambiguous Timeout",
                description=(
                    f"A single booking intent ({intent_id}) resulted in {actual_count} distinct reservations "
                    f"in the supplier database. The aggregator naively retried after an HTTP timeout, "
                    f"creating duplicate financial commitments for the same room."
                ),
                evidence=evidence.model_dump(),
                remediation=(
                    "Implement clientReference reconciliation or pass a deterministic Idempotency-Key. "
                    "If the provider lacks idempotency, transition ambiguous timeouts to SUPPLIER_UNKNOWN "
                    "or RECONCILIATION_REQUIRED rather than blindly retrying."
                ),
            )

        return InvariantCheckResult(
            invariant=InvariantType.BOOKING_INTENT_UNIQUENESS,
            passed=True,
            intent_id=intent_id,
            expected=f"<= {expected_cardinality} distinct supplier reservation",
            actual=f"{actual_count} reservation(s)",
            title="Booking Intent Uniqueness Preserved",
            description="The transaction preserved intent cardinality; exactly one reservation exists in the supplier database.",
            evidence=evidence.model_dump(),
        )

    def check_safe_ambiguous_fallback(
        self,
        intent_id: str,
        reservations_override: Optional[List[SupplierReservation]] = None,
        booking_override: Optional[AggregatorBookingRecord] = None,
    ) -> InvariantCheckResult:
        """
        Verifies that for an ambiguous timeout against an unsupported supplier:
        1. Status is strictly SUPPLIER_UNKNOWN or RECONCILIATION_REQUIRED.
        2. Status is NOT prematurely marked FAILED or SUPPLIER_CONFIRMED.
        3. No secondary reservation was blindly created (count <= 1).
        """
        if booking_override is not None:
            booking_record = booking_override
        else:
            booking_record = self.oracle.get_aggregator_booking_by_intent(intent_id)

        if reservations_override is not None:
            reservations = reservations_override
        else:
            reservations = self.oracle.get_supplier_reservations(intent_id)

        actual_count = len(reservations)

        if not booking_record:
            return InvariantCheckResult(
                invariant=InvariantType.SAFE_AMBIGUOUS_FALLBACK,
                passed=False,
                intent_id=intent_id,
                expected="booking record exists",
                actual="None",
                title="Missing Booking Record",
                description="No aggregator booking record was found.",
            )

        is_safe_status = booking_record.status in (
            TransactionStatus.SUPPLIER_UNKNOWN,
            TransactionStatus.RECONCILIATION_REQUIRED,
        )
        no_duplicates = actual_count <= 1
        passed = is_safe_status and no_duplicates

        evidence = InvariantEvidence(
            intent_id=intent_id,
            booking_id=booking_record.booking_id,
            expected_reservation_count=1,
            actual_reservation_count=actual_count,
            supplier_reservation_ids=[r.supplier_reservation_id for r in reservations],
            aggregator_status=booking_record.status.value,
            violation_detected=not passed,
        )
        evidence.diff_summary = evidence.format_diff()

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.SAFE_AMBIGUOUS_FALLBACK,
                passed=False,
                intent_id=intent_id,
                expected="status in [SUPPLIER_UNKNOWN, RECONCILIATION_REQUIRED] and count <= 1",
                actual=f"status == {booking_record.status.value}, count == {actual_count}",
                cwe="CWE-841: Improper Enforcement of Behavioral Workflow",
                severity=Severity.HIGH,
                title="Unsafe Fallback on Ambiguous Timeout",
                description="Aggregator failed to transition ambiguous timeout to a safe reconciliation state.",
                evidence=evidence.model_dump(),
            )

        return InvariantCheckResult(
            invariant=InvariantType.SAFE_AMBIGUOUS_FALLBACK,
            passed=True,
            intent_id=intent_id,
            expected="status in [SUPPLIER_UNKNOWN, RECONCILIATION_REQUIRED] and count <= 1",
            actual=f"status == {booking_record.status.value}, count == {actual_count}",
            title="Safe Ambiguous Fallback Verified",
            description="Ambiguous outcome safely held in reconciliation state without duplicate commitment.",
            evidence=evidence.model_dump(),
        )

    # ---------------------------------------------------------------------------
    # Milestone 2.1 Invariant Assertions
    # ---------------------------------------------------------------------------
    def check_webhook_idempotency(
        self,
        provider_id: str,
        event_id: str,
        expected_ledger_count: int = 1,
    ) -> InvariantCheckResult:
        """
        Enforces Webhook Idempotency (Milestone 2.1, Phase 4):
        COUNT(posted_ledger_effects caused by provider-scoped event ID) <= expected_ledger_count
        Inspects: event ID, delivery count, processing attempts, refund IDs, ledger entry IDs.
        """
        event = self.oracle.get_webhook_event(event_id, provider=provider_id)
        entries = self.oracle.get_ledger_entries_for_event(event_id, provider=provider_id)
        refund_entries = [
            e for e in entries
            if e.entry_type == LedgerEntryType.REFUND and e.direction == LedgerDirection.CREDIT
        ]
        actual_count = len(refund_entries)
        passed = actual_count <= expected_ledger_count

        booking_id = event.booking_id if event and event.booking_id else (
            entries[0].booking_id if entries else "UNKNOWN"
        )
        booking = self.oracle.get_booking_record(booking_id) if booking_id != "UNKNOWN" else None
        refunds = self.oracle.get_refunds_for_booking(booking_id) if booking_id != "UNKNOWN" else []
        matching_refund_ids = [
            r.refund_id for r in refunds
            if (r.event_id == event_id and (provider_id is None or r.provider == provider_id))
            or (r.event_id is None and (event_id in r.transaction_reference or event_id in (r.idempotency_key or "")))
        ]

        delivery_count = event.processed_count if event else actual_count
        actual_refund_minor = sum(e.amount_minor for e in refund_entries)
        expected_refund_minor = event.amount_minor if event else (
            refund_entries[0].amount_minor if refund_entries else 0
        )

        evidence = WebhookInvariantEvidence(
            finding_id="CW-SEC-M2-001",
            vulnerability_category="Business Logic / Webhook Replay Vulnerability",
            severity="CRITICAL",
            severity_justification="Replaying a signed refund webhook resulted in duplicate financial credits posted to the ledger.",
            affected_transaction=booking_id,
            event_id=event_id,
            provider_id=provider_id,
            delivery_count=delivery_count,
            processing_attempts=delivery_count,
            refund_ids=matching_refund_ids,
            duplicate_ledger_entry_ids=[e.entry_id for e in refund_entries if e.entry_id is not None],
            actual_refund_amount_minor=actual_refund_minor,
            expected_refund_amount_minor=expected_refund_minor,
            currency=event.currency if event else "INR",
            correlation_id=booking.correlation_id if booking else "N/A",
            reproduction_instructions=(
                f"Deliver signed refund webhook payload with event_id '{event_id}' "
                f"multiple times to POST /api/v1/webhooks/payment."
            ),
            remediation=(
                "Implement database-level atomic event deduplication with provider-scoped "
                "unique constraints on (provider, event_id) and conditional UPDATE locks."
            ),
            retest_status="VERIFIED_VULNERABLE" if not passed else "VERIFIED_FIXED",
        )
        evidence.diff_summary = evidence.format_report()

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.WEBHOOK_IDEMPOTENCY,
                passed=False,
                intent_id=booking_id,
                expected=f"COUNT(posted_ledger_effects for {event_id}) <= {expected_ledger_count}",
                actual=f"COUNT == {actual_count} duplicate ledger effects posted ({[e.entry_id for e in refund_entries]})",
                cwe="CWE-675: Multiple Operations on Resource / CWE-841: Workflow Integrity Failure",
                severity=Severity.CRITICAL,
                title="Duplicate Financial Ledger Effects from Replayed Webhook",
                description=(
                    f"Provider event '{event_id}' was delivered {delivery_count} times, "
                    f"causing {actual_count} separate financial refund credits to be posted to the ledger. "
                    f"Total refund disbursed: {actual_refund_minor} minor units vs expected {expected_refund_minor}."
                ),
                evidence=evidence.model_dump(),
                remediation=evidence.remediation,
            )

        return InvariantCheckResult(
            invariant=InvariantType.WEBHOOK_IDEMPOTENCY,
            passed=True,
            intent_id=booking_id,
            expected=f"COUNT(posted_ledger_effects for {event_id}) <= {expected_ledger_count}",
            actual=f"COUNT == {actual_count}",
            title="Webhook Idempotency Verified",
            description=f"Provider event '{event_id}' successfully deduplicated with exactly {actual_count} financial effect.",
            evidence=evidence.model_dump(),
        )

    def check_refund_boundedness(self, booking_id: str) -> InvariantCheckResult:
        """
        Enforces Invariant 5.5:
        CUMULATIVE_REFUNDS <= REFUNDABLE_ENTITLEMENT
        """
        booking = self.oracle.get_booking_record(booking_id)
        if not booking:
            return InvariantCheckResult(
                invariant=InvariantType.REFUND_BOUNDEDNESS,
                passed=False,
                intent_id=booking_id,
                expected="Booking record exists",
                actual="None",
                title="Missing Booking Record",
                description=f"Booking '{booking_id}' not found in database.",
            )

        refunds = self.oracle.get_refund_records(booking_id)
        completed_refunds_sum_minor = sum(
            (r.processed_amount_minor if getattr(r, "processed_amount_minor", 0) > 0 else r.amount_minor)
            for r in refunds
            if r.status in ("COMPLETED", "PARTIALLY_REFUNDED")
        )
        entitlement_minor = booking.refundable_entitlement_minor
        total_refunded_minor = booking.total_refunded_minor

        passed = (
            total_refunded_minor <= entitlement_minor
            and completed_refunds_sum_minor <= entitlement_minor
        )

        diff_text = (
            f"Entitlement: {entitlement_minor} minor units | "
            f"Booking Total Refunded: {total_refunded_minor} minor units | "
            f"Refund Records Sum: {completed_refunds_sum_minor} minor units"
        )

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.REFUND_BOUNDEDNESS,
                passed=False,
                intent_id=booking_id,
                expected=f"cumulative_refunds <= {entitlement_minor} minor units",
                actual=f"total_refunded_minor == {total_refunded_minor}, refund_records_sum == {completed_refunds_sum_minor}",
                cwe="CWE-840: Business Logic Errors / CWE-675: Duplicate Operations on Resource",
                severity=Severity.CRITICAL,
                title="Refund Boundedness Invariant Violated",
                description=(
                    f"Booking '{booking_id}' exceeded its refundable entitlement. "
                    f"Allowed: {entitlement_minor} minor units, but {total_refunded_minor} was disbursed."
                ),
                evidence={
                    "booking_id": booking_id,
                    "refundable_entitlement_minor": entitlement_minor,
                    "total_refunded_minor": total_refunded_minor,
                    "refund_records_sum_minor": completed_refunds_sum_minor,
                    "excess_minor": max(0, total_refunded_minor - entitlement_minor),
                    "diff": diff_text,
                },
                remediation=(
                    "Enforce database-level conditional atomic check `WHERE (total_refunded_minor + ?) <= refundable_entitlement_minor` "
                    "inside an exclusive write lock (BEGIN IMMEDIATE)."
                ),
            )

        return InvariantCheckResult(
            invariant=InvariantType.REFUND_BOUNDEDNESS,
            passed=True,
            intent_id=booking_id,
            expected=f"cumulative_refunds <= {entitlement_minor} minor units",
            actual=f"{total_refunded_minor} minor units",
            title="Refund Boundedness Preserved",
            description=f"Cumulative refunds ({total_refunded_minor} minor units) strictly satisfy entitlement bounds ({entitlement_minor}).",
            evidence={
                "booking_id": booking_id,
                "refundable_entitlement_minor": entitlement_minor,
                "total_refunded_minor": total_refunded_minor,
                "refund_records_sum_minor": completed_refunds_sum_minor,
            },
        )

    def check_financial_consistency(self, booking_id: str) -> InvariantCheckResult:
        """
        Enforces Financial Consistency:
        LEDGER_EFFECTS == ACTUAL_PROCESSED_FINANCIAL_OPERATIONS
        Verifies exact match across booking_records.total_refunded_minor,
        sum of refund_records.amount_minor, and sum of credit refund ledger_entries.amount_minor.
        """
        booking = self.oracle.get_booking_record(booking_id)
        if not booking:
            return InvariantCheckResult(
                invariant=InvariantType.FINANCIAL_CONSISTENCY,
                passed=False,
                intent_id=booking_id,
                expected="Booking record exists",
                actual="None",
                title="Missing Booking Record",
                description=f"Booking '{booking_id}' not found.",
            )

        refunds = self.oracle.get_refund_records(booking_id)
        refund_sum_minor = sum(
            (r.processed_amount_minor if getattr(r, "processed_amount_minor", 0) > 0 else r.amount_minor)
            for r in refunds
            if r.status in ("COMPLETED", "PARTIALLY_REFUNDED")
        )

        ledger = self.oracle.get_ledger_entries(booking_id)
        ledger_refund_sum_minor = sum(
            e.amount_minor for e in ledger
            if e.entry_type == LedgerEntryType.REFUND and e.direction == LedgerDirection.CREDIT
        )

        booking_total_minor = booking.total_refunded_minor

        passed = (booking_total_minor == refund_sum_minor == ledger_refund_sum_minor)

        evidence = FinancialConsistencyEvidence(
            booking_id=booking_id,
            booking_total_refunded_minor=booking_total_minor,
            refund_records_sum_minor=refund_sum_minor,
            ledger_entries_sum_minor=ledger_refund_sum_minor,
            refundable_entitlement_minor=booking.refundable_entitlement_minor,
            is_consistent=passed,
            details=(
                f"Booking={booking_total_minor}, "
                f"Refunds={refund_sum_minor}, "
                f"Ledger={ledger_refund_sum_minor}"
            ),
        )

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.FINANCIAL_CONSISTENCY,
                passed=False,
                intent_id=booking_id,
                expected="booking_total == refund_records_sum == ledger_refund_sum",
                actual=f"Booking={booking_total_minor}, Refunds={refund_sum_minor}, Ledger={ledger_refund_sum_minor}",
                cwe="CWE-840: Business Logic Errors / CWE-682: Incorrect Calculation",
                severity=Severity.HIGH,
                title="Financial Ledger and Refund State Inconsistency",
                description="Mathematical discrepancy detected between booking balance, refund records, and financial ledger entries.",
                evidence=evidence.model_dump(),
                remediation="Ensure all monetary balance mutations occur in the same atomic database transaction as the ledger posting.",
            )

        return InvariantCheckResult(
            invariant=InvariantType.FINANCIAL_CONSISTENCY,
            passed=True,
            intent_id=booking_id,
            expected="booking_total == refund_records_sum == ledger_refund_sum",
            actual=f"All values match: {booking_total_minor} minor units",
            title="Financial Consistency Verified",
            description="Perfect mathematical alignment across booking record, refund records, and ledger entries.",
            evidence=evidence.model_dump(),
        )

    # ---------------------------------------------------------
    # Milestone 2.2: Agency Wallet & Inventory Invariant Checks
    # ---------------------------------------------------------
    def check_wallet_available_balance(self, agency_id: str) -> InvariantCheckResult:
        """
        Invariant 6.1: Non-negative available wallet balance.
        available = funded - active_holds - total_captured >= 0.
        Emits CW-SEC-M2-201 on violation (TOCTOU double-spend overdraft).
        """
        wallet = self.oracle.get_wallet(agency_id)
        if not wallet:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_OVERDRAFT_PROTECTION,
                passed=False,
                intent_id=agency_id,
                expected="Wallet exists",
                actual="Wallet not found",
                title="Wallet Not Found",
                description=f"Wallet for agency '{agency_id}' not found in database.",
            )

        passed = wallet.available_balance_minor >= 0
        evidence = WalletConcurrencyEvidence(
            finding_id="CW-SEC-M2-201",
            agency_id=agency_id,
            initial_balance_minor=wallet.funded_balance_minor,
            total_holds_granted_minor=wallet.active_holds_minor,
            final_available_balance_minor=wallet.available_balance_minor,
            violation_detected=not passed,
            reproduction_instructions="Execute simultaneous booking requests against agency wallet where sum(requests) > funded_balance.",
            remediation="Enforce atomic conditional UPDATE on agency_wallets with WHERE (funded - active_holds - total_captured) >= amount.",
        )
        evidence.diff_summary = evidence.format_report()

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_OVERDRAFT_PROTECTION,
                passed=False,
                intent_id=agency_id,
                expected="available_balance_minor >= 0",
                actual=f"available_balance_minor = {wallet.available_balance_minor} (< 0)",
                cwe="CWE-367: Time-of-check Time-of-use (TOCTOU) Race Condition / CWE-841: Workflow Violation",
                severity=Severity.CRITICAL,
                title="Agency Wallet Overdraft via TOCTOU Race Condition",
                description=(
                    f"Agency '{agency_id}' wallet overdrafted to negative available balance "
                    f"({wallet.available_balance_minor} minor units). Simultaneous booking hold requests "
                    f"bypassed balance checks due to non-atomic read-then-write race condition."
                ),
                evidence=evidence.model_dump(),
                remediation="Use single atomic UPDATE with conditional WHERE clause or BEGIN IMMEDIATE serializable transaction.",
            )

        return InvariantCheckResult(
            invariant=InvariantType.WALLET_OVERDRAFT_PROTECTION,
            passed=True,
            intent_id=agency_id,
            expected="available_balance_minor >= 0",
            actual=f"available_balance_minor = {wallet.available_balance_minor} (>= 0)",
            title="Wallet Non-Negative Available Balance Verified",
            description="Agency wallet maintains non-negative available balance under all concurrent booking operations.",
            evidence=evidence.model_dump(),
        )

    def check_wallet_holds_bounded(self, agency_id: str) -> InvariantCheckResult:
        """
        Invariant 6.2: Active holds must not exceed funded balance.
        active_holds_minor <= funded_balance_minor.
        """
        wallet = self.oracle.get_wallet(agency_id)
        if not wallet:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_OVERDRAFT_PROTECTION,
                passed=False,
                intent_id=agency_id,
                expected="Wallet exists",
                actual="Wallet not found",
                title="Wallet Not Found",
                description=f"Wallet for agency '{agency_id}' not found.",
            )

        passed = wallet.active_holds_minor <= wallet.funded_balance_minor
        return InvariantCheckResult(
            invariant=InvariantType.WALLET_OVERDRAFT_PROTECTION,
            passed=passed,
            intent_id=agency_id,
            expected=f"active_holds <= funded_balance ({wallet.funded_balance_minor})",
            actual=f"active_holds = {wallet.active_holds_minor}",
            cwe="CWE-841: Improper Enforcement of Behavioral Workflow" if not passed else None,
            severity=Severity.HIGH if not passed else None,
            title="Wallet Holds Bounded by Funded Balance" if passed else "Wallet Holds Exceed Funded Balance",
            description="Active holds are strictly bounded by total funded balance." if passed else "Cumulative active holds exceed agency funded balance.",
            evidence=wallet.model_dump(mode="json"),
        )

    def check_wallet_total_accounting(self, agency_id: str) -> InvariantCheckResult:
        """
        Canonical Invariant 5.1 & Accounting Consistency:
        funded_balance == sum(deposits),
        total_captured == sum(captures),
        total_refunded == sum(refunds),
        available_balance == funded + total_refunded - total_captured - active_holds.
        """
        wallet = self.oracle.get_wallet(agency_id)
        txs = self.oracle.get_wallet_transactions(agency_id)
        if not wallet:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_BALANCE_DERIVATION,
                passed=False,
                intent_id=agency_id,
                expected="Wallet exists",
                actual="Wallet not found",
                title="Wallet Not Found",
                description=f"Wallet for agency '{agency_id}' not found.",
            )

        deposit_sum = sum(t.amount_minor for t in txs if t.transaction_type.value == "DEPOSIT")
        capture_sum = sum(t.amount_minor for t in txs if t.transaction_type.value == "CAPTURE")
        refund_sum = sum(t.amount_minor for t in txs if t.transaction_type.value == "REFUND")

        expected_avail = wallet.funded_balance_minor + wallet.total_refunded_minor - wallet.total_captured_minor - wallet.active_holds_minor

        passed = (
            (deposit_sum == wallet.funded_balance_minor)
            and (capture_sum == wallet.total_captured_minor)
            and (refund_sum == wallet.total_refunded_minor)
            and (wallet.available_balance_minor == expected_avail)
        )
        return InvariantCheckResult(
            invariant=InvariantType.WALLET_BALANCE_DERIVATION,
            passed=passed,
            intent_id=agency_id,
            expected=f"Deposits={wallet.funded_balance_minor}, Captures={wallet.total_captured_minor}, Refunds={wallet.total_refunded_minor}, Avail={expected_avail}",
            actual=f"Deposits={deposit_sum}, Captures={capture_sum}, Refunds={refund_sum}, Avail={wallet.available_balance_minor}",
            cwe="CWE-682: Incorrect Calculation" if not passed else None,
            severity=Severity.HIGH if not passed else None,
            title="Wallet Accounting Consistency Verified" if passed else "Wallet Transaction History Discrepancy",
            description="Wallet balances match transaction journal history perfectly." if passed else "Mismatch between wallet balance and transaction sums.",
            evidence={"wallet": wallet.model_dump(mode="json"), "tx_count": len(txs)},
        )

    def check_canonical_wallet_balance_derivation(self, agency_id: str) -> InvariantCheckResult:
        """
        Enforces Canonical Invariant 5.1 from MASTER_PLAN_V2.md:
        closing_balance = opening_balance + sum(signed_posted_ledger_entries)
        available_funds = closing_balance - active_holds >= 0.
        Pending holds are reservations, NOT posted debits.
        """
        wallet = self.oracle.get_wallet(agency_id)
        if not wallet:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_BALANCE_DERIVATION,
                passed=False,
                intent_id=agency_id,
                expected="Wallet exists",
                actual="None",
                title="Missing Agency Wallet",
                description=f"Wallet for agency '{agency_id}' not found.",
            )

        txs = self.oracle.get_wallet_transactions(agency_id)
        opening_balance_minor = wallet.funded_balance_minor

        # Signed posted ledger entries: Deposits (+), Refunds (+), Captures (-)
        # Note: Holds (HELD) do NOT post to closing ledger balance
        net_posted_movements = 0
        for tx in txs:
            t_type = tx.transaction_type.value if hasattr(tx.transaction_type, "value") else str(tx.transaction_type)
            if t_type == "CAPTURE":
                net_posted_movements -= tx.amount_minor
            elif t_type == "REFUND":
                net_posted_movements += tx.amount_minor

        derived_closing_balance = opening_balance_minor + net_posted_movements
        actual_closing_balance = wallet.funded_balance_minor + wallet.total_refunded_minor - wallet.total_captured_minor

        closing_matches = (derived_closing_balance == actual_closing_balance)

        expected_available = actual_closing_balance - wallet.active_holds_minor
        available_matches = (wallet.available_balance_minor == expected_available)
        available_non_negative = (wallet.available_balance_minor >= 0)

        passed = closing_matches and available_matches and available_non_negative

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_BALANCE_DERIVATION,
                passed=False,
                intent_id=agency_id,
                expected=f"closing={derived_closing_balance}, available={expected_available} (>=0)",
                actual=f"closing={actual_closing_balance}, available={wallet.available_balance_minor}",
                cwe="CWE-682: Incorrect Calculation",
                severity=Severity.CRITICAL,
                title="Wallet Balance Derivation Invariant Violation",
                description="Discrepancy detected between posted ledger events and reported wallet closing/available balances.",
                evidence={
                    "agency_id": agency_id,
                    "opening_balance_minor": opening_balance_minor,
                    "net_posted_movements": net_posted_movements,
                    "derived_closing_balance": derived_closing_balance,
                    "actual_closing_balance": actual_closing_balance,
                    "active_holds_minor": wallet.active_holds_minor,
                    "available_balance_minor": wallet.available_balance_minor,
                },
                remediation="Ensure closing balance is derived strictly from signed posted ledger events, with active holds tracked separately.",
            )

        return InvariantCheckResult(
            invariant=InvariantType.WALLET_BALANCE_DERIVATION,
            passed=True,
            intent_id=agency_id,
            expected=f"closing={derived_closing_balance}, available={expected_available}",
            actual=f"closing={actual_closing_balance}, available={wallet.available_balance_minor}",
            title="Wallet Balance Derivation Verified",
            description="Closing balance strictly matches signed posted ledger entries, and active holds correctly reduce available balance without mutating closing ledger.",
            evidence={
                "agency_id": agency_id,
                "opening_balance_minor": opening_balance_minor,
                "net_posted_movements": net_posted_movements,
                "closing_balance_minor": actual_closing_balance,
                "active_holds_minor": wallet.active_holds_minor,
                "available_balance_minor": wallet.available_balance_minor,
            },
        )

    def check_inventory_capacity(self, hotel_id: str, room_type: str) -> InvariantCheckResult:
        """
        Invariant 6.6: Hotel room inventory capacity constraint.
        allocated_units <= capacity.
        Emits CW-SEC-M2-202 on violation (TOCTOU last-room overbooking).
        """
        inv = self.oracle.get_hotel_inventory(hotel_id, room_type)
        if not inv:
            return InvariantCheckResult(
                invariant=InvariantType.HOTEL_INVENTORY_CAPACITY,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected="Inventory record exists",
                actual="Not found",
                title="Inventory Record Missing",
                description=f"Inventory for {hotel_id}/{room_type} not found.",
            )

        reservations = [r for r in self.oracle.get_all_supplier_reservations() if r.hotel_id == hotel_id and r.room_type == room_type and r.status != "CANCELLED"]
        res_ids = [r.supplier_reservation_id for r in reservations]
        passed = (inv.allocated_units <= inv.capacity) and (len(res_ids) <= inv.capacity)

        evidence = InventoryCapacityEvidence(
            finding_id="CW-SEC-M2-202",
            hotel_id=hotel_id,
            room_type=room_type,
            capacity=inv.capacity,
            allocated_units=inv.allocated_units,
            available_units=inv.available_units,
            reservations_created=res_ids,
            violation_detected=not passed,
            reproduction_instructions="Execute simultaneous booking requests from distinct agencies competing for the last remaining room.",
            remediation="Enforce atomic inventory deduction: UPDATE hotel_inventory SET available_units = available_units - 1 WHERE available_units > 0.",
        )
        evidence.diff_summary = evidence.format_report()

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.HOTEL_INVENTORY_CAPACITY,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected=f"allocated_units <= {inv.capacity} and confirmed_reservations <= {inv.capacity}",
                actual=f"allocated_units={inv.allocated_units}, confirmed_reservations={len(res_ids)}",
                cwe="CWE-362: Concurrent Execution using Shared Resource / CWE-675: Duplicate Operations on Resource",
                severity=Severity.CRITICAL,
                title="Hotel Inventory Overbooking via TOCTOU Race Condition",
                description=(
                    f"Room type '{room_type}' at hotel '{hotel_id}' was overbooked beyond capacity "
                    f"(Capacity: {inv.capacity}, Allocated: {inv.allocated_units}, Reservations: {len(res_ids)}). "
                    f"Concurrent requests both confirmed the last available room due to non-atomic inventory checks."
                ),
                evidence=evidence.model_dump(),
                remediation="Execute atomic inventory reservation via single SQL query with conditional WHERE available_units >= requested.",
            )

        return InvariantCheckResult(
            invariant=InvariantType.HOTEL_INVENTORY_CAPACITY,
            passed=True,
            intent_id=f"{hotel_id}_{room_type}",
            expected=f"allocated_units <= {inv.capacity}",
            actual=f"allocated_units={inv.allocated_units}, reservations={len(res_ids)}",
            title="Hotel Inventory Capacity Preserved",
            description="Hotel inventory allocations and reservations strictly respect capacity constraints.",
            evidence=evidence.model_dump(),
        )

    def check_inventory_non_negative(self, hotel_id: str, room_type: str) -> InvariantCheckResult:
        """
        Invariant 6.7: Available inventory non-negativity.
        available_units >= 0.
        """
        inv = self.oracle.get_hotel_inventory(hotel_id, room_type)
        if not inv:
            return InvariantCheckResult(
                invariant=InvariantType.HOTEL_INVENTORY_CAPACITY,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected="Inventory exists",
                actual="Not found",
                title="Inventory Record Missing",
                description=f"Inventory for {hotel_id}/{room_type} not found.",
            )

        passed = inv.available_units >= 0
        return InvariantCheckResult(
            invariant=InvariantType.HOTEL_INVENTORY_CAPACITY,
            passed=passed,
            intent_id=f"{hotel_id}_{room_type}",
            expected="available_units >= 0",
            actual=f"available_units = {inv.available_units}",
            cwe="CWE-682: Incorrect Calculation" if not passed else None,
            severity=Severity.HIGH if not passed else None,
            title="Inventory Non-Negativity Preserved" if passed else "Negative Inventory Detected",
            description="Available inventory units is non-negative." if passed else "Available inventory dropped below zero.",
            evidence=inv.model_dump(mode="json"),
        )

    def check_saga_state_consistency(self, booking_id: str) -> InvariantCheckResult:
        """
        Invariants 6.10 & 6.11: Distributed saga state consistency across wallet, supplier, and booking.
        - If booking CONFIRMED/VOUCHER: wallet hold MUST be CAPTURED and supplier reservation exists.
        - If booking FAILED: wallet hold MUST be RELEASED (never left in HELD or CAPTURED).
        - If booking SUPPLIER_UNKNOWN: wallet hold MUST be preserved in HELD (not debited, not leaked).
        Emits CW-SEC-M2-204 on violation.
        """
        booking = self.oracle.get_booking_record(booking_id)
        if not booking:
            return InvariantCheckResult(
                invariant=InvariantType.SAGA_COMPENSATION_CONSISTENCY,
                passed=False,
                intent_id=booking_id,
                expected="Booking exists",
                actual="Not found",
                title="Booking Record Not Found",
                description=f"Booking '{booking_id}' not found.",
            )

        if not booking.wallet_hold_id:
            # Not a wallet booking, invariant passes trivially
            return InvariantCheckResult(
                invariant=InvariantType.SAGA_COMPENSATION_CONSISTENCY,
                passed=True,
                intent_id=booking_id,
                expected="Wallet hold consistent",
                actual="N/A (Card booking)",
                title="Saga State Consistency Verified (Card Payment)",
                description="Card booking does not use wallet saga.",
            )

        holds = self.oracle.get_wallet_holds(booking.agency_id)
        hold = next((h for h in holds if h.hold_id == booking.wallet_hold_id), None)
        hold_status = hold.status.value if hold else "UNKNOWN"
        booking_status = booking.status.value

        violation = False
        detail = ""

        if booking_status in ["SUPPLIER_CONFIRMED", "VOUCHER_ISSUED"]:
            if hold_status != "CAPTURED":
                violation = True
                detail = f"Booking is '{booking_status}' but wallet hold '{booking.wallet_hold_id}' is '{hold_status}' (expected CAPTURED)."
        elif booking_status == "FAILED":
            if hold_status != "RELEASED":
                violation = True
                detail = f"Booking is 'FAILED' but wallet hold '{booking.wallet_hold_id}' was NOT released (status: '{hold_status}')."
        elif booking_status in ["SUPPLIER_UNKNOWN", "RECONCILIATION_REQUIRED"]:
            if hold_status != "HELD":
                violation = True
                detail = f"Booking is in ambiguous state '{booking_status}' but wallet hold is '{hold_status}' (expected HELD pending reconciliation)."

        evidence = SagaCompensationEvidence(
            finding_id="CW-SEC-M2-204",
            booking_id=booking_id,
            agency_id=booking.agency_id,
            wallet_hold_status=hold_status,
            supplier_status=booking.primary_supplier_reservation_id or "NONE",
            booking_status=booking_status,
            violation_detected=violation,
            details=detail or "Saga state consistent across wallet and booking lifecycle.",
        )

        if violation:
            return InvariantCheckResult(
                invariant=InvariantType.SAGA_COMPENSATION_CONSISTENCY,
                passed=False,
                intent_id=booking_id,
                expected="Saga state consistency across wallet hold and booking status",
                actual=detail,
                cwe="CWE-841: Improper Enforcement of Behavioral Workflow",
                severity=Severity.HIGH,
                title="Distributed Saga Compensation Failure",
                description=f"Inconsistency in distributed transaction saga for booking {booking_id}: {detail}",
                evidence=evidence.model_dump(),
                remediation="Ensure saga compensation handlers guarantee wallet hold release upon supplier rejection or capture upon confirmation.",
            )

        return InvariantCheckResult(
            invariant=InvariantType.SAGA_COMPENSATION_CONSISTENCY,
            passed=True,
            intent_id=booking_id,
            expected="Consistent saga state",
            actual=f"Booking={booking_status}, Hold={hold_status}",
            title="Distributed Saga Consistency Verified",
            description="Perfect correlation between booking lifecycle status and agency wallet hold state.",
            evidence=evidence.model_dump(),
        )

    def check_cross_agency_wallet_isolation(self, agency_alpha: str, agency_beta: str) -> InvariantCheckResult:
        """
        Invariant 6.13: Strict cross-agency wallet isolation.
        No wallet transaction or hold belonging to agency_alpha may reference or modify agency_beta's wallet.
        Emits CW-SEC-M2-205 on violation.
        """
        holds_alpha = self.oracle.get_wallet_holds(agency_alpha)
        txs_alpha = self.oracle.get_wallet_transactions(agency_alpha)

        # Check that no holds for alpha point to beta's wallet_id
        cross_holds = [h for h in holds_alpha if h.wallet_id == f"wallet_{agency_beta}" or h.agency_id != agency_alpha]
        cross_txs = [t for t in txs_alpha if t.wallet_id == f"wallet_{agency_beta}" or t.agency_id != agency_alpha]

        violation = len(cross_holds) > 0 or len(cross_txs) > 0
        if violation:
            return InvariantCheckResult(
                invariant=InvariantType.CROSS_AGENCY_WALLET_ISOLATION,
                passed=False,
                intent_id=f"{agency_alpha}_{agency_beta}",
                expected="Zero cross-agency wallet holds or transactions",
                actual=f"Found {len(cross_holds)} cross holds and {len(cross_txs)} cross transactions",
                cwe="CWE-284: Improper Access Control / CWE-639: IDOR",
                severity=Severity.CRITICAL,
                title="Cross-Agency Wallet Isolation Violation (CW-SEC-M2-205)",
                description=f"Agency '{agency_alpha}' has operations or holds targeting agency '{agency_beta}' wallet.",
                remediation="Enforce strict authorization checks ensuring authenticated agency_id matches requested wallet.",
            )

        return InvariantCheckResult(
            invariant=InvariantType.CROSS_AGENCY_WALLET_ISOLATION,
            passed=True,
            intent_id=f"{agency_alpha}_{agency_beta}",
            expected="Zero cross-agency wallet operations",
            actual="Zero cross-agency wallet operations detected",
            title="Cross-Agency Wallet Isolation Verified",
            description=f"Complete separation and authorization barriers verified between '{agency_alpha}' and '{agency_beta}'.",
        )

    def check_wallet_hold_exclusivity(self, agency_id: str) -> InvariantCheckResult:
        """
        Invariant 6.4: Wallet Hold Exclusivity.
        No hold may simultaneously exist in multiple states. Transactions for a hold
        cannot contain both CAPTURE and RELEASE.
        """
        holds = self.oracle.get_wallet_holds(agency_id)
        txs = self.oracle.get_wallet_transactions(agency_id)

        violations = []
        for hold in holds:
            h_txs = [t for t in txs if t.hold_id == hold.hold_id]
            types = set(t.transaction_type.value for t in h_txs)
            if "CAPTURE" in types and "RELEASE" in types:
                violations.append(f"Hold '{hold.hold_id}' has both CAPTURE and RELEASE transactions.")

        passed = len(violations) == 0
        return InvariantCheckResult(
            invariant=InvariantType.WALLET_HOLD_EXCLUSIVITY,
            passed=passed,
            intent_id=agency_id,
            expected="Hold transactions mutually exclusive (no hold has both CAPTURE and RELEASE)",
            actual="Holds are mutually exclusive" if passed else "; ".join(violations),
            cwe="CWE-841: Improper Enforcement of Behavioral Workflow" if not passed else None,
            severity=Severity.HIGH if not passed else None,
            title="Wallet Hold Exclusivity Verified" if passed else "Wallet Hold Exclusivity Violation",
            description="Wallet holds strictly enforce mutual exclusivity between capture and release." if passed else "Hold state conflict detected.",
            evidence={"holds_count": len(holds), "violations": violations},
        )

    def check_wallet_refund_conservation(self, agency_id: str) -> InvariantCheckResult:
        """
        Invariant 6.5: Wallet Refund Conservation.
        Cumulative wallet refunds must not exceed gross captures, and total_refunded_minor
        must equal the sum of REFUND transactions.
        """
        wallet = self.oracle.get_wallet(agency_id)
        if not wallet:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_REFUND_CONSERVATION,
                passed=False,
                intent_id=agency_id,
                expected="Wallet exists",
                actual="Wallet not found",
                title="Wallet Not Found",
                description=f"Wallet for agency '{agency_id}' not found.",
            )

        txs = self.oracle.get_wallet_transactions(agency_id)
        refund_tx_sum = sum(t.amount_minor for t in txs if t.transaction_type.value == "REFUND")

        bounded = wallet.total_refunded_minor <= wallet.total_captured_minor
        reconciled = wallet.total_refunded_minor == refund_tx_sum
        passed = bounded and reconciled

        evidence = {
            "total_captured_minor": wallet.total_captured_minor,
            "total_refunded_minor": wallet.total_refunded_minor,
            "refund_tx_sum": refund_tx_sum,
            "bounded": bounded,
            "reconciled": reconciled,
        }

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.WALLET_REFUND_CONSERVATION,
                passed=False,
                intent_id=agency_id,
                expected="total_refunded_minor <= total_captured_minor AND total_refunded_minor == sum(refund_txs)",
                actual=f"total_refunded={wallet.total_refunded_minor}, total_captured={wallet.total_captured_minor}, refund_tx_sum={refund_tx_sum}",
                cwe="CWE-682: Incorrect Calculation",
                severity=Severity.HIGH,
                title="Wallet Refund Conservation Violation",
                description="Cumulative wallet refunds exceeded gross captures or diverged from transaction journal.",
                evidence=evidence,
            )

        return InvariantCheckResult(
            invariant=InvariantType.WALLET_REFUND_CONSERVATION,
            passed=True,
            intent_id=agency_id,
            expected="total_refunded_minor <= total_captured_minor AND total_refunded_minor == sum(refund_txs)",
            actual=f"Refunded={wallet.total_refunded_minor} <= Captured={wallet.total_captured_minor}",
            title="Wallet Refund Conservation Verified",
            description="Wallet refunds are bounded by gross captures and reconcile with transaction journal.",
            evidence=evidence,
        )

    def check_inventory_allocation_conservation(self, hotel_id: str, room_type: str) -> InvariantCheckResult:
        """
        Invariant 6.8: Inventory Allocation Conservation.
        capacity == available_units + allocated_units, and allocated_units == count(active reservations).
        """
        inv = self.oracle.get_hotel_inventory(hotel_id, room_type)
        if not inv:
            return InvariantCheckResult(
                invariant=InvariantType.INVENTORY_ALLOCATION_CONSERVATION,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected="Inventory exists",
                actual="Not found",
                title="Inventory Record Missing",
                description=f"Inventory for {hotel_id}/{room_type} not found.",
            )

        active_res = [
            r for r in self.oracle.get_all_supplier_reservations()
            if r.hotel_id == hotel_id and r.room_type == room_type and r.status != "CANCELLED"
        ]

        sum_ok = (inv.capacity == inv.available_units + inv.allocated_units)
        alloc_ok = (inv.allocated_units == len(active_res))
        passed = sum_ok and alloc_ok

        evidence = {
            "capacity": inv.capacity,
            "available_units": inv.available_units,
            "allocated_units": inv.allocated_units,
            "active_reservations_count": len(active_res),
        }

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.INVENTORY_ALLOCATION_CONSERVATION,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected=f"capacity ({inv.capacity}) == available + allocated AND allocated == active_res ({len(active_res)})",
                actual=f"available={inv.available_units}, allocated={inv.allocated_units}, active_res={len(active_res)}",
                cwe="CWE-682: Incorrect Calculation",
                severity=Severity.HIGH,
                title="Inventory Allocation Conservation Discrepancy",
                description="Discrepancy detected between physical capacity, available units, and allocated active reservations.",
                evidence=evidence,
            )

        return InvariantCheckResult(
            invariant=InvariantType.INVENTORY_ALLOCATION_CONSERVATION,
            passed=True,
            intent_id=f"{hotel_id}_{room_type}",
            expected="capacity == available + allocated == active_res",
            actual=f"Capacity {inv.capacity} = {inv.available_units} + {inv.allocated_units} (active res: {len(active_res)})",
            title="Inventory Allocation Conservation Verified",
            description="Perfect conservation of room inventory capacity, availability, and active reservations.",
            evidence=evidence,
        )

    def check_cancellation_inventory_conservation(self, hotel_id: str, room_type: str) -> InvariantCheckResult:
        """
        Invariant 6.9: Cancellation Inventory Restoration Conservation.
        Cancelled reservations must restore exactly 1 available unit and transition atomically.
        """
        inv = self.oracle.get_hotel_inventory(hotel_id, room_type)
        if not inv:
            return InvariantCheckResult(
                invariant=InvariantType.CANCELLATION_INVENTORY_CONSERVATION,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected="Inventory exists",
                actual="Not found",
                title="Inventory Record Missing",
                description=f"Inventory for {hotel_id}/{room_type} not found.",
            )

        all_res = [r for r in self.oracle.get_all_supplier_reservations() if r.hotel_id == hotel_id and r.room_type == room_type]
        active_res = [r for r in all_res if r.status != "CANCELLED"]

        passed = (inv.capacity - len(active_res) == inv.available_units)
        evidence = {
            "capacity": inv.capacity,
            "available_units": inv.available_units,
            "total_reservations": len(all_res),
            "active_reservations": len(active_res),
            "cancelled_reservations": len(all_res) - len(active_res),
        }

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.CANCELLATION_INVENTORY_CONSERVATION,
                passed=False,
                intent_id=f"{hotel_id}_{room_type}",
                expected=f"available_units ({inv.available_units}) == capacity ({inv.capacity}) - active_res ({len(active_res)})",
                actual=f"available_units={inv.available_units}, expected={inv.capacity - len(active_res)}",
                cwe="CWE-362: Race Condition / CWE-682: Incorrect Calculation",
                severity=Severity.HIGH,
                title="Cancellation Inventory Restoration Discrepancy",
                description="Inventory was not restored exactly 1:1 upon cancellation or was double-restored.",
                evidence=evidence,
            )

        return InvariantCheckResult(
            invariant=InvariantType.CANCELLATION_INVENTORY_CONSERVATION,
            passed=True,
            intent_id=f"{hotel_id}_{room_type}",
            expected="available_units == capacity - active_res",
            actual=f"Available units ({inv.available_units}) matches capacity ({inv.capacity}) minus active reservations ({len(active_res)})",
            title="Cancellation Inventory Restoration Verified",
            description="All cancellations restored inventory accurately without loss or duplicate restoration.",
            evidence=evidence,
        )

    def check_ambiguous_timeout_hold_preservation(self, booking_id: str) -> InvariantCheckResult:
        """
        Invariant 6.12: Ambiguous Timeout Hold Preservation.
        When a booking enters SUPPLIER_UNKNOWN, the wallet hold must remain HELD
        pending reconciliation, never prematurely debited (CAPTURED) or prematurely released.
        """
        booking = self.oracle.get_booking_record(booking_id)
        if not booking:
            return InvariantCheckResult(
                invariant=InvariantType.AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION,
                passed=False,
                intent_id=booking_id,
                expected="Booking exists",
                actual="Not found",
                title="Booking Not Found",
                description=f"Booking '{booking_id}' not found.",
            )

        if not booking.wallet_hold_id:
            return InvariantCheckResult(
                invariant=InvariantType.AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION,
                passed=True,
                intent_id=booking_id,
                expected="Wallet hold exists",
                actual="N/A (Card booking)",
                title="Not Applicable",
                description="Card booking does not use wallet holds.",
            )

        holds = self.oracle.get_wallet_holds(booking.agency_id)
        hold = next((h for h in holds if h.hold_id == booking.wallet_hold_id), None)
        hold_status = hold.status.value if hold else "UNKNOWN"

        is_ambiguous = booking.status.value in ["SUPPLIER_UNKNOWN", "RECONCILIATION_REQUIRED"]
        passed = (hold_status == "HELD") if is_ambiguous else True

        if is_ambiguous and not passed:
            return InvariantCheckResult(
                invariant=InvariantType.AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION,
                passed=False,
                intent_id=booking_id,
                expected="wallet_hold.status == 'HELD'",
                actual=f"wallet_hold.status == '{hold_status}'",
                cwe="CWE-841: Workflow Violation",
                severity=Severity.HIGH,
                title="Ambiguous Timeout Hold Prematurely Mutated",
                description=f"Booking in status '{booking.status.value}' had its wallet hold mutated to '{hold_status}' before reconciliation.",
                evidence={"booking_id": booking_id, "booking_status": booking.status.value, "hold_status": hold_status},
            )

        return InvariantCheckResult(
            invariant=InvariantType.AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION,
            passed=True,
            intent_id=booking_id,
            expected="wallet_hold.status == 'HELD' for ambiguous bookings",
            actual=f"Hold status '{hold_status}' for booking status '{booking.status.value}'",
            title="Ambiguous Timeout Hold Preserved",
            description="Wallet hold was preserved in HELD state without premature release or debit.",
            evidence={"booking_id": booking_id, "booking_status": booking.status.value, "hold_status": hold_status},
        )

    def check_voucher_entitlement(
        self,
        booking_id: str,
        booking_override: Optional[AggregatorBookingRecord] = None,
        supplier_reservation_override: Optional[SupplierReservation] = None,
    ) -> InvariantCheckResult:
        """
        Enforces Invariant 5.4 from MASTER_PLAN_V2.md:
        voucher_issued(B) => supplier_confirmed(B) AND payment_or_wallet_obligation_satisfied(B)

        1. Inspects booking record and voucher issuance state.
        2. Verifies upstream supplier reservation is genuinely CONFIRMED in supplier database.
        3. For WALLET: verifies wallet hold is CAPTURED and ledger/transaction capture entry exists.
        4. For CARD: verifies payment status is CAPTURED in payment provider database.
        5. Enforces agency isolation (booking agency matches wallet/payment agency).
        """
        if booking_override is not None:
            booking = booking_override
        else:
            booking = self.oracle.get_booking_record(booking_id)

        if not booking:
            return InvariantCheckResult(
                invariant=InvariantType.VOUCHER_ENTITLEMENT,
                passed=False,
                intent_id=booking_id,
                expected="Booking record exists",
                actual="None",
                cwe="CWE-841: Improper Enforcement of Behavioral Workflow",
                severity=Severity.HIGH,
                title="Booking Record Missing for Voucher Entitlement Check",
                description=f"No booking record found for booking ID '{booking_id}'.",
                evidence={"booking_id": booking_id},
            )

        voucher_issued = bool(booking.voucher_id) or booking.status == TransactionStatus.VOUCHER_ISSUED

        # If no voucher was issued, the implication P => Q is vacuously satisfied
        if not voucher_issued:
            return InvariantCheckResult(
                invariant=InvariantType.VOUCHER_ENTITLEMENT,
                passed=True,
                intent_id=booking.intent_id,
                expected="voucher_issued(B) => supplier_confirmed(B) AND obligation_satisfied(B)",
                actual="No voucher issued; entitlement implication satisfied",
                title="Voucher Entitlement Preserved (No Voucher Issued)",
                description=f"Booking '{booking_id}' has not issued a voucher (status '{booking.status.value}').",
                evidence={"booking_id": booking_id, "status": booking.status.value, "voucher_id": booking.voucher_id},
            )

        details_list = []

        # 0. Verify Voucher Genuineness and Exact Booking Linkage against Vouchers Ground Truth Table
        voucher_valid = True
        if booking.status == TransactionStatus.VOUCHER_ISSUED and not booking.voucher_id:
            voucher_valid = False
            details_list.append("Voucher record is missing despite VOUCHER_ISSUED status")
        elif booking.voucher_id:
            voucher_rec = self.oracle.get_voucher(booking.voucher_id)
            if voucher_rec is None:
                voucher_valid = False
                details_list.append(f"Voucher ID '{booking.voucher_id}' does not exist in vouchers table")
            else:
                status_val = voucher_rec.status.value if hasattr(voucher_rec.status, "value") else str(voucher_rec.status)
                if status_val != VoucherStatus.ISSUED.value:
                    voucher_valid = False
                    details_list.append(f"Voucher record '{booking.voucher_id}' is not in ISSUED state (status: '{status_val}')")
                elif voucher_rec.booking_id != booking.booking_id:
                    voucher_valid = False
                    details_list.append(f"Voucher record '{booking.voucher_id}' belongs to booking '{voucher_rec.booking_id}', not '{booking.booking_id}'")
                elif voucher_rec.agency_id != booking.agency_id:
                    voucher_valid = False
                    details_list.append(f"Voucher record '{booking.voucher_id}' belongs to agency '{voucher_rec.agency_id}', not '{booking.agency_id}'")
                else:
                    bookings_with_voucher = self.oracle.get_bookings_by_voucher(booking.voucher_id)
                    if len(bookings_with_voucher) > 1:
                        voucher_valid = False
                        other_ids = [b.booking_id for b in bookings_with_voucher if b.booking_id != booking.booking_id]
                        details_list.append(f"Voucher ID '{booking.voucher_id}' is reused across multiple bookings ({other_ids})")

        # 1. Verify Supplier Confirmation in database ground truth
        sup_res = None
        if supplier_reservation_override is not None:
            sup_res = supplier_reservation_override
        elif booking.primary_supplier_reservation_id:
            sup_res = self.oracle.get_supplier_reservation(booking.primary_supplier_reservation_id)
        elif booking.intent_id:
            res_list = self.oracle.get_supplier_reservations(booking.intent_id)
            sup_res = res_list[0] if res_list else None

        supplier_res_id = sup_res.supplier_reservation_id if sup_res else (booking.primary_supplier_reservation_id or "NONE")
        supplier_status_val = getattr(sup_res, "status", None) if sup_res else "MISSING"

        supplier_confirmed = False
        if not sup_res:
            details_list.append(f"Supplier reservation '{supplier_res_id}' is not confirmed (status: '{supplier_status_val}')")
        elif sup_res.intent_id != booking.intent_id:
            details_list.append(
                f"Supplier reservation '{sup_res.supplier_reservation_id}' belongs to different intent "
                f"'{sup_res.intent_id}', not booking intent '{booking.intent_id}'"
            )
        elif getattr(sup_res, "status", None) != "CONFIRMED":
            details_list.append(f"Supplier reservation '{sup_res.supplier_reservation_id}' is not confirmed (status: '{sup_res.status}')")
        else:
            supplier_confirmed = True

        # 2. Verify Payment / Wallet Obligation with exact minor units and currency
        payment_method = (booking.payment_method or "CARD").upper()
        wallet_captured = False
        card_captured = False
        wallet_hold_status = "NONE"
        payment_status_val = "NONE"
        obligation_satisfied = False
        agency_isolated = True

        booking_payable_minor = booking.amount_minor if booking.amount_minor is not None else int(booking.amount * 100)
        booking_currency = (booking.currency or "INR").upper()

        if payment_method == "WALLET":
            matching_hold = None
            if booking.wallet_hold_id:
                matching_hold = self.oracle.aggregator_db.get_wallet_hold(booking.wallet_hold_id)
            if not matching_hold:
                holds = self.oracle.get_wallet_holds(booking.agency_id)
                matching_hold = next((h for h in holds if h.booking_id == booking_id), None)

            if not matching_hold:
                wallet_hold_status = "MISSING_HOLD"
                details_list.append("Wallet hold record not found for booking")
            else:
                wallet_hold_status = matching_hold.status.value if hasattr(matching_hold.status, "value") else str(matching_hold.status)
                
                # Check 1: Must belong to exact booking
                if matching_hold.booking_id != booking.booking_id:
                    details_list.append(
                        f"Wallet hold '{matching_hold.hold_id}' belongs to different booking "
                        f"'{matching_hold.booking_id}', not '{booking.booking_id}'"
                    )
                # Check 2: Agency tenant isolation
                elif matching_hold.agency_id != booking.agency_id:
                    agency_isolated = False
                    details_list.append(
                        f"Cross-agency evidence mismatch detected: Wallet hold belongs to agency '{matching_hold.agency_id}', "
                        f"not booking agency '{booking.agency_id}'"
                    )
                # Check 3: Currency comparison
                elif (matching_hold.currency or "").upper() != booking_currency:
                    details_list.append(
                        f"Wallet hold currency '{matching_hold.currency}' differs from booking currency '{booking_currency}'"
                    )
                # Check 4: Exact integer minor unit amount comparison
                elif matching_hold.amount_minor < booking_payable_minor:
                    details_list.append(
                        f"Wallet hold amount ({matching_hold.amount_minor} minor units) is below booking payable "
                        f"amount ({booking_payable_minor} minor units)"
                    )
                # Check 5: Status must be CAPTURED
                elif wallet_hold_status != WalletHoldStatus.CAPTURED.value:
                    details_list.append(f"Wallet hold is not captured (status: '{wallet_hold_status}')")
                else:
                    wallet_captured = True
                    obligation_satisfied = True

        elif payment_method == "CARD":
            pay_rec = None
            if booking.payment_id:
                pay_rec = self.oracle.get_payment_record(booking.payment_id)
            if not pay_rec and hasattr(self.oracle.payment_db, "get_payment_by_booking"):
                pay_rec = self.oracle.payment_db.get_payment_by_booking(booking_id)
            elif not pay_rec and hasattr(self.oracle.payment_db, "get_payments_by_booking"):
                payments = self.oracle.payment_db.get_payments_by_booking(booking_id)
                pay_rec = payments[0] if payments else None

            if not pay_rec:
                payment_status_val = "MISSING_PAYMENT_RECORD"
                details_list.append("Card payment record not found for booking")
            else:
                payment_status_val = pay_rec.status.value if hasattr(pay_rec.status, "value") else str(pay_rec.status)

                # Check 1: Must belong to exact booking
                if pay_rec.booking_id != booking.booking_id:
                    details_list.append(
                        f"Card payment '{pay_rec.payment_id}' belongs to different booking "
                        f"'{pay_rec.booking_id}', not '{booking.booking_id}'"
                    )
                # Check 2: Agency tenant isolation
                elif pay_rec.agency_id != booking.agency_id:
                    agency_isolated = False
                    details_list.append(
                        f"Cross-agency evidence mismatch detected: Card payment belongs to agency '{pay_rec.agency_id}', "
                        f"not booking agency '{booking.agency_id}'"
                    )
                # Check 3: Currency comparison
                elif (pay_rec.currency or "").upper() != booking_currency:
                    details_list.append(
                        f"Card payment currency '{pay_rec.currency}' differs from booking currency '{booking_currency}'"
                    )
                # Check 4: Exact integer minor unit amount comparison
                elif pay_rec.amount_minor < booking_payable_minor:
                    details_list.append(
                        f"Card payment amount ({pay_rec.amount_minor} minor units) is below booking payable "
                        f"amount ({booking_payable_minor} minor units)"
                    )
                # Check 5: Status must be CAPTURED
                elif payment_status_val != PaymentStatus.CAPTURED.value:
                    details_list.append(f"Card payment is not captured (status: '{payment_status_val}')")
                else:
                    card_captured = True
                    obligation_satisfied = True
        else:
            details_list.append(f"Unsupported payment method '{payment_method}'")

        passed = voucher_valid and supplier_confirmed and obligation_satisfied and agency_isolated

        details_str = "; ".join(details_list) if details_list else "Supplier confirmed and payment obligation fully satisfied"

        evidence = VoucherEntitlementEvidence(
            booking_id=booking_id,
            agency_id=booking.agency_id,
            voucher_id=booking.voucher_id,
            booking_status=booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            payment_method=payment_method,
            supplier_reservation_id=supplier_res_id,
            supplier_status=supplier_status_val,
            payment_status=payment_status_val,
            wallet_hold_status=wallet_hold_status,
            wallet_captured=wallet_captured,
            card_captured=card_captured,
            violation_detected=not passed,
            details=details_str,
        )

        if not passed:
            return InvariantCheckResult(
                invariant=InvariantType.VOUCHER_ENTITLEMENT,
                passed=False,
                intent_id=booking.intent_id,
                expected="voucher_issued(B) => supplier_confirmed(B) AND obligation_satisfied(B)",
                actual=details_str,
                cwe="CWE-841: Improper Enforcement of Behavioral Workflow / CWE-670: Always-Incorrect Control Flow Implementation",
                severity=Severity.CRITICAL,
                title="Voucher Issued Without Valid Supplier Confirmation or Payment Fulfillment",
                description=(
                    f"A travel voucher ({booking.voucher_id}) was issued for booking '{booking_id}', "
                    f"but precondition invariants were violated: {details_str}."
                ),
                evidence=evidence.model_dump(),
                remediation=(
                    "Enforce strict gate precondition: voucher_id may only be generated and returned after "
                    "both supplier_confirmed status is verified and payment/wallet obligation is atomically captured."
                ),
            )

        return InvariantCheckResult(
            invariant=InvariantType.VOUCHER_ENTITLEMENT,
            passed=True,
            intent_id=booking.intent_id,
            expected="voucher_issued(B) => supplier_confirmed(B) AND obligation_satisfied(B)",
            actual="Supplier confirmed and obligation captured",
            title="Ticket / Voucher Entitlement Invariant Preserved",
            description="Voucher was issued with genuine upstream supplier confirmation and verified payment capture.",
            evidence=evidence.model_dump(),
        )


invariant_engine = InvariantEngine()


def get_invariant_engine() -> InvariantEngine:
    return invariant_engine

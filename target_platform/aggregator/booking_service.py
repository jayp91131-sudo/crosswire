"""
Aggregator Booking Orchestration Service.
Implements the canonical hotel transaction saga:
SEARCH/QUOTE -> HOLD -> BOOKING -> PAYMENT -> SUPPLIER CONFIRMATION -> VOUCHER -> CANCELLATION -> REFUND.

Key features:
- Explicit state transitions and validation
- Minor units exact monetary representation (zero IEEE-754 drift)
- Balanced double-entry accounting batches (Sum(Debits) == Sum(Credits))
- Database-level atomic refund reservation with conditional UPDATE
- Payment authorization and capture via synthetic PaymentClient
- Cancellation penalty calculation and bounded refund entitlement enforcement
- Vulnerable blind retry upon HTTP timeout (spawns duplicate reservations)
- Fixed idempotent recovery
- Safe fallback for unsupported legacy providers
- Agency tenant isolation and idempotency conflict detection
"""

import os
import uuid
import asyncio
import hashlib
from typing import Optional, List
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from engine.event_collector import get_active_collector
from target_platform.money import to_minor_units, from_minor_units, round_minor_units
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    SupplierBookingRequest,
    AggregatorMode,
    QuoteRequest,
    QuoteResponse,
    HoldRequest,
    HoldResponse,
    PaymentAuthorizationRequest,
    PaymentCaptureRequest,
    PaymentTransactionRecord,
    PaymentStatus,
    CancellationRequest,
    CancellationResponse,
    RefundRequest,
    RefundRecord,
    RefundResponse,
    RefundAuthorizationRequest,
    LedgerEntry,
    LedgerDirection,
    LedgerEntryType,
    WebhookPayload,
    WebhookProcessingResponse,
    DEFAULT_WEBHOOK_SECRET,
    verify_webhook_signature,
    compute_webhook_signature,
    get_provider_registry,
    ProviderSecretRegistry,
    UnregisteredProviderError,
    WebhookTimestampError,
    StaleWebhookEventError,
    FutureWebhookEventError,
    validate_timestamp_freshness,
    MAX_CLOCK_SKEW_SECONDS,
    InsufficientWalletFundsError,
    RoomSoldOutError,
    WalletHoldNotFoundError,
    VoucherRecord,
    VoucherStatus,
    WalletIsolationError,
    AggregatorIdempotencyConflictError,
    AggregatorFencingError,
    BookingClaimStatus,
    BookingOperationClaimRecord,
    WalletHoldStatus,
)
from target_platform.aggregator.database import (
    get_aggregator_db,
    IdempotencyPayloadConflictError,
    WebhookPayloadConflictError,
    InvalidWebhookSignatureError,
    InvalidStateTransitionError,
    ExcessiveRefundError,
)
from target_platform.aggregator.supplier_client import (
    SupplierClient,
    AggregatorSupplierTimeoutError,
    AggregatorSupplierUnsupportedError,
    AggregatorSupplierSoldOutError,
)
from target_platform.aggregator.payment_client import (
    PaymentClient,
    AggregatorPaymentError,
    AggregatorPaymentConflictError,
)


class AggregatorBookingService:
    def __init__(
        self,
        supplier_client: Optional[SupplierClient] = None,
        payment_client: Optional[PaymentClient] = None,
    ):
        self.db = get_aggregator_db()
        self.supplier_client = supplier_client or SupplierClient()
        self.payment_client = payment_client or PaymentClient()
        self.mode: AggregatorMode = AggregatorMode.VULNERABLE
        self.webhook_mode: AggregatorMode = AggregatorMode.FIXED
        self.wallet_mode: AggregatorMode = AggregatorMode.FIXED
        self.provider_registry: ProviderSecretRegistry = get_provider_registry()
        self.max_clock_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS
        self.clock_func = None
        self.owner_token: str = f"worker_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self.current_fencing_token: int = 1

    def set_mode(self, mode: AggregatorMode):
        self.mode = mode

    def set_webhook_mode(self, mode: AggregatorMode):
        self.webhook_mode = mode

    def set_wallet_mode(self, mode: AggregatorMode):
        self.wallet_mode = mode

    def _persist_voucher_if_present(self, record: AggregatorBookingRecord):
        if record.voucher_id:
            existing = self.db.get_voucher(record.voucher_id)
            if not existing:
                self.db.save_voucher(
                    VoucherRecord(
                        voucher_id=record.voucher_id,
                        booking_id=record.booking_id,
                        agency_id=record.agency_id,
                        status=VoucherStatus.ISSUED,
                        issued_at=datetime.utcnow(),
                    )
                )

    def set_clock_func(self, clock_func):
        self.clock_func = clock_func

    def _compute_webhook_payload_hash(self, payload: WebhookPayload) -> str:
        d = payload.model_dump(mode="json")
        keys = sorted(k for k in d.keys() if k != "signature")
        canonical = "&".join(f"{k}={d[k]}" for k in keys)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def authorize_refund(
        self,
        booking_id: str,
        refund_id: str,
        amount_minor: int,
        currency: str = "INR",
        payment_id: Optional[str] = None,
        agency_id: Optional[str] = None,
        reason: str = "Authorized refund instruction",
        idempotency_key: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> RefundRecord:
        """Registers an authorized refund instruction in the aggregator database (CW-REV-M2-002)."""
        return self.db.authorize_refund(
            booking_id=booking_id,
            refund_id=refund_id,
            amount_minor=amount_minor,
            currency=currency,
            payment_id=payment_id,
            agency_id=agency_id,
            reason=reason,
            idempotency_key=idempotency_key,
            provider=provider,
        )

    async def process_webhook(
        self, payload: WebhookPayload, signature: Optional[str] = None
    ) -> WebhookProcessingResponse:
        """
        Processes incoming refund webhook from synthetic payment provider.
        Supports both VULNERABLE and FIXED configurations (Milestone 2.1).
        Enforces provider-specific authentication (CW-REV-M2-001) and timestamp freshness (CW-REV-M2-005).
        """
        if not payload.event_id or not str(payload.event_id).strip():
            raise ValueError("Missing required event_id in webhook payload")

        payload_dict = payload.model_dump(mode="json")
        payload_hash = self._compute_webhook_payload_hash(payload)
        sig = signature or payload.signature

        # 1. Provider-Specific Authentication (CW-REV-M2-001)
        provider_id = payload.provider_id
        if not self.provider_registry.is_registered(provider_id):
            raise UnregisteredProviderError(
                f"Webhook provider '{provider_id}' is not registered in the provider registry"
            )

        provider_secret = self.provider_registry.get_secret(provider_id)
        if not verify_webhook_signature(provider_secret, payload_dict, sig):
            raise InvalidWebhookSignatureError(
                f"Invalid HMAC-SHA256 webhook signature for provider '{provider_id}'"
            )

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="PAYMENT",
                event_type="REQUEST_DISPATCHED",
                event_status="INFO",
                title="Payment Provider Webhook Delivery",
                detail=f"Delivered signed refund webhook event '{payload.event_id}' for {payload.amount_minor / 100:,.0f} {payload.currency}",
                source_component="PaymentService",
                metadata={"event_id": payload.event_id, "amount_minor": payload.amount_minor, "provider_id": provider_id},
                evidence_references={"event_id": payload.event_id, "booking_id": payload.booking_id}
            )

        # 2. Check if this is an idempotent replay of an already-processed event
        existing = self.db.get_webhook_event(payload.event_id, provider=provider_id)
        if existing:
            # Replays of already-processed events bypass timestamp freshness check (CW-REV-M2-005)
            if existing.payload_hash != payload_hash:
                raise WebhookPayloadConflictError(
                    f"Webhook event '{payload.event_id}' conflict for provider '{provider_id}': payload hash mismatch"
                )
            if self.webhook_mode == AggregatorMode.VULNERABLE:
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="FAULT_TRIGGERED",
                        event_status="CRITICAL",
                        title="Duplicate Webhook Delivery Processed Without Deduplication",
                        detail=f"Vulnerable mode lacks idempotency locking; duplicate ledger mutation applied for replayed event '{payload.event_id}'",
                        source_component="AggregatorBookingService",
                        metadata={"event_id": payload.event_id, "amount_minor": payload.amount_minor},
                        evidence_references={"event_id": payload.event_id, "booking_id": payload.booking_id, "table": "ledger_entries"}
                    )
                return self.db.process_webhook_event_vulnerable(payload, payload_hash)
            else:
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="RETRY_RECONCILE",
                        event_status="SUCCESS",
                        title="Atomic Idempotency Deduplication Enforced",
                        detail=f"Delivery matched processed event ID '{payload.event_id}' in SQLite; acknowledged with HTTP 200 without duplicate ledger mutations",
                        source_component="AggregatorBookingService",
                        metadata={"event_id": payload.event_id, "deduplicated": True},
                        evidence_references={"event_id": payload.event_id, "booking_id": payload.booking_id, "table": "processed_webhook_events"}
                    )
                return self.db.process_webhook_event_fixed(payload, payload_hash)

        # 3. Timestamp Freshness for Unseen Events (CW-REV-M2-005)
        validate_timestamp_freshness(
            payload.created_at,
            max_clock_skew_seconds=self.max_clock_skew_seconds,
            clock_func=self.clock_func,
        )

        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="SUPPLIER_COMMITTED",
                event_status="INFO",
                title="Refund Webhook Processed & Ledger Credited",
                detail=f"Processed initial refund event '{payload.event_id}'; credited {payload.amount_minor / 100:,.0f} {payload.currency} to ledger",
                source_component="AggregatorBookingService",
                metadata={"event_id": payload.event_id, "amount_minor": payload.amount_minor},
                evidence_references={"event_id": payload.event_id, "booking_id": payload.booking_id, "table": "ledger_entries"}
            )

        if self.webhook_mode == AggregatorMode.VULNERABLE:
            # Vulnerable mode accepts delivery and applies duplicate financial credit
            return self.db.process_webhook_event_vulnerable(payload, payload_hash)
        else:
            # Fixed mode strictly verifies entity bounds and guarantees atomic idempotency
            return self.db.process_webhook_event_fixed(payload, payload_hash)

    # ---------------------------------------------------------
    # 1. Quote & Hold Phase
    # ---------------------------------------------------------
    async def create_quote(self, req: QuoteRequest, agency_id: str) -> QuoteResponse:
        quote_id = f"QTE-{int(datetime.utcnow().timestamp() * 1000)}"
        expires_at = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
        amount_minor = to_minor_units(req.amount, req.currency)
        quote = QuoteResponse(
            quote_id=quote_id,
            hotel_id=req.hotel_id,
            room_type=req.room_type,
            amount=req.amount,
            amount_minor=amount_minor,
            currency=req.currency,
            expires_at=expires_at,
            status=TransactionStatus.QUOTED,
        )
        self.db.save_quote(quote, agency_id, req.check_in, req.check_out, req.guest_name)
        self.db.log_event(
            booking_id=None,
            correlation_id=quote_id,
            actor="AGGREGATOR",
            event_type="QUOTE_CREATED",
            details=f"Quote {quote_id} created for {req.hotel_id}: {req.amount} {req.currency}",
        )
        return quote

    async def create_hold(self, req: HoldRequest, agency_id: str) -> HoldResponse:
        quote = self.db.get_quote(req.quote_id, requesting_agency_id=agency_id)
        if not quote:
            raise ValueError(f"Quote '{req.quote_id}' not found or unauthorized")

        hold_id = f"HLD-{int(datetime.utcnow().timestamp() * 1000)}"
        expires_at = (datetime.utcnow() + timedelta(seconds=req.hold_duration_seconds)).isoformat()
        amount = quote["amount"]
        currency = quote["currency"]
        amount_minor = quote["amount_minor"] if "amount_minor" in quote and quote["amount_minor"] else to_minor_units(amount, currency)
        hold = HoldResponse(
            hold_id=hold_id,
            quote_id=req.quote_id,
            hotel_id=quote["hotel_id"],
            room_type=quote["room_type"],
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            status=TransactionStatus.HELD,
            expires_at=expires_at,
        )
        self.db.save_hold(hold, agency_id)
        self.db.log_event(
            booking_id=None,
            correlation_id=hold_id,
            actor="AGGREGATOR",
            event_type="HOLD_CONFIRMED",
            details=f"Hold {hold_id} locked for quote {req.quote_id} until {expires_at}",
        )
        return hold

    async def recover_external_dispatch(
        self,
        intent: BookingIntent,
        claim: BookingOperationClaimRecord,
        owner_token: str,
        fencing_token: int,
        mode: AggregatorMode,
    ) -> AggregatorBookingRecord:
        """
        Executes external dispatch recovery after a worker crash or lease expiration.
        Queries supplier via stable reference (intent.intent_id).
        Adopts existing confirmed reservation if found.
        Never spawns duplicate reservations.
        """
        booking_id = claim.booking_id or f"TBOH-{intent.intent_id}"
        if not self.db.get_booking_intent(intent.intent_id):
            self.db.save_booking_intent(intent)
        record = self.db.get_booking_record(booking_id, requesting_agency_id=intent.agency_id)
        if not record:
            amount = intent.amount
            currency = intent.currency
            amount_minor = intent.amount_minor or to_minor_units(amount, currency)
            penalty_rate = Decimal(str(intent.cancellation_penalty_rate))
            penalty_minor = round_minor_units(int((Decimal(amount_minor) * penalty_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
            entitlement_minor = amount_minor - penalty_minor
            penalty = from_minor_units(penalty_minor, currency)
            refundable_entitlement = from_minor_units(entitlement_minor, currency)
            payment_method = getattr(intent, "payment_method", "CARD") or "CARD"
            record = AggregatorBookingRecord(
                booking_id=booking_id,
                intent_id=intent.intent_id,
                agency_id=intent.agency_id,
                status=TransactionStatus.BOOKING_PENDING,
                amount=amount,
                amount_minor=amount_minor,
                currency=currency,
                cancellation_penalty=penalty,
                cancellation_penalty_minor=penalty_minor,
                refundable_entitlement=refundable_entitlement,
                refundable_entitlement_minor=entitlement_minor,
                total_refunded=0.0,
                total_refunded_minor=0,
                correlation_id=intent.correlation_id,
                payment_method=payment_method,
                idempotency_key=intent.idempotency_key,
                payload_hash=intent.payload_hash,
            )
            self.db.save_booking_record(record)

        self.db.log_event(
            booking_id=booking_id,
            correlation_id=intent.correlation_id,
            actor="AGGREGATOR_RECOVERY",
            event_type="EXTERNAL_DISPATCH_RECOVERY_STARTED",
            details=f"Recovering in-flight operation for intent {intent.intent_id} with fencing token {fencing_token}",
        )

        sup_res = None
        if self.supplier_client.supports_lookup:
            try:
                sup_res = await self.supplier_client.call_reconcile_by_reference(intent.intent_id)
            except Exception as exc:
                record.status = TransactionStatus.SUPPLIER_UNKNOWN
                record.error_message = f"Supplier reconciliation query failed during recovery: {str(exc)}"
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                self.db.update_claim_status(
                    intent.agency_id,
                    intent.idempotency_key,
                    BookingClaimStatus.RECONCILIATION_REQUIRED,
                    failure_reason=record.error_message,
                    expected_fencing_token=fencing_token,
                    expected_owner_token=owner_token,
                )
                return record
        else:
            sup_res = None

        if sup_res is not None and getattr(sup_res, "status", None) in ["CONFIRMED", "SUCCESS", "RESERVED"]:
            self.db.log_event(
                booking_id=booking_id,
                correlation_id=intent.correlation_id,
                actor="AGGREGATOR_RECOVERY",
                event_type="SUPPLIER_RESERVATION_ADOPTED",
                details=f"Adopted existing reservation {sup_res.supplier_reservation_id} for recovered intent {intent.intent_id}",
            )
            record.primary_supplier_reservation_id = sup_res.supplier_reservation_id
            record.status = TransactionStatus.SUPPLIER_CONFIRMED
            record.voucher_id = f"VOUCHER-{booking_id}"
            record.updated_at = datetime.utcnow()
            self.db.save_booking_record(record)
            self._persist_voucher_if_present(record)

            if record.payment_method == "WALLET":
                holds = self.db.get_wallet_holds(intent.agency_id)
                h = next((x for x in holds if x.booking_id == booking_id), None)
                if h and h.status == WalletHoldStatus.HELD:
                    try:
                        self.db.capture_wallet_hold(h.hold_id)
                    except Exception as cap_exc:
                        await self.supplier_client.call_cancel_reservation(sup_res.supplier_reservation_id)
                        record.status = TransactionStatus.FAILED
                        record.error_message = f"Wallet capture failed during recovery: {str(cap_exc)}"
                        self.db.save_booking_record(record)
                        self.db.update_claim_status(
                            intent.agency_id,
                            intent.idempotency_key,
                            BookingClaimStatus.FAILED,
                            failure_reason=record.error_message,
                            expected_fencing_token=fencing_token,
                            expected_owner_token=owner_token,
                        )
                        return record

            self.db.update_claim_status(
                intent.agency_id,
                intent.idempotency_key,
                BookingClaimStatus.COMPLETED,
                expected_fencing_token=fencing_token,
                expected_owner_token=owner_token,
            )
            return record

        elif sup_res is None:
            supports_idemp = getattr(self.supplier_client, "supports_idempotency", False)
            if mode == AggregatorMode.FIXED and supports_idemp:
                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR_RECOVERY",
                    event_type="RECOVERY_DISPATCH_RETRY",
                    details=f"No existing reservation found on supplier. Retrying with stable idempotency key for {booking_id}",
                )
                supplier_idemp_key = f"IDEMP-TXN-{intent.intent_id}"
                req = SupplierBookingRequest(
                    intent_id=intent.intent_id,
                    hotel_id=intent.hotel_id,
                    room_type=intent.room_type,
                    guest_name=intent.guest_name,
                    amount=intent.amount,
                    amount_minor=intent.amount_minor or to_minor_units(intent.amount, intent.currency),
                    currency=intent.currency,
                    correlation_id=intent.correlation_id,
                    idempotency_key=supplier_idemp_key,
                )
                try:
                    new_sup_res = await self.supplier_client.call_create_reservation(req)
                    record.primary_supplier_reservation_id = new_sup_res.supplier_reservation_id
                    record.status = TransactionStatus.SUPPLIER_CONFIRMED
                    record.voucher_id = f"VOUCHER-{booking_id}"
                    record.updated_at = datetime.utcnow()
                    self.db.save_booking_record(record)

                    if record.payment_method == "WALLET":
                        holds = self.db.get_wallet_holds(intent.agency_id)
                        h = next((x for x in holds if x.booking_id == booking_id), None)
                        if h and h.status == WalletHoldStatus.HELD:
                            self.db.capture_wallet_hold(h.hold_id)

                    self.db.update_claim_status(
                        intent.agency_id,
                        intent.idempotency_key,
                        BookingClaimStatus.COMPLETED,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    return record
                except Exception as dispatch_exc:
                    record.status = TransactionStatus.SUPPLIER_UNKNOWN
                    record.error_message = f"Supplier dispatch failed during recovery: {str(dispatch_exc)}"
                    self.db.save_booking_record(record)
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent.idempotency_key,
                        BookingClaimStatus.RECONCILIATION_REQUIRED,
                        failure_reason=record.error_message,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    return record
            else:
                record.status = TransactionStatus.SUPPLIER_UNKNOWN
                record.error_message = (
                    f"Ambiguous supplier state for intent {intent.intent_id}: Provider lacks idempotency protection or "
                    f"mode is not FIXED. Blind redispatch halted to prevent duplicate commitments."
                )
                self.db.save_booking_record(record)
                self.db.update_claim_status(
                    intent.agency_id,
                    intent.idempotency_key,
                    BookingClaimStatus.RECONCILIATION_REQUIRED,
                    failure_reason=record.error_message,
                    expected_fencing_token=fencing_token,
                    expected_owner_token=owner_token,
                )
                return record
        else:
            record.status = TransactionStatus.SUPPLIER_UNKNOWN
            record.error_message = f"Ambiguous supplier state: {getattr(sup_res, 'status', 'UNKNOWN')}"
            self.db.save_booking_record(record)
            self.db.update_claim_status(
                intent.agency_id,
                intent.idempotency_key,
                BookingClaimStatus.RECONCILIATION_REQUIRED,
                failure_reason=record.error_message,
                expected_fencing_token=fencing_token,
                expected_owner_token=owner_token,
            )
            return record

    # ---------------------------------------------------------
    # 2. Booking & Payment Phase
    # ---------------------------------------------------------
    async def process_booking_intent(
        self, intent: BookingIntent, override_mode: Optional[AggregatorMode] = None
    ) -> AggregatorBookingRecord:
        mode = override_mode or self.mode

        # 0. Idempotency Check per (agency_id, idempotency_key) via database-backed claims (CW-ARC-M2-207)
        intent_idemp_key = getattr(intent, "idempotency_key", None)
        amount_minor = intent.amount_minor or to_minor_units(intent.amount, intent.currency)
        payload_str = f"{intent.hotel_id}|{intent.room_type}|{intent.check_in}|{intent.check_out}|{amount_minor}|{intent.currency}|{intent.guest_name}"
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        intent.payload_hash = payload_hash

        booking_id = f"TBOH-{intent.intent_id}"
        owner_token = getattr(self, "owner_token", None) or f"worker_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self.owner_token = owner_token
        current_fencing_token = 1

        if intent_idemp_key:
            acquired, claim = self.db.acquire_booking_claim(
                agency_id=intent.agency_id,
                idempotency_key=intent_idemp_key,
                payload_hash=payload_hash,
                operation_id=f"op_{intent.intent_id}",
                booking_id=booking_id,
                owner_token=owner_token,
                correlation_id=intent.correlation_id,
                lease_duration_seconds=10.0,
            )
            if not acquired:
                target_b_id = claim.booking_id or booking_id
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    c = self.db.get_booking_claim(intent.agency_id, intent_idemp_key)
                    if not c:
                        break
                    if c.status in [
                        BookingClaimStatus.COMPLETED,
                        BookingClaimStatus.FAILED,
                        BookingClaimStatus.RECONCILIATION_REQUIRED,
                    ]:
                        for _ in range(20):
                            b_rec = self.db.get_booking_record(target_b_id, requesting_agency_id=intent.agency_id)
                            if b_rec and b_rec.status in [
                                TransactionStatus.SUPPLIER_CONFIRMED,
                                TransactionStatus.VOUCHER_ISSUED,
                                TransactionStatus.FAILED,
                            ]:
                                return b_rec
                            await asyncio.sleep(0.05)
                        b_rec = self.db.get_booking_record(target_b_id, requesting_agency_id=intent.agency_id)
                        if b_rec:
                            return b_rec
                        break
                    now_dt = datetime.utcnow()
                    if c.lease_expires_at:
                        try:
                            if now_dt >= datetime.fromisoformat(c.lease_expires_at):
                                claim = c
                                break
                        except Exception:
                            pass

                now_dt = datetime.utcnow()
                c = self.db.get_booking_claim(intent.agency_id, intent_idemp_key)
                lease_expired = False
                if c and c.lease_expires_at:
                    try:
                        lease_expired = (now_dt >= datetime.fromisoformat(c.lease_expires_at))
                    except Exception:
                        lease_expired = True

                if lease_expired and c:
                    if c.status in [BookingClaimStatus.CLAIMED, BookingClaimStatus.WALLET_HOLD_CREATED]:
                        reclaimed, new_claim = self.db.acquire_booking_claim(
                            agency_id=intent.agency_id,
                            idempotency_key=intent_idemp_key,
                            payload_hash=payload_hash,
                            operation_id=f"op_{intent.intent_id}",
                            booking_id=booking_id,
                            owner_token=owner_token,
                            correlation_id=intent.correlation_id,
                            lease_duration_seconds=10.0,
                        )
                        if reclaimed:
                            acquired = True
                            claim = new_claim
                    elif c.status in [
                        BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
                        BookingClaimStatus.SUPPLIER_CONFIRMED,
                        BookingClaimStatus.WALLET_CAPTURE_PENDING,
                    ]:
                        stolen, rec_claim = self.db.reclaim_expired_claim_for_external_recovery(
                            intent.agency_id, intent_idemp_key, owner_token, lease_duration_seconds=10.0
                        )
                        if stolen and rec_claim:
                            return await self.recover_external_dispatch(
                                intent, rec_claim, owner_token, rec_claim.fencing_token, mode
                            )

                if not acquired:
                    for _ in range(40):
                        b_rec = self.db.get_booking_record(target_b_id, requesting_agency_id=intent.agency_id)
                        if b_rec and (b_rec.status in [TransactionStatus.VOUCHER_ISSUED, TransactionStatus.FAILED] or b_rec.voucher_id):
                            return b_rec
                        existing = self.db.get_booking_by_idempotency_key(intent.agency_id, intent_idemp_key)
                        if existing and (existing.status in [TransactionStatus.VOUCHER_ISSUED, TransactionStatus.FAILED] or existing.voucher_id):
                            return existing
                        await asyncio.sleep(0.05)

                    b_rec = self.db.get_booking_record(target_b_id, requesting_agency_id=intent.agency_id)
                    if b_rec:
                        return b_rec
                    existing = self.db.get_booking_by_idempotency_key(intent.agency_id, intent_idemp_key)
                    if existing:
                        return existing

            current_fencing_token = claim.fencing_token

        # 1. Persist Intent
        self.db.save_booking_intent(intent)

        # Calculate exact financial bounds using minor units
        amount = intent.amount
        currency = intent.currency
        penalty_rate = Decimal(str(intent.cancellation_penalty_rate))
        penalty_minor = round_minor_units(int((Decimal(amount_minor) * penalty_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
        entitlement_minor = amount_minor - penalty_minor
        penalty = from_minor_units(penalty_minor, currency)
        refundable_entitlement = from_minor_units(entitlement_minor, currency)

        # 2. Allocate Aggregator Booking Record (CREATED -> BOOKING_PENDING)
        booking_id = f"TBOH-{intent.intent_id}"
        payment_method = getattr(intent, "payment_method", "CARD") or "CARD"
        record = AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent.intent_id,
            agency_id=intent.agency_id,
            status=TransactionStatus.BOOKING_PENDING,
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            cancellation_penalty=penalty,
            cancellation_penalty_minor=penalty_minor,
            refundable_entitlement=refundable_entitlement,
            refundable_entitlement_minor=entitlement_minor,
            total_refunded=0.0,
            total_refunded_minor=0,
            correlation_id=intent.correlation_id,
            payment_method=payment_method,
            idempotency_key=intent_idemp_key,
            payload_hash=payload_hash,
        )
        self.db.save_booking_record(record)
        self.db.log_event(
            booking_id=booking_id,
            correlation_id=intent.correlation_id,
            actor="AGGREGATOR",
            event_type="BOOKING_INITIALIZED",
            details=f"Booking initialized under mode {mode.value} with payment method {payment_method}",
        )

        # -----------------------------------------------------
        # BRANCH A: WALLET PAYMENT METHOD SAGA (Milestone 2.2)
        # -----------------------------------------------------
        if payment_method == "WALLET":
            record.status = TransactionStatus.PAYMENT_PENDING
            self.db.save_booking_record(record)

            # Hold funds in Agency Wallet
            if self.wallet_mode == AggregatorMode.VULNERABLE:
                wallet_hold = self.db.create_wallet_hold_vulnerable(
                    agency_id=intent.agency_id,
                    booking_id=booking_id,
                    amount_minor=amount_minor,
                    currency=currency,
                    idempotency_key=intent_idemp_key,
                )
            else:
                wallet_hold = self.db.create_wallet_hold_fixed(
                    agency_id=intent.agency_id,
                    booking_id=booking_id,
                    amount_minor=amount_minor,
                    currency=currency,
                    idempotency_key=intent_idemp_key,
                )

            record.wallet_hold_id = wallet_hold.hold_id
            record.payment_id = f"PAY-WALLET-{wallet_hold.hold_id}"
            self.db.save_booking_record(record)

            if intent_idemp_key:
                self.db.update_claim_status(
                    intent.agency_id,
                    intent_idemp_key,
                    BookingClaimStatus.WALLET_HOLD_CREATED,
                    expected_fencing_token=current_fencing_token,
                    expected_owner_token=owner_token,
                )

            self.db.log_event(
                booking_id=booking_id,
                correlation_id=intent.correlation_id,
                actor="AGGREGATOR",
                event_type="WALLET_HOLD_CREATED",
                details=f"Wallet hold {wallet_hold.hold_id} locked {amount_minor} {currency} for agency {intent.agency_id}",
            )

            # Supplier Booking Attempt
            supplier_idemp_key = f"IDEMP-TXN-{intent.intent_id}" if mode == AggregatorMode.FIXED else None
            req = SupplierBookingRequest(
                intent_id=intent.intent_id,
                hotel_id=intent.hotel_id,
                room_type=intent.room_type,
                guest_name=intent.guest_name,
                amount=amount,
                amount_minor=amount_minor,
                currency=currency,
                correlation_id=intent.correlation_id,
                idempotency_key=supplier_idemp_key,
            )

            if intent_idemp_key:
                self.db.update_claim_status(
                    intent.agency_id,
                    intent_idemp_key,
                    BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
                    expected_fencing_token=current_fencing_token,
                    expected_owner_token=owner_token,
                )

            try:
                supplier_res = await self.supplier_client.call_create_reservation(req)
                
                if intent_idemp_key:
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent_idemp_key,
                        BookingClaimStatus.SUPPLIER_CONFIRMED,
                        expected_fencing_token=current_fencing_token,
                        expected_owner_token=owner_token,
                    )
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent_idemp_key,
                        BookingClaimStatus.WALLET_CAPTURE_PENDING,
                        expected_fencing_token=current_fencing_token,
                        expected_owner_token=owner_token,
                    )

                # CW-ARC-M2-208: Wallet Capture with Ambiguous-Capture Check and Reliable Saga Compensation
                try:
                    self.db.capture_wallet_hold(wallet_hold.hold_id)
                except Exception as cap_exc:
                    # Ambiguous capture check: verify if the hold was actually captured
                    h_state = self.db.get_wallet_hold(wallet_hold.hold_id)
                    if not (h_state and h_state.status == WalletHoldStatus.CAPTURED):
                        # Capture definitely did not commit. Compensate supplier reservation!
                        self.db.log_event(
                            booking_id=booking_id,
                            correlation_id=intent.correlation_id,
                            actor="AGGREGATOR",
                            event_type="SAGA_COMPENSATION_TRIGGERED",
                            details=f"Wallet capture failed: {str(cap_exc)}. Compensating supplier reservation {supplier_res.supplier_reservation_id}",
                        )
                        try:
                            self.db.release_wallet_hold(wallet_hold.hold_id, reason="SAGA_CAPTURE_FAILURE")
                        except Exception:
                            pass

                        cancel_success = False
                        try:
                            c_res = await self.supplier_client.call_cancel_reservation(
                                supplier_res.supplier_reservation_id,
                                reason="Saga compensation: wallet capture failed",
                            )
                            if c_res is True:
                                cancel_success = True
                            elif isinstance(c_res, dict) and c_res.get("status") in ["CANCELLED", "CONFIRMED", "SUCCESS"]:
                                cancel_success = True
                            elif getattr(c_res, "status", None) in ["CANCELLED", "CONFIRMED", "SUCCESS"] or getattr(c_res, "success", False):
                                cancel_success = True
                        except Exception:
                            cancel_success = False

                        if cancel_success:
                            record.status = TransactionStatus.FAILED
                            record.error_message = f"Wallet capture failed ({str(cap_exc)}); supplier reservation cancelled via saga compensation"
                            record.updated_at = datetime.utcnow()
                            self.db.save_booking_record(record)
                            if intent_idemp_key:
                                self.db.update_claim_status(
                                    intent.agency_id,
                                    intent_idemp_key,
                                    BookingClaimStatus.FAILED,
                                    failure_reason=record.error_message,
                                    expected_fencing_token=current_fencing_token,
                                    expected_owner_token=owner_token,
                                )
                            self.db.record_saga_recovery(
                                booking_id=booking_id,
                                agency_id=intent.agency_id,
                                action="CANCEL_SUPPLIER_RESERVATION",
                                status="COMPENSATED",
                                target_supplier_reservation_id=supplier_res.supplier_reservation_id,
                                wallet_hold_id=wallet_hold.hold_id,
                                last_error=str(cap_exc),
                            )
                            return record
                        else:
                            record.status = TransactionStatus.SUPPLIER_UNKNOWN
                            record.error_message = "Wallet capture failed and supplier cancellation failed/timed out. Manual reconciliation required"
                            record.updated_at = datetime.utcnow()
                            self.db.save_booking_record(record)
                            if intent_idemp_key:
                                self.db.update_claim_status(
                                    intent.agency_id,
                                    intent_idemp_key,
                                    BookingClaimStatus.RECONCILIATION_REQUIRED,
                                    failure_reason=record.error_message,
                                    expected_fencing_token=current_fencing_token,
                                    expected_owner_token=owner_token,
                                )
                            self.db.record_saga_recovery(
                                booking_id=booking_id,
                                agency_id=intent.agency_id,
                                action="CANCEL_SUPPLIER_RESERVATION",
                                status="RECONCILIATION_REQUIRED",
                                target_supplier_reservation_id=supplier_res.supplier_reservation_id,
                                wallet_hold_id=wallet_hold.hold_id,
                                last_error=f"Capture err: {str(cap_exc)} | Cancel failed",
                            )
                            return record

                record.status = TransactionStatus.SUPPLIER_CONFIRMED
                record.primary_supplier_reservation_id = supplier_res.supplier_reservation_id
                record.voucher_id = f"VOUCHER-{booking_id}"
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                self._persist_voucher_if_present(record)

                if intent_idemp_key:
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent_idemp_key,
                        BookingClaimStatus.COMPLETED,
                        expected_fencing_token=current_fencing_token,
                        expected_owner_token=owner_token,
                    )

                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="BOOKING_COMPLETED_VIA_WALLET",
                    details=f"Reservation {supplier_res.supplier_reservation_id} confirmed and wallet hold captured",
                )
                return record

            except AggregatorSupplierSoldOutError as exc:
                self.db.release_wallet_hold(wallet_hold.hold_id, reason="SUPPLIER_SOLD_OUT")
                record.status = TransactionStatus.FAILED
                record.error_message = f"Supplier inventory sold out: {str(exc)}"
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                if intent_idemp_key:
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent_idemp_key,
                        BookingClaimStatus.FAILED,
                        failure_reason=str(exc),
                        expected_fencing_token=current_fencing_token,
                        expected_owner_token=owner_token,
                    )

                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="SAGA_COMPENSATION_RELEASE_HOLD",
                    details=f"Released wallet hold {wallet_hold.hold_id} due to room sold out",
                )
                raise RoomSoldOutError(str(exc))

            except AggregatorSupplierTimeoutError as exc:
                record.status = TransactionStatus.SUPPLIER_UNKNOWN
                record.error_message = "Supplier timed out; reconciliation required"
                record.retry_count += 1
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                if intent_idemp_key:
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent_idemp_key,
                        BookingClaimStatus.RECONCILIATION_REQUIRED,
                        failure_reason="Supplier timed out",
                        expected_fencing_token=current_fencing_token,
                        expected_owner_token=owner_token,
                    )

                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="SUPPLIER_TIMEOUT_WALLET_HELD",
                    details="Supplier timed out during wallet booking; hold kept active for reconciliation",
                )
                return record

            except Exception as exc:
                self.db.release_wallet_hold(wallet_hold.hold_id, reason="SUPPLIER_ERROR")
                record.status = TransactionStatus.FAILED
                record.error_message = str(exc)
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                if intent_idemp_key:
                    self.db.update_claim_status(
                        intent.agency_id,
                        intent_idemp_key,
                        BookingClaimStatus.FAILED,
                        failure_reason=str(exc),
                        expected_fencing_token=current_fencing_token,
                        expected_owner_token=owner_token,
                    )
                return record

        # -----------------------------------------------------
        # BRANCH B: CARD PAYMENT METHOD (Milestones 1 & 2.1)
        # -----------------------------------------------------
        # 3. Payment Processing Step (Priority 1 & 2)
        record.status = TransactionStatus.PAYMENT_PENDING
        self.db.save_booking_record(record)

        auth_req = PaymentAuthorizationRequest(
            booking_id=booking_id,
            intent_id=intent.intent_id,
            agency_id=intent.agency_id,
            amount=amount,
            currency=currency,
            correlation_id=intent.correlation_id,
            idempotency_key=f"PAY-AUTH-{intent.intent_id}",
            simulate_failure=intent.simulate_payment_failure,
            failure_reason="Synthetic card authorization declined" if intent.simulate_payment_failure else None,
        )

        try:
            auth_res = await self.payment_client.call_authorize(auth_req)
            if auth_res.status == PaymentStatus.FAILED:
                record.status = TransactionStatus.PAYMENT_FAILED
                record.error_message = auth_res.message or "Payment authorization declined by provider"
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="PAYMENT_AUTHORIZATION_FAILED",
                    details="Payment authorization returned FAILED status",
                )
                return record
        except (AggregatorPaymentError, AggregatorPaymentConflictError) as exc:
            record.status = TransactionStatus.PAYMENT_FAILED
            record.error_message = str(exc)
            record.updated_at = datetime.utcnow()
            self.db.save_booking_record(record)
            self.db.log_event(
                booking_id=booking_id,
                correlation_id=intent.correlation_id,
                actor="AGGREGATOR",
                event_type="PAYMENT_AUTHORIZATION_FAILED",
                details=f"Payment declined: {str(exc)}",
            )
            return record

        # Capture Payment
        cap_req = PaymentCaptureRequest(
            payment_id=auth_res.payment_id,
            booking_id=booking_id,
            amount=amount,
            currency=currency,
            correlation_id=intent.correlation_id,
            idempotency_key=f"PAY-CAP-{intent.intent_id}",
        )
        cap_res = await self.payment_client.call_capture(cap_req)

        # Save payment transaction record
        pay_tx = PaymentTransactionRecord(
            payment_id=auth_res.payment_id,
            booking_id=booking_id,
            intent_id=intent.intent_id,
            agency_id=intent.agency_id,
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            status=PaymentStatus.CAPTURED,
            transaction_reference=cap_res.transaction_reference,
            idempotency_key=f"PAY-{intent.intent_id}",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.save_payment_transaction(pay_tx)

        # Post Financial Ledger Entry with exact minor units (Priority 3)
        self.db.record_ledger_entry(
            LedgerEntry(
                transaction_reference=f"LEDGER-PAY-{booking_id}",
                booking_id=booking_id,
                agency_id=intent.agency_id,
                entry_type=LedgerEntryType.PAYMENT_CAPTURE,
                direction=LedgerDirection.DEBIT,
                amount=amount,
                amount_minor=amount_minor,
                currency=currency,
                journal_batch_id=f"BATCH-PAY-{booking_id}",
                account_code="1000_PLATFORM_CASH",
                description=f"Captured payment for {booking_id}",
            )
        )

        record.payment_id = auth_res.payment_id
        record.status = TransactionStatus.PAYMENT_CONFIRMED
        self.db.save_booking_record(record)

        self.db.log_event(
            booking_id=booking_id,
            correlation_id=intent.correlation_id,
            actor="AGGREGATOR",
            event_type="PAYMENT_CONFIRMED",
            details=f"Payment {auth_res.payment_id} captured. Amount: {amount} {currency}",
        )

        # 4. Supplier Booking Request
        idemp_key = f"IDEMP-TXN-{intent.intent_id}" if mode == AggregatorMode.FIXED else None

        req = SupplierBookingRequest(
            intent_id=intent.intent_id,
            hotel_id=intent.hotel_id,
            room_type=intent.room_type,
            guest_name=intent.guest_name,
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            correlation_id=intent.correlation_id,
            idempotency_key=idemp_key,
        )

        record.status = TransactionStatus.BOOKING_PENDING
        self.db.save_booking_record(record)

        # Record first supplier operation attempt
        self.db.record_supplier_operation(
            operation_id=f"OP-1-{booking_id}",
            booking_id=booking_id,
            intent_id=intent.intent_id,
            attempt_number=1,
            idempotency_key=idemp_key,
            endpoint="/api/v1/supplier/reservations",
            status_code=None,
            supplier_reservation_id=None,
            error_type=None,
        )

        if intent_idemp_key:
            self.db.update_claim_status(
                intent.agency_id,
                intent_idemp_key,
                BookingClaimStatus.SUPPLIER_REQUEST_PENDING,
                expected_fencing_token=current_fencing_token,
                expected_owner_token=owner_token,
            )

        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="REQUEST_DISPATCHED",
                event_status="INFO",
                title="Supplier HTTP Request Dispatched",
                detail=f"Aggregator sent POST /reservations with client timeout configured to 0.15s (Mode: {mode.value})",
                source_component="AggregatorBookingService",
                metadata={"endpoint": "POST /reservations", "client_timeout_s": 0.15, "mode": mode.value, "idempotency_key": idemp_key},
                evidence_references={"intent_id": intent.intent_id}
            )

        try:
            # First attempt
            supplier_res = await self.supplier_client.call_create_reservation(req)
            self.db.record_supplier_operation(
                operation_id=f"OP-1-{booking_id}",
                booking_id=booking_id,
                intent_id=intent.intent_id,
                attempt_number=1,
                idempotency_key=idemp_key,
                endpoint="/api/v1/supplier/reservations",
                status_code=200,
                supplier_reservation_id=supplier_res.supplier_reservation_id,
                error_type=None,
            )
            record.status = TransactionStatus.SUPPLIER_CONFIRMED
            record.primary_supplier_reservation_id = supplier_res.supplier_reservation_id
            record.voucher_id = f"VOUCHER-{booking_id}"
            record.updated_at = datetime.utcnow()
            self.db.save_booking_record(record)
            self._persist_voucher_if_present(record)

            if intent_idemp_key:
                self.db.update_claim_status(
                    intent.agency_id,
                    intent_idemp_key,
                    BookingClaimStatus.COMPLETED,
                    expected_fencing_token=current_fencing_token,
                    expected_owner_token=owner_token,
                )

            self.db.log_event(
                booking_id=booking_id,
                correlation_id=intent.correlation_id,
                actor="AGGREGATOR",
                event_type="SUPPLIER_CONFIRMED",
                details=f"Confirmed reservation {supplier_res.supplier_reservation_id}",
            )
            return record

        except AggregatorSupplierSoldOutError as exc:
            record.status = TransactionStatus.FAILED
            record.error_message = f"Supplier inventory sold out: {str(exc)}"
            record.updated_at = datetime.utcnow()
            self.db.save_booking_record(record)
            raise RoomSoldOutError(str(exc))

        except AggregatorSupplierTimeoutError:
            # --- TIMEOUT OCCURRED ---
            self.db.record_supplier_operation(
                operation_id=f"OP-1-{booking_id}",
                booking_id=booking_id,
                intent_id=intent.intent_id,
                attempt_number=1,
                idempotency_key=idemp_key,
                endpoint="/api/v1/supplier/reservations",
                status_code=None,
                supplier_reservation_id=None,
                error_type="HTTP_TIMEOUT",
            )
            record.retry_count += 1
            record.updated_at = datetime.utcnow()
            self.db.save_booking_record(record)

            self.db.log_event(
                booking_id=booking_id,
                correlation_id=intent.correlation_id,
                actor="AGGREGATOR",
                event_type="SUPPLIER_TIMEOUT",
                details="HTTP timeout received. Ambiguous state detected.",
            )

            if mode == AggregatorMode.VULNERABLE:
                # --- BRANCH A: VULNERABLE BEHAVIOR (BLIND RETRY) ---
                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="BLIND_RETRY_TRIGGERED",
                    details="Vulnerable mode: triggering blind non-idempotent retry",
                )
                collector = get_active_collector()
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="RETRY_RECONCILE",
                        event_status="WARNING",
                        title="Vulnerable Blind Retry Dispatched",
                        detail="Vulnerable aggregator naively re-issued POST /reservations without idempotency key or reference lookup",
                        source_component="AggregatorBookingService",
                        metadata={"retry_attempt": 2, "idempotency_key": None},
                        evidence_references={"intent_id": intent.intent_id, "booking_id": booking_id}
                    )

                vulnerable_req = SupplierBookingRequest(
                    intent_id=intent.intent_id,
                    hotel_id=intent.hotel_id,
                    room_type=intent.room_type,
                    guest_name=intent.guest_name,
                    amount=amount,
                    amount_minor=amount_minor,
                    currency=currency,
                    correlation_id=intent.correlation_id,
                    idempotency_key=None,  # No idempotency key provided
                )

                self.db.record_supplier_operation(
                    operation_id=f"OP-2-{booking_id}",
                    booking_id=booking_id,
                    intent_id=intent.intent_id,
                    attempt_number=2,
                    idempotency_key=None,
                    endpoint="/api/v1/supplier/reservations",
                    status_code=None,
                    supplier_reservation_id=None,
                    error_type="TIMEOUT_RETRY",
                )

                retry_res = await self.supplier_client.call_create_reservation(vulnerable_req, timeout_override=2.0)
                self.db.record_supplier_operation(
                    operation_id=f"OP-2-{booking_id}",
                    booking_id=booking_id,
                    intent_id=intent.intent_id,
                    attempt_number=2,
                    idempotency_key=None,
                    endpoint="/api/v1/supplier/reservations",
                    status_code=200,
                    supplier_reservation_id=retry_res.supplier_reservation_id,
                    error_type="TIMEOUT_RETRY",
                )

                record.status = TransactionStatus.SUPPLIER_CONFIRMED
                record.primary_supplier_reservation_id = retry_res.supplier_reservation_id
                record.voucher_id = f"VOUCHER-{booking_id}"
                record.updated_at = datetime.utcnow()
                self.db.save_booking_record(record)
                self._persist_voucher_if_present(record)

                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="DUPLICATE_RESERVATION_CREATED",
                    details=f"Vulnerable retry created secondary reservation {retry_res.supplier_reservation_id}",
                )
                return record

            else:
                # --- BRANCH B: FIXED BEHAVIOR (IDEMPOTENT RECOVERY & RECONCILIATION) ---
                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=intent.correlation_id,
                    actor="AGGREGATOR",
                    event_type="RECONCILIATION_ATTEMPT",
                    details="Attempting safe reconciliation by reference",
                )
                collector = get_active_collector()
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="RETRY_RECONCILE",
                        event_status="SUCCESS",
                        title="Capability-Aware Reconciliation Invoked",
                        detail="Fixed aggregator preserved deterministic Idempotency-Key and invoked safe lookup/reconciliation query",
                        source_component="AggregatorBookingService",
                        metadata={"reconciliation_strategy": "IDEMPOTENT_REPLAY_LOOKUP", "idempotency_key": idemp_key},
                        evidence_references={"intent_id": intent.intent_id, "booking_id": booking_id}
                    )

                try:
                    # Method 1: Reconcile by reference
                    existing_res = await self.supplier_client.call_reconcile_by_reference(intent.intent_id)
                    if existing_res:
                        record.status = TransactionStatus.SUPPLIER_CONFIRMED
                        record.primary_supplier_reservation_id = existing_res.supplier_reservation_id
                        record.voucher_id = f"VOUCHER-{booking_id}"
                        record.updated_at = datetime.utcnow()
                        self.db.save_booking_record(record)
                        self._persist_voucher_if_present(record)

                        if intent_idemp_key:
                            self.db.update_claim_status(
                                intent.agency_id,
                                intent_idemp_key,
                                BookingClaimStatus.COMPLETED,
                                expected_fencing_token=current_fencing_token,
                                expected_owner_token=owner_token,
                            )

                        self.db.log_event(
                            booking_id=booking_id,
                            correlation_id=intent.correlation_id,
                            actor="AGGREGATOR",
                            event_type="RECONCILIATION_SUCCESS",
                            details=f"Recovered original reservation {existing_res.supplier_reservation_id}",
                        )
                        return record

                    # Method 2: Idempotent Retry with same key
                    idemp_retry_res = await self.supplier_client.call_create_reservation(req, timeout_override=2.0)
                    record.status = TransactionStatus.SUPPLIER_CONFIRMED
                    record.primary_supplier_reservation_id = idemp_retry_res.supplier_reservation_id
                    record.voucher_id = f"VOUCHER-{booking_id}"
                    record.updated_at = datetime.utcnow()
                    self.db.save_booking_record(record)
                    self._persist_voucher_if_present(record)

                    if intent_idemp_key:
                        self.db.update_claim_status(
                            intent.agency_id,
                            intent_idemp_key,
                            BookingClaimStatus.COMPLETED,
                            expected_fencing_token=current_fencing_token,
                            expected_owner_token=owner_token,
                        )
                    return record

                except AggregatorSupplierUnsupportedError:
                    # --- BRANCH C: UNSUPPORTED SUPPLIER SAFE FALLBACK ---
                    record.status = TransactionStatus.SUPPLIER_UNKNOWN
                    record.error_message = (
                        "Supplier timed out and does not support idempotency. "
                        "Held in SUPPLIER_UNKNOWN for manual ops reconciliation."
                    )
                    record.updated_at = datetime.utcnow()
                    self.db.save_booking_record(record)

                    if intent_idemp_key:
                        self.db.update_claim_status(
                            intent.agency_id,
                            intent_idemp_key,
                            BookingClaimStatus.RECONCILIATION_REQUIRED,
                            failure_reason=record.error_message,
                            expected_fencing_token=current_fencing_token,
                            expected_owner_token=owner_token,
                        )

                    self.db.log_event(
                        booking_id=booking_id,
                        correlation_id=intent.correlation_id,
                        actor="AGGREGATOR",
                        event_type="SUPPLIER_UNKNOWN_HALT",
                        details="Safe halt to prevent duplicate commitment on non-idempotent provider",
                    )
                    return record

    # ---------------------------------------------------------
    # 3. Voucher Issuance (Priority 2 & Invariant 5.4)
    # ---------------------------------------------------------
    async def issue_voucher(self, booking_id: str, agency_id: str) -> AggregatorBookingRecord:
        record = self.db.get_booking_record(booking_id, requesting_agency_id=agency_id)
        if not record:
            raise ValueError(f"Booking '{booking_id}' not found or unauthorized")

        # Invariant 5.4: Voucher requires supplier confirmation AND payment obligation satisfied
        if record.status not in [TransactionStatus.SUPPLIER_CONFIRMED, TransactionStatus.VOUCHER_ISSUED]:
            raise InvalidStateTransitionError(
                f"Cannot issue voucher for booking in status '{record.status.value}'. Must be SUPPLIER_CONFIRMED."
            )
        if not record.payment_id and not record.wallet_hold_id:
            raise InvalidStateTransitionError("Cannot issue voucher: payment obligation not satisfied")

        record.voucher_id = record.voucher_id or f"VOUCHER-{booking_id}"
        record.status = TransactionStatus.VOUCHER_ISSUED
        record.updated_at = datetime.utcnow()
        self.db.save_booking_record(record)
        self._persist_voucher_if_present(record)
        self.db.log_event(
            booking_id=booking_id,
            correlation_id=record.correlation_id,
            actor="AGGREGATOR",
            event_type="VOUCHER_ISSUED",
            details=f"Voucher {record.voucher_id} issued for booking {booking_id}",
        )
        return record

    # ---------------------------------------------------------
    # 4. Cancellation (Priority 2, 4 & Balanced Double-Entry)
    # ---------------------------------------------------------
    async def cancel_booking(
        self, booking_id: str, agency_id: str, req: CancellationRequest
    ) -> CancellationResponse:
        record = self.db.get_booking_record(booking_id, requesting_agency_id=agency_id)
        if not record:
            raise ValueError(f"Booking '{booking_id}' not found or unauthorized")

        # Validate state transition: can only cancel active bookings
        allowed_prior = [
            TransactionStatus.SUPPLIER_CONFIRMED,
            TransactionStatus.VOUCHER_ISSUED,
            TransactionStatus.PAYMENT_CONFIRMED,
            TransactionStatus.BOOKING_PENDING,
        ]
        if record.status not in allowed_prior:
            raise InvalidStateTransitionError(
                f"Cannot cancel booking in status '{record.status.value}'. Prior status must be active."
            )

        # Transition status
        record.status = TransactionStatus.CANCELLED
        record.updated_at = datetime.utcnow()
        self.db.save_booking_record(record)

        if record.voucher_id:
            v = self.db.get_voucher(record.voucher_id)
            if v:
                v.status = VoucherStatus.CANCELLED
                self.db.save_voucher(v)

        # Post cancellation penalty to financial ledger if applicable
        if record.cancellation_penalty_minor > 0:
            self.db.record_ledger_entry(
                LedgerEntry(
                    transaction_reference=f"LEDGER-PENALTY-{booking_id}",
                    booking_id=booking_id,
                    agency_id=agency_id,
                    entry_type=LedgerEntryType.PENALTY,
                    direction=LedgerDirection.CREDIT,
                    amount=record.cancellation_penalty,
                    amount_minor=record.cancellation_penalty_minor,
                    currency=record.currency,
                    journal_batch_id=f"BATCH-PENALTY-{booking_id}",
                    account_code="4000_PENALTY_REVENUE",
                    description=f"Cancellation penalty retained for {booking_id}",
                )
            )

        self.db.log_event(
            booking_id=booking_id,
            correlation_id=record.correlation_id,
            actor="AGGREGATOR",
            event_type="BOOKING_CANCELLED",
            details=(
                f"Booking cancelled. Penalty: {record.cancellation_penalty} {record.currency}, "
                f"Refundable Entitlement: {record.refundable_entitlement} {record.currency}"
            ),
        )

        return CancellationResponse(
            booking_id=booking_id,
            status=TransactionStatus.CANCELLED,
            penalty_amount=record.cancellation_penalty,
            penalty_amount_minor=record.cancellation_penalty_minor,
            refundable_entitlement=record.refundable_entitlement,
            refundable_entitlement_minor=record.refundable_entitlement_minor,
            message="Booking cancelled successfully",
        )

    # ---------------------------------------------------------
    # 5. Atomic Refund Processing (Requirements 2, 3, 5 & Invariant 5.5)
    # ---------------------------------------------------------
    async def refund_booking(
        self, booking_id: str, agency_id: str, req: RefundRequest
    ) -> RefundResponse:
        record = self.db.get_booking_record(booking_id, requesting_agency_id=agency_id)
        if not record:
            raise ValueError(f"Booking '{booking_id}' not found or unauthorized")

        # Validate status: booking must be cancelled or partially refunded (or already completed for this idempotency key)
        if record.status not in [TransactionStatus.CANCELLED, TransactionStatus.PARTIALLY_REFUNDED]:
            if req.idempotency_key and self.db.get_refund_by_idempotency_key(agency_id, req.idempotency_key):
                pass
            else:
                raise InvalidStateTransitionError(
                    f"Cannot refund booking in status '{record.status.value}'. Booking must be CANCELLED first."
                )

        refund_amount_minor = to_minor_units(req.amount, record.currency)
        payload_hash = hashlib.sha256(f"{booking_id}:{agency_id}:{req.amount}".encode()).hexdigest()

        # Delegate atomic reservation, entitlement verification, and balanced ledger posting to database
        saved_refund, updated_booking, entries = self.db.execute_atomic_refund(
            booking_id=booking_id,
            agency_id=agency_id,
            refund_amount_minor=refund_amount_minor,
            currency=record.currency,
            reason=req.reason,
            idempotency_key=req.idempotency_key,
            payload_hash=payload_hash,
            payment_id=record.payment_id,
        )

        # Notify payment gateway for actual payment ledger settlement if this was a fresh refund
        if entries and record.payment_id:
            try:
                await self.payment_client.call_refund(record.payment_id, req.amount)
            except Exception as e:
                # Log provider settlement note
                self.db.log_event(
                    booking_id=booking_id,
                    correlation_id=record.correlation_id,
                    actor="AGGREGATOR",
                    event_type="PAYMENT_REFUND_NOTIFIED",
                    details=f"Payment refund call: {str(e)}",
                )

        remaining_minor = updated_booking.refundable_entitlement_minor - updated_booking.total_refunded_minor
        remaining = from_minor_units(remaining_minor, record.currency)

        return RefundResponse(
            refund_id=saved_refund.refund_id,
            booking_id=booking_id,
            amount=saved_refund.amount,
            amount_minor=saved_refund.amount_minor,
            currency=saved_refund.currency,
            total_refunded=updated_booking.total_refunded,
            total_refunded_minor=updated_booking.total_refunded_minor,
            remaining_entitlement=remaining,
            remaining_entitlement_minor=remaining_minor,
            status=updated_booking.status,
            transaction_reference=saved_refund.transaction_reference,
            message="Refund processed successfully",
        )


# Global booking service instance
aggregator_booking_service = AggregatorBookingService()


def get_aggregator_booking_service() -> AggregatorBookingService:
    return aggregator_booking_service

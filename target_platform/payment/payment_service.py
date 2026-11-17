"""
Business logic service for Mock Payment Provider.
Supports:
- Payment authorization (with synthetic IDs and failure simulation)
- Payment capture
- Payment failure
- Payment status lookup
- Idempotency scoping and conflict detection
"""

import hashlib
from typing import Optional
from datetime import datetime
from target_platform.models import (
    PaymentAuthorizationRequest,
    PaymentCaptureRequest,
    PaymentTransactionRecord,
    PaymentStatus,
)
from target_platform.payment.database import (
    get_payment_db,
    PaymentIdempotencyConflictError,
)


class PaymentFailedError(Exception):
    """Raised when payment authorization fails."""
    pass


class PaymentNotFoundError(Exception):
    """Raised when payment record is not found."""
    pass


class PaymentService:
    def __init__(self):
        self.db = get_payment_db()

    def _compute_auth_hash(self, req: PaymentAuthorizationRequest) -> str:
        raw = f"{req.booking_id}:{req.intent_id}:{req.agency_id}:{req.amount}:{req.currency}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def authorize(self, req: PaymentAuthorizationRequest) -> PaymentTransactionRecord:
        payload_hash = self._compute_auth_hash(req)

        # 1. Idempotency Check
        if req.idempotency_key:
            existing = self.db.get_payment_by_idempotency_key(req.agency_id, req.idempotency_key)
            if existing:
                if existing.payload_hash and existing.payload_hash != payload_hash:
                    self.db.log_event(
                        existing.payment_id,
                        req.booking_id,
                        "IDEMPOTENCY_CONFLICT",
                        f"Key '{req.idempotency_key}' reused with mismatched payload",
                    )
                    raise PaymentIdempotencyConflictError(
                        f"Idempotency key '{req.idempotency_key}' already used with different payload"
                    )
                self.db.log_event(
                    existing.payment_id,
                    req.booking_id,
                    "IDEMPOTENT_REPLAY",
                    f"Returning existing payment {existing.payment_id}",
                )
                return existing

        # 2. Check failure simulation
        status = PaymentStatus.FAILED if req.simulate_failure else PaymentStatus.AUTHORIZED
        error_msg = req.failure_reason if req.simulate_failure else None

        payment_id = f"PAY-{req.intent_id}"
        tx_ref = f"TXN-AUTH-{req.booking_id}-{int(datetime.utcnow().timestamp() * 1000)}"

        record = PaymentTransactionRecord(
            payment_id=payment_id,
            booking_id=req.booking_id,
            intent_id=req.intent_id,
            agency_id=req.agency_id,
            amount=req.amount,
            currency=req.currency,
            status=status,
            transaction_reference=tx_ref,
            idempotency_key=req.idempotency_key,
            payload_hash=payload_hash,
            error_message=error_msg,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        saved = self.db.save_payment(record)
        self.db.log_event(
            saved.payment_id,
            req.booking_id,
            f"PAYMENT_{status.value}",
            f"Amount: {req.amount} {req.currency}, Ref: {tx_ref}",
        )

        if status == PaymentStatus.FAILED:
            raise PaymentFailedError(error_msg or "Payment authorization declined by provider")

        return saved

    async def capture(self, req: PaymentCaptureRequest) -> PaymentTransactionRecord:
        payment = self.db.get_payment(req.payment_id)
        if not payment:
            raise PaymentNotFoundError(f"Payment '{req.payment_id}' not found")

        if payment.status == PaymentStatus.CAPTURED:
            # Idempotent capture replay
            return payment

        if payment.status != PaymentStatus.AUTHORIZED:
            raise RuntimeError(f"Cannot capture payment in status '{payment.status.value}'")

        tx_ref = f"TXN-CAP-{req.booking_id}-{int(datetime.utcnow().timestamp() * 1000)}"
        updated = self.db.update_payment_status(req.payment_id, PaymentStatus.CAPTURED, tx_ref)
        self.db.log_event(
            req.payment_id,
            req.booking_id,
            "PAYMENT_CAPTURED",
            f"Captured {req.amount} {req.currency}, Ref: {tx_ref}",
        )
        return updated

    async def refund(self, payment_id: str, amount: float) -> PaymentTransactionRecord:
        payment = self.db.get_payment(payment_id)
        if not payment:
            raise PaymentNotFoundError(f"Payment '{payment_id}' not found")

        new_status = PaymentStatus.REFUNDED if amount >= payment.amount else PaymentStatus.PARTIALLY_REFUNDED
        tx_ref = f"TXN-RFD-{payment_id}-{int(datetime.utcnow().timestamp() * 1000)}"
        updated = self.db.update_payment_status(payment_id, new_status, tx_ref)
        self.db.log_event(
            payment_id,
            payment.booking_id,
            f"PAYMENT_{new_status.value}",
            f"Refunded {amount} {payment.currency}, Ref: {tx_ref}",
        )
        return updated

    def get_payment(self, payment_id: str) -> Optional[PaymentTransactionRecord]:
        return self.db.get_payment(payment_id)

    def get_payment_by_booking(self, booking_id: str) -> Optional[PaymentTransactionRecord]:
        return self.db.get_payment_by_booking(booking_id)

    def generate_refund_webhook_event(
        self,
        booking_id: str,
        payment_id: str,
        refund_id: str,
        amount_minor: int,
        currency: str = "INR",
        event_id: Optional[str] = None,
        provider_id: str = "mock_pay_provider",
        event_type: str = "refund.processed",
        created_at: Optional[str] = None,
        secret: Optional[str] = None,
    ):
        from target_platform.models import WebhookPayload, get_provider_registry
        sec = secret or get_provider_registry().get_secret(provider_id)
        if not sec:
            raise ValueError(f"No secret registered for provider '{provider_id}'")
        evt_id = event_id or f"evt_rfd_{int(datetime.utcnow().timestamp() * 1000)}"
        payload = WebhookPayload(
            provider_id=provider_id,
            event_id=evt_id,
            event_type=event_type,
            booking_id=booking_id,
            payment_id=payment_id,
            refund_id=refund_id,
            amount_minor=amount_minor,
            currency=currency,
            created_at=created_at or datetime.utcnow().isoformat(),
        )
        return payload.sign(sec)


payment_service = PaymentService()


def get_payment_service() -> PaymentService:
    return payment_service

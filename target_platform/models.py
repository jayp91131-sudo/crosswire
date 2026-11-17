"""
Common Pydantic domain models for Crosswire 2.0 synthetic travel platform.
Models the complete canonical hotel booking saga:
SEARCH/QUOTE -> HOLD -> BOOKING -> PAYMENT -> SUPPLIER CONFIRMATION -> VOUCHER -> CANCELLATION -> REFUND.
Implements exact monetary representation via integer minor units (ISO 4217) alongside
major unit decimals to guarantee zero floating-point drift.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from target_platform.money import to_minor_units, from_minor_units


class TransactionStatus(str, Enum):
    # Discovery & Preparation
    SEARCHED = "SEARCHED"
    QUOTED = "QUOTED"
    HELD = "HELD"

    # Booking Initialization
    CREATED = "CREATED"
    BOOKING_PENDING = "BOOKING_PENDING"

    # Payment Processing
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"

    # Supplier Fulfillment & Ticketing
    SUPPLIER_CONFIRMED = "SUPPLIER_CONFIRMED"
    VOUCHER_ISSUED = "VOUCHER_ISSUED"

    # Cancellation & Refund
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REFUND_PENDING = "REFUND_PENDING"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"

    # Ambiguous, Recovery & Terminal States
    SUPPLIER_UNKNOWN = "SUPPLIER_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class SupplierMode(str, Enum):
    VULNERABLE = "VULNERABLE"
    IDEMPOTENT = "IDEMPOTENT"
    UNSUPPORTED = "UNSUPPORTED"


class SupplierCapability(str, Enum):
    IDEMPOTENT_WITH_LOOKUP = "IDEMPOTENT_WITH_LOOKUP"  # Supports idempotent creation & reference lookup
    LOOKUP_ONLY = "LOOKUP_ONLY"                        # Supports reference lookup but not idempotency
    NEITHER = "NEITHER"                                # Legacy / unsupported provider


class AggregatorMode(str, Enum):
    VULNERABLE = "VULNERABLE"
    FIXED = "FIXED"


class PaymentMethod(str, Enum):
    CARD = "CARD"
    WALLET = "WALLET"


# ---------------------------------------------------------
# Quote and Hold Models
# ---------------------------------------------------------
class QuoteRequest(BaseModel):
    hotel_id: str
    room_type: str
    check_in: str
    check_out: str
    guest_name: str
    amount: float = 15000.0
    amount_minor: Optional[int] = None
    currency: str = "INR"

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        return self


class QuoteResponse(BaseModel):
    quote_id: str
    hotel_id: str
    room_type: str
    amount: float
    amount_minor: int = 1500000
    currency: str
    expires_at: str
    status: TransactionStatus = TransactionStatus.QUOTED


class HoldRequest(BaseModel):
    quote_id: str
    hold_duration_seconds: int = 600


class HoldResponse(BaseModel):
    hold_id: str
    quote_id: str
    hotel_id: str
    room_type: str
    amount: float
    amount_minor: int = 1500000
    currency: str
    status: TransactionStatus = TransactionStatus.HELD
    expires_at: str


# ---------------------------------------------------------
# Booking Models
# ---------------------------------------------------------
class BookingIntent(BaseModel):
    intent_id: str
    agency_id: str = "agency_alpha"
    hotel_id: str = "htl_mumbai_grand"
    hotel_name: str = "Synthetic Hotel Mumbai"
    room_type: str = "deluxe_suite"
    check_in: str = "2026-12-15"
    check_out: str = "2026-12-18"
    amount: float = 15000.0
    amount_minor: Optional[int] = None
    currency: str = "INR"
    guest_name: str = "Aarav Sharma"
    correlation_id: str = Field(default_factory=lambda: f"corr_{datetime.utcnow().timestamp()}")
    cancellation_penalty_rate: float = 0.20  # Default 20% cancellation penalty
    simulate_payment_failure: bool = False
    payment_method: str = "CARD"  # CARD or WALLET
    idempotency_key: Optional[str] = None
    payload_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        return self


class SupplierBookingRequest(BaseModel):
    intent_id: str
    hotel_id: str
    room_type: str
    guest_name: str
    amount: float
    amount_minor: Optional[int] = None
    currency: str = "INR"
    correlation_id: str
    idempotency_key: Optional[str] = None

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        return self


class SupplierReservation(BaseModel):
    supplier_reservation_id: str
    intent_id: str
    hotel_id: str
    room_type: str
    guest_name: str
    amount: float
    amount_minor: int = 1500000
    currency: str
    idempotency_key: Optional[str] = None
    payload_hash: Optional[str] = None
    status: str = "CONFIRMED"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AggregatorBookingRecord(BaseModel):
    booking_id: str
    intent_id: str
    agency_id: str
    status: TransactionStatus
    amount: float = 15000.0
    amount_minor: int = 1500000
    currency: str = "INR"
    cancellation_penalty: float = 0.0
    cancellation_penalty_minor: int = 0
    refundable_entitlement: float = 0.0
    refundable_entitlement_minor: int = 0
    total_refunded: float = 0.0
    total_refunded_minor: int = 0
    primary_supplier_reservation_id: Optional[str] = None
    payment_id: Optional[str] = None
    voucher_id: Optional[str] = None
    retry_count: int = 0
    correlation_id: str
    payment_method: str = "CARD"  # CARD or WALLET
    wallet_hold_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    payload_hash: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BookingInitiateResponse(BaseModel):
    booking_id: str
    intent_id: str
    status: TransactionStatus
    primary_supplier_reservation_id: Optional[str] = None
    payment_id: Optional[str] = None
    voucher_id: Optional[str] = None
    retry_count: int = 0
    message: str
    correlation_id: str


class VoucherStatus(str, Enum):
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"
    REVOKED = "REVOKED"


class VoucherRecord(BaseModel):
    voucher_id: str
    booking_id: str
    agency_id: str
    status: VoucherStatus = VoucherStatus.ISSUED
    issued_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------
# Payment Provider Models
# ---------------------------------------------------------
class PaymentStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class PaymentAuthorizationRequest(BaseModel):
    booking_id: str
    intent_id: str
    agency_id: str
    amount: float
    amount_minor: Optional[int] = None
    currency: str = "INR"
    correlation_id: str
    idempotency_key: Optional[str] = None
    simulate_failure: bool = False
    failure_reason: Optional[str] = None
    provider_id: str = "mock_pay_provider"

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        return self


class PaymentCaptureRequest(BaseModel):
    payment_id: str
    booking_id: str
    amount: float
    amount_minor: Optional[int] = None
    currency: str = "INR"
    correlation_id: str
    idempotency_key: Optional[str] = None
    provider_id: str = "mock_pay_provider"

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        return self


class PaymentTransactionRecord(BaseModel):
    payment_id: str
    booking_id: str
    intent_id: str
    agency_id: str
    amount: float
    amount_minor: int = 1500000
    currency: str
    status: PaymentStatus
    transaction_reference: str
    idempotency_key: Optional[str] = None
    payload_hash: Optional[str] = None
    error_message: Optional[str] = None
    provider: str = "mock_pay_provider"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PaymentResponse(BaseModel):
    payment_id: str
    booking_id: str
    amount: float
    amount_minor: int = 1500000
    currency: str
    status: PaymentStatus
    transaction_reference: str
    message: str
    correlation_id: str


# ---------------------------------------------------------
# Cancellation & Refund Models
# ---------------------------------------------------------
class CancellationRequest(BaseModel):
    reason: str = "Client requested cancellation"
    idempotency_key: Optional[str] = None


class CancellationResponse(BaseModel):
    booking_id: str
    status: TransactionStatus
    penalty_amount: float
    penalty_amount_minor: int = 0
    refundable_entitlement: float
    refundable_entitlement_minor: int = 0
    message: str


class RefundRequest(BaseModel):
    amount: float
    amount_minor: Optional[int] = None
    reason: str = "Cancellation refund"
    idempotency_key: Optional[str] = None

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, "INR")
        return self


class RefundAuthorizationRequest(BaseModel):
    booking_id: Optional[str] = None
    refund_id: str
    amount: Optional[float] = None
    amount_minor: Optional[int] = None
    currency: str = "INR"
    reason: str = "Cancellation refund"
    payment_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    provider: Optional[str] = None

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None and self.amount is not None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        elif self.amount is None and self.amount_minor is not None:
            self.amount = from_minor_units(self.amount_minor, self.currency)
        return self


class RefundRecord(BaseModel):
    refund_id: str
    booking_id: str
    payment_id: str
    agency_id: str
    amount: float
    amount_minor: int
    authorized_amount: float = 0.0
    authorized_amount_minor: int = 0
    processed_amount: float = 0.0
    processed_amount_minor: int = 0
    currency: str
    penalty_amount: float = 0.0
    penalty_amount_minor: int = 0
    refundable_entitlement: float = 0.0
    refundable_entitlement_minor: int = 0
    status: str = "COMPLETED"
    reason: str
    transaction_reference: str
    idempotency_key: Optional[str] = None
    payload_hash: Optional[str] = None
    provider: Optional[str] = None
    event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RefundResponse(BaseModel):
    refund_id: str
    booking_id: str
    amount: float
    amount_minor: int
    currency: str
    total_refunded: float
    total_refunded_minor: int
    remaining_entitlement: float
    remaining_entitlement_minor: int
    status: TransactionStatus
    transaction_reference: str
    message: str


# ---------------------------------------------------------
# Financial Ledger Models (Single-Entry & Double-Entry)
# ---------------------------------------------------------
class LedgerDirection(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerEntryType(str, Enum):
    PAYMENT_CAPTURE = "PAYMENT_CAPTURE"
    PAYMENT = "PAYMENT"
    DEPOSIT = "DEPOSIT"
    REFUND = "REFUND"
    PENALTY = "PENALTY"
    HOLD = "HOLD"


class LedgerEntry(BaseModel):
    entry_id: Optional[int] = None
    transaction_reference: str
    booking_id: str
    agency_id: str
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: float
    amount_minor: int = 0
    currency: str
    balance_after: Optional[float] = None
    balance_after_minor: Optional[int] = None
    journal_batch_id: Optional[str] = None
    account_code: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
    event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookEventRecord(BaseModel):
    event_id: str
    provider: str
    event_type: str
    booking_id: Optional[str] = None
    payload_hash: str
    signature: Optional[str] = None
    processed_count: int = 1
    status: str = "PROCESSED"
    amount_minor: int = 0
    currency: str = "INR"
    payment_id: Optional[str] = None
    refund_id: Optional[str] = None
    first_received_at: datetime = Field(default_factory=datetime.utcnow)
    last_received_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------
# Webhook Provider Registry & Authentication (CW-REV-M2-001)
# ---------------------------------------------------------
SYNTHETIC_PROVIDER_SECRETS = {
    "mock_pay_provider": "whsec_mock_pay_provider_secret_99",
    "stripe_synthetic": "whsec_stripe_synthetic_secret_88",
    "razorpay_synthetic": "whsec_razorpay_synthetic_secret_77",
    "adyen_synthetic": "whsec_adyen_synthetic_secret_66",
}

DEFAULT_WEBHOOK_SECRET = SYNTHETIC_PROVIDER_SECRETS["mock_pay_provider"]


class ProviderSecretRegistry:
    """
    Per-provider synthetic webhook secret registry.
    Maps provider identities to discrete secrets; enforces registration and active status.
    """
    def __init__(self, initial_secrets: Optional[dict] = None):
        self._secrets: dict = dict(initial_secrets or SYNTHETIC_PROVIDER_SECRETS)
        self._disabled: set = set()

    def register_provider(self, provider_id: str, secret: str, enabled: bool = True):
        self._secrets[provider_id] = secret
        if not enabled:
            self._disabled.add(provider_id)
        elif provider_id in self._disabled:
            self._disabled.remove(provider_id)

    def disable_provider(self, provider_id: str):
        self._disabled.add(provider_id)

    def enable_provider(self, provider_id: str):
        if provider_id in self._disabled:
            self._disabled.remove(provider_id)

    def get_secret(self, provider_id: str) -> Optional[str]:
        if provider_id in self._disabled:
            return None
        return self._secrets.get(provider_id)

    def is_registered(self, provider_id: str) -> bool:
        return provider_id in self._secrets and provider_id not in self._disabled

    def list_providers(self) -> List[str]:
        return [p for p in self._secrets.keys() if p not in self._disabled]


provider_registry = ProviderSecretRegistry()


def get_provider_registry() -> ProviderSecretRegistry:
    return provider_registry


# ---------------------------------------------------------
# Timestamp Freshness & Validation (CW-REV-M2-005)
# ---------------------------------------------------------
MAX_CLOCK_SKEW_SECONDS = 300  # 5 minutes permitted clock skew


class WebhookTimestampError(Exception):
    """Raised when webhook timestamp is malformed or invalid."""
    pass


class StaleWebhookEventError(WebhookTimestampError):
    """Raised when an unseen webhook event is older than the allowed tolerance window."""
    pass


class FutureWebhookEventError(WebhookTimestampError):
    """Raised when an unseen webhook event timestamp is too far in the future."""
    pass


def parse_iso_timestamp(ts_str: str) -> datetime:
    """Parses ISO-8601 timestamp string into UTC naive datetime."""
    from datetime import timezone
    try:
        cleaned = ts_str.strip()
        if cleaned.endswith("Z") or cleaned.endswith("z"):
            cleaned = cleaned[:-1] + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception as exc:
        raise WebhookTimestampError(f"Malformed timestamp '{ts_str}': {str(exc)}") from exc


def validate_timestamp_freshness(
    ts_str: str,
    max_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS,
    max_clock_skew_seconds: Optional[int] = None,
    now_func: Optional[object] = None,
    clock_func: Optional[object] = None,
) -> datetime:
    effective_skew = max_clock_skew_seconds if max_clock_skew_seconds is not None else max_skew_seconds
    effective_now = clock_func or now_func
    event_dt = parse_iso_timestamp(ts_str)
    now_dt = effective_now() if (effective_now and callable(effective_now)) else datetime.utcnow()
    diff = (now_dt - event_dt).total_seconds()
    if diff > effective_skew:
        raise StaleWebhookEventError(
            f"Webhook timestamp '{ts_str}' is stale (age: {int(diff)}s > max permitted: {effective_skew}s)"
        )
    if diff < -effective_skew:
        raise FutureWebhookEventError(
            f"Webhook timestamp '{ts_str}' is too far in the future (skew: {int(-diff)}s > max permitted: {effective_skew}s)"
        )
    return event_dt


# ---------------------------------------------------------
# Entity Relationship & Authorization Exceptions (CW-REV-M2-002)
# ---------------------------------------------------------
SUPPORTED_REFUND_EVENT_TYPES = {"refund.processed", "refund.succeeded"}


class UnregisteredProviderError(Exception):
    """Raised when incoming webhook specifies an unregistered or disabled provider."""
    pass


class InvalidWebhookSignatureError(Exception):
    """Raised when incoming webhook signature fails cryptographic verification."""
    pass


class WebhookPayloadConflictError(Exception):
    """Raised when an existing event ID is reused with a conflicting payload hash."""
    pass


class ExcessiveRefundError(Exception):
    """Raised when a refund exceeds remaining entitlement or authorized amount."""
    pass


class UnknownRefundError(Exception):
    """Raised when a refund webhook specifies an unknown or unauthorized refund ID."""
    pass


class PaymentMismatchError(Exception):
    """Raised when payment ID does not match booking or provider obligation."""
    pass


class CurrencyMismatchError(Exception):
    """Raised when webhook currency does not match payment or booking currency."""
    pass


class UnsupportedWebhookEventError(Exception):
    """Raised when webhook event type is not in supported refund events."""
    pass


class InvalidBookingStateForRefundError(Exception):
    """Raised when booking status is not eligible for refunding."""
    pass


class InsufficientWalletFundsError(Exception):
    """Raised when an agency wallet has insufficient available balance for a hold or debit."""
    pass


class RoomSoldOutError(Exception):
    """Raised when supplier hotel inventory has zero available units."""
    pass


class WalletHoldNotFoundError(Exception):
    """Raised when a specified wallet hold cannot be found."""
    pass


class WalletIsolationError(Exception):
    """Raised when an agency attempts to access or mutate another agency's wallet."""
    pass


class AggregatorIdempotencyConflictError(Exception):
    """Raised when an agency reuses an idempotency key with a mismatched booking payload."""
    pass


class AggregatorFencingError(Exception):
    """Raised when a stale worker attempts a state transition after losing claim ownership or lease."""
    pass


# ---------------------------------------------------------
# Milestone 2.2: Agency Wallet & Hotel Inventory Models
# ---------------------------------------------------------
class WalletHoldStatus(str, Enum):
    HELD = "HELD"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"


class WalletTransactionType(str, Enum):
    DEPOSIT = "DEPOSIT"
    HOLD = "HOLD"
    CAPTURE = "CAPTURE"
    RELEASE = "RELEASE"
    REFUND = "REFUND"


class AgencyWalletRecord(BaseModel):
    wallet_id: str
    agency_id: str
    currency: str = "INR"
    funded_balance: float = 0.0
    funded_balance_minor: int = 0
    active_holds: float = 0.0
    active_holds_minor: int = 0
    total_captured: float = 0.0
    total_captured_minor: int = 0
    total_refunded: float = 0.0
    total_refunded_minor: int = 0
    available_balance: float = 0.0
    available_balance_minor: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BookingClaimStatus(str, Enum):
    CLAIMED = "CLAIMED"
    WALLET_HOLD_CREATED = "WALLET_HOLD_CREATED"
    SUPPLIER_REQUEST_PENDING = "SUPPLIER_REQUEST_PENDING"
    SUPPLIER_CONFIRMED = "SUPPLIER_CONFIRMED"
    WALLET_CAPTURE_PENDING = "WALLET_CAPTURE_PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class BookingOperationClaimRecord(BaseModel):
    agency_id: str
    idempotency_key: str
    payload_hash: str
    operation_id: str
    booking_id: str
    status: BookingClaimStatus
    owner_token: str
    fencing_token: int = 1
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    last_owner_token: Optional[str] = None
    persisted_stage: Optional[str] = None
    created_at: str
    updated_at: str
    failure_reason: Optional[str] = None
    correlation_id: Optional[str] = None


class SagaRecoveryRecord(BaseModel):
    recovery_id: str
    booking_id: str
    agency_id: str
    target_supplier_reservation_id: Optional[str] = None
    wallet_hold_id: Optional[str] = None
    action: str
    status: str
    attempt_count: int = 1
    last_error: Optional[str] = None
    lease_expires_at: Optional[str] = None
    owner_token: Optional[str] = None
    fencing_token: int = 0
    next_attempt_at: Optional[str] = None
    created_at: str
    updated_at: str


class WalletHoldRecord(BaseModel):
    hold_id: str
    wallet_id: str
    agency_id: str
    booking_id: str
    amount: float
    amount_minor: int
    currency: str = "INR"
    status: WalletHoldStatus = WalletHoldStatus.HELD
    idempotency_key: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WalletTransactionRecord(BaseModel):
    transaction_id: str
    wallet_id: str
    agency_id: str
    booking_id: Optional[str] = None
    hold_id: Optional[str] = None
    transaction_type: WalletTransactionType
    amount: float
    amount_minor: int
    currency: str = "INR"
    balance_after_minor: int
    holds_after_minor: int
    reference: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WalletDepositRequest(BaseModel):
    amount: float
    amount_minor: Optional[int] = None
    currency: str = "INR"
    reference: Optional[str] = None

    @model_validator(mode="after")
    def populate_minor(self):
        if self.amount_minor is None:
            self.amount_minor = to_minor_units(self.amount, self.currency)
        return self


class HotelInventoryRecord(BaseModel):
    hotel_id: str
    room_type: str
    capacity: int = 1
    allocated_units: int = 0
    available_units: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class HotelInventoryUpdateRequest(BaseModel):
    capacity: int
    available_units: Optional[int] = None


class SupplierCancelRequest(BaseModel):
    supplier_reservation_id: str
    reason: str = "Client cancellation or saga compensation"


# ---------------------------------------------------------
# HMAC Signature Computation & Verification
# ---------------------------------------------------------
def compute_webhook_signature(secret: str, payload_dict: dict) -> str:
    """
    Computes deterministic HMAC-SHA256 signature over sorted key-value pairs (excluding 'signature').
    """
    import hmac
    import hashlib
    keys = sorted(k for k in payload_dict.keys() if k != "signature")
    canonical = "&".join(f"{k}={payload_dict[k]}" for k in keys)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_webhook_signature(secret: str, payload_dict: dict, signature: Optional[str]) -> bool:
    """
    Constant-time comparison for webhook HMAC-SHA256 signatures.
    """
    import hmac
    if not signature:
        return False
    expected = compute_webhook_signature(secret, payload_dict)
    return hmac.compare_digest(expected, signature)


class WebhookPayload(BaseModel):
    provider_id: str = "mock_pay_provider"
    event_id: Optional[str] = None
    event_type: str = "refund.processed"
    booking_id: str
    payment_id: str
    refund_id: str
    amount_minor: int
    currency: str = "INR"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    signature: Optional[str] = None

    def sign(self, secret: Optional[str] = None) -> "WebhookPayload":
        sec = secret or get_provider_registry().get_secret(self.provider_id)
        if not sec:
            raise ValueError(f"No secret registered for provider '{self.provider_id}'")
        d = self.model_dump(mode="json")
        self.signature = compute_webhook_signature(sec, d)
        return self


class WebhookProcessingResponse(BaseModel):
    status: str
    event_id: str
    duplicate: bool
    processed_count: int
    message: str
    booking_status: Optional[TransactionStatus] = None
    total_refunded_minor: Optional[int] = None


"""
Crosswire M2.9: Target Adapter Contracts & Capability Model.
Defines target-independent execution adapters, ground-truth observation contracts,
capability flags, and normalized evidence schemas.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Set, Generic, TypeVar
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone


class TargetCapability(str, Enum):
    """Capabilities that synthetic or real target travel platforms may advertise."""
    BOOKING_INTENTS = "BOOKING_INTENTS"
    SUPPLIER_RESERVATIONS = "SUPPLIER_RESERVATIONS"
    IDEMPOTENCY_KEYS = "IDEMPOTENCY_KEYS"
    REFUND_WEBHOOKS = "REFUND_WEBHOOKS"
    LEDGER_OBSERVATIONS = "LEDGER_OBSERVATIONS"
    CANCELLATION = "CANCELLATION"
    MULTI_SUPPLIER_OUTBOX = "MULTI_SUPPLIER_OUTBOX"
    CONTROLLED_FAULT_INJECTION = "CONTROLLED_FAULT_INJECTION"
    GROUND_TRUTH_OBSERVATIONS = "GROUND_TRUTH_OBSERVATIONS"
    WALLET_OPERATIONS = "WALLET_OPERATIONS"
    AUTHORIZATION_PERIMETER = "AUTHORIZATION_PERIMETER"
    QUOTE_OPERATIONS = "QUOTE_OPERATIONS"
    VOUCHER_ISSUANCE = "VOUCHER_ISSUANCE"
    REFUND_BOUNDEDNESS = "REFUND_BOUNDEDNESS"


@dataclass(frozen=True)
class TargetMetadata:
    """Identity and capability metadata for an integrated target platform."""
    target_id: str
    display_name: str
    version: str
    description: str
    base_url: str
    capabilities: Set[TargetCapability]
    is_local_sandbox: bool = True

    @property
    def name(self) -> str:
        return self.display_name

    def has_capability(self, cap: TargetCapability) -> bool:
        return cap in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "display_name": self.display_name,
            "name": self.display_name,
            "version": self.version,
            "description": self.description,
            "base_url": self.base_url,
            "capabilities": [c.value for c in self.capabilities],
            "is_local_sandbox": self.is_local_sandbox,
        }


# ============================================================================
# Normalized Evidence Schemas (Crosswire Core Observation Domain)
# ============================================================================

@dataclass
class BookingObservation:
    """Target-independent normalized booking state."""
    booking_intent_id: str
    booking_state: str  # CONFIRMED, FAILED, PENDING, CANCELLED, UNKNOWN
    amount: float
    amount_minor: int
    currency: str
    primary_supplier_reservation_id: Optional[str] = None
    retry_count: int = 0
    voucher_id: Optional[str] = None
    target_evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SupplierReservationObservation:
    """Target-independent normalized supplier reservation state."""
    reservation_id: str
    intent_id: str
    supplier_id: str
    reservation_state: str  # CONFIRMED, CANCELLED, PENDING
    amount: float
    currency: str
    idempotency_key: Optional[str] = None
    created_at: Optional[str] = None
    target_evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def supplier_reservation_id(self) -> str:
        return self.reservation_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "supplier_reservation_id": self.reservation_id,
            "intent_id": self.intent_id,
            "supplier_id": self.supplier_id,
            "reservation_state": self.reservation_state,
            "amount": self.amount,
            "currency": self.currency,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "target_evidence": self.target_evidence,
        }


@dataclass
class LedgerObservation:
    """Target-independent financial ledger observation."""
    ledger_entry_id: str
    event_id: Optional[str]
    booking_id: Optional[str]
    amount: float
    amount_minor: int
    currency: str
    entry_type: str  # REFUND, CAPTURE, HOLD, FEE
    direction: str   # CREDIT, DEBIT
    target_evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def movement_type(self) -> str:
        return self.entry_type

    @property
    def transaction_reference(self) -> str:
        return self.event_id or self.ledger_entry_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ledger_entry_id": self.ledger_entry_id,
            "event_id": self.event_id,
            "booking_id": self.booking_id,
            "amount": self.amount,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "entry_type": self.entry_type,
            "direction": self.direction,
            "target_evidence": self.target_evidence,
        }


@dataclass
class WebhookObservation:
    """Target-independent webhook processing history observation."""
    provider_event_id: str
    provider_id: str
    delivery_count: int
    accepted_count: int
    financial_effect_count: int
    is_deduplicated: bool
    target_evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WalletObservation:
    """Target-independent agency wallet observation."""
    agency_id: str
    available_balance: float
    available_balance_minor: int
    active_holds_minor: int
    currency: str
    is_overdrawn: bool = False
    target_evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agency_id": self.agency_id,
            "available_balance": self.available_balance,
            "available_balance_minor": self.available_balance_minor,
            "active_holds_minor": self.active_holds_minor,
            "currency": self.currency,
            "is_overdrawn": self.is_overdrawn,
            "target_evidence": self.target_evidence,
        }


T = TypeVar("T")


@dataclass
class ObservationResult(Generic[T]):
    """
    Encapsulates observation retrieval.
    Enforces clear distinction between an unavailable observation and an observation
    proving an invariant passed or failed. Never treats unavailable as passed.
    """
    available: bool
    data: Optional[T] = None
    unavailable_reason: Optional[str] = None
    observed_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def success(cls, data: T) -> "ObservationResult[T]":
        return cls(available=True, data=data)

    @classmethod
    def unavailable(cls, reason: str) -> "ObservationResult[T]":
        return cls(available=False, data=None, unavailable_reason=reason)


@dataclass
class NormalizedBookingResult:
    """Standardized response from an execution adapter booking submission."""
    booking_id: str
    intent_id: str
    status: str
    amount: float
    currency: str
    retry_count: int = 0
    supplier_reservation_ids: List[str] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Target Adapter Interfaces
# ============================================================================

class TargetExecutionAdapter(ABC):
    """
    Abstract contract for black-box/gray-box execution against an external target platform.
    """
    @property
    @abstractmethod
    def target_id(self) -> str:
        pass

    @abstractmethod
    def get_metadata(self) -> TargetMetadata:
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        pass

    @abstractmethod
    def reset(self) -> bool:
        """Cleans target test database / state for reproducible deterministic testing."""
        pass

    def reset_target(self) -> bool:
        """Alias for reset()."""
        return self.reset()

    @abstractmethod
    def configure_fault_injection(
        self, scenario_id: str, mode: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        """Configures controlled fault injection (e.g. commit-then-timeout delay)."""
        pass

    @abstractmethod
    async def create_booking_intent(
        self,
        intent_id: str,
        hotel_id: str = "htl_default_grand",
        amount: float = 15000.0,
        currency: str = "INR",
        guest_name: str = "Ada Lovelace",
        idempotency_key: Optional[str] = None,
    ) -> Any:
        pass

    @abstractmethod
    async def submit_booking(self, intent: Any) -> NormalizedBookingResult:
        pass

    @abstractmethod
    async def cancel_booking(self, booking_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def deliver_refund_webhook(self, payload: Dict[str, Any]) -> int:
        """Delivers a refund webhook and returns HTTP status code."""
        pass

    @abstractmethod
    def generate_refund_webhook(
        self,
        booking_id: str,
        amount_minor: int,
        currency: str = "INR",
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        pass


class GroundTruthObservationAdapter(ABC):
    """
    Abstract contract for read-only ground-truth observation inspection.
    Normalizes target-specific evidence into canonical Crosswire schemas.
    """
    @property
    @abstractmethod
    def target_id(self) -> str:
        pass

    @abstractmethod
    def get_booking_observation(self, intent_id: str) -> ObservationResult[BookingObservation]:
        pass

    def get_booking_observations(self, intent_id: str) -> ObservationResult[List[BookingObservation]]:
        res = self.get_booking_observation(intent_id)
        if res.available and res.data:
            return ObservationResult.success([res.data])
        return ObservationResult.unavailable(res.unavailable_reason or "Booking observations unavailable")

    @abstractmethod
    def get_supplier_reservations(
        self, intent_id: str
    ) -> ObservationResult[List[SupplierReservationObservation]]:
        pass

    @abstractmethod
    def get_ledger_observations(
        self, booking_id_or_event_id: str
    ) -> ObservationResult[List[LedgerObservation]]:
        pass

    def get_ledger_entries(
        self, booking_id_or_event_id: str
    ) -> ObservationResult[List[LedgerObservation]]:
        """Alias for get_ledger_observations."""
        return self.get_ledger_observations(booking_id_or_event_id)

    @abstractmethod
    def get_webhook_observation(
        self, event_id: str
    ) -> ObservationResult[WebhookObservation]:
        pass

    def get_webhook_history(
        self, event_id: str
    ) -> ObservationResult[WebhookObservation]:
        """Alias for get_webhook_observation."""
        return self.get_webhook_observation(event_id)

    @abstractmethod
    def get_outbox_observations(
        self, booking_id: str
    ) -> ObservationResult[Dict[str, Any]]:
        pass

    def get_wallet_observation(
        self, agency_id: str
    ) -> ObservationResult[WalletObservation]:
        """Optional target-independent wallet observation."""
        return ObservationResult.unavailable(
            f"Target '{self.target_id}' does not implement direct wallet observation"
        )

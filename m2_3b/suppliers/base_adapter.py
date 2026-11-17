"""
Crosswire M2.3B Supplier Capability Contracts.
Defines base adapter interface and capability-aware execution contracts.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class SupplierError(Exception):
    """Base exception for supplier communication failures."""
    pass

class SupplierTimeoutError(SupplierError):
    """Raised when a supplier call times out or network drops before receiving a response."""
    pass

class SupplierDefinitiveRejection(SupplierError):
    """Raised when a supplier explicitly and definitively rejects a request (e.g. rate expired, sold out)."""
    def __init__(self, reason: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}

class SupplierAmbiguousError(SupplierError):
    """Raised when supplier returns an ambiguous 5xx or connection error after processing may have started."""
    pass

class BaseSupplierAdapter(ABC):
    """Abstract base class for all synthetic and real supplier adapters."""

    def __init__(
        self,
        supplier_id: str,
        create_capability: str = "IDEMPOTENT_WITH_LOOKUP",
        cancel_capability: str = "LOOKUP_ONLY"
    ):
        self.supplier_id = supplier_id
        self.create_capability = create_capability
        self.cancel_capability = cancel_capability

    @abstractmethod
    def create_reservation(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Dispatches reservation creation request."""
        pass

    @abstractmethod
    def cancel_reservation(
        self,
        supplier_reservation_id: str,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Dispatches cancellation request."""
        pass

    @abstractmethod
    def lookup_reservation(
        self,
        query: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Looks up existing reservation by reference, child intent ID, or guest details."""
        pass

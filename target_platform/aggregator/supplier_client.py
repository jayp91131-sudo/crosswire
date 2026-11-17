"""
HTTPX client for communicating from Travel Aggregator to Mock Hotel Supplier over HTTP.
Provides actual HTTP serialization, network timeout detection, and status translation.
Uses asyncio.wait_for to ensure timeouts are reliably enforced across real TCP sockets and ASGI transports.
"""

import os
import asyncio
from typing import Optional
import httpx
from target_platform.models import (
    SupplierBookingRequest,
    SupplierReservation,
    SupplierCapability,
    AggregatorIdempotencyConflictError,
)


class AggregatorSupplierTimeoutError(Exception):
    """Raised when an HTTP timeout occurs while awaiting supplier response."""
    pass


class AggregatorSupplierSoldOutError(Exception):
    """Raised when supplier returns 409 due to exhausted inventory."""
    pass


class AggregatorSupplierUnsupportedError(Exception):
    """Raised when the supplier returns 501 Not Implemented for reconciliation."""
    pass


class AggregatorSupplierHttpError(Exception):
    """Raised for unexpected non-200 responses from supplier."""
    pass


class SupplierClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 0.20,  # Aggregator expects supplier to reply within 200ms
        transport: Optional[httpx.AsyncBaseTransport] = None,
        capability: SupplierCapability = SupplierCapability.NEITHER,
    ):
        self.base_url = base_url or os.getenv("SUPPLIER_BASE_URL", "http://127.0.0.1:8001")
        self.timeout = timeout
        self.transport = transport

        # Unknown, missing, or invalid capability configuration must fail closed to NEITHER
        if capability is None:
            self.capability = SupplierCapability.NEITHER
        elif isinstance(capability, SupplierCapability):
            self.capability = capability
        elif isinstance(capability, str):
            try:
                self.capability = SupplierCapability(capability)
            except ValueError:
                self.capability = SupplierCapability.NEITHER
        else:
            self.capability = SupplierCapability.NEITHER

    @property
    def supports_idempotency(self) -> bool:
        return self.capability == SupplierCapability.IDEMPOTENT_WITH_LOOKUP

    @property
    def supports_lookup(self) -> bool:
        return self.capability in (SupplierCapability.IDEMPOTENT_WITH_LOOKUP, SupplierCapability.LOOKUP_ONLY)

    async def call_create_reservation(
        self, req: SupplierBookingRequest, timeout_override: Optional[float] = None
    ) -> SupplierReservation:
        """
        Dispatches booking request to supplier over HTTP.
        If supplier delays past timeout, timeout exception is raised -> converted to AggregatorSupplierTimeoutError.
        """
        timeout = timeout_override or self.timeout
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self.transport) as client:
            try:
                resp = await asyncio.wait_for(
                    client.post(
                        "/supplier/v1/reservations",
                        json=req.model_dump(mode="json"),
                    ),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
                raise AggregatorSupplierTimeoutError(
                    f"HTTP timeout while waiting for supplier confirmation: {str(exc)}"
                ) from exc
            except httpx.RequestError as exc:
                raise AggregatorSupplierHttpError(f"HTTP transport failure: {str(exc)}") from exc

            if resp.status_code == 200:
                return SupplierReservation.model_validate(resp.json())
            elif resp.status_code == 409 and "sold out" in resp.text.lower():
                raise AggregatorSupplierSoldOutError(f"Hotel room sold out: {resp.text}")
            elif resp.status_code == 409 and ("idempotency" in resp.text.lower() or "conflict" in resp.text.lower()):
                raise AggregatorIdempotencyConflictError(f"Supplier idempotency conflict: {resp.text}")
            else:
                raise AggregatorSupplierHttpError(
                    f"Supplier returned HTTP {resp.status_code}: {resp.text}"
                )

    async def call_cancel_reservation(
        self, supplier_reservation_id: str, reason: str = "Saga compensation", timeout_override: Optional[float] = 1.0
    ) -> bool:
        """Calls supplier cancellation endpoint for compensation."""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout_override, transport=self.transport) as client:
            try:
                resp = await client.post(
                    f"/supplier/v1/reservations/{supplier_reservation_id}/cancel",
                    json={"supplier_reservation_id": supplier_reservation_id, "reason": reason},
                )
                return resp.status_code == 200
            except Exception:
                return False

    async def call_reconcile_by_reference(
        self, intent_id: str, timeout_override: Optional[float] = 1.0
    ) -> Optional[SupplierReservation]:
        """
        Attempts to reconcile with supplier by clientReference / intent_id.
        """
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout_override, transport=self.transport) as client:
            try:
                resp = await asyncio.wait_for(
                    client.get(f"/supplier/v1/reconcile/{intent_id}"),
                    timeout=timeout_override,
                )
            except (asyncio.TimeoutError, httpx.TimeoutException, httpx.RequestError) as exc:
                raise AggregatorSupplierHttpError(f"Failed to query supplier reconciliation: {str(exc)}")

            if resp.status_code == 200:
                return SupplierReservation.model_validate(resp.json())
            elif resp.status_code == 404:
                return None
            elif resp.status_code == 501:
                raise AggregatorSupplierUnsupportedError("Supplier does not support reconciliation.")
            else:
                raise AggregatorSupplierHttpError(f"Supplier reconciliation returned HTTP {resp.status_code}")

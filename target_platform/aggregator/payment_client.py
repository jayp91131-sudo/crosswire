"""
HTTPX client for communicating from Travel Aggregator to Mock Payment Provider.
Provides serialization, timeout handling, and error mapping for financial transactions.
Defaults to in-process ASGITransport targeting payment_app if no external URL is specified.
"""

import os
from typing import Optional
import httpx
from target_platform.models import (
    PaymentAuthorizationRequest,
    PaymentCaptureRequest,
    PaymentResponse,
    PaymentTransactionRecord,
)


class AggregatorPaymentError(Exception):
    """Raised when payment operations fail."""
    pass


class AggregatorPaymentConflictError(AggregatorPaymentError):
    """Raised when an idempotency key conflict occurs."""
    pass


class PaymentClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 2.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = base_url or os.getenv("PAYMENT_BASE_URL", "http://payment.local")
        self.timeout = timeout
        if transport is not None:
            self.transport = transport
        elif os.getenv("PAYMENT_BASE_URL"):
            self.transport = None  # Standard TCP socket transport
        else:
            # Default to in-process ASGI transport targeting payment_app
            from target_platform.payment.app import payment_app
            self.transport = httpx.ASGITransport(app=payment_app)

    async def call_authorize(self, req: PaymentAuthorizationRequest) -> PaymentResponse:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(
                "/payment/v1/authorize",
                json=req.model_dump(mode="json"),
            )
            if resp.status_code == 409:
                raise AggregatorPaymentConflictError(f"Payment conflict: {resp.text}")
            if resp.status_code != 200:
                raise AggregatorPaymentError(f"Payment authorization failed ({resp.status_code}): {resp.text}")
            return PaymentResponse.model_validate(resp.json())

    async def call_capture(self, req: PaymentCaptureRequest) -> PaymentResponse:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(
                "/payment/v1/capture",
                json=req.model_dump(mode="json"),
            )
            if resp.status_code != 200:
                raise AggregatorPaymentError(f"Payment capture failed ({resp.status_code}): {resp.text}")
            return PaymentResponse.model_validate(resp.json())

    async def call_refund(self, payment_id: str, amount: float) -> PaymentResponse:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(
                f"/payment/v1/payments/{payment_id}/refund",
                json={"amount": amount},
            )
            if resp.status_code != 200:
                raise AggregatorPaymentError(f"Payment refund failed ({resp.status_code}): {resp.text}")
            return PaymentResponse.model_validate(resp.json())

    async def call_get_status(self, payment_id: str) -> PaymentTransactionRecord:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
            resp = await client.get(f"/payment/v1/payments/{payment_id}")
            if resp.status_code != 200:
                raise AggregatorPaymentError(f"Failed to fetch payment ({resp.status_code}): {resp.text}")
            return PaymentTransactionRecord.model_validate(resp.json())

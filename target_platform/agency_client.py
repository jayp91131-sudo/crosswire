"""
Synthetic B2B Travel Agency Client (Agency Alpha).
Provides client credentials, intent generation, booking submission, quote/hold creation,
cancellation, refunding, and status retrieval.
"""

from typing import Optional, List
from datetime import datetime
import httpx
from target_platform.money import to_minor_units, from_minor_units
from target_platform.models import (
    BookingIntent,
    BookingInitiateResponse,
    AggregatorBookingRecord,
    QuoteRequest,
    QuoteResponse,
    HoldRequest,
    HoldResponse,
    CancellationRequest,
    CancellationResponse,
    RefundRequest,
    RefundResponse,
    RefundAuthorizationRequest,
    LedgerEntry,
    RefundRecord,
    AgencyWalletRecord,
    WalletDepositRequest,
)


class AgencyClient:
    def __init__(
        self,
        agency_id: str = "agency_alpha",
        api_key: str = "secret_alpha_key_99",
        aggregator_base_url: str = "http://127.0.0.1:8000",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.agency_id = agency_id
        self.api_key = api_key
        self.aggregator_base_url = aggregator_base_url
        self.transport = transport

    def _headers(self) -> dict:
        return {
            "X-Agency-ID": self.agency_id,
            "X-API-Key": self.api_key,
        }

    def create_synthetic_intent(
        self,
        intent_id: str = "INTENT-001",
        hotel_id: str = "htl_mumbai_grand",
        hotel_name: str = "Synthetic Hotel Mumbai",
        room_type: str = "deluxe_suite",
        amount: float = 15000.0,
        amount_minor: Optional[int] = None,
        currency: str = "INR",
        guest_name: str = "Aarav Sharma",
        cancellation_penalty_rate: float = 0.20,
        simulate_payment_failure: bool = False,
        payment_method: str = "CARD",
        idempotency_key: Optional[str] = None,
    ) -> BookingIntent:
        """Constructs a deterministic synthetic hotel booking intent for Agency."""
        correlation_id = f"corr_{self.agency_id}_{intent_id}_{int(datetime.utcnow().timestamp())}"
        return BookingIntent(
            intent_id=intent_id,
            agency_id=self.agency_id,
            hotel_id=hotel_id,
            hotel_name=hotel_name,
            room_type=room_type,
            check_in="2026-12-15",
            check_out="2026-12-18",
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            guest_name=guest_name,
            correlation_id=correlation_id,
            cancellation_penalty_rate=cancellation_penalty_rate,
            simulate_payment_failure=simulate_payment_failure,
            payment_method=payment_method,
            idempotency_key=idempotency_key,
        )

    async def create_quote(self, req: QuoteRequest) -> QuoteResponse:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.post("/api/v1/quotes", json=req.model_dump(mode="json"), headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Quote creation failed: HTTP {resp.status_code} - {resp.text}")
            return QuoteResponse.model_validate(resp.json())

    async def create_hold(self, req: HoldRequest) -> HoldResponse:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.post("/api/v1/holds", json=req.model_dump(mode="json"), headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Hold creation failed: HTTP {resp.status_code} - {resp.text}")
            return HoldResponse.model_validate(resp.json())

    async def submit_booking(self, intent: BookingIntent) -> BookingInitiateResponse:
        """Dispatches booking intent to the Aggregator API."""
        async with httpx.AsyncClient(
            base_url=self.aggregator_base_url, transport=self.transport, timeout=10.0
        ) as client:
            resp = await client.post(
                "/api/v1/bookings",
                json=intent.model_dump(mode="json"),
                headers=self._headers(),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Aggregator booking failed: HTTP {resp.status_code} - {resp.text}")
            return BookingInitiateResponse.model_validate(resp.json())

    async def get_booking_status(self, booking_id: str) -> AggregatorBookingRecord:
        """Queries booking record by booking ID."""
        async with httpx.AsyncClient(
            base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0
        ) as client:
            resp = await client.get(f"/api/v1/bookings/{booking_id}", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch booking: HTTP {resp.status_code}")
            return AggregatorBookingRecord.model_validate(resp.json())

    async def get_booking_by_intent(self, intent_id: str) -> AggregatorBookingRecord:
        """Queries booking record by original intent ID."""
        async with httpx.AsyncClient(
            base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0
        ) as client:
            resp = await client.get(f"/api/v1/bookings/by-intent/{intent_id}", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch booking by intent: HTTP {resp.status_code}")
            return AggregatorBookingRecord.model_validate(resp.json())

    async def issue_voucher(self, booking_id: str) -> AggregatorBookingRecord:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.post(f"/api/v1/bookings/{booking_id}/voucher", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Voucher issuance failed: HTTP {resp.status_code} - {resp.text}")
            return AggregatorBookingRecord.model_validate(resp.json())

    async def cancel_booking(
        self, booking_id: str, reason: str = "Client requested cancellation", idempotency_key: Optional[str] = None
    ) -> CancellationResponse:
        req = CancellationRequest(reason=reason, idempotency_key=idempotency_key)
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.post(
                f"/api/v1/bookings/{booking_id}/cancel",
                json=req.model_dump(mode="json"),
                headers=self._headers(),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Cancellation failed: HTTP {resp.status_code} - {resp.text}")
            return CancellationResponse.model_validate(resp.json())

    async def refund_booking(
        self, booking_id: str, amount: float, reason: str = "Cancellation refund", idempotency_key: Optional[str] = None
    ) -> RefundResponse:
        req = RefundRequest(amount=amount, reason=reason, idempotency_key=idempotency_key)
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.post(
                f"/api/v1/bookings/{booking_id}/refund",
                json=req.model_dump(mode="json"),
                headers=self._headers(),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Refund failed: HTTP {resp.status_code} - {resp.text}")
            return RefundResponse.model_validate(resp.json())

    async def get_ledger(self, booking_id: str) -> List[LedgerEntry]:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.get(f"/api/v1/bookings/{booking_id}/ledger", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch ledger: HTTP {resp.status_code} - {resp.text}")
            return [LedgerEntry.model_validate(e) for e in resp.json()]

    async def get_refunds(self, booking_id: str) -> List[RefundRecord]:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=5.0) as client:
            resp = await client.get(f"/api/v1/bookings/{booking_id}/refunds", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch refunds: HTTP {resp.status_code} - {resp.text}")
            return [RefundRecord.model_validate(r) for r in resp.json()]

    async def authorize_refund(
        self,
        booking_id: str,
        refund_id: str,
        amount_minor: Optional[int] = None,
        amount: Optional[float] = None,
        currency: str = "INR",
        payment_id: Optional[str] = None,
        reason: str = "Authorized refund instruction",
        idempotency_key: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> RefundRecord:
        body = {
            "booking_id": booking_id,
            "refund_id": refund_id,
            "currency": currency,
            "reason": reason,
        }
        if amount_minor is not None:
            body["amount_minor"] = amount_minor
            body["amount"] = from_minor_units(amount_minor, currency)
        elif amount is not None:
            body["amount"] = amount
            body["amount_minor"] = to_minor_units(amount, currency)
        if payment_id is not None:
            body["payment_id"] = payment_id
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        if provider is not None:
            body["provider"] = provider

        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=10.0) as client:
            resp = await client.post(
                f"/api/v1/bookings/{booking_id}/refunds/authorize",
                json=body,
                headers=self._headers(),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to authorize refund: HTTP {resp.status_code} - {resp.text}")
            return RefundRecord.model_validate(resp.json())

    async def deliver_webhook(
        self,
        payload,
        signature_override: Optional[str] = None,
    ) -> httpx.Response:
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        headers = {}
        if signature_override is not None:
            headers["X-Webhook-Signature"] = signature_override
        elif isinstance(body, dict) and body.get("signature"):
            headers["X-Webhook-Signature"] = body["signature"]

        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=10.0) as client:
            return await client.post("/api/v1/webhooks/payment", json=body, headers=headers)

    async def deposit_wallet(
        self, amount: float, amount_minor: Optional[int] = None, currency: str = "INR", reference: Optional[str] = None
    ) -> AgencyWalletRecord:
        body = {
            "amount": amount,
            "currency": currency,
        }
        if amount_minor is not None:
            body["amount_minor"] = amount_minor
        if reference is not None:
            body["reference"] = reference

        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=10.0) as client:
            resp = await client.post("/api/v1/wallets/deposit", json=body, headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Wallet deposit failed: HTTP {resp.status_code} - {resp.text}")
            return AgencyWalletRecord.model_validate(resp.json())

    async def get_wallet(self) -> AgencyWalletRecord:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=10.0) as client:
            resp = await client.get("/api/v1/wallets/me", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Get wallet failed: HTTP {resp.status_code} - {resp.text}")
            return AgencyWalletRecord.model_validate(resp.json())

    async def get_agency_wallet(self, target_agency_id: str) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self.aggregator_base_url, transport=self.transport, timeout=10.0) as client:
            return await client.get(f"/api/v1/wallets/{target_agency_id}", headers=self._headers())

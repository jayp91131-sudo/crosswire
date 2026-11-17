"""
FastAPI application for Travel Aggregator.
Exposes booking initiation, quote/hold, voucher, cancellation, refund, ledger lookup,
and admin controls with agency authentication, intent ownership enforcement,
correlation ID preservation, and secret sanitization.
"""

import os
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel

from target_platform.models import (
    BookingIntent,
    BookingInitiateResponse,
    AggregatorBookingRecord,
    AggregatorMode,
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
    PaymentTransactionRecord,
    WebhookPayload,
    WebhookProcessingResponse,
    UnregisteredProviderError,
    WebhookTimestampError,
    StaleWebhookEventError,
    FutureWebhookEventError,
    UnknownRefundError,
    PaymentMismatchError,
    CurrencyMismatchError,
    UnsupportedWebhookEventError,
    InvalidBookingStateForRefundError,
    AgencyWalletRecord,
    WalletDepositRequest,
    InsufficientWalletFundsError,
    RoomSoldOutError,
    WalletHoldNotFoundError,
    WalletIsolationError,
    AggregatorIdempotencyConflictError,
)
from target_platform.money import to_minor_units
from target_platform.aggregator.booking_service import (
    get_aggregator_booking_service,
    InvalidStateTransitionError,
    ExcessiveRefundError,
    WebhookPayloadConflictError,
    InvalidWebhookSignatureError,
)
from target_platform.aggregator.database import (
    get_aggregator_db,
    IdempotencyPayloadConflictError,
)
from target_platform.aggregator.saga_recovery_worker import SagaRecoveryWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_enabled = os.getenv("SAGA_RECOVERY_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")
    poll_interval = float(os.getenv("SAGA_RECOVERY_POLL_INTERVAL", "1.0"))
    worker = None
    if worker_enabled:
        worker = SagaRecoveryWorker()
        worker.start_background_worker(poll_interval=poll_interval)
        app.state.saga_recovery_worker = worker
    try:
        yield
    finally:
        if worker is not None:
            await worker.stop_background_worker_async()


aggregator_app = FastAPI(
    title="Crosswire Travel Aggregator Platform",
    description="B2B Travel Aggregator handling agency bookings, payment fulfillment, and refunds",
    version="2.0.0",
    lifespan=lifespan,
)


class AggregatorConfigUpdateRequest(BaseModel):
    mode: Optional[AggregatorMode] = None
    webhook_mode: Optional[AggregatorMode] = None
    wallet_mode: Optional[AggregatorMode] = None
    supplier_base_url: Optional[str] = None
    supplier_timeout: Optional[float] = None
    payment_base_url: Optional[str] = None


def _check_auth(x_agency_id: Optional[str], x_api_key: Optional[str]) -> str:
    if not x_agency_id:
        raise HTTPException(status_code=401, detail="Authentication required: Missing X-Agency-ID header")
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Authentication required: Missing X-API-Key header")
    db = get_aggregator_db()
    if not db.authenticate_agency(x_agency_id, x_api_key):
        raise HTTPException(status_code=401, detail="Invalid agency credentials")
    return x_agency_id


@aggregator_app.get("/")
def health_check():
    service = get_aggregator_booking_service()
    return {
        "service": "Travel Aggregator Platform",
        "status": "online",
        "mode": service.mode.value,
    }


# ---------------------------------------------------------
# Quote and Hold Endpoints
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/quotes", response_model=QuoteResponse)
async def create_quote(
    req: QuoteRequest,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    service = get_aggregator_booking_service()
    return await service.create_quote(req, agency_id)


@aggregator_app.post("/api/v1/holds", response_model=HoldResponse)
async def create_hold(
    req: HoldRequest,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    service = get_aggregator_booking_service()
    try:
        return await service.create_hold(req, agency_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------
# Booking Endpoints
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/bookings", response_model=BookingInitiateResponse)
async def create_booking(
    intent: BookingIntent,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    # CW-SEC-M2-206: Mandatory authentication and intent ownership verification
    authenticated_agency = _check_auth(x_agency_id, x_api_key)
    if intent.agency_id and intent.agency_id != authenticated_agency:
        raise HTTPException(
            status_code=403,
            detail=f"Intent ownership mismatch: Authenticated as '{authenticated_agency}' but intent is for '{intent.agency_id}'",
        )
    # The trusted agency identity must come from authenticated context, never permit body to override
    intent.agency_id = authenticated_agency

    service = get_aggregator_booking_service()
    try:
        record = await service.process_booking_intent(intent)
    except InsufficientWalletFundsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RoomSoldOutError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (AggregatorIdempotencyConflictError, IdempotencyPayloadConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    message = f"Booking status: {record.status.value}"
    if record.retry_count > 0:
        message += f" (retries: {record.retry_count})"

    return BookingInitiateResponse(
        booking_id=record.booking_id,
        intent_id=record.intent_id,
        status=record.status,
        primary_supplier_reservation_id=record.primary_supplier_reservation_id,
        payment_id=record.payment_id,
        voucher_id=record.voucher_id,
        retry_count=record.retry_count,
        message=message,
        correlation_id=record.correlation_id,
    )


@aggregator_app.get("/api/v1/bookings/{booking_id}", response_model=AggregatorBookingRecord)
def get_booking(
    booking_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    authenticated_agency = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    record = db.get_booking_record(booking_id, requesting_agency_id=authenticated_agency)
    if not record:
        raise HTTPException(status_code=404, detail="Booking not found or unauthorized")
    return record


@aggregator_app.get("/api/v1/bookings/by-intent/{intent_id}", response_model=AggregatorBookingRecord)
def get_booking_by_intent(
    intent_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    authenticated_agency = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    record = db.get_booking_by_intent(intent_id, requesting_agency_id=authenticated_agency)
    if not record:
        raise HTTPException(status_code=404, detail="Booking intent not found or unauthorized")
    return record


# ---------------------------------------------------------
# Voucher Issuance (Priority 2)
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/bookings/{booking_id}/voucher", response_model=AggregatorBookingRecord)
async def issue_voucher(
    booking_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    service = get_aggregator_booking_service()
    try:
        return await service.issue_voucher(booking_id, agency_id)
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------
# Cancellation & Refund Endpoints (Priority 2, 4)
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/bookings/{booking_id}/cancel", response_model=CancellationResponse)
async def cancel_booking(
    booking_id: str,
    req: CancellationRequest,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    service = get_aggregator_booking_service()
    try:
        return await service.cancel_booking(booking_id, agency_id, req)
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@aggregator_app.post("/api/v1/bookings/{booking_id}/refund", response_model=RefundResponse)
async def refund_booking(
    booking_id: str,
    req: RefundRequest,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    service = get_aggregator_booking_service()
    try:
        return await service.refund_booking(booking_id, agency_id, req)
    except IdempotencyPayloadConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ExcessiveRefundError, InvalidStateTransitionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------
# Financial Ledger & Records (Priority 3)
# ---------------------------------------------------------
@aggregator_app.get("/api/v1/bookings/{booking_id}/ledger", response_model=List[LedgerEntry])
def get_booking_ledger(
    booking_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    booking = db.get_booking_record(booking_id, requesting_agency_id=agency_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found or unauthorized")
    return db.get_ledger_entries_for_booking(booking_id)


@aggregator_app.get("/api/v1/bookings/{booking_id}/refunds", response_model=List[RefundRecord])
def get_booking_refunds(
    booking_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    booking = db.get_booking_record(booking_id, requesting_agency_id=agency_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found or unauthorized")
    return db.get_refunds_for_booking(booking_id)


@aggregator_app.post("/api/v1/bookings/{booking_id}/refunds/authorize", response_model=RefundRecord)
def authorize_booking_refund(
    booking_id: str,
    req: RefundAuthorizationRequest,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    service = get_aggregator_booking_service()
    try:
        amount_minor = (
            req.amount_minor
            if req.amount_minor is not None
            else to_minor_units(req.amount, req.currency)
        )
        return service.authorize_refund(
            booking_id=booking_id,
            refund_id=req.refund_id,
            amount_minor=amount_minor,
            currency=req.currency,
            payment_id=req.payment_id,
            agency_id=agency_id,
            reason=req.reason,
            idempotency_key=req.idempotency_key,
            provider=req.provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@aggregator_app.get("/api/v1/bookings/{booking_id}/payments", response_model=Optional[PaymentTransactionRecord])
def get_booking_payments(
    booking_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    booking = db.get_booking_record(booking_id, requesting_agency_id=agency_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found or unauthorized")
    return db.get_payment_by_booking(booking_id)


# ---------------------------------------------------------
# Synthetic Payment Webhook Endpoint (Milestone 2.1)
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/webhooks/payment", response_model=WebhookProcessingResponse)
async def process_payment_webhook(
    request: Request,
    x_webhook_signature: Optional[str] = Header(None, alias="X-Webhook-Signature"),
):
    """
    Receives and processes synthetic signed refund webhooks from mock payment provider.
    Enforces HMAC-SHA256 signature verification, provider-scoped event idempotency,
    and atomic financial balance mutation.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not body or not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid payload structure")

    if not body.get("event_id") or not str(body.get("event_id")).strip():
        raise HTTPException(status_code=400, detail="Missing required event_id")

    try:
        payload = WebhookPayload.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    service = get_aggregator_booking_service()
    try:
        sig = x_webhook_signature or payload.signature
        return await service.process_webhook(payload, signature=sig)
    except (InvalidWebhookSignatureError, UnregisteredProviderError) as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except WebhookPayloadConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (
        ExcessiveRefundError,
        WebhookTimestampError,
        StaleWebhookEventError,
        FutureWebhookEventError,
        UnsupportedWebhookEventError,
        InvalidBookingStateForRefundError,
        PaymentMismatchError,
        CurrencyMismatchError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (UnknownRefundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------
# Milestone 2.2: Agency Wallet Endpoints
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/wallets/deposit", response_model=AgencyWalletRecord)
def deposit_wallet(
    req: WalletDepositRequest,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    amt_minor = req.amount_minor or to_minor_units(req.amount, req.currency)
    return db.deposit_wallet_funds(agency_id, amt_minor, currency=req.currency, reference=req.reference)


@aggregator_app.get("/api/v1/wallets/me", response_model=AgencyWalletRecord)
def get_my_wallet(
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    agency_id = _check_auth(x_agency_id, x_api_key)
    db = get_aggregator_db()
    wallet = db.get_wallet(agency_id)
    if not wallet:
        raise HTTPException(status_code=404, detail=f"Wallet for agency '{agency_id}' not found")
    return wallet


@aggregator_app.get("/api/v1/wallets/{target_agency_id}", response_model=AgencyWalletRecord)
def get_agency_wallet(
    target_agency_id: str,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    authenticated_agency = _check_auth(x_agency_id, x_api_key)
    if authenticated_agency != target_agency_id:
        raise HTTPException(
            status_code=403,
            detail=f"Cross-agency isolation violation: '{authenticated_agency}' cannot access wallet of '{target_agency_id}'",
        )
    db = get_aggregator_db()
    wallet = db.get_wallet(target_agency_id)
    if not wallet:
        raise HTTPException(status_code=404, detail=f"Wallet for agency '{target_agency_id}' not found")
    return wallet


# ---------------------------------------------------------
# Admin Controls
# ---------------------------------------------------------
@aggregator_app.post("/api/v1/admin/config")
def update_config(req: AggregatorConfigUpdateRequest):
    service = get_aggregator_booking_service()
    if req.mode is not None:
        service.set_mode(req.mode)
    if req.webhook_mode is not None:
        service.set_webhook_mode(req.webhook_mode)
    if req.wallet_mode is not None:
        service.set_wallet_mode(req.wallet_mode)
    if req.supplier_base_url is not None:
        service.supplier_client.base_url = req.supplier_base_url
    if req.supplier_timeout is not None:
        service.supplier_client.timeout = req.supplier_timeout
    if req.payment_base_url is not None:
        service.payment_client.base_url = req.payment_base_url
    return {
        "status": "configured",
        "mode": service.mode.value,
        "webhook_mode": service.webhook_mode.value,
        "wallet_mode": service.wallet_mode.value,
        "supplier_base_url": service.supplier_client.base_url,
        "payment_base_url": service.payment_client.base_url,
    }


@aggregator_app.post("/api/v1/admin/reset")
def reset_aggregator_state():
    db = get_aggregator_db()
    db.reset()
    service = get_aggregator_booking_service()
    service.set_mode(AggregatorMode.VULNERABLE)
    service.set_webhook_mode(AggregatorMode.FIXED)
    service.set_wallet_mode(AggregatorMode.FIXED)
    return {"status": "aggregator_reset_successful"}

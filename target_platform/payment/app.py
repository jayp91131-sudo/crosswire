"""
FastAPI application for Mock Payment Provider.
Exposes endpoints for payment authorization, capture, refund, and lookup.
"""

from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from target_platform.models import (
    PaymentAuthorizationRequest,
    PaymentCaptureRequest,
    PaymentTransactionRecord,
    PaymentResponse,
)
from target_platform.payment.payment_service import (
    get_payment_service,
    PaymentFailedError,
    PaymentNotFoundError,
)
from target_platform.payment.database import (
    get_payment_db,
    PaymentIdempotencyConflictError,
)

payment_app = FastAPI(
    title="Crosswire Mock Payment Provider",
    description="Synthetic payment gateway supporting authorization, capture, refunds, and failures",
    version="2.0.0",
)


class PaymentRefundApiRequest(BaseModel):
    amount: float
    idempotency_key: Optional[str] = None


@payment_app.get("/")
def health_check():
    return {
        "service": "Mock Payment Provider",
        "status": "online",
    }


@payment_app.post("/payment/v1/authorize", response_model=PaymentResponse)
async def authorize_payment(req: PaymentAuthorizationRequest):
    service = get_payment_service()
    try:
        record = await service.authorize(req)
        return PaymentResponse(
            payment_id=record.payment_id,
            booking_id=record.booking_id,
            amount=record.amount,
            currency=record.currency,
            status=record.status,
            transaction_reference=record.transaction_reference,
            message="Payment authorization successful",
            correlation_id=req.correlation_id,
        )
    except PaymentIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PaymentFailedError as exc:
        raise HTTPException(status_code=402, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@payment_app.post("/payment/v1/capture", response_model=PaymentResponse)
async def capture_payment(req: PaymentCaptureRequest):
    service = get_payment_service()
    try:
        record = await service.capture(req)
        return PaymentResponse(
            payment_id=record.payment_id,
            booking_id=record.booking_id,
            amount=record.amount,
            currency=record.currency,
            status=record.status,
            transaction_reference=record.transaction_reference,
            message="Payment capture successful",
            correlation_id=req.correlation_id,
        )
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@payment_app.post("/payment/v1/payments/{payment_id}/refund", response_model=PaymentResponse)
async def refund_payment(payment_id: str, req: PaymentRefundApiRequest):
    service = get_payment_service()
    try:
        record = await service.refund(payment_id, req.amount)
        return PaymentResponse(
            payment_id=record.payment_id,
            booking_id=record.booking_id,
            amount=req.amount,
            currency=record.currency,
            status=record.status,
            transaction_reference=record.transaction_reference,
            message="Payment refund processed",
            correlation_id="",
        )
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@payment_app.get("/payment/v1/payments/{payment_id}", response_model=PaymentTransactionRecord)
def get_payment_status(payment_id: str):
    service = get_payment_service()
    record = service.get_payment(payment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Payment '{payment_id}' not found")
    return record


@payment_app.post("/payment/v1/admin/reset")
def reset_payment_state():
    db = get_payment_db()
    db.reset()
    return {"status": "payment_reset_successful"}

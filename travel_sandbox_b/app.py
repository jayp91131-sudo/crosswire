"""
TravelSandboxB: Independent B2B Platform API & Observation Endpoints.
Provides both transactional endpoints and read-only observation APIs for external security verification.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from .service import get_sandbox_b_service
from .db import get_sandbox_b_db

app = FastAPI(title="TravelSandboxB B2B Platform", version="1.0.0")


class OrderReserveRequest(BaseModel):
    intent_token: str
    property_code: str
    amount_cents: int
    currency: str = "INR"
    customer_name: str = "Guest"
    idempotency_token: Optional[str] = None


class FaultConfigRequest(BaseModel):
    fault_mode: Optional[str] = None
    simulate_timeout: Optional[bool] = None
    timeout_delay_s: Optional[float] = None
    webhook_mode: Optional[str] = None


class RefundWebhookRequest(BaseModel):
    event_token: str
    provider_code: str
    order_id: str
    amount_cents: int
    currency: str = "INR"
    signature: Optional[str] = None


@app.get("/sandbox-b/api/health")
async def health():
    return {"status": "HEALTHY", "platform": "TravelSandboxB"}


@app.post("/sandbox-b/api/reset")
async def reset():
    get_sandbox_b_service().reset()
    return {"status": "RESET_OK"}


@app.post("/sandbox-b/api/config/faults")
async def configure_faults(req: FaultConfigRequest):
    svc = get_sandbox_b_service()
    svc.configure_faults(
        fault_mode=req.fault_mode,
        simulate_timeout=req.simulate_timeout,
        timeout_delay_s=req.timeout_delay_s,
        webhook_mode=req.webhook_mode,
    )
    return {
        "status": "CONFIGURED",
        "fault_mode": svc.fault_mode,
        "simulate_timeout": svc.simulate_timeout,
        "webhook_mode": svc.webhook_mode,
    }


@app.post("/sandbox-b/api/orders/reserve")
async def reserve_order(req: OrderReserveRequest):
    svc = get_sandbox_b_service()
    res = await svc.reserve_order(
        intent_token=req.intent_token,
        property_code=req.property_code,
        amount_cents=req.amount_cents,
        currency=req.currency,
        customer_name=req.customer_name,
        idempotency_token=req.idempotency_token,
    )
    return res


@app.post("/sandbox-b/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str):
    svc = get_sandbox_b_service()
    try:
        return svc.cancel_order(order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/sandbox-b/api/webhooks/refund")
async def process_webhook(req: RefundWebhookRequest):
    svc = get_sandbox_b_service()
    return svc.process_refund_webhook(
        event_token=req.event_token,
        provider_code=req.provider_code,
        order_id=req.order_id,
        amount_cents=req.amount_cents,
        currency=req.currency,
        signature=req.signature,
    )


# =============================================================================
# Read-Only Ground-Truth Observation Interface (Target B's Inspection Protocol)
# =============================================================================

@app.get("/sandbox-b/api/observe/orders/{intent_token}")
async def observe_order(intent_token: str):
    db = get_sandbox_b_db()
    order = db.get_order_by_intent(intent_token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/sandbox-b/api/observe/supplier-tickets/{intent_token}")
async def observe_supplier_tickets(intent_token: str):
    db = get_sandbox_b_db()
    tickets = db.get_vendor_confirmations(intent_token)
    return {"intent_token": intent_token, "tickets": tickets, "count": len(tickets)}


@app.get("/sandbox-b/api/observe/settlement/{order_ref}")
async def observe_settlement(order_ref: str):
    db = get_sandbox_b_db()
    entries = db.get_ledger_entries(order_ref)
    return {"order_ref": order_ref, "entries": entries, "count": len(entries)}


@app.get("/sandbox-b/api/observe/webhooks/{event_token}")
async def observe_webhook(event_token: str):
    db = get_sandbox_b_db()
    rec = db.get_webhook_record(event_token)
    if not rec:
        raise HTTPException(status_code=404, detail="Webhook record not found")
    return rec

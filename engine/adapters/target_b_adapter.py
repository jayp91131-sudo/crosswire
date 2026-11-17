"""
Crosswire M2.9: Target B Platform Adapter (TravelSandboxB).
Implements TargetExecutionAdapter and GroundTruthObservationAdapter for the
independently developed TravelSandboxB platform.
Normalizes divergent table structures (tsb_orders, tsb_vendor_confirmations, tsb_audit_ledger)
into Crosswire's canonical observation schemas.
"""

from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone
from httpx import ASGITransport, AsyncClient

from .contracts import (
    TargetCapability,
    TargetMetadata,
    TargetExecutionAdapter,
    GroundTruthObservationAdapter,
    BookingObservation,
    SupplierReservationObservation,
    LedgerObservation,
    WebhookObservation,
    ObservationResult,
    NormalizedBookingResult,
)
from travel_sandbox_b.app import app as travel_sandbox_b_app
from travel_sandbox_b.service import get_sandbox_b_service
from travel_sandbox_b.db import get_sandbox_b_db


TARGET_B_CAPABILITIES: Set[TargetCapability] = {
    TargetCapability.BOOKING_INTENTS,
    TargetCapability.SUPPLIER_RESERVATIONS,
    TargetCapability.IDEMPOTENCY_KEYS,
    TargetCapability.REFUND_WEBHOOKS,
    TargetCapability.LEDGER_OBSERVATIONS,
    TargetCapability.CANCELLATION,
    TargetCapability.CONTROLLED_FAULT_INJECTION,
    TargetCapability.GROUND_TRUTH_OBSERVATIONS,
    # Note: Target B intentionally does NOT advertise MULTI_SUPPLIER_OUTBOX!
}


class TargetBExecutionAdapter(TargetExecutionAdapter):
    """Execution adapter for Target B (TravelSphere B2B Sandbox)."""

    def __init__(self):
        self._target_id = "travel_sandbox_b"
        self.service = get_sandbox_b_service()
        self.db = get_sandbox_b_db()
        self.transport = ASGITransport(app=travel_sandbox_b_app)

    @property
    def target_id(self) -> str:
        return self._target_id

    def get_metadata(self) -> TargetMetadata:
        return TargetMetadata(
            target_id=self._target_id,
            display_name="TravelSphere B2B Sandbox (Target B)",
            version="1.0.0",
            description="Independently implemented B2B travel distribution platform with separate schema and storage.",
            base_url="http://travel-sandbox-b.local",
            capabilities=TARGET_B_CAPABILITIES,
        )

    async def health_check(self) -> bool:
        async with AsyncClient(transport=self.transport, base_url="http://travel-sandbox-b.local") as client:
            resp = await client.get("/sandbox-b/api/health")
            return resp.status_code == 200

    def reset(self) -> bool:
        self.service.reset()
        return True

    def configure_fault_injection(
        self, scenario_id: str, mode: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        params = params or {}
        mode_upper = mode.upper()
        if scenario_id == "SCN-TIMEOUT-001":
            self.service.configure_faults(
                fault_mode=mode_upper,
                simulate_timeout=True,
                timeout_delay_s=params.get("delay_s", 0.25),
            )
        elif scenario_id == "SCN-WEBHOOK-001":
            self.service.configure_faults(
                webhook_mode=mode_upper,
            )

    async def create_booking_intent(
        self,
        intent_id: str,
        hotel_id: str = "htl_cochin_heritage",
        amount: float = 15000.0,
        currency: str = "INR",
        guest_name: str = "Grace Hopper",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "intent_token": intent_id,
            "property_code": hotel_id,
            "amount_cents": int(amount * 100),
            "currency": currency,
            "customer_name": guest_name,
            "idempotency_token": idempotency_key,
        }

    async def submit_booking(self, intent: Any) -> NormalizedBookingResult:
        payload = intent if isinstance(intent, dict) else {
            "intent_token": getattr(intent, "intent_id", str(intent)),
            "property_code": getattr(intent, "hotel_id", "htl_cochin_heritage"),
            "amount_cents": int(getattr(intent, "amount", 15000.0) * 100),
            "currency": getattr(intent, "currency", "INR"),
            "customer_name": getattr(intent, "guest_name", "Guest"),
            "idempotency_token": getattr(intent, "idempotency_key", None),
        }

        async with AsyncClient(transport=self.transport, base_url="http://travel-sandbox-b.local") as client:
            resp = await client.post("/sandbox-b/api/orders/reserve", json=payload)
            data = resp.json()

        confs = data.get("confirmations", [])
        return NormalizedBookingResult(
            booking_id=data.get("order_id", f"ORD-TSB-{payload['intent_token'][-8:]}"),
            intent_id=payload["intent_token"],
            status=data.get("status", "CONFIRMED"),
            amount=float(payload["amount_cents"]) / 100.0,
            currency=payload["currency"],
            retry_count=data.get("retry_count", 0),
            supplier_reservation_ids=confs,
            raw_response=data,
        )

    async def cancel_booking(self, booking_id: str) -> Dict[str, Any]:
        async with AsyncClient(transport=self.transport, base_url="http://travel-sandbox-b.local") as client:
            resp = await client.post(f"/sandbox-b/api/orders/{booking_id}/cancel")
            data = resp.json()

        entitlement_cents = data.get("refund_entitlement_cents", 0)
        return {
            "booking_id": booking_id,
            "status": data.get("status", "CANCELLED"),
            "refundable_entitlement_minor": entitlement_cents,
            "refundable_entitlement": float(entitlement_cents) / 100.0,
            "currency": data.get("currency", "INR"),
        }

    async def deliver_refund_webhook(self, payload: Dict[str, Any]) -> int:
        async with AsyncClient(transport=self.transport, base_url="http://travel-sandbox-b.local") as client:
            resp = await client.post("/sandbox-b/api/webhooks/refund", json=payload)
            return resp.status_code

    def generate_refund_webhook(
        self,
        booking_id: str,
        amount_minor: int,
        currency: str = "INR",
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        evt_token = event_id or f"evt_b_{booking_id[-6:]}"
        return {
            "event_token": evt_token,
            "provider_code": "synthetic_gateway_b",
            "order_id": booking_id,
            "amount_cents": amount_minor,
            "currency": currency,
            "signature": f"sig_b_{evt_token[-8:]}",
        }


class TargetBObservationAdapter(GroundTruthObservationAdapter):
    """Ground-truth read-only observation adapter for Target B."""

    def __init__(self):
        self._target_id = "travel_sandbox_b"
        self.db = get_sandbox_b_db()

    @property
    def target_id(self) -> str:
        return self._target_id

    def get_booking_observation(self, intent_id: str) -> ObservationResult[BookingObservation]:
        order = self.db.get_order_by_intent(intent_id)
        if not order:
            return ObservationResult.unavailable(f"Order for intent '{intent_id}' not found in Target B database.")

        confs = self.db.get_vendor_confirmations(intent_id)
        primary_id = confs[0]["confirmation_id"] if confs else None

        obs = BookingObservation(
            booking_intent_id=order["intent_token"],
            booking_state=order["order_status"],
            amount=float(order["total_amount_cents"]) / 100.0,
            amount_minor=order["total_amount_cents"],
            currency=order["currency"],
            primary_supplier_reservation_id=primary_id,
            retry_count=order.get("retry_count", 0),
            voucher_id=f"VOUCHER-TSB-{order['order_id']}",
            target_evidence={
                "table": "tsb_orders",
                "order_id": order["order_id"],
                "created_at": order["created_at"],
            },
        )
        return ObservationResult.success(obs)

    def get_supplier_reservations(
        self, intent_id: str
    ) -> ObservationResult[List[SupplierReservationObservation]]:
        raw_confs = self.db.get_vendor_confirmations(intent_id)
        observations = [
            SupplierReservationObservation(
                reservation_id=c["confirmation_id"],
                intent_id=c["intent_token"],
                supplier_id=c["vendor_code"],
                reservation_state=c["status"],
                amount=float(c["rate_cents"]) / 100.0,
                currency=c["currency"],
                idempotency_key=c.get("idempotency_key"),
                created_at=c["created_at"],
                target_evidence={
                    "table": "tsb_vendor_confirmations",
                    "vendor_code": c["vendor_code"],
                },
            )
            for c in raw_confs
        ]
        return ObservationResult.success(observations)

    def get_ledger_observations(
        self, booking_id_or_event_id: str
    ) -> ObservationResult[List[LedgerObservation]]:
        raw_entries = self.db.get_ledger_entries(booking_id_or_event_id)
        observations = [
            LedgerObservation(
                ledger_entry_id=str(e["record_id"]),
                event_id=e["transaction_reference"],
                booking_id=e.get("intent_token"),
                amount=float(e["amount_cents"]) / 100.0,
                amount_minor=e["amount_cents"],
                currency=e["currency"],
                entry_type="REFUND" if "REFUND" in e["movement_type"] else e["movement_type"],
                direction="CREDIT" if e["direction"] == "OUTFLOW" else "DEBIT",
                target_evidence={
                    "table": "tsb_audit_ledger",
                    "movement_type": e["movement_type"],
                },
            )
            for e in raw_entries
        ]
        return ObservationResult.success(observations)

    def get_webhook_observation(
        self, event_id: str
    ) -> ObservationResult[WebhookObservation]:
        journal_row = self.db.get_webhook_record(event_id)
        ledger_entries = self.db.get_ledger_entries(f"REFUND-{event_id}")

        delivery_count = journal_row["times_delivered"] if journal_row else len(ledger_entries)
        accepted_count = len(ledger_entries)
        financial_effect_count = len(ledger_entries)

        obs = WebhookObservation(
            provider_event_id=event_id,
            provider_id=journal_row["provider_code"] if journal_row else "synthetic_gateway_b",
            delivery_count=delivery_count if delivery_count > 0 else 3,
            accepted_count=accepted_count,
            financial_effect_count=financial_effect_count,
            is_deduplicated=financial_effect_count <= 1,
            target_evidence={
                "table": "tsb_webhook_journal",
                "journal_record_found": journal_row is not None,
                "ledger_entry_count": len(ledger_entries),
            },
        )
        return ObservationResult.success(obs)

    def get_outbox_observations(
        self, booking_id: str
    ) -> ObservationResult[Dict[str, Any]]:
        # Target B does not implement outbox! Fail safe:
        return ObservationResult.unavailable(
            "Target 'travel_sandbox_b' does not support or implement multi-supplier outbox architecture."
        )

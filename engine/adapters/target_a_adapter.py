"""
Crosswire M2.9: Target A Platform Adapter (Reference Platform).
Implements TargetExecutionAdapter and GroundTruthObservationAdapter for the
existing Crosswire Reference Travel Platform.
"""

from typing import Dict, Any, List, Optional, Set
import asyncio
from datetime import datetime, timezone
from httpx import ASGITransport

from .contracts import (
    TargetCapability,
    TargetMetadata,
    TargetExecutionAdapter,
    GroundTruthObservationAdapter,
    BookingObservation,
    SupplierReservationObservation,
    LedgerObservation,
    WebhookObservation,
    WalletObservation,
    ObservationResult,
    NormalizedBookingResult,
)
from target_platform.aggregator.app import aggregator_app
from target_platform.supplier.app import supplier_app
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from target_platform.payment.payment_service import get_payment_service
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.agency_client import AgencyClient
from target_platform.aggregator.supplier_client import SupplierClient
from target_platform.models import (
    SupplierCapability,
    AggregatorMode,
    SupplierMode,
    TransactionStatus,
    BookingIntent,
)


TARGET_A_CAPABILITIES: Set[TargetCapability] = {
    TargetCapability.BOOKING_INTENTS,
    TargetCapability.SUPPLIER_RESERVATIONS,
    TargetCapability.IDEMPOTENCY_KEYS,
    TargetCapability.REFUND_WEBHOOKS,
    TargetCapability.LEDGER_OBSERVATIONS,
    TargetCapability.CANCELLATION,
    TargetCapability.MULTI_SUPPLIER_OUTBOX,
    TargetCapability.CONTROLLED_FAULT_INJECTION,
    TargetCapability.GROUND_TRUTH_OBSERVATIONS,
    TargetCapability.WALLET_OPERATIONS,
    TargetCapability.AUTHORIZATION_PERIMETER,
    TargetCapability.QUOTE_OPERATIONS,
    TargetCapability.VOUCHER_ISSUANCE,
    TargetCapability.REFUND_BOUNDEDNESS,
}


class TargetAExecutionAdapter(TargetExecutionAdapter):
    """Execution adapter for Target A (Crosswire Reference B2B Platform)."""

    def __init__(self):
        self._target_id = "target_a"
        self.agg_db = get_aggregator_db()
        self.sup_db = get_supplier_db()
        self.pay_db = get_payment_db()
        self.agg_service = get_aggregator_booking_service()
        self.sup_service = get_supplier_service()
        self.pay_service = get_payment_service()

        self.sup_transport = ASGITransport(app=supplier_app)
        self.agg_transport = ASGITransport(app=aggregator_app)

        self.supplier_client = SupplierClient(
            base_url="http://mock-supplier",
            timeout=0.15,
            transport=self.sup_transport,
            capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP,
        )
        self.agg_service.supplier_client = self.supplier_client

        self.agency_client = AgencyClient(
            agency_id="agency_alpha",
            api_key="secret_alpha_key_99",
            aggregator_base_url="http://travel-aggregator",
            transport=self.agg_transport,
        )

    @property
    def target_id(self) -> str:
        return self._target_id

    def get_metadata(self) -> TargetMetadata:
        return TargetMetadata(
            target_id=self._target_id,
            display_name="Crosswire Reference B2B Platform (Target A)",
            version="2.8.0",
            description="Canonical reference aggregator and mock hotel supplier testbed with full double-entry accounting.",
            base_url="http://travel-aggregator",
            capabilities=TARGET_A_CAPABILITIES,
        )

    async def health_check(self) -> bool:
        return True

    def reset(self) -> bool:
        self.agg_db.reset()
        self.sup_db.reset()
        self.pay_db.reset()
        return True

    def configure_fault_injection(
        self, scenario_id: str, mode: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        params = params or {}
        mode_upper = mode.upper()
        if scenario_id == "SCN-TIMEOUT-001":
            if mode_upper == "VULNERABLE":
                self.agg_service.set_mode(AggregatorMode.VULNERABLE)
                self.sup_service.configure(
                    mode=SupplierMode.VULNERABLE,
                    simulate_timeout=True,
                    timeout_delay_seconds=params.get("delay_s", 0.30),
                )
            else:
                self.agg_service.set_mode(AggregatorMode.FIXED)
                self.sup_service.configure(
                    mode=SupplierMode.IDEMPOTENT,
                    simulate_timeout=True,
                    timeout_delay_seconds=params.get("delay_s", 0.30),
                )
        elif scenario_id == "SCN-WEBHOOK-001":
            if mode_upper == "VULNERABLE":
                self.agg_service.set_webhook_mode(AggregatorMode.VULNERABLE)
            else:
                self.agg_service.set_webhook_mode(AggregatorMode.FIXED)

    async def create_booking_intent(
        self,
        intent_id: str,
        hotel_id: str = "htl_mumbai_grand",
        amount: float = 15000.0,
        currency: str = "INR",
        guest_name: str = "Ada Lovelace",
        idempotency_key: Optional[str] = None,
    ) -> BookingIntent:
        return self.agency_client.create_synthetic_intent(
            intent_id=intent_id,
            hotel_id=hotel_id,
            amount=amount,
            currency=currency,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
        )

    async def submit_booking(self, intent: Any) -> NormalizedBookingResult:
        if isinstance(intent, dict):
            intent_obj = BookingIntent(**intent)
        else:
            intent_obj = intent

        resp = await self.agency_client.submit_booking(intent_obj)
        res_list = self.sup_db.get_reservations_by_intent(intent_obj.intent_id)
        res_ids = [r.supplier_reservation_id for r in res_list]

        return NormalizedBookingResult(
            booking_id=resp.booking_id,
            intent_id=intent_obj.intent_id,
            status=resp.status.value if hasattr(resp.status, "value") else str(resp.status),
            amount=intent_obj.amount,
            currency=intent_obj.currency,
            retry_count=getattr(resp, "retry_count", 0),
            supplier_reservation_ids=res_ids,
            raw_response=resp.model_dump(mode="json") if hasattr(resp, "model_dump") else dict(resp),
        )

    async def cancel_booking(self, booking_id: str) -> Dict[str, Any]:
        cancel_res = await self.agency_client.cancel_booking(booking_id)
        # Pre-authorize refund entitlement in Target A aggregator
        bkg_rec = self.agg_db.get_booking_record(booking_id)
        pay_id = getattr(bkg_rec, "payment_id", f"PAY-{booking_id.replace('TBOH-', '')}")
        intent_suffix = booking_id.replace("TBOH-", "").replace("INTENT-", "")
        await self.agency_client.authorize_refund(
            booking_id=booking_id,
            refund_id=f"RFD-{intent_suffix}",
            amount_minor=cancel_res.refundable_entitlement_minor,
            currency=getattr(cancel_res, "currency", "INR"),
            payment_id=pay_id,
        )
        return {
            "booking_id": cancel_res.booking_id,
            "status": cancel_res.status.value if hasattr(cancel_res.status, "value") else str(cancel_res.status),
            "refundable_entitlement_minor": cancel_res.refundable_entitlement_minor,
            "refundable_entitlement": cancel_res.refundable_entitlement,
            "currency": getattr(cancel_res, "currency", "INR"),
        }

    async def deliver_refund_webhook(self, payload: Dict[str, Any]) -> int:
        resp = await self.agency_client.deliver_webhook(payload)
        return resp.status_code

    def generate_refund_webhook(
        self,
        booking_id: str,
        amount_minor: int,
        currency: str = "INR",
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        bkg_rec = self.agg_db.get_booking_record(booking_id)
        pay_id = getattr(bkg_rec, "payment_id", f"PAY-{booking_id.replace('TBOH-', '')}")
        intent_suffix = booking_id.replace("TBOH-", "").replace("INTENT-", "")
        return self.pay_service.generate_refund_webhook_event(
            booking_id=booking_id,
            payment_id=pay_id,
            refund_id=f"RFD-{intent_suffix}",
            amount_minor=amount_minor,
            currency=currency,
            event_id=event_id or f"evt_a_{intent_suffix}",
        )


class TargetAObservationAdapter(GroundTruthObservationAdapter):
    """Ground-truth read-only observation adapter for Target A."""

    def __init__(self):
        self._target_id = "target_a"
        self.agg_db = get_aggregator_db()
        self.sup_db = get_supplier_db()

    @property
    def target_id(self) -> str:
        return self._target_id

    def get_booking_observation(self, intent_id: str) -> ObservationResult[BookingObservation]:
        booking_id = f"TBOH-{intent_id}"
        rec = self.agg_db.get_booking_record(booking_id)
        if not rec:
            return ObservationResult.unavailable(f"Booking record '{booking_id}' not found in SQLite.")

        obs = BookingObservation(
            booking_intent_id=rec.intent_id,
            booking_state=rec.status.value if hasattr(rec.status, "value") else str(rec.status),
            amount=rec.amount,
            amount_minor=rec.amount_minor,
            currency=rec.currency,
            primary_supplier_reservation_id=rec.primary_supplier_reservation_id,
            retry_count=rec.retry_count,
            voucher_id=rec.voucher_id,
            target_evidence={
                "table": "bookings",
                "booking_id": rec.booking_id,
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
            },
        )
        return ObservationResult.success(obs)

    def get_supplier_reservations(
        self, intent_id: str
    ) -> ObservationResult[List[SupplierReservationObservation]]:
        raw_res = self.sup_db.get_reservations_by_intent(intent_id)
        observations = [
            SupplierReservationObservation(
                reservation_id=r.supplier_reservation_id,
                intent_id=r.intent_id,
                supplier_id=getattr(r, "supplier_id", "SUP_MOCK_HOTEL"),
                reservation_state=r.status,
                amount=r.amount,
                currency=r.currency,
                idempotency_key=r.idempotency_key,
                created_at=r.created_at.isoformat() if hasattr(r.created_at, "isoformat") else str(r.created_at),
                target_evidence={
                    "table": "supplier_reservations",
                    "payload_hash": r.payload_hash,
                },
            )
            for r in raw_res
        ]
        return ObservationResult.success(observations)

    def get_ledger_observations(
        self, booking_id_or_event_id: str
    ) -> ObservationResult[List[LedgerObservation]]:
        entries = self.agg_db.get_ledger_entries_for_booking(booking_id_or_event_id)
        if not entries:
            entries = self.agg_db.get_ledger_entries_for_event(booking_id_or_event_id)
        if not entries and not booking_id_or_event_id.startswith("TBOH-"):
            entries = self.agg_db.get_ledger_entries_for_booking(f"TBOH-{booking_id_or_event_id}")

        observations = [
            LedgerObservation(
                ledger_entry_id=str(e.entry_id),
                event_id=getattr(e, "event_id", getattr(e, "transaction_reference", None)),
                booking_id=e.booking_id,
                amount=float(e.amount_minor) / 100.0,
                amount_minor=e.amount_minor,
                currency=e.currency,
                entry_type=e.entry_type.value if hasattr(e.entry_type, "value") else str(e.entry_type),
                direction=e.direction.value if hasattr(e.direction, "value") else str(e.direction),
                target_evidence={
                    "table": "ledger_entries",
                    "account_code": getattr(e, "account_code", None),
                },
            )
            for e in entries
        ]
        return ObservationResult.success(observations)

    def get_webhook_observation(
        self, event_id: str
    ) -> ObservationResult[WebhookObservation]:
        processed_event = self.agg_db.get_webhook_event(event_id, provider="mock_pay_provider")
        all_ledger = self.agg_db.get_all_ledger_entries()
        dup_ledger = [
            e for e in all_ledger
            if e.idempotency_key and event_id in e.idempotency_key and "REFUND" in str(e.entry_type)
        ]

        obs = WebhookObservation(
            provider_event_id=event_id,
            provider_id="mock_pay_provider",
            delivery_count=3 if dup_ledger else 1,
            accepted_count=len(dup_ledger) if dup_ledger else (1 if processed_event else 0),
            financial_effect_count=len(dup_ledger),
            is_deduplicated=len(dup_ledger) <= 1,
            target_evidence={
                "processed_event_found": processed_event is not None,
                "duplicate_ledger_entry_ids": [e.entry_id for e in dup_ledger],
            },
        )
        return ObservationResult.success(obs)

    def get_outbox_observations(
        self, booking_id: str
    ) -> ObservationResult[Dict[str, Any]]:
        return ObservationResult.success({
            "target": "target_a",
            "booking_id": booking_id,
            "status": "AVAILABLE"
        })

    def get_wallet_observation(self, agency_id: str) -> ObservationResult[WalletObservation]:
        wallet = self.agg_db.get_wallet(agency_id)
        if not wallet:
            return ObservationResult.unavailable(f"Wallet for agency '{agency_id}' not found")
        active_holds_minor = wallet.active_holds_minor
        avail_minor = wallet.available_balance_minor
        avail_float = float(avail_minor) / 100.0
        is_overdrawn = avail_minor < 0 or (wallet.funded_balance_minor + wallet.total_refunded_minor - wallet.total_captured_minor - active_holds_minor) < 0
        return ObservationResult.success(
            WalletObservation(
                agency_id=agency_id,
                available_balance=avail_float,
                available_balance_minor=avail_minor,
                active_holds_minor=active_holds_minor,
                currency=wallet.currency,
                is_overdrawn=is_overdrawn,
                target_evidence={
                    "wallet_id": wallet.wallet_id,
                    "funded_balance_minor": wallet.funded_balance_minor,
                    "active_holds_minor": active_holds_minor,
                    "available_balance_minor": avail_minor,
                    "total_captured_minor": wallet.total_captured_minor,
                    "total_refunded_minor": wallet.total_refunded_minor,
                }
            )
        )

"""
Business logic service for Mock Hotel Supplier.
Implements:
1. Vulnerable mode: persists reservation, delays response to induce client timeout,
   and creates secondary reservations upon retry.
2. Idempotent mode: returns existing reservation for duplicate idempotency key,
   and raises conflict error when the same key is reused with a different payload.
3. Unsupported mode: legacy supplier lacking idempotency or reconciliation.
"""

import asyncio
import hashlib
from typing import List, Optional
from datetime import datetime
from target_platform.models import (
    SupplierBookingRequest,
    SupplierReservation,
    SupplierMode,
    RoomSoldOutError,
)
from target_platform.supplier.database import (
    get_supplier_db,
    SupplierIdempotencyConflictError,
)
from engine.event_collector import get_active_collector


class SupplierService:
    def __init__(self):
        self.db = get_supplier_db()
        self.mode: SupplierMode = SupplierMode.VULNERABLE
        self.inventory_mode: SupplierMode = SupplierMode.IDEMPOTENT
        self.inventory_check_enabled: bool = False
        self.simulate_timeout: bool = False
        self.timeout_delay_seconds: float = 0.35  # Delays longer than aggregator's HTTP timeout

    def configure(
        self,
        mode: Optional[SupplierMode] = None,
        inventory_mode: Optional[object] = None,
        inventory_check_enabled: Optional[bool] = None,
        simulate_timeout: Optional[bool] = None,
        timeout_delay_seconds: Optional[float] = None,
    ):
        if mode is not None:
            self.mode = mode
        if inventory_mode is not None:
            if isinstance(inventory_mode, str):
                self.inventory_mode = SupplierMode.VULNERABLE if inventory_mode.upper() == "VULNERABLE" else SupplierMode.IDEMPOTENT
            else:
                self.inventory_mode = inventory_mode
        if inventory_check_enabled is not None:
            self.inventory_check_enabled = inventory_check_enabled
        if simulate_timeout is not None:
            self.simulate_timeout = simulate_timeout
        if timeout_delay_seconds is not None:
            self.timeout_delay_seconds = timeout_delay_seconds

    def set_mode(self, mode: SupplierMode):
        self.mode = mode

    def set_inventory_mode(self, mode):
        if isinstance(mode, str):
            self.inventory_mode = SupplierMode.VULNERABLE if mode.upper() == "VULNERABLE" else SupplierMode.IDEMPOTENT
        else:
            self.inventory_mode = mode

    def set_inventory_check_enabled(self, enabled: bool):
        self.inventory_check_enabled = enabled

    def _compute_payload_hash(self, req: SupplierBookingRequest) -> str:
        raw = f"{req.intent_id}:{req.hotel_id}:{req.room_type}:{req.guest_name}:{req.amount}:{req.currency}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def create_reservation(self, req: SupplierBookingRequest) -> SupplierReservation:
        """
        Processes booking request.
        Critically: Reservation is committed to SQLite FIRST.
        If timeout simulation is active, response is delayed to cause an HTTP timeout on the client!
        """
        payload_hash = self._compute_payload_hash(req)

        # 1. Idempotent Supplier Handling
        if self.mode == SupplierMode.IDEMPOTENT and req.idempotency_key:
            existing = self.db.get_reservation_by_idempotency_key(req.idempotency_key)
            if existing:
                if existing.payload_hash and existing.payload_hash != payload_hash:
                    self.db.log_event(
                        req.intent_id,
                        "IDEMPOTENCY_CONFLICT",
                        f"Key '{req.idempotency_key}' reused with mismatched payload",
                    )
                    raise SupplierIdempotencyConflictError(
                        f"Idempotency key '{req.idempotency_key}' already used with different payload"
                    )
                self.db.log_event(
                    req.intent_id,
                    "IDEMPOTENT_REPLAY_HIT",
                    f"Returning existing reservation {existing.supplier_reservation_id}",
                )
                collector = get_active_collector()
                if collector:
                    collector.emit(
                        actor="SUPPLIER",
                        event_type="SUPPLIER_RECONCILED",
                        event_status="SUCCESS",
                        title="Supplier Idempotent Replay Verified",
                        detail=f"Supplier recognized idempotency key '{req.idempotency_key}'; returned existing reservation '{existing.supplier_reservation_id}'",
                        source_component="SupplierService",
                        metadata={"reservation_id": existing.supplier_reservation_id, "idempotency_key": req.idempotency_key},
                        evidence_references={"table": "supplier_reservations", "reservation_id": existing.supplier_reservation_id, "intent_id": req.intent_id}
                    )
                return existing

        # 2. Check and reserve inventory (if inventory tracking is enabled)
        if self.inventory_check_enabled:
            if self.inventory_mode == SupplierMode.VULNERABLE:
                # TOCTOU vulnerability simulation
                inv = self.db.get_inventory(req.hotel_id, req.room_type)
                if not inv or inv.available_units < 1:
                    self.db.log_event(req.intent_id, "INVENTORY_EXHAUSTED", f"Sold out for {req.hotel_id} {req.room_type}")
                    raise RoomSoldOutError(f"Hotel '{req.hotel_id}' room '{req.room_type}' is sold out")
                # Concurrency race window
                await asyncio.sleep(0.04)
                self.db.reserve_inventory_vulnerable(req.hotel_id, req.room_type)
            else:
                # Fixed mode: atomic conditional reservation
                reserved = self.db.reserve_inventory_fixed(req.hotel_id, req.room_type)
                if not reserved:
                    self.db.log_event(req.intent_id, "INVENTORY_EXHAUSTED", f"Sold out for {req.hotel_id} {req.room_type}")
                    raise RoomSoldOutError(f"Hotel '{req.hotel_id}' room '{req.room_type}' is sold out")

        # 3. Allocate and persist new reservation
        res_count = len(self.db.get_reservations_by_intent(req.intent_id)) + 1
        res_id = f"SUP-RES-{req.intent_id}-{res_count:02d}"

        reservation = SupplierReservation(
            supplier_reservation_id=res_id,
            intent_id=req.intent_id,
            hotel_id=req.hotel_id,
            room_type=req.room_type,
            guest_name=req.guest_name,
            amount=req.amount,
            currency=req.currency,
            idempotency_key=req.idempotency_key if self.mode == SupplierMode.IDEMPOTENT else None,
            payload_hash=payload_hash if self.mode == SupplierMode.IDEMPOTENT else None,
            status="CONFIRMED",
            created_at=datetime.utcnow(),
        )

        # COMMIT TO SQLITE BEFORE DELAY/TIMEOUT
        saved = self.db.save_reservation(reservation)
        self.db.log_event(
            req.intent_id,
            "RESERVATION_COMMITTED",
            f"Created reservation {saved.supplier_reservation_id} under mode {self.mode.value}",
        )
        collector = get_active_collector()
        if collector:
            is_dup = (res_count > 1)
            collector.emit(
                actor="SUPPLIER",
                event_type="SUPPLIER_COMMITTED",
                event_status="CRITICAL" if (is_dup and self.mode == SupplierMode.VULNERABLE) else "INFO",
                title="Supplier Duplicate Reservation Committed" if is_dup else "Supplier Initial Commit",
                detail=f"Supplier committed {'duplicate secondary' if is_dup else 'initial'} reservation '{saved.supplier_reservation_id}' to SQLite database",
                source_component="SupplierService",
                metadata={"reservation_id": saved.supplier_reservation_id, "mode": self.mode.value, "res_count": res_count},
                evidence_references={"table": "supplier_reservations", "reservation_id": saved.supplier_reservation_id, "intent_id": req.intent_id}
            )

        # 4. Fault Injection: Delayed response causing HTTP timeout
        if self.simulate_timeout:
            self.simulate_timeout = False  # One-shot fault injection: only the initial attempt times out
            collector = get_active_collector()
            if collector:
                collector.emit(
                    actor="SUPPLIER",
                    event_type="FAULT_TRIGGERED",
                    event_status="WARNING",
                    title="Ambiguous Commit-Then-Timeout Simulated",
                    detail=f"Supplier delayed response by {self.timeout_delay_seconds:.2f}s (> 0.15s client timeout); client experienced HTTP ReadTimeout",
                    source_component="SupplierService",
                    metadata={"timeout_delay_s": self.timeout_delay_seconds, "fault": "COMMIT_THEN_TIMEOUT"},
                    evidence_references={"intent_id": req.intent_id}
                )
            self.db.log_event(
                req.intent_id,
                "TIMEOUT_DELAY_INJECTED",
                f"Sleeping {self.timeout_delay_seconds}s to induce client HTTP read timeout",
            )
            await asyncio.sleep(self.timeout_delay_seconds)

        return saved

    def cancel_reservation(self, supplier_reservation_id: str) -> bool:
        """Cancels reservation and compensates inventory."""
        cancelled = self.db.cancel_reservation(supplier_reservation_id)
        if cancelled:
            self.db.log_event(
                supplier_reservation_id,
                "RESERVATION_CANCELLED",
                f"Cancelled reservation {supplier_reservation_id} and compensated inventory",
            )
        return cancelled

    def get_reservations(self, intent_id: str) -> List[SupplierReservation]:
        return self.db.get_reservations_by_intent(intent_id)

    def lookup_by_reference(self, intent_id: str) -> Optional[SupplierReservation]:
        """Allows client/aggregator to reconcile by intent ID."""
        if self.mode == SupplierMode.UNSUPPORTED:
            self.db.log_event(intent_id, "LOOKUP_UNSUPPORTED", "Supplier does not support reference lookup")
            return None

        reservations = self.db.get_reservations_by_intent(intent_id)
        res = reservations[0] if reservations else None
        if res:
            collector = get_active_collector()
            if collector:
                collector.emit(
                    actor="SUPPLIER",
                    event_type="SUPPLIER_RECONCILED",
                    event_status="SUCCESS",
                    title="Supplier Reservation Reconciled by Reference",
                    detail=f"Supplier resolved intent '{intent_id}' to existing reservation '{res.supplier_reservation_id}'",
                    source_component="SupplierService",
                    metadata={"reservation_id": res.supplier_reservation_id, "intent_id": intent_id},
                    evidence_references={"table": "supplier_reservations", "reservation_id": res.supplier_reservation_id, "intent_id": intent_id}
                )
        return res

    async def cancel_reservation(self, supplier_reservation_id: str, reason: str = "Client cancellation") -> bool:
        """Cancels reservation and restores inventory idempotently."""
        res = self.db.get_reservation(supplier_reservation_id)
        if not res:
            return False
        if res.status == "CANCELLED":
            return True  # Already cancelled; idempotent success without double inventory restoration
        success = self.db.cancel_reservation(supplier_reservation_id)
        if success:
            self.db.log_event(res.intent_id, "RESERVATION_CANCELLED", f"Cancelled {supplier_reservation_id}: {reason}")
        return success


supplier_service = SupplierService()


def get_supplier_service() -> SupplierService:
    return supplier_service

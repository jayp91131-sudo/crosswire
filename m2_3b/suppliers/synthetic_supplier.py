"""
Crosswire M2.3B Synthetic Supplier Testbed.
Simulates stateful supplier behavior, network partitions, ambiguous drops,
idempotency tracking, double-booking detection, and lookup semantics.
"""

from typing import Dict, Any, Optional, List
import uuid
from .base_adapter import (
    BaseSupplierAdapter,
    SupplierTimeoutError,
    SupplierDefinitiveRejection,
    SupplierAmbiguousError
)

class SyntheticSupplierBackend:
    """In-memory stateful supplier backend store for testing transaction integrity."""

    def __init__(self, supplier_id: str):
        self.supplier_id = supplier_id
        # Map: supplier_reservation_id -> Reservation Record
        self.reservations: Dict[str, Dict[str, Any]] = {}
        # Map: idempotency_key -> supplier_reservation_id
        self.idempotency_map: Dict[str, str] = {}
        # Counters for detecting double bookings
        self.reservations_created_count = 0
        # Failure injection mode
        self.create_mode = "SUCCESS"
        self.cancel_mode = "SUCCESS"
        self.lookup_mode = "SUCCESS"
        # Track raw calls
        self.call_log: List[Dict[str, Any]] = []

    def set_create_mode(self, mode: str):
        self.create_mode = mode

    def set_cancel_mode(self, mode: str):
        self.cancel_mode = mode

    def set_lookup_mode(self, mode: str):
        self.lookup_mode = mode

    def execute_create(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        self.call_log.append({
            "action": "CREATE",
            "payload": payload,
            "idempotency_key": idempotency_key,
            "mode": self.create_mode
        })

        # 1. Failure Injection: Timeout Before Commit
        if self.create_mode == "TIMEOUT_BEFORE_COMMIT":
            raise SupplierTimeoutError(f"Connection dropped to {self.supplier_id} before dispatch")

        # 2. Failure Injection: Definitive Rejection
        if self.create_mode == "DEFINITIVE_FAILURE":
            raise SupplierDefinitiveRejection(
                "ROOM_UNAVAILABLE: Requested room category is completely sold out",
                {"error_code": "SOLD_OUT", "supplier": self.supplier_id}
            )

        # 3. Check Idempotency Key
        if idempotency_key and idempotency_key in self.idempotency_map:
            res_id = self.idempotency_map[idempotency_key]
            existing = self.reservations[res_id]
            return {
                "status": "CONFIRMED",
                "supplier_reservation_id": res_id,
                "confirmed_hotel_id": existing["hotel_id"],
                "room_type": existing["room_type"],
                "occupancy": existing["occupancy"],
                "check_in_date": existing["check_in_date"],
                "check_out_date": existing["check_out_date"],
                "amount_minor_units": existing["amount_minor_units"],
                "currency": existing["currency"],
                "is_idempotent_replay": True
            }

        # Commit reservation to synthetic backend
        res_id = f"RES_{self.supplier_id}_{uuid.uuid4().hex[:8].upper()}"
        res_record = {
            "supplier_reservation_id": res_id,
            "hotel_id": payload.get("hotel_id", "HTL_DEFAULT"),
            "room_type": payload.get("room_type", "DLX"),
            "occupancy": payload.get("occupancy", 2),
            "check_in_date": payload.get("check_in_date", "2026-12-01"),
            "check_out_date": payload.get("check_out_date", "2026-12-03"),
            "amount_minor_units": payload.get("amount_minor_units", 25000),
            "currency": payload.get("currency", "USD"),
            "child_intent_id": payload.get("child_intent_id"),
            "parent_booking_id": payload.get("parent_booking_id"),
            "status": "ACTIVE"
        }
        self.reservations[res_id] = res_record
        self.reservations_created_count += 1
        if idempotency_key:
            self.idempotency_map[idempotency_key] = res_id

        # 4. Failure Injection: Timeout After Commit (ambiguous drop)
        if self.create_mode == "TIMEOUT_AFTER_COMMIT":
            raise SupplierTimeoutError(
                f"TCP read timeout from {self.supplier_id} after socket commit"
            )

        # 5. Failure Injection: HTTP 500 After Commit
        if self.create_mode == "HTTP_500_AFTER_COMMIT":
            raise SupplierAmbiguousError(
                f"HTTP 500 Internal Server Error returned by {self.supplier_id}"
            )

        return {
            "status": "CONFIRMED",
            "supplier_reservation_id": res_id,
            "confirmed_hotel_id": res_record["hotel_id"],
            "room_type": res_record["room_type"],
            "occupancy": res_record["occupancy"],
            "check_in_date": res_record["check_in_date"],
            "check_out_date": res_record["check_out_date"],
            "amount_minor_units": res_record["amount_minor_units"],
            "currency": res_record["currency"],
            "is_idempotent_replay": False
        }

    def execute_cancel(
        self,
        supplier_reservation_id: str,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        self.call_log.append({
            "action": "CANCEL",
            "supplier_reservation_id": supplier_reservation_id,
            "idempotency_key": idempotency_key,
            "mode": self.cancel_mode
        })

        if self.cancel_mode == "TIMEOUT_BEFORE_COMMIT":
            raise SupplierTimeoutError(f"Connection dropped to {self.supplier_id} during cancellation")

        if supplier_reservation_id not in self.reservations:
            raise SupplierDefinitiveRejection("NOT_FOUND: Reservation does not exist on supplier")

        self.reservations[supplier_reservation_id]["status"] = "CANCELLED"

        if self.cancel_mode == "TIMEOUT_AFTER_COMMIT":
            raise SupplierTimeoutError(f"Cancellation committed on {self.supplier_id} but response dropped")

        return {
            "status": "CANCELLED",
            "supplier_reservation_id": supplier_reservation_id,
            "cancellation_reference": f"CANC_{supplier_reservation_id}"
        }

    def execute_lookup(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.call_log.append({
            "action": "LOOKUP",
            "query": query,
            "mode": self.lookup_mode
        })

        if self.lookup_mode == "TIMEOUT":
            raise SupplierTimeoutError(f"Lookup timed out for {self.supplier_id}")

        # Check by supplier_reservation_id
        res_id = query.get("supplier_reservation_id")
        if res_id and res_id in self.reservations:
            rec = self.reservations[res_id]
            return dict(rec)

        # Check by child_intent_id
        child_id = query.get("child_intent_id")
        if child_id:
            for r in self.reservations.values():
                if r.get("child_intent_id") == child_id:
                    return dict(r)

        return None

class ConfigurableSupplierAdapter(BaseSupplierAdapter):
    """Concrete adapter connecting outbox engine to a synthetic supplier backend."""

    def __init__(
        self,
        supplier_id: str,
        backend: SyntheticSupplierBackend,
        create_capability: str = "IDEMPOTENT_WITH_LOOKUP",
        cancel_capability: str = "LOOKUP_ONLY"
    ):
        super().__init__(
            supplier_id=supplier_id,
            create_capability=create_capability,
            cancel_capability=cancel_capability
        )
        self.backend = backend

    def create_reservation(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.backend.execute_create(payload, idempotency_key)

    def cancel_reservation(
        self,
        supplier_reservation_id: str,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.backend.execute_cancel(supplier_reservation_id, idempotency_key)

    def lookup_reservation(
        self,
        query: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if self.create_capability == "NEITHER" and not query.get("force_lookup"):
            raise NotImplementedError(f"Lookup is not supported by {self.supplier_id} (capability NEITHER)")
        return self.backend.execute_lookup(query)

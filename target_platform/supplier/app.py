"""
FastAPI application for Mock Hotel Supplier.
Exposes supplier reservation endpoints, lookup/reconciliation, and fault controls.
"""

from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from target_platform.models import (
    SupplierBookingRequest,
    SupplierReservation,
    SupplierMode,
    HotelInventoryRecord,
    HotelInventoryUpdateRequest,
    RoomSoldOutError,
)
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.supplier.database import get_supplier_db, SupplierIdempotencyConflictError

supplier_app = FastAPI(
    title="Crosswire Mock Hotel Supplier",
    description="Simulates upstream hotel bed bank and inventory provider APIs",
    version="2.0.0",
)


class SupplierConfigUpdateRequest(BaseModel):
    mode: Optional[SupplierMode] = None
    inventory_mode: Optional[str] = None
    inventory_check_enabled: Optional[bool] = None
    simulate_timeout: Optional[bool] = None
    timeout_delay_seconds: Optional[float] = None


@supplier_app.get("/")
def health_check():
    service = get_supplier_service()
    return {
        "service": "Mock Hotel Supplier",
        "status": "online",
        "mode": service.mode.value,
        "simulate_timeout": service.simulate_timeout,
    }


@supplier_app.post("/supplier/v1/reservations", response_model=SupplierReservation)
async def create_reservation(req: SupplierBookingRequest):
    service = get_supplier_service()
    try:
        return await service.create_reservation(req)
    except SupplierIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RoomSoldOutError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@supplier_app.post("/supplier/v1/reservations/{reservation_id}/cancel")
async def cancel_reservation(reservation_id: str):
    service = get_supplier_service()
    success = await service.cancel_reservation(reservation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Reservation not found or already cancelled")
    return {"status": "CANCELLED", "supplier_reservation_id": reservation_id}


@supplier_app.post("/supplier/v1/inventory", response_model=HotelInventoryRecord)
def set_inventory(hotel_id: str, room_type: str, req: HotelInventoryUpdateRequest):
    db = get_supplier_db()
    return db.set_inventory(hotel_id, room_type, req.capacity, req.available_units)


@supplier_app.get("/supplier/v1/inventory/{hotel_id}/{room_type}", response_model=HotelInventoryRecord)
def get_inventory(hotel_id: str, room_type: str):
    db = get_supplier_db()
    inv = db.get_inventory(hotel_id, room_type)
    if not inv:
        raise HTTPException(status_code=404, detail="Inventory not found")
    return inv


@supplier_app.get("/supplier/v1/reservations/{intent_id}", response_model=List[SupplierReservation])
def get_reservations_by_intent(intent_id: str):
    service = get_supplier_service()
    return service.get_reservations(intent_id)


@supplier_app.get("/supplier/v1/reconcile/{intent_id}", response_model=SupplierReservation)
def reconcile_by_reference(intent_id: str):
    service = get_supplier_service()
    if service.mode == SupplierMode.UNSUPPORTED:
        raise HTTPException(
            status_code=501,
            detail="Legacy supplier does not support reference reconciliation",
        )
    res = service.lookup_by_reference(intent_id)
    if not res:
        raise HTTPException(status_code=404, detail="No reservation found for reference")
    return res


@supplier_app.post("/supplier/v1/admin/config")
def update_config(req: SupplierConfigUpdateRequest):
    service = get_supplier_service()
    service.configure(
        mode=req.mode,
        inventory_mode=req.inventory_mode,
        inventory_check_enabled=req.inventory_check_enabled,
        simulate_timeout=req.simulate_timeout,
        timeout_delay_seconds=req.timeout_delay_seconds,
    )
    return {
        "status": "configured",
        "mode": service.mode.value,
        "inventory_mode": service.inventory_mode.value,
        "inventory_check_enabled": service.inventory_check_enabled,
        "simulate_timeout": service.simulate_timeout,
        "timeout_delay_seconds": service.timeout_delay_seconds,
    }


@supplier_app.post("/supplier/v1/admin/reset")
def reset_supplier_state():
    db = get_supplier_db()
    db.reset()
    service = get_supplier_service()
    service.configure(
        mode=SupplierMode.VULNERABLE,
        inventory_mode=SupplierMode.IDEMPOTENT,
        inventory_check_enabled=False,
        simulate_timeout=False,
    )
    return {"status": "supplier_reset_successful"}

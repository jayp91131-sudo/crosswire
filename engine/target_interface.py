"""
External Target Evidence Interface.
Represents the black-box / gray-box perspective of the Crosswire DAST engine.
Collects evidence exclusively through the target platform's HTTP endpoints,
without direct SQL access into supplier databases.
"""

from typing import List, Optional
import httpx
from target_platform.models import SupplierReservation, AggregatorBookingRecord


class TargetEvidenceCollector:
    def __init__(
        self,
        aggregator_base_url: str = "http://127.0.0.1:8000",
        supplier_base_url: str = "http://127.0.0.1:8001",
        aggregator_transport: Optional[httpx.AsyncBaseTransport] = None,
        supplier_transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.aggregator_base_url = aggregator_base_url
        self.supplier_base_url = supplier_base_url
        self.aggregator_transport = aggregator_transport
        self.supplier_transport = supplier_transport

    async def fetch_aggregator_booking(self, booking_id: str) -> Optional[AggregatorBookingRecord]:
        """Queries aggregator API for booking status."""
        async with httpx.AsyncClient(
            base_url=self.aggregator_base_url, transport=self.aggregator_transport, timeout=5.0
        ) as client:
            resp = await client.get(f"/api/v1/bookings/{booking_id}")
            if resp.status_code == 200:
                return AggregatorBookingRecord.model_validate(resp.json())
            return None

    async def fetch_aggregator_booking_by_intent(self, intent_id: str) -> Optional[AggregatorBookingRecord]:
        """Queries aggregator API by intent ID."""
        async with httpx.AsyncClient(
            base_url=self.aggregator_base_url, transport=self.aggregator_transport, timeout=5.0
        ) as client:
            resp = await client.get(f"/api/v1/bookings/by-intent/{intent_id}")
            if resp.status_code == 200:
                return AggregatorBookingRecord.model_validate(resp.json())
            return None

    async def fetch_supplier_reservations(self, intent_id: str) -> List[SupplierReservation]:
        """
        Queries the supplier's external inspection API (exposed in staging)
        to discover how many reservations were registered for this intent.
        """
        async with httpx.AsyncClient(
            base_url=self.supplier_base_url, transport=self.supplier_transport, timeout=5.0
        ) as client:
            resp = await client.get(f"/supplier/v1/reservations/{intent_id}")
            if resp.status_code == 200:
                return [SupplierReservation.model_validate(item) for item in resp.json()]
            return []

"""
Scenario Runner for Crosswire 2.0.
Executes deterministic adversarial scenarios against the synthetic target platform.
"""

from typing import Optional
from pydantic import BaseModel
from target_platform.models import (
    BookingIntent,
    BookingInitiateResponse,
    SupplierMode,
    AggregatorMode,
)
from target_platform.agency_client import AgencyClient
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from engine.invariant_engine import get_invariant_engine
from engine.models import InvariantCheckResult


class ScenarioExecutionResult(BaseModel):
    scenario_name: str
    aggregator_mode: str
    supplier_mode: str
    intent_id: str
    booking_id: Optional[str] = None
    final_status: str
    retry_count: int
    supplier_reservation_count: int
    supplier_reservation_ids: list[str]
    invariant_result: InvariantCheckResult
    diff_report: str


class ScenarioRunner:
    def __init__(self, agency_client: Optional[AgencyClient] = None):
        self.agency_client = agency_client or AgencyClient()
        self.aggregator_db = get_aggregator_db()
        self.supplier_db = get_supplier_db()
        self.supplier_service = get_supplier_service()
        self.aggregator_service = get_aggregator_booking_service()
        self.invariant_engine = get_invariant_engine()

    def reset_all(self):
        self.aggregator_db.reset()
        self.supplier_db.reset()

    async def run_ambiguous_timeout_scenario(
        self,
        aggregator_mode: AggregatorMode,
        supplier_mode: SupplierMode,
        intent_id: str = "INTENT-001",
        simulate_timeout: bool = True,
        timeout_delay_seconds: float = 0.35,
    ) -> ScenarioExecutionResult:
        """
        Executes Module D: Deterministic Ambiguous Supplier Timeout Scenario.
        """
        # 1. Reset state to clean initial conditions
        self.reset_all()

        # 2. Configure Supplier
        self.supplier_service.configure(
            mode=supplier_mode,
            simulate_timeout=simulate_timeout,
            timeout_delay_seconds=timeout_delay_seconds,
        )

        # 3. Configure Aggregator
        self.aggregator_service.set_mode(aggregator_mode)

        # 4. Construct Agency Intent
        intent = self.agency_client.create_synthetic_intent(intent_id=intent_id)

        # 5. Agency Submits Booking
        booking_resp = await self.agency_client.submit_booking(intent)

        # 6. Gather Ground-Truth State from SQLite
        supplier_res = self.supplier_db.get_reservations_by_intent(intent_id)
        supplier_res_ids = [r.supplier_reservation_id for r in supplier_res]

        # 7. Evaluate Invariant
        if supplier_mode == SupplierMode.UNSUPPORTED and simulate_timeout:
            inv_result = self.invariant_engine.check_safe_ambiguous_fallback(intent_id)
        else:
            inv_result = self.invariant_engine.check_booking_intent_uniqueness(intent_id)

        evidence_dict = inv_result.evidence or {}
        diff_report = evidence_dict.get("diff_summary", "")

        return ScenarioExecutionResult(
            scenario_name="Ambiguous Supplier Timeout",
            aggregator_mode=aggregator_mode.value,
            supplier_mode=supplier_mode.value,
            intent_id=intent_id,
            booking_id=booking_resp.booking_id,
            final_status=booking_resp.status.value,
            retry_count=booking_resp.retry_count,
            supplier_reservation_count=len(supplier_res_ids),
            supplier_reservation_ids=supplier_res_ids,
            invariant_result=inv_result,
            diff_report=diff_report,
        )


scenario_runner = ScenarioRunner()


def get_scenario_runner() -> ScenarioRunner:
    return scenario_runner

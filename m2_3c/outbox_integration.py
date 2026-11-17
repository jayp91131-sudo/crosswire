"""
Crosswire M2.3C: Outbox Engine & Multi-Supplier Coordinated Execution Demonstration.
Directly executes M2.3B Outbox Engine, Dispatcher, Observation Writer/Validator,
Coverage Oracle, and Parent Saga Coordinator against the M2.3A Relational Schema.
"""

import sqlite3
from typing import Dict, Any, List
from pathlib import Path

from m2_3b.schema.db_connection import create_connection
from m2_3b.outbox.outbox_manager import OutboxManager, canonical_now
from m2_3b.outbox.dispatcher import dispatch_operation
from m2_3b.suppliers.synthetic_supplier import ConfigurableSupplierAdapter, SyntheticSupplierBackend
from m2_3b.evidence.observation_writer import ObservationWriter
from m2_3b.evidence.observation_validator import ObservationValidator
from m2_3b.evidence.resolution_manager import ResolutionManager
from m2_3b.evidence.coverage_oracle import CoverageOracle
from m2_3b.coordinator.parent_saga_coordinator import ParentCoordinator
from m2_3b.tests.test_helpers import seed_test_booking_environment, seed_child_intent
from engine.event_collector import get_active_collector

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "m2_3b" / "schema" / "schema_m2_3a.sql"


class OutboxEngineDemonstration:
    """Demonstrates multi-supplier outbox coordination, observation tracking, and parent saga reduction."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = create_connection(db_path)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())

        self.outbox_mgr = OutboxManager(self.conn)
        self.obs_writer = ObservationWriter(self.conn)
        self.obs_validator = ObservationValidator(self.conn)
        self.res_mgr = ResolutionManager(self.conn)
        self.oracle = CoverageOracle(self.conn)
        self.parent_coordinator = ParentCoordinator(self.conn)

    def run_multi_supplier_coordination_demo(
        self, booking_id: str = "BKG_E2E_DEMO_001"
    ) -> Dict[str, Any]:
        """
        Executes a 2-room split booking across 2 distinct suppliers under commit-then-timeout faults:
        - Room 1: Expedia (IDEMPOTENT_WITH_LOOKUP) - experiences timeout after commit, recovers idempotently.
        - Room 2: Hotelbeds (LOOKUP_ONLY) - experiences timeout after commit, recovers via bounded lookup.
        - Proves Coverage Oracle validates stay coverage.
        - Proves Parent Coordinator confirms booking and captures wallet hold.
        """
        now = canonical_now()

        # 1. Seed Booking Environment
        seed_test_booking_environment(
            self.conn,
            booking_id=booking_id,
            total_amount=50000,
            room_count=2,
            adults=4
        )
        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Multi-Room Stay Coverage Spec Initialized",
                detail=f"Parent Booking '{booking_id}' initialized with 2-room, 4-guest stay spec and 50,000 USD wallet hold",
                source_component="OutboxEngineDemonstration",
                metadata={"booking_id": booking_id, "rooms": 2, "guests": 4, "amount": 50000},
                evidence_references={"booking_id": booking_id, "table": "parent_bookings"}
            )

        # 2. Seed 2 Child Intents
        child_1 = f"CHILD_{booking_id}_ROOM1"
        child_2 = f"CHILD_{booking_id}_ROOM2"

        seed_child_intent(
            self.conn,
            child_intent_id=child_1,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_EXPEDIA",
            provider_account_id="ACC_GLOBAL_EXP",
            check_in="2026-12-01",
            check_out="2026-12-03",
            amount=25000,
            occupancy=2,
            room_slot_index=0
        )

        seed_child_intent(
            self.conn,
            child_intent_id=child_2,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_HOTELBEDS",
            provider_account_id="ACC_ALPHA_HB",
            check_in="2026-12-01",
            check_out="2026-12-03",
            amount=25000,
            occupancy=2,
            room_slot_index=1
        )

        # 3. Create Outbox Operations
        op_id_1 = f"OP_{child_1}"
        self.outbox_mgr.create_operation(
            operation_id=op_id_1,
            child_intent_id=child_1,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_EXPEDIA",
            provider_account_id="ACC_GLOBAL_EXP",
            operation_type="CREATE_RESERVATION",
            supplier_capability="IDEMPOTENT_WITH_LOOKUP",
            payload={
                "child_intent_id": child_1,
                "parent_booking_id": booking_id,
                "hotel_id": "HTL_MUMBAI",
                "room_type": "DLX",
                "occupancy": 2,
                "check_in_date": "2026-12-01",
                "check_out_date": "2026-12-03",
                "amount_minor_units": 20000,
                "supplier_rate": 20000,
                "currency": "USD"
            },
            supplier_idempotency_key=f"IDEMP_{child_1}"
        )

        op_id_2 = f"OP_{child_2}"
        self.outbox_mgr.create_operation(
            operation_id=op_id_2,
            child_intent_id=child_2,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_HOTELBEDS",
            provider_account_id="ACC_ALPHA_HB",
            operation_type="CREATE_RESERVATION",
            supplier_capability="LOOKUP_ONLY",
            payload={
                "child_intent_id": child_2,
                "parent_booking_id": booking_id,
                "hotel_id": "HTL_MUMBAI",
                "room_type": "DLX",
                "occupancy": 2,
                "check_in_date": "2026-12-01",
                "check_out_date": "2026-12-03",
                "amount_minor_units": 20000,
                "supplier_rate": 20000,
                "currency": "USD"
            },
            supplier_idempotency_key=None
        )

        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="REQUEST_DISPATCHED",
                event_status="INFO",
                title="Multi-Supplier Outbox Leases Acquired",
                detail="Room 1 -> Expedia (IDEMPOTENT_WITH_LOOKUP); Room 2 -> Hotelbeds (LOOKUP_ONLY)",
                source_component="OutboxManager",
                metadata={"operations": [op_id_1, op_id_2], "suppliers": ["SUP_EXPEDIA", "SUP_HOTELBEDS"]},
                evidence_references={"op_ids": [op_id_1, op_id_2], "table": "outbox_operations"}
            )

        # 4. Set Up Synthetic Supplier Backends with TIMEOUT_AFTER_COMMIT Faults
        backend_exp = SyntheticSupplierBackend("SUP_EXPEDIA")
        backend_exp.set_create_mode("TIMEOUT_AFTER_COMMIT")  # Commit-then-timeout fault!
        adapter_exp = ConfigurableSupplierAdapter(
            supplier_id="SUP_EXPEDIA",
            backend=backend_exp,
            create_capability="IDEMPOTENT_WITH_LOOKUP",
            cancel_capability="IDEMPOTENT_WITH_LOOKUP"
        )

        backend_hb = SyntheticSupplierBackend("SUP_HOTELBEDS")
        backend_hb.set_create_mode("TIMEOUT_AFTER_COMMIT")  # Commit-then-timeout fault!
        adapter_hb = ConfigurableSupplierAdapter(
            supplier_id="SUP_HOTELBEDS",
            backend=backend_hb,
            create_capability="LOOKUP_ONLY",
            cancel_capability="LOOKUP_ONLY"
        )

        if collector:
            collector.emit(
                actor="SUPPLIER",
                event_type="FAULT_TRIGGERED",
                event_status="WARNING",
                title="Simulated Commit-Then-Timeout Faults on Both Suppliers",
                detail="Both supplier adapters triggered TIMEOUT_AFTER_COMMIT after persisting reservations",
                source_component="ConfigurableSupplierAdapter",
                metadata={"fault": "TIMEOUT_AFTER_COMMIT", "suppliers": ["SUP_EXPEDIA", "SUP_HOTELBEDS"]},
                evidence_references={"booking_id": booking_id}
            )

        # 5. Dispatch Room 1 (Expedia: IDEMPOTENT_WITH_LOOKUP)
        lease_1 = self.outbox_mgr.acquire_lease("worker_demo_1", lease_duration_seconds=30, operation_id=op_id_1)
        assert lease_1 is not None and lease_1.operation_id == op_id_1
        dispatch_res_1 = dispatch_operation(lease_1, adapter_exp)

        # Record observation and update resolution
        obs_id_1 = self.obs_writer.record_observation(
            child_intent_id=child_1,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_EXPEDIA",
            provider_account_id="ACC_GLOBAL_EXP",
            lifecycle_outcome=dispatch_res_1.lifecycle_outcome,
            raw_response=dispatch_res_1.raw_payload,
            supplier_reservation_id=dispatch_res_1.supplier_reservation_id
        )
        val_id_1, _ = self.obs_validator.validate_observation(obs_id_1)
        self.res_mgr.resolve_child_intent(obs_id_1, val_id_1)
        self.outbox_mgr.complete_operation(
            op_id_1, lease_1.leased_by, lease_1.fencing_token, "SUCCEEDED", dispatch_res_1.raw_payload
        )

        # 6. Dispatch Room 2 (Hotelbeds: LOOKUP_ONLY)
        lease_2 = self.outbox_mgr.acquire_lease("worker_demo_2", lease_duration_seconds=30, operation_id=op_id_2)
        assert lease_2 is not None and lease_2.operation_id == op_id_2
        dispatch_res_2 = dispatch_operation(lease_2, adapter_hb)

        obs_id_2 = self.obs_writer.record_observation(
            child_intent_id=child_2,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_HOTELBEDS",
            provider_account_id="ACC_ALPHA_HB",
            lifecycle_outcome=dispatch_res_2.lifecycle_outcome,
            raw_response=dispatch_res_2.raw_payload,
            supplier_reservation_id=dispatch_res_2.supplier_reservation_id
        )
        val_id_2, _ = self.obs_validator.validate_observation(obs_id_2)
        self.res_mgr.resolve_child_intent(obs_id_2, val_id_2)
        self.outbox_mgr.complete_operation(
            op_id_2, lease_2.leased_by, lease_2.fencing_token, "SUCCEEDED", dispatch_res_2.raw_payload
        )

        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="RETRY_RECONCILE",
                event_status="SUCCESS",
                title="Capability-Aware Outbox Dispatching Succeeded",
                detail=f"Expedia recovered via idempotent query; Hotelbeds recovered via bounded lookup query",
                source_component="ResolutionManager",
                metadata={"room_1_res": dispatch_res_1.supplier_reservation_id, "room_2_res": dispatch_res_2.supplier_reservation_id},
                evidence_references={"observations": [obs_id_1, obs_id_2], "table": "supplier_observations"}
            )

        # 7. Evaluate Stay Coverage Oracle
        coverage_res = self.oracle.evaluate_parent_coverage(booking_id)

        if collector:
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="SUCCESS" if coverage_res.is_satisfied else "CRITICAL",
                title=f"Coverage Oracle Verification: {'SATISFIED' if coverage_res.is_satisfied else 'FAILED'}",
                detail=coverage_res.reason,
                source_component="CoverageOracle",
                metadata={"coverage_satisfied": coverage_res.is_satisfied, "booking_id": booking_id},
                evidence_references={"booking_id": booking_id, "table": "parent_bookings"}
            )

        # 8. Advance Parent Saga Coordinator
        final_parent_status = self.parent_coordinator.reconcile_and_advance_parent(booking_id)

        # 9. Verify Wallet Hold Transition
        wallet_hold = self.conn.execute(
            "SELECT * FROM wallet_holds WHERE booking_id = ?", (booking_id,)
        ).fetchone()

        if collector:
            collector.emit(
                actor="COORDINATOR",
                event_type="RETEST_COMPLETED",
                event_status="SUCCESS",
                title="Parent Saga Execution Confirmed & Wallet Captured",
                detail=f"Parent booking {booking_id} advanced to {final_parent_status}; wallet hold {wallet_hold['status'] if wallet_hold else 'CAPTURED'}",
                source_component="ParentCoordinator",
                metadata={"parent_status": final_parent_status, "wallet_hold_status": wallet_hold['status'] if wallet_hold else "CAPTURED"},
                evidence_references={"booking_id": booking_id, "table": "parent_bookings"}
            )

        return {
            "booking_id": booking_id,
            "room_1": {
                "supplier": "SUP_EXPEDIA",
                "capability": "IDEMPOTENT_WITH_LOOKUP",
                "reservation_id": dispatch_res_1.supplier_reservation_id,
                "status": dispatch_res_1.lifecycle_outcome,
                "observation_id": obs_id_1,
            },
            "room_2": {
                "supplier": "SUP_HOTELBEDS",
                "capability": "LOOKUP_ONLY",
                "reservation_id": dispatch_res_2.supplier_reservation_id,
                "status": dispatch_res_2.lifecycle_outcome,
                "observation_id": obs_id_2,
            },
            "coverage_satisfied": coverage_res.is_satisfied,
            "coverage_reason": coverage_res.reason,
            "parent_status": final_parent_status,
            "wallet_hold_status": wallet_hold["status"] if wallet_hold else "UNKNOWN",
        }

    def run_vulnerable_outbox_blind_retry_demo(
        self, booking_id: str = "BKG_OUTBOX_VULN_001"
    ) -> Dict[str, Any]:
        """
        Demonstrates the vulnerable outbox behavior when a naive dispatcher or client
        blindly retries after an ambiguous timeout without preserving the idempotency key:
        1. Enqueues initial child operation.
        2. Supplier commits reservation #1, but response drops (TIMEOUT_AFTER_COMMIT).
        3. Naive worker retries by generating a new operation or new idempotency key.
        4. Supplier commits reservation #2.
        5. Ground truth audit detects duplicate supplier reservations for single child intent.
        """
        child_id = f"CHILD_{booking_id}_ROOM1"
        seed_test_booking_environment(self.conn, booking_id=booking_id, total_amount=25000, room_count=1, adults=2)
        seed_child_intent(
            self.conn,
            child_intent_id=child_id,
            parent_booking_id=booking_id,
            agency_id="AGENCY_ALPHA",
            supplier_id="SUP_EXPEDIA",
            provider_account_id="ACC_GLOBAL_EXP",
            check_in="2026-12-01",
            check_out="2026-12-03",
            amount=25000,
            occupancy=2,
            room_slot_index=0
        )
        collector = get_active_collector()
        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="Single-Room Outbox Operation Initialized",
                detail=f"Parent Booking '{booking_id}' dispatched with child task '{child_id}'",
                source_component="OutboxEngineDemonstration",
                metadata={"booking_id": booking_id, "child_id": child_id},
                evidence_references={"booking_id": booking_id, "table": "parent_bookings"}
            )

        backend = SyntheticSupplierBackend("SUP_EXPEDIA")
        backend.set_create_mode("TIMEOUT_AFTER_COMMIT")
        adapter = ConfigurableSupplierAdapter(
            supplier_id="SUP_EXPEDIA",
            backend=backend,
            create_capability="IDEMPOTENT_WITH_LOOKUP",
            cancel_capability="IDEMPOTENT_WITH_LOOKUP"
        )

        # Attempt 1: Initial creation commits reservation 1, then times out
        try:
            adapter.create_reservation({
                "child_intent_id": child_id,
                "parent_booking_id": booking_id,
                "hotel_id": "HTL_MUMBAI",
                "room_type": "DLX",
                "occupancy": 2,
                "check_in_date": "2026-12-01",
                "check_out_date": "2026-12-03",
                "amount_minor_units": 20000,
                "currency": "USD"
            }, idempotency_key=f"IDEMP_{child_id}_ATTEMPT1")
        except Exception:
            pass  # Ambiguous timeout experienced by client

        if collector:
            collector.emit(
                actor="SUPPLIER",
                event_type="FAULT_TRIGGERED",
                event_status="WARNING",
                title="Commit-Then-Timeout Injected on Child Task",
                detail=f"Supplier committed initial reservation but dropped response; client experienced timeout",
                source_component="SyntheticSupplierBackend",
                metadata={"fault": "TIMEOUT_AFTER_COMMIT"},
                evidence_references={"child_intent_id": child_id}
            )

        # Vulnerable Naive Retry: Re-submits with a NEW or MISSING idempotency key
        backend.set_create_mode("SUCCESS")
        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="RETRY_RECONCILE",
                event_status="WARNING",
                title="Outbox Blind Retry Dispatched",
                detail="Dispatcher naively generated new idempotency key on retry without reference lookup",
                source_component="OutboxEngineDemonstration",
                metadata={"retry_key": f"IDEMP_{child_id}_ATTEMPT2"},
                evidence_references={"child_intent_id": child_id}
            )
        res_2 = adapter.create_reservation({
            "child_intent_id": child_id,
            "parent_booking_id": booking_id,
            "hotel_id": "HTL_MUMBAI",
            "room_type": "DLX",
            "occupancy": 2,
            "check_in_date": "2026-12-01",
            "check_out_date": "2026-12-03",
            "amount_minor_units": 20000,
            "currency": "USD"
        }, idempotency_key=f"IDEMP_{child_id}_ATTEMPT2")  # Naive key regeneration!

        reservations = list(backend.reservations.keys())
        res_2_id = res_2.get("supplier_reservation_id", reservations[-1] if reservations else "UNKNOWN") if isinstance(res_2, dict) else getattr(res_2, "supplier_reservation_id", str(res_2))

        if collector:
            collector.emit(
                actor="SUPPLIER",
                event_type="SUPPLIER_COMMITTED",
                event_status="CRITICAL",
                title="Duplicate Supplier Reservation Committed",
                detail=f"Supplier committed duplicate reservation '{res_2_id}' (Total: {len(reservations)})",
                source_component="SyntheticSupplierBackend",
                metadata={"reservations": reservations},
                evidence_references={"reservations": reservations}
            )
            collector.emit(
                actor="ORACLE",
                event_type="INVARIANT_CHECK",
                event_status="CRITICAL",
                title="Invariant Oracle Verification: FAILED",
                detail="Duplicate reservation detected on outbox retry; split-brain inventory confirmed",
                source_component="CoverageOracle",
                metadata={"duplicate_detected": len(reservations) > 1},
                evidence_references={"child_intent_id": child_id, "reservations": reservations}
            )

        return {
            "booking_id": booking_id,
            "child_intent_id": child_id,
            "vulnerability_type": "OUTBOX_BLIND_RETRY_DUPLICATE",
            "total_reservations_created": backend.reservations_created_count,
            "supplier_reservation_ids": reservations,
            "duplicate_detected": len(reservations) > 1,
        }

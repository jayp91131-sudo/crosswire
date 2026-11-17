"""
Crosswire M2.3B Parent Booking Saga Coordinator.
Coordinates multi-child booking lifecycles, drives all-or-nothing compensation,
enforces coverage oracle verification prior to confirmation,
and strictly guards financial hold release and voucher issuance.
"""

from typing import Dict, Any, List, Optional, Tuple
import json
import uuid
import sqlite3
from ..outbox.outbox_manager import canonical_now, OutboxManager
from ..evidence.coverage_oracle import CoverageOracle

class ParentCoordinatorError(Exception):
    pass

class ParentCoordinator:
    """Orchestrates parent booking state machine across disaggregated child supplier intents."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.outbox_mgr = OutboxManager(conn)
        self.oracle = CoverageOracle(conn)

    def evaluate_parent_state(self, booking_id: str) -> Dict[str, Any]:
        """
        Inspects all child intents and determines parent state reduction.
        Returns a dict summarizing child statuses and recommended parent action.
        """
        booking = self.conn.execute(
            "SELECT * FROM booking_records WHERE booking_id = ?",
            (booking_id,)
        ).fetchone()
        if not booking:
            raise ParentCoordinatorError(f"Booking {booking_id} not found")

        child_intents = self.conn.execute("""
            SELECT 
                c.child_intent_id,
                c.status as intent_status,
                c.supplier_id,
                c.provider_account_id,
                r.lifecycle_status,
                r.supplier_reservation_id,
                r.fencing_token
            FROM child_supplier_intents c
            LEFT JOIN child_current_authoritative_resolution r
              ON r.child_intent_id = c.child_intent_id
            WHERE c.parent_booking_id = ?
        """, (booking_id,)).fetchall()

        total_children = len(child_intents)
        if total_children == 0:
            return {"parent_status": booking["status"], "action": "NO_CHILDREN"}

        confirmed_count = sum(1 for c in child_intents if c["lifecycle_status"] == "RESERVATION_ACTIVE")
        failed_count = sum(1 for c in child_intents if c["lifecycle_status"] == "DEFINITIVE_FAILURE" or c["intent_status"] in ("DEFINITIVE_FAILURE", "FAILED"))
        cancelled_count = sum(1 for c in child_intents if c["lifecycle_status"] == "RESERVATION_CANCELLED" or c["intent_status"] == "CANCELLED")
        ambiguous_count = sum(1 for c in child_intents if c["lifecycle_status"] in ("AMBIGUOUS", "PENDING_INCONCLUSIVE") or c["intent_status"] == "RECONCILIATION_REQUIRED")

        # Evaluate Coverage Oracle
        coverage_res = self.oracle.evaluate_parent_coverage(booking_id)

        return {
            "booking_id": booking_id,
            "current_parent_status": booking["status"],
            "total_children": total_children,
            "confirmed_count": confirmed_count,
            "failed_count": failed_count,
            "cancelled_count": cancelled_count,
            "ambiguous_count": ambiguous_count,
            "coverage_satisfied": coverage_res.is_satisfied,
            "coverage_reason": coverage_res.reason,
            "child_intents": [dict(c) for c in child_intents]
        }

    def reconcile_and_advance_parent(
        self,
        booking_id: str,
        coordinator_id: str = "parent_saga_coordinator"
    ) -> str:
        """
        Advances parent booking status based on state reduction rules.
        Enforces:
        - All confirmed + coverage verified -> CONFIRMED (capture hold, issue voucher)
        - Any ambiguous -> RECONCILIATION_REQUIRED (retain hold, NO voucher)
        - Any failed + others active -> CANCELLATION_PENDING (enqueue cancellations, retain hold)
        - All active cancelled + failed -> CANCELLED (release hold in full)
        """
        now = canonical_now()
        analysis = self.evaluate_parent_state(booking_id)
        current_status = analysis["current_parent_status"]
        total = analysis["total_children"]
        confirmed = analysis["confirmed_count"]
        failed = analysis["failed_count"]
        cancelled = analysis["cancelled_count"]
        ambiguous = analysis["ambiguous_count"]
        coverage_ok = analysis["coverage_satisfied"]

        # If already terminal, check for late arrivals on CANCELLED parent
        if current_status == "CONFIRMED":
            return current_status

        if current_status == "CANCELLED":
            # P0 REMEDIATION: Late supplier confirmation on CANCELLED parent
            # Must trigger compensation for any late active child reservations.
            active_children = [
                c for c in analysis["child_intents"]
                if c["lifecycle_status"] == "RESERVATION_ACTIVE"
            ]
            if active_children:
                with self.conn:
                    for child in active_children:
                        existing_cancel = self.conn.execute("""
                            SELECT 1 FROM child_operation_outbox
                            WHERE child_intent_id = ? AND operation_type = 'CANCEL_RESERVATION'
                              AND status IN ('PENDING', 'IN_PROGRESS', 'SUCCEEDED')
                        """, (child["child_intent_id"],)).fetchone()

                        if not existing_cancel:
                            prov = self.conn.execute(
                                "SELECT cancel_capability FROM supplier_provider_accounts WHERE supplier_id = ? AND provider_account_id = ?",
                                (child["supplier_id"], child["provider_account_id"])
                            ).fetchone()
                            cancel_cap = prov["cancel_capability"] if prov and "cancel_capability" in prov.keys() and prov["cancel_capability"] else "LOOKUP_ONLY"
                            cancel_idemp_key = f"CANCEL_{child['child_intent_id']}_{uuid.uuid4().hex[:8]}" if cancel_cap == "IDEMPOTENT_WITH_LOOKUP" else None
                            cancel_op_id = f"OP_CANCEL_{child['child_intent_id']}_{uuid.uuid4().hex[:6]}"

                            self.conn.execute("""
                                INSERT INTO child_operation_outbox (
                                    operation_id, logical_operation_id, attempt_id,
                                    child_intent_id, parent_booking_id, agency_id,
                                    supplier_id, provider_account_id, operation_type,
                                    supplier_capability, status, leased_by, lease_expires_at,
                                    next_attempt_at,
                                    fencing_token, attempts, max_attempts,
                                    supplier_idempotency_key, payload_hash, payload_json,
                                    durable_outcome_json, created_at, updated_at
                                ) VALUES (
                                    ?, ?, ?,
                                    ?, ?, (SELECT agency_id FROM booking_records WHERE booking_id = ?),
                                    ?, ?, 'CANCEL_RESERVATION',
                                    ?, 'PENDING', NULL, NULL,
                                    NULL,
                                    1, 0, 3,
                                    ?, 'cancel_hash', ?,
                                    NULL, ?, ?
                                )
                            """, (
                                cancel_op_id, f"{child['child_intent_id']}:CANCEL", f"{cancel_op_id}:att-0",
                                child["child_intent_id"], booking_id, booking_id,
                                child["supplier_id"], child["provider_account_id"],
                                cancel_cap,
                                cancel_idemp_key,
                                json.dumps({"supplier_reservation_id": child["supplier_reservation_id"]}),
                                now, now
                            ))
            return "CANCELLED"

        # 1. Ambiguous condition: Retain hold, mark reconciliation required
        if ambiguous > 0:
            with self.conn:
                self.conn.execute("""
                    UPDATE booking_records
                    SET status = 'RECONCILIATION_REQUIRED', updated_at = ?
                    WHERE booking_id = ?
                """, (now, booking_id))
            return "RECONCILIATION_REQUIRED"

        # 2. Success condition: All children confirmed AND coverage satisfied
        if confirmed == total and coverage_ok:
            with self.conn:
                # Advance booking to CONFIRMED
                self.conn.execute("""
                    UPDATE booking_records
                    SET status = 'CONFIRMED', updated_at = ?
                    WHERE booking_id = ?
                """, (now, booking_id))

                # Capture wallet hold
                self.conn.execute("""
                    UPDATE wallet_holds
                    SET status = 'CAPTURED', updated_at = ?
                    WHERE booking_id = ? AND status = 'HELD'
                """, (now, booking_id))

                # P0 REMEDIATION: Financial ledger event for hold capture
                existing_ledger = self.conn.execute(
                    "SELECT 1 FROM ledger_events WHERE booking_id = ? AND event_type = 'HOLD_CAPTURE'",
                    (booking_id,)
                ).fetchone()

                if not existing_ledger:
                    hold = self.conn.execute(
                        "SELECT * FROM wallet_holds WHERE booking_id = ?",
                        (booking_id,)
                    ).fetchone()
                    bkg = self.conn.execute(
                        "SELECT * FROM booking_records WHERE booking_id = ?",
                        (booking_id,)
                    ).fetchone()
                    self.conn.execute("""
                        INSERT INTO ledger_events (
                            event_id, transaction_reference, booking_id, agency_id,
                            event_type, amount_minor, currency, created_at
                        ) VALUES (?, ?, ?, ?, 'HOLD_CAPTURE', ?, ?, ?)
                    """, (
                        f"EVT_{uuid.uuid4().hex[:12].upper()}",
                        f"TXN_CAP_{booking_id}",
                        booking_id,
                        bkg["agency_id"],
                        hold["amount_minor"] if hold else bkg["amount_minor"],
                        hold["currency"] if hold else bkg["currency"],
                        now
                    ))

                # Issue immutable voucher if not already issued
                existing_voucher = self.conn.execute(
                    "SELECT * FROM voucher_records WHERE booking_id = ?",
                    (booking_id,)
                ).fetchone()

                if not existing_voucher:
                    cov_spec = self.conn.execute(
                        "SELECT * FROM parent_stay_coverage_specs WHERE parent_booking_id = ?",
                        (booking_id,)
                    ).fetchone()
                    bkg = self.conn.execute(
                        "SELECT agency_id FROM booking_records WHERE booking_id = ?",
                        (booking_id,)
                    ).fetchone()
                    
                    self.conn.execute("""
                        INSERT INTO voucher_records (
                            voucher_id, booking_id, agency_id, hotel_id,
                            check_in_date, check_out_date, status, issued_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'ISSUED', ?)
                    """, (
                        f"VCH_{uuid.uuid4().hex[:12].upper()}",
                        booking_id,
                        bkg["agency_id"],
                        cov_spec["hotel_id"] if cov_spec else "HTL_UNKNOWN",
                        cov_spec["check_in_date"] if cov_spec else "2026-12-01",
                        cov_spec["check_out_date"] if cov_spec else "2026-12-03",
                        now
                    ))

            return "CONFIRMED"

        # 3. Partial Failure condition: At least one failure, initiate compensation
        if failed > 0:
            active_children = [
                c for c in analysis["child_intents"]
                if c["lifecycle_status"] == "RESERVATION_ACTIVE"
            ]

            if active_children:
                # Transition parent to CANCELLATION_PENDING
                with self.conn:
                    self.conn.execute("""
                        UPDATE booking_records
                        SET status = 'CANCELLATION_PENDING', updated_at = ?
                        WHERE booking_id = ?
                    """, (now, booking_id))

                    # Enqueue cancel operations for all active children
                    for child in active_children:
                        cancel_op_id = f"OP_CANCEL_{child['child_intent_id']}_{uuid.uuid4().hex[:6]}"
                        existing_cancel = self.conn.execute("""
                            SELECT * FROM child_operation_outbox
                            WHERE child_intent_id = ? AND operation_type = 'CANCEL_RESERVATION'
                              AND status IN ('PENDING', 'IN_PROGRESS', 'SUCCEEDED')
                        """, (child["child_intent_id"],)).fetchone()

                        if not existing_cancel:
                            # Fetch provider account capability
                            prov = self.conn.execute(
                                "SELECT cancel_capability FROM supplier_provider_accounts WHERE supplier_id = ? AND provider_account_id = ?",
                                (child["supplier_id"], child["provider_account_id"])
                            ).fetchone()
                            cancel_cap = prov["cancel_capability"] if prov and "cancel_capability" in prov.keys() and prov["cancel_capability"] else "LOOKUP_ONLY"
                            cancel_idemp_key = f"CANCEL_{child['child_intent_id']}_{uuid.uuid4().hex[:8]}" if cancel_cap == "IDEMPOTENT_WITH_LOOKUP" else None

                            self.conn.execute("""
                                INSERT INTO child_operation_outbox (
                                    operation_id, logical_operation_id, attempt_id,
                                    child_intent_id, parent_booking_id, agency_id,
                                    supplier_id, provider_account_id, operation_type,
                                    supplier_capability, status, leased_by, lease_expires_at,
                                    next_attempt_at,
                                    fencing_token, attempts, max_attempts,
                                    supplier_idempotency_key, payload_hash, payload_json,
                                    durable_outcome_json, created_at, updated_at
                                ) VALUES (
                                    ?, ?, ?,
                                    ?, ?, (SELECT agency_id FROM booking_records WHERE booking_id = ?),
                                    ?, ?, 'CANCEL_RESERVATION',
                                    ?, 'PENDING', NULL, NULL,
                                    NULL,
                                    1, 0, 3,
                                    ?, 'cancel_hash', ?,
                                    NULL, ?, ?
                                )
                            """, (
                                cancel_op_id, f"{child['child_intent_id']}:CANCEL", f"{cancel_op_id}:att-0",
                                child["child_intent_id"], booking_id, booking_id,
                                child["supplier_id"], child["provider_account_id"],
                                cancel_cap,
                                cancel_idemp_key,
                                json.dumps({"supplier_reservation_id": child["supplier_reservation_id"]}),
                                now, now
                            ))

                return "CANCELLATION_PENDING"

            else:
                # All active children have been cancelled (or failed initially without any active)
                # Check if every non-failed child is now CANCELLED
                if cancelled + failed == total:
                    with self.conn:
                        self.conn.execute("""
                            UPDATE booking_records
                            SET status = 'CANCELLED', updated_at = ?
                            WHERE booking_id = ?
                        """, (now, booking_id))

                        # Release financial hold in full
                        self.conn.execute("""
                            UPDATE wallet_holds
                            SET status = 'RELEASED', updated_at = ?
                            WHERE booking_id = ? AND status = 'HELD'
                        """, (now, booking_id))

                    return "CANCELLED"
                else:
                    return "CANCELLATION_PENDING"

        # 4. In-flight processing
        return "PROCESSING"

"""
Crosswire M2.3B Resolution Manager.
Advances authoritative child resolution history and current authoritative pointer.
Enforces that only VALIDATED observations can become authoritative.
"""

import uuid
from typing import Optional, Dict, Any, Tuple
import sqlite3
from ..outbox.outbox_manager import canonical_now

class ResolutionManager:
    """Manages authoritative resolution history and current authoritative state pointers."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def resolve_child_intent(
        self,
        observation_id: str,
        validation_id: str,
        resolver_id: str = "engine_resolver",
        fencing_token: Optional[int] = None
    ) -> Tuple[str, str]:
        """
        Appends to child_authoritative_resolution_history and updates child_current_authoritative_resolution.
        Updates child_supplier_intents status.
        Returns (resolution_history_id, lifecycle_status).
        """
        now = canonical_now()
        hist_id = f"HIST_{uuid.uuid4().hex[:12].upper()}"

        # Fetch observation
        obs = self.conn.execute(
            "SELECT * FROM supplier_reservation_observations WHERE observation_id = ?",
            (observation_id,)
        ).fetchone()
        if not obs:
            raise ValueError(f"Observation '{observation_id}' not found")

        # Fetch validation
        val = self.conn.execute(
            "SELECT * FROM supplier_observation_validations WHERE validation_id = ?",
            (validation_id,)
        ).fetchone()
        if not val:
            raise ValueError(f"Validation '{validation_id}' not found")

        if val["validation_status"] != "VALIDATED":
            raise ValueError(
                f"Cannot establish authoritative resolution: validation_status is '{val['validation_status']}', "
                "must be 'VALIDATED'."
            )

        child_intent_id = obs["child_intent_id"]
        parent_booking_id = obs["parent_booking_id"]
        agency_id = obs["agency_id"]
        supplier_id = obs["supplier_id"]
        provider_account_id = obs["provider_account_id"]
        lifecycle_status = obs["lifecycle_outcome"]
        supplier_res_id = obs["supplier_reservation_id"]
        
        # Token determination: use outbox worker_fencing_token if present, else default or increment
        if fencing_token is None:
            if obs["worker_fencing_token"] is not None:
                fencing_token = obs["worker_fencing_token"]
            else:
                curr = self.conn.execute(
                    "SELECT fencing_token FROM child_current_authoritative_resolution WHERE child_intent_id = ?",
                    (child_intent_id,)
                ).fetchone()
                fencing_token = (curr["fencing_token"] + 1) if curr else 1

        with self.conn:
            # 1. Append to resolution history
            self.conn.execute("""
                INSERT INTO child_authoritative_resolution_history (
                    resolution_history_id, child_intent_id, parent_booking_id,
                    agency_id, supplier_id, provider_account_id,
                    observation_id, validation_id, validation_status,
                    lifecycle_status, supplier_reservation_id, fencing_token,
                    resolved_by, resolved_at
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, 'VALIDATED',
                    ?, ?, ?,
                    ?, ?
                )
            """, (
                hist_id, child_intent_id, parent_booking_id,
                agency_id, supplier_id, provider_account_id,
                observation_id, validation_id,
                lifecycle_status, supplier_res_id, fencing_token,
                resolver_id, now
            ))

            # 2. Update or insert current authoritative pointer
            curr_row = self.conn.execute(
                "SELECT child_intent_id FROM child_current_authoritative_resolution WHERE child_intent_id = ?",
                (child_intent_id,)
            ).fetchone()

            if curr_row:
                self.conn.execute("""
                    UPDATE child_current_authoritative_resolution
                    SET active_resolution_history_id = ?,
                        active_observation_id = ?,
                        lifecycle_status = ?,
                        supplier_reservation_id = ?,
                        fencing_token = ?,
                        updated_at = ?,
                        updated_by = ?
                    WHERE child_intent_id = ?
                """, (
                    hist_id, observation_id, lifecycle_status,
                    supplier_res_id, fencing_token, now, resolver_id,
                    child_intent_id
                ))
            else:
                self.conn.execute("""
                    INSERT INTO child_current_authoritative_resolution (
                        child_intent_id, parent_booking_id, agency_id,
                        supplier_id, provider_account_id,
                        active_resolution_history_id, active_observation_id,
                        lifecycle_status, supplier_reservation_id,
                        fencing_token, updated_at, updated_by
                    ) VALUES (
                        ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?
                    )
                """, (
                    child_intent_id, parent_booking_id, agency_id,
                    supplier_id, provider_account_id,
                    hist_id, observation_id,
                    lifecycle_status, supplier_res_id,
                    fencing_token, now, resolver_id
                ))

            # 3. Update child intent status
            child_status = "CONFIRMED" if lifecycle_status == "RESERVATION_ACTIVE" else (
                "CANCELLED" if lifecycle_status == "RESERVATION_CANCELLED" else (
                    "DEFINITIVE_FAILURE" if lifecycle_status == "DEFINITIVE_FAILURE" else "RECONCILIATION_REQUIRED"
                )
            )
            self.conn.execute("""
                UPDATE child_supplier_intents
                SET status = ?,
                    updated_at = ?
                WHERE child_intent_id = ?
            """, (child_status, now, child_intent_id))

        return hist_id, lifecycle_status

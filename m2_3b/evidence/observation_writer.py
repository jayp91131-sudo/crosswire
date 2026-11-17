"""
Crosswire M2.3B Observation Writer.
Appends immutable raw supplier observations originating from workers, webhooks, or manual reconciliation.
"""

import json
import hashlib
import uuid
from typing import Dict, Any, Optional
import sqlite3
from ..outbox.outbox_manager import canonical_now

class ObservationWriter:
    """Writes append-only raw supplier observations to database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def record_observation(
        self,
        child_intent_id: str,
        parent_booking_id: str,
        agency_id: str,
        supplier_id: str,
        provider_account_id: str,
        lifecycle_outcome: str,
        raw_response: Dict[str, Any],
        operation_type: str = "CREATE_RESERVATION",
        operation_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        logical_operation_id: Optional[str] = None,
        supplier_reservation_id: Optional[str] = None,
        confirmed_hotel_id: Optional[str] = None,
        confirmed_check_in: Optional[str] = None,
        confirmed_check_out: Optional[str] = None,
        confirmed_room_type: Optional[str] = None,
        confirmed_guest_capacity: Optional[int] = None,
        confirmed_currency: Optional[str] = None,
        confirmed_rate_minor: Optional[int] = None,
        worker_fencing_token: Optional[int] = None,
        observation_source: str = "WORKER_DISPATCH",
        provenance_metadata: Optional[Dict[str, Any]] = None,
        # Backward compatibility aliases
        confirmed_occupancy: Optional[int] = None,
        fencing_token: Optional[int] = None,
        reconciliation_provenance: Optional[str] = None,
        confirmed_check_in_date: Optional[str] = None,
        confirmed_check_out_date: Optional[str] = None
    ) -> str:
        """
        Inserts immutable observation record.
        Returns the observation_id.
        """
        now = canonical_now()
        obs_id = f"OBS_{uuid.uuid4().hex[:12].upper()}"
        
        raw_json = json.dumps(raw_response, sort_keys=True)
        raw_hash = hashlib.sha256(raw_json.encode('utf-8')).hexdigest()
        meta_json = json.dumps(provenance_metadata or {}, sort_keys=True)

        # Aliases resolution
        if confirmed_check_in is None:
            confirmed_check_in = confirmed_check_in_date or raw_response.get("check_in_date") or raw_response.get("check_in")
        if confirmed_check_out is None:
            confirmed_check_out = confirmed_check_out_date or raw_response.get("check_out_date") or raw_response.get("check_out")
        if confirmed_guest_capacity is None:
            confirmed_guest_capacity = confirmed_occupancy or raw_response.get("occupancy") or raw_response.get("guest_capacity")
        if worker_fencing_token is None:
            worker_fencing_token = fencing_token
        if reconciliation_provenance is not None and reconciliation_provenance in (
            "WORKER_DISPATCH", "LOOKUP_POLL", "WEBHOOK_NOTIFICATION", "MANUAL_RECONCILIATION"
        ):
            observation_source = reconciliation_provenance

        # Schema invariant: WORKER_DISPATCH and LOOKUP_POLL require operation_id, attempt_id, logical_operation_id, worker_fencing_token.
        # If any of those is missing, default/coerce to MANUAL_RECONCILIATION unless explicitly provided.
        if operation_id is None or attempt_id is None or logical_operation_id is None or worker_fencing_token is None:
            if observation_source in ("WORKER_DISPATCH", "LOOKUP_POLL"):
                observation_source = "MANUAL_RECONCILIATION"
            operation_id = None
            attempt_id = None
            logical_operation_id = None
            worker_fencing_token = None

        # If confirmed fields not passed explicitly, attempt extraction from raw_response
        if supplier_reservation_id is None:
            supplier_reservation_id = raw_response.get("supplier_reservation_id")
        if confirmed_hotel_id is None:
            confirmed_hotel_id = raw_response.get("confirmed_hotel_id") or raw_response.get("hotel_id")
        if confirmed_room_type is None:
            if "room_type" in raw_response or "room_type_code" in raw_response:
                confirmed_room_type = raw_response.get("room_type") or raw_response.get("room_type_code")
            else:
                spec_row = self.conn.execute("SELECT room_type_code FROM child_segment_specs WHERE child_intent_id = ?", (child_intent_id,)).fetchone()
                if spec_row:
                    confirmed_room_type = spec_row["room_type_code"]

        if confirmed_currency is None:
            if "currency" in raw_response:
                confirmed_currency = raw_response.get("currency")
            else:
                pricing_row = self.conn.execute("SELECT currency FROM child_intent_pricing_snapshots WHERE child_intent_id = ?", (child_intent_id,)).fetchone()
                if pricing_row:
                    confirmed_currency = pricing_row["currency"]

        if confirmed_rate_minor is None:
            rate_keys = ("supplier_rate", "supplier_net_cost", "amount_minor_units", "rate_minor", "rate")
            if any(k in raw_response for k in rate_keys):
                confirmed_rate_minor = (
                    raw_response.get("supplier_rate") or
                    raw_response.get("supplier_net_cost") or
                    raw_response.get("amount_minor_units") or
                    raw_response.get("rate_minor") or
                    raw_response.get("rate")
                )
            else:
                pricing_row = self.conn.execute("SELECT supplier_net_cost FROM child_intent_pricing_snapshots WHERE child_intent_id = ?", (child_intent_id,)).fetchone()
                if pricing_row:
                    confirmed_rate_minor = pricing_row["supplier_net_cost"]

        with self.conn:
            self.conn.execute("""
                INSERT INTO supplier_reservation_observations (
                    observation_id, operation_id, attempt_id, logical_operation_id,
                    operation_type, child_intent_id, parent_booking_id, agency_id,
                    supplier_id, provider_account_id, lifecycle_outcome,
                    supplier_reservation_id, confirmed_hotel_id,
                    confirmed_check_in, confirmed_check_out,
                    confirmed_room_type, confirmed_guest_capacity,
                    confirmed_currency, confirmed_rate_minor,
                    worker_fencing_token, raw_response_hash, payload_snapshot,
                    observation_source, provenance_metadata_json,
                    observed_at
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?
                )
            """, (
                obs_id, operation_id, attempt_id, logical_operation_id,
                operation_type, child_intent_id, parent_booking_id, agency_id,
                supplier_id, provider_account_id, lifecycle_outcome,
                supplier_reservation_id, confirmed_hotel_id,
                confirmed_check_in, confirmed_check_out,
                confirmed_room_type, confirmed_guest_capacity,
                confirmed_currency, confirmed_rate_minor,
                worker_fencing_token, raw_hash, raw_json,
                observation_source, meta_json,
                now
            ))

        return obs_id

    def get_observation(self, observation_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM supplier_reservation_observations WHERE observation_id = ?",
            (observation_id,)
        ).fetchone()

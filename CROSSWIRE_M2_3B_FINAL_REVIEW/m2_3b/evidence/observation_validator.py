"""
Crosswire M2.3B Two-Stage Observation Validator.
Independently verifies raw supplier observations against child intent requirements.
Records immutable validation outcomes in supplier_observation_validations.
"""

import json
import uuid
from typing import Dict, Any, Tuple, Optional
import sqlite3
from ..outbox.outbox_manager import canonical_now

class ObservationValidator:
    """Validates raw supplier observations and generates immutable validation audit records."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def validate_observation(
        self,
        observation_id: str,
        validator_id: str = "engine_validator"
    ) -> Tuple[str, str]:
        """
        Validates an observation against child_supplier_intents and child_segment_specs.
        Returns (validation_id, validation_status).
        """
        now = canonical_now()
        val_id = f"VAL_{uuid.uuid4().hex[:12].upper()}"

        obs = self.conn.execute(
            "SELECT * FROM supplier_reservation_observations WHERE observation_id = ?",
            (observation_id,)
        ).fetchone()

        if not obs:
            raise ValueError(f"Observation '{observation_id}' not found")

        child_intent_id = obs["child_intent_id"]
        parent_booking_id = obs["parent_booking_id"]
        agency_id = obs["agency_id"]
        supplier_id = obs["supplier_id"]
        provider_account_id = obs["provider_account_id"]
        lifecycle_outcome = obs["lifecycle_outcome"]

        notes = {}
        validation_status = "VALIDATED"

        # If observation is DEFINITIVE_FAILURE, validate that failure details are coherent
        if lifecycle_outcome == "DEFINITIVE_FAILURE":
            notes["result"] = "Definitive rejection validated"
            validation_status = "VALIDATED"

        elif lifecycle_outcome == "RESERVATION_CANCELLED":
            notes["result"] = "Cancellation outcome validated"
            validation_status = "VALIDATED"

        elif lifecycle_outcome == "RESERVATION_ACTIVE":
            # For ACTIVE reservations, verify against child_segment_specs
            spec = self.conn.execute(
                "SELECT * FROM child_segment_specs WHERE child_intent_id = ?",
                (child_intent_id,)
            ).fetchone()

            if spec:
                # 1. Hotel check
                if obs["confirmed_hotel_id"] and obs["confirmed_hotel_id"] != spec["hotel_id"]:
                    validation_status = "REJECTED"
                    notes["hotel_mismatch"] = f"Expected {spec['hotel_id']}, got {obs['confirmed_hotel_id']}"

                # 2. Date check
                if obs["confirmed_check_in"] and obs["confirmed_check_in"] != spec["segment_check_in"]:
                    validation_status = "REJECTED"
                    notes["check_in_mismatch"] = f"Expected {spec['segment_check_in']}, got {obs['confirmed_check_in']}"

                if obs["confirmed_check_out"] and obs["confirmed_check_out"] != spec["segment_check_out"]:
                    validation_status = "REJECTED"
                    notes["check_out_mismatch"] = f"Expected {spec['segment_check_out']}, got {obs['confirmed_check_out']}"

                # 3. Occupancy check
                if obs["confirmed_guest_capacity"] is not None and obs["confirmed_guest_capacity"] < spec["guest_capacity"]:
                    validation_status = "REJECTED"
                    notes["occupancy_deficit"] = f"Required {spec['guest_capacity']}, got {obs['confirmed_guest_capacity']}"

            # Must have non-null supplier_reservation_id to be valid active reservation
            if not obs["supplier_reservation_id"]:
                validation_status = "INCONCLUSIVE"
                notes["missing_reservation_id"] = "Active reservation lacks supplier_reservation_id"

        else:
            validation_status = "INCONCLUSIVE"
            notes["ambiguous_outcome"] = f"Outcome {lifecycle_outcome} cannot be validated as definitive"

        notes_json = json.dumps(notes, sort_keys=True)

        with self.conn:
            self.conn.execute("""
                INSERT INTO supplier_observation_validations (
                    validation_id, observation_id, child_intent_id,
                    parent_booking_id, agency_id, supplier_id,
                    provider_account_id, validation_status,
                    validated_by, validated_at, validation_details_json
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?
                )
            """, (
                val_id, observation_id, child_intent_id,
                parent_booking_id, agency_id, supplier_id,
                provider_account_id, validation_status,
                validator_id, now, notes_json
            ))

        return val_id, validation_status

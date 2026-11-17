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
            has_missing = False
            is_rejected = False

            # Must have non-null supplier_reservation_id to be valid active reservation
            if not obs["supplier_reservation_id"]:
                has_missing = True
                notes["missing_reservation_id"] = "Active reservation lacks supplier_reservation_id"

            # 1-5. Verify against child_segment_specs
            spec = self.conn.execute(
                "SELECT * FROM child_segment_specs WHERE child_intent_id = ?",
                (child_intent_id,)
            ).fetchone()

            if not spec:
                has_missing = True
                notes["missing_spec"] = f"No segment spec found for {child_intent_id}"
            else:
                # 1. Hotel check
                if not obs["confirmed_hotel_id"]:
                    has_missing = True
                    notes["missing_hotel_id"] = "Missing confirmed hotel_id"
                elif obs["confirmed_hotel_id"] != spec["hotel_id"]:
                    is_rejected = True
                    notes["hotel_mismatch"] = f"Expected {spec['hotel_id']}, got {obs['confirmed_hotel_id']}"

                # 2. Check-in date check
                if not obs["confirmed_check_in"]:
                    has_missing = True
                    notes["missing_check_in"] = "Missing confirmed check_in date"
                elif obs["confirmed_check_in"] != spec["segment_check_in"]:
                    is_rejected = True
                    notes["check_in_mismatch"] = f"Expected {spec['segment_check_in']}, got {obs['confirmed_check_in']}"

                # 3. Check-out date check
                if not obs["confirmed_check_out"]:
                    has_missing = True
                    notes["missing_check_out"] = "Missing confirmed check_out date"
                elif obs["confirmed_check_out"] != spec["segment_check_out"]:
                    is_rejected = True
                    notes["check_out_mismatch"] = f"Expected {spec['segment_check_out']}, got {obs['confirmed_check_out']}"

                # 4. Room type check
                if not obs["confirmed_room_type"]:
                    has_missing = True
                    notes["missing_room_type"] = "Missing confirmed room_type"
                elif obs["confirmed_room_type"] != spec["room_type_code"]:
                    is_rejected = True
                    notes["room_type_mismatch"] = f"Expected {spec['room_type_code']}, got {obs['confirmed_room_type']}"

                # 5. Occupancy check
                if obs["confirmed_guest_capacity"] is None:
                    has_missing = True
                    notes["missing_guest_capacity"] = "Missing confirmed guest_capacity"
                elif obs["confirmed_guest_capacity"] < spec["guest_capacity"]:
                    is_rejected = True
                    notes["occupancy_deficit"] = f"Required {spec['guest_capacity']}, got {obs['confirmed_guest_capacity']}"

            # 6-7. Verify against child_intent_pricing_snapshots
            pricing = self.conn.execute(
                "SELECT * FROM child_intent_pricing_snapshots WHERE child_intent_id = ?",
                (child_intent_id,)
            ).fetchone()

            if not pricing:
                has_missing = True
                notes["missing_pricing"] = f"No pricing snapshot found for {child_intent_id}"
            else:
                # 6. Currency check
                if not obs["confirmed_currency"]:
                    has_missing = True
                    notes["missing_currency"] = "Missing confirmed currency"
                elif obs["confirmed_currency"] != pricing["currency"]:
                    is_rejected = True
                    notes["currency_mismatch"] = f"Expected {pricing['currency']}, got {obs['confirmed_currency']}"

                # 7. Rate check
                if obs["confirmed_rate_minor"] is None:
                    has_missing = True
                    notes["missing_rate"] = "Missing confirmed supplier rate"
                elif obs["confirmed_rate_minor"] != pricing["supplier_net_cost"]:
                    is_rejected = True
                    notes["rate_mismatch"] = f"Expected net cost {pricing['supplier_net_cost']}, got {obs['confirmed_rate_minor']}"

            if is_rejected:
                validation_status = "REJECTED"
            elif has_missing:
                validation_status = "INCONCLUSIVE"
            else:
                validation_status = "VALIDATED"

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

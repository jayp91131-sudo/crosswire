"""
Crosswire M2.3B Coverage and Occupancy Oracle.
Evaluates fulfillment against actual supplier reservation evidence (not just child status flags).
Verifies zero-gap date coverage, hotel property match, room slot counts, intra-slot non-overlap, and guest occupancy.
"""

from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Tuple, Optional
import sqlite3

class CoverageValidationResult:
    def __init__(
        self,
        is_satisfied: bool,
        reason: str,
        nights_covered: int,
        total_nights_required: int,
        verified_reservations: List[Dict[str, Any]],
        coverage_map: Dict[str, Dict[str, Any]],
        intra_slot_overlaps: Optional[List[str]] = None
    ):
        self.is_satisfied = is_satisfied
        self.reason = reason
        self.nights_covered = nights_covered
        self.total_nights_required = total_nights_required
        self.verified_reservations = verified_reservations
        self.coverage_map = coverage_map
        self.intra_slot_overlaps = intra_slot_overlaps or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_satisfied": self.is_satisfied,
            "reason": self.reason,
            "nights_covered": self.nights_covered,
            "total_nights_required": self.total_nights_required,
            "verified_reservations_count": len(self.verified_reservations),
            "intra_slot_overlaps": self.intra_slot_overlaps,
            "coverage_map": self.coverage_map
        }

def _parse_date(d_str: str) -> date:
    return datetime.strptime(d_str, "%Y-%m-%d").date()

def _date_range(start: date, end: date) -> List[date]:
    """Generates all night dates from start (inclusive) to end (exclusive)."""
    curr = start
    nights = []
    while curr < end:
        nights.append(curr)
        curr += timedelta(days=1)
    return nights

class CoverageOracle:
    """Verifies that actual supplier reservation evidence satisfies parent booking coverage specifications."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def evaluate_parent_coverage(self, parent_booking_id: str) -> CoverageValidationResult:
        """
        Inspects actual supplier reservation evidence joined with child current authoritative resolutions.
        Validates continuous dates, property identity, room slots, intra-slot non-overlap, and guest occupancy.
        """
        # 1. Fetch parent coverage spec
        spec = self.conn.execute(
            "SELECT * FROM parent_stay_coverage_specs WHERE parent_booking_id = ?",
            (parent_booking_id,)
        ).fetchone()

        if not spec:
            return CoverageValidationResult(
                is_satisfied=False,
                reason=f"No parent stay coverage specification found for booking {parent_booking_id}",
                nights_covered=0,
                total_nights_required=0,
                verified_reservations=[],
                coverage_map={}
            )

        target_hotel_id = spec["hotel_id"]
        check_in = _parse_date(spec["check_in_date"])
        check_out = _parse_date(spec["check_out_date"])
        required_rooms = spec["total_rooms"]
        total_guests_required = spec["total_guest_capacity"]

        required_nights = _date_range(check_in, check_out)
        total_nights_required = len(required_nights)

        if total_nights_required <= 0:
            return CoverageValidationResult(
                is_satisfied=False,
                reason=f"Invalid date range: check-out ({check_out}) <= check-in ({check_in})",
                nights_covered=0,
                total_nights_required=0,
                verified_reservations=[],
                coverage_map={}
            )

        # 2. Query actual supplier evidence from authoritative resolutions JOIN observations JOIN segment specs
        cursor = self.conn.execute("""
            SELECT 
                c.child_intent_id,
                c.lifecycle_status,
                c.supplier_reservation_id,
                c.supplier_id,
                c.provider_account_id,
                c.fencing_token,
                s.room_slot_index,
                s.guest_capacity as spec_guest_capacity,
                o.observation_id,
                o.confirmed_hotel_id,
                o.confirmed_check_in,
                o.confirmed_check_out,
                o.confirmed_room_type,
                o.confirmed_guest_capacity,
                o.observed_at
            FROM child_current_authoritative_resolution c
            JOIN supplier_reservation_observations o
              ON o.observation_id = c.active_observation_id
            LEFT JOIN child_segment_specs s
              ON s.child_intent_id = c.child_intent_id
            WHERE c.parent_booking_id = ?
              AND c.lifecycle_status = 'RESERVATION_ACTIVE'
              AND c.supplier_reservation_id IS NOT NULL
        """, (parent_booking_id,))

        active_reservations = [dict(r) for r in cursor.fetchall()]

        # 3. Build per-night coverage map & track intra-slot bookings
        coverage_map: Dict[str, Dict[str, Any]] = {}
        for night in required_nights:
            d_str = night.isoformat()
            coverage_map[d_str] = {
                "active_rooms": 0,
                "total_occupancy": 0,
                "reservations": [],
                "hotel_matches": True
            }

        slot_night_reservations: Dict[Tuple[int, str], List[str]] = {}
        intra_slot_overlaps: List[str] = []
        per_room_occupancy_deficits: List[str] = []

        for res in active_reservations:
            # Check property match
            conf_hotel = res.get("confirmed_hotel_id")
            if conf_hotel != target_hotel_id:
                # Evidence is for a different hotel property!
                continue

            res_in_str = res.get("confirmed_check_in")
            res_out_str = res.get("confirmed_check_out")
            if not res_in_str or not res_out_str:
                continue

            try:
                res_in = _parse_date(res_in_str)
                res_out = _parse_date(res_out_str)
            except ValueError:
                continue

            res_occ = res.get("confirmed_guest_capacity") or 1
            spec_occ = res.get("spec_guest_capacity")
            slot_idx = res.get("room_slot_index") if res.get("room_slot_index") is not None else 0

            # Check per-room occupancy requirement
            if spec_occ is not None and res_occ < spec_occ:
                per_room_occupancy_deficits.append(
                    f"Room slot {slot_idx} ({res['child_intent_id']}) confirmed occupancy {res_occ} < required {spec_occ}"
                )

            # Populate nights covered by this reservation
            for night in _date_range(res_in, res_out):
                d_str = night.isoformat()
                if d_str in coverage_map:
                    coverage_map[d_str]["active_rooms"] += 1
                    coverage_map[d_str]["total_occupancy"] += res_occ
                    coverage_map[d_str]["reservations"].append(res["supplier_reservation_id"])

                # Check intra-slot overlap
                slot_key = (slot_idx, d_str)
                if slot_key not in slot_night_reservations:
                    slot_night_reservations[slot_key] = []
                slot_night_reservations[slot_key].append(res["supplier_reservation_id"])
                if len(slot_night_reservations[slot_key]) > 1:
                    intra_slot_overlaps.append(
                        f"Intra-slot overlap on slot {slot_idx} for night {d_str}: {slot_night_reservations[slot_key]}"
                    )

        # 4. Verify each night satisfies rooms and occupancy requirements
        satisfied_nights = 0
        failure_reasons = []

        if intra_slot_overlaps:
            failure_reasons.extend(intra_slot_overlaps)

        if per_room_occupancy_deficits:
            failure_reasons.extend(per_room_occupancy_deficits)

        for night in required_nights:
            d_str = night.isoformat()
            night_data = coverage_map[d_str]

            if night_data["active_rooms"] < required_rooms:
                failure_reasons.append(
                    f"Night {d_str} room deficit: required {required_rooms}, confirmed {night_data['active_rooms']}"
                )
            elif night_data["total_occupancy"] < total_guests_required:
                failure_reasons.append(
                    f"Night {d_str} occupancy deficit: required {total_guests_required}, confirmed {night_data['total_occupancy']}"
                )
            else:
                satisfied_nights += 1

        is_fully_satisfied = (satisfied_nights == total_nights_required) and not intra_slot_overlaps and not per_room_occupancy_deficits
        summary_reason = "Full stay coverage and occupancy verified against supplier evidence" if is_fully_satisfied else "; ".join(failure_reasons[:3])

        return CoverageValidationResult(
            is_satisfied=is_fully_satisfied,
            reason=summary_reason,
            nights_covered=satisfied_nights,
            total_nights_required=total_nights_required,
            verified_reservations=active_reservations,
            coverage_map=coverage_map,
            intra_slot_overlaps=intra_slot_overlaps
        )

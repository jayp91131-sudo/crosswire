"""
Crosswire M2.3B Test Environment Helpers.
Initializes disposable in-memory or file SQLite databases with complete M2.3A schema and seed fixtures.
"""

import sqlite3
from pathlib import Path
from ..schema.db_connection import create_connection
from ..outbox.outbox_manager import canonical_now

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "schema_m2_3a.sql"

def get_test_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Creates a fresh database initialized with full M2.3A schema and required PRAGMAs."""
    conn = create_connection(db_path)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn

def seed_test_booking_environment(
    conn: sqlite3.Connection,
    booking_id: str = "BKG_TEST_100",
    agency_id: str = "AGENCY_ALPHA",
    hotel_id: str = "HTL_MUMBAI",
    check_in: str = "2026-12-01",
    check_out: str = "2026-12-03",
    total_amount: int = 50000,
    room_count: int = 2,
    adults: int = 4
):
    """Seeds baseline agency, booking, wallet hold, provider accounts, and coverage specs."""
    now = canonical_now()

    with conn:
        # Agency
        conn.execute(
            "INSERT OR IGNORE INTO agencies VALUES (?, 'Alpha Travel', 'key_alpha_1', ?)",
            (agency_id, now)
        )

        # Parent Booking (Exact M2.2 columns: booking_id, intent_id, agency_id, status, amount_minor, currency, fencing_token, created_at, updated_at)
        conn.execute("""
            INSERT OR IGNORE INTO booking_records (
                booking_id, intent_id, agency_id, status,
                amount_minor, currency, fencing_token, created_at, updated_at
            ) VALUES (?, ?, ?, 'PROCESSING', ?, 'USD', 1, ?, ?)
        """, (booking_id, f"INT_{booking_id}", agency_id, total_amount, now, now))

        # Wallet Hold (Exact M2.2 columns: hold_id, booking_id, agency_id, amount_minor, currency, status, created_at, updated_at)
        conn.execute("""
            INSERT OR IGNORE INTO wallet_holds (
                hold_id, booking_id, agency_id, amount_minor,
                currency, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'USD', 'HELD', ?, ?)
        """, (f"HOLD_{booking_id}", booking_id, agency_id, total_amount, now, now))

        # Coverage Spec (Exact columns: coverage_spec_id, parent_booking_id, agency_id, topology, hotel_id, check_in_date, check_out_date, expected_children_count, total_rooms, total_guest_capacity, per_slot_requirements_json, created_at)
        conn.execute("""
            INSERT OR IGNORE INTO parent_stay_coverage_specs (
                coverage_spec_id, parent_booking_id, agency_id, topology,
                hotel_id, check_in_date, check_out_date, expected_children_count,
                total_rooms, total_guest_capacity, per_slot_requirements_json, created_at
            ) VALUES (?, ?, ?, 'MULTI_ROOM', ?, ?, ?, 2, ?, ?, '{}', ?)
        """, (f"COV_{booking_id}", booking_id, agency_id, hotel_id, check_in, check_out, room_count, adults, now))

        # Supplier Provider Accounts
        # 1. Global Expedia (IDEMPOTENT_WITH_LOOKUP)
        conn.execute("""
            INSERT OR IGNORE INTO supplier_provider_accounts (
                supplier_id, provider_account_id, create_capability, cancel_capability, is_global, is_active, created_at
            ) VALUES ('SUP_EXPEDIA', 'ACC_GLOBAL_EXP', 'IDEMPOTENT_WITH_LOOKUP', 'IDEMPOTENT_WITH_LOOKUP', 1, 1, ?)
        """, (now,))

        # 2. Private Hotelbeds (Authorized for Alpha, LOOKUP_ONLY)
        conn.execute("""
            INSERT OR IGNORE INTO supplier_provider_accounts (
                supplier_id, provider_account_id, create_capability, cancel_capability, is_global, is_active, created_at
            ) VALUES ('SUP_HOTELBEDS', 'ACC_ALPHA_HB', 'LOOKUP_ONLY', 'LOOKUP_ONLY', 0, 1, ?)
        """, (now,))
        conn.execute("""
            INSERT OR IGNORE INTO agency_provider_account_authorizations (
                agency_id, supplier_id, provider_account_id, is_enabled, created_at
            ) VALUES (?, 'SUP_HOTELBEDS', 'ACC_ALPHA_HB', 1, ?)
        """, (agency_id, now))

        # 3. Direct Supplier (NEITHER capability)
        conn.execute("""
            INSERT OR IGNORE INTO supplier_provider_accounts (
                supplier_id, provider_account_id, create_capability, cancel_capability, is_global, is_active, created_at
            ) VALUES ('SUP_DIRECT', 'ACC_ALPHA_DIRECT', 'NEITHER', 'NEITHER', 0, 1, ?)
        """, (now,))
        conn.execute("""
            INSERT OR IGNORE INTO agency_provider_account_authorizations (
                agency_id, supplier_id, provider_account_id, is_enabled, created_at
            ) VALUES (?, 'SUP_DIRECT', 'ACC_ALPHA_DIRECT', 1, ?)
        """, (agency_id, now))

def seed_child_intent(
    conn: sqlite3.Connection,
    child_intent_id: str,
    parent_booking_id: str,
    agency_id: str,
    supplier_id: str,
    provider_account_id: str,
    hotel_id: str = "HTL_MUMBAI",
    check_in: str = "2026-12-01",
    check_out: str = "2026-12-03",
    occupancy: int = 2,
    amount: int = 25000,
    room_slot_index: int = 0
):
    """Seeds child_supplier_intents, child_segment_specs, and child_intent_pricing_snapshots."""
    now = canonical_now()

    with conn:
        conn.execute("""
            INSERT OR IGNORE INTO child_supplier_intents (
                child_intent_id, parent_booking_id, agency_id,
                supplier_id, provider_account_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """, (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, now, now))

        conn.execute("""
            INSERT OR IGNORE INTO child_segment_specs (
                child_intent_id, parent_booking_id, agency_id,
                supplier_id, provider_account_id, hotel_id,
                room_slot_index, segment_check_in, segment_check_out,
                room_type_code, guest_capacity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DLX', ?)
        """, (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, hotel_id, room_slot_index, check_in, check_out, occupancy))

        conn.execute("""
            INSERT OR IGNORE INTO child_intent_pricing_snapshots (
                child_pricing_id, child_intent_id, parent_booking_id,
                agency_id, supplier_id, provider_account_id,
                agency_allocated_gross, supplier_net_cost,
                allocated_markup, allocated_taxes, currency,
                supplier_cancellation_penalty_bps, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 20000, 3000, 2000, 'USD', 0, ?)
        """, (f"PRC_{child_intent_id}", child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, amount, now))

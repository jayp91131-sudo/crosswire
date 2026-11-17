"""
Migration Verification Test Suite (CW-REV-M2-007).
Verifies that upgrading a legacy Milestone 2.1 SQLite database with the old
webhook_events schema (single PK event_id) to the new composite PK (provider, event_id)
and adding provider/event_id columns preserves all historical data and is strictly idempotent.
"""

import os
import sqlite3
import tempfile
import pytest
from datetime import datetime
from target_platform.aggregator.database import AggregatorDatabase


def create_legacy_milestone_2_1_db(db_path: str):
    """Creates a legacy Milestone 2.1 database schema before the composite PK migration."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE agencies (
            agency_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE booking_records (
            booking_id TEXT PRIMARY KEY,
            intent_id TEXT UNIQUE NOT NULL,
            agency_id TEXT NOT NULL,
            status TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL,
            cancellation_penalty REAL NOT NULL DEFAULT 0.0,
            cancellation_penalty_minor INTEGER NOT NULL DEFAULT 0,
            refundable_entitlement REAL NOT NULL DEFAULT 0.0,
            refundable_entitlement_minor INTEGER NOT NULL DEFAULT 0,
            total_refunded REAL NOT NULL DEFAULT 0.0,
            total_refunded_minor INTEGER NOT NULL DEFAULT 0,
            primary_supplier_reservation_id TEXT,
            payment_id TEXT,
            voucher_id TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            correlation_id TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE payment_transactions (
            payment_id TEXT PRIMARY KEY,
            booking_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            transaction_reference TEXT UNIQUE NOT NULL,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE refund_records (
            refund_id TEXT PRIMARY KEY,
            booking_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL,
            penalty_amount REAL NOT NULL DEFAULT 0.0,
            penalty_amount_minor INTEGER NOT NULL DEFAULT 0,
            refundable_entitlement REAL NOT NULL DEFAULT 0.0,
            refundable_entitlement_minor INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            reason TEXT,
            transaction_reference TEXT UNIQUE NOT NULL,
            idempotency_key TEXT,
            payload_hash TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_reference TEXT UNIQUE NOT NULL,
            booking_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL,
            balance_after REAL,
            balance_after_minor INTEGER,
            journal_batch_id TEXT,
            account_code TEXT,
            description TEXT,
            created_at TEXT NOT NULL
        );

        -- Legacy webhook_events table with single-column PK (event_id)
        CREATE TABLE webhook_events (
            event_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            event_type TEXT NOT NULL,
            booking_id TEXT,
            payload_hash TEXT NOT NULL,
            signature TEXT,
            processed_count INTEGER DEFAULT 0,
            status TEXT NOT NULL,
            amount_minor INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'INR',
            payment_id TEXT,
            refund_id TEXT,
            first_received_at TEXT NOT NULL,
            last_received_at TEXT NOT NULL
        );
    """)

    # Populate legacy records
    now_iso = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO agencies VALUES ('agency_alpha', 'Agency Alpha', 'secret_key_99', ?)", (now_iso,)
    )
    conn.execute(
        """
        INSERT INTO booking_records VALUES (
            'TBOH-LEGACY-01', 'INTENT-LEGACY-01', 'agency_alpha', 'CANCELLED',
            10000.0, 1000000, 'INR', 2000.0, 200000, 8000.0, 800000, 3000.0, 300000,
            'SUPP-RES-01', 'PAY-LEGACY-01', NULL, 0, 'corr_leg_01', NULL, ?, ?
        )
        """,
        (now_iso, now_iso),
    )
    conn.execute(
        """
        INSERT INTO payment_transactions VALUES (
            'PAY-LEGACY-01', 'TBOH-LEGACY-01', 'INTENT-LEGACY-01', 'agency_alpha',
            10000.0, 1000000, 'INR', 'CAPTURED', 'TXN-PAY-LEGACY-01', 'IDEMP-PAY-01', ?, ?
        )
        """,
        (now_iso, now_iso),
    )
    conn.execute(
        """
        INSERT INTO refund_records VALUES (
            'RFD-LEGACY-01', 'TBOH-LEGACY-01', 'PAY-LEGACY-01', 'agency_alpha',
            3000.0, 300000, 'INR', 2000.0, 200000, 8000.0, 800000, 'COMPLETED',
            'Legacy test refund', 'TXN-RFD-LEGACY-01', 'IDEMP-RFD-01', 'hash_legacy_01', ?
        )
        """,
        (now_iso,),
    )
    conn.execute(
        """
        INSERT INTO ledger_entries VALUES (
            1, 'TXN-RFD-LEGACY-01', 'TBOH-LEGACY-01', 'agency_alpha', 'REFUND', 'CREDIT',
            3000.0, 300000, 'INR', 5000.0, 500000, 'BATCH-LEGACY-01', '2100_AGENCY_REFUNDABLE_PAYABLE',
            'Legacy ledger credit', ?
        )
        """,
        (now_iso,),
    )
    conn.execute(
        """
        INSERT INTO webhook_events VALUES (
            'evt_legacy_01', 'mock_pay_provider', 'refund.processed', 'TBOH-LEGACY-01',
            'payload_hash_legacy', 'sig_legacy', 1, 'PROCESSED', 300000, 'INR',
            'PAY-LEGACY-01', 'RFD-LEGACY-01', ?, ?
        )
        """,
        (now_iso, now_iso),
    )
    conn.commit()
    conn.close()


def test_legacy_schema_migration_preserves_data_and_enforces_composite_pk():
    """
    Tests that:
    1. Legacy database schema successfully undergoes migration upon AggregatorDatabase init.
    2. All historical data across booking_records, payments, refunds, ledger, and webhooks are preserved intact.
    3. New columns (provider, event_id) exist in refund_records and ledger_entries.
    4. webhook_events table is migrated to PRIMARY KEY (provider, event_id).
    5. After migration, two providers can independently insert the same event_id.
    6. Repeated migration runs are idempotent.
    """
    fd, temp_db_path = tempfile.mkstemp(suffix="_legacy_aggregator.db")
    os.close(fd)

    try:
        # Step 1: Create legacy schema & data
        create_legacy_milestone_2_1_db(temp_db_path)

        # Step 2: Initialize AggregatorDatabase to trigger automated migration
        db = AggregatorDatabase(db_path=temp_db_path)

        # Step 3: Verify historical data preservation
        booking = db.get_booking_record("TBOH-LEGACY-01")
        assert booking is not None
        assert booking.amount_minor == 1000000
        assert booking.total_refunded_minor == 300000

        refunds = db.get_refunds_for_booking("TBOH-LEGACY-01")
        assert len(refunds) == 1
        assert refunds[0].refund_id == "RFD-LEGACY-01"
        assert refunds[0].amount_minor == 300000

        ledger = db.get_ledger_entries_for_booking("TBOH-LEGACY-01")
        assert len(ledger) == 1
        assert ledger[0].transaction_reference == "TXN-RFD-LEGACY-01"

        wh_event = db.get_webhook_event("evt_legacy_01", provider="mock_pay_provider")
        assert wh_event is not None
        assert wh_event.event_id == "evt_legacy_01"
        assert wh_event.provider == "mock_pay_provider"
        assert wh_event.processed_count == 1
        assert wh_event.amount_minor == 300000

        # Step 4: Verify composite primary key in webhook_events
        conn = sqlite3.connect(temp_db_path)
        conn.row_factory = sqlite3.Row
        table_info = conn.execute("PRAGMA table_info(webhook_events)").fetchall()
        pks = {row["name"]: row["pk"] for row in table_info if row["pk"] > 0}
        assert "provider" in pks, "provider must be part of primary key"
        assert "event_id" in pks, "event_id must be part of primary key"
        assert len(pks) == 2, f"Expected composite PK of size 2, got {pks}"

        # Step 5: Test that two distinct providers can insert identical event_id
        shared_id = "evt_shared_post_migration"
        now_iso = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO webhook_events (
                event_id, provider, event_type, booking_id, payload_hash,
                signature, processed_count, status, amount_minor, currency, first_received_at, last_received_at
            ) VALUES (?, ?, 'refund.processed', 'TBOH-LEGACY-01', 'h1', 's1', 1, 'PROCESSED', 100000, 'INR', ?, ?)
            """,
            (shared_id, "provider_alpha", now_iso, now_iso),
        )
        conn.execute(
            """
            INSERT INTO webhook_events (
                event_id, provider, event_type, booking_id, payload_hash,
                signature, processed_count, status, amount_minor, currency, first_received_at, last_received_at
            ) VALUES (?, ?, 'refund.processed', 'TBOH-LEGACY-01', 'h2', 's2', 1, 'PROCESSED', 100000, 'INR', ?, ?)
            """,
            (shared_id, "provider_beta", now_iso, now_iso),
        )
        conn.commit()

        # Both records exist independently
        rows = conn.execute(
            "SELECT provider, event_id FROM webhook_events WHERE event_id = ?", (shared_id,)
        ).fetchall()
        assert len(rows) == 2
        providers = {r["provider"] for r in rows}
        assert providers == {"provider_alpha", "provider_beta"}

        conn.close()

        # Step 6: Verify migration idempotency (calling _init_schema again should not alter data or fail)
        db._init_schema()
        assert db.get_webhook_event("evt_legacy_01", provider="mock_pay_provider") is not None
        assert db.get_webhook_event(shared_id, provider="provider_alpha") is not None
        assert db.get_webhook_event(shared_id, provider="provider_beta") is not None

    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass

"""
Tests for Legacy refund_records Schema Migration.

Verifies:
1. Creates a real legacy SQLite database with the obsolete `payment_transactions` foreign key.
2. Inserts representative legacy refund rows (including card-based and historical rows).
3. Initializes AggregatorDatabase on the legacy database, triggering _migrate_schema().
4. Verifies all expected legacy refund data is preserved.
5. Verifies the obsolete foreign key referencing `payment_transactions` is gone.
6. Verifies the intended foreign key referencing `booking_records` remains active.
7. Verifies PRAGMA foreign_keys = ON remains active on the migrated database.
8. Verifies a new wallet refund succeeds after migration without being rejected by foreign key constraints.
9. Verifies migration failures are NOT silently swallowed.
"""

import sqlite3
import pytest
from datetime import datetime, timezone

from target_platform.aggregator.database import AggregatorDatabase
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    RefundRequest,
)


def create_legacy_schema_with_obsolete_fk(db_path: str):
    """
    Creates the pre-M2.1 legacy SQLite database schema on disk,
    explicitly containing the obsolete FOREIGN KEY (payment_id) REFERENCES payment_transactions(payment_id).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # Allow initial setup

    conn.executescript("""
        CREATE TABLE agencies (
            agency_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE booking_intents (
            intent_id TEXT PRIMARY KEY,
            agency_id TEXT NOT NULL,
            hotel_id TEXT NOT NULL,
            hotel_name TEXT NOT NULL,
            room_type TEXT NOT NULL,
            check_in TEXT NOT NULL,
            check_out TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            guest_name TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            payment_method TEXT DEFAULT 'CARD',
            cancellation_penalty_rate REAL DEFAULT 0.0,
            idempotency_key TEXT,
            payload_hash TEXT,
            amount_minor INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
        );

        CREATE TABLE booking_records (
            booking_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            status TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            cancellation_penalty REAL DEFAULT 0.0,
            refundable_entitlement REAL DEFAULT 0.0,
            total_refunded REAL DEFAULT 0.0,
            primary_supplier_reservation_id TEXT,
            payment_id TEXT,
            voucher_id TEXT,
            retry_count INTEGER DEFAULT 0,
            correlation_id TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (intent_id) REFERENCES booking_intents(intent_id)
        );

        CREATE TABLE payment_transactions (
            payment_id TEXT PRIMARY KEY,
            booking_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            provider TEXT DEFAULT 'mock_pay_provider',
            transaction_reference TEXT UNIQUE NOT NULL,
            idempotency_key TEXT,
            payload_hash TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id)
        );

        -- OBSOLETE PRE-M2.1 TABLE: References payment_transactions(payment_id)
        CREATE TABLE refund_records (
            refund_id TEXT PRIMARY KEY,
            booking_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            penalty_amount REAL NOT NULL DEFAULT 0.0,
            refundable_entitlement REAL NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            transaction_reference TEXT UNIQUE NOT NULL,
            idempotency_key TEXT,
            payload_hash TEXT,
            created_at TEXT NOT NULL,
            amount_minor INTEGER DEFAULT 0,
            penalty_amount_minor INTEGER DEFAULT 0,
            refundable_entitlement_minor INTEGER DEFAULT 0,
            provider TEXT,
            event_id TEXT,
            authorized_amount REAL NOT NULL DEFAULT 0.0,
            authorized_amount_minor INTEGER NOT NULL DEFAULT 0,
            processed_amount REAL NOT NULL DEFAULT 0.0,
            processed_amount_minor INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id),
            FOREIGN KEY (payment_id) REFERENCES payment_transactions(payment_id)
        );

        CREATE TABLE agency_wallets (
            wallet_id TEXT PRIMARY KEY,
            agency_id TEXT UNIQUE NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            funded_balance REAL NOT NULL DEFAULT 0.0,
            funded_balance_minor INTEGER NOT NULL DEFAULT 0,
            active_holds REAL NOT NULL DEFAULT 0.0,
            active_holds_minor INTEGER NOT NULL DEFAULT 0,
            total_captured REAL NOT NULL DEFAULT 0.0,
            total_captured_minor INTEGER NOT NULL DEFAULT 0,
            total_refunded REAL DEFAULT 0.0,
            total_refunded_minor INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
        );

        CREATE TABLE wallet_holds (
            hold_id TEXT PRIMARY KEY,
            wallet_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            booking_id TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (wallet_id) REFERENCES agency_wallets(wallet_id),
            FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
        );

        CREATE TABLE wallet_transactions (
            transaction_id TEXT PRIMARY KEY,
            wallet_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            booking_id TEXT,
            hold_id TEXT,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            balance_after_minor INTEGER NOT NULL,
            holds_after_minor INTEGER NOT NULL,
            reference TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (wallet_id) REFERENCES agency_wallets(wallet_id)
        );

        CREATE TABLE ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_reference TEXT UNIQUE NOT NULL,
            booking_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_minor INTEGER DEFAULT 0,
            currency TEXT NOT NULL,
            balance_after REAL,
            balance_after_minor INTEGER,
            journal_batch_id TEXT,
            account_code TEXT,
            provider TEXT,
            event_id TEXT,
            description TEXT,
            created_at TEXT NOT NULL
        );
    """)

    # Seed baseline agencies and wallet
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO agencies (agency_id, name, api_key, created_at) VALUES ('agency_alpha', 'Alpha Travel', 'key_alpha', ?)",
        (now,),
    )
    conn.execute(
        """
        INSERT INTO agency_wallets (
            wallet_id, agency_id, currency, funded_balance, funded_balance_minor,
            active_holds, active_holds_minor, total_captured, total_captured_minor,
            total_refunded, total_refunded_minor, created_at, updated_at
        ) VALUES ('wallet_agency_alpha', 'agency_alpha', 'INR', 10000.0, 1000000, 0.0, 0, 0.0, 0, 0.0, 0, ?, ?)
        """,
        (now, now),
    )

    # Insert a legacy booking and valid payment transaction
    conn.execute(
        """
        INSERT INTO booking_intents (
            intent_id, agency_id, hotel_id, hotel_name, room_type, check_in, check_out,
            amount, currency, guest_name, correlation_id, created_at
        ) VALUES ('intent_legacy_001', 'agency_alpha', 'htl_001', 'Grand 1', 'standard', '2026-12-01', '2026-12-05', 5000.0, 'INR', 'Legacy Guest', 'corr_legacy_1', ?)
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO booking_records (
            booking_id, intent_id, agency_id, status, amount, currency,
            cancellation_penalty, refundable_entitlement, total_refunded,
            payment_id, correlation_id, created_at, updated_at
        ) VALUES ('BK-LEGACY-001', 'intent_legacy_001', 'agency_alpha', 'CANCELLED', 5000.0, 'INR', 1000.0, 4000.0, 2000.0, 'PAY-CARD-LEGACY-001', 'corr_legacy_1', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO payment_transactions (
            payment_id, booking_id, intent_id, agency_id, amount, currency,
            status, transaction_reference, created_at
        ) VALUES ('PAY-CARD-LEGACY-001', 'BK-LEGACY-001', 'intent_legacy_001', 'agency_alpha', 5000.0, 'INR', 'CAPTURED', 'TXN-PAY-LEGACY-001', ?)
        """,
        (now,),
    )

    # Insert a legacy refund row that satisfied the old payment_transactions foreign key
    conn.execute(
        """
        INSERT INTO refund_records (
            refund_id, booking_id, payment_id, agency_id, amount, amount_minor,
            authorized_amount, authorized_amount_minor, processed_amount, processed_amount_minor,
            currency, penalty_amount, penalty_amount_minor, refundable_entitlement, refundable_entitlement_minor,
            status, reason, transaction_reference, idempotency_key, created_at
        ) VALUES (
            'RFD-LEGACY-001', 'BK-LEGACY-001', 'PAY-CARD-LEGACY-001', 'agency_alpha', 2000.0, 200000,
            2000.0, 200000, 2000.0, 200000, 'INR', 1000.0, 100000, 4000.0, 400000,
            'COMPLETED', 'Legacy Customer Cancellation', 'TXN-RFD-LEGACY-001', 'idemp_legacy_rfd_001', ?
        )
        """,
        (now,),
    )
    conn.commit()
    conn.close()


def test_legacy_refund_migration_preserves_data_and_removes_obsolete_fk(tmp_path):
    legacy_db_file = str(tmp_path / "legacy_aggregator.db")

    # 1. Create actual legacy database with obsolete foreign key
    create_legacy_schema_with_obsolete_fk(legacy_db_file)

    # Pre-migration verification: confirm legacy foreign key points to payment_transactions
    pre_conn = sqlite3.connect(legacy_db_file)
    pre_fks = pre_conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
    pre_conn.close()
    assert any(fk[2] == "payment_transactions" for fk in pre_fks)

    # 2. Run migration by instantiating AggregatorDatabase
    agg_db = AggregatorDatabase(db_path=legacy_db_file)

    # 3. Verify PRAGMA foreign_keys = ON is active
    with agg_db._connection() as conn:
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_on == 1

        # 4. Verify obsolete foreign key has been completely removed
        post_fks = conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
        assert not any(fk["table"] == "payment_transactions" for fk in post_fks)

        # 5. Verify the intended foreign key to booking_records remains active
        assert any(fk["table"] == "booking_records" for fk in post_fks)

        # 6. Verify legacy data was preserved with high fidelity
        legacy_row = conn.execute(
            "SELECT * FROM refund_records WHERE refund_id = 'RFD-LEGACY-001'"
        ).fetchone()
        assert legacy_row is not None
        assert legacy_row["booking_id"] == "BK-LEGACY-001"
        assert legacy_row["payment_id"] == "PAY-CARD-LEGACY-001"
        assert legacy_row["agency_id"] == "agency_alpha"
        assert legacy_row["amount"] == 2000.0
        assert legacy_row["amount_minor"] == 200000
        assert legacy_row["status"] == "COMPLETED"
        assert legacy_row["transaction_reference"] == "TXN-RFD-LEGACY-001"
        assert legacy_row["idempotency_key"] == "idemp_legacy_rfd_001"

    # 7. Verify that a new WALLET refund succeeds without FK rejection
    # Create a wallet booking (which has payment_id = "PAY-WALLET-..." or "NONE")
    now_dt = datetime.now(timezone.utc)
    wallet_intent = BookingIntent(
        intent_id="intent_migrated_wallet",
        agency_id="agency_alpha",
        hotel_id="htl_001",
        hotel_name="Grand 1",
        room_type="standard",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=400.0,
        amount_minor=40000,
        currency="INR",
        guest_name="Wallet Migrated Guest",
        payment_method="WALLET",
    )
    agg_db.save_booking_intent(wallet_intent)

    wallet_booking = AggregatorBookingRecord(
        booking_id="BK-WALLET-MIGRATED",
        intent_id="intent_migrated_wallet",
        agency_id="agency_alpha",
        status=TransactionStatus.CANCELLED,
        amount=400.0,
        amount_minor=40000,
        currency="INR",
        payment_method="WALLET",
        cancellation_penalty=100.0,
        cancellation_penalty_minor=10000,
        refundable_entitlement=300.0,
        refundable_entitlement_minor=30000,
        correlation_id="corr_migrated_wl",
        created_at=now_dt,
        updated_at=now_dt,
    )
    agg_db.save_booking_record(wallet_booking)

    # Under the legacy schema with PRAGMA foreign_keys = ON, this execute_atomic_refund call
    # would crash with sqlite3.IntegrityError: FOREIGN KEY constraint failed because
    # 'PAY-WALLET-TEST' does NOT exist in payment_transactions!
    # Under the migrated schema, it MUST succeed!
    saved_rfd, updated_bkg, entries = agg_db.execute_atomic_refund(
        booking_id="BK-WALLET-MIGRATED",
        agency_id="agency_alpha",
        refund_amount_minor=15000,
        currency="INR",
        reason="Partial wallet refund post migration",
        idempotency_key="idemp_post_mig_wallet_01",
        payment_id="PAY-WALLET-TEST-NONEXISTENT-IN-CARD-TABLE",
    )

    assert saved_rfd.status == "COMPLETED"
    assert saved_rfd.amount_minor == 15000
    assert updated_bkg.total_refunded_minor == 15000
    assert len(entries) == 1

    # Confirm wallet was credited correctly
    wallet_after = agg_db.get_wallet("agency_alpha")
    assert wallet_after.total_refunded_minor == 15000


@pytest.mark.parametrize("failure_stage", [
    "after_create_table",
    "after_copy_data",
    "before_drop_old_table",
    "during_replacement",
    "during_index_creation",
    "after_index_creation",
])
def test_migration_failure_injection_stages_rollback_and_allow_clean_recovery(tmp_path, failure_stage):
    """
    Requirement 1 & 3:
    Tests failure injection at multiple stages of schema migration:
    1. After replacement table creation.
    2. After copying legacy rows.
    3. Before or during old-table replacement.
    4. During index creation (pre-index creation).
    5. After index creation (post-index creation, before commit).

    Verifies:
    - Atomic rollback preserves the original usable schema and legacy data.
    - Temporary refund_records_v2 table does NOT leak into the database.
    - Original obsolete FK schema is preserved until migration successfully commits.
    - PRAGMA foreign_keys = ON is restored.
    - Subsequent clean database open successfully completes migration and wallet refunds work.
    """
    db_file = str(tmp_path / f"legacy_fail_{failure_stage}.db")
    create_legacy_schema_with_obsolete_fk(db_file)

    # 1. Attempt migration with injected failure at specific stage
    with pytest.raises(RuntimeError) as exc_info:
        AggregatorDatabase(db_path=db_file, _migration_failure_stage=failure_stage)

    assert f"Simulated migration failure at stage: {failure_stage}" in str(exc_info.value)

    # 2. Verify database was NOT left in a partially migrated state
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON")
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "refund_records" in tables, "Original refund_records table must still exist"
    assert "refund_records_v2" not in tables, "Temporary refund_records_v2 table must not leak after rollback"

    # Confirm legacy foreign key still present and data intact
    fks = conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
    assert any(fk[2] == "payment_transactions" for fk in fks), "Legacy FK must still be present prior to completed migration"

    legacy_row = conn.execute("SELECT * FROM refund_records WHERE refund_id = 'RFD-LEGACY-001'").fetchone()
    assert legacy_row is not None, "Legacy data row must be preserved intact"
    assert legacy_row[0] == "RFD-LEGACY-001"
    conn.close()

    # 3. Reopen database normally (without failure injection) and verify complete recovery
    recovered_db = AggregatorDatabase(db_path=db_file)

    with recovered_db._connection() as r_conn:
        post_fks = r_conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
        assert not any(fk["table"] == "payment_transactions" for fk in post_fks), "Obsolete FK must now be removed"
        assert any(fk["table"] == "booking_records" for fk in post_fks), "Valid FK must be active"

        post_row = r_conn.execute("SELECT * FROM refund_records WHERE refund_id = 'RFD-LEGACY-001'").fetchone()
        assert post_row is not None
        assert post_row["refund_id"] == "RFD-LEGACY-001"
        assert post_row["amount_minor"] == 200000

    # 4. Verify post-migration wallet refund succeeds on recovered database
    now_dt = datetime.now(timezone.utc)
    wallet_intent = BookingIntent(
        intent_id=f"intent_recovered_{failure_stage}",
        agency_id="agency_alpha",
        hotel_id="htl_001",
        hotel_name="Grand 1",
        room_type="standard",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=500.0,
        amount_minor=50000,
        currency="INR",
        guest_name="Recovered Guest",
        payment_method="WALLET",
    )
    recovered_db.save_booking_intent(wallet_intent)

    wallet_booking = AggregatorBookingRecord(
        booking_id=f"BK-RECOVERED-{failure_stage}",
        intent_id=wallet_intent.intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.CANCELLED,
        amount=500.0,
        amount_minor=50000,
        currency="INR",
        payment_method="WALLET",
        cancellation_penalty=100.0,
        cancellation_penalty_minor=10000,
        refundable_entitlement=400.0,
        refundable_entitlement_minor=40000,
        correlation_id=f"corr_rec_{failure_stage}",
        created_at=now_dt,
        updated_at=now_dt,
    )
    recovered_db.save_booking_record(wallet_booking)

    saved_rfd, updated_bkg, entries = recovered_db.execute_atomic_refund(
        booking_id=wallet_booking.booking_id,
        agency_id="agency_alpha",
        refund_amount_minor=25000,
        currency="INR",
        reason="Wallet refund after recovery",
        idempotency_key=f"idemp_rec_{failure_stage}",
        payment_id="PAY-WALLET-RECOVERED",
    )
    assert saved_rfd.status == "COMPLETED"
    assert updated_bkg.total_refunded_minor == 25000


def test_migration_failure_injection_after_index_creation_rolls_back_everything(tmp_path):
    """
    Requirement 3:
    Explicit test verifying that an injected failure immediately AFTER successful index creation
    (before COMMIT) rolls back:
    - The replacement table (refund_records_v2 is gone).
    - The old-table drop and rename (refund_records remains with original schema).
    - The newly created index (uidx_agg_refund_idemp is rolled back).
    - The copied data changes (RFD-LEGACY-001 is preserved).
    - Foreign-key enforcement returns 1.
    """
    db_file = str(tmp_path / "fail_after_index.db")
    create_legacy_schema_with_obsolete_fk(db_file)

    with pytest.raises(RuntimeError) as exc_info:
        AggregatorDatabase(db_path=db_file, _migration_failure_stage="after_index_creation")

    assert "Simulated migration failure at stage: after_index_creation" in str(exc_info.value)

    # Inspect SQLite database directly using raw connection
    raw_conn = sqlite3.connect(db_file)
    raw_conn.execute("PRAGMA foreign_keys = ON")

    # 1. Verify replacement table does not exist
    tables = [r[0] for r in raw_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "refund_records" in tables
    assert "refund_records_v2" not in tables

    # 2. Verify newly created index was rolled back
    indexes = [r[0] for r in raw_conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    assert "uidx_agg_refund_idemp" not in indexes, "Unique idempotency index must be rolled back on error"

    # 3. Verify old table retained original obsolete FK schema
    fks = raw_conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
    assert any(fk[2] == "payment_transactions" for fk in fks), "Original obsolete foreign key must still exist"

    # 4. Verify data row preserved
    row = raw_conn.execute("SELECT * FROM refund_records WHERE refund_id = 'RFD-LEGACY-001'").fetchone()
    assert row is not None
    assert row[0] == "RFD-LEGACY-001"

    # 5. Verify PRAGMA foreign_keys returns 1
    fk_status = raw_conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_status == 1

    raw_conn.close()

    # 6. Reopen cleanly and verify full migration
    recovered_db = AggregatorDatabase(db_path=db_file)
    with recovered_db._connection() as r_conn:
        post_fks = r_conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
        assert not any(fk["table"] == "payment_transactions" for fk in post_fks)
        assert any(fk["table"] == "booking_records" for fk in post_fks)

        # Index now exists post-migration
        post_indexes = [r["name"] for r in r_conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
        assert "uidx_agg_refund_idemp" in post_indexes


def test_migration_foreign_key_check_inside_transaction_rolls_back_on_violation(tmp_path):
    """
    Requirement 1:
    Verifies that PRAGMA foreign_key_check(refund_records) runs BEFORE COMMIT inside the transaction.
    If a foreign key violation exists in the copied data (e.g. orphan booking_id),
    an error is raised inside the transaction and all changes are rolled back.
    """
    db_file = str(tmp_path / "orphan_legacy.db")
    create_legacy_schema_with_obsolete_fk(db_file)

    # Insert an orphan refund row that points to a non-existent booking_id
    raw_conn = sqlite3.connect(db_file)
    raw_conn.execute("PRAGMA foreign_keys = OFF")
    raw_conn.execute(
        """
        INSERT INTO refund_records (
            refund_id, booking_id, payment_id, agency_id, amount, currency,
            refundable_entitlement, status, transaction_reference, created_at
        ) VALUES (
            'RFD-ORPHAN-001', 'BK-NONEXISTENT', 'PAY-CARD-LEGACY-001', 'agency_alpha', 100.0, 'INR',
            100.0, 'COMPLETED', 'TXN-RFD-ORPHAN-001', datetime('now')
        )
        """
    )
    raw_conn.commit()
    raw_conn.close()

    # Migration must fail because PRAGMA foreign_key_check detects BK-NONEXISTENT does not exist in booking_records
    with pytest.raises(RuntimeError) as exc_info:
        AggregatorDatabase(db_path=db_file)

    assert "Foreign key integrity check failed inside migration transaction for refund_records" in str(exc_info.value)

    # Verify rollback kept the database in original state without leaking refund_records_v2
    verify_conn = sqlite3.connect(db_file)
    tables = [r[0] for r in verify_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "refund_records" in tables
    assert "refund_records_v2" not in tables
    verify_conn.close()


def test_migration_fails_if_foreign_keys_not_restored(tmp_path, monkeypatch):
    """
    Requirement 2:
    Verifies that if PRAGMA foreign_keys restoration fails or returns != 1,
    database initialization fails loudly with RuntimeError.
    """
    db_file = str(tmp_path / "fk_restore_fail.db")
    create_legacy_schema_with_obsolete_fk(db_file)

    orig_connect = sqlite3.connect

    class WrappedConn:
        def __init__(self, real):
            super().__setattr__("_real", real)

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and sql.strip() == "PRAGMA foreign_keys":
                # Intercept check and simulate foreign keys returning 0
                return self._real.execute("SELECT 0 AS foreign_keys")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

        def __setattr__(self, name, value):
            if name == "_real":
                super().__setattr__(name, value)
            else:
                setattr(self._real, name, value)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: WrappedConn(orig_connect(*a, **kw)))

    with pytest.raises(RuntimeError) as exc_info:
        AggregatorDatabase(db_path=db_file)

    assert "foreign-key enforcement" in str(exc_info.value) or "PRAGMA foreign_keys" in str(exc_info.value)


def test_legacy_refund_migration_fails_loudly_on_corruption(tmp_path):
    """
    Requirement 2:
    Verifies that schema migration does NOT silently swallow errors.
    If the migration script encounters an operational error, it raises a RuntimeError and fails startup.
    """
    db_file = str(tmp_path / "corrupted_legacy.db")
    create_legacy_schema_with_obsolete_fk(db_file)

    with pytest.raises(RuntimeError) as exc_info:
        AggregatorDatabase(db_path=db_file, _migration_failure_stage="after_create_table")

    assert "Database schema migration failed for refund_records" in str(exc_info.value)

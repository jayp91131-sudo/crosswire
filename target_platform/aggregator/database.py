"""
SQLite Database Layer for Travel Aggregator Platform.
Manages persistent state for:
- Agencies & authentication credentials
- Booking quotes & holds (Discovery & Holding phase)
- Booking intents & booking lifecycle records
- Supplier operations
- Payment transactions
- Refund records with atomic reservation & idempotency enforcement
- Financial ledger entries with balanced double-entry accounting (Debits == Credits)
- Webhook events
- Complete audit events with explicit connection lifecycle management (guaranteed close)
"""

import os
import sqlite3
import hashlib
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from contextlib import contextmanager
from target_platform.money import to_minor_units, from_minor_units
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    QuoteResponse,
    HoldResponse,
    PaymentTransactionRecord,
    PaymentStatus,
    RefundRecord,
    LedgerEntry,
    LedgerDirection,
    LedgerEntryType,
    WebhookEventRecord,
    WebhookPayload,
    WebhookProcessingResponse,
    RefundAuthorizationRequest,
    SUPPORTED_REFUND_EVENT_TYPES,
    UnregisteredProviderError,
    InvalidWebhookSignatureError,
    WebhookPayloadConflictError,
    ExcessiveRefundError,
    UnknownRefundError,
    PaymentMismatchError,
    CurrencyMismatchError,
    UnsupportedWebhookEventError,
    InvalidBookingStateForRefundError,
    AgencyWalletRecord,
    WalletHoldRecord,
    WalletHoldStatus,
    WalletTransactionRecord,
    WalletTransactionType,
    WalletDepositRequest,
    InsufficientWalletFundsError,
    WalletHoldNotFoundError,
    WalletIsolationError,
    AggregatorIdempotencyConflictError,
    AggregatorFencingError,
    BookingClaimStatus,
    BookingOperationClaimRecord,
    SagaRecoveryRecord,
    VoucherRecord,
    VoucherStatus,
)

DEFAULT_AGGREGATOR_DB_PATH = os.getenv(
    "AGGREGATOR_DB_PATH",
    os.path.join(os.path.dirname(__file__), "aggregator.db"),
)


class IdempotencyPayloadConflictError(Exception):
    """Raised when an idempotency key is reused with a mismatched payload."""
    pass


class InvalidWebhookSignatureError(Exception):
    """Raised when an incoming webhook signature is invalid or missing."""
    pass


class InvalidStateTransitionError(Exception):
    """Raised when an illegal booking state transition is attempted."""
    pass


class ExcessiveRefundError(Exception):
    """Raised when a refund exceeds the configured refundable entitlement."""
    pass


class AggregatorDatabase:
    def __init__(self, db_path: str = DEFAULT_AGGREGATOR_DB_PATH, _migration_failure_stage: Optional[str] = None):
        self.db_path = db_path
        self._migration_failure_stage = _migration_failure_stage
        self._init_schema()

    def _check_migration_failure_hook(self, stage: str):
        if self._migration_failure_stage and self._migration_failure_stage == stage:
            raise RuntimeError(f"Simulated migration failure at stage: {stage}")

    def _create_connection(self, timeout: float = 30.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connection(self):
        """Context manager guaranteeing connection commit/rollback and deterministic close."""
        conn = self._create_connection(timeout=30.0)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agencies (
                    agency_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quotes (
                    quote_id TEXT PRIMARY KEY,
                    agency_id TEXT NOT NULL,
                    hotel_id TEXT NOT NULL,
                    room_type TEXT NOT NULL,
                    check_in TEXT NOT NULL,
                    check_out TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    guest_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
                );

                CREATE TABLE IF NOT EXISTS holds (
                    hold_id TEXT PRIMARY KEY,
                    quote_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (quote_id) REFERENCES quotes(quote_id),
                    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
                );

                CREATE TABLE IF NOT EXISTS booking_intents (
                    intent_id TEXT PRIMARY KEY,
                    agency_id TEXT NOT NULL,
                    hotel_id TEXT NOT NULL,
                    hotel_name TEXT NOT NULL,
                    room_type TEXT NOT NULL,
                    check_in TEXT NOT NULL,
                    check_out TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    guest_name TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    cancellation_penalty_rate REAL DEFAULT 0.20,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
                );

                CREATE TABLE IF NOT EXISTS booking_records (
                    booking_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 15000.0,
                    amount_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'INR',
                    cancellation_penalty REAL DEFAULT 0.0,
                    cancellation_penalty_minor INTEGER DEFAULT 0,
                    refundable_entitlement REAL DEFAULT 0.0,
                    refundable_entitlement_minor INTEGER DEFAULT 0,
                    total_refunded REAL DEFAULT 0.0,
                    total_refunded_minor INTEGER DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS vouchers (
                    voucher_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ISSUED',
                    issued_at TEXT NOT NULL,
                    FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id),
                    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
                );

                CREATE TABLE IF NOT EXISTS payment_transactions (
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
                    payload_hash TEXT,
                    error_message TEXT,
                    provider TEXT DEFAULT 'mock_pay_provider',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uidx_agg_payment_idemp 
                ON payment_transactions (agency_id, idempotency_key) 
                WHERE idempotency_key IS NOT NULL AND idempotency_key != '';

                CREATE TABLE IF NOT EXISTS refund_records (
                    refund_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL,
                    payment_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_minor INTEGER NOT NULL DEFAULT 0,
                    authorized_amount REAL NOT NULL DEFAULT 0.0,
                    authorized_amount_minor INTEGER NOT NULL DEFAULT 0,
                    processed_amount REAL NOT NULL DEFAULT 0.0,
                    processed_amount_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    penalty_amount REAL NOT NULL DEFAULT 0.0,
                    penalty_amount_minor INTEGER NOT NULL DEFAULT 0,
                    refundable_entitlement REAL NOT NULL,
                    refundable_entitlement_minor INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reason TEXT,
                    transaction_reference TEXT UNIQUE NOT NULL,
                    idempotency_key TEXT,
                    payload_hash TEXT,
                    provider TEXT,
                    event_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id)
                );

                CREATE TABLE IF NOT EXISTS ledger_entries (
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
                    provider TEXT,
                    event_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS webhook_events (
                    event_id TEXT NOT NULL,
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
                    last_received_at TEXT NOT NULL,
                    PRIMARY KEY (provider, event_id)
                );

                CREATE TABLE IF NOT EXISTS supplier_operations (
                    operation_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    idempotency_key TEXT,
                    endpoint TEXT NOT NULL,
                    status_code INTEGER,
                    supplier_reservation_id TEXT,
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id)
                );

                CREATE TABLE IF NOT EXISTS transaction_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    booking_id TEXT,
                    correlation_id TEXT,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agency_wallets (
                    wallet_id TEXT PRIMARY KEY,
                    agency_id TEXT UNIQUE NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'INR',
                    funded_balance REAL NOT NULL DEFAULT 0.0,
                    funded_balance_minor INTEGER NOT NULL DEFAULT 0,
                    active_holds REAL NOT NULL DEFAULT 0.0,
                    active_holds_minor INTEGER NOT NULL DEFAULT 0,
                    total_captured REAL NOT NULL DEFAULT 0.0,
                    total_captured_minor INTEGER NOT NULL DEFAULT 0,
                    total_refunded REAL NOT NULL DEFAULT 0.0,
                    total_refunded_minor INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
                );

                CREATE TABLE IF NOT EXISTS booking_operation_claims (
                    agency_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    booking_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL DEFAULT 1,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    last_owner_token TEXT,
                    persisted_stage TEXT,
                    failure_reason TEXT,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agency_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS saga_recovery_records (
                    recovery_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    target_supplier_reservation_id TEXT,
                    wallet_hold_id TEXT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER DEFAULT 1,
                    last_error TEXT,
                    lease_expires_at TEXT,
                    owner_token TEXT,
                    fencing_token INTEGER DEFAULT 0,
                    next_attempt_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wallet_holds (
                    hold_id TEXT PRIMARY KEY,
                    wallet_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    booking_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'INR',
                    status TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (wallet_id) REFERENCES agency_wallets(wallet_id),
                    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id)
                );

                CREATE TABLE IF NOT EXISTS wallet_transactions (
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

                -- Seed synthetic agencies with distinct credentials
                INSERT OR IGNORE INTO agencies (agency_id, name, api_key, created_at)
                VALUES ('agency_alpha', 'Agency Alpha B2B Travels', 'secret_alpha_key_99', datetime('now'));

                INSERT OR IGNORE INTO agencies (agency_id, name, api_key, created_at)
                VALUES ('agency_beta', 'Agency Beta Global Holidays', 'secret_beta_key_88', datetime('now'));

                -- Seed synthetic agency wallets with default ₹10,000 (1,000,000 minor units)
                INSERT OR IGNORE INTO agency_wallets (
                    wallet_id, agency_id, currency, funded_balance, funded_balance_minor,
                    active_holds, active_holds_minor, total_captured, total_captured_minor,
                    created_at, updated_at
                ) VALUES 
                ('wallet_agency_alpha', 'agency_alpha', 'INR', 10000.0, 1000000, 0.0, 0, 0.0, 0, datetime('now'), datetime('now')),
                ('wallet_agency_beta', 'agency_beta', 'INR', 10000.0, 1000000, 0.0, 0, 0.0, 0, datetime('now'), datetime('now'));

                INSERT OR IGNORE INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES
                ('wtx_init_alpha', 'wallet_agency_alpha', 'agency_alpha', NULL, NULL, 'DEPOSIT', 10000.0, 1000000, 'INR', 1000000, 0, 'INITIAL_SEED', 'Initial agency wallet seed balance', datetime('now')),
                ('wtx_init_beta', 'wallet_agency_beta', 'agency_beta', NULL, NULL, 'DEPOSIT', 10000.0, 1000000, 'INR', 1000000, 0, 'INITIAL_SEED', 'Initial agency wallet seed balance', datetime('now'));
            """)

            # Schema migrations for backward compatibility with existing databases on disk
            migrations = [
                ("booking_intents", "cancellation_penalty_rate", "REAL DEFAULT 0.20"),
                ("booking_intents", "amount_minor", "INTEGER DEFAULT 0"),
                ("quotes", "amount_minor", "INTEGER DEFAULT 0"),
                ("booking_records", "amount", "REAL NOT NULL DEFAULT 15000.0"),
                ("booking_records", "amount_minor", "INTEGER DEFAULT 0"),
                ("booking_records", "currency", "TEXT NOT NULL DEFAULT 'INR'"),
                ("booking_records", "cancellation_penalty", "REAL DEFAULT 0.0"),
                ("booking_records", "cancellation_penalty_minor", "INTEGER DEFAULT 0"),
                ("booking_records", "refundable_entitlement", "REAL DEFAULT 0.0"),
                ("booking_records", "refundable_entitlement_minor", "INTEGER DEFAULT 0"),
                ("booking_records", "total_refunded", "REAL DEFAULT 0.0"),
                ("booking_records", "total_refunded_minor", "INTEGER DEFAULT 0"),
                ("booking_records", "payment_id", "TEXT"),
                ("booking_records", "voucher_id", "TEXT"),
                ("payment_transactions", "amount_minor", "INTEGER DEFAULT 0"),
                ("payment_transactions", "provider", "TEXT DEFAULT 'mock_pay_provider'"),
                ("refund_records", "amount_minor", "INTEGER DEFAULT 0"),
                ("refund_records", "authorized_amount", "REAL NOT NULL DEFAULT 0.0"),
                ("refund_records", "authorized_amount_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("refund_records", "processed_amount", "REAL NOT NULL DEFAULT 0.0"),
                ("refund_records", "processed_amount_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("refund_records", "penalty_amount_minor", "INTEGER DEFAULT 0"),
                ("refund_records", "refundable_entitlement_minor", "INTEGER DEFAULT 0"),
                ("refund_records", "provider", "TEXT"),
                ("refund_records", "event_id", "TEXT"),
                ("ledger_entries", "amount_minor", "INTEGER DEFAULT 0"),
                ("ledger_entries", "balance_after_minor", "INTEGER"),
                ("ledger_entries", "journal_batch_id", "TEXT"),
                ("ledger_entries", "account_code", "TEXT"),
                ("ledger_entries", "provider", "TEXT"),
                ("ledger_entries", "event_id", "TEXT"),
                ("webhook_events", "amount_minor", "INTEGER DEFAULT 0"),
                ("webhook_events", "currency", "TEXT DEFAULT 'INR'"),
                ("webhook_events", "payment_id", "TEXT"),
                ("webhook_events", "refund_id", "TEXT"),
                ("booking_records", "payment_method", "TEXT DEFAULT 'CARD'"),
                ("booking_records", "wallet_hold_id", "TEXT"),
                ("booking_records", "idempotency_key", "TEXT"),
                ("booking_records", "payload_hash", "TEXT"),
                ("booking_intents", "payment_method", "TEXT DEFAULT 'CARD'"),
                ("booking_intents", "idempotency_key", "TEXT"),
                ("booking_intents", "payload_hash", "TEXT"),
                ("agency_wallets", "total_refunded", "REAL DEFAULT 0.0"),
                ("agency_wallets", "total_refunded_minor", "INTEGER DEFAULT 0"),
                ("booking_operation_claims", "fencing_token", "INTEGER NOT NULL DEFAULT 1"),
                ("booking_operation_claims", "lease_expires_at", "TEXT"),
                ("booking_operation_claims", "heartbeat_at", "TEXT"),
                ("booking_operation_claims", "last_owner_token", "TEXT"),
                ("booking_operation_claims", "persisted_stage", "TEXT"),
                ("saga_recovery_records", "lease_expires_at", "TEXT"),
                ("saga_recovery_records", "owner_token", "TEXT"),
                ("saga_recovery_records", "fencing_token", "INTEGER DEFAULT 0"),
                ("saga_recovery_records", "next_attempt_at", "TEXT"),
            ]
            for table, col, col_type in migrations:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                except Exception:
                    pass

            # Transactional migration of webhook_events to composite PRIMARY KEY (provider, event_id) (CW-REV-M2-003)
            try:
                table_info = conn.execute("PRAGMA table_info(webhook_events)").fetchall()
                if table_info:
                    pks = [row["name"] for row in table_info if row["pk"] > 0]
                    # If provider is not part of the primary key, migrate to composite PK
                    if "provider" not in pks:
                        conn.executescript("""
                            CREATE TABLE IF NOT EXISTS webhook_events_v2 (
                                event_id TEXT NOT NULL,
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
                                last_received_at TEXT NOT NULL,
                                PRIMARY KEY (provider, event_id)
                            );
                            INSERT OR IGNORE INTO webhook_events_v2 (
                                event_id, provider, event_type, booking_id, payload_hash, signature,
                                processed_count, status, amount_minor, currency, payment_id, refund_id,
                                first_received_at, last_received_at
                            )
                            SELECT 
                                event_id,
                                COALESCE(provider, 'mock_pay_provider'),
                                COALESCE(event_type, 'refund.processed'),
                                booking_id,
                                payload_hash,
                                signature,
                                COALESCE(processed_count, 1),
                                COALESCE(status, 'PROCESSED'),
                                COALESCE(amount_minor, 0),
                                COALESCE(currency, 'INR'),
                                payment_id,
                                refund_id,
                                first_received_at,
                                last_received_at
                            FROM webhook_events;
                            DROP TABLE webhook_events;
                            ALTER TABLE webhook_events_v2 RENAME TO webhook_events;
                        """)
            except Exception:
                pass

            # Migration of refund_records if it carries obsolete foreign key to payment_transactions (which blocks WALLET refunds)
            self._migrate_refund_records_table(conn)

            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_batch ON ledger_entries (journal_batch_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_event_exact ON ledger_entries (provider, event_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_event_id ON ledger_entries (event_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_refund_event_exact ON refund_records (provider, event_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_refund_event_id ON refund_records (event_id)")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uidx_agg_refund_idemp ON refund_records (agency_id, idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key != ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_booking ON webhook_events (booking_id)")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uidx_wh_provider_event ON webhook_events (provider, event_id)")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uidx_agg_booking_idemp ON booking_records (agency_id, idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key != ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_holds_agency ON wallet_holds (agency_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_tx_agency ON wallet_transactions (agency_id)")
            except Exception:
                pass

            # Backfill minor units if needed
            try:
                conn.execute("UPDATE booking_records SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_minor = 0 AND amount > 0")
                conn.execute("UPDATE booking_records SET cancellation_penalty_minor = CAST(ROUND(cancellation_penalty * 100) AS INTEGER) WHERE cancellation_penalty_minor = 0 AND cancellation_penalty > 0")
                conn.execute("UPDATE booking_records SET refundable_entitlement_minor = CAST(ROUND(refundable_entitlement * 100) AS INTEGER) WHERE refundable_entitlement_minor = 0 AND refundable_entitlement > 0")
                conn.execute("UPDATE booking_records SET total_refunded_minor = CAST(ROUND(total_refunded * 100) AS INTEGER) WHERE total_refunded_minor = 0 AND total_refunded > 0")
                conn.execute("UPDATE payment_transactions SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_minor = 0 AND amount > 0")
                conn.execute("UPDATE refund_records SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_minor = 0 AND amount > 0")
                conn.execute("UPDATE refund_records SET authorized_amount = amount, authorized_amount_minor = amount_minor, processed_amount = (CASE WHEN status = 'COMPLETED' THEN amount ELSE 0.0 END), processed_amount_minor = (CASE WHEN status = 'COMPLETED' THEN amount_minor ELSE 0 END) WHERE authorized_amount_minor = 0 AND amount_minor > 0")
                conn.execute("UPDATE refund_records SET refundable_entitlement_minor = CAST(ROUND(refundable_entitlement * 100) AS INTEGER) WHERE refundable_entitlement_minor = 0 AND refundable_entitlement > 0")
                conn.execute("UPDATE ledger_entries SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_minor = 0 AND amount > 0")
            except Exception:
                pass

    def _migrate_refund_records_table(self, conn: sqlite3.Connection):
        """
        Atomically migrates legacy refund_records table to remove obsolete payment_transactions FK.
        Enforces SQLite transaction boundaries and foreign-key PRAGMA rules:
        1. Ensures no transaction is active, then executes PRAGMA foreign_keys = OFF.
        2. Begins an explicit atomic transaction (BEGIN IMMEDIATE).
        3. Creates refund_records_v2 without the obsolete payment_transactions FK constraint.
        4. Copies all legacy data columns into refund_records_v2.
        5. Drops the legacy refund_records table.
        6. Renames refund_records_v2 to refund_records.
        7. Recreates required unique indexes.
        8. Executes PRAGMA foreign_key_check(refund_records) BEFORE COMMIT. If violations exist, rolls back.
        9. Commits the transaction only after validation succeeds.
        10. Restores PRAGMA foreign_keys = ON outside the transaction and verifies it returns 1.
        11. Fails database initialization if foreign-key enforcement could not be restored.
        """
        try:
            fks = conn.execute("PRAGMA foreign_key_list(refund_records)").fetchall()
        except Exception:
            return

        def _is_payment_fk(fk):
            try:
                return fk["table"] == "payment_transactions"
            except (TypeError, IndexError, KeyError):
                return fk[2] == "payment_transactions"

        if not any(_is_payment_fk(fk) for fk in fks):
            return

        logger.info("Migrating legacy refund_records table to remove obsolete payment_transactions foreign key...")
        # 1. Ensure no transaction is currently active before toggling PRAGMA foreign_keys
        conn.commit()

        # 2. Disable foreign keys outside a transaction
        conn.execute("PRAGMA foreign_keys = OFF")

        migration_error = None
        rollback_error = None
        fk_restore_error = None

        try:
            # 3. Begin explicit atomic transaction
            conn.execute("BEGIN IMMEDIATE")

            # 4. Execute all table and index changes
            # Stage 1: Create replacement table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refund_records_v2 (
                    refund_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL,
                    payment_id TEXT NOT NULL,
                    agency_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_minor INTEGER NOT NULL DEFAULT 0,
                    authorized_amount REAL NOT NULL DEFAULT 0.0,
                    authorized_amount_minor INTEGER NOT NULL DEFAULT 0,
                    processed_amount REAL NOT NULL DEFAULT 0.0,
                    processed_amount_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    penalty_amount REAL NOT NULL DEFAULT 0.0,
                    penalty_amount_minor INTEGER NOT NULL DEFAULT 0,
                    refundable_entitlement REAL NOT NULL,
                    refundable_entitlement_minor INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reason TEXT,
                    transaction_reference TEXT UNIQUE NOT NULL,
                    idempotency_key TEXT,
                    payload_hash TEXT,
                    provider TEXT,
                    event_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (booking_id) REFERENCES booking_records(booking_id)
                )
            """)
            self._check_migration_failure_hook("after_create_table")

            # Stage 2: Copy legacy rows
            conn.execute("""
                INSERT INTO refund_records_v2 (
                    refund_id, booking_id, payment_id, agency_id, amount, amount_minor,
                    authorized_amount, authorized_amount_minor, processed_amount, processed_amount_minor,
                    currency, penalty_amount, penalty_amount_minor, refundable_entitlement, refundable_entitlement_minor,
                    status, reason, transaction_reference, idempotency_key, payload_hash,
                    provider, event_id, created_at
                )
                SELECT 
                    refund_id, booking_id, payment_id, agency_id, amount, COALESCE(amount_minor, 0),
                    COALESCE(authorized_amount, 0.0), COALESCE(authorized_amount_minor, 0),
                    COALESCE(processed_amount, 0.0), COALESCE(processed_amount_minor, 0),
                    currency, COALESCE(penalty_amount, 0.0), COALESCE(penalty_amount_minor, 0),
                    refundable_entitlement, COALESCE(refundable_entitlement_minor, 0),
                    status, reason, transaction_reference, idempotency_key, payload_hash,
                    provider, event_id, created_at
                FROM refund_records
            """)
            self._check_migration_failure_hook("after_copy_data")

            # Stage 3: Replace old table
            self._check_migration_failure_hook("before_drop_old_table")
            conn.execute("DROP TABLE refund_records")
            self._check_migration_failure_hook("during_replacement")
            conn.execute("ALTER TABLE refund_records_v2 RENAME TO refund_records")

            # Stage 4: Recreate indexes
            self._check_migration_failure_hook("during_index_creation")
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uidx_agg_refund_idemp 
                ON refund_records (agency_id, idempotency_key) 
                WHERE idempotency_key IS NOT NULL AND idempotency_key != ''
            """)
            self._check_migration_failure_hook("after_index_creation")

            # 5. Run PRAGMA foreign_key_check(refund_records) BEFORE COMMIT
            fk_violations = conn.execute("PRAGMA foreign_key_check(refund_records)").fetchall()
            # 6. If violations exist, raise an error and roll back
            if fk_violations:
                raise RuntimeError(
                    f"Foreign key integrity check failed inside migration transaction for refund_records: {fk_violations}"
                )

            # 7. Commit only after validation succeeds
            conn.execute("COMMIT")
            logger.info("Successfully migrated refund_records table schema.")
        except Exception as exc:
            migration_error = exc
            logger.error(f"Migration error on refund_records: {exc}", exc_info=True)
            try:
                conn.execute("ROLLBACK")
            except Exception as rb_exc:
                rollback_error = rb_exc
                logger.error(f"Rollback failed during migration error handling: {rb_exc}", exc_info=True)
                try:
                    conn.close()
                except Exception:
                    pass
        finally:
            # 8. Restore foreign-key enforcement outside the transaction
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                # 9. Verify PRAGMA foreign_keys returns 1
                fk_status = conn.execute("PRAGMA foreign_keys").fetchone()
                if not fk_status or fk_status[0] != 1:
                    fk_restore_error = RuntimeError(f"PRAGMA foreign_keys check failed; returned {fk_status}, expected 1")
                    logger.error(str(fk_restore_error))
            except Exception as fke:
                fk_restore_error = fke
                logger.error(f"Failed to restore PRAGMA foreign_keys = ON: {fke}", exc_info=True)

        # 10. Fail database initialization if foreign-key enforcement could not be restored or if migration failed
        if migration_error:
            if rollback_error:
                raise RuntimeError(
                    f"Database schema migration failed: {migration_error} AND rollback also failed: {rollback_error}"
                ) from migration_error
            if fk_restore_error:
                raise RuntimeError(
                    f"Database schema migration failed: {migration_error} AND foreign-key restoration also failed: {fk_restore_error}"
                ) from migration_error
            raise RuntimeError(f"Database schema migration failed for refund_records: {migration_error}") from migration_error

        if rollback_error:
            raise RuntimeError(f"Rollback failed unexpectedly: {rollback_error}") from rollback_error

        if fk_restore_error:
            raise RuntimeError(f"Database schema migration failed to restore foreign-key enforcement: {fk_restore_error}") from fk_restore_error

    def reset(self):
        """Resets dynamic tables while preserving registered agencies."""
        with self._connection() as conn:
            conn.execute("DELETE FROM booking_operation_claims")
            conn.execute("DELETE FROM saga_recovery_records")
            conn.execute("DELETE FROM wallet_transactions")
            conn.execute("DELETE FROM wallet_holds")
            conn.execute("DELETE FROM transaction_events")
            conn.execute("DELETE FROM supplier_operations")
            conn.execute("DELETE FROM webhook_events")
            conn.execute("DELETE FROM ledger_entries")
            conn.execute("DELETE FROM refund_records")
            conn.execute("DELETE FROM payment_transactions")
            conn.execute("DELETE FROM vouchers")
            conn.execute("DELETE FROM booking_records")
            conn.execute("DELETE FROM booking_intents")
            conn.execute("DELETE FROM holds")
            conn.execute("DELETE FROM quotes")
            # Reset agency wallets to baseline ₹10,000 (1,000,000 minor units)
            conn.execute("""
                UPDATE agency_wallets 
                SET funded_balance = 10000.0, funded_balance_minor = 1000000,
                    active_holds = 0.0, active_holds_minor = 0,
                    total_captured = 0.0, total_captured_minor = 0,
                    total_refunded = 0.0, total_refunded_minor = 0,
                    updated_at = datetime('now')
                WHERE agency_id IN ('agency_alpha', 'agency_beta')
            """)
            conn.execute("DELETE FROM agency_wallets WHERE agency_id NOT IN ('agency_alpha', 'agency_beta')")
            now_iso = datetime.utcnow().isoformat()
            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES
                ('wtx_init_alpha', 'wallet_agency_alpha', 'agency_alpha', NULL, NULL, 'DEPOSIT', 10000.0, 1000000, 'INR', 1000000, 0, 'INITIAL_SEED', 'Initial agency wallet seed balance', ?),
                ('wtx_init_beta', 'wallet_agency_beta', 'agency_beta', NULL, NULL, 'DEPOSIT', 10000.0, 1000000, 'INR', 1000000, 0, 'INITIAL_SEED', 'Initial agency wallet seed balance', ?)
                """,
                (now_iso, now_iso),
            )

    def authenticate_agency(self, agency_id: str, api_key: str) -> bool:
        if not agency_id or not api_key:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM agencies WHERE agency_id = ? AND api_key = ?",
                (agency_id, api_key),
            ).fetchone()
            return row is not None

    def log_event(self, booking_id: Optional[str], correlation_id: str, actor: str, event_type: str, details: str):
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO transaction_events (booking_id, correlation_id, actor, event_type, details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (booking_id, correlation_id, actor, event_type, details, datetime.utcnow().isoformat()),
            )

    # ---------------------------------------------------------
    # Quotes and Holds
    # ---------------------------------------------------------
    def save_quote(self, quote: QuoteResponse, agency_id: str, check_in: str, check_out: str, guest_name: str):
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quotes (
                    quote_id, agency_id, hotel_id, room_type, check_in, check_out,
                    amount, amount_minor, currency, guest_name, status, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote.quote_id,
                    agency_id,
                    quote.hotel_id,
                    quote.room_type,
                    check_in,
                    check_out,
                    quote.amount,
                    quote.amount_minor,
                    quote.currency,
                    guest_name,
                    quote.status.value,
                    quote.expires_at,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_quote(self, quote_id: str, requesting_agency_id: Optional[str] = None) -> Optional[dict]:
        with self._connection() as conn:
            if requesting_agency_id:
                row = conn.execute(
                    "SELECT * FROM quotes WHERE quote_id = ? AND agency_id = ?",
                    (quote_id, requesting_agency_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM quotes WHERE quote_id = ?", (quote_id,)).fetchone()
            return dict(row) if row else None

    def save_hold(self, hold: HoldResponse, agency_id: str):
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO holds (hold_id, quote_id, agency_id, status, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hold.hold_id,
                    hold.quote_id,
                    agency_id,
                    hold.status.value,
                    hold.expires_at,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_hold(self, hold_id: str, requesting_agency_id: Optional[str] = None) -> Optional[dict]:
        with self._connection() as conn:
            if requesting_agency_id:
                row = conn.execute(
                    "SELECT h.*, q.amount, q.amount_minor, q.currency, q.hotel_id, q.room_type, q.guest_name, q.check_in, q.check_out "
                    "FROM holds h JOIN quotes q ON h.quote_id = q.quote_id "
                    "WHERE h.hold_id = ? AND h.agency_id = ?",
                    (hold_id, requesting_agency_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT h.*, q.amount, q.amount_minor, q.currency, q.hotel_id, q.room_type, q.guest_name, q.check_in, q.check_out "
                    "FROM holds h JOIN quotes q ON h.quote_id = q.quote_id "
                    "WHERE h.hold_id = ?",
                    (hold_id,),
                ).fetchone()
            return dict(row) if row else None

    # ---------------------------------------------------------
    # Booking Intents
    # ---------------------------------------------------------
    def save_booking_intent(self, intent: BookingIntent):
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO booking_intents (
                    intent_id, agency_id, hotel_id, hotel_name, room_type, check_in, check_out,
                    amount, amount_minor, currency, guest_name, correlation_id, cancellation_penalty_rate,
                    payment_method, idempotency_key, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    intent.agency_id,
                    intent.hotel_id,
                    intent.hotel_name,
                    intent.room_type,
                    intent.check_in,
                    intent.check_out,
                    intent.amount,
                    intent.amount_minor,
                    intent.currency,
                    intent.guest_name,
                    intent.correlation_id,
                    intent.cancellation_penalty_rate,
                    getattr(intent, "payment_method", "CARD") or "CARD",
                    getattr(intent, "idempotency_key", None),
                    getattr(intent, "payload_hash", None),
                    intent.created_at.isoformat() if isinstance(intent.created_at, datetime) else str(intent.created_at),
                ),
            )

    def get_booking_intent(
        self, intent_id: str, requesting_agency_id: Optional[str] = None
    ) -> Optional[BookingIntent]:
        with self._connection() as conn:
            if requesting_agency_id:
                row = conn.execute(
                    "SELECT * FROM booking_intents WHERE intent_id = ? AND agency_id = ?",
                    (intent_id, requesting_agency_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM booking_intents WHERE intent_id = ?", (intent_id,)).fetchone()

            if not row:
                return None

            keys = row.keys()
            amount_minor = row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] else to_minor_units(row["amount"], row["currency"])
            return BookingIntent(
                intent_id=row["intent_id"],
                agency_id=row["agency_id"],
                hotel_id=row["hotel_id"],
                hotel_name=row["hotel_name"],
                room_type=row["room_type"],
                check_in=row["check_in"],
                check_out=row["check_out"],
                amount=row["amount"],
                amount_minor=amount_minor,
                currency=row["currency"],
                guest_name=row["guest_name"],
                correlation_id=row["correlation_id"],
                cancellation_penalty_rate=row["cancellation_penalty_rate"] if "cancellation_penalty_rate" in keys else 0.20,
                payment_method=row["payment_method"] if "payment_method" in keys and row["payment_method"] else "CARD",
                idempotency_key=row["idempotency_key"] if "idempotency_key" in keys else None,
                payload_hash=row["payload_hash"] if "payload_hash" in keys else None,
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    save_intent = save_booking_intent
    get_intent = get_booking_intent

    # ---------------------------------------------------------
    # Booking Records
    # ---------------------------------------------------------
    def save_booking_record(self, record: AggregatorBookingRecord):
        status_val = record.status.value if isinstance(record.status, TransactionStatus) else str(record.status)
        pm = getattr(record, "payment_method", "CARD") or "CARD"
        wh = getattr(record, "wallet_hold_id", None)
        ik = getattr(record, "idempotency_key", None)
        ph = getattr(record, "payload_hash", None)
        created_str = record.created_at.isoformat() if isinstance(record.created_at, datetime) else str(record.created_at)
        updated_str = record.updated_at.isoformat() if isinstance(record.updated_at, datetime) else str(record.updated_at)

        with self._connection() as conn:
            existing = conn.execute("SELECT 1 FROM booking_records WHERE booking_id = ?", (record.booking_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE booking_records SET
                        intent_id = ?, agency_id = ?, status = ?, amount = ?, amount_minor = ?, currency = ?,
                        cancellation_penalty = ?, cancellation_penalty_minor = ?,
                        refundable_entitlement = ?, refundable_entitlement_minor = ?,
                        total_refunded = ?, total_refunded_minor = ?,
                        primary_supplier_reservation_id = ?, payment_id = ?, voucher_id = ?,
                        retry_count = ?, correlation_id = ?, payment_method = ?, wallet_hold_id = ?,
                        idempotency_key = ?, payload_hash = ?, error_message = ?, updated_at = ?
                    WHERE booking_id = ?
                    """,
                    (
                        record.intent_id,
                        record.agency_id,
                        status_val,
                        record.amount,
                        record.amount_minor,
                        record.currency,
                        record.cancellation_penalty,
                        record.cancellation_penalty_minor,
                        record.refundable_entitlement,
                        record.refundable_entitlement_minor,
                        record.total_refunded,
                        record.total_refunded_minor,
                        record.primary_supplier_reservation_id,
                        record.payment_id,
                        record.voucher_id,
                        record.retry_count,
                        record.correlation_id,
                        pm,
                        wh,
                        ik,
                        ph,
                        record.error_message,
                        updated_str,
                        record.booking_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO booking_records (
                        booking_id, intent_id, agency_id, status, amount, amount_minor, currency,
                        cancellation_penalty, cancellation_penalty_minor,
                        refundable_entitlement, refundable_entitlement_minor,
                        total_refunded, total_refunded_minor,
                        primary_supplier_reservation_id, payment_id, voucher_id,
                        retry_count, correlation_id, payment_method, wallet_hold_id,
                        idempotency_key, payload_hash, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.booking_id,
                        record.intent_id,
                        record.agency_id,
                        status_val,
                        record.amount,
                        record.amount_minor,
                        record.currency,
                        record.cancellation_penalty,
                        record.cancellation_penalty_minor,
                        record.refundable_entitlement,
                        record.refundable_entitlement_minor,
                        record.total_refunded,
                        record.total_refunded_minor,
                        record.primary_supplier_reservation_id,
                        record.payment_id,
                        record.voucher_id,
                        record.retry_count,
                        record.correlation_id,
                        pm,
                        wh,
                        ik,
                        ph,
                        record.error_message,
                        created_str,
                        updated_str,
                    ),
                )

    def get_booking_record(
        self, booking_id: str, requesting_agency_id: Optional[str] = None
    ) -> Optional[AggregatorBookingRecord]:
        with self._connection() as conn:
            if requesting_agency_id:
                row = conn.execute(
                    "SELECT * FROM booking_records WHERE booking_id = ? AND agency_id = ?",
                    (booking_id, requesting_agency_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM booking_records WHERE booking_id = ?", (booking_id,)).fetchone()

            if not row:
                return None
            return self._row_to_booking(row)

    def get_booking_by_intent(
        self, intent_id: str, requesting_agency_id: Optional[str] = None
    ) -> Optional[AggregatorBookingRecord]:
        with self._connection() as conn:
            if requesting_agency_id:
                row = conn.execute(
                    "SELECT * FROM booking_records WHERE intent_id = ? AND agency_id = ?",
                    (intent_id, requesting_agency_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM booking_records WHERE intent_id = ?", (intent_id,)).fetchone()

            if not row:
                return None
            return self._row_to_booking(row)

    def get_booking_by_idempotency_key(
        self, agency_id: str, idempotency_key: str
    ) -> Optional[AggregatorBookingRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM booking_records WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            if not row:
                return None
            return self._row_to_booking(row)

    # ---------------------------------------------------------
    # Voucher Management
    # ---------------------------------------------------------
    def save_voucher(self, voucher: VoucherRecord) -> VoucherRecord:
        now_iso = voucher.issued_at.isoformat() if isinstance(voucher.issued_at, datetime) else str(voucher.issued_at)
        status_val = voucher.status.value if hasattr(voucher.status, "value") else str(voucher.status)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO vouchers
                (voucher_id, booking_id, agency_id, status, issued_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (voucher.voucher_id, voucher.booking_id, voucher.agency_id, status_val, now_iso),
            )
        return voucher

    def get_voucher(self, voucher_id: str) -> Optional[VoucherRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM vouchers WHERE voucher_id = ?",
                (voucher_id,),
            ).fetchone()
            if not row:
                return None
            return VoucherRecord(
                voucher_id=row["voucher_id"],
                booking_id=row["booking_id"],
                agency_id=row["agency_id"],
                status=VoucherStatus(row["status"]),
                issued_at=datetime.fromisoformat(row["issued_at"]),
            )

    def get_vouchers_by_booking(self, booking_id: str) -> List[VoucherRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM vouchers WHERE booking_id = ?",
                (booking_id,),
            ).fetchall()
            return [
                VoucherRecord(
                    voucher_id=r["voucher_id"],
                    booking_id=r["booking_id"],
                    agency_id=r["agency_id"],
                    status=VoucherStatus(r["status"]),
                    issued_at=datetime.fromisoformat(r["issued_at"]),
                )
                for r in rows
            ]

    def get_bookings_by_voucher(self, voucher_id: str) -> List[AggregatorBookingRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM booking_records WHERE voucher_id = ?",
                (voucher_id,),
            ).fetchall()
            return [self._row_to_booking(r) for r in rows]

    def _row_to_booking(self, row: sqlite3.Row) -> AggregatorBookingRecord:
        keys = row.keys()
        currency = row["currency"] if "currency" in keys else "INR"
        amount = row["amount"] if "amount" in keys else 15000.0
        amount_minor = row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] else to_minor_units(amount, currency)
        
        cancellation_penalty = row["cancellation_penalty"] if "cancellation_penalty" in keys else 0.0
        cancellation_penalty_minor = row["cancellation_penalty_minor"] if "cancellation_penalty_minor" in keys and row["cancellation_penalty_minor"] else to_minor_units(cancellation_penalty, currency)
        
        refundable_entitlement = row["refundable_entitlement"] if "refundable_entitlement" in keys else 0.0
        refundable_entitlement_minor = row["refundable_entitlement_minor"] if "refundable_entitlement_minor" in keys and row["refundable_entitlement_minor"] else to_minor_units(refundable_entitlement, currency)
        
        total_refunded = row["total_refunded"] if "total_refunded" in keys else 0.0
        total_refunded_minor = row["total_refunded_minor"] if "total_refunded_minor" in keys and row["total_refunded_minor"] else to_minor_units(total_refunded, currency)

        return AggregatorBookingRecord(
            booking_id=row["booking_id"],
            intent_id=row["intent_id"],
            agency_id=row["agency_id"],
            status=TransactionStatus(row["status"]),
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            cancellation_penalty=cancellation_penalty,
            cancellation_penalty_minor=cancellation_penalty_minor,
            refundable_entitlement=refundable_entitlement,
            refundable_entitlement_minor=refundable_entitlement_minor,
            total_refunded=total_refunded,
            total_refunded_minor=total_refunded_minor,
            primary_supplier_reservation_id=row["primary_supplier_reservation_id"],
            payment_id=row["payment_id"] if "payment_id" in keys else None,
            voucher_id=row["voucher_id"] if "voucher_id" in keys else None,
            retry_count=row["retry_count"],
            correlation_id=row["correlation_id"],
            payment_method=row["payment_method"] if "payment_method" in keys and row["payment_method"] else "CARD",
            wallet_hold_id=row["wallet_hold_id"] if "wallet_hold_id" in keys else None,
            idempotency_key=row["idempotency_key"] if "idempotency_key" in keys else None,
            payload_hash=row["payload_hash"] if "payload_hash" in keys else None,
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ---------------------------------------------------------
    # Payment Transactions
    # ---------------------------------------------------------
    def save_payment_transaction(self, tx: PaymentTransactionRecord) -> PaymentTransactionRecord:
        try:
            with self._connection() as conn:
                provider = getattr(tx, "provider", "mock_pay_provider") or "mock_pay_provider"
                conn.execute(
                    """
                    INSERT INTO payment_transactions (
                        payment_id, booking_id, intent_id, agency_id, amount, amount_minor, currency,
                        status, transaction_reference, idempotency_key, payload_hash, error_message,
                        created_at, updated_at, provider
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx.payment_id,
                        tx.booking_id,
                        tx.intent_id,
                        tx.agency_id,
                        tx.amount,
                        tx.amount_minor,
                        tx.currency,
                        tx.status.value if hasattr(tx.status, "value") else str(tx.status),
                        tx.transaction_reference,
                        tx.idempotency_key,
                        tx.payload_hash,
                        tx.error_message,
                        tx.created_at.isoformat() if isinstance(tx.created_at, datetime) else str(tx.created_at),
                        tx.updated_at.isoformat() if isinstance(tx.updated_at, datetime) else str(tx.updated_at),
                        provider,
                    ),
                )
            return tx
        except sqlite3.IntegrityError:
            if tx.idempotency_key:
                existing = self.get_payment_by_idempotency_key(tx.agency_id, tx.idempotency_key)
                if existing:
                    if tx.payload_hash and existing.payload_hash and tx.payload_hash != existing.payload_hash:
                        raise IdempotencyPayloadConflictError(
                            f"Payment idempotency key '{tx.idempotency_key}' conflict: payload hash mismatch"
                        )
                    return existing
            raise

    def get_payment_transaction(self, payment_id: str) -> Optional[PaymentTransactionRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM payment_transactions WHERE payment_id = ?", (payment_id,)).fetchone()
            if not row:
                return None
            return self._row_to_payment(row)

    def get_payment_by_booking(self, booking_id: str) -> Optional[PaymentTransactionRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM payment_transactions WHERE booking_id = ? ORDER BY created_at DESC LIMIT 1",
                (booking_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_payment(row)

    def get_payment_by_idempotency_key(self, agency_id: str, idempotency_key: str) -> Optional[PaymentTransactionRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM payment_transactions WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            if not row:
                return None
            return self._row_to_payment(row)

    def _row_to_payment(self, row: sqlite3.Row) -> PaymentTransactionRecord:
        keys = row.keys()
        amount = row["amount"]
        currency = row["currency"]
        amount_minor = row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] else to_minor_units(amount, currency)
        provider = row["provider"] if "provider" in keys and row["provider"] else "mock_pay_provider"
        return PaymentTransactionRecord(
            payment_id=row["payment_id"],
            booking_id=row["booking_id"],
            intent_id=row["intent_id"],
            agency_id=row["agency_id"],
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            status=PaymentStatus(row["status"]),
            transaction_reference=row["transaction_reference"],
            idempotency_key=row["idempotency_key"],
            payload_hash=row["payload_hash"],
            error_message=row["error_message"],
            provider=provider,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ---------------------------------------------------------
    # Refund Records & Atomic Refund Execution (Requirement 2 & 3)
    # ---------------------------------------------------------
    def get_refund_record(self, refund_id: str) -> Optional[RefundRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM refund_records WHERE refund_id = ?", (refund_id,)).fetchone()
            if not row:
                return None
            return self._row_to_refund(row)

    def authorize_refund(
        self,
        booking_id: str,
        refund_id: str,
        amount_minor: int,
        currency: str = "INR",
        payment_id: Optional[str] = None,
        agency_id: Optional[str] = None,
        reason: str = "Authorized refund instruction",
        idempotency_key: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> RefundRecord:
        """
        Registers an authorized refund instruction in the database (CW-REV-M2-002).
        Validates booking existence and remaining refundable entitlement.
        """
        with self._connection() as conn:
            b_row = conn.execute("SELECT * FROM booking_records WHERE booking_id = ?", (booking_id,)).fetchone()
            if not b_row:
                raise ValueError(f"Booking '{booking_id}' not found")

            p_id = payment_id or b_row["payment_id"]
            a_id = agency_id or b_row["agency_id"]
            p_minor = b_row["cancellation_penalty_minor"]
            e_minor = b_row["refundable_entitlement_minor"]
            now_iso = datetime.utcnow().isoformat()
            tx_ref = f"TXN-AUTH-RFD-{refund_id}"
            auth_amount = from_minor_units(amount_minor, currency)

            conn.execute(
                """
                INSERT OR REPLACE INTO refund_records (
                    refund_id, booking_id, payment_id, agency_id, amount, amount_minor,
                    authorized_amount, authorized_amount_minor, processed_amount, processed_amount_minor,
                    currency, penalty_amount, penalty_amount_minor, refundable_entitlement, refundable_entitlement_minor,
                    status, reason, transaction_reference, idempotency_key, payload_hash,
                    provider, event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refund_id,
                    booking_id,
                    p_id,
                    a_id,
                    auth_amount,
                    amount_minor,
                    auth_amount,
                    amount_minor,
                    0.0,
                    0,
                    currency,
                    from_minor_units(p_minor, currency),
                    p_minor,
                    from_minor_units(e_minor, currency),
                    e_minor,
                    "AUTHORIZED",
                    reason,
                    tx_ref,
                    idempotency_key,
                    None,
                    provider,
                    None,
                    now_iso,
                ),
            )
            row = conn.execute("SELECT * FROM refund_records WHERE refund_id = ?", (refund_id,)).fetchone()
            return self._row_to_refund(row)

    def get_refunds_for_booking(self, booking_id: str) -> List[RefundRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM refund_records WHERE booking_id = ? ORDER BY created_at ASC",
                (booking_id,),
            ).fetchall()
            return [self._row_to_refund(r) for r in rows]

    def get_refund_by_idempotency_key(self, agency_id: str, idempotency_key: str) -> Optional[RefundRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM refund_records WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            if not row:
                return None
            return self._row_to_refund(row)

    def _row_to_refund(self, row: sqlite3.Row) -> RefundRecord:
        keys = row.keys()
        amount = row["amount"]
        currency = row["currency"]
        amount_minor = row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] else to_minor_units(amount, currency)
        auth_minor = row["authorized_amount_minor"] if "authorized_amount_minor" in keys and row["authorized_amount_minor"] is not None and row["authorized_amount_minor"] > 0 else amount_minor
        auth_amount = row["authorized_amount"] if "authorized_amount" in keys and row["authorized_amount"] is not None and row["authorized_amount"] > 0 else from_minor_units(auth_minor, currency)
        proc_minor = row["processed_amount_minor"] if "processed_amount_minor" in keys and row["processed_amount_minor"] is not None else (amount_minor if row["status"] == "COMPLETED" else 0)
        proc_amount = row["processed_amount"] if "processed_amount" in keys and row["processed_amount"] is not None else from_minor_units(proc_minor, currency)
        penalty = row["penalty_amount"]
        penalty_minor = row["penalty_amount_minor"] if "penalty_amount_minor" in keys and row["penalty_amount_minor"] else to_minor_units(penalty, currency)
        entitlement = row["refundable_entitlement"]
        entitlement_minor = row["refundable_entitlement_minor"] if "refundable_entitlement_minor" in keys and row["refundable_entitlement_minor"] else to_minor_units(entitlement, currency)
        provider = row["provider"] if "provider" in keys else None
        event_id = row["event_id"] if "event_id" in keys else None
        return RefundRecord(
            refund_id=row["refund_id"],
            booking_id=row["booking_id"],
            payment_id=row["payment_id"],
            agency_id=row["agency_id"],
            amount=amount,
            amount_minor=amount_minor,
            authorized_amount=auth_amount,
            authorized_amount_minor=auth_minor,
            processed_amount=proc_amount,
            processed_amount_minor=proc_minor,
            currency=currency,
            penalty_amount=penalty,
            penalty_amount_minor=penalty_minor,
            refundable_entitlement=entitlement,
            refundable_entitlement_minor=entitlement_minor,
            status=row["status"],
            reason=row["reason"],
            transaction_reference=row["transaction_reference"],
            idempotency_key=row["idempotency_key"],
            payload_hash=row["payload_hash"],
            provider=provider,
            event_id=event_id,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def execute_atomic_refund(
        self,
        booking_id: str,
        agency_id: str,
        refund_amount_minor: int,
        currency: str,
        reason: str,
        idempotency_key: Optional[str] = None,
        payload_hash: Optional[str] = None,
        payment_id: Optional[str] = None,
    ) -> Tuple[RefundRecord, AggregatorBookingRecord, List[LedgerEntry]]:
        """
        Executes an atomic refund operation inside an exclusive SQLite transaction (BEGIN IMMEDIATE).
        Guarantees:
        1. Atomic verification of refundable entitlement vs cumulative completed refunds.
        2. Database-level atomic reservation via conditional UPDATE:
           WHERE (total_refunded_minor + ?) <= refundable_entitlement_minor
        3. Strict idempotency enforcement and payload conflict detection.
        4. Balanced double-entry financial ledger posting (Debits == Credits).
        5. Zero race conditions under parallel concurrent execution.
        """
        if refund_amount_minor <= 0:
            raise ValueError("Refund amount must be strictly greater than zero")

        conn = self._create_connection(timeout=30.0)
        conn.isolation_level = None  # explicit transaction control
        try:
            conn.execute("BEGIN IMMEDIATE")

            # 1. Idempotency Check
            if idempotency_key:
                existing_row = conn.execute(
                    "SELECT * FROM refund_records WHERE agency_id = ? AND idempotency_key = ?",
                    (agency_id, idempotency_key),
                ).fetchone()
                if existing_row:
                    existing_refund = self._row_to_refund(existing_row)
                    if payload_hash and existing_refund.payload_hash and existing_refund.payload_hash != payload_hash:
                        conn.execute("ROLLBACK")
                        raise IdempotencyPayloadConflictError(
                            f"Refund idempotency key '{idempotency_key}' conflict: payload hash mismatch"
                        )
                    # Fetch current booking record and commit idempotent read
                    b_row = conn.execute(
                        "SELECT * FROM booking_records WHERE booking_id = ? AND agency_id = ?",
                        (booking_id, agency_id),
                    ).fetchone()
                    booking_rec = self._row_to_booking(b_row)
                    conn.execute("COMMIT")
                    return existing_refund, booking_rec, []

            # 2. Fetch booking record with exclusive lock
            b_row = conn.execute(
                "SELECT * FROM booking_records WHERE booking_id = ? AND agency_id = ?",
                (booking_id, agency_id),
            ).fetchone()
            if not b_row:
                conn.execute("ROLLBACK")
                raise ValueError(f"Booking '{booking_id}' not found or unauthorized")

            status = b_row["status"]
            if status not in [TransactionStatus.CANCELLED.value, TransactionStatus.PARTIALLY_REFUNDED.value]:
                conn.execute("ROLLBACK")
                raise InvalidStateTransitionError(
                    f"Cannot refund booking in status '{status}'. Booking must be CANCELLED first."
                )

            entitlement_minor = b_row["refundable_entitlement_minor"]
            current_refunded_minor = b_row["total_refunded_minor"]
            available_minor = entitlement_minor - current_refunded_minor

            # 3. Check remaining entitlement
            if refund_amount_minor > available_minor:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError(
                    f"Requested refund amount {from_minor_units(refund_amount_minor, currency)} {currency} "
                    f"exceeds remaining refundable entitlement {from_minor_units(available_minor, currency)} {currency}"
                )

            new_total_refunded_minor = current_refunded_minor + refund_amount_minor
            new_total_refunded = from_minor_units(new_total_refunded_minor, currency)
            new_status = (
                TransactionStatus.REFUNDED.value
                if new_total_refunded_minor >= entitlement_minor
                else TransactionStatus.PARTIALLY_REFUNDED.value
            )
            now_iso = datetime.utcnow().isoformat()

            # 4. Atomic conditional reservation
            cur = conn.execute(
                """
                UPDATE booking_records
                SET total_refunded_minor = total_refunded_minor + ?,
                    total_refunded = ?,
                    status = ?,
                    updated_at = ?
                WHERE booking_id = ? AND agency_id = ?
                  AND (total_refunded_minor + ?) <= refundable_entitlement_minor
                """,
                (
                    refund_amount_minor,
                    new_total_refunded,
                    new_status,
                    now_iso,
                    booking_id,
                    agency_id,
                    refund_amount_minor,
                ),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError("Concurrent refund exceeded refundable entitlement")

            # 5. Persist Refund Record
            refund_count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM refund_records WHERE booking_id = ?", (booking_id,)
            ).fetchone()
            count = refund_count_row["cnt"] if refund_count_row else 0
            refund_id = f"RFD-{booking_id}-{count + 1:02d}"
            tx_ref = f"TXN-RFD-{booking_id}-{int(datetime.utcnow().timestamp() * 1000)}"
            refund_amount = from_minor_units(refund_amount_minor, currency)
            entitlement = from_minor_units(entitlement_minor, currency)
            penalty_amount = from_minor_units(b_row["cancellation_penalty_minor"], currency)

            conn.execute(
                """
                INSERT OR REPLACE INTO refund_records (
                    refund_id, booking_id, payment_id, agency_id, amount, amount_minor,
                    authorized_amount, authorized_amount_minor, processed_amount, processed_amount_minor,
                    currency, penalty_amount, penalty_amount_minor, refundable_entitlement, refundable_entitlement_minor,
                    status, reason, transaction_reference, idempotency_key, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refund_id,
                    booking_id,
                    payment_id or b_row["payment_id"] or "NONE",
                    agency_id,
                    refund_amount,
                    refund_amount_minor,
                    refund_amount,
                    refund_amount_minor,
                    refund_amount,
                    refund_amount_minor,
                    currency,
                    penalty_amount,
                    b_row["cancellation_penalty_minor"],
                    entitlement,
                    entitlement_minor,
                    "COMPLETED",
                    reason,
                    tx_ref,
                    idempotency_key,
                    payload_hash,
                    now_iso,
                ),
            )

            # 6. Post Financial Ledger Entry with exact minor units and journal metadata
            batch_id = f"BATCH-RFD-{booking_id}-{int(datetime.utcnow().timestamp() * 1000)}"

            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_ref,
                    booking_id,
                    agency_id,
                    LedgerEntryType.REFUND.value,
                    LedgerDirection.CREDIT.value,
                    refund_amount,
                    refund_amount_minor,
                    currency,
                    from_minor_units(entitlement_minor - new_total_refunded_minor, currency),
                    entitlement_minor - new_total_refunded_minor,
                    batch_id,
                    "2100_AGENCY_REFUNDABLE_PAYABLE",
                    f"Refund credited for {booking_id}",
                    now_iso,
                ),
            )

            # CW-FIN-M2-209: If payment_method == 'WALLET', also credit the agency wallet
            is_wallet = (b_row["payment_method"] == "WALLET") if "payment_method" in b_row.keys() else False
            if is_wallet:
                conn.execute(
                    """
                    UPDATE agency_wallets
                    SET total_refunded_minor = total_refunded_minor + ?,
                        total_refunded = (total_refunded_minor + ?) / 100.0,
                        updated_at = ?
                    WHERE agency_id = ?
                    """,
                    (refund_amount_minor, refund_amount_minor, now_iso, agency_id),
                )
                wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
                balance_after_minor = (
                    wrow["funded_balance_minor"] + wrow["total_refunded_minor"] - wrow["active_holds_minor"] - wrow["total_captured_minor"]
                )
                holds_after_minor = wrow["active_holds_minor"]
                wtx_id = f"wtx_rfd_{booking_id}_{int(datetime.utcnow().timestamp() * 1000)}"

                conn.execute(
                    """
                    INSERT INTO wallet_transactions (
                        transaction_id, wallet_id, agency_id, booking_id, hold_id,
                        transaction_type, amount, amount_minor, currency,
                        balance_after_minor, holds_after_minor, reference, description, created_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        wtx_id, wrow["wallet_id"], agency_id, booking_id,
                        WalletTransactionType.REFUND.value,
                        refund_amount, refund_amount_minor, currency,
                        balance_after_minor, holds_after_minor, tx_ref, f"Wallet credit for refund {booking_id}", now_iso,
                    ),
                )

            conn.execute("COMMIT")

            # Construct return objects
            refund_record = RefundRecord(
                refund_id=refund_id,
                booking_id=booking_id,
                payment_id=payment_id or b_row["payment_id"] or "NONE",
                agency_id=agency_id,
                amount=refund_amount,
                amount_minor=refund_amount_minor,
                authorized_amount=refund_amount,
                authorized_amount_minor=refund_amount_minor,
                processed_amount=refund_amount,
                processed_amount_minor=refund_amount_minor,
                currency=currency,
                penalty_amount=penalty_amount,
                penalty_amount_minor=b_row["cancellation_penalty_minor"],
                refundable_entitlement=entitlement,
                refundable_entitlement_minor=entitlement_minor,
                status="COMPLETED",
                reason=reason,
                transaction_reference=tx_ref,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                created_at=datetime.fromisoformat(now_iso),
            )

            updated_booking = AggregatorBookingRecord(
                booking_id=booking_id,
                intent_id=b_row["intent_id"],
                agency_id=agency_id,
                status=TransactionStatus(new_status),
                amount=b_row["amount"],
                amount_minor=b_row["amount_minor"],
                currency=currency,
                cancellation_penalty=penalty_amount,
                cancellation_penalty_minor=b_row["cancellation_penalty_minor"],
                refundable_entitlement=entitlement,
                refundable_entitlement_minor=entitlement_minor,
                total_refunded=new_total_refunded,
                total_refunded_minor=new_total_refunded_minor,
                primary_supplier_reservation_id=b_row["primary_supplier_reservation_id"],
                payment_id=b_row["payment_id"],
                voucher_id=b_row["voucher_id"],
                retry_count=b_row["retry_count"],
                correlation_id=b_row["correlation_id"],
                error_message=b_row["error_message"],
                created_at=datetime.fromisoformat(b_row["created_at"]),
                updated_at=datetime.fromisoformat(now_iso),
            )

            entries = self.get_ledger_entries_for_booking(booking_id)
            return refund_record, updated_booking, entries

        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ---------------------------------------------------------
    # Financial Ledger Entries (Balanced Double-Entry Support)
    # ---------------------------------------------------------
    def record_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        amount_minor = entry.amount_minor if entry.amount_minor else to_minor_units(entry.amount, entry.currency)
        with self._connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type,
                    direction, amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.transaction_reference,
                    entry.booking_id,
                    entry.agency_id,
                    entry.entry_type.value if hasattr(entry.entry_type, "value") else str(entry.entry_type),
                    entry.direction.value if hasattr(entry.direction, "value") else str(entry.direction),
                    entry.amount,
                    amount_minor,
                    entry.currency,
                    entry.balance_after,
                    entry.balance_after_minor,
                    entry.journal_batch_id,
                    entry.account_code,
                    entry.description,
                    entry.created_at.isoformat() if isinstance(entry.created_at, datetime) else str(entry.created_at),
                ),
            )
            entry.entry_id = cur.lastrowid
            entry.amount_minor = amount_minor
            return entry

    def record_double_entry_batch(
        self,
        batch_id: str,
        booking_id: str,
        agency_id: str,
        entry_type: LedgerEntryType,
        debit_account: str,
        credit_account: str,
        amount_minor: int,
        currency: str,
        description: str,
    ) -> List[LedgerEntry]:
        """
        Records a balanced double-entry accounting journal batch where Debits == Credits.
        """
        amount = from_minor_units(amount_minor, currency)
        now_iso = datetime.utcnow().isoformat()
        leg1_ref = f"{batch_id}-DEBIT"
        leg2_ref = f"{batch_id}-CREDIT"

        with self._connection() as conn:
            cur1 = conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    leg1_ref,
                    booking_id,
                    agency_id,
                    entry_type.value,
                    LedgerDirection.DEBIT.value,
                    amount,
                    amount_minor,
                    currency,
                    batch_id,
                    debit_account,
                    description,
                    now_iso,
                ),
            )
            cur2 = conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    leg2_ref,
                    booking_id,
                    agency_id,
                    entry_type.value,
                    LedgerDirection.CREDIT.value,
                    amount,
                    amount_minor,
                    currency,
                    batch_id,
                    credit_account,
                    description,
                    now_iso,
                ),
            )

        return [
            LedgerEntry(
                entry_id=cur1.lastrowid,
                transaction_reference=leg1_ref,
                booking_id=booking_id,
                agency_id=agency_id,
                entry_type=entry_type,
                direction=LedgerDirection.DEBIT,
                amount=amount,
                amount_minor=amount_minor,
                currency=currency,
                journal_batch_id=batch_id,
                account_code=debit_account,
                description=description,
                created_at=datetime.fromisoformat(now_iso),
            ),
            LedgerEntry(
                entry_id=cur2.lastrowid,
                transaction_reference=leg2_ref,
                booking_id=booking_id,
                agency_id=agency_id,
                entry_type=entry_type,
                direction=LedgerDirection.CREDIT,
                amount=amount,
                amount_minor=amount_minor,
                currency=currency,
                journal_batch_id=batch_id,
                account_code=credit_account,
                description=description,
                created_at=datetime.fromisoformat(now_iso),
            ),
        ]

    def get_ledger_entries_for_booking(self, booking_id: str) -> List[LedgerEntry]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE booking_id = ? ORDER BY entry_id ASC",
                (booking_id,),
            ).fetchall()
            entries = []
            for r in rows:
                keys = r.keys()
                amount = r["amount"]
                currency = r["currency"]
                amount_minor = r["amount_minor"] if "amount_minor" in keys and r["amount_minor"] else to_minor_units(amount, currency)
                balance_after_minor = r["balance_after_minor"] if "balance_after_minor" in keys else None
                journal_batch_id = r["journal_batch_id"] if "journal_batch_id" in keys else None
                account_code = r["account_code"] if "account_code" in keys else None
                provider_val = r["provider"] if "provider" in keys else None
                event_id_val = r["event_id"] if "event_id" in keys else None
                entries.append(
                    LedgerEntry(
                        entry_id=r["entry_id"],
                        transaction_reference=r["transaction_reference"],
                        booking_id=r["booking_id"],
                        agency_id=r["agency_id"],
                        entry_type=LedgerEntryType(r["entry_type"]),
                        direction=LedgerDirection(r["direction"]),
                        amount=amount,
                        amount_minor=amount_minor,
                        currency=currency,
                        balance_after=r["balance_after"],
                        balance_after_minor=balance_after_minor,
                        journal_batch_id=journal_batch_id,
                        account_code=account_code,
                        description=r["description"],
                        provider=provider_val,
                        event_id=event_id_val,
                        created_at=datetime.fromisoformat(r["created_at"]),
                    )
                )
            return entries

    # ---------------------------------------------------------
    # Webhook Events
    # ---------------------------------------------------------
    def record_webhook_event(self, event: WebhookEventRecord) -> WebhookEventRecord:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO webhook_events (
                    event_id, provider, event_type, booking_id, payload_hash,
                    signature, processed_count, status, amount_minor, currency, payment_id, refund_id,
                    first_received_at, last_received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.provider,
                    event.event_type,
                    event.booking_id,
                    event.payload_hash,
                    event.signature,
                    event.processed_count,
                    event.status,
                    event.amount_minor,
                    event.currency,
                    event.payment_id,
                    event.refund_id,
                    event.first_received_at.isoformat() if isinstance(event.first_received_at, datetime) else str(event.first_received_at),
                    event.last_received_at.isoformat() if isinstance(event.last_received_at, datetime) else str(event.last_received_at),
                ),
            )
            return event

    def get_webhook_event(self, event_id: str, provider: Optional[str] = None) -> Optional[WebhookEventRecord]:
        with self._connection() as conn:
            if provider:
                row = conn.execute(
                    "SELECT * FROM webhook_events WHERE provider = ? AND event_id = ?",
                    (provider, event_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM webhook_events WHERE event_id = ?", (event_id,)).fetchone()
            if not row:
                return None
            return self._row_to_webhook_event(row)

    def get_webhook_events_for_booking(self, booking_id: str) -> List[WebhookEventRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM webhook_events WHERE booking_id = ? ORDER BY first_received_at ASC",
                (booking_id,),
            ).fetchall()
            return [self._row_to_webhook_event(r) for r in rows]

    def get_all_webhook_events(self) -> List[WebhookEventRecord]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM webhook_events ORDER BY first_received_at ASC").fetchall()
            return [self._row_to_webhook_event(r) for r in rows]

    def _row_to_webhook_event(self, row: sqlite3.Row) -> WebhookEventRecord:
        keys = row.keys()
        return WebhookEventRecord(
            event_id=row["event_id"],
            provider=row["provider"],
            event_type=row["event_type"],
            booking_id=row["booking_id"],
            payload_hash=row["payload_hash"],
            signature=row["signature"],
            processed_count=row["processed_count"],
            status=row["status"],
            amount_minor=row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] is not None else 0,
            currency=row["currency"] if "currency" in keys and row["currency"] is not None else "INR",
            payment_id=row["payment_id"] if "payment_id" in keys else None,
            refund_id=row["refund_id"] if "refund_id" in keys else None,
            first_received_at=datetime.fromisoformat(row["first_received_at"]),
            last_received_at=datetime.fromisoformat(row["last_received_at"]),
        )

    def get_ledger_entries_for_event(self, event_id: str, provider: Optional[str] = None) -> List[LedgerEntry]:
        """Returns all ledger entries generated by a specific webhook event (CW-REV-M2-004 exact equality)."""
        with self._connection() as conn:
            if provider:
                rows = conn.execute(
                    "SELECT * FROM ledger_entries WHERE provider = ? AND event_id = ? ORDER BY entry_id ASC",
                    (provider, event_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ledger_entries WHERE event_id = ? ORDER BY entry_id ASC",
                    (event_id,),
                ).fetchall()

            # Backward compatibility fallback for pre-migration entries without event_id column populated
            if not rows and not provider:
                ref_pattern = f"%{event_id}%"
                rows = conn.execute(
                    "SELECT * FROM ledger_entries WHERE event_id IS NULL AND (transaction_reference LIKE ? OR journal_batch_id LIKE ?) ORDER BY entry_id ASC",
                    (ref_pattern, ref_pattern),
                ).fetchall()

            entries = []
            for r in rows:
                keys = r.keys()
                amount = r["amount"]
                currency = r["currency"]
                amount_minor = r["amount_minor"] if "amount_minor" in keys and r["amount_minor"] else to_minor_units(amount, currency)
                provider_val = r["provider"] if "provider" in keys else None
                event_id_val = r["event_id"] if "event_id" in keys else None
                entries.append(
                    LedgerEntry(
                        entry_id=r["entry_id"],
                        transaction_reference=r["transaction_reference"],
                        booking_id=r["booking_id"],
                        agency_id=r["agency_id"],
                        entry_type=LedgerEntryType(r["entry_type"]),
                        direction=LedgerDirection(r["direction"]),
                        amount=amount,
                        amount_minor=amount_minor,
                        currency=currency,
                        balance_after=r["balance_after"],
                        balance_after_minor=r["balance_after_minor"] if "balance_after_minor" in keys else None,
                        journal_batch_id=r["journal_batch_id"] if "journal_batch_id" in keys else None,
                        account_code=r["account_code"] if "account_code" in keys else None,
                        description=r["description"],
                        provider=provider_val,
                        event_id=event_id_val,
                        created_at=datetime.fromisoformat(r["created_at"]),
                    )
                )
            return entries

    def process_webhook_event_vulnerable(
        self, payload: WebhookPayload, payload_hash: str
    ) -> WebhookProcessingResponse:
        """
        VULNERABLE MODE (Phase 2):
        Naively processes incoming webhook deliveries without idempotency deduplication.
        Each delivery:
        - Mutates the financial ledger (adds a new credit).
        - Creates a new duplicate refund record.
        - Increases total_refunded_minor (can exceed refundable_entitlement_minor).
        - Violates Invariant 5.5 (Refund Boundedness) and Invariant 5.3 (Webhook Idempotency).
        """
        conn = self._create_connection(timeout=30.0)
        try:
            now_iso = datetime.utcnow().isoformat()
            now_ms = int(datetime.utcnow().timestamp() * 1000)

            # 1. Fetch booking record
            b_row = conn.execute(
                "SELECT * FROM booking_records WHERE booking_id = ?", (payload.booking_id,)
            ).fetchone()
            if not b_row:
                raise ValueError(f"Booking '{payload.booking_id}' not found")

            # 2. Check existing event delivery count
            existing = conn.execute(
                "SELECT * FROM webhook_events WHERE provider = ? AND event_id = ?",
                (payload.provider_id, payload.event_id),
            ).fetchone()
            new_count = (existing["processed_count"] + 1) if existing else 1

            conn.execute(
                """
                INSERT INTO webhook_events (
                    event_id, provider, event_type, booking_id, payload_hash,
                    signature, processed_count, status, amount_minor, currency, payment_id, refund_id,
                    first_received_at, last_received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, event_id) DO UPDATE SET
                    processed_count = processed_count + 1,
                    last_received_at = excluded.last_received_at
                """,
                (
                    payload.event_id,
                    payload.provider_id,
                    payload.event_type,
                    payload.booking_id,
                    payload_hash,
                    payload.signature,
                    1,
                    "PROCESSED_VULNERABLE",
                    payload.amount_minor,
                    payload.currency,
                    payload.payment_id,
                    payload.refund_id,
                    now_iso,
                    now_iso,
                ),
            )

            # 3. Naively mutate financial state on EVERY delivery:
            refund_id = f"{payload.refund_id}-DUP-{new_count}" if new_count > 1 else payload.refund_id
            tx_ref = f"TXN-WH-{payload.event_id}-{new_count}-{now_ms}"
            refund_amount = from_minor_units(payload.amount_minor, payload.currency)
            entitlement = from_minor_units(b_row["refundable_entitlement_minor"], payload.currency)

            conn.execute(
                """
                INSERT OR REPLACE INTO refund_records (
                    refund_id, booking_id, payment_id, agency_id, amount, amount_minor, currency,
                    penalty_amount, penalty_amount_minor, refundable_entitlement, refundable_entitlement_minor,
                    status, reason, transaction_reference, idempotency_key, payload_hash,
                    provider, event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refund_id,
                    payload.booking_id,
                    payload.payment_id,
                    b_row["agency_id"],
                    refund_amount,
                    payload.amount_minor,
                    payload.currency,
                    from_minor_units(b_row["cancellation_penalty_minor"], payload.currency),
                    b_row["cancellation_penalty_minor"],
                    entitlement,
                    b_row["refundable_entitlement_minor"],
                    "COMPLETED",
                    f"Vulnerable webhook delivery #{new_count}",
                    tx_ref,
                    f"WH-{payload.event_id}-{new_count}",
                    payload_hash,
                    payload.provider_id,
                    payload.event_id,
                    now_iso,
                ),
            )

            # 4. Naively post duplicate ledger entry
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, journal_batch_id, account_code, description,
                    provider, event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_ref,
                    payload.booking_id,
                    b_row["agency_id"],
                    LedgerEntryType.REFUND.value,
                    LedgerDirection.CREDIT.value,
                    refund_amount,
                    payload.amount_minor,
                    payload.currency,
                    f"BATCH-WH-VULN-{payload.event_id}-{new_count}",
                    "2100_AGENCY_REFUNDABLE_PAYABLE",
                    f"Vulnerable duplicate webhook refund credit #{new_count}",
                    payload.provider_id,
                    payload.event_id,
                    now_iso,
                ),
            )

            # 5. Naively increment total_refunded_minor (permits exceeding entitlement)
            new_total_refunded_minor = b_row["total_refunded_minor"] + payload.amount_minor
            new_total_refunded = from_minor_units(new_total_refunded_minor, payload.currency)
            new_status = (
                TransactionStatus.REFUNDED.value
                if new_total_refunded_minor >= b_row["refundable_entitlement_minor"]
                else TransactionStatus.PARTIALLY_REFUNDED.value
            )

            conn.execute(
                """
                UPDATE booking_records
                SET total_refunded_minor = ?,
                    total_refunded = ?,
                    status = ?,
                    updated_at = ?
                WHERE booking_id = ?
                """,
                (new_total_refunded_minor, new_total_refunded, new_status, now_iso, payload.booking_id),
            )

            conn.commit()

            return WebhookProcessingResponse(
                status="PROCESSED_VULNERABLE",
                event_id=payload.event_id,
                duplicate=(new_count > 1),
                processed_count=new_count,
                message=f"Vulnerable processing: financial credit duplicated (delivery #{new_count})",
                booking_status=TransactionStatus(new_status),
                total_refunded_minor=new_total_refunded_minor,
            )
        finally:
            conn.close()

    def process_webhook_event_fixed(
        self, payload: WebhookPayload, payload_hash: str
    ) -> WebhookProcessingResponse:
        """
        FIXED MODE (Phase 2 & 3, CW-REV-M2-002, CW-REV-M2-003, CW-REV-M2-004):
        Executes atomic registration and balance mutation inside an exclusive SQLite transaction (BEGIN IMMEDIATE).
        Guarantees:
        1. Provider-scoped event idempotency (provider, event_id).
        2. Idempotent replay: identical re-deliveries return status="IDEMPOTENT_REPLAY" with ZERO financial side-effects.
        3. Payload conflict detection: reusing an event_id with mismatched payload raises WebhookPayloadConflictError (HTTP 409).
        4. Entity validation:
           - Supported refund event types only (refund.processed, refund.succeeded).
           - Booking existence & eligible lifecycle state (CANCELLED, PARTIALLY_REFUNDED, SUPPLIER_UNKNOWN, etc.).
           - Payment existence, booking relationship & provider matching.
           - Currency consistency across booking, payment & webhook.
           - Reconciliation against authorized refund instruction (amount <= authorized).
        5. Entitlement bounding: cumulative refunds cannot exceed refundable entitlement.
        6. Atomic balance reservation via conditional UPDATE.
        7. Exactly 1 ledger entry, 1 refund record, and 1 webhook record with explicit provider & event_id.
        """
        conn = self._create_connection(timeout=30.0)
        conn.isolation_level = None  # explicit transaction control
        try:
            conn.execute("BEGIN IMMEDIATE")
            now_iso = datetime.utcnow().isoformat()

            # 1. Check if (provider, event_id) already exists
            existing = conn.execute(
                "SELECT * FROM webhook_events WHERE provider = ? AND event_id = ?",
                (payload.provider_id, payload.event_id),
            ).fetchone()

            if existing:
                # Payload conflict check
                if existing["payload_hash"] != payload_hash:
                    conn.execute("ROLLBACK")
                    raise WebhookPayloadConflictError(
                        f"Webhook event '{payload.event_id}' conflict for provider '{payload.provider_id}': payload hash mismatch"
                    )

                # Idempotent replay: increment delivery count, update timestamp, DO NOT duplicate financial effects
                new_count = existing["processed_count"] + 1
                conn.execute(
                    "UPDATE webhook_events SET processed_count = ?, last_received_at = ? WHERE provider = ? AND event_id = ?",
                    (new_count, now_iso, payload.provider_id, payload.event_id),
                )
                b_row = conn.execute(
                    "SELECT * FROM booking_records WHERE booking_id = ?", (payload.booking_id,)
                ).fetchone()
                conn.execute("COMMIT")

                return WebhookProcessingResponse(
                    status="IDEMPOTENT_REPLAY",
                    event_id=payload.event_id,
                    duplicate=True,
                    processed_count=new_count,
                    message="Idempotent replay: webhook already processed without duplicating financial effect",
                    booking_status=TransactionStatus(b_row["status"]) if b_row else None,
                    total_refunded_minor=b_row["total_refunded_minor"] if b_row else None,
                )

            # 2. Entity & Lifecycle Validations (CW-REV-M2-002)
            if payload.amount_minor <= 0:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError("Webhook refund amount must be strictly greater than zero")

            # Event type validation
            if payload.event_type not in SUPPORTED_REFUND_EVENT_TYPES:
                conn.execute("ROLLBACK")
                raise UnsupportedWebhookEventError(
                    f"Unsupported webhook event type: '{payload.event_type}'. Supported: {sorted(SUPPORTED_REFUND_EVENT_TYPES)}"
                )

            # Booking existence
            b_row = conn.execute(
                "SELECT * FROM booking_records WHERE booking_id = ?", (payload.booking_id,)
            ).fetchone()
            if not b_row:
                conn.execute("ROLLBACK")
                raise ValueError(f"Booking '{payload.booking_id}' not found")

            # Booking lifecycle state validation
            allowed_refund_statuses = {
                TransactionStatus.CANCELLED.value,
                TransactionStatus.PARTIALLY_REFUNDED.value,
                TransactionStatus.SUPPLIER_UNKNOWN.value,
                TransactionStatus.RECONCILIATION_REQUIRED.value,
                TransactionStatus.FAILED.value,
            }
            if b_row["status"] not in allowed_refund_statuses:
                conn.execute("ROLLBACK")
                raise InvalidBookingStateForRefundError(
                    f"Booking '{payload.booking_id}' is in status '{b_row['status']}', which is not eligible for refund. Booking must be cancelled or failed."
                )

            # Payment validation
            p_row = conn.execute(
                "SELECT * FROM payment_transactions WHERE payment_id = ?", (payload.payment_id,)
            ).fetchone()
            if not p_row or p_row["booking_id"] != payload.booking_id:
                conn.execute("ROLLBACK")
                raise PaymentMismatchError(
                    f"Payment '{payload.payment_id}' does not exist or does not belong to booking '{payload.booking_id}'"
                )
            if p_row["provider"] != payload.provider_id:
                conn.execute("ROLLBACK")
                raise PaymentMismatchError(
                    f"Payment '{payload.payment_id}' provider '{p_row['provider']}' does not match webhook provider '{payload.provider_id}'"
                )
            if p_row["status"] != PaymentStatus.CAPTURED.value:
                conn.execute("ROLLBACK")
                raise PaymentMismatchError(
                    f"Payment '{payload.payment_id}' status is '{p_row['status']}', not CAPTURED. Cannot refund uncaptured payment."
                )

            # Currency validation
            if payload.currency != b_row["currency"] or payload.currency != p_row["currency"]:
                conn.execute("ROLLBACK")
                raise CurrencyMismatchError(
                    f"Webhook currency '{payload.currency}' mismatches booking '{b_row['currency']}' or payment '{p_row['currency']}'"
                )

            # Reconcile against authorized refund instruction
            rfd_row = conn.execute(
                "SELECT * FROM refund_records WHERE refund_id = ?", (payload.refund_id,)
            ).fetchone()
            if not rfd_row or rfd_row["booking_id"] != payload.booking_id:
                conn.execute("ROLLBACK")
                raise UnknownRefundError(
                    f"Authorized refund instruction '{payload.refund_id}' not found for booking '{payload.booking_id}'"
                )

            rfd_keys = rfd_row.keys()
            auth_minor = rfd_row["authorized_amount_minor"] if ("authorized_amount_minor" in rfd_keys and rfd_row["authorized_amount_minor"] is not None and rfd_row["authorized_amount_minor"] > 0) else rfd_row["amount_minor"]
            proc_minor = rfd_row["processed_amount_minor"] if ("processed_amount_minor" in rfd_keys and rfd_row["processed_amount_minor"] is not None) else (rfd_row["amount_minor"] if rfd_row["status"] == "COMPLETED" else 0)
            remaining_auth_minor = auth_minor - proc_minor

            if rfd_row["status"] == "COMPLETED" or remaining_auth_minor <= 0:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError(
                    f"Authorized refund instruction '{payload.refund_id}' has already been fully completed (authorized={auth_minor}, processed={proc_minor})"
                )

            if payload.amount_minor > remaining_auth_minor:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError(
                    f"Webhook refund amount {from_minor_units(payload.amount_minor, payload.currency)} {payload.currency} "
                    f"exceeds authorized amount (remaining authorized: {from_minor_units(remaining_auth_minor, payload.currency)} {payload.currency})"
                )

            # Entitlement bound check
            available_minor = b_row["refundable_entitlement_minor"] - b_row["total_refunded_minor"]
            if payload.amount_minor > available_minor:
                # Record rejected attempt for audit trace
                conn.execute(
                    """
                    INSERT INTO webhook_events (
                        event_id, provider, event_type, booking_id, payload_hash,
                        signature, processed_count, status, amount_minor, currency, payment_id, refund_id,
                        first_received_at, last_received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.event_id,
                        payload.provider_id,
                        payload.event_type,
                        payload.booking_id,
                        payload_hash,
                        payload.signature,
                        1,
                        "REJECTED_EXCESSIVE",
                        payload.amount_minor,
                        payload.currency,
                        payload.payment_id,
                        payload.refund_id,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.execute("COMMIT")
                raise ExcessiveRefundError(
                    f"Webhook refund amount {from_minor_units(payload.amount_minor, payload.currency)} {payload.currency} "
                    f"exceeds remaining refundable entitlement {from_minor_units(available_minor, payload.currency)} {payload.currency}"
                )

            # 3. Reserve balance atomically with conditional update on both refund and booking
            new_proc_minor = proc_minor + payload.amount_minor
            new_proc = from_minor_units(new_proc_minor, payload.currency)
            new_refund_status = (
                "COMPLETED" if new_proc_minor >= auth_minor else "PARTIALLY_REFUNDED"
            )
            tx_ref = f"TXN-WH-{payload.provider_id}-{payload.event_id}"
            refund_amount = from_minor_units(payload.amount_minor, payload.currency)

            cur_rfd = conn.execute(
                """
                UPDATE refund_records
                SET processed_amount_minor = processed_amount_minor + ?,
                    processed_amount = ?,
                    amount_minor = processed_amount_minor + ?,
                    amount = ?,
                    status = ?,
                    provider = ?,
                    event_id = ?,
                    payload_hash = ?,
                    transaction_reference = ?
                WHERE refund_id = ?
                  AND (processed_amount_minor + ?) <= authorized_amount_minor
                """,
                (
                    payload.amount_minor,
                    new_proc,
                    payload.amount_minor,
                    new_proc,
                    new_refund_status,
                    payload.provider_id,
                    payload.event_id,
                    payload_hash,
                    tx_ref,
                    payload.refund_id,
                    payload.amount_minor,
                ),
            )
            if cur_rfd.rowcount == 0:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError("Concurrent webhook delivery exceeded remaining authorized refund amount")

            new_total_refunded_minor = b_row["total_refunded_minor"] + payload.amount_minor
            new_total_refunded = from_minor_units(new_total_refunded_minor, payload.currency)
            new_status = (
                TransactionStatus.REFUNDED.value
                if new_total_refunded_minor >= b_row["refundable_entitlement_minor"]
                else TransactionStatus.PARTIALLY_REFUNDED.value
            )

            cur_b = conn.execute(
                """
                UPDATE booking_records
                SET total_refunded_minor = total_refunded_minor + ?,
                    total_refunded = ?,
                    status = ?,
                    updated_at = ?
                WHERE booking_id = ?
                  AND (total_refunded_minor + ?) <= refundable_entitlement_minor
                """,
                (
                    payload.amount_minor,
                    new_total_refunded,
                    new_status,
                    now_iso,
                    payload.booking_id,
                    payload.amount_minor,
                ),
            )
            if cur_b.rowcount == 0:
                conn.execute("ROLLBACK")
                raise ExcessiveRefundError("Concurrent refund exceeded refundable entitlement")

            # 4. Insert into webhook_events (composite PK provider, event_id)
            conn.execute(
                """
                INSERT INTO webhook_events (
                    event_id, provider, event_type, booking_id, payload_hash,
                    signature, processed_count, status, amount_minor, currency, payment_id, refund_id,
                    first_received_at, last_received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.event_id,
                    payload.provider_id,
                    payload.event_type,
                    payload.booking_id,
                    payload_hash,
                    payload.signature,
                    1,
                    "PROCESSED",
                    payload.amount_minor,
                    payload.currency,
                    payload.payment_id,
                    payload.refund_id,
                    now_iso,
                    now_iso,
                ),
            )

            # 5. Ledger entry already handled in step 6 (refund record updated above)

            # 6. Post exactly 1 ledger entry with provider and event_id (CW-REV-M2-004)
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, provider, event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_ref,
                    payload.booking_id,
                    b_row["agency_id"],
                    LedgerEntryType.REFUND.value,
                    LedgerDirection.CREDIT.value,
                    refund_amount,
                    payload.amount_minor,
                    payload.currency,
                    from_minor_units(b_row["refundable_entitlement_minor"] - new_total_refunded_minor, payload.currency),
                    b_row["refundable_entitlement_minor"] - new_total_refunded_minor,
                    f"BATCH-WH-{payload.provider_id}-{payload.event_id}",
                    "2100_AGENCY_REFUNDABLE_PAYABLE",
                    f"Refund credited via webhook {payload.event_id}",
                    payload.provider_id,
                    payload.event_id,
                    now_iso,
                ),
            )

            conn.execute("COMMIT")

            return WebhookProcessingResponse(
                status="PROCESSED",
                event_id=payload.event_id,
                duplicate=False,
                processed_count=1,
                message="Webhook processed successfully",
                booking_status=TransactionStatus(new_status),
                total_refunded_minor=new_total_refunded_minor,
            )
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ---------------------------------------------------------
    # Supplier Operations & Audit Events
    # ---------------------------------------------------------
    def record_supplier_operation(
        self,
        operation_id: str,
        booking_id: str,
        intent_id: str,
        attempt_number: int,
        idempotency_key: Optional[str],
        endpoint: str,
        status_code: Optional[int],
        supplier_reservation_id: Optional[str],
        error_type: Optional[str],
    ):
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO supplier_operations (
                    operation_id, booking_id, intent_id, attempt_number,
                    idempotency_key, endpoint, status_code, supplier_reservation_id, error_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    booking_id,
                    intent_id,
                    attempt_number,
                    idempotency_key,
                    endpoint,
                    status_code,
                    supplier_reservation_id,
                    error_type,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_supplier_operations_for_booking(self, booking_id: str) -> List[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM supplier_operations WHERE booking_id = ? ORDER BY attempt_number ASC",
                (booking_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_events_for_booking(self, booking_id: str) -> List[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM transaction_events WHERE booking_id = ? ORDER BY event_id ASC",
                (booking_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------------------------------------------------
    # Milestone 2.2: Agency Wallet Operations
    # ---------------------------------------------------------
    def _row_to_wallet_hold(self, row: sqlite3.Row) -> WalletHoldRecord:
        keys = row.keys()
        amt_minor = row["amount_minor"]
        curr = row["currency"]
        return WalletHoldRecord(
            hold_id=row["hold_id"],
            wallet_id=row["wallet_id"],
            agency_id=row["agency_id"],
            booking_id=row["booking_id"],
            amount=row["amount"] if "amount" in keys else from_minor_units(amt_minor, curr),
            amount_minor=amt_minor,
            currency=curr,
            status=WalletHoldStatus(row["status"]),
            idempotency_key=row["idempotency_key"] if "idempotency_key" in keys else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_wallet_transaction(self, row: sqlite3.Row) -> WalletTransactionRecord:
        keys = row.keys()
        amt_minor = row["amount_minor"]
        curr = row["currency"]
        return WalletTransactionRecord(
            transaction_id=row["transaction_id"],
            wallet_id=row["wallet_id"],
            agency_id=row["agency_id"],
            booking_id=row["booking_id"] if "booking_id" in keys else None,
            hold_id=row["hold_id"] if "hold_id" in keys else None,
            transaction_type=WalletTransactionType(row["transaction_type"]),
            amount=row["amount"] if "amount" in keys else from_minor_units(amt_minor, curr),
            amount_minor=amt_minor,
            currency=curr,
            balance_after_minor=row["balance_after_minor"],
            holds_after_minor=row["holds_after_minor"],
            reference=row["reference"],
            description=row["description"] if "description" in keys else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_wallet(self, agency_id: str) -> Optional[AgencyWalletRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            if not row:
                return None
            funded_minor = row["funded_balance_minor"]
            active_holds_minor = row["active_holds_minor"]
            total_captured_minor = row["total_captured_minor"]
            total_refunded_minor = row["total_refunded_minor"] if "total_refunded_minor" in row.keys() else 0
            available_minor = funded_minor + total_refunded_minor - active_holds_minor - total_captured_minor
            currency = row["currency"]
            return AgencyWalletRecord(
                wallet_id=row["wallet_id"],
                agency_id=row["agency_id"],
                currency=currency,
                funded_balance=from_minor_units(funded_minor, currency),
                funded_balance_minor=funded_minor,
                active_holds=from_minor_units(active_holds_minor, currency),
                active_holds_minor=active_holds_minor,
                total_captured=from_minor_units(total_captured_minor, currency),
                total_captured_minor=total_captured_minor,
                total_refunded=from_minor_units(total_refunded_minor, currency),
                total_refunded_minor=total_refunded_minor,
                available_balance=from_minor_units(available_minor, currency),
                available_balance_minor=available_minor,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def deposit_wallet_funds(
        self, agency_id: str, amount_minor: int, currency: str = "INR", reference: Optional[str] = None
    ) -> AgencyWalletRecord:
        if amount_minor <= 0:
            raise ValueError("Deposit amount must be strictly positive.")
        ref = reference or f"dep_{agency_id}_{int(datetime.utcnow().timestamp() * 1000)}"
        tx_id = f"wtx_{ref}"
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            wallet_id = f"wallet_{agency_id}"
            conn.execute(
                """
                INSERT OR IGNORE INTO agency_wallets (
                    wallet_id, agency_id, currency, funded_balance, funded_balance_minor,
                    active_holds, active_holds_minor, total_captured, total_captured_minor,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 0.0, 0, 0.0, 0, 0.0, 0, ?, ?)
                """,
                (wallet_id, agency_id, currency, now, now),
            )
            conn.execute(
                """
                UPDATE agency_wallets
                SET funded_balance_minor = funded_balance_minor + ?,
                    funded_balance = (funded_balance_minor + ?) / 100.0,
                    updated_at = ?
                WHERE agency_id = ?
                """,
                (amount_minor, amount_minor, now, agency_id),
            )
            row = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            balance_after_minor = row["funded_balance_minor"] - row["active_holds_minor"] - row["total_captured_minor"]
            holds_after_minor = row["active_holds_minor"]

            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, wallet_id, agency_id,
                    WalletTransactionType.DEPOSIT.value,
                    from_minor_units(amount_minor, currency), amount_minor, currency,
                    balance_after_minor, holds_after_minor, ref, "Agency wallet deposit", now,
                ),
            )

            # Balanced double-entry financial ledger records
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{tx_id}_DR", "WALLET_FUNDING", agency_id, LedgerEntryType.DEPOSIT.value,
                    LedgerDirection.DEBIT.value, from_minor_units(amount_minor, currency), amount_minor, currency,
                    from_minor_units(balance_after_minor, currency), balance_after_minor,
                    ref, "1000_BANK_CLEARING", "Agency deposit bank receipt", now,
                ),
            )
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{tx_id}_CR", "WALLET_FUNDING", agency_id, LedgerEntryType.DEPOSIT.value,
                    LedgerDirection.CREDIT.value, from_minor_units(amount_minor, currency), amount_minor, currency,
                    from_minor_units(balance_after_minor, currency), balance_after_minor,
                    ref, f"2100_AGENCY_WALLET_{agency_id}", "Agency wallet liability funded", now,
                ),
            )

        return self.get_wallet(agency_id)

    def create_wallet_hold_fixed(
        self, agency_id: str, booking_id: str, amount_minor: int, currency: str = "INR", idempotency_key: Optional[str] = None
    ) -> WalletHoldRecord:
        conn = self._create_connection(timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if idempotency_key:
                row = conn.execute(
                    "SELECT * FROM wallet_holds WHERE agency_id = ? AND idempotency_key = ? AND status != 'RELEASED'",
                    (agency_id, idempotency_key),
                ).fetchone()
                if row:
                    conn.commit()
                    return self._row_to_wallet_hold(row)

            now = datetime.utcnow().isoformat()
            wallet_id = f"wallet_{agency_id}"

            # Atomic conditional deduction: available = funded + total_refunded - active_holds - total_captured
            cursor = conn.execute(
                """
                UPDATE agency_wallets
                SET active_holds_minor = active_holds_minor + ?,
                    active_holds = (active_holds_minor + ?) / 100.0,
                    updated_at = ?
                WHERE agency_id = ?
                  AND (funded_balance_minor + COALESCE(total_refunded_minor, 0) - active_holds_minor - total_captured_minor) >= ?
                """,
                (amount_minor, amount_minor, now, agency_id, amount_minor),
            )
            if cursor.rowcount == 0:
                wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
                avail = (
                    wrow["funded_balance_minor"] + (wrow["total_refunded_minor"] if "total_refunded_minor" in wrow.keys() else 0)
                    - wrow["active_holds_minor"] - wrow["total_captured_minor"]
                ) if wrow else 0
                conn.rollback()
                raise InsufficientWalletFundsError(
                    f"Insufficient available wallet balance for agency '{agency_id}'. Required: {amount_minor}, Available: {avail}"
                )

            hold_id = f"whold_{booking_id}_{int(datetime.utcnow().timestamp() * 1000)}"
            conn.execute(
                """
                INSERT INTO wallet_holds (
                    hold_id, wallet_id, agency_id, booking_id, amount, amount_minor,
                    currency, status, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hold_id, wallet_id, agency_id, booking_id,
                    from_minor_units(amount_minor, currency), amount_minor,
                    currency, WalletHoldStatus.HELD.value, idempotency_key, now, now,
                ),
            )

            wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            total_ref = wrow["total_refunded_minor"] if "total_refunded_minor" in wrow.keys() else 0
            balance_after_minor = wrow["funded_balance_minor"] + total_ref - wrow["active_holds_minor"] - wrow["total_captured_minor"]
            holds_after_minor = wrow["active_holds_minor"]
            tx_id = f"wtx_{hold_id}"

            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, wallet_id, agency_id, booking_id, hold_id,
                    WalletTransactionType.HOLD.value,
                    from_minor_units(amount_minor, currency), amount_minor, currency,
                    balance_after_minor, holds_after_minor, hold_id, f"Hold for booking {booking_id}", now,
                ),
            )
            conn.commit()
            hrow = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            return self._row_to_wallet_hold(hrow)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def create_wallet_hold_vulnerable(
        self, agency_id: str, booking_id: str, amount_minor: int, currency: str = "INR", idempotency_key: Optional[str] = None
    ) -> WalletHoldRecord:
        """
        Vulnerable wallet hold reproduction:
        Performs a non-atomic read-then-write (TOCTOU) with an artificial delay.
        Allows concurrent requests to spend past the funded balance!
        """
        import time
        with self._connection() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT * FROM wallet_holds WHERE agency_id = ? AND idempotency_key = ? AND status != 'RELEASED'",
                    (agency_id, idempotency_key),
                ).fetchone()
                if row:
                    return self._row_to_wallet_hold(row)

            # 1. Non-atomic check
            wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            if not wrow:
                raise InsufficientWalletFundsError(f"Agency wallet '{agency_id}' not found.")
            available_minor = wrow["funded_balance_minor"] - wrow["active_holds_minor"] - wrow["total_captured_minor"]
            if available_minor < amount_minor:
                raise InsufficientWalletFundsError(
                    f"Insufficient available wallet balance for agency '{agency_id}'. Required: {amount_minor}, Available: {available_minor}"
                )

        # 2. TOCTOU race window - simulate concurrent processing interleaving
        time.sleep(0.05)

        with self._connection() as conn:
            now = datetime.utcnow().isoformat()
            wallet_id = f"wallet_{agency_id}"

            # 3. Blind update without checking available balance again!
            conn.execute(
                """
                UPDATE agency_wallets
                SET active_holds_minor = active_holds_minor + ?,
                    active_holds = (active_holds_minor + ?) / 100.0,
                    updated_at = ?
                WHERE agency_id = ?
                """,
                (amount_minor, amount_minor, now, agency_id),
            )

            hold_id = f"whold_{booking_id}_{int(datetime.utcnow().timestamp() * 1000)}"
            conn.execute(
                """
                INSERT INTO wallet_holds (
                    hold_id, wallet_id, agency_id, booking_id, amount, amount_minor,
                    currency, status, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hold_id, wallet_id, agency_id, booking_id,
                    from_minor_units(amount_minor, currency), amount_minor,
                    currency, WalletHoldStatus.HELD.value, idempotency_key, now, now,
                ),
            )

            wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            total_ref = wrow["total_refunded_minor"] if "total_refunded_minor" in wrow.keys() else 0
            balance_after_minor = wrow["funded_balance_minor"] + total_ref - wrow["active_holds_minor"] - wrow["total_captured_minor"]
            holds_after_minor = wrow["active_holds_minor"]
            tx_id = f"wtx_{hold_id}"

            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, wallet_id, agency_id, booking_id, hold_id,
                    WalletTransactionType.HOLD.value,
                    from_minor_units(amount_minor, currency), amount_minor, currency,
                    balance_after_minor, holds_after_minor, hold_id, f"Vulnerable hold for booking {booking_id}", now,
                ),
            )
            hrow = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            return self._row_to_wallet_hold(hrow)

    def capture_wallet_hold(self, hold_id: str) -> Tuple[WalletHoldRecord, AgencyWalletRecord]:
        conn = self._create_connection(timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            hrow = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            if not hrow:
                conn.rollback()
                raise WalletHoldNotFoundError(f"Wallet hold '{hold_id}' not found.")

            if hrow["status"] == WalletHoldStatus.CAPTURED.value:
                conn.commit()
                w = self.get_wallet(hrow["agency_id"])
                return self._row_to_wallet_hold(hrow), w

            if hrow["status"] != WalletHoldStatus.HELD.value:
                conn.rollback()
                raise InvalidStateTransitionError(f"Cannot capture wallet hold in status '{hrow['status']}'")

            now = datetime.utcnow().isoformat()
            agency_id = hrow["agency_id"]
            amount_minor = hrow["amount_minor"]
            currency = hrow["currency"]
            booking_id = hrow["booking_id"]
            wallet_id = hrow["wallet_id"]

            conn.execute(
                "UPDATE wallet_holds SET status = ?, updated_at = ? WHERE hold_id = ?",
                (WalletHoldStatus.CAPTURED.value, now, hold_id),
            )

            conn.execute(
                """
                UPDATE agency_wallets
                SET active_holds_minor = active_holds_minor - ?,
                    active_holds = (active_holds_minor - ?) / 100.0,
                    total_captured_minor = total_captured_minor + ?,
                    total_captured = (total_captured_minor + ?) / 100.0,
                    updated_at = ?
                WHERE agency_id = ?
                """,
                (amount_minor, amount_minor, amount_minor, amount_minor, now, agency_id),
            )

            wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            total_ref = wrow["total_refunded_minor"] if "total_refunded_minor" in wrow.keys() else 0
            balance_after_minor = wrow["funded_balance_minor"] + total_ref - wrow["active_holds_minor"] - wrow["total_captured_minor"]
            holds_after_minor = wrow["active_holds_minor"]

            tx_id = f"wtx_cap_{hold_id}"
            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, wallet_id, agency_id, booking_id, hold_id,
                    WalletTransactionType.CAPTURE.value,
                    from_minor_units(amount_minor, currency), amount_minor, currency,
                    balance_after_minor, holds_after_minor, hold_id, f"Capture for booking {booking_id}", now,
                ),
            )

            batch_id = f"wcap_{hold_id}"
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{tx_id}_DR", booking_id, agency_id, LedgerEntryType.PAYMENT.value,
                    LedgerDirection.DEBIT.value, from_minor_units(amount_minor, currency), amount_minor, currency,
                    from_minor_units(balance_after_minor, currency), balance_after_minor,
                    batch_id, f"2100_AGENCY_WALLET_{agency_id}", f"Wallet capture for booking {booking_id}", now,
                ),
            )
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{tx_id}_CR", booking_id, agency_id, LedgerEntryType.PAYMENT.value,
                    LedgerDirection.CREDIT.value, from_minor_units(amount_minor, currency), amount_minor, currency,
                    from_minor_units(balance_after_minor, currency), balance_after_minor,
                    batch_id, "4000_REVENUE_HOTEL_BOOKINGS", f"Revenue recognized for booking {booking_id}", now,
                ),
            )

            conn.commit()
            h_updated = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            w = self.get_wallet(agency_id)
            return self._row_to_wallet_hold(h_updated), w
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def release_wallet_hold(self, hold_id: str, reason: str = "SAGA_COMPENSATION") -> Tuple[WalletHoldRecord, AgencyWalletRecord]:
        conn = self._create_connection(timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            hrow = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            if not hrow:
                conn.rollback()
                raise WalletHoldNotFoundError(f"Wallet hold '{hold_id}' not found.")

            if hrow["status"] == WalletHoldStatus.RELEASED.value:
                conn.commit()
                w = self.get_wallet(hrow["agency_id"])
                return self._row_to_wallet_hold(hrow), w

            if hrow["status"] != WalletHoldStatus.HELD.value:
                conn.rollback()
                raise InvalidStateTransitionError(f"Cannot release wallet hold in status '{hrow['status']}'")

            now = datetime.utcnow().isoformat()
            agency_id = hrow["agency_id"]
            amount_minor = hrow["amount_minor"]
            currency = hrow["currency"]
            booking_id = hrow["booking_id"]
            wallet_id = hrow["wallet_id"]

            conn.execute(
                "UPDATE wallet_holds SET status = ?, updated_at = ? WHERE hold_id = ?",
                (WalletHoldStatus.RELEASED.value, now, hold_id),
            )

            conn.execute(
                """
                UPDATE agency_wallets
                SET active_holds_minor = active_holds_minor - ?,
                    active_holds = (active_holds_minor - ?) / 100.0,
                    updated_at = ?
                WHERE agency_id = ?
                """,
                (amount_minor, amount_minor, now, agency_id),
            )

            wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            total_ref = wrow["total_refunded_minor"] if "total_refunded_minor" in wrow.keys() else 0
            balance_after_minor = wrow["funded_balance_minor"] + total_ref - wrow["active_holds_minor"] - wrow["total_captured_minor"]
            holds_after_minor = wrow["active_holds_minor"]

            tx_id = f"wtx_rel_{hold_id}"
            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, wallet_id, agency_id, booking_id, hold_id,
                    WalletTransactionType.RELEASE.value,
                    from_minor_units(amount_minor, currency), amount_minor, currency,
                    balance_after_minor, holds_after_minor, hold_id, f"Release hold: {reason}", now,
                ),
            )

            conn.commit()
            h_updated = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            w = self.get_wallet(agency_id)
            return self._row_to_wallet_hold(h_updated), w
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_wallet_hold(self, hold_id: str) -> Optional[WalletHoldRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM wallet_holds WHERE hold_id = ?", (hold_id,)).fetchone()
            return self._row_to_wallet_hold(row) if row else None

    def credit_wallet_refund(
        self, agency_id: str, booking_id: str, amount_minor: int, currency: str = "INR", reason: str = "Booking refund"
    ) -> AgencyWalletRecord:
        """
        Credits an agency wallet for a refunded booking (CW-FIN-M2-209).
        Maintains lifetime gross captures and increases total_refunded_minor.
        """
        if amount_minor <= 0:
            raise ValueError("Refund credit amount must be strictly positive.")
        now = datetime.utcnow().isoformat()
        ref = f"wref_{booking_id}_{int(datetime.utcnow().timestamp() * 1000)}"
        tx_id = f"wtx_{ref}"
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE agency_wallets
                SET total_refunded_minor = total_refunded_minor + ?,
                    total_refunded = (total_refunded_minor + ?) / 100.0,
                    updated_at = ?
                WHERE agency_id = ?
                """,
                (amount_minor, amount_minor, now, agency_id),
            )
            wrow = conn.execute("SELECT * FROM agency_wallets WHERE agency_id = ?", (agency_id,)).fetchone()
            balance_after_minor = (
                wrow["funded_balance_minor"] + wrow["total_refunded_minor"] - wrow["active_holds_minor"] - wrow["total_captured_minor"]
            )
            holds_after_minor = wrow["active_holds_minor"]

            conn.execute(
                """
                INSERT INTO wallet_transactions (
                    transaction_id, wallet_id, agency_id, booking_id, hold_id,
                    transaction_type, amount, amount_minor, currency,
                    balance_after_minor, holds_after_minor, reference, description, created_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, wrow["wallet_id"], agency_id, booking_id,
                    WalletTransactionType.REFUND.value,
                    from_minor_units(amount_minor, currency), amount_minor, currency,
                    balance_after_minor, holds_after_minor, ref, reason, now,
                ),
            )

            batch_id = f"BATCH-WREF-{booking_id}"
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{tx_id}_DR", booking_id, agency_id, LedgerEntryType.REFUND.value,
                    LedgerDirection.DEBIT.value, from_minor_units(amount_minor, currency), amount_minor, currency,
                    from_minor_units(balance_after_minor, currency), balance_after_minor,
                    batch_id, "4000_REVENUE_HOTEL_BOOKINGS", f"Revenue reduced for wallet refund {booking_id}", now,
                ),
            )
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    transaction_reference, booking_id, agency_id, entry_type, direction,
                    amount, amount_minor, currency, balance_after, balance_after_minor,
                    journal_batch_id, account_code, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{tx_id}_CR", booking_id, agency_id, LedgerEntryType.REFUND.value,
                    LedgerDirection.CREDIT.value, from_minor_units(amount_minor, currency), amount_minor, currency,
                    from_minor_units(balance_after_minor, currency), balance_after_minor,
                    batch_id, f"2100_AGENCY_WALLET_{agency_id}", f"Wallet credited for refund {booking_id}", now,
                ),
            )

        return self.get_wallet(agency_id)

    # ---------------------------------------------------------
    # Distributed Operation Claims (CW-ARC-M2-207)
    # ---------------------------------------------------------
    def _row_to_claim(self, row: sqlite3.Row) -> BookingOperationClaimRecord:
        keys = row.keys()
        return BookingOperationClaimRecord(
            agency_id=row["agency_id"],
            idempotency_key=row["idempotency_key"],
            payload_hash=row["payload_hash"],
            operation_id=row["operation_id"],
            booking_id=row["booking_id"],
            status=BookingClaimStatus(row["status"]),
            owner_token=row["owner_token"],
            fencing_token=row["fencing_token"] if "fencing_token" in keys and row["fencing_token"] is not None else 1,
            lease_expires_at=row["lease_expires_at"] if "lease_expires_at" in keys else None,
            heartbeat_at=row["heartbeat_at"] if "heartbeat_at" in keys else None,
            last_owner_token=row["last_owner_token"] if "last_owner_token" in keys else None,
            persisted_stage=row["persisted_stage"] if "persisted_stage" in keys else None,
            failure_reason=row["failure_reason"] if "failure_reason" in keys else None,
            correlation_id=row["correlation_id"] if "correlation_id" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def acquire_booking_claim(
        self,
        agency_id: str,
        idempotency_key: str,
        payload_hash: str,
        operation_id: str,
        booking_id: str,
        owner_token: str,
        correlation_id: Optional[str] = None,
        lease_duration_seconds: float = 10.0,
    ) -> Tuple[bool, BookingOperationClaimRecord]:
        """
        Attempts to atomically acquire or refresh a booking operation claim using BEGIN IMMEDIATE.
        Supports lease-based crash recovery:
        - If no claim exists: creates claim with fencing_token=1, lease_expires_at = now + lease_duration. Returns (True, claim).
        - If claim exists with matching owner_token: refreshes lease. Returns (True, claim).
        - If claim exists and is active (lease not expired): returns (False, existing_claim).
        - If claim exists and lease is expired BEFORE external dispatch (status in CLAIMED, WALLET_HOLD_CREATED):
          steals claim atomically, incrementing fencing_token to fence out the dead/slow worker. Returns (True, stolen_claim).
        - If claim exists and lease is expired AFTER external dispatch (status in SUPPLIER_REQUEST_PENDING, SUPPLIER_CONFIRMED, WALLET_CAPTURE_PENDING):
          does NOT steal blindly; returns (False, claim) with claim indicating in-flight dispatch so caller can perform external dispatch recovery.
        Raises AggregatorIdempotencyConflictError if payload_hash does not match.
        """
        conn = self._create_connection(timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            now_dt = datetime.utcnow()
            now = now_dt.isoformat()
            lease_expires = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()

            if row:
                claim = self._row_to_claim(row)
                if claim.payload_hash != payload_hash:
                    conn.rollback()
                    raise AggregatorIdempotencyConflictError(
                        f"Idempotency conflict: key '{idempotency_key}' was already used with a different booking payload"
                    )

                # If same owner re-acquiring, refresh lease
                if claim.owner_token == owner_token and claim.status not in (BookingClaimStatus.COMPLETED, BookingClaimStatus.FAILED):
                    conn.execute(
                        """
                        UPDATE booking_operation_claims
                        SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                        WHERE agency_id = ? AND idempotency_key = ? AND owner_token = ?
                        """,
                        (lease_expires, now, now, agency_id, idempotency_key, owner_token),
                    )
                    conn.commit()
                    r = conn.execute(
                        "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                        (agency_id, idempotency_key),
                    ).fetchone()
                    return True, self._row_to_claim(r)

                # If terminal, cannot acquire
                if claim.status in (BookingClaimStatus.COMPLETED, BookingClaimStatus.FAILED):
                    conn.commit()
                    return False, claim

                # Check lease expiration
                lease_expired = False
                if claim.lease_expires_at:
                    try:
                        lease_dt = datetime.fromisoformat(claim.lease_expires_at)
                        if now_dt >= lease_dt:
                            lease_expired = True
                    except Exception:
                        lease_expired = True
                else:
                    lease_expired = False

                if not lease_expired:
                    conn.commit()
                    return False, claim

                # Lease is expired! Can we safely reclaim before external dispatch?
                if claim.status in (BookingClaimStatus.CLAIMED, BookingClaimStatus.WALLET_HOLD_CREATED):
                    new_fencing = claim.fencing_token + 1
                    cur = conn.execute(
                        """
                        UPDATE booking_operation_claims
                        SET last_owner_token = owner_token,
                            owner_token = ?,
                            fencing_token = ?,
                            lease_expires_at = ?,
                            heartbeat_at = ?,
                            persisted_stage = 'RECLAIMED_PRE_DISPATCH',
                            updated_at = ?
                        WHERE agency_id = ? AND idempotency_key = ? AND fencing_token = ?
                        """,
                        (owner_token, new_fencing, lease_expires, now, now, agency_id, idempotency_key, claim.fencing_token),
                    )
                    if cur.rowcount > 0:
                        conn.commit()
                        r = conn.execute(
                            "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                            (agency_id, idempotency_key),
                        ).fetchone()
                        return True, self._row_to_claim(r)
                    else:
                        conn.rollback()
                        return False, claim

                # If external dispatch has already started, do not steal pre-dispatch
                conn.commit()
                return False, claim

            # New claim
            conn.execute(
                """
                INSERT INTO booking_operation_claims (
                    agency_id, idempotency_key, payload_hash, operation_id, booking_id,
                    status, owner_token, fencing_token, lease_expires_at, heartbeat_at,
                    last_owner_token, persisted_stage, failure_reason, correlation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, 'CLAIMED', NULL, ?, ?, ?)
                """,
                (
                    agency_id,
                    idempotency_key,
                    payload_hash,
                    operation_id,
                    booking_id,
                    BookingClaimStatus.CLAIMED.value,
                    owner_token,
                    lease_expires,
                    now,
                    correlation_id,
                    now,
                    now,
                ),
            )
            conn.commit()
            r = conn.execute(
                "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            return True, self._row_to_claim(r)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def update_claim_status(
        self,
        agency_id: str,
        idempotency_key: str,
        status: BookingClaimStatus,
        failure_reason: Optional[str] = None,
        expected_fencing_token: Optional[int] = None,
        expected_owner_token: Optional[str] = None,
        persisted_stage: Optional[str] = None,
        lease_duration_seconds: float = 10.0,
    ) -> Optional[BookingOperationClaimRecord]:
        now_dt = datetime.utcnow()
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # First check current state if fencing or owner token was specified
            if expected_fencing_token is not None or expected_owner_token is not None:
                cur_row = conn.execute(
                    "SELECT fencing_token, owner_token, status FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                    (agency_id, idempotency_key),
                ).fetchone()
                if cur_row:
                    if expected_fencing_token is not None and cur_row["fencing_token"] != expected_fencing_token:
                        conn.rollback()
                        raise AggregatorFencingError(
                            f"Stale worker fenced out: claim '{idempotency_key}' fencing token mismatch (expected {expected_fencing_token}, current {cur_row['fencing_token']})"
                        )
                    if expected_owner_token is not None and cur_row["owner_token"] != expected_owner_token:
                        conn.rollback()
                        raise AggregatorFencingError(
                            f"Stale worker fenced out: claim '{idempotency_key}' owner mismatch (expected {expected_owner_token}, current {cur_row['owner_token']})"
                        )

            conn.execute(
                """
                UPDATE booking_operation_claims
                SET status = ?,
                    failure_reason = COALESCE(?, failure_reason),
                    persisted_stage = COALESCE(?, ?),
                    heartbeat_at = ?,
                    lease_expires_at = CASE WHEN ? IN ('COMPLETED', 'FAILED') THEN lease_expires_at ELSE ? END,
                    updated_at = ?
                WHERE agency_id = ? AND idempotency_key = ?
                  AND (? IS NULL OR fencing_token = ?)
                  AND (? IS NULL OR owner_token = ?)
                """,
                (
                    status.value,
                    failure_reason,
                    persisted_stage,
                    status.value,
                    now,
                    status.value,
                    lease_expires,
                    now,
                    agency_id,
                    idempotency_key,
                    expected_fencing_token,
                    expected_fencing_token,
                    expected_owner_token,
                    expected_owner_token,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            return self._row_to_claim(row) if row else None

    def heartbeat_claim(
        self,
        agency_id: str,
        idempotency_key: str,
        owner_token: str,
        fencing_token: int,
        lease_duration_seconds: float = 10.0,
    ) -> bool:
        now_dt = datetime.utcnow()
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE booking_operation_claims
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE agency_id = ? AND idempotency_key = ? AND owner_token = ? AND fencing_token = ?
                  AND status NOT IN ('COMPLETED', 'FAILED')
                """,
                (now, lease_expires, now, agency_id, idempotency_key, owner_token, fencing_token),
            )
            if cur.rowcount == 0:
                raise AggregatorFencingError(
                    f"Heartbeat failed: worker '{owner_token}' has lost ownership or lease on claim '{idempotency_key}' (fencing {fencing_token})"
                )
            return True

    def reclaim_expired_claim_for_external_recovery(
        self,
        agency_id: str,
        idempotency_key: str,
        new_owner_token: str,
        lease_duration_seconds: float = 10.0,
    ) -> Tuple[bool, Optional[BookingOperationClaimRecord]]:
        now_dt = datetime.utcnow()
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            if not row:
                conn.rollback()
                return False, None
            claim = self._row_to_claim(row)
            if claim.status in (BookingClaimStatus.COMPLETED, BookingClaimStatus.FAILED):
                conn.rollback()
                return False, claim

            # Verify lease expired
            if claim.lease_expires_at:
                try:
                    lease_dt = datetime.fromisoformat(claim.lease_expires_at)
                    if now_dt < lease_dt:
                        conn.rollback()
                        return False, claim
                except Exception:
                    pass

            cur = conn.execute(
                """
                UPDATE booking_operation_claims
                SET last_owner_token = owner_token,
                    owner_token = ?,
                    fencing_token = fencing_token + 1,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    persisted_stage = 'EXTERNAL_RECOVERY_IN_PROGRESS',
                    updated_at = ?
                WHERE agency_id = ? AND idempotency_key = ? AND fencing_token = ?
                """,
                (new_owner_token, lease_expires, now, now, agency_id, idempotency_key, claim.fencing_token),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False, None
            conn.commit()
            r = conn.execute(
                "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            return True, self._row_to_claim(r)

    def get_booking_claim(self, agency_id: str, idempotency_key: str) -> Optional[BookingOperationClaimRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM booking_operation_claims WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            return self._row_to_claim(row) if row else None

    # ---------------------------------------------------------
    # Saga Recovery Records (CW-ARC-M2-208)
    # ---------------------------------------------------------
    def _row_to_saga_recovery(self, row: sqlite3.Row) -> SagaRecoveryRecord:
        keys = row.keys()
        return SagaRecoveryRecord(
            recovery_id=row["recovery_id"],
            booking_id=row["booking_id"],
            agency_id=row["agency_id"],
            target_supplier_reservation_id=row["target_supplier_reservation_id"] if "target_supplier_reservation_id" in keys else None,
            wallet_hold_id=row["wallet_hold_id"] if "wallet_hold_id" in keys else None,
            action=row["action"],
            status=row["status"],
            attempt_count=row["attempt_count"] if "attempt_count" in keys else 1,
            last_error=row["last_error"] if "last_error" in keys else None,
            lease_expires_at=row["lease_expires_at"] if "lease_expires_at" in keys else None,
            owner_token=row["owner_token"] if "owner_token" in keys else None,
            fencing_token=row["fencing_token"] if "fencing_token" in keys and row["fencing_token"] is not None else 0,
            next_attempt_at=row["next_attempt_at"] if "next_attempt_at" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_saga_recovery(
        self,
        booking_id: str,
        agency_id: str,
        action: str,
        status: str,
        target_supplier_reservation_id: Optional[str] = None,
        wallet_hold_id: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> SagaRecoveryRecord:
        rec_id = f"saga_{booking_id}_{int(datetime.utcnow().timestamp() * 1000)}"
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO saga_recovery_records (
                    recovery_id, booking_id, agency_id, target_supplier_reservation_id,
                    wallet_hold_id, action, status, attempt_count, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    rec_id,
                    booking_id,
                    agency_id,
                    target_supplier_reservation_id,
                    wallet_hold_id,
                    action,
                    status,
                    last_error,
                    now,
                    now,
                ),
            )
            r = conn.execute("SELECT * FROM saga_recovery_records WHERE recovery_id = ?", (rec_id,)).fetchone()
            return self._row_to_saga_recovery(r)

    def get_saga_recovery_records(self, booking_id: Optional[str] = None) -> List[SagaRecoveryRecord]:
        with self._connection() as conn:
            if booking_id:
                rows = conn.execute(
                    "SELECT * FROM saga_recovery_records WHERE booking_id = ? ORDER BY created_at DESC",
                    (booking_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM saga_recovery_records ORDER BY created_at DESC").fetchall()
            return [self._row_to_saga_recovery(r) for r in rows]

    def get_saga_recovery_record(self, recovery_id: str) -> Optional[SagaRecoveryRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM saga_recovery_records WHERE recovery_id = ?", (recovery_id,)).fetchone()
            if not row:
                return None
            return self._row_to_saga_recovery(row)

    def claim_pending_saga_recovery_task(
        self,
        owner_token: str,
        recovery_id: Optional[str] = None,
        lease_duration_seconds: float = 15.0,
        max_attempts: int = 5,
    ) -> Optional[SagaRecoveryRecord]:
        now_dt = datetime.utcnow()
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            query = """
                SELECT * FROM saga_recovery_records
                WHERE (
                    status IN ('PENDING', 'RECONCILIATION_REQUIRED')
                    OR (status = 'IN_PROGRESS' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                )
                AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                AND attempt_count <= ?
            """
            params = [now, now, max_attempts]
            if recovery_id:
                query += " AND recovery_id = ?"
                params.append(recovery_id)
            query += " ORDER BY created_at ASC LIMIT 1"

            row = conn.execute(query, tuple(params)).fetchone()
            if not row:
                conn.rollback()
                return None

            rec = self._row_to_saga_recovery(row)
            new_fencing = rec.fencing_token + 1
            cur = conn.execute(
                """
                UPDATE saga_recovery_records
                SET status = 'IN_PROGRESS',
                    owner_token = ?,
                    fencing_token = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE recovery_id = ? AND fencing_token = ?
                """,
                (owner_token, new_fencing, lease_expires, now, rec.recovery_id, rec.fencing_token),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return None
            conn.commit()
            r = conn.execute("SELECT * FROM saga_recovery_records WHERE recovery_id = ?", (rec.recovery_id,)).fetchone()
            return self._row_to_saga_recovery(r)

    def complete_saga_recovery_task(
        self,
        recovery_id: str,
        expected_fencing_token: Optional[int] = None,
        expected_owner_token: Optional[str] = None,
    ) -> bool:
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE saga_recovery_records
                SET status = 'COMPENSATED', updated_at = ?
                WHERE recovery_id = ?
                  AND (? IS NULL OR fencing_token = ?)
                  AND (? IS NULL OR owner_token = ?)
                """,
                (now, recovery_id, expected_fencing_token, expected_fencing_token, expected_owner_token, expected_owner_token),
            )
            if cur.rowcount == 0 and (expected_fencing_token is not None or expected_owner_token is not None):
                raise AggregatorFencingError(f"Stale recovery worker fenced out on task {recovery_id}")
            return cur.rowcount > 0

    def fail_or_retry_saga_recovery_task(
        self,
        recovery_id: str,
        error_message: str,
        is_retryable: bool = True,
        expected_fencing_token: Optional[int] = None,
        expected_owner_token: Optional[str] = None,
        backoff_seconds: float = 2.0,
        max_attempts: int = 5,
    ) -> Optional[SagaRecoveryRecord]:
        now_dt = datetime.utcnow()
        now = now_dt.isoformat()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM saga_recovery_records WHERE recovery_id = ?", (recovery_id,)).fetchone()
            if not row:
                return None
            rec = self._row_to_saga_recovery(row)
            new_attempts = rec.attempt_count + 1
            if is_retryable and new_attempts < max_attempts:
                next_attempt = (now_dt + timedelta(seconds=backoff_seconds)).isoformat()
                new_status = "PENDING"
            else:
                next_attempt = None
                new_status = "FAILED_MANUAL_RECONCILIATION_REQUIRED"

            cur = conn.execute(
                """
                UPDATE saga_recovery_records
                SET status = ?,
                    attempt_count = ?,
                    last_error = ?,
                    next_attempt_at = ?,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE recovery_id = ?
                  AND (? IS NULL OR fencing_token = ?)
                  AND (? IS NULL OR owner_token = ?)
                """,
                (
                    new_status,
                    new_attempts,
                    error_message,
                    next_attempt,
                    now,
                    recovery_id,
                    expected_fencing_token,
                    expected_fencing_token,
                    expected_owner_token,
                    expected_owner_token,
                ),
            )
            if cur.rowcount == 0 and (expected_fencing_token is not None or expected_owner_token is not None):
                raise AggregatorFencingError(f"Stale recovery worker fenced out on task {recovery_id}")
            r = conn.execute("SELECT * FROM saga_recovery_records WHERE recovery_id = ?", (recovery_id,)).fetchone()
            return self._row_to_saga_recovery(r)

    def heartbeat_saga_recovery_task(
        self,
        recovery_id: str,
        owner_token: str,
        fencing_token: int,
        lease_duration_seconds: float = 15.0,
    ) -> bool:
        now_dt = datetime.utcnow()
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE saga_recovery_records
                SET lease_expires_at = ?, updated_at = ?
                WHERE recovery_id = ? AND owner_token = ? AND fencing_token = ? AND status = 'IN_PROGRESS'
                """,
                (lease_expires, now, recovery_id, owner_token, fencing_token),
            )
            if cur.rowcount == 0:
                raise AggregatorFencingError(f"Heartbeat failed: worker '{owner_token}' lost lease on saga task '{recovery_id}'")
            return True


    def get_wallet_holds(self, agency_id: str) -> List[WalletHoldRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM wallet_holds WHERE agency_id = ? ORDER BY created_at DESC",
                (agency_id,),
            ).fetchall()
            return [self._row_to_wallet_hold(r) for r in rows]

    def get_active_wallet_holds(self, agency_id: str) -> List[WalletHoldRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM wallet_holds WHERE agency_id = ? AND status = 'HELD' ORDER BY created_at DESC",
                (agency_id,),
            ).fetchall()
            return [self._row_to_wallet_hold(r) for r in rows]

    def get_wallet_transactions(self, agency_id: str) -> List[WalletTransactionRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM wallet_transactions WHERE agency_id = ? ORDER BY created_at ASC",
                (agency_id,),
            ).fetchall()
            return [self._row_to_wallet_transaction(r) for r in rows]


aggregator_db = AggregatorDatabase()


def get_aggregator_db(db_path: Optional[str] = None) -> AggregatorDatabase:
    if db_path:
        return AggregatorDatabase(db_path=db_path)
    env_path = os.getenv("AGGREGATOR_DB_PATH")
    if env_path and env_path != aggregator_db.db_path:
        return AggregatorDatabase(db_path=env_path)
    return aggregator_db

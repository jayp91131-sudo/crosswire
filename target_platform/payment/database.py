"""
SQLite Database Layer for Mock Payment Provider.
Persists payment transactions and audit events with explicit connection lifecycle management,
integer minor units exact monetary representation, and idempotency integrity enforcement.
"""

import os
import sqlite3
import hashlib
from typing import List, Optional
from datetime import datetime
from contextlib import contextmanager
from target_platform.money import to_minor_units
from target_platform.models import PaymentTransactionRecord, PaymentStatus


class PaymentIdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a mismatched payload."""
    pass


class PaymentDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.db_path = os.path.join(base_dir, "payment.db")
        else:
            self.db_path = db_path
        self._init_db()

    @contextmanager
    def _connection(self):
        """Context manager guaranteeing deterministic SQLite connection closure."""
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS payment_records (
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_payment_booking ON payment_records (booking_id);
                CREATE INDEX IF NOT EXISTS idx_payment_intent ON payment_records (intent_id);

                CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_rec_idemp 
                ON payment_records (agency_id, idempotency_key) 
                WHERE idempotency_key IS NOT NULL AND idempotency_key != '';

                CREATE TABLE IF NOT EXISTS payment_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT,
                    booking_id TEXT,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    timestamp TEXT NOT NULL
                );
            """)

            try:
                conn.execute("ALTER TABLE payment_records ADD COLUMN amount_minor INTEGER DEFAULT 0")
            except Exception:
                pass

            try:
                conn.execute("UPDATE payment_records SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_minor = 0 AND amount > 0")
            except Exception:
                pass

    def reset(self):
        with self._connection() as conn:
            conn.execute("DELETE FROM payment_audit_events")
            conn.execute("DELETE FROM payment_records")

    def log_event(self, payment_id: Optional[str], booking_id: Optional[str], event_type: str, details: str):
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO payment_audit_events (payment_id, booking_id, event_type, details, timestamp) VALUES (?, ?, ?, ?, ?)",
                (payment_id, booking_id, event_type, details, datetime.utcnow().isoformat()),
            )

    def save_payment(self, rec: PaymentTransactionRecord) -> PaymentTransactionRecord:
        amount_minor = rec.amount_minor if rec.amount_minor else to_minor_units(rec.amount, rec.currency)
        try:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO payment_records (
                        payment_id, booking_id, intent_id, agency_id, amount, amount_minor, currency,
                        status, transaction_reference, idempotency_key, payload_hash, error_message,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec.payment_id,
                        rec.booking_id,
                        rec.intent_id,
                        rec.agency_id,
                        rec.amount,
                        amount_minor,
                        rec.currency,
                        rec.status.value if hasattr(rec.status, "value") else str(rec.status),
                        rec.transaction_reference,
                        rec.idempotency_key,
                        rec.payload_hash,
                        rec.error_message,
                        rec.created_at.isoformat() if isinstance(rec.created_at, datetime) else str(rec.created_at),
                        rec.updated_at.isoformat() if isinstance(rec.updated_at, datetime) else str(rec.updated_at),
                    ),
                )
            rec.amount_minor = amount_minor
            return rec
        except sqlite3.IntegrityError:
            if rec.idempotency_key:
                existing = self.get_payment_by_idempotency_key(rec.agency_id, rec.idempotency_key)
                if existing:
                    if rec.payload_hash and existing.payload_hash and rec.payload_hash != existing.payload_hash:
                        raise PaymentIdempotencyConflictError(
                            f"Idempotency key '{rec.idempotency_key}' conflict: payload hash mismatch"
                        )
                    return existing
            raise

    def update_payment_status(
        self, payment_id: str, status: PaymentStatus, transaction_reference: str, error_message: Optional[str] = None
    ) -> Optional[PaymentTransactionRecord]:
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE payment_records 
                SET status = ?, transaction_reference = ?, error_message = ?, updated_at = ?
                WHERE payment_id = ?
                """,
                (
                    status.value if hasattr(status, "value") else str(status),
                    transaction_reference,
                    error_message,
                    now,
                    payment_id,
                ),
            )
        return self.get_payment(payment_id)

    def get_payment(self, payment_id: str) -> Optional[PaymentTransactionRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM payment_records WHERE payment_id = ?", (payment_id,)).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def get_payment_by_booking(self, booking_id: str) -> Optional[PaymentTransactionRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM payment_records WHERE booking_id = ? ORDER BY created_at DESC LIMIT 1", (booking_id,)).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def get_payment_by_idempotency_key(self, agency_id: str, idempotency_key: str) -> Optional[PaymentTransactionRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM payment_records WHERE agency_id = ? AND idempotency_key = ?",
                (agency_id, idempotency_key),
            ).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> PaymentTransactionRecord:
        keys = row.keys()
        amount = row["amount"]
        currency = row["currency"]
        amount_minor = row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] else to_minor_units(amount, currency)
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
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


payment_db = PaymentDatabase()


def get_payment_db() -> PaymentDatabase:
    return payment_db

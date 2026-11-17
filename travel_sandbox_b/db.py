"""
TravelSandboxB: Independent B2B Travel Platform Database.
Completely separate SQLite persistence layer with divergent table & column schema.
Zero dependency on Target A's models or database modules.
"""

import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import threading

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tsb_orders (
    order_id TEXT PRIMARY KEY,
    intent_token TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    property_code TEXT NOT NULL,
    total_amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    order_status TEXT NOT NULL,
    idempotency_token TEXT,
    retry_count INTEGER DEFAULT 0,
    refund_entitlement_cents INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tsb_vendor_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    intent_token TEXT NOT NULL,
    vendor_code TEXT NOT NULL,
    status TEXT NOT NULL,
    rate_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tsb_audit_ledger (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_reference TEXT NOT NULL,
    intent_token TEXT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    movement_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tsb_webhook_journal (
    event_token TEXT PRIMARY KEY,
    provider_code TEXT NOT NULL,
    payload_sha TEXT NOT NULL,
    first_received_at TEXT NOT NULL,
    times_delivered INTEGER DEFAULT 1
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SandboxBDatabase:
    """Thread-safe SQLite connection manager for TravelSandboxB."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    def reset(self) -> None:
        """Wipes all rows cleanly for deterministic test reproducibility."""
        with self._lock:
            self._conn.execute("DELETE FROM tsb_vendor_confirmations")
            self._conn.execute("DELETE FROM tsb_orders")
            self._conn.execute("DELETE FROM tsb_audit_ledger")
            self._conn.execute("DELETE FROM tsb_webhook_journal")
            self._conn.commit()

    # ---------------- Orders ----------------
    def save_order(self, order: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO tsb_orders (
                    order_id, intent_token, customer_name, property_code,
                    total_amount_cents, currency, order_status, idempotency_token,
                    retry_count, refund_entitlement_cents, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order["order_id"],
                    order["intent_token"],
                    order.get("customer_name", "Guest"),
                    order["property_code"],
                    order["total_amount_cents"],
                    order["currency"],
                    order["order_status"],
                    order.get("idempotency_token"),
                    order.get("retry_count", 0),
                    order.get("refund_entitlement_cents", 0),
                    order.get("created_at", _now_iso()),
                    _now_iso(),
                ),
            )
            self._conn.commit()

    def get_order_by_intent(self, intent_token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tsb_orders WHERE intent_token = ?", (intent_token,)
            ).fetchone()
            return dict(row) if row else None

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tsb_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    # ---------------- Vendor Confirmations ----------------
    def save_vendor_confirmation(self, conf: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tsb_vendor_confirmations (
                    confirmation_id, intent_token, vendor_code, status,
                    rate_cents, currency, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conf["confirmation_id"],
                    conf["intent_token"],
                    conf["vendor_code"],
                    conf["status"],
                    conf["rate_cents"],
                    conf["currency"],
                    conf.get("idempotency_key"),
                    conf.get("created_at", _now_iso()),
                ),
            )
            self._conn.commit()

    def get_vendor_confirmations(self, intent_token: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tsb_vendor_confirmations WHERE intent_token = ? ORDER BY created_at ASC",
                (intent_token,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_confirmation_by_idempotency(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tsb_vendor_confirmations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return dict(row) if row else None

    # ---------------- Ledger ----------------
    def record_ledger_movement(
        self,
        transaction_ref: str,
        amount_cents: int,
        currency: str,
        movement_type: str,
        direction: str,
        intent_token: Optional[str] = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO tsb_audit_ledger (
                    transaction_reference, intent_token, amount_cents,
                    currency, movement_type, direction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_ref,
                    intent_token,
                    amount_cents,
                    currency,
                    movement_type,
                    direction,
                    _now_iso(),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_ledger_entries(self, ref_or_token: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tsb_audit_ledger
                WHERE transaction_reference = ? OR intent_token = ?
                ORDER BY record_id ASC
                """,
                (ref_or_token, ref_or_token),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------- Webhook Journal ----------------
    def get_webhook_record(self, event_token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tsb_webhook_journal WHERE event_token = ?", (event_token,)
            ).fetchone()
            return dict(row) if row else None

    def record_webhook(self, event_token: str, provider_code: str, payload_sha: str) -> bool:
        """Returns True if newly inserted, False if already existed (increments delivery count)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT times_delivered FROM tsb_webhook_journal WHERE event_token = ?",
                (event_token,),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE tsb_webhook_journal SET times_delivered = times_delivered + 1 WHERE event_token = ?",
                    (event_token,),
                )
                self._conn.commit()
                return False
            else:
                self._conn.execute(
                    """
                    INSERT INTO tsb_webhook_journal (
                        event_token, provider_code, payload_sha, first_received_at, times_delivered
                    ) VALUES (?, ?, ?, ?, 1)
                    """,
                    (event_token, provider_code, payload_sha, _now_iso()),
                )
                self._conn.commit()
                return True


# Global in-memory instance for testing
_sandbox_b_db = SandboxBDatabase()


def get_sandbox_b_db() -> SandboxBDatabase:
    return _sandbox_b_db

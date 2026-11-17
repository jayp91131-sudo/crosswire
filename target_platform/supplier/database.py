"""
SQLite Database Layer for Mock Hotel Supplier.
Persists supplier reservations and audit events with explicit connection lifecycle management (guaranteed close),
integer minor units exact monetary representation, and database-level uniqueness enforcement for idempotency keys.
"""

import os
import sqlite3
from typing import List, Optional
from datetime import datetime
from contextlib import contextmanager
from target_platform.money import to_minor_units
from target_platform.models import SupplierReservation, HotelInventoryRecord

DEFAULT_SUPPLIER_DB_PATH = os.path.join(os.path.dirname(__file__), "supplier.db")


class SupplierIdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a mismatched payload."""
    pass


class SupplierDatabase:
    def __init__(self, db_path: str = DEFAULT_SUPPLIER_DB_PATH):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _connection(self):
        """Context manager guaranteeing connection commit/rollback and deterministic close."""
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

    def _init_schema(self):
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS supplier_reservations (
                    supplier_reservation_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    hotel_id TEXT NOT NULL,
                    room_type TEXT NOT NULL,
                    guest_name TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload_hash TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_supplier_res_intent ON supplier_reservations (intent_id);

                CREATE UNIQUE INDEX IF NOT EXISTS uidx_supplier_res_idemp 
                ON supplier_reservations (idempotency_key) 
                WHERE idempotency_key IS NOT NULL AND idempotency_key != '';

                CREATE TABLE IF NOT EXISTS supplier_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hotel_inventory (
                    hotel_id TEXT NOT NULL,
                    room_type TEXT NOT NULL,
                    capacity INTEGER NOT NULL DEFAULT 1,
                    allocated_units INTEGER NOT NULL DEFAULT 0,
                    available_units INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (hotel_id, room_type)
                );

                INSERT OR IGNORE INTO hotel_inventory (
                    hotel_id, room_type, capacity, allocated_units, available_units, created_at, updated_at
                ) VALUES
                    ('htl_mumbai_grand', 'deluxe_suite', 1, 0, 1, datetime('now'), datetime('now')),
                    ('htl_mumbai_grand', 'standard_room', 10, 0, 10, datetime('now'), datetime('now'));
            """)

            try:
                conn.execute("ALTER TABLE supplier_reservations ADD COLUMN payload_hash TEXT")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE supplier_reservations ADD COLUMN amount_minor INTEGER DEFAULT 0")
            except Exception:
                pass

            try:
                conn.execute("UPDATE supplier_reservations SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_minor = 0 AND amount > 0")
            except Exception:
                pass

    def reset(self):
        """Wipes table contents for clean test isolation."""
        with self._connection() as conn:
            conn.execute("DELETE FROM supplier_audit_events")
            conn.execute("DELETE FROM supplier_reservations")
            conn.execute("DELETE FROM hotel_inventory")
            now_iso = datetime.utcnow().isoformat()
            conn.execute(
                """
                INSERT INTO hotel_inventory (
                    hotel_id, room_type, capacity, allocated_units, available_units, created_at, updated_at
                ) VALUES
                    ('htl_mumbai_grand', 'deluxe_suite', 1, 0, 1, ?, ?),
                    ('htl_mumbai_grand', 'standard_room', 10, 0, 10, ?, ?)
                """,
                (now_iso, now_iso, now_iso, now_iso),
            )

    def log_event(self, intent_id: str, event_type: str, details: str):
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO supplier_audit_events (intent_id, event_type, details, timestamp) VALUES (?, ?, ?, ?)",
                (intent_id, event_type, details, datetime.utcnow().isoformat()),
            )

    def save_reservation(self, res: SupplierReservation) -> SupplierReservation:
        """
        Persists reservation to SQLite.
        If an identical idempotency_key is concurrently inserted, SQLite raises IntegrityError.
        This method catches it and returns the winning atomic reservation, or detects conflict.
        """
        amount_minor = res.amount_minor if res.amount_minor else to_minor_units(res.amount, res.currency)
        try:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO supplier_reservations (
                        supplier_reservation_id, intent_id, hotel_id, room_type,
                        guest_name, amount, amount_minor, currency, idempotency_key, payload_hash, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        res.supplier_reservation_id,
                        res.intent_id,
                        res.hotel_id,
                        res.room_type,
                        res.guest_name,
                        res.amount,
                        amount_minor,
                        res.currency,
                        res.idempotency_key,
                        res.payload_hash,
                        res.status,
                        res.created_at.isoformat() if isinstance(res.created_at, datetime) else str(res.created_at),
                    ),
                )
            res.amount_minor = amount_minor
            return res
        except sqlite3.IntegrityError:
            if res.idempotency_key:
                existing = self.get_reservation_by_idempotency_key(res.idempotency_key)
                if existing:
                    if res.payload_hash and existing.payload_hash and res.payload_hash != existing.payload_hash:
                        raise SupplierIdempotencyConflictError(
                            f"Idempotency key '{res.idempotency_key}' conflict: payload hash mismatch"
                        )
                    return existing
            raise

    def get_reservations_by_intent(self, intent_id: str) -> List[SupplierReservation]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM supplier_reservations WHERE intent_id = ? ORDER BY created_at ASC",
                (intent_id,),
            ).fetchall()

            return [self._row_to_res(row) for row in rows]

    def get_reservation_count_for_intent(self, intent_id: str) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM supplier_reservations WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            return row["cnt"] if row else 0

    def get_reservation_by_idempotency_key(self, idempotency_key: str) -> Optional[SupplierReservation]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM supplier_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_res(row)

    def _row_to_res(self, row: sqlite3.Row) -> SupplierReservation:
        keys = row.keys()
        amount = row["amount"]
        currency = row["currency"]
        amount_minor = row["amount_minor"] if "amount_minor" in keys and row["amount_minor"] else to_minor_units(amount, currency)
        return SupplierReservation(
            supplier_reservation_id=row["supplier_reservation_id"],
            intent_id=row["intent_id"],
            hotel_id=row["hotel_id"],
            room_type=row["room_type"],
            guest_name=row["guest_name"],
            amount=amount,
            amount_minor=amount_minor,
            currency=currency,
            idempotency_key=row["idempotency_key"],
            payload_hash=row["payload_hash"] if "payload_hash" in keys else None,
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_reservation(self, supplier_reservation_id: str) -> Optional[SupplierReservation]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM supplier_reservations WHERE supplier_reservation_id = ?",
                (supplier_reservation_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_res(row)

    def get_all_reservations(self) -> List[SupplierReservation]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM supplier_reservations ORDER BY created_at ASC").fetchall()
            return [self._row_to_res(r) for r in rows]

    def cancel_reservation(self, supplier_reservation_id: str) -> bool:
        """Cancels a supplier reservation and compensates inventory atomically."""
        now_iso = datetime.utcnow().isoformat()
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """
                UPDATE supplier_reservations
                SET status = 'CANCELLED'
                WHERE supplier_reservation_id = ? AND status != 'CANCELLED'
                """,
                (supplier_reservation_id,),
            )
            if cur.rowcount == 0:
                conn.commit()
                return False

            row = conn.execute(
                "SELECT hotel_id, room_type FROM supplier_reservations WHERE supplier_reservation_id = ?",
                (supplier_reservation_id,),
            ).fetchone()

            if row:
                conn.execute(
                    """
                    UPDATE hotel_inventory
                    SET allocated_units = MAX(0, allocated_units - 1),
                        available_units = MIN(capacity, available_units + 1),
                        updated_at = ?
                    WHERE hotel_id = ? AND room_type = ?
                    """,
                    (now_iso, row["hotel_id"], row["room_type"]),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------------------------------------------------------
    # Hotel Inventory Management (Milestone 2.2)
    # ---------------------------------------------------------
    def get_inventory(self, hotel_id: str, room_type: str) -> Optional[HotelInventoryRecord]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM hotel_inventory WHERE hotel_id = ? AND room_type = ?",
                (hotel_id, room_type),
            ).fetchone()
            if not row:
                return None
            return HotelInventoryRecord(
                hotel_id=row["hotel_id"],
                room_type=row["room_type"],
                capacity=row["capacity"],
                allocated_units=row["allocated_units"],
                available_units=row["available_units"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def set_inventory(
        self, hotel_id: str, room_type: str, capacity: int, available_units: Optional[int] = None
    ) -> HotelInventoryRecord:
        avail = available_units if available_units is not None else capacity
        now_iso = datetime.utcnow().isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO hotel_inventory (
                    hotel_id, room_type, capacity, allocated_units, available_units, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hotel_id, room_type) DO UPDATE SET
                    capacity = excluded.capacity,
                    allocated_units = excluded.allocated_units,
                    available_units = excluded.available_units,
                    updated_at = excluded.updated_at
                """,
                (hotel_id, room_type, capacity, capacity - avail, avail, now_iso, now_iso),
            )
        return self.get_inventory(hotel_id, room_type)

    def reserve_inventory_fixed(self, hotel_id: str, room_type: str) -> bool:
        """
        FIXED MODE: Atomic conditional reservation.
        Guarantees:
        1. available_units >= 1 condition inside atomic update.
        2. Inventory never becomes negative.
        3. Confirmed reservations never exceed capacity under concurrency.
        """
        now_iso = datetime.utcnow().isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE hotel_inventory
                SET allocated_units = allocated_units + 1,
                    available_units = available_units - 1,
                    updated_at = ?
                WHERE hotel_id = ? AND room_type = ?
                  AND available_units >= 1
                """,
                (now_iso, hotel_id, room_type),
            )
            return cur.rowcount > 0

    def reserve_inventory_vulnerable(self, hotel_id: str, room_type: str) -> bool:
        """
        VULNERABLE MODE: Deliberate TOCTOU race condition.
        Naively decrements inventory without atomic condition.
        Allows concurrent requests to oversell the last room.
        """
        now_iso = datetime.utcnow().isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE hotel_inventory
                SET allocated_units = allocated_units + 1,
                    available_units = available_units - 1,
                    updated_at = ?
                WHERE hotel_id = ? AND room_type = ?
                """,
                (now_iso, hotel_id, room_type),
            )
            return True

    def release_inventory(self, hotel_id: str, room_type: str) -> bool:
        """Compensates/releases one inventory unit upon cancellation or booking failure."""
        now_iso = datetime.utcnow().isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE hotel_inventory
                SET allocated_units = MAX(0, allocated_units - 1),
                    available_units = MIN(capacity, available_units + 1),
                    updated_at = ?
                WHERE hotel_id = ? AND room_type = ?
                """,
                (now_iso, hotel_id, room_type),
            )
            return cur.rowcount > 0

    def get_events_for_intent(self, intent_id: str) -> List[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM supplier_audit_events WHERE intent_id = ? ORDER BY event_id ASC",
                (intent_id,),
            ).fetchall()
            return [dict(r) for r in rows]


supplier_db = SupplierDatabase()


def get_supplier_db() -> SupplierDatabase:
    return supplier_db

"""
Database connection factory for the M2.3A Prototype.
Enforces and verifies PRAGMA foreign_keys = ON and PRAGMA recursive_triggers = ON.
"""

import sqlite3
from contextlib import contextmanager
from typing import Generator


class SystemIntegrityError(Exception):
    """Raised when critical connection PRAGMAs cannot be enabled or verified."""
    pass


def create_connection(db_path: str, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row

    # 1. Enforce and verify foreign keys
    conn.execute("PRAGMA foreign_keys = ON;")
    fk_row = conn.execute("PRAGMA foreign_keys;").fetchone()
    if not fk_row or fk_row[0] != 1:
        conn.close()
        raise SystemIntegrityError("Failed to enable and verify PRAGMA foreign_keys = ON")

    # 2. Enforce and verify recursive triggers (prevents INSERT OR REPLACE bypasses)
    conn.execute("PRAGMA recursive_triggers = ON;")
    rec_row = conn.execute("PRAGMA recursive_triggers;").fetchone()
    if not rec_row or rec_row[0] != 1:
        conn.close()
        raise SystemIntegrityError("Failed to enable and verify PRAGMA recursive_triggers = ON")

    # 3. Configure WAL mode for disk-based databases
    if db_path != ":memory:" and not db_path.startswith("file::memory:"):
        conn.execute("PRAGMA journal_mode = WAL;")

    return conn


@contextmanager
def managed_connection(db_path: str, timeout: float = 30.0) -> Generator[sqlite3.Connection, None, None]:
    conn = create_connection(db_path, timeout=timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

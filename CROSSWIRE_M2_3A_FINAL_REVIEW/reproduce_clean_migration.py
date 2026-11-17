"""
Crosswire M2.3A Clean Reproducibility & Rollback Verification.
Builds a fresh disposable SQLite database, verifies schema integrity,
and validates transactional rollback upon intentional failure.
"""

import os
import sys
import sqlite3
import json
from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parent
DISPOSABLE_DB = PROTOTYPE_DIR / "disposable_reproduce.db"
SCHEMA_SQL = PROTOTYPE_DIR / "schema_m2_3a.sql"

def run_reproducibility():
    if DISPOSABLE_DB.exists():
        DISPOSABLE_DB.unlink()

    conn = sqlite3.connect(str(DISPOSABLE_DB))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA recursive_triggers = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    sqlite_ver = conn.execute("SELECT sqlite_version();").fetchone()[0]
    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    rec_trg = conn.execute("PRAGMA recursive_triggers;").fetchone()[0]

    print(f"=== DATABASE PRAGMAS ===")
    print(f"SQLite Version: {sqlite_ver}")
    print(f"Journal Mode: {journal_mode}")
    print(f"PRAGMA foreign_keys: {fk_status}")
    print(f"PRAGMA recursive_triggers: {rec_trg}")

    # Read and execute full schema
    with open(SCHEMA_SQL, "r", encoding="utf-8") as f:
        ddl = f.read()
    conn.executescript(ddl)

    # Introspect objects
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
    ).fetchall()]
    
    indexes = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
    ).fetchall()]
    
    triggers = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name;"
    ).fetchall()]

    fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()

    print(f"\n=== SCHEMA INTROSPECTION ===")
    print(f"Tables ({len(tables)}): {tables}")
    print(f"Indexes ({len(indexes)}): {indexes}")
    print(f"Triggers ({len(triggers)}): {triggers}")
    print(f"PRAGMA foreign_key_check violations: {len(fk_violations)}")

    # Intentional failure and rollback verification
    print(f"\n=== TRANSACTIONAL ROLLBACK VERIFICATION ===")
    rollback_successful = False
    try:
        with conn:
            conn.execute("INSERT INTO agencies VALUES ('TEST_AGENCY', 'Test Agency', 'key_test', '2026-11-13T12:00:00Z');")
            # Force violation: inserting child intent with non-existent parent booking violates FK
            conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_FAIL', 'BKG_NONEXISTENT', 'TEST_AGENCY', 'SUP_EXPEDIA', 'ACC_1', 'PENDING', '2026-11-13T12:00:00Z', '2026-11-13T12:00:00Z');")
    except sqlite3.IntegrityError as e:
        print(f"Expected failure caught: {type(e).__name__}: {e}")
        # Verify that TEST_AGENCY was rolled back
        row = conn.execute("SELECT * FROM agencies WHERE agency_id = 'TEST_AGENCY';").fetchone()
        if row is None:
            print("Rollback Confirmed: 'TEST_AGENCY' was not persisted after aborted transaction.")
            rollback_successful = True
        else:
            print("Rollback Failed: 'TEST_AGENCY' was leaked into database!")

    conn.close()
    
    return {
        "sqlite_version": sqlite_ver,
        "journal_mode": journal_mode,
        "foreign_keys_enabled": bool(fk_status),
        "recursive_triggers_enabled": bool(rec_trg),
        "table_count": len(tables),
        "tables": tables,
        "index_count": len(indexes),
        "indexes": indexes,
        "trigger_count": len(triggers),
        "triggers": triggers,
        "fk_violations": len(fk_violations),
        "rollback_verified": rollback_successful
    }

if __name__ == "__main__":
    res = run_reproducibility()
    with open(PROTOTYPE_DIR / "M2_3A_CLEAN_REPRODUCIBILITY_OUTPUT.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print("\nClean reproducibility successfully completed.")

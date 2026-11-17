"""
M2.3A Prototype Migration Execution Runner.
Executes schema_m2_3a.sql against a clean disposable database and captures all schema evidence.
"""

import os
import json
import sqlite3
from pathlib import Path
from db_connection import managed_connection, create_connection

PROTOTYPE_DIR = Path(__file__).resolve().parent
DISPOSABLE_DB_PATH = PROTOTYPE_DIR / "disposable_prototype.db"
SCHEMA_SQL_PATH = PROTOTYPE_DIR / "schema_m2_3a.sql"
LOG_JSON_PATH = PROTOTYPE_DIR / "migration_execution_log.json"


def run_migration():
    if DISPOSABLE_DB_PATH.exists():
        DISPOSABLE_DB_PATH.unlink()

    print(f"Creating disposable database at: {DISPOSABLE_DB_PATH}")
    
    with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Capture environment and SQLite details
    conn_raw = create_connection(str(DISPOSABLE_DB_PATH))
    sqlite_version = conn_raw.execute("SELECT sqlite_version();").fetchone()[0]
    fk_status = conn_raw.execute("PRAGMA foreign_keys;").fetchone()[0]
    rec_status = conn_raw.execute("PRAGMA recursive_triggers;").fetchone()[0]
    conn_raw.close()

    executed_statements = []
    
    with managed_connection(str(DISPOSABLE_DB_PATH)) as conn:
        # Execute migration DDL
        conn.executescript(sql_content)

    # Reconnect and inspect database schema
    inspection_conn = create_connection(str(DISPOSABLE_DB_PATH))
    
    # 1. Inspect sqlite_master objects
    master_rows = inspection_conn.execute("""
        SELECT type, name, tbl_name, sql 
        FROM sqlite_master 
        ORDER BY type, name;
    """).fetchall()

    schema_inventory = [
        {"type": row["type"], "name": row["name"], "tbl_name": row["tbl_name"], "sql": row["sql"]}
        for row in master_rows
    ]

    # 2. Run PRAGMA foreign_key_check
    fk_violations = inspection_conn.execute("PRAGMA foreign_key_check;").fetchall()
    fk_violations_list = [dict(v) for v in fk_violations]

    # 3. Collect foreign keys for each table
    tables = [row["name"] for row in master_rows if row["type"] == "table" and not row["name"].startswith("sqlite_")]
    table_fks = {}
    for tbl in tables:
        fks = inspection_conn.execute(f"PRAGMA foreign_key_list({tbl});").fetchall()
        table_fks[tbl] = [
            {"id": fk["id"], "seq": fk["seq"], "table": fk["table"], "from": fk["from"], "to": fk["to"], "on_update": fk["on_update"], "on_delete": fk["on_delete"], "match": fk["match"]}
            for fk in fks
        ]

    # 4. Collect triggers
    triggers = [row["name"] for row in master_rows if row["type"] == "trigger"]

    # 5. Collect indexes
    indexes = [row["name"] for row in master_rows if row["type"] == "index"]

    inspection_conn.close()

    result = {
        "sqlite_version": sqlite_version,
        "disposable_db_path": str(DISPOSABLE_DB_PATH),
        "schema_sql_path": str(SCHEMA_SQL_PATH),
        "pragma_foreign_keys": fk_status,
        "pragma_recursive_triggers": rec_status,
        "pragma_foreign_key_check_violations_count": len(fk_violations_list),
        "pragma_foreign_key_check_violations": fk_violations_list,
        "total_tables_created": len(tables),
        "total_indexes_created": len(indexes),
        "total_triggers_created": len(triggers),
        "tables": tables,
        "indexes": indexes,
        "triggers": triggers,
        "foreign_keys_per_table": table_fks,
        "schema_inventory": schema_inventory
    }

    with open(LOG_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"Migration completed successfully.")
    print(f"SQLite Version: {sqlite_version}")
    print(f"Tables created: {len(tables)}")
    print(f"Indexes created: {len(indexes)}")
    print(f"Triggers created: {len(triggers)}")
    print(f"Foreign-Key Violations: {len(fk_violations_list)}")
    print(f"Metadata written to: {LOG_JSON_PATH}")

    return result


if __name__ == "__main__":
    run_migration()

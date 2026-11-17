"""
Crosswire 2.3A Adversarial Database Test Suite.
Executes 30 rigorous adversarial tests against the executable SQLite schema prototype.
Captures exact assertion evidence, integrity errors, and trigger aborts.
"""

import os
import sys
from pathlib import Path

# Ensure .venv site-packages is accessible
VENV_SITE = Path(r"C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\.venv\Lib\site-packages")
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

import sqlite3
import pytest
from db_connection import create_connection, managed_connection

PROTOTYPE_DIR = Path(__file__).resolve().parent
DISPOSABLE_DB_PATH = PROTOTYPE_DIR / "disposable_prototype.db"
SCHEMA_SQL_PATH = PROTOTYPE_DIR / "schema_m2_3a.sql"


def reset_test_db() -> sqlite3.Connection:
    """Creates a fresh in-memory or clean file database initialized with schema_m2_3a.sql."""
    conn = create_connection(":memory:")
    with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def seed_base_environment(conn: sqlite3.Connection):
    """Seeds baseline agency, supplier accounts, authorizations, and parent booking."""
    now = "2026-11-13T12:00:00Z"
    
    # 1. Agencies
    conn.execute("INSERT INTO agencies VALUES ('AGENCY_ALPHA', 'Alpha Travel', 'key_alpha_1', ?)", (now,))
    conn.execute("INSERT INTO agencies VALUES ('AGENCY_BETA', 'Beta Travel', 'key_beta_1', ?)", (now,))

    # 2. Supplier Accounts
    # Global account
    conn.execute("INSERT INTO supplier_provider_accounts VALUES ('SUP_EXPEDIA', 'ACC_GLOBAL_1', 1, 1, ?)", (now,))
    # Non-global account
    conn.execute("INSERT INTO supplier_provider_accounts VALUES ('SUP_HOTELBEDS', 'ACC_ALPHA_PRIVATE', 0, 1, ?)", (now,))
    conn.execute("INSERT INTO supplier_provider_accounts VALUES ('SUP_HOTELBEDS', 'ACC_DISABLED_1', 0, 1, ?)", (now,))
    
    # 3. Supplier Authorizations
    conn.execute("INSERT INTO agency_provider_account_authorizations VALUES ('AGENCY_ALPHA', 'SUP_HOTELBEDS', 'ACC_ALPHA_PRIVATE', 1, ?)", (now,))
    conn.execute("INSERT INTO agency_provider_account_authorizations VALUES ('AGENCY_ALPHA', 'SUP_HOTELBEDS', 'ACC_DISABLED_1', 0, ?)", (now,))

    # 4. Payment Accounts & Authorizations
    conn.execute("INSERT INTO payment_provider_accounts VALUES ('PAY_STRIPE', 'STRIPE_GLOBAL', 1, 1, ?)", (now,))
    conn.execute("INSERT INTO payment_provider_accounts VALUES ('PAY_STRIPE', 'STRIPE_ALPHA_CUSTOM', 0, 1, ?)", (now,))
    conn.execute("INSERT INTO payment_provider_accounts VALUES ('PAY_STRIPE', 'STRIPE_DISABLED', 0, 1, ?)", (now,))
    conn.execute("INSERT INTO agency_payment_account_authorizations VALUES ('AGENCY_ALPHA', 'PAY_STRIPE', 'STRIPE_ALPHA_CUSTOM', 1, ?)", (now,))
    conn.execute("INSERT INTO agency_payment_account_authorizations VALUES ('AGENCY_ALPHA', 'PAY_STRIPE', 'STRIPE_DISABLED', 0, ?)", (now,))

    # 5. Parent Booking Records
    conn.execute("INSERT INTO booking_records VALUES ('BKG_100', 'INTENT_100', 'AGENCY_ALPHA', 'IN_PROGRESS', 50000, 'USD', 1, ?, ?)", (now, now))
    conn.execute("INSERT INTO booking_records VALUES ('BKG_200', 'INTENT_200', 'AGENCY_BETA', 'IN_PROGRESS', 30000, 'USD', 1, ?, ?)", (now, now))

    # 6. Payment Intent
    conn.execute("INSERT INTO payment_intents VALUES ('PI_100', 'BKG_100', 'AGENCY_ALPHA', 'PAY_STRIPE', 'STRIPE_ALPHA_CUSTOM', 50000, 'USD', 'AUTHORIZED', ?, ?)", (now, now))


# ============================================================================
# ADVERSARIAL TESTS 1 - 20 (Mandated by Specification)
# ============================================================================

def test_01_invalid_child_parent_relationship_rejected():
    """1. Invalid child-parent relationship rejected via composite FK."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Attempt to link child with AGENCY_BETA to BKG_100 (owned by AGENCY_ALPHA)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO child_supplier_intents VALUES (
                'CHILD_X', 'BKG_100', 'AGENCY_BETA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?
            )
        """, (now, now))


def test_02_unauthorized_supplier_account_rejected():
    """2. Unauthorized supplier account rejected via trigger trg_check_supplier_account_authorization."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # AGENCY_BETA attempts to use ACC_ALPHA_PRIVATE (not authorized for Beta)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Unauthorized or disabled supplier provider account"):
        conn.execute("""
            INSERT INTO child_supplier_intents VALUES (
                'CHILD_X', 'BKG_200', 'AGENCY_BETA', 'SUP_HOTELBEDS', 'ACC_ALPHA_PRIVATE', 'PENDING', ?, ?
            )
        """, (now, now))


def test_03_disabled_supplier_authorization_rejected_at_dispatch():
    """3. Disabled supplier authorization rejected at outbox dispatch."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Attempt outbox dispatch with disabled account ACC_DISABLED_1
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Unauthorized or disabled supplier provider account at outbox dispatch"):
        conn.execute("""
            INSERT INTO child_operation_outbox VALUES (
                'OP_DISPATCH_1', 'CHILD_1:CREATE', 'OP_DISPATCH_1:att-1', 'CHILD_1', 'BKG_100', 'AGENCY_ALPHA',
                'SUP_HOTELBEDS', 'ACC_DISABLED_1', 'CREATE_RESERVATION', 'NEITHER', 'PENDING',
                NULL, NULL, 1, 0, 3, NULL, 'hash_abc', '{}', NULL, ?, ?
            )
        """, (now, now))


def test_04_unauthorized_payment_account_rejected():
    """4. Unauthorized payment account rejected via trigger trg_check_payment_account_authorization."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # AGENCY_BETA attempts to create payment outbox using STRIPE_ALPHA_CUSTOM
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Unauthorized or disabled payment provider account"):
        conn.execute("""
            INSERT INTO parent_payment_outbox VALUES (
                'PAY_OP_X', 'BKG_200', 'AGENCY_BETA', 'PI_100', 'PAY_STRIPE', 'STRIPE_ALPHA_CUSTOM',
                'CAPTURE_CARD', 'PENDING', NULL, NULL, 1, 0, 'idemp_pay_x', 'hash_p', NULL, NULL, NULL, ?, ?
            )
        """, (now, now))


def test_05_disabled_payment_authorization_rejected_at_dispatch():
    """5. Disabled payment authorization rejected at dispatch."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # AGENCY_ALPHA attempts to dispatch payment outbox with STRIPE_DISABLED (is_enabled = 0)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Unauthorized or disabled payment provider account"):
        conn.execute("""
            INSERT INTO parent_payment_outbox VALUES (
                'PAY_OP_DIS', 'BKG_100', 'AGENCY_ALPHA', 'PI_100', 'PAY_STRIPE', 'STRIPE_DISABLED',
                'CAPTURE_CARD', 'PENDING', NULL, NULL, 1, 0, 'idemp_pay_dis', 'hash_p', NULL, NULL, NULL, ?, ?
            )
        """, (now, now))


def test_06_update_to_immutable_observation_blocked():
    """6. UPDATE to immutable observation blocked via trg_prevent_update_observations."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Insert valid child intent and outbox operation
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_101', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_101:CREATE:1', 'CHILD_101:CREATE_RESERVATION', 'CHILD_101:CREATE:att-1', 'CHILD_101', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_101', 'hash_101', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Insert valid observation
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_101', 'OP_101:CREATE:1', 'CHILD_101:CREATE:att-1', 'CHILD_101:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_101', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_999', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            1, 'hash_obs_101', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))

    # Attempt UPDATE on observation
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: UPDATE on supplier_reservation_observations is strictly prohibited"):
        conn.execute("UPDATE supplier_reservation_observations SET confirmed_hotel_id = 'HTL_TAMPERED' WHERE observation_id = 'OBS_101'")


def test_07_delete_from_immutable_observation_blocked():
    """7. DELETE from immutable observation blocked via trg_prevent_delete_observations."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_102', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_102:CREATE:1', 'CHILD_102:CREATE_RESERVATION', 'CHILD_102:CREATE:att-1', 'CHILD_102', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_102', 'hash_102', '{}', NULL, ?, ?
        )
    """, (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_102', 'OP_102:CREATE:1', 'CHILD_102:CREATE:att-1', 'CHILD_102:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_102', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_102', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            1, 'hash_obs_102', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))

    # Attempt DELETE on observation
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: DELETE on supplier_reservation_observations is strictly prohibited"):
        conn.execute("DELETE FROM supplier_reservation_observations WHERE observation_id = 'OBS_102'")


def test_08_insert_or_replace_cannot_bypass_immutability():
    """8. INSERT OR REPLACE triggers BEFORE DELETE and is aborted."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_103', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_103:CREATE:1', 'CHILD_103:CREATE_RESERVATION', 'CHILD_103:CREATE:att-1', 'CHILD_103', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_103', 'hash_103', '{}', NULL, ?, ?
        )
    """, (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_103', 'OP_103:CREATE:1', 'CHILD_103:CREATE:att-1', 'CHILD_103:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_103', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_103', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            1, 'hash_obs_103', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))

    # Attempt INSERT OR REPLACE with identical PK OBS_103
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: DELETE on supplier_reservation_observations is strictly prohibited"):
        conn.execute("""
            INSERT OR REPLACE INTO supplier_reservation_observations VALUES (
                'OBS_103', 'OP_103:CREATE:1', 'CHILD_103:CREATE:att-1', 'CHILD_103:CREATE_RESERVATION',
                'CREATE_RESERVATION', 'CHILD_103', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                'RESERVATION_ACTIVE', 'RES_EXP_TAMPERED', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
                1, 'hash_obs_tamper', '{}', 'WORKER_DISPATCH', '{}', ?
            )
        """, (now,))


def test_09_rejected_validation_cannot_become_authoritative():
    """9. REJECTED validation cannot become authoritative via foreign key constraint."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_104', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_104:CREATE:1', 'CHILD_104:CREATE_RESERVATION', 'CHILD_104:CREATE:att-1', 'CHILD_104', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_104', 'hash_104', '{}', NULL, ?, ?
        )
    """, (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_104', 'OP_104:CREATE:1', 'CHILD_104:CREATE:att-1', 'CHILD_104:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_104', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_104', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            1, 'hash_obs_104', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))

    # Record validation outcome as REJECTED
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES (
            'VAL_104', 'OBS_104', 'CHILD_104', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'REJECTED', 'adapter_verifier', ?, '{\"reason\": \"signature_mismatch\"}'
        )
    """, (now,))

    # Attempt to insert authoritative resolution history referencing VAL_104
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO child_authoritative_resolution_history VALUES (
                'RES_HIST_104', 'CHILD_104', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                'OBS_104', 'VAL_104', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_EXP_104', 1, 'worker_1', ?
            )
        """, (now,))


def test_10_inconclusive_validation_cannot_become_authoritative():
    """10. INCONCLUSIVE validation cannot become authoritative via foreign key constraint."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_105', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_105:CREATE:1', 'CHILD_105:CREATE_RESERVATION', 'CHILD_105:CREATE:att-1', 'CHILD_105', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_105', 'hash_105', '{}', NULL, ?, ?
        )
    """, (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_105', 'OP_105:CREATE:1', 'CHILD_105:CREATE:att-1', 'CHILD_105:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_105', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'AMBIGUOUS', NULL, NULL, NULL, NULL, NULL, NULL,
            1, 'hash_obs_105', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))

    # Record validation outcome as INCONCLUSIVE
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES (
            'VAL_105', 'OBS_105', 'CHILD_105', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'INCONCLUSIVE', 'adapter_verifier', ?, '{\"reason\": \"timeout_upstream\"}'
        )
    """, (now,))

    # Attempt to insert authoritative resolution history referencing VAL_105
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO child_authoritative_resolution_history VALUES (
                'RES_HIST_105', 'CHILD_105', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                'OBS_105', 'VAL_105', 'VALIDATED', 'AMBIGUOUS', NULL, 1, 'worker_1', ?
            )
        """, (now,))


def test_11_validated_observation_can_become_authoritative():
    """11. VALIDATED observation can become authoritative and update current pointer."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_106', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_106:CREATE:1', 'CHILD_106:CREATE_RESERVATION', 'CHILD_106:CREATE:att-1', 'CHILD_106', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_106', 'hash_106', '{}', NULL, ?, ?
        )
    """, (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_106', 'OP_106:CREATE:1', 'CHILD_106:CREATE:att-1', 'CHILD_106:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_106', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_106', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            1, 'hash_obs_106', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))

    # Record validation outcome as VALIDATED
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES (
            'VAL_106', 'OBS_106', 'CHILD_106', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'VALIDATED', 'adapter_verifier', ?, '{\"verified\": true}'
        )
    """, (now,))

    # Successfully insert authoritative resolution history
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES (
            'RES_HIST_106', 'CHILD_106', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'OBS_106', 'VAL_106', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_EXP_106', 1, 'worker_1', ?
        )
    """, (now,))

    # Successfully update current authoritative pointer
    conn.execute("""
        INSERT INTO child_current_authoritative_resolution VALUES (
            'CHILD_106', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RES_HIST_106', 'OBS_106', 'RESERVATION_ACTIVE', 'RES_EXP_106', 1, ?, 'worker_1'
        )
    """, (now,))

    row = conn.execute("SELECT * FROM child_current_authoritative_resolution WHERE child_intent_id = 'CHILD_106'").fetchone()
    assert row["active_observation_id"] == "OBS_106"
    assert row["lifecycle_status"] == "RESERVATION_ACTIVE"
    assert row["supplier_reservation_id"] == "RES_EXP_106"


def test_12_mismatched_operation_type_rejected():
    """12. Observation claiming CANCEL_RESERVATION pointing to CREATE outbox is rejected."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_107', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_107:CREATE:1', 'CHILD_107:CREATE_RESERVATION', 'CHILD_107:CREATE:att-1', 'CHILD_107', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_107', 'hash_107', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Attempt to insert observation with operation_type = CANCEL_RESERVATION pointing to OP_107:CREATE:1
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO supplier_reservation_observations VALUES (
                'OBS_107', 'OP_107:CREATE:1', 'CHILD_107:CREATE:att-1', 'CHILD_107:CREATE_RESERVATION',
                'CANCEL_RESERVATION', 'CHILD_107', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                'RESERVATION_CANCELLED', 'RES_EXP_107', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
                1, 'hash_obs_107', '{}', 'WORKER_DISPATCH', '{}', ?
            )
        """, (now,))


def test_13_mismatched_operation_attempt_relationship_rejected():
    """13. Observation with mismatched attempt_id pointing to outbox is rejected."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_108', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_108:CREATE:1', 'CHILD_108:CREATE_RESERVATION', 'CHILD_108:CREATE:att-1', 'CHILD_108', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_108', 'hash_108', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Attempt observation with attempt_id = CHILD_108:CREATE:att-99 (mismatched)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO supplier_reservation_observations VALUES (
                'OBS_108', 'OP_108:CREATE:1', 'CHILD_108:CREATE:att-99', 'CHILD_108:CREATE_RESERVATION',
                'CREATE_RESERVATION', 'CHILD_108', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                'RESERVATION_ACTIVE', 'RES_EXP_108', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
                1, 'hash_obs_108', '{}', 'WORKER_DISPATCH', '{}', ?
            )
        """, (now,))


def test_14_webhook_observation_stored_without_worker_fence():
    """14. Webhook observation stored with NULL operation/attempt and NULL worker fence."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_109', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))

    # Insert observation from WEBHOOK_NOTIFICATION
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_WEBHOOK_1', NULL, NULL, NULL,
            'CREATE_RESERVATION', 'CHILD_109', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_WH_1', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            NULL, 'hash_wh_1', '{}', 'WEBHOOK_NOTIFICATION', '{\"source_ip\": \"198.51.100.1\"}', ?
        )
    """, (now,))

    row = conn.execute("SELECT * FROM supplier_reservation_observations WHERE observation_id = 'OBS_WEBHOOK_1'").fetchone()
    assert row["observation_source"] == "WEBHOOK_NOTIFICATION"
    assert row["worker_fencing_token"] is None
    assert row["operation_id"] is None


def test_15_manual_reconciliation_observation_stored_without_worker_fence():
    """15. Manual reconciliation observation stored without worker fence."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_110', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'RECONCILIATION_REQUIRED', ?, ?)", (now, now))

    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_MANUAL_1', NULL, NULL, NULL,
            'LOOKUP_RESERVATION', 'CHILD_110', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_CANCELLED', 'RES_EXP_MAN_1', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            NULL, 'hash_man_1', '{}', 'MANUAL_RECONCILIATION', '{\"cleared_by\": \"ops_agent_42\"}', ?
        )
    """, (now,))

    row = conn.execute("SELECT * FROM supplier_reservation_observations WHERE observation_id = 'OBS_MANUAL_1'").fetchone()
    assert row["observation_source"] == "MANUAL_RECONCILIATION"
    assert row["worker_fencing_token"] is None


def test_16_cross_child_resolution_pointer_rejected():
    """16. Cross-child resolution pointer rejected via composite FK."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Child 111 setup
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_111', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_112', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))

    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_111:CREATE:1', 'CHILD_111:CREATE_RESERVATION', 'CHILD_111:CREATE:att-1', 'CHILD_111', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T13:00:00Z', 1, 1, 3, 'idemp_key_111', 'hash_111', '{}', NULL, ?, ?
        )
    """, (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_111', 'OP_111:CREATE:1', 'CHILD_111:CREATE:att-1', 'CHILD_111:CREATE_RESERVATION',
            'CREATE_RESERVATION', 'CHILD_111', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_111', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2,
            1, 'hash_obs_111', '{}', 'WORKER_DISPATCH', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES (
            'VAL_111', 'OBS_111', 'CHILD_111', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'VALIDATED', 'adapter_verifier', ?, '{}'
        )
    """, (now,))
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES (
            'RES_HIST_111', 'CHILD_111', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'OBS_111', 'VAL_111', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_EXP_111', 1, 'worker_1', ?
        )
    """, (now,))

    # Attempt to point CHILD_112 to RES_HIST_111 (owned by CHILD_111)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO child_current_authoritative_resolution VALUES (
                'CHILD_112', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                'RES_HIST_111', 'OBS_111', 'RESERVATION_ACTIVE', 'RES_EXP_111', 1, ?, 'worker_1'
            )
        """, (now,))


def test_17_expired_worker_lease_rejected():
    """17. Expired worker lease rejected during conditional outbox state transition."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Outbox with expired lease (lease_expires_at is in the past: 2026-11-13T11:59:00Z)
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_113', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_113:CREATE:1', 'CHILD_113:CREATE_RESERVATION', 'CHILD_113:CREATE:att-1', 'CHILD_113', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_1', '2026-11-13T11:59:00Z', 1, 1, 3, 'idemp_key_113', 'hash_113', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Worker attempts conditional update asserting unexpired lease (> 2026-11-13T12:00:00Z)
    cur = conn.execute("""
        UPDATE child_operation_outbox
        SET status = 'SUCCEEDED', updated_at = ?
        WHERE operation_id = 'OP_113:CREATE:1'
          AND leased_by = 'worker_1'
          AND fencing_token = 1
          AND lease_expires_at > ?
          AND status = 'IN_PROGRESS'
    """, (now, now))

    # Assert 0 rows affected
    assert cur.rowcount == 0, "Stale worker with expired lease must affect 0 rows"


def test_18_stale_fencing_token_rejected():
    """18. Stale fencing token rejected during conditional outbox state transition."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Outbox operation has been bumped to fencing_token = 2
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_114', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_114:CREATE:1', 'CHILD_114:CREATE_RESERVATION', 'CHILD_114:CREATE:att-1', 'CHILD_114', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'IN_PROGRESS',
            'worker_2', '2026-11-13T13:00:00Z', 2, 2, 3, 'idemp_key_114', 'hash_114', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Worker 1 (stale, fence 1) attempts conditional update
    cur = conn.execute("""
        UPDATE child_operation_outbox
        SET status = 'SUCCEEDED', updated_at = ?
        WHERE operation_id = 'OP_114:CREATE:1'
          AND leased_by = 'worker_1'
          AND fencing_token = 1
          AND lease_expires_at > ?
          AND status = 'IN_PROGRESS'
    """, (now, now))

    assert cur.rowcount == 0, "Worker with stale fence must affect 0 rows"


def test_19_duplicate_child_pricing_allocation_rejected():
    """19. Duplicate child pricing allocation rejected via uidx_child_pricing_bijection."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_115', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?)", (now, now))

    # Insert first pricing allocation for CHILD_115
    conn.execute("""
        INSERT INTO child_intent_pricing_snapshots VALUES (
            'PRICING_1', 'CHILD_115', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            25000, 20000, 3000, 2000, 'USD', 0, ?
        )
    """, (now,))

    # Attempt to insert second pricing allocation for same parent and CHILD_115
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute("""
            INSERT INTO child_intent_pricing_snapshots VALUES (
                'PRICING_2', 'CHILD_115', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                25000, 20000, 3000, 2000, 'USD', 0, ?
            )
        """, (now,))


def test_20_invalid_pricing_allocation_relationship_rejected():
    """20. Pricing allocation referencing mismatched agency rejected via composite FK."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_116', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?)", (now, now))

    # Attempt to insert pricing snapshot claiming AGENCY_BETA
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO child_intent_pricing_snapshots VALUES (
                'PRICING_X', 'CHILD_116', 'BKG_100', 'AGENCY_BETA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
                25000, 20000, 3000, 2000, 'USD', 0, ?
            )
        """, (now,))


# ============================================================================
# SUPPLEMENTAL ADVERSARIAL TESTS 21 - 30 (Extended Invariant Coverage)
# ============================================================================

def test_21_duplicate_active_reservation_across_children_rejected():
    """21. Two distinct children cannot simultaneously hold RESERVATION_ACTIVE for same reservation ID."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_201', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CONFIRMED', ?, ?)", (now, now))
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_202', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CONFIRMED', ?, ?)", (now, now))

    # Seed observations and resolutions
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_201', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_201', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_COLLISION_1', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_201', 'OBS_201', 'CHILD_201', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'VALIDATED', 'verifier', ?, '{}')
    """, (now,))
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES ('HIST_201', 'CHILD_201', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'OBS_201', 'VAL_201', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_COLLISION_1', 1, 'worker_1', ?)
    """, (now,))
    conn.execute("""
        INSERT INTO child_current_authoritative_resolution VALUES ('CHILD_201', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'HIST_201', 'OBS_201', 'RESERVATION_ACTIVE', 'RES_COLLISION_1', 1, ?, 'worker_1')
    """, (now,))

    # Child 202 attempts to claim same active reservation RES_COLLISION_1
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_202', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_202', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_COLLISION_1', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_202', 'OBS_202', 'CHILD_202', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'VALIDATED', 'verifier', ?, '{}')
    """, (now,))
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES ('HIST_202', 'CHILD_202', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'OBS_202', 'VAL_202', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_COLLISION_1', 1, 'worker_2', ?)
    """, (now,))

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute("""
            INSERT INTO child_current_authoritative_resolution VALUES ('CHILD_202', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'HIST_202', 'OBS_202', 'RESERVATION_ACTIVE', 'RES_COLLISION_1', 1, ?, 'worker_2')
        """, (now,))


def test_22_multiple_observations_for_same_child_and_reservation_accepted():
    """22. Multiple observations for same child and reservation reference are accepted in append-only table."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_203', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))

    # Observation 1: Active
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_203_A', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_203', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_203', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash_a', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))

    # Observation 2: Lookup confirmation of same reservation reference
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_203_B', NULL, NULL, NULL, 'LOOKUP_RESERVATION', 'CHILD_203', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_203', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash_b', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))

    # Observation 3: Subsequent cancellation of same reservation reference
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_203_C', NULL, NULL, NULL, 'CANCEL_RESERVATION', 'CHILD_203', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_CANCELLED', 'RES_EXP_203', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash_c', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))

    rows = conn.execute("SELECT observation_id FROM supplier_reservation_observations WHERE child_intent_id = 'CHILD_203'").fetchall()
    assert len(rows) == 3


def test_23_update_to_validations_table_blocked():
    """23. UPDATE to supplier_observation_validations blocked by trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_204', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_204', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_204', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_204', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_204', 'OBS_204', 'CHILD_204', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'REJECTED', 'verifier', ?, '{}')
    """, (now,))

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: UPDATE on supplier_observation_validations is strictly prohibited"):
        conn.execute("UPDATE supplier_observation_validations SET validation_status = 'VALIDATED' WHERE validation_id = 'VAL_204'")


def test_24_delete_from_validations_table_blocked():
    """24. DELETE from supplier_observation_validations blocked by trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_205', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_205', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_205', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_205', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_205', 'OBS_205', 'CHILD_205', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'REJECTED', 'verifier', ?, '{}')
    """, (now,))

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: DELETE on supplier_observation_validations is strictly prohibited"):
        conn.execute("DELETE FROM supplier_observation_validations WHERE validation_id = 'VAL_205'")


def test_25_update_to_resolution_history_blocked():
    """25. UPDATE to child_authoritative_resolution_history blocked by trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_206', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_206', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_206', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_206', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_206', 'OBS_206', 'CHILD_206', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'VALIDATED', 'verifier', ?, '{}')
    """, (now,))
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES ('HIST_206', 'CHILD_206', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'OBS_206', 'VAL_206', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_EXP_206', 1, 'worker_1', ?)
    """, (now,))

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: UPDATE on child_authoritative_resolution_history is strictly prohibited"):
        conn.execute("UPDATE child_authoritative_resolution_history SET lifecycle_status = 'RESERVATION_CANCELLED' WHERE resolution_history_id = 'HIST_206'")


def test_26_delete_from_resolution_history_blocked():
    """26. DELETE from child_authoritative_resolution_history blocked by trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_207', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_207', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_207', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_207', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_207', 'OBS_207', 'CHILD_207', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'VALIDATED', 'verifier', ?, '{}')
    """, (now,))
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES ('HIST_207', 'CHILD_207', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'OBS_207', 'VAL_207', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_EXP_207', 1, 'worker_1', ?)
    """, (now,))

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ImmutabilityViolation: DELETE on child_authoritative_resolution_history is strictly prohibited"):
        conn.execute("DELETE FROM child_authoritative_resolution_history WHERE resolution_history_id = 'HIST_207'")


def test_27_payment_outbox_cross_agency_intent_rejected():
    """27. Payment outbox referencing payment intent of different agency rejected via composite FK."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Attempt to link payment outbox for BKG_200 (AGENCY_BETA) with PI_100 (owned by AGENCY_ALPHA)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("""
            INSERT INTO parent_payment_outbox VALUES (
                'PAY_OP_X', 'BKG_200', 'AGENCY_BETA', 'PI_100', 'PAY_STRIPE', 'STRIPE_GLOBAL',
                'CAPTURE_CARD', 'PENDING', NULL, NULL, 1, 0, 'idemp_pay_x', 'hash_p', NULL, NULL, NULL, ?, ?
            )
        """, (now, now))


def test_28_null_idempotency_key_enforced_for_neither_and_lookup_only():
    """28. Supplying non-null supplier_idempotency_key on NEITHER or LOOKUP_ONLY outbox is rejected by CHECK."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_208', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?)", (now, now))

    # Attempt to insert NEITHER outbox with non-null supplier_idempotency_key
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""
            INSERT INTO child_operation_outbox VALUES (
                'OP_208:CREATE:1', 'CHILD_208:CREATE_RESERVATION', 'CHILD_208:CREATE:att-1', 'CHILD_208', 'BKG_100', 'AGENCY_ALPHA',
                'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'NEITHER', 'PENDING',
                NULL, NULL, 1, 0, 3, 'ILLEGAL_KEY_FOR_NEITHER', 'hash_208', '{}', NULL, ?, ?
            )
        """, (now, now))

    # Attempt to insert LOOKUP_ONLY outbox with non-null supplier_idempotency_key
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""
            INSERT INTO child_operation_outbox VALUES (
                'OP_208:CREATE:2', 'CHILD_208:CREATE_RESERVATION', 'CHILD_208:CREATE:att-2', 'CHILD_208', 'BKG_100', 'AGENCY_ALPHA',
                'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'LOOKUP_ONLY', 'PENDING',
                NULL, NULL, 1, 0, 3, 'ILLEGAL_KEY_FOR_LOOKUP_ONLY', 'hash_208', '{}', NULL, ?, ?
            )
        """, (now, now))


def test_29_mandatory_idempotency_key_enforced_for_idempotent_with_lookup():
    """29. Missing supplier_idempotency_key on IDEMPOTENT_WITH_LOOKUP outbox is rejected by CHECK."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_209', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?)", (now, now))

    # Attempt to insert IDEMPOTENT_WITH_LOOKUP outbox with NULL supplier_idempotency_key
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""
            INSERT INTO child_operation_outbox VALUES (
                'OP_209:CREATE:1', 'CHILD_209:CREATE_RESERVATION', 'CHILD_209:CREATE:att-1', 'CHILD_209', 'BKG_100', 'AGENCY_ALPHA',
                'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'IDEMPOTENT_WITH_LOOKUP', 'PENDING',
                NULL, NULL, 1, 0, 3, NULL, 'hash_209', '{}', NULL, ?, ?
            )
        """, (now, now))


def test_30_global_supplier_account_accessible_without_agency_grant():
    """30. Global supplier account (is_global = 1) is accessible by any agency without explicit grant."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # AGENCY_BETA accesses SUP_EXPEDIA / ACC_GLOBAL_1 (no entry in agency_provider_account_authorizations)
    conn.execute("""
        INSERT INTO child_supplier_intents VALUES (
            'CHILD_BETA_EXP', 'BKG_200', 'AGENCY_BETA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?
        )
    """, (now, now))

    row = conn.execute("SELECT * FROM child_supplier_intents WHERE child_intent_id = 'CHILD_BETA_EXP'").fetchone()
    assert row["supplier_id"] == "SUP_EXPEDIA"
    assert row["provider_account_id"] == "ACC_GLOBAL_1"


def test_31_account_revocation_before_dispatch_rejected():
    """31. Account revocation after outbox creation blocks dispatch to IN_PROGRESS via trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Insert child intent with ACC_ALPHA_PRIVATE (enabled initially)
    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_301', 'BKG_100', 'AGENCY_ALPHA', 'SUP_HOTELBEDS', 'ACC_ALPHA_PRIVATE', 'PENDING', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_301:CREATE:1', 'CHILD_301:CREATE', 'OP_301:att-1', 'CHILD_301', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_HOTELBEDS', 'ACC_ALPHA_PRIVATE', 'CREATE_RESERVATION', 'NEITHER', 'PENDING',
            NULL, NULL, 1, 0, 3, NULL, 'hash_301', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Revoke account authorization while operation is PENDING
    conn.execute("""
        UPDATE agency_provider_account_authorizations 
        SET is_enabled = 0 
        WHERE agency_id = 'AGENCY_ALPHA' AND supplier_id = 'SUP_HOTELBEDS' AND provider_account_id = 'ACC_ALPHA_PRIVATE'
    """)

    # Attempt to dispatch / transition outbox to IN_PROGRESS
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Supplier provider account authorization was revoked before outbox dispatch"):
        conn.execute("""
            UPDATE child_operation_outbox 
            SET status = 'IN_PROGRESS', leased_by = 'worker_1', lease_expires_at = '2026-11-13T13:00:00Z', attempts = 1
            WHERE operation_id = 'OP_301:CREATE:1'
        """)


def test_32_payment_account_revocation_before_dispatch_rejected():
    """32. Payment account revocation after outbox creation blocks dispatch to IN_PROGRESS via trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    # Insert payment outbox with STRIPE_ALPHA_CUSTOM (enabled initially)
    conn.execute("""
        INSERT INTO parent_payment_outbox VALUES (
            'PAY_OP_302', 'BKG_100', 'AGENCY_ALPHA', 'PI_100', 'PAY_STRIPE', 'STRIPE_ALPHA_CUSTOM',
            'CAPTURE_CARD', 'PENDING', NULL, NULL, 1, 0, 'idemp_302', 'hash_302', NULL, NULL, NULL, ?, ?
        )
    """, (now, now))

    # Revoke payment account authorization while operation is PENDING
    conn.execute("""
        UPDATE agency_payment_account_authorizations 
        SET is_enabled = 0 
        WHERE agency_id = 'AGENCY_ALPHA' AND payment_provider_id = 'PAY_STRIPE' AND provider_account_id = 'STRIPE_ALPHA_CUSTOM'
    """)

    # Attempt to transition payment outbox to IN_PROGRESS
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Payment provider account authorization was revoked before outbox dispatch"):
        conn.execute("""
            UPDATE parent_payment_outbox 
            SET status = 'IN_PROGRESS', leased_by = 'worker_pay_1', lease_expires_at = '2026-11-13T13:00:00Z', attempts = 1
            WHERE payment_operation_id = 'PAY_OP_302'
        """)


def test_33_stale_fencing_token_resolution_pointer_rejected():
    """33. Stale fencing token update to child_current_authoritative_resolution rejected by trigger."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_303', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'DISPATCHED', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO supplier_reservation_observations VALUES (
            'OBS_303A', NULL, NULL, NULL, 'CREATE_RESERVATION', 'CHILD_303', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'RESERVATION_ACTIVE', 'RES_EXP_303', 'HTL_MUMBAI', '2026-12-01', '2026-12-03', 'DLX', 2, NULL, 'hash', '{}', 'WEBHOOK_NOTIFICATION', '{}', ?
        )
    """, (now,))
    conn.execute("""
        INSERT INTO supplier_observation_validations VALUES ('VAL_303A', 'OBS_303A', 'CHILD_303', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'VALIDATED', 'verifier', ?, '{}')
    """, (now,))
    conn.execute("""
        INSERT INTO child_authoritative_resolution_history VALUES ('HIST_303A', 'CHILD_303', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'OBS_303A', 'VAL_303A', 'VALIDATED', 'RESERVATION_ACTIVE', 'RES_EXP_303', 5, 'worker_5', ?)
    """, (now,))

    # Insert current resolution pointer with fencing_token = 5
    conn.execute("""
        INSERT INTO child_current_authoritative_resolution VALUES (
            'CHILD_303', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1',
            'HIST_303A', 'OBS_303A', 'RESERVATION_ACTIVE', 'RES_EXP_303', 5, ?, 'worker_5'
        )
    """, (now,))

    # Attempt to regress fencing_token to 3
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="ConcurrencyViolation: Stale fencing token cannot overwrite current authoritative resolution"):
        conn.execute("""
            UPDATE child_current_authoritative_resolution 
            SET fencing_token = 3, updated_by = 'stale_worker_3' 
            WHERE child_intent_id = 'CHILD_303'
        """)


def test_34_global_account_revocation_blocks_outbox_dispatch():
    """34. Deactivation of global account (is_active = 0) blocks outbox dispatch to IN_PROGRESS."""
    conn = reset_test_db()
    seed_base_environment(conn)
    now = "2026-11-13T12:00:00Z"

    conn.execute("INSERT INTO child_supplier_intents VALUES ('CHILD_304', 'BKG_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'PENDING', ?, ?)", (now, now))
    conn.execute("""
        INSERT INTO child_operation_outbox VALUES (
            'OP_304:CREATE:1', 'CHILD_304:CREATE', 'OP_304:att-1', 'CHILD_304', 'BKG_100', 'AGENCY_ALPHA',
            'SUP_EXPEDIA', 'ACC_GLOBAL_1', 'CREATE_RESERVATION', 'NEITHER', 'PENDING',
            NULL, NULL, 1, 0, 3, NULL, 'hash_304', '{}', NULL, ?, ?
        )
    """, (now, now))

    # Deactivate global account
    conn.execute("UPDATE supplier_provider_accounts SET is_active = 0 WHERE supplier_id = 'SUP_EXPEDIA' AND provider_account_id = 'ACC_GLOBAL_1'")

    # Attempt to dispatch
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="AuthorizationError: Supplier provider account authorization was revoked before outbox dispatch"):
        conn.execute("""
            UPDATE child_operation_outbox 
            SET status = 'IN_PROGRESS', leased_by = 'worker_1', lease_expires_at = '2026-11-13T13:00:00Z', attempts = 1
            WHERE operation_id = 'OP_304:CREATE:1'
        """)


# ============================================================================
# RUNNER EXECUTION
# ============================================================================

def run_all_tests():
    test_functions = [
        test_01_invalid_child_parent_relationship_rejected,
        test_02_unauthorized_supplier_account_rejected,
        test_03_disabled_supplier_authorization_rejected_at_dispatch,
        test_04_unauthorized_payment_account_rejected,
        test_05_disabled_payment_authorization_rejected_at_dispatch,
        test_06_update_to_immutable_observation_blocked,
        test_07_delete_from_immutable_observation_blocked,
        test_08_insert_or_replace_cannot_bypass_immutability,
        test_09_rejected_validation_cannot_become_authoritative,
        test_10_inconclusive_validation_cannot_become_authoritative,
        test_11_validated_observation_can_become_authoritative,
        test_12_mismatched_operation_type_rejected,
        test_13_mismatched_operation_attempt_relationship_rejected,
        test_14_webhook_observation_stored_without_worker_fence,
        test_15_manual_reconciliation_observation_stored_without_worker_fence,
        test_16_cross_child_resolution_pointer_rejected,
        test_17_expired_worker_lease_rejected,
        test_18_stale_fencing_token_rejected,
        test_19_duplicate_child_pricing_allocation_rejected,
        test_20_invalid_pricing_allocation_relationship_rejected,
        test_21_duplicate_active_reservation_across_children_rejected,
        test_22_multiple_observations_for_same_child_and_reservation_accepted,
        test_23_update_to_validations_table_blocked,
        test_24_delete_from_validations_table_blocked,
        test_25_update_to_resolution_history_blocked,
        test_26_delete_from_resolution_history_blocked,
        test_27_payment_outbox_cross_agency_intent_rejected,
        test_28_null_idempotency_key_enforced_for_neither_and_lookup_only,
        test_29_mandatory_idempotency_key_enforced_for_idempotent_with_lookup,
        test_30_global_supplier_account_accessible_without_agency_grant,
        test_31_account_revocation_before_dispatch_rejected,
        test_32_payment_account_revocation_before_dispatch_rejected,
        test_33_stale_fencing_token_resolution_pointer_rejected,
        test_34_global_account_revocation_blocks_outbox_dispatch,
    ]

    results = []
    print(f"Executing {len(test_functions)} adversarial database tests...")
    
    for fn in test_functions:
        test_name = fn.__name__
        try:
            fn()
            results.append((test_name, "PASSED", None))
            print(f"  [PASS] {test_name}")
        except Exception as exc:
            results.append((test_name, "FAILED", str(exc)))
            print(f"  [FAIL] {test_name}: {exc}")

    passed = sum(1 for _, status, _ in results if status == "PASSED")
    failed = sum(1 for _, status, _ in results if status == "FAILED")
    print(f"\nTest Summary: {passed} passed, {failed} failed out of {len(results)} total tests.")
    return results


if __name__ == "__main__":
    run_all_tests()

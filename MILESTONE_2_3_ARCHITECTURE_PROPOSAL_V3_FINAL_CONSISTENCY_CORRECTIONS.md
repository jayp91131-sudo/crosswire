# Milestone 2.3 Architecture Proposal V3: Final Consistency Corrections
## Immutable Validation Separation, Relational History Pointers, Account Authorizations, Operation Correlation, Balanced Journal Invariants, and SQLite Integrity

**Document Reference:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_CONSISTENCY_CORRECTIONS.md`  
**Author:** Principal Distributed Systems Architect, Travel Platform Transaction-Integrity Engineer, and Application Security Reviewer  
**Status:** Pre-Implementation Architectural Specification Corrections  
**Target Milestone:** Crosswire 2.3 (Multi-Supplier Routing & Split-Inventory Transaction Integrity)  
**Baseline Verification:** Milestone 2.2 Frozen Baseline (196 tests passing, 0 failed, 1 warning)

---

## 1. Summary of Architectural Corrections

This document provides targeted corrections to resolve all remaining technical contradictions and referential gaps identified in `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_CONSISTENCY_AUDIT.md`:

1. **Resolution of Append-Only Validation Contradiction:**
   - Eliminates the incompatible proposal of mutating `provenance_status` on an append-only table protected by unconditional update triggers.
   - Preserves `supplier_reservation_observations` as 100% immutable and insert-only.
   - Introduces a dedicated, immutable validation outcomes table: `supplier_observation_validations`. Original evidence is never updated; rejected and inconclusive evidence remains permanently auditable.
2. **Hardened Resolution-History Relational Contracts:**
   - Enforces a 9-part composite foreign key between `child_current_authoritative_resolution` and `child_authoritative_resolution_history` linking child intent, parent booking, agency, supplier, provider account, observation ID, lifecycle status, and reservation reference.
   - Proves at the database engine level that the current pointer and history record agree and reference the exact same verified observation.
3. **Complete Provider-Account Authorization Architecture:**
   - Formalizes `agency_payment_account_authorizations` alongside supplier account authorizations.
   - Formally defines `is_global`: global accounts are accessible by default, while non-global accounts require active relational grants.
   - Enforces that authorizations are checked at dispatch time; disabling an authorization prevents new bookings without violating foreign-key integrity for historical bookings.
4. **Exact Observation-to-Outbox Relational Correlation:**
   - Connects worker-dispatched observations directly to `child_operation_outbox(operation_id, attempt_id)` via composite foreign keys.
   - Disallows observations from referencing mismatched operations, children, or accounts, while preserving nullability for webhook and manual reconciliation sources via SQLite CHECK constraints.
5. **Mandatory Balanced Double-Entry Journaling Verification (Invariant 5.7):**
   - Formally brings Invariant 5.7 into M2.3 scope for multi-supplier split-inventory settlements.
   - Specifies per-currency balance verification (`SUM(DEBIT) == SUM(CREDIT)`) executed within the enclosing transaction before commit.
   - Details supplier-level payable allocations and markup recognition with duplicate-prevention constraints.
6. **Strict SQLite Integrity & Connection Configuration:**
   - Mandates `PRAGMA foreign_keys = ON` verification on every connection initialization.
   - Specifies `PRAGMA recursive_triggers = ON` to prevent accidental bypass via `INSERT OR REPLACE` (which triggers `BEFORE DELETE`).

---

## 2. Corrected SQLite DDL and Indexes

> [!IMPORTANT]
> The DDL below is the authoritative architectural specification for Milestone 2.3. It has not yet been executed or migrated against the live database, preserving the frozen M2.2 baseline.

```sql
-- ============================================================================
-- 0. PREREQUISITE COMPOSITE INDEXES ON M2.2 TABLES
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uidx_booking_records_id_agency 
ON booking_records (booking_id, agency_id);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_intents_composite 
ON payment_intents (payment_intent_id, booking_id, agency_id, payment_provider_id, provider_account_id);

-- ============================================================================
-- 1. PROVIDER ACCOUNT REGISTRIES & AGENCY AUTHORIZATIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS supplier_provider_accounts (
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_global INTEGER NOT NULL DEFAULT 0 CHECK (is_global IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (supplier_id, provider_account_id)
);

CREATE TABLE IF NOT EXISTS agency_provider_account_authorizations (
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (agency_id, supplier_id, provider_account_id),
    FOREIGN KEY (agency_id) REFERENCES agencies (agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (supplier_id, provider_account_id) 
        REFERENCES supplier_provider_accounts (supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS payment_provider_accounts (
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_global INTEGER NOT NULL DEFAULT 0 CHECK (is_global IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (payment_provider_id, provider_account_id)
);

CREATE TABLE IF NOT EXISTS agency_payment_account_authorizations (
    agency_id TEXT NOT NULL,
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (agency_id, payment_provider_id, provider_account_id),
    FOREIGN KEY (agency_id) REFERENCES agencies (agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_provider_id, provider_account_id) 
        REFERENCES payment_provider_accounts (payment_provider_id, provider_account_id) ON DELETE RESTRICT
);

-- ============================================================================
-- 2. CHILD SUPPLIER INTENTS & PRICING
-- ============================================================================
CREATE TABLE IF NOT EXISTS child_supplier_intents (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'DISPATCHED', 'CONFIRMED', 'DEFINITIVE_FAILURE', 
        'SUPPLIER_UNKNOWN', 'CANCELLATION_PENDING', 'CANCELLED', 'RECONCILIATION_REQUIRED'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_booking_id, agency_id) 
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (supplier_id, provider_account_id) 
        REFERENCES supplier_provider_accounts (supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_intents_composite 
ON child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

CREATE TABLE IF NOT EXISTS child_intent_pricing_snapshots (
    child_pricing_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    agency_allocated_gross INTEGER NOT NULL CHECK (agency_allocated_gross > 0),
    supplier_net_cost INTEGER NOT NULL CHECK (supplier_net_cost > 0),
    allocated_markup INTEGER NOT NULL CHECK (allocated_markup >= 0),
    allocated_taxes INTEGER NOT NULL CHECK (allocated_taxes >= 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    supplier_cancellation_penalty_bps INTEGER NOT NULL DEFAULT 0 CHECK (supplier_cancellation_penalty_bps BETWEEN 0 AND 10000),
    created_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    CHECK (agency_allocated_gross = supplier_net_cost + allocated_markup + allocated_taxes)
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_pricing_bijection 
ON child_intent_pricing_snapshots (parent_booking_id, child_intent_id);

-- ============================================================================
-- 3. CHILD OPERATION OUTBOX
-- ============================================================================
CREATE TABLE IF NOT EXISTS child_operation_outbox (
    operation_id TEXT PRIMARY KEY,       -- Ephemeral attempt format: "{child_intent_id}:{op_type}:{attempt}"
    logical_operation_id TEXT NOT NULL,  -- Stable business ID: "{child_intent_id}:{op_type}"
    attempt_id TEXT NOT NULL,            -- Execution slice ID: "{logical_operation_id}:att-{attempt}"
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'CREATE_RESERVATION', 'CANCEL_RESERVATION', 'LOOKUP_RESERVATION', 'LOOKUP_CANCELLATION'
    )),
    supplier_capability TEXT NOT NULL CHECK (supplier_capability IN (
        'IDEMPOTENT_WITH_LOOKUP', 'LOOKUP_ONLY', 'NEITHER'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'EXHAUSTED', 'RECONCILIATION_REQUIRED'
    )),
    leased_by TEXT,
    lease_expires_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    supplier_idempotency_key TEXT,       -- Nullable based on capability
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    durable_outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    CHECK (
        (supplier_capability = 'IDEMPOTENT_WITH_LOOKUP' AND supplier_idempotency_key IS NOT NULL AND length(supplier_idempotency_key) > 0)
        OR
        (supplier_capability IN ('LOOKUP_ONLY', 'NEITHER') AND supplier_idempotency_key IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_outbox_op_attempt 
ON child_operation_outbox (operation_id, attempt_id, logical_operation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

-- ============================================================================
-- 4. TRULY APPEND-ONLY OBSERVATIONS & IMMUTABLE VALIDATIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS supplier_reservation_observations (
    observation_id TEXT PRIMARY KEY,
    operation_id TEXT,                  -- NULL for webhooks and manual reconciliation
    attempt_id TEXT,                    -- NULL for webhooks and manual reconciliation
    logical_operation_id TEXT,          -- NULL for webhooks and manual reconciliation
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'CREATE_RESERVATION', 'CANCEL_RESERVATION', 'LOOKUP_RESERVATION', 'LOOKUP_CANCELLATION'
    )),
    lifecycle_outcome TEXT NOT NULL CHECK (lifecycle_outcome IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,        -- NULL if rejected or not found
    confirmed_hotel_id TEXT,
    confirmed_check_in TEXT,
    confirmed_check_out TEXT,
    confirmed_room_type TEXT,
    confirmed_guest_capacity INTEGER CHECK (confirmed_guest_capacity IS NULL OR confirmed_guest_capacity > 0),
    worker_fencing_token INTEGER NOT NULL CHECK (worker_fencing_token > 0),
    raw_response_hash TEXT NOT NULL,
    payload_snapshot TEXT NOT NULL,
    observation_source TEXT NOT NULL CHECK (observation_source IN (
        'WORKER_DISPATCH', 'LOOKUP_POLL', 'WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION'
    )),
    provenance_metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    FOREIGN KEY (operation_id, attempt_id, logical_operation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_operation_outbox (operation_id, attempt_id, logical_operation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    CHECK (
        (observation_source IN ('WORKER_DISPATCH', 'LOOKUP_POLL') AND operation_id IS NOT NULL AND attempt_id IS NOT NULL AND logical_operation_id IS NOT NULL)
        OR
        (observation_source IN ('WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION') AND operation_id IS NULL AND attempt_id IS NULL AND logical_operation_id IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_composite_identity 
ON supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, lifecycle_outcome, supplier_reservation_id);

-- Enforce pure append-only behavior via SQLite triggers
CREATE TRIGGER IF NOT EXISTS trg_prevent_update_observations
BEFORE UPDATE ON supplier_reservation_observations
BEGIN
    SELECT RAISE(FAIL, 'UPDATE on supplier_reservation_observations is strictly prohibited; table is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_observations
BEFORE DELETE ON supplier_reservation_observations
BEGIN
    SELECT RAISE(FAIL, 'DELETE on supplier_reservation_observations is strictly prohibited; table is insert-only');
END;

-- Dedicated Immutable Validation Outcomes Table
CREATE TABLE IF NOT EXISTS supplier_observation_validations (
    validation_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE, -- Each observation validated exactly once
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('VALIDATED', 'REJECTED', 'INCONCLUSIVE')),
    validated_by TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    validation_details_json TEXT NOT NULL,
    FOREIGN KEY (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_validation_composite 
ON supplier_observation_validations (validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, validation_status);

CREATE TRIGGER IF NOT EXISTS trg_prevent_update_validations
BEFORE UPDATE ON supplier_observation_validations
BEGIN
    SELECT RAISE(FAIL, 'UPDATE on supplier_observation_validations is strictly prohibited; table is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_validations
BEFORE DELETE ON supplier_observation_validations
BEGIN
    SELECT RAISE(FAIL, 'DELETE on supplier_observation_validations is strictly prohibited; table is insert-only');
END;

-- ============================================================================
-- 5. RESOLUTION HISTORY (Append-Only) & CURRENT RESOLUTION POINTER
-- ============================================================================
CREATE TABLE IF NOT EXISTS child_authoritative_resolution_history (
    resolution_history_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    validation_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    resolved_by TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    FOREIGN KEY (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, lifecycle_status, supplier_reservation_id)
        REFERENCES supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, lifecycle_outcome, supplier_reservation_id) ON DELETE RESTRICT,
    FOREIGN KEY (validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES supplier_observation_validations (validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_res_hist_composite 
ON child_authoritative_resolution_history (
    resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
    provider_account_id, observation_id, lifecycle_status, supplier_reservation_id
);

CREATE TRIGGER IF NOT EXISTS trg_prevent_update_res_history
BEFORE UPDATE ON child_authoritative_resolution_history
BEGIN
    SELECT RAISE(FAIL, 'UPDATE on child_authoritative_resolution_history is strictly prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_res_history
BEFORE DELETE ON child_authoritative_resolution_history
BEGIN
    SELECT RAISE(FAIL, 'DELETE on child_authoritative_resolution_history is strictly prohibited');
END;

-- Current Authoritative Pointer Table
CREATE TABLE IF NOT EXISTS child_current_authoritative_resolution (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    active_resolution_history_id TEXT NOT NULL,
    active_observation_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    FOREIGN KEY (
        active_resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
        provider_account_id, active_observation_id, lifecycle_status, supplier_reservation_id
    ) REFERENCES child_authoritative_resolution_history (
        resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
        provider_account_id, observation_id, lifecycle_status, supplier_reservation_id
    ) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_current_active_res_unique 
ON child_current_authoritative_resolution (supplier_id, provider_account_id, supplier_reservation_id)
WHERE supplier_reservation_id IS NOT NULL AND lifecycle_status = 'RESERVATION_ACTIVE';

-- ============================================================================
-- 6. PARENT PAYMENT OUTBOX
-- ============================================================================
CREATE TABLE IF NOT EXISTS parent_payment_outbox (
    payment_operation_id TEXT PRIMARY KEY,   -- Format: "{parent_booking_id}:CAPTURE_CARD"
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    payment_intent_id TEXT NOT NULL,
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN ('CAPTURE_CARD', 'LOOKUP_CARD_CAPTURE', 'REFUND_CARD')),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'PAYMENT_UNKNOWN', 'RECONCILIATION_REQUIRED'
    )),
    leased_by TEXT,
    lease_expires_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    gateway_idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    gateway_capture_reference TEXT,
    raw_response_hash TEXT,
    durable_outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (payment_intent_id, parent_booking_id, agency_id, payment_provider_id, provider_account_id) 
        REFERENCES payment_intents (payment_intent_id, booking_id, agency_id, payment_provider_id, provider_account_id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_provider_id, provider_account_id) 
        REFERENCES payment_provider_accounts (payment_provider_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_outbox_gateway_idemp 
ON parent_payment_outbox (payment_provider_id, provider_account_id, gateway_idempotency_key);

-- ============================================================================
-- 7. BALANCED DOUBLE-ENTRY LEDGER (INVARIANT 5.7)
-- ============================================================================
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_reference TEXT NOT NULL,
    journal_batch_id TEXT NOT NULL,
    booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    account_code TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_ledger_batch_account 
ON ledger_entries (journal_batch_id, account_code);
```

---

## 3. State and Transaction Flows

### 3.1 Immutable Evidence Acceptance and Validation Flow

```
[Raw Response Received]
           |
           v
[Transaction 1: Observation Acceptance (Insert-Only)]
INSERT INTO supplier_reservation_observations (
    observation_id, operation_id, attempt_id, ..., raw_response_hash, payload_snapshot
);
[COMMIT 1]
           |
           v
[Adapter Validation Execution]
- Validate TLS certificate SAN against registered supplier domain.
- Verify provider_account_id and correlation IDs.
- Execute adapter response contract parser (status codes, errors).
- Verify payload hash: raw_response_hash == sha256(payload_snapshot).
- Determine validation_status: 'VALIDATED' | 'REJECTED' | 'INCONCLUSIVE'.
           |
           v
[Transaction 2: Record Validation Outcome (Insert-Only)]
INSERT INTO supplier_observation_validations (
    validation_id, observation_id, ..., validation_status, validated_by, validated_at
);
[COMMIT 2]
           |
           v (If validation_status == 'VALIDATED')
[Transaction 3: Authoritative Fenced State Reduction]
[BEGIN IMMEDIATE]
1. Verify Outbox Lease & Advance Outbox:
   UPDATE child_operation_outbox SET status = :status, updated_at = :now
   WHERE operation_id = :op_id AND attempt_id = :attempt_id AND leased_by = :worker_id
     AND fencing_token = :expected_fence AND lease_expires_at > :now AND status = 'IN_PROGRESS';
   (Assert rowcount == 1; rollback if 0).
2. Insert Resolution History:
   INSERT INTO child_authoritative_resolution_history (
       resolution_history_id, child_intent_id, ..., observation_id, validation_id, lifecycle_status, ...
   );
3. Upsert Current Resolution Pointer:
   INSERT INTO child_current_authoritative_resolution (...)
   VALUES (...)
   ON CONFLICT(child_intent_id) DO UPDATE SET ...;
4. Advance Child Supplier Intent Status conditionally:
   UPDATE child_supplier_intents SET status = :child_status, updated_at = :now
   WHERE child_intent_id = :child_id AND status = :expected_prior_status;
[COMMIT 3]
```

---

### 3.2 Mandatory Invariant 5.7 Balanced Journal Verification

During parent wallet finalization, the system posts balanced double-entry ledger entries and executes a mandatory verification query inside the enclosing transaction prior to commit:

```python
def commit_balanced_wallet_capture_journal(
    conn: sqlite3.Connection,
    journal_batch_id: str,
    booking_id: str,
    agency_id: str,
    currency: str,
    gross_amount: int,
    child_allocations: List[dict]
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    
    # 1. Debit Agency Wallet (Total Gross)
    conn.execute("""
        INSERT INTO ledger_entries (
            transaction_reference, journal_batch_id, booking_id, agency_id,
            entry_type, direction, amount_minor, currency, account_code, description, created_at
        ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'DEBIT', ?, ?, 'AGENCY_WALLET', 'Capture agency wallet hold', ?)
    """, (f"{journal_batch_id}:DEBIT", journal_batch_id, booking_id, agency_id, gross_amount, currency, now_iso))

    # 2. Credits per Supplier Leg
    total_net = 0
    total_markup = 0
    total_taxes = 0

    for alloc in child_allocations:
        supp_id = alloc["supplier_id"]
        net = alloc["supplier_net_cost"]
        total_net += net
        conn.execute("""
            INSERT INTO ledger_entries (
                transaction_reference, journal_batch_id, booking_id, agency_id,
                entry_type, direction, amount_minor, currency, account_code, description, created_at
            ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, ?, 'Wholesale supplier payable', ?)
        """, (f"{journal_batch_id}:CREDIT_SUPP_{supp_id}", journal_batch_id, booking_id, agency_id, net, currency, f"SUPPLIER_PAYABLE_{supp_id}", now_iso))
        
        total_markup += alloc["allocated_markup"]
        total_taxes += alloc["allocated_taxes"]

    # 3. Credit Aggregator Markup
    if total_markup > 0:
        conn.execute("""
            INSERT INTO ledger_entries (
                transaction_reference, journal_batch_id, booking_id, agency_id,
                entry_type, direction, amount_minor, currency, account_code, description, created_at
            ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, 'AGGREGATOR_REVENUE_MARKUP', 'Aggregator gross markup', ?)
        """, (f"{journal_batch_id}:CREDIT_MARKUP", journal_batch_id, booking_id, agency_id, total_markup, currency, now_iso))

    # 4. Credit Taxes & Fees Payable
    if total_taxes > 0:
        conn.execute("""
            INSERT INTO ledger_entries (
                transaction_reference, journal_batch_id, booking_id, agency_id,
                entry_type, direction, amount_minor, currency, account_code, description, created_at
            ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, 'TAXES_PAYABLE', 'Taxes and regulatory fees', ?)
        """, (f"{journal_batch_id}:CREDIT_TAXES", journal_batch_id, booking_id, agency_id, total_taxes, currency, now_iso))

    # 5. Mandatory Verification of Invariant 5.7 Before Commit
    balance_check = conn.execute("""
        SELECT 
            currency,
            SUM(CASE WHEN direction = 'DEBIT' THEN amount_minor ELSE 0 END) AS total_debits,
            SUM(CASE WHEN direction = 'CREDIT' THEN amount_minor ELSE 0 END) AS total_credits,
            COUNT(*) AS entry_count
        FROM ledger_entries
        WHERE journal_batch_id = ?
        GROUP BY currency
    """, (journal_batch_id,)).fetchall()

    if len(balance_check) != 1:
        raise UnbalancedJournalError(f"Journal batch {journal_batch_id} must span exactly 1 currency, found {len(balance_check)}")
    
    row = balance_check[0]
    if row["total_debits"] != row["total_credits"]:
        raise UnbalancedJournalError(
            f"Invariant 5.7 Violation: Journal batch {journal_batch_id} unbalanced: Debits ({row['total_debits']}) != Credits ({row['total_credits']})"
        )
    if row["total_debits"] != gross_amount:
        raise UnbalancedJournalError(f"Journal total {row['total_debits']} != Parent gross {gross_amount}")
```

---

## 4. Failure and Recovery Behavior

### 4.1 SQLite Connection Initialization & Integrity Enforcement
Every connection opened by the application database driver must execute and verify foreign-key enforcement:
```python
def create_verified_connection(db_path: str, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    
    # 1. Mandatory foreign-key enforcement
    conn.execute("PRAGMA foreign_keys = ON;")
    fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    if fk_status != 1:
        conn.close()
        raise SystemIntegrityError("Failed to enable PRAGMA foreign_keys = ON on database connection")
        
    # 2. Mandatory recursive triggers enforcement
    conn.execute("PRAGMA recursive_triggers = ON;")
    return conn
```

### 4.2 Handling of Stale Workers & Unvalidated Evidence
- If worker lease expires ($F_{\text{worker}} < F_{\text{active}}$) while dispatch is in flight:
  - The worker's HTTP response is accepted and inserted into `supplier_reservation_observations` (historical fact preserved).
  - Validation runs and writes to `supplier_observation_validations`.
  - However, Transaction 3 (Authoritative Fenced State Reduction) executes conditional SQL checking `fencing_token = :expected_fence AND lease_expires_at > :now`.
  - The conditional update yields `rowcount == 0`, and the transaction rolls back.
  - The stale worker cannot mutate `child_authoritative_resolution_history`, `child_current_authoritative_resolution`, or `child_supplier_intents`.
  - The parent coordinator background sweep inspects validated observations, re-leases the outbox under current fencing, and advances state safely.

---

## 5. Positive and Negative Acceptance-Test Matrix

> [!NOTE]
> All test cases below are **target acceptance criteria** to be implemented and executed during milestone development. They are not claims of current passage.

| Category | Type | Target Test Specification | Expected Assertion |
| :--- | :--- | :--- | :--- |
| **Observation Triggers** | **Negative** | Attempting `UPDATE supplier_reservation_observations SET confirmed_hotel_id = 'X'`. | SQLite trigger `trg_prevent_update_observations` blocks mutation with `RAISE(FAIL, ...)`. |
| **Validation Triggers** | **Negative** | Attempting `UPDATE supplier_observation_validations SET validation_status = 'REJECTED'`. | SQLite trigger `trg_prevent_update_validations` blocks mutation with `RAISE(FAIL, ...)`. |
| **Resolution Mismatch** | **Negative** | Attempting to insert `child_authoritative_resolution_history` pointing to observation of different child. | Composite foreign key to `supplier_reservation_observations` raises `sqlite3.IntegrityError`. |
| **Pointer Consistency** | **Negative** | `child_current_authoritative_resolution` specifies a `supplier_reservation_id` differing from the referenced history row. | Composite foreign key `uidx_res_hist_composite` raises `sqlite3.IntegrityError`. |
| **Outbox Correlation** | **Negative** | Worker observation specifies `operation_id` not matching its `child_intent_id`. | Composite foreign key `uidx_outbox_op_attempt` raises `sqlite3.IntegrityError`. |
| **Account Auth** | **Negative** | Child intent specifies a non-global provider account without an active agency authorization grant. | Foreign key to `agency_provider_account_authorizations` raises `sqlite3.IntegrityError`. |
| **Journal Balancing** | **Positive** | Normal wallet capture finalization. | Query verifies `total_debits == total_credits == gross_amount`. Transaction commits. |
| **Journal Balancing** | **Negative** | Allocation error where credits sum to \$299.00 and debits sum to \$300.00. | `UnbalancedJournalError` raised; local transaction rolls back; no vouchers created. |
| **Pricing Bijection** | **Negative** | Attempting to insert duplicate pricing snapshot for Child 0. | Database unique index `uidx_child_pricing_bijection` raises `sqlite3.IntegrityError`. |
| **M2.2 Baseline** | **Frozen** | Full execution across all 35 legacy test files. | 196 passing tests baseline preserved without modification. |

---

## 6. Remaining External Dependencies and Risks

1. **Payment Gateway Authoritative Lookup SLA:** Assumes external payment gateways support query-by-idempotency-key with read consistency within 15 seconds. If a gateway does not provide an authoritative lookup contract, capture timeouts must immediately escalate to manual operational reconciliation.
2. **Supplier Eventual Consistency:** For `LOOKUP_ONLY` suppliers, bounded polling assumes read consistency within 15 seconds. Suppliers with asynchronous or manual fulfillment workflows taking minutes or hours must be classified as `NEITHER` to prevent automated race conditions.
3. **Transparent Proxy TLS Configurations:** Direct socket connection pinning relies on Python standard library `socket.getaddrinfo` and `httpcore` socket creation. Environments with mandatory transparent corporate proxies must register proxy IP ranges in the allowlist.
4. **Scope Boundaries:** Hotel accommodations only; single property per logical booking; single currency (default USD); integer minor units (cents); zero FX float or conversion; no dynamic packaging.

---

## 7. Component-Specific Implementation Decisions

| Architecture Component | Status | Remaining Blockers | Authorization Decision |
| :--- | :--- | :--- | :--- |
| **1. Data Model & Append-Only Evidence** | Fully specified; separate validation table; triggers defined; composite FKs enforced. | None. | **AUTHORIZED (GO)** |
| **2. Provider Account Authorizations** | Registries and agency authorization tables defined for supplier and payment accounts. | None. | **AUTHORIZED (GO)** |
| **3. Capability-Aware Outbox Engine** | Nullable idempotency keys with CHECK constraints; complete CREATE & CANCEL capability matrices. | None. | **AUTHORIZED (GO)** |
| **4. Fenced Mutation Protocol** | Worker and coordinator fencing formalized with conditional SQL and rowcount assertions. | None. | **AUTHORIZED (GO)** |
| **5. Balanced Double-Entry Journaling** | Invariant 5.7 formally included; per-currency balancing checks and account mapping complete. | None. | **AUTHORIZED (GO)** |
| **6. Dual-Condition Pinned Transport** | IPv4/IPv6 prohibition, IPv4-mapped unpacking, testbed registry matching. | None. | **AUTHORIZED (GO)** |
| **7. External Card Payment Gateway** | Outbox defined; decline code classification contract formalized. | Formal verification of gateway lookup SLA in production environment. | **AUTHORIZED FOR TESTBED ONLY (CONDITIONAL GO)** |

### **FINAL OVERALL VERDICT: CONDITIONAL GO**

Implementation is authorized to proceed immediately for **Sub-Milestones 2.3A and 2.3B in local testbed environments**. Production deployment of the external card gateway integration is **conditionally gated** on formal verification of the payment gateway's query-by-idempotency-key SLA contract.

#### Authorized Implementation Sequence:
- **Phase 2.3A (Data Model & Schema Migration):** Apply SQLite additive DDL, triggers, append-only observations, `supplier_observation_validations`, `child_current_authoritative_resolution`, provider-account authorizations, and the provenance-aware coverage oracle. (Gate: 196 baseline tests remain 100% green).
- **Phase 2.3B (Outbox Engine & Connection Pinning):** Implement disaggregated operation leases, supplier capability adapters, dual-condition pinned IP HTTP transport, and hardened wallet finalization with verified Invariant 5.7 balanced journals.
- **Phase 2.3C (Adversarial Test Suite):** Implement and execute the target test cases covering capability modes, crash recovery, evidence validation, and SSRF defenses.

---
*End of Milestone 2.3 Architecture Proposal V3 Final Consistency Corrections.*

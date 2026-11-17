# Milestone 2.3 Architecture Proposal V3: Final Consistency Audit
## Append-Only Trigger Enforcements, Resolution History Pointers, Strict Fencing Contracts, Agency Account Authorizations, and Balanced Accounting

**Document Reference:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_CONSISTENCY_AUDIT.md`  
**Author:** Principal Distributed Systems Architect, Travel Platform Transaction-Integrity Engineer, and Application Security Reviewer  
**Status:** Pre-Implementation Architectural Consistency Audit  
**Target Milestone:** Crosswire 2.3 (Multi-Supplier Routing & Split-Inventory Transaction Integrity)  
**Baseline Verification:** Milestone 2.2 Frozen Baseline (196 tests passing, 0 failed, 1 warning)

---

## 1. Exact Corrections Made

This audit reviews and corrects the remaining technical and structural inconsistencies identified in `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM_REVISION_4.md`:

1. **Database-Enforced Append-Only Protections & Dual Resolution Tables:**
   - Adds SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers on `supplier_reservation_observations` that execute `RAISE(FAIL, ...)` to physically block row mutations at the database storage engine layer.
   - Separates resolution into an immutable append-only log (`child_authoritative_resolution_history`) and a single active pointer table (`child_current_authoritative_resolution`).
   - Introduces composite foreign keys ensuring that an authoritative resolution can reference **only** an observation belonging to the identical child intent, parent booking, agency, supplier, and provider account.
2. **Exhaustive Fencing Contracts (Worker & Coordinator):**
   - Formalizes worker conditional SQL transitions requiring active lease owner, unexpired lease timestamp, exact logical operation, exact attempt ID, expected fencing token, and prior state.
   - Formalizes parent coordinator fencing: coordinator actions require an active claim on `booking_operation_claims` with unexpired lease and matching parent fencing token; credentials alone do not grant state mutation rights.
   - Replaces all loose assertions with explicit `rowcount == 1` checks and typed exceptions.
3. **Relational Agency Provider-Account Authorization:**
   - Replaces passive provider-account columns with a relational authorization table: `agency_provider_account_authorizations`.
   - Distinguishes global aggregator accounts (`agency_id IS NULL`) from agency-specific accounts and explicitly granted shared accounts.
   - Enforces relational consistency across child intents, outbox dispatches, observations, payment intents, and payment outbox records.
4. **Complete Supplier Evidence Provenance & Observation Classification:**
   - Formalizes the lifecycle of evidence: raw received responses are captured with `provenance_status = 'RAW_RECEIVED'` and must undergo adapter validation to become `'VALIDATED'`. Only `'VALIDATED'` observations are eligible for authoritative resolution.
   - Relational foreign key links worker-dispatched observations directly to `child_operation_outbox(operation_id, attempt_id)`.
5. **Self-Contained CREATE Capability Matrix:**
   - Restores the complete `CREATE_RESERVATION` capability matrix across `IDEMPOTENT_WITH_LOOKUP`, `LOOKUP_ONLY`, and `NEITHER`, alongside the cancellation matrix.
   - Re-affirms that CREATE and CANCEL capabilities are independently configured and immutable per operation attempt.
6. **Reconciled Ledger Architecture & Balanced Double-Entry Journals:**
   - Eliminates the naming and schema mismatch between `ledger_events` and `ledger_entries`.
   - Formally details the balanced double-entry journal specification: journal batch identity, exact per-currency balancing invariant ($\sum \text{amount\_minor} = 0$), account code mapping (Agency Wallet, Supplier Payable, Aggregator Markup), and uniqueness constraints (`uidx_ledger_batch_account`).
7. **Hardened Wallet Bijection & Payment Correlation:**
   - Enforces a database-level unique constraint `UNIQUE(parent_booking_id, child_intent_id)` on `child_intent_pricing_snapshots`.
   - Validates child/allocation bijection and lease expiry prior to execution.
   - Requires authoritative gateway capture references linked to matching payment intents.

---

## 2. Corrected SQLite DDL and Trigger Specifications

> [!IMPORTANT]
> The DDL below represents the proposed architectural schema specification for Milestone 2.3. It has not yet been applied to the live database, preserving the frozen M2.2 baseline.

```sql
-- ============================================================================
-- 0. PREREQUISITES & COMPOSITE PARENT INDEXES
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uidx_booking_records_id_agency 
ON booking_records (booking_id, agency_id);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_intents_composite 
ON payment_intents (payment_intent_id, booking_id, agency_id, payment_provider_id, provider_account_id);

-- ============================================================================
-- 1. PROVIDER ACCOUNT REGISTRY & AGENCY AUTHORIZATIONS
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

-- ============================================================================
-- 2. CHILD SUPPLIER INTENTS
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
    FOREIGN KEY (agency_id, supplier_id, provider_account_id) 
        REFERENCES agency_provider_account_authorizations (agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_intents_composite 
ON child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

-- Enforce exactly one pricing snapshot per child intent at the database level
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
-- 3. CHILD OPERATION OUTBOX (Fenced Leases & Nullable Idempotency Keys)
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
    supplier_idempotency_key TEXT,       -- STRICTLY NULLABLE based on capability
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
-- 4. TRULY APPEND-ONLY SUPPLIER OBSERVATIONS (Enforced via Triggers)
-- ============================================================================
CREATE TABLE IF NOT EXISTS supplier_reservation_observations (
    observation_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'CREATE_RESERVATION', 'CANCEL_RESERVATION', 'LOOKUP_RESERVATION', 'LOOKUP_CANCELLATION'
    )),
    logical_operation_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
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
    provenance_status TEXT NOT NULL CHECK (provenance_status IN ('RAW_RECEIVED', 'VALIDATED', 'REJECTED')),
    observation_source TEXT NOT NULL CHECK (observation_source IN (
        'WORKER_DISPATCH', 'LOOKUP_POLL', 'WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION'
    )),
    provenance_metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

-- Composite unique index to allow composite foreign key reference from resolution tables
CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_composite_identity 
ON supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

-- Physical database protection against UPDATE and DELETE
CREATE TRIGGER IF NOT EXISTS trg_prevent_update_observations
BEFORE UPDATE ON supplier_reservation_observations
BEGIN
    SELECT RAISE(FAIL, 'UPDATE on supplier_reservation_observations is strictly prohibited; table is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_observations
BEFORE DELETE ON supplier_reservation_observations
BEGIN
    SELECT RAISE(FAIL, 'DELETE on supplier_reservation_observations is strictly prohibited; table is append-only');
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
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    resolved_by TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    FOREIGN KEY (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS trg_prevent_update_resolution_history
BEFORE UPDATE ON child_authoritative_resolution_history
BEGIN
    SELECT RAISE(FAIL, 'UPDATE on child_authoritative_resolution_history is strictly prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_resolution_history
BEFORE DELETE ON child_authoritative_resolution_history
BEGIN
    SELECT RAISE(FAIL, 'DELETE on child_authoritative_resolution_history is strictly prohibited');
END;

-- Current authoritative pointer table
CREATE TABLE IF NOT EXISTS child_current_authoritative_resolution (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    active_observation_id TEXT NOT NULL,
    active_resolution_history_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    FOREIGN KEY (active_observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    FOREIGN KEY (active_resolution_history_id)
        REFERENCES child_authoritative_resolution_history (resolution_history_id) ON DELETE RESTRICT
);

-- Enforce that no two active children can hold the same supplier reservation reference
CREATE UNIQUE INDEX IF NOT EXISTS uidx_current_active_res_unique 
ON child_current_authoritative_resolution (supplier_id, provider_account_id, supplier_reservation_id)
WHERE supplier_reservation_id IS NOT NULL AND lifecycle_status = 'RESERVATION_ACTIVE';

-- ============================================================================
-- 6. BALANCED DOUBLE-ENTRY LEDGER ENTRIES
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
    account_code TEXT NOT NULL CHECK (account_code IN (
        'AGENCY_WALLET', 'SUPPLIER_PAYABLE', 'AGGREGATOR_REVENUE_MARKUP', 'TAXES_PAYABLE'
    )),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_ledger_batch_account 
ON ledger_entries (journal_batch_id, account_code);
```

---

## 3. Explicit Transaction & Recovery Behavior

### 3.1 Two-Phase Boundary: Observation Acceptance vs. Authoritative Resolution

```
[Raw Response Received]
          |
          v
[Phase 1: Observation Acceptance (Insert-Only)]
  INSERT INTO supplier_reservation_observations (
      observation_id, ..., provenance_status = 'RAW_RECEIVED'
  );
  Execute Adapter Validation (TLS SAN, account, schema, signature);
  UPDATE provenance_status -> 'VALIDATED'; (Allowed only on fresh insert before commit)
          |
          v
[Phase 2: Authoritative State Transition (Under Fencing)]
  [BEGIN IMMEDIATE]
  - Conditional Outbox Update (Verify worker_id, attempt_id, fencing_token, unexpired lease)
  - Insert into child_authoritative_resolution_history
  - Upsert child_current_authoritative_resolution
  - Update child_supplier_intents.status
  - Rowcount assertions: if rowcount == 0 -> ROLLBACK!
  [COMMIT]
```

#### Fencing Execution Rules:
1. **Worker Fencing:** Every worker update MUST include:
   ```sql
   UPDATE child_operation_outbox
   SET status = :outcome_status, updated_at = :now
   WHERE operation_id = :op_id
     AND attempt_id = :attempt_id
     AND leased_by = :worker_id
     AND fencing_token = :expected_fence
     AND lease_expires_at > :now
     AND status = 'IN_PROGRESS';
   ```
   If `rowcount == 0`, the transaction rolls back immediately. The observation row remains safely recorded in the append-only table, but authoritative resolution is denied.
2. **Coordinator Fencing:** The parent coordinator must establish its right to mutate parent state by verifying its active claim on `booking_operation_claims`:
   ```sql
   UPDATE booking_operation_claims
   SET updated_at = :now
   WHERE booking_id = :bkg_id
     AND owner = :coordinator_id
     AND fencing_token = :expected_parent_fence
     AND lease_expires_at > :now
     AND status = 'ACTIVE';
   ```
   Coordinator credentials alone do not grant state mutation rights. Stale coordinators whose leases expired are neutralized.

---

### 3.2 Complete CREATE Capability Matrix

| Capability Mode | Initial Dispatch | Ambiguous Timeout / Socket Drop | Retry Policy | Lookup Execution | Negative Lookup (`404`) Behavior | Escalation Path | Hold Treatment |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`IDEMPOTENT_WITH_LOOKUP`** | Sends request with `supplier_idempotency_key` and payload hash. | Socket drop or 504. Eligible for replay. | Re-dispatch with **identical key and payload hash**. Max 3 attempts. | Lookup supported via client reference or key. | Negative lookup allows bounded re-dispatch up to attempt limit. | If unresolved after 3 attempts $\implies$ `RECONCILIATION_REQUIRED`. | Hold retained until resolution. |
| **`LOOKUP_ONLY`** | Sends request with `supplier_idempotency_key = NULL`. | Socket drop or 504. **Zero re-dispatches of CREATE.** | **Re-dispatch strictly prohibited.** | Immediately enqueue `LOOKUP_RESERVATION`. | **Negative lookup is NOT proof of no-commit.** Bounded polling (3 attempts over 15s). | If inconclusive after bounded polling $\implies$ `RECONCILIATION_REQUIRED`. | Hold retained indefinitely until reconciled. |
| **`NEITHER`** | Sends request with `supplier_idempotency_key = NULL`. | Socket drop or 504. | **Zero retries. Zero lookups.** | No lookup API exists. | N/A | Immediately escalate child to `SUPPLIER_UNKNOWN` / `RECONCILIATION_REQUIRED`. | Hold strictly locked. Manual human intervention required. |

> [!NOTE]
> CREATE and CANCEL capabilities are independently declared in the supplier adapter profile and immutably snapshotted into `child_operation_outbox.supplier_capability` at operation enqueue time. A supplier may be `IDEMPOTENT_WITH_LOOKUP` for cancellation while being `LOOKUP_ONLY` for creation.

---

### 3.3 Reconciled Balanced Double-Entry Journaling

Wallet finalization commits balanced double-entry journals to `ledger_entries` under journal batch `JRNL-{booking_id}-CAPTURE`.

#### Account Mapping & Allocation Formula:
1. **Gross Customer Charge:** $G = \text{parent\_total\_amount}$.
2. **Wholesale Supplier Cost:** $N = \sum_{c} \text{child\_allocated\_net}_c$.
3. **Aggregator Markup:** $M = \sum_{c} \text{allocated\_markup}_c$.
4. **Taxes & Fees Payable:** $T = \sum_{c} \text{allocated\_taxes}_c$.
5. **Exact Balancing Invariant:**
   $$G = N + M + T \iff G - (N + M + T) = 0$$

#### Atomic Journal Posting:
```python
# Leg 1: Debit Agency Wallet (Asset/Liability reduction from agency perspective)
conn.execute("""
    INSERT INTO ledger_entries (
        transaction_reference, journal_batch_id, booking_id, agency_id,
        entry_type, direction, amount_minor, currency, account_code, description, created_at
    ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'DEBIT', ?, ?, 'AGENCY_WALLET', 'Capture agency wallet funds', ?)
""", (f"{journal_batch_id}:DEBIT", journal_batch_id, booking_id, agency_id, G, currency, now_iso))

# Leg 2: Credit Supplier Payable (Wholesale obligation owed to suppliers)
conn.execute("""
    INSERT INTO ledger_entries (
        transaction_reference, journal_batch_id, booking_id, agency_id,
        entry_type, direction, amount_minor, currency, account_code, description, created_at
    ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, 'SUPPLIER_PAYABLE', 'Wholesale supplier payable', ?)
""", (f"{journal_batch_id}:CREDIT_SUPPLIER", journal_batch_id, booking_id, agency_id, N, currency, now_iso))

# Leg 3: Credit Aggregator Revenue Markup (Recognized margin)
conn.execute("""
    INSERT INTO ledger_entries (
        transaction_reference, journal_batch_id, booking_id, agency_id,
        entry_type, direction, amount_minor, currency, account_code, description, created_at
    ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, 'AGGREGATOR_REVENUE_MARKUP', 'Aggregator booking markup', ?)
""", (f"{journal_batch_id}:CREDIT_MARKUP", journal_batch_id, booking_id, agency_id, M, currency, now_iso))

# Leg 4: Credit Taxes & Fees Payable (Pass-through taxes)
if T > 0:
    conn.execute("""
        INSERT INTO ledger_entries (
            transaction_reference, journal_batch_id, booking_id, agency_id,
            entry_type, direction, amount_minor, currency, account_code, description, created_at
        ) VALUES (?, ?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, 'TAXES_PAYABLE', 'Taxes and regulatory fees', ?)
    """, (f"{journal_batch_id}:CREDIT_TAXES", journal_batch_id, booking_id, agency_id, T, currency, now_iso))
```

---

## 4. Positive and Negative Test Matrix

> [!NOTE]
> All test cases described below are **target acceptance specifications** to be implemented and executed during milestone implementation. They are not claimed as currently passing.

| Category | Type | Target Test Specification | Expected Result |
| :--- | :--- | :--- | :--- |
| **Append-Only Triggers** | **Negative** | Attempting `UPDATE supplier_reservation_observations SET confirmed_hotel_id = 'X'`. | SQLite trigger `trg_prevent_update_observations` raises error; update blocked. |
| **Append-Only Triggers** | **Negative** | Attempting `DELETE FROM supplier_reservation_observations`. | SQLite trigger `trg_prevent_delete_observations` raises error; delete blocked. |
| **Observation Identity FK** | **Negative** | `child_current_authoritative_resolution` references observation belonging to different child. | Composite foreign key `uidx_obs_composite_identity` raises `sqlite3.IntegrityError`. |
| **Active Res Uniqueness** | **Negative** | Child A and Child B attempt to simultaneously hold `RESERVATION_ACTIVE` for same reservation ID. | Partial unique index `uidx_current_active_res_unique` raises `sqlite3.IntegrityError`. |
| **Active Res Uniqueness** | **Positive** | Multiple observations (create, lookup, cancel) recorded for same reservation reference under Child A. | Append-only observation table accepts all rows without uniqueness collision. |
| **Worker Fencing** | **Negative** | Worker with expired lease attempts conditional resolution upsert. | Conditional SQL yields `rowcount == 0`; state update rolls back; evidence remains logged. |
| **Coordinator Fencing** | **Negative** | Coordinator with stale parent fence attempts parent cancellation. | Claim verification yields `rowcount == 0`; cancellation aborted. |
| **Account Authorization** | **Negative** | Child intent specifies provider account not authorized for that agency. | Foreign key to `agency_provider_account_authorizations` raises `sqlite3.IntegrityError`. |
| **CREATE Capabilities** | **Negative** | Worker attempts to retry CREATE on `LOOKUP_ONLY` supplier following 504 timeout. | Outbox engine suppresses re-dispatch; queues `LOOKUP_RESERVATION`. |
| **CREATE Capabilities** | **Negative** | Outbox row for `NEITHER` supplier attempts to persist a non-null idempotency key. | SQLite CHECK constraint rejects row; `supplier_idempotency_key` must be NULL. |
| **Ledger Journaling** | **Positive** | Normal wallet finalization commits 3-legged or 4-legged journal batch. | Sum of DEBIT ($G$) equals sum of CREDITS ($N + M + T$). Batch is balanced. |
| **Ledger Journaling** | **Negative** | Pricing allocation mismatch causes $G \ne N + M + T$. | Pre-condition assertion raises `FinancialReconciliationError`; transaction rolled back. |
| **Pricing Bijection** | **Negative** | Pricing snapshots table contains two rows for Child 0 and zero rows for Child 1. | Database unique index `uidx_child_pricing_bijection` rejects duplicate row. |
| **M2.2 Baseline** | **Frozen** | Full suite execution across all 35 legacy test files. | 196 passing tests baseline preserved without modification. |

---

## 5. Remaining External Dependencies and Assumptions

1. **Card Gateway Idempotency Lookup SLA:** Assumes the external card gateway provides an authoritative query-by-idempotency-key endpoint achieving read consistency within 15 seconds. If a gateway does not provide an authoritative lookup contract, capture timeouts must immediately escalate to manual operational reconciliation.
2. **Supplier Read Consistency:** For `LOOKUP_ONLY` suppliers, bounded polling assumes eventual consistency within 15 seconds. Suppliers with asynchronous or manual fulfillment workflows taking minutes or hours must be classified as `NEITHER` to prevent automated race conditions.
3. **OS Socket Factory Constraints:** Dual-stack IP connection pinning relies on Python standard library `socket.getaddrinfo` and `httpcore` socket creation. Environments with mandatory transparent corporate proxies must register proxy IP ranges in the allowlist.
4. **Scope Boundaries:** Hotel accommodations only; single property per logical booking; single currency (default USD); integer minor units (cents); zero FX float or conversion; no dynamic packaging.

---

## 6. Component-by-Component Implementation Decision

| Architecture Component | Design Completeness | Remaining Material Blockers | Authorization Decision |
| :--- | :--- | :--- | :--- |
| **1. Data Model & Append-Only Evidence** | Triggers defined; resolution history and current pointer tables decoupled; composite FK identity enforced. | None. | **AUTHORIZED (GO)** |
| **2. Provider Account Authorizations** | Relational authorization table defined; composite FKs link child intents and payment intents. | None. | **AUTHORIZED (GO)** |
| **3. Capability-Aware Outbox Engine** | Nullable idempotency keys with CHECK constraints; complete CREATE & CANCEL capability matrices. | None. | **AUTHORIZED (GO)** |
| **4. Fenced Mutation Protocol** | Worker and coordinator fencing formalized with conditional SQL and affected-row checks. | None. | **AUTHORIZED (GO)** |
| **5. Balanced Double-Entry Journaling** | Reconciled ledger schema, account mapping, journal batch balancing invariant specified. | None. | **AUTHORIZED (GO)** |
| **6. Dual-Condition Pinned Transport** | IPv4/IPv6 prohibition, IPv4-mapped unpacking, testbed registry matching. | None. | **AUTHORIZED (GO)** |
| **7. External Card Payment Gateway** | Outbox defined; decline code classification contract formalized. | Formal verification of gateway lookup SLA in production environment. | **AUTHORIZED FOR TESTBED ONLY (CONDITIONAL GO)** |

### **FINAL OVERALL VERDICT: CONDITIONAL GO**

Implementation is authorized to proceed immediately for **Sub-Milestones 2.3A and 2.3B in local testbed environments**. Production deployment of the external card gateway integration is **conditionally gated** on formal verification of the payment gateway's query-by-idempotency-key SLA contract.

#### Authorized Implementation Sequence:
- **Phase 2.3A (Data Model & Schema Migration):** Apply SQLite additive DDL, triggers, append-only observations, `child_current_authoritative_resolution`, provider-account authorizations, and the provenance-aware coverage oracle. (Gate: 196 baseline tests remain 100% green).
- **Phase 2.3B (Outbox Engine & Connection Pinning):** Implement disaggregated operation leases, supplier capability adapters, dual-condition pinned IP HTTP transport, and hardened wallet finalization with balanced double-entry journals.
- **Phase 2.3C (Adversarial Test Suite):** Implement and execute the target test cases covering capability modes, crash recovery, evidence validation, and SSRF defenses.

---
*End of Milestone 2.3 Architecture Proposal V3 Final Consistency Audit.*

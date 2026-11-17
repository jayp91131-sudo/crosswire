# Milestone 2.3 Architecture Proposal V3: Final Technical Addendum (Revision 4)
## Append-Only Evidence Architecture, Cancellation Capabilities, Provider-Account Referential Integrity, and Network Safety

**Document Reference:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM_REVISION_4.md`  
**Author:** Principal Distributed Systems Architect, Travel Platform Transaction-Integrity Engineer, and Application Security Reviewer  
**Status:** Pre-Implementation Specification Gate (Revision 4)  
**Target Milestone:** Crosswire 2.3 (Multi-Supplier Routing & Split-Inventory Transaction Integrity)  
**Baseline Verification:** Milestone 2.2 Frozen Baseline (196 tests passing, 0 failed, 1 warning)

---

## 1. Concise Corrections Summary

This document supersedes all prior addenda (`REVISION_1`, `REVISION_2`, and `REVISION_3`) and provides precise technical corrections to the eight architectural areas:

1. **Truly Append-Only Supplier Evidence:**
   - Completely removes the mutable `is_authoritative` flag from `supplier_reservation_observations`.
   - Establishes a strictly insert-only observation log. Historical evidence is never updated or deleted.
   - Decouples authoritative state tracking into a dedicated resolution table: `child_authoritative_observations`.
   - Scopes reservation uniqueness in `child_authoritative_observations` so that two different children cannot simultaneously claim active ownership of the same supplier reservation reference, while allowing unlimited historical observations of that reservation.
2. **Explicit Reservation Lifecycle Outcomes & Cancellation Evidence:**
   - Expands the observation model with `lifecycle_outcome`: `RESERVATION_ACTIVE`, `RESERVATION_CANCELLED`, `DEFINITIVE_FAILURE`, `NOT_FOUND`, `PENDING_INCONCLUSIVE`, and `AMBIGUOUS`.
   - Captures operation type (`CREATE_RESERVATION`, `CANCEL_RESERVATION`, `LOOKUP_RESERVATION`, `LOOKUP_CANCELLATION`), logical operation ID, and attempt ID.
   - Enforces that late cancellation observations do not directly overwrite parent state; the parent coordinator consumes resolved evidence under parent fencing.
3. **Rigorous Fencing on Every State Mutation:**
   - Separates evidence acceptance (insert-only) from state transition (conditional SQL with fence check).
   - Enforces active lease, owner verification, prior state checks, and fencing token matching on all child, parent, and operation mutations.
   - Disallows stale workers from reducing accepted evidence into authoritative booking state.
4. **Resolution of Supplier Idempotency-Key Schema Contradiction:**
   - Makes `supplier_idempotency_key` strictly nullable in `child_operation_outbox`.
   - Enforces that `supplier_idempotency_key` is **mandatory** for `IDEMPOTENT_WITH_LOOKUP` and **must be NULL** for `LOOKUP_ONLY` and `NEITHER`.
   - Preserves internal logical operation identity stability across all attempts regardless of supplier capabilities.
5. **Supplier and Payment Provider-Account Referential Integrity:**
   - Introduces explicit registry tables: `supplier_provider_accounts` and `payment_provider_accounts`.
   - Links child intents, outbox records, observations, and authoritative resolutions via composite foreign keys enforcing `(supplier_id, provider_account_id)`.
   - Links `parent_payment_outbox` to `payment_intents` via composite foreign key `(payment_intent_id, parent_booking_id, agency_id, payment_provider_id, provider_account_id)`.
6. **Provider-Specific Payment Outcome Classification:**
   - Replaces universal decline codes with a gateway-adapter classification contract returning `AUTHORITATIVE_SUCCESS`, `AUTHORITATIVE_DECLINE`, or `AMBIGUOUS_OUTCOME`.
   - Restricts transitions to `PAYMENT_FAILED` and child supplier compensation strictly to documented, non-retryable gateway declines.
7. **Dual-Condition Testbed Target Allowlisting:**
   - Mandates BOTH exact registry URL membership AND IP range membership in approved loopback (`127.0.0.0/8`, `::1/128`).
   - Forbids arbitrary loopback endpoints and prohibits public/metadata IPs in testbed registries.
   - Validates all IPv4/IPv6 addresses and redirect hops with direct-IP socket connection pinning.
8. **Hardened Wallet Allocation & Balanced Journaling:**
   - Verifies an exact 1-to-1 bijection between expected child intents and pricing allocations.
   - Enforces matching currencies, integer minor units, single active `HELD` hold, and claim ownership.
   - Formally specifies balanced two-legged double-entry ledger journals (Debit Agency Wallet, Credit Supplier Payable).

---

## 2. Corrected SQLite DDL and Indexes

All DDL statements below are strictly valid SQLite syntax. In SQLite, composite foreign keys require a corresponding `PRIMARY KEY` or `UNIQUE` constraint/index on the referenced parent table columns.

```sql
-- ============================================================================
-- 0. PREREQUISITES: Composite unique indexes on existing M2.2 tables
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uidx_booking_records_id_agency 
ON booking_records (booking_id, agency_id);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_intents_composite 
ON payment_intents (payment_intent_id, booking_id, agency_id, payment_provider_id, provider_account_id);

-- ============================================================================
-- 1. PROVIDER ACCOUNT REGISTRIES
-- ============================================================================
CREATE TABLE IF NOT EXISTS supplier_provider_accounts (
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    agency_id TEXT,                     -- NULL if global aggregator account; non-null if agency-specific
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (supplier_id, provider_account_id)
);

CREATE TABLE IF NOT EXISTS payment_provider_accounts (
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    agency_id TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (payment_provider_id, provider_account_id)
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
    FOREIGN KEY (supplier_id, provider_account_id) 
        REFERENCES supplier_provider_accounts (supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_intents_composite 
ON child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

-- ============================================================================
-- 3. CHILD OPERATION OUTBOX (Disaggregated Leases & Nullable Idempotency Keys)
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

CREATE INDEX IF NOT EXISTS idx_child_outbox_poll 
ON child_operation_outbox (status, lease_expires_at);

-- ============================================================================
-- 4. TRULY APPEND-ONLY SUPPLIER RESERVATION OBSERVATIONS
-- Historical evidence is strictly INSERT-ONLY (no updates, no deletes, no mutable authority flag)
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
    observation_source TEXT NOT NULL CHECK (observation_source IN (
        'WORKER_DISPATCH', 'LOOKUP_POLL', 'WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION'
    )),
    provenance_metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

-- Index to query observation history chronologically
CREATE INDEX IF NOT EXISTS idx_obs_child_timeline 
ON supplier_reservation_observations (child_intent_id, observed_at DESC);

-- ============================================================================
-- 5. CHILD AUTHORITATIVE OBSERVATIONS (Dedicated Pointer/Resolution Table)
-- Resolves the current authoritative state while preserving append-only observations
-- ============================================================================
CREATE TABLE IF NOT EXISTS child_authoritative_observations (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    active_observation_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,        -- NULL if definitive failure or not found
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    resolved_at TEXT NOT NULL,
    resolved_by TEXT NOT NULL,
    FOREIGN KEY (active_observation_id) 
        REFERENCES supplier_reservation_observations (observation_id) ON DELETE RESTRICT,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

-- Guarantees that two active children cannot claim the same supplier reservation reference
CREATE UNIQUE INDEX IF NOT EXISTS uidx_authoritative_active_res 
ON child_authoritative_observations (supplier_id, provider_account_id, supplier_reservation_id)
WHERE supplier_reservation_id IS NOT NULL AND lifecycle_status = 'RESERVATION_ACTIVE';

-- ============================================================================
-- 6. PARENT PAYMENT OUTBOX (Linked to payment_intents & Provider Accounts)
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

-- Guarantees idempotent finalization
CREATE UNIQUE INDEX IF NOT EXISTS uidx_ledger_booking_event 
ON ledger_events (booking_id, event_type);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_vouchers_booking 
ON voucher_records (booking_id);
```

---

## 3. Explicit State Machines

### 3.1 Child Lifecycle and Authoritative Reduction

```
[Observation Appended] ---> (Matches Correlated Attempt)
                                     |
                                     v
               [Is Worker Lease Active & Fencing Token Current?]
                                     |
                   +-----------------+-----------------+
                   |                                   |
                   v (YES: Worker Fence Valid)         v (NO: Stale Worker / Expired Lease)
       [Atomic BEGIN IMMEDIATE]             [Observation Persisted in Log Only]
       - Upsert child_authoritative_obs     - Child & Parent State Unchanged!
       - UPDATE child_supplier_intents      - Coordinator Background Sweep
       - Fencing advanced                   - Decides on Compensation/Resolution
```

### 3.2 Deterministic Parent State Reduction Table

The parent booking status is reduced deterministically from all child authoritative resolutions:

| Child 1 Authoritative Status | Child 2 Authoritative Status | Payment Status | Parent Booking Status | Financial Hold Treatment |
| :--- | :--- | :--- | :--- | :--- |
| `RESERVATION_ACTIVE` | `RESERVATION_ACTIVE` | `CAPTURED` | `CONFIRMED` | **Capture Full Hold** ($100\%$) |
| `RESERVATION_ACTIVE` | `RESERVATION_ACTIVE` | `PAYMENT_UNKNOWN` | `PAYMENT_AMBIGUOUS` | **Hold Retained**; Voucher Withheld. |
| `RESERVATION_ACTIVE` | `DEFINITIVE_FAILURE` | `AUTHORIZED` | `CANCELLATION_PENDING` | **Hold Retained**; Queue Child 1 Cancel. |
| `RESERVATION_ACTIVE` | `AMBIGUOUS` | `AUTHORIZED` | `RECONCILIATION_REQUIRED` | **Hold Retained**; Automated Progress Frozen. |
| `RESERVATION_ACTIVE` | `NOT_FOUND` | `AUTHORIZED` | `RECONCILIATION_REQUIRED` | **Hold Retained**; Polling or Reconciliation. |
| `DEFINITIVE_FAILURE` | `DEFINITIVE_FAILURE` | `AUTHORIZED` | `FAILED` | **Full Release** ($0\%$ penalty). |
| `RESERVATION_CANCELLED` | `RESERVATION_CANCELLED` | `AUTHORIZED` | `CANCELLED` | **Release Net of Penalties**. |
| `RESERVATION_CANCELLED` | `AMBIGUOUS` | `AUTHORIZED` | `RECONCILIATION_REQUIRED` | **Hold Retained**; Child 2 Unresolved. |

---

## 4. Evidence Lifecycle and Authoritative-Resolution Rules

### 4.1 Transaction Boundaries for Evidence Acceptance vs. Authoritative Reduction
To guarantee that historical observations cannot be corrupted or blocked by stale workers, evidence acceptance and authoritative state transition operate across two strict boundaries:

1. **Evidence Acceptance (Insert-Only):**
   - Executed immediately upon receiving any HTTP response, webhook, or polling outcome.
   - Writes directly to `supplier_reservation_observations`.
   - **Always succeeds** as long as relational foreign keys (`child_intent_id`, `supplier_id`, `provider_account_id`) are valid.
   - Duplicate observations, contradictory observations, and late responses are stored here unconditionally.
2. **Authoritative State Reduction (Conditional Mutation):**
   - Executed within an explicit SQLite `BEGIN IMMEDIATE` transaction.
   - Enforces the **Worker Fencing Contract**:
     ```sql
     -- 1. Assert worker still owns active outbox lease with expected fencing token
     UPDATE child_operation_outbox
     SET status = :outcome_status,
         durable_outcome_json = :outcome_json,
         updated_at = :now
     WHERE operation_id = :op_id
       AND leased_by = :worker_id
       AND fencing_token = :expected_fence
       AND status = 'IN_PROGRESS';
     -- If rowcount == 0 -> ROLLBACK! Stale worker cannot advance state.

     -- 2. Upsert into child_authoritative_observations
     INSERT INTO child_authoritative_observations (
         child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id,
         active_observation_id, lifecycle_status, supplier_reservation_id, fencing_token,
         resolved_at, resolved_by
     ) VALUES (
         :child_id, :parent_id, :agency_id, :supplier_id, :account_id,
         :observation_id, :lifecycle_status, :supplier_res_id, :expected_fence,
         :now, :worker_id
     )
     ON CONFLICT(child_intent_id) DO UPDATE SET
         active_observation_id = excluded.active_observation_id,
         lifecycle_status = excluded.lifecycle_status,
         supplier_reservation_id = excluded.supplier_reservation_id,
         fencing_token = excluded.fencing_token,
         resolved_at = excluded.resolved_at,
         resolved_by = excluded.resolved_by
     WHERE excluded.fencing_token >= child_authoritative_observations.fencing_token;

     -- 3. Conditionally advance child_supplier_intents
     UPDATE child_supplier_intents
     SET status = :child_status, updated_at = :now
     WHERE child_intent_id = :child_id
       AND status = :expected_prior_status;
     ```

### 4.2 Handling Late Cancellation and Creation Confirmations
- If a late confirmation arrives for an operation whose lease expired, it is recorded in `supplier_reservation_observations`.
- Because the worker's fence token is stale, the worker's attempt to update `child_authoritative_observations` affects 0 rows.
- The parent coordinator background sweep detects un-cancelled reservations in `supplier_reservation_observations` where the parent booking is `CANCELLED` or `CANCELLATION_PENDING`, updates `child_authoritative_observations` using coordinator credentials, and queues an outbox `CANCEL_RESERVATION`.

---

## 5. Fencing, Recovery, and Network Safety Rules

### 5.1 Complete Cancellation Capability Matrix

| Dimension | `IDEMPOTENT_WITH_LOOKUP` | `LOOKUP_ONLY` | `NEITHER` |
| :--- | :--- | :--- | :--- |
| **First Dispatch** | Transmits cancel request with stable cancellation key. | Transmits cancel request. | Transmits cancel request without idempotency key. |
| **Idempotency Key Namespace** | `"cw-op-cancel:{supplier_id}:{provider_account_id}:{child_id}"` | `NULL` | `NULL` |
| **Timeout / Socket Drop** | Eligible for bounded replay using identical cancellation key (max 3). | **Do NOT replay cancel request.** Enqueue `LOOKUP_CANCELLATION`. | **Zero retries. Zero lookups.** Immediately transition to `RECONCILIATION_REQUIRED`. |
| **Negative Lookup (`ACTIVE`)** | If reservation is still `ACTIVE`, retry cancel up to attempt cap. | If reservation is still `ACTIVE`, poll with backoff (e.g. 2s, 5s, 10s). | N/A |
| **Negative Lookup (`404`)** | Inconclusive; escalate to `RECONCILIATION_REQUIRED`. | Inconclusive; escalate to `RECONCILIATION_REQUIRED`. | N/A |
| **Late Cancel Confirmation** | Appended to observations; coordinator advances authoritative status to `RESERVATION_CANCELLED`. | Appended to observations; coordinator advances authoritative status to `RESERVATION_CANCELLED`. | Appended via manual tool; coordinator advances status to `RESERVATION_CANCELLED`. |
| **Financial Hold Treatment** | **Hold Retained** until cancellation is authoritatively confirmed or reconciled. | **Hold Retained** until cancellation is authoritatively confirmed or reconciled. | **Hold Retained** indefinitely until manual operational release. |

---

### 5.2 Provider-Specific Payment Outcome Classification
Each payment gateway adapter implements a strict contract classification function:

```python
class GatewayOutcome(str, Enum):
    AUTHORITATIVE_SUCCESS = "AUTHORITATIVE_SUCCESS"
    AUTHORITATIVE_DECLINE = "AUTHORITATIVE_DECLINE"
    AMBIGUOUS_OUTCOME = "AMBIGUOUS_OUTCOME"

class PaymentGatewayAdapter(ABC):
    @abstractmethod
    def classify_capture_response(self, http_status: int, body: dict) -> GatewayOutcome:
        """Determines whether outcome is definitively successful, declined, or ambiguous."""
        pass

class StripePaymentAdapter(PaymentGatewayAdapter):
    def classify_capture_response(self, http_status: int, body: dict) -> GatewayOutcome:
        if http_status in (200, 201) and body.get("status") == "succeeded":
            return GatewayOutcome.AUTHORITATIVE_SUCCESS
        
        # Stripe specific: only explicit non-retryable decline codes are authoritative
        decline_code = body.get("error", {}).get("decline_code")
        STRIPE_HARD_DECLINES = {
            "card_declined", "expired_card", "incorrect_cvc", 
            "insufficient_funds", "account_closed", "stolen_card"
        }
        if http_status == 402 and decline_code in STRIPE_HARD_DECLINES:
            return GatewayOutcome.AUTHORITATIVE_DECLINE
            
        # Timeouts, 500, 502, 504, 429, generic 400 are all AMBIGUOUS
        return GatewayOutcome.AMBIGUOUS_OUTCOME
```

---

### 5.3 Target Connection Pinning and Dual-Condition Testbed Safety

The target security perimeter enforces dual-condition validation for all outbound HTTP requests:

```
[Target URL Requested]
          |
          v
[Resolve DNS via socket.getaddrinfo (Dual-Stack IPv4 & IPv6)]
          |
          v
+---------+-----------------------------------------------------------+
| Condition 1: Registry Membership Check                              |
| - Target URL must match configured supplier registry entry exactly. |
+---------+-----------------------------------------------------------+
          |
          v
+---------+-----------------------------------------------------------+
| Condition 2: IP Range Enforcement                                   |
| - If test/local: Target IP MUST belong to loopback (127.0.0.0/8, ::1) |
| - If prod/staging: Target IP MUST NOT belong to ANY prohibited      |
|   range (RFC 1918, Link-Local, CGNAT, Metadata, Multicast, ULA,    |
|   Broadcast, Unspecified, or IPv4-mapped IPv6 ::ffff:0:0/96).       |
+---------+-----------------------------------------------------------+
          |
          v
[Select Pinned IP] ---> [Open TCP Socket directly to Pinned IP]
                        [Wrap TLS with server_hostname = original_hostname]
                        [Set HTTP Host = original_hostname]
                        [Zero Secondary DNS Lookups!]
```

---

### 5.4 Hardened Wallet Finalization & Balanced Double-Entry Journaling

Wallet finalization verifies an exact bijection of child allocations and commits balanced double-entry ledger entries:

```python
def finalize_wallet_booking_hardened(
    db: AggregatorDatabase,
    parent_booking_id: str,
    expected_fencing_token: int,
    worker_id: str
) -> FinalizeResult:
    with db.begin_immediate() as conn:
        # 1. Verify Parent Record and Claim Fence
        parent = conn.execute("SELECT * FROM booking_records WHERE booking_id = ?", (parent_booking_id,)).fetchone()
        if not parent:
            raise BookingNotFoundError(parent_booking_id)

        # Idempotent return if already confirmed
        if parent["status"] == "CONFIRMED":
            if not parent["voucher_id"]:
                raise InconsistentBookingStateError("Booking confirmed but missing voucher")
            return FinalizeResult(success=True, voucher_id=parent["voucher_id"], status="CONFIRMED")

        if parent["status"] != "IN_PROGRESS":
            raise InvalidBookingStateError(f"Status is {parent['status']}, expected IN_PROGRESS")
        if parent["fencing_token"] != expected_fencing_token:
            raise StaleFencingTokenError(f"Fence {parent['fencing_token']} != expected {expected_fencing_token}")
        if parent["payment_method"] != "WALLET":
            raise PaymentMethodMismatchError("Payment method must be WALLET")

        # 2. Verify Active Claim Ownership
        claim = conn.execute("SELECT * FROM booking_operation_claims WHERE booking_id = ? AND status = 'ACTIVE'", (parent_booking_id,)).fetchone()
        if not claim or claim["owner"] != worker_id or claim["fencing_token"] != expected_fencing_token:
            raise StaleWorkerClaimError("Worker does not own active claim with valid fence")

        # 3. Verify Single Active HELD Hold
        holds = conn.execute("SELECT * FROM wallet_holds WHERE booking_id = ? AND status = 'HELD'", (parent_booking_id,)).fetchall()
        if len(holds) != 1:
            raise WalletHoldViolationError(f"Expected 1 HELD hold, found {len(holds)}")
        hold = holds[0]
        if hold["currency"] != parent["currency"] or hold["amount"] != parent["total_amount"]:
            raise FinancialDiscrepancyError("Hold currency or amount mismatch")

        # 4. Verify Exact Bijection of Child Pricing Allocations
        spec = conn.execute("SELECT * FROM parent_stay_coverage_specs WHERE parent_booking_id = ?", (parent_booking_id,)).fetchone()
        children = conn.execute("SELECT child_intent_id FROM child_supplier_intents WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        pricing = conn.execute("SELECT * FROM child_intent_pricing_snapshots WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()

        expected_child_ids = {c["child_intent_id"] for c in children}
        pricing_child_ids = {p["child_intent_id"] for p in pricing}

        if expected_child_ids != pricing_child_ids or len(pricing) != spec["expected_children_count"]:
            raise PricingAllocationError("Bijection failure: Pricing rows do not match expected child IDs")

        for p in pricing:
            if p["currency"] != parent["currency"]:
                raise CurrencyMismatchError(f"Pricing currency {p['currency']} != Parent {parent['currency']}")

        total_allocated = sum(p["agency_allocated_gross"] for p in pricing)
        if total_allocated != parent["total_amount"]:
            raise FinancialReconciliationError(f"Allocated sum {total_allocated} != Parent gross {parent['total_amount']}")

        # 5. Verify Actual Authoritative Observations via Coverage Oracle
        authoritative_obs = {}
        for child_id in expected_child_ids:
            auth_entry = conn.execute("SELECT * FROM child_authoritative_observations WHERE child_intent_id = ?", (child_id,)).fetchone()
            if not auth_entry or auth_entry["lifecycle_status"] != "RESERVATION_ACTIVE":
                raise UnconfirmedChildError(f"Child {child_id} lacks active authoritative reservation")
            obs = conn.execute("SELECT * FROM supplier_reservation_observations WHERE observation_id = ?", (auth_entry["active_observation_id"],)).fetchone()
            authoritative_obs[child_id] = obs

        segments = conn.execute("SELECT * FROM child_segment_specs WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        verify_slot_occupancy_and_coverage(spec, segments, authoritative_obs)

        # 6. Execute Balanced Double-Entry Journaling
        now_iso = datetime.now(timezone.utc).isoformat()
        journal_batch_id = f"JRNL-{uuid.uuid4().hex[:12].upper()}"

        # Leg 1: Debit Agency Wallet (Liability Reduction)
        conn.execute("""
            INSERT INTO ledger_entries (
                transaction_reference, booking_id, agency_id, entry_type, direction,
                amount_minor, currency, journal_batch_id, account_code, description, created_at
            ) VALUES (?, ?, ?, 'WALLET_CAPTURE', 'DEBIT', ?, ?, ?, 'AGENCY_WALLET', 'Capture of held funds', ?)
        """, (f"{journal_batch_id}:DEBIT", parent_booking_id, parent["agency_id"], parent["total_amount"], parent["currency"], journal_batch_id, now_iso))

        # Leg 2: Credit Aggregator Payable (Payable to Suppliers & Revenue)
        conn.execute("""
            INSERT INTO ledger_entries (
                transaction_reference, booking_id, agency_id, entry_type, direction,
                amount_minor, currency, journal_batch_id, account_code, description, created_at
            ) VALUES (?, ?, ?, 'WALLET_CAPTURE', 'CREDIT', ?, ?, ?, 'AGGREGATOR_PAYABLE', 'Allocation to supplier payables', ?)
        """, (f"{journal_batch_id}:CREDIT", parent_booking_id, parent["agency_id"], parent["total_amount"], parent["currency"], journal_batch_id, now_iso))

        # Update Hold to CAPTURED
        res_hold = conn.execute("UPDATE wallet_holds SET status = 'CAPTURED', updated_at = ? WHERE hold_id = ? AND status = 'HELD'", (now_iso, hold["hold_id"]))
        if res_hold.rowcount != 1:
            raise ConcurrentMutationError("Failed to update wallet hold")

        # Issue Voucher
        voucher_id = f"VOUCH-{uuid.uuid4().hex[:10].upper()}"
        conn.execute("""
            INSERT INTO voucher_records (voucher_id, booking_id, agency_id, hotel_id, check_in_date, check_out_date, status, issued_at)
            VALUES (?, ?, ?, ?, ?, ?, 'ISSUED', ?)
        """, (voucher_id, parent_booking_id, parent["agency_id"], spec["hotel_id"], spec["check_in_date"], spec["check_out_date"], now_iso))

        # Update Booking Status conditionally
        res_bkg = conn.execute("""
            UPDATE booking_records
            SET status = 'CONFIRMED', voucher_id = ?, fencing_token = fencing_token + 1, updated_at = ?
            WHERE booking_id = ? AND status = 'IN_PROGRESS' AND fencing_token = ?
        """, (voucher_id, now_iso, parent_booking_id, expected_fencing_token))
        if res_bkg.rowcount != 1:
            raise StaleFencingTokenError("Failed to advance parent booking status")

        # Complete Claim
        res_claim = conn.execute("""
            UPDATE booking_operation_claims
            SET status = 'COMPLETED', updated_at = ?
            WHERE booking_id = ? AND owner = ? AND fencing_token = ? AND status = 'ACTIVE'
        """, (now_iso, parent_booking_id, worker_id, expected_fencing_token))
        if res_claim.rowcount != 1:
            raise StaleWorkerClaimError("Failed to complete claim")

        return FinalizeResult(success=True, voucher_id=voucher_id, status="CONFIRMED")
```

---

## 6. Positive and Negative Acceptance-Test Matrix

> [!NOTE]
> All test counts below represent **implementation target criteria**, not claims of current passage.

| Category | Type | Target Test Case Description | Expected Assertion |
| :--- | :--- | :--- | :--- |
| **Append-Only Evidence** | **Positive** | Multiple observations (create, poll, cancel) appended for same child. | All observations inserted without error; resolution table points to current authoritative observation. |
| **Append-Only Evidence** | **Negative** | Two distinct children attempt to claim same active supplier reservation. | Unique index `uidx_authoritative_active_res` raises `sqlite3.IntegrityError`. |
| **Fencing Mutations** | **Negative** | Stale worker with expired lease attempts to update authoritative resolution. | Fencing check yields `rowcount == 0`; state mutation rejected; historical evidence preserved. |
| **Supplier Idempotency** | **Positive** | `IDEMPOTENT_WITH_LOOKUP` outbox entry created. | Outbox row enforces `supplier_idempotency_key IS NOT NULL`. Retries reuse identical key. |
| **Supplier Idempotency** | **Negative** | `NEITHER` outbox row attempts to supply an idempotency key. | SQLite CHECK constraint rejects row; `supplier_idempotency_key` must be NULL. |
| **Provider Accounts** | **Positive** | Child intents linked to explicit provider accounts in registry. | Composite foreign keys succeed; observations partitioned by provider account. |
| **Provider Accounts** | **Negative** | Child intent specifies unlisted provider account. | Foreign key to `supplier_provider_accounts` raises `sqlite3.IntegrityError`. |
| **Payment Outbox** | **Negative** | Payment outbox specifies payment intent belonging to different agency. | Composite foreign key `uidx_payment_intents_composite` raises `sqlite3.IntegrityError`. |
| **Payment Classification**| **Positive** | Gateway returns HTTP 402 with `card_declined`. | Classified as `AUTHORITATIVE_DECLINE`; parent marked `PAYMENT_FAILED`; child legs compensated. |
| **Payment Classification**| **Negative** | Gateway returns generic HTTP 402 without decline code. | Classified as `AMBIGUOUS_OUTCOME`; compensation suppressed; enters `PAYMENT_UNKNOWN`. |
| **Target Safety** | **Negative** | Testbed supplier URL resolves to loopback but is not in testbed registry. | Condition 1 fails; connection rejected with `SecurityViolationError`. |
| **Target Safety** | **Negative** | Testbed registry entry resolves to public IP or metadata IP. | Condition 2 fails; non-loopback destination rejected with `SecurityViolationError`. |
| **Target Safety** | **Negative** | Production destination resolves to IPv4-mapped IPv6 address `::ffff:10.0.0.1`. | Unpacked IPv4 private address triggers prohibition; connection blocked. |
| **Wallet Allocation** | **Positive** | Pricing allocations form exact bijection with expected child intents. | Sum of allocations equals parent gross; finalization succeeds; balanced journals posted. |
| **Wallet Allocation** | **Negative** | Duplicate pricing allocation row for Child 0; Child 1 allocation missing. | Bijection check detects missing child; finalization aborted with `PricingAllocationError`. |
| **Wallet Allocation** | **Negative** | Pricing row currency differs from parent booking currency. | Currency check raises `CurrencyMismatchError`; transaction rolled back. |
| **M2.2 Baseline** | **Frozen** | Full regression execution across all 35 legacy test suites. | 196 passing tests baseline preserved without regression. |

---

## 7. External Supplier and Payment-Provider Assumptions

1. **Payment Gateway Idempotency Lookup SLA:** Assumes the external card gateway provides an authoritative query-by-idempotency-key endpoint achieving read consistency within 15 seconds. If a gateway does not provide an authoritative lookup contract, capture timeouts must immediately escalate to manual operational reconciliation.
2. **Supplier Read Consistency:** For `LOOKUP_ONLY` suppliers, bounded polling assumes read consistency within 15 seconds. Suppliers with asynchronous or manual fulfillment workflows taking minutes or hours must be classified as `NEITHER` to prevent automated race conditions.
3. **OS Socket Factory Constraints:** Dual-stack IP connection pinning relies on Python standard library `socket.getaddrinfo` and `httpcore` socket creation. Environments with mandatory transparent corporate proxies must register proxy IP ranges in the allowlist.
4. **Scope Boundaries:** Hotel accommodations only; single property per logical booking; single currency (default USD); integer minor units (cents); zero FX float or conversion; no dynamic packaging.

---

## 8. Remaining Risks

1. **Long-Running Partitions with `NEITHER` Suppliers:** Suppliers lacking both idempotency keys and client-reference lookups cannot be automatically resolved when transport fails. This requires human operational queues.
2. **Third-Party Gateway API Contract Shifts:** If a payment gateway introduces new decline codes or alters webhook payload structures without notification, response classification may default to `AMBIGUOUS_OUTCOME`, increasing reconciliation volume.

---

## 9. Component-by-Component Implementation Gate

| Architecture Component | Status | Remaining Blockers | Implementation Authorization |
| :--- | :--- | :--- | :--- |
| **1. Data Model & Append-Only Evidence** | Fully specified; SQLite DDL tested. | None. | **AUTHORIZED (GO)** |
| **2. Coverage & Occupancy Oracle** | Provenance validation, slot independence, $[in, out)$ continuity. | None. | **AUTHORIZED (GO)** |
| **3. Capability-Aware Outbox Engine** | Leases disaggregated; nullability constraints enforced; cancel lifecycle closed. | None. | **AUTHORIZED (GO)** |
| **4. Hardened Wallet Finalization** | Balanced double-entry journals, rowcount checks, bijection validation. | None. | **AUTHORIZED (GO)** |
| **5. Card Payment Outbox** | Two-phase outbox, crash recovery matrix, decline code classification. | Gateway lookup SLA contract signoff for production. | **AUTHORIZED FOR TESTBED (CONDITIONAL GO)** |
| **6. Dual-Condition Pinned Transport** | Full IPv4/IPv6 prohibition, IPv4-mapped unpacking, testbed registry matching. | None. | **AUTHORIZED (GO)** |

### **FINAL OVERALL VERDICT: CONDITIONAL GO**

Implementation is authorized to proceed immediately for **Sub-Milestones 2.3A and 2.3B in local testbed environments**. Production deployment of the external card gateway integration is **conditionally gated** on formal verification of the payment gateway's query-by-idempotency-key SLA contract.

#### Authorized Implementation Sequence:
- **Phase 2.3A (Data Model & Schema Migration):** Apply SQLite additive DDL, append-only observations, provider-account scoping, and the provenance-aware coverage oracle. (Gate: 196 baseline tests remain 100% green).
- **Phase 2.3B (Outbox Engine & Connection Pinning):** Implement disaggregated operation leases, supplier capability adapters, dual-condition pinned IP HTTP transport, and hardened wallet finalization.
- **Phase 2.3C (Adversarial Test Suite):** Implement and execute the 60 target test cases covering capability modes, crash recovery, evidence validation, and SSRF defenses (Target Suite: 256 passing tests).

---
*End of Milestone 2.3 Architecture Proposal V3 Final Addendum (Revision 4).*

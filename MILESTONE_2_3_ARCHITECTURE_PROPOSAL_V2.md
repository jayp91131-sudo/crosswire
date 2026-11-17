# CROSSWIRE 2.3 — ARCHITECTURE PROPOSAL V2
## Multi-Supplier Routing and Split-Inventory Transaction Integrity
### Principal Distributed Systems & Financial Integrity Revision

**Audit & Revision Date:** November 10, 2026  
**Author:** Principal Distributed Systems Architect, Travel Platform Engineering & Security Review  
**Status:** REVISED ARCHITECTURAL BLUEPRINT (PROPOSAL V2)  
**Target Milestone:** Crosswire Milestone 2.3  
**Repository:** `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`  
**Reference Standards:** `MASTER_PLAN_V2.md`, Milestone 2.2 Final Closure Reports  

---

## 1. Executive Summary & Review of V1 Shortcomings

The initial architectural proposal for Milestone 2.3 established the core concept of a parent-child hierarchical booking saga. However, a rigorous devil's advocate engineering audit identified critical distributed-systems and financial-safety vulnerabilities in V1:

1. **Failure Conflation:** V1 treated timeouts and ambiguous connection drops similarly to definitive rejections (e.g., 409 Sold Out), leading to dangerous premature release of wallet holds and stranded inventory.
2. **Capability Over-Generalization:** V1 assumed creation idempotency implied cancellation idempotency, and assumed negative lookups proved an operation never committed.
3. **Evidence Suppression via Schema Constraints:** V1 placed a `UNIQUE(child_intent_id)` constraint on `supplier_reservations`. This prevented the platform from recording duplicate supplier reservations as empirical evidence of upstream defects.
4. **Fencing Scope Ambiguity:** V1 did not define independent parent and child fencing tokens, creating a vulnerability where a delayed child worker could overwrite parent cancellation state.
5. **Superficial Financial Modeling:** V1 conflated agency gross billing with supplier net cost, assumed 100% margin pass-through, and relied on floating-point penalty rates.
6. **In-Memory Durability Fragility:** V1 relied on runtime `asyncio.gather()` as its primary orchestration mechanism without an atomic transactional outbox.

This V2 proposal resolves all six structural flaws. It delivers a battle-tested, durable, fail-closed design that preserves every Milestone 2.2 invariant while enabling split-inventory fulfillment.

---

## 2. Definitive vs. Ambiguous State Taxonomy

In distributed travel systems, network and upstream failures must **never** be lumped into a generic `FAILED` bucket. Doing so causes financial leakage, dual-booking, or stranded liability.

### 2.1 Child Leg Dispatched States

```
                                  [CHILD_PENDING]
                                         |
                                         v (Outbox Lease Acquired)
                                  [CHILD_CLAIMED]
                                         |
                                         v (HTTP Request Dispatched)
                             [SUPPLIER_REQUEST_PENDING]
                                         |
             +---------------------------+---------------------------+
             |                           |                           |
             v (HTTP 200 Confirmed)      v (409 Sold Out / 400 Bad)  v (Socket Timeout / 502 / Conn Reset)
       [CONFIRMED]              [DEFINITIVE_FAILURE]        [SUPPLIER_UNKNOWN]
             |                           |                           |
             | (Parent Aborts)           | (Clean Abort)             | (Lookup / Recovery Scan)
             v                           v                           v
   [CANCELLATION_PENDING]        [TERMINAL_ABORT]        +-----------+-----------+
             |                                           |                       |
     +-------+-------+                                   v (Positively Found)    v (NEITHER / Unresolved)
     |               |                              [CONFIRMED]         [RECONCILIATION_REQUIRED]
     v (Cancel 200)  v (Cancel Fail / Timeout)
[CANCELLED]   [RECONCILIATION_REQUIRED]
```

### 2.2 Precise Child State Definitions
1. **`CONFIRMED`:** Upstream supplier responded with an unequivocal booking confirmation (HTTP 200 with non-empty reservation reference).
2. **`DEFINITIVE_FAILURE`:** Upstream supplier definitively rejected the booking before committing inventory (e.g., HTTP 409 Room Sold Out, HTTP 400 Validation Error, HTTP 403 Forbidden). No inventory was allocated.
3. **`SUPPLIER_UNKNOWN`:** Ambiguous dispatch outcome (e.g., socket read timeout, connection reset, 502/503/504 gateway error). The request was sent, but the aggregator has no acknowledgment. Inventory may or may not be committed upstream.
4. **`CANCELLATION_PENDING`:** Cancellation request dispatched upstream; awaiting acknowledgment.
5. **`CANCELLED`:** Upstream supplier unequivocally acknowledged cancellation of the reservation.
6. **`RECONCILIATION_REQUIRED`:** A terminal ambiguous state. Occurs when:
   - A `SUPPLIER_UNKNOWN` leg cannot be resolved via lookup (capability `NEITHER` or lookup returns 500).
   - A `CANCELLATION_PENDING` leg times out or fails upstream.
   - Requires out-of-band operator or clearinghouse settlement.

### 2.3 Strict Parent Aggregate State Transition Rules

The parent booking coordinates $K$ child legs ($C_1, \dots, C_K$). A parent booking **must never** transition to a clean terminal state or release funds until every child leg has reached a final, unambiguous state.

| Child States Distribution ($C_1 \dots C_K$) | Parent Booking State | Agency Wallet Action | Voucher Action |
| :--- | :--- | :--- | :--- |
| **All $K$ are `CONFIRMED`** | `ALL_CONFIRMED` $\to$ `VOUCHER_ISSUED` | **Capture Total Hold** | **Issue Parent Voucher** |
| **$\ge 1$ `DEFINITIVE_FAILURE`, other legs `CANCELLED` or `DEFINITIVE_FAILURE`** | `FAILED` | **Release Total Hold in Full** | None (`NULL`) |
| **$\ge 1$ `CONFIRMED`, $\ge 1$ `DEFINITIVE_FAILURE`** | `COMPENSATING` $\to$ Dispatches cancellation for confirmed legs | **Hold Kept Locked** | None (`NULL`) |
| **$\ge 1$ `SUPPLIER_UNKNOWN` (any remaining legs)** | `SUPPLIER_UNKNOWN` $\to$ Suspends resolution | **Hold Kept Locked** | None (`NULL`) |
| **$\ge 1$ `RECONCILIATION_REQUIRED` (any remaining legs)** | `RECONCILIATION_REQUIRED` | **Hold Kept Locked** (Prevents agency from spending funds that may owe upstream) | None (`NULL`) |
| **User cancels confirmed booking, all $K$ legs reach `CANCELLED`** | `CANCELLED` $\to$ `REFUND_PENDING` | Credit Wallet for aggregate net refund entitlement | Revoke Voucher (`CANCELLED`) |
| **User cancels confirmed booking, $\ge 1$ leg reaches `RECONCILIATION_REQUIRED`** | `CANCEL_SUSPENDED_RECONCILIATION` | Process refund only for cleanly cancelled legs; hold contested portion | Revoke Voucher (`CANCELLED`) |

---

## 3. Independent Per-Operation Supplier Capability Model

A major flaw in naive travel engines is assuming that a supplier with idempotent booking creation also supports idempotent cancellation. In reality, supplier APIs are asymmetric.

### 3.1 Two-Dimensional Capability Taxonomy
Every upstream adapter in `supplier_routing_registry` must declare its capabilities **per operation**:

```python
class CreationCapability(str, Enum):
    IDEMPOTENT_WITH_LOOKUP = "IDEMPOTENT_WITH_LOOKUP"  # Native header idempotency + query endpoint
    LOOKUP_ONLY = "LOOKUP_ONLY"                        # Query endpoint only; POST is NOT idempotent
    NEITHER = "NEITHER"                                # Blind dispatch; no query, no idempotency (FAIL CLOSED)

class CancellationCapability(str, Enum):
    IDEMPOTENT_WITH_LOOKUP = "IDEMPOTENT_WITH_LOOKUP"  # Idempotent cancel + status query endpoint
    LOOKUP_ONLY = "LOOKUP_ONLY"                        # Cancellation query supported; cancel request non-idempotent
    NEITHER = "NEITHER"                                # Cancel request is fire-and-forget / non-idempotent
```

### 3.2 Rigorous Recovery Rules by Capability Pair

#### Creation Ambiguity Recovery:
1. **`CreationCapability.NEITHER`:**
   - **Zero Automatic Retries:** If timeout occurs, the worker **must never** re-send the request.
   - **Zero Synthetic Guesses:** The worker cannot call a lookup endpoint that does not exist.
   - **Immediate Escalation:** Child state transitions directly to `SUPPLIER_UNKNOWN` $\to$ `RECONCILIATION_REQUIRED`.
2. **`CreationCapability.LOOKUP_ONLY`:**
   - Worker queries the upstream lookup endpoint by correlation ID / intent reference.
   - *Positive Lookup (Found):* Adopt the reservation and transition to `CONFIRMED`.
   - *Negative Lookup (Not Found):* **CRITICAL INVARIANT:** A negative lookup does **not** prove the delayed request will not commit in the next second. The worker **must not** redispatch blindly. It must mark the leg `SUPPLIER_UNKNOWN` and initiate bounded polling. If it remains not found after the timeout window, it enters `RECONCILIATION_REQUIRED`.
3. **`CreationCapability.IDEMPOTENT_WITH_LOOKUP`:**
   - Safe to query lookup first. If found, adopt.
   - If not found, safe to replay creation request with the **exact same child idempotency key**. Upstream deduplication guarantees at most one reservation.

#### Cancellation Ambiguity Recovery:
1. **`CancellationCapability.NEITHER`:**
   - If cancel request times out, worker **must not** spam repeated cancel requests (which may return 400 and cause false failure assumptions). Child leg enters `RECONCILIATION_REQUIRED`.
2. **`CancellationCapability.LOOKUP_ONLY` / `IDEMPOTENT_WITH_LOOKUP`:**
   - Query reservation status. If `CANCELLED`, mark child `CANCELLED`. If still `CONFIRMED` and capability is `IDEMPOTENT_WITH_LOOKUP`, re-issue cancel request.

---

## 4. Reservation Evidence Model & Invariant Oracle

### 4.1 Schema Correction: Uniqueness Separation
V1 erroneously placed `UNIQUE(child_intent_id)` on the reservation table. In V2, we strictly separate **the expected logical child intent** from **the empirical observations of supplier reservations**.

```sql
-- Logical Fulfillment Units (The Plan)
CREATE TABLE child_supplier_intents (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    parent_intent_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    leg_sequence INTEGER NOT NULL,          -- 1 .. K
    room_type TEXT NOT NULL,
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,
    allocated_amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    cancellation_policy_type TEXT NOT NULL,
    cancellation_penalty_rate_bps INTEGER NOT NULL DEFAULT 2000, -- 2000 bps = 20.00%
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_booking_id) REFERENCES booking_records(booking_id),
    FOREIGN KEY (parent_intent_id) REFERENCES booking_intents(intent_id),
    UNIQUE (parent_booking_id, leg_sequence)
);

-- Empirical Upstream Observations (The Physical Reality)
-- CRITICAL FIX: No UNIQUE constraint on child_intent_id!
-- Allows recording accidental duplicates to prove invariant violations.
CREATE TABLE supplier_reservation_observations (
    observation_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    supplier_reservation_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CONFIRMED', -- 'CONFIRMED', 'CANCELLED'
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id) REFERENCES child_supplier_intents(child_intent_id),
    FOREIGN KEY (parent_booking_id) REFERENCES booking_records(booking_id),
    -- Provider-scoped uniqueness: a single supplier cannot issue the same reservation ID twice
    UNIQUE (supplier_id, supplier_reservation_id)
);
CREATE INDEX idx_obs_child ON supplier_reservation_observations (child_intent_id);
```

### 4.2 Invariant 5.2 Oracle Formulations

#### Check 1: Cardinality Alignment
For any parent booking intent with declared expected cardinality $K$:
$$\text{Count}\left(R \in \text{Observations} \mid \text{status}(R) = \text{'CONFIRMED'} \land R.\text{parent\_booking\_id} = B\right) \equiv K$$

#### Check 2: Logical Child Leg Duplication Detection (SQL Oracle Query)
```sql
-- If this query returns ANY rows, Invariant 5.2 is VIOLATED:
SELECT 
    child_intent_id,
    COUNT(observation_id) AS confirmed_reservation_count
FROM supplier_reservation_observations
WHERE status = 'CONFIRMED'
GROUP BY child_intent_id
HAVING COUNT(observation_id) > 1;
```

#### Check 3: Total Cardinality Overflow Detection (SQL Oracle Query)
```sql
-- If confirmed observations exceed parent expected cardinality, Invariant 5.2 is VIOLATED:
SELECT 
    b.booking_id,
    bi.expected_cardinality,
    COUNT(sro.observation_id) AS actual_confirmed_count
FROM booking_records b
JOIN booking_intents bi ON b.intent_id = bi.intent_id
JOIN supplier_reservation_observations sro ON b.booking_id = sro.parent_booking_id
WHERE sro.status = 'CONFIRMED'
GROUP BY b.booking_id, bi.expected_cardinality
HAVING COUNT(sro.observation_id) > bi.expected_cardinality;
```

---

## 5. Two-Tier Parent-Child Concurrency and Fencing

A critical distributed race condition occurs if a child worker completes an operation and attempts to update the parent booking record while the parent is being concurrently cancelled by an agency or timed out by an orchestrator.

### 5.1 Independent Fencing Token Scopes
1. **Parent Operation Claim (`booking_operation_claims`):**
   - Keys on `(agency_id, idempotency_key)`.
   - Protects the parent booking lifecycle (`BOOKING_PENDING`, `COMPENSATING`, `VOUCHER_ISSUED`, `CANCELLED`).
   - Carries **Parent Fencing Token** ($F_{\text{parent}}$).
2. **Child Operation Claim (`child_operation_claims`):**
   - Keys on `child_intent_id`.
   - Protects upstream dispatch to Supplier $i$ (`DISPATCHING`, `CONFIRMED`, `FAILED`).
   - Carries **Child Fencing Token** ($F_{\text{child}, i}$).

### 5.2 Parent State Serialization Protocol
A child worker **is never permitted** to write directly to `booking_records.status`. It can only write to its own `child_supplier_intents` and `supplier_reservation_observations` tables under its child fencing token $F_{\text{child}, i}$.

Parent state aggregation is governed by the **Parent Coordinator**:
1. When all child operations settle, the parent coordinator acquires/validates the parent claim ($F_{\text{parent}}$).
2. It executes an atomic database transaction:
   ```sql
   -- Atomically verify parent fencing token before advancing to VOUCHER_ISSUED:
   UPDATE booking_operation_claims
   SET status = 'COMPLETED', updated_at = datetime('now')
   WHERE agency_id = :agency_id 
     AND idempotency_key = :idemp_key 
     AND fencing_token = :expected_parent_fencing_token;
   ```
3. If an agency cancellation arrived while children were dispatching, the cancellation process incremented $F_{\text{parent}} \leftarrow F_{\text{parent}} + 1$. The child aggregation update fails with `AggregatorFencingError`. The parent is safely cancelled, and delayed child confirmations are immediately routed to saga cancellation!

---

## 6. Durable Transactional Outbox Architecture

Relying on in-memory async primitives (`asyncio.gather()`) guarantees inventory leakage if the worker process crashes mid-flight. Crosswire 2.3 mandates a **Transactional Outbox**.

```
[Client Request: Split Booking Intent (K=2)]
                  |
                  v
[ATOMIC DATABASE TRANSACTION: BEGIN IMMEDIATE]
   |-- 1. Insert booking_intents (expected_cardinality = 2)
   |-- 2. Insert booking_records (status = 'BOOKING_PENDING')
   |-- 3. Insert child_supplier_intents (C1, C2: status = 'PENDING')
   |-- 4. Insert child_dispatch_outbox (Msg 1 for C1, Msg 2 for C2: status = 'ENQUEUED')
   |-- 5. Lock Agency Wallet Hold (Full ₹15,000)
   |-- 6. Insert booking_operation_claims (Parent Claim: F_parent = 1)
   +-- COMMIT
                  |
                  +-----------------------------------+
                  | (Post-Commit Dispatch Pipeline)   |
                  v                                   v
       [Worker/Task A for C1]              [Worker/Task B for C2]
       Lease Outbox Msg 1 ($F_{c1}=1$)     Lease Outbox Msg 2 ($F_{c2}=1$)
       HTTP POST to Supplier Alpha         HTTP POST to Supplier Beta
                  |                                   |
                  v                                   v
       Record Outcome in DB                Record Outcome in DB
       Ack Outbox Msg 1                    Ack Outbox Msg 2
                  |                                   |
                  +-----------------+-----------------+
                                    |
                                    v
                     [Parent Coordinator Aggregation]
                     Evaluates C1 + C2 in Database
                     Acquires Parent Fencing Token ($F_{\text{parent}}$)
                     Advances Parent State (VOUCHER or SAGA CANCEL)
```

### Outbox Schema Specification
```sql
CREATE TABLE child_dispatch_outbox (
    outbox_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ENQUEUED', -- 'ENQUEUED', 'IN_FLIGHT', 'ACKED', 'FAILED'
    owner_token TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 1,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id) REFERENCES child_supplier_intents(child_intent_id),
    FOREIGN KEY (parent_booking_id) REFERENCES booking_records(booking_id)
);
CREATE INDEX idx_outbox_poll ON child_dispatch_outbox (status, lease_expires_at);
```

---

## 7. Multi-Tier Financial Accounting & Minor-Unit Math

### 7.1 Tiered Financial Model
In professional travel distribution, the amount charged to the B2B agency is **not** identical to the gross cost paid to upstream suppliers. Conflating them causes severe accounting corruption.

For every split booking intent:
1. **Agency Parent Gross (`amount_minor`):** Total minor units billed to the agency.
2. **Agency Child Allocation (`allocated_amount_minor`):** Portion of the agency billing assigned to leg $i$.
3. **Supplier Net Cost (`supplier_cost_minor`):** Actual wholesale cost payable to Supplier $i$.
4. **Agency Markup (`markup_minor`):** Aggregator margin: $\text{GrossChild}_i - \text{NetSupplier}_i$.
5. **Upstream Taxes & Fees (`taxes_minor`):** Included in Net Cost or itemized.

### 7.2 Integer Minor-Unit Mathematical Proofs

#### Equation 1: Agency Allocation Completeness (Zero Financial Leakage)
The sum of agency child allocations must match the agency parent gross with zero tolerance:
$$\sum_{i=1}^K \text{ChildIntent}_i\text{.allocated\_amount\_minor} \equiv \text{ParentBooking.amount\_minor}$$
*Enforcement:* If $\Delta \neq 0$, the request fails validation at schema entry before any wallet hold or outbox insertion.

#### Equation 2: Exact Basis-Point Cancellation Penalty
Floating-point penalty rates (e.g., `0.20`) are banned. Cancellation policies are configured in integer **basis points** ($\text{bps}$), where $1 \text{ bps} = 0.01\%$ ($10,000 \text{ bps} = 100\%$):
$$\text{Penalty\_minor}_i = \left\lfloor \frac{\text{Allocated\_minor}_i \times \text{penalty\_bps}_i + 5000}{10000} \right\rfloor \quad \text{(Exact Round-Half-Up)}$$
$$\text{LegEntitlement\_minor}_i = \text{Allocated\_minor}_i - \text{Penalty\_minor}_i$$
$$\text{TotalRefundEntitlement\_minor} = \sum_{i=1}^K \text{LegEntitlement\_minor}_i$$

*Proof of Non-Negative Bounds:*
$$\forall i, \quad 0 \le \text{penalty\_bps}_i \le 10000 \implies 0 \le \text{Penalty\_minor}_i \le \text{Allocated\_minor}_i$$
$$\implies 0 \le \text{TotalRefundEntitlement\_minor} \le \text{ParentBooking.amount\_minor}$$

### 7.3 Multi-Leg Double-Entry Journal Batching (Invariant 5.7)
When $K$ child legs confirm, the single agency wallet debit balances across discrete supplier payable sub-accounts and aggregator margin:
```
Journal Batch: BATCH-SPLIT-CONFIRM-{booking_id}
  DR  Account:2100 (Agency Wallet Liability)       ₹15,000  (Parent Gross)
  CR  Account:2201 (Supplier Alpha Payable)                 ₹7,000  (Leg 1 Net Cost)
  CR  Account:2202 (Supplier Beta Payable)                  ₹6,500  (Leg 2 Net Cost)
  CR  Account:4100 (Aggregator Booking Margin)              ₹1,500  (Gross Markup)
----------------------------------------------------------------------------------
Total Debits = ₹15,000  |  Total Credits = ₹15,000  (Sum(Debits) - Sum(Credits) == 0)
```

---

## 8. Database Tenant and Referential Integrity Constraints

To guarantee tenant isolation and eliminate cross-agency object injection at the database engine level, SQLite composite foreign keys and check constraints are enforced:

```sql
-- Enforce Agency Match between Parent Booking and Child Intents
-- Step 1: Add composite unique key on booking_records
CREATE UNIQUE INDEX IF NOT EXISTS uidx_bkg_id_agency ON booking_records (booking_id, agency_id);

-- Step 2: Child table enforces composite FK to prevent tenant mismatch
-- In SQLite:
-- FOREIGN KEY (parent_booking_id, agency_id) REFERENCES booking_records(booking_id, agency_id)

-- Step 3: Enforce positive financial allocations
ALTER TABLE child_supplier_intents ADD CONSTRAINT chk_pos_alloc 
CHECK (allocated_amount_minor > 0);

ALTER TABLE child_supplier_intents ADD CONSTRAINT chk_pos_bps 
CHECK (cancellation_penalty_rate_bps >= 0 AND cancellation_penalty_rate_bps <= 10000);
```

---

## 9. Split-Inventory Scope Definition (Milestone 2.3 Boundaries)

To eliminate ambiguity between product scope and schema extensibility:
- **In-Scope (M2.3):**
  - **Single Hotel Split-Room Booking:** An agency books 2 rooms for the same dates at the same hotel, fulfilled via 2 distinct upstream suppliers (e.g., Room 1 via Supplier Alpha, Room 2 via Supplier Beta).
  - **Single Hotel Split-Date Booking:** An agency books a 3-night stay at the same hotel, split across suppliers (Nights 1–2 via Supplier Alpha, Night 3 via Supplier Beta).
- **Strictly Out-of-Scope (Banned in M2.3):**
  - Multi-hotel bookings.
  - Flights, GDS/NDC, rail, car rental.
  - Foreign exchange conversion or cross-currency settlement (All legs strictly share parent currency `INR` or `EUR`).
  - Dynamic bundling or packaging margin algorithms.

---

## 10. Rewritten 10 Adversarial Scenarios & Expected Behavior

| Scenario ID | Architectural Failure Class | Naive / Vulnerable Behavior | Resilient V2 Specification | Invariant Verified |
| :--- | :--- | :--- | :--- | :--- |
| **ADV-2.3-01** | **All Suppliers Succeed** (Happy Path) | Dispatches in-memory; emits voucher before all child rows commit. | Outbox leases dispatch concurrently. Both legs reach `CONFIRMED`. Parent coordinator validates $|\text{Confirmed}| = 2$, captures hold, issues single voucher. | Invariants 5.1, 5.2, 5.4 |
| **ADV-2.3-02** | **Supplier 1 Confirms, Supplier 2 Sold Out** | Leaves Supplier 1 confirmed; deducts partial hold; strands customer with half a booking. | Supplier 2 returns 409 $\implies$ `DEFINITIVE_FAILURE`. Outbox dispatches compensating cancellation for Supplier 1. Upon cancel acknowledgment, hold is released in full, parent marks `FAILED`. Zero customer charge, zero orphaned inventory. | Invariants 5.1, 5.4 |
| **ADV-2.3-03** | **Supplier 1 Commits, Response Lost (Timeout)** | Treats timeout as rejection; cancels Supplier 2; leaves Supplier 1 orphaned upstream. | ReadTimeout transitions Leg 1 to `SUPPLIER_UNKNOWN`. **Wallet hold remains locked**. If `IDEMPOTENT`, lookup discovers reservation and adopts. If `NEITHER`, leg halts in `RECONCILIATION_REQUIRED`. Parent does not release funds. | Invariants 5.1, 5.6 |
| **ADV-2.3-04** | **Both Commit, Aggregator Dies Before Recording** | Both reservations active upstream; customer told booking failed; aggregator database wiped. | Dispatches tracked in durable `child_dispatch_outbox` and `child_operation_claims`. Post-restart recovery worker leases expired outbox claims ($F \leftarrow F+1$). If supplier has lookup, discovers both and confirms parent. If `NEITHER`, flags `RECONCILIATION_REQUIRED` with full audit logs. | Invariant 5.6, SEC-01 |
| **ADV-2.3-05** | **Cancellation Races with Delayed Confirmation** | User cancels while Leg 2 delayed; Leg 2 arrives late and confirms; voucher issued on cancelled booking. | User cancellation increments Parent Fencing Token ($F_{\text{parent}} \leftarrow F_{\text{parent}} + 1$). When delayed Leg 2 returns, parent coordinator rejects write via fencing error (`AggregatorFencingError`). Delayed Leg 2 is immediately sent to compensating cancellation. | Invariants 5.4, 5.6, SEC-03 |
| **ADV-2.3-06** | **Leg 1 Cancel Fails, Leg 2 Cancel Succeeds** | Aggregator marks parent `CANCELLED`, refunds entire amount, leaking Supplier 1 payable. | Leg 2 marks `CANCELLED`. Leg 1 cancel failure transitions Leg 1 to `RECONCILIATION_REQUIRED`. Parent enters `CANCEL_SUSPENDED_RECONCILIATION`. Refund for Leg 1 is blocked until operator resolves upstream cancellation. | Invariant 5.5, SEC-05 |
| **ADV-2.3-07** | **Concurrent Child Intent Replay** | Rapid double-clicks spawn two simultaneous dispatch threads for Leg 1, creating duplicate room bookings. | Unique database claim on `child_operation_claims(child_intent_id)`. Second thread fails lease acquisition (`acquired = False`), waits on first thread outcome. Exactly 1 request dispatched upstream. | Invariant 5.2, SEC-03 |
| **ADV-2.3-08** | **Partial Leg Cancellation** (1 of 2 rooms cancelled) | Flat penalty applied across total parent gross, miscalculating refund. | Evaluates specific cancelled leg's `cancellation_penalty_rate_bps`. Calculates exact minor-unit entitlement $\text{Allocated}_1 - \text{Penalty}_1$. Credits wallet strictly for Leg 1; Leg 2 remains `CONFIRMED`. | Invariants 5.1, 5.5 |
| **ADV-2.3-09** | **Recovery Worker Dies Mid-Compensation** | Compensation task lost in memory; upstream room never cancelled. | Compensation tasks recorded in `saga_recovery_records` with leasing (`lease_expires_at`). Restarted worker steals expired lease ($F \leftarrow F+1$), re-evaluates upstream state, and completes cancellation. | SEC-01, SEC-03 |
| **ADV-2.3-10** | **Cross-Tenant Injection Attack** | Agency B injects Child Intent ID belonging to Agency A into its own booking payload. | Database enforces composite foreign key `(parent_booking_id, agency_id)`. Attempted cross-agency assignment is rejected with `sqlite3.IntegrityError: FOREIGN KEY constraint failed`. | Tenant Security (M4) |

---

## 11. Acceptance Test Plan & Verification Criteria

```text
========================================================================================
ACCEPTANCE TEST SUITE MATRIX: MILESTONE 2.3
========================================================================================
Test Category              Test Count    Target Verification
----------------------------------------------------------------------------------------
Preservation Baseline      196 tests     All Milestone 2.2 regression tests pass untouched
Split Allocation Engine     12 tests     Exact integer minor-unit and basis-point math proofs
Transactional Outbox       10 tests     Outbox leasing, fencing, and crash-resume recovery
Multi-Supplier Routing       8 tests     Fail-closed registry allowlist, per-operation capability
Adversarial Failures        10 tests     ADV-2.3-01 through ADV-2.3-10 automated proofs
Schema Integrity             6 tests     Composite tenant foreign keys, duplicate observation
----------------------------------------------------------------------------------------
Total Acceptance Tests     242 tests     Target: 100% Pass, 0 Failures
========================================================================================
```

---

## 12. Unresolved Technical Risks & Real-World Limitations

1. **The Inevitability of Manual Reconciliation:**  
   If an upstream supplier implements `CreationCapability.NEITHER` (no query, no idempotency) and experiences a socket timeout while a second supplier is sold out, software **cannot mathematically resolve this state safely without human intervention**. The system must hold agency funds and trigger operations alerts.
2. **SQLite Locking in Multi-Worker Environments:**  
   Scatter-gather dispatching increases write transaction contention on SQLite. While fully supported for the testbed using WAL mode and `timeout=30.0s`, a production deployment would require migration to PostgreSQL with row-level locks (`FOR UPDATE`).

---

## 13. Final Feasibility Recommendation

### Recommendation: **CONDITIONAL GO**

**Mandatory Conditions for Entering Milestone 2.3 Implementation:**
1. **Preserve M2.2 Codebase:** Do not modify any working Milestone 2.2 files or test fixtures until the Milestone 2.3 testbed scaffolding is complete.
2. **Execute in Two Strict Sub-Phases:**
   - *Phase 2.3A (Data Model, Registry & Outbox Foundation):* Land database schema extensions, composite foreign keys, outbox tables, and baseline multi-supplier positive routing.
   - *Phase 2.3B (Scatter-Gather Orchestrator & Adversarial Hardening):* Land concurrent outbox workers, child fencing tokens, partial saga compensations, and the 10 adversarial scenario tests.
3. **No Product Scope Creep:** Strictly hotel rooms, strictly single-currency minor units.

---
*End of Architectural Proposal V2 — Milestone 2.3*

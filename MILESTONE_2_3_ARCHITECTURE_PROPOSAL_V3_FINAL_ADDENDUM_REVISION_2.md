# Milestone 2.3 Architecture Proposal V3: Final Technical Addendum (Revision 2)
## Multi-Supplier Routing, Capability-Aware Retries, Evidence Verification, Slot Occupancy, Card Recovery, and Connection Pinning

**Document Reference:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM_REVISION_2.md`  
**Author:** Principal Distributed Systems Architect, Travel Platform Transaction-Integrity Engineer, and Application Security Reviewer  
**Status:** Pre-Implementation Specification Gate (Revision 2)  
**Target Milestone:** Crosswire 2.3 (Multi-Supplier Routing & Split-Inventory Transaction Integrity)  
**Baseline Verification:** Milestone 2.2 Frozen Baseline (196 tests passing, 0 failed, 1 warning)

---

## 1. Concise List of Corrections Made

This document supersedes `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM.md` and provides precise technical corrections to the six core architectural areas:

1. **Supplier Capability-Aware Retries:** 
   - Explicitly disaggregates retry behavior across the three canonical capabilities (`IDEMPOTENT_WITH_LOOKUP`, `LOOKUP_ONLY`, `NEITHER`) separately for `CREATE_RESERVATION` and `CANCEL_RESERVATION`.
   - Prohibits blind redispatch for `LOOKUP_ONLY` and `NEITHER`.
   - Enforces the **Negative Lookup Rule**: a negative lookup (`NOT_FOUND`) is not proof of no-commit; bounded polling is required before escalating to `RECONCILIATION_REQUIRED`.
   - Embeds supplier identity and provider account scope into the idempotency key namespace: `"cw-op:{supplier_id}:{provider_account_id}:{agency_id}:{child_id}:{op_type}"`.
2. **Actual Supplier Reservation Evidence Verification:**
   - Replaces planned segment verification with verification of durable `supplier_reservation_observations` (reservation reference, property, dates, room category, occupancy capacity, and cryptographic response hash).
   - The coverage oracle rejects bookings where the planned segment spec is satisfied but actual supplier evidence differs.
   - Formally specifies handling of late, duplicate, and contradictory supplier responses.
3. **Rigorous Slot-Level Occupancy Semantics:**
   - Enforces independent per-slot occupancy verification: capacity surplus in one room slot **cannot** subsidize a capacity shortfall in another.
   - Strictly enforces half-open date intervals $[check\_in, check\_out)$ with zero intra-slot gaps and zero intra-slot overlaps.
   - Validates total guest count mode by requiring a valid non-zero integer partition $(g_0, \dots, g_{N-1})$ across all confirmed room slots.
4. **Complete Card-Payment Crash Recovery:**
   - Formalizes the complete lifecycle of `CAPTURE_CARD` in `parent_payment_outbox` outside SQLite write locks.
   - Exhaustively maps crash scenarios: crash before gateway call, crash during gateway call, crash after gateway capture before local persistence, crash before parent finalization, and local finalization lock failure.
   - Prohibits blind retries on ambiguous timeouts; enforces authoritative lookup resolution before voucher generation or compensation.
   - Establishes idempotency constraints on `ledger_events` and `voucher_records`.
5. **Concrete Target Connection Pinning and Anti-SSRF:**
   - Eliminates DNS rebinding windows by resolving DNS records once, validating every resolved IPv4/IPv6 address against prohibited ranges, and connecting the TCP socket **directly to the pre-validated IP address**.
   - Preserves TLS SNI (`server_hostname`) and HTTP `Host` header for strict certificate verification.
   - Restricts testbed loopback to explicitly declared registry entries and enforces `follow_redirects=False` with cross-origin credential stripping.
6. **Hardened Local Wallet Finalization:**
   - Implements conditional SQL mutations (`WHERE status = 'IN_PROGRESS' AND fencing_token = :f`) with affected-row assertions (`rowcount == 1`).
   - Verifies claim ownership, single active `HELD` hold, matching currency, and integer minor units with zero FX conversion.

---

## 2. Explicit State Machines and Transition Rules

### 2.1 Child Intent Lifecycle State Machine

```
                    +--------------------+
                    |      PENDING       |
                    +---------+----------+
                              | (Worker leases CREATE outbox entry)
                              v
                    +--------------------+
                    |     DISPATCHED     |
                    +---+----+-----+-----+
                        |    |     |
      (Confirmed)       |    |     | (Network Timeout / Socket Drop / 504)
     +------------------+    |     +-------------------------+
     |                       |                               |
     v                       | (Definitive 409 / Sold Out)   v
+-----------+                v                         +------------------+
| CONFIRMED |      +--------------------+              | SUPPLIER_UNKNOWN |
+-----+-----+      | DEFINITIVE_FAILURE |              +--------+---------+
      |            +--------------------+                       |
      | (Parent Compensation Triggered)                         | (Lookup inconclusive / NEITHER)
      v                                                         v
+----------------------+                               +-------------------------+
| CANCELLATION_PENDING |                               | RECONCILIATION_REQUIRED |
+-----+----------+-----+                               +-------------------------+
      |          |
      |          +----------------------------------------------+
      | (Cancel confirmed)                                      | (Cancel failed / timeout / NEITHER)
      v                                                         v
+-----------+                                          +-------------------------+
| CANCELLED |                                          | RECONCILIATION_REQUIRED |
+-----------+                                          +-------------------------+
```

### 2.2 Payment Outbox State Machine (`parent_payment_outbox`)

```
                      +---------------------------+
                      |          PENDING          |
                      +-------------+-------------+
                                    | (Worker leases capture operation)
                                    v
                      +---------------------------+
                      |        IN_PROGRESS        |
                      +------+--------------+-----+
                             |              |
    (HTTP 200 + Capture Ref) |              | (Socket Timeout / Network Drop / 5xx)
                             v              v
                      +-----------+  +-----------------+
                      | SUCCEEDED |  | PAYMENT_UNKNOWN |
                      +-----+-----+  +--------+--------+
                            |                 |
     (Phase 3 Local Commit) |                 | (Dispatch LOOKUP_CARD_CAPTURE)
                            v                 v
                      +-----------+  +-------------------------+
                      | CONFIRMED |  | RECONCILIATION_REQUIRED |
                      | (Voucher) |  +-------------------------+
                      +-----------+
```

### 2.3 Deterministic Parent Booking Reduction Table

The parent booking status is a deterministic reduction over all its child intent states and payment state:

| Child 1 State | Child 2 State | Payment State | Parent Booking Status | Active Wallet Hold / Card Auth Treatment | Action / Next Step |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `CONFIRMED` | `CONFIRMED` | `CAPTURED` | `CONFIRMED` | **Capture Full Hold** ($100\%$) | Coverage verified; Parent Voucher issued. |
| `CONFIRMED` | `CONFIRMED` | `PAYMENT_UNKNOWN` | `PAYMENT_AMBIGUOUS` | **Hold Retained** | Disallow voucher; dispatch `LOOKUP_CARD_CAPTURE`. |
| `CONFIRMED` | `DEFINITIVE_FAILURE` | `AUTHORIZED` | `CANCELLATION_PENDING` | **Hold Retained** | Enqueue `CANCEL_RESERVATION` for Child 1. |
| `CONFIRMED` | `SUPPLIER_UNKNOWN` | `AUTHORIZED` | `RECONCILIATION_REQUIRED` | **Hold Retained** | Ambiguous commitment on Child 2. Hold locked. |
| `CONFIRMED` | `RECONCILIATION_REQUIRED`| `AUTHORIZED` | `RECONCILIATION_REQUIRED` | **Hold Retained** | Blocked for manual human investigation. |
| `DEFINITIVE_FAILURE`| `DEFINITIVE_FAILURE` | `AUTHORIZED` | `FAILED` | **Full Release** ($0\%$ penalty) | Zero reservations exist. Clean release. |
| `CANCELLED` | `CANCELLED` | `AUTHORIZED` | `CANCELLED` | **Release Net of Penalties** | Both legs cancelled. Post cancellation fees. |
| `CANCELLED` | `RECONCILIATION_REQUIRED`| `AUTHORIZED` | `RECONCILIATION_REQUIRED` | **Hold Retained** | Child 2 cancel unconfirmed. Hold locked. |

---

## 3. Data Model and Uniqueness/Foreign-Key Requirements

To ensure complete referential and tenant integrity in SQLite without invalid `ALTER TABLE ADD CONSTRAINT` syntax, all tables use inline composite unique and foreign keys.

```sql
-- Composite unique index to allow composite foreign keys referencing booking_records
CREATE UNIQUE INDEX IF NOT EXISTS uidx_booking_records_id_agency 
ON booking_records (booking_id, agency_id);

-- Child Supplier Intents Table
CREATE TABLE IF NOT EXISTS child_supplier_intents (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'DISPATCHED', 'CONFIRMED', 'DEFINITIVE_FAILURE', 
        'SUPPLIER_UNKNOWN', 'CANCELLATION_PENDING', 'CANCELLED', 'RECONCILIATION_REQUIRED'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_booking_id, agency_id) 
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_intents_composite 
ON child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id);

-- Supplier Reservation Observations (Authoritative Evidence)
CREATE TABLE IF NOT EXISTS supplier_reservation_observations (
    observation_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    supplier_reservation_id TEXT NOT NULL,
    confirmed_hotel_id TEXT NOT NULL,
    confirmed_check_in TEXT NOT NULL,
    confirmed_check_out TEXT NOT NULL,
    confirmed_room_type TEXT NOT NULL,
    confirmed_guest_capacity INTEGER NOT NULL CHECK (confirmed_guest_capacity > 0),
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    raw_response_hash TEXT NOT NULL,
    payload_snapshot TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_child_unique 
ON supplier_reservation_observations (child_intent_id);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_supplier_reservation 
ON supplier_reservation_observations (supplier_id, supplier_reservation_id);

-- Parent Payment Outbox Table
CREATE TABLE IF NOT EXISTS parent_payment_outbox (
    payment_operation_id TEXT PRIMARY KEY,   -- Stable Logical ID: "{parent_booking_id}:CAPTURE_CARD"
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    payment_intent_id TEXT NOT NULL,
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
    FOREIGN KEY (parent_booking_id, agency_id) 
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_outbox_gateway_idemp 
ON parent_payment_outbox (gateway_idempotency_key);

-- Uniqueness constraints to guarantee idempotent local finalization
CREATE UNIQUE INDEX IF NOT EXISTS uidx_ledger_booking_event 
ON ledger_events (booking_id, event_type);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_vouchers_booking 
ON voucher_records (booking_id);
```

---

## 4. Failure and Recovery Behavior

### 4.1 Supplier Capability-Aware Dispatch & Recovery

#### Idempotency Key Namespace
$$\text{supplier\_idempotency\_key} = \text{"cw-op:"} + \text{sha256}(f"\{\text{supplier\_id}\}:\{\text{provider\_account\_id}\}:\{\text{agency\_id}\}:\{\text{child\_intent\_id}\}:\{\text{operation\_type}\}")[:32]$$

#### Capability Dispatch Execution Rules

1. **`IDEMPOTENT_WITH_LOOKUP` Mode:**
   - *Attempt 1:* Dispatches request with `supplier_idempotency_key` and canonical payload.
   - *Ambiguous Timeout / Connection Reset:* Recovery worker increments `fencing_token` ($F=2$), increments `attempts`, and re-dispatches using the **identical `supplier_idempotency_key`** and identical payload.
   - *Payload Integrity:* If the dispatch payload does not match stored `payload_hash`, the worker aborts with `PayloadTamperError`.
   - *Bounded Replay:* Max 3 attempts. If still unresolved, transitions to `RECONCILIATION_REQUIRED`.
2. **`LOOKUP_ONLY` Mode:**
   - *Attempt 1:* Dispatches request.
   - *Ambiguous Timeout:* Worker **must not replay CREATE**.
   - Enqueues `LOOKUP_RESERVATION` outbox operation.
   - *The Negative Lookup Rule:* If lookup returns `404 Not Found`, the worker **does not assume the booking was not created**. It polls up to 3 times with exponential backoff (e.g., 2s, 5s, 10s).
   - If lookup confirms reservation: records observation, advances child to `CONFIRMED`.
   - If lookup remains `404` after 3 attempts: escalates to `RECONCILIATION_REQUIRED`. No fresh creation request is ever issued.
3. **`NEITHER` Mode:**
   - *Attempt 1:* Dispatches request without idempotency guarantees.
   - *Ambiguous Timeout:* Zero automated retries. Zero automated lookups.
   - Child immediately transitions to `SUPPLIER_UNKNOWN` / `RECONCILIATION_REQUIRED`. Active financial hold is retained. Automated progress is frozen until human operational clearance.
4. **Cancellation Independence:**
   - `CANCEL_RESERVATION` uses independent logical IDs (`{child_intent_id}:CANCEL_RESERVATION`) and distinct cancellation keys (`"cw-op-cancel:" + hash(...)`). Capability is evaluated independently based on `cancellation_capability`.

---

### 4.2 Card-Payment Crash Recovery Matrix

| Crash Scenario | State at Failure | Recovery Mechanism | Invariant Protection |
| :--- | :--- | :--- | :--- |
| **Crash In-Flight** | Outbox `IN_PROGRESS`; worker dead. Gateway received call. | Lease expires. Recovery worker marks `PAYMENT_UNKNOWN` and enqueues `LOOKUP_CARD_CAPTURE`. Queries gateway by idempotency key. | Zero blind retries. No double charging. |
| **Crash Post-Capture** | Gateway captured; worker died before writing outbox. | Lookup queries gateway. Gateway returns `captured: true` with `capture_reference`. Worker records `SUCCEEDED` and proceeds to Phase 3. | Payment captured is authoritatively recorded. |
| **Crash Pre-Finalize** | Outbox `SUCCEEDED`; booking `PAYMENT_CAPTURE_PENDING`. | Background sweep detects `SUCCEEDED` payment outbox with pending booking. Resumes Phase 3 local finalization. | Voucher issued and ledger posted without re-calling gateway. |
| **Phase 3 Lock Failure** | Phase 3 transaction fails due to local SQLite lock or I/O error. | Transaction rolls back. Outbox remains `SUCCEEDED`. Phase 3 is retried locally. Idempotent via `UNIQUE(booking_id)`. | Exactly one ledger debit and one voucher created. |
| **Authoritative Decline** | Gateway returns HTTP 402 Card Declined / Expired. | Outbox marked `FAILED`. Parent booking transitions to `PAYMENT_FAILED`. Enqueues cancellations for confirmed children. | Zero vouchers issued; supplier legs compensated. |
| **Inconclusive Lookup** | Gateway lookup times out or returns inconclusive state. | Outbox escalates to `RECONCILIATION_REQUIRED`. Booking transitions to `PAYMENT_AMBIGUOUS`. | Holds retained; voucher strictly withheld. |

---

### 4.3 Concrete Connection Pinning and Anti-SSRF Architecture

```
                               Target URL: https://api.supplier-alpha.com/v1/bookings
                                                         |
                                                         v
                               [Step 1: Resolve ALL DNS Records via socket.getaddrinfo]
                                                         |
                                                         v
                               [Step 2: Validate EVERY Resolved IPv4 & IPv6 Address]
                               - Prohibit Loopback, Private, Link-Local, Metadata, Multicast
                               - If any IP fails -> REJECT IMMEDIATELY with SecurityViolation
                                                         |
                                                         v
                               [Step 3: Select Pin IP (e.g. 198.51.100.45)]
                                                         |
                                                         v
                               [Step 4: Custom Pinned Async Transport]
                               - Connect TCP Socket directly to (198.51.100.45, 443)
                               - Perform TLS Handshake with server_hostname="api.supplier-alpha.com"
                               - Transmit HTTP Host: "api.supplier-alpha.com"
                               - ZERO SECONDARY DNS LOOKUPS!
```

- **DNS Rebinding Defense:** The TCP socket connects directly to the IP address validated during step 2. The HTTP client library is prevented from executing a second, unvalidated DNS lookup during socket creation.
- **SNI & Certificate Validation:** TLS handshake wraps the socket with `server_hostname="api.supplier-alpha.com"`, ensuring TLS certificate validation and Subject Alternative Name (SAN) verification proceed against the intended domain name.
- **Prohibited Destination Ranges (Production/Staging):**
  - IPv4 Loopback: `127.0.0.0/8`
  - RFC 1918 Private: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
  - Link-Local / Cloud Metadata: `169.254.0.0/16`
  - Carrier-Grade NAT: `100.64.0.0/10`
  - IPv6 Loopback: `::1/128`
  - IPv6 Unique Local: `fc00::/7`
  - IPv6 Link-Local: `fe80::/10`
- **Testbed Loopback Restriction:** Testbed environments allow loopback destinations **only** if they match the exact pre-configured `testbed_registry` (e.g., `http://127.0.0.1:8001`). Mistaken public or metadata IP entries in testbed registries are rejected.
- **Redirect Policy:** `follow_redirects = False`. If HTTP 3xx is returned, the client intercepts the `Location` header, validates that the destination origin matches the approved supplier domain, strips credentials if crossing origins, and re-pins the socket.

---

### 4.4 Hardened Local Wallet Finalization

The local wallet finalization transaction enforces atomic integrity inside SQLite `BEGIN IMMEDIATE`:
```python
# Atomic Verification & Mutation
with db.begin_immediate() as conn:
    # Verify Parent Record and Claim Fence
    parent = conn.execute("SELECT * FROM booking_records WHERE booking_id = ?", (bkg_id,)).fetchone()
    if parent["fencing_token"] != expected_fence or parent["status"] != "IN_PROGRESS":
        raise StaleFencingTokenError()

    # Verify Single Active HELD Hold
    holds = conn.execute("SELECT * FROM wallet_holds WHERE booking_id = ? AND status = 'HELD'", (bkg_id,)).fetchall()
    if len(holds) != 1 or holds[0]["amount"] != parent["total_amount"] or holds[0]["currency"] != parent["currency"]:
        raise WalletHoldViolationError()

    # Verify Actual Observations via Coverage Oracle
    verify_slot_occupancy_and_coverage(spec, segments, observations)

    # Execute Conditional Mutations with Affected-Row Checks
    res_hold = conn.execute("UPDATE wallet_holds SET status='CAPTURED' WHERE hold_id=? AND status='HELD'", (holds[0]["hold_id"],))
    assert res_hold.rowcount == 1

    conn.execute("INSERT INTO ledger_events (event_id, booking_id, event_type, amount, currency) VALUES (?, ?, 'WALLET_CAPTURE', ?, ?)", ...)
    conn.execute("INSERT INTO voucher_records (voucher_id, booking_id, status) VALUES (?, ?, 'ISSUED')", ...)

    res_bkg = conn.execute("UPDATE booking_records SET status='CONFIRMED', fencing_token=fencing_token+1 WHERE booking_id=? AND status='IN_PROGRESS' AND fencing_token=?", (bkg_id, expected_fence))
    assert res_bkg.rowcount == 1
```

---

## 5. Positive and Negative Acceptance-Test Matrix

> [!NOTE]
> All test counts below represent **implementation target criteria**, not claims of current passage.

| Category | Type | Target Test Case Description | Expected Assertion |
| :--- | :--- | :--- | :--- |
| **Capabilities** | **Positive** | Retry `CREATE` on supplier explicitly declared `IDEMPOTENT_WITH_LOOKUP`. | Second attempt reuses identical `supplier_idempotency_key` and canonical payload; supplier returns original reservation without duplication. |
| **Capabilities** | **Negative** | Attempting retry of `CREATE` on `LOOKUP_ONLY` supplier. | Redispatch of CREATE is suppressed; `LOOKUP_RESERVATION` outbox task is created instead. |
| **Capabilities** | **Negative** | Attempting retry of `CREATE` on `NEITHER` supplier. | Zero retries and zero lookups; child immediately transitions to `SUPPLIER_UNKNOWN` / `RECONCILIATION_REQUIRED`. |
| **Capabilities** | **Negative** | Tampering with payload on retry (altering guest name/room code). | Outbox engine detects hash mismatch and raises `PayloadTamperError`. Replay blocked. |
| **Capabilities** | **Negative** | Reusing `CREATE` idempotency key for `CANCEL_RESERVATION`. | Outbox engine rejects key reuse; cancel uses distinct `cw-op-cancel:` namespace. |
| **Evidence** | **Positive** | Valid multi-room fulfillment with matching observations. | Observations match hotel, dates $[in, out)$, room type, and occupancy. Coverage oracle passes. |
| **Evidence** | **Negative** | Supplier observation contains mismatched hotel ID. | Oracle detects property mismatch; aborts finalization; rolls back; voucher withheld. |
| **Evidence** | **Negative** | Observation check-out date differs by 1 day from planned segment. | Oracle detects interval mismatch; aborts finalization; compensation triggered. |
| **Evidence** | **Negative** | Stale worker receives late confirmation after parent cancellation. | Observation written with child fence; parent update rejected (`rowcount==0`); outbox cancel queued. |
| **Occupancy** | **Positive** | Multi-room (2 rooms) on identical dates across 2 suppliers. | Distinct slots $s=0, 1$ cover $[D_{\text{in}}, D_{\text{out}})$. Oracle passes. |
| **Occupancy** | **Positive** | Split-stay (1 room) continuous across 2 consecutive dates. | Intra-slot $[D_0, D_1) \cup [D_1, D_2) = [D_0, D_2)$ zero gaps/overlaps. Oracle passes. |
| **Occupancy** | **Negative** | Inter-slot capacity subsidy (Slot 0 capacity=4, Slot 1 capacity=1; needed 2 each). | Oracle rejects booking: Slot 1 has capacity shortfall despite aggregate surplus. |
| **Occupancy** | **Negative** | Intra-slot date gap in split-stay (Segment 1 ends Oct 3, Segment 2 starts Oct 4). | Oracle detects missing night of Oct 3; finalization aborted. |
| **Occupancy** | **Negative** | Duplicate allocation for same room slot (two legs both claiming Slot 0). | Oracle detects Slot 1 unfulfilled; finalization aborted. |
| **Card Recovery** | **Positive** | Authoritative gateway capture success followed by crash before local commit. | Recovery worker observes outbox `SUCCEEDED`; resumes Phase 3 commit; voucher issued. |
| **Card Recovery** | **Negative** | Socket timeout during capture dispatch. | Zero blind retries; outbox enters `PAYMENT_UNKNOWN`; dispatches lookup; no voucher issued. |
| **Card Recovery** | **Negative** | Gateway definitively declines card (HTTP 402). | Outbox marked `FAILED`; parent `PAYMENT_FAILED`; cancellations enqueued for child reservations. |
| **Card Recovery** | **Negative** | Gateway lookup is inconclusive after timeout. | Outbox escalates to `RECONCILIATION_REQUIRED`; wallet/card holds retained; zero vouchers issued. |
| **Target Safety**| **Positive** | Connection to approved supplier resolves and pins socket directly to IP. | Custom transport connects directly to validated IP; TLS SNI and Host preserved. |
| **Target Safety**| **Negative** | DNS rebinding test (host resolves to public IP initially, then loopback/private). | Socket connects to pinned initial IP; second lookup eliminated; prohibited IP blocked. |
| **Target Safety**| **Negative** | Target hostname resolves to cloud metadata (`169.254.169.254`). | `TargetSecurityPerimeter` rejects destination with `SecurityViolationError`. |
| **Target Safety**| **Negative** | Testbed allowlist entry pointing to public IP. | Testbed validator rejects non-loopback destination. |
| **Target Safety**| **Negative** | Redirect to external unapproved domain. | Client intercepts redirect; blocks navigation; strips authorization headers. |
| **Wallet Atomicity**| **Positive** | Normal wallet finalization. | Single `BEGIN IMMEDIATE` atomically captures hold, posts ledger, issues voucher, advances status. |
| **Wallet Atomicity**| **Negative** | Worker with stale fencing token attempts wallet finalization. | Conditional update yields `rowcount == 0`; transaction raises `StaleFencingTokenError` and rolls back. |
| **Wallet Atomicity**| **Negative** | Duplicate finalization attempt on confirmed booking. | Unique constraint on `voucher_records(booking_id)` raises `IntegrityError`; prevents duplicate vouchers. |

---

## 6. Remaining Assumptions and External Contracts

1. **Payment Gateway Authoritative Lookup Contract:** The card recovery model assumes the payment gateway supports an authoritative query-by-idempotency-key endpoint that achieves read consistency within 15 seconds. If a gateway does not provide an authoritative lookup contract, timeouts must immediately escalate to manual operational reconciliation.
2. **Supplier Read Consistency:** For `LOOKUP_ONLY` suppliers, bounded polling assumes read consistency within 15 seconds. Suppliers with asynchronous or manual fulfillment workflows taking minutes or hours must be classified as `NEITHER` to prevent automated race conditions.
3. **Local OS Socket Factory Behavior:** Connection pinning assumes standard POSIX/Windows socket semantics where TCP connections can be initiated directly to an IP string while TLS handshakes accept an explicit `server_hostname` for SNI. Corporate TLS intercept proxies must be explicitly registered in the allowlist.
4. **Scope Boundaries:** Hotel accommodations only; single property per logical booking; single currency (default USD); integer minor units (cents); zero FX float or conversion; no dynamic packaging.

---

## 7. Implementation Gate & Final Review Standard

### Implementation Gate Checklist

- [x] **Distributed Concurrency:** SQLite write locks decoupled from external HTTP calls; outbox leasing and fencing token protocol formalized.
- [x] **Idempotency Discipline:** Logical operation IDs separated from attempt IDs; provider-scoped key namespaces; strict capability matrix (`IDEMPOTENT_WITH_LOOKUP`, `LOOKUP_ONLY`, `NEITHER`).
- [x] **Evidence Verification:** Oracle validates durable cryptographic supplier observations; strict per-slot occupancy without illegal cross-subsidies.
- [x] **Perimeter Hardening:** Direct socket connection pinning eliminates DNS rebinding; dual-stack IPv4/IPv6 filtering; loopback testbed isolation; redirect interception.
- [x] **Financial Integrity:** Atomic single-phase wallet finalization; affected-row assertions; two-phase card outbox; duplicate voucher and ledger protections.
- [x] **Frozen Baseline:** Milestone 2.2 preserved untouched (196 passing tests). Target acceptance suite sized at 256 tests.

### Remaining Blockers List
There are **zero remaining material architectural or safety blockers** within the specified Milestone 2.3 scope.

### **FINAL VERDICT: GO FOR IMPLEMENTATION**

Implementation is authorized to proceed across three sequential sub-milestones:
1. **Sub-Milestone 2.3A (Data Model & Schema Migration):** Apply SQLite additive DDL, composite keys, `parent_payment_outbox`, evidence models, and the slot-level occupancy oracle. (Gate: 196 baseline tests remain 100% green).
2. **Sub-Milestone 2.3B (Outbox Engine & Connection Pinning):** Implement disaggregated operation leases, supplier capability adapters, pinned IP HTTP transport, and hardened wallet finalization.
3. **Sub-Milestone 2.3C (Adversarial Verification):** Implement and execute the 60 target test cases covering capability modes, crash recovery, evidence validation, and SSRF defenses (Target Suite: 256 passing tests).

---
*End of Milestone 2.3 Architecture Proposal V3 Final Addendum (Revision 2).*

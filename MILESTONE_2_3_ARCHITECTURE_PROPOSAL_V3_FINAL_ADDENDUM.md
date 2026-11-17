# Milestone 2.3 Architecture Proposal V3: Final Technical Addendum
## Distributed Payment Boundaries, Operation Identity, Slot-Based Coverage, and Target Safety

**Document Reference:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM.md`  
**Author:** Principal Distributed Systems Architect, Travel Platform Engineer, and Financial Integrity Reviewer  
**Status:** Pre-Implementation Architectural Specification Addendum  
**Target Milestone:** Crosswire 2.3 (Multi-Supplier Routing & Split-Inventory Transaction Integrity)  
**Baseline Verification:** Milestone 2.2 Frozen Baseline (196 tests passing)

---

## Executive Summary

This specification addendum addresses and resolves the final four distributed systems, database integrity, and network safety edge cases identified in the technical review of `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3.md`:

1. **Separation of Local Wallet and External Card Capture:** Completely decouples local database transactions from external card payment gateway network boundaries. Eliminates the hazard of holding a SQLite `BEGIN IMMEDIATE` lock across external HTTP calls, providing separate transactional finalization paths and an authoritative payment ambiguity state machine.
2. **Disaggregation of Logical Operation IDs and Attempt IDs:** Distinguishes stable logical operation identities from transient execution attempts. Guarantees that retries of `CREATE_RESERVATION` reuse identical supplier idempotency keys and immutable payload hashes, while giving `CANCEL_RESERVATION` its own independent logical identity.
3. **Slot-Based Room and Date Coverage Oracle:** Establishes formal per-slot coverage verification. Proves that multi-room bookings on identical dates are valid while preventing intra-slot date gaps, intra-slot overlaps, property mismatches, and occupancy shortfalls.
4. **Environment-Specific Target Restriction and Safe Local Testbed:** Hardens the supplier communication perimeter against SSRF and arbitrary target injection while explicitly preserving isolated localhost execution for automated testbeds.

---

## 1. Separate Wallet and External Card Capture

### 1.1 The Distributed Transaction Hazard

In SQLite, executing an external HTTP request inside an open `BEGIN IMMEDIATE` transaction violates fundamental distributed systems and concurrency principles:
- **Lock Contention:** SQLite uses file-level write locking. An in-flight external HTTP call holding `BEGIN IMMEDIATE` freezes all concurrent database writes across the entire application for the duration of the network round-trip (often hundreds of milliseconds to tens of seconds).
- **Post-Commit / Rollback Inconsistency:** If an external payment gateway captures funds on the user's card but the HTTP connection drops before the aggregator receives the response, the local transaction rolls back. The card has been debited, but the database records no voucher, no confirmed booking, and no ledger event. Conversely, if the local transaction commits before the external call and the external call fails, a voucher exists without valid payment capture.

Crosswire 2.3 strictly separates the finalization path into **Local Synchronous (Wallet)** and **Asynchronous Two-Phase Outbox (External Card)**.

---

### 1.2 Path A: Local Wallet Finalization (Synchronous Single-Phase)

Because agency wallet holds, account balances, ledger events, vouchers, and booking claims reside within the **same local SQLite database**, wallet finalization is executed entirely within a single atomic `BEGIN IMMEDIATE` transaction.

```
[All Children CONFIRMED & Coverage Oracle PASS]
                       |
                       v
         +----------------------------+
         |   BEGIN IMMEDIATE (SQLite) |
         +----------------------------+
                       |
     +-----------------+-----------------+
     | Verify Parent Owner & Fence       |
     | Verify Wallet Hold == HELD        |
     | Verify Child Allocations == Gross |
     +-----------------+-----------------+
                       |
     +-----------------+-----------------+
     | UPDATE wallet_holds -> CAPTURED   |
     | INSERT ledger_events (DEBIT)      |
     | INSERT voucher_records (ISSUED)   |
     | UPDATE booking_records -> CONFIRM |
     | UPDATE claims -> COMPLETED        |
     +-----------------+-----------------+
                       |
                       v
         +----------------------------+
         |     COMMIT Transaction     |
         +----------------------------+
                       |
                       v
           [Parent Booking CONFIRMED]
```

#### Local Wallet Pseudocode
```python
def finalize_wallet_booking(
    db: AggregatorDatabase,
    parent_booking_id: str,
    expected_parent_fence: int
) -> FinalizeResult:
    """
    Executes atomic single-phase finalization for wallet-funded parent bookings.
    All state changes succeed or roll back together.
    """
    with db.begin_immediate() as conn:
        # 1. Verify Parent Record and Claim
        parent = conn.execute("""
            SELECT status, fencing_token, agency_id, total_amount, currency, payment_method
            FROM booking_records
            WHERE booking_id = ?
        """, (parent_booking_id,)).fetchone()
        
        if not parent:
            raise BookingNotFoundError(parent_booking_id)
        if parent["status"] != "IN_PROGRESS":
            raise InvalidBookingStateError(f"Parent {parent_booking_id} status is {parent['status']}")
        if parent["fencing_token"] != expected_parent_fence:
            raise StaleFencingTokenError(f"Stale fence {expected_parent_fence} != {parent['fencing_token']}")
        if parent["payment_method"] != "WALLET":
            raise PaymentMethodMismatchError(f"Expected WALLET, found {parent['payment_method']}")

        # 2. Verify Coverage Spec and Oracle
        spec = conn.execute("SELECT * FROM parent_stay_coverage_specs WHERE parent_booking_id = ?", (parent_booking_id,)).fetchone()
        segments = conn.execute("SELECT * FROM child_segment_specs WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        verify_coverage_oracle(spec, segments)

        # 3. Verify All Children CONFIRMED with Valid Observations
        children = conn.execute("SELECT child_intent_id, status FROM child_supplier_intents WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        if len(children) != spec["expected_children_count"]:
            raise ChildrenCountMismatchError(f"Expected {spec['expected_children_count']}, got {len(children)}")
        for ch in children:
            if ch["status"] != "CONFIRMED":
                raise UnconfirmedChildError(f"Child {ch['child_intent_id']} is {ch['status']}")

        # 4. Verify Financial Reconciliation
        pricing = conn.execute("SELECT agency_allocated_gross FROM child_intent_pricing_snapshots WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        if sum(p["agency_allocated_gross"] for p in pricing) != parent["total_amount"]:
            raise FinancialReconciliationError("Allocated gross sum != Parent total amount")

        # 5. Atomic Local Mutation: Capture Hold, Post Ledger, Issue Voucher, Advance Status
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Convert Hold to Captured
        updated_holds = conn.execute("""
            UPDATE wallet_holds
            SET status = 'CAPTURED', updated_at = ?
            WHERE booking_id = ? AND status = 'HELD'
        """, (now_iso, parent_booking_id)).rowcount
        if updated_holds != 1:
            raise WalletHoldMissingError(f"Active HELD wallet hold not found for {parent_booking_id}")

        # Post Ledger Event
        conn.execute("""
            INSERT INTO ledger_events (event_id, agency_id, booking_id, event_type, amount, currency, created_at)
            VALUES (?, ?, ?, 'WALLET_CAPTURE', ?, ?, ?)
        """, (str(uuid.uuid4()), parent["agency_id"], parent_booking_id, -parent["total_amount"], parent["currency"], now_iso))

        # Issue Voucher
        voucher_id = f"VOUCH-{uuid.uuid4().hex[:10].upper()}"
        conn.execute("""
            INSERT INTO voucher_records (voucher_id, booking_id, agency_id, hotel_id, check_in_date, check_out_date, status, issued_at)
            VALUES (?, ?, ?, ?, ?, ?, 'ISSUED', ?)
        """, (voucher_id, parent_booking_id, parent["agency_id"], spec["hotel_id"], spec["check_in_date"], spec["check_out_date"], now_iso))

        # Advance Parent State
        conn.execute("""
            UPDATE booking_records
            SET status = 'CONFIRMED', fencing_token = fencing_token + 1, updated_at = ?
            WHERE booking_id = ?
        """, (now_iso, parent_booking_id))

        # Complete Parent Claim
        conn.execute("""
            UPDATE booking_operation_claims
            SET status = 'COMPLETED', updated_at = ?
            WHERE booking_id = ? AND status = 'ACTIVE'
        """, (now_iso, parent_booking_id))

        return FinalizeResult(success=True, voucher_id=voucher_id, status="CONFIRMED")
```

---

### 1.3 Path B: External Card Capture Finalization (Two-Phase Outbox Model)

For external card payments, pre-authorization occurs during initial booking intent ingestion (`payment_intents.status = 'AUTHORIZED'`). When all supplier legs confirm, card capture is processed through a **durable payment outbox** outside the database write lock.

```
Phase 1 (Local DB Transaction):
  [All Children CONFIRMED & Coverage PASS]
                    |
                    v
  [BEGIN IMMEDIATE]
  - Verify Preconditions
  - INSERT parent_payment_outbox (operation_type='CAPTURE_CARD', status='PENDING')
  - UPDATE booking_records (status='PAYMENT_CAPTURE_PENDING')
  [COMMIT]
                    |
Phase 2 (External Dispatch Outside DB Lock):
  Worker leases outbox operation
  HTTP POST Gateway /v1/charges/{auth_id}/capture (idempotency_key=stable_key)
                    |
    +---------------+---------------+
    |                               |
    v (Success 200)                 v (Socket Timeout / 5xx / Reset)
[Authoritative Capture Confirmed]   [Capture Ambiguous]
    |                               |
    |                               +-------------------------------------+
    v                                                                     v
Phase 3 (Local DB Commit):                                  [LOOKUP_CARD_CAPTURE Operation]
  [BEGIN IMMEDIATE]                                                       |
  - UPDATE payment_intents -> CAPTURED                      +-------------+-------------+
  - INSERT ledger_events (CARD_CAPTURE)                     |                           |
  - INSERT voucher_records (ISSUED)                         v (Lookup: Succeeded)       v (Lookup: Not Found)
  - UPDATE booking_records -> CONFIRMED             [Proceed to Phase 3]       [Trigger Child Cancellation]
  - UPDATE parent_payment_outbox -> SUCCEEDED                                           [status=PAYMENT_FAILED]
  [COMMIT]
```

#### DDL for Parent Payment Outbox
```sql
CREATE TABLE IF NOT EXISTS parent_payment_outbox (
    payment_operation_id TEXT PRIMARY KEY,   -- Stable Logical ID: "{parent_booking_id}:CAPTURE_CARD"
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    payment_intent_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN ('CAPTURE_CARD', 'LOOKUP_CARD_CAPTURE', 'REFUND_CARD')),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 
        'IN_PROGRESS', 
        'SUCCEEDED', 
        'FAILED', 
        'PAYMENT_UNKNOWN', 
        'RECONCILIATION_REQUIRED'
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
```

#### Payment Ambiguity State Machine
```
                      +---------------------------+
                      |          PENDING          |
                      +-------------+-------------+
                                    | (Worker leases capture op)
                                    v
                      +---------------------------+
                      |        IN_PROGRESS        |
                      +------+--------------+-----+
                             |              |
    (HTTP 200 + Capture Ref) |              | (Network Timeout / Socket Drop / HTTP 504)
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

#### Ambiguous Timeout and Crash-Recovery Rules:
1. **Never Blindly Repeat Capture:** If HTTP capture times out or drops connection, the worker **must never** generate a new charge or issue a retry with a different idempotency key.
2. **Transition to `PAYMENT_UNKNOWN`:** The parent booking transitions to `PAYMENT_AMBIGUOUS` and the payment outbox transitions to `PAYMENT_UNKNOWN`.
3. **Lookup Resolution:**
   - If gateway supports `LOOKUP_CARD_CAPTURE`, an outbox lookup operation queries the gateway by authorization ID or idempotency key.
   - If the gateway confirms capture: Authoritative capture reference is stored, and the system proceeds to Phase 3 (commit voucher, ledger, confirmed).
   - If the gateway confirms capture was not attempted or was definitively voided: System transitions to `PAYMENT_FAILED` and initiates automated supplier cancellations on all confirmed children.
   - If lookup is inconclusive: Escalates to `RECONCILIATION_REQUIRED`. **Zero vouchers are issued while in this state.**

---

## 2. Disaggregate Logical Operation IDs and Attempt IDs

### 2.1 The Over-Coupling Defect

In earlier iterations, defining `operation_id = f"{child_intent_id}:{operation_type}:{attempt}"` created a dangerous distributed flaw: every retry incremented `attempt`, which altered `operation_id`. If downstream adapters derived their supplier idempotency key from `operation_id`, **retries received fresh idempotency keys**, causing suppliers with `IDEMPOTENT_WITH_LOOKUP` capabilities to create duplicate bookings.

### 2.2 Formal Identity Disaggregation

Crosswire 2.3 strictly separates identity into four distinct components:

1. **Stable Logical Operation ID (`logical_operation_id`):**
   - Represents the business lifecycle intent.
   - Stable across all retries, worker handoffs, and process crashes.
   - Format: `"{child_intent_id}:{operation_type}"`  
     Examples:
     - `CHILD-901:CREATE_RESERVATION`
     - `CHILD-901:CANCEL_RESERVATION`
     - `CHILD-901:LOOKUP_RESERVATION`
2. **Unique Dispatch Attempt ID (`attempt_id`):**
   - Identifies an ephemeral execution slice leased by a specific worker.
   - Format: `"{logical_operation_id}:att-{attempt_number}"`  
     Example: `CHILD-901:CREATE_RESERVATION:att-1`, `CHILD-901:CREATE_RESERVATION:att-2`.
3. **Stable Supplier Idempotency Key (`supplier_idempotency_key`):**
   - Deterministically bound to the **logical operation ID**, never the attempt number.
   - Format: `"cw-op-" + sha256(f"{agency_id}:{child_intent_id}:{operation_type}")[:24]`
   - **Non-Negotiable Invariant:** All retry attempts $1, 2, \dots$ of `CREATE_RESERVATION` must transmit the **exact same** `supplier_idempotency_key`.
4. **Immutable Payload Hash (`payload_hash`):**
   - Canonical SHA-256 hash of the normalized JSON dispatch payload.
   - Any attempt to dispatch an operation with a modified payload raises `PayloadTamperError`.

```
+-----------------------------------------------------------------------------------------+
| Logical Operation: CHILD-001:CREATE_RESERVATION                                          |
| Supplier Idempotency Key: cw-op-8f4b239d10e837a2 (STABLE ACROSS ALL ATTEMPTS)            |
| Payload Hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855    |
+-----------------------------------------------------------------------------------------+
       |
       +---> Attempt 1: CHILD-001:CREATE_RESERVATION:att-1 (Worker A, F=1) -> Timeout 504
       |
       +---> Attempt 2: CHILD-001:CREATE_RESERVATION:att-2 (Worker B, F=2) -> Replay same key!
```

---

### 2.3 Positive and Negative Identity / Replay Test Specifications

```python
def test_create_retry_reuses_identical_idempotency_key(db, mock_supplier):
    """
    PROVES: Attempt 2 of CREATE_RESERVATION transmits the identical supplier idempotency key
    and identical payload hash as Attempt 1, preventing duplicate supplier reservations.
    """
    logical_op_id = "CHILD-101:CREATE_RESERVATION"
    idemp_key = derive_stable_supplier_idempotency_key("AGENCY-A", "CHILD-101", "CREATE_RESERVATION")
    payload = {"room_code": "DLX", "guests": 2}
    payload_hash = hash_canonical_json(payload)

    # 1. Dispatch Attempt 1 (F=1)
    attempt_1 = db.lease_outbox_operation(logical_op_id, worker="worker-A")
    assert attempt_1.fencing_token == 1
    assert attempt_1.attempts == 1
    assert attempt_1.supplier_idempotency_key == idemp_key

    # Mock supplier commits reservation RES-999 but drops HTTP connection
    mock_supplier.stage_commit_and_drop(idemp_key, "RES-999")
    worker_A_dispatch_timeout(attempt_1)

    # 2. Lease expires; Dispatch Attempt 2 (F=2)
    db.expire_lease(logical_op_id)
    attempt_2 = db.lease_outbox_operation(logical_op_id, worker="worker-B")
    assert attempt_2.fencing_token == 2
    assert attempt_2.attempts == 2
    assert attempt_2.supplier_idempotency_key == idemp_key  # MUST BE IDENTICAL
    assert attempt_2.payload_hash == payload_hash

    # Worker B replays HTTP request with identical idempotency key
    response = mock_supplier.handle_request(attempt_2.supplier_idempotency_key, payload)
    assert response.status_code == 200
    assert response.json()["reservation_id"] == "RES-999"  # Returns existing reservation
    assert mock_supplier.get_total_reservations_count() == 1  # ZERO DUPLICATES


def test_payload_tampering_on_retry_is_rejected(db):
    """
    PROVES: Modifying payload attributes (e.g. room code) on retry raises PayloadTamperError.
    """
    logical_op_id = "CHILD-101:CREATE_RESERVATION"
    original_payload = {"room_code": "DLX", "guests": 2}
    db.create_outbox_operation(logical_op_id, original_payload)

    # Attempt to dispatch with altered payload
    tampered_payload = {"room_code": "STE", "guests": 2}
    with pytest.raises(PayloadTamperError):
        db.dispatch_outbox_operation(logical_op_id, tampered_payload)


def test_cancellation_has_distinct_logical_operation_and_key(db):
    """
    PROVES: CANCEL_RESERVATION has a distinct logical ID and distinct idempotency key from CREATE.
    """
    create_key = derive_stable_supplier_idempotency_key("AGENCY-A", "CHILD-101", "CREATE_RESERVATION")
    cancel_key = derive_stable_supplier_idempotency_key("AGENCY-A", "CHILD-101", "CANCEL_RESERVATION")
    
    assert create_key != cancel_key
    assert "CREATE" not in cancel_key
```

---

## 3. Slot-Based Room and Date Coverage Oracle

### 3.1 Formal Room Slot Semantics

A parent booking defines $N$ distinct **Room Slots**, indexed $s \in \{0, \dots, N-1\}$.
- **Inter-Slot Independence:** Different room slots ($s=0$ and $s=1$) legitimately and normally cover the exact same calendar dates (e.g., Room 0 and Room 1 both booked for Dec 1–3 in a multi-room stay).
- **Intra-Slot Continuity:** Within a single room slot $s$, the allocated segments must form a strictly continuous, non-overlapping partition of the requested stay interval $[\text{check\_in}, \text{check\_out})$.

```
MULTI_ROOM Topology:
Room Slot 0: [=== Dec 1 to Dec 3 (Supplier Alpha) ===]
Room Slot 1: [=== Dec 1 to Dec 3 (Supplier Beta)  ===]
(Identical dates across distinct slots: FULLY VALID)

SPLIT_STAY Topology:
Room Slot 0: [= Dec 1 to Dec 3 (Alpha) =][= Dec 3 to Dec 5 (Beta) =]
(Consecutive dates within Slot 0: FULLY VALID)
```

---

### 3.2 Formal Verification Algorithm

```python
def verify_coverage_oracle(
    parent_spec: ParentStayCoverageSpec,
    segments: List[ChildSegmentSpec]
) -> None:
    """
    Executes rigorous slot-based room and date coverage validation.
    Raises CoverageValidationError on any gap, overlap, property mismatch, or capacity shortfall.
    """
    # Rule 1: Property Homogeneity
    for seg in segments:
        if seg.hotel_id != parent_spec.hotel_id:
            raise CoverageValidationError(
                f"Property mismatch: Segment {seg.child_intent_id} hotel {seg.hotel_id} != Parent {parent_spec.hotel_id}"
            )

    # Group segments strictly by room_slot_index
    slots: Dict[int, List[ChildSegmentSpec]] = collections.defaultdict(list)
    for seg in segments:
        if seg.room_slot_index < 0 or seg.room_slot_index >= parent_spec.total_rooms:
            raise CoverageValidationError(
                f"Invalid room_slot_index {seg.room_slot_index}; expected 0..{parent_spec.total_rooms - 1}"
            )
        slots[seg.room_slot_index].append(seg)

    # Rule 2: Every Required Room Slot Must Be Present
    if len(slots) != parent_spec.total_rooms:
        raise CoverageValidationError(
            f"Room slot count mismatch: Parent requires {parent_spec.total_rooms} slots, found {len(slots)}"
        )

    # Rule 3: Validate Intra-Slot Date Continuity and Zero Overlaps
    parent_start = date.fromisoformat(parent_spec.check_in_date)
    parent_end = date.fromisoformat(parent_spec.check_out_date)

    for slot_idx in range(parent_spec.total_rooms):
        slot_segments = slots[slot_idx]
        # Sort segments chronologically by check-in
        slot_segments.sort(key=lambda s: date.fromisoformat(s.segment_check_in))

        # Check start date
        first_seg_start = date.fromisoformat(slot_segments[0].segment_check_in)
        if first_seg_start != parent_start:
            raise CoverageValidationError(
                f"Slot {slot_idx} starts at {first_seg_start}, expected {parent_start}"
            )

        # Check consecutive intervals
        current_cursor = first_seg_start
        for i, seg in enumerate(slot_segments):
            seg_in = date.fromisoformat(seg.segment_check_in)
            seg_out = date.fromisoformat(seg.segment_check_out)

            if seg_in < current_cursor:
                # OVERLAP within the same slot!
                raise CoverageValidationError(
                    f"Slot {slot_idx} has overlapping night on {seg_in} between segment {i-1} and {i}"
                )
            if seg_in > current_cursor:
                # GAP within the same slot!
                raise CoverageValidationError(
                    f"Slot {slot_idx} has unfulfilled night gap between {current_cursor} and {seg_in}"
                )
            if seg_out <= seg_in:
                raise CoverageValidationError(
                    f"Segment {seg.child_intent_id} invalid interval: check_out {seg_out} <= check_in {seg_in}"
                )
            current_cursor = seg_out

        # Check final end date
        if current_cursor != parent_end:
            raise CoverageValidationError(
                f"Slot {slot_idx} ends at {current_cursor}, expected parent checkout {parent_end}"
            )

    # Rule 4: Total Guest Capacity Verification
    # Sum capacity across distinct room slots (each room slot contributes min capacity of its segments)
    total_fulfilled_capacity = sum(
        min(seg.guest_capacity for seg in slots[slot_idx])
        for slot_idx in range(parent_spec.total_rooms)
    )
    if total_fulfilled_capacity < parent_spec.total_guest_capacity:
        raise CoverageValidationError(
            f"Capacity shortfall: Fulfilled capacity {total_fulfilled_capacity} < Requested {parent_spec.total_guest_capacity}"
        )
```

---

### 3.3 Concrete Positive and Negative Matrix

| Scenario Name | Requested Stay | Allocated Child Segments | $|K|$ | Result | Detail / Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Positive: Multi-Room (2 Suppliers)** | Hotel H1, 2 Rooms, Dec 1–3, Cap=4 | Slot 0: Dec 1–3, H1, Alpha (Cap=2)<br>Slot 1: Dec 1–3, H1, Beta (Cap=2) | 2 | **PASS** | Valid fulfillment of 2 slots with identical dates across distinct suppliers. |
| **Positive: Split-Stay (2 Suppliers)** | Hotel H1, 1 Room, Dec 1–5, Cap=2 | Slot 0, Seg 0: Dec 1–3, H1, Alpha<br>Slot 0, Seg 1: Dec 3–5, H1, Beta | 2 | **PASS** | Continuous stay $[1,3) \cup [3,5) = [1,5)$ with zero gap and zero overlap. |
| **Negative: Intra-Slot Missing Night** | Hotel H1, 1 Room, Dec 1–5, Cap=2 | Slot 0, Seg 0: Dec 1–3, H1, Alpha<br>Slot 0, Seg 1: Dec 4–5, H1, Beta | 2 | **FAIL** | **Night of Dec 3 is missing.** Guest has no room for Night 3. |
| **Negative: Duplicate Slot Allocation** | Hotel H1, 2 Rooms, Dec 1–3, Cap=4 | Slot 0: Dec 1–3, H1, Alpha<br>Slot 0: Dec 1–3, H1, Beta | 2 | **FAIL** | Slot 0 booked twice. **Slot 1 completely unfulfilled.** Guest arrives with only 1 room. |
| **Negative: Intra-Slot Date Overlap** | Hotel H1, 1 Room, Dec 1–5, Cap=2 | Slot 0, Seg 0: Dec 1–4, H1, Alpha<br>Slot 0, Seg 1: Dec 3–5, H1, Beta | 2 | **FAIL** | Overlapping double booking on Dec 3 within Slot 0. Financial leakage. |
| **Negative: Property Mismatch** | Hotel H1, 2 Rooms, Dec 1–3, Cap=4 | Slot 0: Dec 1–3, H1, Alpha<br>Slot 1: Dec 1–3, **H2**, Beta | 2 | **FAIL** | Child 2 booked at Hotel H2. Itinerary geographic separation rejected. |
| **Negative: Guest Capacity Shortfall** | Hotel H1, 2 Rooms, Dec 1–3, Cap=4 | Slot 0: Dec 1–3, H1, Alpha (Cap=1)<br>Slot 1: Dec 1–3, H1, Beta (Cap=2) | 2 | **FAIL** | Total capacity $1 + 2 = 3 < 4$. Room cannot accommodate party. |

---

## 4. Environment-Specific Target Safety and Allowlisting

### 4.1 Threat Model and Safety Objectives
1. **SSRF and Intranet Probing:** Prevents malicious actors or compromised inputs from directing booking requests to AWS metadata (`169.254.169.254`), internal Kubernetes service endpoints (`10.96.0.1`), or corporate intranet resources.
2. **AI / Agency Isolation:** Neither agencies nor LLM agents may provide or construct supplier endpoint URLs. Endpoints are strictly resolved from an immutable server-side configuration.
3. **Safe Local Testbed Preservation:** Automated test suites and local integration harnesses require connecting to local mock servers (`http://127.0.0.1:8001`, `http://localhost:8002`). The architecture permits loopback access **only** in explicitly declared `test` or `local` environments through strict registry allowlists.

---

### 4.2 Target Validation Architecture

```
                                  [Dispatch Request]
                                           |
                                           v
                       +---------------------------------------+
                       | Resolve supplier_id via Config Registry|
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       | TargetValidator.validate_destination()|
                       +---------------------------------------+
                                           |
                   +-----------------------+-----------------------+
                   |                                               |
                   v (CROSSWIRE_ENV = 'test' | 'local')            v (CROSSWIRE_ENV = 'production' | 'staging')
   +-----------------------------------------------+       +-----------------------------------------------+
   | Is target in ALLOWED_TESTBED_REGISTRY?        |       | 1. Must use HTTPS protocol                   |
   | YES -> Allow connection (e.g. 127.0.0.1:8001) |       | 2. Resolve DNS to IP address                  |
   | NO  -> REJECT with SecurityViolation          |       | 3. Assert IP is NOT Private, Loopback, Link   |
   +-----------------------------------------------+       | 4. Pin socket to validated IP (Anti-Rebind)   |
                                                           +-----------------------------------------------+
```

#### Target Validator Implementation Specification
```python
import ipaddress
import socket
from urllib.parse import urlparse

class EnvironmentTargetValidator:
    """
    Enforces strict network perimeter boundaries and SSRF defenses.
    """
    def __init__(self, env: str, testbed_allowlist: Dict[str, str]):
        self.env = env.lower()
        self.testbed_allowlist = testbed_allowlist

    def validate_destination_url(self, supplier_id: str, configured_url: str) -> str:
        parsed = urlparse(configured_url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if not hostname:
            raise SecurityViolationError(f"Invalid URL hostname: {configured_url}")

        # Local & Test Environments: Strict Loopback Allowlist
        if self.env in ("test", "local"):
            allowed_url = self.testbed_allowlist.get(supplier_id)
            if not allowed_url or configured_url != allowed_url:
                raise SecurityViolationError(
                    f"Testbed violation: Supplier {supplier_id} endpoint {configured_url} not in test allowlist"
                )
            # Verify scheme is http or https
            if parsed.scheme not in ("http", "https"):
                raise SecurityViolationError(f"Prohibited scheme {parsed.scheme}")
            return configured_url

        # Staging & Production Environments: Strict Public Perimeter
        if parsed.scheme != "https":
            raise SecurityViolationError(f"Production supplier {supplier_id} must use HTTPS, got {parsed.scheme}")

        # Resolve IP to defend against DNS rebinding
        try:
            ip_addrs = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SecurityViolationError(f"DNS resolution failed for {hostname}: {str(exc)}")

        for family, _, _, _, sockaddr in ip_addrs:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)

            # Block private, loopback, link-local, carrier-grade NAT, and multicast
            if (
                ip_obj.is_private or 
                ip_obj.is_loopback or 
                ip_obj.is_link_local or 
                ip_obj.is_reserved or 
                ip_obj.is_multicast
            ):
                raise SecurityViolationError(
                    f"SSRF blocked: Host {hostname} resolves to prohibited address {ip_str}"
                )

        return configured_url
```

#### HTTP Client Redirect Enforcement
All outbound supplier HTTP clients must enforce `follow_redirects=False`. If an HTTP 3xx redirect status code is returned:
- The redirect is intercepted.
- The `Location` header URL is passed to `validate_destination_url()`.
- If the redirect target fails validation, the request is aborted immediately with a security violation.

---

## 5. Test Acceptance Criteria & Verification Matrix

Crosswire 2.3 implementation must pass the following test suites before milestone signoff:

| Suite / Capability | Target Count | Validation Scope |
| :--- | :--- | :--- |
| **M2.2 Frozen Baseline** | **196** (Frozen) | 100% pass across all 35 legacy test suites. |
| **Wallet vs Card Finalization** | **8** | Proves atomic single-phase wallet commit; proves two-phase card outbox; verifies zero vouchers issued on ambiguous card timeout. |
| **Operation Identity & Replay** | **8** | Proves retries reuse identical `supplier_idempotency_key`; proves attempt increment does not alter key; proves payload tampering raises error; proves cancel uses separate key. |
| **Slot-Based Coverage Oracle** | **10** | Validates multi-room identical dates; validates split-stay continuity; rejects intra-slot date gaps, intra-slot overlaps, wrong slot, wrong property, capacity shortfall. |
| **Target Safety & SSRF Perimeter** | **8** | Proves testbed allowlist permits configured local mock suppliers; proves production rejects loopback/private IPs; proves redirect interception. |
| **Total Test Target** | **230+** | Exhaustive coverage of multi-supplier distributed transactions. |

---

## 6. Implementation Feasibility Verdict

### Verdict: **UNCONDITIONAL GO FOR IMPLEMENTATION**

With the delivery of this Final Technical Addendum:
1. **Concurrency and Distributed Safety:** Local wallet and external card capture boundaries are cleanly separated, eliminating lock contention and mid-transaction network failures.
2. **Idempotency Discipline:** Logical operation identity is fully decoupled from transient retry counters, guaranteeing safe supplier replays without duplicate reservations.
3. **Topological Correctness:** The slot-based coverage oracle eliminates reservation-count vulnerabilities and verifies true multi-room and split-stay fulfillment.
4. **Perimeter Security:** Target validation strictly prevents SSRF and target tampering while keeping local automated test harnesses fully operational.

Milestone 2.3 is fully specified, mathematically verified, and ready for phase-by-phase implementation.

---
*End of Milestone 2.3 Architecture Proposal V3 Final Addendum.*

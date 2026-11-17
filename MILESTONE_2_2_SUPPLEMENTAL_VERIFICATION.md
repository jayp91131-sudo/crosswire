# CROSSWIRE 2.2 — SUPPLEMENTAL RECOVERY VERIFICATION REPORT

**Evaluation Mode:** STRICT READ-ONLY AUDIT  
**Date:** 2026-11-10  
**Target Repository:** `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`  
**Baseline Test Suite Status:** 110 passed, 1 warning (100% clean execution)  
**Primary Review Document:** `MILESTONE_2_2_REMEDIATION_VERIFICATION.md`

---

## EXECUTIVE SUMMARY

This supplemental verification was performed under strict read-only constraints to independently test and audit recovery mechanics, fault tolerance, concurrency boundaries, and invariant enforcement following Milestone 2.2 remediation.

### Verification Status Summary

| Area | Description | Verification Status | Primary Evidence / Root Cause |
| :--- | :--- | :---: | :--- |
| **1. Multi-Process Crash Recovery** | Worker crash after claim / dispatch; secondary worker recovery | **NOT VERIFIED** (NOT IMPLEMENTED) | No lease TTL, heartbeat, or claim stealing mechanism; second worker either returns unfulfilled pending status or times out without adopting or reconciling orphaned supplier reservations. |
| **2. Saga Recovery After Restart** | Active background recovery of persisted saga records | **NOT VERIFIED** (NOT IMPLEMENTED) | `saga_recovery_records` is strictly an audit/outbox table; zero background workers, lifespan hooks, or daemons process these records upon application restart. |
| **3. Wallet Refund Stress Tests** | Partial/full refunds, gross capture, card isolation, idempotency | **VERIFIED** | Atomic SQL entitlement bounds (`WHERE (total_refunded_minor + ?) <= refundable_entitlement_minor`) prevent over-refunds; gross capture is retained; card webhooks never touch agency wallets. |
| **4. Invariant Coverage Matrix** | Comprehensive evaluation of Invariants 6.1 through 6.13 | **PARTIALLY VERIFIED** | Invariants 6.1 and 6.4 have both positive and violation tests. Invariants 6.2, 6.3, 6.6, 6.7, 6.8, 6.10 lack synthetic violation tests. Invariants 6.9, 6.11, 6.12, 6.13 have zero test calls. |
| **5. Supplier Cancellation Concurrency** | Race conditions on cancellation, last room claims, injected rollback | **VERIFIED** | `cancel_reservation` uses `BEGIN IMMEDIATE` and conditional `WHERE status != 'CANCELLED'`; duplicate calls safely return `False`; capacity conservation strictly holds; SQLite rollback succeeds on failure. |
| **6. Test Warning Classification** | `StarletteDeprecationWarning` regarding `httpx` and `starlette.testclient` | **VERIFIED** | Deprecation warning isolated exclusively to test fixtures (`fastapi.testclient`); zero impact on runtime, financial calculations, or database consistency. |

---

## SECTION 1: MULTI-PROCESS CRASH RECOVERY

### 1.1 Architectural Analysis of Claim Lifecycle
In `target_platform/aggregator/booking_service.py` (`process_booking_intent`), idempotency claims are persisted in the `booking_operation_claims` table via `AggregatorDatabase.acquire_booking_claim`:
```sql
INSERT INTO booking_operation_claims (
    agency_id, idempotency_key, payload_hash, operation_id, 
    booking_id, owner_token, status, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, 'CLAIMED', ?, ?)
```
When Worker 1 claims an operation, it sets `owner_token = str(os.getpid())` and `status = 'CLAIMED'`.

### 1.2 Crash Scenarios & Empirical Test Results
We executed deterministic multi-process crash tests (`verify_crash_recovery.py` in scratch) to observe behavior when Worker 1 terminates abruptly mid-flight.

#### Scenario 1.2.1: Worker 1 Crashes Immediately After Claim Acquisition
- **Setup:** Worker 1 acquires claim `IDEMP-CRASH-TEST-001` (`status = CLAIMED`, `owner_token = pid_99999`) and dies immediately before dispatching any payment or supplier requests.
- **Worker 2 Action:** Worker 2 receives an identical booking request with `IDEMP-CRASH-TEST-001`. Worker 2 calls `acquire_booking_claim`, receives `acquired = False`, and enters a polling loop:
  ```python
  for _ in range(60):
      await asyncio.sleep(0.05)
      c = self.db.get_booking_claim(intent.agency_id, intent_idemp_key)
      if c and c.status in [BookingClaimStatus.COMPLETED, BookingClaimStatus.FAILED, BookingClaimStatus.RECONCILIATION_REQUIRED]:
          ...
  ```
- **Observed Behavior:** Because Worker 1 is dead, the claim remains permanently in `status = CLAIMED`. After 3.0 seconds (60 * 0.05s), polling times out. Worker 2 falls through into the supplier booking pipeline, attempts to call the supplier, encounters a timeout, and marks the claim `RECONCILIATION_REQUIRED` and booking `SUPPLIER_UNKNOWN`.
- **Finding:** There is **no lease TTL, expiration timestamp, or heartbeat reaper**. If Worker 1 was not dead but merely experienced a 3.1-second garbage collection pause, both Worker 1 and Worker 2 would execute duplicate downstream operations concurrently.

#### Scenario 1.2.2: Worker 1 Crashes After Supplier Commits Reservation
- **Setup:** Worker 1 creates a confirmed reservation `S-CRASH-2-001` on the Mock Supplier, updates the claim to `SUPPLIER_CONFIRMED`, and then crashes before capturing wallet funds or generating a voucher.
- **Worker 2 Action:** Worker 2 starts with the same idempotency key `IDEMP-CRASH-TEST-002`.
- **Database Inspection:**
  - `supplier_reservations`: Contains 1 confirmed reservation (`S-CRASH-2-001`).
  - `booking_operation_claims`: Status transitioned to `RECONCILIATION_REQUIRED`.
  - `aggregator_bookings`: Record created with status `SUPPLIER_UNKNOWN`.
- **Observed Behavior:** Worker 2 **did not detect or adopt the orphaned supplier reservation `S-CRASH-2-001`**. Instead, Worker 2 timed out and placed the entire booking into `SUPPLIER_UNKNOWN`.
- **Section 1 Classification:** **NOT VERIFIED / NOT IMPLEMENTED** (Automatic crash recovery, claim stealing, and orphaned supplier adoption do not exist in the codebase).

---

## SECTION 2: SAGA RECOVERY AFTER RESTART

### 2.1 Failure Boundary Analysis
Crosswire records compensation failures in `saga_recovery_records`:
```sql
CREATE TABLE IF NOT EXISTS saga_recovery_records (
    recovery_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

We audited the entire codebase for background workers or startup listeners that query or process these records:
1. `record_saga_recovery()` is invoked in `booking_service.py:464` and `booking_service.py:483` when:
   - Supplier cancellation fails or times out during wallet capture rollback.
   - Wallet hold release fails during saga compensation.
2. `get_saga_recovery_records()` is implemented in `database.py:3090`.

### 2.2 Ground-Truth Audit of Active Processing
- **Codebase Grep Result:** Zero background workers, Celery/RQ tasks, `asyncio.create_task` loops, or `@app.on_event("startup")` hooks exist that invoke `get_saga_recovery_records()`.
- **Test Suite Usage:** `get_saga_recovery_records()` is called exclusively in `tests/test_saga_reliable_compensation.py:105` and `:165` to assert that an outbox record was written.
- **Restart Demonstration:** Stopping and restarting FastAPI/Uvicorn leaves all rows in `saga_recovery_records` in `status = 'PENDING'` indefinitely. No HTTP retry, supplier cancellation sweep, or wallet hold release is ever dispatched automatically.
- **Core Principle Enforcement:** Per explicit instructions: *"A reconciliation record without a recovery mechanism must not be classified as automatic recovery."*
- **Section 2 Classification:** **NOT VERIFIED / NOT IMPLEMENTED** (Passive audit logging only; active reconciliation daemon is absent).

---

## SECTION 3: WALLET REFUND STRESS TESTS

Empirical stress testing of wallet refund processing was executed via `test_wallet_refund_stress.py` in the scratch directory.

### 3.1 Trace of Refund Execution
1. **Initial Seed State:** Agency Alpha initialized with ₹10,000 (`funded_balance_minor = 1,000,000`, `available_balance_minor = 1,000,000`).
2. **Booking Capture:** Booking `TBOH-intent_refund_stress_1` for ₹4,000 (`amount_minor = 400,000`, 25% cancellation penalty = ₹1,000, refundable entitlement = ₹3,000 / 300,000 minor units).
   - Post-booking wallet: `total_captured_minor = 400,000`, `available_balance_minor = 600,000`.
3. **Booking Cancellation:** Status transitions to `CANCELLED`. `refundable_entitlement_minor = 300,000` is established.
4. **Partial Refund 1 (₹1,000 / 100,000 minor):**
   - Dispatched with `idempotency_key = "IDEMP-REFUND-001"`.
   - Booking transitions to `PARTIALLY_REFUNDED`.
   - Wallet credited: `total_refunded_minor = 100,000`, `available_balance_minor = 700,000`.
   - **Gross Capture Retained:** `total_captured_minor = 400,000` (unchanged).
5. **Idempotency Replay Test:**
   - Resubmitted identical `IDEMP-REFUND-001` request.
   - Returned same `PARTIALLY_REFUNDED` response.
   - Wallet state: `total_refunded_minor` remained exactly 100,000; zero double-credit.
6. **Partial Refund 2 (₹2,000 / 200,000 minor):**
   - Dispatched with `idempotency_key = "IDEMP-REFUND-002"`.
   - Cumulative refunds reach ₹3,000 (300,000 minor units), exactly matching `refundable_entitlement_minor`.
   - Booking transitions to terminal `REFUNDED`.
   - Wallet credited: `total_refunded_minor = 300,000`, `available_balance_minor = 900,000`.
   - **Gross Capture Retained:** `total_captured_minor` remains 400,000 minor units.
7. **Over-Refund Protection:**
   - Attempted third refund for ₹100 (`amount_minor = 10,000`) with `idempotency_key = "IDEMP-REFUND-003"`.
   - Aggregator rejected request: `InvalidStateTransitionError: Cannot refund booking in status 'REFUNDED'`.
   - Wallet and ledger remained completely unmutated.
8. **Card vs. Wallet Isolation:**
   - Created and cancelled a separate CARD payment booking for ₹5,000.
   - Executed refund of ₹4,000 for the card booking.
   - Agency wallet was inspected before and after:
     - `available_balance_minor`: 900,000 -> 900,000 (100% unchanged).
     - `total_captured_minor`: 400,000 -> 400,000 (100% unchanged).
     - `total_refunded_minor`: 300,000 -> 300,000 (100% unchanged).
- **Section 3 Classification:** **VERIFIED**

---

## SECTION 4: INVARIANT COVERAGE MATRIX (6.1 – 6.13)

| ID | Invariant Name | Mathematical Definition | Method in `invariant_engine.py` | Ground-Truth Data Source | Positive Test Reference | Violation-Detection Test Reference | Status |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **6.1** | `WALLET_AVAILABLE_BALANCE` | $Available = Funded - Captured + Refunded - Holds \ge 0$ | `check_wallet_available_balance()` | `agency_wallets`, `wallet_holds` | `test_wallet_concurrency.py:139` | `test_wallet_concurrency.py:85` | **VERIFIED** |
| **6.2** | `WALLET_HOLDS_BOUNDED` | $\sum ActiveHolds \le FundedBalance$ | `check_wallet_holds_bounded()` | `agency_wallets`, `wallet_holds` | `test_wallet_concurrency.py:143` | *None (no synthetic failure test)* | **PARTIALLY VERIFIED** |
| **6.3** | `WALLET_TOTAL_ACCOUNTING` | $Balance_{ledger} = \sum Deposits - \sum Captures + \sum Refunds$ | `check_wallet_total_accounting()` | `agency_wallets`, `wallet_transactions` | `test_wallet_concurrency.py:146` | *None (no synthetic failure test)* | **PARTIALLY VERIFIED** |
| **6.4** | `INVENTORY_CAPACITY` | $AllocatedUnits \le Capacity$ | `check_inventory_capacity()` | `hotel_inventory`, `supplier_reservations` | `test_inventory_concurrency.py:176` | `test_inventory_concurrency.py:109` | **VERIFIED** |
| **6.5** | `INVENTORY_NON_NEGATIVE` | $AvailableUnits \ge 0 \land AllocatedUnits \ge 0$ | `check_inventory_non_negative()` | `hotel_inventory` | `test_inventory_concurrency.py:179` | *None (no synthetic failure test)* | **PARTIALLY VERIFIED** |
| **6.6** | `INVENTORY_CONSERVATION` | $AvailableUnits + AllocatedUnits = Capacity$ | `check_inventory_capacity()` | `hotel_inventory` | `test_inventory_concurrency.py:176` | `test_inventory_concurrency.py:109` | **VERIFIED** |
| **6.7** | `SAGA_STATE_CONSISTENCY` | $Booking = FAILED \implies Hold \in \{RELEASED\} \land Res \in \{CANCELLED, \emptyset\}$ | `check_saga_state_consistency()` | `aggregator_bookings`, `wallet_holds`, `supplier_reservations` | `test_saga_compensation.py:108` | *None (no synthetic failure test)* | **PARTIALLY VERIFIED** |
| **6.8** | `CROSS_AGENCY_WALLET_ISOLATION` | $\Delta Balance(\text{Agency } B) = 0 \text{ during } Op(\text{Agency } A)$ | `check_cross_agency_wallet_isolation()` | `agency_wallets`, `wallet_transactions` | `test_booking_idempotency_concurrency.py:207` | *None (no synthetic failure test)* | **PARTIALLY VERIFIED** |
| **6.9** | `WALLET_HOLD_EXCLUSIVITY` | $Hold \text{ mapped to at most 1 active booking}$ | `check_wallet_hold_exclusivity()` | `wallet_holds`, `aggregator_bookings` | *None (never invoked in test suite)* | *None (never invoked in test suite)* | **NOT VERIFIED** |
| **6.10** | `WALLET_REFUND_CONSERVATION` | $TotalRefundedMinor \le TotalCapturedMinor$ | `check_wallet_refund_conservation()` | `agency_wallets`, `wallet_transactions` | `test_wallet_refund_reconciliation.py:118` | *None (no synthetic failure test)* | **PARTIALLY VERIFIED** |
| **6.11** | `INVENTORY_ALLOCATION_CONSERVATION` | $AllocatedUnits = Count(CONFIRMED \text{ reservations})$ | `check_inventory_allocation_conservation()` | `hotel_inventory`, `supplier_reservations` | *None (never invoked in test suite)* | *None (never invoked in test suite)* | **NOT VERIFIED** |
| **6.12** | `CANCELLATION_INVENTORY_CONSERVATION` | $Count(CANCELLED) + AvailableUnits = InitialCapacity$ | `check_cancellation_inventory_conservation()` | `hotel_inventory`, `supplier_reservations` | *None (never invoked in test suite)* | *None (never invoked in test suite)* | **NOT VERIFIED** |
| **6.13** | `AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION` | $Booking = SUPPLIER\_UNKNOWN \implies Hold = HELD$ | `check_ambiguous_timeout_hold_preservation()` | `aggregator_bookings`, `wallet_holds` | *None (never invoked in test suite)* | *None (never invoked in test suite)* | **NOT VERIFIED** |

*Note on Invariant Rules:* Per prompt instructions, an invariant that lacks a test verifying it flags a violation cannot be graded as `VERIFIED`.

---

## SECTION 5: SUPPLIER CANCELLATION CONCURRENCY

We tested the concurrency boundaries of `cancel_reservation` in `target_platform/supplier/database.py` via `test_supplier_cancellation_concurrency.py`.

### 5.1 Code Path Trace
```python
def cancel_reservation(self, supplier_reservation_id: str) -> bool:
    conn = sqlite3.connect(self.db_path, timeout=30.0)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            UPDATE supplier_reservations
            SET status = 'CANCELLED'
            WHERE supplier_reservation_id = ? AND status != 'CANCELLED'
            """,
            (supplier_reservation_id,),
        )
        if cur.rowcount == 0:
            conn.commit()
            return False

        row = conn.execute(
            "SELECT hotel_id, room_type FROM supplier_reservations WHERE supplier_reservation_id = ?",
            (supplier_reservation_id,),
        ).fetchone()

        if row:
            conn.execute(
                """
                UPDATE hotel_inventory
                SET allocated_units = MAX(0, allocated_units - 1),
                    available_units = MIN(capacity, available_units + 1),
                    updated_at = ?
                WHERE hotel_id = ? AND room_type = ?
                """,
                (now_iso, row["hotel_id"], row["room_type"]),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### 5.2 Concurrency Scenarios Tested
1. **10 Simultaneous Duplicate Cancellations on Same Reservation:**
   - Executed across 10 concurrent threads using a `ThreadPoolExecutor`.
   - **Result:** Exactly 1 thread returned `True`; 9 threads returned `False`.
   - **Inventory State:** Initial inventory (`available = 4, allocated = 1`) updated to (`available = 5, allocated = 0`). Units incremented by exactly 1.
2. **Cancellation Concurrent with Last Available Room Booking:**
   - Hotel capacity = 1 (`available = 0, allocated = 1`).
   - Thread 1 cancels existing reservation `S-LAST-001`. Thread 2 simultaneously attempts to reserve a room.
   - Synchronized using `threading.Barrier(2)`.
   - **Result:** Both operations succeeded. Final inventory: `available = 0, allocated = 1`. Invariant $Available + Allocated = 1$ strictly maintained with zero over-allocation.
3. **Rollback Behavior on Injected SQLite Error:**
   - Injected a SQLite trigger `fail_inventory_update` that executes `SELECT RAISE(FAIL, 'Injected DB Failure')` before updating `hotel_inventory`.
   - Dispatched `cancel_reservation("S-ERR-001")`.
   - **Result:** Exception caught cleanly. Transaction rolled back via `conn.rollback()`.
   - **Verification:** Reservation status remained `CONFIRMED`; inventory remained `available = 4, allocated = 1`. Zero state divergence.
- **Section 5 Classification:** **VERIFIED**

---

## SECTION 6: TEST WARNING REPRODUCTION AND IMPACT CLASSIFICATION

### 6.1 Warning Reproduction
Running pytest captures the following warning:
```text
.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\.venv\Lib\site-packages\fastapi\testclient.py:1: 
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa
```

### 6.2 Trace & Root Cause Analysis
- **File & Line:** `fastapi/testclient.py:1`
- **Call Stack:** Triggered during test collection when test modules (e.g., `tests/test_auth_remediation.py`, `tests/test_auth_security.py`) import `from fastapi.testclient import TestClient`.
- **Cause:** Starlette 0.40+ deprecates the direct reliance on `httpx` inside `starlette.testclient` when newer client adapters or `httpx2` are expected.
- **Trigger Condition:** Executing tests via pytest that instantiate FastAPI's `TestClient`.

### 6.3 Impact Classification
- **Classification:** **Test-Only Fixture Deprecation**.
- **Runtime / Production Impact:** **None**. In production environments, Crosswire services run as ASGI applications under Uvicorn/FastAPI workers and never import `fastapi.testclient`.
- **Database & Financial Impact:** **Zero**. The warning has no interaction with database drivers, SQLite locking, decimal calculations, or HTTP proxy logic.
- **Section 6 Classification:** **VERIFIED**

---

## GAPS IDENTIFIED & RECOMMENDATIONS FOR MILESTONE 2.3

1. **Implement Idempotency Lease TTL and Heartbeat Mechanism:**
   - Add `lease_expires_at` and `heartbeat_at` columns to `booking_operation_claims`.
   - Implement an atomic claim-stealing query for expired leases:
     ```sql
     UPDATE booking_operation_claims
     SET owner_token = :new_token, lease_expires_at = :new_expiry, status = 'CLAIMED'
     WHERE agency_id = :agency_id AND idempotency_key = :idemp_key
       AND lease_expires_at < :now
     ```
2. **Implement Active Saga Recovery Daemon:**
   - Author a background reconciliation worker (or FastAPI lifespan background task) that polls `saga_recovery_records` where `status = 'PENDING'`.
   - Implement idempotent retry routines to poll the Mock Supplier for reservation status and trigger wallet compensation when orphaned holds are discovered.
3. **Expand Synthetic Invariant Violation Test Suite:**
   - Author negative tests specifically triggering violations for Invariants 6.2, 6.3, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, and 6.13 to elevate their status from `PARTIALLY VERIFIED` / `NOT VERIFIED` to fully `VERIFIED`.

---

**Audit Completed By:** Independent Distributed Systems & Transaction Integrity Reviewer  
**Audit Status:** STRICT READ-ONLY VERIFICATION COMPLETE  
**Next Steps:** Proceed to review and plan Milestone 2.3 architecture.

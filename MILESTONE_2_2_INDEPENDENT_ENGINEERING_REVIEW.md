# CROSSWIRE 2.0 — MILESTONE 2.2 INDEPENDENT ENGINEERING AUDIT
## CONCURRENT BOOKING, AGENCY WALLET INTEGRITY & SAGA ORCHESTRATION

**Audit Date:** 2026-11-09  
**Auditor Role:** Independent Principal Distributed Systems, Financial & Application-Security Engineer  
**Repository Path:** `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`  
**Primary Specifications:** `MASTER_PLAN_V2.md`, `MILESTONE_2_2_AUDIT_CHANGELOG.md`  
**Operating Mode:** STRICT READ-ONLY AUDIT (Zero application code or test modifications)  

---

## 1. EXECUTIVE VERDICT: CONDITIONAL PASS

| Assessment Dimension | Rating | Summary |
|:---|:---:|:---|
| **Single-Process Concurrency Protection** | **PASS** | Atomic conditional SQL operations in `agency_wallets` and `hotel_inventory` reliably prevent wallet overdrafts and overbooking under single-worker execution. |
| **Financial Monies & Decimal Handling** | **PASS** | Exact integer minor unit representation (ISO 4217) is used throughout database schemas, Pydantic models, and ledger records. |
| **Authentication & Authorization Perimeter** | **FAIL (CRITICAL)** | `POST /api/v1/bookings` conditionally validates credentials *only* when headers are supplied; unauthenticated callers omitting headers bypass authentication and can drain arbitrary agency wallets. |
| **Multi-Process Distributed Idempotency** | **NOT VERIFIED (HIGH)** | In-flight idempotency relies on application-level asyncio polling with `INSERT OR REPLACE` table writes; concurrent requests across two aggregator workers will dual-call upstream suppliers. |
| **Automated Saga Compensation** | **FAIL (HIGH)** | In `process_booking_intent()`, if wallet capture fails after supplier confirmation, the error handler releases the wallet hold but fails to call `cancel_reservation()`, permanently orphaning supplier inventory. |
| **Wallet Refund Accounting Alignment** | **CONDITIONAL** | `total_captured` is strictly gross lifetime captures. Refunds for wallet-funded bookings do not restore agency wallet available balances. |
| **Invariant Engine Coverage** | **CONDITIONAL** | Invariants 6.1, 6.2, 6.3, 6.6, 6.7, 6.10, 6.11, 6.12 are implemented; Invariants 6.5, 6.8, and 6.9 are completely absent from `engine/invariant_engine.py`. |

### Recommendation
**CONDITIONAL PASS** for local single-process development milestones. **DO NOT PROCEED TO MILESTONE 2.3** until the authentication bypass (`CW-SEC-M2-206`), the automated saga supplier cancellation gap (`CW-ARC-M2-208`), and the multi-process distributed claim lock (`CW-ARC-M2-207`) are remediated.

---

## 2. REPOSITORY STATE & SCOPE REVIEWED

- **Git Commit / Working Directory:** `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire` (No `.git` VCS root present; working tree verified).
- **Active Milestones Reviewed:**
  - Milestone 1: Timeout-after-commit, vulnerable blind retries, reconciliation fallback.
  - Milestone 2.1: Duplicate refund webhooks, HMAC signature verification, multi-provider secret registry, entity validation, atomic refund processing.
  - Milestone 2.2: Concurrent booking, agency wallet debit/hold lifecycle, hotel inventory allocation, distributed saga compensation, and localhost TCP concurrency.
- **Core Files Audited:**
  - `target_platform/models.py` (Domain models, financial minor units, enums, exceptions)
  - `target_platform/aggregator/database.py` (SQLite WAL transactions, wallet hold/capture/release, double-entry ledger)
  - `target_platform/aggregator/booking_service.py` (Wallet booking saga, idempotency wait loop, error compensation)
  - `target_platform/aggregator/app.py` (FastAPI router, header authentication, wallet endpoints)
  - `target_platform/aggregator/supplier_client.py` (HTTP client, timeout & sold-out error handling, cancellation client)
  - `target_platform/supplier/database.py` (Inventory table, atomic vs TOCTOU reservation, cancellation compensation)
  - `target_platform/supplier/supplier_service.py` (Supplier reservation handling, inventory toggle, delay injection)
  - `target_platform/supplier/app.py` (Supplier REST endpoints, config updates)
  - `engine/invariant_engine.py` (Invariants 6.1–6.13, finding mappers)
  - `engine/ground_truth_oracle.py` (Independent database access layer)
  - `tests/test_wallet_concurrency.py`, `tests/test_inventory_concurrency.py`, `tests/test_booking_idempotency_concurrency.py`, `tests/test_saga_compensation.py`, `tests/test_concurrent_booking_tcp.py`

---

## 3. ACTUAL TEST RUN & RUNTIME TELEMETRY

The test suite was executed in the clean, verified Python 3.11 environment.

### Command Executed
```powershell
Set-Location "C:\Users\jayp9\.gemini\antigravity\scratch\crosswire"
$env:PYTHONPATH="C:\Users\jayp9\.gemini\antigravity\scratch\crosswire;C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\.venv\Lib\site-packages"
& "C:\Users\jayp9\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe" -m pytest -v
```

### Execution Telemetry
- **Platform:** Windows x86-64 (win32)
- **Interpreter:** Python 3.11.15 (`cpython-3.11-windows-x86_64-none`)
- **Pytest Version:** 9.2.0 (Pluggy 1.7.0, AnyIO 4.16.0, Pytest-Asyncio 1.4.1.post1)
- **Total Tests Collected:** 95
- **Passed:** 95 (100%)
- **Failed:** 0
- **Skipped / XFailed:** 0
- **Wall-Clock Duration:** 28.91 seconds
- **External Network Access:** Zero outbound socket connections (strictly localhost TCP loopback `127.0.0.1:8016` and `127.0.0.1:8017`).

---

## 4. AUDIT FINDINGS BY SEVERITY

| Finding ID | Severity | CWE | Title | File & Line References |
|:---|:---:|:---:|:---|:---|
| **CW-SEC-M2-206** | **CRITICAL** | CWE-287 / CWE-306 | Unauthenticated Booking Intent & Arbitrary Wallet Draining Bypass | `target_platform/aggregator/app.py:136-146` |
| **CW-ARC-M2-207** | **HIGH** | CWE-362 / CWE-675 | Multi-Process Idempotency Failure & Duplicate Upstream Booking Commitment | `target_platform/aggregator/booking_service.py:275-300`, `database.py:720` |
| **CW-ARC-M2-208** | **HIGH** | CWE-662 / CWE-841 | Missing Automated Supplier Cancellation on Downstream Wallet Capture Failure | `target_platform/aggregator/booking_service.py:450-458`, `tests/test_saga_compensation.py:148-150` |
| **CW-FIN-M2-209** | **MEDIUM** | CWE-682 | Wallet Accounting Disconnect: Wallet Refunds Do Not Restore Available Balance | `target_platform/aggregator/database.py:1204-1265`, `database.py:2040-2100` |
| **CW-INV-M2-210** | **MEDIUM** | CWE-1068 | Specification Discrepancy: Missing Invariants 6.5, 6.8, and 6.9 in Invariant Engine | `engine/invariant_engine.py:450-802` |
| **CW-CON-M2-211** | **LOW** | CWE-362 | Supplier Cancellation Concurrency Window in Deferred Transaction Mode | `target_platform/supplier/database.py:240-262` |

---

## 5. AUDIT A — WALLET CONCURRENCY & FINANCIAL INTEGRITY

### 5.1 Verification Checklist

| Criterion | Result | Technical Analysis & Evidence |
|:---|:---:|:---|
| 1. Available balance derived from authoritative records? | **PARTIAL** | Available balance is calculated from cached projection columns `funded_balance_minor - active_holds_minor - total_captured_minor` on `agency_wallets`, rather than a live sum over `wallet_transactions` or `ledger_entries`. |
| 2. Can multiple concurrent requests reserve the same funds? | **PREVENTED (Fixed)** / **CONFIRMED (Vuln)** | Fixed mode uses atomic conditional SQL update: `WHERE (funded_balance_minor - active_holds_minor - total_captured_minor) >= ?`. Vulnerable mode executes non-atomic read-then-write with `time.sleep(0.05)`, granting over-allocation. |
| 3. Are holds reserved atomically? | **YES** | `create_wallet_hold_fixed()` executes under explicit SQLite `BEGIN IMMEDIATE` transaction. |
| 4. Can a hold be captured more than once? | **NO** | `capture_wallet_hold()` explicitly checks `if hrow["status"] == CAPTURED: return existing`. Repeated captures are idempotent no-ops. |
| 5. Can a hold be released more than once? | **NO** | `release_wallet_hold()` checks `if hrow["status"] == RELEASED: return existing`. Repeated releases are idempotent no-ops. |
| 6. Can a captured hold subsequently be released? | **NO** | Attempting to release a `CAPTURED` hold raises `InvalidStateTransitionError` (`status != HELD`). |
| 7. Can a released hold subsequently be captured? | **NO** | Attempting to capture a `RELEASED` hold raises `InvalidStateTransitionError` (`status != HELD`). |
| 8. Are wallet transactions idempotent? | **PARTIAL** | Idempotency key lookup checks `status != 'RELEASED'`. However, `wallet_holds` lacks a database-level unique constraint on `(agency_id, idempotency_key)`. |
| 9. Database-level constraints enforced? | **PARTIAL** | `uidx_agg_booking_idemp` exists on `booking_records`, but `wallet_holds` relies on application-level queries inside `BEGIN IMMEDIATE`. |
| 10. Projections reconcile with ledger ground truth? | **YES** | Invariant 6.3 verifies `deposit_sum == funded_balance_minor` and `capture_sum == total_captured_minor`. |

### 5.2 Scenario Walkthrough: ₹10,000 Balance vs Two ₹7,000 Concurrent Requests

- **Initial State:** `funded_balance_minor = 1000000` (₹10,000), `active_holds_minor = 0`, `total_captured_minor = 0`. Available balance = 1,000,000.
- **Vulnerable Execution Path (`create_wallet_hold_vulnerable`, Lines 2442–2468):**
  1. Thread A reads available balance = 1,000,000 $\ge$ 700,000 (Pass).
  2. Thread B reads available balance = 1,000,000 $\ge$ 700,000 (Pass).
  3. Artificial delay window `time.sleep(0.05)` allows context interleaving.
  4. Thread A executes blind `UPDATE agency_wallets SET active_holds_minor = active_holds_minor + 700000`.
  5. Thread B executes blind `UPDATE agency_wallets SET active_holds_minor = active_holds_minor + 700000`.
  6. Final DB State: `active_holds_minor = 1400000` (₹14,000), `available_balance_minor = -400000` (-₹4,000). **CRITICAL OVERDRAFT CONFIRMED (`CW-SEC-M2-201`).**
- **Fixed Execution Path (`create_wallet_hold_fixed`, Lines 2359–2374):**
  1. Connection acquires exclusive lock via `BEGIN IMMEDIATE`.
  2. Thread A executes conditional update:
     ```sql
     UPDATE agency_wallets
     SET active_holds_minor = active_holds_minor + 700000,
         active_holds = (active_holds_minor + 700000) / 100.0,
         updated_at = ?
     WHERE agency_id = 'agency_alpha'
       AND (funded_balance_minor - active_holds_minor - total_captured_minor) >= 700000;
     ```
     `cursor.rowcount == 1`. Thread A succeeds; active holds = 700,000; available = 300,000. Commits.
  3. Thread B acquires exclusive lock. Conditional query evaluates `(1000000 - 700000 - 0) >= 700000` $\rightarrow$ `300000 >= 700000` (False).
  4. `cursor.rowcount == 0`. Thread B rolls back and raises `InsufficientWalletFundsError`.
  5. Final DB State: exactly 1 hold granted (₹7,000); available balance = ₹3,000 (300,000 minor units).

### 5.3 Determinism of Race Reproduction
The testbed uses `asyncio.to_thread` with an artificial delay of `time.sleep(0.05)` inside `create_wallet_hold_vulnerable()`. While reliable in localhost test runs, it is not an operating-system synchronization barrier (such as `threading.Barrier(2)`). In overloaded CI environments, timing jitter could theoretically cause serialized execution.

### 5.4 Accounting Equation Audit

```
available_balance = funded_balance - total_captured - active_holds
```

- `funded_balance`: Cumulative lifetime pre-funded credit deposits credited by the platform operator.
- `active_holds`: Temporary commitments locked for in-flight booking sagas.
- `total_captured`: **GROSS LIFETIME CAPTURES** (confirmed debits for issued vouchers).
- **CRITICAL ACCOUNTING FINDING (`CW-FIN-M2-209`):**
  When a booking paid via `WALLET` is subsequently cancelled and refunded via `execute_atomic_refund()` (Lines 1079–1265) or webhook processing:
  - `booking_records.total_refunded_minor` is updated.
  - `refund_records` and `ledger_entries` record credits to `2100_AGENCY_REFUNDABLE_PAYABLE`.
  - **`agency_wallets` is NOT updated.** `total_captured_minor` is never decremented, nor is a `REFUND` entry posted to `wallet_transactions`.
  - Consequently, refunded money does **not** flow back into the agency's floating wallet balance. The accounting equation models `total_captured` as gross rather than net.

---

## 6. AUDIT B — INVENTORY CONCURRENCY

### 6.1 Verification Checklist

| Criterion | Result | Technical Analysis & Evidence |
|:---|:---:|:---|
| 1. Available inventory cannot become negative? | **YES** | `reserve_inventory_fixed()` uses `WHERE available_units >= 1`. |
| 2. Allocated inventory cannot exceed capacity? | **YES** | `allocated_units = capacity - available_units`. Because `available_units >= 1` is required, allocated cannot exceed capacity. |
| 3. Available + allocated reconcile with capacity? | **YES** | Enforced by Invariant 6.6 (`allocated_units + available_units == capacity`). |
| 4. Active supplier reservation corresponds to allocation? | **YES** | Confirmed supplier reservations directly match `allocated_units`. |
| 5. Every cancellation releases inventory exactly once? | **YES** | `cancel_reservation()` checks `if row["status"] == "CANCELLED": return False`. |
| 6. Duplicate cancellations do not inflate inventory? | **YES** | SQL update uses `available_units = MIN(capacity, available_units + 1)`. |
| 7. Failed reservations cannot leave inconsistent allocations? | **YES** | On sold-out or error, reservation creation is aborted before incrementing allocations. |
| 8. Inventory restored safely after saga compensation? | **YES** | `release_inventory()` and `cancel_reservation()` decrement `allocated_units` and increment `available_units`. |

### 6.2 Concurrency Window in Supplier Cancellation (`CW-CON-M2-211`)
In `target_platform/supplier/database.py`:
```python
def cancel_reservation(self, supplier_reservation_id: str) -> bool:
    with self._connection() as conn:
        row = conn.execute("SELECT * FROM supplier_reservations WHERE supplier_reservation_id = ?", ...).fetchone()
        if not row or row["status"] == "CANCELLED":
            return False
        conn.execute("UPDATE supplier_reservations SET status = 'CANCELLED' WHERE supplier_reservation_id = ?", ...)
        conn.execute("UPDATE hotel_inventory SET allocated_units = MAX(0, allocated_units - 1), ...")
```
Because `_connection()` uses default deferred SQLite transactions, two concurrent cancellation requests could both execute the `SELECT` prior to either `UPDATE` committing. Both would see `status != 'CANCELLED'` and both would execute the inventory release update. While `MIN(capacity, ...)` prevents exceeding capacity, it could restore an extra unit belonging to another reservation if the hotel was partially booked.
**Remediation:** Make the status check atomic:
```sql
UPDATE supplier_reservations SET status = 'CANCELLED' WHERE supplier_reservation_id = ? AND status != 'CANCELLED';
```
Only proceed to update `hotel_inventory` if `cursor.rowcount == 1`.

---

## 7. AUDIT C — MULTI-PROCESS IDEMPOTENCY

### 7.1 Analysis of In-Flight Wait Locks
In `target_platform/aggregator/booking_service.py` (Lines 275–300):
```python
if idemp_key:
    existing = self.db.get_booking_by_idempotency_key(intent.agency_id, idemp_key)
    if existing:
        ...
        for _ in range(30):
            await asyncio.sleep(0.05)
            updated = self.db.get_booking_by_idempotency_key(intent.agency_id, idemp_key)
            if updated and updated.status in [TransactionStatus.SUPPLIER_CONFIRMED, ...]:
                return updated
        return existing
```

### 7.2 Multi-Worker Race Condition Analysis (`CW-ARC-M2-207`)
1. **Nature of Lock:** The wait lock is **purely an application-level asyncio sleep polling loop**, NOT a distributed lock, Redis mutex, or database-backed advisory lock.
2. **Dual-Aggregator Failure Scenario:**
   Suppose two separate Uvicorn aggregator worker processes ($W_1$ and $W_2$) share the same backend SQLite database file in WAL mode:
   - Request 1 arrives at $W_1$ with `idempotency_key = "IDEMP-001"`.
   - Request 2 arrives simultaneously at $W_2$ with identical key `"IDEMP-001"` and identical payload.
   - Both $W_1$ and $W_2$ execute `get_booking_by_idempotency_key()` before either has written to the database. Both receive `None`.
   - Both proceed to call `save_booking_intent()`.
   - Both call `save_booking_record()`. Notice line 720 of `database.py`:
     ```sql
     INSERT OR REPLACE INTO booking_records (...) VALUES (...)
     ```
     `INSERT OR REPLACE` deletes conflicting rows rather than raising a constraint conflict!
   - Both $W_1$ and $W_2$ dispatch parallel HTTP requests to the upstream supplier:
     ```python
     supplier_res = await self.supplier_client.call_create_reservation(req)
     ```
   - **Result:** The upstream supplier receives two distinct requests. If the supplier is non-idempotent, **two supplier reservations are created**, double-committing hotel inventory.
3. **Audit Verdict on Distributed Idempotency:** **NOT VERIFIED**. The system is protected against single-process coroutine interleaving after the first insert, but completely vulnerable across multi-process workers.
**Remediation:** Implement an atomic database claim lock:
```sql
INSERT INTO idempotency_claims (agency_id, idempotency_key, locked_at) VALUES (?, ?, ?);
```
Under `BEGIN IMMEDIATE`, if an `sqlite3.IntegrityError` is thrown, the second process must immediately enter the wait-polling state without calling the upstream supplier.

---

## 8. AUDIT D — SAGA COMPENSATION & CRASH RECOVERY

### 8.1 Ten Canonical Failure Scenarios

| # | Failure Case | Persisted Saga State | Wallet Hold State | Supplier Res State | Booking State | Ledger Effects | Automated Recovery / Manual Reconciliation |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|
| **1** | Wallet hold succeeds, supplier sold out | `FAILED` | `RELEASED` | None | `FAILED` | None | **Automated:** Succeeded. Wallet funds restored immediately. |
| **2** | Supplier reservation succeeds, wallet capture fails | `FAILED` | `RELEASED` | **CONFIRMED (Orphaned)** | `FAILED` | None | **DEFECT (`CW-ARC-M2-208`):** Aggregator releases wallet hold but **fails to call supplier cancellation**. Physical inventory leaked. Manual reconciliation required. |
| **3** | Supplier succeeds, cancellation compensation fails | `FAILED` | `RELEASED` | `CONFIRMED` | `FAILED` | None | **Manual Reconciliation Required:** No transactional outbox exists to retry failed cancellations. |
| **4** | Supplier cancellation times out | `FAILED` | `RELEASED` | Ambiguous | `FAILED` | None | **Manual Reconciliation Required:** `call_cancel_reservation()` returns `False` on timeout; no background reconciler scheduled. |
| **5** | Wallet hold release fails | `FAILED` | `HELD` | None | `FAILED` | None | **Defect:** Unreleased hold remains in `HELD`; agency funds frozen indefinitely. No TTL hold reaper exists. |
| **6** | Application crashes after supplier confirmation | `PAYMENT_PENDING` | `HELD` | `CONFIRMED` | `PAYMENT_PENDING` | None | **Manual Reconciliation Required:** On restart, no startup reconciler inspects orphaned `PAYMENT_PENDING` bookings. |
| **7** | Application crashes after wallet capture | `PAYMENT_PENDING` | `CAPTURED` | `CONFIRMED` | `PAYMENT_PENDING` | `PAYMENT` | **Inconsistent State:** Money debited from agency, but booking marked pending without voucher. |
| **8** | Supplier response times out with unknown outcome | `SUPPLIER_UNKNOWN` | `HELD` | Ambiguous | `SUPPLIER_UNKNOWN` | None | **Correctly Handled:** Transitions to `SUPPLIER_UNKNOWN`, preserves hold without double-debiting. |
| **9** | Duplicate retry arrives while original saga unresolved | Unchanged | `HELD` | In-Flight | In-Flight | None | **Correctly Handled:** In-flight polling awaits resolution or returns existing ambiguous state. |
| **10** | Two operations attempt to compensate same booking | `FAILED` | `RELEASED` | `CANCELLED` | `FAILED` | None | **Idempotent:** Sequential repeated calls to `release_wallet_hold` and `cancel_reservation` are no-ops. |

---

## 9. AUDIT E — CROSS-AGENCY ISOLATION & PERIMETER SECURITY

### 9.1 Critical Authentication Perimeter Bypass (`CW-SEC-M2-206`)
In `target_platform/aggregator/app.py` (Lines 133–146):
```python
@aggregator_app.post("/api/v1/bookings", response_model=BookingInitiateResponse)
async def create_booking(
    intent: BookingIntent,
    x_agency_id: Optional[str] = Header(None, alias="X-Agency-ID"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    db = get_aggregator_db()

    # Authentication validation when credentials are provided
    if x_agency_id or x_api_key:
        if not db.authenticate_agency(x_agency_id, x_api_key):
            raise HTTPException(status_code=401, detail="Invalid agency credentials")

        # Intent ownership verification: authenticated agency must match intent agency
        if intent.agency_id != x_agency_id:
            raise HTTPException(status_code=403, detail=...)
```

### Exploit Mechanism
1. The route inspects credentials **only if** `x_agency_id` or `x_api_key` is provided in the request headers.
2. If an attacker sends `POST /api/v1/bookings` with **no headers**:
   - `if x_agency_id or x_api_key:` evaluates to `False`.
   - The entire authentication block is bypassed.
   - The ownership verification check is bypassed.
   - The service executes `process_booking_intent(intent)` with whatever `intent.agency_id` was supplied in the JSON body (e.g., `intent.agency_id = "agency_beta"`).
   - `agency_beta`'s pre-funded wallet balance is debited and held.
**Severity:** **CRITICAL (CVSS 9.1).** Immediate remediation required. Authentication must be mandatory:
```python
agency_id = _check_mandatory_auth(x_agency_id, x_api_key)
if intent.agency_id != agency_id:
    raise HTTPException(status_code=403, detail="Intent ownership mismatch")
```

---

## 10. AUDIT F — INVARIANT-BY-INVARIANT VERIFICATION

| Invariant | Formal Mathematical Definition | Implementation File & Line | Data Source | Vulnerable Test | Fixed Test | Known Blind Spots |
|:---:|:---|:---|:---|:---|:---|:---|
| **6.1** | $\forall a: F(a) - C(a) - H(a) \ge 0$ | `invariant_engine.py:450-510` | Oracle SQLite `agency_wallets` | `test_wallet_concurrency.py:40` | `test_wallet_concurrency.py:92` | Checks projection table; does not check live ledger sum. |
| **6.2** | $\forall a: H(a) \le F(a)$ | `invariant_engine.py:511-541` | Oracle SQLite `agency_wallets` | N/A (tested via 6.1) | `test_wallet_concurrency.py:92` | Only checks active holds vs funded balance; ignores captures. |
| **6.3** | $F(a) = \sum \text{DEP}, C(a) = \sum \text{CAP}$ | `invariant_engine.py:542-576` | Oracle SQLite `wallet_transactions` | N/A | `test_wallet_concurrency.py:125` | Does not verify ledger entries in `ledger_entries` table. |
| **6.4** | $H \cap C = \emptyset \lor H \cap R = \emptyset$ | *Implicit in database.py:2525, 2636* | SQLite `wallet_holds` | N/A | `test_wallet_concurrency.py:160` | No dedicated check method in `invariant_engine.py`. |
| **6.5** | **MISSING FROM ENGINE** | *None* | *None* | *None* | *None* | Completely omitted from `invariant_engine.py`. |
| **6.6** | $A(h, r) \le \text{Cap}(h, r) \land \text{Res}(h, r) \le \text{Cap}$ | `invariant_engine.py:577-642` | Oracle SQLite `hotel_inventory` & `supplier_reservations` | `test_inventory_concurrency.py:43` | `test_inventory_concurrency.py:86` | Relies on supplier internal table rather than external target interface. |
| **6.7** | $\text{Avail}(h, r) \ge 0$ | `invariant_engine.py:643-673` | Oracle SQLite `hotel_inventory` | N/A (tested via 6.6) | `test_inventory_concurrency.py:86` | Does not verify uncommitted hold reservations. |
| **6.8** | **MISSING FROM ENGINE** | *None* | *None* | *None* | *None* | Completely omitted from `invariant_engine.py`. |
| **6.9** | **MISSING FROM ENGINE** | *None* | *None* | *None* | *None* | Completely omitted from `invariant_engine.py`. |
| **6.10** | $|\text{Bookings}(a, k)| = 1$ | `invariant_engine.py:674-763` | Oracle SQLite `booking_records` | N/A | `test_booking_idempotency_concurrency.py:38` | Relies on single-process SQLite serial execution. |
| **6.11** | $\text{Status}=\text{FAILED} \implies H(b)=\text{RELEASED}$ | `invariant_engine.py:674-763` | Oracle SQLite `wallet_holds` | N/A | `test_saga_compensation.py:53` | Does not verify whether supplier reservation was cancelled. |
| **6.12** | $\text{Holds}(a) \cap W(b) = \emptyset$ | `invariant_engine.py:764-802` | Oracle SQLite `wallet_holds` | N/A | `test_booking_idempotency_concurrency.py:165` | Mislabeled in changelog as 6.13. |
| **6.13** | Agency Wallet Authorization Isolation | *Handled under 6.12* | Oracle SQLite `agency_wallets` | N/A | `test_booking_idempotency_concurrency.py:165` | Merged into 6.12 implementation. |

---

## 11. AUDIT G — TCP AND REGRESSION VERIFICATION

### 7.1 Real Sockets & Process Inspection
- `tests/test_concurrent_booking_tcp.py` spawns separate Uvicorn processes using `subprocess.Popen([sys.executable, "-m", "uvicorn", ...])`:
  - Aggregator: port 8016
  - Supplier: port 8017
- The test suite connects via real TCP sockets over `http://127.0.0.1:8016` using `httpx.AsyncClient`.
- There is **no ASGI in-memory client substitution** in `test_concurrent_booking_tcp.py`.

### 7.2 Concurrency Levels Verified
- **25 concurrent requests:** ₹1,000 requests against ₹10,000 balance $\rightarrow$ exactly 10 succeed, 15 receive HTTP 400. Available balance ₹0.
- **10 concurrent requests:** Competing for single room $\rightarrow$ exactly 1 succeeds, 9 receive HTTP 409 (Sold Out).
- **50 concurrent requests:** Same idempotency key and payload $\rightarrow$ all 50 return HTTP 200 with identical `booking_id` and `voucher_id`. Zero duplicate database records.

---

## 12. CLAIMS VERIFIED VS CLAIMS NOT VERIFIED

### Verified Claims
- Integer minor units are implemented across all wallet and inventory tables.
- Single-process concurrent requests cannot overdraft agency wallets in fixed mode.
- Single-process concurrent requests cannot overbook hotel inventory beyond physical room capacity.
- 50x concurrent re-delivery of the same idempotency key produces a single booking within a single worker process.
- All 95 test suite tests execute and pass in under 30 seconds.

### Not Verified / Invalidated Claims
- **Multi-Process Distributed Idempotency (INVALIDATED):** In-flight idempotency wait locks do not protect against dual-supplier commitments when two separate aggregator processes run against the shared database.
- **Automated Supplier Cancellation on Capture Failure (INVALIDATED):** The application code does not automatically invoke `call_cancel_reservation()` when wallet capture fails; the test masked this by manually invoking the cancellation method.
- **Mandatory Agency Authentication (INVALIDATED):** Header authentication is optional in `create_booking()`, allowing unauthorized callers to drain agency wallets.
- **Complete Invariant Engine (INVALIDATED):** Invariants 6.5, 6.8, and 6.9 are missing.

---

## 13. REQUIRED REMEDIATION BEFORE MILESTONE 2.3

The following remediations must be completed and verified before proceeding to Milestone 2.3:

1. **Fix Critical Authentication Bypass (`CW-SEC-M2-206`):**
   - Make `X-Agency-ID` and `X-API-Key` strictly mandatory on all booking and wallet mutation endpoints.
   - Reject unauthenticated requests with HTTP 401; reject mismatched agency IDs with HTTP 403.
2. **Implement Automated Saga Compensation (`CW-ARC-M2-208`):**
   - In `booking_service.py:process_booking_intent()`, wrap the wallet capture call in a dedicated `try/except` block.
   - If `capture_wallet_hold()` fails after `call_create_reservation()` succeeded, automatically invoke `await self.supplier_client.call_cancel_reservation(supplier_res.supplier_reservation_id, reason="Wallet capture failure")`.
3. **Database-Backed Idempotency Claim Lock (`CW-ARC-M2-207`):**
   - Replace `INSERT OR REPLACE` with an atomic idempotency claim table (`agency_id`, `idempotency_key`, `intent_id`, `status`, `created_at`).
   - Use `INSERT ... ON CONFLICT DO NOTHING` inside `BEGIN IMMEDIATE` to ensure only one process can proceed to the supplier.
4. **Implement Missing Invariants 6.5, 6.8, and 6.9:**
   - Define and implement check methods in `engine/invariant_engine.py`.
5. **Atomic Supplier Cancellation:**
   - Update `target_platform/supplier/database.py:cancel_reservation` to conditionally update status: `WHERE supplier_reservation_id = ? AND status != 'CANCELLED'`.

---

## 14. FINAL GO / NO-GO RECOMMENDATION

### Verdict: **NO-GO FOR MILESTONE 2.3 UNTIL REMEDIATION COMPLETE**

Milestone 2.2 has established high-quality financial mechanics, exact minor unit representation, and solid single-process concurrency primitives. However, due to the **CRITICAL authentication bypass on booking creation (`CW-SEC-M2-206`)** and the **missing automated supplier compensation on capture failure (`CW-ARC-M2-208`)**, the platform cannot be certified as transactionally secure.

A focused remediation sprint addressing findings `CW-SEC-M2-206`, `CW-ARC-M2-207`, and `CW-ARC-M2-208` is required prior to starting Milestone 2.3.

# Crosswire Milestone 2.3B: Outbox Engine & Multi-Supplier Execution (Remediated Release)

> **Milestone 2.3B:** A durable, capability-aware outbox transaction engine for multi-supplier split-inventory execution with atomic leasing, crash recovery, fencing-token monotonicity, and append-only evidence auditing.
> **Prototype Classification:** Security Testing & Transaction-Integrity DAST Engine Prototype (Not deployed to production).

---

## 1. System Requirements & Environment

- **Python Version:** 3.11+ (Tested on Python 3.11.15)
- **SQLite Version:** 3.35+ (Requires `RETURNING` clause support; tested on SQLite 3.45+)
- **OS Platform:** Windows / Linux / macOS compatible
- **Required Dependencies:**
  - `pytest>=8.0.0`
  - `cryptography>=42.0.0`

Install required dependencies:
```bash
pip install -r requirements.txt
```

---

## 2. Directory Structure

```text
m2_3b/
├── coordinator/
│   ├── __init__.py
│   └── parent_saga_coordinator.py      # Multi-child saga coordinator with late arrival compensation & ledger capture
├── evidence/
│   ├── __init__.py
│   ├── coverage_oracle.py              # Parent stay coverage & occupancy verification oracle
│   ├── observation_validator.py        # Strict two-stage observation validator (missing -> INCONCLUSIVE, contradictory -> REJECTED)
│   ├── observation_writer.py           # Append-only raw observation writer with confirmed financials
│   └── resolution_manager.py           # Authoritative resolution state machine & resolution history
├── outbox/
│   ├── __init__.py
│   ├── dispatcher.py                   # Capability-aware dispatcher with explicit lookup classification & cancel retry lookup
│   └── outbox_manager.py               # Durable outbox with atomic leasing, expired lease blocking, attempt decoupling & backoff
├── schema/
│   ├── __init__.py
│   ├── db_connection.py                # Database factory enforcing PRAGMA foreign_keys, recursive_triggers & WAL mode
│   └── schema_m2_3a.sql                # Complete relational schema with child_operation_attempts & provider capabilities
├── suppliers/
│   ├── __init__.py
│   ├── base_adapter.py                 # Supplier adapter interfaces & capability specifications
│   └── synthetic_supplier.py           # In-memory adversarial testbed with failure injection & double-booking detection
├── tests/
│   ├── __init__.py
│   ├── test_helpers.py                 # Database fixture factory and test booking seeders
│   ├── test_coverage_oracle.py         # 7 tests: coverage oracle & split-stay continuity
│   ├── test_crash_recovery_concurrency.py # 10 tests: worker crashes, lease renewals & race conditions
│   ├── test_outbox_engine.py           # 7 tests: atomic leases, fencing tokens & account authorizations
│   ├── test_parent_coordinator.py      # 5 tests: saga coordination, compensation & voucher guards
│   ├── test_pinned_transport.py        # 10 tests: IP pinning, CGNAT blocking, DNS rebinding & TLS
│   ├── test_supplier_capabilities.py   # 6 tests: 3 capability modes (IDEMPOTENT_WITH_LOOKUP, LOOKUP_ONLY, NEITHER)
│   └── test_remediation_regressions.py # 9 tests: dedicated regression proofs for all 8 blocker defects
└── README.md                           # This documentation
```

---

## 3. Reproduction Commands

### A. Run Complete Remediated M2.3B Test Suite (54 Tests)
```bash
python -m pytest m2_3b/tests/ -v
```
*Expected Result:* `54 passed in ~1.1s` (0 failed, 0 skipped, 100% pass rate).

### B. Run Dedicated Remediation Regression Suite (9 Tests)
```bash
python -m pytest m2_3b/tests/test_remediation_regressions.py -v
```
*Expected Result:* `9 passed in ~0.3s`.

### C. Run Frozen M2.2 Baseline Test Suite (196 Tests)
```bash
python -m pytest tests -q
```
*Expected Result:* `196 passed in ~55s`.

### D. Verify SQLite Relational Foreign Keys and Database Integrity
```bash
python -c "from m2_3b.tests.test_helpers import get_test_db, seed_test_booking_environment, seed_child_intent; conn = get_test_db(); seed_test_booking_environment(conn); seed_child_intent(conn, 'C1', 'BKG_TEST_100', 'AGENCY_ALPHA', 'SUP_EXPEDIA', 'ACC_GLOBAL_EXP'); fks = conn.execute('PRAGMA foreign_key_check;').fetchall(); ic = conn.execute('PRAGMA integrity_check;').fetchall(); assert len(fks) == 0; assert [r[0] for r in ic] == ['ok']; print('Integrity OK, 0 FK errors')"
```
*Expected Result:* `Integrity OK, 0 FK errors`.

---

## 4. Remediated Defects Overview

1. **Attempt ID Foreign Key Breakage (P0):**
   - Decoupled mutable outbox leasing from immutable attempt history via new `child_operation_attempts` table.
   - Observations reference `child_operation_attempts`, preventing FK failure on retries.
2. **Lease Expiry Enforcement (P0):**
   - Added `AND lease_expires_at > CURRENT_TIMESTAMP` to `renew_lease`, `complete_operation`, and `release_for_retry`.
3. **Orphan Late Supplier Confirmation (P0):**
   - Coordinator detects active children on `CANCELLED` parents and enqueues compensating cancellation outbox operations. Parent remains `CANCELLED`, hold remains `RELEASED`, zero voucher issued.
4. **Financial Ledger Atomicity (P0):**
   - Holds captured on parent confirmation atomically post a `HOLD_CAPTURE` event to `ledger_events` with stable reference `TXN_CAP_{booking_id}`.
5. **Observation Validator Hardening (P1):**
   - Strictly enforces 7 required fields: hotel, check-in, check-out, room type, occupancy, currency, and rate. Contradictory $\to$ `REJECTED`, missing $\to$ `INCONCLUSIVE`, exact match $\to$ `VALIDATED`.
6. **Lookup Misclassification (P1):**
   - `_dispatch_lookup` maps `ACTIVE` $\to$ `RESERVATION_ACTIVE`, `CANCELLED` $\to$ `RESERVATION_CANCELLED`, `NOT_FOUND` $\to$ `NOT_FOUND`, and `PENDING`/`UNKNOWN` $\to$ `AMBIGUOUS`.
7. **Cancellation Capabilities & Deduplication (P1):**
   - Honors account `cancel_capability`, deduplicates cancellation outbox rows, and checks lookup before re-issuing cancel requests on retry.
8. **Transport Redirect Security (P1):**
   - Rejects redirects on mutating methods (`POST`), strips all sensitive credential headers on cross-origin redirects, and blocks external endpoints when `test_mode=True`.
9. **Persisted Backoff & WAL Configuration (P2):**
   - Added `next_attempt_at` to outbox schema; configured WAL journal mode for disk databases.

---

## 5. Explicit Disclosures & Known Boundaries

- **Single Hotel / Single Currency:** Prototype is strictly bounded to single-hotel, single-currency operation (Invariant 5.7 multi-currency ledger decomposition remains deferred).
- **Simulated External Network:** No live supplier or payment APIs were contacted during testing. All transport operations enforce `test_mode=True`.
- **Git Repository Non-Interference:** The workspace is not initialized as a Git repository; non-interference with the M2.2 baseline was verified via direct SHA-256 and byte-size hash manifest matching.

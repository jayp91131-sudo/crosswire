# CROSSWIRE 2.2 — FINAL RECOVERY HARDENING CHANGELOG

**Date:** 2026-11-10  
**Repository:** `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`  
**Milestone:** 2.2 Final Recovery Hardening  
**Target Specification:** `MASTER_PLAN_V2.md`  
**Prior Audits & Verifications:**
- `MILESTONE_2_2_INDEPENDENT_ENGINEERING_REVIEW.md`
- `MILESTONE_2_2_REMEDIATION_VERIFICATION.md`
- `MILESTONE_2_2_SUPPLEMENTAL_VERIFICATION.md`

---

## 1. Executive Summary

This changelog records the complete hardening sprint resolving all remaining crash-recovery, fencing, autonomous saga execution, and invariant-verification gaps identified in the Supplemental Verification report.

Before this sprint, the suite had 110 passing tests. With the completion of this sprint, **146 tests pass across the entire repository (0 failures, 1 deprecation warning)**.

---

## 2. Inventory of Changes

### A. Data Models (`target_platform/models.py`)
- **`AggregatorFencingError`**: Added dedicated exception class for stale/zombie worker fencing violations.
- **`BookingOperationClaimRecord`**:
  - `fencing_token: int = 1`: Monotonically increasing generation number.
  - `lease_expires_at: Optional[str]`: UTC ISO-8601 lease expiration timestamp.
  - `heartbeat_at: Optional[str]`: Last active worker heartbeat.
  - `last_owner_token: Optional[str]`: Traceability identifier of previous owner before recovery.
  - `persisted_stage: Optional[str]`: Checkpoint stage marker (`CLAIMED`, `WALLET_HOLD_CREATED`, `SUPPLIER_REQUEST_PENDING`, `SUPPLIER_CONFIRMED`, `WALLET_CAPTURE_PENDING`, `COMPLETED`, `FAILED`, `RECONCILIATION_REQUIRED`).
- **`SagaRecoveryRecord`**:
  - `owner_token: Optional[str]`: Worker identity holding active compensation lease.
  - `fencing_token: int = 0`: Monotonically increasing fencing counter preventing split-brain compensation.
  - `lease_expires_at: Optional[str]`: UTC ISO-8601 task lease expiration.
  - `next_attempt_at: Optional[str]`: UTC ISO-8601 exponential backoff schedule.
- **`WalletTransactionRecord`**:
  - `hold_id: Optional[str] = None`: Exposed associated hold ID for transaction-level hold exclusivity auditing.

### B. Database Engine (`target_platform/aggregator/database.py`)
- **Schema Migration & Tables**:
  - `booking_operation_claims`: Altered table with `fencing_token`, `lease_expires_at`, `heartbeat_at`, `last_owner_token`, `persisted_stage`.
  - `saga_recovery_records`: Altered table with `owner_token`, `fencing_token`, `lease_expires_at`, `next_attempt_at`.
- **Claim Lease Acquisition & Stealing (`acquire_booking_claim`)**:
  - Atomic conditional claim stealing for expired claims in pre-dispatch stages (`CLAIMED`, `WALLET_HOLD_CREATED`).
  - Lease refresh when re-invoked by current owner.
  - Monotonic fencing increment upon claim transfer.
- **Claim Status Mutation with Fencing Enforcement (`update_claim_status`)**:
  - Verifies `expected_fencing_token` and `expected_owner_token` inside `BEGIN IMMEDIATE`.
  - Raises `AggregatorFencingError` immediately on mismatch.
- **Mid-Flight Claim Reclaim (`reclaim_expired_claim_for_external_recovery`)**:
  - Allows atomic mid-flight claim adoption when lease has expired and status is `SUPPLIER_REQUEST_PENDING`, `SUPPLIER_CONFIRMED`, or `WALLET_CAPTURE_PENDING`.
- **Claim Heartbeating (`heartbeat_claim`)**:
  - Extends active claim leases periodically.
- **Saga Recovery Task Claiming (`claim_pending_saga_recovery_task`)**:
  - Atomically leases tasks in `PENDING` or `RECONCILIATION_REQUIRED` whose `lease_expires_at` has elapsed and `next_attempt_at <= now`.
  - Increments fencing token and assigns `owner_token`.
- **Saga Task Completion (`complete_saga_recovery_task`)**:
  - Transition task to `COMPENSATED` guarded by fencing token and owner token.
- **Saga Task Failure & Backoff (`fail_or_retry_saga_recovery_task`)**:
  - Schedules next attempt with exponential backoff; transitions to `FAILED_MANUAL_RECONCILIATION_REQUIRED` upon exceeding `max_attempts`.
- **Saga Record Retrieval (`get_saga_recovery_record`)**:
  - Added direct primary-key fetch for single saga recovery records.

### C. Booking Service Orchestration (`target_platform/aggregator/booking_service.py`)
- **Worker Identity & Fencing State**:
  - Added unique instance owner token (`worker_{pid}_{uuid}`).
  - Added `current_fencing_token` tracking.
- **External Dispatch Recovery (`recover_external_dispatch`)**:
  - Handles expired in-flight claims without spawning duplicate supplier bookings.
  - Reconciles with upstream supplier via `intent_id`.
  - If supplier reservation exists: adopts reservation, captures wallet hold, issues voucher.
  - If supplier confirms non-existence in FIXED mode: retries once under strict claim ownership.
  - If ambiguous or unsupported: transitions claim to `RECONCILIATION_REQUIRED` without over-booking.
- **Fenced Upstream Calls**:
  - Updated all wallet and card booking status transitions to pass `expected_fencing_token` and `expected_owner_token`.
- **Intermediate State Shielding**:
  - Non-winning concurrent readers waiting on an existing claim never return uncommitted `BOOKING_PENDING` records; polls until final transaction state is committed.
- **Refund Idempotency Short-Circuit**:
  - Allows idempotent replay of completed refunds even if booking is in terminal `REFUNDED` status.

### D. Autonomous Saga Recovery Worker (`target_platform/aggregator/saga_recovery_worker.py`)
- Authoritative daemon worker executing recovery cycles:
  - Fetches and claims eligible tasks atomically.
  - Implements idempotent `CANCEL_SUPPLIER_RESERVATION` calling supplier cancellation API.
  - Implements idempotent `RELEASE_WALLET_HOLD` restoring available wallet balance.
  - Handles transient network timeouts with exponential backoff.
  - Protects against stale execution via fencing token verification.

---

## 3. Test Suites Implemented

1. **`tests/test_claim_recovery_hardening.py` (4 tests)**
   - Pre-dispatch crash: Worker 1 dies at `CLAIMED`, Worker 2 reclaims and completes booking.
   - Mid-dispatch crash with lost response: Worker 1 dies at `SUPPLIER_REQUEST_PENDING`, Worker 2 recovers, adopts orphaned reservation, and captures hold.
   - Zombie worker fencing: Paused Worker 1 attempts mutation after lease expiration and is rejected by `AggregatorFencingError`.
   - Concurrent claim recovery race: Exactly one worker wins recovery lease.

2. **`tests/test_saga_worker_active_recovery.py` (5 tests)**
   - Active cancellation: Worker executes supplier cancellation and marks task `COMPENSATED`.
   - Active hold release: Worker releases wallet hold, restoring balance.
   - Backoff retry: Network timeout caught, attempt count incremented, backoff scheduled.
   - Crash & successor recovery: Worker 1 dies mid-compensation; Worker 2 steals lease and completes task.
   - Concurrent worker race: Two workers competing for the same saga task resolved atomically.

3. **`tests/test_complete_invariant_verification.py` (20 tests)**
   - 10 paired tests (healthy baseline vs synthetic violation) for Invariants 6.2, 6.3, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, and 6.13.

4. **`tests/test_wallet_refund_concurrency.py` (4 tests)**
   - Concurrent partial refunds exceeding entitlement atomically gated.
   - Concurrent partial refunds within entitlement split and exhaust entitlement accurately.
   - Concurrent duplicate refund requests with same idempotency key are idempotent.
   - Concurrent card refunds produce ZERO mutations on agency wallet.

5. **`tests/test_supplier_cancellation_audit.py` (3 tests)**
   - Cancellation concurrent with new booking claiming the last room preserves inventory non-negativity and conservation.
   - 25 concurrent cancellation requests on a single reservation: exactly 1 succeeds, 24 return False.
   - Injected disk error during cancellation triggers complete transaction rollback, preserving confirmed state and original inventory.

---

## 4. Verification Summary

```text
======================= 146 passed, 1 warning in 46.72s =======================
```
- Total test modules: 26
- Total tests executed: 146
- Passing: 146
- Failing: 0
- Regressions: 0

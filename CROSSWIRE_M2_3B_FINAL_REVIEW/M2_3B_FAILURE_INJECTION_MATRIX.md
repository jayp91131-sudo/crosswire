# Crosswire 2.3B — Failure Injection Matrix

**Engine Version:** Milestone 2.3B Multi-Supplier Outbox Execution Engine  
**Execution Timestamp:** 2026-11-14T12:37:52+05:30  
**Status:** ALL 10 CONCURRENCY & CRASH SCENARIOS VERIFIED (`10/10 PASSED`)  
**Test Suite:** `m2_3b/tests/test_crash_recovery_concurrency.py`

---

## 1. Executive Summary

Milestone 2.3B introduces a high-integrity, capability-aware outbox engine and parent saga coordinator designed to survive arbitrary distributed-system anomalies, network drops, split-brain worker races, and process crashes without violating travel-platform financial integrity.

This matrix documents the verification of the 10 mandatory failure modes executed against disposable SQLite databases running the complete M2.3A schema and M2.3B outbox engine.

---

## 2. Summary Failure Matrix

| # | Failure Mode Scenario | Injection Mechanism | Governing Invariant | Observed System Behavior | Automated Test Reference | Result |
|---|----------------------|---------------------|---------------------|--------------------------|--------------------------|--------|
| **1** | **Worker crash after dispatch before DB commit** | Worker drops dead after HTTP 200 from supplier; no local outbox/observation commit | **Zero Duplicate Bookings** | Lease expires; peer worker recovers lease; capability lookup recovers existing reservation with 0 duplicates | `test_01_worker_crash_after_dispatch_before_commit` | **PASSED** |
| **2** | **Worker crash during lease renewal** | Active worker heartbeats once, then dies without renewing lease | **Liveness / Orphan Reclaim** | Heartbeat ceases; lease timestamp elapses; peer worker safely re-leases task | `test_02_worker_crash_during_lease_renewal` | **PASSED** |
| **3** | **Lease expiry during slow supplier call** | Supplier call takes longer than lease timeout; peer worker claims task | **Monotonic Fencing Tokens** | Peer worker advances fencing token to 3; original worker's delayed commit is rejected (`token=2 < token=3`) | `test_03_lease_expiry_during_long_running_supplier_call` | **PASSED** |
| **4** | **Two workers racing on expired lease** | Concurrent worker threads simultaneously execute atomic lease acquisition query | **Mutual Exclusion (`RETURNING`)** | Exactly one worker succeeds via atomic conditional `UPDATE ... RETURNING`; second worker receives `None` | `test_04_two_workers_racing_on_expired_lease` | **PASSED** |
| **5** | **Late response arrives after lease takeover & resolution** | Worker 1 receives delayed response after Worker 2 already resolved child | **Immutable Evidence & DB Trigger Guard** | Worker 1's observation is appended immutably, but authoritative resolution is aborted by `trg_prevent_stale_fencing_token_resolution` | `test_05_late_response_rejected_by_fencing_token_trigger` | **PASSED** |
| **6** | **Duplicate supplier webhook arrives for resolved child** | Webhook delivers identical confirmation payload for already-active child | **Append-Only Idempotence** | Raw observation is stored as new audit row; child authoritative pointer remains valid; zero state regression | `test_06_duplicate_supplier_webhook_preserves_evidence_idempotently` | **PASSED** |
| **7** | **Outbox operation retry exhaustion** | Operation fails on all attempts up to `max_attempts` | **Bounded Retries & Escalation** | Outbox marks op `FAILED`; child intent set to `RECONCILIATION_REQUIRED`; parent saga retains hold and halts | `test_07_outbox_retry_exhaustion_escalates_to_reconciliation` | **PASSED** |
| **8** | **Cancellation dispatched while creation in flight** | Partial failure on sibling child triggers all-or-nothing compensation | **All-or-Nothing Saga Consistency** | Parent enters `CANCELLATION_PENDING`; compensation cancel op enqueued in outbox; hold retained until cancel confirmed | `test_08_cancellation_dispatched_while_creation_in_flight` | **PASSED** |
| **9** | **Concurrent parent saga evaluations** | Multiple coordinator threads race to reconcile and advance parent booking | **Single Voucher & Single Capture** | Exactly one voucher record issued; wallet hold transitioned to `CAPTURED` exactly once; all threads agree | `test_09_concurrent_parent_saga_evaluations` | **PASSED** |
| **10** | **Database restart / connection drop mid-transaction** | Connection abruptly drops mid-transaction after partial uncommitted writes | **ACID Rollback & Immutability** | Database automatically rolls back uncommitted changes; subsequent connection sees clean previous state | `test_10_database_restart_mid_transaction_rollback` | **PASSED** |

---

## 3. Detailed Failure Mode Analysis & Security Guarantees

### Scenario 1: Worker Crash After Dispatch Before Commit
- **The Threat:** A worker dispatches a reservation creation request to Expedia. The supplier succeeds and allocates room inventory. Before the worker can write the supplier reservation ID to SQLite, the worker process is killed (`SIGKILL` / OOM / power loss). Blindly retrying on restart would issue a second booking and create an expensive double booking.
- **Engine Defense:** The outbox tracks the stable `logical_operation_id` (`{child_intent_id}:CREATE_RESERVATION`) and immutable `supplier_idempotency_key`. When Worker 2 reclaims the expired lease, its dispatcher checks supplier capability `IDEMPOTENT_WITH_LOOKUP`. The adapter polls `lookup_reservation` or sends the identical idempotency key. The existing reservation (`RES_...`) is returned, the observation is validated and resolved, and `reservations_created_count` remains strictly `1`.

### Scenario 2: Worker Crash During Lease Renewal
- **The Threat:** Workers renewing leases via background heartbeat might crash mid-execution, leaving operations stuck in `IN_PROGRESS` forever (zombie tasks).
- **Engine Defense:** Leases have strict wall-clock expiration timestamps (`lease_expires_at`). In `OutboxManager.acquire_lease`, runnable candidates include `status = 'PENDING'` OR `(status = 'IN_PROGRESS' AND lease_expires_at < current_timestamp)`. When heartbeats cease, peer workers automatically steal the abandoned lease.

### Scenario 3: Lease Expiry During Slow Supplier Call
- **The Threat:** A supplier network call stalls for 45 seconds while the lease timeout is 30 seconds. A peer worker assumes the original worker died and takes over the operation. If the original worker suddenly wakes up and completes the task, it could overwrite state or cause split-brain results.
- **Engine Defense:** Every lease acquisition atomically increments a monotonic `fencing_token`. Worker 1 holds token 2; Worker 2 acquires token 3. When Worker 1 calls `complete_operation(..., fencing_token=2)`, the database query matches `WHERE fencing_token = ?`. The update matches 0 rows and returns `False`. Worker 1 is safely fenced out.

### Scenario 4: Two Workers Racing on Expired Lease
- **The Threat:** When a lease expires, multiple idle workers may attempt to claim it at the exact same millisecond.
- **Engine Defense:** Lease acquisition uses an atomic SQLite conditional `UPDATE child_operation_outbox ... RETURNING *` executed within an immediate transaction. SQLite's write serialization ensures exactly one worker modifies the row and receives the returned record; all competing workers receive `None`.

### Scenario 5: Late Response Arrives After Lease Takeover & Resolution
- **The Threat:** Worker 2 has already acquired the lease, resolved the reservation with fencing token 2, and advanced the child intent. Worker 1's packet finally arrives with stale token 1.
- **Engine Defense:** Two distinct defense layers:
  1. The raw evidence from Worker 1 is recorded in `supplier_reservation_observations` (append-only history is never discarded).
  2. When `ResolutionManager` attempts to update `child_current_authoritative_resolution`, the schema trigger `trg_prevent_stale_fencing_token_resolution` checks `WHEN NEW.fencing_token < OLD.fencing_token` and raises an immediate abort (`ConcurrencyViolation: Stale fencing token cannot overwrite current authoritative resolution`).

### Scenario 6: Duplicate Supplier Webhook Arrives for Resolved Child
- **The Threat:** Hotel suppliers frequently redeliver asynchronous webhook confirmations. A webhook arriving after manual or automated resolution must not disrupt the booking or trigger multiple captures.
- **Engine Defense:** Webhook observations specify `observation_source = 'WEBHOOK_NOTIFICATION'` and omit worker operation/fencing token fields (enforced by schema CHECK constraint). The raw observation is appended. If the reservation details match, the child current resolution pointer is maintained idempotently.

### Scenario 7: Outbox Operation Retry Exhaustion
- **The Threat:** Continuous supplier network timeouts could trigger endless retry loops or prematurely fail the parent booking without operator awareness.
- **Engine Defense:** Operations enforce `max_attempts`. Once attempts reach `max_attempts`, the capability dispatcher marks the outcome as `RECONCILIATION_REQUIRED`. The child intent enters `RECONCILIATION_REQUIRED`, which halts the parent coordinator. The financial hold is strictly retained (`HELD`), and voucher issuance is blocked until human intervention.

### Scenario 8: Cancellation Dispatched While Creation In Flight
- **The Threat:** In a 2-room booking, Room 1 confirms, but Room 2 is definitively rejected. If Room 1 is left confirmed, the customer is billed for an incomplete stay (partial fulfillment hazard).
- **Engine Defense:** The `ParentCoordinator` reduces child states. With 1 active and 1 failed child, it immediately transitions the parent booking to `CANCELLATION_PENDING` and enqueues a `CANCEL_RESERVATION` operation in the outbox for Room 1. The wallet hold remains `HELD` until Room 1's cancellation is confirmed by supplier evidence.

### Scenario 9: Concurrent Parent Saga Evaluations
- **The Threat:** Two asynchronous worker threads completing sibling child intents simultaneously invoke `reconcile_and_advance_parent` on the parent booking.
- **Engine Defense:** The final transition to `CONFIRMED`, the wallet hold capture (`UPDATE wallet_holds SET status = 'CAPTURED' WHERE status = 'HELD'`), and the voucher record insertion (`INSERT INTO voucher_records ...`) occur in an atomic transaction guarded by unique constraints on `booking_id` in `voucher_records`. Exactly one voucher is created, and exactly one capture occurs.

### Scenario 10: Database Restart / Connection Drop Mid-Transaction
- **The Threat:** Network failure between the application server and the database engine mid-transaction could leave half-written outbox operations or partial ledger events.
- **Engine Defense:** SQLite adheres to strict ACID semantics. Uncommitted transactions are automatically rolled back upon connection termination or engine restart. Verified via crash simulation where uncommitted updates reverted cleanly to baseline state.

---

## 4. Verification Proof

Execution of the full test suite demonstrates that all 10 failure scenarios pass deterministically:
```
m2_3b/tests/test_crash_recovery_concurrency.py::test_01_worker_crash_after_dispatch_before_commit PASSED [ 10%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_02_worker_crash_during_lease_renewal PASSED [ 20%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_03_lease_expiry_during_long_running_supplier_call PASSED [ 30%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_04_two_workers_racing_on_expired_lease PASSED [ 40%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_05_late_response_rejected_by_fencing_token_trigger PASSED [ 50%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_06_duplicate_supplier_webhook_preserves_evidence_idempotently PASSED [ 60%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_07_outbox_retry_exhaustion_escalates_to_reconciliation PASSED [ 70%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_08_cancellation_dispatched_while_creation_in_flight PASSED [ 80%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_09_concurrent_parent_saga_evaluations PASSED [ 90%]
m2_3b/tests/test_crash_recovery_concurrency.py::test_10_database_restart_mid_transaction_rollback PASSED [100%]
```

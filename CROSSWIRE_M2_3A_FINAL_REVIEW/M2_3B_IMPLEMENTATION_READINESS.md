# CROSSWIRE 2.3B — IMPLEMENTATION READINESS PLAN
## Technical Specification, State Machines, Worker Pipeline, and Execution Sequence

**Status:** PLANNING ONLY (DO NOT IMPLEMENT UNTIL FORMAL M2.3A REVIEW SIGNOFF)  
**Parent Architecture:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_CONSISTENCY_CORRECTIONS.md`  
**Prerequisite:** M2.3A Schema Prototype Verified & Accepted  

---

## 1. Executive Summary & Scope

Milestone 2.3B delivers the outbox execution engine, background worker daemon, capability-aware supplier adapters, two-stage observation validation pipeline, and parent saga coordinator for multi-room / multi-segment bookings.

### Strict Scope Boundaries
- **In Scope:**
  - Multi-child booking coordination for a single hotel property.
  - Capability-aware dispatch (`IDEMPOTENT_WITH_LOOKUP`, `LOOKUP_ONLY`, `NEITHER`).
  - Worker lease acquisition, heartbeat, fencing, and crash recovery.
  - Append-only observation generation from worker responses, webhooks, and manual reconciliation.
  - Continuous stay coverage and occupancy validation.
  - Pinned safe HTTP transport (SSRF prevention).
- **Out of Scope (Deferred):**
  - Multi-hotel bookings, flights, rental cars.
  - Foreign exchange / multi-currency arithmetic.
  - Invariant 5.7 double-entry accounting decomposition (signed ledger preserved).
  - External card gateway live transactions (gated by separate contract verification).

---

## 2. Required Modules & File Topology

```
engine/
├── outbox/
│   ├── __init__.py
│   ├── outbox_manager.py           # Durable queue enqueue, claim, lease heartbeat
│   ├── worker_daemon.py            # Polling loop, worker lifecycle, lease expiration
│   ├── attempt_tracker.py          # Attempt lineage, fencing tokens, exponential backoff
│   └── payment_outbox_manager.py   # Card capture & refund outbox handlers
├── suppliers/
│   ├── __init__.py
│   ├── base_adapter.py             # Capability contract (CREATE, CANCEL, LOOKUP)
│   ├── expedia_adapter.py          # IDEMPOTENT_WITH_LOOKUP implementation
│   ├── hotelbeds_adapter.py        # LOOKUP_ONLY implementation
│   └── direct_adapter.py           # NEITHER implementation
├── transport/
│   ├── __init__.py
│   ├── pinned_transport.py         # Socket-level IP pinning, SSL verification
│   └── ssrf_guard.py               # Integrates safe_target_validator
├── reconciliation/
│   ├── __init__.py
│   ├── observation_writer.py       # Append-only raw evidence recording
│   ├── observation_validator.py    # Two-stage independent validation engine
│   └── resolution_manager.py       # Updates child_current_authoritative_resolution
└── coordinator/
    ├── __init__.py
    ├── parent_saga_coordinator.py  # Parent state machine, coverage verification
    └── coverage_validator.py       # Continuous room-night and occupancy math
```

---

## 3. State Machines & Transition Rules

### A. Child Outbox Operation Lifecycle
```
                 ┌─────────────┐
                 │   PENDING   │
                 └──────┬──────┘
                        │ (Worker claims lease)
                        ▼
                 ┌─────────────┐
                 │ IN_PROGRESS │◄── (Heartbeat & Attempt increment)
                 └──────┬──────┘
         ┌──────────────┼──────────────┬──────────────┐
         ▼              ▼              ▼              ▼
   ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────────────────────┐
   │ SUCCEEDED │  │  FAILED   │  │ EXHAUSTED │  │ RECONCILIATION_REQUIRED │
   └───────────┘  └───────────┘  └───────────┘  └─────────────────────────┘
```

1. **PENDING → IN_PROGRESS:** Worker claims row using `UPDATE ... WHERE status = 'PENDING' OR (status = 'IN_PROGRESS' AND lease_expires_at < current_timestamp)`. Fencing token incremented. Database trigger verifies provider account is still active and authorized.
2. **IN_PROGRESS → SUCCEEDED:** Definitive success observation written and validated.
3. **IN_PROGRESS → FAILED:** Definitive failure observation written (e.g. invalid rate, room sold out).
4. **IN_PROGRESS → RECONCILIATION_REQUIRED:** Network timeout, 5xx server error, or ambiguous response on non-idempotent supplier. Outbox does NOT retry blindly.
5. **IN_PROGRESS → EXHAUSTED:** Maximum retries reached on retryable transient error.

### B. Parent Booking Saga State Machine
- **INITIATED:** Parent booking created, hold placed on agency credit line.
- **PROCESSING:** Child supplier intents dispatched to outbox.
- **CONFIRMED:** All child supplier intents reach `RESERVATION_ACTIVE` and coverage validator proves continuous dates and occupancy. Parent voucher issued; wallet hold captured.
- **CANCELLATION_PENDING:** One child fails definitively while others are active; coordinator triggers compensation outbox operations.
- **CANCELLED:** All active children successfully cancelled; hold released in full.
- **RECONCILIATION_REQUIRED:** Any child in `SUPPLIER_UNKNOWN` or ambiguous state. Hold remains locked until manual or automated lookup reconciles state.

---

## 4. Implementation Sequence

### Phase 1: Core Outbox & Pinned Transport (Days 1–3)
1. Port verified M2.3A DDL into production migration pipeline `target_platform/migrations/`.
2. Implement `pinned_transport.py` and `ssrf_guard.py` utilizing `safe_target_validator.py`.
3. Implement `outbox_manager.py` with atomic claim query and lease renewal.

### Phase 2: Capability-Aware Supplier Adapters (Days 4–6)
1. Implement `base_adapter.py` defining required methods:
   - `create_reservation(payload, idempotency_key)`
   - `lookup_reservation(reference)`
   - `cancel_reservation(reference)`
2. Implement Expedia (`IDEMPOTENT_WITH_LOOKUP`), Hotelbeds (`LOOKUP_ONLY`), and Direct (`NEITHER`).
3. Wire attempt tracker with exponential backoff and jitter.

### Phase 3: Observation & Two-Stage Validation Pipeline (Days 7–9)
1. Implement `observation_writer.py` for raw responses.
2. Implement `observation_validator.py` with payload hash, timestamp, and field matching checks.
3. Implement `resolution_manager.py` writing to `child_authoritative_resolution_history` and updating pointer `child_current_authoritative_resolution`.

### Phase 4: Parent Saga Coordinator & Financial Finalization (Days 10–12)
1. Implement `coverage_validator.py` verifying zero-gap date intervals and guest occupancy.
2. Implement `parent_saga_coordinator.py` driving all-or-nothing parent compensation.
3. Implement wallet capture and voucher issuance integration with preserved M2.2 signed ledger.

### Phase 5: Adversarial Concurrency & Chaos Testing (Days 13–15)
1. Simulated worker crash tests (worker killed while holding lease).
2. Late-arriving worker race tests (stale worker completes after timeout).
3. Split-brain multi-worker lease contention tests.
4. Comprehensive end-to-end multi-room booking scenarios.

---

## 5. Failure Scenarios & Mitigations

| Failure Mode | Root Cause | M2.3B Mitigation |
| :--- | :--- | :--- |
| **Worker Process Crash** | Out-of-memory or SIGKILL during HTTP dispatch | Lease expires after 30s. Peer worker claims expired lease, checks attempt count, and initiates recovery. |
| **Ambiguous HTTP Timeout** | Supplier server hangs or network packet drops | Handled via capability: If `IDEMPOTENT_WITH_LOOKUP`, worker repeats with identical idempotency key; if `LOOKUP_ONLY`, worker performs lookup; if `NEITHER`, enters `RECONCILIATION_REQUIRED`. |
| **Split-Brain Workers** | Network partition causes two workers to process same task | Fencing token passed in observation. Database trigger aborts update if fencing token regresses. |
| **Account Revocation Mid-Flight** | Admin disables account while tasks are queued | Status update trigger aborts transition to `IN_PROGRESS`. Task safely marked `RECONCILIATION_REQUIRED`. |
| **DNS Rebinding Attack** | Malicious DNS returns public IP then private IP | Transport pins resolved IP for duration of TCP socket lifetime. |

---

## 6. Acceptance Criteria for M2.3B Sign-Off
1. Complete outbox test suite passing with 100% assertion rate.
2. Zero dropped or duplicated reservations under 50-worker concurrency tests.
3. Clean rollback to parent `CANCELLED` when any child fails.
4. No financial drift: wallet holds released or captured with 100% ledger balance reconciliation.
5. All 196 M2.2 baseline tests passing without regression.

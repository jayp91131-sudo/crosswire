# Crosswire Milestone 2.3C: End-to-End Integration Demonstration

## Executive Overview

Milestone 2.3C integrates the core subsystems developed across Crosswire milestones into one unified, executable product:
1. **M2.2 Travel Platform & Invariant Oracle**: Fast, deterministic HTTP/ASGI synthetic travel platform with mathematical transaction and financial invariant verifiers.
2. **M2.3A Relational Schema**: Durable SQLite transactional tables for outbox leases, attempts, append-only observations, authoritative resolutions, coverage specifications, and financial holds.
3. **M2.3B Outbox Engine & Coordinator**: Capability-aware outbox dispatcher (`IDEMPOTENT_WITH_LOOKUP`, `LOOKUP_ONLY`, `NEITHER`), observation validation, stay coverage evaluation (`CoverageOracle`), and parent booking reduction (`ParentCoordinator`).

---

## One-Command Reproduction

Execute the full end-to-end demonstration from the workspace root:

```bash
python run_m2_3c_demo.py
```

Or via module execution:

```bash
python -m m2_3c.demo
```

### Automated Pytest Suite

Run the M2.3C integration test suite:

```bash
python -m pytest m2_3c/tests/ -v
```

Run all system test suites:

```bash
# M2.3C Integration Tests (7 tests)
python -m pytest m2_3c/tests/ -v

# M2.3B Outbox & Multi-Supplier Tests (54 tests)
python -m pytest m2_3b/tests/ -q

# M2.2 Frozen Baseline Platform Tests (196 tests)
python -m pytest tests -q
```

---

## Demonstration Scenarios

### 1. Primary Scenario: Ambiguous Supplier Commit-Then-Timeout (`SCN-TIMEOUT-001`)

* **Vulnerable Workflow (`Aggregator=VULNERABLE`, `Supplier=VULNERABLE`)**:
  1. Agency submits a hotel booking intent (`INTENT-DEMO-VULN-001`).
  2. Supplier persists the initial reservation (`SUP-RES-INTENT-DEMO-VULN-001-01`).
  3. Supplier delays response (0.30s > 0.15s client timeout).
  4. Vulnerable aggregator experiences `ReadTimeout` and blindly re-issues `POST /reservations` without an idempotency key.
  5. Supplier persists a second reservation (`SUP-RES-INTENT-DEMO-VULN-001-02`).
  6. **Crosswire Invariant Oracle** detects the violation:
     - Violated: `INV-5.2: Booking Intent Uniqueness`
     - CWE: `CWE-675 / CWE-841`
     - Finding ID: `CW-SEC-M2-001` (Severity: `HIGH`)
     - Ground-Truth Evidence: 2 distinct supplier reservations committed for 1 intent.

* **Fixed Workflow (`Aggregator=FIXED`, `Supplier=IDEMPOTENT`)**:
  1. Agency submits a hotel booking intent (`INTENT-DEMO-FIXED-001`).
  2. Supplier persists the initial reservation (`SUP-RES-INTENT-DEMO-FIXED-001-01`) and response times out.
  3. Fixed aggregator catches the timeout and executes capability-aware reconciliation / idempotent query using the deterministic key.
  4. Supplier recognizes the idempotency key and returns the existing reservation without creating a duplicate.
  5. Invariant Oracle proves `INV-5.2` passes: exactly 1 reservation exists in the supplier database.
  6. Retest result attached to `CW-SEC-M2-001` with automated proof of fix.

---

### 2. Secondary Scenario: Duplicate Refund Webhook Replay (`SCN-WEBHOOK-001`)

* **Vulnerable Workflow (`Aggregator=VULNERABLE`)**:
  1. Agency establishes a cancelled booking with 12,000 INR eligible refund entitlement.
  2. Payment provider sends refund webhook `evt_demo_wh_vuln_replay` (5,000 INR).
  3. Network re-delivery simulates 3 identical deliveries.
  4. Vulnerable aggregator naively processes each delivery, creating 3 duplicate ledger entries totaling 15,000 INR.
  5. Disbursed amount exceeds the 12,000 INR entitlement by 3,000 INR (direct financial leak).
  6. **Crosswire Invariant Oracle** detects the violations:
     - Violated: `INV-5.3: Webhook Idempotency` & `INV-5.5: Refund Boundedness`
     - CWE: `CWE-675 / CWE-799`
     - Finding ID: `CW-SEC-M2-002` (Severity: `CRITICAL`)

* **Fixed Workflow (`Aggregator=FIXED`)**:
  1. Same booking, same 3 webhook deliveries in fixed mode.
  2. Aggregator enforces atomic event deduplication.
  3. Delivery 1 commits refund; deliveries 2 and 3 return HTTP 200 with idempotent acknowledgement.
  4. Exactly 1 refund (5,000 INR) committed in SQLite and financial ledger.
  5. Invariants 5.3 & 5.5 pass; Retest attached to `CW-SEC-M2-002`.

---

### 3. Outbox Multi-Supplier Coordinated Execution

Demonstrates the M2.3A relational schema and M2.3B outbox engine orchestrating a 2-room split booking across distinct suppliers:
1. Room 1 dispatched to Expedia (`IDEMPOTENT_WITH_LOOKUP`) under `TIMEOUT_AFTER_COMMIT`.
2. Room 2 dispatched to Hotelbeds (`LOOKUP_ONLY`) under `TIMEOUT_AFTER_COMMIT`.
3. Dispatcher handles both according to provider capabilities:
   - Expedia: idempotent query recovery -> `RESERVATION_ACTIVE`.
   - Hotelbeds: bounded lookup query -> `RESERVATION_ACTIVE`.
4. Raw supplier observations recorded in `supplier_reservation_observations` with cryptographic hashes.
5. Observations independently verified by `ObservationValidator`.
6. Authoritative resolutions established via `ResolutionManager` with monotonic fencing tokens.
7. Multi-room stay coverage verified via `CoverageOracle`.
8. `ParentCoordinator` advances parent booking from `PROCESSING` to `CONFIRMED` and captures wallet hold.
9. Vulnerable contrast proves naive blind retry generates duplicate reservations in the supplier backend.

---

## Deliverable File Inventory

| Path | Purpose |
|---|---|
| `run_m2_3c_demo.py` | One-command CLI entrypoint |
| `m2_3c/__init__.py` | Package root and version |
| `m2_3c/findings.py` | Structured `SecurityFinding` and `RetestResult` models and ASCII card formatter |
| `m2_3c/primary_scenario.py` | Primary ambiguous commit-then-timeout runner (vulnerable, fixed, retest) |
| `m2_3c/secondary_scenario.py` | Secondary duplicate refund webhook runner (vulnerable, fixed, retest) |
| `m2_3c/outbox_integration.py` | M2.3A schema & M2.3B outbox engine multi-supplier execution |
| `m2_3c/demo.py` | Unified interactive demonstration engine and report exporter |
| `m2_3c/tests/test_m2_3c_demo.py` | Automated test suite (7 pytest tests) |
| `M2_3C_FINDINGS.json` | Exported structured security findings with retest proofs |
| `M2_3C_DEMO_EXECUTION_REPORT.json` | Complete metadata and execution traces of all demo runs |

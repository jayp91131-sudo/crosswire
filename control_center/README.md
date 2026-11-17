# Crosswire Control Center (Milestone 2.4)

A local, web-based cybersecurity testing operations platform and scenario runner built directly on top of the Crosswire transactional engine.

Designed for hackathon judges, technical reviewers, and security engineers to operate and verify transaction invariants, fault injection, and automated remediation in a real browser.

---

## 1. Quick Start & Launch Instructions

From the Crosswire workspace root, launch the Control Center:

```bash
python run_control_center.py
```

Optional flags:
```bash
python run_control_center.py --port 8080 --host 127.0.0.1
```

Open your browser and navigate to:
**[http://127.0.0.1:8000](http://127.0.0.1:8000)**

*(API documentation is automatically available at `http://127.0.0.1:8000/docs`)*

---

## 2. Core Operational Capabilities

### 1. Security Operations Dashboard
* **Real Execution Metrics**: Live KPI cards for Total Executions, Confirmed Vulnerabilities, Critical/High Severity findings, and Successful Retests.
* **Grounded in Ground Truth**: Invariant verifications evaluate ground-truth state directly against target platform SQLite transactions, while session execution records are held in-memory in `StateStore`—zero fabricated statistics.
* **Invariant Status Monitor**: Real-time status for `INV-5.2` (Uniqueness), `INV-5.3` (Webhook Idempotency), `INV-5.5` (Refund Boundedness), and `INV-M2.3B` (Stay Coverage & Fencing).
* **Recent Activity Feed**: Chronological execution logs with duration in milliseconds and invariant results.

### 2. Adversarial Scenario Runner
* Select from 3 approved local security scenarios:
  1. `SCN-TIMEOUT-001`: Ambiguous Supplier Commit-Then-Timeout.
  2. `SCN-WEBHOOK-001`: Duplicate Refund Webhook Replay.
  3. `SCN-OUTBOX-001`: Multi-Supplier Coordinated Outbox.
* **Execution Mode Switcher**:
  - `VULNERABLE MODE`: Injects simulated network drops, timeout faults, and tests whether the aggregator blindly commits duplicate reservations or financial leaks.
  - `FIXED MODE`: Enforces idempotency keys, atomic deduplication, and capability-aware outbox leases.
* **Live Execution Console**: Real-time step-by-step progress display.

### 3. Execution Timeline Audit
* Step-by-step chronological trace of every transaction event:
  1. Booking Intent Created (`INTENT_CREATED`)
  2. Supplier Request Dispatched (`REQUEST_DISPATCHED`)
  3. Supplier Committed Initial Reservation (`SUPPLIER_COMMITTED`)
  4. Timeout / Replay Fault Triggered (`FAULT_TRIGGERED`)
  5. Retry / Reconciliation Invoked (`RETRY_RECONCILE`)
  6. Invariant Oracle Evaluated (`INVARIANT_CHECK`)
  7. Finding Generated (`FINDING_GENERATED`) or Retest Completed (`RETEST_COMPLETED`).
* Color-coded glowing indicators (`INFO`, `WARNING`, `CRITICAL`, `SUCCESS`) with exact timestamps and metadata payloads.

### 4. Findings & Evidence Viewer
* Comprehensive security findings cards:
  - Finding ID (`CW-SEC-M2-001`, `CW-SEC-M2-002`)
  - Severity (`HIGH`, `CRITICAL`)
  - CWE Reference (`CWE-675`, `CWE-841`, `CWE-799`)
  - Violated Invariant
  - Expected vs Actual behavior comparison
  - Technical root cause breakdown
  - Remediation guidance
  - Expandable ground-truth evidence (SQLite reservation records, duplicate ledger entries, monetary minor units).
* **Interactive Retest Button**: Click **"RUN RETEST (FIXED MODE)"** directly on any finding to execute the fixed workflow and prove remediation in real time!

### 5. Retest Verification
* Directly contrasts the vulnerable execution against the fixed execution.
* Shows the original failure, the remediation mechanism, and the passing invariant proof.
* Attaches immutable `RetestResult` audit records to the finding.

### 6. Reports & Export
* **Executive HTML Security Assessment Report**: Self-contained, styled HTML report ready for printing to PDF or reviewing in a browser.
* **JSON Findings Export**: One-click download of all security findings (`crosswire_findings.json`).
* **JSON Executions Export**: Complete metadata trace of all scenario runs (`crosswire_executions.json`).

---

## 3. Automated Test Suite

Verify all Control Center endpoints, scenario execution bridges, timeline generation, and report rendering:

```bash
python -m pytest control_center/tests/ -v
```

All 9 tests pass deterministically in ~2 seconds.

---

## 4. Technical Constraints Adherence

* **Lightweight & Local**: Built on standard FastAPI, Uvicorn, and HTML5/vanilla JavaScript. No heavy Node/React build steps required.
* **CLI Preservation**: Root CLI `python run_m2_3c_demo.py` remains 100% functional.
* **Strict Safety Boundaries**: Only approved local synthetic scenarios can be executed. Zero external network calls or arbitrary target URLs.
* **Baseline Preservation**: 100% non-invasive—M2.2, M2.3B, and M2.3C modules remain completely intact.

# Crosswire 2.0: Business-Logic & Transaction-Integrity DAST for B2B Travel Platforms

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Release: v2.0.0](https://img.shields.io/badge/Release-v2.0.0-emerald.svg)](https://github.com/jayp91131-sudo/crosswire/releases/tag/v2.0.0)
[![Python: 3.11](https://img.shields.io/badge/Python-3.11-cyan.svg)](https://www.python.org/)
[![Tests: 332 Passing](https://img.shields.io/badge/Tests-332%20Passing-brightgreen.svg)](CROSSWIRE_FINAL_TEST_RESULTS.md)
[![Status: Synthetic Testbed RC](https://img.shields.io/badge/Status-Synthetic%20Testbed%20RC-orange.svg)](CROSSWIRE_FINAL_ACCEPTANCE_REPORT.md)

---

## 1. Project Overview

**Crosswire 2.0** is an open-source, specialized Dynamic Application Security Testing (DAST) engine engineered specifically for B2B travel distribution architectures, multi-party saga workflows, and stateful transaction platforms.

Unlike traditional web application vulnerability scanners that focus strictly on HTTP syntax, injection strings (SQLi, XSS), and perimeter authentication headers, Crosswire tests the **deep semantic integrity of multi-party financial and booking transactions**.

---

## 2. The Problem

In B2B travel distribution platforms (aggregators, bedbanks, global distribution systems, online travel agencies), **an API call can return `HTTP 200 OK` and still represent a catastrophic financial or business-logic failure**:

- **Ambiguous Supplier Commit-Then-Timeout:** An aggregator initiates a hotel reservation; the supplier confirms it internally, but the response drops or times out. A blind client retry creates duplicate paid supplier bookings for a single guest intent (CWE-675).
- **Duplicate Webhook Delivery & Ledger Inflation:** Asynchronous payment and refund notifications delivered multiple times under at-least-once network guarantees credit agency wallets repeatedly, exceeding booking entitlements (CWE-799 / CWE-675).
- **Concurrency & Race Conditions:** Parallel client checkouts or refund requests exploit check-then-act gaps, driving agency credit balances negative or bypassing hold limits (CWE-362).
- **Entitlement & State-Machine Violations:** Cancellations racing against delayed supplier confirmations result in active vouchers for cancelled reservations, or refunds processed multiple times against the same ledger entry (CWE-841 / CWE-670).

Traditional DAST and API scanners operate statelessly at Layer 7 and are fundamentally blind to relational database state, double-entry financial ledgers, and distributed sagas.

---

## 3. The Solution

Crosswire bridges this gap by acting as an adversarial transaction harness coupled to an authoritative **Ground-Truth Invariant Engine**:

1. **Controlled Adversarial Injections:** Crosswire dispatches structured transaction workflows across synthetic target services, injecting network timeouts, latency spikes, duplicate webhook deliveries, concurrency races, and parameter tampering.
2. **Ground-Truth Relational Observation:** Rather than relying merely on HTTP response status codes, Crosswire queries backend relational tables (reservations, ledger transactions, agency wallet balances, outbox messages) via dedicated read-only observation adapters.
3. **Formal Invariant Verification:** Crosswire evaluates formal transaction invariants against ground truth:
   - `INV-5.1`: Wallet Balance Conservation & Active Hold Bounds.
   - `INV-5.2`: Booking Intent Uniqueness (`COUNT(supplier_reservations) <= 1`).
   - `INV-5.3`: Webhook Processing Idempotency.
   - `INV-5.4`: Voucher Entitlement Preconditions.
   - `INV-5.5`: Strict Refund Boundedness (`SUM(refunds) <= entitlement`).
   - `INV-5.6`: State Transition Validity & Tenant Isolation.
   - `INV-5.7`: Financial Ledger Double-Entry Consistency.
4. **Vulnerable vs. Fixed Duality:** Every scenario executes in both **VULNERABLE** and **FIXED** modes, providing concrete proof that a developer patch eliminates the defect without introducing regressions.

---

## 4. Core Capabilities

- **Travel-Specific Security Scenarios:** 10 standardized scenarios addressing real-world failure modes in distributed travel architectures.
- **Transaction Integrity Verification:** Mathematical invariant assertions against backend database ground truth.
- **Fail-Closed Forensic Reporting:** Complete fail-closed architecture—missing observations, missing checks, or placeholder data evaluate strictly to `INCONCLUSIVE`, never a false `PASS`.
- **Runtime Event Instrumentation:** Monotonic event sequence tracking and thread-local audit traces.
- **Ground-Truth Observation Adapters:** Clean read-only schema abstractions decoupling Crosswire from proprietary target databases.
- **Target Adapter Framework:** Target-independent execution and observation interfaces allowing portable testing across heterogeneous target platforms.
- **Webhook & Refund Testing:** Multi-delivery adversarial webhook replays, currency mismatch tests, and cumulative refund ceiling enforcement.
- **Wallet & Concurrency Testing:** Parallel barrier synchronization testing TOCTOU races and ledger balances.
- **Evidence Integrity & Tamper Proofing:** Full-dossier SHA-256 evidence digests linking runtime event traces, observations, invariant results, findings, and retest proofs.
- **Change-Aware Security Testing:** Local git diff analysis to map modified source files to affected scenarios for fast, targeted test execution.
- **Forensic Operations Console:** Web-based executive dashboard featuring an interactive timeline, finding cards, retest proofs, and HTML/JSON report exports.
- **Operational Safety Controls:** Strict local loopback perimeter enforcement, target allowlisting, synthetic identity quarantine, rate-limiting, and an emergency kill switch.

---

## 5. System Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CROSSWIRE CORE ENGINE                             │
│                                                                             │
│  ┌──────────────────────┐   ┌──────────────────────┐   ┌─────────────────┐  │
│  │ Portable Scenarios   │──>│ Invariant Engine     │──>│ Forensic Report │  │
│  │ (10 Travel Workflows)│   │ (INV-5.1 to INV-5.7) │   │ (SHA-256 Digest)│  │
│  └──────────────────────┘   └──────────────────────┘   └─────────────────┘  │
│             │                          │                        │           │
│             │                          ▼                        │           │
│             │             ┌─────────────────────────┐           │           │
│             │             │ Runtime Event Collector │           │           │
│             │             │ (Monotonic Audit Trace) │           │           │
│             │             └─────────────────────────┘           │           │
└─────────────┼──────────────────────────┼────────────────────────┼───────────┘
              │                          │                        │
              ▼                          ▼                        ▼
┌──────────────────────────┐  ┌───────────────────────────────────────────────┐
│ Target Execution Adapter │  │       Ground-Truth Observation Adapter        │
│ • Booking Intents        │  │ • Supplier Reservations (SQLite/Relational)   │
│ • Confirmations & Holds  │  │ • Wallet Ledger & Balance Snapshots           │
│ • Webhook Injections     │  │ • Outbox Message Leasing & Fencing Tokens     │
└─────────────┬────────────┘  └───────────────────────┬───────────────────────┘
              │                                       │
              ▼                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     TARGET PLATFORM (SYNTHETIC SANDBOX)                     │
│                                                                             │
│   Target A: Reference Platform         TravelSandboxB: Clean-Room Target   │
│   (Aggregator, Supplier, Payment)      (Independent In-Memory SQLite)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Subsystem Description:
1. **Crosswire Core:** The orchestration layer coordinating scenario lifecycle, parameter generation, and execution assertions.
2. **Portable Scenarios:** Standardized implementations of the 10 core transaction attack models.
3. **Invariant Verification Engine:** Domain rules evaluator translating raw database observations into formal mathematical invariant evaluations.
4. **Target Execution Adapters:** Abstract interface (`TargetExecutionAdapter`) translating abstract test actions into concrete HTTP/ASGI calls against target endpoints.
5. **Ground-Truth Observation Adapters:** Abstract interface (`GroundTruthObservationAdapter`) querying target backend databases in read-only mode to capture actual state.
6. **Runtime Event Collector:** Monotonic sequencing collector creating an audit trace of all events during scenario execution.
7. **Forensic Reporting Engine:** Fail-closed dossier compiler generating cryptographic evidence bundles with automated `verify_integrity()` validation.
8. **Control Center:** High-density, dark-mode web console (`control_center/`) providing real-time visibility, execution controls, and exportable reports.

---

## 6. Verified Scope & Reality Disclosure

To maintain total engineering transparency and technical integrity:

- **10 Core Scenarios:**
  - `SCN-TIMEOUT-001`: Supplier Commit-Then-Timeout (Target A & TravelSandboxB).
  - `SCN-WEBHOOK-001`: Duplicate Refund Webhook Replay (Target A & TravelSandboxB).
  - `SCN-WALLET-001`: Agency Wallet Concurrency & Double-Spend (Target A).
  - `SCN-RACE-001`: Cancellation vs Late Supplier Confirmation (Target A).
  - `SCN-BOLA-001`: Cross-Agency Booking Data Exfiltration (Target A).
  - `SCN-PRIV-001`: Sub-Agent Administrative Privilege Escalation (Target A).
  - `SCN-TAMPER-001`: Client Fare & Currency Manipulation (Target A).
  - `SCN-SKIP-001`: Direct Voucher Issuance Without Payment (Target A).
  - `SCN-ABUSE-001`: Cancel-Rebook Excess Cumulative Refund Abuse (Target A).
  - `SCN-OUTBOX-001`: Multi-Supplier Outbox Execution & Fencing (Target A).
- **Target Platform Portability:**
  - `Target A`: Full reference B2B travel platform with Aggregator, Mock Supplier, and Payment Gateway.
  - `TravelSandboxB`: Independent, clean-room in-memory SQLite sandbox (`:memory:`) proving adapter independence. Exercises `SCN-TIMEOUT-001` and `SCN-WEBHOOK-001`. Target B intentionally does not support `MULTI_SUPPLIER_OUTBOX` and fails-closed.
- **Test Suite Status:** **332 passed, 0 failed, 0 skipped** (100% passing in Windows / Python 3.11.15).
- **Release Status:** Certified as a **Synthetic-Testbed Release Candidate (v2.0.0)**. It is designed and validated against local synthetic testbeds and has not been certified for live production environments.
- **Benchmark Honesty:** In accordance with rigorous testing ethics, all external tools (`OWASP ZAP`, `Nuclei`, `Postman/Newman`) remain categorized as `NOT_RUN` with metrics `None`, and `Toxiproxy` as `NOT_COMPARABLE` (Layer-4 fault proxy), until real-world side-by-side benchmark runs are conducted.

---

## 7. Installation & Quickstart

### Prerequisites
- Python 3.11+
- Git

### 1. Clone the Repository
```bash
git clone https://github.com/jayp91131-sudo/crosswire.git
cd crosswire
```

### 2. Create and Activate Virtual Environment
```bash
# On Windows:
python -m venv .venv
.venv\Scripts\activate

# On Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Execute Native Test Suite
Run the complete 332-test automated verification suite:
```bash
pytest
```
Expected output:
```text
============================ 332 passed in ~60s =============================
```

### 5. Launch the Forensic Operations Console
Start the local web application:
```bash
python run_control_center.py
```
Or with Uvicorn directly:
```bash
python -m uvicorn control_center.app:app --host 127.0.0.1 --port 8000
```

Open your browser at:
```
http://127.0.0.1:8000
```

### 6. Safe Synthetic Execution
Crosswire includes built-in safety guardrails:
- Execution is strictly restricted to local loopback interfaces (`127.0.0.1`, `localhost`).
- Non-synthetic target hosts raise immediate `ScopeViolationError`.
- Live credentials or production tokens raise `LiveCredentialDetectedError`.
- An emergency stop kill switch (`POST /api/safety/emergency-stop`) halts all active scenarios immediately.

---

## 8. Hackathon & Engineering Context

Crosswire was engineered as an advanced technical prototype demonstrating the necessity of **stateful, transaction-aware security testing** in distributed financial and travel systems.

It proves that:
1. Deep business-logic vulnerabilities cannot be discovered through HTTP fuzzing alone.
2. Invariant verification against ground-truth database observations provides deterministic, zero-false-positive security findings.
3. Automated retest verification proves mathematically that a developer patch resolves the defect without regressions.

---

## 9. Canonical Specification & Documentation

Detailed engineering documentation is included in the repository:
- **[MASTER_PLAN_V2.md](./MASTER_PLAN_V2.md):** Canonical specification and requirements crosswalk.
- **[CROSSWIRE_FINAL_ACCEPTANCE_REPORT.md](./CROSSWIRE_FINAL_ACCEPTANCE_REPORT.md):** Verified acceptance and sign-off report.
- **[CROSSWIRE_FINAL_TEST_RESULTS.md](./CROSSWIRE_FINAL_TEST_RESULTS.md):** Full 332-test execution breakdown.
- **[CROSSWIRE_FINAL_ARCHIVE_MANIFEST.md](./CROSSWIRE_FINAL_ARCHIVE_MANIFEST.md):** Package manifest and verification instructions.
- **[CROSSWIRE_FINAL_OPERATOR_GUIDE.md](./CROSSWIRE_FINAL_OPERATOR_GUIDE.md):** Complete operator manual.
- **[CROSSWIRE_2_0_FINAL_RELEASE_NOTES.md](./CROSSWIRE_2_0_FINAL_RELEASE_NOTES.md):** Release candidate highlights and architecture summary.

---

## 10. License

This project is licensed under the MIT License — see the [LICENSE](./LICENSE) file for details.

Copyright (c) 2026 jayp91131-sudo

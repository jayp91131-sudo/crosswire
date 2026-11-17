# CROSSWIRE — COMPLETE OPERATOR GUIDE

> **Audience**: Hackathon judges, technical evaluators, software engineers, and security auditors.
> **Scope**: Step-by-step instructions to install, launch, operate, test, and verify the Crosswire Control Center from scratch.

---

## 1. PRODUCT OVERVIEW

**Crosswire** is a specialized Dynamic Application Security Testing (DAST) platform for B2B travel distribution systems.

It detects whether distributed travel bookings, supplier reservations, agency wallets, and refund webhooks preserve business and financial correctness when subjected to adversarial network timeouts, blind retries, and duplicate event delivery.

Unlike traditional DAST tools that only inspect HTTP status codes and headers, Crosswire asserts formal mathematical invariants against **actual relational database and financial ledger ground truth**.

*Note on Testing Environment*: The current implementation operates on a synthetic local testbed using SQLite. Its simulated transaction failures do not establish production-scale distributed-system guarantees or integrations with real travel suppliers.

---

## 2. PREREQUISITES & SYSTEM REQUIREMENTS

- **Operating System**: Windows, macOS, or Linux.
- **Python**: Version 3.10, 3.11, or 3.12.
- **Network**: **Zero internet connection required** at runtime. All assets, scripts, and synthetic components are entirely self-contained locally.
- **Ports**: TCP Port `8000` (default) or configurable via CLI argument.

---

## 3. INSTALLATION & SETUP

### Step 1: Extract Distribution Archive
Extract `CROSSWIRE_M2_4_HACKATHON_MVP.zip` into your chosen workspace directory:
```bash
unzip CROSSWIRE_M2_4_HACKATHON_MVP.zip -d crosswire
cd crosswire
```

### Step 2: Install Runtime Dependencies
Install the required packages using pip:
```bash
pip install -r requirements.txt
```
*(Dependencies installed: `fastapi`, `uvicorn`, `pydantic`, `httpx`, `jinja2`, `pytest`, `pytest-asyncio`)*

---

## 4. LAUNCHING THE CONTROL CENTER

Start the web application launcher:
```bash
python run_control_center.py
```

### Expected Terminal Output:
```text
==============================================================================
  CROSSWIRE CONTROL CENTER M2.4
  Web-Based Cybersecurity Testing Platform & Scenario Runner
==============================================================================
  Access URL          : http://127.0.0.1:8000
  API Docs            : http://127.0.0.1:8000/docs
  Security Report     : http://127.0.0.1:8000/api/reports/html
  Target Scope        : Single-Hotel / Single-Currency Local Synthetic Platform
==============================================================================

INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

To run on a custom port or host:
```bash
python run_control_center.py --port 8080 --host 0.0.0.0
```

---

## 5. OPERATING THE CONTROL CENTER

Open your browser and navigate to:
```text
http://127.0.0.1:8000
```

### 1. Main Dashboard
- **Executive KPIs**: View live counts for Total Executions, Confirmed Vulnerabilities, Critical/High Severity findings, and Successful Retests.
- **Invariant Status Monitor**: Inspect real-time status of monitored invariants:
  - `INV-5.2`: Booking Intent Uniqueness
  - `INV-5.3`: Webhook Idempotency
  - `INV-5.4`: Voucher Entitlement
  - `INV-5.5`: Refund Boundedness
  - `INV-5.7`: Financial Ledger Double-Entry Consistency (deferred)
  - `INV-M2.3B`: Parent Stay Coverage & Monotonic Fencing

---

### 2. Executing a Vulnerable Security Scenario
1. In the sidebar, click **Scenario Runner**.
2. Select card **`SCN-TIMEOUT-001: Ambiguous Supplier Commit-Then-Timeout`**.
3. Under Execution Mode, click **`VULNERABLE MODE`** (red active state).
4. Click **`[⚡ Run Vulnerable Scenario]`**.

**What to Observe**:
- Progress bar streams execution progress (~560ms).
- Execution Timeline displays:
  1. Booking intent generated for 15,000 INR.
  2. Supplier persists reservation `SUP-RES-*-01` to SQLite.
  3. Simulated 0.30s delay triggers client `ReadTimeout`.
  4. Aggregator naively re-dispatches request without idempotency key.
  5. Supplier commits second reservation `SUP-RES-*-02`.
  6. Invariant Oracle evaluates `INV-5.2`: **FAILED** (2 reservations found).
  7. Finding `CW-SEC-M2-001` emitted (Severity: HIGH, CWE-675).

---

### 3. Inspecting Finding Evidence
1. Click the **Findings** tab in the sidebar.
2. Click card `CW-SEC-M2-001: Duplicate Supplier Reservation Generated Under Ambiguous Timeout`.
3. Inspect details:
   - **CWE**: `CWE-675: Multiple Operations on Resource / CWE-841`
   - **Root Cause & Remediation Diff**: Side-by-side code diff showing how to add an idempotency contract and reconciliation query.
   - **Ground-Truth Evidence**: Click **`[Expand Raw Evidence]`** to inspect exact SQLite reservation row IDs: `SUP-RES-*-01` and `SUP-RES-*-02`.

---

### 4. Executing the Fixed Remediation (1-Click Retest)
1. Return to **Scenario Runner**.
2. Select `SCN-TIMEOUT-001`.
3. Under Execution Mode, click **`FIXED MODE`** (green active state).
4. Click **`[✔ Run Fixed Scenario]`**.

**What to Observe**:
- Execution status changes to **`INVARIANT_PASSED`**.
- Timeline shows: Idempotency contract attached $\to$ Reconciliation probe dispatched $\to$ Existing reservation confirmed $\to$ Strictly 1 reservation exists in SQLite.
- Navigate to **Findings** tab: Finding `CW-SEC-M2-001` now displays an emerald **`[✔ RETEST VERIFIED]`** badge with Retest ID and invariant proof summary.

---

### 5. Running Secondary Scenario: Webhook Replay
1. In Scenario Runner, select **`SCN-WEBHOOK-001: Duplicate Refund Webhook Replay`**.
2. Click **`Run Vulnerable`**:
   - Webhook delivered 3 times.
   - Wallet credited 15,000 INR against a 12,000 INR entitlement.
   - Invariants `INV-5.3` and `INV-5.5` fail; direct 3,000 INR cash leak detected; Critical finding `CW-SEC-M2-002` emitted.
3. Click **`Run Fixed`**:
   - Atomic SQLite deduplication table intercepts deliveries 2 and 3.
   - Total refunded strictly 5,000 INR; 0 INR excess leak; Invariants 5.3 & 5.5 pass; retest verified.

---

### 6. Exporting Security Assessment Reports
Click **`[📄 View Security Assessment Report]`** in the header navigation or visit:
```text
http://127.0.0.1:8000/api/reports/html
```
- Opens a standalone, beautifully styled executive HTML security report.
- Contains executive summary cards, invariant verification table, finding details, and retest proofs.
- JSON exports are also available at:
  - `GET /api/reports/json/findings`
  - `GET /api/reports/json/executions`

---

## 6. VERIFYING AUTOMATED TEST SUITES

To reproduce the verified test claims, run pytest from the project root:

### Run All 266 Tests:
```bash
python -m pytest control_center/tests/ m2_3c/tests/ m2_3b/tests/ tests/ -q
```
**Expected Output**:
```text
266 passed in ~59s
```

### Run Specific Test Modules:
- **Control Center Tests (9 tests)**:
  ```bash
  python -m pytest control_center/tests/ -v
  ```
- **End-to-End Integration Tests (7 tests)**:
  ```bash
  python -m pytest m2_3c/tests/ -v
  ```
- **Transactional Outbox Engine Tests (54 tests)**:
  ```bash
  python -m pytest m2_3b/tests/ -v
  ```
- **Baseline Engine & Platform Tests (196 tests)**:
  ```bash
  python -m pytest tests/ -q
  ```

---

## 7. STOPPING THE APPLICATION

To stop the web server gracefully:
- In the terminal running `python run_control_center.py`, press **`CTRL + C`**.
- Uvicorn will cleanly terminate background workers and release port `8000`.

---

## 8. TROUBLESHOOTING

| Problem | Cause | Solution |
|---|---|---|
| `Address already in use` | Another process is using port 8000 | Specify a different port: `python run_control_center.py --port 8080`. |
| `Template not found` | Command run from wrong working directory | Ensure your terminal is in the project root containing `control_center/`. |
| Browser shows stale counts | Browser cached previous session | Hard refresh using `Ctrl + F5` or `Cmd + Shift + R`. |
| `ModuleNotFoundError: No module named 'fastapi'` | Dependencies not installed in active environment | Run `pip install -r requirements.txt`. |

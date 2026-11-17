# CROSSWIRE — LIVE DEMONSTRATION OPERATOR RUNBOOK

> **Document Purpose**: Complete, fail-safe operational procedure for presenting Crosswire live to hackathon judges, technical evaluators, or investors.
> **Environment**: 100% Offline-Capable Localhost (`http://127.0.0.1:8000`).

---

## 1. PRE-FLIGHT VERIFICATION CHECKLIST (T - 15 MINUTES)

Complete these checks prior to stepping up to the presentation desk:

| # | Step | Command / Action | Expected Result | Pass? |
|---|---|---|---|:---:|
| 1 | **Python Environment** | `python --version` | Python 3.10+ displayed | [ ] |
| 2 | **Dependencies** | `pip show fastapi uvicorn httpx pydantic` | All packages installed | [ ] |
| 3 | **Port 8000 Availability** | `netstat -ano \| findstr :8000` | No existing process listening | [ ] |
| 4 | **Start Control Center** | `python run_control_center.py` | Terminal logs: `Uvicorn running on http://127.0.0.1:8000` | [ ] |
| 5 | **Browser Sanity Check** | Open `http://127.0.0.1:8000` | Control Center dark UI loads with 0 errors | [ ] |
| 6 | **Offline Readiness** | Disconnect WiFi / Ethernet briefly | UI loads completely from localhost assets | [ ] |
| 7 | **Browser Zoom** | Set browser zoom to **100%** or **110%** | All KPI cards and tables clearly visible | [ ] |

---

## 2. STEP-BY-STEP LIVE DEMONSTRATION RUNBOOK

### SECTION 1: SYSTEM OVERVIEW & KPI DASHBOARD (30 SECONDS)

1. **Screen Location**: Main Dashboard tab (`#dashboard-view`).
2. **Action**:
   - Point to the **4 top KPI cards**: Total Executions, Confirmed Vulnerabilities, Critical/High Severity, Successful Retests.
   - Point to the **Invariant Status Monitor table**: Show rows for `INV-5.2` (Booking Intent Uniqueness), `INV-5.3` (Webhook Idempotency), `INV-5.4` (Voucher Entitlement), `INV-5.5` (Refund Boundedness), and `INV-M2.3B`.
3. **Presenter Dialogue**:
   > *"We are looking at the Crosswire Control Center. Notice our Invariant Status Monitor: Crosswire tests transactions against formal mathematical invariants, not HTTP status codes. Invariants check booking intent uniqueness, webhook idempotency, and refund entitlement ceilings."*
4. **Judge Takeaway**: The tool is operational, data-driven, and focused on business invariants rather than generic uptime.

---

### SECTION 2: PRIMARY DEMONSTRATION — COMMIT-THEN-TIMEOUT (90 SECONDS)

#### Step 2.1: Run Vulnerable Mode
1. **Screen Location**: Click the **Scenario Runner** tab in the sidebar.
2. **Action**:
   - Click the card for **`SCN-TIMEOUT-001: Ambiguous Supplier Commit-Then-Timeout`**.
   - Verify the mode toggle is set to **`VULNERABLE MODE`** (rose-colored active state).
   - Click the red button: **`[⚡ Run Vulnerable Scenario]`**.
3. **Expected UI Response**:
   - Progress bar pulses and completes within ~560ms.
   - Execution Timeline populates with 8 chronological events.
   - Event `EVT-SUPPLIER-COMMITTED`: Shows initial reservation `SUP-RES-*-01` committed to SQLite.
   - Event `EVT-FAULT-TRIGGERED`: Shows simulated 0.30s delay exceeding 0.15s client timeout (`ReadTimeout`).
   - Event `EVT-RETRY-RECONCILE`: Shows blind retry dispatched.
   - Event `EVT-SUPPLIER-COMMITTED`: Shows duplicate reservation `SUP-RES-*-02` committed.
   - Event `EVT-INVARIANT-CHECK`: Shows Invariant `INV-5.2` **FAILED** (2 reservations found).
   - Event `EVT-FINDING-GENERATED`: Emits finding `CW-SEC-M2-001`.
4. **Presenter Dialogue**:
   > *"Watch the timeline. The supplier committed the first reservation. Then we injected a network delay that caused a client ReadTimeout. The vulnerable aggregator naively retried, causing the supplier to commit a SECOND reservation record for the same booking intent in SQLite. Crosswire immediately proved Invariant 5.2 was broken. In a live system, this un-reconciled duplicate can lead to double billing and locked inventory."*

#### Step 2.2: Inspect Structured Finding & Evidence
1. **Screen Location**: Click the **Findings** tab in the sidebar (or scroll down to Finding `CW-SEC-M2-001`).
2. **Action**:
   - Click the finding card to expand the details.
   - Point to the CWE tag: **`CWE-675: Multiple Operations on Resource`** / **`CWE-841`**.
   - Click **`[Expand Raw Evidence]`**: Point to the SQLite reservation IDs array showing both `SUP-RES-*-01` and `SUP-RES-*-02`.
3. **Presenter Dialogue**:
   > *"This isn't a vague warning. Crosswire provides structured findings with CWE classification, actionable remediation diffs, and exact database row evidence proving the duplicate reservation exists in SQLite."*

#### Step 2.3: Run Fixed Remediation & Verify Retest
1. **Screen Location**: Return to Scenario Runner (or use Quick Run button on card).
2. **Action**:
   - Select `SCN-TIMEOUT-001`.
   - Toggle to **`FIXED MODE`** (emerald-colored active state).
   - Click the green button: **`[✔ Run Fixed Scenario]`**.
3. **Expected UI Response**:
   - Timeline shows: Idempotency contract attached $\to$ Reconciliation probe dispatched $\to$ Existing reservation confirmed $\to$ Invariant `INV-5.2` **PASSED** (strictly 1 reservation).
   - Status displays **`INVARIANT_PASSED`**.
   - Navigate to **Findings** tab: Finding `CW-SEC-M2-001` now displays an emerald **`[✔ RETEST VERIFIED]`** badge with Retest ID and invariant proof summary.
4. **Presenter Dialogue**:
   > *"Now we run the fixed build. With an idempotency contract and reconciliation query, the supplier returns the existing reservation without creating a duplicate. Invariant 5.2 passes, and Crosswire automatically proves the remediation with a verified retest."*

---

### SECTION 3: SECONDARY DEMONSTRATION — DUPLICATE REFUND WEBHOOK (60 SECONDS)

#### Step 3.1: Execute Vulnerable Webhook Replay
1. **Action**:
   - In Scenario Runner, click **`SCN-WEBHOOK-001: Duplicate Refund Webhook Replay`**.
   - Click **`Run Vulnerable`**.
2. **Expected UI Response**:
   - Timeline shows payment gateway delivered 5,000 INR refund webhook 3 times.
   - Ledger records 3 independent credit mutations totaling 15,000 INR.
   - Total booking entitlement was 12,000 INR.
   - Invariants `INV-5.3` (Webhook Idempotency) & `INV-5.5` (Refund Boundedness) BOTH **VIOLATED**.
   - Financial leak of 3,000 INR detected. Finding `CW-SEC-M2-002` emitted with **CRITICAL** severity (CWE-799).
3. **Presenter Dialogue**:
   > *"In this scenario, a payment webhook delivers three times. In vulnerable mode, the platform credits the agency wallet three times—15,000 INR on a 12,000 INR booking! Crosswire detects a direct 3,000 INR cash leak in ledger ground truth. Both Invariants 5.3 and 5.5 failed."*

#### Step 3.2: Execute Fixed Webhook Deduplication
1. **Action**:
   - Click **`Run Fixed`** on `SCN-WEBHOOK-001`.
2. **Expected UI Response**:
   - Timeline shows atomic SQLite event deduplication table intercepting deliveries 2 and 3.
   - HTTP 200 returned without ledger mutation.
   - Total refunded strictly 5,000 INR ($\le 12,000$ INR entitlement). Excess leak = 0 INR.
   - Finding `CW-SEC-M2-002` updated with verified retest.
3. **Presenter Dialogue**:
   > *"In Fixed Mode, atomic deduplication stores the processed event ID in an SQLite transaction lock. Deliveries 2 and 3 are safely acknowledged without touching the ledger. Invariants 5.3 and 5.5 pass."*

---

### SECTION 4: SUPPORTING DEMONSTRATION — MULTI-SUPPLIER OUTBOX (30 SECONDS)

1. **Action**:
   - In Scenario Runner, point to **`SCN-OUTBOX-001: Multi-Supplier Coordinated Outbox`**.
   - Click **`Run Fixed`**.
2. **Expected UI Response**:
   - Demonstrates M2.3B transactional outbox engine coordinating two simulated suppliers (e.g., Hotelbeds + Sabre) for a split-stay itinerary.
   - Shows lease coordinator, worker heartbeat, and monotonic fencing token preventing worker collisions.
3. **Presenter Dialogue**:
   > *"Behind the scenes, Crosswire also tests multi-supplier split-stay bookings using our transactional outbox engine with monotonic fencing tokens to prevent split-brain worker collisions under lease expiration."*

---

### SECTION 5: EXECUTIVE REPORT EXPORT & WRAP-UP (30 SECONDS)

1. **Action**:
   - Click the top navigation button: **`[📄 View Security Assessment Report]`**.
2. **Expected UI Response**:
   - Browser opens standalone HTML report (`/api/reports/html`).
   - Shows executive summary cards, verified invariant matrix table, confirmed vulnerabilities, and retest certificates.
3. **Presenter Dialogue**:
   > *"Everything can be exported as a self-contained, portable HTML security assessment report ready for engineering and security compliance teams.*
   > 
   > *To close: Traditional DAST asks whether the API responded. Crosswire asks whether the transaction remained correct."*

---

## 3. CONTINGENCY & DISASTER RECOVERY PLAN

| Potential Fault | Symptom | Immediate Operator Fix |
|---|---|---|
| **Port 8000 Conflict** | Server fails to bind on launch (`OSError: [Errno 10048] address already in use`) | Launch on port 8080: `python run_control_center.py --port 8080` and open `http://127.0.0.1:8080`. |
| **Browser Cached Old State** | UI shows stale counts or previous session runs | Hard refresh browser using `Ctrl + F5` (or `Cmd + Shift + R`). |
| **Accidental Server Terminal Closure** | Browser requests fail with `ERR_CONNECTION_REFUSED` | Open terminal: `python run_control_center.py` (server cold-starts in < 1 second). |
| **Slow Scenario Execution (> 2s)** | UI progress indicator stays spinning | Click browser refresh; `StateStore` holds previous results instantly. |
| **No Internet Connection** | WiFi drops or venue network is blocked | **Do nothing.** Crosswire uses zero external CDN links or external APIs; it is 100% locally self-contained. |
| **Projector Resolution Issues** | Dashboard text wraps awkwardly on small projector | Press `Ctrl + -` or `Ctrl + +` to scale the browser viewport cleanly. |

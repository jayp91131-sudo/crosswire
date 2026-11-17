# CROSSWIRE — 3-MINUTE LIVE HACKATHON DEMO SCRIPT

> **Presenter Pacing**: Fast, confident, cybersecurity-focused. Keep the browser open at `http://127.0.0.1:8000`. Do not touch code during the demo; drive everything from the Control Center UI.

---

## TIMING BREAKDOWN

| Time | Segment | Focus | Target Screen |
|---|---|---|---|
| **0:00 – 0:30** | **The Hook** | The Travel Transaction Blind Spot | Dashboard KPI View |
| **0:30 – 1:35** | **Primary Demo** | Supplier Commit-Then-Timeout (Vulnerable vs Fixed) | Scenario Runner & Timeline |
| **1:35 – 2:25** | **Secondary Demo** | Duplicate Refund Webhook Replay (Over-Disbursement) | Finding Cards & Retest |
| **2:25 – 3:00** | **The Verdict** | Ground-Truth Invariant Proof & HTML Export | Executive Report View |

---

## ACT 1: THE HOOK (0:00 – 0:30)

### Presenter Action:
- Stand in front of the Crosswire Control Center dashboard (`http://127.0.0.1:8000`).
- Ensure the KPI cards and Invariant Status Monitor are visible.

### Presenter Speaks:
> *"Judges, in B2B travel distribution, an API can return HTTP 200 OK, pass traditional syntax and protocol checks, and still fail at the transaction level—leading to duplicate supplier reservations and leaked refund credits.*
> 
> *Travel bookings are multi-party distributed sagas. When a supplier commits a room but the response drops over a flaky network, what does the aggregator do? It blindly retries. The supplier creates a second reservation. The API reports success. But behind the scenes, two reservations exist for one booking intent, which can lead to double-billing and locked inventory in production.*
> 
> *This is Crosswire: a specialized Dynamic Application Security Testing prototype built to catch travel transaction-integrity failures by validating database and ledger ground truth against business invariants."*

---

## ACT 2: PRIMARY SCENARIO — COMMIT-THEN-TIMEOUT (0:30 – 1:35)

### Presenter Action:
1. Click **Scenario Runner** tab.
2. Select card `SCN-TIMEOUT-001: Ambiguous Supplier Commit-Then-Timeout`.
3. Ensure mode is set to **`VULNERABLE MODE`**.
4. Click the red button: **`[⚡ Run Vulnerable Scenario]`**.

### What Happens on Screen:
- Real-time progress bar streams execution events.
- Execution Timeline populates within ~600ms.

### Presenter Speaks:
> *"Watch our synthetic travel platform execute a booking scenario. Agency Alpha requests a 15,000 INR Deluxe Suite. Look at the timeline:*
> 
> 1. *The supplier persists reservation `SUP-RES-01` to SQLite.*
> 2. *We inject a 0.30-second network stall—exceeding the aggregator's 0.15-second client timeout.*
> 3. *The aggregator experiences an ambiguous HTTP `ReadTimeout`.*
> 4. *A traditional aggregator naively retries the POST request.*
> 5. *Look at the database ground truth: the supplier committed a second reservation, `SUP-RES-02`, for the exact same booking intent!*
> 
> *Crosswire's Invariant Engine immediately flags the violation: Invariant `INV-5.2` (Booking Intent Uniqueness) failed because two reservation records were committed in SQLite for one intent ID. We emit finding `CW-SEC-M2-001` (Severity: High, CWE-675: Multiple Operations on Resource)."*

### Presenter Action:
1. Toggle mode to **`FIXED MODE`**.
2. Click the green button: **`[✔ Run Fixed Scenario]`**.

### What Happens on Screen:
- Timeline shows idempotent reconciliation query.
- Status changes to `INVARIANT_PASSED`.
- Under Findings, finding `CW-SEC-M2-001` immediately updates with a green **`[✔ RETEST VERIFIED]`** badge.

### Presenter Speaks:
> *"Now let's test the hardened build. In Fixed Mode, the aggregator attaches a deterministic idempotency key and executes a reconciliation probe.*
> 
> *The supplier recognizes the intent, returns the existing room, and creates zero duplicates. Exactly one reservation exists in the database. Invariant 5.2 passes, and Crosswire automatically proves the fix with a verified retest."*

---

## ACT 3: SECONDARY SCENARIO — DUPLICATE REFUND WEBHOOK (1:35 – 2:25)

### Presenter Action:
1. Select scenario `SCN-WEBHOOK-001: Duplicate Refund Webhook Replay`.
2. Click **`Run Vulnerable`**.

### What Happens on Screen:
- Timeline shows 3 consecutive deliveries of a 5,000 INR refund webhook.
- Finding `CW-SEC-M2-002` appears with **`CRITICAL`** severity.

### Presenter Speaks:
> *"Now let's test financial accounting. Payment gateways deliver webhooks at least once. Here, a 5,000 INR refund event is delivered three times.*
> 
> *In vulnerable mode, the platform blindly credits the agency wallet three times—15,000 INR on a booking with a strict 12,000 INR refundable entitlement ceiling.*
> 
> *Crosswire's Invariant Engine catches both failures: Invariant `INV-5.3` (Webhook Idempotency) and Invariant `INV-5.5` (Refund Boundedness) are violated. The synthetic ledger proved a direct 3,000 INR excess credit!*
> 
> *When we run Fixed Mode, atomic SQLite deduplication locks the event. Deliveries 2 and 3 return HTTP 200 without mutating the ledger. Exactly 5,000 INR is disbursed. Invariant 5.5 passes."*

---

## ACT 4: THE VERDICT & CONCLUSION (2:25 – 3:00)

### Presenter Action:
1. Click the top button: **`[📄 View Security Assessment Report]`**.
2. A standalone executive HTML security report opens in a new tab.
3. Scroll quickly through the executive metrics and ground-truth evidence tables.

### Presenter Speaks:
> *"Everything you saw is grounded in verifiable evidence. Crosswire generates an executive security assessment report with cryptographic diffs, database row IDs, and automated retest certificates.*
> 
> *Our prototype is backed by 266 passing automated tests verified in isolated clean extraction across our engine, outbox coordinator, and synthetic target platform.*
> 
> *To summarize: General-purpose DAST asks whether your API responded correctly.*
> 
> *Crosswire asks whether your transaction remained correct.*
> 
> *Thank you. We welcome your questions."*

---

## OPERATOR EMERGENCY RECOVERY PLAN

| Emergency | Immediate Action | Backup / Pivot |
|---|---|---|
| **Server not responding** | Terminal: `python run_control_center.py --port 8000` | Open pre-generated [`CROSSWIRE_SECURITY_ASSESSMENT_REPORT.html`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/CROSSWIRE_SECURITY_ASSESSMENT_REPORT.html) in browser and present findings from live report. |
| **Scenario run takes > 2s** | Click scenario card directly; quick-run buttons execute within 500ms. | Explain that SQLite lock is asserting ground truth across 3 databases. |
| **Accidental port conflict** | Run launcher on port 8080: `python run_control_center.py --port 8080`. | Open `http://127.0.0.1:8080`. |
| **No Internet Connection** | Crosswire is **100% offline-ready**. All assets, CSS, and scripts are embedded locally. | Proceed normally without WiFi. |

# CROSSWIRE: Master Project Dossier & Complete Execution Plan
**Autonomous Real-Time DAST & API Inventory Engine for B2B Travel Platforms**  
*TBO Hackathon 2026 — Track 2: Fraud Prevention & Security*

---

## 1. Executive Summary & Vision

### 1.1 The High-Concept Pitch
**Crosswire** is an autonomous, event-driven Dynamic Application Security Testing (DAST) agent system specifically designed for high-velocity B2B travel platforms.

Unlike legacy security scanners (ZAP, Burp, StackHawk) that run periodic, hours-long scans spraying generic SQLi/XSS payloads, **Crosswire activates the second a code deployment lands**. It understands the travel transactional state machine (`Search` $\to$ `Quote` $\to$ `Hold` $\to$ `Pay` $\to$ `Issue` $\to$ `Cancel`), extracts changes from deploy deltas and client JavaScript bundles, and uses **Dual-Persona Symmetrical Replay** (swapping session contexts between two isolated travel agencies) to uncover critical financial and business-logic flaws (IDOR, fare tampering, step-skipping, and zombie APIs) in near real time with **zero false positives**.

### 1.2 Core Metrics & Targets
* **Speed to Finding:** $< 60$ seconds from CI/CD deploy webhook to verified proof.
* **False Positive Rate:** $0\%$ on business-logic flaws (verified through dual-persona differential validation).
* **Inventory Accuracy:** Auto-classification of $100\%$ of endpoints into **Active**, **Zombie**, and **Shadow** states, with automatic OpenAPI 3.1 specification generation.
* **Safety & Non-Destructive Operation:** Strict token-bucket rate limiting ($< 10$ req/sec), target domain allowlisting, and state-rollback safeguards.

---

## 2. Alignment with TBO Hackathon Rubric & Rules

Crosswire is engineered to map directly to TBO’s 100-point judging criteria:

| Rubric Criterion | Weight | How Crosswire Scores Top Marks |
|---|:---:|---|
| **Valid Findings & Severity** | **30%** | Catches high-severity financial & business logic flaws: Cross-Agency IDOR, Negative Markup Tampering, Step-Skipping Ticket Issuance, and Deprecated Admin Actions. |
| **Precision & Noise Control** | **20%** | Dual-persona differential testing flags issues *only* when Agency B successfully accesses Agency A's data or mutates its state. No generic scanner noise. |
| **Safety & Scope Discipline** | **20%** | Enforces a strict domain allowlist, token-bucket throttling, synthetic test data only, and an instant emergency kill-switch. |
| **Speed After Change** | **10%** | Performs delta-targeted testing: analyzes Git/bundle diffs and tests only the affected state transitions in seconds rather than rescanning the entire app. |
| **Report Quality & Actionability** | **10%** | Every finding includes an executable 1-click cURL proof, estimated business impact (INR), code-fix recommendation, and an auto-closing retest loop. |
| **Discovery & API Inventory** | **10%** | Auto-builds the API map from UI/bundle crawl, compares against 90-day gateway traffic logs, tags Active/Zombie/Shadow routes, and exports an OpenAPI 3.1 spec. |
| **Total** | **100%** | **Full-spectrum coverage of every participant pack requirement.** |

---

## 3. System Architecture & End-to-End Workflow

```
                               ┌──────────────────────────────────────────────┐
                               │             CI/CD DEPLOY / GIT DIFF          │
                               │        Webhook / PR Commit / Bundle Delta    │
                               └──────────────────────┬───────────────────────┘
                                                      │
                                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ MODULE 1: RECON & INVENTORY ENGINE                                                                              │
│ • Client-side JavaScript bundle crawler (parses route definitions and API calls)                                │
│ • Historical Traffic Comparator (analyzes 90-day gateway logs)                                                  │
│ • State Tagging:                                                                                                │
│     - [ACTIVE]: In code AND receiving legitimate traffic                                                        │
│     - [ZOMBIE]: In code / still live on server, but 0 traffic in 90 days (deprecated endpoints)                 │
│     - [SHADOW]: Receiving traffic in gateway logs, but completely absent from mapped code                       │
│ • Artifact Generation: Dynamic OpenAPI 3.1 specification annotated with x-tbo-status                            │
└─────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ MODULE 2: TRAVEL STATE-MACHINE & DELTA PLANNER                                                                   │
│ • Travel Domain DAG: Search ➔ Quote ➔ Hold ➔ Pay ➔ Issue Ticket ➔ Cancel ➔ Refund                                │
│ • Delta Mapper: Identifies which state transition was modified in the deployment                                │
│ • LLM Attack Planner (Gemini 3.8 Flash): Formulates targeted probes based on parameter semantics:              │
│     1. Dual-Persona IDOR: Swap tokens between Agency Alpha and Agency Beta on object IDs                       │
│     2. Fare / Markup Tampering: Invert/zero out pricing attributes during confirmation                          │
│     3. Step-Skipping: Attempt transition (Issue) bypassing prerequisite state (Payment)                         │
│     4. Zombie API Abuse: Probe unauthenticated legacy endpoints for authorization leaks                         │
└─────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ MODULE 3: SAFE EXECUTION & DUAL-PERSONA HARNESS                                                                 │
│ • Safety Layer: Target domain allowlist, Token-Bucket rate limiter (max 10 req/s), synthetic data isolation     │
│ • Dual Sessions: Authenticates simultaneously as Agency Alpha (agency_001) and Agency Beta (agency_002)         │
│ • Non-destructive execution: Creates and cleans up its own test bookings                                       │
└─────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ MODULE 4: VERIFICATION, TRIAGE & AUTO-RETEST HARNESS                                                            │
│ • Proof Generator: Produces a standalone, reproducible cURL command proving the flaw                            │
│ • Business Impact Calculator: Scores severity based on financial ledger risk and PII exposure                    │
│ • Auto-Retest Loop: Re-executes verification proof after developer fixes code; auto-closes ticket if 403/fixed  │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. The Planted Testbed & Target Platform

To demonstrate Crosswire live to the judges, we build a lightweight, realistic **TBO Staging Platform Mock** (`target_platform/app.py`) with pre-seeded agencies and realistic travel flows.

### 4.1 Seed Personas
* **Agency Alpha (`agency_001`):** Active B2B travel agency with active credit wallet.
* **Agency Beta (`agency_002`):** Competitor B2B travel agency.
* **Sub-Agent (`sub_001`):** Restricted child account under Agency Alpha.
* **Platform Admin (`admin_001`):** Operations manager.

### 4.2 Seed Endpoints & Planted Vulnerabilities
1. **`GET /api/v1/bookings/{booking_id}` (IDOR - High Severity):**
   * *Flaw:* Returns full passenger details, PII, and financial vouchers without verifying that `booking.agency_id == req.user.agency_id`.
   * *Crosswire Detection:* Agency Alpha creates booking `#TBOH-1001`; Agency Beta calls the endpoint with its own token. Agency Beta receives Alpha's booking data $\to$ **100% Confirmed IDOR**.
2. **`POST /api/v1/bookings/confirm` (Fare Tampering - High Severity):**
   * *Flaw:* Server accepts client-submitted `markup` or `total_amount` rather than verifying against the server-side quote session.
   * *Crosswire Detection:* Agent modifies `markup: -1500` or `total_amount: 1` $\to$ Booking succeeds at ₹1 instead of ₹15,000 $\to$ **100% Confirmed Price Tampering**.
3. **`POST /api/v1/bookings/{booking_id}/issue-ticket` (State-Skipping - Critical Severity):**
   * *Flaw:* Issues confirmed PNR ticket even if the booking status is `HELD` and payment was never completed.
   * *Crosswire Detection:* Agent skips `/api/v1/payments/process` and calls `/issue-ticket` directly $\to$ Ticket issued without payment $\to$ **100% Confirmed Step-Skipping**.
4. **`POST /api/v0/agency/refund-direct` (Zombie API - Medium/High Severity):**
   * *Flaw:* Legacy version 0 refund endpoint left alive from a 2024 deployment. Bypasses 2FA/approval workflows and has zero traffic in 90-day logs.
   * *Crosswire Detection:* Discovered by bundle analysis; verified active on staging; flagged as **Zombie API**.
5. **`GET /api/internal/debug-tenants` (Shadow API - Medium Severity):**
   * *Flaw:* Undocumented internal diagnostic endpoint appearing in gateway logs but absent from any code repo or documentation.
   * *Crosswire Detection:* Traffic log diffing flags route as **Shadow API** exposing tenant IDs.

---

## 5. Technical Implementation Plan & Milestones

The project is structured in `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`:

### Directory Structure
```
crosswire/
├── README.md                           # Quickstart, architecture overview, and judging guide
├── MASTER_PLAN.md                      # This comprehensive document
├── requirements.txt                    # Python dependencies
│
├── target_platform/                    # Mock TBO Staging Platform
│   ├── __init__.py
│   ├── app.py                          # FastAPI B2B travel server (bookings, quotes, payments)
│   ├── database.py                     # In-memory SQLite/Dictionary state store
│   ├── seed_data.py                    # Pre-populated agencies, hotels, bookings
│   └── logs/
│       └── gateway_90day_traffic.json  # Simulated historical load balancer logs
│
├── crosswire_engine/                   # Core Security Agent
│   ├── __init__.py
│   ├── config.py                       # Scope allowlists, rate-limit configs, API keys
│   ├── change_detector.py              # Git diff & CI/CD deploy event parser
│   ├── inventory_analyzer.py           # Active/Zombie/Shadow classifier & OpenAPI generator
│   ├── state_graph.py                  # Travel business-logic state machine (DAG)
│   ├── attack_planner.py               # Contextual probe planner (Gemini 3.8 Flash + rules)
│   ├── executor.py                     # Safe Dual-Persona runner with token-bucket limiter
│   ├── verifier.py                     # Proof-of-concept generator & ticket formatter
│   └── retest_harness.py               # Auto-retest and ticket resolution engine
│
├── ui/                                 # Operator & Judge Dashboard
│   ├── dashboard.py                    # Streamlit interactive live operations center
│   └── static/                         # Visual assets & flow diagrams
│
└── tests/
    └── run_demo.py                     # Automated 3-minute end-to-end judge demonstration
```

### Sprint Milestones

* **Milestone 1: Target Platform & Synthetic Environment**
  * Stand up the FastAPI mock travel platform with authentic B2B travel schemas (TBOH booking codes, agency credits, quote tokens).
  * Seed the 90-day gateway traffic log containing active, zombie, and shadow traces.
  * Implement the 5 intentional vulnerabilities.

* **Milestone 2: Recon, Bundle Analyzer & API Inventory**
  * Build the endpoint discovery module that crawls the target platform.
  * Implement the log-diffing algorithm:
    $$\text{Zombie} = \text{Route in Code/Server} \cap \{\text{Traffic in 90 Days} = 0\}$$
    $$\text{Shadow} = \text{Route in Traffic Logs} \setminus \text{Mapped Code Routes}$$
  * Auto-generate the annotated OpenAPI 3.1 schema.

* **Milestone 3: State-Machine & Dual-Persona Execution Engine**
  * Define the travel transaction FSM graph.
  * Build the Dual-Persona runner that holds simultaneous valid bearer tokens for Agency Alpha and Agency Beta.
  * Implement the safety harness: max requests/sec ceiling, synthetic ID boundaries, error circuit-breaker.

* **Milestone 4: Verification, Reporting & Auto-Retest Loop**
  * Generate 1-click reproducible cURL commands with clean expected vs. actual diffs.
  * Build the re-test verification routine: once a fix flag is toggled on the target platform, the agent re-runs the exact proof to confirm `403 Forbidden` and auto-closes the incident.

* **Milestone 5: Interactive Dashboard & Demo Script**
  * Build a clean Streamlit dashboard showing:
    1. Real-time Deploy Watcher & Event Stream.
    2. API Inventory Matrix (Active / Zombie / Shadow badges).
    3. Live State-Machine Attack Visualizer.
    4. Verified Vulnerabilities with 1-Click cURL Reproducers & Auto-Retest status.

---

## 6. The 3-Minute Winning Demo Script (Walkthrough for Judges)

| Time | Action on Screen | What You Say to the Judges | What it Proves |
|:---:|---|---|---|
| **0:00 - 0:40** | Open Dashboard. Show the API Inventory tab with an auto-generated OpenAPI 3.1 map. | *"B2B travel platforms deploy daily. Legacy scanners rely on manual Swagger files and miss hidden routes. Crosswire automatically crawled our platform, compared it against 90 days of traffic, and identified 4 Active APIs, 1 Zombie API left open from 2024, and 1 Shadow debug route."* | **Criterion 6:** Discovery & API Inventory (10%) |
| **0:40 - 1:20** | Trigger a mock CI/CD deploy event: Developer pushed a commit modifying `booking_cancel.py` and `checkout_fare.py`. | *"A deploy just landed. Instead of running a 4-hour full scan, Crosswire’s Delta Engine identifies the exact modified endpoints and state transitions. In under 5 seconds, it selects targeted business-logic probes."* | **Criterion 4:** Speed after change (10%) & **Criterion 3:** Safety & Scope (20%) |
| **1:20 - 2:15** | Run the Live Attack Loop. Show Dual-Persona execution: Agency Alpha creates Booking `#TBOH-1001`. Agency Beta attempts to read and cancel it. | *"Here is the dual-persona test: Agency Alpha booked a hotel. Crosswire immediately replays the request with Agency Beta's credentials. Agency Beta successfully pulls Alpha's passenger PII and cancels the booking. This is a critical IDOR—caught with zero false positives."* | **Criterion 1:** Valid Findings & Severity (30%) & **Criterion 2:** Precision (20%) |
| **2:15 - 3:00** | Click on the generated ticket. Show the clean cURL proof. Click "Apply Developer Fix" on the mock server, then click "Auto-Retest". Crosswire runs the proof, sees `403 Forbidden`, and closes the ticket. | *"No noisy 50-page PDFs. Crosswire generated a 2-line reproducible cURL command and exact code patch. Once the developer patches the code, Crosswire auto-retests the endpoint, verifies the 403 response, and automatically closes the vulnerability ticket."* | **Criterion 5:** Report Quality & Actionability (10%) |

---

## 7. Immediate Next Steps

1. Create directory `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`.
2. Save this master plan into the project root.
3. Scaffold `target_platform/app.py` and `crosswire_engine/`.
4. Run initial verification tests.

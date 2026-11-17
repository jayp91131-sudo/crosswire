# CROSSWIRE — JUDGE QUESTION & ANSWER COMPENDIUM

> **Purpose**: Rigorous, transparent, and technically grounded answers to the top 20 questions from hackathon judges, security auditors, and investors.
> **Integrity Rule**: Explicitly distinguish between **Implemented**, **Demonstrated**, **Reported**, **Planned**, and **Unvalidated Assumptions**. Never bluff.

---

### 1. What exactly is Crosswire?
**Answer**:
Crosswire is a specialized Dynamic Application Security Testing (DAST) prototype focused on **business-logic and transaction-integrity vulnerabilities** in travel distribution systems.

Unlike traditional web vulnerability scanners that search for protocol-level bugs (like SQL injection, XSS, or broken authentication), Crosswire injects domain-specific distributed failure modes (such as ambiguous commit-then-timeouts, socket drops, and replayed webhooks) and validates whether the target system's relational database and financial ledger state conform to formal mathematical business invariants.

---

### 2. What real problem does it solve?
**Answer**:
In B2B travel distribution, **an API call can return HTTP 200 OK and still cause severe operational and financial damage behind the scenes**.

When an aggregator attempts to book a hotel room, and the supplier commits the reservation but the network response drops or times out, the aggregator client cannot know whether the booking succeeded or failed. If the aggregator naively retries the request without an idempotent reconciliation contract, the supplier commits a second, duplicate reservation. The API reports success, but the agency is billed twice, inventory is double-booked, and cash leaks silently. Crosswire detects and proves these multi-party transactional failures before they reach production.

---

### 3. Why is this relevant specifically to B2B travel?
**Answer**:
B2B travel architecture has three unique structural characteristics that make it uniquely vulnerable:
1. **Multi-Party Sagas Without Distributed Transactions**: Travel bookings span independent legal entities (travel agency, aggregator, payment gateway, GDS, hotel CRS) that cannot participate in a 2-Phase Commit (2PC). Every operation is an eventually consistent saga.
2. **Heterogeneous Supplier Capabilities**: Modern suppliers vary widely. Some support strict idempotency keys; others rely on reference lookup queries; others have no native idempotency at all.
3. **High Monetary Asymmetry**: An API timeout costs fractions of a cent in cloud compute, but an un-reconciled duplicate hotel booking or replayed refund webhook directly costs hundreds or thousands of dollars in unrecoverable supplier commitments.

---

### 4. Why is traditional API testing insufficient for this problem?
**Answer**:
Traditional API testing operates at the **request-response boundary**:
- **Stateless Verification**: Tools like Postman or Playwright assert on the HTTP response status code, latency, and JSON payload returned to the caller.
- **Protocol Security Scanners**: Tools like Burp Suite, OWASP ZAP, and Nuclei fuzz parameters to find protocol injection flaws, broken access control, and header misconfigurations.

None of these tools query the **internal database ground truth** of the aggregator, payment gateway, and third-party suppliers to verify whether two reservations were committed for one logical intent, or whether ledger credits exceeded refundable entitlements.

General-purpose DAST tools like Burp Suite or OWASP ZAP focus primarily on web application perimeter vulnerabilities (such as injection flaws, authentication issues, and security headers). They typically do not inspect backend database ground truth across multi-party sagas. Crosswire is designed as a complementary layer focused specifically on travel transaction scenarios and verifying business invariants against the synthetic platform's database and ledger ground truth.

---

### 5. How does Crosswire know that a vulnerability actually happened?
**Answer**:
Crosswire does not guess based on error logs or HTTP response codes. It verifies violations using a **Ground-Truth Oracle** (`engine/ground_truth_oracle.py`) that executes direct, read-only SQL queries against the target platform's relational databases (`aggregator.db`, `supplier.db`, `payment.db`).

A vulnerability is confirmed if and only if the ground-truth state violates a formal mathematical assertion evaluated by the Invariant Engine. Every finding includes exact database row IDs, timestamps, and cryptographic state diffs.

---

### 6. What is an invariant?
**Answer**:
An invariant is a formal mathematical predicate that **must evaluate to True across all valid states of the system**, regardless of concurrency, network latency, timeouts, crashes, or message reordering.

In Crosswire, invariants are derived from [`MASTER_PLAN_V2.md`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/MASTER_PLAN_V2.md#L280):
- **INV-5.2 (Booking Intent Uniqueness)**: $\text{COUNT}(\text{Supplier Reservations linked to Intent } I) \le 1$.
- **INV-5.3 (Webhook Idempotency)**: $\text{COUNT}(\text{Posted Ledger Credits for Event ID } E) \le 1$.
- **INV-5.4 (Voucher Entitlement)**: $\text{Voucher Issued} \implies \text{Supplier Confirmed} \land \text{Payment Obligation Satisfied}$.
- **INV-5.5 (Refund Boundedness)**: $\sum \text{Completed Refunds}(B, c) \le \text{Refundable Entitlement}(B, c)$.
- **INV-5.7 (Financial Ledger Double-Entry Consistency)**: Dual-entry balanced debits and credits in the financial ledger (deferred in current prototype).
- **INV-M2.3B (Stay Coverage & Monotonic Fencing)**: Worker leases must have monotonically increasing fencing tokens to prevent split-brain dual commits.

---

### 7. How does Crosswire detect duplicate supplier reservations?
**Answer**:
In `m2_3c/primary_scenario.py` and `engine/invariant_engine.py`:
1. Crosswire generates a unique synthetic booking intent ID (e.g. `INTENT-CC-VULN-2894`).
2. It injects a fault where the supplier adapter delays its response by 0.30s, exceeding the aggregator's 0.15s client timeout.
3. The aggregator retries the booking.
4. After the transaction finishes, `InvariantEngine.check_booking_intent_uniqueness()` queries the supplier database:
   ```sql
   SELECT supplier_reservation_id FROM supplier_reservations WHERE intent_id = ?
   ```
5. If the count of distinct supplier reservations is greater than 1, Invariant 5.2 evaluates to `False`. Crosswire confirms the duplicate and records both reservation IDs in the finding.

---

### 8. How does it detect duplicate refunds?
**Answer**:
In `m2_3c/secondary_scenario.py`:
1. Crosswire simulates a payment gateway delivering the exact same refund webhook event three consecutive times.
2. In vulnerable mode, the aggregator executes ledger mutations for each delivery without an idempotency lock.
3. Crosswire's oracle queries the financial ledger and asserts two invariants:
   - **INV-5.3**: Counts distinct ledger credit entries created by the same event ID. If $> 1$, idempotency is broken.
   - **INV-5.5**: Sums all refund transactions for the booking and compares it to the booking's authorized refundable entitlement.
4. When 3 deliveries of 5,000 INR result in 15,000 INR refunded on a 12,000 INR booking, Crosswire mathematically proves a direct 3,000 INR excess financial leak.

---

### 9. How does the fixed retest work?
**Answer**:
Crosswire implements closed-loop verification:
1. When a scenario runs in **Fixed Mode**, the target platform executes with hardened remediation logic (e.g. deterministic idempotency keys, reconciliation query contracts, atomic SQLite deduplication tables).
2. The exact same adversarial scenario (same timeout delay or same repeated webhooks) is executed.
3. Crosswire queries the ground truth databases again.
4. If all monitored invariants evaluate to `True`, Crosswire generates a formal `RetestResult` object with a unique timestamped Retest ID.
5. In the Control Center, this retest result is automatically bound to the open finding, displaying a verified green badge and updating the dashboard metrics.

---

### 10. Why not simply use Burp Suite or OWASP ZAP?
**Answer**:
Burp Suite and OWASP ZAP are world-class tools for **application-layer syntax, web protocol, and perimeter security**:
- They excel at finding XSS, SQL injection, SSRF, broken access control, and insecure cryptographic configurations at the web protocol layer.
- However, general-purpose scanners typically do not model travel domain sagas, outbox coordinators, supplier commit timeouts, or multi-party ledger balances.
- Crosswire is **complementary** to Burp and ZAP: while general DAST secures the HTTP interface, Crosswire focuses specifically on travel transaction scenarios and validates business-state invariants against the synthetic platform's database and ledger ground truth.

---

### 11. How is this different from chaos engineering (e.g., Chaos Mesh, Gremlin)?
**Answer**:
- **Chaos Engineering**: Injects infrastructure-level faults (dropping packets, killing Kubernetes pods, exhausting CPU) and monitors operational metrics (HTTP 5xx rate, latency p99, error budgets, SLOs). Chaos tools do not evaluate whether business transactions remained correct.
- **Crosswire**: Injects domain-specific transactional failure modes and uses **semantic business oracles** to determine whether business rules survived. A chaos tool sees an HTTP timeout as an expected degradation; Crosswire checks whether that timeout caused a duplicate room booking or leaked 3,000 INR.

---

### 12. Can Crosswire work with real travel APIs (Amadeus, Sabre, Hotelbeds)?
**Answer**:
- **Current State (Demonstrated)**: Crosswire operates on a synthetic B2B travel platform with mock supplier adapters that mimic the behavior and failure modes of major travel GDSs and hotel bedbanks.
- **Feasibility with Real APIs**: Crosswire can test against real travel APIs in their **certification or sandbox environments**. However, testing against real production APIs carries the risk of committing real bookings with financial cancellation penalties.
- **Architectural Requirement**: Connecting to real supplier sandboxes requires authoring specific supplier protocol adapters that map supplier reference IDs to Crosswire's booking intent oracle. This is on our Phase 1 technical roadmap.

---

### 13. Can it work with multiple suppliers in a single booking?
**Answer**:
**Yes**. This was implemented and verified in Milestone 2.3B and Milestone 2.3C:
- Scenario `SCN-OUTBOX-001` coordinates split-stay itineraries across multiple simulated suppliers (e.g. Hotelbeds and Sabre).
- Crosswire implements an atomic Outbox Engine (`m2_3b/outbox/engine.py`) with monotonic fencing tokens and a lease coordinator to handle worker crashes and partial supplier commitments.
- The `CoverageOracle` verifies that all legs of a multi-supplier itinerary are fulfilled without room drops or duplicate overlaps.

---

### 14. What prevents unsafe testing against production systems?
**Answer**:
Crosswire is strictly engineered as a **pre-production testing tool**:
1. **Isolated Testbed Boundary**: The current engine runs strictly against local SQLite databases (`aggregator.db`, `supplier.db`, `payment.db`).
2. **Explicit Safety Policy**: In production travel systems, blind fault injection can create real monetary obligations. Crosswire's design guidelines explicitly prohibit firing uncontrolled live requests into production travel networks without certified sandbox isolation.
3. **Read-Only Invariant Oracles**: The invariant engine only performs read-only `SELECT` queries on ground truth; it never modifies production state during an assertion.

---

### 15. What are the current technical limitations?
**Answer (Completely Honest)**:
1. **Synthetic Local Testbed**: The current implementation operates on a synthetic local testbed using SQLite. Its simulated transaction failures do not establish production-scale distributed-system guarantees or integrations with real travel suppliers.
2. **Relational SQLite Ground Truth**: Ground truth is currently stored in local SQLite databases, which do not test distributed network partitions or distributed consensus (Raft/Paxos).
3. **Scope Constraints**: Operates on a single-hotel, single-currency prototype scope. Invariant 5.7 (Financial Ledger Double-Entry Consistency) remains deferred.
4. **Session State**: In the Control Center, session execution history and active finding cards are managed in-memory in `StateStore` (while target assertions evaluate against persistent SQLite databases).

---

### 16. What is the potential business model?
**Answer (Unvalidated Assumptions)**:
If commercialized, Crosswire could operate as a **B2B Developer Tool / Enterprise Security SaaS**:
- **Pre-Production CI/CD Gate**: Licensed per developer or per repository to travel tech companies (OTAs, aggregators, hotel tech providers) to block pull requests that introduce transaction regressions.
- **Supplier Certification & Compliance**: Used by travel suppliers to certify that aggregator integrations handle commit-then-timeouts and cancellation sagas correctly before granting API access.
- *Status*: This is an unvalidated product hypothesis; we have no current commercial customers.

---

### 17. Who would be the potential customer?
**Answer (Unvalidated Assumptions)**:
- **Online Travel Agencies (OTAs)**: Booking.com, Expedia, MakeMyTrip, Cleartrip.
- **B2B Travel Aggregators & Bedbanks**: Hotelbeds, WebBeds, TBO Holidays.
- **Travel Distribution Tech Providers**: Amadeus, Sabre, Travelport.
- **Fintech & Payment Orchestration Platforms**: Companies processing travel refunds and high-frequency webhook callbacks.

---

### 18. How would it integrate into an existing development workflow?
**Answer**:
- **Local Development**: Engineers run `python run_control_center.py` locally to inspect scenarios and verify fixes before opening a PR.
- **CI/CD Automation (Roadmap)**: A headless CLI version (`crosswire-cli run --scenario SCN-TIMEOUT-001 --fail-on-violation`) can run in a GitHub Actions or GitLab CI runner against Dockerized staging environments.
- If an invariant fails, the CI pipeline fails and outputs the JSON finding and database diffs directly into the pull request comments.

---

### 19. What evidence supports the current functionality?
**Answer**:
Every capability presented is grounded in verifiable evidence:
1. **266 Passing Automated Tests**:
   - `control_center/tests/`: 9 passed
   - `m2_3c/tests/`: 7 passed
   - `m2_3b/tests/`: 54 passed
   - `tests/` (Baseline): 196 passed
   - Verified via isolated clean extraction in Antigravity.
2. **Live Operational Control Center**: Fast, responsive FastAPI web dashboard running on `http://127.0.0.1:8000`.
3. **Executable CLI**: Standalone CLI runner `python run_m2_3c_demo.py` executes all scenarios with color-coded terminal proof.
4. **Cryptographic Reports**: Self-contained HTML assessment report (`/api/reports/html`) generated directly from live SQLite state.

---

### 20. What would you build next?
**Answer (Technical Roadmap)**:
1. **Amadeus & Sabre Sandbox Connectors**: Replace mock adapters with real certification API testbeds.
2. **Invariant 5.7 (Financial Ledger Double-Entry Consistency)**: Implement formal double-entry accounting ledger balance checks across booking, hold, capture, and refund cycles.
3. **Headless CI/CD GitHub Action**: Package the test runner as an official GitHub Action for automated pull request gating.
4. **Distributed Storage Support**: Extend the Ground-Truth Oracle to query PostgreSQL and MySQL instances in cloud environments.

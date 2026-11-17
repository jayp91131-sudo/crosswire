# CROSSWIRE — 5-MINUTE JUDGE PITCH SCRIPT

> **Format**: Slide Presentation (Slides 1–5) $\to$ Live Demo Walkthrough $\to$ Slide Presentation (Slides 8–10) $\to$ Q&A.
> **Total Duration**: Exactly 5 Minutes.
> **Key Objective**: Deliver a technically credible, cybersecurity-grounded pitch proving that Crosswire solves a high-value blind spot in travel transaction integrity.

---

## TIMING & STRUCTURE

```
[0:00 - 1:00]  MIN 1: The Travel Transaction Integrity Gap (Slides 1-3)
[1:00 - 2:00]  MIN 2: The Invariant-Driven Security Architecture (Slides 4-5)
[2:00 - 3:40]  MIN 3-4: Live Control Center Demonstration (Primary & Secondary Scenarios)
[3:40 - 4:30]  MIN 4: Market Positioning & Honest Boundaries (Slides 9-10)
[4:30 - 5:00]  MIN 5: Roadmap & Final Punchline (Slide 10)
```

---

## MINUTE 1: THE TRAVEL TRANSACTION BLIND SPOT (0:00 – 1:00)

*(Show Slide 1: Title $\to$ Slide 2: The Problem)*

> *"Good afternoon, judges. In the global travel industry, billions of dollars flow through complex distributed transactions every day. A single family vacation booking touches an online travel agency, an aggregator, payment gateways, global distribution systems, and multiple airline and hotel backends.*
> 
> *Here is the critical challenge in travel tech:*
> **An API call can return HTTP 200 OK, pass traditional syntax and protocol tests, and still cause business and financial failures behind the scenes.**
> 
> *(Show Slide 2)*
> *Consider what happens when a network timeout occurs. An agency requests a hotel room. The supplier commits the room to its database, but the HTTP response times out. The aggregator client experiences a ReadTimeout. What does normal client software do? It retries.*
> 
> *The supplier receives the second request, doesn't recognize it as an ambiguous retry, and commits a second reservation.*
> 
> *The API returns 200 OK. But behind the scenes, two reservation records exist in the database for one booking intent. In a live system, this un-reconciled duplicate can lead to customer double-billing and locked inventory.*
> 
> *(Show Slide 3)*
> *Why can traditional security testing miss this? Because tools like Burp Suite, OWASP ZAP, and Postman inspect HTTP status codes, headers, and protocol payloads. They focus on web protocols, syntax, and endpoint access control rather than verifying multi-party transaction state and business invariants across backend databases and financial ledgers."*

---

## MINUTE 2: THE CROSSWIRE SOLUTION & ARCHITECTURE (1:00 – 2:00)

*(Show Slide 4: The Solution $\to$ Slide 5: System Architecture)*

> *"Enter Crosswire: a specialized Dynamic Application Security Testing prototype designed for travel transaction integrity.*
> 
> *Instead of only asking whether an endpoint returns a valid response, Crosswire asks:*
> **Did the transaction preserve fundamental business invariants in the database?**
> 
> *(Show Slide 4 & 5)*
> *Here is how Crosswire works:*
> 1. *We deploy a synthetic B2B travel platform—complete with an aggregator, payment gateway, and mock suppliers.*
> 2. *Our Adversarial Engine injects domain-specific failure modes: commit-then-timeouts, socket drops, and replayed webhooks.*
> 3. *Our Ground-Truth Oracle queries the actual relational database records and ledger entries in SQLite.*
> 4. *Our Invariant Engine mathematically asserts core business rules: Invariant 5.2 (Booking Intent Uniqueness), Invariant 5.3 (Webhook Idempotency), Invariant 5.4 (Voucher Entitlement), and Invariant 5.5 (Refund Boundedness).*
> 5. *When an invariant breaks, Crosswire emits a structured security finding classified under CWE standards (such as CWE-675 and CWE-799), complete with database row diffs.*
> 6. *When engineers remediate the code, Crosswire runs an automated retest against the hardened build to verify the vulnerability is eliminated.*
> 
> *Let's see this running live in our Control Center."*

---

## MINUTES 3–4: LIVE CONTROL CENTER DEMONSTRATION (2:00 – 3:40)

*(Switch to live browser running Control Center at `http://127.0.0.1:8000`)*

> *"This is the Crosswire Control Center. It's a fully operational local web cockpit built with FastAPI and a modern cybersecurity SPA dashboard.*
> 
> *(Select SCN-TIMEOUT-001 $\to$ Click 'Run Vulnerable')*
> *First, we execute our primary scenario: Ambiguous Supplier Commit-Then-Timeout. Look at the real-time execution timeline:*
> - *Event 1: Booking intent created for 15,000 INR.*
> - *Event 2: The supplier commits reservation `SUP-RES-01` to SQLite.*
> - *Event 3: We inject a 0.30-second delay, exceeding the aggregator's 0.15-second client timeout.*
> - *Event 4: Aggregator experiences `ReadTimeout` and naively re-issues the request without an idempotency key.*
> - *Event 5: Look at the database: the supplier created reservation `SUP-RES-02`!*
> 
> *Crosswire's Invariant Engine flags the violation immediately: Invariant 5.2 failed because COUNT(reservations) equals 2. We generated finding `CW-SEC-M2-001` (Severity: High, CWE-675: Multiple Operations on Resource).*
> 
> *(Toggle to Fixed Mode $\to$ Click 'Run Fixed')*
> *Now watch what happens when we run the remediated build. In Fixed Mode, the aggregator attaches an idempotency contract and executes a reconciliation probe.*
> 
> *The supplier recognizes the intent, returns the original confirmation, and commits zero duplicate records. Invariant 5.2 passes, and Crosswire automatically issues an official Retest Pass Proof attached to the finding.*
> 
> *(Select SCN-WEBHOOK-001 $\to$ Click 'Run Vulnerable')*
> *Next, let's examine financial integrity. Here, a payment provider delivers a 5,000 INR refund webhook three times.*
> 
> *In vulnerable mode, the platform credits the agency wallet three times—15,000 INR on a booking with a 12,000 INR entitlement. Crosswire detects a direct 3,000 INR excess credit in synthetic ledger ground truth! Invariants 5.3 and 5.5 fail, generating Critical finding `CW-SEC-M2-002`.*
> 
> *(Click 'Run Fixed')*
> *In Fixed Mode, atomic SQLite deduplication locks the event. Deliveries 2 and 3 return 200 OK without ledger mutation. Exactly 5,000 INR is disbursed. Invariants 5.3 and 5.5 pass.*
> 
> *(Click 'View Security Assessment Report')*
> *Finally, with one click, Crosswire exports an executive HTML security assessment report containing all findings, chronological audits, and cryptographic proofs ready for engineering and security leadership."*

---

## MINUTE 4: MARKET POSITIONING & HONEST BOUNDARIES (3:40 – 4:30)

*(Return to Slides $\to$ Show Slide 9: Differentiation $\to$ Slide 10: Boundaries)*

> *"*(Show Slide 9)*
> *Let's talk about where Crosswire fits in the cybersecurity landscape. We do not replace Burp Suite or OWASP ZAP—we complement them. General DAST secures your HTTP perimeter against injection and auth attacks. Functional tests verify happy paths. Chaos engineering tests if servers crash.*
> 
> *Crosswire provides a complementary focus on travel-specific transaction scenarios, validating business-state invariants against synthetic database and ledger ground truth.*
> 
> *(Show Slide 10)*
> *We want to be completely honest about our current boundaries:*
> - *This is a functional security testing prototype, not a production accounting engine.*
> - *It runs on a single-host synthetic B2B travel platform with relational SQLite storage. Its simulated transaction failures do not establish production-scale distributed guarantees or integrations with real travel suppliers.*
> - *The session execution history is maintained in-memory in the Control Center, while invariant verifications evaluate actual SQLite database ground truth.*
> - *Our current scope is single-hotel, single-currency; Invariant 5.7 (Financial Ledger Double-Entry Consistency) remains deferred.*
> - *Every claim we make today is backed by 266 passing automated tests verified in isolated clean extraction."*

---

## MINUTE 5: ROADMAP & CLOSING (4:30 – 5:00)

*(Show Slide 10: Closing Punchline)*

> *"Looking ahead, our technical roadmap focuses on three milestones:*
> 1. *Developing pre-built connectors for real travel supplier certification sandboxes like Amadeus, Sabre, and Hotelbeds.*
> 2. *Implementing Invariant 5.7 (Financial Ledger Double-Entry Consistency) mathematical audits.*
> 3. *Packaging a headless CLI tool for CI/CD pipeline blocking in GitHub Actions and GitLab CI.*
> 
> *In closing:*
> 
> **General-purpose DAST asks whether your API responded correctly.**
> **Crosswire asks whether your transaction remained correct.**
> 
> *Thank you. We are ready for your questions."*

---

## JUDGE HANDOFF CHECKLIST

- [ ] Presentation opened to Slide 10.
- [ ] Control Center tab open at `http://127.0.0.1:8000`.
- [ ] HTML Assessment Report open in secondary tab.
- [ ] Both findings (`CW-SEC-M2-001` and `CW-SEC-M2-002`) visible with verified retest badges.

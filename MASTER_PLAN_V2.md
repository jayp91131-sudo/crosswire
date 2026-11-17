# CROSSWIRE 2.0 — Master Plan V2

**Product:** Business-Logic & Transaction-Integrity DAST for B2B Travel Platforms  
**Hackathon:** TBO 2026 — Fraud Prevention & Security  
**Decision status:** Conditional GO for a bounded hackathon prototype; commercial demand and defensibility remain unproven.  
**Document status:** Pre-implementation specification. No product capability or benchmark result is claimed as implemented.

---

## 0. Decision Summary

### 0.1 Strategic decision

Retire the positioning of Crosswire as a generic autonomous DAST scanner. Retain the security-testing foundation and focus it on **travel-specific, stateful, multi-party business-logic and financial exploits**.

Crosswire 2.0 is a pre-release security-testing engine for B2B travel transaction workflows. It runs controlled adversarial scenarios against an isolated staging system containing an agency, travel platform, supplier simulator and payment simulator. It checks whether the system preserves authorization, booking, payment, wallet, ticketing and refund invariants when requests time out, events repeat or arrive out of order, and transactions race.

### 0.2 What is and is not validated

**Supported by public technical documentation:**
- Travel booking APIs explicitly document idempotency, retry and ambiguous-result handling. Hotelbeds recommends a minimum timeout for booking confirmation; Booking.com documents duplicate-request handling after timeouts; Travel Curious specifies idempotent supplier booking and confirmation operations.
- Payment-webhook testing products already offer CI-triggered duplicate/late-event delivery and application-outcome assertions. MockCard is a direct adjacent competitor for this slice.
- API security products already advertise business-logic-aware testing, exploit validation and CI/CD integration. Crosswire must not claim these capabilities are absent from the market.
- Shadow/zombie API inventory is an established product category. Crosswire must use precise lifecycle definitions.

**Not yet validated:**
- That travel companies will buy a dedicated Crosswire product rather than combine existing tools and custom tests.
- That a material segment of travel companies has no adequate in-house solution.
- That Crosswire is the first, only or best product in this category.
- That the proposed prototype will outperform any competitor before controlled benchmark runs are complete.
- Any specific financial-loss estimate, customer count, market size or percentage margin.

### 0.3 Go / pivot / stop gate

Proceed with the hackathon prototype only if the team can demonstrate, in a reproducible local testbed:
1. A supplier booking is persisted, the response is lost, and a naïve retry creates a second supplier reservation.
2. Crosswire detects the duplicate through a transaction-intent and supplier-reservation oracle—not merely an HTTP timeout.
3. A repeated signed refund event produces more than one financial side effect in the vulnerable build and exactly one in the fixed build.
4. The system provides clear evidence, repeatable test instructions and a successful retest.
5. The complete demonstration remains within the hackathon's scope and safety rules.

This is a **hackathon GO**, not proof of commercial product-market fit.

---

## 1. Product Definition

### 1.1 Positioning

> Crosswire 2.0 is a travel-specific business-logic and transaction-integrity DAST engine. It exercises multi-party booking, payment, supplier-callback and refund workflows under controlled adversarial conditions, then verifies whether security and financial invariants still hold before a release.

### 1.2 The product is not

- A generic payment webhook simulator.
- A general-purpose chaos engineering platform.
- A production reconciliation or accounting system.
- A fraud-scoring product trained on consumer behavior.
- A replacement for ZAP, Burp, Nuclei, API gateways or financial ledgers.
- An autonomous agent allowed to execute arbitrary model-generated requests.

### 1.3 Proposed differentiating wedge

The potential differentiation is the integration of:
- A canonical B2B travel transaction model.
- Multi-party actors: agency/sub-agent, aggregator, supplier and payment provider.
- Adversarial timing and event-delivery scenarios.
- Travel-aware financial and operational invariants.
- Security-oriented findings with reproducible evidence and fix verification.
- Deploy-triggered selection of tests affected by a code or API change.

This wedge is a hypothesis to test. The individual capabilities are not unique.

### 1.4 Initial customer and user hypotheses

- **Primary internal user:** application security or product security engineer at a B2B travel platform.
- **Daily technical user:** backend / integration engineer responsible for supplier and payment workflows.
- **Economic sponsor hypothesis:** Head of Engineering, VP Technology or security leadership.
- **Potential co-sponsor:** financial operations, where the issue concerns preventable leakage.

These are hypotheses, not validated buyer interviews.

---

## 2. Problem Model

### 2.1 System actors

1. **Agency / sub-agent:** initiates a booking, uses wallet or credit, views vouchers and requests cancellation.
2. **Travel aggregator:** owns workflow orchestration, access control, quote/markup rules, wallet ledger and booking state.
3. **Supplier / bed bank / GDS:** owns or confirms the underlying reservation and may send asynchronous updates.
4. **Payment provider:** authorizes/captures payments and sends payment/refund events.
5. **Crosswire:** operates only in an expressly authorized, isolated test environment and observes all synthetic state.

### 2.2 Canonical hotel transaction lifecycle

Use a state machine / saga graph, not a strict DAG. A workflow can branch, wait, retry, compensate or enter a reconciliation state.

`SEARCHED → QUOTED → HELD → BOOKING_PENDING → SUPPLIER_CONFIRMED → PAYMENT_CONFIRMED → VOUCHER_ISSUED`

Alternative and compensation states include:

- `SUPPLIER_UNKNOWN` — request outcome is ambiguous.
- `PAYMENT_PENDING` / `PAYMENT_FAILED`.
- `CANCEL_REQUESTED` / `CANCEL_PENDING` / `CANCELLED`.
- `REFUND_PENDING` / `PARTIALLY_REFUNDED` / `REFUNDED`.
- `EXPIRED` / `FAILED` / `MANUAL_RECONCILIATION_REQUIRED`.

Exact allowed transitions depend on the configured business contract. Crosswire must not hardcode a universal travel lifecycle and assume every supplier works identically.

### 2.3 Failure classes to test

- Ambiguous supplier outcome after a timeout or connection reset.
- Repeated or concurrent delivery of the same signed webhook event.
- Out-of-order supplier or payment callbacks.
- Concurrent wallet/credit reservations and booking confirmations.
- Cancellation racing with supplier confirmation or ticket/voucher issuance.
- Duplicate, excessive or unauthorized refunds.
- Payment / supplier confirmation prerequisites bypassed.
- Fare, markup, currency or quote integrity tampering.
- Cross-agency object access and sub-agent privilege escalation.
- Deprecated, undocumented or unsafe supplier/webhook endpoints.
- Unsafe server-side fetching of attacker-controlled supplier or webhook URLs (SSRF), tested only against controlled local targets.

---

## 3. Competitor and Adjacent-Tool Reality

### 3.1 Verified overlap

| Product / category | Publicly documented overlap | Crosswire implication |
|---|---|---|
| **MockCard** | CI runs for payment webhook scenarios including duplicate, late, latency and abandoned-payment events; concurrent duplicate delivery; SDK assertions against application database outcomes. | Direct adjacent competitor for payment-webhook chaos. Crosswire cannot claim duplicate-webhook CI testing or outcome assertions are novel. |
| **Aptori** | API security testing / semantic testing and business-logic workflows are in its public positioning. | Do not claim all existing DAST is endpoint-only or incapable of workflow logic. Validate product limits with controlled hands-on research where possible. |
| **Escape** | Public docs describe business-logic-aware DAST, agentic attackers, exploit validation and integrations. | Generic business-logic-aware API testing is not a defensible novelty claim. |
| **Toxiproxy** | Network fault injection such as latency and connection disruption. | A useful baseline for transport faults; it does not itself provide Crosswire's travel-domain oracle. |
| **Chaos Mesh / Gremlin** | Distributed and infrastructure fault injection. | Adjacent chaos tools; benchmark what can be composed rather than dismissing them as incapable. |
| **Postman / Pact / WireMock** | API collections, contract tests and controllable service mocks. | Can implement parts of the workflow. Compare against a realistic custom test suite, not a strawman. |
| **Cointab / reconciliation vendors** | Financial / operational reconciliation. | Adjacent post-transaction controls; validate timing and scope product by product before claiming they are purely post-facto. |
| **Wallarm and API discovery vendors** | Traffic-versus-spec inventory, shadow and zombie API discovery. | API inventory is a required capability, not a product moat. |

### 3.2 Claims prohibited until proven

Do not say:
- “No competitor can test asynchronous workflows.”
- “Aptori/Escape are stateless or purely synchronous.”
- “MockCard cannot detect double processing.”
- “ZAP/Nuclei always miss these cases.”
- “Crosswire is the world's first / only / unique product.”
- “Zero false positives.”

Use narrower language: “Crosswire is designed to test travel-specific, multi-party transaction invariants that are not demonstrated by the generic scanner baseline in our measured testbed.”

### 3.3 Commercial validation still required

Before making a commercial uniqueness claim:
1. Review vendor documentation and product demos for each material capability.
2. Ask at least 5–10 travel engineering / security practitioners how they test supplier timeout, retry, duplicate callback, wallet race and cancellation-race failures.
3. Ask what they currently use and what remains painful.
4. Obtain a design-partner conversation or written willingness to trial.
5. Test whether a travel-specific test model saves meaningful engineering effort versus combining MockCard, Toxiproxy, WireMock, API tests and custom assertions.

---

## 4. Product Architecture

```text
CI / PR / Deploy Event or Manual Test Run
                  │
                  ▼
        Change & Scope Detector
                  │
                  ▼
  API + Event Inventory / Contract Registry
      OpenAPI routes + webhook event catalog
                  │
                  ▼
      Travel Domain / Saga Model
                  │
                  ▼
    Scenario Selection (rules + optional AI)
                  │
                  ▼
     Safety Policy / Scope Guard
                  │
                  ▼
    Controlled Adversarial Executor
       ┌──────────┼───────────┐
       ▼          ▼           ▼
  Agency App  Supplier Mock  Payment Mock
       └──────────┼───────────┘
                  ▼
      Evidence & Invariant Engine
                  │
                  ▼
     Finding / Ledger Diff / Report
                  │
                  ▼
        Patch Verification / Retest
```

### 4.1 Module responsibilities

**A. Change detector**
- Ingest a signed local CI webhook or a manually selected commit range.
- Identify changed routes, schemas, handlers and event consumers.
- Expand the test scope through known dependencies and state-transition edges. Do not test only the directly changed endpoint.
- Record commit SHA, build ID, target environment and test policy.

**B. Inventory and contract registry**
- Extract routes from the FastAPI mock, OpenAPI specifications, source metadata and controlled traffic logs.
- Track webhook/event handlers separately from synchronous HTTP routes.
- Export OpenAPI 3.1 for HTTP operations.
- Keep an event catalog; optionally add AsyncAPI later if the project needs formal event-channel contracts.

**C. Travel domain model**
- Define entities: Agency, SubAgent, Quote, BookingIntent, SupplierReservation, Payment, WalletAccount, LedgerEntry, WebhookEvent, Voucher, Cancellation and Refund.
- Define allowed transitions, preconditions, side effects, ownership and compensation rules.
- Make supplier-specific behavior configurable through adapters.

**D. Scenario planner**
- Start with deterministic, versioned scenario templates.
- Use rules to map changed operations to relevant tests.
- Optional AI may classify route meaning, map business terms to known templates and draft a report.
- AI must not write arbitrary executable requests, override safety policy or choose targets outside the pre-authorized scenario library.

**E. Fault injector and executor**
- Operate only against local synthetic services in the MVP.
- Inject delay, lost response after commit, duplicate event delivery, concurrent event delivery and event reordering at explicit boundaries.
- Use synthetic Agency Alpha, Agency Beta, Sub-Agent Alpha-1 and synthetic admin identities.
- Store trace IDs and correlation keys across each actor.

**F. Invariant and evidence engine**
- Query the synthetic database / ledger after each scenario.
- Compare observed results with a versioned expected-outcome oracle.
- Distinguish confirmed vulnerability, failed test, inconclusive outcome and expected transient state.
- Preserve request/response evidence with secrets and personal data redacted.

**G. Reporting and retesting**
- Produce severity, affected transaction, violated invariant, expected versus actual result, replay steps, trace IDs and remediation guidance.
- Retest the same scenario against a patched build.
- Close a finding only when the invariant passes and legitimate control flows still work—not merely because an HTTP status is 403.

---

## 5. Correct Transaction Invariants

Crosswire must not compare unlike quantities (for example, wallet currency with ticket count). Every invariant is scoped by transaction, currency, account and time/state boundary.

### 5.1 Wallet balance derived from posted ledger events

For an agency account and currency `c`:

`closing_balance(c) = opening_balance(c) + Σ signed_posted_ledger_entries(c)`

The sign convention must be defined once for the ledger. Pending authorizations and holds are not posted debits. Available credit/funds must account for active holds separately.

For a credit-limit model:

`available_credit = credit_limit - posted_outstanding_exposure - active_reservations`

The exact formula depends on whether the wallet is prepaid, postpaid or hybrid. The MVP will model one explicit policy rather than pretending every agency wallet works identically.

**Violation:** balance or available credit differs from the ledger-derived expected value, or parallel commits exceed the configured credit policy.

### 5.2 Booking-intent / supplier-reservation uniqueness

For one unique supplier operation intent `I`:

`count(distinct confirmed_supplier_reservations linked to I) <= expected_reservation_count(I)`

For the MVP, `expected_reservation_count` is one for a single-room booking intent. Multiple rooms, split supplier inventory or intentionally separate reservations must be represented as distinct child intents.

**Violation:** one logical intent creates more confirmed supplier reservations than its declared cardinality after retries or ambiguous responses.

### 5.3 Webhook idempotency

For one provider-scoped event ID `E`:

`count(posted_ledger_effects caused by E) <= 1`

The event may be delivered many times. Processing should be idempotent at the event / business-effect boundary. A valid duplicate delivery is not automatically an attack; the vulnerability is repeated side effects.

Also test signature verification, event freshness / replay policy and binding between the signed payload and event identity. A replay of a valid event is different from an invalidly signed forged event.

### 5.4 Ticket / voucher entitlement

`voucher_issued(B) => supplier_confirmed(B) AND payment_or_wallet_obligation_satisfied(B)`

The second predicate must reflect the platform's configured payment methods: captured/settled payment, approved agency credit debit, or another explicitly supported commitment. Do not hardcode a single payment model.

### 5.5 Refund boundedness

For booking `B` and currency `c`:

`cumulative_refunds(B,c) <= refundable_entitlement(B,c)`

`refundable_entitlement` is computed from the original captured/posted amount, supplier cancellation penalty, applicable fees, taxes and approved adjustments under the configured policy. FX conversions must be represented explicitly; do not net unlike currencies.

### 5.6 State-transition validity

Each transition must satisfy:
- Correct actor and tenant permissions.
- Required prior state and preconditions.
- Optimistic concurrency / version check where required.
- Valid compensation or reconciliation path for late events.
- No terminal-state regression unless a documented business transition permits it.

A delayed callback may create a temporary `PENDING_RECONCILIATION` state. It is not necessarily a vulnerability. Crosswire reports a security finding only when the configured policy is violated and evidence proves an unauthorized or financially harmful effect.

### 5.7 Double-entry accounting check (optional advanced check)

For every journal batch and currency, debits must equal credits according to the ledger's accounting convention. This is a ledger integrity check; it is not a claim that supplier gross charge equals agency net debit.

---

## 6. MVP Scenarios and Expected Evidence

### Scenario 1 — Ambiguous supplier timeout / duplicate reservation

**Setup:** The supplier mock persists a booking, then deliberately drops the response before the aggregator receives it.

**Attack sequence:** The client/aggregator experiences an ambiguous outcome and exercises its configured retry behavior using the same business intent.

**Expected secure behavior:** The platform reconciles the existing supplier operation or reuses the correct idempotency/reference key; it does not create a second reservation for the same single-room intent.

**Finding evidence:** Same logical intent, two distinct confirmed supplier reservation IDs, and correlation traces proving the second came from a retry.

**Important:** Supplier idempotency contracts differ. Use a configurable supplier adapter. Do not assume every provider accepts the same idempotency header or permits blind replay.

### Scenario 2 — Duplicate refund webhook

**Setup:** The payment/supplier mock signs a synthetic refund event.

**Attack sequence:** Deliver the same event ID concurrently or repeatedly, with valid signatures, to model at-least-once delivery/retry.

**Expected secure behavior:** Multiple deliveries produce one financial side effect and one refund state transition.

**Finding evidence:** One event ID, N deliveries, more than one posted refund/credit effect, ledger journal IDs and resulting balance difference.

### Scenario 3 — Wallet concurrency / double spend

**Setup:** Agency has a known credit limit or prepaid balance. Two simultaneous bookings each fit individually but not collectively.

**Attack sequence:** Send both booking confirmations concurrently at a controlled barrier.

**Expected secure behavior:** Atomic reservation/ledger behavior permits only the operations supported by available funds/credit. No lost update or overdraft beyond the configured policy.

**Finding evidence:** Both bookings accepted despite combined outstanding exposure exceeding the configured credit ceiling, or a ledger/balance mismatch.

### Scenario 4 — Cancel versus delayed supplier confirmation

**Setup:** Supplier accepts a booking but delays the confirmation callback. Agency requests cancellation while the aggregator sees an ambiguous state.

**Attack sequence:** Deliver the late confirmation after the cancellation path begins, then deliver any cancellation response in controlled permutations.

**Expected secure behavior:** Platform follows its configured compensation/reconciliation policy. It must not simultaneously grant a refund and issue an active voucher unless the state model explicitly permits and accounts for it.

**Finding evidence:** Contradictory terminal states, duplicate financial side effect, voucher issuance without required entitlement, or a confirmed unauthorized state transition.

### Mandatory security controls retained from the original challenge

At minimum, preserve test templates for:
- Cross-agency BOLA/IDOR and sub-agent role/privilege escalation.
- Fare / markup / currency / quote tampering.
- Skipping payment or required approval before ticket/voucher issuance.
- Cancel-rebook-refund abuse.
- Supplier/webhook endpoint security, including controlled SSRF checks where explicitly in scope.
- Deprecated partner endpoint abuse and API inventory (active / shadow / zombie).

These can be implemented as a compact security scenario pack alongside the four headline transaction scenarios. The project must not omit an explicit challenge requirement merely because the new pivot is more interesting.

---

## 7. API and Event Inventory Definitions

Use separate, evidence-backed labels:

- **Active/documented:** Present in the current approved API contract and observed in an approved baseline or test traffic.
- **Shadow:** Observed in live/approved traffic but absent from all applicable current API specifications.
- **Zombie:** Present in a prior API specification, removed or deprecated in the current specification, but still observed receiving traffic.
- **Dormant/unobserved candidate:** Present in code, runtime route metadata or a previous contract but no traffic observed in the defined lookback window. This is not automatically a zombie API.

A zero-traffic interval alone is insufficient to classify an endpoint as a zombie. Store evidence source, specification version, host/application scope, last-seen time and confidence.

---

## 8. AI, Determinism and Safety

### 8.1 Deterministic responsibilities

- Fault injection and timing.
- Request construction from approved templates.
- Scope checks and rate limits.
- Ledger calculations and invariant assertions.
- Event deduplication checks.
- State-transition checks.
- Severity evidence rules.
- Retest pass/fail decision.

### 8.2 Optional AI responsibilities

- Classify endpoint purpose from approved metadata.
- Map changed routes/events to a pre-approved scenario catalog.
- Explain a confirmed invariant violation in developer-friendly language.
- Draft a remediation suggestion for human review.

### 8.3 Safety policy

- Default target is localhost / isolated container network only.
- No real supplier, airline, hotel or payment credentials.
- No production testing in the hackathon MVP.
- Fixed allowlist and explicit target identity.
- Deny outbound internet egress from fault-injection workers unless a specific approved test requires it.
- Rate limits, concurrency ceilings, maximum scenario duration and emergency stop.
- Synthetic identities and records only.
- Secrets never appear in logs, reports or reproduction commands.
- Reproduction instructions must point to the local mock environment and use synthetic credentials.
- Arbitrary model-generated URLs, shell commands and payloads are prohibited.

---

## 9. Technical Stack and Repository Layout

### 9.1 Initial stack

| Layer | Choice | Reason |
|---|---|---|
| Target services | Python 3.11+ / FastAPI | Fast, inspectable mock APIs |
| Async client | HTTPX | Async requests and explicit timeout control |
| Database | SQLite for single-process demo; transactional DB tests for concurrency | Lightweight MVP; ensure tests use real transaction semantics |
| Fault injection | In-process scenario hooks first; Toxiproxy optional for network-level cases | Reproducible commit-after-drop fault |
| Test runner | Pytest + asyncio | Repeatable scenario tests |
| Property/model tests | Hypothesis / explicit state-machine model | Generate transition and race permutations |
| Dashboard | Streamlit | Fast hackathon console |
| AI provider | Pluggable adapter; optional Gemini model | Avoid hard-coupling the test engine to one model |
| HTTP API contract | OpenAPI 3.1 | REST inventory |
| Event contract | Versioned JSON Schema; AsyncAPI only if useful | Webhook/event documentation |

### 9.2 Proposed repository

```text
crosswire/
├── README.md
├── MASTER_PLAN_V2.md
├── requirements.txt
├── target_platform/
│   ├── app.py
│   ├── auth.py
│   ├── booking_service.py
│   ├── wallet_service.py
│   ├── ledger.py
│   ├── state_machine.py
│   ├── mock_supplier.py
│   ├── mock_payment.py
│   ├── webhook_consumer.py
│   ├── database.py
│   └── seed_data.py
├── engine/
│   ├── deploy_watcher.py
│   ├── change_mapper.py
│   ├── api_inventory.py
│   ├── event_inventory.py
│   ├── travel_model.py
│   ├── scenario_catalog.py
│   ├── scenario_planner.py
│   ├── executor.py
│   ├── safety_policy.py
│   ├── invariant_engine.py
│   ├── evidence_store.py
│   ├── reporter.py
│   └── retest.py
├── scenarios/
│   ├── ambiguous_timeout.yaml
│   ├── duplicate_refund_webhook.yaml
│   ├── wallet_concurrency.yaml
│   ├── cancel_late_confirmation.yaml
│   ├── cross_agency_access.yaml
│   ├── fare_tampering.yaml
│   ├── payment_step_skip.yaml
│   └── api_inventory.yaml
├── ui/
│   └── dashboard.py
└── tests/
    ├── test_supplier_timeout.py
    ├── test_webhook_replay.py
    ├── test_wallet_concurrency.py
    ├── test_cancel_callback_race.py
    ├── test_authorization.py
    ├── test_fare_integrity.py
    ├── test_api_inventory.py
    ├── test_safety.py
    └── run_demo.py
```

---

## 10. Hackathon Alignment

The following mapping preserves the rubric described in the challenge materials. It is a coverage plan, not a predicted score.

| Criterion | Weight | Crosswire 2.0 evidence | Status before implementation |
|---|---:|---|---|
| Valid findings & severity | 30% | Confirmed duplicate booking, duplicate refund side effect, wallet policy bypass, workflow/auth/price exploit evidence | Planned; must prove in target |
| Precision & noise control | 20% | Explicit transaction oracle, expected-state model, control-flow positive tests, repeat runs | Planned; cannot claim zero FP |
| Safety & scope discipline | 20% | Isolated sandbox, local allowlist, synthetic data, bounded faults, kill switch | Planned; must test safety controls |
| Speed after change | 10% | CI/deploy event maps changed code to impacted routes, events and scenario templates | Planned; timing benchmark required |
| Report quality & actionability | 10% | Reproduction steps, traces, ledger diff, impact, fix guidance and retest evidence | Planned |
| Discovery & API inventory | 10% | Route/event inventory, current/prior contract comparison, Active/Shadow/Zombie/Dormant evidence | Planned |

**Important:** Transaction testing does not excuse missing explicit security requirements such as sub-agent privilege escalation, fare tampering, supplier/webhook endpoint risks or zombie/shadow inventory. Keep those within the security scenario catalog.

---

## 11. Benchmark Plan — No Fabricated Results

### 11.1 Baselines

Run the same local target and record real outcomes for:
- OWASP ZAP.
- Nuclei with documented relevant templates.
- Postman collection with conventional assertions.
- MockCard for the payment-webhook scenarios it publicly documents, subject to product access and setup.
- Toxiproxy for transport-level timeout/drop scenarios, combined with the same application oracle.
- Crosswire 2.0.

A baseline may require custom configuration. Record setup effort and code authored; do not configure the baseline deliberately poorly.

### 11.2 Measures

- True positives against planted ground truth.
- False positives against known-safe controls.
- False negatives by scenario class.
- Time from trigger to confirmed finding.
- Setup/configuration effort.
- Reproducibility of the finding.
- Ability to correlate supplier reservation IDs, event IDs, wallet ledger entries and booking state.
- Safety policy violations (must be zero in the test suite).

### 11.3 Reporting rules

- Do not publish anticipated outcomes as benchmark results.
- Do not state ZAP, Nuclei, Postman or MockCard “missed” a scenario until the exact configuration has been run and results saved.
- Distinguish tool capability from the result of one tested configuration.
- Preserve command lines, versions, templates, setup files, test data and raw outputs.

---

## 12. MVP Boundaries and Demo Plan

### 12.1 MVP scope

Focus on **hotel booking and refund workflows only**. Do not model flights, cars, rail, cruise or every supplier protocol.

The first working release must include:
1. Agency Alpha, Agency Beta, restricted sub-agent and synthetic platform admin.
2. Mock aggregator, mock hotel supplier and mock payment provider.
3. Transaction state model and persisted ledger.
4. Four headline adversarial transaction scenarios.
5. A compact security pack for cross-agency/sub-agent authorization, fare tampering and payment step-skipping.
6. Active / Shadow / Zombie API inventory plus Dormant candidates.
7. Deploy/change-triggered scenario selection.
8. Evidence report and patch retest.
9. Dashboard with one-click seed reset and repeatable demo.

### 12.2 Three-minute judge demo

**0:00–0:30 — Surface and scope**
- Show the local hotel transaction actors and API/event inventory.
- Show documented, shadow, zombie and dormant-candidate labels with evidence.

**0:30–0:55 — Change trigger**
- Trigger a simulated commit/deploy changing booking retry or refund handling.
- Show the impacted transaction path and selected tests.

**0:55–1:50 — Exploit demonstration**
- Run supplier commit-then-timeout.
- Show naïve retry creates two reservations for one intent.
- Show the invariant violation and trace IDs.

**1:50–2:30 — Fix and retest**
- Apply the idempotency/reconciliation fix.
- Rerun the same scenario.
- Show one reservation, no duplicate ledger effect and a passed invariant.

**2:30–3:00 — Security evidence**
- Show one additional webhook replay or sub-agent authorization finding.
- Display the report, expected/actual result and remediation.

Do not promise a specific number of seconds until measured on the demo hardware.

---

## 13. Milestones and Acceptance Tests

### M0 — Challenge and source verification
- Confirm final hackathon rules, allowed test targets, expected artifacts and demo timing.
- Map every explicit in-scope class to one scenario or documented limitation.

### M1 — Synthetic travel platform
- Implement actors, hotel quote/hold/booking/cancel/refund, supplier and payment mocks.
- Persist bookings, event deliveries and ledger postings.
- Seed reproducible vulnerable and fixed modes.

**Acceptance:** A human can complete a valid booking, cancel it and issue a bounded refund in both modes.

### M2 — Invariant oracle
- Implement booking-intent uniqueness, webhook side-effect uniqueness, wallet/credit policy, ticket entitlement, refund bounds and state transition checks.

**Acceptance:** Each planted defect fails its specific invariant; known-safe control cases pass.

### M3 — Adversarial executor
- Implement deterministic timeout-after-commit, duplicate event, concurrent wallet operation and delayed callback scenarios.

**Acceptance:** Each scenario is repeatable from a clean seed and produces traceable evidence.

### M4 — Security scenario pack and inventory
- Implement sub-agent/tenant authorization, fare tampering, step-skipping and API inventory scenarios.

**Acceptance:** Role matrix tests distinguish authorized from unauthorized access; API classification is backed by code/spec/traffic evidence.

### M5 — Change selection, reporting and retest
- Map diff to relevant routes/events and run selected tests.
- Generate an evidence report and retest on a patched build.

**Acceptance:** Same scenario fails on vulnerable build and passes on fixed build, while legitimate positive controls continue to work.

### M6 — Benchmark and demo hardening
- Run the baseline tools with saved configurations and results.
- Test reset, repeatability, emergency stop and offline demo behavior.

**Acceptance:** All benchmark claims are traceable to raw artifacts; demo can be reset and replayed reliably.

---

## 14. Risks and Kill Criteria

| Risk | Why it matters | Mitigation / kill criterion |
|---|---|---|
| MockCard overlap | Payment webhook chaos is already a documented commercial feature | Differentiate only if supplier-side booking saga, travel-specific oracle and security findings prove measurable value. If not, narrow further or stop. |
| DAST overlap | Business-logic workflow testing is already an established AppSec product area | Benchmark actual workflows and avoid generic DAST positioning. |
| QA/product-category confusion | The tool could be perceived as ordinary integration or chaos testing | Every scenario must prove a security/financial invariant violation, attacker-controlled abuse path or authorization failure. |
| False positives | Eventual consistency and valid compensation can resemble an inconsistency | Define temporal windows, allowed transitions, control cases and manual/inconclusive states. |
| Incorrect supplier assumptions | Idempotency behavior differs across suppliers | Implement provider-specific contracts; never assume all suppliers support a common header or safe replay. |
| Overbroad financial equation | Wallet, supplier charge and ticket count are different concepts | Use per-currency ledger postings and transaction-specific expected values. |
| Scope bloat | Too many products and suppliers are impossible for a hackathon | Hotel-only mock platform; one supplier adapter; four headline scenarios. |
| Fragile concurrency demo | SQLite or in-memory state may hide production race behaviors | Use explicit transactions/locking and test concurrency with multiple workers; document demo limitations. |
| Unsupported benchmark claims | A comparison table can misrepresent competitors | No results until controlled runs are complete. |
| Weak market validation | Technical feasibility does not prove willingness to pay | Conduct practitioner interviews and obtain a design-partner trial before commercial investment. |

### Stop / pivot triggers

Stop or narrow the product if:
- A well-configured existing product reproduces the full travel-specific scenario and invariant oracle at similar setup cost.
- The team cannot create a stable, deterministic vulnerable/fixed testbed.
- The invariant engine cannot distinguish normal eventual-consistency windows from exploitable business violations.
- The required hackathon DAST/security scope cannot be credibly demonstrated.
- No travel engineering practitioner confirms the problem is costly enough to prioritize.

---

## 15. First Five Validation Experiments

1. **Timeout semantics:** supplier persists the reservation then drops the response; verify the retry path and detect duplicate reservation IDs for one intent.
2. **Webhook replay:** deliver one valid signed refund event ID concurrently; verify exactly one ledger side effect and reject invalid signatures.
3. **Wallet race:** run simultaneous bookings at the credit boundary; verify atomic reservation and available-credit rules.
4. **Late callback:** run cancellation and supplier confirmation permutations; verify the configured compensation/reconciliation policy.
5. **Benchmark and gap test:** configure MockCard for payment webhook duplicate/late cases, Toxiproxy for network drops, and ZAP/Nuclei/Postman where relevant; compare setup effort and measurable outcomes against Crosswire.

Each experiment must save test inputs, build ID, raw requests/events, traces, database snapshots, expected result, actual result and repeat count.

---

## 16. Open Questions Before Commercialization

- Which travel supplier protocols and idempotency contracts matter most to target customers?
- Which booking / refund event sources are accessible in staging?
- What temporal guarantees do platforms expect for each transaction state?
- How do customers express supplier cancellation penalties, taxes, FX and markup rules?
- Can Crosswire integrate with existing CI systems without production credentials?
- Is the buyer AppSec, engineering, QA or financial operations, and who owns the budget?
- Will teams buy a dedicated product or prefer a reusable SDK / scenario library?
- What specific workflow can Crosswire detect that a realistic composition of existing tools does not detect economically?

---

## 17. Source Register and Research Caveats

The following primary/public documentation supports selected technical statements. Product capabilities can change; re-check before external publication or a commercial decision.

1. MockCard CI runs and monitoring: https://www.mockcard.io/docs/ci
2. MockCard API / chaos scenarios: https://www.mockcard.io/docs
3. Escape documentation: https://docs.escape.tech/documentation/
4. Hotelbeds hotel API best practices: https://developer.hotelbeds.com/documentation/hotels/knowledge-base/best-practices/
5. Booking.com duplicate request / timeout handling: https://developers.booking.com/demand/docs/support/error-handling/http-4xx-scenarios
6. Travel Curious booking idempotency: https://docs.travelcurious.com/operator-api-v2-booking-system
7. Expedia Group booking retries and ambiguous result handling: https://developers.expediagroup.com/rapid/flights/reference/handle-booking-reqs?locale=en_US
8. Wallarm shadow/zombie API definitions: https://docs.wallarm.com/api-discovery/rogue-api/
9. OWASP API Security Top 10: https://owasp.org/API-Security/editions/2023/en/0x11-t10/
10. MITRE CWE-841 Improper Enforcement of Behavioral Workflow: https://cwe.mitre.org/data/definitions/841.html
11. MITRE CWE-675 Duplicate Operations on Resource: https://cwe.mitre.org/data/definitions/675.html
12. MITRE CWE-294 Authentication Bypass by Capture-replay: https://cwe.mitre.org/data/definitions/294.html

### Evidence caveats
- Hotelbeds' timeout recommendation is provider-specific; it is not a universal travel API timeout standard.
- Booking.com, Expedia and Travel Curious documentation illustrates provider-specific handling; it does not prove all suppliers behave identically.
- MockCard is a direct adjacent competitor for payment webhook testing and CI assertions. Its public documentation does not, by itself, establish whether it supports a complete travel supplier saga.
- Competitor limitations in this plan are intentionally not stated as absolutes unless supported by public documentation or a measured test.
- Benchmark outcomes, financial loss estimates, market size and customer willingness to pay remain unverified.

---

## Final Recommendation

**Build a bounded Crosswire 2.0 security prototype, not a universal transaction platform.**

The winning technical thesis is: *a travel-specific, adversarial, multi-party transaction test harness that proves security and financial invariant violations before deployment, with evidence and retest.*

The next action is M0/M1: validate the original challenge deliverables, then construct the isolated hotel booking testbed. Do not start with the dashboard or an LLM agent. First prove one vulnerable and one fixed transaction path end to end.

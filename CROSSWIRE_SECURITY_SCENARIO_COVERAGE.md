# CROSSWIRE 2.0 — SECURITY SCENARIO COVERAGE
**Technical Specification & Forensic Dossiers for 10 Distributed Transaction Scenarios**

---

## Scenario 1: SCN-TIMEOUT-001 — Ambiguous Supplier Commit-Then-Timeout

- **Identifier:** `SCN-TIMEOUT-001`
- **Finding ID:** `CW-SEC-M2-001`
- **Severity:** CRITICAL
- **CWE References:**
  - CWE-675: Multiple Operations on Resource in Single-Operation Context
  - CWE-841: Improper Enforcement of Behavioral Workflow Logic
- **Target Invariant:** `INV-5.2: Booking Intent Uniqueness`
  $$\forall \text{ intent\_id}, \quad \text{Count}(\text{supplier\_reservations}) \le 1$$
- **Threat Model:**
  When a travel aggregator dispatches a room reservation to an external supplier, transient network partitions, packet loss, or supplier gateway timeouts may occur *after* the supplier has committed the room inventory and charged the aggregator. If the aggregator naively re-attempts booking with a new transaction without supplier reconciliation, two physical room reservations are created for one logical guest booking, leaving the aggregator with stranded, unrecouped inventory costs.
- **Fault Injection Mechanics:**
  `TargetExecutionAdapter.simulate_supplier_timeout(intent_id)` forces a socket timeout or synthetic connection drop on the HTTP response stream immediately after the supplier's internal database commit finishes.
- **Vulnerable Execution Path:**
  1. Aggregator receives booking intent `INTENT-101`.
  2. Aggregator dispatches reservation request to supplier mock.
  3. Supplier writes reservation `SUP-RES-01` to its database.
  4. Connection drops before aggregator receives HTTP 200 OK.
  5. Aggregator's blind retry loop executes, generating a second request without idempotency keys.
  6. Supplier writes a second reservation `SUP-RES-02` to its database.
  7. Invariant evaluation checks `supplier_db`: discovers 2 active reservations for intent `INTENT-101`.
  8. Invariant `INV-5.2` fails. Vulnerability confirmed.
- **Fixed / Patched Execution Path:**
  1. Aggregator generates a deterministic `supplier_request_id` derived from `hash(intent_id + attempt)`.
  2. Aggregator experiences timeout.
  3. Before re-dispatching, aggregator calls supplier reconciliation endpoint `GET /supplier/reservations?idempotency_key=...`.
  4. Supplier returns existing reservation `SUP-RES-01`.
  5. Aggregator attaches `SUP-RES-01` to guest booking and terminates retry loop.
  6. Invariant evaluation checks `supplier_db`: discovers exactly 1 reservation. Retest passes.
- **Ground-Truth Database Observations:**
  - `supplier_db.reservations`: `COUNT(*) WHERE intent_id = :id` (Expected: 1, Actual Vulnerable: 2).
  - `aggregator_db.bookings`: `supplier_reference` matches `SUP-RES-01`.

---

## Scenario 2: SCN-WEBHOOK-001 — Duplicate Refund Webhook Replay

- **Identifier:** `SCN-WEBHOOK-001`
- **Finding ID:** `CW-SEC-M2-002`
- **Severity:** HIGH
- **CWE References:**
  - CWE-675: Multiple Operations on Resource
  - CWE-799: Improper Control of Interaction Frequency
- **Target Invariant:**
  - `INV-5.3: Webhook Idempotency` (Duplicate webhook deliveries produce exactly one state change)
  - `INV-5.5: Refund Boundedness` ($\sum \text{Refunds} \le \text{Captured Payment}$)
- **Threat Model:**
  Payment gateways operate under at-least-once delivery guarantees, re-transmitting webhook events upon receiving non-200 responses or network timeouts. If a webhook handler does not perform atomic deduplication, duplicate refund events credit an agency or customer wallet multiple times, causing financial loss.
- **Fault Injection Mechanics:**
  `TargetExecutionAdapter.dispatch_webhook(event_payload)` transmits the identical webhook body and event ID (`evt_refund_99`) 3 times in rapid succession.
- **Vulnerable Execution Path:**
  1. Payment provider emits refund webhook for 12,000 INR.
  2. Aggregator webhook consumer receives event 1, inserts ledger credit of 12,000 INR into `ledger_records`.
  3. Payment provider re-delivers event 2 due to simulated ack timeout.
  4. Webhook consumer processes event 2, inserts second ledger credit of 12,000 INR.
  5. Webhook consumer processes event 3, inserts third ledger credit of 12,000 INR.
  6. Cumulative ledger credit = 36,000 INR (exceeding initial 15,000 INR booking payment by 21,000 INR).
  7. Invariants `INV-5.3` and `INV-5.5` fail. Vulnerability confirmed.
- **Fixed / Patched Execution Path:**
  1. Webhook consumer opens transaction with `BEGIN IMMEDIATE`.
  2. Consumer inserts event record into `processed_webhooks (event_id, source, processed_at)` with a unique constraint on `(source, event_id)`.
  3. Events 2 and 3 trigger `sqlite3.IntegrityError` (Unique Constraint Violation) and are discarded with HTTP 200 OK without ledger mutation.
  4. Cumulative ledger credit = 12,000 INR. Retest passes.
- **Ground-Truth Database Observations:**
  - `payment_db.ledger_records`: `COUNT(*) WHERE intent_id = :id` (Expected: 1, Actual Vulnerable: 3).
  - `payment_db.processed_webhooks`: exactly 1 entry for `evt_refund_99`.

---

## Scenario 3: SCN-OUTBOX-001 — Multi-Supplier Coordinated Outbox

- **Identifier:** `SCN-OUTBOX-001`
- **Severity:** DEFENSIVE / MEDIUM
- **CWE References:**
  - CWE-841: Improper Enforcement of Behavioral Workflow Logic
- **Target Invariant:**
  - `Parent Stay Coverage`: All legs of a multi-room stay must either simultaneously confirm or uniformly compensate.
  - `Monotonic Fencing Tokens`: Outbox worker leases must increment monotonically to prevent split-brain execution.
- **Threat Model:**
  Multi-room family bookings often require splitting rooms across distinct suppliers (e.g. Expedia for Room 1, Hotelbeds for Room 2). If one supplier confirms and the second fails or times out, an uncoordinated dispatcher can leave the guest with half a hotel booking while charging full price.
- **Fault Injection Mechanics:**
  `OutboxEngineDemonstration` injects a commit-then-timeout fault into the Hotelbeds leg while allowing the Expedia leg to succeed.
- **Vulnerable Execution Path:**
  Aggregator blind-retries Hotelbeds outbox message without fencing tokens, leading to duplicate outbox dispatch and orphaned room reservations.
- **Fixed / Patched Execution Path:**
  Aggregator coordinates via durable outbox table with distributed lease fencing (`worker_lease_token`). When Hotelbeds fails repeatedly, the saga coordinator executes compensating cancellation against Expedia, ensuring 0 orphaned reservations and complete refund.
- **Ground-Truth Database Observations:**
  - `aggregator_db.outbox_messages`: state transitions from `PENDING` -> `LEASED` -> `COMMITTED` or `COMPENSATED`.

---

## Scenario 4: SCN-WALLET-001 — Agency Wallet Concurrency & Double-Spend

- **Identifier:** `SCN-WALLET-001`
- **Finding ID:** `CW-SEC-M2-201`
- **Severity:** CRITICAL
- **CWE References:**
  - CWE-362: Concurrent Execution using Shared Resource with Improper Synchronization (Race Condition)
  - CWE-841: Improper Enforcement of Behavioral Workflow Logic
- **Target Invariant:**
  - `INV-5.1: Wallet Balance Conservation and Active Hold Reservation Bounds` ($\text{Balance} \ge 0$ and $\text{Balance} - \sum \text{Active Holds} \ge 0$)
- **Threat Model:**
  B2B travel sub-agents book hotel inventory against a pre-funded agency credit limit. An attacker dispatches concurrent booking hold requests simultaneously. Under a Time-of-Check to Time-of-Use (TOCTOU) flaw, both threads read sufficient credit before either thread writes the deduction, driving the wallet into an unauthorized negative balance (overdraft).
- **Fault Injection Mechanics:**
  `asyncio.gather()` dispatches two concurrent wallet hold requests of 7,000 INR against an agency wallet holding exactly 10,000 INR.
- **Vulnerable Execution Path:**
  1. Agency wallet funded with 10,000 INR.
  2. Request 1 reads balance: 10,000 INR $\ge$ 7,000 INR (Check passes).
  3. Request 2 reads balance: 10,000 INR $\ge$ 7,000 INR (Check passes before Request 1 writes).
  4. Request 1 writes deduction: balance = 3,000 INR.
  5. Request 2 writes deduction: balance = 3,000 - 7,000 = -4,000 INR.
  6. Invariant `INV-5.1` evaluated: balance < 0. Finding `CW-SEC-M2-201` confirmed.
- **Fixed / Patched Execution Path:**
  1. Wallet deduction executed using atomic SQL condition:
     `UPDATE agency_wallets SET balance_minor = balance_minor - :hold WHERE agency_id = :id AND balance_minor >= :hold`.
  2. Request 1 succeeds (balance becomes 3,000 INR).
  3. Request 2 affects 0 rows, raises `InsufficientWalletFundsError`, and is rejected.
  4. Final balance = 3,000 INR $\ge$ 0. Retest passes.

---

## Scenario 5: SCN-RACE-001 — Cancellation vs Delayed Supplier Confirmation

- **Identifier:** `SCN-RACE-001`
- **Finding ID:** `CW-SEC-M2-202`
- **Severity:** HIGH
- **CWE References:**
  - CWE-362: Race Condition
  - CWE-670: Always-Incorrect Control Flow Implementation
- **Target Invariant:**
  - `INV-5.4: Voucher Entitlement Preconditions` (Vouchers cannot exist for cancelled bookings)
  - `INV-5.6: State Transition Validity` (Terminal state `CANCELLED` cannot transition to `CONFIRMED`)
- **Threat Model:**
  A guest submits an immediate booking cancellation while supplier confirmation is delayed in flight. If the supplier's late confirmation callback is processed without checking whether the booking was already cancelled and refunded, the system resurrects the booking into `VOUCHER_ISSUED`, allowing the guest to utilize the voucher while retaining their refund.
- **Fault Injection Mechanics:**
  Crosswire initiates a booking, cancels it (transitioning to `CANCELLED` with refund issued), then injects a delayed supplier confirmation callback with `SUPPLIER_CONFIRMED`.
- **Vulnerable Execution Path:**
  1. Booking `BKG-101` created in `PENDING`.
  2. Guest cancels: status set to `CANCELLED`, refund issued.
  3. Delayed supplier webhook arrives with confirmation `SUP-CONF-777`.
  4. Handler overwrites status: `status = VOUCHER_ISSUED`, generates active `VOUCHER-999`.
  5. Invariants `INV-5.4` and `INV-5.6` fail. Finding `CW-SEC-M2-202` confirmed.
- **Fixed / Patched Execution Path:**
  1. State transition engine verifies state machine rules: `CANCELLED` is a terminal sink state.
  2. Delayed confirmation arrives; handler checks `current_status`.
  3. Finding `current_status == CANCELLED`, confirmation is rejected, voucher generation is blocked, and an automated cancellation message is dispatched to the supplier. Retest passes.

---

## Scenario 6: SCN-BOLA-001 — Cross-Agency BOLA / IDOR Tenant Isolation

- **Identifier:** `SCN-BOLA-001`
- **Finding ID:** `CW-SEC-M2-203`
- **Severity:** CRITICAL
- **CWE References:**
  - CWE-639: Authorization Bypass Through User-Controlled Key (IDOR / BOLA)
  - CWE-284: Improper Access Control
- **Target Invariant:** `INV-AUTH-01: Cross-Tenant Booking Boundary`
  $$\forall \text{ read}(b), \quad b.\text{agency\_id} == \text{caller}.\text{agency\_id}$$
- **Threat Model:**
  In a multi-tenant B2B travel portal, agencies manage private customer reservations containing PII, corporate itinerary details, and negotiated rates. If booking lookup or modification endpoints query bookings by primary key without asserting caller tenant identity, an attacker agency can exfiltrate competitor customer bookings.
- **Fault Injection Mechanics:**
  Attacker `agency_attacker` issues lookup request `GET /api/v1/bookings/{victim_booking_id}` where `victim_booking_id` belongs to `agency_victim`.
- **Vulnerable Execution Path:**
  1. Victim booking `BKG-VIC-01` created for 15,000 INR by `agency_victim`.
  2. `agency_attacker` authenticates and requests `BKG-VIC-01`.
  3. Booking service executes `SELECT * FROM bookings WHERE booking_id = :id` without `agency_id` filter.
  4. Dossier containing financial amounts and voucher codes returned to attacker.
  5. Invariant `INV-AUTH-01` fails. Finding `CW-SEC-M2-203` confirmed.
- **Fixed / Patched Execution Path:**
  1. Booking service validates: `if booking.agency_id != caller.agency_id: raise AuthorizationError()`.
  2. Attacker request rejected with HTTP 403 Forbidden. Retest passes.

---

## Scenario 7: SCN-PRIV-001 — Sub-Agent Privilege Escalation via Deposit

- **Identifier:** `SCN-PRIV-001`
- **Finding ID:** `CW-SEC-M2-204`
- **Severity:** HIGH
- **CWE References:**
  - CWE-269: Improper Privilege Management
  - CWE-285: Improper Authorization
- **Target Invariant:** `INV-AUTH-02: Role-Based Financial Authorization`
  $$\text{deposit}(a) \implies \text{caller.role} == \text{FINANCIAL\_ADMIN}$$
- **Threat Model:**
  Travel platforms allow agencies to create restricted sub-agent accounts for frontline booking agents. If financial endpoints (such as manual credit deposit or balance overrides) fail to enforce Role-Based Access Control (RBAC), an unprivileged sub-agent can grant themselves unlimited booking credit.
- **Fault Injection Mechanics:**
  Sub-agent with role `FRONT_DESK_AGENT` issues a credit deposit request of 50,000 INR to `agency_corp`'s wallet.
- **Vulnerable Execution Path:**
  1. Initial wallet balance: 10,000 INR.
  2. Sub-agent calls `POST /api/v1/wallet/deposit` with amount 50,000 INR.
  3. Endpoint updates balance without role verification.
  4. Balance increases to 60,000 INR.
  5. Invariant `INV-AUTH-02` fails. Finding `CW-SEC-M2-204` confirmed.
- **Fixed / Patched Execution Path:**
  1. Endpoint checks `caller.role in ['FINANCIAL_ADMIN', 'PLATFORM_SUPERUSER']`.
  2. Sub-agent's token lacks required role; request rejected with HTTP 403 Forbidden.
  3. Balance remains unchanged at 10,000 INR. Retest passes.

---

## Scenario 8: SCN-TAMPER-001 — Client-Side Fare, Markup & Currency Tampering

- **Identifier:** `SCN-TAMPER-001`
- **Finding ID:** `CW-SEC-M2-205`
- **Severity:** CRITICAL
- **CWE References:**
  - CWE-472: External Parameter Pollution
  - CWE-602: Client-Side Enforcement of Server-Side Security
- **Target Invariant:** `INV-PRICE-01: Server-Side Quote Integrity`
  $$\text{Charged Amount} == \text{Authoritative Quote Amount}$$
- **Threat Model:**
  During flight or hotel checkout, the client receives a server-generated quote of 15,000 INR. If the checkout request accepts the amount or currency from the client-submitted JSON body rather than verifying against the server quote store, an attacker can modify the price to 1.00 INR and obtain valid vouchers at near-zero cost.
- **Fault Injection Mechanics:**
  Crosswire initiates a booking with quote ID `QTE-888` (authoritative price: 15,000 INR), but mutates the checkout payload to `{"amount": 1.00, "currency": "INR"}`.
- **Vulnerable Execution Path:**
  1. Legitimate quote generated for 15,000 INR.
  2. Checkout request submitted with tampered payload `amount = 1.00`.
  3. Booking service creates booking and charges 1.00 INR without quote check.
  4. Valid voucher issued.
  5. Invariant `INV-PRICE-01` fails (Charged 1.00 INR vs Quoted 15,000.00 INR). Finding `CW-SEC-M2-205` confirmed.
- **Fixed / Patched Execution Path:**
  1. Booking service fetches authoritative quote from `quote_store` using `quote_id`.
  2. Service asserts `payload.amount == quote.amount`.
  3. Discrepancy detected; transaction aborted with `PriceDiscrepancyError`. Retest passes.

---

## Scenario 9: SCN-SKIP-001 — Payment-Step Skipping / Unentitled Voucher Issuance

- **Identifier:** `SCN-SKIP-001`
- **Finding ID:** `CW-SEC-M2-206`
- **Severity:** CRITICAL
- **CWE References:**
  - CWE-841: Improper Enforcement of Behavioral Workflow Logic
  - CWE-670: Always-Incorrect Control Flow Implementation
- **Target Invariant:** `INV-5.4: Voucher Entitlement Preconditions`
  $$\text{voucher\_issued} \implies \text{payment\_captured} == \text{True}$$
- **Threat Model:**
  In a multi-step booking saga, the expected sequence is: `INTENT_CREATED` $\to$ `INVENTORY_HELD` $\to$ `PAYMENT_CAPTURED` $\to$ `SUPPLIER_CONFIRMED` $\to$ `VOUCHER_ISSUED`. If the voucher generation API endpoint does not verify payment capture status, an attacker can invoke voucher issuance directly after inventory hold, completely bypassing the payment gateway.
- **Fault Injection Mechanics:**
  Crosswire creates a booking in `BOOKING_HELD` state, skips calling the payment service, and immediately invokes the voucher generation endpoint.
- **Vulnerable Execution Path:**
  1. Booking created in `BOOKING_HELD`.
  2. Attacker skips `/api/v1/payment/capture`.
  3. Attacker calls `POST /api/v1/bookings/{id}/issue-voucher`.
  4. Service generates voucher `VCH-UNPAID-01` without checking payment status.
  5. Invariant `INV-5.4` fails. Finding `CW-SEC-M2-206` confirmed.
- **Fixed / Patched Execution Path:**
  1. Voucher service asserts `booking.status == SUPPLIER_CONFIRMED and booking.payment_captured == True`.
  2. Request rejected with `PreconditionFailedError` (HTTP 412). Retest passes.

---

## Scenario 10: SCN-ABUSE-001 — Cancel-Rebook-Refund Cumulative Overflow Abuse

- **Identifier:** `SCN-ABUSE-001`
- **Finding ID:** `CW-SEC-M2-207`
- **Severity:** HIGH
- **CWE References:**
  - CWE-840: Business Logic Errors
  - CWE-799: Improper Control of Interaction Frequency
- **Target Invariant:** `INV-5.5: Strict Cumulative Refund Boundedness`
  $$\sum \text{RefundsIssued}(\text{booking}) \le \text{TotalPaymentCaptured}(\text{booking})$$
- **Threat Model:**
  When a guest cancels a booking subject to a cancellation penalty (e.g. 15,000 INR payment minus 3,000 INR penalty yields a 12,000 INR refund), automated re-try scripts or flawed customer support override tools may allow issuing a secondary "full refund" of 15,000 INR, resulting in cumulative refunds exceeding the original payment.
- **Fault Injection Mechanics:**
  Crosswire initiates a 15,000 INR booking, cancels it (receiving 12,000 INR refund), and immediately attempts an additional automated refund of 15,000 INR.
- **Vulnerable Execution Path:**
  1. Booking paid: 15,000 INR.
  2. Legitimate cancellation refund: 12,000 INR.
  3. Secondary refund requested: 15,000 INR.
  4. Vulnerable refund handler checks each refund individually against original price ($15,000 \le 15,000$), without summing historical refunds.
  5. Secondary refund approved. Total refunded = 27,000 INR (12,000 INR excess).
  6. Invariant `INV-5.5` fails. Finding `CW-SEC-M2-207` confirmed.
- **Fixed / Patched Execution Path:**
  1. Refund handler queries `SELECT SUM(amount_minor) FROM refund_records WHERE booking_id = :id`.
  2. Handler calculates `remaining_refundable = captured_amount - previous_refunds`.
  3. For secondary request, $12,000 + 15,000 > 15,000$; request rejected with `RefundLimitExceededError`. Retest passes.

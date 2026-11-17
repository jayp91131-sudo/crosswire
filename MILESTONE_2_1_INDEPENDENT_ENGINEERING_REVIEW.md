# CROSSWIRE 2.0 — INDEPENDENT ENGINEERING REVIEW
## Milestone 2.1: Adversarial Duplicate Refund Webhook Testing
*Reviewer Role: Independent Principal Application Security Engineer & Financial Systems Auditor*  
*Target Repository:* `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`  
*Target Changelog / Primary Evidence:* `MILESTONE_2_1_AUDIT_CHANGELOG.md`  
*Date of Audit:* November 8, 2026  
*Operating Mode:* STRICT READ-ONLY AUDIT  

---

## 1. EXECUTIVE VERDICT: CONDITIONAL PASS

Milestone 2.1 has achieved **CONDITIONAL PASS**.

### Core Strengths Verified
1. **Adversarial Failure Reproduction:** In vulnerable mode, Crosswire reliably reproduces duplicate webhook processing, inserting duplicate refund rows, creating repeated ledger credit entries, and driving cumulative refund balances beyond the refundable entitlement.
2. **Detection Oracle:** The Crosswire Invariant Engine ([`engine/invariant_engine.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/invariant_engine.py#L192-L245)) successfully detects this invariant violation and generates structured finding **`CW-SEC-M2-001`**.
3. **Fixed Engine Concurrency:** In fixed mode, an exclusive SQLite `BEGIN IMMEDIATE` write transaction, combined with payload SHA-256 hash checking and SQL conditional updates (`WHERE (total_refunded_minor + ?) <= refundable_entitlement_minor`), reliably prevents duplicate financial effects across 10 concurrent requests in-process and over real localhost TCP sockets on port 8012.
4. **Automated Regression:** All **47/47 automated tests pass** cleanly in 15.88s.

### Material Deficiencies Identified (Pre-Milestone 2.2 Conditions)
Despite passing tests, the code review revealed significant architectural and security weaknesses:
- **Global Shared Webhook Secret:** The aggregator relies on a single static secret (`DEFAULT_WEBHOOK_SECRET`) for all payment providers without a per-provider secret registry, enabling provider impersonation.
- **Unverified Payment Obligation and Currency:** The fixed webhook processor checks that the booking exists and that the amount does not exceed entitlement, but completely skips verifying that `payload.payment_id` belongs to the booking, that `payload.currency` matches `booking.currency`, that `payload.event_type` is valid, or that the booking is in a refundable state (e.g. `CANCELLED`).
- **Defective Provider-Scoped Primary Key:** The `webhook_events` table defines `event_id TEXT PRIMARY KEY`, which enforces global uniqueness across all providers rather than composite `(provider, event_id)` uniqueness.
- **Loose Substring Traceability:** The financial ledger does not store structured `event_id` or `provider_id` columns; the invariant engine traces ledger entries using SQL `LIKE '%{event_id}%'`, which is brittle.
- **Missing Replay Freshness Window:** The payload `created_at` timestamp is completely ignored during verification, permitting stale replays of previously unseen historical events.
- **Inaccurate Evidence Terminology:** The evidence report `CW-SEC-M2-001` describes accounting entries as "Excess Money Disbursed" when no external payment API call or cash disbursement was executed.

---

## 2. SCOPE AND REPOSITORY STATE REVIEWED

- **Repository Root:** `C:\Users\jayp9\.gemini\antigravity\scratch\crosswire`
- **Audit Timestamp:** 2026-11-08T16:18:18+05:30
- **Version Control Status:** Local standalone directory (no `.git` directory present).
- **Environment:**
  - Python: `3.11.15` (CPython 64-bit) via `uv`
  - Pytest: `9.2.0` (pluggy 1.7.0, anyio 4.16.0, asyncio 1.4.1.post1)
  - Operating System: Windows 11 (win32)
- **Primary Source Files Examined:**
  - [`target_platform/models.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py)
  - [`target_platform/aggregator/app.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/app.py)
  - [`target_platform/aggregator/booking_service.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/booking_service.py)
  - [`target_platform/aggregator/database.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py)
  - [`target_platform/payment/payment_service.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/payment/payment_service.py)
  - [`target_platform/agency_client.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/agency_client.py)
  - [`engine/invariant_engine.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/invariant_engine.py)
  - [`engine/evidence.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/evidence.py)
  - [`engine/ground_truth_oracle.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/ground_truth_oracle.py)
  - [`tests/test_webhook_adversarial.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py)

---

## 3. ACTUAL TEST EXECUTION RESULTS

**Command Executed:**
```powershell
cd C:\Users\jayp9\.gemini\antigravity\scratch\crosswire; uv run pytest -v
```

**Execution Log Transcript (Verbatim):**
```text
============================= test session starts =============================
platform win32 -- Python 3.11.15, pytest-9.2.0, pluggy-1.7.0 -- C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\jayp9\.gemini\antigravity\scratch\crosswire
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.16.0, asyncio-1.4.1.post1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 47 items

tests/test_auth_security.py::test_auth_invalid_credentials_rejected PASSED [  2%]
tests/test_auth_security.py::test_intent_ownership_mismatch_rejected PASSED [  4%]
tests/test_auth_security.py::test_cross_agency_booking_access_forbidden PASSED [  6%]
tests/test_auth_security.py::test_correlation_id_propagation_and_secret_handling PASSED [  8%]
tests/test_booking.py::test_normal_successful_booking_lifecycle PASSED   [ 10%]
tests/test_booking.py::test_traceability_across_distributed_actors PASSED [ 12%]
tests/test_financial_hardening.py::test_exact_monetary_representation_minor_units PASSED [ 14%]
tests/test_financial_hardening.py::test_atomic_concurrent_partial_refunds_competing_keys PASSED [ 17%]
tests/test_financial_hardening.py::test_idempotency_scope_and_payload_conflict PASSED [ 19%]
tests/test_financial_hardening.py::test_balanced_double_entry_accounting_model PASSED [ 21%]
tests/test_financial_hardening.py::test_schema_migration_preserves_existing_data PASSED [ 23%]
tests/test_idempotency.py::test_idempotent_supplier_deduplication_on_repeated_requests PASSED [ 25%]
tests/test_idempotency.py::test_unsupported_supplier_enters_reconciliation_fallback PASSED [ 27%]
tests/test_idempotency.py::test_concurrent_duplicate_requests_with_same_idempotency_key PASSED [ 29%]
tests/test_invariants.py::test_invariant_engine_detects_duplicate_with_structured_evidence PASSED [ 31%]
tests/test_invariants.py::test_invariant_engine_passes_fixed_mode_with_clean_evidence PASSED [ 34%]
tests/test_invariants.py::test_deterministic_reproducibility_across_multiple_runs PASSED [ 36%]
tests/test_invariants.py::test_isolated_sandbox_no_external_network_calls PASSED [ 38%]
tests/test_payment_and_lifecycle.py::test_successful_booking_and_payment PASSED [ 40%]
tests/test_payment_and_lifecycle.py::test_payment_failure_halts_booking_without_supplier_commitment PASSED [ 42%]
tests/test_payment_and_lifecycle.py::test_valid_cancellation PASSED      [ 44%]
tests/test_payment_and_lifecycle.py::test_full_and_partial_refunds PASSED [ 46%]
tests/test_payment_and_lifecycle.py::test_refund_exceeding_eligible_amount_rejected PASSED [ 48%]
tests/test_payment_and_lifecycle.py::test_duplicate_refund_operation_idempotency_and_conflict PASSED [ 51%]
tests/test_payment_and_lifecycle.py::test_agency_isolation PASSED        [ 53%]
tests/test_payment_and_lifecycle.py::test_concurrent_financial_operations PASSED [ 55%]
tests/test_payment_and_lifecycle.py::test_existing_timeout_vulnerable_mode_with_payment PASSED [ 57%]
tests/test_payment_and_lifecycle.py::test_existing_timeout_fixed_mode_with_payment PASSED [ 59%]
tests/test_supplier_timeout.py::test_vulnerable_blind_retry_produces_duplicate_supplier_reservations PASSED [ 61%]
tests/test_supplier_timeout.py::test_fixed_mode_recovers_original_reservation_after_timeout PASSED [ 63%]
tests/test_tcp_integration.py::test_tcp_vulnerable_blind_retry_creates_duplicate_reservations PASSED [ 65%]
tests/test_tcp_integration.py::test_tcp_fixed_mode_recovers_single_reservation PASSED [ 68%]
tests/test_tcp_integration.py::test_tcp_unsupported_supplier_enters_reconciliation_safely PASSED [ 70%]
tests/test_webhook_adversarial.py::test_successful_single_refund_webhook PASSED [ 72%]
tests/test_webhook_adversarial.py::test_duplicate_sequential_delivery PASSED [ 74%]
tests/test_webhook_adversarial.py::test_duplicate_concurrent_delivery PASSED [ 76%]
tests/test_webhook_adversarial.py::test_invalid_signature_rejection PASSED [ 78%]
tests/test_webhook_adversarial.py::test_same_event_id_with_modified_payload PASSED [ 80%]
tests/test_webhook_adversarial.py::test_missing_event_id_rejected PASSED [ 82%]
tests/test_webhook_adversarial.py::test_already_processed_event_idempotent PASSED [ 85%]
tests/test_webhook_adversarial.py::test_refund_exceeding_eligible_entitlement_rejected PASSED [ 87%]
tests/test_webhook_adversarial.py::test_concurrent_different_refund_events_against_same_entitlement PASSED [ 89%]
tests/test_webhook_adversarial.py::test_ledger_consistency_invariant PASSED [ 91%]
tests/test_webhook_adversarial.py::test_fixed_mode_prevents_duplicate_credits PASSED [ 93%]
tests/test_webhook_adversarial.py::test_vulnerable_mode_detection_and_evidence_generation PASSED [ 95%]
tests/test_webhook_adversarial.py::test_tcp_webhook_concurrent_delivery PASSED [ 97%]
tests/test_webhook_adversarial.py::test_no_external_network_access PASSED [100%]

============================= 47 passed in 15.88s =============================
```

- **Collected:** 47
- **Passed:** 47
- **Failed:** 0
- **Skipped:** 0
- **Warnings:** 0

---

## 4. FINDINGS SUMMARY TABLE

| Finding ID | Severity | Category | File Reference | Brief Summary |
|---|---|---|---|---|
| **CW-REV-M2-001** | **HIGH** | Security / Auth | [`models.py:395`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py#L395), [`booking_service.py:83`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/booking_service.py#L83) | Global shared webhook secret permits provider impersonation and lacks per-provider key lookup. |
| **CW-REV-M2-002** | **HIGH** | Financial Integrity | [`database.py:1496-1505`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1496-L1505) | Webhook processor omits payment ID, currency, event type, and booking cancellation status checks. |
| **CW-REV-M2-003** | **HIGH** | Schema Defect | [`database.py:234`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L234) | `webhook_events.event_id` is defined as single-column `PRIMARY KEY`, defeating multi-provider event scoping. |
| **CW-REV-M2-004** | **MEDIUM** | Invariant / Evidence | [`database.py:1256`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1256), [`invariant_engine.py:204`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/invariant_engine.py#L204) | Invariant engine queries event-caused ledger entries via loose SQL `LIKE '%{event_id}%'` pattern. |
| **CW-REV-M2-005** | **MEDIUM** | Replay Security | [`models.py:429`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py#L429), [`database.py:1459`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1459) | Complete absence of webhook timestamp freshness window, allowing stale historical replay. |
| **CW-REV-M2-006** | **LOW** | Evidence Accuracy | [`evidence.py:94`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/evidence.py#L94) | `CW-SEC-M2-001` labels accounting entries as "Excess Money Disbursed" without external cash transfer. |
| **CW-REV-M2-007** | **LOW** | Test Coverage | [`test_financial_hardening.py:235`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_financial_hardening.py#L235) | Schema migration test suite covers `booking_records` but lacks a legacy `webhook_events` migration test. |

---

## 5. DETAILED AUDIT RESULTS (AUDITS A–G)

### Audit A — Webhook Signature and Provider Identity

#### 1. Exact Bytes Signed and Serialization Mechanism
- **Verification:** In [`target_platform/models.py`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py#L398-L407):
  ```python
  def compute_webhook_signature(secret: str, payload_dict: dict) -> str:
      keys = sorted(k for k in payload_dict.keys() if k != "signature")
      canonical = "&".join(f"{k}={payload_dict[k]}" for k in keys)
      return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
  ```
- **Finding:** The signature is **not computed over raw HTTP request bytes**; instead, it is computed over a canonical query-string serialization of dictionary keys sorted alphabetically.
- **Field Exclusion:** The key `"signature"` is explicitly excluded (`if k != "signature"`).
- **Payload Sensitivity:** Changing any payload attribute (`amount_minor`, `currency`, `booking_id`, `payment_id`, `refund_id`, `provider_id`) changes the canonical string and invalidates the signature.
- **Whitespace / Key-Order Invariance:** Because the incoming body is parsed into a Python dictionary prior to signature computation, differences in raw JSON whitespace or JSON key ordering do not break signature verification. While robust for synthetic tests, this deviates from standard real-world payment gateway webhook signing (e.g., Stripe, Adyen, Razorpay) which verifies raw unparsed request bytes.

#### 2. Constant-Time Verification
- **Verification:** [`target_platform/models.py:409-417`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py#L409-L417) explicitly uses `hmac.compare_digest(expected, signature)`. Timing attacks against signature verification are mitigated.

#### 3. Provider Secret Isolation & Identity Handling (CW-REV-M2-001)
- **Verification:** [`target_platform/models.py:395`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py#L395) defines:
  ```python
  DEFAULT_WEBHOOK_SECRET = "whsec_synthetic_crosswire_test_secret_99"
  ```
  In [`target_platform/aggregator/booking_service.py:83`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/booking_service.py#L83), `self.webhook_secret = DEFAULT_WEBHOOK_SECRET`.
- **Vulnerability:** The aggregator uses **one static global secret** for all providers. There is no registry mapping `provider_id -> secret`.
- **Impact:** An attacker or rogue provider sending `provider_id="stripe"` or `provider_id="unauthorized_gateway"` can sign the payload with `DEFAULT_WEBHOOK_SECRET`, and the aggregator will accept it. Unsupported providers are not rejected. Missing signatures are rejected safely with HTTP 401.

---

### Audit B — Refund Legitimacy and Financial Authorization

#### 1. Entity Relationship Cross-Validation (CW-REV-M2-002)
- **Trace in `process_webhook_event_fixed` ([`database.py:1496-1537`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1496-L1537)):
  ```python
  b_row = conn.execute(
      "SELECT * FROM booking_records WHERE booking_id = ?", (payload.booking_id,)
  ).fetchone()
  if not b_row:
      conn.execute("ROLLBACK")
      raise ValueError(f"Booking '{payload.booking_id}' not found")

  available_minor = b_row["refundable_entitlement_minor"] - b_row["total_refunded_minor"]
  if payload.amount_minor > available_minor:
      ... raise ExcessiveRefundError(...)
  ```
- **Critical Flaws Identified:**
  1. **`payment_id` Unchecked:** The code never checks whether `payload.payment_id == b_row["payment_id"]`. A webhook specifying `payment_id="FAKE-PAY-999"` is accepted and posted to the ledger.
  2. **`currency` Unchecked:** The code compares `payload.amount_minor > available_minor` without verifying `payload.currency == b_row["currency"]`. If a booking is in USD ($100 = 10000 cents) and a webhook arrives in INR (10000 paise = ₹100), the processor treats them as equivalent numbers.
  3. **`event_type` Ignored:** Any event type (e.g. `"payment.captured"`, `"dispute.opened"`, or arbitrary strings) is treated as a valid refund credit.
  4. **Booking Status Ignored:** Unlike manual refunds in `booking_service.py:636` which require `TransactionStatus.CANCELLED`, `process_webhook_event_fixed` does not check booking status. An active, confirmed, or uncancelled booking can have its balance refunded via webhook.
  5. **Prior Authorization Ignored:** The event creates a new `refund_record` directly from the incoming event without verifying if a refund was previously initiated.

#### 2. Internal Ledger Posting vs. External Cash Disbursement
- In vulnerable mode, lines [`1352-1403`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1352-L1403) create internal SQLite rows in `refund_records` and `ledger_entries` (crediting account `2100_AGENCY_REFUNDABLE_PAYABLE`).
- **Crucial Distinction:** No outbound HTTP call to a bank, wallet payout, or payment gateway (`call_refund`) is executed. The vulnerability represents an **unbounded internal accounting liability / ledger balance corruption**, not an external cash drain.

---

### Audit C — Event Deduplication and Database Consistency

#### 1. Interaction of Vulnerable Mode with SQLite Unique Constraint
- **Schema:** Line [`233`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L233):
  `CREATE TABLE IF NOT EXISTS webhook_events (event_id TEXT PRIMARY KEY, provider TEXT NOT NULL, ...)`
- **Vulnerable Bypass:** Line [`1324-1327`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1324-L1327):
  ```sql
  INSERT INTO webhook_events (...) VALUES (...)
  ON CONFLICT(event_id) DO UPDATE SET
      processed_count = processed_count + 1,
      last_received_at = excluded.last_received_at
  ```
- **Mechanics:** The vulnerable path bypasses duplicate failure via SQLite's `ON CONFLICT DO UPDATE`. It then generates synthetically suffixed IDs:
  `refund_id = f"{payload.refund_id}-DUP-{new_count}"`
  `tx_ref = f"TXN-WH-{payload.event_id}-{new_count}-{now_ms}"`
  This allows multiple rows to be inserted into `refund_records` and `ledger_entries` on every duplicate delivery.

#### 2. Fixed Mode Transactional Atomicity
- Lines [`1456-1658`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1456-L1658) use `conn.isolation_level = None` and `conn.execute("BEGIN IMMEDIATE")`.
- All writes (`UPDATE booking_records`, `INSERT webhook_events`, `INSERT refund_records`, `INSERT ledger_entries`) occur in the same exclusive transaction.
- If any write fails, lines [`1669-1674`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1669-L1674) trigger `conn.execute("ROLLBACK")`. Atomicity is guaranteed.

#### 3. Payload Conflict Detection
- Lines [`1467-1473`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1467-L1473):
  If `existing["payload_hash"] != payload_hash`, the processor executes `conn.execute("ROLLBACK")` and raises `WebhookPayloadConflictError` (HTTP 409). No financial changes occur.

#### 4. Provider-Scoped Uniqueness Defect (CW-REV-M2-003)
- The changelog claims composite uniqueness via:
  `CREATE UNIQUE INDEX IF NOT EXISTS uidx_wh_provider_event ON webhook_events (provider, event_id);`
- **However, line 234 specifies:**
  `event_id TEXT PRIMARY KEY`
- **Impact:** Because `event_id` is a single-column primary key, if Provider A and Provider B both generate an event with `event_id="evt_100"`, Provider B's insert will crash with `sqlite3.IntegrityError: UNIQUE constraint failed: webhook_events.event_id`. Uniqueness is **not** properly scoped by provider at the physical table level.

#### 5. Ledger Entry Traceability & Invariant Engine (CW-REV-M2-004)
- **Table Definition ([`database.py:215-231`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L215-L231)):** `ledger_entries` does not contain `event_id` or `provider_id` columns.
- **Lookup Method ([`database.py:1256`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/aggregator/database.py#L1256)):**
  ```sql
  SELECT * FROM ledger_entries 
  WHERE transaction_reference LIKE ? OR journal_batch_id LIKE ?
  ```
  where `ref_pattern = f"%{event_id}%"`.
- **Weakness:** The invariant engine determines causality by checking if `event_id` is a substring of the transaction reference or batch ID. If an event ID is numeric (e.g. `"1"`), it will match unrelated transaction references like `TXN-WH-10-1`. Causal attribution must be backed by explicit relational columns.

---

### Audit D — Replay Freshness and Event Lifecycle

#### 1. Timestamp Verification & Freshness Window (CW-REV-M2-005)
- **Inspection:** In [`target_platform/models.py:429`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/target_platform/models.py#L429), `WebhookPayload` has a `created_at` field.
- **Trace:** Neither `process_webhook_event_fixed` nor `booking_service.py` inspects `payload.created_at`.
- **Finding:**
  - No freshness tolerance window (e.g., ±300 seconds).
  - No clock-skew handling.
  - A signed payload created months or years ago will be processed and credited immediately, provided its `event_id` is new. Event deduplication is not equivalent to complete replay protection.

#### 2. Event Lifecycle States
- Events are immediately inserted with terminal status `"PROCESSED"` or `"REJECTED_EXCESSIVE"`. There are no intermediate states (e.g. `RECEIVED`, `PROCESSING`, `FAILED`). While safe in SQLite due to atomic transactions, in distributed systems with external network I/O, two-phase state transitions are mandatory.

---

### Audit E — Concurrency and Failure Testing

| Audit E Requirement | Tested in Suite? | Test Name / Reference | Verified Database Ground Truth |
|---|---|---|---|
| **10 Concurrent Deliveries (Same Event ID)** | **YES** | `test_duplicate_concurrent_delivery` ([`test_webhook_adversarial.py:160`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py#L160)) | Exactly 1 refund record, 1 ledger entry, `processed_count == 10`. |
| **Repeated Sequential Deliveries** | **YES** | `test_duplicate_sequential_delivery` ([`test_webhook_adversarial.py:105`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py#L105)) | Exactly 1 refund record, 1 ledger entry, `processed_count == 3`. |
| **Concurrent Competing Refunds (Exceeding Entitlement)** | **YES** | `test_concurrent_different_refund_events_against_same_entitlement` ([`test_webhook_adversarial.py:394`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py#L394)) | Two ₹8,000 requests against ₹12,000 entitlement; exactly one succeeds (200), one rejected (400); cumulative refund == ₹8,000. |
| **Same Event ID Across Different Providers** | **NO** | *Missing Test* | **NOT VERIFIED** (Will fail due to `event_id` Primary Key). |
| **Same Event ID With Modified Payload** | **YES** | `test_same_event_id_with_modified_payload` ([`test_webhook_adversarial.py:250`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py#L250)) | Returns HTTP 409 Conflict, DB state untouched. |
| **Invalid Signature Rejection** | **YES** | `test_invalid_signature_rejection` ([`test_webhook_adversarial.py:211`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py#L211)) | Returns HTTP 401 Unauthorized, zero DB side effects. |
| **Provider Identity Mismatch** | **NO** | *Missing Test* | **NOT VERIFIED** (Passes due to global secret). |
| **Failure Injected Between Transaction Writes** | **NO** | *Missing Test* | **NOT VERIFIED** (Relies on standard SQLite ROLLBACK). |
| **Real TCP Socket Integration** | **YES** | `test_tcp_webhook_concurrent_delivery` ([`test_webhook_adversarial.py:631`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_webhook_adversarial.py#L631)) | Spawns separate Uvicorn server on port 8012; 10 concurrent HTTP requests over socket verified. |
| **Vulnerable vs. Fixed Mode Reproduction** | **YES** | `test_vulnerable_mode_detection_and_evidence_generation` & `test_fixed_mode_prevents_duplicate_credits` | Both modes verified against Invariant Engine. |

---

### Audit F — Finding Quality and Evidence (CW-SEC-M2-001)

#### 1. Evidence Completeness
The structured finding generated by [`engine/evidence.py:77-101`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/engine/evidence.py#L77-L101) includes:
- Finding ID: `CW-SEC-M2-001`
- Affected Transaction ID: `TBOH-INTENT-EVID-01`
- Provider Event ID: `evt_vuln_demo_99` (Provider: `mock_payment_provider_99`)
- Correlation ID: Captured from booking record
- Delivery Count: `3` (Processing Attempts: `3`)
- Duplicate Ledger IDs: List of integer primary keys `[631, 632, 633]`
- Duplicate Refund IDs: `['RFD-DEMO-99', 'RFD-DEMO-99-DUP-2', 'RFD-DEMO-99-DUP-3']`
- Exact Amounts in Minor Units: Expected 500000 INR, Actual 1500000 INR
- Clear Reproduction & Remediation Guidance

#### 2. Terminology Correction (CW-REV-M2-006)
- The report states: `Excess Money Disbursed : 1000000 INR`.
- **Review Correction:** No money was disbursed externally. The evidence demonstrates **Excess Internal Ledger Credit / Unbounded Accounting Liability**. The report should use accounting terminology (`Excess Ledger Credit Posted`) to prevent confusion with banking drain.

---

### Audit G — Regression and Reproducibility

1. **Test Suite Health:** All 47 tests passed in 15.88 seconds without test failures or flaky behavior.
2. **Database Isolation:** The `setup_and_teardown_databases` autouse fixture in [`tests/conftest.py:25-45`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/conftest.py#L25-L45) resets all SQLite databases before and after every test function.
3. **Process Teardown:** The TCP server fixture in `test_webhook_adversarial.py:584-627` terminates the Uvicorn subprocess cleanly with fallback to `.kill()`.
4. **Migration Testing Gap (CW-REV-M2-007):** In [`tests/test_financial_hardening.py:235-293`](file:///C:/Users/jayp9/.gemini/antigravity/scratch/crosswire/tests/test_financial_hardening.py#L235-L293), the legacy database migration test validates `booking_records` minor unit backfilling, but does not test migrating an existing `webhook_events` table that lacked the new Milestone 2.1 columns (`amount_minor`, `currency`, `payment_id`, `refund_id`).

---

## 6. CLAIMS FROM CHANGELOG INDEPENDENTLY VERIFIED

- [x] **Claim 1:** Prerequisite financial hardening tests pass (33/33). *(Verified)*
- [x] **Claim 2:** HMAC-SHA256 signature verification rejects tampered requests with HTTP 401. *(Verified)*
- [x] **Claim 3:** Vulnerable mode permits duplicate webhook processing, creating duplicate refund records and ledger entries. *(Verified)*
- [x] **Claim 4:** Vulnerable mode allows cumulative refunds to exceed the refundable entitlement. *(Verified)*
- [x] **Claim 5:** Invariant Engine detects the violation and formats structured finding `CW-SEC-M2-001`. *(Verified)*
- [x] **Claim 6:** Fixed mode uses `BEGIN IMMEDIATE` to serialize concurrent requests. *(Verified)*
- [x] **Claim 7:** Replaying an identical event returns an idempotent response with zero duplicate ledger entries. *(Verified)*
- [x] **Claim 8:** Replaying an event ID with a modified payload returns HTTP 409 Conflict without side effects. *(Verified)*
- [x] **Claim 9:** Competing concurrent refund requests exceeding remaining entitlement are safely serialized and bounded. *(Verified)*
- [x] **Claim 10:** 10 concurrent requests over localhost TCP socket port 8012 succeed without duplicate financial postings. *(Verified)*
- [x] **Claim 11:** Test harness runs offline without external internet calls. *(Verified)*

---

## 7. CLAIMS REMAINING UNVERIFIED OR CONTRADICTED

- [ ] **Unverified Claim 1: "Provider-Scoped Idempotency":** Contradicted by `webhook_events.event_id TEXT PRIMARY KEY`. Two distinct providers cannot use the same event ID.
- [ ] **Unverified Claim 2: "Provider Identity Verification":** Contradicted by hardcoded global `DEFAULT_WEBHOOK_SECRET`. Any caller can claim any `provider_id`.
- [ ] **Unverified Claim 3: "Excess Money Disbursed":** Contradicted by execution trace. Only internal SQLite ledger rows are written; no external cash disbursement occurs.
- [ ] **Unverified Claim 4: "Failure Injected Between Transaction Writes":** No test in the suite injects a failure between intermediate writes to empirically verify rollback.

---

## 8. MISSING TESTS AND REPRODUCIBILITY GAPS

Before closing Milestone 2.1, the following test cases should be added to the test suite:
1. **Multi-Provider Collision Test:** An event with the same `event_id` delivered by `provider_alpha` and `provider_beta` (must succeed for both once table primary key is corrected).
2. **Provider Impersonation Test:** Webhook with `provider_id="unregistered"` or signed with a key not belonging to that provider.
3. **Mismatched Currency Test:** Webhook with `currency="USD"` against a booking with `currency="INR"`.
4. **Mismatched Payment ID Test:** Webhook with `payment_id="UNKNOWN-PAY"` against a valid booking.
5. **Uncancelled Booking Webhook Test:** Refund webhook delivered against a booking in `SUPPLIER_CONFIRMED` status.
6. **Stale Timestamp Test:** Webhook with `created_at` timestamp older than 300 seconds.
7. **Legacy Webhook Events Migration Test:** Migration test verifying legacy v1 `webhook_events` data survives schema evolution.

---

## 9. REQUIRED REMEDIATION BEFORE MILESTONE 2.2

The following items must be resolved before authorizing Milestone 2.2:

1. **Fix `webhook_events` Table Primary Key:**
   Change primary key to composite `PRIMARY KEY (provider, event_id)`:
   ```sql
   CREATE TABLE webhook_events (
       provider TEXT NOT NULL,
       event_id TEXT NOT NULL,
       ...
       PRIMARY KEY (provider, event_id)
   );
   ```
2. **Implement Per-Provider Secret Registry:**
   Store provider secrets in a registry dictionary or database table; lookup secret by `payload.provider_id` during signature verification.
3. **Enforce Entity Cross-Validation:**
   In `process_webhook_event_fixed`, assert:
   - `payload.payment_id == b_row["payment_id"]`
   - `payload.currency == b_row["currency"]`
   - `payload.event_type in ("refund.processed", "refund.succeeded")`
   - `b_row["status"] in (TransactionStatus.CANCELLED, TransactionStatus.PARTIALLY_REFUNDED)`
4. **Add Structured Event Columns to `ledger_entries`:**
   Add `provider TEXT` and `event_id TEXT` columns to `ledger_entries` and index them, eliminating reliance on `LIKE '%{event_id}%'` pattern matching.
5. **Add Timestamp Freshness Validation:**
   Reject webhooks where `|now - payload.created_at| > 300` seconds.

---

## 10. FINAL GO / NO-GO RECOMMENDATION

### Recommendation: **HOLD ON MILESTONE 2.2 UNTIL BLOCKING FIXES ARE APPLIED**

The prototype has demonstrated exceptional fidelity in reproducing the core adversarial timeout, duplicate webhook replay, and invariant detection flows. However, because Milestone 2.2 introduces **Concurrent Booking and Agency Wallet Balance Manipulation**, leaving the financial ledger lookup (`LIKE %event_id%`), provider identity verification, and currency validation unhardened creates significant compounded risk.

Remediate Findings **CW-REV-M2-001**, **CW-REV-M2-002**, and **CW-REV-M2-003** first, then proceed to Milestone 2.2.

---
*End of Independent Engineering Review Report.*

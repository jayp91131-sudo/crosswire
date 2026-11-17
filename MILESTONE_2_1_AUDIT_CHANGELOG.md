# CROSSWIRE 2.0 — MILESTONE 2.1 AUDIT & CHANGELOG REPORT
**Adversarial Duplicate Refund Webhook Testing & Transaction Integrity Verification**
*Authoritative Audit & Technical Changelog Document*
*Date: November 8, 2026 | Test Suite Status: 47/47 Passing (100%)*

---

## 1. EXECUTIVE SUMMARY

Milestone 2.1 delivers **Adversarial Duplicate Refund Webhook Testing** for the Crosswire 2.0 Business-Logic & Transaction-Integrity DAST platform. 

This phase validates the resilience of distributed B2B travel platforms against duplicated, replayed, delayed, concurrent, and adversarial payment/refund webhooks.

### Key Audit Highlights:
1. **Prerequisite Integrity Verified:** Verified that all 33 previous financial hardening and booking lifecycle tests pass without regression before starting.
2. **Cryptographic Authentication:** Implemented synthetic HMAC-SHA256 signature verification over webhook payloads (`provider_id`, `event_id`, `event_type`, `booking_id`, `payment_id`, `refund_id`, `amount_minor`, `currency`, `created_at`). Unauthorized payloads are rejected with HTTP 401.
3. **Vulnerable Mode vs. Fixed Mode Dual-Engine:**
   - **Vulnerable Mode:** Replays naively post duplicate refund ledger entries and unconstrained cash disbursements exceeding the refundable entitlement, generating high-fidelity structured security finding **`CW-SEC-M2-001`**.
   - **Fixed Mode:** Enforces composite uniqueness on `(provider, event_id)` with `BEGIN IMMEDIATE` transaction locking, payload hash validation (HTTP 409 Conflict on payload mismatch), remaining entitlement checks (HTTP 400 ExcessiveRefundError), and atomic single-effect posting.
4. **Adversarial Concurrency Proof:** Successfully survived 10 concurrent asynchronous deliveries across in-process ASGI and real localhost TCP sockets (`http://127.0.0.1:8012`), confirming zero duplicate ledger credits and strict idempotency under thread contention.
5. **Comprehensive Test Suite:** 14 newly authored integration tests in `tests/test_webhook_adversarial.py`; **47/47 tests passing across the entire project suite** in 18.24s.

---

## 2. REPOSITORY TREE

```
C:\Users\jayp9\.gemini\antigravity\scratch\crosswire
├── engine/
│   ├── evidence.py               # Webhook & Financial Consistency evidence collectors
│   ├── ground_truth_oracle.py    # Testbed Oracle with webhook & refund record inspection
│   ├── invariant_engine.py       # Invariant Engine (CW-SEC-M2-001 structured finding generator)
│   ├── models.py                 # Core Invariant domain models (FINANCIAL_CONSISTENCY)
│   ├── scenario_runner.py        # Orchestration harness for automated scenario execution
│   └── tracer.py                 # Distributed trace and span collector
├── target_platform/
│   ├── aggregator/
│   │   ├── app.py                # FastAPI routes (added POST /api/v1/webhooks/payment)
│   │   ├── booking_service.py    # Aggregator service with dual-mode webhook processors
│   │   ├── database.py           # SQLite layer with (provider, event_id) atomic locking
│   │   ├── payment_client.py     # HTTP/ASGI client for mock payment provider
│   │   └── supplier_client.py    # HTTP/ASGI client for mock hotel supplier
│   ├── payment/
│   │   ├── app.py                # Synthetic payment gateway endpoints
│   │   ├── database.py           # Payment persistence layer
│   │   └── payment_service.py    # HMAC-SHA256 signed refund webhook event generator
│   ├── supplier/
│   │   ├── app.py                # Mock hotel supplier routes
│   │   ├── database.py           # Supplier reservation persistence
│   │   └── supplier_service.py   # Supplier business logic & commit-then-timeout simulator
│   ├── agency_client.py          # Client SDK with deliver_webhook() helper
│   ├── models.py                 # WebhookPayload, WebhookProcessingResponse, HMAC utilities
│   └── money.py                  # ISO-4217 integer minor unit arithmetic & Decimal safety
├── tests/
│   ├── conftest.py               # Shared test fixtures, DB auto-cleanup, and ASGI bindings
│   ├── test_auth_security.py     # Authentication, API key, & agency isolation tests (4)
│   ├── test_booking.py           # Standard booking lifecycle & distributed traces (2)
│   ├── test_financial_hardening.py # Exact minor units, double-entry ledger & migrations (5)
│   ├── test_idempotency.py       # Supplier idempotency key deduplication & race tests (3)
│   ├── test_invariants.py        # Invariant engine detection & reproducibility tests (4)
│   ├── test_payment_and_lifecycle.py # Payment authorization/capture & refund states (10)
│   ├── test_supplier_timeout.py  # Commit-then-timeout failure reproduction (2)
│   ├── test_tcp_integration.py   # Real localhost TCP integration over sockets (3)
│   └── test_webhook_adversarial.py # MILESTONE 2.1: 14 adversarial webhook test scenarios (14)
├── pytest.ini
├── requirements.txt
├── README.md
└── MILESTONE_2_1_AUDIT_CHANGELOG.md
```

---

## 3. FILE-BY-FILE CHANGELOG

### 3.1. `target_platform/models.py`
* **HMAC Cryptographic Utilities:**
  - Added `DEFAULT_WEBHOOK_SECRET = "whsec_synthetic_crosswire_test_secret_99"`.
  - Added `compute_webhook_signature(payload_bytes, secret) -> str`: Produces a standard `sha256=<hex>` HMAC digest.
  - Added `verify_webhook_signature(payload_bytes, signature, secret) -> bool`: Performs constant-time comparison via `hmac.compare_digest` to prevent timing attacks.
* **Webhook Models:**
  - Added `WebhookPayload`: Pydantic model for payment gateway events (`provider_id`, `event_id`, `event_type`, `booking_id`, `payment_id`, `refund_id`, `amount_minor`, `currency`, `created_at`, `signature`). Includes `.sign(secret)` helper.
  - Added `WebhookProcessingResponse`: API response schema returning `status`, `event_id`, `booking_id`, `duplicate`, `total_refunded_minor`, `remaining_entitlement_minor`, and audit `message`.
* **Cancellation Model Alignment:**
  - Added `penalty_amount_minor` and `refundable_entitlement_minor` to `CancellationResponse` for exact integer minor unit transparency.

### 3.2. `target_platform/payment/payment_service.py`
* Added `generate_refund_webhook_event()`: Deterministically generates a signed synthetic refund event conforming to `WebhookPayload` using HMAC-SHA256.

### 3.3. `target_platform/aggregator/database.py`
* **Schema Evolution & Composite Indexing:**
  - Added migration columns to `webhook_events`: `amount_minor INTEGER DEFAULT 0`, `currency TEXT DEFAULT 'INR'`, `payment_id TEXT`, `refund_id TEXT`.
  - Added database-level composite unique index:
    ```sql
    CREATE UNIQUE INDEX IF NOT EXISTS uidx_wh_provider_event 
    ON webhook_events (provider, event_id);
    ```
* **Dual-Mode Webhook Processors:**
  - `process_webhook_event_vulnerable()`: Deliberately ignores event duplication. Increments delivery count, posts duplicate refund records (`RFD-DUP-n`), posts duplicate CREDIT entries to `ledger_entries`, updates booking `total_refunded_minor` beyond entitlement, and produces financial state inconsistencies.
  - `process_webhook_event_fixed()`:
    1. Acquires SQLite exclusive write transaction using `BEGIN IMMEDIATE`.
    2. Queries existing `(provider, event_id)`.
    3. If event exists, validates SHA-256 payload hash against stored hash. Raises `WebhookPayloadConflictError` (HTTP 409) if mismatched; otherwise increments `delivery_count` and idempotently returns existing state without posting financial effects.
    4. If new event, validates that requested `amount_minor <= (refundable_entitlement_minor - total_refunded_minor)`. Raises `ExcessiveRefundError` (HTTP 400) if exceeded.
    5. Conditionally reserves balance and updates booking state via atomic SQL update.
    6. Inserts exactly 1 `refund_records` row and exactly 1 `ledger_entries` CREDIT row.
    7. Inserts event record into `webhook_events`.
* **Inspection Queries:**
  - Added `get_webhook_event()`, `get_webhook_events_for_booking()`, `get_all_webhook_events()`, `get_ledger_entries_for_event()`.

### 3.4. `target_platform/aggregator/booking_service.py`
* Added `self.webhook_mode: AggregatorMode = AggregatorMode.FIXED` and `set_webhook_mode(mode)`.
* Implemented `process_webhook(payload, signature)`:
  - Validates HMAC-SHA256 signature using `verify_webhook_signature`. Raises `InvalidWebhookSignatureError` on bad digest.
  - Routes payload to either `db.process_webhook_event_vulnerable()` or `db.process_webhook_event_fixed()`.
* In `cancel_booking()`, populated `penalty_amount_minor` and `refundable_entitlement_minor` in `CancellationResponse`.

### 3.5. `target_platform/aggregator/app.py`
* Added FastAPI endpoint `POST /api/v1/webhooks/payment`:
  - Extracts signature from header `X-Webhook-Signature` or JSON body `signature`.
  - Rejects missing `event_id` with HTTP 400.
  - Maps exceptions:
    - `InvalidWebhookSignatureError` -> HTTP 401 Unauthorized
    - `WebhookPayloadConflictError` -> HTTP 409 Conflict
    - `ExcessiveRefundError` -> HTTP 400 Bad Request
    - `ValueError` (booking not found) -> HTTP 404 Not Found
    - Invalid schema -> HTTP 422 Unprocessable Entity
* Updated `AggregatorConfigUpdateRequest` and `/api/v1/admin/config` to dynamically toggle `webhook_mode`.

### 3.6. `target_platform/agency_client.py`
* Added `deliver_webhook(payload, signature_override=None)`: Dispatches signed webhook requests to `/api/v1/webhooks/payment` with optional signature tampering support for security testing.

### 3.7. `engine/models.py`
* Added `FINANCIAL_CONSISTENCY = "FINANCIAL_CONSISTENCY"` to `InvariantType` enum.

### 3.8. `engine/evidence.py`
* Implemented `WebhookInvariantEvidence`: Formats detailed audit-grade diff summaries for webhook replay vulnerabilities (`CW-SEC-M2-001`).
* Implemented `FinancialConsistencyEvidence`: Tracks mathematical parity between `AggregatorBookingRecord.total_refunded_minor`, `sum(RefundRecord.amount_minor)`, and `sum(LedgerEntry.amount_minor)`.

### 3.9. `engine/ground_truth_oracle.py`
* Extended oracle to query SQLite database directly for testbed verification:
  - `get_webhook_event()`, `get_webhook_events_for_booking()`, `get_all_webhook_events()`, `get_ledger_entries_for_event()`, `get_refund_records()`, `get_refunds_for_booking()`.

### 3.10. `engine/invariant_engine.py`
* Implemented `check_webhook_idempotency(provider_id, event_id, expected_ledger_count=1)`:
  - Asserts `COUNT(ledger_entries caused by provider-scoped event ID) <= expected_ledger_count`.
  - When violated, formats structured finding **`CW-SEC-M2-001`**.
* Implemented `check_refund_boundedness(booking_id)`:
  - Asserts `total_refunded_minor <= refundable_entitlement_minor`.
* Implemented `check_financial_consistency(booking_id)`:
  - Asserts exact integer equality: `booking.total_refunded_minor == sum(refund_records) == sum(ledger_refund_entries)`.

### 3.11. `tests/test_webhook_adversarial.py`
* Authored comprehensive 14-test suite validating all functional, adversarial, concurrency, security, invariant, and network isolation requirements.

---

## 4. DATABASE MIGRATIONS & SCHEMA HARDENING

The aggregator SQLite database (`aggregator.db`) was updated with explicit schema migrations preserving all existing records:

```sql
-- 1. Webhook Events Table Migration
ALTER TABLE webhook_events ADD COLUMN amount_minor INTEGER DEFAULT 0;
ALTER TABLE webhook_events ADD COLUMN currency TEXT DEFAULT 'INR';
ALTER TABLE webhook_events ADD COLUMN payment_id TEXT;
ALTER TABLE webhook_events ADD COLUMN refund_id TEXT;

-- 2. Composite Uniqueness Enforcement
CREATE UNIQUE INDEX IF NOT EXISTS uidx_wh_provider_event 
ON webhook_events (provider, event_id);

-- 3. Invariant Ledger Index
CREATE INDEX IF NOT EXISTS idx_ledger_event 
ON ledger_entries (booking_id, entry_type);
```

---

## 5. ACTUAL TEST EXECUTION RESULTS

### 5.1. Milestone 2.1 Test Suite Run (`test_webhook_adversarial.py`)
Command: `uv run pytest -v tests/test_webhook_adversarial.py`
Output:
```
============================= test session starts =============================
platform win32 -- Python 3.11.15, pytest-9.2.0, pluggy-1.7.0
cachedir: .pytest_cache
rootdir: C:\Users\jayp9\.gemini\antigravity\scratch\crosswire
configfile: pytest.ini
plugins: anyio-4.16.0, asyncio-1.4.1.post1
asyncio: mode=Mode.AUTO, debug=False
collected 14 items

tests/test_webhook_adversarial.py::test_successful_single_refund_webhook PASSED [  7%]
tests/test_webhook_adversarial.py::test_duplicate_sequential_delivery PASSED [ 14%]
tests/test_webhook_adversarial.py::test_duplicate_concurrent_delivery PASSED [ 21%]
tests/test_webhook_adversarial.py::test_invalid_signature_rejection PASSED [ 28%]
tests/test_webhook_adversarial.py::test_same_event_id_with_modified_payload PASSED [ 35%]
tests/test_webhook_adversarial.py::test_missing_event_id_rejected PASSED [ 42%]
tests/test_webhook_adversarial.py::test_already_processed_event_idempotent PASSED [ 50%]
tests/test_webhook_adversarial.py::test_refund_exceeding_eligible_entitlement_rejected PASSED [ 57%]
tests/test_webhook_adversarial.py::test_concurrent_different_refund_events_against_same_entitlement PASSED [ 64%]
tests/test_webhook_adversarial.py::test_ledger_consistency_invariant PASSED [ 71%]
tests/test_webhook_adversarial.py::test_fixed_mode_prevents_duplicate_credits PASSED [ 78%]
tests/test_webhook_adversarial.py::test_vulnerable_mode_detection_and_evidence_generation PASSED [ 85%]
tests/test_webhook_adversarial.py::test_tcp_webhook_concurrent_delivery PASSED [ 92%]
tests/test_webhook_adversarial.py::test_no_external_network_access PASSED [100%]

============================= 14 passed in 3.19s ==============================
```

### 5.2. Complete Project Regression Run (All 47 Tests)
Command: `uv run pytest -v`
Output:
```
============================= test session starts =============================
platform win32 -- Python 3.11.15, pytest-9.2.0, pluggy-1.7.0
cachedir: .pytest_cache
rootdir: C:\Users\jayp9\.gemini\antigravity\scratch\crosswire
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.16.0, asyncio-1.4.1.post1
asyncio: mode=Mode.AUTO, debug=False
collected 47 items

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

============================= 47 passed in 18.24s =============================
```

---

## 6. VULNERABLE MODE VS. FIXED MODE EVIDENCE

### 6.1. Vulnerable Mode Output: Security Finding CW-SEC-M2-001
When Aggregator is set to `AggregatorMode.VULNERABLE` and replayed with a signed refund webhook 3 times:
```text
======================================================================
CROSSWIRE SECURITY FINDING: CW-SEC-M2-001
======================================================================
Vulnerability Category: Business Logic / Webhook Replay Vulnerability
Severity               : CRITICAL (Replaying a signed refund webhook resulted in duplicate financial credits posted to the ledger.)
Affected Transaction   : TBOH-INTENT-EVID-01
Provider Event ID      : evt_vuln_demo_99 (Provider: mock_payment_provider_99)
Correlation ID         : corr_alpha_INTENT-EVID-01_1794106259
----------------------------------------------------------------------
Delivery Count         : 3
Processing Attempts    : 3
Duplicate Ledger IDs   : [631, 632, 633]
Recorded Refund IDs    : ['RFD-DEMO-99', 'RFD-DEMO-99-DUP-2', 'RFD-DEMO-99-DUP-3']
Expected Refund Minor  : 500000 INR
Actual Refund Minor    : 1500000 INR
Excess Money Disbursed : 1000000 INR
----------------------------------------------------------------------
Reproduction: Deliver signed refund webhook payload with event_id 'evt_vuln_demo_99' multiple times to POST /api/v1/webhooks/payment.
Remediation : Implement database-level atomic event deduplication with provider-scoped unique constraints on (provider, event_id) and conditional UPDATE locks.
Retest Status: VERIFIED_VULNERABLE
======================================================================
```

### 6.2. Fixed Mode Output: Idempotent Replay & Deduplication
When Aggregator is set to `AggregatorMode.FIXED` and replayed with the same signed refund webhook:
- **First Delivery:** HTTP 200 `{"status": "PROCESSED", "duplicate": false, "total_refunded_minor": 500000, "message": "Refund webhook processed successfully"}`
- **Subsequent Deliveries (2 to 10):** HTTP 200 `{"status": "PROCESSED", "duplicate": true, "total_refunded_minor": 500000, "message": "Duplicate webhook received; idempotent response returned"}`
- **Database Ground Truth:**
  - `COUNT(refund_records) == 1`
  - `COUNT(ledger_entries WHERE entry_type='REFUND') == 1`
  - `total_refunded_minor == 500000`
  - `webhook_events.delivery_count == 10`

---

## 7. MILESTONE 2.1 ACCEPTANCE CRITERIA VERIFICATION

| # | Requirement | Verification Method | Status |
|---|-------------|---------------------|:------:|
| 1 | Exact Integer Minor Units | Verified `amount_minor` throughout models, DB, and invariants | PASS |
| 2 | Atomic Entitlement & Refund | Exclusive `BEGIN IMMEDIATE` lock & conditional reservation | PASS |
| 3 | Concurrent Competing Refunds | Test 9 asserts mutual exclusion under entitlement limit | PASS |
| 4 | HMAC-SHA256 Signatures | Constant-time validation; Test 4 rejects invalid signatures | PASS |
| 5 | Payload Conflict Detection | Test 5 rejects modified payload under same event ID (409) | PASS |
| 6 | Missing Event ID Rejection | Test 6 rejects missing `event_id` with HTTP 400 | PASS |
| 7 | Invariant Engine Assertions | Webhook Idempotency, Refund Boundedness, Consistency | PASS |
| 8 | Vulnerable Mode Finding | Generates structured evidence report `CW-SEC-M2-001` | PASS |
| 9 | 10-Request ASGI Concurrency | Test 3 fires 10 simultaneous requests over ASGI | PASS |
| 10 | 10-Request Real TCP Sockets | Test 13 fires 10 simultaneous requests over port 8012 | PASS |
| 11 | Complete Sandbox Offline Isolation | Test 14 socket monkeypatch confirms zero external calls | PASS |
| 12 | Regression Test Suite | 47/47 passing tests across entire project | PASS |

---

## 8. REMAINING RISKS & LIMITATIONS

1. **SQLite Concurrency Contention:** High concurrent load (e.g. >100 simultaneous writers) on SQLite can experience `database is locked` if busy timeouts are short. Mitigated in our test harness by using `busy_timeout=5000ms` and `BEGIN IMMEDIATE`.
2. **Double-Entry General Ledger Separation:** Customer-facing statement queries (`get_ledger_entries_for_booking`) intentionally return single-leg activity entries (`CREDIT` for refund), while general ledger balancing (`record_double_entry_batch`) writes dual debit/credit journal entries.
3. **No External Payment Gateway:** All gateways and webhooks remain synthetic and local.

---

## 9. NEXT MILESTONE TRANSITION

Milestone 2.1 is completed and fully verified. In accordance with system instructions, **execution is halted here to await independent engineering review before starting Milestone 2.2** (Concurrent Booking & Wallet Balance Manipulation).

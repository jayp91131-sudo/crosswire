# CROSSWIRE 2.0 — MILESTONE 2.1 REMEDIATION CHANGELOG
**Generated:** 2026-11-09  
**Specification:** `MASTER_PLAN_V2.md`  
**Reference Audit:** `MILESTONE_2_1_INDEPENDENT_ENGINEERING_REVIEW.md`  
**Status:** ALL 7 REVIEW FINDINGS RESOLVED — 64/64 AUTOMATED TESTS PASSING  

---

## Executive Summary

The Milestone 2.1 Remediation Sprint resolved all 7 security, architectural, and financial transaction integrity findings (`CW-REV-M2-001` through `CW-REV-M2-007`) identified in `MILESTONE_2_1_INDEPENDENT_ENGINEERING_REVIEW.md`.

Prior to this remediation, while Milestone 2.1 demonstrated duplicate webhook idempotency and invariant detection, the implementation harbored vulnerabilities including a global static secret, unvalidated entity relationships, single-column primary keys causing multi-provider collision crashes, non-relational `LIKE '%{event_id}%'` causal queries, missing timestamp replay protection, inaccurate reporting terminology, and missing schema migration verification.

All vulnerabilities have been systematically remediated with zero regression to baseline capabilities:
- **Baseline suite:** 47/47 tests passing.
- **Remediation & regression suite:** 17 new tests passing (15 remediation + 1 migration + 1 atomicity).
- **Total test coverage:** 64/64 tests passing in 15.91s.
- **Safety guarantee:** 100% offline local sandbox execution (no external network calls).

---

## Remediation Matrix

| Finding ID | Severity | Category | Description & Impact | Resolution Summary | Verified By |
|---|---|---|---|---|---|
| **CW-REV-M2-001** | HIGH | Security / Auth | Global static shared webhook secret; unregistered providers accepted if signed with static secret | Implemented `ProviderSecretRegistry` with per-provider synthetic secrets; disabled/unregistered providers rejected with HTTP 401; dynamic secret resolution in `WebhookPayload.sign()` and `process_webhook()` | `tests/test_webhook_remediation.py::test_provider_authentication_*` |
| **CW-REV-M2-002** | HIGH | Integrity / Auth | Entity relationships unchecked (`payment_id`, `currency`, `event_type`, booking state machine, authorized refund reconciliation) | Enforced strict entity cross-validation; payment ownership & provider matching; currency parity; supported event type whitelist; booking lifecycle gating; pre-authorized refund reconciliation | `tests/test_webhook_remediation.py::test_entity_validation_*` |
| **CW-REV-M2-003** | HIGH | Architecture / Schema | Physical table collision: `PRIMARY KEY (event_id)` prevented multi-provider duplicate event IDs | Re-architected `webhook_events` to `PRIMARY KEY (provider, event_id)` with transactional SQLite table migration; provider-isolated transaction refs (`TXN-WH-{provider}-{event_id}`) | `tests/test_webhook_remediation.py::test_multi_provider_same_event_id_collision_safe`, `test_webhook_migration.py` |
| **CW-REV-M2-004** | MEDIUM | Traceability / Data | `LIKE '%{event_id}%'` caused causal attribution cross-contamination on overlapping event IDs | Added relational `provider` and `event_id` columns with indexes to `ledger_entries` and `refund_records`; query engine updated to exact relational equality | `tests/test_webhook_remediation.py::test_exact_relational_traceability_no_like_substring_collision` |
| **CW-REV-M2-005** | MEDIUM | Integrity / Replay | Unbounded timestamp freshness window allowed replay of stale historical unseen events | Added ISO-8601 parsing & 5-minute freshness window (`MAX_CLOCK_SKEW_SECONDS = 300`); stale/future unseen rejected (HTTP 400); clock injectable; old duplicate retries remain idempotent (HTTP 200) | `tests/test_webhook_remediation.py::test_stale_unseen_webhook_rejected`, `test_old_duplicate_retry_bypasses_freshness_window` |
| **CW-REV-M2-006** | LOW | Audit Accuracy | Inaccurate finding terminology: "Excess Money Disbursed" claimed outbound cash transfers | Updated finding `CW-SEC-M2-001` in `engine/evidence.py` to "Excess Internal Ledger Credit Posted" and "Unbounded Accounting Liability" | `tests/test_webhook_adversarial.py::test_vulnerable_mode_detection_and_evidence_generation` |
| **CW-REV-M2-007** | LOW | Test Coverage | Zero test coverage for legacy `webhook_events` SQLite migration | Authored `tests/test_webhook_migration.py` verifying legacy schema migration, data preservation, composite PK enforcement, and idempotency | `tests/test_webhook_migration.py::test_legacy_schema_migration_preserves_data_and_enforces_composite_pk` |

---

## Detailed File Changes

### 1. `target_platform/models.py`
- **`ProviderSecretRegistry` & `SYNTHETIC_PROVIDER_SECRETS`:**
  - Added discrete synthetic secrets: `mock_pay_provider`, `stripe_synthetic`, `razorpay_synthetic`, `adyen_synthetic`.
  - Added `ProviderSecretRegistry` class supporting `register_provider`, `get_secret`, `is_registered`, `disable_provider`.
  - Added global singleton accessor `get_provider_registry()`.
- **Timestamp Freshness:**
  - Added `MAX_CLOCK_SKEW_SECONDS = 300` (5 minutes).
  - Added `WebhookTimestampError`, `StaleWebhookEventError`, and `FutureWebhookEventError`.
  - Added `parse_iso_timestamp(ts_str)` supporting UTC offsets and "Z" suffixes.
  - Added `validate_timestamp_freshness(ts_str, max_skew_seconds, now_func)` with custom injectable clock support.
- **Entity Validation Exceptions:**
  - Added `UnregisteredProviderError`, `InvalidWebhookSignatureError`.
  - Added `UnknownRefundError`, `PaymentMismatchError`, `CurrencyMismatchError`, `UnsupportedWebhookEventError`, `InvalidBookingStateForRefundError`.
  - Added `SUPPORTED_REFUND_EVENT_TYPES = {"refund.processed", "refund.succeeded"}`.
- **Models Updated:**
  - `WebhookPayload`: dynamically resolves secret from `get_provider_registry().get_secret(self.provider_id)` in `sign()`.
  - `RefundAuthorizationRequest`: added model for pre-authorizing refunds with path/body parameter flexibility and minor unit validator.
  - `RefundRecord`: added `provider: Optional[str] = None` and `event_id: Optional[str] = None`.
  - `LedgerEntry`: added `provider: Optional[str] = None` and `event_id: Optional[str] = None`.
  - `PaymentTransactionRecord`: added `provider: str = "mock_pay_provider"`.

### 2. `target_platform/aggregator/database.py`
- **Schema Migration (`_migrate_schema`):**
  - Updated DDL for `webhook_events` to `PRIMARY KEY (provider, event_id)`.
  - Added automated table recreation migration from legacy `webhook_events` to `webhook_events_v2` with `PRIMARY KEY (provider, event_id)`, preserving all historical data and default values.
  - Added `provider` column to `payment_transactions`, `refund_records`, and `ledger_entries`.
  - Added `event_id` column to `refund_records` and `ledger_entries`.
  - Added indexes: `idx_ledger_event_exact`, `idx_ledger_event_id`, `idx_refund_event_exact`, `idx_refund_event_id`, `uidx_wh_provider_event`.
- **Pre-Authorization Methods:**
  - Implemented `authorize_refund(...)` and `get_refund_record(...)`.
- **Exact Relational Querying:**
  - Updated `get_ledger_entries_for_event(event_id, provider=None)` to use exact equality matching (`WHERE provider = ? AND event_id = ?` or `WHERE event_id = ?`) with fallback for pre-migration historical rows.
- **Webhook Processing (`process_webhook_event_fixed`):**
  - Composite PK deduplication `(provider, event_id)`.
  - Strict validation of `event_type`, booking state machine, payment ID & provider match, currency parity.
  - Reconciliation against authorized refund instruction (`payload.amount_minor <= rfd_row["amount_minor"]`).
  - Atomic entitlement check and conditional `UPDATE booking_records`.
  - Transition refund record to `COMPLETED`, setting `provider`, `event_id`, and `transaction_reference = f"TXN-WH-{provider}-{event_id}"`.
  - Insert exactly 1 ledger entry setting `provider`, `event_id`, and `journal_batch_id = f"BATCH-WH-{provider}-{event_id}"`.
  - Full rollback via `ROLLBACK` on any validation failure or intermediate error.
- **Vulnerable Processing (`process_webhook_event_vulnerable`):**
  - Updated to `ON CONFLICT(provider, event_id) DO UPDATE ...`.
  - Populates `provider` and `event_id` in duplicate refund records and ledger entries.

### 3. `target_platform/aggregator/booking_service.py`
- **Provider Authentication Integration:**
  - Injected `self.provider_registry = get_provider_registry()`.
  - Dynamic provider secret resolution; rejects unregistered providers with `UnregisteredProviderError`.
  - Verifies HMAC-SHA256 signature against provider-specific secret.
- **Timestamp Freshness & Replay Routing:**
  - Checks if `(provider, event_id)` already exists in DB.
  - Replays of already-processed events bypass freshness window and return `IDEMPOTENT_REPLAY` (HTTP 200).
  - Unseen events undergo `validate_timestamp_freshness(...)` within `max_clock_skew_seconds`.
- **Refund Authorization:**
  - Added `authorize_refund(...)` method delegating to database.

### 4. `target_platform/aggregator/app.py`
- Added endpoint `POST /api/v1/bookings/{booking_id}/refunds/authorize` requiring agency authentication and returning `RefundRecord`.
- Mapped all new domain exceptions to explicit HTTP status codes:
  * 401: `InvalidWebhookSignatureError`, `UnregisteredProviderError`
  * 409: `WebhookPayloadConflictError`
  * 400: `ExcessiveRefundError`, `WebhookTimestampError`, `StaleWebhookEventError`, `FutureWebhookEventError`, `UnsupportedWebhookEventError`, `InvalidBookingStateForRefundError`, `PaymentMismatchError`, `CurrencyMismatchError`
  * 404: `UnknownRefundError`, `ValueError`

### 5. `target_platform/agency_client.py`
- Added `authorize_refund(booking_id, refund_id, amount_minor, amount, currency, payment_id, reason, idempotency_key, provider)` client helper.

### 6. `engine/evidence.py` & `engine/invariant_engine.py` & `engine/ground_truth_oracle.py`
- Corrected terminology in `CW-SEC-M2-001` (`engine/evidence.py`):
  * "Excess Money Disbursed" -> "Excess Internal Ledger Credit Posted: {amount} {currency} (Unbounded Accounting Liability)".
  * Severity justification updated to unbounded internal accounting liability and ledger credit manipulation.
- Updated `engine/ground_truth_oracle.py` and `engine/invariant_engine.py`:
  * `get_ledger_entries_for_event(event_id, provider=None)` passes `provider` for exact relational filtering.
  * `matching_refund_ids` matches exact `event_id` and `provider`.

### 7. New Automated Test Suites
- `tests/test_webhook_remediation.py` (15 tests):
  * Registered provider verification.
  * Wrong secret rejection.
  * Unregistered provider rejection.
  * Multi-provider same `event_id` collision safety.
  * Payment ID mismatch rejection.
  * Currency mismatch rejection.
  * Unsupported event type rejection.
  * Uncancelled booking refund rejection.
  * Unknown refund instruction rejection.
  * Refund exceeding authorized amount rejection.
  * Exact relational traceability (no substring collision between `"1"`, `"10"`, `"TXN-1"`).
  * Stale unseen webhook rejection (> 300s).
  * Future unseen webhook rejection (> 300s).
  * Malformed timestamp rejection.
  * Old duplicate retry replay window bypass.
- `tests/test_webhook_migration.py` (1 test):
  * Automated migration of legacy Milestone 2.1 schema to composite PK `(provider, event_id)` with full historical data preservation.
- `tests/test_webhook_atomicity.py` (1 test):
  * Simulated disk/lock fault injection during intermediate writes proving complete atomic rollback (zero side effects in booking, refund, ledger, or webhook tables) and successful subsequent retry.

---

## Verification Summary

- **Execution Command:**
  ```powershell
  $env:PYTHONPATH="C:\Users\jayp9\.gemini\antigravity\scratch\crosswire;C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\.venv\Lib\site-packages"
  & "C:\Users\jayp9\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe" -m pytest -v
  ```
- **Execution Result:**
  ```text
  ============================= 64 passed in 15.91s =============================
  ```
- **Test Breakdown:**
  * `tests/test_auth_security.py`: 4 passed
  * `tests/test_booking.py`: 2 passed
  * `tests/test_financial_hardening.py`: 5 passed
  * `tests/test_idempotency.py`: 3 passed
  * `tests/test_invariants.py`: 4 passed
  * `tests/test_payment_and_lifecycle.py`: 10 passed
  * `tests/test_supplier_timeout.py`: 2 passed
  * `tests/test_tcp_integration.py`: 3 passed
  * `tests/test_webhook_adversarial.py`: 14 passed
  * `tests/test_webhook_atomicity.py`: 1 passed
  * `tests/test_webhook_migration.py`: 1 passed
  * `tests/test_webhook_remediation.py`: 15 passed
  * **Total: 64 passed, 0 failed, 0 skipped.**

# CROSSWIRE 2.0 — FINAL DISCREPANCY RESOLUTION REPORT

**Platform:** Crosswire Business-Logic & Transaction-Integrity DAST  
**Version:** 2.0.0 (Gold Release Build)  
**Date:** November 16, 2026  
**Scope:** Exhaustive Register of Identified Discrepancies, Source Audit, Implementation Corrections & Regression Verification  

---

## 1. PURPOSE & AUDIT METHODOLOGY

During the transition from Milestone 2.9 to the Crosswire 2.0 closeout, an exhaustive audit was conducted across the source code, test suites, architecture documents, and historical completion reports. This audit revealed multiple conflicts between documented claims and actual source code behavior.

In accordance with Section 1 of the Release Mission, an internal Discrepancy Register was established. Every discrepancy was investigated down to the Python AST, execution traces, database queries, and pytest assertions. 

This document records each identified discrepancy, the conflicting claims, the underlying ground truth, the code/doc changes made, and the regression tests ensuring permanent resolution.

---

## 2. DISCREPANCY REGISTER & RESOLUTION MATRIX

### Discrepancy 1: Fail-Open Reporting & Lack of Invariant Evidence Contract
- **Conflicting Claims:**
  - *Defect Identified:* In earlier builds, `ForensicReportingEngine.build_report()` returned `PASS` whenever no finding existed, even if no events had been captured, ground truth was unverified, or no explicit invariant checks had been performed.
  - *Engineering Reality:* Absence of a finding does NOT constitute proof of an invariant passing. A successful HTTP response does NOT guarantee database integrity.
- **Root Cause:** A placeholder default in `build_report()` that fell back to `PASS` when `finding is None`.
- **Resolution:** Re-architected `build_report()` with strict fail-closed verdict rules:
  1. `FAIL`: Confirmed finding or explicit ground-truth invariant failure or failed retest.
  2. `PASS`: All required invariant evaluations completed successfully (`True`) with sufficient, verified ground-truth observations.
  3. `INCONCLUSIVE`: Empty execution trace, missing observations, missing invariant results, missing or empty required invariants, or contradictory evidence.
- **Verification Test:** `tests/test_reporting_fail_closed.py` (8 focused regression tests verifying all fail-closed rules and unknown/empty requirement rejection).

---

### Discrepancy 2: Evidence Integrity Scope & Full-Dossier Provenance
- **Conflicting Claims:**
  - *Defect Identified:* SHA-256 evidence digests originally hashed only `finding.evidence`, leaving the runtime event trace, database observations, execution IDs, and retest evidence unprotected.
  - *Marketing Overstatement:* Historical text casually referred to "blockchain immutability".
- **Resolution:**
  - Implemented `get_canonical_evidence_bundle()` covering the entire forensic dossier: `report_id`, `execution_id`, `scenario_id`, `scenario_name`, `target_id`, `target_name`, `generated_at_utc`, `verdict`, `affected_transaction`, `violated_invariant`, `expected_state`, `actual_state`, `root_cause`, `cwe`, `severity`, `required_invariants`, `invariant_evaluations`, `runtime_events_count`, `runtime_event_trace`, `ground_truth_observations`, `finding_evidence`, `evidence_bundle`, `reproduction_instructions`, `remediation_guidance`, and `retest_outcome`.
  - Deterministically serialized via `json.dumps(..., sort_keys=True)` and protected with SHA-256.
  - Verified that mutating event data, observations, findings, retest evidence, verdict, affected transaction amount, expected state, or required invariants immediately causes `verify_integrity()` to fail.
  - Expunged all blockchain claims; documented exact local SHA-256 integrity scope.
- **Verification Test:** `tests/test_reporting_fail_closed.py::test_rule_7_full_dossier_tamper_detection`.

---

### Discrepancy 3: Supplier Idempotency Key Specification in SCN-TIMEOUT-001
- **Conflicting Claims:**
  - *Documentation Claim:* `CROSSWIRE_SECURITY_SCENARIO_COVERAGE.md:30` stated that retries used `hash(intent_id + attempt)`, which caused duplicate supplier reservations because the key changed on each attempt.
  - *Implementation Reality:* `target_platform/aggregator/booking_service.py:753` actually generated `supplier_idemp_key = f"IDEMP-TXN-{intent.intent_id}"`. This key remained perfectly stable across all retry attempts. The vulnerability in vulnerable mode arose because the mock supplier lacked idempotency support, or the aggregator failed to query supplier status before issuing a new booking request.
- **Resolution:** Corrected documentation to document the actual mechanism: the key is `f"IDEMP-TXN-{intent.intent_id}"`. Fixed mode remediation enforces capability-aware lookup (`reconcile_supplier_intent`) before retrying.
- **Verification Test:** `tests/test_all_scenarios.py::test_timeout_scenario_portable` and `tests/test_supplier_timeout.py`.

---

### Discrepancy 4: TravelSandboxB Persistence & Data Architecture
- **Conflicting Claims:**
  - *Previous Assumption:* Ambiguity regarding whether TravelSandboxB used an external PostgreSQL instance, file-backed SQLite, or pure Python in-memory state.
  - *Implementation Reality:* `travel_sandbox_b/db.py:27` opens `sqlite3.connect(":memory:", check_same_thread=False)` with a dedicated mutex lock. It maintains independent tables (`tsb_booking_intents`, `tsb_supplier_reservations`, `tsb_agency_wallets`, `tsb_financial_ledger`, `tsb_processed_webhooks`) completely isolated from Target A.
- **Resolution:** Formally documented that TravelSandboxB is an in-memory SQLite relational platform with independent tables and thread-safe locking. Clarified that zero Target A models or database helpers are imported. Removed any claim of "zero-code target onboarding" since implementing target adapters requires mapping target-specific REST/SQL semantics.
- **Verification Test:** `tests/test_target_adapters.py` (all tests passing against `travel_sandbox_b`).

---

### Discrepancy 5: Target Portability Claims & Scope of Cross-Target Execution
- **Conflicting Claims:**
  - *Documentation Overstatement:* Implying that all 10 scenarios were fully executed across both Target A and TravelSandboxB.
  - *Test Reality:* SCN-TIMEOUT-001 and SCN-WEBHOOK-001 are implemented and proven on both Target A and TravelSandboxB. The remaining scenarios are exercised against Target A. Furthermore, Target B intentionally does NOT advertise `MULTI_SUPPLIER_OUTBOX`.
- **Resolution:** Clearly documented the two distinct cross-target scenario classes. Formally noted that Target B does not support outbox dispatch sagas and fails-closed when outbox capabilities are requested (`UnsupportedCapabilityError`).
- **Verification Test:** `tests/test_target_adapters.py::test_unsupported_capability_fail_closed`.

---

### Discrepancy 6: Canonical Invariant Definitions & Removal of Synthetic INV-6.1
- **Conflicting Claims:**
  - *Previous Reports:* Referenced a synthetic invariant `INV-6.1: Non-Negative Available Balance`.
  - *Canonical Specification:* `MASTER_PLAN_V2.md` Section 5 defines `INV-5.1` as the comprehensive wallet invariant: `Wallet balance conservation and active hold reservation bounds` (`balance_minor >= 0` and `available_minor = balance_minor - active_holds_minor >= 0`).
- **Resolution:** Refactored active source code (`control_center/app.py`, `control_center/engine_bridge.py`, `control_center/templates/index.html`, `engine/portable_scenarios/portable_wallet.py`, `engine/change_detector.py`) and documentation. Expunged all occurrences of synthetic `INV-6.1` and standardized on canonical `INV-5.1`.
- **Verification Test:** `tests/test_all_scenarios.py::test_wallet_concurrency_vulnerable_and_fixed`.

---

### Discrepancy 7: Webhook Replay & Refund Boundedness Quantification (SCN-WEBHOOK-001 & SCN-ABUSE-001)
- **Conflicting Claims:**
  - *Documentation Conflict:* Conflicting monetary figures for booking amount, cancellation penalties, and refundable entitlements.
  - *Implementation Baseline:* Reconciled 15,000 INR booking with 3,000 INR cancellation penalty, yielding a net refundable entitlement of 12,000 INR.
- **Resolution:**
  - Reconciled SCN-WEBHOOK-001: 3 deliveries of 5,000 INR yield 15,000 INR in vulnerable mode, violating `INV-5.3` (count = 3 > 1) and `INV-5.5` (total 15,000 INR > 12,000 INR entitlement). In fixed mode, exactly 1 credit of 5,000 INR is applied, satisfying both `INV-5.3` and `INV-5.5`.
  - Reconciled SCN-ABUSE-001: Initial refund of 12,000 INR succeeds (legitimate partial refund after penalty). Secondary refund of 15,000 INR is rejected in fixed mode because cumulative refunds would reach 27,000 INR > 12,000 INR entitlement.
  - Added positive control regression test verifying legitimate partial refunds are not falsely flagged.
- **Verification Test:** `tests/test_all_scenarios.py::test_positive_control_legitimate_cancellation_and_partial_refund`.

---

### Discrepancy 8: Tool Benchmarking Realities & Removal of Fabricated Latencies
- **Conflicting Claims:**
  - *Previous Report Claim:* Reported precise execution millisecond values for external tools (e.g. OWASP ZAP 145,000 ms, Postman 12,000 ms, MockCard 1,800 ms, Toxiproxy 800 ms), implying they had been executed live on the test machine.
  - *Environment Reality:* Uninstalled tools were not executed. Toxiproxy is a Layer-4 fault proxy, not a vulnerability scanner.
- **Resolution:**
  - Introduced `RawBenchmarkRunRecord` dataclass; refactored `BenchmarkHarness` to derive all metrics dynamically from raw run records.
  - Tools marked `NOT_RUN` (ZAP, Nuclei, Postman, MockCard) have empirical TP, FP, FN, precision, recall, F1, latency, and reproducibility dynamically evaluated to `None`. Scenarios marked `NOT_RUN`.
  - Toxiproxy classified as `NOT_COMPARABLE (FAULT INJECTOR ONLY)`.
  - Conventional Custom Tests classified as limited functional baseline derived from empirical test executions.
  - Crosswire 2.0 metrics preserved as measured facts on synthetic testbeds.
- **Verification Test:** `tests/test_benchmarks_evidence.py` and `tests/test_benchmarks_and_safety.py`.

---

### Discrepancy 9: Change-Aware Testing & Empty Git Diff Fallback
- **Conflicting Claims:**
  - *Implementation Bug:* When `git diff` returned empty, `engine/change_detector.py` injected 3 fake baseline files, artificially selecting 4 scenarios.
- **Resolution:** Removed the fallback code entirely. `get_git_diff_summary()` cleanly returns an empty list if no files have changed. Clarified that change detection operates via local `git diff`, not external CI/CD webhooks.
- **Verification Test:** `tests/test_inventory_and_change.py::test_change_detector_git_diff_and_impact`.

---

### Discrepancy 10: Operational Safety Claims & Production Impact Scope
- **Conflicting Claims:**
  - *Marketing Claim:* "Zero accidental production impact guarantee under all circumstances."
- **Resolution:** Replaced absolute claims with precise architectural controls: enforced local allowlisting (`target_a`, `travel_sandbox_b`), synthetic header inspection, token rate-limiting, concurrency caps, and an emergency stop kill switch (`POST /api/safety/emergency-stop`). Explicitly documented residual risks.
- **Verification Test:** `tests/test_benchmarks_and_safety.py::test_safety_emergency_stop_lifecycle`.

---

## 3. AUDIT CONCLUSION

All 10 discrepancies have been resolved. The code, tests, documentation, and release artifacts now exist in 100% agreement. No unsubstantiated claims or synthetic workarounds remain in Crosswire 2.0.

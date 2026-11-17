# CROSSWIRE 2.0 — FINAL RELEASE NOTES

**Platform:** Crosswire Business-Logic & Transaction-Integrity DAST  
**Version:** 2.0.0 (Gold Release Build)  
**Release Date:** November 16, 2026  
**Build Status:** Certified & 100% Passing (332 Tests)  

---

## 1. RELEASE HIGHLIGHTS

Crosswire 2.0 represents the complete, reconciled, and hardened release of the Crosswire platform. Moving beyond synthetic testbeds, Crosswire 2.0 delivers an extensible, target-independent DAST engine specifically engineered to uncover distributed transaction vulnerabilities, race conditions, and financial ledger defects across multi-party B2B travel ecosystems.

### Major Highlights:
- **10 Complete Portable Security Scenarios:** Covering supplier commit timeouts, webhook replay, wallet double-spend, cancellation races, BOLA/IDOR, privilege escalation, fare tampering, step skipping, refund abuse, and outbox sagas.
- **Vulnerable vs. Fixed Duality:** Every scenario operates in both vulnerable and fixed modes, enabling automated verification of developer remediations.
- **Strict Fail-Closed Invariant Oracle:** Evaluates canonical transaction invariants (`INV-5.1` to `INV-5.7`) against ground-truth database state; strictly emits `INCONCLUSIVE` instead of false `PASS` on missing observations or missing invariant evaluations.
- **Target Adapter Framework:** Target independence validated across Target A and the independent in-memory SQLite platform `TravelSandboxB` (on supported capabilities).
- **Full-Dossier Cryptographic Provenance:** Every execution dossier includes a SHA-256 evidence bundle digest covering event trace, database observations, invariant results, finding evidence, and retest evidence with an automated `verify_integrity()` validation method.
- **M2.7.1 Forensic Operations Console:** Dark-mode Swiss editorial console featuring an interactive execution timeline, non-clipping execution tables, and detailed CWE dossiers.
- **Honest Empirical Benchmarking:** Transparent tool comparison matrix distinguishing live testbed executions from qualitative architectural baseline evaluations.
- **Operational Hardening:** Local loopback perimeter enforcement, synthetic credential quarantine, rate-limiting, and an emergency stop kill switch.

---

## 2. REPOSITORY FILE LAYOUT & PACKAGE MANIFEST

```
crosswire/
├── control_center/              # M2.7.1 Forensic Operations Console
│   ├── app.py                   # FastAPI backend server
│   ├── static/                  # Swiss industrial web application
│   │   ├── index.html           # Single-page console interface
│   │   ├── app.js               # Reactive workspace logic
│   │   └── style.css            # Swiss typography & dark palette
│   └── tests/                   # Console backend tests (15 tests)
├── engine/                      # Core Crosswire Engine
│   ├── benchmarking.py          # Empirical Tool Benchmarking Suite
│   ├── change_detector.py       # Local Git Change-Aware Testing Engine
│   ├── event_collector.py       # Canonical Event Instrumentation Engine
│   ├── inventory.py             # On-Demand API & Async Inventory Scanner
│   ├── portable_scenarios/      # 10 Standardized Transaction Scenarios
│   │   ├── base.py              # Scenario base classes & contracts
│   │   ├── portable_timeout.py  # SCN-TIMEOUT-001 (Supplier Timeout)
│   │   ├── portable_webhook.py  # SCN-WEBHOOK-001 (Webhook Replay)
│   │   ├── portable_wallet.py   # SCN-WALLET-001 (Wallet Double-Spend)
│   │   ├── portable_race.py     # SCN-RACE-001 (Cancellation Race)
│   │   ├── portable_bola.py     # SCN-BOLA-001 (Cross-Agency BOLA)
│   │   ├── portable_priv.py     # SCN-PRIV-001 (Privilege Escalation)
│   │   ├── portable_tamper.py   # SCN-TAMPER-001 (Fare Tampering)
│   │   ├── portable_skip.py     # SCN-SKIP-001 (Payment Skipping)
│   │   ├── portable_abuse.py    # SCN-ABUSE-001 (Refund Abuse)
│   │   └── portable_outbox.py   # SCN-OUTBOX-001 (Outbox Multi-Supplier)
│   ├── reporting.py             # Fail-Closed Forensic Dossier & SHA-256 Engine
│   ├── safety.py                # Operational Allowlisting & Kill Switch
│   └── target_adapters/         # Target Abstraction Framework
│       ├── base.py              # Execution & Observation Contracts
│       ├── registry.py          # Dynamic Target Registry
│       ├── target_a.py          # Target A Adapter Implementation
│       └── travel_sandbox_b.py  # TravelSandboxB Adapter Implementation
├── m2_3b/                       # Multi-Supplier Outbox & Sagas (54 tests)
├── m2_3c/                       # Structured Findings & Formatter (7 tests)
├── target_platform/             # Reference Target A Implementation
├── travel_sandbox_b/            # Reference In-Memory SQLite Target B
├── tests/                       # Core Scenario & Security Tests (256 tests)
├── pytest.ini                   # Unified test discovery configuration
├── requirements.txt             # Dependency declarations
├── CROSSWIRE_FINAL_ACCEPTANCE_REPORT.md
├── CROSSWIRE_FINAL_DISCREPANCY_RESOLUTION.md
├── CROSSWIRE_FINAL_TEST_RESULTS.md
├── CROSSWIRE_FINAL_BENCHMARK_EVIDENCE.md
├── CROSSWIRE_FINAL_OPERATOR_GUIDE.md
└── CROSSWIRE_2_0_FINAL_RELEASE_NOTES.md
```

---

## 3. SUMMARY OF DISCREPANCIES RESOLVED IN THIS RELEASE

1. **Fail-Closed Reporting:** Fixed fail-open bug where missing findings produced PASS without invariant evidence.
2. **Full-Dossier Provenance:** Expanded SHA-256 evidence integrity to cover event trace, observations, findings, and retest evidence.
3. **Supplier Idempotency:** Corrected documentation to reflect stable `IDEMP-TXN-{intent_id}` keys across retries in SCN-TIMEOUT-001.
4. **TravelSandboxB Architecture:** Formally documented as in-memory SQLite (`:memory:`) with dedicated mutex locking and custom tables; confirmed zero Target A model imports.
5. **Target Portability Scope:** Accurately documented that SCN-TIMEOUT-001 and SCN-WEBHOOK-001 run on both targets, while remaining scenarios run on Target A; Target B intentionally lacks outbox support.
6. **Canonical Invariants:** Expunged synthetic `INV-6.1`; restored canonical `INV-5.1: Wallet Balance Conservation and Active Hold Reservation Bounds`.
7. **Refund Scenarios Reconciled:** Reconciled 15,000 INR booking with 3,000 INR cancellation penalty yielding 12,000 INR refundable entitlement in SCN-WEBHOOK-001 and SCN-ABUSE-001. Added positive control tests.
8. **Tool Benchmarks:** Categorized tool evaluations transparently into `EXECUTED_AND_VERIFIED`, `NOT_RUN`, and `NOT_COMPARABLE`. Removed fabricated execution milliseconds.
9. **Change Detection:** Fixed empty git diff behavior to return empty file lists without injecting synthetic baseline files.
10. **Operational Safety:** Documented enforced allowlisting, synthetic identity checks, rate-limiting, and residual operational risks.

---

## 4. VERIFICATION OF DISTRIBUTION PACKAGE

- **Release Archive:** `CROSSWIRE_2_0_FINAL_RELEASE.zip`
- **Total Test Count:** 332 passing tests
- **Integrity Validation:** Clean extraction and self-contained execution verified on an isolated filesystem path.

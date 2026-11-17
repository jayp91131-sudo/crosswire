# CROSSWIRE 2.0 — FINAL EMPIRICAL BENCHMARK EVIDENCE

**Platform:** Crosswire Business-Logic & Transaction-Integrity DAST  
**Version:** 2.0.0 (Gold Release Build)  
**Date:** November 16, 2026  
**Scope:** Comparative Benchmark Architecture of Crosswire 2.0 Against Industry Tooling  
**Methodology:** Transparent Classification of Executed vs. Unrun Tools; Zero Pre-Seeded Metrics  

---

## 1. BENCHMARKING METHODOLOGY & INTEGRITY PLEDGE

To maintain strict scientific and technical honesty, Crosswire 2.0 completely rejects hardcoded or pre-seeded benchmark figures:

- `NOT_RUN`: When no genuine execution records exist, the tool is strictly classified as `NOT_RUN` and all empirical metrics (TP, FP, FN, precision, recall, F1, detection latency, reproducibility) evaluate to **`None` (*Unavailable*)**.
- `NOT_COMPARABLE (FAULT INJECTOR ONLY)`: Tool operates at a fundamentally different layer (e.g. Toxiproxy at Layer-4 TCP) and does not generate vulnerability findings or invariant evaluations without an external oracle.
- `EXECUTED_AND_VERIFIED`: Tool metrics are dynamically computed *only* when genuine `RawBenchmarkRunRecord` instances are captured and recorded via `harness.record_run()`.
- **No Inferred Misses:** In the scenario coverage matrix, tools without execution records are marked `NOT_RUN` rather than inferred as `MISSED`.

All previously published claims of 10 TP, 0 FP, 0 FN, 210 ms latency, 100% reproducibility, and 5/5 evidence quality have been completely expunged as unproven until genuine benchmark records are recorded.

---

## 2. COMPARATIVE TOOL BENCHMARK MATRIX (INITIAL BASELINE)

| Tool Name | Tool Version | Category | Execution Status | True Positives | False Positives | False Negatives | Precision | Recall | F1 Score | Measured Detection Latency | Reproducibility | Evidence Quality Score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Crosswire 2.0** | 2.0.0 Release | Business-Logic & Transaction DAST | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* |
| **Conventional Custom Tests** | Pytest 9.1 + httpx | In-House QA Test Suite | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* |
| **OWASP ZAP** | 2.14.0 | General DAST Scanner | `NOT_RUN (UNINSTALLED)` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* |
| **Nuclei** | v3.2.0 | Signature & CVE Scanner | `NOT_RUN (UNINSTALLED)` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* |
| **Postman / Newman** | v10.24 / v6.1.1 | API Collection Runner | `NOT_RUN (UNINSTALLED)` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* |
| **Toxiproxy** | v2.9.0 | TCP Chaos & Latency Proxy | `NOT_COMPARABLE (FAULT ONLY)` | *N/A* | *N/A* | *N/A* | *N/A* | *N/A* | *N/A* | *N/A* | *Unavailable* | *Unavailable* |
| **MockCard** | v2.1.0 | Webhook Replay Simulator | `NOT_RUN (COMMERCIAL)` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* |

---

## 3. SCENARIO DETECTION MATRIX (INITIAL BASELINE)

| Scenario ID | Scenario Description | Crosswire 2.0 | Custom QA Tests | OWASP ZAP | Nuclei | Postman / Newman | Toxiproxy | MockCard |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SCN-TIMEOUT-001** | Supplier Commit-Then-Timeout Duplicate Booking | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-WEBHOOK-001** | Duplicate Refund Webhook Replay | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-WALLET-001** | Agency Wallet Concurrency & Double-Spend | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-RACE-001** | Cancellation vs Delayed Supplier Confirmation | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-BOLA-001** | Cross-Agency BOLA/IDOR Boundary Violation | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-PRIV-001** | Sub-Agent Privilege Escalation | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-TAMPER-001** | Fare, Markup, and Currency Tampering | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-SKIP-001** | Payment-Step Skipping & Voucher Issuance | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-ABUSE-001** | Cancel-Rebook-Refund Cumulative Abuse | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-OUTBOX-001** | Multi-Supplier Outbox Execution & Transport | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |

---

## 4. ARCHITECTURAL CAPABILITY COMPARISON

| Feature / Capability | Crosswire 2.0 | Traditional DAST (ZAP) | Signature Scanners (Nuclei) | Sequential Runners (Postman) | Layer-4 Fault Injection (Toxiproxy) |
|---|---|---|---|---|---|
| **Transaction State Model** | Multi-step saga context with thread correlation | Single request/response | Single request/response | Scripted sequence | Pure Layer-4 TCP stream |
| **Concurrency Testing** | Parallel barrier synchronization | Concurrency unaware | Concurrency unaware | Strictly sequential | Network latency simulation |
| **Ground-Truth Verification** | Direct SQL/Ledger state inspection | HTTP response inspection only | HTTP regex matching only | HTTP assertion inspection | No application oracle |
| **Financial Ledger Invariants** | Double-entry conservation checks | None | None | None | None |
| **Transport Isolation** | DNS rebinding / IP pinning checks | None | None | None | Fault proxy only |

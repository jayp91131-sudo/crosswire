# CROSSWIRE 2.0 — BENCHMARK RESULTS & COMPARATIVE EVALUATION
**Empirical Benchmark Architecture & Methodology**

> **Authoritative Specification:** [`CROSSWIRE_FINAL_BENCHMARK_EVIDENCE.md`](CROSSWIRE_FINAL_BENCHMARK_EVIDENCE.md).

---

## 1. Benchmark Overview & Methodology

To ensure strict scientific integrity and avoid ungrounded marketing claims, Crosswire 2.0 implements an evidence-driven benchmarking harness (`engine/benchmarking.py`). All quantitative metrics are derived strictly from captured `RawBenchmarkRunRecord` instances.

### Core Principles:
1. **Zero Pre-Seeded Metrics:** No benchmark numbers (TP, FP, FN, precision, recall, latency, or reproducibility) are hard-coded into the product or documentation.
2. **Initial State:** A fresh `BenchmarkHarness` begins with zero run records. In this state, all tools—including Crosswire 2.0—are classified as `NOT_RUN` (or `NOT_COMPARABLE` for Toxiproxy), and all quantitative metrics evaluate to `None` (*Unavailable*).
3. **Strict Attribution:** Metrics are only computed when genuine, reproducible test runs are captured and recorded via `harness.record_run()`.
4. **No Inferred Failures:** Tools without execution records are reported as `NOT_RUN` in the scenario coverage matrix; absence of a run is never inferred as a `MISSED` detection.

---

## 2. Tool Classification & Status Matrix

| Tool Name | Tool Category | Execution Status | True Positives | False Positives | False Negatives | Precision | Recall | $F_1$-Score | Detection Latency | Reproducibility | Evidence Quality | Safety Violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Crosswire 2.0** | Transaction DAST | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | 0 |
| **Conventional Custom Tests** | In-House QA Suite | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | 0 |
| **OWASP ZAP** | Protocol DAST | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | 0 |
| **Nuclei** | Signature Scanner | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | 0 |
| **Postman / Newman** | Functional API Runner | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | 0 |
| **Toxiproxy** | Network Chaos Proxy | `NOT_COMPARABLE` | *N/A* | *N/A* | *N/A* | *N/A* | *N/A* | *N/A* | *N/A* | *Unavailable* | *Unavailable* | 0 |
| **MockCard** | Webhook Chaos Tool | `NOT_RUN` | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | *Unavailable* | 0 |

*Note: Toxiproxy is classified as `NOT_COMPARABLE (FAULT INJECTOR ONLY)` because it operates at Layer 4 (TCP transport) and does not evaluate transaction invariants or generate security vulnerability findings.*

---

## 3. Scenario Coverage Matrix (Default Baseline)

In the absence of captured benchmark runs, all tools default to `NOT_RUN` across all 10 scenarios:

| Scenario ID & Vulnerability Title | Crosswire 2.0 | Custom Tests | OWASP ZAP | Nuclei | Postman / Newman | Toxiproxy | MockCard |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SCN-TIMEOUT-001** (Supplier Commit-Then-Timeout) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-WEBHOOK-001** (Duplicate Refund Webhook) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-OUTBOX-001** (Multi-Supplier Outbox) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-WALLET-001** (Wallet Concurrency / TOCTOU) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-RACE-001** (Cancellation vs Delayed Conf.) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-BOLA-001** (Cross-Agency BOLA / IDOR) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-PRIV-001** (Sub-Agent Privilege Escalation) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-TAMPER-001** (Fare & Markup Tampering) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-SKIP-001** (Payment-Step Skipping) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |
| **SCN-ABUSE-001** (Cancel-Rebook-Refund Abuse) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_APPLICABLE | NOT_RUN |

---

## 4. Running Benchmarks & Capturing Empirical Evidence

To capture empirical benchmark run records and generate calculated metrics:

```python
from engine.benchmarking import get_benchmark_harness, RawBenchmarkRunRecord, get_iso_now

harness = get_benchmark_harness()

# Record an empirical run
harness.record_run(
    RawBenchmarkRunRecord(
        tool_name="Crosswire 2.0",
        scenario_id="SCN-TIMEOUT-001",
        run_id="RUN-EMPIRICAL-001",
        executed_at=get_iso_now(),
        is_positive_control=True,
        detected_vulnerability=True,
        duration_ms=185.4,
        evidence_collected=True,
        reproduced=True,
        raw_output="Confirmed duplicate supplier reservation on retry.",
    )
)

# Run benchmark aggregation
results = harness.run_comprehensive_benchmark()
```

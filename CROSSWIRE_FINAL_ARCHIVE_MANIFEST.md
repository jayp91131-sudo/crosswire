# CROSSWIRE 2.0 — RELEASE ARCHIVE MANIFEST & VERIFICATION GUIDE

**Platform:** Crosswire Business-Logic & Transaction-Integrity DAST  
**Version:** 2.0.0 (Release Candidate Build)  
**Distribution Archive:** `CROSSWIRE_2_0_FINAL_RELEASE.zip`  
**Packaged File Count:** 307 files  
**Checksum Protocol:** Detached SHA-256 Digest (`CROSSWIRE_2_0_FINAL_RELEASE.zip.sha256`)  
**Verification Status:** Clean Independent Extraction Verified (332 / 332 Tests Passing, 100%)  

---

## 1. ARCHIVE VERIFICATION VIA DETACHED CHECKSUM

In accordance with strict packaging standards, this self-contained manifest does not embed a circular self-referential checksum. The authoritative cryptographic digest and byte size are stored in the detached checksum file:

`CROSSWIRE_2_0_FINAL_RELEASE.zip.sha256`

### Verification Commands:

```powershell
# Windows PowerShell SHA-256 Verification:
$expected = (Get-Content CROSSWIRE_2_0_FINAL_RELEASE.zip.sha256).Split(' ')[0].Trim()
$actual = (Get-FileHash -Path CROSSWIRE_2_0_FINAL_RELEASE.zip -Algorithm SHA256).Hash.ToLower()

if ($expected.ToLower() -eq $actual) {
    Write-Host "INTEGRITY VERIFIED: $actual" -ForegroundColor Green
} else {
    Write-Host "MISMATCH! Expected $expected but got $actual" -ForegroundColor Red
}
```

```bash
# Linux / macOS sha256sum Verification:
sha256sum -c CROSSWIRE_2_0_FINAL_RELEASE.zip.sha256
```

---

## 2. PACKAGE STRUCTURE MANIFEST (307 Files)

The release archive contains complete source implementations, test suites, static frontend assets, configuration manifests, and documentation:

```
CROSSWIRE_2_0_FINAL_RELEASE.zip (307 files)
├── control_center/                          # M2.7.1 Forensic Operations Console
│   ├── __init__.py
│   ├── app.py                               # FastAPI backend with fail-closed reports & exports
│   ├── engine_bridge.py                     # Bridge translating scenarios to timeline events
│   ├── reports.py                           # HTML security report & structured JSON export
│   ├── state.py                             # Execution record store with verdict & SHA-256
│   ├── static/                              # Swiss dark-mode web application
│   │   ├── app.js                           # UI reactive controllers & timeline renderer
│   │   ├── index.html                       # Single-page console interface
│   │   └── style.css                        # Swiss typography & high-contrast palette
│   ├── templates/                           # HTML templates
│   │   └── index.html
│   └── tests/                               # Operations console test suite (15 tests)
│       ├── __init__.py
│       └── test_control_center.py
├── engine/                                  # Core Crosswire Engine
│   ├── __init__.py
│   ├── adapters.py                          # Adapter interfaces & registry
│   ├── benchmarking.py                      # Dynamic empirical benchmark harness
│   ├── change_detector.py                   # Local git change-aware testing engine
│   ├── event_collector.py                   # Canonical event instrumentation & correlation
│   ├── inventory.py                         # On-demand API & async worker inventory scanner
│   ├── portable_scenarios/                  # 10 Standardized Transaction Scenarios
│   │   ├── __init__.py
│   │   ├── base.py                          # Scenario contracts & positive control helpers
│   │   ├── portable_timeout.py              # SCN-TIMEOUT-001 (Supplier Timeout)
│   │   ├── portable_webhook.py              # SCN-WEBHOOK-001 (Webhook Replay)
│   │   ├── portable_wallet.py               # SCN-WALLET-001 (Wallet Double-Spend)
│   │   ├── portable_race.py                 # SCN-RACE-001 (Cancellation vs Delayed Conf)
│   │   ├── portable_bola.py                 # SCN-BOLA-001 (Cross-Agency BOLA)
│   │   ├── portable_priv.py                 # SCN-PRIV-001 (Privilege Escalation)
│   │   ├── portable_tamper.py               # SCN-TAMPER-001 (Fare & Currency Tampering)
│   │   ├── portable_skip.py                 # SCN-SKIP-001 (Payment Step Skipping)
│   │   ├── portable_abuse.py                # SCN-ABUSE-001 (Cancel-Rebook-Refund Abuse)
│   │   └── portable_outbox.py               # SCN-OUTBOX-001 (Multi-Supplier Outbox)
│   ├── reporting.py                         # Fail-closed forensic dossier & SHA-256 digest
│   ├── safety.py                            # Operational allowlist & emergency kill switch
│   └── target_adapters/                     # Target Abstraction Framework
│       ├── __init__.py
│       ├── base.py                          # Execution & Observation adapter contracts
│       ├── registry.py                      # Target registration & capability discovery
│       ├── target_a.py                      # Reference Platform (Target A) adapter
│       └── travel_sandbox_b.py              # Standalone in-memory SQLite (TravelSandboxB)
├── m2_3b/                                   # Transactional Outbox & Distributed Sagas
│   ├── __init__.py
│   ├── coverage_oracle.py
│   ├── outbox_engine.py
│   ├── parent_coordinator.py
│   ├── pinned_transport.py
│   └── tests/                               # Outbox test suite (54 tests)
├── m2_3c/                                   # Findings & Retest Verification Demo
│   ├── __init__.py
│   ├── findings.py
│   ├── outbox_integration.py
│   └── tests/                               # Retest verification tests (7 tests)
├── target_platform/                         # Target A Platform Reference Implementation
│   ├── aggregator/
│   ├── models/
│   └── suppliers/
├── travel_sandbox_b/                        # Standalone Clean-Room Platform
│   ├── __init__.py
│   ├── api.py                               # Independent FastAPI router
│   └── db.py                                # Independent in-memory SQLite schema
├── tests/                                   # Core Scenario & Hardening Test Suite (256 tests)
│   ├── test_all_scenarios.py
│   ├── test_benchmarks_and_safety.py
│   ├── test_benchmarks_evidence.py
│   ├── test_reporting_fail_closed.py
│   └── ... (42 test modules total)
├── pytest.ini                               # Test paths and async configuration
├── requirements.txt                         # Certified dependency manifest
├── README.md                                # Project introduction
└── Documentation Reports:
    ├── CROSSWIRE_FINAL_ACCEPTANCE_REPORT.md
    ├── CROSSWIRE_FINAL_TEST_RESULTS.md
    ├── CROSSWIRE_FINAL_BENCHMARK_EVIDENCE.md
    ├── CROSSWIRE_FINAL_OPERATOR_GUIDE.md
    ├── CROSSWIRE_2_0_FINAL_RELEASE_NOTES.md
    ├── CROSSWIRE_FINAL_DISCREPANCY_RESOLUTION.md
    └── CROSSWIRE_FINAL_ARCHIVE_MANIFEST.md
```

---

## 3. INSTRUCTIONS FOR THE OPERATOR / RECIPIENT

```bash
# 1. Unpack archive
unzip CROSSWIRE_2_0_FINAL_RELEASE.zip -d crosswire_2_0
cd crosswire_2_0

# 2. Setup Python environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Execute test suite (verifying 332 passing tests)
pytest

# 5. Launch Forensic Operations Console
python -m uvicorn control_center.app:app --host 127.0.0.1 --port 8000
```
Open `http://localhost:8000` to interact with the Forensic Operations Console.

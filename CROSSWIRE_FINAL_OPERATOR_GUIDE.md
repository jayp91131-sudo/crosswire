# CROSSWIRE 2.0 — OPERATOR'S MANUAL & INTEGRATION GUIDE

**Platform:** Crosswire Business-Logic & Transaction-Integrity DAST  
**Version:** 2.0.0 (Gold Release Build)  
**Audience:** Security Engineers, QA Automation Leads, Platform Architects  

---

## 1. SYSTEM OVERVIEW & ARCHITECTURE

Crosswire 2.0 is a specialized Dynamic Application Security Testing (DAST) platform designed to detect complex business-logic vulnerabilities, concurrency races, financial ledger drift, and multi-party saga failures in distributed B2B travel and fintech transaction systems.

### Core Architecture Components:
1. **Scenario Engine:** Executes standardized transaction scenarios with configurable parameters and dual execution modes (`VULNERABLE` and `FIXED`).
2. **Target Adapter Framework:** Decouples scenario logic from target platform implementations via `TargetExecutionAdapter` and `GroundTruthObservationAdapter`.
3. **Fail-Closed Invariant Engine:** Evaluates canonical transaction invariants (`INV-5.1` to `INV-5.7`) directly against ground-truth database state; missing observations or missing invariant checks yield `INCONCLUSIVE`, never a false `PASS`.
4. **Forensic Operations Console:** High-performance Swiss editorial console (M2.7.1) for real-time execution monitoring, interactive event timelines, and evidence inspection.
5. **Operational Safety Manager:** Strict allowlisting, synthetic credential quarantine, rate-limiting, concurrency caps, and emergency kill switches.

---

## 2. QUICK START & INSTALLATION

### Prerequisites:
- Python 3.11+
- Git
- Virtual environment (`venv`)

### Setup Instructions:
```bash
# 1. Clone repository and navigate to root
cd crosswire

# 2. Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Verify test suite (332 tests)
pytest
```

---

## 3. RUNNING THE FORENSIC OPERATIONS CONSOLE

To launch the web-based operations console:
```bash
python -m uvicorn control_center.app:app --host 127.0.0.1 --port 8000 --reload
```
Navigate to `http://localhost:8000` in any modern web browser.

### Key Console Workspaces:
- **Overview:** Real-time summary of target health, scenario counts, recent executions, and active findings.
- **Scenarios:** Interactive execution workbench to run any of the 10 scenarios in vulnerable or fixed modes against Target A or TravelSandboxB (on supported capabilities).
- **Timeline & Forensic Dossier:** Inspection of monotonically sequenced runtime events with actor tags, status indicators, and full-dossier SHA-256 evidence bundles.
- **API & Async Inventory:** On-demand inventory scan classifying endpoints as `ACTIVE_DOCUMENTED`, `SHADOW`, `ZOMBIE`, or `DORMANT_CANDIDATE`.
- **Change Impact Testing:** Local git diff analysis selecting the minimal necessary test scenario suite.
- **Benchmark Center:** Qualitative comparisons and tool capability matrices.

---

## 4. TARGET ADAPTER INTEGRATION GUIDE

Crosswire 2.0 ships with two reference target integrations:
- `target_a`: Default SQLite disk-backed B2B travel platform with asynchronous sagas and multi-supplier outbox support.
- `travel_sandbox_b`: Completely independent in-memory SQLite (`:memory:`) target platform with custom tables and mutex locking.

### Onboarding a New Target Platform:
To test an independent target platform, implement two adapter classes:

#### Step 1: Implement `TargetExecutionAdapter`
```python
from engine.target_adapters.base import TargetExecutionAdapter, BookingIntentResult, BookingSubmissionResult

class MyCustomPlatformExecutionAdapter(TargetExecutionAdapter):
    async def create_booking_intent(self, intent_id: str, hotel_id: str, amount: float, currency: str) -> BookingIntentResult:
        # Call target platform REST API / SDK to create booking intent
        ...

    async def submit_booking(self, intent: BookingIntentResult) -> BookingSubmissionResult:
        # Call target platform to execute booking
        ...

    async def cancel_booking(self, booking_id: str) -> Dict[str, Any]:
        # Call target cancellation endpoint
        ...

    async def deliver_refund_webhook(self, payload: Dict[str, Any]) -> int:
        # Deliver webhook to target endpoint and return HTTP status code
        ...
```

#### Step 2: Implement `GroundTruthObservationAdapter`
```python
from engine.target_adapters.base import (
    GroundTruthObservationAdapter,
    ObservationResult,
    BookingObservation,
    SupplierReservationObservation,
    LedgerObservation,
)

class MyCustomPlatformObservationAdapter(GroundTruthObservationAdapter):
    def get_booking_observation(self, booking_id: str) -> ObservationResult[BookingObservation]:
        # Query target database directly (read-only)
        ...

    def get_supplier_reservations(self, intent_id: str) -> ObservationResult[List[SupplierReservationObservation]]:
        # Query supplier reservation table
        ...

    def get_ledger_observations(self, booking_id: str) -> ObservationResult[List[LedgerObservation]]:
        # Query financial ledger table
        ...
```

#### Step 3: Register Target in Adapter Registry
```python
from engine.target_adapters.registry import get_adapter_registry, TargetMetadata

registry = get_adapter_registry()
registry.register_target(
    metadata=TargetMetadata(
        target_id="my_custom_target",
        name="Custom Enterprise Travel Hub",
        target_type="REST_API",
        base_url="http://127.0.0.1:9090",
    ),
    execution_adapter=MyCustomPlatformExecutionAdapter(base_url="http://127.0.0.1:9090"),
    observation_adapter=MyCustomPlatformObservationAdapter(db_connection_string="..."),
)
```

---

## 5. CLI AUTOMATION & CI/CD PIPELINE INTEGRATION

Crosswire provides dedicated CLI commands and Python APIs for automated test automation:

### Executing All Portable Scenarios:
```bash
pytest tests/test_all_scenarios.py -v
```

### Change-Aware Impact Testing:
```python
from engine.change_detector import ChangeAwareSecurityTestingEngine

engine = ChangeAwareSecurityTestingEngine()
# Analyzes local git diff against HEAD
report = engine.analyze_change_impact(git_ref="HEAD")

print(f"Triggered Scenarios: {report.total_scenarios_triggered}")
for sc in report.selected_scenarios:
    print(f" - {sc.scenario_id}: {sc.justification}")
```

### Full-Dossier Cryptographic Evidence Verification:
Every generated `ForensicReport` includes an automated SHA-256 evidence integrity check covering event trace, observations, finding evidence, and retest outcomes:
```python
from engine.reporting import ForensicReportingEngine

# Build report
report = ForensicReportingEngine.build_report(...)

# Validate evidence integrity
if not report.verify_integrity():
    raise SecurityError("Evidence bundle has been tampered with or corrupted!")
```

---

## 6. OPERATIONAL SAFETY & WATCHDOG CONTROLS

Crosswire is designed with strict safety boundaries:

### Allowlisting Targets:
Targets must be registered in the internal allowlist. Any request to an unregistered target raises `TargetNotAllowedError`.

### Network Perimeter Scoping:
Outbound HTTP requests are strictly limited to local loopback (`127.0.0.1`, `localhost`) and designated test hostnames. Calls to public URLs raise `ScopeViolationError`.

### Synthetic Credential Quarantine:
All headers and tokens are checked against production API key regex patterns (e.g. `sk_live_`, `ghp_`). Detecting live keys immediately aborts execution with `LiveCredentialDetectedError`.

### Emergency Stop (Kill Switch):
In the event of an operational anomaly, trigger the immediate kill switch:
```bash
# CLI / HTTP POST
curl -X POST http://localhost:8000/api/safety/emergency-stop \
  -H "Content-Type: application/json" \
  -d '{"reason": "Emergency operational pause"}'
```
To resume operations after verification:
```bash
curl -X POST http://localhost:8000/api/safety/reset
```

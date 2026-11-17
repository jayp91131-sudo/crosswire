# CROSSWIRE 2.0 — OPERATOR GUIDE
**Operations Manual for Security Engineers, QA Architects, and DAST Operators**

---

## 1. System Requirements & Setup

### Prerequisites
- Python 3.11+ (CPython recommended)
- Git 2.30+ (for change-aware impact analysis)
- SQLite 3.40+ (embedded in standard Python distribution)
- Web Browser (Chrome, Firefox, Safari, or Edge) for Control Center UI

### Installation
Clone the repository and set up a clean Python virtual environment:
```powershell
# Navigate to workspace
cd C:\Users\jayp9\.gemini\antigravity\scratch\crosswire

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Verify test suite readiness
python -m pytest tests/ -q
```

---

## 2. Launching the Crosswire Control Center

The Control Center exposes a local single-page forensic console built with Swiss editorial typography, industrial control density, and real-time execution streaming:

```powershell
# Start Control Center web server
python -m uvicorn control_center.app:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser and navigate to:
```
http://127.0.0.1:8000/
```

### Dashboard Layout & Navigation:
- **Overview Registry:** High-level security KPIs, active target health, and recent execution ledger.
- **Scenario Library:** Catalog of all 10 approved transaction scenarios with one-click `▶ Run Vulnerable` and `✓ Run Fixed` action triggers.
- **3-Pane Forensic Workbench:**
  - *Pane 1 (Control Station):* Target platform selector, scenario picker, and mode toggle (VULNERABLE / FIXED).
  - *Pane 2 (Runtime Event Stream):* Millisecond-accurate timeline displaying monotonic sequence numbers, actors, fault injection points, and invariant verdicts.
  - *Pane 3 (Evidence Inspector):* JSON dossier, CWE metadata, cryptographic Merkle hashes, and direct database observation proof.
- **Findings Dossier:** Inventory of confirmed vulnerabilities, severity levels, and automated retest status.
- **API Inventory Explorer:** Scanned active, shadow, zombie, and dormant endpoints.

---

## 3. Command-Line Interface (CLI) Workflows

Crosswire 2.0 provides powerful programmatic workflows via standard Python and pytest interfaces:

### Running an Individual Scenario
```powershell
# Run a specific scenario in both vulnerable and fixed modes
python -m pytest tests/test_all_scenarios.py -k "test_wallet_concurrency" -v
```

### Running Change-Aware Selective Testing
When code modifications are made, run change-aware security testing to execute only affected scenarios:
```powershell
python -c "
from engine.change_detector import ChangeAwareSecurityTestingEngine
detector = ChangeAwareSecurityTestingEngine()
report = detector.analyze_change_impact(git_ref='HEAD')
print(f'Triggered Scenarios: {report.execution_plan}')
"
```

### Running the API & Asynchronous Worker Inventory Scan
```powershell
python -c "
from engine.inventory import InventoryEngine
engine = InventoryEngine()
engine.scan_inventory()
summary = engine.get_summary()
print(f'Total Assets: {summary[\"total_inventory_items\"]}')
print(f'Shadow APIs: {summary[\"classifications\"][\"shadow_apis\"]}')
"
```

### Running the Comparative Benchmark Suite
```powershell
python -c "
from engine.benchmarking import BenchmarkHarness
harness = BenchmarkHarness()
results = harness.run_comprehensive_benchmark()
print(f'Evaluated {results[\"total_scenarios_evaluated\"]} scenarios across {len(results[\"tool_metrics\"])} tools.')
"
```

---

## 4. Target Onboarding Guide

To test a custom, external B2B travel booking engine with Crosswire 2.0 without altering core scenario logic:

1. **Subclass `TargetExecutionAdapter`:**
   Implement dispatch methods in `engine/adapters/my_custom_adapter.py`:
   ```python
   from engine.adapters.contracts import TargetExecutionAdapter, ExecutionResult

   class CustomTravelExecutionAdapter(TargetExecutionAdapter):
       def create_booking_intent(self, intent_id, agency_id, amount_minor, currency):
           # Call custom platform REST API or RPC
           ...

       def simulate_supplier_timeout(self, intent_id):
           # Trigger custom mock or proxy fault
           ...
   ```

2. **Subclass `GroundTruthObservationAdapter`:**
   Implement read-only inspection methods:
   ```python
   from engine.adapters.contracts import GroundTruthObservationAdapter, ObservationResult

   class CustomTravelObservationAdapter(GroundTruthObservationAdapter):
       def get_booking_observation(self, booking_id):
           # Query read-replica or database
           ...

       def get_supplier_reservations(self, intent_id):
           # Inspect supplier records
           ...
   ```

3. **Register in `TargetRegistry`:**
   ```python
   from engine.adapters.registry import get_target_registry
   from engine.adapters.contracts import TargetMetadata, TargetCapability

   registry = get_target_registry()
   registry.register_target(
       TargetMetadata(
           target_id="custom_travel_platform",
           name="Custom Enterprise Travel Engine",
           capabilities=[
               TargetCapability.BOOKING_OPERATIONS,
               TargetCapability.CONTROLLED_FAULT_INJECTION,
               TargetCapability.WALLET_OPERATIONS,
           ]
       ),
       execution_adapter=CustomTravelExecutionAdapter(),
       observation_adapter=CustomTravelObservationAdapter(),
   )
   ```

---

## 5. Safety Guardrails & Emergency Kill Switch

Crosswire 2.0 includes automated safety mechanisms to guarantee zero accidental impact to production systems:

### Emergency Stop (Kill Switch)
If an abnormal system condition or unexpected network anomaly is detected, trigger the kill switch immediately:
- **Via Control Center:** Click the `EMERGENCY STOP` button in the header toolbar.
- **Via API:**
  ```powershell
  curl -X POST http://127.0.0.1:8000/api/safety/emergency-stop -H "Content-Type: application/json" -d '{"reason": "Operator drill"}'
  ```
- **Via Python:**
  ```python
  from engine.safety import get_safety_manager
  get_safety_manager().trigger_emergency_stop(reason="Operator halt")
  ```

### Resuming Operations
```powershell
curl -X POST http://127.0.0.1:8000/api/safety/reset
```

### Guardrail Policies
- **Strict Allowlisting:** Only `target_a` and `travel_sandbox_b` can be targeted by default.
- **Synthetic Quarantine:** Any credential matching live token regexes (`sk_live_`, `AKIA`) is rejected instantly.
- **Concurrency & Rate Limits:** Maximum 5 simultaneous scenario runs, 100 requests/second ceiling.

---

## 6. Audit & Evidence Export

Crosswire enables exporting full compliance dossiers for executive review and bug tracking:
- **HTML Security Report:** `GET http://127.0.0.1:8000/api/reports/html`
- **JSON Findings Export:** `GET http://127.0.0.1:8000/api/reports/json/findings`
- **JSON Executions Trace:** `GET http://127.0.0.1:8000/api/reports/json/executions`
- **Standalone Markdown Dossiers:** Stored in conversation artifacts directory with SHA-256 integrity hashes.

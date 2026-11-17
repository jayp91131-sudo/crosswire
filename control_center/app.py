"""
Crosswire Control Center: FastAPI Web Application.
Exposes security testing dashboard, scenario runner APIs, timeline audits, and report generation.
"""

from typing import Optional, List, Dict, Any
from pathlib import Path
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .state import get_state_store
from .engine_bridge import get_engine_bridge
from .reports import generate_html_security_report, export_findings_json, export_executions_json
from engine.adapters import get_target_registry
from engine.inventory import InventoryEngine
from engine.change_detector import ChangeAwareSecurityTestingEngine
from engine.benchmarking import BenchmarkHarness
from engine.safety import get_safety_manager

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(
    title="Crosswire Control Center",
    description="Local Security Operations Dashboard and Scenario Runner for Crosswire",
    version="2.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

state_store = get_state_store()
engine_bridge = get_engine_bridge()


class ScenarioRunRequest(BaseModel):
    scenario_id: str
    mode: str = "VULNERABLE"  # "VULNERABLE" or "FIXED"
    target_finding_id: Optional[str] = None
    target_id: Optional[str] = "target_a"


@app.get("/", response_class=HTMLResponse)
async def serve_control_center_ui():
    """Serves the main Control Center single-page dashboard."""
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Control center template not found")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/api/targets")
async def list_registered_targets():
    """Lists registered target platforms and their advertised capabilities."""
    registry = get_target_registry()
    return [registry.get_target_metadata(tid).to_dict() for tid in registry.list_targets()]


@app.get("/api/stats")
async def get_dashboard_stats():
    """Returns aggregated security KPIs, finding counts, and recent execution records."""
    return state_store.get_stats()


@app.get("/api/scenarios")
async def list_available_scenarios():
    """Lists approved scenarios available for local security testing."""
    return [
        {
            "scenario_id": "SCN-TIMEOUT-001",
            "name": "Ambiguous Supplier Commit-Then-Timeout",
            "description": "Supplier persists reservation but network response is lost. Vulnerable mode naively blind-retries, causing duplicate reservations. Fixed mode uses idempotency contract.",
            "target_invariant": "INV-5.2: Booking Intent Uniqueness",
            "cwe": "CWE-675 / CWE-841",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-WEBHOOK-001",
            "name": "Duplicate Refund Webhook Replay",
            "description": "Payment provider re-delivers the same refund webhook 3 times. Vulnerable mode creates multiple duplicate ledger entries. Fixed mode enforces atomic deduplication.",
            "target_invariant": "INV-5.3: Webhook Idempotency & INV-5.5: Refund Boundedness",
            "cwe": "CWE-675 / CWE-799",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-OUTBOX-001",
            "name": "Multi-Supplier Coordinated Outbox",
            "description": "Multi-room stay distributed across distinct suppliers (Expedia & Hotelbeds) under commit-then-timeout faults. Verifies stay coverage and monotonic fencing.",
            "target_invariant": "Parent Stay Coverage & Monotonic Fencing Tokens",
            "cwe": "CWE-841",
            "supported_modes": ["FIXED", "VULNERABLE"]
        },
        {
            "scenario_id": "SCN-WALLET-001",
            "name": "Agency Wallet Concurrency & Double-Spend",
            "description": "Concurrent holds exceed available credit via TOCTOU check-then-act race condition, driving wallet balance negative.",
            "target_invariant": "INV-5.1: Wallet Balance Conservation and Active Hold Reservation Bounds",
            "cwe": "CWE-362 / CWE-841",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-RACE-001",
            "name": "Cancellation vs Delayed Supplier Confirmation",
            "description": "Delayed supplier commit arriving after guest cancellation resurrects active booking voucher and allows dual refund + stay.",
            "target_invariant": "INV-5.4: Voucher Entitlement Preconditions & INV-5.6: State Transition Validity",
            "cwe": "CWE-362 / CWE-670",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-BOLA-001",
            "name": "Cross-Agency BOLA / IDOR Tenant Isolation",
            "description": "Authenticated agency accesses and manipulates booking dossiers of victim agencies without multi-tenant authorization checks.",
            "target_invariant": "INV-AUTH-01: Cross-Tenant Booking Boundary",
            "cwe": "CWE-639 / CWE-284",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-PRIV-001",
            "name": "Sub-Agent Privilege Escalation via Deposit",
            "description": "Sub-agent with read-only booking permissions invokes unprivileged wallet deposit endpoint to artificially inflate agency credit.",
            "target_invariant": "INV-AUTH-02: Role-Based Financial Authorization",
            "cwe": "CWE-269 / CWE-285",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-TAMPER-001",
            "name": "Client-Side Fare, Markup & Currency Tampering",
            "description": "Attacker overrides quote price and currency in checkout request payload without server-side quote recalculation.",
            "target_invariant": "INV-PRICE-01: Server-Side Quote Integrity",
            "cwe": "CWE-472 / CWE-602",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-SKIP-001",
            "name": "Payment-Step Skipping / Unentitled Voucher Issuance",
            "description": "Premature voucher issuance invoked while booking is still in HELD state, bypassing payment gateway capture.",
            "target_invariant": "INV-5.4: Voucher Entitlement Preconditions",
            "cwe": "CWE-841 / CWE-670",
            "supported_modes": ["VULNERABLE", "FIXED"]
        },
        {
            "scenario_id": "SCN-ABUSE-001",
            "name": "Cancel-Rebook-Refund Cumulative Overflow Abuse",
            "description": "Secondary cancellation refund executed after initial cancellation penalty, resulting in cumulative refunds exceeding captured payment.",
            "target_invariant": "INV-5.5: Strict Cumulative Refund Boundedness",
            "cwe": "CWE-840 / CWE-799",
            "supported_modes": ["VULNERABLE", "FIXED"]
        }
    ]


@app.post("/api/scenarios/run")
async def run_scenario_endpoint(req: ScenarioRunRequest):
    """Executes an approved scenario in the requested mode and returns the execution trace."""
    try:
        safety = get_safety_manager()
        with safety.execution_context(target_id=req.target_id):
            record = await engine_bridge.execute_scenario(
                scenario_id=req.scenario_id,
                mode=req.mode,
                target_finding_id=req.target_finding_id,
                target_id=req.target_id,
            )
            return record.to_dict()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/executions")
async def list_executions():
    """Lists all execution records in reverse chronological order."""
    return [e.to_dict() for e in state_store.executions]


@app.get("/api/executions/{execution_id}")
async def get_execution_detail(execution_id: str):
    """Returns detailed trace and timeline events for a specific execution."""
    ex = state_store.get_execution(execution_id)
    if not ex:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
    return ex.to_dict()


@app.get("/api/findings")
async def list_findings():
    """Lists all confirmed security findings with current retest status."""
    return [f.to_dict() for f in state_store.findings.values()]


@app.get("/api/findings/{finding_id}")
async def get_finding_detail(finding_id: str):
    """Returns complete details, evidence, and retest proof for a security finding."""
    f = state_store.get_finding(finding_id)
    if not f:
        raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")
    return f.to_dict()


@app.post("/api/findings/{finding_id}/retest")
async def retest_finding_endpoint(finding_id: str):
    """Triggers the corresponding fixed scenario for a finding and updates retest status."""
    f = state_store.get_finding(finding_id)
    if not f:
        raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")

    record = await engine_bridge.execute_scenario(
        scenario_id=f.scenario_id,
        mode="FIXED",
        target_finding_id=finding_id
    )

    updated_finding = state_store.get_finding(finding_id)
    return {
        "execution": record.to_dict(),
        "finding": updated_finding.to_dict() if updated_finding else None,
        "retest_passed": record.invariants_passed
    }


@app.get("/api/reports/html", response_class=HTMLResponse)
async def get_html_security_report():
    """Generates and serves the executive HTML Security Assessment Report."""
    return HTMLResponse(content=generate_html_security_report())


@app.get("/api/reports/json/findings")
async def download_findings_json():
    """Exports findings as downloadable JSON."""
    content = export_findings_json()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=crosswire_findings.json"}
    )


@app.get("/api/reports/json/executions")
async def download_executions_json():
    """Exports executions as downloadable JSON."""
    content = export_executions_json()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=crosswire_executions.json"}
    )


@app.get("/api/inventory")
async def get_inventory():
    """Returns scanned active, shadow, zombie, and dormant APIs and event handlers."""
    engine = InventoryEngine()
    items = engine.scan_inventory()
    summary = engine.get_summary()
    return {
        "summary": summary,
        "items": [item.to_dict() for item in items]
    }


@app.get("/api/change-detection")
async def get_change_detection_report(ref: str = "HEAD"):
    """Runs change-aware security testing impact analysis against git repository."""
    engine = ChangeAwareSecurityTestingEngine()
    report = engine.analyze_change_impact(git_ref=ref)
    return report.to_dict()


@app.get("/api/benchmarks")
async def get_benchmark_results():
    """Returns empirical benchmark evaluation across Crosswire 2.0 and baseline tooling."""
    harness = BenchmarkHarness()
    return harness.run_comprehensive_benchmark()


@app.get("/api/safety/status")
async def get_safety_status():
    """Returns current operational safety, watchdog, and rate-limiting status."""
    manager = get_safety_manager()
    return manager.get_status()


class EmergencyStopRequest(BaseModel):
    reason: Optional[str] = "Manual operator kill switch triggered"


@app.post("/api/safety/emergency-stop")
async def trigger_emergency_stop_endpoint(req: EmergencyStopRequest = EmergencyStopRequest()):
    """Triggers operational emergency stop / kill switch to halt all test dispatches."""
    manager = get_safety_manager()
    manager.trigger_emergency_stop(reason=req.reason or "Manual kill switch")
    return manager.get_status()


@app.post("/api/safety/reset")
async def reset_emergency_stop_endpoint():
    """Resets operational emergency stop / kill switch to resume test capabilities."""
    manager = get_safety_manager()
    manager.reset_emergency_stop()
    return manager.get_status()


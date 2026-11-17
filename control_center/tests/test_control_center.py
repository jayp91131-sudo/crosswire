"""
Crosswire Milestone 2.4 Control Center Test Suite.
Verifies:
1. Control Center HTML single-page dashboard renders cleanly.
2. Dashboard KPIs and statistics endpoint returns actual execution numbers.
3. Scenario runner API executes SCN-TIMEOUT-001 (Vulnerable & Fixed) with granular timeline events.
4. Scenario runner API executes SCN-WEBHOOK-001 (Vulnerable & Fixed) with duplicate ledger detection.
5. Scenario runner API executes SCN-OUTBOX-001 with multi-supplier coordination and coverage verification.
6. Execution timeline events are correctly generated with timestamps, statuses, and details.
7. Finding retest endpoint executes the fixed workflow and attaches retest proof.
8. Security assessment report HTML endpoint generates complete report with finding cards and invariants.
9. JSON export endpoints provide well-formed downloadable findings and execution traces.
"""

import pytest
from starlette.testclient import TestClient
from control_center.app import app
from control_center.state import get_state_store


@pytest.fixture
def client():
    from engine.safety import get_safety_manager
    get_safety_manager().reset_emergency_stop()
    yield TestClient(app)
    get_safety_manager().reset_emergency_stop()


def test_serve_control_center_ui(client):
    """Verifies that root URL serves the Control Center HTML single-page interface."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "CROSSWIRE" in resp.text
    assert "CONTROL CENTER M2.4" in resp.text
    assert "Ambiguous Commit-Then-Timeout" in resp.text


def test_get_dashboard_stats(client):
    """Verifies that /api/stats returns accurate KPI numbers grounded in actual state."""
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()

    assert "total_executions" in data
    assert "confirmed_vulnerabilities" in data
    assert "severity_counts" in data
    assert "successful_retests" in data
    assert "recent_activity" in data
    assert data["total_executions"] >= 1
    assert data["confirmed_vulnerabilities"] >= 1


def test_list_scenarios(client):
    """Verifies that /api/scenarios returns all 10 approved security testing scenarios."""
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    scenarios = resp.json()

    assert len(scenarios) == 10
    scen_ids = [s["scenario_id"] for s in scenarios]
    assert "SCN-TIMEOUT-001" in scen_ids
    assert "SCN-WEBHOOK-001" in scen_ids
    assert "SCN-OUTBOX-001" in scen_ids
    assert "SCN-WALLET-001" in scen_ids
    assert "SCN-RACE-001" in scen_ids
    assert "SCN-BOLA-001" in scen_ids
    assert "SCN-PRIV-001" in scen_ids
    assert "SCN-TAMPER-001" in scen_ids
    assert "SCN-SKIP-001" in scen_ids
    assert "SCN-ABUSE-001" in scen_ids


def test_run_wallet_scenario_via_control_center(client):
    """Verifies executing SCN-WALLET-001 through control center endpoint."""
    # Vulnerable run
    resp_v = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-WALLET-001",
        "mode": "VULNERABLE",
        "target_id": "target_a"
    })
    assert resp_v.status_code == 200
    data_v = resp_v.json()
    assert data_v["scenario_id"] == "SCN-WALLET-001"
    assert data_v["status"] == "VULNERABILITY_CONFIRMED"
    assert data_v["invariants_passed"] is False

    # Fixed run
    resp_f = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-WALLET-001",
        "mode": "FIXED",
        "target_id": "target_a"
    })
    assert resp_f.status_code == 200
    data_f = resp_f.json()
    assert data_f["scenario_id"] == "SCN-WALLET-001"
    assert data_f["status"] == "INVARIANT_PASSED"
    assert data_f["invariants_passed"] is True


def test_api_inventory_endpoint(client):
    """Verifies /api/inventory returns scanned active, shadow, zombie, and dormant APIs."""
    resp = client.get("/api/inventory")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "items" in data
    assert data["summary"]["total_inventory_items"] >= 25
    assert data["summary"]["classifications"]["shadow_apis"] >= 1


def test_api_change_detection_endpoint(client):
    """Verifies /api/change-detection returns impact report based on git diff."""
    resp = client.get("/api/change-detection")
    assert resp.status_code == 200
    data = resp.json()
    assert "git_ref" in data
    assert "changed_files" in data
    assert "selected_scenarios" in data
    assert "execution_plan" in data


def test_api_benchmarks_endpoint(client):
    """Verifies /api/benchmarks returns empirical comparative benchmarking results."""
    resp = client.get("/api/benchmarks")
    assert resp.status_code == 200
    data = resp.json()
    assert "tool_metrics" in data
    assert "scenario_coverage_matrix" in data
    tools = [t["tool_name"] for t in data["tool_metrics"]]
    assert "Crosswire 2.0" in tools
    assert "OWASP ZAP" in tools
    assert "Nuclei" in tools


def test_api_safety_endpoints_and_emergency_stop(client):
    """Verifies /api/safety endpoints, kill switch engagement, and resumption."""
    try:
        # Check status
        resp_stat = client.get("/api/safety/status")
        assert resp_stat.status_code == 200
        assert resp_stat.json()["emergency_stop_engaged"] is False

        # Trigger emergency stop
        resp_stop = client.post("/api/safety/emergency-stop", json={"reason": "Test drill"})
        assert resp_stop.status_code == 200
        assert resp_stop.json()["emergency_stop_engaged"] is True

        # Attempting to run scenario should now be rejected with 400
        resp_blocked = client.post("/api/scenarios/run", json={
            "scenario_id": "SCN-TIMEOUT-001",
            "mode": "FIXED"
        })
        assert resp_blocked.status_code == 400
        assert "EMERGENCY KILL SWITCH ENGAGED" in resp_blocked.json()["detail"]
    finally:
        # Reset emergency stop
        resp_reset = client.post("/api/safety/reset")
        assert resp_reset.status_code == 200
        assert resp_reset.json()["emergency_stop_engaged"] is False


def test_run_timeout_scenario_vulnerable_and_fixed(client):
    """Verifies executing SCN-TIMEOUT-001 in vulnerable mode (finding created) and fixed mode (passed)."""
    # 1. Vulnerable run
    resp_v = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-TIMEOUT-001",
        "mode": "VULNERABLE"
    })
    assert resp_v.status_code == 200
    data_v = resp_v.json()

    assert data_v["scenario_id"] == "SCN-TIMEOUT-001"
    assert data_v["mode"] == "VULNERABLE"
    assert data_v["status"] == "VULNERABILITY_CONFIRMED"
    assert data_v["invariants_passed"] is False
    assert data_v["finding_id"] == "CW-SEC-M2-001"

    # Verify timeline events
    event_types = [e["event_type"] for e in data_v["events"]]
    assert "INTENT_CREATED" in event_types
    assert "FAULT_TRIGGERED" in event_types
    assert "INVARIANT_CHECK" in event_types
    assert "FINDING_GENERATED" in event_types

    # 2. Fixed run
    resp_f = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-TIMEOUT-001",
        "mode": "FIXED"
    })
    assert resp_f.status_code == 200
    data_f = resp_f.json()

    assert data_f["scenario_id"] == "SCN-TIMEOUT-001"
    assert data_f["mode"] == "FIXED"
    assert data_f["status"] == "INVARIANT_PASSED"
    assert data_f["invariants_passed"] is True

    event_types_f = [e["event_type"] for e in data_f["events"]]
    assert "RETRY_RECONCILE" in event_types_f
    assert "RETEST_COMPLETED" in event_types_f


def test_run_webhook_scenario_vulnerable_and_fixed(client):
    """Verifies executing SCN-WEBHOOK-001 in vulnerable and fixed modes."""
    # 1. Vulnerable run
    resp_v = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-WEBHOOK-001",
        "mode": "VULNERABLE"
    })
    assert resp_v.status_code == 200
    data_v = resp_v.json()

    assert data_v["status"] == "VULNERABILITY_CONFIRMED"
    assert data_v["finding_id"] == "CW-SEC-M2-002"

    # 2. Fixed run
    resp_f = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-WEBHOOK-001",
        "mode": "FIXED"
    })
    assert resp_f.status_code == 200
    data_f = resp_f.json()

    assert data_f["status"] == "INVARIANT_PASSED"
    assert data_f["invariants_passed"] is True


def test_run_outbox_scenario(client):
    """Verifies executing SCN-OUTBOX-001 multi-supplier coordination."""
    resp = client.post("/api/scenarios/run", json={
        "scenario_id": "SCN-OUTBOX-001",
        "mode": "FIXED"
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "CONTROL_TEST_SUCCESS"
    assert data["invariants_passed"] is True
    assert data["raw_data"]["parent_status"] == "CONFIRMED"


def test_retest_finding_endpoint(client):
    """Verifies running the retest directly from a finding ID."""
    resp = client.post("/api/findings/CW-SEC-M2-001/retest")
    assert resp.status_code == 200
    data = resp.json()

    assert data["retest_passed"] is True
    assert data["finding"]["retest_result"] is not None
    assert data["finding"]["retest_result"]["fixed_mode_passed"] is True


def test_html_security_report_generation(client):
    """Verifies generating the executive HTML security assessment report."""
    resp = client.get("/api/reports/html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Crosswire Security Assessment Report" in resp.text
    assert "INV-5.2" in resp.text
    assert "INV-5.3" in resp.text
    assert "INV-5.5" in resp.text
    assert "CW-SEC-M2-001" in resp.text
    assert "Evidence SHA-256" in resp.text


def test_json_exports(client):
    """Verifies exporting findings and executions as JSON downloads."""
    resp_f = client.get("/api/reports/json/findings")
    assert resp_f.status_code == 200
    assert "application/json" in resp_f.headers["content-type"]
    assert len(resp_f.json()) >= 1

    resp_e = client.get("/api/reports/json/executions")
    assert resp_e.status_code == 200
    assert "application/json" in resp_e.headers["content-type"]
    execs = resp_e.json()
    assert len(execs) >= 1
    # Verify fail-closed verdict and SHA-256 evidence integrity on executions
    for ex in execs:
        assert "verdict" in ex
        assert ex["verdict"] in ("FAIL", "PASS", "INCONCLUSIVE")
        assert "evidence_sha256" in ex
        assert len(ex["evidence_sha256"]) == 64


def test_html_report_verdict_rendering_empty_unknown_inconclusive(client):
    """Verifies that empty, unknown, and INCONCLUSIVE verdicts render as INCONCLUSIVE and never receive PASS styling."""
    from control_center.state import get_state_store, ExecutionRecord
    from control_center.reports import generate_html_security_report

    state = get_state_store()

    # Add execution with empty verdict
    rec_empty = ExecutionRecord(
        execution_id="EXEC-TEST-EMPTY",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        mode="FIXED",
        timestamp="2026-11-17T12:00:00Z",
        duration_ms=100.0,
        status="INVARIANT_PASSED",
        intent_id="INT-EMPTY",
        invariants_checked=["INV-5.2"],
        invariants_passed=True,
        verdict="",  # Empty!
    )
    # Add execution with unknown verdict
    rec_unknown = ExecutionRecord(
        execution_id="EXEC-TEST-UNKNOWN",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        mode="FIXED",
        timestamp="2026-11-17T12:01:00Z",
        duration_ms=100.0,
        status="INVARIANT_PASSED",
        intent_id="INT-UNKNOWN",
        invariants_checked=["INV-5.2"],
        invariants_passed=True,
        verdict="UNKNOWN_VALUE",  # Unknown!
    )
    # Add execution with explicit INCONCLUSIVE verdict
    rec_inconclusive = ExecutionRecord(
        execution_id="EXEC-TEST-INCONC",
        scenario_id="SCN-TIMEOUT-001",
        scenario_name="Supplier Timeout",
        mode="FIXED",
        timestamp="2026-11-17T12:02:00Z",
        duration_ms=100.0,
        status="INVARIANT_PASSED",
        intent_id="INT-INCONC",
        invariants_checked=["INV-5.2"],
        invariants_passed=True,
        verdict="INCONCLUSIVE",
    )

    state.executions.insert(0, rec_inconclusive)
    state.executions.insert(0, rec_unknown)
    state.executions.insert(0, rec_empty)

    html = generate_html_security_report()

    # Verify that all three render with status-inconclusive and warning icon
    assert "status-inconclusive" in html
    assert "&#9888; INCONCLUSIVE" in html

    # Verify each row in html
    for ex_id in ["EXEC-TEST-EMPTY", "EXEC-TEST-UNKNOWN", "EXEC-TEST-INCONC"]:
        assert ex_id in html
        # Extract row
        row_start = html.find(f"<code>{ex_id}</code>")
        row_end = html.find("</tr>", row_start)
        row_html = html[row_start:row_end]
        assert "status-inconclusive" in row_html
        assert "INCONCLUSIVE" in row_html
        assert "status-pass" not in row_html
        assert "&#10004;" not in row_html  # No verified checkmark!


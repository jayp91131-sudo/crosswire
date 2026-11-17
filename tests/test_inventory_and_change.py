"""Crosswire 2.0: Tests for API Inventory and Change-Aware Security Testing Engines.

Verifies:
1. Inventory scanning across documented, shadow, zombie, and dormant APIs.
2. Asynchronous event handler inventory (outbox dispatcher, saga recovery worker, webhook processor).
3. Traffic recording and dormancy transition.
4. Git change detection and dependency mapping to security scenarios.
5. Impact scoring and execution plan generation.
"""

import pytest
from engine.inventory import (
    InventoryEngine,
    EndpointClassification,
    HandlerType,
)
from engine.change_detector import (
    ChangeAwareSecurityTestingEngine,
    ChangedFileSummary,
)


def test_api_inventory_scan_and_classifications():
    """Verifies that InventoryEngine discovers and classifies endpoints accurately."""
    engine = InventoryEngine()
    items = engine.scan_inventory()
    summary = engine.get_summary()

    assert len(items) >= 25
    assert summary["total_inventory_items"] >= 25

    # Check classifications exist
    classes = summary["classifications"]
    assert classes["active_documented"] >= 10
    assert classes["shadow_apis"] >= 1
    assert classes["zombie_apis"] >= 1
    assert classes["dormant_candidates"] >= 5

    # Check shadow API discovery
    shadows = [i for i in items if i.classification == EndpointClassification.SHADOW]
    shadow_paths = [s.path for s in shadows]
    assert any("/api/v1/internal/debug-state" in p for p in shadow_paths)

    # Check zombie API discovery
    zombies = [i for i in items if i.classification == EndpointClassification.ZOMBIE]
    zombie_paths = [z.path for z in zombies]
    assert any("/api/v0/legacy-checkout" in p for p in zombie_paths)


def test_asynchronous_event_handler_inventory():
    """Verifies that async workers and event consumers are inventoried alongside HTTP endpoints."""
    engine = InventoryEngine()
    items = engine.scan_inventory()

    async_handlers = [
        i for i in items
        if i.handler_type in (HandlerType.OUTBOX_DISPATCHER, HandlerType.SAGA_RECOVERY_WORKER, HandlerType.INBOUND_WEBHOOK)
    ]
    assert len(async_handlers) >= 3

    paths = [a.path for a in async_handlers]
    assert any("outbox" in p for p in paths)
    assert any("saga" in p for p in paths)
    assert any("webhook" in p for p in paths)


def test_traffic_recording_dormancy_update():
    """Verifies that observed live traffic promotes dormant endpoints to active."""
    engine = InventoryEngine()
    items_before = engine.scan_inventory()

    dormant_before = [i for i in items_before if i.classification == EndpointClassification.DORMANT_CANDIDATE]
    assert len(dormant_before) > 0
    target_item = dormant_before[0]

    # Record traffic
    engine.record_traffic(target_item.method, target_item.path)
    items_after = engine.scan_inventory()
    updated_item = next(i for i in items_after if i.item_id == target_item.item_id)
    assert updated_item.classification == EndpointClassification.ACTIVE_DOCUMENTED
    assert updated_item.observed_traffic_count == 1


def test_change_detector_git_diff_and_impact():
    """Verifies ChangeAwareSecurityTestingEngine maps file modifications to targeted security scenarios."""
    detector = ChangeAwareSecurityTestingEngine()
    report = detector.analyze_change_impact(git_ref="HEAD")

    assert report.git_ref == "HEAD"
    assert isinstance(report.changed_files, list)
    assert isinstance(report.selected_scenarios, list)
    assert isinstance(report.execution_plan, list)

    report_dict = report.to_dict()
    assert "total_scenarios_triggered" in report_dict
    assert "scan_timestamp" in report_dict


def test_change_detector_synthetic_dependency_selection():
    """Verifies targeted selection logic for synthetic change sets."""
    detector = ChangeAwareSecurityTestingEngine()

    # Test wallet file change
    test_files_wallet = [
        ChangedFileSummary(
            path="target_platform/aggregator/database.py",
            status="MODIFIED",
            lines_added=10,
            lines_deleted=2,
            touched_functions=["create_wallet_hold_vulnerable"]
        )
    ]
    selected_wallet = detector.select_scenarios_for_changes(test_files_wallet)
    scen_ids_wallet = [s.scenario_id for s in selected_wallet]
    assert "SCN-WALLET-001" in scen_ids_wallet

    # Test webhook file change
    test_files_webhook = [
        ChangedFileSummary(
            path="target_platform/payment/webhook_handler.py",
            status="MODIFIED",
            lines_added=5,
            lines_deleted=1,
            touched_functions=["handle_refund_webhook"]
        )
    ]
    selected_webhook = detector.select_scenarios_for_changes(test_files_webhook)
    scen_ids_webhook = [s.scenario_id for s in selected_webhook]
    assert "SCN-WEBHOOK-001" in scen_ids_webhook

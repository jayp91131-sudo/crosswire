"""
Pytest fixtures and configuration for Crosswire 2.0 synthetic platform tests.
Connects Agency Client -> Travel Aggregator -> Mock Hotel Supplier over ASGI HTTP transports,
backed by persistent SQLite databases.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport

from target_platform.aggregator.app import aggregator_app
from target_platform.supplier.app import supplier_app
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from target_platform.models import SupplierCapability
from target_platform.aggregator.supplier_client import SupplierClient
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.supplier.supplier_service import get_supplier_service
from target_platform.agency_client import AgencyClient
from target_platform.models import BookingIntent
from engine.scenario_runner import ScenarioRunner


@pytest.fixture(autouse=True)
def setup_and_teardown_databases():
    """Ensures clean SQLite databases and reset service configs between tests."""
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    pay_db = get_payment_db()
    agg_db.reset()
    sup_db.reset()
    pay_db.reset()

    # Reset service default states
    agg_service = get_aggregator_booking_service()
    sup_service = get_supplier_service()
    agg_service.set_mode(agg_service.mode.VULNERABLE)
    sup_service.configure(simulate_timeout=False, timeout_delay_seconds=0.35)

    yield

    agg_db.reset()
    sup_db.reset()
    pay_db.reset()


@pytest.fixture
def supplier_transport():
    """ASGI transport targeting the Mock Hotel Supplier app."""
    return ASGITransport(app=supplier_app)


@pytest.fixture
def aggregator_transport():
    """ASGI transport targeting the Travel Aggregator app."""
    return ASGITransport(app=aggregator_app)


@pytest.fixture
def supplier_client(supplier_transport):
    """Supplier client configured with 0.15s HTTP timeout and supplier ASGI transport."""
    return SupplierClient(
        base_url="http://mock-supplier",
        timeout=0.15,
        transport=supplier_transport,
        capability=SupplierCapability.IDEMPOTENT_WITH_LOOKUP,
    )


@pytest.fixture
def agency_client(aggregator_transport, supplier_client):
    """
    Agency Alpha client connected to Aggregator app.
    Wires aggregator booking service to use the ASGI-transported supplier client.
    """
    agg_service = get_aggregator_booking_service()
    agg_service.supplier_client = supplier_client

    return AgencyClient(
        agency_id="agency_alpha",
        api_key="secret_alpha_key_99",
        aggregator_base_url="http://travel-aggregator",
        transport=aggregator_transport,
    )


@pytest.fixture
def scenario_runner(agency_client):
    """Scenario runner wired with the test agency client."""
    runner = ScenarioRunner(agency_client=agency_client)
    return runner


@pytest.fixture
def synthetic_intent(agency_client) -> BookingIntent:
    """Standard synthetic booking intent for Agency Alpha."""
    return agency_client.create_synthetic_intent(
        intent_id="INTENT-001",
        hotel_id="htl_mumbai_grand",
        hotel_name="Synthetic Hotel Mumbai",
        amount=15000.0,
        currency="INR",
        guest_name="Aarav Sharma",
    )

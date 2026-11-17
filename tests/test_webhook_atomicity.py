"""
Atomicity and Failure-Injection Verification Test Suite for Webhook Processing.
Verifies that if an unexpected error occurs during intermediate database writes
inside process_webhook_event_fixed, the entire transaction rolls back completely (BEGIN IMMEDIATE),
leaving zero partial side effects in booking_records, refund_records, ledger_entries, or webhook_events,
and allowing a subsequent retry to succeed cleanly.
"""

import sqlite3
import pytest
from datetime import datetime
from target_platform.agency_client import AgencyClient
from target_platform.aggregator.database import get_aggregator_db
from target_platform.payment.payment_service import get_payment_service
from target_platform.models import (
    TransactionStatus,
    WebhookPayload,
    get_provider_registry,
    LedgerEntryType,
)


@pytest.mark.asyncio
async def test_webhook_processing_atomicity_and_rollback_on_failure(agency_client: AgencyClient, monkeypatch):
    """
    Simulates a failure during intermediate database writes inside process_webhook_event_fixed.
    Verifies that:
    1. Entire transaction rolls back.
    2. total_refunded_minor remains 0.
    3. refund_record status remains 'AUTHORIZED'.
    4. No orphan ledger_entry is created.
    5. No webhook_events record is left behind.
    6. Retrying the valid webhook afterwards succeeds cleanly.
    """
    agg_db = get_aggregator_db()
    pay_service = get_payment_service()
    reg = get_provider_registry()

    # 1. Setup cancelled booking & authorized refund
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-ATOMIC-FAIL-01", amount=10000.0)
    init_res = await agency_client.submit_booking(intent)
    assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED
    cancel_res = await agency_client.cancel_booking(init_res.booking_id)
    assert cancel_res.status == TransactionStatus.CANCELLED

    booking_id = init_res.booking_id
    rfd_id = "RFD-ATOMIC-FAIL-01"
    event_id = "evt_atomic_injection_01"

    await agency_client.authorize_refund(
        booking_id=booking_id,
        refund_id=rfd_id,
        amount_minor=500000,
        currency="INR",
        payment_id=f"PAY-INTENT-ATOMIC-FAIL-01",
    )

    payload = pay_service.generate_refund_webhook_event(
        booking_id=booking_id,
        payment_id=f"PAY-INTENT-ATOMIC-FAIL-01",
        refund_id=rfd_id,
        amount_minor=500000,
        currency="INR",
        event_id=event_id,
    )

    # 2. Intercept sqlite3.connect to inject a failure specifically on the ledger entry insert
    real_connect = sqlite3.connect
    fail_injected = False

    class FaultyConnectionProxy:
        def __init__(self, raw_conn):
            self.__dict__["_conn"] = raw_conn

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO ledger_entries" in sql:
                nonlocal fail_injected
                fail_injected = True
                raise sqlite3.OperationalError("SIMULATED DISK/LOCK FAULT DURING LEDGER INSERTION")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            if name == "_conn":
                self.__dict__["_conn"] = value
            else:
                setattr(self._conn, name, value)

    def faulty_connect(*args, **kwargs):
        c = real_connect(*args, **kwargs)
        return FaultyConnectionProxy(c)

    monkeypatch.setattr(sqlite3, "connect", faulty_connect)

    with pytest.raises(Exception) as excinfo:
        agg_db.process_webhook_event_fixed(payload, "test_hash")
    assert "SIMULATED DISK/LOCK FAULT" in str(excinfo.value)
    assert fail_injected is True, "Fault injection hook was not triggered"

    # Remove patch so subsequent normal operations use real connection
    monkeypatch.undo()

    # 3. Verify Rollback Guarantees: ZERO partial side effects in SQLite
    b_record = agg_db.get_booking_record(booking_id)
    assert b_record.total_refunded_minor == 0, "Booking total_refunded_minor must NOT have updated"
    assert b_record.status == TransactionStatus.CANCELLED, "Booking status must remain CANCELLED"

    r_record = agg_db.get_refund_record(rfd_id)
    assert r_record is not None
    assert r_record.status == "AUTHORIZED", "Refund record status must remain AUTHORIZED (not COMPLETED)"
    assert r_record.event_id is None, "Refund record event_id must remain None"

    ledger_entries = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_ledger_entries = [e for e in ledger_entries if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_ledger_entries) == 0, "No orphan ledger entries should have been committed"

    wh_event = agg_db.get_webhook_event(event_id, provider="mock_pay_provider")
    assert wh_event is None, "No webhook_event row should have been committed"

    # 4. Normal Retry: Deliver the webhook normally without faults -> must succeed cleanly
    resp = await agency_client.deliver_webhook(payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "PROCESSED"

    # Verify final consistent state
    b_record_after = agg_db.get_booking_record(booking_id)
    assert b_record_after.total_refunded_minor == 500000

    r_record_after = agg_db.get_refund_record(rfd_id)
    assert r_record_after.status == "COMPLETED"
    assert r_record_after.event_id == event_id

    ledger_after = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_entries_after = [e for e in ledger_after if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries_after) == 1
    assert refund_entries_after[0].amount_minor == 500000
    assert refund_entries_after[0].event_id == event_id

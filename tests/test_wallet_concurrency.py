"""
Milestone 2.2 — Test Suite: Agency Wallet Concurrency & Integrity (Scenario A).
Verifies:
1. Vulnerable mode TOCTOU race: two simultaneous requests of ₹7,000 against ₹10,000 balance
   both succeed, causing wallet overdraft and emitting finding CW-SEC-M2-201.
2. Fixed mode atomic enforcement: exactly one request succeeds, the second fails with
   InsufficientWalletFundsError (HTTP 400), available balance remains non-negative (₹3,000).
3. Invariants 6.1 (non-negative balance), 6.2 (holds bounded), and 6.3 (ledger accounting).
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from target_platform.aggregator.app import aggregator_app
from target_platform.aggregator.database import get_aggregator_db
from target_platform.aggregator.booking_service import get_aggregator_booking_service
from target_platform.models import (
    AggregatorMode,
    BookingIntent,
    InsufficientWalletFundsError,
    WalletDepositRequest,
)
from engine.invariant_engine import get_invariant_engine
from engine.ground_truth_oracle import get_ground_truth_oracle


@pytest.fixture(autouse=True)
def setup_teardown():
    db = get_aggregator_db()
    db.reset()
    service = get_aggregator_booking_service()
    service.set_mode(AggregatorMode.FIXED)
    service.set_wallet_mode(AggregatorMode.FIXED)
    yield
    db.reset()
    service.set_wallet_mode(AggregatorMode.FIXED)


@pytest.mark.asyncio
async def test_vulnerable_mode_wallet_overdraft_toctou_race():
    """
    Scenario A (Vulnerable):
    Initial balance: ₹10,000 (1,000,000 minor units).
    Simultaneous requests: A (₹7,000) & B (₹7,000).
    Vulnerable mode introduces non-atomic TOCTOU check-then-act.
    Both requests pass check and grant holds, causing total holds = ₹14,000 (> ₹10,000).
    Invariant 6.1 fails and emits CW-SEC-M2-201.
    """
    db = get_aggregator_db()
    service = get_aggregator_booking_service()
    service.set_wallet_mode(AggregatorMode.VULNERABLE)
    invariant_engine = get_invariant_engine()

    agency_id = "agency_alpha"
    wallet_before = db.get_wallet(agency_id)
    assert wallet_before.available_balance_minor == 1000000  # ₹10,000

    # Execute two simultaneous holds of ₹7,000 (700,000 minor)
    async def create_hold(booking_suffix: str):
        return await asyncio.to_thread(
            db.create_wallet_hold_vulnerable,
            agency_id=agency_id,
            booking_id=f"BKG-VULN-{booking_suffix}",
            amount_minor=700000,
            currency="INR",
        )

    # Launch both concurrently
    results = await asyncio.gather(
        create_hold("1"),
        create_hold("2"),
        return_exceptions=True,
    )

    # In vulnerable mode, both holds succeed due to TOCTOU window
    assert not isinstance(results[0], Exception)
    assert not isinstance(results[1], Exception)

    wallet_after = db.get_wallet(agency_id)
    assert wallet_after.active_holds_minor == 1400000  # ₹14,000 active holds!
    assert wallet_after.available_balance_minor == -400000  # -₹4,000 (OVERDRAFT)

    # Invariant 6.1 check MUST fail and emit CW-SEC-M2-201
    inv_res = invariant_engine.check_wallet_available_balance(agency_id)
    assert not inv_res.passed
    assert inv_res.cwe.startswith("CWE-367")
    assert "CW-SEC-M2-201" in inv_res.evidence["finding_id"]
    assert inv_res.evidence["violation_detected"] is True


@pytest.mark.asyncio
async def test_fixed_mode_prevents_wallet_overdraft_atomic_enforcement():
    """
    Scenario A (Fixed):
    Initial balance: ₹10,000 (1,000,000 minor units).
    Simultaneous requests: A (₹7,000) & B (₹7,000).
    Fixed mode uses atomic conditional UPDATE.
    Exactly one succeeds; the other is rejected with InsufficientWalletFundsError.
    Final available balance: ₹3,000 (300,000 minor).
    Invariants 6.1, 6.2, 6.3 all pass.
    """
    db = get_aggregator_db()
    service = get_aggregator_booking_service()
    service.set_wallet_mode(AggregatorMode.FIXED)
    invariant_engine = get_invariant_engine()

    agency_id = "agency_alpha"
    wallet_before = db.get_wallet(agency_id)
    assert wallet_before.available_balance_minor == 1000000

    async def create_hold(booking_suffix: str):
        return await asyncio.to_thread(
            db.create_wallet_hold_fixed,
            agency_id=agency_id,
            booking_id=f"BKG-FIXED-{booking_suffix}",
            amount_minor=700000,
            currency="INR",
        )

    # Launch both concurrently
    results = await asyncio.gather(
        create_hold("1"),
        create_hold("2"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 failure, got {len(failures)}"
    assert isinstance(failures[0], InsufficientWalletFundsError)

    wallet_after = db.get_wallet(agency_id)
    assert wallet_after.active_holds_minor == 700000  # exactly ₹7,000
    assert wallet_after.available_balance_minor == 300000  # exactly ₹3,000 remaining

    # Check Invariants 6.1, 6.2, 6.3
    inv_6_1 = invariant_engine.check_wallet_available_balance(agency_id)
    assert inv_6_1.passed
    assert inv_6_1.actual == "available_balance_minor = 300000 (>= 0)"

    inv_6_2 = invariant_engine.check_wallet_holds_bounded(agency_id)
    assert inv_6_2.passed

    inv_6_3 = invariant_engine.check_wallet_total_accounting(agency_id)
    assert inv_6_3.passed


@pytest.mark.asyncio
async def test_wallet_deposit_and_ledger_double_entry_integrity():
    """
    Tests wallet deposit, updating funded balance, transaction journaling,
    and balanced double-entry ledger records.
    """
    db = get_aggregator_db()
    agency_id = "agency_beta"

    wallet_before = db.get_wallet(agency_id)
    assert wallet_before.funded_balance_minor == 1000000

    # Deposit ₹5,000 (500,000 minor)
    wallet_after = db.deposit_wallet_funds(agency_id, amount_minor=500000, reference="DEP-TEST-001")
    assert wallet_after.funded_balance_minor == 1500000
    assert wallet_after.available_balance_minor == 1500000

    # Verify transaction record
    txs = db.get_wallet_transactions(agency_id)
    dep_tx = [t for t in txs if t.reference == "DEP-TEST-001"]
    assert len(dep_tx) == 1
    assert dep_tx[0].amount_minor == 500000

    # Verify double-entry ledger
    ledger_entries = db.get_ledger_entries_for_booking("WALLET_FUNDING")
    batch_entries = [e for e in ledger_entries if e.journal_batch_id == "DEP-TEST-001"]
    assert len(batch_entries) == 2
    debit = next(e for e in batch_entries if e.direction.value == "DEBIT")
    credit = next(e for e in batch_entries if e.direction.value == "CREDIT")
    assert debit.amount_minor == credit.amount_minor == 500000


@pytest.mark.asyncio
async def test_wallet_hold_capture_and_release_lifecycle():
    """
    Tests complete lifecycle: HOLD -> CAPTURE (balance debited) and HOLD -> RELEASE (funds restored).
    """
    db = get_aggregator_db()
    agency_id = "agency_alpha"

    # 1. Create hold of ₹2,000 (200,000 minor)
    hold1 = db.create_wallet_hold_fixed(agency_id, booking_id="BKG-CAP-01", amount_minor=200000)
    w1 = db.get_wallet(agency_id)
    assert w1.active_holds_minor == 200000
    assert w1.available_balance_minor == 800000

    # 2. Capture hold1
    h1_captured, w2 = db.capture_wallet_hold(hold1.hold_id)
    assert h1_captured.status.value == "CAPTURED"
    assert w2.active_holds_minor == 0
    assert w2.total_captured_minor == 200000
    assert w2.available_balance_minor == 800000

    # 3. Create hold of ₹3,000 (300,000 minor)
    hold2 = db.create_wallet_hold_fixed(agency_id, booking_id="BKG-REL-01", amount_minor=300000)
    w3 = db.get_wallet(agency_id)
    assert w3.active_holds_minor == 300000
    assert w3.available_balance_minor == 500000

    # 4. Release hold2 (saga compensation)
    h2_released, w4 = db.release_wallet_hold(hold2.hold_id, reason="TEST_RELEASE")
    assert h2_released.status.value == "RELEASED"
    assert w4.active_holds_minor == 0
    assert w4.available_balance_minor == 800000  # Funds restored!

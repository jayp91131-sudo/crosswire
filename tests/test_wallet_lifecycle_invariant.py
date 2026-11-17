"""
Comprehensive Wallet Lifecycle Test Suite for Canonical Invariant 5.1
Specification: MASTER_PLAN_V2.md Section 5.1

Verifies:
1. Start with a known funded balance.
2. Create a wallet hold (pending hold is a reservation, NOT a posted debit).
3. Capture the hold (posts debit, decrements active hold, increments lifetime captured).
4. Post a partial wallet refund (restores available balance and closing balance).
5. Create another hold using the restored available balance (succeeds where it would have failed pre-refund).
6. Verify closing balance against signed posted ledger entries:
   closing_balance = opening_balance + sum(signed_posted_ledger_entries).
7. Verify available balance after the second hold:
   available_balance = closing_balance - active_holds >= 0.
8. Verify refund is credited exactly once (idempotent duplicate refund does not duplicate credits).
9. Verify captured lifetime total is NOT silently decremented to hide a refund.
10. Verify card refunds do NOT mutate the agency wallet.
All calculations use exact integer minor units.
"""

import pytest
from datetime import datetime
from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    LedgerEntry,
    LedgerEntryType,
    LedgerDirection,
    WalletHoldStatus,
    WalletTransactionType,
)
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from engine.invariant_engine import get_invariant_engine
from engine.models import InvariantType


@pytest.fixture
def clean_dbs():
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    pay_db = get_payment_db()
    agg_db.reset()
    sup_db.reset()
    pay_db.reset()
    yield agg_db, sup_db, pay_db
    agg_db.reset()
    sup_db.reset()
    pay_db.reset()


@pytest.fixture
def inv_engine():
    return get_invariant_engine()


def test_complete_wallet_lifecycle_invariant_5_1(clean_dbs, inv_engine):
    agg_db, _, _ = clean_dbs
    agency_id = "agency_alpha"

    # 1. Start with a known funded balance (Opening deposit = 100,000 minor units / ₹1,000.00)
    # Reset seeds agency_alpha with 1,000,000 minor units. Let's inspect initial state.
    wallet_0 = agg_db.get_wallet(agency_id)
    assert wallet_0 is not None
    initial_funded_minor = wallet_0.funded_balance_minor
    assert initial_funded_minor > 0
    assert wallet_0.total_captured_minor == 0
    assert wallet_0.total_refunded_minor == 0
    assert wallet_0.active_holds_minor == 0
    assert wallet_0.available_balance_minor == initial_funded_minor

    # 2. Create a wallet hold for Booking 1 (40,000 minor units / ₹400.00)
    booking_1 = "BK-WL-001"
    hold_1_amount_minor = 40000
    hold_1 = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_1,
        amount_minor=hold_1_amount_minor,
        currency="INR",
        idempotency_key="idemp_hold_1",
    )
    assert hold_1.status == WalletHoldStatus.HELD
    assert hold_1.amount_minor == hold_1_amount_minor

    wallet_1 = agg_db.get_wallet(agency_id)
    assert wallet_1.active_holds_minor == hold_1_amount_minor
    assert wallet_1.available_balance_minor == initial_funded_minor - hold_1_amount_minor
    # Closing balance remains initial_funded_minor: pending holds are reservations, NOT posted debits!
    closing_1 = wallet_1.funded_balance_minor + wallet_1.total_refunded_minor - wallet_1.total_captured_minor
    assert closing_1 == initial_funded_minor

    # Save booking intent 1 and booking record 1 (WALLET payment)
    agg_db.save_booking_intent(
        BookingIntent(
            intent_id="INTENT-WL-001",
            agency_id=agency_id,
            hotel_id="htl_001",
            hotel_name="Grand 1",
            room_type="standard",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=400.0,
            amount_minor=hold_1_amount_minor,
            currency="INR",
            guest_name="Guest 1",
            payment_method="WALLET",
        )
    )
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_1,
            intent_id="INTENT-WL-001",
            agency_id=agency_id,
            status=TransactionStatus.BOOKING_PENDING,
            amount=400.0,
            amount_minor=hold_1_amount_minor,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold_1.hold_id,
            refundable_entitlement=300.0,
            refundable_entitlement_minor=30000,  # 30,000 minor units refundable after 10,000 penalty
            cancellation_penalty=100.0,
            cancellation_penalty_minor=10000,
            correlation_id="corr-wl-1",
        )
    )

    # 3. Capture the hold
    h_captured, wallet_2 = agg_db.capture_wallet_hold(hold_1.hold_id)
    assert h_captured.status == WalletHoldStatus.CAPTURED
    assert wallet_2.active_holds_minor == 0
    assert wallet_2.total_captured_minor == hold_1_amount_minor
    assert wallet_2.available_balance_minor == initial_funded_minor - hold_1_amount_minor
    # Closing balance is now initial_funded_minor - 40,000
    closing_2 = wallet_2.funded_balance_minor + wallet_2.total_refunded_minor - wallet_2.total_captured_minor
    assert closing_2 == initial_funded_minor - hold_1_amount_minor

    # Update booking record to CANCELLED for refund
    b1_rec = agg_db.get_booking_record(booking_1)
    b1_rec.status = TransactionStatus.CANCELLED
    agg_db.save_booking_record(b1_rec)

    # 4. Post a partial wallet refund (30,000 minor units / ₹300.00)
    refund_amount_minor = 30000
    saved_rfd, updated_b1, rfd_entries = agg_db.execute_atomic_refund(
        booking_id=booking_1,
        agency_id=agency_id,
        refund_amount_minor=refund_amount_minor,
        currency="INR",
        reason="Guest cancellation within policy",
        idempotency_key="idemp_rfd_wl_01",
    )
    assert saved_rfd.status == "COMPLETED"
    assert saved_rfd.amount_minor == refund_amount_minor

    wallet_3 = agg_db.get_wallet(agency_id)
    assert wallet_3.total_refunded_minor == refund_amount_minor
    # 9. Verify captured lifetime total is NOT silently decremented to hide a refund
    assert wallet_3.total_captured_minor == hold_1_amount_minor
    # Closing balance restored: initial - 40,000 + 30,000
    closing_3 = wallet_3.funded_balance_minor + wallet_3.total_refunded_minor - wallet_3.total_captured_minor
    assert closing_3 == initial_funded_minor - hold_1_amount_minor + refund_amount_minor
    assert wallet_3.available_balance_minor == closing_3

    # 5. Create another hold using the restored available balance
    # Try creating a hold for (initial_funded_minor - 40,000 + 10,000)
    # This amount is strictly GREATER than the pre-refund available balance (which was initial - 40,000).
    # This explicitly proves the refund restored purchasing power!
    booking_2 = "BK-WL-002"
    hold_2_amount_minor = (initial_funded_minor - hold_1_amount_minor) + 10000
    hold_2 = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_2,
        amount_minor=hold_2_amount_minor,
        currency="INR",
        idempotency_key="idemp_hold_2",
    )
    assert hold_2.status == WalletHoldStatus.HELD
    assert hold_2.amount_minor == hold_2_amount_minor

    wallet_4 = agg_db.get_wallet(agency_id)
    assert wallet_4.active_holds_minor == hold_2_amount_minor

    # 7. Verify available balance after the second hold
    # Available = Closing Balance - Active Holds
    expected_avail_after_hold_2 = closing_3 - hold_2_amount_minor
    assert expected_avail_after_hold_2 == 20000  # 30,000 refund - 10,000 extra hold
    assert wallet_4.available_balance_minor == expected_avail_after_hold_2
    assert wallet_4.available_balance_minor >= 0

    # 6. Verify closing balance against signed posted ledger entries
    # closing_balance = opening_balance + sum(signed_posted_ledger_entries)
    # Run canonical derivation invariant check
    res_derivation = inv_engine.check_canonical_wallet_balance_derivation(agency_id)
    assert res_derivation.passed is True
    assert res_derivation.invariant == InvariantType.WALLET_BALANCE_DERIVATION

    # Run total accounting invariant check
    res_accounting = inv_engine.check_wallet_total_accounting(agency_id)
    assert res_accounting.passed is True

    # 8. Verify the refund is credited exactly once (idempotent replay)
    dup_rfd, _, dup_entries = agg_db.execute_atomic_refund(
        booking_id=booking_1,
        agency_id=agency_id,
        refund_amount_minor=refund_amount_minor,
        currency="INR",
        reason="Duplicate refund attempt",
        idempotency_key="idemp_rfd_wl_01",  # Same idempotency key
    )
    assert dup_rfd.refund_id == saved_rfd.refund_id
    assert len(dup_entries) == 0  # Zero additional ledger entries posted

    wallet_after_dup = agg_db.get_wallet(agency_id)
    assert wallet_after_dup.total_refunded_minor == refund_amount_minor
    assert wallet_after_dup.available_balance_minor == expected_avail_after_hold_2

    # Check transactions in wallet_transactions: exactly 1 REFUND transaction
    w_txs = agg_db.get_wallet_transactions(agency_id)
    refund_txs = [t for t in w_txs if t.transaction_type == WalletTransactionType.REFUND]
    assert len(refund_txs) == 1
    assert refund_txs[0].amount_minor == refund_amount_minor

    # 10. Verify card refunds do not mutate the agency wallet
    booking_card = "BK-CARD-REFUND-001"
    card_amount_minor = 25000
    agg_db.save_booking_intent(
        BookingIntent(
            intent_id="INTENT-CARD-RFD-001",
            agency_id=agency_id,
            hotel_id="htl_002",
            hotel_name="Grand 2",
            room_type="standard",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=250.0,
            amount_minor=card_amount_minor,
            currency="INR",
            guest_name="Card Guest",
            payment_method="CARD",
        )
    )
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_card,
            intent_id="INTENT-CARD-RFD-001",
            agency_id=agency_id,
            status=TransactionStatus.CANCELLED,
            amount=250.0,
            amount_minor=card_amount_minor,
            currency="INR",
            payment_method="CARD",
            payment_id="PAY-CARD-001",
            refundable_entitlement=250.0,
            refundable_entitlement_minor=card_amount_minor,
            cancellation_penalty=0.0,
            cancellation_penalty_minor=0,
            correlation_id="corr-card-rfd",
        )
    )

    card_rfd, _, card_rfd_entries = agg_db.execute_atomic_refund(
        booking_id=booking_card,
        agency_id=agency_id,
        refund_amount_minor=card_amount_minor,
        currency="INR",
        reason="Card refund",
        idempotency_key="idemp_card_rfd_01",
        payment_id="PAY-CARD-001",
    )
    assert card_rfd.status == "COMPLETED"

    # Wallet state MUST be completely untouched by card refund!
    wallet_final = agg_db.get_wallet(agency_id)
    assert wallet_final.funded_balance_minor == wallet_4.funded_balance_minor
    assert wallet_final.total_captured_minor == wallet_4.total_captured_minor
    assert wallet_final.total_refunded_minor == wallet_4.total_refunded_minor
    assert wallet_final.active_holds_minor == wallet_4.active_holds_minor
    assert wallet_final.available_balance_minor == wallet_4.available_balance_minor

    # Final invariant check: canonical derivation remains strictly verified
    res_final = inv_engine.check_canonical_wallet_balance_derivation(agency_id)
    assert res_final.passed is True

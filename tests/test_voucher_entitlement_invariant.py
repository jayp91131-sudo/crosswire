"""
Test suite for Canonical Invariant 5.4: Ticket / Voucher Entitlement
Specification: MASTER_PLAN_V2.md Section 5.4
voucher_issued(B) => supplier_confirmed(B) AND payment_or_wallet_obligation_satisfied(B)
"""

import pytest
from datetime import datetime

from target_platform.models import (
    BookingIntent,
    AggregatorBookingRecord,
    TransactionStatus,
    SupplierReservation,
    WalletHoldStatus,
    PaymentTransactionRecord,
    PaymentStatus,
    VoucherRecord,
    VoucherStatus,
)
from engine.invariant_engine import get_invariant_engine
from engine.models import InvariantType, Severity
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db


@pytest.fixture
def inv_engine():
    return get_invariant_engine()


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


def _create_intent(agg_db, intent_id: str, agency_id: str = "agency_alpha", amount: float = 100.0) -> BookingIntent:
    intent = BookingIntent(
        intent_id=intent_id,
        agency_id=agency_id,
        hotel_id="hotel_1",
        hotel_name="Hotel Grand",
        room_type="deluxe",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=amount,
        amount_minor=int(amount * 100),
        currency="INR",
        guest_name="Guest",
        correlation_id=f"corr_{intent_id}",
    )
    agg_db.save_booking_intent(intent)
    return intent


def test_valid_wallet_booking_voucher_entitled_pass(clean_dbs, inv_engine):
    """Test 1: Valid supplier confirmation + wallet capture + voucher (Pass)."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-WALLET-001"
    intent_id = "INTENT-W-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-W-001"

    _create_intent(agg_db, intent_id, agency_id)

    # 1. Supplier reservation confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Alice",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # 2. Wallet hold captured
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_w_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # 3. Booking record with voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-001",
            correlation_id="corr-1",
        )
    )
    agg_db.save_voucher(
        VoucherRecord(
            voucher_id="VOUCHER-001",
            booking_id=booking_id,
            agency_id=agency_id,
            status=VoucherStatus.ISSUED,
            issued_at=datetime.utcnow(),
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is True
    assert result.invariant == InvariantType.VOUCHER_ENTITLEMENT


def test_valid_card_booking_voucher_entitled_pass(clean_dbs, inv_engine):
    """Test 2: Valid supplier confirmation + card capture + voucher (Pass)."""
    agg_db, sup_db, pay_db = clean_dbs
    booking_id = "BK-CARD-001"
    intent_id = "INTENT-C-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-C-001"
    payment_id = "PAY-C-001"

    _create_intent(agg_db, intent_id, agency_id, amount=150.0)

    # 1. Supplier reservation confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Bob",
            amount=150.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # 2. Card payment captured
    pay_db.save_payment(
        PaymentTransactionRecord(
            payment_id=payment_id,
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            amount=150.0,
            amount_minor=15000,
            currency="INR",
            status=PaymentStatus.CAPTURED,
            transaction_reference="TXN-CARD-99",
        )
    )

    # 3. Booking record with voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=150.0,
            amount_minor=15000,
            currency="INR",
            payment_method="CARD",
            payment_id=payment_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-002",
            correlation_id="corr-2",
        )
    )
    agg_db.save_voucher(
        VoucherRecord(
            voucher_id="VOUCHER-002",
            booking_id=booking_id,
            agency_id=agency_id,
            status=VoucherStatus.ISSUED,
            issued_at=datetime.utcnow(),
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is True
    assert result.invariant == InvariantType.VOUCHER_ENTITLEMENT


def test_voucher_issued_before_supplier_confirmation_fail(clean_dbs, inv_engine):
    """Test 3: Voucher issued before supplier confirmation (Fail)."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-PREMATURE-SUP-001"
    intent_id = "INTENT-PRE-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-PRE-001"

    _create_intent(agg_db, intent_id, agency_id)

    # 1. Supplier reservation exists but is PENDING (Not CONFIRMED!)
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Charlie",
            amount=100.0,
            currency="INR",
            status="PENDING",
        )
    )

    # 2. Wallet hold captured
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_w_pre_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # 3. Booking with prematurely issued voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-PREMATURE",
            correlation_id="corr-3",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert result.invariant == InvariantType.VOUCHER_ENTITLEMENT
    assert result.severity == Severity.CRITICAL
    assert "Supplier reservation 'SUP-PRE-001' is not confirmed" in result.actual


def test_voucher_issued_before_wallet_capture_fail(clean_dbs, inv_engine):
    """Test 4: Voucher issued before wallet capture (Fail)."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-PREMATURE-HOLD-001"
    intent_id = "INTENT-PRE-HOLD-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-PRE-HOLD-001"

    _create_intent(agg_db, intent_id, agency_id)

    # 1. Supplier confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="David",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # 2. Wallet hold is still HELD (not CAPTURED!)
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_w_pre_hold",
    )

    # 3. Booking with voucher issued
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-UNCAPTURED-HOLD",
            correlation_id="corr-4",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Wallet hold is not captured" in result.actual


def test_voucher_issued_with_failed_card_payment_fail(clean_dbs, inv_engine):
    """Test 5: Voucher issued with failed card payment (Fail)."""
    agg_db, sup_db, pay_db = clean_dbs
    booking_id = "BK-FAILED-CARD-001"
    intent_id = "INTENT-FAILED-CARD-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-FAIL-CARD-001"
    payment_id = "PAY-FAIL-CARD-001"

    _create_intent(agg_db, intent_id, agency_id, amount=120.0)

    # 1. Supplier confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Eve",
            amount=120.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # 2. Payment is FAILED
    pay_db.save_payment(
        PaymentTransactionRecord(
            payment_id=payment_id,
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            amount=120.0,
            amount_minor=12000,
            currency="INR",
            status=PaymentStatus.FAILED,
            transaction_reference="TXN-FAIL-01",
        )
    )

    # 3. Booking with voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=120.0,
            amount_minor=12000,
            currency="INR",
            payment_method="CARD",
            payment_id=payment_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-FAILED-CARD",
            correlation_id="corr-5",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Card payment is not captured (status: 'FAILED')" in result.actual


def test_voucher_issued_while_payment_is_pending_fail(clean_dbs, inv_engine):
    """Test 6: Voucher issued while payment is pending/authorized (Fail)."""
    agg_db, sup_db, pay_db = clean_dbs
    booking_id = "BK-PENDING-CARD-001"
    intent_id = "INTENT-PENDING-CARD-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-PEND-CARD-001"
    payment_id = "PAY-PEND-CARD-001"

    _create_intent(agg_db, intent_id, agency_id, amount=120.0)

    # 1. Supplier confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Frank",
            amount=120.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # 2. Payment is only AUTHORIZED, not CAPTURED
    pay_db.save_payment(
        PaymentTransactionRecord(
            payment_id=payment_id,
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            amount=120.0,
            amount_minor=12000,
            currency="INR",
            status=PaymentStatus.AUTHORIZED,
            transaction_reference="TXN-AUTH-ONLY",
        )
    )

    # 3. Booking with voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=120.0,
            amount_minor=12000,
            currency="INR",
            payment_method="CARD",
            payment_id=payment_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-PENDING-PAY",
            correlation_id="corr-6",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Card payment is not captured (status: 'AUTHORIZED')" in result.actual


def test_missing_supplier_reservation_fail(clean_dbs, inv_engine):
    """Test 7: Missing supplier reservation (Fail)."""
    agg_db, _, _ = clean_dbs
    booking_id = "BK-GHOST-SUP-001"
    intent_id = "INTENT-GHOST-001"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_id, agency_id)

    # Wallet hold captured
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_w_ghost",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # Booking with voucher but NO supplier reservation exists in supplier.db
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id="NONEXISTENT-SUP-ID",
            voucher_id="VOUCHER-GHOST-SUP",
            correlation_id="corr-7",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Supplier reservation 'NONEXISTENT-SUP-ID' is not confirmed (status: 'MISSING')" in result.actual


def test_cross_agency_booking_voucher_evidence_mismatch_fail(clean_dbs, inv_engine):
    """Test 8: Cross-agency booking/voucher evidence mismatch (Fail)."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-CROSS-AGENCY-001"
    intent_id = "INTENT-CROSS-001"
    sup_res_id = "SUP-CROSS-001"

    # Save intent for Agency Alpha
    _create_intent(agg_db, intent_id, "agency_alpha")

    # Supplier confirmed for intent
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Grace",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Hold belongs to Agency BETA
    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_beta",  # Different agency!
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_w_cross",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # Booking belongs to Agency ALPHA
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id="agency_alpha",  # Agency Alpha
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-CROSS-AGENCY",
            correlation_id="corr-8",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Cross-agency evidence mismatch detected" in result.actual


# ---------------------------------------------------------
# Release Gate Task 2: Explicit Negative Proof Tests
# ---------------------------------------------------------

def test_negative_same_agency_wallet_hold_belongs_to_different_booking(clean_dbs, inv_engine):
    """Negative Test 1: Same-agency wallet hold belongs to a different booking."""
    agg_db, sup_db, _ = clean_dbs
    booking_1 = "BK-SAME-AG-001"
    booking_2 = "BK-SAME-AG-002"
    intent_1 = "INTENT-SAG-001"
    intent_2 = "INTENT-SAG-002"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_1, agency_id)
    _create_intent(agg_db, intent_2, agency_id)

    # Supplier confirmed for intent 1
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-SAG-001",
            intent_id=intent_1,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Hold was created and captured for BOOKING 2!
    hold_2 = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_2,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_sag_002",
    )
    agg_db.capture_wallet_hold(hold_2.hold_id)

    # Booking 1 claims voucher using Booking 2's captured hold
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_1,
            intent_id=intent_1,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold_2.hold_id,
            primary_supplier_reservation_id="SUP-SAG-001",
            voucher_id=f"VOUCHER-{booking_1}",
            correlation_id="corr-sag-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_1)
    assert result.passed is False
    assert f"belongs to different booking '{booking_2}'" in result.actual


def test_negative_same_agency_card_transaction_belongs_to_different_booking(clean_dbs, inv_engine):
    """Negative Test 2: Same-agency card transaction belongs to a different booking."""
    agg_db, sup_db, pay_db = clean_dbs
    booking_1 = "BK-CARD-DIFF-001"
    booking_2 = "BK-CARD-DIFF-002"
    intent_1 = "INTENT-CDIFF-001"
    intent_2 = "INTENT-CDIFF-002"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_1, agency_id)
    _create_intent(agg_db, intent_2, agency_id)

    # Supplier confirmed for intent 1
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-CDIFF-001",
            intent_id=intent_1,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Card payment captured for BOOKING 2!
    pay_rec_2 = pay_db.save_payment(
        PaymentTransactionRecord(
            payment_id="PAY-DIFF-002",
            booking_id=booking_2,
            intent_id=intent_2,
            agency_id=agency_id,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            status=PaymentStatus.CAPTURED,
            transaction_reference="TX-DIFF-002",
        )
    )

    # Booking 1 issued voucher referencing Booking 2's payment
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_1,
            intent_id=intent_1,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="CARD",
            payment_id=pay_rec_2.payment_id,
            primary_supplier_reservation_id="SUP-CDIFF-001",
            voucher_id=f"VOUCHER-{booking_1}",
            correlation_id="corr-cdiff-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_1)
    assert result.passed is False
    assert f"belongs to different booking '{booking_2}'" in result.actual


def test_negative_captured_amount_below_booking_payable(clean_dbs, inv_engine):
    """Negative Test 3: Captured amount is below the booking payable amount."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-UNDERPAID-001"
    intent_id = "INTENT-UNDER-001"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_id, agency_id, amount=200.0)

    # Supplier confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-UNDER-001",
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=200.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Hold captured for only 150.0 (15000 minor units), but payable is 200.0 (20000 minor units)
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=15000,
        currency="INR",
        idempotency_key="idemp_under_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # Booking issued voucher despite underpayment
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=200.0,
            amount_minor=20000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id="SUP-UNDER-001",
            voucher_id=f"VOUCHER-{booking_id}",
            correlation_id="corr-under-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "is below booking payable amount (20000 minor units)" in result.actual


def test_negative_payment_currency_differs_from_booking_currency(clean_dbs, inv_engine):
    """Negative Test 4: Payment currency differs from booking currency."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-CURR-MISMATCH-001"
    intent_id = "INTENT-CURR-001"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_id, agency_id)

    # Supplier confirmed
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-CURR-001",
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Hold captured in USD instead of INR
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="USD",
        idempotency_key="idemp_curr_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id="SUP-CURR-001",
            voucher_id=f"VOUCHER-{booking_id}",
            correlation_id="corr-curr-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "differs from booking currency 'INR'" in result.actual


def test_negative_voucher_missing_or_linked_to_another_booking(clean_dbs, inv_engine):
    """Negative Test 5: Voucher record missing or linked to another booking."""
    agg_db, sup_db, _ = clean_dbs
    agency_id = "agency_alpha"

    # Part A: VOUCHER_ISSUED status with None voucher_id
    booking_a = "BK-V-MISSING-001"
    intent_a = "INTENT-V-MISSING"
    _create_intent(agg_db, intent_a, agency_id)

    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-VM-001",
            intent_id=intent_a,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )
    hold_a = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_a,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_vm_01",
    )
    agg_db.capture_wallet_hold(hold_a.hold_id)

    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_a,
            intent_id=intent_a,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold_a.hold_id,
            primary_supplier_reservation_id="SUP-VM-001",
            voucher_id=None,  # Missing voucher record!
            correlation_id="corr-vm-1",
        )
    )

    res_a = inv_engine.check_voucher_entitlement(booking_a)
    assert res_a.passed is False
    assert "Voucher record is missing" in res_a.actual

    # Part B: Voucher linked to another existing booking
    booking_b1 = "BK-V-LINK-001"
    booking_b2 = "BK-V-LINK-002"
    intent_b1 = "INTENT-VB-001"
    intent_b2 = "INTENT-VB-002"
    _create_intent(agg_db, intent_b1, agency_id)
    _create_intent(agg_db, intent_b2, agency_id)

    # Save booking 2 as legitimate existing booking with voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_b2,
            intent_id=intent_b2,
            agency_id=agency_id,
            status=TransactionStatus.CREATED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            voucher_id=f"VOUCHER-{booking_b2}",
            correlation_id="corr-vb-2",
        )
    )
    agg_db.save_voucher(
        VoucherRecord(
            voucher_id=f"VOUCHER-{booking_b2}",
            booking_id=booking_b2,
            agency_id=agency_id,
            status=VoucherStatus.ISSUED,
            issued_at=datetime.utcnow(),
        )
    )

    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-VB-001",
            intent_id=intent_b1,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )
    hold_b1 = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_b1,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_vb_01",
    )
    agg_db.capture_wallet_hold(hold_b1.hold_id)

    # Booking 1 uses Booking 2's voucher
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_b1,
            intent_id=intent_b1,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold_b1.hold_id,
            primary_supplier_reservation_id="SUP-VB-001",
            voucher_id=f"VOUCHER-{booking_b2}",  # Linked to booking 2!
            correlation_id="corr-vb-1",
        )
    )

    res_b = inv_engine.check_voucher_entitlement(booking_b1)
    assert res_b.passed is False
    assert f"belongs to booking '{booking_b2}'" in res_b.actual


def test_negative_supplier_reservation_belongs_to_different_intent(clean_dbs, inv_engine):
    """Negative Test 6: Supplier reservation belongs to a different intent."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-INTENT-DIFF-001"
    intent_booking = "INTENT-CORRECT-001"
    intent_mismatched = "INTENT-FOREIGN-999"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_booking, agency_id)

    # Supplier reservation points to FOREIGN intent
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-FOREIGN-999",
            intent_id=intent_mismatched,  # Different intent!
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_foreign_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_booking,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id="SUP-FOREIGN-999",
            voucher_id=f"VOUCHER-{booking_id}",
            correlation_id="corr-foreign-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert f"belongs to different intent '{intent_mismatched}'" in result.actual


def test_negative_payment_captured_but_booking_own_obligation_unsatisfied(clean_dbs, inv_engine):
    """Negative Test 7: Wallet/card payment captured but booking's own obligation is not satisfied."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-OWN-UNSAT-001"
    intent_id = "INTENT-UNSAT-001"
    agency_id = "agency_alpha"

    _create_intent(agg_db, intent_id, agency_id)

    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-UNSAT-001",
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Booking hold is created, but remained in HELD (not CAPTURED!)
    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_unsat_01",
    )
    assert hold.status == WalletHoldStatus.HELD

    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id="SUP-UNSAT-001",
            voucher_id=f"VOUCHER-{booking_id}",
            correlation_id="corr-unsat-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Wallet hold is not captured (status: 'HELD')" in result.actual


# ---------------------------------------------------------
# Release Gate Task 5: Canonical Voucher Ground Truth Verification Tests
# ---------------------------------------------------------

def test_negative_voucher_id_not_found_in_vouchers_table(clean_dbs, inv_engine):
    """Negative Test 8: Voucher ID referenced by booking does not exist in vouchers table."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-V-NO-TABLE-001"
    intent_id = "INTENT-V-NOTBL-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-V-NOTBL-001"

    _create_intent(agg_db, intent_id, agency_id)

    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_v_notbl_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # Booking references voucher ID, but no voucher record was inserted into vouchers table
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id="VOUCHER-NONEXISTENT-999",
            correlation_id="corr-notbl-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert "Voucher ID 'VOUCHER-NONEXISTENT-999' does not exist in vouchers table" in result.actual


def test_negative_voucher_not_in_issued_state(clean_dbs, inv_engine):
    """Negative Test 9: Voucher record exists in table but status is CANCELLED/REVOKED."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-V-REVOKED-001"
    intent_id = "INTENT-V-REV-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-V-REV-001"
    voucher_id = f"VOUCHER-{booking_id}"

    _create_intent(agg_db, intent_id, agency_id)

    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_v_rev_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # 1. Save booking record first (satisfying foreign key)
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id=voucher_id,
            correlation_id="corr-rev-1",
        )
    )

    # 2. Voucher is in CANCELLED state in vouchers table
    agg_db.save_voucher(
        VoucherRecord(
            voucher_id=voucher_id,
            booking_id=booking_id,
            agency_id=agency_id,
            status=VoucherStatus.CANCELLED,
            issued_at=datetime.utcnow(),
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert f"Voucher record '{voucher_id}' is not in ISSUED state (status: 'CANCELLED')" in result.actual


def test_negative_voucher_belongs_to_different_booking(clean_dbs, inv_engine):
    """Negative Test 10: Voucher in table belongs to different booking ID."""
    agg_db, sup_db, _ = clean_dbs
    booking_id = "BK-V-DIFF-BK-001"
    other_booking_id = "BK-OTHER-REAL-888"
    intent_id = "INTENT-V-DIFFBK-001"
    agency_id = "agency_alpha"
    sup_res_id = "SUP-V-DIFFBK-001"
    voucher_id = "VOUCHER-ASSIGNED-TO-OTHER"

    _create_intent(agg_db, intent_id, agency_id)

    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id=sup_res_id,
            intent_id=intent_id,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    hold = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_id,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_v_diffbk_01",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    # Save other_booking_id first to satisfy foreign key
    _create_intent(agg_db, "INTENT-OTHER-888", agency_id)
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=other_booking_id,
            intent_id="INTENT-OTHER-888",
            agency_id=agency_id,
            status=TransactionStatus.CREATED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            correlation_id="corr-other-888",
        )
    )

    # Voucher record belongs to other_booking_id
    agg_db.save_voucher(
        VoucherRecord(
            voucher_id=voucher_id,
            booking_id=other_booking_id,
            agency_id=agency_id,
            status=VoucherStatus.ISSUED,
            issued_at=datetime.utcnow(),
        )
    )

    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold.hold_id,
            primary_supplier_reservation_id=sup_res_id,
            voucher_id=voucher_id,
            correlation_id="corr-diffbk-1",
        )
    )

    result = inv_engine.check_voucher_entitlement(booking_id)
    assert result.passed is False
    assert f"belongs to booking '{other_booking_id}', not '{booking_id}'" in result.actual


def test_negative_voucher_id_reused_across_multiple_bookings(clean_dbs, inv_engine):
    """Negative Test 11: Single voucher ID reused across multiple distinct bookings."""
    agg_db, sup_db, _ = clean_dbs
    booking_1 = "BK-V-REUSE-001"
    booking_2 = "BK-V-REUSE-002"
    intent_1 = "INTENT-V-REUSE-001"
    intent_2 = "INTENT-V-REUSE-002"
    agency_id = "agency_alpha"
    shared_voucher_id = "VOUCHER-SHARED-REUSE-001"

    _create_intent(agg_db, intent_1, agency_id)
    _create_intent(agg_db, intent_2, agency_id)

    # Supplier reservations for both
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-REUSE-001",
            intent_id=intent_1,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest 1",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )
    sup_db.save_reservation(
        SupplierReservation(
            supplier_reservation_id="SUP-REUSE-002",
            intent_id=intent_2,
            hotel_id="hotel_1",
            room_type="deluxe",
            guest_name="Guest 2",
            amount=100.0,
            currency="INR",
            status="CONFIRMED",
        )
    )

    # Holds for both
    hold_1 = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_1,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_reuse_01",
    )
    agg_db.capture_wallet_hold(hold_1.hold_id)

    hold_2 = agg_db.create_wallet_hold_fixed(
        agency_id=agency_id,
        booking_id=booking_2,
        amount_minor=10000,
        currency="INR",
        idempotency_key="idemp_reuse_02",
    )
    agg_db.capture_wallet_hold(hold_2.hold_id)

    # Save booking 1 first to satisfy foreign key
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_1,
            intent_id=intent_1,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold_1.hold_id,
            primary_supplier_reservation_id="SUP-REUSE-001",
            voucher_id=shared_voucher_id,
            correlation_id="corr-reuse-1",
        )
    )

    # Voucher in table points to booking 1
    agg_db.save_voucher(
        VoucherRecord(
            voucher_id=shared_voucher_id,
            booking_id=booking_1,
            agency_id=agency_id,
            status=VoucherStatus.ISSUED,
            issued_at=datetime.utcnow(),
        )
    )

    # Booking 2 also points to shared_voucher_id
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_2,
            intent_id=intent_2,
            agency_id=agency_id,
            status=TransactionStatus.VOUCHER_ISSUED,
            amount=100.0,
            amount_minor=10000,
            currency="INR",
            payment_method="WALLET",
            wallet_hold_id=hold_2.hold_id,
            primary_supplier_reservation_id="SUP-REUSE-002",
            voucher_id=shared_voucher_id,
            correlation_id="corr-reuse-2",
        )
    )

    # Checking booking_1 will detect that shared_voucher_id is reused across both bookings
    result_1 = inv_engine.check_voucher_entitlement(booking_1)
    assert result_1.passed is False
    assert "is reused across multiple bookings" in result_1.actual
    assert booking_2 in result_1.actual

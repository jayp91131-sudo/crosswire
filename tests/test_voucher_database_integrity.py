"""
Tests for Voucher Database Integrity and Foreign Key Enforcement.

Verifies:
1. PRAGMA foreign_keys = ON is strictly active on all aggregator SQLite connections.
2. Voucher persistence fails with sqlite3.IntegrityError if booking_id does not exist in booking_records.
3. Voucher persistence fails with sqlite3.IntegrityError if agency_id does not exist in agencies.
4. Existing vouchers referencing mismatching/corrupted tenant or booking linkage are caught by InvariantEngine.
5. InvariantEngine checks physical SQLite ground truth for issued vouchers.
"""

import sqlite3
import pytest
from datetime import datetime, timezone

from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from target_platform.models import (
    BookingIntent,
    VoucherRecord,
    VoucherStatus,
    AggregatorBookingRecord,
    TransactionStatus,
    PaymentMethod,
)
from engine.invariant_engine import get_invariant_engine


@pytest.fixture(autouse=True)
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


def test_voucher_foreign_key_on_nonexistent_booking():
    agg_db = get_aggregator_db()

    # agency_alpha is automatically seeded in agencies table during init_db()
    # Attempt to insert a voucher with a nonexistent booking_id
    voucher = VoucherRecord(
        voucher_id="vouch_orphan_booking",
        booking_id="nonexistent_booking_id",
        agency_id="agency_alpha",
        status=VoucherStatus.ISSUED,
        issued_at=datetime.now(timezone.utc),
    )

    with pytest.raises(sqlite3.IntegrityError) as exc_info:
        agg_db.save_voucher(voucher)

    assert "FOREIGN KEY constraint failed" in str(exc_info.value)


def test_voucher_foreign_key_on_nonexistent_agency():
    agg_db = get_aggregator_db()

    # Save a valid intent and booking for agency_alpha
    intent_id = "intent_vfk_valid"
    booking_id = "book_vfk_valid"
    agg_db.save_booking_intent(
        BookingIntent(
            intent_id=intent_id,
            agency_id="agency_alpha",
            hotel_id="htl_001",
            hotel_name="Grand 1",
            room_type="standard",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=50.0,
            amount_minor=5000,
            currency="USD",
            guest_name="FK Guest",
            payment_method="WALLET",
        )
    )
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id="agency_alpha",
            currency="USD",
            amount=50.0,
            amount_minor=5000,
            status=TransactionStatus.SUPPLIER_CONFIRMED,
            payment_method=PaymentMethod.WALLET,
            supplier_id="mock_supp",
            supplier_reservation_id="supp_res_123",
            correlation_id="corr_vfk_1",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )

    # Attempt to insert a voucher referencing the valid booking but a nonexistent agency
    voucher = VoucherRecord(
        voucher_id="vouch_orphan_agency",
        booking_id=booking_id,
        agency_id="nonexistent_agency_id",
        status=VoucherStatus.ISSUED,
        issued_at=datetime.now(timezone.utc),
    )

    with pytest.raises(sqlite3.IntegrityError) as exc_info:
        agg_db.save_voucher(voucher)

    assert "FOREIGN KEY constraint failed" in str(exc_info.value)


def test_invariant_engine_catches_voucher_cross_tenant_linkage():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    intent_id = "intent_alpha_vfk"
    booking_id = "book_alpha_vfk"
    # agency_alpha and agency_beta exist in agencies table
    agg_db.save_booking_intent(
        BookingIntent(
            intent_id=intent_id,
            agency_id="agency_alpha",
            hotel_id="htl_001",
            hotel_name="Grand 1",
            room_type="standard",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=50.0,
            amount_minor=5000,
            currency="USD",
            guest_name="Alpha Guest",
            payment_method="WALLET",
        )
    )
    # Save Alpha booking
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id="agency_alpha",
            currency="USD",
            amount=50.0,
            amount_minor=5000,
            status=TransactionStatus.VOUCHER_ISSUED,
            payment_method=PaymentMethod.WALLET,
            supplier_id="mock_supp",
            supplier_reservation_id="supp_res_alpha",
            voucher_id="vouch_mismatch",
            correlation_id="corr_vfk_2",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )

    # Voucher references book_alpha_vfk, but agency_beta (both exist in DB, so FK passes)
    voucher = VoucherRecord(
        voucher_id="vouch_mismatch",
        booking_id=booking_id,
        agency_id="agency_beta",  # Tenant mismatch!
        status=VoucherStatus.ISSUED,
        issued_at=datetime.now(timezone.utc),
    )
    agg_db.save_voucher(voucher)

    # Invariant 5.4 check on booking must catch that voucher tenant != booking agency
    res = engine.check_voucher_entitlement(booking_id)
    assert res.passed is False
    assert "belongs to agency 'agency_beta', not 'agency_alpha'" in res.evidence.get("details", "")


def test_invariant_engine_catches_voucher_missing_physical_record():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    intent_id = "intent_ghost_vfk"
    booking_id = "book_ghost_vfk"
    agg_db.save_booking_intent(
        BookingIntent(
            intent_id=intent_id,
            agency_id="agency_alpha",
            hotel_id="htl_001",
            hotel_name="Grand 1",
            room_type="standard",
            check_in="2026-12-01",
            check_out="2026-12-05",
            amount=50.0,
            amount_minor=5000,
            currency="USD",
            guest_name="Ghost Guest",
            payment_method="WALLET",
        )
    )
    # Booking claims voucher_id = "vouch_ghost", but voucher was never saved
    agg_db.save_booking_record(
        AggregatorBookingRecord(
            booking_id=booking_id,
            intent_id=intent_id,
            agency_id="agency_alpha",
            currency="USD",
            amount=50.0,
            amount_minor=5000,
            status=TransactionStatus.VOUCHER_ISSUED,
            payment_method=PaymentMethod.WALLET,
            supplier_id="mock_supp",
            supplier_reservation_id="supp_res_ghost",
            voucher_id="vouch_ghost",
            correlation_id="corr_vfk_3",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )

    res = engine.check_voucher_entitlement(booking_id)
    assert res.passed is False
    assert "does not exist in vouchers table" in res.evidence.get("details", "")

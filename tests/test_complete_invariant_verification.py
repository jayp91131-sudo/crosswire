"""
Milestone 2.2 Final Recovery Hardening — Complete Invariant Verification Suite.
Validates both positive (healthy) and negative (controlled synthetic violation)
scenarios for all 10 Milestone 2.2 invariants:
1. 6.2 WALLET_HOLDS_BOUNDED
2. 6.3 WALLET_TOTAL_ACCOUNTING
3. 6.5 INVENTORY_NON_NEGATIVE
4. 6.7 SAGA_STATE_CONSISTENCY
5. 6.8 CROSS_AGENCY_WALLET_ISOLATION
6. 6.9 WALLET_HOLD_EXCLUSIVITY
7. 6.10 WALLET_REFUND_CONSERVATION
8. 6.11 INVENTORY_ALLOCATION_CONSERVATION
9. 6.12 CANCELLATION_INVENTORY_CONSERVATION
10. 6.13 AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION
"""

import pytest
import sqlite3
from datetime import datetime

from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from engine.invariant_engine import get_invariant_engine
from target_platform.models import (
    WalletHoldStatus,
    TransactionStatus,
    SupplierReservation,
    AggregatorBookingRecord,
    BookingIntent,
)


@pytest.fixture(autouse=True)
def setup_teardown():
    agg_db = get_aggregator_db()
    sup_db = get_supplier_db()
    agg_db.reset()
    sup_db.reset()

    yield

    agg_db.reset()
    sup_db.reset()


# =============================================================================
# 1. Invariant 6.2: WALLET_HOLDS_BOUNDED
# =============================================================================
def test_invariant_6_2_wallet_holds_bounded_healthy():
    engine = get_invariant_engine()
    res = engine.check_wallet_holds_bounded("agency_alpha")
    assert res.passed is True


def test_invariant_6_2_wallet_holds_bounded_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    # Artificially set active_holds_minor greater than funded_balance_minor
    with sqlite3.connect(agg_db.db_path) as conn:
        conn.execute(
            "UPDATE agency_wallets SET active_holds_minor = 20000000, funded_balance_minor = 10000000 WHERE agency_id = 'agency_alpha'"
        )

    res = engine.check_wallet_holds_bounded("agency_alpha")
    assert res.passed is False
    assert "Wallet Holds Exceed Funded Balance" in res.title


# =============================================================================
# 2. Invariant 6.3: WALLET_TOTAL_ACCOUNTING
# =============================================================================
def test_invariant_6_3_wallet_total_accounting_healthy():
    engine = get_invariant_engine()
    res = engine.check_wallet_total_accounting("agency_alpha")
    assert res.passed is True


def test_invariant_6_3_wallet_total_accounting_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    # Corrupt total_captured_minor to deviate from ledger sum
    with sqlite3.connect(agg_db.db_path) as conn:
        conn.execute(
            "UPDATE agency_wallets SET total_captured_minor = 999999 WHERE agency_id = 'agency_alpha'"
        )

    res = engine.check_wallet_total_accounting("agency_alpha")
    assert res.passed is False
    assert "Wallet Transaction History Discrepancy" in res.title


# =============================================================================
# 3. Invariant 6.5: INVENTORY_NON_NEGATIVE
# =============================================================================
def test_invariant_6_5_inventory_non_negative_healthy():
    engine = get_invariant_engine()
    res = engine.check_inventory_non_negative("htl_mumbai_grand", "standard_room")
    assert res.passed is True


def test_invariant_6_5_inventory_non_negative_violation():
    sup_db = get_supplier_db()
    engine = get_invariant_engine()

    # Corrupt inventory available_units below zero
    with sqlite3.connect(sup_db.db_path) as conn:
        conn.execute(
            "UPDATE hotel_inventory SET available_units = -2 WHERE hotel_id = 'htl_mumbai_grand' AND room_type = 'standard_room'"
        )

    res = engine.check_inventory_non_negative("htl_mumbai_grand", "standard_room")
    assert res.passed is False
    assert "Negative Inventory Detected" in res.title


# =============================================================================
# 4. Invariant 6.7: SAGA_STATE_CONSISTENCY
# =============================================================================
def test_invariant_6_7_saga_state_consistency_healthy():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    intent_id = "intent_saga_healthy_01"
    booking_id = f"TBOH-{intent_id}"

    # Create intent & hold
    intent = BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Healthy Guest",
        correlation_id="corr_healthy_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_intent(intent)

    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id=booking_id,
        amount_minor=500000,
        currency="INR",
        idempotency_key="hold_saga_healthy",
    )
    agg_db.capture_wallet_hold(hold.hold_id)

    record = AggregatorBookingRecord(
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.SUPPLIER_CONFIRMED,
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        cancellation_penalty=0.0,
        cancellation_penalty_minor=0,
        refundable_entitlement=5000.0,
        refundable_entitlement_minor=500000,
        total_refunded=0.0,
        total_refunded_minor=0,
        wallet_hold_id=hold.hold_id,
        correlation_id="corr_healthy_01",
        payment_method="WALLET",
        primary_supplier_reservation_id="SUP-test-01",
    )
    agg_db.save_booking_record(record)

    res = engine.check_saga_state_consistency(booking_id)
    assert res.passed is True


def test_invariant_6_7_saga_state_consistency_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    intent_id = "intent_saga_viol_01"
    booking_id = f"TBOH-{intent_id}"

    intent = BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Viol Guest",
        correlation_id="corr_viol_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_intent(intent)

    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id=booking_id,
        amount_minor=500000,
        currency="INR",
        idempotency_key="hold_saga_viol",
    )
    # Leave hold in HELD, but mark booking as FAILED (violation: hold was not released)
    record = AggregatorBookingRecord(
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.FAILED,
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        cancellation_penalty=0.0,
        cancellation_penalty_minor=0,
        refundable_entitlement=0.0,
        refundable_entitlement_minor=0,
        total_refunded=0.0,
        total_refunded_minor=0,
        wallet_hold_id=hold.hold_id,
        correlation_id="corr_viol_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_record(record)

    res = engine.check_saga_state_consistency(booking_id)
    assert res.passed is False
    assert "Distributed Saga Compensation Failure" in res.title


# =============================================================================
# 5. Invariant 6.8: CROSS_AGENCY_WALLET_ISOLATION
# =============================================================================
def test_invariant_6_8_cross_agency_wallet_isolation_healthy():
    engine = get_invariant_engine()
    res = engine.check_cross_agency_wallet_isolation("agency_alpha", "agency_beta")
    assert res.passed is True


def test_invariant_6_8_cross_agency_wallet_isolation_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    # Inject cross-agency hold: agency_alpha holds funds from agency_beta's wallet
    with sqlite3.connect(agg_db.db_path) as conn:
        conn.execute(
            """
            INSERT INTO wallet_holds (
                hold_id, wallet_id, agency_id, booking_id, amount, amount_minor, currency, status,
                idempotency_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "hold_cross_attack",
                "wallet_agency_beta",  # Targets Beta!
                "agency_alpha",         # Claimed by Alpha!
                "TBOH-cross-attack",
                5000.0,
                500000,
                "INR",
                "HELD",
                "idemp_cross_attack",
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat(),
            ),
        )

    res = engine.check_cross_agency_wallet_isolation("agency_alpha", "agency_beta")
    assert res.passed is False
    assert "Cross-Agency Wallet Isolation Violation" in res.title


# =============================================================================
# 6. Invariant 6.9: WALLET_HOLD_EXCLUSIVITY
# =============================================================================
def test_invariant_6_9_wallet_hold_exclusivity_healthy():
    engine = get_invariant_engine()
    res = engine.check_wallet_hold_exclusivity("agency_alpha")
    assert res.passed is True


def test_invariant_6_9_wallet_hold_exclusivity_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    # Create a hold and record BOTH CAPTURE and RELEASE transactions for it
    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id="TBOH-excl-viol",
        amount_minor=100000,
        currency="INR",
        idempotency_key="hold_excl_viol",
    )
    with sqlite3.connect(agg_db.db_path) as conn:
        conn.execute(
            """
            INSERT INTO wallet_transactions (
                transaction_id, wallet_id, agency_id, booking_id, hold_id, transaction_type,
                amount, amount_minor, currency, balance_after_minor, holds_after_minor, reference, description, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("tx_cap_01", "wallet_agency_alpha", "agency_alpha", "TBOH-excl", hold.hold_id, "CAPTURE", 1000.0, 100000, "INR", 9900000, 0, "TBOH-excl", "Capture", datetime.utcnow().isoformat()),
        )
        conn.execute(
            """
            INSERT INTO wallet_transactions (
                transaction_id, wallet_id, agency_id, booking_id, hold_id, transaction_type,
                amount, amount_minor, currency, balance_after_minor, holds_after_minor, reference, description, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("tx_rel_01", "wallet_agency_alpha", "agency_alpha", "TBOH-excl", hold.hold_id, "RELEASE", 1000.0, 100000, "INR", 10000000, 0, "TBOH-excl", "Release", datetime.utcnow().isoformat()),
        )

    res = engine.check_wallet_hold_exclusivity("agency_alpha")
    assert res.passed is False
    assert "Wallet Hold Exclusivity Violation" in res.title


# =============================================================================
# 7. Invariant 6.10: WALLET_REFUND_CONSERVATION
# =============================================================================
def test_invariant_6_10_wallet_refund_conservation_healthy():
    engine = get_invariant_engine()
    res = engine.check_wallet_refund_conservation("agency_alpha")
    assert res.passed is True


def test_invariant_6_10_wallet_refund_conservation_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    # Over-refund: total_refunded_minor exceeds total_captured_minor
    with sqlite3.connect(agg_db.db_path) as conn:
        conn.execute(
            "UPDATE agency_wallets SET total_captured_minor = 100000, total_refunded_minor = 200000 WHERE agency_id = 'agency_alpha'"
        )

    res = engine.check_wallet_refund_conservation("agency_alpha")
    assert res.passed is False
    assert "Wallet Refund Conservation Violation" in res.title


# =============================================================================
# 8. Invariant 6.11: INVENTORY_ALLOCATION_CONSERVATION
# =============================================================================
def test_invariant_6_11_inventory_allocation_conservation_healthy():
    engine = get_invariant_engine()
    res = engine.check_inventory_allocation_conservation("htl_mumbai_grand", "standard_room")
    assert res.passed is True


def test_invariant_6_11_inventory_allocation_conservation_violation():
    sup_db = get_supplier_db()
    engine = get_invariant_engine()

    # Inject an active reservation without updating inventory capacity/available units
    res = SupplierReservation(
        supplier_reservation_id="SUP-ghost-01",
        intent_id="intent_ghost_01",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        guest_name="Ghost Guest",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        idempotency_key="idemp_ghost_01",
        payload_hash="dummy_hash",
        status="CONFIRMED",
        created_at=datetime.utcnow(),
    )
    sup_db.save_reservation(res)

    res_check = engine.check_inventory_allocation_conservation("htl_mumbai_grand", "standard_room")
    assert res_check.passed is False
    assert "Inventory Allocation Conservation Discrepancy" in res_check.title


# =============================================================================
# 9. Invariant 6.12: CANCELLATION_INVENTORY_CONSERVATION
# =============================================================================
def test_invariant_6_12_cancellation_inventory_conservation_healthy():
    engine = get_invariant_engine()
    res = engine.check_cancellation_inventory_conservation("htl_mumbai_grand", "standard_room")
    assert res.passed is True


def test_invariant_6_12_cancellation_inventory_conservation_violation():
    sup_db = get_supplier_db()
    engine = get_invariant_engine()

    # Corrupt available_units so available != capacity - active_res
    with sqlite3.connect(sup_db.db_path) as conn:
        conn.execute(
            "UPDATE hotel_inventory SET available_units = 1 WHERE hotel_id = 'htl_mumbai_grand' AND room_type = 'standard_room'"
        )

    res = engine.check_cancellation_inventory_conservation("htl_mumbai_grand", "standard_room")
    assert res.passed is False
    assert "Cancellation Inventory Restoration Discrepancy" in res.title


# =============================================================================
# 10. Invariant 6.13: AMBIGUOUS_TIMEOUT_HOLD_PRESERVATION
# =============================================================================
def test_invariant_6_13_ambiguous_timeout_hold_preservation_healthy():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    intent_id = "intent_ambig_healthy_01"
    booking_id = f"TBOH-{intent_id}"

    intent = BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Ambig Guest",
        correlation_id="corr_ambig_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_intent(intent)

    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id=booking_id,
        amount_minor=500000,
        currency="INR",
        idempotency_key="hold_ambig_healthy",
    )
    # Hold is HELD, booking is SUPPLIER_UNKNOWN -> preserves hold safely
    record = AggregatorBookingRecord(
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.SUPPLIER_UNKNOWN,
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        cancellation_penalty=0.0,
        cancellation_penalty_minor=0,
        refundable_entitlement=5000.0,
        refundable_entitlement_minor=500000,
        total_refunded=0.0,
        total_refunded_minor=0,
        wallet_hold_id=hold.hold_id,
        correlation_id="corr_ambig_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_record(record)

    res = engine.check_ambiguous_timeout_hold_preservation(booking_id)
    assert res.passed is True


def test_invariant_6_13_ambiguous_timeout_hold_preservation_violation():
    agg_db = get_aggregator_db()
    engine = get_invariant_engine()

    intent_id = "intent_ambig_viol_01"
    booking_id = f"TBOH-{intent_id}"

    intent = BookingIntent(
        intent_id=intent_id,
        agency_id="agency_alpha",
        hotel_id="htl_mumbai_grand",
        room_type="standard_room",
        check_in="2026-12-01",
        check_out="2026-12-05",
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        guest_name="Ambig Viol Guest",
        correlation_id="corr_ambig_viol_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_intent(intent)

    hold = agg_db.create_wallet_hold_fixed(
        agency_id="agency_alpha",
        booking_id=booking_id,
        amount_minor=500000,
        currency="INR",
        idempotency_key="hold_ambig_viol",
    )
    # Prematurely release hold while booking is SUPPLIER_UNKNOWN
    agg_db.release_wallet_hold(hold.hold_id)

    record = AggregatorBookingRecord(
        booking_id=booking_id,
        intent_id=intent_id,
        agency_id="agency_alpha",
        status=TransactionStatus.SUPPLIER_UNKNOWN,
        amount=5000.0,
        amount_minor=500000,
        currency="INR",
        cancellation_penalty=0.0,
        cancellation_penalty_minor=0,
        refundable_entitlement=5000.0,
        refundable_entitlement_minor=500000,
        total_refunded=0.0,
        total_refunded_minor=0,
        wallet_hold_id=hold.hold_id,
        correlation_id="corr_ambig_viol_01",
        payment_method="WALLET",
    )
    agg_db.save_booking_record(record)

    res = engine.check_ambiguous_timeout_hold_preservation(booking_id)
    assert res.passed is False
    assert "Ambiguous Timeout Hold Prematurely Mutated" in res.title

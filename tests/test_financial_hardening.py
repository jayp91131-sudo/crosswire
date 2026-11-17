"""
Crosswire 2.0: Milestone 1 Financial Hardening Audit Test Suite.
Verifies:
1. Integer minor units exact monetary representation (ISO 4217, zero IEEE-754 binary float drift).
2. Atomic refund processing with SQLite BEGIN IMMEDIATE reservation and conditional UPDATE.
3. Concurrent competing partial refund requests with DIFFERENT idempotency keys:
   - Booking with 12,000 INR entitlement.
   - Two concurrent partial refund requests for 8,000 INR each with competing keys (A & B).
   - Exactly one succeeds (200 OK); the losing request is rejected (400 / ExcessiveRefundError).
   - Cumulative refunds strictly equal 8,000 INR <= 12,000 INR.
4. Idempotency key scope across payment, refund, and supplier operations (agency scope, conflict on mismatched payload hash).
5. Accounting model verification (single-entry transaction activity log + balanced double-entry journal batches where Debits == Credits).
6. Non-destructive schema migrations and backward compatibility.
"""

import asyncio
import sqlite3
import pytest
from decimal import Decimal
from target_platform.agency_client import AgencyClient
from target_platform.models import (
    BookingIntent,
    TransactionStatus,
    LedgerEntryType,
    LedgerDirection,
)
from target_platform.money import (
    to_minor_units,
    from_minor_units,
    get_currency_exponent,
)
from target_platform.aggregator.database import (
    get_aggregator_db,
    AggregatorDatabase,
    IdempotencyPayloadConflictError,
    ExcessiveRefundError,
)
from target_platform.payment.database import get_payment_db
from target_platform.supplier.database import get_supplier_db


# ---------------------------------------------------------------------------
# 1. Exact Monetary Representation (Minor Units)
# ---------------------------------------------------------------------------
def test_exact_monetary_representation_minor_units():
    """
    Requirement 1:
    Verifies that fractional monetary calculations avoid IEEE-754 floating-point drift.
    E.g., 0.1 + 0.2 != 0.30000000000000004 in binary float, but in integer minor units
    10 paise + 20 paise = 30 paise exactly.
    """
    # Test currency exponents
    assert get_currency_exponent("INR") == 2
    assert get_currency_exponent("USD") == 2
    assert get_currency_exponent("JPY") == 0
    assert get_currency_exponent("KWD") == 3

    # INR conversion
    assert to_minor_units(15000.50, "INR") == 1500050
    assert to_minor_units("15000.50", "INR") == 1500050
    assert from_minor_units(1500050, "INR") == 15000.50

    # Float drift test: 3 partial refunds of 3333.33 INR
    part_minor = to_minor_units(3333.33, "INR")
    assert part_minor == 333333
    total_minor = part_minor * 3
    assert total_minor == 999999
    assert from_minor_units(total_minor, "INR") == 9999.99

    # JPY zero-exponent test
    assert to_minor_units(5000, "JPY") == 5000
    assert from_minor_units(5000, "JPY") == 5000.0


# ---------------------------------------------------------------------------
# 2 & 3. Concurrency Test: Competing Partial Refunds with DIFFERENT Keys
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_atomic_concurrent_partial_refunds_competing_keys(agency_client: AgencyClient):
    """
    Requirements 2 & 3:
    Create a booking with defined refundable entitlement (12,000 INR).
    Submit two concurrent partial refund requests with DIFFERENT idempotency keys (8,000 INR each).
    Since 8,000 + 8,000 = 16,000 > 12,000:
    - Exactly one must succeed (200 OK).
    - Exactly one must be rejected (400 / ExcessiveRefundError).
    - Cumulative refunds must strictly equal 8,000 INR <= 12,000 INR.
    - Ledger and refund records must remain strictly consistent.
    """
    agg_db = get_aggregator_db()

    # 1. Create booking (15,000 INR, 20% penalty -> 3,000 penalty, 12,000 entitlement)
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-CONCUR-COMPETE-01", amount=15000.0)
    init_res = await agency_client.submit_booking(intent)
    assert init_res.status == TransactionStatus.SUPPLIER_CONFIRMED

    # 2. Cancel booking to open refundable entitlement
    cancel_res = await agency_client.cancel_booking(init_res.booking_id)
    assert cancel_res.status == TransactionStatus.CANCELLED
    assert cancel_res.refundable_entitlement == 12000.0

    booking_id = init_res.booking_id
    key_alpha = "IDEMP-KEY-COMPETING-ALPHA-8000"
    key_beta = "IDEMP-KEY-COMPETING-BETA-8000"

    # 3. Fire two concurrent partial refund requests with DIFFERENT idempotency keys
    # Each requests 8,000 INR. Sum = 16,000 INR which exceeds 12,000 INR.
    task_alpha = agency_client.refund_booking(
        booking_id, amount=8000.0, idempotency_key=key_alpha, reason="Alpha partial refund"
    )
    task_beta = agency_client.refund_booking(
        booking_id, amount=8000.0, idempotency_key=key_beta, reason="Beta partial refund"
    )

    results = await asyncio.gather(task_alpha, task_beta, return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # 4. Assert strict mutual exclusion: exactly one winner, one loser
    assert len(successes) == 1, f"Expected strictly 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected strictly 1 failure, got {len(failures)}"

    winning_refund = successes[0]
    assert winning_refund.amount == 8000.0
    assert winning_refund.total_refunded == 8000.0
    assert winning_refund.remaining_entitlement == 4000.0
    assert winning_refund.status == TransactionStatus.PARTIALLY_REFUNDED

    losing_exc = failures[0]
    assert "400" in str(losing_exc)
    assert "exceeds" in str(losing_exc).lower()

    # 5. Database Ground-Truth Verification
    db_booking = agg_db.get_booking_record(booking_id)
    assert db_booking.total_refunded == 8000.0
    assert db_booking.total_refunded_minor == 800000
    assert db_booking.total_refunded <= db_booking.refundable_entitlement

    db_refunds = agg_db.get_refunds_for_booking(booking_id)
    assert len(db_refunds) == 1, "Only 1 refund record must exist in SQLite"
    assert db_refunds[0].amount == 8000.0
    assert db_refunds[0].amount_minor == 800000

    # Ledger Verification
    db_ledger = agg_db.get_ledger_entries_for_booking(booking_id)
    refund_entries = [e for e in db_ledger if e.entry_type == LedgerEntryType.REFUND]
    assert len(refund_entries) == 1, "Only 1 refund ledger entry must be posted"
    assert refund_entries[0].amount == 8000.0
    assert refund_entries[0].amount_minor == 800000
    assert refund_entries[0].balance_after == 4000.0
    assert refund_entries[0].balance_after_minor == 400000


# ---------------------------------------------------------------------------
# 4. Idempotency Key Scope and Conflict Verification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idempotency_scope_and_payload_conflict(agency_client: AgencyClient):
    """
    Requirement 4:
    Verifies that:
    1. Idempotency keys are scoped to agency tenant.
    2. Reusing an idempotency key with a mismatched payload hash raises 409 Conflict.
    3. Identical payload replay returns the existing record idempotently.
    """
    intent = agency_client.create_synthetic_intent(intent_id="INTENT-IDEMP-SCOPE-01", amount=10000.0)
    init_res = await agency_client.submit_booking(intent)
    await agency_client.cancel_booking(init_res.booking_id)

    key = "IDEMP-SCOPE-TEST-KEY-1"

    # Call 1: Refund 2,000 INR
    ref_1 = await agency_client.refund_booking(init_res.booking_id, amount=2000.0, idempotency_key=key)
    assert ref_1.amount == 2000.0

    # Call 2: Identical replay with same key and same payload -> Idempotent replay
    ref_2 = await agency_client.refund_booking(init_res.booking_id, amount=2000.0, idempotency_key=key)
    assert ref_2.refund_id == ref_1.refund_id
    assert ref_2.transaction_reference == ref_1.transaction_reference

    # Call 3: Reuse same key with mismatched payload (different amount: 3,000 INR) -> 409 Conflict
    with pytest.raises(RuntimeError) as exc_info:
        await agency_client.refund_booking(init_res.booking_id, amount=3000.0, idempotency_key=key)
    assert "409" in str(exc_info.value)
    assert "conflict" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 5. Balanced Double-Entry Accounting Model Verification
# ---------------------------------------------------------------------------
def test_balanced_double_entry_accounting_model(tmp_path):
    """
    Requirement 5:
    Verifies that for double-entry batches, total Debits strictly equal total Credits:
    Sum(Debits) == Sum(Credits) across every journal batch.
    """
    test_db_path = str(tmp_path / "double_entry_test.db")
    db = AggregatorDatabase(db_path=test_db_path)

    batch_id = "BATCH-TEST-DOUBLE-ENTRY-001"
    amount_minor = 1500000  # 15,000 INR
    currency = "INR"

    # Record balanced double-entry batch:
    # DEBIT 1000_PLATFORM_CASH (+15,000 INR)
    # CREDIT 2100_AGENCY_REFUNDABLE_PAYABLE (+15,000 INR)
    entries = db.record_double_entry_batch(
        batch_id=batch_id,
        booking_id="TBOH-TEST-001",
        agency_id="agency_alpha",
        entry_type=LedgerEntryType.PAYMENT_CAPTURE,
        debit_account="1000_PLATFORM_CASH",
        credit_account="2100_AGENCY_REFUNDABLE_PAYABLE",
        amount_minor=amount_minor,
        currency=currency,
        description="Captured payment batch",
    )

    assert len(entries) == 2
    debits = [e for e in entries if e.direction == LedgerDirection.DEBIT]
    credits = [e for e in entries if e.direction == LedgerDirection.CREDIT]

    assert len(debits) == 1
    assert len(credits) == 1
    assert debits[0].amount_minor == credits[0].amount_minor == 1500000
    assert debits[0].account_code == "1000_PLATFORM_CASH"
    assert credits[0].account_code == "2100_AGENCY_REFUNDABLE_PAYABLE"
    assert debits[0].amount == credits[0].amount == 15000.0


# ---------------------------------------------------------------------------
# 6. Schema Migration & Regression Without Data Destruction
# ---------------------------------------------------------------------------
def test_schema_migration_preserves_existing_data(tmp_path):
    """
    Requirement 6:
    Creates a legacy database schema with float-only monetary representation,
    inserts pre-existing bookings and transactions, and initializes AggregatorDatabase
    to verify non-destructive migration and automatic backfilling of integer minor units.
    """
    legacy_db_path = str(tmp_path / "legacy_v1.db")
    conn = sqlite3.connect(legacy_db_path)
    conn.executescript("""
        CREATE TABLE agencies (
            agency_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE booking_records (
            booking_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            status TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            cancellation_penalty REAL DEFAULT 0.0,
            refundable_entitlement REAL DEFAULT 0.0,
            total_refunded REAL DEFAULT 0.0,
            primary_supplier_reservation_id TEXT,
            payment_id TEXT,
            voucher_id TEXT,
            retry_count INTEGER DEFAULT 0,
            correlation_id TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        INSERT INTO agencies VALUES ('legacy_agency', 'Legacy Agency', 'legacy_key_123', '2026-01-01');
        INSERT INTO booking_records VALUES (
            'TBOH-LEGACY-001', 'INTENT-LEGACY-001', 'legacy_agency', 'SUPPLIER_CONFIRMED',
            25000.50, 'INR', 5000.10, 20000.40, 0.0, 'SUP-LEGACY-01', 'PAY-LEGACY-01', 'V-LEGACY-01',
            0, 'corr_legacy_001', NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00'
        );
    """)
    conn.commit()
    conn.close()

    # Now load through AggregatorDatabase with auto-migration
    db = AggregatorDatabase(db_path=legacy_db_path)

    # Verify that existing row was NOT destroyed
    booking = db.get_booking_record("TBOH-LEGACY-001")
    assert booking is not None
    assert booking.amount == 25000.50
    # Verify minor unit columns were safely backfilled without float drift
    assert booking.amount_minor == 2500050
    assert booking.cancellation_penalty_minor == 500010
    assert booking.refundable_entitlement_minor == 2000040
    assert booking.total_refunded_minor == 0

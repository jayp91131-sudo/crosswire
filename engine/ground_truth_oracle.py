"""
Testbed Ground-Truth Oracle.
Represents the white-box testing harness perspective.
Inspects underlying SQLite databases directly to provide testbed ground truth.
Milestone 2.1: Extended with webhook, ledger, and refund verification methods.
"""

from typing import List, Optional
from target_platform.aggregator.database import get_aggregator_db
from target_platform.supplier.database import get_supplier_db
from target_platform.payment.database import get_payment_db
from target_platform.models import (
    SupplierReservation,
    AggregatorBookingRecord,
    WebhookEventRecord,
    LedgerEntry,
    RefundRecord,
    PaymentTransactionRecord,
    AgencyWalletRecord,
    WalletHoldRecord,
    WalletTransactionRecord,
    HotelInventoryRecord,
    VoucherRecord,
)


class TestbedGroundTruthOracle:
    def __init__(self):
        self.aggregator_db = get_aggregator_db()
        self.supplier_db = get_supplier_db()
        self.payment_db = get_payment_db()

    def get_supplier_reservations(self, intent_id: str) -> List[SupplierReservation]:
        return self.supplier_db.get_reservations_by_intent(intent_id)

    def get_supplier_reservation(self, supplier_reservation_id: str) -> Optional[SupplierReservation]:
        return self.supplier_db.get_reservation(supplier_reservation_id)

    def get_all_supplier_reservations(self) -> List[SupplierReservation]:
        return self.supplier_db.get_all_reservations()

    def get_hotel_inventory(self, hotel_id: str, room_type: str) -> Optional[HotelInventoryRecord]:
        return self.supplier_db.get_inventory(hotel_id, room_type)

    def get_wallet(self, agency_id: str) -> Optional[AgencyWalletRecord]:
        return self.aggregator_db.get_wallet(agency_id)

    def get_wallet_holds(self, agency_id: str) -> List[WalletHoldRecord]:
        return self.aggregator_db.get_wallet_holds(agency_id)

    def get_active_wallet_holds(self, agency_id: str) -> List[WalletHoldRecord]:
        return self.aggregator_db.get_active_wallet_holds(agency_id)

    def get_wallet_transactions(self, agency_id: str) -> List[WalletTransactionRecord]:
        return self.aggregator_db.get_wallet_transactions(agency_id)

    def get_aggregator_booking_by_intent(self, intent_id: str) -> Optional[AggregatorBookingRecord]:
        return self.aggregator_db.get_booking_by_intent(intent_id)

    def get_booking_record(self, booking_id: str) -> Optional[AggregatorBookingRecord]:
        return self.aggregator_db.get_booking_record(booking_id)

    def get_supplier_operations(self, booking_id: str) -> List[dict]:
        return self.aggregator_db.get_supplier_operations_for_booking(booking_id)

    def get_transaction_events(self, booking_id: str) -> List[dict]:
        return self.aggregator_db.get_events_for_booking(booking_id)

    def get_webhook_event(self, event_id: str, provider: Optional[str] = None) -> Optional[WebhookEventRecord]:
        return self.aggregator_db.get_webhook_event(event_id, provider=provider)

    def get_webhook_events_for_booking(self, booking_id: str) -> List[WebhookEventRecord]:
        return self.aggregator_db.get_webhook_events_for_booking(booking_id)

    def get_all_webhook_events(self) -> List[WebhookEventRecord]:
        return self.aggregator_db.get_all_webhook_events()

    def get_ledger_entries(self, booking_id: str) -> List[LedgerEntry]:
        return self.aggregator_db.get_ledger_entries_for_booking(booking_id)

    def get_ledger_entries_for_event(self, event_id: str, provider: Optional[str] = None) -> List[LedgerEntry]:
        return self.aggregator_db.get_ledger_entries_for_event(event_id, provider=provider)

    def get_refund_records(self, booking_id: str) -> List[RefundRecord]:
        return self.aggregator_db.get_refunds_for_booking(booking_id)

    def get_refunds_for_booking(self, booking_id: str) -> List[RefundRecord]:
        return self.aggregator_db.get_refunds_for_booking(booking_id)

    def get_payment_record(self, payment_id: str) -> Optional[PaymentTransactionRecord]:
        return self.payment_db.get_payment(payment_id)

    def get_voucher(self, voucher_id: str) -> Optional[VoucherRecord]:
        return self.aggregator_db.get_voucher(voucher_id)

    def get_vouchers_by_booking(self, booking_id: str) -> List[VoucherRecord]:
        return self.aggregator_db.get_vouchers_by_booking(booking_id)

    def get_bookings_by_voucher(self, voucher_id: str) -> List[AggregatorBookingRecord]:
        return self.aggregator_db.get_bookings_by_voucher(voucher_id)


ground_truth_oracle = TestbedGroundTruthOracle()


def get_ground_truth_oracle() -> TestbedGroundTruthOracle:
    return ground_truth_oracle

"""
TravelSandboxB: Independent B2B Platform Service Layer.
Demonstrates completely decoupled business logic, fault injection, and reconciliation.
Emits native runtime events when a collector is active.
"""

import asyncio
import hashlib
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from .db import SandboxBDatabase, get_sandbox_b_db
from engine.event_collector import get_active_collector


class TravelSandboxBService:
    """Core B2B service for TravelSandboxB."""

    def __init__(self, db: Optional[SandboxBDatabase] = None):
        self.db = db or get_sandbox_b_db()
        self.fault_mode: str = "VULNERABLE"  # VULNERABLE or FIXED
        self.simulate_timeout: bool = False
        self.timeout_delay_s: float = 0.25
        self.vendor_timeout_s: float = 0.10
        self.webhook_mode: str = "VULNERABLE"  # VULNERABLE or FIXED

    def configure_faults(
        self,
        fault_mode: Optional[str] = None,
        simulate_timeout: Optional[bool] = None,
        timeout_delay_s: Optional[float] = None,
        webhook_mode: Optional[str] = None,
    ) -> None:
        if fault_mode is not None:
            self.fault_mode = fault_mode.upper()
        if simulate_timeout is not None:
            self.simulate_timeout = simulate_timeout
        if timeout_delay_s is not None:
            self.timeout_delay_s = timeout_delay_s
        if webhook_mode is not None:
            self.webhook_mode = webhook_mode.upper()

    def reset(self) -> None:
        self.db.reset()
        self.simulate_timeout = False
        self.fault_mode = "VULNERABLE"
        self.webhook_mode = "VULNERABLE"

    # =========================================================================
    # Booking & Vendor Saga
    # =========================================================================

    async def reserve_order(
        self,
        intent_token: str,
        property_code: str,
        amount_cents: int,
        currency: str = "INR",
        customer_name: str = "Guest",
        idempotency_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Orchestrates reservation order submission against synthetic hotel vendor.
        Emits live runtime telemetry at every step.
        """
        collector = get_active_collector()
        order_id = f"ORD-TSB-{intent_token[-8:]}"

        # 1. Intent / Order Creation
        order_record = {
            "order_id": order_id,
            "intent_token": intent_token,
            "customer_name": customer_name,
            "property_code": property_code,
            "total_amount_cents": amount_cents,
            "currency": currency,
            "order_status": "PENDING",
            "idempotency_token": idempotency_token,
            "retry_count": 0,
            "refund_entitlement_cents": 0,
        }
        self.db.save_order(order_record)

        if collector:
            collector.emit(
                actor="AGENCY",
                event_type="INTENT_CREATED",
                event_status="INFO",
                title="TravelSandboxB Order Intent Created",
                detail=f"Agency initiated order '{order_id}' for {amount_cents / 100:,.0f} {currency} on property '{property_code}'",
                source_component="TravelSandboxBClient",
                metadata={"order_id": order_id, "amount_cents": amount_cents, "currency": currency},
                evidence_references={"table": "tsb_orders", "order_id": order_id},
            )

        # 2. Outbound Dispatch to Synthetic Vendor
        if collector:
            collector.emit(
                actor="AGGREGATOR",
                event_type="REQUEST_DISPATCHED",
                event_status="INFO",
                title="Vendor Allocation Request Dispatched",
                detail=f"TravelSandboxB dispatched vendor reservation request (Client timeout: {self.vendor_timeout_s:.2f}s)",
                source_component="TravelSandboxBEngine",
                metadata={"vendor": "SYNTH_VENDOR_B", "timeout_s": self.vendor_timeout_s},
                evidence_references={"intent_token": intent_token},
            )

        # 3. Vendor Commits Initial Ticket to Database
        initial_conf_id = f"VND-TKT-{intent_token[-6:]}-01"
        conf_record = {
            "confirmation_id": initial_conf_id,
            "intent_token": intent_token,
            "vendor_code": "SYNTH_VENDOR_B",
            "status": "ISSUED",
            "rate_cents": amount_cents,
            "currency": currency,
            "idempotency_key": idempotency_token if self.fault_mode == "FIXED" else None,
        }
        self.db.save_vendor_confirmation(conf_record)

        if collector:
            collector.emit(
                actor="SUPPLIER",
                event_type="SUPPLIER_COMMITTED",
                event_status="INFO",
                title="Vendor Initial Ticket Committed",
                detail=f"Vendor persisted confirmed ticket '{initial_conf_id}' to SQLite database",
                source_component="SyntheticVendorB",
                metadata={"confirmation_id": initial_conf_id, "vendor": "SYNTH_VENDOR_B"},
                evidence_references={"table": "tsb_vendor_confirmations", "confirmation_id": initial_conf_id},
            )

        # 4. Fault Injection (Delayed response causing HTTP client timeout)
        if self.simulate_timeout:
            self.simulate_timeout = False  # One-shot fault trigger
            if collector:
                collector.emit(
                    actor="SUPPLIER",
                    event_type="FAULT_TRIGGERED",
                    event_status="WARNING",
                    title="Vendor Timeout Simulated",
                    detail=f"Vendor delayed response by {self.timeout_delay_s:.2f}s (> {self.vendor_timeout_s:.2f}s client timeout); client experienced ReadTimeout",
                    source_component="SyntheticVendorB",
                    metadata={"delay_s": self.timeout_delay_s, "client_timeout_s": self.vendor_timeout_s},
                    evidence_references={"intent_token": intent_token},
                )
            await asyncio.sleep(self.timeout_delay_s)

            # Simulated client experienced timeout: evaluate recovery behavior
            if self.fault_mode == "VULNERABLE":
                # --- BRANCH A: VULNERABLE BLIND RETRY ---
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="RETRY_RECONCILE",
                        event_status="WARNING",
                        title="Vulnerable Blind Retry Dispatched",
                        detail="TravelSandboxB re-issued creation request without idempotency key or reference lookup",
                        source_component="TravelSandboxBEngine",
                        metadata={"retry_attempt": 1},
                        evidence_references={"intent_token": intent_token},
                    )

                # Vendor commits duplicate ticket on naive retry
                dup_conf_id = f"VND-TKT-{intent_token[-6:]}-02"
                dup_record = {
                    "confirmation_id": dup_conf_id,
                    "intent_token": intent_token,
                    "vendor_code": "SYNTH_VENDOR_B",
                    "status": "ISSUED",
                    "rate_cents": amount_cents,
                    "currency": currency,
                    "idempotency_key": None,
                }
                self.db.save_vendor_confirmation(dup_record)

                if collector:
                    collector.emit(
                        actor="SUPPLIER",
                        event_type="SUPPLIER_COMMITTED",
                        event_status="CRITICAL",
                        title="Duplicate Vendor Ticket Committed",
                        detail=f"Vendor committed secondary ticket '{dup_conf_id}' for identical order intent",
                        source_component="SyntheticVendorB",
                        metadata={"confirmation_id": dup_conf_id, "total_tickets": 2},
                        evidence_references={"table": "tsb_vendor_confirmations", "confirmation_id": dup_conf_id},
                    )

                order_record["order_status"] = "CONFIRMED"
                order_record["retry_count"] = 1
                self.db.save_order(order_record)

                return {
                    "order_id": order_id,
                    "intent_token": intent_token,
                    "status": "CONFIRMED",
                    "retry_count": 1,
                    "confirmations": [initial_conf_id, dup_conf_id],
                }

            else:
                # --- BRANCH B: FIXED RECONCILIATION ---
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="RETRY_RECONCILE",
                        event_status="SUCCESS",
                        title="Capability-Aware Reconciliation Invoked",
                        detail="TravelSandboxB preserved idempotency token and queried vendor reconciliation by reference",
                        source_component="TravelSandboxBEngine",
                        metadata={"strategy": "LOOKUP_BEFORE_RETRY"},
                        evidence_references={"intent_token": intent_token},
                    )

                existing_confs = self.db.get_vendor_confirmations(intent_token)
                matched_id = existing_confs[0]["confirmation_id"] if existing_confs else initial_conf_id

                if collector:
                    collector.emit(
                        actor="SUPPLIER",
                        event_type="SUPPLIER_RECONCILED",
                        event_status="SUCCESS",
                        title="Vendor Ticket Reconciled by Reference",
                        detail=f"Vendor verified existing ticket '{matched_id}'; returned single confirmed record without duplicate mutation",
                        source_component="SyntheticVendorB",
                        metadata={"confirmation_id": matched_id},
                        evidence_references={"table": "tsb_vendor_confirmations", "confirmation_id": matched_id},
                    )

                order_record["order_status"] = "CONFIRMED"
                self.db.save_order(order_record)

                return {
                    "order_id": order_id,
                    "intent_token": intent_token,
                    "status": "CONFIRMED",
                    "retry_count": 0,
                    "confirmations": [matched_id],
                }

        # Normal clean path without timeout
        order_record["order_status"] = "CONFIRMED"
        self.db.save_order(order_record)
        return {
            "order_id": order_id,
            "intent_token": intent_token,
            "status": "CONFIRMED",
            "retry_count": 0,
            "confirmations": [initial_conf_id],
        }

    # =========================================================================
    # Cancellation & Refund Webhooks
    # =========================================================================

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancels order and computes refund entitlement."""
        order = self.db.get_order(order_id)
        if not order:
            raise ValueError(f"Order '{order_id}' not found")

        amount_cents = order["total_amount_cents"]
        # 80% refund entitlement (20% cancellation fee)
        entitlement_cents = int(amount_cents * 0.8)
        order["order_status"] = "CANCELLED"
        order["refund_entitlement_cents"] = entitlement_cents
        self.db.save_order(order)

        return {
            "order_id": order_id,
            "status": "CANCELLED",
            "refund_entitlement_cents": entitlement_cents,
            "currency": order["currency"],
        }

    def process_refund_webhook(
        self,
        event_token: str,
        provider_code: str,
        order_id: str,
        amount_cents: int,
        currency: str = "INR",
        signature: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes refund webhook from payment gateway.
        Vulnerable: applies ledger credit on every delivery without idempotency check.
        Fixed: checks journal table; returns 200 without ledger mutation on duplicate.
        """
        collector = get_active_collector()

        if collector:
            collector.emit(
                actor="PAYMENT",
                event_type="REQUEST_DISPATCHED",
                event_status="INFO",
                title="Payment Gateway Webhook Delivery",
                detail=f"Delivered signed refund webhook '{event_token}' for {amount_cents / 100:,.0f} {currency}",
                source_component="PaymentGatewayB",
                metadata={"event_token": event_token, "provider": provider_code, "amount_cents": amount_cents},
                evidence_references={"event_token": event_token, "order_id": order_id},
            )

        payload_sha = hashlib.sha256(f"{event_token}:{order_id}:{amount_cents}:{currency}".encode()).hexdigest()

        if self.webhook_mode == "VULNERABLE":
            # Vulnerable mode: blindly credits ledger on every replay
            rec_id = self.db.record_ledger_movement(
                transaction_ref=f"REFUND-{event_token}",
                amount_cents=amount_cents,
                currency=currency,
                movement_type="CREDIT_REFUND",
                direction="OUTFLOW",
                intent_token=order_id,
            )

            existing = self.db.get_webhook_record(event_token)
            self.db.record_webhook(event_token, provider_code, payload_sha)

            if existing and collector:
                collector.emit(
                    actor="AGGREGATOR",
                    event_type="FAULT_TRIGGERED",
                    event_status="CRITICAL",
                    title="Duplicate Webhook Delivery Processed Without Deduplication",
                    detail=f"Vulnerable mode lacks idempotency locking; duplicate ledger credit applied for replayed event '{event_token}'",
                    source_component="TravelSandboxBEngine",
                    metadata={"event_token": event_token, "ledger_record_id": rec_id},
                    evidence_references={"table": "tsb_audit_ledger", "record_id": rec_id},
                )
            elif collector:
                collector.emit(
                    actor="AGGREGATOR",
                    event_type="SUPPLIER_COMMITTED",
                    event_status="INFO",
                    title="Refund Webhook Processed & Ledger Credited",
                    detail=f"Processed initial refund event '{event_token}'; credited {amount_cents / 100:,.0f} {currency} to ledger",
                    source_component="TravelSandboxBEngine",
                    metadata={"event_token": event_token, "ledger_record_id": rec_id},
                    evidence_references={"table": "tsb_audit_ledger", "record_id": rec_id},
                )

            return {
                "status": "ACCEPTED",
                "event_token": event_token,
                "ledger_record_id": rec_id,
                "deduplicated": False,
            }

        else:
            # Fixed mode: atomic journal deduplication
            is_new = self.db.record_webhook(event_token, provider_code, payload_sha)
            if not is_new:
                # Deduplicated hit!
                if collector:
                    collector.emit(
                        actor="AGGREGATOR",
                        event_type="RETRY_RECONCILE",
                        event_status="SUCCESS",
                        title="Atomic Idempotency Deduplication Enforced",
                        detail=f"Delivery matched processed event ID '{event_token}' in journal; acknowledged with HTTP 200 without ledger mutation",
                        source_component="TravelSandboxBEngine",
                        metadata={"event_token": event_token, "deduplicated": True},
                        evidence_references={"table": "tsb_webhook_journal", "event_token": event_token},
                    )
                return {
                    "status": "DEDUPLICATED",
                    "event_token": event_token,
                    "deduplicated": True,
                }

            # First delivery
            rec_id = self.db.record_ledger_movement(
                transaction_ref=f"REFUND-{event_token}",
                amount_cents=amount_cents,
                currency=currency,
                movement_type="CREDIT_REFUND",
                direction="OUTFLOW",
                intent_token=order_id,
            )

            if collector:
                collector.emit(
                    actor="AGGREGATOR",
                    event_type="SUPPLIER_COMMITTED",
                    event_status="INFO",
                    title="Refund Webhook Processed & Ledger Credited",
                    detail=f"Processed initial refund event '{event_token}'; credited {amount_cents / 100:,.0f} {currency} to ledger",
                    source_component="TravelSandboxBEngine",
                    metadata={"event_token": event_token, "ledger_record_id": rec_id},
                    evidence_references={"table": "tsb_audit_ledger", "record_id": rec_id},
                )

            return {
                "status": "ACCEPTED",
                "event_token": event_token,
                "ledger_record_id": rec_id,
                "deduplicated": False,
            }


# Global singleton instance
_sandbox_b_service = TravelSandboxBService()


def get_sandbox_b_service() -> TravelSandboxBService:
    return _sandbox_b_service

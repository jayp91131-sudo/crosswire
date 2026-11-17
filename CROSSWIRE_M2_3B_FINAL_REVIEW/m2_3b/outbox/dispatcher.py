"""
Crosswire M2.3B Capability-Aware Dispatcher.
Executes outbox operations according to supplier capabilities:
- IDEMPOTENT_WITH_LOOKUP: safe replays with identical idempotency key & lookup fallback.
- LOOKUP_ONLY: never blind retry on ambiguous drops; bounded polling lookup.
- NEITHER: no blind retry, no lookup; immediate escalation to RECONCILIATION_REQUIRED.
"""

from typing import Dict, Any, Tuple, Optional
from .outbox_manager import OutboxOperation
from ..suppliers.base_adapter import (
    BaseSupplierAdapter,
    SupplierTimeoutError,
    SupplierDefinitiveRejection,
    SupplierAmbiguousError
)

class DispatchResult:
    """Encapsulates the outcome of a capability-aware operation dispatch."""
    def __init__(
        self,
        outcome_status: str,  # SUCCEEDED, FAILED, EXHAUSTED, RECONCILIATION_REQUIRED
        lifecycle_outcome: str,  # RESERVATION_ACTIVE, RESERVATION_CANCELLED, DEFINITIVE_FAILURE, AMBIGUOUS
        raw_payload: Dict[str, Any],
        supplier_reservation_id: Optional[str] = None,
        error_message: Optional[str] = None
    ):
        self.outcome_status = outcome_status
        self.lifecycle_outcome = lifecycle_outcome
        self.raw_payload = raw_payload
        self.supplier_reservation_id = supplier_reservation_id
        self.error_message = error_message

def dispatch_operation(
    op: OutboxOperation,
    adapter: BaseSupplierAdapter
) -> DispatchResult:
    """
    Dispatches an outbox operation using the appropriate capability workflow.
    """
    if op.operation_type == "CREATE_RESERVATION":
        return _dispatch_create(op, adapter)
    elif op.operation_type == "CANCEL_RESERVATION":
        return _dispatch_cancel(op, adapter)
    elif op.operation_type == "LOOKUP_RESERVATION":
        return _dispatch_lookup(op, adapter)
    else:
        raise ValueError(f"Unsupported operation type: {op.operation_type}")

def _dispatch_create(op: OutboxOperation, adapter: BaseSupplierAdapter) -> DispatchResult:
    capability = op.supplier_capability
    payload = op.payload

    try:
        # Initial attempt
        raw_resp = adapter.create_reservation(payload, idempotency_key=op.supplier_idempotency_key)
        return DispatchResult(
            outcome_status="SUCCEEDED",
            lifecycle_outcome="RESERVATION_ACTIVE",
            raw_payload=raw_resp,
            supplier_reservation_id=raw_resp.get("supplier_reservation_id")
        )

    except SupplierDefinitiveRejection as rej:
        # Definitive rejection on initial attempt
        return DispatchResult(
            outcome_status="FAILED",
            lifecycle_outcome="DEFINITIVE_FAILURE",
            raw_payload={"error": str(rej), "details": rej.details},
            error_message=str(rej)
        )

    except (SupplierTimeoutError, SupplierAmbiguousError) as ambig_err:
        # Ambiguous drop / network timeout! Behavior strictly dictated by capability:

        if capability == "IDEMPOTENT_WITH_LOOKUP":
            # Can perform safe replay or lookup
            try:
                lookup_res = adapter.lookup_reservation({"child_intent_id": op.child_intent_id})
                if lookup_res and lookup_res.get("status") == "ACTIVE":
                    return DispatchResult(
                        outcome_status="SUCCEEDED",
                        lifecycle_outcome="RESERVATION_ACTIVE",
                        raw_payload=lookup_res,
                        supplier_reservation_id=lookup_res.get("supplier_reservation_id")
                    )
            except Exception:
                pass  # Fall through to retry or reconciliation

            # If max attempts reached, mark reconciliation required
            if op.attempts >= op.max_attempts:
                return DispatchResult(
                    outcome_status="RECONCILIATION_REQUIRED",
                    lifecycle_outcome="AMBIGUOUS",
                    raw_payload={"error": str(ambig_err), "attempts_exhausted": True},
                    error_message=f"Timeout after {op.attempts} idempotent attempts: {ambig_err}"
                )

            # Not exhausted yet -> can be retried cleanly by outbox
            return DispatchResult(
                outcome_status="PENDING_RETRY",
                lifecycle_outcome="AMBIGUOUS",
                raw_payload={"error": str(ambig_err), "retryable": True},
                error_message=str(ambig_err)
            )

        elif capability == "LOOKUP_ONLY":
            # CRITICAL: NEVER blind retry CREATE without idempotency key!
            # Instead, perform bounded lookup.
            try:
                lookup_res = adapter.lookup_reservation({"child_intent_id": op.child_intent_id})
                if lookup_res and lookup_res.get("status") == "ACTIVE":
                    return DispatchResult(
                        outcome_status="SUCCEEDED",
                        lifecycle_outcome="RESERVATION_ACTIVE",
                        raw_payload=lookup_res,
                        supplier_reservation_id=lookup_res.get("supplier_reservation_id")
                    )
                elif lookup_res and lookup_res.get("status") == "NOT_FOUND":
                    # Definitive not found
                    return DispatchResult(
                        outcome_status="FAILED",
                        lifecycle_outcome="DEFINITIVE_FAILURE",
                        raw_payload=lookup_res,
                        error_message="Lookup confirmed reservation was not created"
                    )
            except Exception as lookup_err:
                pass

            # If lookup did not confirm -> escalate to RECONCILIATION_REQUIRED
            return DispatchResult(
                outcome_status="RECONCILIATION_REQUIRED",
                lifecycle_outcome="AMBIGUOUS",
                raw_payload={"error": str(ambig_err), "lookup_ambiguous": True},
                error_message=f"Ambiguous drop on LOOKUP_ONLY supplier: {ambig_err}"
            )

        elif capability == "NEITHER":
            # CRITICAL: Cannot retry, cannot lookup. Immediate escalation!
            return DispatchResult(
                outcome_status="RECONCILIATION_REQUIRED",
                lifecycle_outcome="AMBIGUOUS",
                raw_payload={"error": str(ambig_err), "neither_capability": True},
                error_message=f"Ambiguous drop on NEITHER supplier requires manual reconciliation: {ambig_err}"
            )

        else:
            raise ValueError(f"Unknown capability: {capability}")

def _dispatch_cancel(op: OutboxOperation, adapter: BaseSupplierAdapter) -> DispatchResult:
    payload = op.payload
    res_id = payload.get("supplier_reservation_id")
    if not res_id:
        return DispatchResult(
            outcome_status="FAILED",
            lifecycle_outcome="DEFINITIVE_FAILURE",
            raw_payload={"error": "Missing supplier_reservation_id in CANCEL payload"},
            error_message="Missing supplier_reservation_id"
        )

    try:
        raw_resp = adapter.cancel_reservation(res_id, idempotency_key=op.supplier_idempotency_key)
        return DispatchResult(
            outcome_status="SUCCEEDED",
            lifecycle_outcome="RESERVATION_CANCELLED",
            raw_payload=raw_resp,
            supplier_reservation_id=res_id
        )
    except SupplierDefinitiveRejection as rej:
        return DispatchResult(
            outcome_status="FAILED",
            lifecycle_outcome="DEFINITIVE_FAILURE",
            raw_payload={"error": str(rej)},
            supplier_reservation_id=res_id,
            error_message=str(rej)
        )
    except (SupplierTimeoutError, SupplierAmbiguousError) as ambig_err:
        # Bounded lookup check if supported
        if adapter.cancel_capability in ("IDEMPOTENT_WITH_LOOKUP", "LOOKUP_ONLY"):
            try:
                rec = adapter.lookup_reservation({"supplier_reservation_id": res_id})
                if rec and rec.get("status") == "CANCELLED":
                    return DispatchResult(
                        outcome_status="SUCCEEDED",
                        lifecycle_outcome="RESERVATION_CANCELLED",
                        raw_payload=rec,
                        supplier_reservation_id=res_id
                    )
            except Exception:
                pass

        return DispatchResult(
            outcome_status="RECONCILIATION_REQUIRED",
            lifecycle_outcome="AMBIGUOUS",
            raw_payload={"error": str(ambig_err)},
            supplier_reservation_id=res_id,
            error_message=str(ambig_err)
        )

def _dispatch_lookup(op: OutboxOperation, adapter: BaseSupplierAdapter) -> DispatchResult:
    payload = op.payload
    try:
        res = adapter.lookup_reservation(payload)
        if res:
            lifecycle = "RESERVATION_ACTIVE" if res.get("status") == "ACTIVE" else "RESERVATION_CANCELLED"
            return DispatchResult(
                outcome_status="SUCCEEDED",
                lifecycle_outcome=lifecycle,
                raw_payload=res,
                supplier_reservation_id=res.get("supplier_reservation_id")
            )
        else:
            return DispatchResult(
                outcome_status="SUCCEEDED",
                lifecycle_outcome="NOT_FOUND",
                raw_payload={"status": "NOT_FOUND"}
            )
    except Exception as e:
        return DispatchResult(
            outcome_status="FAILED",
            lifecycle_outcome="AMBIGUOUS",
            raw_payload={"error": str(e)},
            error_message=str(e)
        )

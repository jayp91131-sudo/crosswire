"""
Crosswire M2.3B Durable Outbox Engine.
Provides atomic operation creation, leasing, attempt lineage tracking,
fencing token monotonicity, and conditional state transitions.
"""

import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import sqlite3

def canonical_now() -> str:
    """Returns current UTC timestamp formatted as ISO 8601 with trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def compute_payload_hash(payload: Dict[str, Any]) -> str:
    """Computes deterministic SHA-256 hash of canonical sorted JSON payload."""
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical_bytes).hexdigest()

class OutboxError(Exception):
    """Base exception for outbox operations."""
    pass

class StaleWorkerError(OutboxError):
    """Raised when an operation attempt fails due to stale fencing token or expired lease."""
    pass

class OutboxOperation:
    def __init__(self, row: sqlite3.Row):
        self.operation_id: str = row["operation_id"]
        self.logical_operation_id: str = row["logical_operation_id"]
        self.attempt_id: str = row["attempt_id"]
        self.child_intent_id: str = row["child_intent_id"]
        self.parent_booking_id: str = row["parent_booking_id"]
        self.agency_id: str = row["agency_id"]
        self.supplier_id: str = row["supplier_id"]
        self.provider_account_id: str = row["provider_account_id"]
        self.operation_type: str = row["operation_type"]
        self.supplier_capability: str = row["supplier_capability"]
        self.status: str = row["status"]
        self.leased_by: Optional[str] = row["leased_by"]
        self.lease_expires_at: Optional[str] = row["lease_expires_at"]
        self.fencing_token: int = row["fencing_token"]
        self.attempts: int = row["attempts"]
        self.max_attempts: int = row["max_attempts"]
        self.supplier_idempotency_key: Optional[str] = row["supplier_idempotency_key"]
        self.payload_hash: str = row["payload_hash"]
        self.payload_json: str = row["payload_json"]
        self.durable_outcome_json: Optional[str] = row["durable_outcome_json"]
        self.created_at: str = row["created_at"]
        self.updated_at: str = row["updated_at"]

    @property
    def payload(self) -> Dict[str, Any]:
        return json.loads(self.payload_json)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "logical_operation_id": self.logical_operation_id,
            "attempt_id": self.attempt_id,
            "child_intent_id": self.child_intent_id,
            "parent_booking_id": self.parent_booking_id,
            "agency_id": self.agency_id,
            "supplier_id": self.supplier_id,
            "provider_account_id": self.provider_account_id,
            "operation_type": self.operation_type,
            "supplier_capability": self.supplier_capability,
            "status": self.status,
            "leased_by": self.leased_by,
            "lease_expires_at": self.lease_expires_at,
            "fencing_token": self.fencing_token,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "supplier_idempotency_key": self.supplier_idempotency_key,
            "payload_hash": self.payload_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class OutboxManager:
    """Manages creation, atomic claiming, leasing, and outcome tracking of child outbox operations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def create_operation(
        self,
        operation_id: str,
        child_intent_id: str,
        parent_booking_id: str,
        agency_id: str,
        supplier_id: str,
        provider_account_id: str,
        operation_type: str,
        supplier_capability: str,
        payload: Dict[str, Any],
        supplier_idempotency_key: Optional[str] = None,
        max_attempts: int = 3
    ) -> OutboxOperation:
        """
        Creates a new PENDING outbox operation.
        Enforces payload hashing, capability constraints, and stable logical identity.
        """
        now = canonical_now()
        logical_op_id = f"{child_intent_id}:{operation_type}"
        initial_attempt_id = f"{operation_id}:att-0"
        payload_hash = compute_payload_hash(payload)
        payload_json = json.dumps(payload, sort_keys=True)

        # Enforce capability-idempotency constraint
        if supplier_capability == "IDEMPOTENT_WITH_LOOKUP":
            if not supplier_idempotency_key:
                supplier_idempotency_key = f"idemp_{operation_id}"
        else:
            # LOOKUP_ONLY and NEITHER must have NULL supplier_idempotency_key
            supplier_idempotency_key = None

        with self.conn:
            self.conn.execute("""
                INSERT INTO child_operation_outbox (
                    operation_id, logical_operation_id, attempt_id,
                    child_intent_id, parent_booking_id, agency_id,
                    supplier_id, provider_account_id, operation_type,
                    supplier_capability, status, leased_by, lease_expires_at,
                    fencing_token, attempts, max_attempts,
                    supplier_idempotency_key, payload_hash, payload_json,
                    durable_outcome_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, 'PENDING', NULL, NULL,
                    1, 0, ?,
                    ?, ?, ?,
                    NULL, ?, ?
                )
            """, (
                operation_id, logical_op_id, initial_attempt_id,
                child_intent_id, parent_booking_id, agency_id,
                supplier_id, provider_account_id, operation_type,
                supplier_capability, max_attempts,
                supplier_idempotency_key, payload_hash, payload_json,
                now, now
            ))

        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> Optional[OutboxOperation]:
        """Retrieves an outbox operation by ID."""
        row = self.conn.execute(
            "SELECT * FROM child_operation_outbox WHERE operation_id = ?",
            (operation_id,)
        ).fetchone()
        return OutboxOperation(row) if row else None

    def acquire_lease(
        self,
        worker_id: str,
        lease_duration_seconds: int = 30,
        operation_id: Optional[str] = None
    ) -> Optional[OutboxOperation]:
        """
        Atomically acquires a lease on the next available runnable operation (or a specific operation).
        Increments attempts, updates attempt_id, increments fencing_token, and transitions to IN_PROGRESS.
        """
        now = canonical_now()
        lease_expiry = (datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Atomic conditional UPDATE with RETURNING
        with self.conn:
            if operation_id:
                cursor = self.conn.execute("""
                    UPDATE child_operation_outbox
                    SET status = 'IN_PROGRESS',
                        leased_by = ?,
                        lease_expires_at = ?,
                        attempts = attempts + 1,
                        attempt_id = operation_id || ':att-' || (attempts + 1),
                        fencing_token = fencing_token + 1,
                        updated_at = ?
                    WHERE operation_id = ?
                      AND (status = 'PENDING' OR (status = 'IN_PROGRESS' AND lease_expires_at < ?))
                      AND attempts < max_attempts
                    RETURNING *;
                """, (worker_id, lease_expiry, now, operation_id, now))
            else:
                cursor = self.conn.execute("""
                    UPDATE child_operation_outbox
                    SET status = 'IN_PROGRESS',
                        leased_by = ?,
                        lease_expires_at = ?,
                        attempts = attempts + 1,
                        attempt_id = operation_id || ':att-' || (attempts + 1),
                        fencing_token = fencing_token + 1,
                        updated_at = ?
                    WHERE operation_id = (
                        SELECT operation_id FROM child_operation_outbox
                        WHERE (status = 'PENDING' OR (status = 'IN_PROGRESS' AND lease_expires_at < ?))
                          AND attempts < max_attempts
                        ORDER BY created_at ASC
                        LIMIT 1
                    )
                    RETURNING *;
                """, (worker_id, lease_expiry, now, now))

            row = cursor.fetchone()
            if row:
                return OutboxOperation(row)
        return None

    def renew_lease(
        self,
        operation_id: str,
        worker_id: str,
        fencing_token: int,
        additional_seconds: int = 30
    ) -> bool:
        """
        Heartbeat / renews an active lease.
        Fails if the worker has been fenced out or the lease was claimed by another worker.
        """
        now = canonical_now()
        new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=additional_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self.conn:
            cursor = self.conn.execute("""
                UPDATE child_operation_outbox
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE operation_id = ?
                  AND status = 'IN_PROGRESS'
                  AND leased_by = ?
                  AND fencing_token = ?
            """, (new_expiry, now, operation_id, worker_id, fencing_token))
            return cursor.rowcount > 0

    def complete_operation(
        self,
        operation_id: str,
        worker_id: str,
        fencing_token: int,
        outcome_status: str,
        outcome_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Transitions an in-flight operation to SUCCEEDED, FAILED, EXHAUSTED, or RECONCILIATION_REQUIRED.
        Strictly requires matching worker_id and fencing_token.
        """
        if outcome_status not in ('SUCCEEDED', 'FAILED', 'EXHAUSTED', 'RECONCILIATION_REQUIRED'):
            raise ValueError(f"Invalid terminal outcome status: {outcome_status}")

        now = canonical_now()
        outcome_json = json.dumps(outcome_data, sort_keys=True) if outcome_data else None

        with self.conn:
            cursor = self.conn.execute("""
                UPDATE child_operation_outbox
                SET status = ?,
                    leased_by = NULL,
                    lease_expires_at = NULL,
                    durable_outcome_json = ?,
                    updated_at = ?
                WHERE operation_id = ?
                  AND status = 'IN_PROGRESS'
                  AND leased_by = ?
                  AND fencing_token = ?
            """, (outcome_status, outcome_json, now, operation_id, worker_id, fencing_token))

            return cursor.rowcount > 0

    def release_for_retry(
        self,
        operation_id: str,
        worker_id: str,
        fencing_token: int,
        mark_exhausted: bool = False
    ) -> bool:
        """
        Releases an operation back to PENDING (or EXHAUSTED if max attempts exceeded).
        Clears worker lease so peer workers can take ownership.
        """
        now = canonical_now()
        next_status = 'EXHAUSTED' if mark_exhausted else 'PENDING'

        with self.conn:
            cursor = self.conn.execute("""
                UPDATE child_operation_outbox
                SET status = ?,
                    leased_by = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE operation_id = ?
                  AND status = 'IN_PROGRESS'
                  AND leased_by = ?
                  AND fencing_token = ?
            """, (next_status, now, operation_id, worker_id, fencing_token))

            return cursor.rowcount > 0

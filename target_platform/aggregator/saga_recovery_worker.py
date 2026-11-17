"""
Active Saga Recovery Worker (Milestone 2.2 Hardening).
Provides active background reconciliation and compensation for orphaned saga records.
Features:
- Lease and fencing-token protected task claims
- Idempotent compensation operations
- Retry with exponential backoff
- Permanent failure retention for manual reconciliation
- Re-verification of external supplier and wallet state
"""

import os
import uuid
import asyncio
import logging
from typing import Optional, List
from datetime import datetime

from target_platform.aggregator.database import (
    AggregatorDatabase,
    get_aggregator_db,
)
from target_platform.aggregator.supplier_client import (
    SupplierClient,
    AggregatorSupplierTimeoutError,
    AggregatorSupplierHttpError,
)
from target_platform.models import (
    SagaRecoveryRecord,
    AggregatorFencingError,
    WalletHoldStatus,
    TransactionStatus,
)

logger = logging.getLogger("saga_recovery_worker")


class SagaRecoveryWorker:
    def __init__(
        self,
        db: Optional[AggregatorDatabase] = None,
        supplier_client: Optional[SupplierClient] = None,
        worker_id: Optional[str] = None,
        lease_duration_seconds: float = 15.0,
        max_attempts: int = 5,
    ):
        self.db = db or get_aggregator_db()
        self.supplier_client = supplier_client or SupplierClient()
        self.worker_id = worker_id or f"saga_worker_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.lease_duration_seconds = lease_duration_seconds
        self.max_attempts = max_attempts
        self._running = False
        self._stopping = False
        self._task: Optional[asyncio.Task] = None
        self._wake_event: asyncio.Event = asyncio.Event()

    async def process_task(self, record: SagaRecoveryRecord) -> bool:
        """
        Executes idempotent compensation for a single claimed saga recovery task.
        Verifies external state before marking completed.
        """
        # If task is already completed, no-op
        if record.status == "COMPENSATED":
            return True

        fencing_token = record.fencing_token
        owner_token = self.worker_id

        try:
            if record.action == "CANCEL_SUPPLIER_RESERVATION":
                if not record.target_supplier_reservation_id:
                    self.db.complete_saga_recovery_task(
                        record.recovery_id,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    return True

                # Check / execute cancellation
                cancel_result = False
                try:
                    c_res = await self.supplier_client.call_cancel_reservation(
                        record.target_supplier_reservation_id,
                        reason="Active saga worker compensation",
                    )
                    if c_res is True:
                        cancel_result = True
                    elif isinstance(c_res, dict) and c_res.get("status") in ["CANCELLED", "CONFIRMED", "SUCCESS"]:
                        cancel_result = True
                    elif getattr(c_res, "status", None) in ["CANCELLED", "CONFIRMED", "SUCCESS"] or getattr(c_res, "success", False):
                        cancel_result = True
                except (AggregatorSupplierTimeoutError, AggregatorSupplierHttpError) as net_err:
                    # Retryable failure
                    self.db.fail_or_retry_saga_recovery_task(
                        recovery_id=record.recovery_id,
                        error_message=f"Supplier cancellation network error: {str(net_err)}",
                        is_retryable=True,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                        backoff_seconds=2.0 * (2 ** (record.attempt_count - 1)),
                        max_attempts=self.max_attempts,
                    )
                    return False
                except Exception as perm_err:
                    # Permanent error
                    self.db.fail_or_retry_saga_recovery_task(
                        recovery_id=record.recovery_id,
                        error_message=f"Permanent supplier cancellation failure: {str(perm_err)}",
                        is_retryable=False,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                        max_attempts=self.max_attempts,
                    )
                    return False

                if cancel_result:
                    # Mark task compensated
                    self.db.complete_saga_recovery_task(
                        record.recovery_id,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    # Update booking status to FAILED if it was ambiguous
                    b_rec = self.db.get_booking_record(record.booking_id, requesting_agency_id=record.agency_id)
                    if b_rec and b_rec.status in [TransactionStatus.SUPPLIER_UNKNOWN, TransactionStatus.RECONCILIATION_REQUIRED]:
                        b_rec.status = TransactionStatus.FAILED
                        b_rec.error_message = "Cancelled via saga compensation recovery worker"
                        b_rec.updated_at = datetime.utcnow()
                        self.db.save_booking_record(b_rec)
                    return True
                else:
                    # Cancellation returned false or failed
                    self.db.fail_or_retry_saga_recovery_task(
                        recovery_id=record.recovery_id,
                        error_message="Supplier cancellation rejected or returned false",
                        is_retryable=True,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                        backoff_seconds=2.0 * (2 ** (record.attempt_count - 1)),
                        max_attempts=self.max_attempts,
                    )
                    return False

            elif record.action == "RELEASE_WALLET_HOLD":
                if not record.wallet_hold_id:
                    self.db.complete_saga_recovery_task(
                        record.recovery_id,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    return True

                hold = self.db.get_wallet_hold(record.wallet_hold_id)
                if hold and hold.status == WalletHoldStatus.RELEASED:
                    # Already released
                    self.db.complete_saga_recovery_task(
                        record.recovery_id,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    return True

                try:
                    self.db.release_wallet_hold(record.wallet_hold_id, reason="SAGA_WORKER_COMPENSATION")
                    self.db.complete_saga_recovery_task(
                        record.recovery_id,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                    )
                    return True
                except Exception as exc:
                    self.db.fail_or_retry_saga_recovery_task(
                        recovery_id=record.recovery_id,
                        error_message=f"Wallet hold release error: {str(exc)}",
                        is_retryable=True,
                        expected_fencing_token=fencing_token,
                        expected_owner_token=owner_token,
                        backoff_seconds=2.0 * (2 ** (record.attempt_count - 1)),
                        max_attempts=self.max_attempts,
                    )
                    return False

            else:
                # Unknown action
                self.db.fail_or_retry_saga_recovery_task(
                    recovery_id=record.recovery_id,
                    error_message=f"Unknown saga action: {record.action}",
                    is_retryable=False,
                    expected_fencing_token=fencing_token,
                    expected_owner_token=owner_token,
                    max_attempts=self.max_attempts,
                )
                return False

        except AggregatorFencingError:
            # Fenced out by another worker or lease lost
            logger.warning(f"Saga worker {self.worker_id} was fenced out on task {record.recovery_id}")
            return False

    async def run_recovery_cycle(self, limit: int = 10) -> int:
        """
        Runs one synchronous sweep over pending saga recovery records.
        Returns the number of tasks processed.
        """
        processed_count = 0
        for _ in range(limit):
            if self._stopping:
                break
            claimed = self.db.claim_pending_saga_recovery_task(
                owner_token=self.worker_id,
                lease_duration_seconds=self.lease_duration_seconds,
                max_attempts=self.max_attempts,
            )
            if not claimed:
                break
            await self.process_task(claimed)
            processed_count += 1
        return processed_count

    async def _worker_loop(self, poll_interval: float = 1.0):
        while self._running and not self._stopping:
            try:
                await self.run_recovery_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in saga recovery cycle: {e}")

            if not self._running or self._stopping:
                break

            try:
                # Wait on wake event (if stopped early) or timeout until next poll
                await asyncio.wait_for(self._wake_event.wait(), timeout=poll_interval)
                self._wake_event.clear()
            except (asyncio.TimeoutError, TimeoutError):
                pass
            except asyncio.CancelledError:
                break

    def start_background_worker(self, poll_interval: float = 1.0):
        if not self._running:
            self._running = True
            self._stopping = False
            self._wake_event.clear()
            self._task = asyncio.create_task(self._worker_loop(poll_interval))

    def stop_background_worker(self):
        self._running = False
        self._stopping = True
        self._wake_event.set()
        if self._task and not self._task.done():
            self._task.cancel()

    async def stop_background_worker_async(self, shutdown_timeout: float = 3.0):
        """
        Graceful worker shutdown:
        1. Sets _running = False and _stopping = True so no new recovery tasks are claimed.
        2. Sets _wake_event to immediately interrupt polling sleep.
        3. Allows currently in-flight recovery task to finish within shutdown_timeout.
        4. If shutdown_timeout is exceeded, forces task cancellation without corrupting DB state.
        """
        self._running = False
        self._stopping = True
        self._wake_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=shutdown_timeout)
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    f"Saga recovery worker {self.worker_id} shutdown limit ({shutdown_timeout}s) exceeded; cancelling."
                )
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

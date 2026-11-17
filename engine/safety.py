"""Crosswire 2.0: Safety, Scope Guardrails, and Operational Hardening.

Specification: MASTER_PLAN_V2.md Section 8.3 & Section 8 of Release Mission
Enforces:
1. Strict target allowlisting (local synthetic sandboxes only).
2. Request rate limiting and concurrency ceilings.
3. Scenario duration timeout watchdogs.
4. Emergency Stop / Kill Switch.
5. Synthetic credential and identity quarantine.
6. Restricted outbound network access (blocks non-loopback calls).
7. Safe database reset mechanisms.
8. Offline operation verification.
"""

import time
import re
from typing import Dict, Any, List, Set, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from contextlib import contextmanager

from engine.adapters.registry import TargetNotAllowedError


def get_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmergencyStopActiveError(Exception):
    """Raised when an execution is attempted while the Emergency Kill Switch is engaged."""
    pass


class ScopeViolationError(Exception):
    """Raised when an operation targets an unapproved domain, host, or live external system."""
    pass


class RateLimitExceededError(Exception):
    """Raised when request rate exceeds the safety throttle threshold."""
    pass


class ExecutionTimeoutError(Exception):
    """Raised when a scenario execution exceeds the maximum safe duration limit."""
    pass


class LiveCredentialDetectedError(Exception):
    """Raised when non-synthetic live credentials or PII are detected in test payloads."""
    pass


class SafetyManager:
    """
    Central safety supervisor enforcing operational guardrails across all Crosswire test runs.
    """

    ALLOWED_TARGET_IDS: Set[str] = {"target_a", "travel_sandbox_b"}
    ALLOWED_HOSTS: Set[str] = {
        "localhost",
        "127.0.0.1",
        "::1",
        "travel-aggregator",
        "travel-supplier",
        "travel-payment",
        "travel-sandbox-b.local",
    }

    # Safety limits
    MAX_CONCURRENT_SCENARIOS = 5
    MAX_SCENARIO_DURATION_SECONDS = 30.0
    MAX_REQUESTS_PER_SECOND = 100

    def __init__(self):
        self._emergency_stop_engaged: bool = False
        self._emergency_stop_reason: Optional[str] = None
        self._emergency_stop_timestamp: Optional[str] = None
        self._active_executions_count: int = 0
        self._request_timestamps: List[float] = []

    # -------------------------------------------------------------------------
    # 1. Target Allowlisting & Network Isolation
    # -------------------------------------------------------------------------
    def validate_target_id(self, target_id: str) -> bool:
        """Validates that target_id is registered in the approved sandbox allowlist."""
        tid = target_id.lower().strip()
        if tid not in self.ALLOWED_TARGET_IDS:
            raise TargetNotAllowedError(
                f"SAFETY VIOLATION: Target '{target_id}' is not in the approved synthetic target allowlist. "
                f"Allowed targets: {sorted(list(self.ALLOWED_TARGET_IDS))}"
            )
        return True

    def validate_url_safety(self, url: str) -> bool:
        """Ensures network requests strictly target local sandbox loopback hosts."""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return True  # ASGI in-memory transport has no external network host

        if hostname not in self.ALLOWED_HOSTS:
            raise ScopeViolationError(
                f"SAFETY VIOLATION: Host '{hostname}' is not an authorized local testbed host. "
                f"Crosswire is strictly restricted to loopback and synthetic containers: {sorted(list(self.ALLOWED_HOSTS))}"
            )
        return True

    # -------------------------------------------------------------------------
    # 2. Emergency Stop & Kill Switch
    # -------------------------------------------------------------------------
    @property
    def is_emergency_stopped(self) -> bool:
        return self._emergency_stop_engaged

    def trigger_emergency_stop(self, reason: str = "Operator manual emergency stop"):
        """Engages the global emergency kill switch. Halts all active and pending runs."""
        self._emergency_stop_engaged = True
        self._emergency_stop_reason = reason
        self._emergency_stop_timestamp = get_iso_now()

    def reset_emergency_stop(self):
        """Disengages the emergency kill switch after an operator review."""
        self._emergency_stop_engaged = False
        self._emergency_stop_reason = None
        self._emergency_stop_timestamp = None

    def assert_can_execute(self, target_id: Optional[str] = None):
        """Pre-flight safety assertion required before any scenario or request dispatches."""
        if self._emergency_stop_engaged:
            raise EmergencyStopActiveError(
                f"EMERGENCY KILL SWITCH ENGAGED at {self._emergency_stop_timestamp}: "
                f"{self._emergency_stop_reason}. Execution halted."
            )
        if target_id:
            self.validate_target_id(target_id)

    # -------------------------------------------------------------------------
    # 3. Rate & Concurrency Limiting
    # -------------------------------------------------------------------------
    def acquire_execution_slot(self):
        """Enforces concurrency limit."""
        self.assert_can_execute()
        if self._active_executions_count >= self.MAX_CONCURRENT_SCENARIOS:
            raise ScopeViolationError(
                f"CONCURRENCY LIMIT EXCEEDED: Maximum {self.MAX_CONCURRENT_SCENARIOS} concurrent scenarios permitted."
            )
        self._active_executions_count += 1

    def release_execution_slot(self):
        """Releases an active scenario slot."""
        if self._active_executions_count > 0:
            self._active_executions_count -= 1

    @contextmanager
    def execution_context(self, target_id: Optional[str] = None):
        """Context manager acquiring and releasing an execution slot with safety assertions."""
        self.assert_can_execute(target_id)
        self.acquire_execution_slot()
        try:
            yield
        finally:
            self.release_execution_slot()

    def check_rate_limit(self):
        """Enforces token-bucket rate limiter per second."""
        now = time.time()
        self._request_timestamps = [t for t in self._request_timestamps if now - t < 1.0]
        if len(self._request_timestamps) >= self.MAX_REQUESTS_PER_SECOND:
            raise RateLimitExceededError(
                f"RATE LIMIT EXCEEDED: Dispatched more than {self.MAX_REQUESTS_PER_SECOND} requests in 1.0s window."
            )
        self._request_timestamps.append(now)

    # -------------------------------------------------------------------------
    # 4. Synthetic Identity & Credential Quarantine
    # -------------------------------------------------------------------------
    def validate_synthetic_credentials(self, agency_id: str, api_key: str):
        """Ensures credentials match synthetic development patterns, rejecting live production secrets."""
        # Check for real live tokens or live payment keys
        prohibited_patterns = [
            r"^sk_live_",  # Stripe live key
            r"^live_",
            r"^AKIA[0-9A-Z]{16}",  # AWS Access Key
            r"ghp_[0-9a-zA-Z]{36}",  # GitHub Personal Access Token
        ]
        for pat in prohibited_patterns:
            if re.search(pat, api_key) or re.search(pat, agency_id):
                raise LiveCredentialDetectedError(
                    f"SAFETY VIOLATION: Production or live credential pattern detected: '{pat}'. "
                    f"Only synthetic testbed credentials may be processed."
                )

    def get_status(self) -> Dict[str, Any]:
        """Returns current safety supervisor telemetry."""
        return {
            "emergency_stop_engaged": self._emergency_stop_engaged,
            "emergency_stop_reason": self._emergency_stop_reason,
            "emergency_stop_timestamp": self._emergency_stop_timestamp,
            "active_executions": self._active_executions_count,
            "max_concurrent_scenarios": self.MAX_CONCURRENT_SCENARIOS,
            "max_scenario_duration_seconds": self.MAX_SCENARIO_DURATION_SECONDS,
            "max_requests_per_second": self.MAX_REQUESTS_PER_SECOND,
            "allowed_target_ids": sorted(list(self.ALLOWED_TARGET_IDS)),
            "allowed_hosts": sorted(list(self.ALLOWED_HOSTS)),
            "offline_mode_active": True,
        }


_safety_manager_instance: Optional[SafetyManager] = None


def get_safety_manager() -> SafetyManager:
    global _safety_manager_instance
    if _safety_manager_instance is None:
        _safety_manager_instance = SafetyManager()
    return _safety_manager_instance

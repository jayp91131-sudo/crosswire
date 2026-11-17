"""
Crosswire M2.8: Canonical Runtime Event Model & Native Instrumentation Collector.
Captures genuine real-time transaction events during adversarial testing runs.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from contextvars import ContextVar
import uuid


def get_iso_now() -> str:
    """Returns current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CanonicalEvent:
    """
    Canonical Event Model representing a discrete transaction event.
    Captures origin, monotonic sequence, timestamp, actor, status, and evidence provenance.
    """
    event_id: str
    execution_id: str
    scenario_id: str
    sequence_number: int
    occurred_at_utc: str
    actor: str  # AGGREGATOR, SUPPLIER, PAYMENT, ORACLE
    event_type: str  # INTENT_CREATED, REQUEST_DISPATCHED, SUPPLIER_COMMITTED, FAULT_TRIGGERED, RETRY_RECONCILE, etc.
    event_status: str  # INFO, WARNING, SUCCESS, CRITICAL
    title: str
    detail: str
    intent_id: Optional[str] = None
    correlation_id: Optional[str] = None
    source_component: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    evidence_references: Dict[str, Any] = field(default_factory=dict)
    # Backward compatibility aliases for existing templates/tests:
    timestamp: str = ""
    status: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = self.occurred_at_utc
        if not self.occurred_at_utc:
            self.occurred_at_utc = self.timestamp
        if not self.status:
            self.status = self.event_status
        if not self.event_status:
            self.event_status = self.status

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class RuntimeEventCollector:
    """
    Active collector scoped to a specific execution run.
    Assigns monotonically increasing sequence numbers and ensures consistent correlation.
    """

    def __init__(
        self,
        execution_id: str,
        scenario_id: str,
        intent_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        self.execution_id = execution_id
        self.scenario_id = scenario_id
        self.intent_id = intent_id or f"INTENT-{execution_id[-6:]}"
        self.correlation_id = correlation_id or f"CORR-{uuid.uuid4().hex[:8].upper()}"
        self.events: List[CanonicalEvent] = []
        self._seq = 0
        self._token = None

    def emit(
        self,
        actor: str,
        event_type: str,
        event_status: str,
        title: str,
        detail: str,
        source_component: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        evidence_references: Optional[Dict[str, Any]] = None,
    ) -> CanonicalEvent:
        """Emits an event at the exact point of execution."""
        self._seq += 1
        now_ts = get_iso_now()
        event_id = f"EVT-{self.execution_id[-6:]}-{self._seq:02d}"

        evt = CanonicalEvent(
            event_id=event_id,
            execution_id=self.execution_id,
            scenario_id=self.scenario_id,
            sequence_number=self._seq,
            occurred_at_utc=now_ts,
            actor=actor,
            event_type=event_type,
            event_status=event_status,
            title=title,
            detail=detail,
            intent_id=self.intent_id,
            correlation_id=self.correlation_id,
            source_component=source_component or "CrosswireRuntime",
            metadata=metadata or {},
            evidence_references=evidence_references or {},
            timestamp=now_ts,
            status=event_status,
        )
        self.events.append(evt)
        return evt

    def __enter__(self):
        self._token = _active_collector_var.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            _active_collector_var.reset(self._token)
            self._token = None


# ContextVar for implicit propagation across async calls
_active_collector_var: ContextVar[Optional[RuntimeEventCollector]] = ContextVar(
    "active_event_collector", default=None
)


class suspend_active_collector:
    """Context manager to temporarily suspend event collection (e.g. during pre-test fixture setup)."""
    def __enter__(self):
        self._token = _active_collector_var.set(None)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            _active_collector_var.reset(self._token)
            self._token = None


def get_active_collector() -> Optional[RuntimeEventCollector]:
    """Retrieves the currently active event collector for the execution context."""
    return _active_collector_var.get()

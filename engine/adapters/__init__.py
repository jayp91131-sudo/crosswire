"""Crosswire Target Adapter Framework.

Provides contracts, registry, and concrete adapters for target platforms.
"""

from engine.adapters.contracts import (
    TargetCapability,
    TargetMetadata,
    TargetExecutionAdapter,
    GroundTruthObservationAdapter,
    ObservationResult,
    BookingObservation,
    SupplierReservationObservation,
    LedgerObservation,
    WebhookObservation,
    NormalizedBookingResult,
)
from engine.adapters.registry import (
    TargetAdapterRegistry,
    get_target_registry,
    TargetNotAllowedError,
    UnsupportedCapabilityError,
    TargetNotFoundError,
)
from engine.adapters.target_a_adapter import (
    TargetAExecutionAdapter,
    TargetAObservationAdapter,
)
from engine.adapters.target_b_adapter import (
    TargetBExecutionAdapter,
    TargetBObservationAdapter,
)

# Auto-register default local targets in the global registry
def register_default_targets() -> None:
    registry = get_target_registry()
    if "target_a" not in registry.list_targets():
        target_a_exec = TargetAExecutionAdapter()
        target_a_obs = TargetAObservationAdapter()
        registry.register(target_a_exec, target_a_obs)

    if "travel_sandbox_b" not in registry.list_targets():
        target_b_exec = TargetBExecutionAdapter()
        target_b_obs = TargetBObservationAdapter()
        registry.register(target_b_exec, target_b_obs)

# Initialize registrations on import
register_default_targets()

__all__ = [
    "TargetCapability",
    "TargetMetadata",
    "TargetExecutionAdapter",
    "GroundTruthObservationAdapter",
    "ObservationResult",
    "BookingObservation",
    "SupplierReservationObservation",
    "LedgerObservation",
    "WebhookObservation",
    "NormalizedBookingResult",
    "TargetAdapterRegistry",
    "get_target_registry",
    "TargetNotAllowedError",
    "UnsupportedCapabilityError",
    "TargetNotFoundError",
    "TargetAExecutionAdapter",
    "TargetAObservationAdapter",
    "TargetBExecutionAdapter",
    "TargetBObservationAdapter",
    "register_default_targets",
]

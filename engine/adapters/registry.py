"""
Crosswire M2.9: Target Capability & Adapter Registry.
Enforces safe target allowlisting, capability discovery, and adapter resolution.
"""

from typing import Dict, Any, List, Optional, Set
from .contracts import (
    TargetCapability,
    TargetMetadata,
    TargetExecutionAdapter,
    GroundTruthObservationAdapter,
)


class TargetNotAllowedError(Exception):
    """Raised when an execution attempts to target an unregistered or disallowed target."""
    pass


class TargetNotFoundError(TargetNotAllowedError):
    """Raised when a target cannot be found in the registry."""
    pass


class UnsupportedCapabilityError(Exception):
    """Raised when a scenario requires capabilities not advertised by the target platform."""
    pass


class TargetAdapterRegistry:
    """
    Central registry for execution and observation adapters.
    Guarantees fail-closed safety boundaries and strict capability verification.
    """

    def __init__(self):
        self._execution_adapters: Dict[str, TargetExecutionAdapter] = {}
        self._observation_adapters: Dict[str, GroundTruthObservationAdapter] = {}

    def register(
        self,
        execution_adapter: TargetExecutionAdapter,
        observation_adapter: GroundTruthObservationAdapter,
    ) -> None:
        target_id = execution_adapter.target_id.lower()
        if target_id != observation_adapter.target_id.lower():
            raise ValueError(
                f"Target ID mismatch: execution='{target_id}', observation='{observation_adapter.target_id}'"
            )
        self._execution_adapters[target_id] = execution_adapter
        self._observation_adapters[target_id] = observation_adapter

    def is_registered(self, target_id: str) -> bool:
        return target_id.lower() in self._execution_adapters

    def get_execution_adapter(self, target_id: str) -> TargetExecutionAdapter:
        tid = target_id.lower()
        if tid not in self._execution_adapters:
            raise TargetNotAllowedError(
                f"Target '{target_id}' is not registered or allowed in Crosswire environment. "
                f"Available targets: {list(self._execution_adapters.keys())}"
            )
        return self._execution_adapters[tid]

    def get_observation_adapter(self, target_id: str) -> GroundTruthObservationAdapter:
        tid = target_id.lower()
        if tid not in self._observation_adapters:
            raise TargetNotAllowedError(
                f"Target '{target_id}' observation adapter is not registered."
            )
        return self._observation_adapters[tid]

    def list_targets(self) -> List[str]:
        return list(self._execution_adapters.keys())

    def list_target_metadata(self) -> List[TargetMetadata]:
        return [adapter.get_metadata() for adapter in self._execution_adapters.values()]

    def get_target_metadata(self, target_id: Any) -> TargetMetadata:
        if isinstance(target_id, TargetMetadata):
            return target_id
        return self.get_execution_adapter(str(target_id)).get_metadata()

    def validate_capabilities(
        self, target_id: str, required_capabilities: List[TargetCapability]
    ) -> None:
        """
        Validates that the target advertises all required capabilities.
        Fails closed with UnsupportedCapabilityError if any capability is missing.
        """
        meta = self.get_target_metadata(target_id)
        missing = [cap for cap in required_capabilities if not meta.has_capability(cap)]
        if missing:
            missing_names = [c.value for c in missing]
            raise UnsupportedCapabilityError(
                f"Target '{target_id}' ({meta.display_name}) lacks required capabilities: {missing_names}. "
                f"Advertised capabilities: {[c.value for c in meta.capabilities]}"
            )


# Global singleton registry
_target_registry = TargetAdapterRegistry()


def get_target_registry() -> TargetAdapterRegistry:
    return _target_registry

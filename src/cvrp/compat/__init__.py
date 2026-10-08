"""Compatibility shims (optional CORAL shell)."""

from cvrp.compat.coral_adapter import (
    CoralInfo,
    coral_available,
    coral_info,
    coral_runtime_available,
)

__all__ = ["CoralInfo", "coral_available", "coral_info", "coral_runtime_available"]

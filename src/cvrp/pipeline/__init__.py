"""Pipeline: the stage tables, gates, stage runtime and orchestrator.

Deliberately **not** re-exporting the submodules here. `cvrp.pipeline.spec` is imported by
`cvrp.config` while that config is still being built, and importing `orchestrator` at
package import time would pull `cvrp.config` back in (orchestrator → config → pipeline →
orchestrator). Import the module you need directly: `from cvrp.pipeline.orchestrator import
start`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DEFAULT_LOOP", "STAGES"]


def __getattr__(name: str) -> Any:
    """Lazy access to the submodules, so `cvrp.pipeline.gates` still works as before."""
    import importlib

    if name == "spec":
        return importlib.import_module("cvrp.pipeline.spec")
    if name in {"gates", "orchestrator", "stages"}:
        return importlib.import_module(f"cvrp.pipeline.{name}")
    if name in {"STAGES", "DEFAULT_LOOP"}:
        return getattr(importlib.import_module("cvrp.pipeline.spec"), name)
    if name == "Orchestrator":
        return importlib.import_module("cvrp.pipeline.orchestrator").Orchestrator
    if name == "REPLAYABLE_STAGES":
        return importlib.import_module("cvrp.pipeline.orchestrator").REPLAYABLE_STAGES
    if name in {"replay_stage", "start"}:
        return getattr(importlib.import_module("cvrp.pipeline.orchestrator"), name)
    if name in {"StageOutcome", "render_instruction", "run_agent_stage"}:
        return getattr(importlib.import_module("cvrp.pipeline.stages"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

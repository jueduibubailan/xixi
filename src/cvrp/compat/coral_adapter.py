"""Thin CORAL shell.

The user-facing promise of ``cvrp`` is "feels like CORAL, but the pipeline is
sequential instead of competitive". This module is the entire shell:

* :func:`coral_available` -- is the CORAL checkout importable?
* :func:`coral_runtime_available` -- is CORAL's Claude Code runtime importable?
* :func:`coral_info` -- a one-line report used by ``cvrp status`` and the UI.

Nothing here is required for the pipeline to run; every call site degrades to
the built-in launcher when CORAL is absent. We deliberately do **not** import
``coral.agent.manager`` -- that class owns a competitive, heartbeat-driven
multi-agent loop, which is the opposite of this pipeline's staged contract.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass


@dataclass
class CoralInfo:
    available: bool
    version: str = ""
    path: str = ""
    runtime_available: bool = False
    note: str = ""

    def describe(self) -> str:
        if not self.available:
            return f"CORAL not importable ({self.note or 'not installed'}) -- using built-in launcher"
        runtime = "runtime OK" if self.runtime_available else "runtime MISSING"
        return f"CORAL {self.version or '?'} at {self.path} ({runtime})"


def _spec(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def coral_available() -> bool:
    return _spec("coral")


def coral_runtime_available() -> bool:
    return _spec("coral.agent.builtin.claude_code")


def coral_info() -> CoralInfo:
    if not _spec("coral"):
        return CoralInfo(available=False, note="pip install -e ../CORAL1 (optional)")
    try:
        import coral  # type: ignore

        version = getattr(coral, "__version__", "")
        path = getattr(coral, "__file__", "") or ""
    except Exception as exc:  # pragma: no cover - defensive
        return CoralInfo(available=False, note=f"import failed: {exc}")
    return CoralInfo(
        available=True,
        version=str(version),
        path=path,
        runtime_available=coral_runtime_available(),
        note="shell only: we reuse CORAL's Claude Code runtime and config syntax",
    )

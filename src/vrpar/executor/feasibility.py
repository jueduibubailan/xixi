"""Variant-agnostic feasibility verification entry point.

The real checks live with the problem (``problems/<slug>/checks.py``), because
only the problem knows what "feasible" means for its variant. This module exists
so callers -- and tests -- have one stable place to ask, and so that the *shape*
of a verification result is documented in code.

A verification result is a dict with at least::

    {
      "feasible": bool,          # the solution satisfies every constraint
      "skipped": bool,           # no solution dump was available
      "violations": [str, ...],  # human-readable reasons, most useful first
      "checks": {name: bool}     # per-constraint booleans for the dashboard
    }

plus whatever extra keys the problem wants to report (costs, route counts, ...).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from vrpar.problems import ProblemPlugin


def distance_matrix(coords: list[tuple[float, float]]) -> list[list[float]]:
    """Euclidean distance matrix for a list of 2-D points.

    Kept in the core (rather than in a problem) because it is the one piece of
    geometry every planar VRP variant shares, and tests use it as an oracle.
    """
    size = len(coords)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            value = math.hypot(coords[i][0] - coords[j][0], coords[i][1] - coords[j][1])
            matrix[i][j] = value
            matrix[j][i] = value
    return matrix


def verify_solution_file(
    instance_path: str | Path,
    solution_path: str | Path,
    problem: ProblemPlugin,
) -> dict[str, Any]:
    """Verify a solution dump using the problem's own checker."""
    result = dict(problem.verify(Path(instance_path), Path(solution_path)))
    result.setdefault("skipped", False)
    result.setdefault("violations", [])
    result.setdefault("checks", {})
    result["instance"] = str(instance_path)
    result["solution"] = str(solution_path)
    result["problem"] = problem.slug
    return result


__all__ = ["distance_matrix", "verify_solution_file"]

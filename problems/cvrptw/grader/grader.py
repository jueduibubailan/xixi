"""CVRPTW problem plugin: the inference-time checks used by the pipeline.

Loaded by ``vrpar.problems.load_problem``. Everything in this file is
*independent* of the generated solver -- it re-implements the time semantics and
recomputes every cost from the instance coordinates. That independence is the
whole point: "no traceback" is not "correct", and only a second implementation
can tell the difference.

Time semantics enforced here (and documented for the agents in
``baseline/BASELINE_NOTES.md``):

* travel time equals Euclidean distance -- there is no vehicle speed;
* arriving before ``ready`` is legal and the vehicle waits;
* arriving after ``due`` is a violation;
* ``current_time = max(arrival, ready) + service_time``;
* index 0 is the depot, whose window is ``[0, horizon]``.

Expected solution dump (written by the generated program)::

    {"routes": [[0, 3, 7, 0], [0, 5, 0], ...], "cost": 12.34}
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# The generator sits next to this file; the engine puts this directory on sys.path while
# loading the module, so a top-level import resolves where a deferred one would not.
from cvrptw_gen import build_dataset as _build

EPS = 1e-6


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #


def instance_coords(instance: dict[str, Any]) -> list[tuple[float, float]]:
    depot = [(float(x), float(y)) for x, y in instance["depot_xy"]]
    nodes = [(float(x), float(y)) for x, y in instance["node_xy"]]
    return depot + nodes


def distance_matrix(instance: dict[str, Any]) -> list[list[float]]:
    coords = instance_coords(instance)
    size = len(coords)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            value = math.hypot(coords[i][0] - coords[j][0], coords[i][1] - coords[j][1])
            matrix[i][j] = value
            matrix[j][i] = value
    return matrix


def route_cost(route: list[int], matrix: list[list[float]]) -> float:
    return sum(matrix[a][b] for a, b in zip(route, route[1:]))


def solution_cost(routes: list[list[int]], matrix: list[list[float]]) -> float:
    return sum(route_cost(route, matrix) for route in routes)


def load_instance(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------- #
# the check itself
# --------------------------------------------------------------------------- #


def check_solution(
    instance: dict[str, Any],
    routes: list[list[int]],
    claimed_cost: float | None = None,
) -> dict[str, Any]:
    """Replay every route and return a verification result."""
    size = int(instance["problem_size"])
    capacity = float(instance["capacity"])
    demand = [float(v) for v in instance["node_demand"]]
    windows = [(float(a), float(b)) for a, b in instance["time_windows"]]
    service = [float(v) for v in instance["service_times"]]
    horizon = float(instance.get("horizon", windows[0][1]))
    matrix = distance_matrix(instance)

    violations: list[str] = []
    checks: dict[str, Any] = {}

    visited: list[int] = []
    served_routes = 0
    total_load = 0.0
    max_route_load = 0.0
    for index, route in enumerate(routes):
        if not route:
            violations.append(f"route {index} is empty")
            continue
        if route[0] != 0 or route[-1] != 0:
            violations.append(
                f"route {index} does not start and end at the depot: {route[:3]}...{route[-3:]}"
            )
            continue
        customers = route[1:-1]
        if not customers:
            continue
        served_routes += 1
        load = 0.0
        now = 0.0
        previous = 0
        for node in customers:
            if not 1 <= node <= size:
                violations.append(f"route {index} contains invalid node id {node}")
                continue
            visited.append(node)
            load += demand[node]
            now += matrix[previous][node]
            ready, due = windows[node]
            if now > due + EPS:
                violations.append(
                    f"customer {node} in route {index}: arrival {now:.4f} > due {due:.4f}"
                )
            now = max(now, ready) + service[node]
            if now > horizon + EPS and node == customers[-1]:
                violations.append(
                    f"route {index}: service at last customer {node} finishes at {now:.4f} "
                    f"beyond horizon {horizon:.4f}"
                )
            previous = node
        if load > capacity + EPS:
            violations.append(f"route {index}: load {load:.4f} exceeds capacity {capacity:.4f}")
        total_load += load
        max_route_load = max(max_route_load, load)

    duplicates = sorted({node for node in visited if visited.count(node) > 1})
    missing = sorted(set(range(1, size + 1)) - set(visited))
    if duplicates:
        violations.append(f"customers served more than once: {duplicates}")
    if missing:
        violations.append(f"customers never served: {missing}")

    cost = solution_cost(routes, matrix)
    checks["customers_served_once"] = not duplicates and not missing
    checks["capacity_respected"] = max_route_load <= capacity + EPS
    checks["time_windows_respected"] = not any("arrival" in v for v in violations)
    checks["starts_and_ends_at_depot"] = all(
        route and route[0] == 0 and route[-1] == 0 for route in routes if route
    )
    checks["all_routes_non_empty"] = all(route for route in routes)

    return {
        "feasible": not violations,
        "skipped": False,
        "violations": violations,
        "checks": checks,
        "routes": len(routes),
        "served_routes": served_routes,
        "total_load": round(total_load, 4),
        "cost": round(cost, 4),
        "claimed_cost": claimed_cost,
        "cost_matches_claim": (
            None if claimed_cost is None else abs(cost - float(claimed_cost)) <= max(1e-4, 1e-4 * abs(cost))
        ),
    }


# --------------------------------------------------------------------------- #
# problem hooks
# --------------------------------------------------------------------------- #


def verify(instance_path: str | Path, solution_path: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    """``checks.py:verify`` -- the pipeline's independent feasibility check."""
    try:
        instance = load_instance(instance_path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return {
            "feasible": False,
            "skipped": False,
            "violations": [f"could not read instance {instance_path}: {exc}"],
            "checks": {},
        }
    try:
        payload = json.loads(Path(solution_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "feasible": False,
            "skipped": False,
            "violations": [f"could not read solution JSON: {exc}"],
            "checks": {},
        }

    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        return {
            "feasible": False,
            "skipped": False,
            "violations": ["solution JSON has no non-empty 'routes' list"],
            "checks": {},
            "solution_keys": sorted(payload),
        }
    normalized = [[int(node) for node in route] for route in routes if route]
    return check_solution(instance, normalized, payload.get("cost"))


def extract_best_cost(payload: dict[str, Any], spec: dict[str, Any]) -> float | None:
    """``checks.py:extract_best_cost`` -- read the score out of a solution dump."""
    for key in ("cost", "best_cost", "objective"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def build_dataset(out_dir: Path, spec: dict[str, Any], force: bool = False) -> list[Any]:
    """The `build_dataset` hook: synthesize the instance set.

    Imports the generator from the file next to this one. The engine puts this module's
    directory on `sys.path` while loading it, which is what lets a bundle's grader be
    self-contained instead of asking every problem to be an installable package.
    """
    params = dict(spec.get("data") or {})
    sizes = [int(value) for value in (params.get("sizes") or [20])]
    horizon = params.get("horizon")
    return _build(
        out_dir=Path(out_dir),
        sizes=sizes,
        count_per_size=int(params.get("count_per_size") or 3),
        seed=int(params.get("seed") or 1234),
        capacity=float(params.get("capacity") or 30.0),
        service_time=float(params.get("service_time") or 0.05),
        horizon=None if horizon is None else float(horizon),
        force=force,
    )


def revision_hints(spec: dict[str, Any]) -> dict[str, str]:
    """``checks.py:revision_hints`` -- error-class specific repair advice."""
    resources = dict(spec.get("resources") or {})
    checkpoint = resources.get("checkpoint") or "the baseline checkpoint"
    return {
        "initial_invalid": (
            "The constructor/inference path produced an infeasible CVRPTW solution. Re-check the "
            "masking first: returning to the depot must always stay legal, and the time-window mask "
            "must be evaluated on the arrival time, not the departure time."
        ),
        "timeout": (
            "Vectorize the time-window feasibility test (no python loop over batch*pomo), and lower "
            "the training/improvement budget before touching the contract."
        ),
        "torch_load_error": (
            f"Keep the encoder input at 2 dimensions so {checkpoint} still loads, load with "
            "strict=False, and report which keys were skipped in your summary."
        ),
        "shape_error": (
            "Tensor shapes drifted. time_windows is (B, n+1, 2), service_times is (B, n+1), "
            "load/current_time are (B, P), and the action mask is (B, P, n+1)."
        ),
    }

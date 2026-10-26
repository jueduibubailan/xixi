"""CVRPLTW n=50 hooks: verify, score, extract_best_cost, revision_hints, build_dataset.

The constraint set is MVMoE's `VRPLTWEnv` at n = 100, recomputed here from the instance by
code the generated solver cannot edit:

  * capacity 50;
  * a **constant** per-route Euclidean length limit of 3.0, depot legs included;
  * time windows, with unit speed so distance and time are the same quantity;
  * every customer exactly once, every route starting and ending at the depot.

The objective is total distance, which is also MVMoE's reward -- not the total duration of
the real-world variant. Anything else would not be comparable to their published numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# The generator is loaded by path under a **bundle-unique** module name. A plain
# `from mvmoe_gen import ...` would collide with the sibling bundle's generator of the same
# file name: `sys.modules` holds one `mvmoe_gen`, so whichever bundle loaded second would
# silently replace the first one's constants (n=50 capacity 40 vs n=100 capacity 50).
def _load_sibling(stem: str):
    import importlib.util

    path = Path(__file__).with_name(stem + ".py")
    module_name = "{}_{}".format(Path(__file__).resolve().parent.parent.name, stem)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mvmoe = _load_sibling("mvmoe_gen")

EPS = 1e-6


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #


def coords(instance: dict[str, Any]) -> list[tuple[float, float]]:
    """Every node's coordinates, depot first (index 0)."""
    return [(float(x), float(y)) for x, y in instance["depot_xy"]] + [
        (float(x), float(y)) for x, y in instance["node_xy"]
    ]


def distance_matrix(instance: dict[str, Any]) -> list[list[float]]:
    points = coords(instance)
    return [
        [math.dist(points[i], points[j]) for j in range(len(points))] for i in range(len(points))
    ]


def route_cost(route: list[int], matrix: list[list[float]]) -> float:
    return sum(matrix[a][b] for a, b in zip(route, route[1:]))


def solution_cost(routes: list[list[int]], matrix: list[list[float]]) -> float:
    return sum(route_cost(route, matrix) for route in routes)


def load_instance(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "node_xy" not in data:
        raise ValueError(f"not a CVRPLTW instance: {path}")
    return data


# --------------------------------------------------------------------------- #
# the independent check
# --------------------------------------------------------------------------- #


def check_solution(
    instance: dict[str, Any],
    routes: list[list[int]],
    claimed_cost: float | None = None,
) -> dict[str, Any]:
    """Replay every route: capacity, time windows, route length, coverage, return to depot."""
    size = int(instance["problem_size"])
    capacity = float(instance["capacity"])
    route_limit = float(instance.get("route_limit", 3.0))
    demand = [float(value) for value in instance["node_demand"]]
    windows = [(float(a), float(b)) for a, b in instance["time_windows"]]
    service = [float(value) for value in instance["service_times"]]
    horizon = float(instance.get("horizon", 3.0))
    matrix = distance_matrix(instance)

    violations: list[str] = []
    visited: list[int] = []
    total_load = 0.0
    max_load = 0.0
    max_length = 0.0
    served_routes = 0

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
            load += demand[node - 1]
            now += matrix[previous][node]
            ready, due = windows[node - 1]
            if now > due + EPS:
                violations.append(
                    f"customer {node} in route {index}: arrival {now:.4f} > due {due:.4f}"
                )
            now = max(now, ready) + service[node - 1]
            previous = node
        # return leg, then the two bounds MVMoE enforces
        now += matrix[previous][0]
        length = route_cost(route, matrix)
        if load > capacity + EPS:
            violations.append(f"route {index}: load {load:.4f} exceeds capacity {capacity:.4f}")
        if length > route_limit + EPS:
            violations.append(
                f"route {index}: length {length:.4f} exceeds route_limit {route_limit:.4f}"
            )
        if now > horizon + EPS:
            violations.append(
                f"route {index}: returns at {now:.4f}, after the depot window ends at {horizon:.4f}"
            )
        total_load += load
        max_load = max(max_load, load)
        max_length = max(max_length, length)

    duplicates = sorted({node for node in visited if visited.count(node) > 1})
    missing = sorted(set(range(1, size + 1)) - set(visited))
    if duplicates:
        violations.append(f"customers served more than once: {duplicates}")
    if missing:
        violations.append(f"customers never served: {missing}")

    cost = solution_cost(routes, matrix)
    return {
        "feasible": not violations,
        "skipped": False,
        "violations": violations,
        "checks": {
            "customers_served_once": not duplicates and not missing,
            "capacity_respected": max_load <= capacity + EPS,
            "route_length_respected": max_length <= route_limit + EPS,
            "time_windows_respected": not any("arrival" in item for item in violations),
            "returns_before_horizon": not any("depot window" in item for item in violations),
            "starts_and_ends_at_depot": all(
                route and route[0] == 0 and route[-1] == 0 for route in routes if route
            ),
        },
        "routes": len(routes),
        "served_routes": served_routes,
        "total_load": round(total_load, 4),
        "max_route_load": round(max_load, 4),
        "longest_route": round(max_length, 4),
        "route_limit": round(route_limit, 4),
        "cost": round(cost, 4),
        "claimed_cost": claimed_cost,
        "cost_matches_claim": (
            None
            if claimed_cost is None
            else abs(cost - float(claimed_cost)) <= max(1e-4, 1e-4 * abs(cost))
        ),
    }


def _verdict(feasible: bool, violations: list[str], **extra: Any) -> dict[str, Any]:
    payload = {
        "feasible": bool(feasible),
        "skipped": False,
        "violations": list(violations),
        "checks": dict(extra.pop("checks", {}) or {}),
        "cost": extra.pop("cost", None),
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# problem hooks
# --------------------------------------------------------------------------- #


def verify(
    instance_path: str | Path, solution_path: str | Path, spec: dict[str, Any]
) -> dict[str, Any]:
    """`checks:verify` -- the pipeline's independent feasibility check."""
    try:
        instance = load_instance(instance_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _verdict(False, [f"cannot read instance {instance_path}: {exc}"])

    payload = _read_json(Path(solution_path))
    if not isinstance(payload, dict):
        return _verdict(False, [f"solution is missing or not valid JSON: {solution_path}"])

    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        return _verdict(False, [f"`routes` must be a non-empty list, got {routes!r}"])
    try:
        normalized = [[int(node) for node in route] for route in routes]
    except (TypeError, ValueError):
        return _verdict(False, [f"`routes` must contain integers: {routes!r}"])

    claimed = payload.get("cost")
    claimed_cost = float(claimed) if isinstance(claimed, (int, float)) else None
    return check_solution(instance, normalized, claimed_cost)


def extract_best_cost(payload: dict[str, Any], spec: dict[str, Any]) -> float | None:
    """The cost a run printed, or None when it cannot be found."""
    for key in ("best_cost", "cost"):
        value = (payload or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def score(
    best_cost: float | None,
    verification: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
) -> float:
    """Rank a run: lower total distance is better."""
    if best_cost is None:
        return 0.0
    scale = float(dict((spec or {}).get("scorer") or {}).get("scale") or 1.0)
    cost = float(best_cost)
    return scale / max(cost, 1e-6)


def revision_hints(spec: dict[str, Any]) -> dict[str, str]:
    """What to tell the revise stage for a given failure class."""
    return {
        "contract": (
            "The program must print both contract lines: "
            "`the initial process is successful, the initial cost is <float>` and "
            "`the process is successful, the best cost is <float>`."
        ),
        "solution": (
            "With `--save-solution <file>` the program must write "
            '`{"routes": [[0, ...], ...], "cost": <float>}`; routes start and end at 0.'
        ),
        "feasibility": (
            "Check capacity 50, the per-route length limit 3.0 (depot legs included) and the "
            "time windows: arrival must not exceed `tw_end`, early arrival waits, and the "
            "vehicle must be back at the depot by 3.0."
        ),
        "import": (
            "An import failed. Keep every dependency to the standard library, or make the "
            "generated package bootstrap `sys.path` for anything it needs from the seed."
        ),
    }


def build_dataset(out_dir: str | Path, spec: dict[str, Any], force: bool = False) -> list[Any]:
    """`checks:build_dataset` -- write the evaluation instances."""
    return _mvmoe.build_dataset(out_dir, spec, force)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

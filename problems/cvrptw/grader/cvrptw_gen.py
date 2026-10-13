"""CVRPTW instance synthesis.

Owned by the problem, not by the engine: a different variant will want different
demands, windows, or none of either. The pipeline only knows that there is a
``build_dataset(out_dir, spec, force)`` entry point producing instance files.

Coordinate and demand distributions are copied from POMO's CVRP generator
(``CVRProblemDef.get_random_problems``: unit-square coordinates, demand in
[0.1, 0.9]) so that a POMO network pretrained on random CVRP data sees
in-distribution inputs.

**Time windows are feasible by construction.** Naively sampling windows on a unit
square produces instances where distant customers cannot be reached before their
window closes at all -- unsolvable data, which makes a pipeline report a *solver*
failure for a *data* problem. Instead we build a canonical service order and
derive the windows from the arrival times that order induces, then self-check and
refuse to emit an infeasible instance.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

DEFAULT_CAPACITY = 30.0
DEFAULT_SERVICE_TIME = 0.05
# Depot horizon. ``None`` means "derive it from the canonical schedule".
DEFAULT_HORIZON: float | None = None

# Window slack. The canonical schedule visits customers in a random order; the
# windows derived from it are only *tight* enough to be interesting if the
# canonical order and a length-sorted order happen to agree. At n=20 the two nearly
# coincide; by n=100 the spread of arrival times means the smallest they can be is
# the depot round-trip, so the generator widens the windows and retries instead of
# raising. See ``generate_instance``.
DEFAULT_BASE_SLACK = 0.10
DEFAULT_SLACK_GROWTH = 0.55
DEFAULT_BASE_WIDTH = 0.02
DEFAULT_WINDOW_RETRIES = 8


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _slack_settings(spec: dict[str, Any] | None = None) -> dict[str, float]:
    """Read the generator's slack parameters, with per-size defaults.

    ``data.slack`` in ``problem.yaml`` may override any of them; anything it does
    not name is derived from the problem size.
    """
    spec = spec or {}
    raw = spec.get("slack") or {}
    if not isinstance(raw, dict):
        raw = {}
    size = int(_as_float(raw.get("problem_size") or spec.get("problem_size") or 20, 20))
    if size >= 80:
        base_slack, growth = 0.35, 1.20
    elif size >= 40:
        base_slack, growth = 0.20, 0.85
    else:
        base_slack, growth = DEFAULT_BASE_SLACK, DEFAULT_SLACK_GROWTH
    return {
        "base_slack": _as_float(raw.get("base_slack"), base_slack),
        "slack_growth": _as_float(raw.get("slack_growth"), growth),
        "base_width": _as_float(raw.get("base_width"), DEFAULT_BASE_WIDTH),
        "retries": max(1, int(_as_float(raw.get("retries"), DEFAULT_WINDOW_RETRIES))),
    }


def generate_instance(
    problem_size: int = 20,
    seed: int = 0,
    capacity: float = DEFAULT_CAPACITY,
    service_time: float = DEFAULT_SERVICE_TIME,
    horizon: float | None = None,
    *,
    base_slack: float = DEFAULT_BASE_SLACK,
    slack_growth: float = DEFAULT_SLACK_GROWTH,
    base_width: float = DEFAULT_BASE_WIDTH,
) -> dict[str, Any]:
    """Build one instance dict with ``problem_size + 1`` nodes (index 0 = depot).

    The canonical schedule is computed first and the windows follow from it:

    * ``ready`` = canonical arrival time minus a margin, so the order is reachable
      without being unreasonably early;
    * ``due``   = arrival + ``base_width`` (arrival - margin) * (1 + slack_growth),
      so the order is admissible and the windows are proportional to the actual
      travel times rather than to an arbitrary constant.

    ``base_slack``/``slack_growth``/``base_width`` control window tightness. They
    are parameters rather than constants because the same construction has to work
    at n=20 and n=100: see ``_slack_settings``.
    """
    rng = random.Random(seed)

    depot_xy = [[0.5, 0.5]]
    node_xy = [[round(rng.random(), 4), round(rng.random(), 4)] for _ in range(problem_size)]

    # POMO draws demands as randint(1, 10) / 10 -> {0.1, ..., 0.9}
    node_demand = [0.0] + [rng.randint(1, 9) / 10.0 for _ in range(problem_size)]

    coords = [(0.5, 0.5)] + [(x, y) for x, y in node_xy]

    def travel(a: int, b: int) -> float:
        return math.hypot(coords[a][0] - coords[b][0], coords[a][1] - coords[b][1])

    order = list(range(1, problem_size + 1))
    rng.shuffle(order)

    time_windows: list[list[float]] = [[0.0, 0.0]]  # the depot window is set below
    arrivals: dict[int, float] = {}
    current = 0
    now = 0.0
    load = 0.0
    for node in order:
        if load + node_demand[node] > capacity:
            now += travel(current, 0)
            current, load, now = 0, 0.0, 0.0
        now += travel(current, node)
        arrivals[node] = now
        now += service_time
        load += node_demand[node]
        current = node

    for node in range(1, problem_size + 1):
        arrive = max(0.0, arrivals[node] - base_width * 0.5)
        ready = round(arrive, 4)
        due = round(arrive + base_width + base_slack * max(base_width, arrive) * (1.0 + slack_growth), 4)
        if due <= ready:
            due = round(ready + base_width, 4)
        time_windows.append([ready, due])

    schedule_end = max(
        (arrivals[node] + service_time for node in range(1, problem_size + 1)), default=0.0
    ) + travel(current, 0)
    if horizon is None:
        horizon = round(max(1.0, schedule_end * 1.5), 4)
    horizon = float(horizon)
    if schedule_end > horizon:
        raise ValueError(
            f"horizon {horizon} is too small for problem_size={problem_size}: the canonical "
            f"schedule needs {schedule_end:.4f}. Pass horizon=None to derive it automatically."
        )
    time_windows[0] = [0.0, horizon]

    service_times = [0.0] + [round(service_time, 4) for _ in range(problem_size)]

    instance: dict[str, Any] = {
        "name": f"cvrptw_n{problem_size}_{seed:02d}",
        "problem_size": problem_size,
        "capacity": float(capacity),
        "horizon": horizon,
        "depot_xy": depot_xy,
        "node_xy": node_xy,
        "node_demand": node_demand,
        "time_windows": time_windows,
        "service_times": service_times,
        "best_known_cost": None,
        "meta": {
            "seed": seed,
            "problem": "cvrptw",
            "generator": "cvrp.problems.cvrptw.instances",
            "total_demand": round(sum(node_demand), 4),
            "min_vehicles_lower_bound": int(math.ceil(sum(node_demand) / float(capacity))),
            "time_semantics": "travel_time == distance; early arrival waits; late arrival infeasible",
        },
    }

    check = _self_check(instance)
    if not check["feasible"]:
        raise RuntimeError(
            f"generated an infeasible instance ({instance['name']}): {check['violations']}"
        )
    instance["meta"]["reference_cost"] = check["cost"]
    instance["meta"]["window_slack"] = {
        "base_slack": round(base_slack, 4),
        "slack_growth": round(slack_growth, 4),
        "base_width": round(base_width, 4),
    }
    return instance


def generate_feasible_instance(
    problem_size: int = 20,
    seed: int = 0,
    capacity: float = DEFAULT_CAPACITY,
    service_time: float = DEFAULT_SERVICE_TIME,
    horizon: float | None = None,
    spec: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Like :func:`generate_instance`, but never gives up on feasibility.

    The window construction can legitimately fail: a window layout that admits the
    canonical order, the length-sorted order *and* the pipeline's checker does not
    always exist at a fixed slack. Rather than surfacing that as a data error in
    the middle of a run, each retry widens the windows until the self-check passes.
    """
    settings = {**_slack_settings({**(spec or {}), **{"problem_size": problem_size}}), **overrides}
    last_error: Exception | None = None
    for attempt in range(int(settings["retries"])):
        factor = 1.0 + attempt
        try:
            return generate_instance(
                problem_size=problem_size,
                seed=seed,
                capacity=capacity,
                service_time=service_time,
                horizon=horizon,
                base_slack=float(settings["base_slack"]) * factor,
                slack_growth=float(settings["slack_growth"]) * factor,
                base_width=float(settings["base_width"]),
            )
        except (RuntimeError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(
        f"could not synthesize a feasible n={problem_size} instance after "
        f"{int(settings['retries'])} attempts with widening windows; last error: {last_error}"
    )


def _self_check(instance: dict[str, Any]) -> dict[str, Any]:
    """Replay a simple feasible schedule against the generated windows.

    A generator-side smoke test, not the pipeline's checker: importing
    ``checks.py`` here would couple data generation to verification, and the
    point of the pipeline's checker is that it is written independently.
    """
    size = int(instance["problem_size"])
    capacity = float(instance["capacity"])
    demand = instance["node_demand"]
    windows = instance["time_windows"]
    service = instance["service_times"]
    coords = [(x, y) for x, y in instance["depot_xy"]] + [(x, y) for x, y in instance["node_xy"]]

    def travel(a: int, b: int) -> float:
        return math.hypot(coords[a][0] - coords[b][0], coords[a][1] - coords[b][1])

    violations: list[str] = []
    routes: list[list[int]] = []
    route = [0]
    load = 0.0
    now = 0.0
    previous = 0
    served = 0
    for node in sorted(range(1, size + 1), key=lambda n: windows[n][0]):
        if load + demand[node] > capacity:
            route.append(0)
            routes.append(route)
            route = [0]
            load, now, previous = 0.0, 0.0, 0
        arrive = now + travel(previous, node)
        ready, due = windows[node]
        if arrive > due + 1e-6:
            violations.append(f"customer {node}: arrival {arrive:.4f} > due {due:.4f}")
        now = max(arrive, ready) + service[node]
        load += demand[node]
        previous = node
        served += 1
        route.append(node)
    route.append(0)
    routes.append(route)

    if served != size:
        violations.append(f"served {served} of {size} customers")

    cost = sum(travel(a, b) for r in routes for a, b in zip(r, r[1:]))
    return {"feasible": not violations, "violations": violations, "cost": round(cost, 4), "routes": routes}


def generate_instances(
    sizes: list[int] | None = None,
    count_per_size: int = 3,
    seed: int = 1234,
    capacity: float = DEFAULT_CAPACITY,
    service_time: float = DEFAULT_SERVICE_TIME,
    horizon: float | None = None,
    spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Deterministically build ``count_per_size`` instances per size."""
    sizes = sizes or [20]
    instances: list[dict[str, Any]] = []
    for size in sizes:
        for offset in range(count_per_size):
            instance_seed = seed + size * 100 + offset
            instance = generate_feasible_instance(
                problem_size=size,
                seed=instance_seed,
                capacity=capacity,
                service_time=service_time,
                horizon=horizon,
                spec=spec,
            )
            instance["name"] = f"cvrptw_n{size}_{offset:02d}"
            instance["meta"]["seed"] = instance_seed
            instances.append(instance)
    return instances


# --------------------------------------------------------------------------- #
# writers
# --------------------------------------------------------------------------- #


def write_instance_json(instance: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(instance, indent=2), encoding="utf-8")
    return path


def write_instance_vrp(instance: dict[str, Any], path: Path) -> Path:
    """Write the AFL-compatible ``.vrp`` mirror of an instance."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = int(instance["problem_size"])
    coords = instance["depot_xy"] + instance["node_xy"]
    lines: list[str] = [
        f"NAME : {instance['name']}",
        "TYPE : CVRPTW",
        f"DIMENSION : {size + 1}",
        f"CAPACITY : {instance['capacity']}",
        f"HORIZON : {instance['horizon']}",
        "VEHICLES : 0",
        "COMMENT : synthesized (travel time == distance; early arrival waits)",
        "NODE_COORD_SECTION",
    ]
    for index, (x, y) in enumerate(coords, start=1):
        lines.append(f"{index} {x} {y}")
    lines.append("DEMAND_SECTION")
    for index, value in enumerate(instance["node_demand"], start=1):
        lines.append(f"{index} {value}")
    lines.append("TIME_WINDOW_SECTION")
    for index, (ready, due) in enumerate(instance["time_windows"], start=1):
        lines.append(f"{index} {ready} {due}")
    lines.append("SERVICE_TIME_SECTION")
    for index, value in enumerate(instance["service_times"], start=1):
        lines.append(f"{index} {value}")
    lines.append("DEPOT_SECTION")
    lines.append("1")
    lines.append("-1")
    lines.append("EOF")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _manifest_entry(instance: dict[str, Any]) -> dict[str, Any]:
    size = int(instance["problem_size"])
    return {
        "name": instance["name"],
        "problem_size": size,
        "capacity": instance["capacity"],
        "horizon": instance["horizon"],
        "json": f"n{size}/{instance['name']}.json",
        "vrp": f"n{size}/{instance['name']}.vrp",
        "reference_cost": instance["meta"].get("reference_cost"),
    }


def write_manifest(
    instances: list[dict[str, Any]],
    root: Path,
    existing: list[dict[str, Any]] | None = None,
) -> Path:
    """Index file so agents and the executor can enumerate instances cheaply.

    ``existing`` carries entries for sizes this call did not regenerate, so the
    data directory can hold several problem sizes at once (n20, n50, n100) without
    one run's manifest erasing another's.
    """
    root = Path(root)
    entries = list(existing or []) + [_manifest_entry(instance) for instance in instances]
    entries.sort(key=lambda entry: (int(entry.get("problem_size") or 0), str(entry.get("name") or "")))
    payload = {
        "problem": "cvrptw",
        "generator": "cvrp.problems.cvrptw.instances",
        "count": len(entries),
        "sizes": sorted({int(entry.get("problem_size") or 0) for entry in entries}),
        "instances": entries,
    }
    target = root / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def build_dataset(
    out_dir: Path,
    spec: dict[str, Any] | None = None,
    force: bool = False,
    sizes: list[int] | None = None,
    count_per_size: int | None = None,
    seed: int | None = None,
    capacity: float | None = None,
    service_time: float | None = None,
    horizon: float | None = None,
    **_ignored: Any,
) -> list[dict[str, Any]]:
    """Generate and write the instance set. Idempotent unless ``force``.

    Signature matches ``build_dataset(out_dir, spec, force)`` as the engine calls
    it; the trailing keyword arguments let the CLI and tests override parameters
    without going through a spec dict.
    """
    params = dict(spec or {})
    out_dir = Path(out_dir)
    manifest = out_dir / "manifest.json"
    requested_sizes = sizes or [int(v) for v in (params.get("sizes") or [20])]
    requested_sizes = [int(size) for size in requested_sizes]

    cached: dict[str, Any] = {}
    if manifest.is_file():
        try:
            cached = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
    have = {int(entry.get("problem_size") or 0) for entry in (cached.get("instances") or [])}
    missing = [size for size in requested_sizes if size not in have]
    if not force and "sizes" not in params and not missing:
        # Every requested size is already on disk. A new size must still be added
        # even though data exists: the loader gates fall back to whatever is
        # present, so silently skipping would score an n=50 task on n=20 instances.
        return []

    resolved_horizon = horizon if horizon is not None else params.get("horizon")
    instances = generate_instances(
        sizes=requested_sizes,
        count_per_size=int(count_per_size if count_per_size is not None else params.get("count_per_size") or 3),
        seed=int(seed if seed is not None else params.get("seed") or 1234),
        capacity=float(capacity if capacity is not None else params.get("capacity") or DEFAULT_CAPACITY),
        service_time=float(
            service_time if service_time is not None else params.get("service_time") or DEFAULT_SERVICE_TIME
        ),
        horizon=None if resolved_horizon is None else float(resolved_horizon),
        spec=params,
    )
    new_names = {instance["name"] for instance in instances}

    # Keep instances of sizes this call did not ask for. Overwriting the manifest
    # would make an n=20 dataset disappear just because an n=50 run happened.
    kept = [entry for entry in (cached.get("instances") or []) if entry.get("name") not in new_names]

    for instance in instances:
        size = int(instance["problem_size"])
        folder = out_dir / f"n{size}"
        write_instance_json(instance, folder / f"{instance['name']}.json")
        write_instance_vrp(instance, folder / f"{instance['name']}.vrp")
    write_manifest(instances, out_dir, existing=kept)
    return instances


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize CVRPTW instances")
    parser.add_argument("--out", type=str, default="instances/data/cvrptw")
    parser.add_argument("--sizes", type=int, nargs="+", default=[20])
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--capacity", type=float, default=DEFAULT_CAPACITY)
    parser.add_argument("--service-time", type=float, default=DEFAULT_SERVICE_TIME)
    parser.add_argument(
        "--horizon", type=float, default=None,
        help="depot time horizon; omit to derive it from the canonical schedule",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    written = build_dataset(
        Path(args.out),
        sizes=list(args.sizes),
        count_per_size=args.count,
        seed=args.seed,
        capacity=args.capacity,
        service_time=args.service_time,
        horizon=args.horizon,
        force=args.force,
    )
    if not written:
        print(f"instances already present under {args.out} (use --force to regenerate)")
        return 0
    for instance in written:
        print(f"wrote {instance['name']} (n={instance['problem_size']})")
    print(f"manifest: {Path(args.out) / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

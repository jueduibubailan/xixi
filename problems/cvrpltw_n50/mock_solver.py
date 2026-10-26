#!/usr/bin/env python3
"""A runnable first-cut solver for CVRPLTW n=50, emitted by `llm.backend: mock`.

It exists so the whole pipeline can be exercised offline: it respects the same constraints
the checker enforces (capacity 40, per-route length 3.0 including the return leg, time
windows with waiting, every customer once) and prints the same two contract lines. It is a
greedy construction, not a good solver -- its job is to prove the wiring, not to score well.

`--path` accepts one instance JSON or a directory of them, as the contract allows.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

EPS = 1e-6


def load_instances(path: Path) -> list[dict]:
    if path.is_dir():
        return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]
    return [json.loads(path.read_text(encoding="utf-8"))]


def points(instance: dict) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in instance["depot_xy"]] + [
        (float(x), float(y)) for x, y in instance["node_xy"]
    ]


def solve(instance: dict) -> tuple[list[list[int]], float]:
    """Greedy nearest-feasible construction, then return the routes and the total distance."""
    size = int(instance["problem_size"])
    capacity = float(instance["capacity"])
    route_limit = float(instance.get("route_limit", 3.0))
    horizon = float(instance.get("horizon", 3.0))
    demand = [float(value) for value in instance["node_demand"]]
    windows = [(float(a), float(b)) for a, b in instance["time_windows"]]
    service = [float(value) for value in instance["service_times"]]
    xy = points(instance)

    def dist(a: int, b: int) -> float:
        return math.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1])

    unvisited = set(range(1, size + 1))
    routes: list[list[int]] = []

    while unvisited:
        route = [0]
        load = 0.0
        now = 0.0
        length = 0.0
        current = 0
        while True:
            best = None
            for node in unvisited:
                if load + demand[node - 1] > capacity + EPS:
                    continue
                leg = dist(current, node)
                arrive = now + leg
                ready, due = windows[node - 1]
                if arrive > due + EPS:
                    continue
                depart = max(arrive, ready) + service[node - 1]
                back = dist(node, 0)
                # the two bounds MVMoE enforces, both needed: waiting eats the clock without
                # adding length, so neither bound implies the other
                if depart + back > horizon + EPS:
                    continue
                if length + leg + back > route_limit + EPS:
                    continue
                score = leg
                if best is None or score < best[0]:
                    best = (score, node, depart, length + leg)
            if best is None:
                break
            _, node, depart, length = best
            leg = dist(current, node)
            load += demand[node - 1]
            now = depart
            current = node
            route.append(node)
            unvisited.discard(node)
        route.append(0)
        routes.append(route)
        if len(route) == 2:
            # No customer fits: serves the nearest one alone. The MVMoE validity condition
            # (depot -> customer -> depot <= 3.0) guarantees this is feasible, so the loop
            # always makes progress instead of spinning.
            node = min(unvisited, key=lambda candidate: dist(0, candidate))
            routes[-1] = [0, node, 0]
            unvisited.discard(node)

    cost = sum(dist(a, b) for route in routes for a, b in zip(route, route[1:]))
    return routes, round(cost, 4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cvrpltw_n50 (mock backend)")
    parser.add_argument("--path", type=str, required=True, help="instance .json, or a directory of them")
    parser.add_argument("--iteration", type=int, default=100, help="number of improvement iterations")
    parser.add_argument(
        "--save-solution",
        type=str,
        default="",
        help='write {"routes": [[0, ...], ...], "cost": <float>} here',
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"no instance at {path}", file=sys.stderr)
        return 1
    instances = load_instances(path)
    if not instances:
        print(f"no instance json under {path}", file=sys.stderr)
        return 1

    instance = instances[0]
    routes, cost = solve(instance)
    print(f"the initial process is successful, the initial cost is {float(cost)}")

    # A real solver would improve here; the mock reports the same value as its best.
    for _ in range(max(0, int(args.iteration))):
        pass

    if args.save_solution:
        Path(args.save_solution).write_text(
            json.dumps({"routes": routes, "cost": float(cost)}), encoding="utf-8"
        )
    print(f"the process is successful, the best cost is {float(cost)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Mock solver emitted by ``llm.backend: mock``.

Not POMO -- a greedy CVRPLTW constructor. Its only job is to make the pipeline's
acceptance contract meaningful offline: it reads a synthesized instance, builds
a feasible solution, verifies it, and prints the two contract lines.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def read_instance(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def distance_matrix(instance: dict) -> list[list[float]]:
    coords = instance["depot_xy"] + instance["node_xy"]
    size = len(coords)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        xi, yi = coords[i]
        for j in range(i + 1, size):
            xj, yj = coords[j]
            value = math.hypot(xi - xj, yi - yj)
            matrix[i][j] = value
            matrix[j][i] = value
    return matrix


def validate(solution: list[list[int]], instance: dict, matrix: list[list[float]]) -> bool:
    capacity = float(instance["capacity"])
    demand = instance["node_demand"]
    windows = instance["time_windows"]
    service = instance["service_times"]
    seen: set[int] = set()
    for route in solution:
        if not route or route[0] != 0 or route[-1] != 0:
            return False
        load = 0.0
        now = 0.0
        previous = 0
        for node in route[1:-1]:
            if node in seen:
                return False
            seen.add(node)
            now += matrix[previous][node]
            ready, due = windows[node]
            if now > due + 1e-9:
                return False
            now = max(now, ready) + service[node]
            load += demand[node]
            if load > capacity + 1e-9:
                return False
            previous = node
    return len(seen) == instance["problem_size"]


def build_solution(instance: dict, matrix: list[list[float]]) -> list[list[int]]:
    size = instance["problem_size"]
    capacity = float(instance["capacity"])
    demand = instance["node_demand"]
    windows = instance["time_windows"]
    service = instance["service_times"]
    remaining = set(range(1, size + 1))
    routes: list[list[int]] = []

    while remaining:
        route = [0]
        load = 0.0
        now = 0.0
        previous = 0
        while True:
            best = None
            for node in sorted(remaining):
                if load + demand[node] > capacity + 1e-9:
                    continue
                arrive = now + matrix[previous][node]
                ready, due = windows[node]
                if arrive > due + 1e-9:
                    continue
                start = max(arrive, ready)
                back = start + service[node] + matrix[node][0]
                if back > windows[0][1] + 1e-9:
                    continue
                key = (matrix[previous][node], node)
                if best is None or key < best[0]:
                    best = (key, node, start)
            if best is None:
                break
            _, node, start = best
            route.append(node)
            remaining.discard(node)
            load += demand[node]
            now = start + service[node]
            previous = node
        if len(route) == 1:
            # Nothing from the depot was feasible. Serving the nearest leftover
            # customer unconditionally keeps the loop terminating; the instance
            # generator guarantees this should not happen for the shipped data.
            node = min(remaining, key=lambda n: matrix[0][n])
            route.append(node)
            remaining.discard(node)
        route.append(0)
        routes.append(route)
    return routes


def route_cost(solution: list[list[int]], matrix: list[list[float]]) -> float:
    total = 0.0
    for route in solution:
        for a, b in zip(route, route[1:]):
            total += matrix[a][b]
    return total


def improve(solution: list[list[int]], instance: dict, matrix: list[list[float]], iteration: int) -> list[list[int]]:
    """Cheap 2-opt-within-route pass; keeps the placeholder executable and honest."""
    best = [list(route) for route in solution]
    best_cost = route_cost(best, matrix)
    for _ in range(max(0, iteration)):
        improved = False
        for index, route in enumerate(best):
            for i in range(1, len(route) - 2):
                for j in range(i + 1, len(route) - 1):
                    candidate = route[:i] + route[i : j + 1][::-1] + route[j + 1 :]
                    trial = [list(r) for r in best]
                    trial[index] = candidate
                    if validate(trial, instance, matrix):
                        cost = route_cost(trial, matrix)
                        if cost + 1e-9 < best_cost:
                            best, best_cost, improved = trial, cost, True
                            break
                if improved:
                    break
            if improved:
                break
        if not improved:
            break
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="CVRPLTW solver (mock backend placeholder)")
    parser.add_argument("--path", type=str, required=True, help="Path to an instance .json or a directory")
    parser.add_argument("--iteration", type=int, default=100, help="Number of improvement iterations")
    parser.add_argument(
        "--save-solution",
        type=str,
        default=None,
        help="Write the best solution as JSON: {\"routes\": [[0, ...], ...], \"cost\": <float>}",
    )
    args = parser.parse_args()

    instance_path = Path(args.path)
    if instance_path.is_dir():
        candidates = sorted(instance_path.glob("*.json"))
        if not candidates:
            print(f"no instance json under {instance_path}", file=sys.stderr)
            return 2
        instance_path = candidates[0]

    instance = read_instance(str(instance_path))
    matrix = distance_matrix(instance)

    initial = build_solution(instance, matrix)
    if not validate(initial, instance, matrix):
        print("Initial solution is not valid.", file=sys.stderr)
        return 1
    initial_cost = route_cost(initial, matrix)
    print(f"the initial process is successful, the initial cost is {initial_cost}")

    best = improve(initial, instance, matrix, args.iteration)
    best_cost = route_cost(best, matrix)
    if args.save_solution:
        with open(args.save_solution, "w", encoding="utf-8") as handle:
            json.dump({"routes": best, "cost": best_cost}, handle)
    print(f"the process is successful, the best cost is {best_cost}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

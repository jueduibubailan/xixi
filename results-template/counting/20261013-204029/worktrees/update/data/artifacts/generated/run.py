#!/usr/bin/env python3
"""The template's first-cut solver, emitted by `llm.backend: mock`.

It exists so a fresh pipeline can be exercised offline: it satisfies the same contract the
real deliverable must (the two printed lines, the `--save-solution` dump), which is what
lets the corrector, the executor and the grader all be tested without a model.

Replace it with something that solves *your* problem. Its only job here is to prove the
wiring -- the same file is used as the scaffolding a real generation would start from.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_instances(path: Path) -> list[dict]:
    """Accept one instance file or a directory of them, as the contract allows."""
    if path.is_dir():
        return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]
    return [json.loads(path.read_text(encoding="utf-8"))]


def solve(instance: dict) -> tuple[dict, float]:
    """The answer, and its cost. Cost 0.0 means "correct" for this problem."""
    numbers = [int(value) for value in instance.get("numbers") or []]
    answer = {"total": sum(numbers), "squares": [value * value for value in numbers]}
    return answer, 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="counting (mock backend)")
    parser.add_argument("--path", type=str, required=True, help="instance .json, or a directory of them")
    parser.add_argument("--iteration", type=int, default=100, help="number of improvement iterations")
    parser.add_argument(
        "--save-solution",
        type=str,
        default="",
        help='write {"total": <int>, "squares": [...], "cost": <float>} here',
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

    # "Initial" and "best" are the same answer here: the mock does not search. A real
    # solver would report the first feasible solution and then whatever it improved to.
    answer, cost = solve(instances[0])
    print(f"the initial process is successful, the initial cost is {float(cost)}")

    for _ in range(max(0, int(args.iteration))):
        pass  # a real solver would improve here

    if args.save_solution:
        payload = {"total": answer["total"], "squares": answer["squares"], "cost": float(cost)}
        Path(args.save_solution).write_text(json.dumps(payload), encoding="utf-8")
    print(f"the process is successful, the best cost is {float(cost)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

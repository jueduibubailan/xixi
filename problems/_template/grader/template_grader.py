"""The problem's own hooks: verify, score, extract_best_cost, revision_hints, build_dataset.

Everything problem-specific lives here. The engine looks for these names
(`cvrp.problems.HOOK_NAMES`) and calls whichever are present:

    build_dataset(out_dir, spec, force) -> list        the instance set
    verify(instance_path, solution_path, spec) -> dict the independent feasibility check
    extract_best_cost(payload, spec) -> float | None   the cost read out of a run
    score(cost, spec) -> float                         the number the run is ranked by
    revision_hints(spec) -> dict[str, str]             "this error class means fix that"

This file is the template's illustrative problem (return the sum and the sum of squares).
Read it as a worked example, then replace the parts that encode *your* definition of
"correct" -- which is `verify()` and `build_dataset()`, and nothing else.

Why the checker lives here rather than in the pipeline: the generated code is untrusted.
It may print a plausible cost while its solution violates the problem's constraints, so the
verdict is recomputed from the instance by code the agent cannot edit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EPS = 1e-6

#: Keys the engine reads out of an instance to decide whether the data set is complete.
MANIFEST = "manifest.json"


# --------------------------------------------------------------------------------------
# building the data
# --------------------------------------------------------------------------------------


def build_dataset(out_dir: Path, spec: dict[str, Any], force: bool = False) -> list[Any]:
    """Generate the evaluation instances. Called before any agent runs.

    `spec` is this problem's `problem:` block after rendering, so `spec["data"]` carries the
    parameters from `task.yaml`. Instances are cached: unless `force`, an existing set is
    reused, which is what makes a rerun cheap and reproducible.
    """
    out_dir = Path(out_dir)
    params = dict(spec.get("data") or {})
    sizes = [int(value) for value in (params.get("sizes") or [8])]
    count = int(params.get("count_per_size") or 2)
    seed = int(params.get("seed") or 1234)
    largest = int(params.get("largest") or 20)

    manifest_path = out_dir / MANIFEST
    if not force and manifest_path.is_file():
        cached = _read_json(manifest_path) or {}
        have = {int(entry.get("problem_size") or 0) for entry in (cached.get("instances") or [])}
        if set(sizes).issubset(have):
            return list(cached.get("instances") or [])

    entries: list[dict[str, Any]] = []
    for size in sizes:
        folder = out_dir / f"n{size}"
        folder.mkdir(parents=True, exist_ok=True)
        for offset in range(count):
            instance = generate_instance(size=size, seed=seed + size * 100 + offset, largest=largest)
            path = folder / f"counting_n{size}_{offset:02d}.json"
            path.write_text(json.dumps(instance, indent=2), encoding="utf-8")
            entries.append(
                {
                    "path": str(path.relative_to(out_dir)).replace("\\", "/"),
                    "problem_size": size,
                    "name": instance["name"],
                    "seed": instance["seed"],
                }
            )
    manifest_path.write_text(
        json.dumps({"problem": "counting", "instances": entries}, indent=2), encoding="utf-8"
    )
    return entries


def generate_instance(size: int = 8, seed: int = 0, largest: int = 20) -> dict[str, Any]:
    """One deterministic instance: `size` integers, reproducible from `seed`.

    Implements its own small PRNG so an instance depends only on the seed, not on the
    Python version's `random` internals -- the data a run was graded on must stay the same
    when the project is checked out again months later.
    """
    state = (seed * 2654435761 + 1013904223) & 0xFFFFFFFF
    numbers: list[int] = []
    for _ in range(int(size)):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        numbers.append(int(state % int(largest)) + 1)
    return {
        "name": f"counting_n{size}_{seed:02d}",
        "problem_size": int(size),
        "seed": int(seed),
        "numbers": numbers,
    }


# --------------------------------------------------------------------------------------
# the independent check
# --------------------------------------------------------------------------------------


def verify(
    instance_path: str | Path, solution_path: str | Path, spec: dict[str, Any]
) -> dict[str, Any]:
    """Recompute the answer and compare it with what the program claimed.

    This is the hook that makes the pipeline trustworthy. Without it the engine falls back
    to `_default_verify`, which only checks that the dump exists and parses as JSON -- a run
    would then report success for a wrong answer.

    Returning `feasible: False` with `violations` is what drives the revise loop, so the
    messages should name the offending value: that text is what the revise stage reads.
    """
    instance = _read_json(instance_path)
    if not isinstance(instance, dict) or "numbers" not in instance:
        return _verdict(False, [f"instance is not a counting instance: {instance_path}"])

    numbers = [int(value) for value in instance["numbers"]]
    expected_total = sum(numbers)
    expected_squares = [value * value for value in numbers]

    payload = _read_json(solution_path)
    if payload is None:
        return _verdict(False, [f"solution is missing or not valid JSON: {solution_path}"])

    violations: list[str] = []
    total = payload.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        violations.append(f"`total` must be an integer, got {total!r}")
    elif total != expected_total:
        violations.append(f"`total` is {total}, expected {expected_total}")

    squares = payload.get("squares")
    if not isinstance(squares, list):
        violations.append(f"`squares` must be a list, got {type(squares).__name__}")
    elif [int(value) for value in squares] != expected_squares:
        violations.append(
            f"`squares` is {squares}, expected {expected_squares} for numbers {numbers}"
        )

    claimed = payload.get("cost")
    return _verdict(
        not violations,
        violations,
        checks={
            "total_correct": not any("`total`" in item for item in violations),
            "squares_correct": not any("`squares`" in item for item in violations),
        },
        cost=float(claimed) if isinstance(claimed, (int, float)) else None,
    )


def _verdict(
    feasible: bool,
    violations: list[str],
    *,
    checks: dict[str, Any] | None = None,
    cost: float | None = None,
) -> dict[str, Any]:
    """Shape the answer the way the engine expects, so a hook cannot break the contract.

    A dict without `feasible` is treated as "not verified" rather than as a pass, but
    producing the right keys here keeps that from being a surprise.
    """
    return {
        "feasible": bool(feasible),
        "skipped": False,
        "violations": list(violations),
        "checks": dict(checks or {}),
        "cost": cost,
    }


# --------------------------------------------------------------------------------------
# reading a run, and scoring it
# --------------------------------------------------------------------------------------


def extract_best_cost(payload: dict[str, Any], spec: dict[str, Any]) -> float | None:
    """The cost a run printed, or None when it cannot be found.

    Preferred source is the structured payload the contract parser produced; the printed
    line is the fallback, because a program that crashed after printing is still a program
    whose cost we know.
    """
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
    """Rank a run.

    The engine calls this as `hook(best_cost, verification, spec)`, so the signature has
    three parameters even though the default arithmetic only needs the first. A problem with
    nothing special to say can omit this hook entirely and fall back to `scorer.kind`.
    """
    if best_cost is None:
        return 0.0
    # A correct answer scores the full scale. The generic `inverse_cost` rule would divide by
    # the cost, and this problem's correct cost is 0.0.
    scale = float(dict((spec or {}).get("scorer") or {}).get("scale") or 1.0)
    cost = float(best_cost)
    if cost <= EPS:
        return scale
    return scale / (1.0 + cost)


def revision_hints(spec: dict[str, Any]) -> dict[str, str]:
    """What to tell the revise stage for a given failure. Keys are error classes."""
    return {
        "contract": (
            "The program must print both contract lines: "
            "`the initial process is successful, the initial cost is <float>` and "
            "`the process is successful, the best cost is <float>`. Check the wording, "
            "the float formatting, and that both are flushed before exit."
        ),
        "solution": (
            "With `--save-solution <file>` the program must write "
            '`{"total": <int>, "squares": [<int>, ...], "cost": <float>}`. '
            "`total` is the sum of the instance's numbers and `squares` is each number "
            "multiplied by itself, in order."
        ),
        "feasibility": (
            "The answer does not match the instance. Recompute from the instance file "
            "rather than from anything cached, and make sure `--path` may point at a "
            "directory of instances, not only at a single file."
        ),
        "import": (
            "An import failed. Keep every dependency to the standard library, or make the "
            "generated package bootstrap `sys.path` for anything it needs from the seed."
        ),
    }


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _read_json(path: str | Path) -> Any:
    """Read JSON, returning None rather than raising: every caller here is a checker."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def expected_answer(instance: dict[str, Any]) -> dict[str, Any]:
    """The right answer for an instance. Public so a test can assert against it."""
    numbers = [int(value) for value in instance.get("numbers") or []]
    return {"total": sum(numbers), "squares": [value * value for value in numbers]}

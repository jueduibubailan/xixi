"""Stage executor: the "code error agent".

Responsibilities:
* run the three gates (compile -> smoke run -> full run)
* apply the acceptance contract
* classify failures into a stable error class + a message the revise agent can act on
* persist ``artifacts/runs/s3_run_report.json`` plus raw stdout for post-mortems

It never edits code and never calls an LLM.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any

from cvrp.executor.contract import (
    Contract,
    StepResult,
    classify_error,
    condense,
    find_instances,
    py_compile,
    run_program,
    save_stdout,
    write_run_report,
)

SMOKE_ITERATIONS = 5
FULL_ITERATIONS = 1000


def _first_instance(instances_dir: Path, size: int | None = None, pattern: str = "**/*.json") -> Path | None:
    found = find_instances(instances_dir, size=size, pattern=pattern)
    return found[0] if found else None


def run_checks(
    run_dir: Path,
    worktree: Path,
    contract: Contract,
    instances_dir: Path,
    instance_size: int | None = None,
    instance_glob: str = "**/*.json",
    smoke_iterations: int = SMOKE_ITERATIONS,
    full_iterations: int = FULL_ITERATIONS,
    extra_hints: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the gate sequence and return the run report payload."""
    run_dir = Path(run_dir)
    worktree = Path(worktree)

    steps: list[StepResult] = []
    compile_step = py_compile(run_dir, worktree)
    steps.append(compile_step)

    if compile_step.passed:
        instance = _first_instance(instances_dir, instance_size, instance_glob)
        smoke = run_program(
            run_dir=run_dir,
            worktree=worktree,
            contract=contract,
            instance=instance,
            iteration=smoke_iterations,
            name="smoke_run",
        )
        steps.append(smoke)
        if smoke.passed:
            full = run_program(
                run_dir=run_dir,
                worktree=worktree,
                contract=contract,
                instance=instance,
                iteration=full_iterations,
                name="full_run",
            )
            steps.append(full)

    passed = bool(steps) and all(step.passed for step in steps) and len(steps) == 3
    failed = next((step for step in steps if not step.passed), None)

    for step in steps:
        if step.stdout or step.stderr:
            save_stdout(run_dir, f"s3_{step.name}", (step.stdout or "") + ("\n--- stderr ---\n" + step.stderr if step.stderr else ""))

    payload: dict[str, Any] = {
        "stage": "code_error",
        "passed": passed,
        "steps": [step.as_dict() for step in steps],
        "error_class": failed.error_class if failed else "",
        "error_message": condense(failed.message, 6000) if failed else "",
        "failed_step": failed.name if failed else "",
        "initial_cost": next((s.initial_cost for s in steps if s.initial_cost is not None), None),
        "best_cost": next((s.best_cost for s in steps if s.best_cost is not None), None),
        "environment": {
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "contract": {
            "program": contract.program,
            "best_regex": contract.best_regex,
            "initial_regex": contract.initial_regex,
            "timeout_s": contract.timeout_s,
        },
    }

    # A dedicated hint keeps the revise prompt short: AFL needed several rounds of
    # "analyze then fix"; encoding the known failure modes up front saves turns.
    # The problem's own hints override the generic ones for its error classes.
    payload["revision_hint"] = _revision_hint(payload, extra_hints)
    write_run_report(run_dir, payload)
    return payload


def _revision_hint(payload: dict[str, Any], extra_hints: dict[str, str] | None = None) -> str:
    error_class = payload.get("error_class") or ""
    if error_class and extra_hints and error_class in extra_hints:
        return str(extra_hints[error_class])
    hints = {
        "timeout": "Reduce per-step work (vectorize the hot loop, avoid python loops over batch*pomo) "
                   "or lower the default iteration count. If training is the bottleneck, cut training steps.",
        "contract_missing": "The program must print the two contract lines with exactly the wording the "
                            "generation prompt specifies, or the exit gate cannot score it. Check stdout.",
        "initial_invalid": "The constructor/inference path produced an infeasible solution. Re-check the "
                           "masking first: verify that at least one action stays legal in every state.",
        "torch_load_error": "The state_dict keys must match the baseline checkpoint. Keep the input "
                            "dimension of the pretrained module unchanged and load with strict=False; "
                            "report which keys were skipped.",
        "shape_error": "Tensor shapes drifted between the environment and the model. Re-read the shape "
                       "table in the generation prompt and align the new tensors with it.",
        "import_error": "A module is missing or a sibling module name is wrong. All generated modules live in "
                        "the same directory as the entry program; add sys.path handling for the baseline "
                        "package if its helpers are used.",
        "syntax_error": "Fix the syntax/indentation error in place; do not restructure the file.",
        "memory_error": "Reduce batch size or the number of parallel rollouts for the full run; keep the "
                        "acceptance contract unchanged.",
    }
    if error_class in hints:
        return hints[error_class]
    if not error_class:
        return "All gates passed."
    return (
        "Read the traceback in error_message, find the first frame inside the generated directory, "
        "and fix that root cause. Change as few files as possible."
    )


def load_run_report(run_dir: Path, relative: str = "artifacts/runs/s3_run_report.json") -> dict[str, Any]:
    target = Path(run_dir) / relative
    if not target.is_file():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def run_report_summary(report: dict[str, Any]) -> str:
    if not report:
        return "no run report yet"
    if report.get("passed"):
        return f"PASSED (best_cost={report.get('best_cost')}, initial_cost={report.get('initial_cost')})"
    step = report.get("failed_step") or "?"
    return f"FAILED at {step} [{report.get('error_class') or 'unclassified'}]"


def classify_signature(report: dict[str, Any]) -> str:
    """Stable signature so repeated identical failures can short-circuit."""
    return "|".join(
        [
            str(report.get("failed_step") or ""),
            str(report.get("error_class") or ""),
            str((report.get("error_message") or "")[:300]),
        ]
    )


__all__ = [
    "Contract",
    "StepResult",
    "classify_error",
    "classify_signature",
    "load_run_report",
    "run_checks",
    "run_report_summary",
]

"""Stage executor: the "code error agent".

Responsibilities:
* run the gate sequence (compile -> import -> correspondence -> smoke -> full run)
* apply the acceptance contract
* classify failures into a stable error class + a message the revise agent can act on
* persist ``artifacts/runs/s3_run_report.json`` plus raw stdout for post-mortems

It never edits code and never calls an LLM. The correspondence gate is the
deterministic half of "the deliverable is one-to-one with POMO"; the semantic half
(reading the code for missing symbols and logic bugs) is the revise agent's job.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any

from vrpar.executor.contract import (
    Contract,
    StepResult,
    classify_error,
    condense,
    find_instances,
    import_probe,
    py_compile,
    require_instance_size,
    run_program,
    save_stdout,
    write_run_report,
)
from vrpar.executor.correspondence import CorrespondenceReport, correspondence_step

SMOKE_ITERATIONS = 5
FULL_ITERATIONS = 1000


def _first_instance(instances_dir: Path, size: int | None = None, pattern: str = "**/*.json") -> Path | None:
    found = find_instances(instances_dir, size=size, pattern=pattern)
    return found[0] if found else None


def _run_with_cpu_fallback(**kwargs: Any) -> tuple[StepResult, bool]:
    """Run the program, retrying once on CPU when the GPU is unavailable.

    Only `cuda_unavailable` triggers the retry: an OOM, a wrong answer or a syntax
    error is the agent's problem, and re-running those on a slower device would just
    waste the stage's time budget. Returns ``(step, used_fallback)``.
    """
    step = run_program(**kwargs)
    if step.passed or step.error_class != "cuda_unavailable":
        return step, False
    retry = run_program(**kwargs, force_cpu=True)
    if retry.passed:
        # Keep the step name: `reached_end` requires the last step to be exactly
        # `full_run`, and the stdout file is named after it. The flag carries the fact
        # that the CPU retry is what passed.
        retry.device_fallback = True
        retry.message = "GPU unavailable; passed on CPU (VRPAR_FORCE_CPU=1)"
        return retry, True
    # Both failed. Report the GPU attempt: it is the one that names the real cause,
    # and the retry's stderr is nearly the same.
    step.message = (
        f"{step.message}\n\n[CPU retry also failed]\n{retry.message}"
    ).strip()
    return step, True

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
    problem: Any | None = None,
) -> dict[str, Any]:
    """Execute the gate sequence and return the run report payload."""
    run_dir = Path(run_dir)
    worktree = Path(worktree)

    steps: list[StepResult] = []
    used_fallback = False
    compile_step = py_compile(run_dir, worktree)
    steps.append(compile_step)

    # An import pass sits between "it parses" and "it runs": a missing helper file
    # is a much cheaper failure to explain here than as a traceback inside run.py.
    if compile_step.passed:
        probe = import_probe(run_dir, worktree)
        steps.append(probe)

    # The deliverable's shape is checked before anything is executed: one-to-one with
    # POMO, no extra generated code, POMO's training budget, and a training entry
    # point that does not try to load a checkpoint. All of it is static, so it also
    # runs on a deliverable that would otherwise be fine to execute.
    correspondence: CorrespondenceReport | None = None
    if compile_step.passed:
        corr_step, correspondence = correspondence_step(run_dir, worktree, problem)
        steps.append(corr_step)

    if compile_step.passed and all(step.passed for step in steps):
        candidates = find_instances(instances_dir, instance_size, instance_glob)
        # Refuse to run on the wrong problem size. `find_instances` prefers a
        # size-named folder but falls back to whatever exists, and a silent
        # fallback would score a different problem than the task declared.
        wrong_size = require_instance_size(candidates, instance_size, name="instance_size")
        if wrong_size is not None:
            steps.append(wrong_size)
        instance = candidates[0] if candidates else None
        if wrong_size is None:
            smoke, smoke_fallback = _run_with_cpu_fallback(
                run_dir=run_dir,
                worktree=worktree,
                contract=contract,
                instance=instance,
                iteration=smoke_iterations,
                name="smoke_run",
            )
            steps.append(smoke)
            used_fallback = smoke_fallback
            if smoke.passed:
                full, full_fallback = _run_with_cpu_fallback(
                    run_dir=run_dir,
                    worktree=worktree,
                    contract=contract,
                    instance=instance,
                    iteration=full_iterations,
                    name="full_run",
                )
                used_fallback = used_fallback or full_fallback
                steps.append(full)

    # All gates must have run and passed, and the sequence must have reached the
    # end (or the dedicated size guard). Counting the steps makes "a gate was
    # skipped" a failure too, which matters because the sequence short-circuits.
    reached_end = bool(steps) and steps[-1].name in {"full_run", "instance_size"}
    passed = reached_end and all(step.passed for step in steps)
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
        # Honest reporting: a run that only passed on the CPU retry is not the
        # same as one that passed on the GPU, and the report must say so.
        "device_fallback": used_fallback,
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

    # Every finding from the static checks, not just the failing ones, so the revise
    # agent can fix style/naming/correspondence drift in the same round instead of
    # discovering it one failure at a time.
    if correspondence is not None:
        payload["findings"] = {
            "issues": list(correspondence.issues),
            "warnings": list(correspondence.warnings),
            "checked": correspondence.checked,
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
        "correspondence": (
            "The deliverable's shape is wrong. Every file adapted from POMO must be named "
            "`{problem}<Role>.py` and start with `# baseline-correspondence: <pomo path>`; "
            "no `.py` file may exist that `generated.correspondence` does not list; the "
            "training entry point's `model_load` must keep `path`/`epoch` commented out; "
            "and the trainer/tester parameters must keep POMO's own values."
        ),
        "missing_helper": (
            "The generated package cannot import itself: a helper file was not carried "
            "over from the baseline (the logging/plotting `utils` module is the usual "
            "one), or a third-party import is not one the baseline uses. Copy the "
            "helper into the generated directory, or bootstrap sys.path so "
            "`pomo.POMO.utils.utils` resolves."
        ),
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

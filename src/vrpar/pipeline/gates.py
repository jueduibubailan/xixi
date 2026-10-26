"""Stage gates: what "this stage actually succeeded" means.

A gate is a pure predicate over the run directory. Keeping gates separate from
stages means the orchestrator can refuse to advance -- or re-enter a stage --
without any knowledge of prompts or LLMs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vrpar.run.worktree import changed_since_snapshot, untouched_outputs

ANALYSIS_REL = "artifacts/analysis/analysis.json"
# Fields an analysis payload must have for downstream stages to be able to work.
REQUIRED_ANALYSIS_FIELDS = (
    "problem_type",
    "constraints",
    "masking_rules",
    "state_variables",
    "feasibility_checklist",
    "objective",
    "pomo_modification_plan",
    # `run_plan` stays required: it is the work list the code generation stage follows, even
    # though nothing checks the code against it any more.
    "run_plan",
)

GENERATED_REQUIRED_FILES = ("run.py",)

# Files the code-generation prompt asks for. Missing ones are warnings, not
# blockers: the contract only needs the entry program to be runnable. Keeping them
# as warnings lets round 1 succeed with a partial package and pushes the gaps into
# the revise round instead of hard-failing.
GENERATED_EXPECTED_FILES: tuple[str, ...] = ()


def expected_generated_files(problem: Any | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(required, expected)`` file *names* for the generated package.

    Falls back to the engine defaults (``run.py`` required, nothing else) when no
    problem plugin is loaded, so the gate stays usable in isolation.
    """
    if problem is None:
        return GENERATED_REQUIRED_FILES, GENERATED_EXPECTED_FILES
    generated_dir = str((problem.spec.get("generated") or {}).get("dir") or "artifacts/generated").rstrip("/")
    prefix = generated_dir + "/"

    def _names(paths: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        names = []
        for raw in paths:
            text = str(raw)
            names.append(text[len(prefix):] if text.startswith(prefix) else text)
        return tuple(names)

    required = _names(problem.required_paths)
    expected = tuple(name for name in _names(problem.generated_paths) if name not in required)
    return required or GENERATED_REQUIRED_FILES, expected


@dataclass
class GateResult:
    name: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.name,
            "passed": self.passed,
            "issues": self.issues,
            "warnings": self.warnings,
        }

    def describe(self) -> str:
        if self.passed and not self.warnings:
            return f"{self.name}: ok"
        if self.passed:
            return f"{self.name}: ok ({len(self.warnings)} warning(s))"
        return f"{self.name}: FAILED - " + "; ".join(self.issues[:3])


# --------------------------------------------------------------------------- #
# S1
# --------------------------------------------------------------------------- #


def _artifact_roots(run_dir: Path, worktree: Path | None = None) -> list[Path]:
    """Where a declared artifact may live, in priority order.

    Three locations are legitimate: the agent's writable output in its worktree,
    the read-only inputs staged under ``data/``, and the canonical run directory.
    """
    roots: list[Path] = []
    if worktree:
        roots.extend([Path(worktree), Path(worktree) / "data"])
    roots.append(Path(run_dir))
    return roots


def changed_this_round(
    worktree: Path | None,
    relatives: list[str],
    baseline: dict[str, str] | None,
) -> list[str]:
    """Declared output groups whose contents differ from the pre-turn snapshot.

    The comparison is against the content snapshot taken just before the agent ran
    (`vrpar.run.worktree.write_baseline`), not against timestamps: a stage worktree
    is *reused* across runs and seeding preserves mtimes through ``copy2``, so a
    modification-time check would see a stale artifact as fresh.
    """
    if worktree is None or not baseline:
        return []
    return changed_since_snapshot(Path(worktree), list(relatives), baseline)


def require_produced(
    worktree: Path | None,
    relatives: list[str],
    baseline: dict[str, str] | None,
    issues: list[str],
    describe: Any | None = None,
) -> None:
    """Fail the stage when *no* declared output was written this attempt.

    The rule is deliberately about the whole set rather than each file. A stage may
    legitimately update only one of its declared outputs (a revision round that
    patches the entry program and leaves the rest alone), so requiring every file
    to move would reject honest work. But if *nothing* moved, the agent produced
    nothing -- and because the worktree is reused, the previous run's artifacts are
    still sitting there looking complete.

    ``describe`` (optional) is called with each unchanged output and should return a
    short detail, so the failure says *what* was found.
    """
    if worktree is None or not baseline or not relatives:
        return
    if changed_since_snapshot(Path(worktree), list(relatives), baseline):
        return  # something was written; the field-level checks take it from here
    found = untouched_outputs(Path(worktree), list(relatives), baseline)
    detail = ""
    if describe is not None:
        parts = []
        for relative in found:
            path = Path(worktree) / relative
            if path.exists():
                try:
                    parts.append(f"{relative}: {describe(path)}")
                except Exception:  # detail is a nicety; never fail because of it
                    parts.append(relative)
        if parts:
            detail = " (left over: " + "; ".join(parts) + ")"
    issues.append(
        "none of the declared outputs"
        f" ({', '.join(str(item) for item in relatives)}) was written by this attempt"
        f"{detail}; the agent produced nothing"
    )


def describe_artifact(path: Path) -> str:
    """Short summary of an artifact, for diagnostics."""
    import time as _time

    path = Path(path)
    if path.is_file():
        return f"{path.stat().st_size} B, mtime {_time.strftime('%H:%M:%S', _time.localtime(path.stat().st_mtime))}"
    if path.is_dir():
        files = [item for item in path.rglob("*") if item.is_file()]
        if not files:
            return "empty"
        newest = max(item.stat().st_mtime for item in files)
        return (
            f"{len(files)} file(s), newest mtime "
            f"{_time.strftime('%H:%M:%S', _time.localtime(newest))}"
        )
    return "missing"


def read_analysis(run_dir: Path, worktree: Path | None = None) -> dict[str, Any] | None:
    for base in _artifact_roots(run_dir, worktree):
        path = base / ANALYSIS_REL
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
    return None


def gate_analysis(
    run_dir: Path,
    worktree: Path | None = None,
    baseline: dict[str, str] | None = None,
) -> GateResult:
    """The analysis must be machine-usable, and produced by *this* attempt."""
    analysis = read_analysis(run_dir, worktree)
    if analysis is None:
        return GateResult("analysis", False, [f"{ANALYSIS_REL} missing or not valid JSON"])

    issues: list[str] = []
    warnings: list[str] = []
    require_produced(worktree, [ANALYSIS_REL], baseline, issues, describe=describe_artifact)

    for field_name in REQUIRED_ANALYSIS_FIELDS:
        value = analysis.get(field_name)
        if value in (None, "", [], {}):
            issues.append(f"analysis.{field_name} is empty")

    # The analysis reads no knowledge base and no retrieved records, so there is nothing
    # to reconcile here: its only inputs are the problem description and the instances.

    rules = analysis.get("masking_rules") or []
    if isinstance(rules, list):
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                issues.append(f"masking_rules[{index}] is not an object")
                continue
            for key in ("name", "condition"):
                if not rule.get(key):
                    issues.append(f"masking_rules[{index}] is missing '{key}'")
    elif rules:
        issues.append("masking_rules must be a list")

    checklist = analysis.get("feasibility_checklist") or []
    if not isinstance(checklist, list) or len(checklist) < 2:
        issues.append("feasibility_checklist must list at least two concrete checks")

    plan = analysis.get("pomo_modification_plan") or []
    if isinstance(plan, list):
        for index, item in enumerate(plan):
            if not isinstance(item, dict) or not (item.get("file") or item.get("symbol")):
                issues.append(f"pomo_modification_plan[{index}] must name a file or symbol")
    else:
        issues.append("pomo_modification_plan must be a list")

    compatibility = analysis.get("checkpoint_compatibility") or {}
    if not compatibility:
        warnings.append("checkpoint_compatibility is missing; checkpoint reuse is unverified")
    elif int(compatibility.get("encoder_input_dim_must_stay") or 0) != 2:
        warnings.append("checkpoint_compatibility.encoder_input_dim_must_stay should be 2")

    contract = analysis.get("output_contract") or []
    if len(contract) < 2:
        warnings.append("output_contract should repeat both acceptance lines")

    return GateResult("analysis", not issues, issues, warnings, {"analysis": analysis})


# --------------------------------------------------------------------------- #
# S2 / S4
# --------------------------------------------------------------------------- #


def gate_generated(
    run_dir: Path,
    worktree: Path | None = None,
    problem: Any | None = None,
    baseline: dict[str, str] | None = None,
) -> GateResult:
    """The generated package must contain its declared entry program."""
    generated_dir = str((problem.spec.get("generated") or {}).get("dir") or "artifacts/generated") if problem else "artifacts/generated"
    target = None
    for base in _artifact_roots(run_dir, worktree):
        candidate = base / generated_dir
        if candidate.is_dir() and any(candidate.iterdir()):
            target = candidate
            break
    if target is None:
        return GateResult("generated", False, [f"{generated_dir}/ does not exist or is empty"])

    required_files, expected_files = expected_generated_files(problem)
    entry_program = problem.program if problem else "run.py"

    issues: list[str] = []
    warnings: list[str] = []
    require_produced(worktree, [generated_dir], baseline, issues, describe=describe_artifact)
    present = {p.name for p in target.iterdir() if p.is_file()}
    for required in required_files:
        if required not in present:
            issues.append(f"missing required file: {generated_dir}/{required}")
    for expected in expected_files:
        if expected not in present:
            warnings.append(f"missing expected file: {generated_dir}/{expected}")

    python_files = sorted(str(p.relative_to(target)) for p in target.rglob("*.py"))
    if not python_files:
        issues.append("no python files were written")
    if entry_program not in present and f"{entry_program}.py" not in python_files:
        issues.append(f"the entry program {entry_program!r} does not exist; there is nothing to run")

    return GateResult(
        "generated",
        not issues,
        issues,
        warnings,
        {"files": sorted(present), "python_files": python_files, "dir": str(target)},
    )


# --------------------------------------------------------------------------- #
# S3 / S5
# --------------------------------------------------------------------------- #


def gate_run_report(report: dict[str, Any] | None) -> GateResult:
    if not report:
        return GateResult("run_report", False, ["artifacts/runs/s3_run_report.json missing"])
    if report.get("passed"):
        return GateResult("run_report", True, [], [], {"best_cost": report.get("best_cost")})
    issues = [
        f"failed at step '{report.get('failed_step')}'",
        f"error class: {report.get('error_class') or 'unclassified'}",
    ]
    message = (report.get("error_message") or "").strip()
    if message:
        issues.append(message.splitlines()[0][:300])
    return GateResult("run_report", False, issues)


def gate_feasibility(payload: dict[str, Any] | None) -> GateResult:
    """Record the grader's verdict. It no longer decides whether the run succeeded.

    The run's success criterion is "no runtime errors **and** the code follows the plan".
    Whether the produced solution is *good* is still measured -- `s5_feasibility.json` and
    the grader score go into the report -- but a solver that runs cleanly and follows its
    plan still delivers its code, which is what this pipeline promises.
    """
    if not payload:
        return GateResult(
            "feasibility",
            True,
            [],
            ["artifacts/runs/s5_feasibility.json missing; the run is unaffected"],
        )
    if payload.get("skipped"):
        return GateResult("feasibility", True, [], ["feasibility was skipped (no solution dump)"])

    feasible = bool(payload.get("feasible"))
    violations = [str(v) for v in (payload.get("violations") or [])][:10]
    if feasible:
        return GateResult("feasibility", True, [], [], payload)
    warnings = ["grader reports the solution infeasible; recorded, not blocking:"]
    warnings += violations
    return GateResult("feasibility", True, [], warnings, payload)

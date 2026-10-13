"""Stage gates: what "this stage actually succeeded" means.

A gate is a pure predicate over the run directory. Keeping gates separate from
stages means the orchestrator can refuse to advance -- or re-enter a stage --
without any knowledge of prompts or LLMs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cvrp.records.store import toc_missing
from cvrp.run.worktree import changed_since_snapshot, untouched_outputs

ANALYSIS_REL = "artifacts/analysis/analysis.json"
#: What the retrieval stage must leave behind (run-relative).
RETRIEVAL_REL = "artifacts/records/retrieval.json"
#: What the update stage must leave behind. It writes a directory, so the gate checks
#: the directory rather than one path.
UPDATE_REL = "records/main"

# Fields an analysis payload must have for downstream stages to be able to work.
REQUIRED_ANALYSIS_FIELDS = (
    "problem_type",
    "constraints",
    "masking_rules",
    "state_variables",
    "feasibility_checklist",
    "objective",
    "pomo_modification_plan",
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
    (`cvrp.run.worktree.write_baseline`), not against timestamps: a stage worktree
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


def _advise_related_knowledge(analysis: dict[str, Any], warnings: list[str]) -> None:
    """Comment on `related_knowledge` without ever failing on it.

    The retrieval stage hands the analysis a selection of the knowledge base, and that
    selection is *reference material*: it shapes wording, ordering, trade-offs and which
    traps get avoided. A first version of this gate required every `used` entry to be
    findable in `run_plan` as literal text -- and killed a complete, schema-valid analysis
    because the agent wrote `run_plan: declare no dependencies and run no installs` where
    the plan said `Declare no dependencies and run no installs:`. One colon. The turn cost
    460 seconds and it was thrown away.

    That is the lesson: a gate may check that something was *said*, never that it was said
    in the expected words. Knowledge can legitimately change a decision without adding a
    plan item, so "did the knowledge reach the plan?" is not even the right question.

    What remains is visibility, not enforcement. A missing or thin block becomes a warning
    that the report shows and a human can act on; a run is never failed for it.
    """
    block = analysis.get("related_knowledge")
    if block is None:
        return
    if not isinstance(block, dict):
        warnings.append("analysis.related_knowledge is not an object; it is ignored")
        return

    used = block.get("used")
    if used is not None and not isinstance(used, list):
        warnings.append("analysis.related_knowledge.used is not a list; it is ignored")
        used = None
    if isinstance(used, list):
        for index, item in enumerate(used):
            if not isinstance(item, dict):
                warnings.append(f"related_knowledge.used[{index}] is not an object")
                continue
            if not str(item.get("source") or "").strip():
                warnings.append(
                    f"related_knowledge.used[{index}] names no `source`, so the claim cannot "
                    "be traced back to a record"
                )
            if not str(item.get("influence") or item.get("action") or "").strip():
                warnings.append(
                    f"related_knowledge.used[{index}] names no `influence`: say what the "
                    "record changed, or move it to `rejected`"
                )

    rejected = block.get("rejected")
    if isinstance(rejected, list):
        for index, item in enumerate(rejected):
            if not isinstance(item, dict):
                warnings.append(f"related_knowledge.rejected[{index}] is not an object")
                continue
            if not str(item.get("reason") or "").strip():
                warnings.append(
                    f"related_knowledge.rejected[{index}] gives no `reason`; say why the "
                    "record does not apply"
                )

    sources_used = {
        str(item.get("source") or "")
        for item in (used or [])
        if isinstance(item, dict)
    }
    for item in rejected or []:
        if isinstance(item, dict) and str(item.get("source") or "") in sources_used:
            warnings.append(
                f"related_knowledge lists {item.get('source')!r} as both used and rejected"
            )


def _warn_if_oversized(analysis: dict[str, Any], warnings: list[str]) -> None:
    """Flag an analysis payload that has grown past usefulness.

    The contract is a plan, not an essay: a real n=100 payload came in at 38 KB, most of it
    prose restating the task description. Downstream stages read this file, so size is a
    cost paid twice (context and attention). This is a warning, never a failure -- the
    content may be excellent, and nobody should lose a run to a byte count.
    """
    import json as _json

    try:
        size = len(_json.dumps(analysis, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return
    if size > 100_000:
        warnings.append(
            f"analysis payload is {size // 1024} KB; consider trimming prose that repeats "
            "the task description -- downstream stages read this file too"
        )


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def gate_retrieval(
    run_dir: Path,
    worktree: Path | None = None,
    baseline: dict[str, str] | None = None,
) -> GateResult:
    """The retrieval stage must leave behind a *stated* result, produced by this attempt.

    Two outcomes, both fine, and only one thing is ever wrong: a silence.

    * found something -> `selected` is non-empty and each entry says why it was picked;
    * found nothing   -> `{"empty": true, "note": ...}`.

    This stage never decides whether the run continues: an empty knowledge base is the
    normal state of a new problem, and the pipeline proceeds to analysis either way. The
    gate exists so that "I looked and there was nothing" is recorded as a fact rather than
    being indistinguishable from "the stage wrote no output".
    """
    issues: list[str] = []
    require_produced(worktree, [RETRIEVAL_REL], baseline, issues, describe=describe_artifact)

    payload = None
    for base in _artifact_roots(run_dir, worktree):
        candidate = base / RETRIEVAL_REL
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                issues.append(f"{RETRIEVAL_REL} is not valid JSON")
                payload = None
            break
    if payload is None:
        if not issues:
            issues.append(f"{RETRIEVAL_REL} missing")
        return GateResult("retrieval", False, issues)

    if payload.get("empty"):
        if not str(payload.get("note") or "").strip():
            issues.append(
                "retrieval reported an empty knowledge base without a `note`; saying that "
                "there was nothing to reuse is fine, leaving it silent is not"
            )
    else:
        selected = payload.get("selected")
        if not isinstance(selected, list) or not selected:
            issues.append(
                "retrieval.selected is empty, but the knowledge base is not: list what you "
                "selected (or set \"empty\": true if there genuinely was nothing relevant)"
            )
        else:
            for index, item in enumerate(selected):
                if not isinstance(item, dict):
                    issues.append(f"selected[{index}] is not an object")
                    continue
                if not str(item.get("source") or "").strip():
                    issues.append(f"selected[{index}] has no `source`")
    return GateResult("retrieval", not issues, issues)


def gate_update(
    run_dir: Path,
    worktree: Path | None = None,
    baseline: dict[str, str] | None = None,
) -> GateResult:
    """The update stage must leave at least one non-empty knowledge file behind.

    It also reports a missing `## Contents`, because a knowledge base is only usable if a
    reader can see what a record holds *before* loading it — and a record with no table of
    contents is the one nobody opens. That is a **warning**, not a failure: the content may
    be excellent, and losing a run to a missing heading would repeat the mistake this
    pipeline already made once with a stricter gate.
    """
    issues: list[str] = []
    warnings: list[str] = []
    require_produced(worktree, [UPDATE_REL], baseline, issues, describe=describe_artifact)
    if worktree is not None:
        root = Path(worktree) / "records" / "main"
        files = sorted(root.rglob("*.md")) if root.is_dir() else []
        non_empty = [path for path in files if path.stat().st_size > 0]
        if not files:
            issues.append(f"no knowledge files under {root}")
        elif not non_empty:
            issues.append(f"every knowledge file under {root} is empty")
        for path in non_empty:
            text = path.read_text(encoding="utf-8", errors="replace")
            if toc_missing(text):
                warnings.append(
                    f"{path.name} has no usable `## Contents` block: a reader cannot tell "
                    "what is inside without loading the whole file"
                )
    return GateResult("update", not issues, issues, warnings)


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

    # Advisory only: the retrieved records are reference material for this stage, so how
    # (or whether) the analysis accounts for them never decides the gate.
    _advise_related_knowledge(analysis, warnings)
    _warn_if_oversized(analysis, warnings)

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
    if not payload:
        return GateResult("feasibility", False, ["artifacts/runs/s5_feasibility.json missing"])
    if payload.get("skipped"):
        return GateResult("feasibility", True, [], ["feasibility was skipped (no solution dump)"])
    feasible = bool(payload.get("feasible"))
    violations = payload.get("violations") or []
    return GateResult(
        "feasibility",
        feasible,
        [str(v) for v in violations][:10],
        [] if payload.get("cost_matches_claim", True) is not False else ["claimed cost != recomputed cost"],
        payload,
    )

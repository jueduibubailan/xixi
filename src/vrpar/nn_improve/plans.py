"""The nn improve ledger: plan schema, state machine, labels, base assignment.

The pipeline owns every status/label/score transition; the nn_improve agent only
proposes content (``plans/<id>.md``).  Keeping the transitions here, as pure
functions over a dict, is what makes requirement 2.4 ("the first unexecuted plan
becomes running") deterministic instead of a prompt instruction.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml

LEDGER_REL = "artifacts/nn_improve/plans.json"
PLANS_DIR_REL = "artifacts/nn_improve/plans"

PENDING = "pending"
RUNNING = "running"
EXECUTED = "executed"
COMPLETED = "completed"
FAILED = "failed"

STATUSES = (PENDING, RUNNING, EXECUTED, COMPLETED, FAILED)
KINDS = ("strategy", "mutation")


class PlanError(ValueError):
    """A proposed batch or a transition violates the ledger contract."""


# --------------------------------------------------------------------------- #
# direction / comparison
# --------------------------------------------------------------------------- #


def better(new: float | None, ref: float | None, direction: str = "minimize") -> bool:
    """Is ``new`` a better score than ``ref`` under ``direction``?"""
    if new is None or ref is None:
        return False
    return new < ref if direction == "minimize" else new > ref


def improved(
    new: float | None,
    ref: float | None,
    direction: str = "minimize",
    min_delta: float = 0.0,
) -> bool:
    """``better`` with a minimum margin, so sampling noise does not flip a label."""
    if not better(new, ref, direction):
        return False
    if min_delta <= 0:
        return True
    gap = (ref - new) if direction == "minimize" else (new - ref)
    return gap > min_delta


# --------------------------------------------------------------------------- #
# ledger lifecycle
# --------------------------------------------------------------------------- #


def empty_ledger(
    *,
    direction: str = "minimize",
    initial_score: float | None = None,
    initial_search_score: float | None = None,
) -> dict[str, Any]:
    """A ledger before any batch has run.

    ``best_score`` starts at the initial code's search-protocol score: no plan has
    won yet, so the incumbent is the initial code itself.
    """
    return {
        "version": 1,
        "direction": direction,
        "initial_score": initial_score,
        "initial_search_score": initial_search_score,
        "best_score": initial_search_score,
        "best_plan_id": None,
        "final_score": None,
        "batches": [],
    }


def load_ledger(run_dir: Path) -> dict[str, Any] | None:
    from vrpar.run.run_dir import read_json

    return read_json(Path(run_dir) / LEDGER_REL, default=None)


def save_ledger(run_dir: Path, ledger: dict[str, Any]) -> Path:
    from vrpar.run.run_dir import write_json

    return write_json(Path(run_dir) / LEDGER_REL, ledger)


def plan_file_path(root: Path, plan_id: str) -> Path:
    return Path(root) / PLANS_DIR_REL / f"{plan_id}.md"


def read_plan_files(root: Path, batch: int) -> tuple[dict[str, Any], list[str]]:
    """Read ``plans/b{batch}-*.md`` into the payload ``validate_batch`` expects."""
    from vrpar.run.run_dir import read_text

    directory = Path(root) / PLANS_DIR_REL
    payload: dict[str, Any] = {"batch": int(batch), "plans": []}
    if not directory.is_dir():
        return payload, [f"{PLANS_DIR_REL}/ is missing"]
    issues: list[str] = []
    items: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.glob(f"b{int(batch)}-*.md")):
        where = f"{PLANS_DIR_REL}/{path.name}"
        data = _front_matter(read_text(path))
        if not isinstance(data, dict):
            issues.append(f"{where}: front matter is missing or not valid YAML")
            continue
        if str(data.get("id") or "") != path.stem:
            issues.append(f"{where}: id must equal the file stem ({path.stem!r})")
            continue
        if int(data.get("batch", -1)) != int(batch):
            issues.append(f"{where}: batch must be {int(batch)}")
        for key in ("kind", "title", "target", "change", "rationale"):
            if not str(data.get(key) or "").strip():
                issues.append(f"{where}: '{key}' is required")
        items.append(
            (
                path.name,
                {
                    "id": path.stem,
                    "kind": str(data.get("kind") or "").strip().lower(),
                    "title": str(data.get("title") or "").strip(),
                    "target": str(data.get("target") or "").strip(),
                    "change": str(data.get("change") or "").strip(),
                    "rationale": str(data.get("rationale") or "").strip(),
                    "expected": str(data.get("expected") or "").strip(),
                    "source_plan_ids": data.get("source_plan_ids") or [],
                },
            )
        )
    # strategies by id first, then mutations by id: that is the slot order.
    items.sort(key=lambda pair: (pair[1]["kind"] != "strategy", pair[0]))
    payload["plans"] = [item for _, item in items]
    return payload, issues


def _front_matter(text: str) -> Any:
    """The YAML block between the first two ``---`` lines, or ``None``."""
    stripped = str(text or "").lstrip("\ufeff")
    if not stripped.startswith("---"):
        return None
    end = stripped.find("\n---", 3)
    if end < 0:
        return None
    try:
        return yaml.safe_load(stripped[3:end])
    except yaml.YAMLError:
        return None


# --------------------------------------------------------------------------- #
# batches and plans
# --------------------------------------------------------------------------- #


def start_batch(
    ledger: dict[str, Any],
    batch: int,
    *,
    protocol: str,
    entry_best: float | None,
) -> dict[str, Any]:
    record = {
        "batch": int(batch),
        "protocol": protocol,
        "entry_best": entry_best,
        "plans": [],
    }
    ledger.setdefault("batches", []).append(record)
    return record


def batch_record(ledger: dict[str, Any], batch: int) -> dict[str, Any] | None:
    for record in ledger.get("batches", []):
        if int(record.get("batch", -1)) == int(batch):
            return record
    return None


def plans_of(ledger: dict[str, Any], batch: int | None = None) -> list[dict[str, Any]]:
    records = ledger.get("batches", []) if batch is None else [batch_record(ledger, batch) or {}]
    out: list[dict[str, Any]] = []
    for record in records:
        out.extend(record.get("plans") or [])
    return out


def plan_by_id(ledger: dict[str, Any], plan_id: str) -> dict[str, Any] | None:
    for plan in plans_of(ledger):
        if plan.get("id") == plan_id:
            return plan
    return None


def winners(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """label=1 plans across every finished batch, best score first."""
    direction = ledger.get("direction", "minimize")
    picked = [plan for plan in plans_of(ledger) if plan.get("label") == 1]
    return sorted(picked, key=lambda p: (p.get("score") is None, p.get("score") or 0.0), reverse=(direction != "minimize"))


def batch_winners(ledger: dict[str, Any], batch: int) -> list[dict[str, Any]]:
    direction = ledger.get("direction", "minimize")
    picked = [plan for plan in plans_of(ledger, batch) if plan.get("label") == 1]
    return sorted(picked, key=lambda p: (p.get("score") is None, p.get("score") or 0.0), reverse=(direction != "minimize"))


def assign_bases(
    batch: int,
    winner_plans: list[dict[str, Any]],
    count: int,
    pool: int = 10,
) -> list[str | None]:
    """Which base each slot of ``batch`` builds on (requirement R5-2).

    Batch 1 always starts from the initial code (``None``).  Later batches order
    the winners best-first (``winners`` already does) and cycle them across the
    slots, so the strategies land on the best ``pool`` nodes in turn.
    """
    if batch <= 1:
        return [None] * int(count)
    ordered = list(winner_plans)[: max(1, int(pool))]
    if not ordered:
        return []
    return [ordered[index % len(ordered)]["id"] for index in range(int(count))]


# --------------------------------------------------------------------------- #
# validation of an agent-proposed batch
# --------------------------------------------------------------------------- #


def _bare_value(change: str) -> bool:
    """A change with no word in it is a parameter value, not a method change."""
    return not re.search(r"[A-Za-z\u4e00-\u9fff]", str(change or ""))


def _forbidden(text: str, deny: Iterable[str]) -> str:
    """The first denied name mentioned in ``text`` (``""`` when clean)."""
    haystack = str(text or "").lower()
    for pattern in deny or []:
        tail = str(pattern).split(".")[-1].strip().lower()
        if tail and tail in haystack:
            return tail
    return ""


def validate_batch(
    raw: Any,
    *,
    batch: int,
    assignments: list[str | None],
    strategies_per_batch: int,
    mutations_per_batch: int,
    previous_winners: list[dict[str, Any]],
    deny: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise an agent batch into ledger plans, or explain every defect.

    Returns ``(plans, issues)``.  A plan is only accepted when ``issues`` is empty;
    the caller decides whether to fail the run or ask for one more batch.
    """
    issues: list[str] = []
    expected = int(strategies_per_batch) + int(mutations_per_batch)
    if not isinstance(raw, dict):
        return [], ["a batch must be a JSON object"]
    proposed = raw.get("plans")
    if not isinstance(proposed, list):
        return [], ["a batch has no 'plans' list"]
    if len(proposed) != expected:
        issues.append(f"expected {expected} plans (10 strategy + 2 mutation), got {len(proposed)}")
    if len(assignments) != len(proposed):
        # The assignment is derived from the expected count; a mismatch means the
        # agent invented a different number of slots.
        assignments = list(assignments[: len(proposed)]) + [None] * max(0, len(proposed) - len(assignments))

    winner_ids = {str(plan.get("id")) for plan in previous_winners}
    seen: set[str] = set()
    normalised: list[dict[str, Any]] = []
    for index, item in enumerate(proposed):
        where = f"plans[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{where}: must be an object")
            continue
        plan_id = str(item.get("id") or f"b{batch}-p{index + 1:02d}")
        if plan_id in seen:
            issues.append(f"{where}: duplicate id {plan_id!r}")
        seen.add(plan_id)
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in KINDS:
            issues.append(f"{where}: kind must be strategy or mutation, got {kind!r}")
        if index < int(strategies_per_batch) and kind == "mutation":
            issues.append(f"{where}: the first {strategies_per_batch} slots must be strategy plans")
        if index >= int(strategies_per_batch) and kind == "strategy":
            issues.append(f"{where}: the last {mutations_per_batch} slots must be mutation plans")

        for field in ("title", "target", "rationale"):
            if not str(item.get(field) or "").strip():
                issues.append(f"{where}: '{field}' is required")

        change = str(item.get("change") or "").strip()
        if not change:
            issues.append(f"{where}: 'change' is required")
        elif _bare_value(change):
            issues.append(
                f"{where}: 'change' is a bare value; a plan changes a method, not a parameter"
            )
        else:
            banned = _forbidden(" ".join((str(item.get("title") or ""), str(item.get("target") or ""), change)), deny)
            if banned:
                issues.append(f"{where}: {banned!r} is outside the search space")

        source_ids = item.get("source_plan_ids") or []
        if not isinstance(source_ids, list):
            issues.append(f"{where}: source_plan_ids must be a list")
            source_ids = []
        for source in source_ids:
            if str(source) not in winner_ids:
                issues.append(f"{where}: source {source!r} is not a label=1 plan of an earlier batch")

        normalised.append(
            {
                "id": plan_id,
                "kind": kind,
                "title": str(item.get("title") or "").strip(),
                "target": str(item.get("target") or "").strip(),
                "change": change,
                "rationale": str(item.get("rationale") or "").strip(),
                "expected": str(item.get("expected") or "").strip(),
                "source_plan_ids": [str(source) for source in source_ids],
                "base_plan_id": assignments[index] if index < len(assignments) else None,
                "status": PENDING,
                "label": None,
                "score": None,
                "protocol": None,
                "delta_initial": None,
                "delta_base": None,
                "delta_best": None,
                "execution": {"report_dir": None, "repairs": 0},
            }
        )
    return normalised, issues


# --------------------------------------------------------------------------- #
# transitions
# --------------------------------------------------------------------------- #


def _set_status(plan: dict[str, Any], status: str) -> None:
    if status not in STATUSES:
        raise PlanError(f"unknown status: {status}")
    plan["status"] = status


def next_pending(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """The first plan still waiting; ``None`` when the batch is exhausted."""
    for plan in plans_of(ledger):
        if plan.get("status") == PENDING:
            return plan
    return None


def mark_running(plan: dict[str, Any], *, report_dir: str) -> None:
    if plan.get("status") != PENDING:
        raise PlanError(f"plan {plan.get('id')} is {plan.get('status')}, cannot start it")
    _set_status(plan, RUNNING)
    plan.setdefault("execution", {})["report_dir"] = report_dir
    plan["execution"]["started_at"] = _now()


def mark_result(
    ledger: dict[str, Any],
    plan: dict[str, Any],
    *,
    passed: bool,
    evaluated: bool,
    score: float | None,
    protocol: str,
    report_dir: str,
    repairs: int = 0,
    error: str = "",
) -> None:
    """Finish a plan and stamp its label.

    ``evaluated`` separates "the code ran and failed" (executed) from "the agent
    never produced a runnable package" (failed); both get label 0.
    """
    direction = ledger.get("direction", "minimize")
    threshold = ledger.get("initial_search_score")
    min_delta = float(ledger.get("min_delta", 0.0) or 0.0)
    if passed:
        _set_status(plan, COMPLETED)
        plan["label"] = 1 if improved(score, threshold, direction, min_delta) else 0
    else:
        _set_status(plan, EXECUTED if evaluated else FAILED)
        plan["label"] = 0
    plan["score"] = score
    plan["protocol"] = protocol
    plan["error"] = error
    plan["execution"]["report_dir"] = report_dir
    plan["execution"]["repairs"] = int(repairs)
    plan["execution"]["finished_at"] = _now()
    base_score = ledger.get("best_score")
    plan["delta_initial"] = _delta(score, threshold)
    plan["delta_best"] = _delta(score, base_score)


def _delta(score: float | None, ref: float | None) -> float | None:
    if score is None or ref is None:
        return None
    return round(float(score) - float(ref), 4)


def _now() -> float:
    import time

    return time.time()


# --------------------------------------------------------------------------- #
# summary for state.json / the dashboard
# --------------------------------------------------------------------------- #


def summarise(ledger: dict[str, Any]) -> dict[str, Any]:
    plans = plans_of(ledger)
    counts = {status: 0 for status in STATUSES}
    for plan in plans:
        counts[plan.get("status", PENDING)] = counts.get(plan.get("status", PENDING), 0) + 1
    return {
        "total": len(plans),
        "pending": counts.get(PENDING, 0),
        "running": counts.get(RUNNING, 0),
        "executed": counts.get(EXECUTED, 0),
        "completed": counts.get(COMPLETED, 0),
        "failed": counts.get(FAILED, 0),
        "winners": sum(1 for plan in plans if plan.get("label") == 1),
    }


__all__ = [
    "COMPLETED",
    "EXECUTED",
    "FAILED",
    "KINDS",
    "LEDGER_REL",
    "PENDING",
    "PLANS_DIR_REL",
    "PlanError",
    "RUNNING",
    "STATUSES",
    "assign_bases",
    "batch_record",
    "batch_winners",
    "better",
    "empty_ledger",
    "improved",
    "load_ledger",
    "mark_result",
    "mark_running",
    "next_pending",
    "plan_by_id",
    "plan_file_path",
    "plans_of",
    "read_plan_files",
    "save_ledger",
    "start_batch",
    "summarise",
    "validate_batch",
    "winners",
]

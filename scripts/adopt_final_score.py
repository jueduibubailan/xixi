"""Adopt a final acceptance that ran fine but whose score the gate could not read.

Context
-------
The acceptance gate scores ``artifacts/runs/final/{test.stdout.txt, test.exitcode,
test_train.exitcode}``.  Those files are written by the ``code_error`` stage agent
through a shell redirect, and on Windows PowerShell 5.1 ``>`` produces UTF-16LE.
The gate used to decode UTF-8 only, so a run that printed ``AUGMENTATION SCORE:``
was reported as ``the final test entry stdout has no 'AUGMENTATION SCORE:' line``.
The training is the expensive part and its artifacts are intact, so this tool
re-scores them instead of re-running the stage.

What it does
------------
1. re-runs ``gates.check_code_error`` with the current BOM-aware reader;
2. if the gate passes, records the score where ``Orchestrator._finalise_champion``
   would have on success: ``plans.json`` (``final_score`` / ``final_attempted``),
   ``state.json`` (``final_score`` and the ``code_error`` stage record), the two
   ``report.json`` copies, the champion snapshot and the exported deliverable;
3. backs up ``state.json``, ``plans.json`` and ``REPORT.md`` to ``*.pre-adopt``
   first, and refuses to adopt an acceptance that still fails.

It never trains, never calls an agent and never edits generated code.

Usage (from the project root)::

    uv run python scripts/adopt_final_score.py --run 20261115-211029 --dry-run
    uv run python scripts/adopt_final_score.py --run results/mvmoe_ovrpl_n50/20261115-211029
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from vrpar.config import load_config
from vrpar.nn_improve import plans
from vrpar.pipeline import gates
from vrpar.pipeline.orchestrator import Orchestrator
from vrpar.run.run_dir import read_json, write_json
from vrpar.run.state import DONE

FINAL_REPORT = "artifacts/runs/final"


def _find_run(raw: str) -> Path:
    """Resolve a run directory from a path, or from a bare run id under ``results/``."""
    candidate = Path(raw).expanduser()
    if (candidate / "state.json").is_file():
        return candidate.resolve()
    results = Path("results")
    if results.is_dir():
        for task_dir in sorted(path for path in results.iterdir() if path.is_dir()):
            run_dir = task_dir / raw
            if (run_dir / "state.json").is_file():
                return run_dir.resolve()
    raise SystemExit(f"not a run directory (no state.json): {raw}")


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    target = path.with_name(path.name + ".pre-adopt")
    shutil.copy2(path, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-score a completed final acceptance and adopt the champion."
    )
    parser.add_argument("--run", required=True, help="run id or run directory")
    parser.add_argument("--task", default=None, help="task.yaml (default: problems/<slug>/task.yaml)")
    parser.add_argument("--project-root", default=None, help="project root (default: two levels above the run)")
    parser.add_argument("--dry-run", action="store_true", help="report the gate result, write nothing")
    parser.add_argument(
        "--force",
        action="store_true",
        help="adopt even when the gate still fails (a parsed score is still required)",
    )
    args = parser.parse_args(argv)

    run_dir = _find_run(args.run)
    root = Path(args.project_root).expanduser().resolve() if args.project_root else run_dir.parents[2]
    slug = run_dir.parent.name
    task = Path(args.task).expanduser() if args.task else root / "problems" / slug / "task.yaml"
    if not task.is_file():
        raise SystemExit(f"task config not found: {task} (pass --task)")

    # The run's own report keeps the protocol pins it was scored with; reuse them so a
    # re-score cannot silently move the goalposts.
    previous = read_json(run_dir / FINAL_REPORT / "report.json", default={}) or {}
    pins = previous.get("pins") or None
    protocol = str(previous.get("protocol") or "acceptance")

    check = gates.check_code_error(run_dir, report_dir=FINAL_REPORT, pins=pins)
    print(f"run     : {run_dir}")
    print(f"task    : {task}")
    print(f"gate    : {'PASSED' if check.passed else 'FAILED'} - {check.describe()}")
    for issue in check.issues:
        print(f"issue   : {issue}")
    print(f"score   : {check.score}")

    if not check.passed and not args.force:
        print("\nrefusing to adopt a failed acceptance (re-run the stage, or use --force)")
        return 1
    if check.score is None:
        print("\nno score in the captured stdout; nothing to adopt")
        return 2

    ledger = plans.load_ledger(run_dir) or {}
    initial = ledger.get("initial_score")
    direction = str(ledger.get("direction") or "minimize")
    if check.passed and not plans.better(check.score, initial, direction) and not args.force:
        print(
            f"\nchampion {check.score} is not better than the initial {initial} "
            f"({direction}); the pipeline would keep the initial code"
        )
        return 3
    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    for relative in ("state.json", plans.LEDGER_REL, "REPORT.md"):
        backup = _backup(run_dir / relative)
        if backup is not None:
            print(f"backup  : {backup}")

    # Keep the ledger's task identity: RunState builds it from the config, and a run's
    # recorded task/slug must not change because the tool was pointed at it later.
    recorded = read_json(run_dir / "state.json", default={}) or {}
    orchestrator = Orchestrator(load_config(task, project_root=root, cwd=root), run_dir=run_dir)
    for key in ("task", "task_slug"):
        if recorded.get(key) is not None:
            orchestrator.state.data[key] = recorded[key]
    orchestrator.ledger = ledger

    score = check.score
    orchestrator.ledger["final_score"] = score
    orchestrator.ledger["final_attempted"] = True
    orchestrator._save_ledger()

    orchestrator.state.set_final(score)
    stage_record = orchestrator.state.stage("code_error")
    stage_record["status"] = DONE
    stage_record["message"] = f"{protocol} final: score={score}"
    stage_record["error"] = ""
    orchestrator.state.save()
    orchestrator.state.record_event(
        "final_adopted",
        score=score,
        report=FINAL_REPORT,
        note="re-scored the captured stdout with the BOM-aware reader; the stage was not re-run",
    )

    payload = gates.check_report(check, protocol=protocol, pins=pins)
    write_json(run_dir / FINAL_REPORT / "report.json", payload)
    write_json(run_dir / "artifacts" / "runs" / "code_error_report.json", payload)

    # A successful final acceptance leaves the executed package in the run directory;
    # this run fell back to the initial code, so put the executed champion back.
    executed = run_dir / FINAL_REPORT / "generated"
    orchestrator._restore_run_generated(
        executed if executed.is_dir() else orchestrator._best_dir()
    )

    mirror = orchestrator._export()
    report_path = orchestrator._write_report(
        success=True,
        reason="final acceptance re-scored from the captured stdout (gate encoding fix)",
        mirror=mirror,
    )

    print(f"\nadopted : final={score}")
    print(f"export  : {mirror}")
    print(f"report  : {report_path}")
    print("refresh the dashboard; /api/status now reports final_score")
    return 0


if __name__ == "__main__":
    sys.exit(main())

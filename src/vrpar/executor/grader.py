"""The grader: the pipeline's exit gate, in CORAL's `grader` spirit.

`vrpar start` calls :func:`grade_run` after the executor stages. It re-runs the
generated program through the acceptance contract, asks the loaded *problem
plugin* to verify the dumped solution independently, and asks the plugin to turn
that verified result into a score. The output is a `ScoreBundle`-shaped dict, and
the web UI shows exactly these numbers -- so what you see is what the pipeline
gated on.

Nothing here is variant-specific: the program name, its CLI flags, the wording of
the contract, the feasibility check and the scoring kind all come from
``problem.yaml`` / ``checks.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vrpar.executor.contract import Contract, find_instances, run_program
from vrpar.problems import ProblemPlugin

SOLUTION_FILE = "artifacts/runs/best_solution.json"


class CodeGrader:
    """Runs the generated program and scores it via the problem plugin.

    Score semantics:

    * ``0.0``  -- the acceptance contract failed (crash, missing line, timeout),
      or the solution was infeasible while ``require_feasible`` is set.
    * otherwise -- whatever ``scorer`` says. The default kind is
      ``inverse_cost`` (``scale / best_cost``), which is honest only as a relative
      scale, so the raw cost is always reported alongside it in ``details``.
    """

    name = "CodeGrader"

    def __init__(
        self,
        config: dict[str, Any],
        problem: ProblemPlugin | None = None,
        run_dir: Path | None = None,
        worktree: Path | None = None,
    ):
        self.config = config
        self.problem = problem or config.get("_problem")
        self.run_dir = Path(run_dir) if run_dir else None
        self.worktree = Path(worktree) if worktree else None
        self.contract = Contract.from_config(config)
        grader = config.get("grader") or {}
        self.args = dict(grader.get("args") or {})
        self.direction = str(
            self.args.get("direction")
            or (self.problem.direction if self.problem else None)
            or grader.get("direction")
            or "maximize"
        )
        self.iterations = int(self.args.get("iterations") or 1000)
        self.instance_size = self.args.get("instance_size")
        self.instance_size = int(self.instance_size) if self.instance_size is not None else None
        self.require_solution_dump = bool(self.args.get("require_solution_dump", True))

    # -- plumbing ---------------------------------------------------------- #

    def _data_dir(self) -> Path:
        raw = self.args.get("data_dir")
        if not raw and self.problem is not None:
            raw = self.problem.data_dir_name
        raw = raw or "instances/data"
        path = Path(str(raw))
        if not path.is_absolute():
            path = Path(self.config.get("_project_root") or ".") / path
        return path

    def _instances(self) -> list[Path]:
        pattern = self.problem.instance_glob if self.problem else "**/*.json"
        return find_instances(self._data_dir(), self.instance_size, pattern)

    # -- API --------------------------------------------------------------- #

    def grade(self, run_dir: Path | None = None, worktree: Path | None = None) -> dict[str, Any]:
        run_dir = Path(run_dir or self.run_dir or ".")
        worktree = Path(worktree or self.worktree or run_dir)
        instances = self._instances()

        if not instances:
            return self._bundle(
                score=0.0,
                feasible=False,
                best_cost=None,
                details={"error": "no instances available", "data_dir": str(self._data_dir())},
            )

        instance = instances[0]
        solution_path = run_dir / self.args.get("solution_file", SOLUTION_FILE)
        solution_path.parent.mkdir(parents=True, exist_ok=True)

        step = run_program(
            run_dir=run_dir,
            worktree=worktree,
            contract=self.contract,
            instance=instance,
            iteration=self.iterations,
            name="grader_full_run",
            solution_out=solution_path if self.contract.save_solution_flag else None,
        )

        details: dict[str, Any] = {
            "problem": self.problem.slug if self.problem else "",
            "instance": str(instance),
            "returncode": step.returncode,
            "duration_s": round(step.duration_s, 3),
            "initial_cost": step.initial_cost,
            "best_cost": step.best_cost,
            "error_class": step.error_class,
            "command": step.command,
            "stdout_tail": step.stdout[-4000:],
            "stderr_tail": step.stderr[-4000:],
        }

        if not step.passed:
            details["reason"] = step.message or "acceptance contract failed"
            return self._bundle(score=0.0, feasible=False, best_cost=step.best_cost, details=details)

        if self.problem is None:
            details["reason"] = "no problem plugin loaded; grading without verification"
            return self._bundle(
                score=0.0, feasible=False, best_cost=step.best_cost, details=details
            )

        if solution_path.is_file():
            verification = self.problem.verify(instance, solution_path)
        else:
            verification = {
                "feasible": False,
                "skipped": True,
                "violations": [],
                "note": (
                    "the program did not write a solution dump"
                    + ("" if self.require_solution_dump else " (accepted by configuration)")
                ),
            }
        details["feasibility"] = verification

        feasible = bool(verification.get("feasible"))
        if self.contract.require_feasible and not feasible:
            if verification.get("skipped") and not self.require_solution_dump:
                details["reason"] = "solution dump skipped; accepted by configuration"
                return self._bundle(
                    score=self.problem.score(step.best_cost, verification),
                    feasible=False,
                    best_cost=step.best_cost,
                    details=details,
                )
            violations = verification.get("violations") or [verification.get("note") or "unverified"]
            details["reason"] = "solution infeasible: " + "; ".join(str(v) for v in violations)
            return self._bundle(score=0.0, feasible=False, best_cost=step.best_cost, details=details)

        return self._bundle(
            score=self.problem.score(step.best_cost, verification),
            feasible=feasible,
            best_cost=step.best_cost,
            details=details,
        )

    # -- helpers ----------------------------------------------------------- #

    def _bundle(self, score: float, feasible: bool, best_cost: float | None, details: dict[str, Any]) -> dict[str, Any]:
        return {
            "grader": self.name,
            "problem": self.problem.slug if self.problem else "",
            "direction": self.direction,
            "score": float(score),
            "feasible": bool(feasible),
            "best_cost": best_cost,
            "details": details,
        }


def grade_run(
    config: dict[str, Any],
    run_dir: Path,
    worktree: Path | None = None,
    problem: ProblemPlugin | None = None,
) -> dict[str, Any]:
    """Convenience wrapper used by the orchestrator."""
    return CodeGrader(config, problem=problem, run_dir=run_dir, worktree=worktree).grade(run_dir, worktree)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from vrpar.config import load_config, task_slug
    from vrpar.run.run_dir import latest_run_dir, write_json

    parser = argparse.ArgumentParser(description="Grade the generated solver for a task")
    parser.add_argument("task", nargs="?", default="task.yaml")
    parser.add_argument("--run", default=None, help="run directory or run id (default: latest)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.task)
    results_dir = Path(config.get("workspace", {}).get("results_dir", "./results"))
    if not results_dir.is_absolute():
        results_dir = Path(config["_project_root"]) / results_dir
    run_dir = Path(args.run) if args.run else latest_run_dir(results_dir, task_slug(config))
    if run_dir is None or not Path(run_dir).is_dir():
        print("no run found; run `vrpar start task.yaml` first")
        return 2

    bundle = grade_run(config, Path(run_dir), problem=config.get("_problem"))
    write_json(Path(run_dir) / "artifacts" / "runs" / "grader_result.json", bundle)
    if args.json:
        print(json.dumps(bundle, indent=2))
    else:
        print(f"problem  : {bundle.get('problem')}")
        print(f"score    : {bundle['score']}")
        print(f"feasible : {bundle['feasible']}")
        print(f"best_cost: {bundle['best_cost']}")
        reason = (bundle.get("details") or {}).get("reason")
        if reason:
            print(f"reason   : {reason}")
    return 0 if bundle["score"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Sequential pipeline: analysis -> generation -> (test -> revise)* -> export.

Agents do the work.  The orchestrator seeds worktrees, renders instructions,
collects artifacts, checks the three mechanical conditions and exports the final
code plus the runtime POMO payload.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vrpar.config import (
    get_stage,
    loop_config,
    project_root,
    resolve_path,
    stage_names,
    task_slug,
)
from vrpar.pipeline import gates
from vrpar.pipeline.stages import run_agent_stage
from vrpar.problems import ProblemPlugin
from vrpar.run.run_dir import append_log, make_run_dir, read_json, write_json
from vrpar.run.setup import run_setup
from vrpar.run.state import DONE, FAILED, RunState
from vrpar.run.worktree import collect_artifacts, read_baseline, seed_worktree

GENERATED_REL = "artifacts/generated"


@dataclass
class StageResult:
    name: str
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.name}: ok" if self.passed else f"{self.name}: FAILED - " + "; ".join(self.issues[:3])


class Orchestrator:
    def __init__(
        self,
        config: dict[str, Any],
        results_dir: Path | None = None,
        run_id: str | None = None,
        dry_run: bool = False,
        run_dir: Path | None = None,
    ):
        self.config = config
        self.problem: ProblemPlugin = config["_problem"]
        self.root = project_root(config)
        workspace = config.get("workspace") or {}
        raw_results = results_dir or Path(str(workspace.get("results_dir") or "./results"))
        self.results_dir = raw_results if raw_results.is_absolute() else self.root / raw_results
        self.slug = task_slug(config)
        self.run_dir = Path(run_dir) if run_dir else make_run_dir(self.results_dir, self.slug, run_id)
        self.state = RunState(self.run_dir, config)
        self.log_path = self.run_dir / "logs" / "pipeline.log"
        self.final_dir = resolve_path(config, str(workspace.get("final_dir") or "./workspace/final"))
        self.dry_run = dry_run
        self.run_id = self.run_dir.name
        self.config["_run_id"] = self.run_id
        self.config.setdefault("_project_root", str(self.root))
        llm_config = dict(config.get("llm") or {})
        llm_config.update(
            {
                "_run_id": self.run_id,
                "_project_root": str(self.root),
                "_run_dir": str(self.run_dir),
            }
        )
        from vrpar.llm.base import make_backend

        self.backend = make_backend(llm_config, problem=self.problem)
        write_json(self.run_dir / "config.resolved.json", _serializable_config(config), default=str)

    def log(self, message: str) -> None:
        append_log(self.log_path, message)
        if (self.config.get("run") or {}).get("verbose", True):
            print(f"[vrpar] {message}")

    # -- preparation ------------------------------------------------------ #

    def ensure_setup(self) -> None:
        if (self.config.get("run") or {}).get("setup", True) in (False, "false", "skip"):
            self.log("setup: skipped by run.setup=false")
            return
        result = run_setup(self.config, force=bool(self.config.get("_setup_force")), log=self.log)
        for line in result.lines():
            if line.startswith("setup:"):
                self.log(line)

    # -- seeding ---------------------------------------------------------- #

    def _inputs_for(self, stage_name: str) -> list[str]:
        produced: list[str] = []
        for name in stage_names(self.config):
            if name == stage_name:
                break
            produced.extend(str(item) for item in (get_stage(self.config, name).get("produces") or []))
        return produced

    def _seed(self, stage_name: str, reset: bool = False) -> Path:
        inputs = self._inputs_for(stage_name)
        if stage_name == "code_error" and GENERATED_REL not in inputs:
            # A revised package lives in the run directory, not in a stage that
            # precedes code_error in the table; add it so round N+1 sees the fix.
            inputs.append(GENERATED_REL)
        return seed_worktree(
            run_dir=self.run_dir,
            stage_name=stage_name,
            problem=self.problem,
            baseline_dir=self.root / "baseline",
            instances_dir=None,
            artifact_inputs=inputs,
            problem_inputs=["instance_gen", "grader"],
            reset=reset,
        )

    def _collect(self, worktree: Path, produces: list[str]) -> list[str]:
        copied = collect_artifacts(self.run_dir, worktree, produces)
        for relative in copied:
            self.log(f"  artifact collected: {relative}")
        return copied

    def _materialise_deliverable(self, worktree: Path) -> None:
        target = Path(worktree) / GENERATED_REL
        staged = Path(worktree) / "data" / GENERATED_REL
        if not staged.is_dir():
            return
        if target.is_dir() and any(target.iterdir()):
            return
        target.mkdir(parents=True, exist_ok=True)
        for item in staged.rglob("*"):
            if item.is_file():
                destination = target / item.relative_to(staged)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(item.read_bytes())
        self.log("materialised artifacts/generated from the read-only staging")

    # -- stages ----------------------------------------------------------- #

    def stage_analysis(self, round_index: int = 0) -> StageResult:
        stage_config = get_stage(self.config, "analysis")
        worktree = self._seed("analysis", reset=round_index > 0)
        self.state.start_stage("analysis")
        instructions = self.state.consume_instructions("analysis")
        outcome = run_agent_stage(stage_config, self.config, self.run_dir, worktree, self.backend, instructions, round_index)
        self._collect(worktree, list(stage_config.get("produces") or []))
        plan = self.run_dir / "artifacts" / "analysis" / "plan.md"
        result = StageResult("analysis", plan.is_file(), [] if plan.is_file() else ["artifacts/analysis/plan.md is missing"])
        self.state.finish_stage(
            "analysis",
            status=DONE if result.passed else FAILED,
            message=result.describe(),
            artifacts=list(stage_config.get("produces") or []),
            error="" if result.passed else "; ".join(result.issues),
        )
        self._note_outcome(outcome)
        return result

    def stage_code_generation(self, round_index: int = 0) -> StageResult:
        stage_config = get_stage(self.config, "code_generation")
        worktree = self._seed("code_generation", reset=round_index > 0)
        self.state.start_stage("code_generation")
        instructions = self.state.consume_instructions("code_generation")
        outcome = run_agent_stage(stage_config, self.config, self.run_dir, worktree, self.backend, instructions, round_index)
        self._collect(worktree, [GENERATED_REL])
        generated = self.run_dir / GENERATED_REL
        result = StageResult("code_generation", outcome.ok, [] if outcome.ok else ["agent turn failed"])
        if not generated.is_dir() or not any(generated.iterdir()):
            self.log("code_generation: generated directory is empty; code_error will report it")
        self.state.finish_stage(
            "code_generation",
            status=DONE if result.passed else FAILED,
            message=result.describe(),
            artifacts=[GENERATED_REL],
            error="" if result.passed else "; ".join(result.issues),
        )
        self._note_outcome(outcome)
        return result

    def stage_code_error(self, round_index: int = 0) -> tuple[gates.TestCheck, dict[str, Any]]:
        stage_config = get_stage(self.config, "code_error")
        worktree = self._seed("code_error", reset=False)
        generated = worktree / GENERATED_REL
        if generated.exists():
            shutil.rmtree(generated, ignore_errors=True)
        self._materialise_deliverable(worktree)
        self.state.start_stage("code_error")
        instructions = self.state.consume_instructions("code_error")
        outcome = run_agent_stage(stage_config, self.config, self.run_dir, worktree, self.backend, instructions, round_index)
        self._collect(worktree, list(stage_config.get("produces") or []) + [GENERATED_REL])

        check = gates.check_code_error(self.run_dir)
        payload = gates.check_report(check)
        write_json(self.run_dir / "artifacts" / "runs" / "code_error_report.json", payload)
        self.state.finish_stage(
            "code_error",
            status=DONE if check.passed else FAILED,
            message=check.describe(),
            artifacts=["artifacts/runs/code_error_report.json"],
            error="" if check.passed else "; ".join(check.issues[:3]),
        )
        if check.score is not None:
            self.state.set_score(check.score, round_index)
        self.log(f"code_error: {check.describe()}")
        self._note_outcome(outcome)
        return check, payload

    def stage_code_revise(self, round_index: int, report: dict[str, Any]) -> StageResult:
        stage_config = get_stage(self.config, "code_revise")
        worktree = self._seed("code_revise", reset=False)
        generated = worktree / GENERATED_REL
        if generated.exists():
            shutil.rmtree(generated, ignore_errors=True)
        self._materialise_deliverable(worktree)
        brief = {"round": round_index, "run_report": report}
        brief_path = worktree / "artifacts" / "revisions" / f"round-{round_index}-brief.json"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(brief_path, brief)

        self.state.start_stage("code_revise")
        extra_variables = {
            "round_index": str(round_index),
            "error_message": str(report.get("issues") or "")[:6000],
        }
        instructions = self.state.consume_instructions("code_revise")
        outcome = run_agent_stage(
            stage_config, self.config, self.run_dir, worktree, self.backend, instructions, round_index,
            extra_variables=extra_variables,
        )
        self._collect(worktree, [GENERATED_REL, "artifacts/revisions"])
        note = worktree / "artifacts" / "revisions" / f"round-{round_index}.md"
        result = StageResult("code_revise", outcome.ok or note.is_file(), [] if outcome.ok else ["agent turn failed"])
        self.state.finish_stage(
            "code_revise",
            status=DONE if result.passed else FAILED,
            message=f"round {round_index}: {result.describe()}",
            artifacts=[f"artifacts/revisions/round-{round_index}.md", GENERATED_REL],
            error="" if result.passed else "; ".join(result.issues),
        )
        self._note_outcome(outcome)
        return result

    # -- run -------------------------------------------------------------- #

    def run(self) -> dict[str, Any]:
        self.log(f"run: {self.run_dir}")
        self.ensure_setup()
        self.state.record_event("run_start", run_dir=str(self.run_dir), problem=self.problem.slug)
        self.state.save()

        analysis = self.stage_analysis()
        if not analysis.passed:
            self.state.finish("failed")
            return self._finalize(success=False, reason="analysis produced no plan.md")

        generation = self.stage_code_generation()
        if not generation.passed:
            self.state.finish("failed")
            return self._finalize(success=False, reason="code generation agent failed")

        loop = loop_config(self.config)
        rounds = max(1, int(loop.get("max_rounds") or 5))
        check: gates.TestCheck | None = None
        for round_index in range(1, rounds + 1):
            self.state.set_round(round_index)
            self.log(f"--- round {round_index}/{rounds} ---")
            check, report = self.stage_code_error(round_index)
            if check.passed:
                break
            if round_index == rounds:
                self.log("revision budget exhausted")
                break
            self.stage_code_revise(round_index, report)

        if check is not None and check.passed:
            self.state.finish("completed")
            return self._finalize(success=True, reason="")
        self.state.finish("failed")
        return self._finalize(success=False, reason="no score / exit code 0")

    # -- export / report -------------------------------------------------- #

    def _finalize(self, success: bool, reason: str) -> dict[str, Any]:
        mirror = self._export() if success else None
        report_path = self._write_report(success=success, reason=reason, mirror=mirror)
        self.state.record_event("run_finish", success=success, reason=reason, report=str(report_path))
        self.log(f"report: {report_path}")
        return {
            "run_dir": str(self.run_dir),
            "success": success,
            "reason": reason,
            "report": str(report_path),
            "state": self.state.data,
        }

    def _export(self) -> Path:
        mirror = (self.final_dir / self.slug).resolve()
        if mirror.exists():
            shutil.rmtree(mirror, ignore_errors=True)
        mirror.mkdir(parents=True, exist_ok=True)
        generated = self.run_dir / GENERATED_REL
        if generated.is_dir():
            _copy_tree_filtered(generated, mirror / GENERATED_REL, exclude=["result", "__pycache__"])
        baseline = self.root / "baseline" / "pomo"
        if baseline.is_dir():
            _copy_tree_filtered(baseline, mirror / "pomo")
        self.log(f"exported final code to {mirror}")
        return mirror

    def _write_report(self, success: bool, reason: str, mirror: Path | None) -> Path:
        s = self.state.data
        lines = [
            f"# {self.problem.name} synthesis run - {s['run_id']}",
            "",
            f"- task: {s['task']}",
            f"- slug: {self.slug}",
            f"- result: **{'SUCCESS' if success else 'FAILED'}**",
            f"- reason: {reason or 'test_train + final test passed'}",
            f"- rounds used: {s.get('round')}",
            f"- score: {s.get('score')}",
            "",
            "## Stages",
            "",
            "| stage | status | attempts | message |",
            "|---|---|---|---|",
        ]
        for name, record in s["stages"].items():
            lines.append(f"| {name} | {record.get('status')} | {record.get('attempts')} | {str(record.get('message', ''))[:120]} |")
        if mirror is not None:
            lines.extend(["", f"- final export: `{mirror}`"])
        path = self.run_dir / "REPORT.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _note_outcome(self, outcome) -> None:
        for note in outcome.notes or []:
            self.log(f"  note: {note}")
        if outcome.result is not None:
            self.log(f"  agent: {outcome.result.summary(240)}")


def _copy_tree_filtered(source: Path, target: Path, exclude: list[str] | None = None) -> None:
    blocked = set(exclude or []) | {"__pycache__"}
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*blocked, "*.pyc"), dirs_exist_ok=True)


def _serializable_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if not key.startswith("_")}


def start(config: dict[str, Any], results_dir: Path | None = None, run_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    return Orchestrator(config, results_dir=results_dir, run_id=run_id, dry_run=dry_run).run()


REPLAYABLE_STAGES = ("analysis", "code_generation", "code_error", "code_revise")


def replay_stage(config: dict[str, Any], run_dir: Path, stage: str, results_dir: Path | None = None) -> dict[str, Any]:
    if stage not in REPLAYABLE_STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {', '.join(REPLAYABLE_STAGES)}")
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    orchestrator = Orchestrator(config, results_dir=results_dir, run_dir=run_dir)
    orchestrator.ensure_setup()
    orchestrator.state.record_event("stage_replay", stage=stage)
    if stage == "analysis":
        result = orchestrator.stage_analysis()
    elif stage == "code_generation":
        result = orchestrator.stage_code_generation()
    elif stage == "code_error":
        check, _ = orchestrator.stage_code_error(int(orchestrator.state.data.get("round") or 1))
        result = StageResult("code_error", check.passed, list(check.issues))
    else:
        report = read_json(run_dir / "artifacts" / "runs" / "code_error_report.json", default={}) or {}
        result = orchestrator.stage_code_revise(int(orchestrator.state.data.get("round") or 1), report)
    orchestrator.state.save()
    return {"run_dir": str(run_dir), "stage": stage, "passed": result.passed, "summary": result.describe(), "issues": result.issues}

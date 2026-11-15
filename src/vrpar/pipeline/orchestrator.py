"""Sequential pipeline: analysis -> generation -> acceptance loop -> nn improve.

Agents do the work.  The orchestrator seeds worktrees, renders instructions,
collects artifacts, keeps the nn improve ledger (statuses, labels, champion
snapshots), checks the mechanical conditions and exports the winning code.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vrpar.config import (
    get_stage,
    loop_config,
    project_root,
    resolve_path,
    task_slug,
)
from vrpar.nn_improve import plans
from vrpar.nn_improve.budget import EvalSpec, eval_spec, nn_config
from vrpar.pipeline import gates
from vrpar.pipeline.spec import STAGE_INPUTS
from vrpar.pipeline.stages import run_agent_stage
from vrpar.problems import ProblemPlugin
from vrpar.run.run_dir import append_log, make_run_dir, write_json
from vrpar.run.setup import run_setup
from vrpar.run.state import DONE, FAILED, RunState
from vrpar.run.worktree import (
    collect_artifacts,
    find_checkpoint,
    prune_generated,
    seed_worktree,
    snapshot_tree,
)

GENERATED_REL = "artifacts/generated"
NN_REL = "artifacts/nn_improve"
PLANS_REL = "artifacts/nn_improve/plans"
INITIAL_REL = "artifacts/nn_improve/initial/generated"
BEST_REL = "artifacts/nn_improve/best/generated"
WINNERS_REL = "artifacts/nn_improve/winners"


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
        self.ledger: dict[str, Any] | None = None
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

    def _seed(self, stage_name: str, reset: bool = False) -> Path:
        return seed_worktree(
            run_dir=self.run_dir,
            stage_name=stage_name,
            problem=self.problem,
            baseline_dir=self.root / "baseline",
            instances_dir=None,
            artifact_inputs=list(STAGE_INPUTS.get(stage_name, [])),
            problem_inputs=["task.yaml", "instance_gen", "grader"],
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

    # -- paths ------------------------------------------------------------ #

    def _nn_dir(self) -> Path:
        return self.run_dir / NN_REL

    def _initial_dir(self) -> Path:
        return self.run_dir / INITIAL_REL

    def _best_dir(self) -> Path:
        return self.run_dir / BEST_REL

    def _winner_dir(self, plan_id: str) -> Path:
        return self.run_dir / WINNERS_REL / plan_id / "generated"

    def _restore_run_generated(self, source: Path) -> None:
        """Make the run's canonical package an exact copy of ``source``."""
        snapshot_tree(source, self.run_dir / GENERATED_REL)

    def _save_ledger(self) -> None:
        if self.ledger is None:
            return
        plans.save_ledger(self.run_dir, self.ledger)
        self.state.set_plan_summary(plans.summarise(self.ledger))

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

    def stage_code_error(self, spec: EvalSpec, report_key: str) -> tuple[gates.TestCheck, dict[str, Any]]:
        """Run one evaluation: the agent writes/runs test_train.py, the gate scores it.

        ``report_key`` names the run-relative report directory, so 36 plan runs and
        the acceptance runs never overwrite each other.
        """
        stage_config = get_stage(self.config, "code_error")
        worktree = self._seed("code_error", reset=False)
        generated = worktree / GENERATED_REL
        if generated.exists():
            shutil.rmtree(generated, ignore_errors=True)
        self._materialise_deliverable(worktree)
        report_dir = Path(worktree) / "artifacts" / "runs" / report_key
        report_dir.mkdir(parents=True, exist_ok=True)
        self.state.start_stage("code_error")
        instructions = self.state.consume_instructions("code_error")
        extra_variables = {
            "eval_epochs": str(spec.epochs),
            "eval_train_episodes": str(spec.train_episodes),
            "eval_protocol": spec.protocol,
            "report_dir": report_dir.as_posix(),
        }
        outcome = run_agent_stage(
            stage_config, self.config, self.run_dir, worktree, self.backend, instructions,
            extra_variables=extra_variables,
        )
        self._collect(worktree, [GENERATED_REL, f"artifacts/runs/{report_key}"])
        # Keep the weights and the code that produced this score, so a finished run can
        # be verified later without retraining; the working copy is pruned afterwards.
        self._preserve_checkpoint(report_key, spec.epochs)
        self._keep_executed_code(report_key)

        check = gates.check_code_error(
            self.run_dir,
            report_dir=f"artifacts/runs/{report_key}",
            pins=spec.pins,
        )
        payload = gates.check_report(check, protocol=spec.protocol, pins=spec.pins)
        write_json(self.run_dir / "artifacts" / "runs" / report_key / "report.json", payload)
        write_json(self.run_dir / "artifacts" / "runs" / "code_error_report.json", payload)
        self.state.finish_stage(
            "code_error",
            status=DONE if check.passed else FAILED,
            message=f"{spec.protocol} {report_key}: {check.describe()}",
            artifacts=[f"artifacts/runs/{report_key}"],
            error="" if check.passed else "; ".join(check.issues[:3]),
        )
        if check.score is not None:
            self.state.set_score(check.score, int(self.state.data.get("round") or 0))
        self.log(f"code_error[{report_key}] ({spec.protocol}): {check.describe()}")
        self._note_outcome(outcome)
        return check, payload

    def _checkpoint_of(self, report_key: str) -> Path | None:
        return find_checkpoint(self.run_dir / "artifacts" / "runs" / report_key)

    def _checkpoint_rel(self, report_key: str) -> str | None:
        checkpoint = self._checkpoint_of(report_key)
        if checkpoint is None:
            return None
        return checkpoint.relative_to(self.run_dir).as_posix()

    def _preserve_checkpoint(self, report_key: str, epochs: int) -> None:
        """Keep the checkpoint of the scored execution next to its report.

        Repair rounds reuse the report key: the stale file is removed first, so only the
        attempt that was scored survives. A missing checkpoint leaves none behind.
        """
        checkpoint = find_checkpoint(self.run_dir / GENERATED_REL, epochs)
        target_dir = self.run_dir / "artifacts" / "runs" / report_key
        target_dir.mkdir(parents=True, exist_ok=True)
        for stale in target_dir.glob("checkpoint-*.pt"):
            stale.unlink(missing_ok=True)
        if checkpoint is None:
            self.log(f"  checkpoint: none found for {report_key}")
            return
        target = target_dir / checkpoint.name
        shutil.copy2(checkpoint, target)
        self.log(f"  checkpoint kept: {target.relative_to(self.run_dir).as_posix()}")

    def _keep_executed_code(self, report_key: str) -> None:
        """Snapshot the executed package under the report, then prune the working copy."""
        source = self.run_dir / GENERATED_REL
        target = self.run_dir / "artifacts" / "runs" / report_key / "generated"
        snapshot_tree(source, target)
        self.log(f"  executed code kept: {target.relative_to(self.run_dir).as_posix()}")

    def stage_code_adjustment(
        self,
        mode: str,
        *,
        plan: dict[str, Any] | None = None,
        report_key: str | None = None,
        round_index: int | str = 0,
    ) -> StageResult:
        stage_config = get_stage(self.config, "code_adjustment")
        worktree = self._seed("code_adjustment", reset=False)
        generated = worktree / GENERATED_REL
        if generated.exists():
            shutil.rmtree(generated, ignore_errors=True)
        self._materialise_deliverable(worktree)

        if plan is not None:
            suffix = f"plan-{plan['id']}.md"
        else:
            suffix = f"round-{round_index}.md"
        note = worktree / "artifacts" / "revisions" / suffix

        self.state.start_stage("code_adjustment")
        extra_variables: dict[str, str] = {
            "adjustment_mode": mode,
            "round_index": str(round_index),
        }
        if plan is not None:
            extra_variables["plan_id"] = str(plan["id"])
            extra_variables["plan_json"] = json.dumps(plan, indent=2, ensure_ascii=False)
        if report_key:
            extra_variables["evidence_dir"] = (
                Path(worktree) / "data" / "artifacts" / "runs" / report_key
            ).as_posix()
        instructions = self.state.consume_instructions("code_adjustment")
        outcome = run_agent_stage(
            stage_config, self.config, self.run_dir, worktree, self.backend, instructions,
            round_index=int(round_index) if str(round_index).isdigit() else 0,
            extra_variables=extra_variables,
        )
        self._collect(worktree, [GENERATED_REL, f"artifacts/revisions/{suffix}"])
        prune_generated(self.run_dir / GENERATED_REL)
        result = StageResult("code_adjustment", outcome.ok or note.is_file(), [] if outcome.ok else ["agent turn failed"])
        self.state.finish_stage(
            "code_adjustment",
            status=DONE if result.passed else FAILED,
            message=f"{mode}: {result.describe()}",
            artifacts=[f"artifacts/revisions/{suffix}", GENERATED_REL],
            error="" if result.passed else "; ".join(result.issues),
        )
        self._note_outcome(outcome)
        return result

    def stage_nn_improve(
        self,
        batch: int,
        assignments: list[str | None],
        previous_winners: list[dict[str, Any]],
        nn: dict[str, Any],
        extra_instructions: list[str] | None = None,
    ) -> StageResult:
        stage_config = get_stage(self.config, "nn_improve")
        worktree = self._seed("nn_improve", reset=False)
        # The seeder copies the whole nn directory, which grows with every winner.
        # The agent only needs the ledger and the champion pointer.
        staged = worktree / "data" / NN_REL
        staged.mkdir(parents=True, exist_ok=True)
        for relative in ("plans.json", "best/meta.json"):
            source = self._nn_dir() / relative
            if source.is_file():
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        self.state.start_stage("nn_improve")
        instructions = self.state.consume_instructions("nn_improve")
        if extra_instructions:
            instructions = list(instructions) + list(extra_instructions)
        extra_variables = {
            "nn_batch": str(batch),
            "base_assignments": _assignments_text(assignments),
            "strategies_per_batch": str(nn["strategies_per_batch"]),
            "mutations_per_batch": str(nn["mutations_per_batch"]),
        }
        outcome = run_agent_stage(
            stage_config, self.config, self.run_dir, worktree, self.backend, instructions,
            round_index=batch, extra_variables=extra_variables,
        )
        collected = self._collect_plan_files(worktree, batch)
        result = StageResult("nn_improve", outcome.ok or bool(collected), [])
        if not result.passed:
            result.issues.append("agent turn failed and no plan file was written")
        self.state.finish_stage(
            "nn_improve",
            status=DONE if result.passed else FAILED,
            message=f"batch {batch}: {result.describe()}",
            artifacts=collected,
            error="" if result.passed else "; ".join(result.issues),
        )
        self._note_outcome(outcome)
        return result

    def _collect_plan_files(self, worktree: Path, batch: int) -> list[str]:
        """Merge this batch's plan files into the run directory, one file at a time.

        ``collect_artifacts`` replaces a directory wholesale, and the worktree only
        holds the current batch's files, so that would delete the earlier records.
        """
        source_dir = Path(worktree) / PLANS_REL
        target_dir = self._nn_dir() / "plans"
        copied: list[str] = []
        if not source_dir.is_dir():
            return copied
        target_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(source_dir.glob(f"b{int(batch)}-*.md")):
            shutil.copy2(path, target_dir / path.name)
            relative = f"artifacts/nn_improve/plans/{path.name}"
            copied.append(relative)
            self.log(f"  artifact collected: {relative}")
        return copied

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

        acceptance = eval_spec(self.config, "acceptance")
        check = self._acceptance_loop(acceptance)
        if not check.passed:
            self.state.finish("failed")
            return self._finalize(success=False, reason="no score / exit code 0")

        self.state.set_initial(check.score)
        self._snapshot_initial()

        search = eval_spec(self.config, "search")
        self._restore_run_generated(self._initial_dir())
        search_check, _ = self.stage_code_error(search, "initial-search")
        initial_search = search_check.score if search_check.passed else check.score
        if not search_check.passed:
            self.log("initial-search failed; falling back to the acceptance score as the label baseline")
        self.state.set_initial_search(initial_search)

        direction = str((self.config.get("score") or {}).get("direction") or "minimize")
        nn = nn_config(self.config)
        self.ledger = plans.empty_ledger(
            direction=direction,
            initial_score=check.score,
            initial_search_score=initial_search,
        )
        self.ledger["min_delta"] = nn["min_delta"]
        plans.save_ledger(self.run_dir, self.ledger)
        self._initialise_best(initial_search)
        self._save_ledger()

        if nn["enabled"] and nn["rounds"] > 0:
            self._nn_loop(search, nn)

        self._finalise_champion(acceptance)
        self.state.finish("completed")
        return self._finalize(success=True, reason="")

    def resume(self) -> dict[str, Any]:
        """Continue an interrupted run from its ledger.

        Everything up to and including the initial-search baseline is assumed to
        be on disk; the caller only reaches here when ``state.json`` exists.  A
        plan that was ``running`` when the process died is reset to ``pending``
        and re-executed: every plan restores its base snapshot before it starts,
        so re-running one is safe.
        """
        self.log(f"resume: {self.run_dir}")
        self.ensure_setup()
        self.state.record_event("run_resume", run_dir=str(self.run_dir))
        self.state.save()

        plan_md = self.run_dir / "artifacts" / "analysis" / "plan.md"
        if not plan_md.is_file() or not (self.run_dir / GENERATED_REL).is_dir():
            raise RuntimeError(
                "cannot resume: artifacts/analysis/plan.md or artifacts/generated is missing"
            )

        ledger = plans.load_ledger(self.run_dir)
        if ledger is None:
            raise RuntimeError(
                "cannot resume: no nn improve ledger "
                "(artifacts/nn_improve/plans.json); start a fresh run instead"
            )
        self.ledger = ledger
        nn = nn_config(self.config)
        self.ledger["min_delta"] = nn["min_delta"]

        reset = 0
        for plan in plans.plans_of(self.ledger):
            if plan.get("status") == plans.RUNNING:
                plan["status"] = plans.PENDING
                reset += 1
        if reset:
            self.log(f"resume: reset {reset} running plan(s) to pending")

        if not self._initial_dir().is_dir():
            raise RuntimeError(
                "cannot resume: artifacts/nn_improve/initial/generated is missing"
            )

        winners = plans.winners(self.ledger)
        for winner in winners:
            if not self._winner_dir(winner["id"]).is_dir():
                self.log(f"resume: warning: winner {winner['id']} has no code snapshot")
        available = [winner for winner in winners if self._winner_dir(winner["id"]).is_dir()]
        if not self._best_dir().is_dir() and available:
            snapshot_tree(self._winner_dir(available[0]["id"]), self._best_dir())
            self.log(f"resume: rebuilt best/ from {available[0]['id']}")

        if self.ledger.get("best_plan_id") is None and available:
            self.ledger["best_score"] = available[0].get("score")
            self.ledger["best_plan_id"] = available[0].get("id")
            self.state.set_best(available[0].get("score"), available[0].get("id"))
            snapshot_tree(self._winner_dir(available[0]["id"]), self._best_dir())
            self.log(f"resume: champion set to {available[0]['id']}")

        self._save_ledger()
        search = eval_spec(self.config, "search")
        acceptance = eval_spec(self.config, "acceptance")
        batches = [int(record.get("batch", 0)) for record in self.ledger.get("batches", [])]
        current = max(batches) if batches else 0

        if nn["enabled"] and nn["rounds"] > 0:
            self._nn_loop(search, nn, start_batch=current or 1)

        if self.ledger.get("final_attempted"):
            self.log("resume: final acceptance was already attempted; not re-running it")
            if self.ledger.get("final_score") is None:
                self._restore_run_generated(self._initial_dir())
        else:
            self._finalise_champion(acceptance)

        self.state.finish("completed")
        return self._finalize(success=True, reason="")

    def _acceptance_loop(self, spec: EvalSpec) -> gates.TestCheck:
        rounds = max(1, int(loop_config(self.config).get("max_rounds") or 3))
        report_key = "initial"
        check: gates.TestCheck | None = None
        for round_index in range(1, rounds + 1):
            self.state.set_round(round_index)
            self.log(f"--- acceptance round {round_index}/{rounds} ---")
            check, _ = self.stage_code_error(spec, report_key)
            if check.passed:
                return check
            if round_index == rounds:
                self.log("revision budget exhausted")
                return check
            self.stage_code_adjustment("repair", report_key=report_key, round_index=round_index)
            report_key = f"repair-{round_index}"
        return check  # type: ignore[return-value]

    def _nn_loop(self, spec: EvalSpec, nn: dict[str, Any], start_batch: int = 1) -> None:
        assert self.ledger is not None
        for batch in range(start_batch, nn["rounds"] + 1):
            record = plans.batch_record(self.ledger, batch)
            if record is None:
                if not self._begin_batch(batch, spec, nn):
                    return
                record = plans.batch_record(self.ledger, batch)
            self.state.set_nn_round(batch)
            pending = [
                plan for plan in record.get("plans", []) if plan.get("status") == plans.PENDING
            ]
            if pending:
                self.log(
                    f"--- nn improve batch {batch}/{nn['rounds']}: "
                    f"{len(pending)} pending plan(s) ---"
                )
            for plan in pending:
                self._run_plan(plan, spec, nn, batch)
            if not plans.batch_winners(self.ledger, batch):
                if nn.get("stop_when_no_winner", True):
                    self.log(f"batch {batch}: no plan improved on the baseline; stopping")
                    return
                self.log(f"batch {batch}: no winner, continuing as configured")

    def _begin_batch(self, batch: int, spec: EvalSpec, nn: dict[str, Any]) -> bool:
        assert self.ledger is not None
        previous = plans.winners(self.ledger)
        if batch > 1 and not previous:
            self.log("no winners left; stopping the search")
            return False
        total = nn["strategies_per_batch"] + nn["mutations_per_batch"]
        assignments = plans.assign_bases(batch, previous, total, pool=int(nn.get("base_pool_size") or 10))
        if not assignments:
            self.log("no base available; stopping the search")
            return False
        self.state.set_nn_round(batch)
        self.log(f"--- nn improve batch {batch}/{nn['rounds']} ---")
        # The agent must see the champion, not the last plan's package.
        self._restore_run_generated(self._best_dir())
        retry_instructions: list[str] | None = None
        attempts = 0
        while True:
            outcome = self.stage_nn_improve(
                batch, assignments, previous, nn, extra_instructions=retry_instructions
            )
            if not outcome.passed:
                self.log("nn_improve agent failed; stopping the search")
                return False
            raw, parse_issues = plans.read_plan_files(self.run_dir, batch)
            proposed, issues = plans.validate_batch(
                raw,
                batch=batch,
                assignments=assignments,
                strategies_per_batch=nn["strategies_per_batch"],
                mutations_per_batch=nn["mutations_per_batch"],
                previous_winners=previous,
                deny=nn["deny"],
            )
            issues = parse_issues + issues
            if not issues:
                break
            for issue in issues[:6]:
                self.log(f"  invalid plan: {issue}")
            if nn["on_invalid_plans"] == "retry" and attempts == 0:
                attempts += 1
                retry_instructions = [f"- {issue}" for issue in issues[:6]]
                retry_instructions.append(
                    "Rewrite the batch's plan files under artifacts/nn_improve/plans/ "
                    "with exactly the required slots."
                )
                self.log("batch rejected; asking the agent for one corrected batch")
                continue
            self.log(f"batch rejected (on_invalid_plans={nn['on_invalid_plans']})")
            return False
        record = plans.start_batch(
            self.ledger,
            batch,
            protocol=spec.protocol,
            entry_best=self.ledger.get("best_score"),
        )
        record["plans"].extend(proposed)
        self._save_ledger()
        return True

    def _run_plan(self, plan: dict[str, Any], spec: EvalSpec, nn: dict[str, Any], batch: int) -> None:
        assert self.ledger is not None
        plan_id = plan["id"]
        report_key = f"plan-{plan_id}"
        plans.mark_running(plan, report_dir=f"artifacts/runs/{report_key}")
        self._save_ledger()

        base = plan.get("base_plan_id")
        source = self._winner_dir(base) if base else self._initial_dir()
        if not source.is_dir():
            self.log(f"plan {plan_id}: base {base} snapshot missing; using the initial code")
            source = self._initial_dir()
        self._restore_run_generated(source)
        self.log(f"plan {plan_id} [base {base or 'initial'}]: {plan['title']}")

        self.stage_code_adjustment("plan", plan=plan, report_key=report_key)
        check, _ = self.stage_code_error(spec, report_key)
        repairs = 0
        while not check.passed and repairs < int(nn["max_repairs_per_plan"]):
            repairs += 1
            self.stage_code_adjustment(
                "repair", report_key=report_key, round_index=f"{plan_id}-r{repairs}"
            )
            check, _ = self.stage_code_error(spec, report_key)

        plans.mark_result(
            self.ledger,
            plan,
            passed=check.passed,
            evaluated=check.train_exit is not None,
            score=check.score,
            protocol=spec.protocol,
            report_dir=f"artifacts/runs/{report_key}",
            repairs=repairs,
            error="; ".join(check.issues[:3]),
        )
        self._save_ledger()

        if check.passed and plan.get("label") == 1:
            self._snapshot_winner(plan)
            if plans.better(check.score, self.ledger.get("best_score"), self.ledger["direction"]):
                self._update_best(plan)
        self.log(f"plan {plan_id}: {plan['status']} label={plan['label']} score={plan['score']}")

    def _snapshot_initial(self) -> None:
        snapshot_tree(self.run_dir / GENERATED_REL, self._initial_dir())

    def _initialise_best(self, score: float | None) -> None:
        snapshot_tree(self._initial_dir(), self._best_dir())
        write_json(
            self._nn_dir() / "best" / "meta.json",
            {
                "plan_id": None,
                "score": score,
                "protocol": "search",
                "batch": 0,
                "checkpoint": self._checkpoint_rel("initial"),
            },
        )
        if self.ledger is not None:
            self.ledger["best_score"] = score
            self.ledger["best_plan_id"] = None
        self.state.set_best(score, None)

    def _snapshot_winner(self, plan: dict[str, Any]) -> None:
        snapshot_tree(self.run_dir / GENERATED_REL, self._winner_dir(plan["id"]))
        write_json(
            self.run_dir / WINNERS_REL / plan["id"] / "meta.json",
            {
                "plan_id": plan["id"],
                "score": plan["score"],
                "protocol": plan["protocol"],
                "base_plan_id": plan.get("base_plan_id"),
                "title": plan.get("title"),
                "checkpoint": self._checkpoint_rel(f"plan-{plan['id']}"),
            },
        )

    def _update_best(self, plan: dict[str, Any]) -> None:
        snapshot_tree(self.run_dir / GENERATED_REL, self._best_dir())
        write_json(
            self._nn_dir() / "best" / "meta.json",
            {
                "plan_id": plan["id"],
                "score": plan["score"],
                "protocol": plan["protocol"],
                "checkpoint": self._checkpoint_rel(f"plan-{plan['id']}"),
            },
        )
        if self.ledger is not None:
            self.ledger["best_score"] = plan["score"]
            self.ledger["best_plan_id"] = plan["id"]
        self.state.set_best(plan["score"], plan["id"])
        self.log(f"new champion: {plan['id']} score={plan['score']}")

    def _finalise_champion(self, spec: EvalSpec) -> None:
        if self.ledger is None:
            return
        if self.ledger.get("best_plan_id") is None:
            self.log("no plan ever won; exporting the initial code")
            self.ledger["final_attempted"] = True
            self._save_ledger()
            self._restore_run_generated(self._initial_dir())
            return
        self._restore_run_generated(self._best_dir())
        self.log("--- final acceptance: champion re-run ---")
        check, _ = self.stage_code_error(spec, "final")
        initial = self.ledger.get("initial_score")
        if check.passed and plans.better(check.score, initial, self.ledger["direction"]):
            self.ledger["final_score"] = check.score
            self.ledger["final_attempted"] = True
            self.state.set_final(check.score)
            self._save_ledger()
            self.log(f"champion accepted: final={check.score}")
            return
        self.log("champion did not beat the initial code; falling back to it")
        self.ledger["final_score"] = None
        self.ledger["final_attempted"] = True
        self.state.set_final(None)
        self._save_ledger()
        self._restore_run_generated(self._initial_dir())

    # -- export / report -------------------------------------------------- #

    def _export(self) -> Path:
        mirror = (self.final_dir / self.slug).resolve()
        if mirror.exists():
            shutil.rmtree(mirror, ignore_errors=True)
        mirror.mkdir(parents=True, exist_ok=True)
        source = self.run_dir / GENERATED_REL
        if self.ledger is not None:
            if self.ledger.get("final_score") is not None:
                source = self._best_dir()
            elif self.ledger.get("best_plan_id") is None:
                source = self._initial_dir()
            else:
                source = self._best_dir()
        if not source.is_dir():
            source = self.run_dir / GENERATED_REL
        if source.is_dir():
            _copy_tree_filtered(source, mirror / GENERATED_REL, exclude=["result", "__pycache__"])
        baseline = self.root / "baseline" / "pomo"
        if baseline.is_dir():
            _copy_tree_filtered(baseline, mirror / "pomo")
        checkpoint_key = "final" if (self.ledger and self.ledger.get("final_score") is not None) else "initial"
        checkpoint = self._checkpoint_of(checkpoint_key)
        if checkpoint is not None:
            target = mirror / "checkpoints" / checkpoint.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(checkpoint, target)
            self.log(f"exported checkpoint to {target.relative_to(mirror).as_posix()}")
        self.log(f"exported final code to {mirror}")
        return mirror

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

    def _write_report(self, success: bool, reason: str, mirror: Path | None) -> Path:
        s = self.state.data
        lines = [
            f"# {self.problem.name} synthesis run - {s['run_id']}",
            "",
            f"- task: {s['task']}",
            f"- slug: {self.slug}",
            f"- result: **{'SUCCESS' if success else 'FAILED'}**",
            f"- reason: {reason or 'acceptance + final test passed'}",
            f"- rounds used: {s.get('round')}",
            f"- initial score: {s.get('initial_score')}",
            f"- initial search score: {s.get('initial_search_score')}",
            f"- best score: {s.get('best_score')} (plan {s.get('best_plan_id')})",
            f"- final score: {s.get('final_score')}",
            "",
            "## Stages",
            "",
            "| stage | status | attempts | message |",
            "|---|---|---|---|",
        ]
        for name, record in s["stages"].items():
            lines.append(f"| {name} | {record.get('status')} | {record.get('attempts')} | {str(record.get('message', ''))[:120]} |")
        if self.ledger is not None:
            lines.extend(["", "## nn improve", "", "| batch | plan | kind | status | label | score |", "|---|---|---|---|---|---|"])
            for record in self.ledger.get("batches", []):
                for plan in record.get("plans", []):
                    lines.append(
                        f"| {record.get('batch')} | {plan.get('id')} | {plan.get('kind')} | "
                        f"{plan.get('status')} | {plan.get('label')} | {plan.get('score')} |"
                    )
        if mirror is not None:
            checkpoint_key = (
                "final" if (self.ledger and self.ledger.get("final_score") is not None) else "initial"
            )
            checkpoint = self._checkpoint_rel(checkpoint_key)
            if checkpoint:
                lines.extend(["", f"- champion checkpoint: `{checkpoint}`"])
            lines.extend(["", f"- final export: `{mirror}`"])
        path = self.run_dir / "REPORT.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _note_outcome(self, outcome) -> None:
        for note in outcome.notes or []:
            self.log(f"  note: {note}")
        if outcome.result is not None:
            self.log(f"  agent: {outcome.result.summary(240)}")


def _assignments_text(assignments: list[str | None]) -> str:
    lines = []
    for index, base in enumerate(assignments, start=1):
        lines.append(f"- slot {index}: base = {base if base else 'initial'}")
    return "\n".join(lines)


def _copy_tree_filtered(source: Path, target: Path, exclude: list[str] | None = None) -> None:
    blocked = set(exclude or []) | {"__pycache__"}
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*blocked, "*.pyc"), dirs_exist_ok=True)


def _serializable_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if not key.startswith("_")}


def start(config: dict[str, Any], results_dir: Path | None = None, run_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    return Orchestrator(config, results_dir=results_dir, run_id=run_id, dry_run=dry_run).run()


def resume(config: dict[str, Any], run_dir: Path, results_dir: Path | None = None) -> dict[str, Any]:
    """Continue an interrupted run from its own run directory."""
    return Orchestrator(config, results_dir=results_dir, run_dir=Path(run_dir)).resume()


REPLAYABLE_STAGES = ("analysis", "code_generation", "code_error", "code_adjustment", "nn_improve")


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
        check, _ = orchestrator.stage_code_error(eval_spec(config, "acceptance"), "replay")
        result = StageResult("code_error", check.passed, list(check.issues))
    elif stage == "code_adjustment":
        result = orchestrator.stage_code_adjustment(
            "repair",
            report_key="replay",
            round_index=int(orchestrator.state.data.get("round") or 1),
        )
    else:
        orchestrator.ledger = plans.load_ledger(run_dir)
        nn = nn_config(config)
        total = nn["strategies_per_batch"] + nn["mutations_per_batch"]
        winners = plans.winners(orchestrator.ledger) if orchestrator.ledger else []
        assignments = plans.assign_bases(int(orchestrator.state.data.get("nn_round") or 1) + 1, winners, total)
        result = orchestrator.stage_nn_improve(int(orchestrator.state.data.get("nn_round") or 1) + 1, assignments, winners, nn)
    orchestrator.state.save()
    return {"run_dir": str(run_dir), "stage": stage, "passed": result.passed, "summary": result.describe(), "issues": result.issues}

"""The orchestrator: analysis -> code generation -> (error -> revise)* -> grade -> export.

Design notes
------------
* **Sequential, not competitive.** One stage runs at a time, in order. There is no
  leaderboard and no parallel island: the value here is a strict artifact contract
  between stages, not search breadth.
* **Artifacts are the interface.** Each stage writes files, the orchestrator copies
  them into the run directory, and the next stage reads them from there. Nothing
  depends on an agent's prose.
* **The loop is explicit and bounded.** ``loop.on`` names the stages that repeat;
  ``loop.until`` names the predicate that stops it (the acceptance contract);
  ``loop.max_rounds`` bounds it. When the budget runs out, the run fails loudly
  and keeps all evidence rather than pretending to succeed.
* **The variant lives in a plugin.** Everything variant-specific -- prompts,
  generated file layout, CLI flags, instance synthesis, feasibility checks,
  scoring -- comes from ``problems/<slug>/``. This module never names a variant.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from cvrp.compat.coral_adapter import coral_info
from cvrp.config import (
    get_stage,
    loop_config,
    project_root,
    resolve_path,
    stage_names,
    task_slug,
)
from cvrp.executor.contract import Contract
from cvrp.executor.grader import grade_run
from cvrp.executor.runner import run_checks, run_report_summary
from cvrp.llm.base import make_backend
from cvrp.pipeline import gates
from cvrp.pipeline.stages import StageOutcome, run_agent_stage
from cvrp.problems import ProblemPlugin
from cvrp.run.run_dir import append_log, make_run_dir, read_json, write_json
from cvrp.run.setup import run_setup
from cvrp.run.state import DONE, FAILED, RunState
from cvrp.run.worktree import collect_artifacts, read_baseline, seed_worktree

SOLUTION_REL = "artifacts/runs/best_solution.json"


class Orchestrator:
    """Runs the whole pipeline once."""

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
        # `run_dir` is how a single stage is replayed on an existing run; `start`
        # always creates a fresh one.
        self.run_dir = Path(run_dir) if run_dir else make_run_dir(self.results_dir, self.slug, run_id)
        self.state = RunState(self.run_dir, config)
        self.log_path = self.run_dir / "logs" / "pipeline.log"
        self.final_dir = resolve_path(config, str(workspace.get("final_dir") or "./workspace/final"))
        self.data_dir = resolve_path(config, self.problem.data_dir_name)
        self.contract = Contract.from_config(config)
        self.dry_run = dry_run
        self.backend = make_backend(dict(config.get("llm") or {}), problem=self.problem)
        write_json(self.run_dir / "config.resolved.json", _serializable_config(config), default=str)

    # -- logging ----------------------------------------------------------- #

    def log(self, message: str) -> None:
        append_log(self.log_path, message)
        if (self.config.get("run") or {}).get("verbose", True):
            print(f"[cvrp] {message}")

    # -- preparation -------------------------------------------------------- #

    def ensure_setup(self) -> None:
        """Materialize whatever the task says the environment needs.

        Runs before any stage, because a missing runtime dependency (torch for the
        generated solver) would otherwise surface as an `import_error` inside the
        code error stage and read like the agent's mistake.
        """
        if (self.config.get("run") or {}).get("setup", True) in (False, "false", "skip"):
            self.log("setup: skipped by run.setup=false")
            return
        result = run_setup(self.config, force=bool(self.config.get("_setup_force")), log=self.log)
        for line in result.lines():
            if line.startswith("setup:"):
                self.log(line)

    def ensure_instances(self) -> None:
        """Ask the problem to materialize its data if it is not there yet."""
        self.log(f"problem: {self.problem.slug} ({self.problem.name}) at {self.problem.root}")
        if self.problem.data_ready(self.data_dir):
            self.log(f"data: reusing {self.data_dir}")
            return
        if not self.problem.data_generator and self.problem.checks_module is None:
            self.log("data: no generator declared; the problem is expected to bring its own data")
            return
        self.log(f"data: generating under {self.data_dir}")
        written = self.problem.build_dataset(self.data_dir)
        if written:
            self.log(f"data: {len(written)} item(s) written")

    # -- artifact plumbing -------------------------------------------------- #

    def _generated_rel(self) -> str:
        return str((self.problem.spec.get("generated") or {}).get("dir") or "artifacts/generated").rstrip("/")

    def _inputs_for(self, stage_name: str) -> list[str]:
        """Which earlier stage outputs this stage gets to read.

        The problem may declare extra inputs (``data.artifact_inputs``); they are
        merged with the run-relative outputs of all preceding stages.
        """
        produced: list[str] = []
        for name in stage_names(self.config):
            if name == stage_name:
                break
            produced.extend(str(item) for item in (get_stage(self.config, name).get("produces") or []))
        for extra in self.problem.artifact_inputs(stage_name):
            if extra not in produced:
                produced.append(extra)
        return produced

    def _seed(self, stage_name: str, reset: bool = False) -> Path:
        # Validates that the stage exists before we create a directory for it.
        get_stage(self.config, stage_name)
        return seed_worktree(
            run_dir=self.run_dir,
            stage_name=stage_name,
            problem=self.problem,
            instances_dir=self.data_dir,
            artifact_inputs=self._inputs_for(stage_name),
            reset=reset,
        )

    def _collect(self, worktree: Path, produces: list[str]) -> list[str]:
        copied = collect_artifacts(self.run_dir, worktree, produces)
        for relative in copied:
            self.log(f"  artifact collected: {relative}")
        return copied

    # -- stages ------------------------------------------------------------- #

    def stage_analysis(self, round_index: int = 0) -> gates.GateResult:
        stage_config = get_stage(self.config, "analysis")
        worktree = self._seed("analysis", reset=round_index > 0)
        self.state.start_stage("analysis")
        instructions = self.state.consume_instructions("analysis")
        outcome = run_agent_stage(
            stage_config, self.config, self.run_dir, worktree, self.backend, instructions, round_index
        )
        self._collect(worktree, list(stage_config.get("produces") or []))
        gate = gates.gate_analysis(self.run_dir, worktree, baseline=read_baseline(worktree))
        status = DONE if gate.passed else FAILED
        self.state.finish_stage(
            "analysis",
            status=status,
            message=gate.describe(),
            artifacts=list(stage_config.get("produces") or []),
            error="" if gate.passed else "; ".join(gate.issues[:3]),
        )
        self._note_outcome(outcome, gate)
        return gate

    def stage_code_generation(self, round_index: int = 0) -> gates.GateResult:
        stage_config = get_stage(self.config, "code_generation")
        worktree = self._seed("code_generation", reset=round_index > 0)
        self.state.start_stage("code_generation")
        instructions = self.state.consume_instructions("code_generation")
        outcome = run_agent_stage(
            stage_config, self.config, self.run_dir, worktree, self.backend, instructions, round_index
        )
        generated = self._generated_rel()
        self._collect(worktree, [*list(stage_config.get("produces") or []), generated])
        gate = gates.gate_generated(
            self.run_dir, worktree, self.problem, baseline=read_baseline(worktree)
        )
        status = DONE if gate.passed else FAILED
        self.state.finish_stage(
            "code_generation",
            status=status,
            message=gate.describe(),
            artifacts=[generated],
            error="" if gate.passed else "; ".join(gate.issues[:3]),
        )
        self._note_outcome(outcome, gate)
        return gate

    def stage_code_error(self, round_index: int = 0) -> tuple[gates.GateResult, dict[str, Any]]:
        worktree = self._seed("code_error", reset=False)
        self.state.start_stage("code_error")
        grader_args = (self.config.get("grader") or {}).get("args") or {}
        iterations = int(grader_args.get("iterations") or 1000)
        instance_size = grader_args.get("instance_size")
        report = run_checks(
            run_dir=self.run_dir,
            worktree=worktree,
            contract=self.contract,
            instances_dir=worktree / "data" / "instances",
            instance_size=int(instance_size) if instance_size is not None else None,
            instance_glob=self.problem.instance_glob,
            full_iterations=iterations,
            extra_hints=self.problem.revision_hints(),
            problem=self.problem,
        )
        # NOTE: run_checks writes into the canonical run directory, so nothing may
        # be "collected" from the worktree afterwards. The executor worktree has no
        # artifacts/runs of its own, and collect_artifacts replaces its target
        # directory -- collecting here would delete the report just written.
        gate = gates.gate_run_report(report)
        self.state.finish_stage(
            "code_error",
            status=DONE if gate.passed else FAILED,
            message=run_report_summary(report),
            artifacts=["artifacts/runs/s3_run_report.json"],
            error="" if gate.passed else "; ".join(gate.issues[:3]),
        )
        self.state.set_costs(report.get("initial_cost"), report.get("best_cost"))
        self.log(f"code_error: {run_report_summary(report)}")
        return gate, report

    def stage_code_revise(self, round_index: int, report: dict[str, Any]) -> gates.GateResult:
        stage_config = get_stage(self.config, "code_revise")
        worktree = self._seed("code_revise", reset=False)

        # Hand the revise agent the evidence it needs, as files rather than prose.
        brief = {
            "problem": self.problem.slug,
            "round": round_index,
            "run_report": report,
            "error_class": report.get("error_class"),
            "failed_step": report.get("failed_step"),
            "revision_hint": report.get("revision_hint"),
            "analysis": read_json(self.run_dir / gates.ANALYSIS_REL, default={}),
        }
        brief_path = worktree / "artifacts" / "revisions" / f"round-{round_index}-brief.json"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(brief_path, brief)

        self.state.start_stage("code_revise")
        extra_variables = {
            "round_index": str(round_index),
            "failed_step": str(report.get("failed_step") or ""),
            "error_class": str(report.get("error_class") or ""),
            "error_message": str(report.get("error_message") or "")[:6000],
            "revision_hint": str(report.get("revision_hint") or ""),
            "run_report_path": "artifacts/revisions/round-%d-brief.json" % round_index,
            "findings_lines": _findings_lines(report),
        }
        instructions = self.state.consume_instructions("code_revise")

        # Turn 1: read the code. The executor's gate only knows what it can run and
        # parse; the semantic problems (a baseline symbol that never made it across,
        # a mask that is never consulted, a plotting path that cannot resolve) are
        # found by reading. Doing it first means the repair turn fixes a reviewed
        # list instead of guessing from one error message.
        review_config = dict(stage_config)
        review_config["prompt_key"] = "code_review"
        run_agent_stage(
            review_config,
            self.config,
            self.run_dir,
            worktree,
            self.backend,
            instructions,
            round_index,
            extra_variables=extra_variables,
        )

        outcome = run_agent_stage(
            stage_config,
            self.config,
            self.run_dir,
            worktree,
            self.backend,
            instructions,
            round_index,
            extra_variables=extra_variables,
        )
        generated = self._generated_rel()
        self._collect(worktree, [generated, "artifacts/revisions", "artifacts/reviews"])
        gate = gates.gate_generated(
            self.run_dir, worktree, self.problem, baseline=read_baseline(worktree)
        )
        self.state.finish_stage(
            "code_revise",
            status=DONE if gate.passed else FAILED,
            message=f"round {round_index}: {gate.describe()}",
            artifacts=[
                f"artifacts/revisions/round-{round_index}-brief.json",
                "artifacts/reviews/",
                generated,
            ],
            error="" if gate.passed else "; ".join(gate.issues[:3]),
        )
        self._note_outcome(outcome, gate)
        return gate

    def stage_feasibility(self, round_index: int = 0) -> tuple[gates.GateResult, dict[str, Any]]:
        """Turn the grader's solution dump into the run's feasibility verdict."""
        self.state.start_stage("feasibility")
        bundle = read_json(self.run_dir / "artifacts" / "runs" / "grader_result.json", default={}) or {}
        verification = ((bundle.get("details") or {}).get("feasibility")) or {}

        if verification:
            payload = dict(verification)
        else:
            payload = {
                "feasible": False,
                "skipped": True,
                "violations": [],
                "note": "no grader result was available to verify",
            }
        if not payload.get("feasible") and payload.get("skipped") and not bundle:
            payload["note"] = "grader did not run; nothing to verify"

        target = self.run_dir / "artifacts" / "runs" / "s5_feasibility.json"
        write_json(target, payload)

        gate = gates.gate_feasibility(payload)
        self.state.finish_stage(
            "feasibility",
            status=DONE if gate.passed else FAILED,
            message=gate.describe(),
            artifacts=["artifacts/runs/s5_feasibility.json"],
            error="" if gate.passed else "; ".join(gate.issues[:3]),
        )
        return gate, payload

    def _note_outcome(self, outcome: StageOutcome, gate: gates.GateResult) -> None:
        for note in outcome.notes or []:
            self.log(f"  note: {note}")
        for warning in gate.warnings:
            self.log(f"  warning: {warning}")
        if outcome.result is not None:
            self.log(f"  agent: {outcome.result.summary(240)}")

    # -- main loop ---------------------------------------------------------- #

    def run(self) -> dict[str, Any]:
        self.log(f"run: {self.run_dir}")
        self.log(f"CORAL shell: {coral_info().describe()}")
        self.ensure_setup()
        self.ensure_instances()
        self.state.record_event("run_start", run_dir=str(self.run_dir), problem=self.problem.slug)
        self.state.save()

        loop = loop_config(self.config)
        analysis_gate = self.stage_analysis()
        if not analysis_gate.passed:
            self.state.finish("failed")
            return self._finalize(success=False, reason="analysis gate failed")

        generation_gate = self.stage_code_generation()
        if not generation_gate.passed:
            self.state.finish("failed")
            return self._finalize(success=False, reason="code generation gate failed")

        rounds = max(1, int(loop["max_rounds"]))
        report: dict[str, Any] = {}
        for round_index in range(1, rounds + 1):
            self.state.set_round(round_index)
            self.log(f"--- round {round_index}/{rounds} ---")
            error_gate, report = self.stage_code_error(round_index)
            if error_gate.passed:
                break
            if round_index == rounds:
                self.log("revision budget exhausted")
                break
            revise_gate = self.stage_code_revise(round_index, report)
            if not revise_gate.passed:
                self.log("revise stage produced nothing usable; continuing anyway")

        # Grade first: the grader produces the canonical solution dump, and the
        # feasibility stage reports on exactly that verification. Reversing the
        # order would make feasibility describe a file that does not exist yet.
        grader_bundle = grade_run(
            self.config,
            self.run_dir,
            self.run_dir / "worktrees" / "code_error",
            problem=self.problem,
        )
        write_json(self.run_dir / "artifacts" / "runs" / "grader_result.json", grader_bundle)
        self.log(
            f"grader: score={grader_bundle['score']} feasible={grader_bundle['feasible']} "
            f"best_cost={grader_bundle['best_cost']}"
        )

        feasibility_gate, feasibility = self.stage_feasibility()
        self.state.set_costs(feasibility.get("cost"), grader_bundle.get("best_cost"))

        # A skipped feasibility check is only tolerable when the task says so.
        # "No crash, no verification" is exactly the failure mode this pipeline
        # exists to prevent, so the default is to fail rather than to trust.
        grader_args = (self.config.get("grader") or {}).get("args") or {}
        dump_required = bool(grader_args.get("require_solution_dump", True))
        skipped_but_required = bool(feasibility.get("skipped")) and dump_required

        success = (
            bool(report.get("passed"))
            and feasibility_gate.passed
            and bool(grader_bundle.get("feasible", False))
            and not skipped_but_required
        )
        self.state.finish("completed" if success else "failed")
        reason = "" if success else "acceptance contract not satisfied"
        if not success and report.get("passed"):
            if skipped_but_required:
                reason = (
                    "contract passed, but the program did not dump a solution, so the result "
                    "could not be verified independently"
                )
            elif not grader_bundle.get("feasible", False):
                reason = "grader rejected the generated program"
        return self._finalize(success=success, reason=reason)

    # -- export ------------------------------------------------------------- #

    def _finalize(self, success: bool, reason: str) -> dict[str, Any]:
        if success:
            self._export()
        report_path = self._write_report(success=success, reason=reason)
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
        """Publish the run: the generated files, plus the runtime payload they need.

        The export mirrors the *worktree* layout rather than flattening it, because
        the generated code resolves its dependencies by relative position: it imports
        POMO's toolchain as `pomo.…` and its own modules by bare name, which only
        works when the directory holding `pomo/` is the root the program runs from.
        A flat copy would be unreadable *and* unrunnable -- `ModuleNotFoundError` on
        the first import.

        The support payload comes from `resources.export` (plus `workspace.export`),
        so the same mechanism works for any problem that ships a reference
        implementation. Unchanged baseline sources are still not part of
        `artifacts/generated/`; this only decides what travels with the run.
        """
        generated = self.run_dir / self._generated_rel()
        spec = self._export_spec()
        mirror = (self.final_dir / self.slug).resolve()
        if mirror.exists():
            shutil.rmtree(mirror, ignore_errors=True)
        mirror.mkdir(parents=True, exist_ok=True)

        target = mirror / self._generated_rel()
        if generated.is_dir():
            _copy_tree_filtered(generated, target)
        self.log(f"exported final code to {target}")

        for item in spec["include"]:
            source = Path(item) if Path(item).is_absolute() else self._baseline_root() / item
            if not source.exists():
                self.log(f"  export: {item} declared but not found at {source}; skipped")
                continue
            destination = mirror / Path(item).name
            _copy_tree_filtered(source, destination, exclude=spec["exclude"])
            self.log(f"  export: bundled {item} ({_describe_tree(destination)})")

        self._extend_export_readme(mirror, spec)
        return mirror

    def _baseline_root(self) -> Path:
        """Where `resources.export` entries are resolved from."""
        raw = (self.problem.spec.get("resources") or {}).get("baseline_dir")
        if not raw:
            return self.problem.root
        candidate = resolve_path(self.config, str(raw))
        return candidate if candidate.is_dir() else self.problem.root

    def _export_spec(self) -> dict[str, Any]:
        """Merge `resources.export` with any `workspace.export` override."""
        resources = self.problem.spec.get("resources") or {}
        raw = resources.get("export") or {}
        spec: dict[str, Any] = {
            "mirror_root": str(raw.get("mirror_root") or "."),
            "include": [str(item) for item in (raw.get("include") or [])],
            "exclude": [str(item) for item in (raw.get("exclude") or [])] or ["__pycache__"],
        }
        override = (self.config.get("workspace") or {}).get("export") or {}
        if isinstance(override, dict):
            if override.get("include") is not None:
                spec["include"] = [str(item) for item in override["include"]]
            if override.get("exclude") is not None:
                spec["exclude"] = [str(item) for item in override["exclude"]]
            if override.get("mirror_root") is not None:
                spec["mirror_root"] = str(override["mirror_root"])
        return spec

    def _extend_export_readme(self, mirror: Path, spec: dict[str, Any]) -> None:
        """Append the operational section: how to run what was just exported.

        Written here rather than required from the agent because it describes the
        *bundle*, not the code: it depends on what the export actually carried.
        """
        readme = mirror / self._generated_rel() / "README.md"
        bundle_lines = [
            "",
            "## Running this bundle",
            "",
            "This directory is self-contained: run everything **from this directory**, so",
            "that `pomo/` (bundled next to `artifacts/generated/`) resolves as the generated",
            "code expects.",
            "",
            "```bash",
            "# evaluate / improve on one instance",
            f"python {self._generated_rel()}/{self.problem.program} --path <instance.json> --iteration 100",
            "",
            "# train (writes checkpoints and logs under artifacts/generated/result/)",
            f"cd {self._generated_rel()} && python {self._entry_name('train')}",
            "",
            "# evaluate the model you just trained (test_* prefers the newest",
            "# checkpoint under result/, falling back to a pretrained one if you place it",
            "# in checkpoints/)",
            f"cd {self._generated_rel()} && python {self._entry_name('test')}",
            "```",
            "",
            "Bundled runtime payload: " + (", ".join(f"`{item}`" for item in spec["include"]) or "(none)"),
            "",
        ]
        text = "# Generated deliverable\n\n(see the code-generation summary below)\n"
        if readme.is_file():
            text = readme.read_text(encoding="utf-8")
        if "## Running this bundle" in text:
            return
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(text.rstrip() + "\n" + "\n".join(bundle_lines), encoding="utf-8")

    def _entry_name(self, kind: str) -> str:
        entries = (self.problem.spec.get("generated") or {}).get("entries") or {}
        return str(entries.get(kind) or f"{kind}.py")

    def _write_report(self, success: bool, reason: str) -> Path:
        analysis = read_json(self.run_dir / gates.ANALYSIS_REL, default={}) or {}
        s3 = read_json(self.run_dir / "artifacts" / "runs" / "s3_run_report.json", default={}) or {}
        s5 = read_json(self.run_dir / "artifacts" / "runs" / "s5_feasibility.json", default={}) or {}
        grader_bundle = read_json(self.run_dir / "artifacts" / "runs" / "grader_result.json", default={}) or {}

        lines: list[str] = [
            f"# {self.problem.name} synthesis run - {self.state.data['run_id']}",
            "",
            f"- problem: **{self.problem.slug}** ({self.problem.name})",
            f"- task: {self.state.data['task']}",
            f"- result: **{'SUCCESS' if success else 'FAILED'}**",
            f"- reason: {reason or 'acceptance contract satisfied'}",
            f"- rounds used: {self.state.data.get('round')}",
            f"- best cost: {s3.get('best_cost')} (initial {s3.get('initial_cost')})",
            f"- grader score: {grader_bundle.get('score')} (feasible={grader_bundle.get('feasible')})",
            "",
            "## Stages",
            "",
            "| stage | status | attempts | duration | message |",
            "|---|---|---|---|---|",
        ]
        for name, record in self.state.data["stages"].items():
            lines.append(
                f"| {name} | {record.get('status')} | {record.get('attempts')} | "
                f"{record.get('duration_s', '-')} | {str(record.get('message', ''))[:120]} |"
            )

        # A failed run is only useful if the reason survives in the report. The
        # stage message is a one-line summary, so the per-stage error is repeated
        # in full here rather than truncated into the table above.
        failures = [
            (name, record)
            for name, record in self.state.data["stages"].items()
            if record.get("status") == "failed" or record.get("error")
        ]
        if failures:
            lines.extend(["", "## Why it failed", ""])
            for name, record in failures:
                lines.append(f"### {name}")
                lines.append("")
                for line in str(record.get("error") or record.get("message") or "(no message)").splitlines():
                    lines.append(f"    {line}")
                lines.append("")

        lines.extend(self._diagnostics_block())

        lines.extend(
            [
                "## Analysis (S1)",
                "",
                f"- problem type: {analysis.get('problem_type')}",
                f"- constraints: {', '.join(str(c.get('id') or c.get('name')) for c in (analysis.get('constraints') or []) if isinstance(c, dict))}",
                f"- masking rules: {', '.join(str(r.get('name')) for r in (analysis.get('masking_rules') or []) if isinstance(r, dict))}",
                "",
                "## Executor evidence (S3)",
                "",
                f"- passed: {s3.get('passed')}",
                f"- failed step: {s3.get('failed_step')}",
                f"- error class: {s3.get('error_class')}",
                "",
                "## Feasibility (S5, from the grader's own verification)",
                "",
                f"- feasible: {s5.get('feasible')}",
                f"- violations: {s5.get('violations')}",
                "",
                "## Artifacts",
                "",
                f"- generated code: `{self.run_dir / self._generated_rel()}`",
                f"- final export: `{self.final_dir / self.slug}`",
                f"- logs: `{self.run_dir / 'logs'}`",
                "",
                "## How to continue",
                "",
                "```bash",
                f"uv run cvrp stage analysis --run {self.run_dir.name}   # replay one stage",
                f"uv run cvrp log --run {self.run_dir.name} -n 200        # pipeline log",
                f"uv run cvrp show logs/analysis.round0.log --run {self.run_dir.name}",
                "```",
            ]
        )
        path = self.run_dir / "REPORT.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _diagnostics_block(self) -> list[str]:
        """What a reader needs to debug a failure without leaving the report.

        Three questions decide almost every failed run: *did the stage produce its
        declared artifact?*, *what did the agent actually get told?*, and *what did
        it say before it stopped?*. This block answers all three.
        """
        lines: list[str] = ["## Diagnostics", ""]

        lines.append("Declared artifacts:")
        lines.append("")
        for stage in self.config.get("stages", []):
            for relative in stage.get("produces") or []:
                path = self.run_dir / str(relative)
                exists = path.exists()
                detail = ""
                if path.is_file():
                    detail = f" ({path.stat().st_size} B)"
                elif path.is_dir():
                    count = sum(1 for _ in path.rglob("*") if _.is_file())
                    detail = f" ({count} file(s))"
                mark = "ok " if exists else "MISSING"
                lines.append(f"- [{mark}] `{relative}`{detail}")
        lines.append("")

        # The rendered instruction is the single most useful artefact when an
        # agent produces nothing: it shows exactly what it was asked to do.
        for stage in self.config.get("stages", []):
            if stage.get("kind") != "agent":
                continue
            worktree = self.run_dir / "worktrees" / stage["name"]
            instruction = worktree / "CVPR.md"
            log_candidates = sorted((self.run_dir / "logs").glob(f"{stage['name']}.round*.log"))
            log_path = log_candidates[-1] if log_candidates else None
            lines.append(f"Stage `{stage['name']}`:")
            lines.append("")
            if instruction.is_file():
                text = instruction.read_text(encoding="utf-8", errors="replace")
                lines.append(f"- instruction: `{instruction}` ({len(text)} chars)")
                for marker in ("# Your role", "# Task for this stage", "Available inputs"):
                    lines.append(f"  - contains {marker!r}: {marker in text}")
            else:
                lines.append(f"- instruction: MISSING at `{instruction}`")
            if log_path is not None:
                lines.append(f"- agent log: `{log_path}` ({log_path.stat().st_size} B)")
            else:
                lines.append(f"- agent log: none under `{self.run_dir / 'logs'}`")
            lines.append("")

        log_path = self.run_dir / "logs" / "pipeline.log"
        if log_path.is_file():
            lines.append("Pipeline log (last 40 lines):")
            lines.append("")
            lines.append("```")
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
            lines.extend(tail)
            lines.append("```")
            lines.append("")

        agent_logs = sorted((self.run_dir / "logs").glob("*round*.log"))
        if agent_logs:
            latest = agent_logs[-1]
            lines.append(f"Last agent log tail (`{latest.name}`, last 25 lines):")
            lines.append("")
            lines.append("```")
            try:
                tail = latest.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
            except OSError as exc:
                tail = [f"unreadable: {exc}"]
            lines.extend(tail)
            lines.append("```")
            lines.append("")

        return lines


def _copy_tree_filtered(source: Path, target: Path, exclude: list[str] | None = None) -> None:
    """Copy a tree, dropping cache directories and named exclusions.

    `__pycache__` and `*.pyc` are meaningless in an export, and a reference
    implementation may hold training output (`result/`) that would balloon the
    bundle with logs and intermediate weights.
    """
    blocked = set(exclude or []) | {"__pycache__"}
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*blocked, "*.pyc"),
        dirs_exist_ok=True,
    )


def _describe_tree(path: Path) -> str:
    if path.is_file():
        return f"{path.stat().st_size} B"
    files = [item for item in path.rglob("*") if item.is_file()]
    total = sum(item.stat().st_size for item in files)
    return f"{len(files)} file(s), {total / 1024:.0f} KiB"


def _findings_lines(report: dict[str, Any]) -> str:
    """The executor's static findings, as prompt-ready lines.

    The gate only *fails* on some of them; the rest are drift the revise agent should
    fix while it is already in the file. Rendering both keeps the repair turn from
    being followed by a second, near-identical round.
    """
    findings = report.get("findings") or {}
    issues = list(findings.get("issues") or [])
    warnings = list(findings.get("warnings") or [])
    lines: list[str] = []
    lines.extend(f"  - FAIL: {item}" for item in issues)
    lines.extend(f"  - note: {item}" for item in warnings)
    if not lines:
        return "  (the executor's static checks found nothing)"
    return "\n".join(lines)


def _serializable_config(config: dict[str, Any]) -> dict[str, Any]:
    """Drop the live plugin object before writing the config to disk."""
    return {key: value for key, value in config.items() if not key.startswith("_")}


def start(
    config: dict[str, Any],
    results_dir: Path | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Entry point used by ``cvrp start``."""
    orchestrator = Orchestrator(config, results_dir=results_dir, run_id=run_id, dry_run=dry_run)
    return orchestrator.run()


# --------------------------------------------------------------------------- #
# single-stage replay
# --------------------------------------------------------------------------- #

#: Stages a replay can drive, mapped to the method that runs them.
REPLAYABLE_STAGES = (
    "analysis",
    "code_generation",
    "code_error",
    "code_revise",
    "feasibility",
)


def replay_stage(
    config: dict[str, Any],
    run_dir: Path,
    stage: str,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """Re-run exactly one stage against an existing run directory.

    This is the debugging entry point. A run that dies at ``analysis`` should not
    need the whole pipeline replayed to try again -- and the stage's worktree,
    instruction file and logs are all still there to inspect.

    Deliberate limitations, so the semantics stay honest:

    * the run's ``state.json`` is updated (the stage's record is overwritten), and
      a ``stage_replay`` event is appended, so a replayed stage is never mistaken
      for a fresh run;
    * a stage whose inputs are missing (e.g. replaying ``code_error`` before
      ``code_generation`` produced anything) runs anyway and its gate reports the
      problem, rather than silently succeeding;
    * the run is *not* re-finalized: no export, no new REPORT.md. Run
      ``cvrp start`` when you want a graded, exported result.
    """
    if stage not in REPLAYABLE_STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {', '.join(REPLAYABLE_STAGES)}")

    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")

    orchestrator = Orchestrator(config, results_dir=results_dir, run_dir=run_dir)
    orchestrator.ensure_setup()
    orchestrator.ensure_instances()
    orchestrator.state.record_event("stage_replay", stage=stage)
    orchestrator.log(f"replay: stage {stage} in {run_dir}")

    if stage == "analysis":
        gate = orchestrator.stage_analysis()
    elif stage == "code_generation":
        gate = orchestrator.stage_code_generation()
    elif stage == "code_error":
        gate, _ = orchestrator.stage_code_error()
    elif stage == "code_revise":
        report = read_json(run_dir / "artifacts" / "runs" / "s3_run_report.json", default={}) or {}
        gate = orchestrator.stage_code_revise(int(orchestrator.state.data.get("round") or 1) or 1, report)
    else:  # feasibility
        gate, _ = orchestrator.stage_feasibility()

    orchestrator.state.save()
    return {
        "run_dir": str(run_dir),
        "stage": stage,
        "passed": gate.passed,
        "summary": gate.describe(),
        "issues": list(gate.issues),
        "warnings": list(gate.warnings),
        "artifacts": [str(item) for item in (get_stage(config, stage).get("produces") or [])],
    }

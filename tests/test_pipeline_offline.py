"""End-to-end pipeline test on the offline backend.

This is the regression net for the whole contract: problem loading, stage
seeding, artifact copying, gating, the executor's three gates, the grader, the
problem's own feasibility check, export, and the report. It must pass without a
Claude Code session.

The engine is variant-agnostic, so the test drives it through a real problem
directory (the shipped ``cvrptw`` example, whose mock solver is pure Python and
needs no torch).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cvrp.config import get_stage, load_config
from cvrp.pipeline import gates
from cvrp.pipeline.orchestrator import start
from tests.conftest import write_project

TASK_YAML = """
task:
  name: "Tiny CVRPTW test"
  slug: "tiny"
  description: "n=20 instance set used by the offline pipeline test."
  tips: "travel time equals distance"

problem:
  slug: "cvrptw"

llm:
  backend: "mock"
  model: "mock"

stages:
  - name: retrieval
    kind: agent
    role: "src/cvrp/roles/retrieval_agent.md"
    produces: ["artifacts/records/retrieval.json"]
  - name: analysis
    kind: agent
    role: "src/cvrp/roles/analysis_agent.md"
    produces: ["artifacts/analysis/analysis.json"]
  - name: code_generation
    kind: agent
    role: "src/cvrp/roles/code_generation_agent.md"
    produces: ["artifacts/generated/"]
  - name: code_error
    kind: executor
    role: "src/cvrp/roles/code_error_agent.md"
    produces: ["artifacts/runs/s3_run_report.json"]
    steps: [py_compile, import_probe, correspondence, smoke_run, full_run]
  - name: code_revise
    kind: agent
    role: "src/cvrp/roles/code_revise_agent.md"
    produces: ["artifacts/revisions/", "artifacts/reviews/"]
  - name: feasibility
    kind: executor
    role: "src/cvrp/roles/feasibility_agent.md"
    produces: ["artifacts/runs/s5_feasibility.json"]
  - name: update
    kind: agent
    role: "src/cvrp/roles/update_agent.md"
    produces: ["records/main"]

loop:
  on: ["code_error", "code_revise"]
  until: "contract_passed"
  max_rounds: 2

grader:
  entrypoint: "cvrp.executor.grader:CodeGrader"
  direction: "maximize"
  timeout: 300
  args:
    require_feasible: true
    iterations: 5
    require_solution_dump: true

workspace:
  results_dir: "./results"
  final_dir: "./workspace/final"

run:
  verbose: false
  ui: false
  session: "local"
"""


@pytest.fixture()
def project(scratch: Path) -> Path:
    """A complete scratch project, problem directory included."""
    return write_project(scratch / "project", TASK_YAML, problem="cvrptw", with_checkpoint=True)


def test_pipeline_completes_offline(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    result = start(config, results_dir=project / "results")

    run_dir = Path(result["run_dir"])
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))

    assert state["status"] in {"completed", "failed"}
    # the offline mock emits a genuinely runnable solver, so the whole chain must pass
    assert result["success"], json.dumps(state["stages"], indent=2)[:2000]

    for stage in (
        "retrieval",
        "analysis",
        "code_generation",
        "code_error",
        "feasibility",
        "update",
    ):
        assert state["stages"][stage]["status"] == "done", stage
    assert state["stages"]["code_revise"]["status"] == "pending", "no revision should be needed"

    # The records layer ran for real: five per-run records, and a knowledge base the
    # update stage created.
    run_id = run_dir.name
    temp_files = {path.name for path in (project / "records" / "temp" / run_id).glob("*.json")}
    assert {
        "problem_description.json",
        "new_problem_code.json",
        "code_error.json",
        "description_parsing.json",
    } <= temp_files
    assert (project / "records" / "main" / "skills" / "cvrptw_skill.md").is_file()
    assert (project / "records" / "related" / run_id / "retrieval.json").is_file()

    # Only the two reader stages carry the knowledge block. Feeding it to the code
    # stages would lengthen their prompts and let them follow a previous run instead of
    # this run's evidence.
    worktrees = run_dir / "worktrees"
    for stage in ("retrieval", "analysis"):
        text = (worktrees / stage / "CVPR.md").read_text(encoding="utf-8")
        assert "# Related records" in text, stage
    for stage in ("code_generation", "code_revise", "update"):
        path = worktrees / stage / "CVPR.md"
        if path.is_file():
            assert "# Related records" not in path.read_text(encoding="utf-8"), stage

    assert state["best_cost"] is not None
    assert state["initial_cost"] is not None

    assert (run_dir / "artifacts" / "analysis" / "analysis.json").is_file()
    assert (run_dir / "artifacts" / "generated" / "run.py").is_file()
    assert (run_dir / "artifacts" / "runs" / "s3_run_report.json").is_file()
    assert (run_dir / "artifacts" / "runs" / "s5_feasibility.json").is_file()
    assert (run_dir / "artifacts" / "runs" / "grader_result.json").is_file()

    report = json.loads((run_dir / "artifacts" / "runs" / "s3_run_report.json").read_text(encoding="utf-8"))
    assert report["passed"], report.get("error_message")
    assert [step["name"] for step in report["steps"]] == [
        "py_compile",
        "import_probe",
        "correspondence",
        "smoke_run",
        "full_run",
    ]
    # the static findings ride along even when every gate passed, so the revise agent
    # can fix drift in the same round as a real failure
    assert "findings" in report

    feasibility = json.loads((run_dir / "artifacts" / "runs" / "s5_feasibility.json").read_text(encoding="utf-8"))
    assert feasibility["feasible"], feasibility.get("violations")
    assert feasibility["checks"]["customers_served_once"] is True
    assert feasibility["checks"]["capacity_respected"] is True

    bundle = json.loads((run_dir / "artifacts" / "runs" / "grader_result.json").read_text(encoding="utf-8"))
    assert bundle["problem"] == "cvrptw"
    assert bundle["feasible"] is True
    assert bundle["score"] > 0

    # S6 export + human report. The export mirrors the worktree layout so the
    # generated code's `pomo.…` imports resolve, and it bundles `pomo/` alongside.
    final = project / "workspace" / "final" / "tiny"
    assert (final / "artifacts" / "generated" / "run.py").is_file()
    assert (final / "pomo" / "POMO" / "utils" / "utils.py").is_file()
    assert (run_dir / "REPORT.md").is_file()


def test_analysis_payload_comes_from_the_problem(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    result = start(config, results_dir=project / "results")
    analysis = json.loads(
        (Path(result["run_dir"]) / "artifacts" / "analysis" / "analysis.json").read_text(encoding="utf-8")
    )
    assert analysis["problem_type"] == "CVRPTW"
    assert analysis["masking_rules"]
    assert any("depot" in json.dumps(rule).lower() for rule in analysis["masking_rules"])
    assert analysis["checkpoint_compatibility"]["encoder_input_dim_must_stay"] == 2


def test_instances_are_generated_by_the_problem(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    start(config, results_dir=project / "results")
    manifest = json.loads(
        (project / "instances" / "data" / "cvrptw" / "manifest.json").read_text(encoding="utf-8")
    )
    # data parameters belong to the problem, not to the task, so they are the ones
    # declared in problems/cvrptw/problem.yaml
    assert manifest["count"] >= 1
    assert manifest["instances"][0]["problem_size"] == 20


def test_a_missing_deliverable_file_is_a_warning_at_the_gate(project: Path, monkeypatch: pytest.MonkeyPatch):
    """The generated-files list is an expectation at the *gate*, not a requirement.

    The strict version of this lives in the executor's correspondence check, which
    fails the stage; the gate stays permissive so a hand-run replay of a partial
    package can still be inspected rather than being rejected outright.
    """
    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    result = start(config, results_dir=project / "results")
    run_dir = Path(result["run_dir"])
    generated = run_dir / "worktrees" / "code_generation" / "artifacts" / "generated"
    (generated / "CVRPTWProblemDef.py").unlink(missing_ok=True)

    gate = gates.gate_generated(run_dir, run_dir / "worktrees" / "code_generation", config["_problem"])
    assert gate.passed, gate.issues
    assert any("CVRPTWProblemDef.py" in warning for warning in gate.warnings)
    assert (run_dir / "artifacts" / "runs" / "s3_run_report.json").is_file()


def test_prompt_renderer_injects_every_stage_variable(project: Path, monkeypatch: pytest.MonkeyPatch):
    from cvrp.pipeline.stages import PLACEHOLDER_RE, render_instruction

    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    stage = get_stage(config, "code_revise")
    text = render_instruction(
        stage,
        config,
        worktree=project / "wt",
        run_dir=project / "run",
        extra_variables={
            "error_message": "boom",
            "round_index": "3",
            "revision_hint": "hint",
            "failed_step": "full_run",
            "error_class": "shape_error",
        },
    )
    # the engine's own variables reach the prompt
    assert "boom" in text
    assert "hint" in text
    assert "full_run" in text
    assert "shape_error" in text
    assert "stage `code_revise`" in text
    # the problem's identity and the worktree path reach the prompt
    assert "CVRPTW" in text
    assert str(project / "wt") in text
    # and no unresolved engine placeholder survives into the agent's instruction
    leftovers = {match.group(1) for match in PLACEHOLDER_RE.finditer(text)}
    assert leftovers <= {"cost"}, f"unresolved placeholders: {sorted(leftovers)}"


def test_error_path_routes_through_revise(project: Path, monkeypatch: pytest.MonkeyPatch):
    """A broken solver must reach the revise stage, not be silently accepted.

    The mock backend is wrapped so its first generated program has a syntax
    error: the executor then fails at ``py_compile``, the loop's ``on`` stages
    must fire, and the second round must pass.
    """
    from cvrp.llm.mock import MockBackend
    from cvrp.pipeline import orchestrator as orch_mod

    class BrokenFirst(MockBackend):
        name = "mock(broken)"
        broken = True

        def run(self, prompt, cwd, log_path, timeout_s=60):
            result = super().run(prompt, cwd, log_path, timeout_s)
            if self.broken and "[STAGE:code_generation]" in prompt:
                (Path(cwd) / "artifacts" / "generated" / "run.py").write_text(
                    "def broken(:\n    pass\n", encoding="utf-8"
                )
                return result
            if getattr(self, "broken", False) and "[STAGE:code_revise]" in prompt:
                # the "fix": re-emit the solver, which is what a real revision
                # round would do after attributing a syntax error
                self._emit_solver(Path(cwd))
                self.broken = False
            return result

    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    original_init = orch_mod.Orchestrator.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.backend = BrokenFirst(self.config.get("llm") or {}, problem=self.problem)

    monkeypatch.setattr(orch_mod.Orchestrator, "__init__", patched_init)

    result = start(config, results_dir=project / "results")
    run_dir = Path(result["run_dir"])
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["stages"]["code_revise"]["attempts"] >= 1
    report = json.loads((run_dir / "artifacts" / "runs" / "s3_run_report.json").read_text(encoding="utf-8"))
    assert report["passed"], report

"""Regression tests for stale-artifact detection.

The scenario these protect against is the one that produced a confusing
"analysis gate failed" on a real run: a stage worktree is *reused* across runs and
seeded from the run directory, so a previously successful stage leaves its artifact
in place. If the gate only checked the artifact's contents, an agent that crashed
(or never started, because the CLI was missing) would be reported as a pass — and
the previous round's result would be carried forward as the current one.

The mechanism is a *content* snapshot taken just before the agent runs
(`cvrp.run.worktree.write_baseline`), not a timestamp comparison: a worktree can be
recreated, and seeding preserves mtimes through ``copy2``.
"""

from __future__ import annotations

import json
from pathlib import Path

from cvrp.pipeline import gates
from cvrp.problems import load_problem
from cvrp.run.run_dir import write_json
from cvrp.run.worktree import read_baseline, write_baseline
from tests.conftest import PACKAGE_PROBLEMS

PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")

GOOD_ANALYSIS = {
    "problem_type": "CVRPTW",
    "full_name": "CVRPTW",
    "summary": "capacity plus time windows",
    "constraints": [{"id": "C", "name": "Capacity", "rule": "load <= capacity"}],
    "state_variables": [{"name": "t", "shape": "(B,P)", "update": "max(a, r) + s"}],
    "masking_rules": [{"name": "tw", "condition": "arrival > due"}],
    "feasibility_checklist": ["each customer once", "load <= capacity"],
    "objective": "minimize distance",
    "output_contract": ["a", "b"],
    "pomo_modification_plan": [{"file": "pomo/POMO/CVRPEnv.py", "change": "add mask"}],
    "checkpoint_compatibility": {"encoder_input_dim_must_stay": 2},
}


# --------------------------------------------------------------------------- #
# snapshot primitives
# --------------------------------------------------------------------------- #


def test_changed_since_snapshot_detects_new_modified_and_removed_files(scratch: Path):
    from cvrp.run.worktree import changed_since_snapshot

    worktree = scratch / "wt"
    target = worktree / "artifacts" / "analysis"
    target.mkdir(parents=True)
    (target / "analysis.json").write_text('{"a": 1}', encoding="utf-8")

    baseline = read_baseline(worktree) or {}
    write_baseline(worktree, ["artifacts/analysis"])
    baseline = read_baseline(worktree)
    assert baseline, "the snapshot must record the existing file"

    # unchanged -> nothing reported
    assert changed_since_snapshot(worktree, ["artifacts/analysis"], baseline) == []

    # modified -> reported
    (target / "analysis.json").write_text('{"a": 2}', encoding="utf-8")
    assert changed_since_snapshot(worktree, ["artifacts/analysis"], baseline) == ["artifacts/analysis"]

    # back to the same content -> 'unchanged' again (content, not mtime)
    (target / "analysis.json").write_text('{"a": 1}', encoding="utf-8")
    assert changed_since_snapshot(worktree, ["artifacts/analysis"], baseline) == []

    # added file -> reported
    (target / "plan.md").write_text("plan", encoding="utf-8")
    assert changed_since_snapshot(worktree, ["artifacts/analysis"], baseline) == ["artifacts/analysis"]

    (target / "plan.md").unlink()
    # removed relative to the snapshot -> still matches baseline again
    assert changed_since_snapshot(worktree, ["artifacts/analysis"], baseline) == []


def test_empty_baseline_never_fails_a_stage(scratch: Path):
    """No snapshot taken means the caller's field checks stay in charge."""
    from cvrp.run.worktree import changed_since_snapshot

    worktree = scratch / "wt"
    (worktree / "artifacts" / "analysis").mkdir(parents=True)
    assert changed_since_snapshot(worktree, ["artifacts/analysis"], {}) == []


def test_snapshot_survives_an_unreadable_file(scratch: Path, monkeypatch):
    """A snapshot must never be the reason a run fails."""
    from cvrp.run import worktree as wt

    target = scratch / "wt" / "artifacts" / "analysis"
    target.mkdir(parents=True)
    (target / "analysis.json").write_text("{}", encoding="utf-8")

    real_hash = wt._hash_file

    def failing_hash(path):
        raise OSError("locked")

    monkeypatch.setattr(wt, "_hash_file", failing_hash)
    snapshot = wt.snapshot_paths(scratch / "wt", ["artifacts/analysis"])
    assert snapshot == {"artifacts/analysis/analysis.json": "unreadable"}
    monkeypatch.setattr(wt, "_hash_file", real_hash)


# --------------------------------------------------------------------------- #
# gates
# --------------------------------------------------------------------------- #


def _analysis_worktree(scratch: Path, payload: dict | None = None) -> Path:
    worktree = scratch / "worktrees" / "analysis"
    target = worktree / gates.ANALYSIS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, payload or GOOD_ANALYSIS)
    return worktree


def test_analysis_gate_rejects_an_artifact_left_over_from_before(scratch: Path):
    """The bug: a seeded worktree makes an old artifact look like a fresh one."""
    run_dir = scratch / "run"
    worktree = scratch / "worktrees" / "analysis"
    (worktree / gates.ANALYSIS_REL).parent.mkdir(parents=True, exist_ok=True)

    # snapshot taken while the stage had produced nothing yet
    write_baseline(worktree, [gates.ANALYSIS_REL])
    baseline = read_baseline(worktree)
    assert baseline == {}

    # a previous attempt's artifact appears (that is what seeding does)...
    write_json(worktree / gates.ANALYSIS_REL, GOOD_ANALYSIS)

    # ...but from the *pipeline's* point of view the turn produced nothing, because
    # the stage wrote its own snapshot after the artifact was already in place
    write_baseline(worktree, [gates.ANALYSIS_REL])
    stale = read_baseline(worktree)
    result = gates.gate_analysis(run_dir, worktree, baseline=stale)
    assert not result.passed
    assert any("was written by this attempt" in issue for issue in result.issues)


def test_analysis_gate_accepts_an_artifact_written_this_attempt(scratch: Path):
    run_dir = scratch / "run"
    worktree = scratch / "worktrees" / "analysis"
    (worktree / gates.ANALYSIS_REL).parent.mkdir(parents=True, exist_ok=True)

    write_baseline(worktree, [gates.ANALYSIS_REL])
    baseline = read_baseline(worktree)
    assert baseline == {}

    write_json(worktree / gates.ANALYSIS_REL, GOOD_ANALYSIS)
    result = gates.gate_analysis(run_dir, worktree, baseline=baseline)
    assert result.passed, result.issues


def test_generated_gate_rejects_a_seeded_package(scratch: Path):
    run_dir = scratch / "run"
    worktree = scratch / "worktrees" / "code_generation"
    program = worktree / "artifacts" / "generated" / "run.py"
    program.parent.mkdir(parents=True, exist_ok=True)
    program.write_text("print('old')\n", encoding="utf-8")

    write_baseline(worktree, ["artifacts/generated"])
    baseline = read_baseline(worktree)

    result = gates.gate_generated(run_dir, worktree, PROBLEM, baseline=baseline)
    assert not result.passed
    assert any("artifacts/generated" in issue and "was written by this attempt" in issue for issue in result.issues)


def test_generated_gate_accepts_a_package_written_this_attempt(scratch: Path):
    run_dir = scratch / "run"
    worktree = scratch / "worktrees" / "code_generation"
    (worktree / "artifacts" / "generated").mkdir(parents=True, exist_ok=True)
    write_baseline(worktree, ["artifacts/generated"])
    baseline = read_baseline(worktree)

    (worktree / "artifacts" / "generated" / "run.py").write_text("print('new')\n", encoding="utf-8")
    result = gates.gate_generated(run_dir, worktree, PROBLEM, baseline=baseline)
    assert result.passed, result.issues
    assert any("CVRPTWEnv.py" in warning for warning in result.warnings)


def test_a_partial_update_still_counts_as_producing(scratch: Path):
    """A revision round may touch only one file; that is still work."""
    worktree = scratch / "worktrees" / "code_revise"
    generated = worktree / "artifacts" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "run.py").write_text("print('v1')\n", encoding="utf-8")
    (generated / "CVRPTWEnv.py").write_text("# env\n", encoding="utf-8")

    write_baseline(worktree, ["artifacts/generated"])
    baseline = read_baseline(worktree)

    # only the entry program is patched
    (generated / "run.py").write_text("print('v2')\n", encoding="utf-8")
    assert gates.changed_this_round(worktree, ["artifacts/generated"], baseline) == ["artifacts/generated"]
    assert gates.untouched_outputs(worktree, ["artifacts/generated"], baseline) == []


def test_describe_artifact_reports_a_useful_detail(scratch: Path):
    single = scratch / "one.json"
    single.write_text("{}", encoding="utf-8")
    assert "B," in gates.describe_artifact(single)

    folder = scratch / "many"
    folder.mkdir()
    (folder / "a.py").write_text("a", encoding="utf-8")
    (folder / "b.py").write_text("b", encoding="utf-8")
    detail = gates.describe_artifact(folder)
    assert "2 file(s)" in detail
    assert "newest mtime" in detail


# --------------------------------------------------------------------------- #
# end to end through the real entry points
# --------------------------------------------------------------------------- #


class _SilentBackend:
    """A backend that behaves like a missing `claude` CLI: rc=127, writes nothing."""

    name = "silent"

    def run(self, prompt, cwd, log_path, timeout_s=60):
        from cvrp.llm.base import LLMResult

        return LLMResult(
            returncode=127,
            error="claude: command not found",
            backend=self.name,
            log_path=str(log_path),
        )


def _project(scratch: Path) -> Path:
    from tests.conftest import write_project
    from tests.test_pipeline_offline import TASK_YAML

    return write_project(scratch / "project", TASK_YAML, problem="cvrptw", with_checkpoint=True)


def test_replay_with_a_silent_agent_fails_instead_of_reusing_the_old_artifact(scratch: Path):
    """The real-world failure, through the actual replay entry point."""
    from cvrp.config import load_config
    from cvrp.pipeline.orchestrator import replay_stage, start

    project = _project(scratch)
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.chdir(project)
    try:
        config = load_config(project / "task.yaml", cwd=project)
        first = start(config, results_dir=project / "results")
        run_dir = Path(first["run_dir"])
        assert (run_dir / "artifacts" / "analysis" / "analysis.json").is_file()

        from cvrp.pipeline import orchestrator as orch_mod

        original = orch_mod.Orchestrator.__init__

        def patched(self, *args, **kwargs):
            original(self, *args, **kwargs)
            self.backend = _SilentBackend()

        monkeypatch.setattr(orch_mod.Orchestrator, "__init__", patched)
        replay = replay_stage(config, run_dir, "analysis")
    finally:
        monkeypatch.undo()

    assert not replay["passed"], replay
    assert any("was written by this attempt" in issue for issue in replay["issues"]), replay["issues"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["stages"]["analysis"]["status"] == "failed"


def test_silent_agent_from_a_clean_run_reports_missing_not_stale(scratch: Path):
    """On the first attempt there is nothing to be stale, so say 'missing'."""
    from cvrp.config import load_config
    from cvrp.pipeline.orchestrator import start

    project = _project(scratch)
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.chdir(project)
    try:
        config = load_config(project / "task.yaml", cwd=project)
        from cvrp.pipeline import orchestrator as orch_mod

        original = orch_mod.Orchestrator.__init__

        def patched(self, *args, **kwargs):
            original(self, *args, **kwargs)
            self.backend = _SilentBackend()

        monkeypatch.setattr(orch_mod.Orchestrator, "__init__", patched)
        result = start(config, results_dir=project / "results")
    finally:
        monkeypatch.undo()

    assert not result["success"]
    assert "analysis gate failed" in result["reason"]
    state = json.loads((Path(result["run_dir"]) / "state.json").read_text(encoding="utf-8"))
    assert state["stages"]["analysis"]["status"] == "failed"
    assert "missing" in state["stages"]["analysis"]["error"]

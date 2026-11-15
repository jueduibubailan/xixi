"""Resume continues an interrupted nn improve run from its ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vrpar.config import load_config
from vrpar.pipeline.orchestrator import Orchestrator, resume

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "problems" / "mvmoe_ovrpl_n50" / "task.yaml"


def test_resume_continues_after_a_crash(scratch: Path, monkeypatch):
    monkeypatch.chdir(ROOT)
    results = scratch / "results"
    final = scratch / "final"
    config = load_config(
        TASK,
        cwd=ROOT,
        overrides=[
            "llm.backend=mock",
            "run.setup=false",
            "nn_improve.rounds=2",
            "nn_improve.strategies_per_batch=2",
            "nn_improve.mutations_per_batch=1",
            f"workspace.results_dir={results}",
            f"workspace.final_dir={final}",
        ],
    )

    orchestrator = Orchestrator(config, results_dir=results)
    original_adjust = Orchestrator.stage_code_adjustment
    calls = {"plans": 0, "fail": True}

    def flaky(self, mode, **kwargs):
        if mode == "plan":
            calls["plans"] += 1
            if calls["fail"] and calls["plans"] == 2:
                raise RuntimeError("simulated crash")
        return original_adjust(self, mode, **kwargs)

    monkeypatch.setattr(Orchestrator, "stage_code_adjustment", flaky)
    with pytest.raises(RuntimeError):
        orchestrator.run()

    run_dir = orchestrator.run_dir
    ledger = json.loads((run_dir / "artifacts" / "nn_improve" / "plans.json").read_text())
    statuses = [plan["status"] for record in ledger["batches"] for plan in record["plans"]]
    assert "running" in statuses, "the crash should leave one plan mid-flight"

    calls["fail"] = False
    result = resume(config, run_dir, results_dir=results)
    assert result["success"], result

    ledger = json.loads((run_dir / "artifacts" / "nn_improve" / "plans.json").read_text())
    statuses = [plan["status"] for record in ledger["batches"] for plan in record["plans"]]
    assert "pending" not in statuses
    assert "running" not in statuses
    assert ledger["final_attempted"] is True
    assert ledger["final_score"] is not None
    assert len(ledger["batches"]) == 2

    state = json.loads((run_dir / "state.json").read_text())
    assert state["status"] == "completed"
    assert (run_dir / "REPORT.md").is_file()
    mirror = final / config["task"]["slug"]
    assert (mirror / "artifacts" / "generated" / "test_train.py").is_file()


def test_resume_path_forms(scratch: Path):
    from vrpar.cli import _resolve_resume_dir

    run = scratch / "results" / "demo" / "20260101-000000"
    run.mkdir(parents=True)
    (run / "state.json").write_text("{}", encoding="utf-8")
    config = {"task": {"slug": "demo"}}

    found, _ = _resolve_resume_dir(config, str(run), scratch / "results")
    assert found == run.resolve()
    found, _ = _resolve_resume_dir(config, "demo/20260101-000000", scratch / "results")
    assert found == run.resolve()
    found, _ = _resolve_resume_dir(config, "20260101-000000", scratch / "results")
    assert found == run.resolve()

    found, tried = _resolve_resume_dir(config, "nope", scratch / "results")
    assert found is None
    assert len(tried) == 3


def test_resume_requires_a_ledger(scratch: Path, monkeypatch):
    monkeypatch.chdir(ROOT)
    results = scratch / "results"
    config = load_config(
        TASK,
        cwd=ROOT,
        overrides=[
            "llm.backend=mock",
            "run.setup=false",
            f"workspace.results_dir={results}",
            f"workspace.final_dir={scratch / 'final'}",
        ],
    )
    run_dir = results / "mvmoe_ovrpl_n50" / "broken"
    (run_dir / "artifacts" / "analysis").mkdir(parents=True)
    (run_dir / "artifacts" / "analysis" / "plan.md").write_text("# plan\n", encoding="utf-8")
    (run_dir / "artifacts" / "generated").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="no nn improve ledger"):
        resume(config, run_dir, results_dir=results)

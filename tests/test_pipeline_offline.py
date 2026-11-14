"""Offline end-to-end test for the five-stage pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from vrpar.config import load_config, task_slug
from vrpar.pipeline import gates
from vrpar.pipeline.orchestrator import start

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "problems" / "mvmoe_ovrpl_n50" / "task.yaml"


def test_offline_pipeline_runs_the_nn_loop_and_exports_the_champion(scratch: Path, monkeypatch):
    monkeypatch.chdir(ROOT)
    results = scratch / "results"
    final = scratch / "final"
    config = load_config(
        TASK,
        cwd=ROOT,
        overrides=[
            "llm.backend=mock",
            "run.setup=false",
            "nn_improve.rounds=1",
            f"workspace.results_dir={results}",
            f"workspace.final_dir={final}",
        ],
    )
    result = start(config, results_dir=results)
    assert result["success"], result
    run_dir = Path(result["run_dir"])

    assert (run_dir / "artifacts" / "analysis" / "plan.md").is_file()
    assert not (run_dir / "artifacts" / "analysis" / "analysis.json").exists()
    generated = run_dir / "artifacts" / "generated"
    assert (generated / "test_train.py").is_file()
    assert not (generated / "run.py").exists()

    # The acceptance run and the search baseline are both present.
    initial = json.loads((run_dir / "artifacts" / "runs" / "initial" / "report.json").read_text())
    assert initial["passed"] is True
    assert initial["protocol"] == "acceptance"
    assert initial["score"] == 42.0
    search_baseline = json.loads(
        (run_dir / "artifacts" / "runs" / "initial-search" / "report.json").read_text()
    )
    assert search_baseline["protocol"] == "search"
    assert search_baseline["pins"] == {"epochs": 50, "train_episodes": 5000}

    # The ledger: 12 plans, exactly one winner (b1-s01), champion and final score.
    ledger = json.loads((run_dir / "artifacts" / "nn_improve" / "plans.json").read_text())
    plans = ledger["batches"][0]["plans"]
    assert len(plans) == 12
    assert [plan["status"] for plan in plans].count("completed") == 12
    assert planner_winners(plans) == ["b1-s01"]
    assert ledger["best_plan_id"] == "b1-s01"
    assert ledger["best_score"] == 41.5
    assert ledger["final_score"] == 41.0

    assert (run_dir / "artifacts" / "nn_improve" / "best" / "generated" / "MOCKModel.py").is_file()
    assert (run_dir / "artifacts" / "nn_improve" / "winners" / "b1-s01" / "generated").is_dir()

    state = json.loads((run_dir / "state.json").read_text())
    assert state["initial_score"] == 42.0
    assert state["initial_search_score"] == 42.0
    assert state["best_score"] == 41.5
    assert state["best_plan_id"] == "b1-s01"
    assert state["final_score"] == 41.0
    assert state["plans"]["completed"] == 12

    mirror = final / task_slug(config)
    assert (mirror / "artifacts" / "generated" / "test_train.py").is_file()
    assert (mirror / "pomo" / "POMO" / "CVRPEnv.py").is_file()


def planner_winners(plans: list[dict]) -> list[str]:
    return sorted(plan["id"] for plan in plans if plan.get("label") == 1)


def test_score_parser_uses_last_augmentation_line():
    text = "AUGMENTATION SCORE: 1.0\nNO-AUG SCORE: 9.0\nAUGMENTATION SCORE: 2.5\n"
    assert gates.parse_score(text) == 2.5

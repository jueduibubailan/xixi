"""Offline end-to-end test for the four-stage pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from vrpar.config import load_config
from vrpar.pipeline import gates
from vrpar.pipeline.orchestrator import start

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "problems" / "cvrptw" / "task.yaml"


def test_offline_pipeline_writes_plan_and_scores(scratch: Path, monkeypatch):
    monkeypatch.chdir(ROOT)
    results = scratch / "results"
    final = scratch / "final"
    config = load_config(
        TASK,
        cwd=ROOT,
        overrides=[
            "llm.backend=mock",
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

    report = json.loads((run_dir / "artifacts" / "runs" / "code_error_report.json").read_text())
    assert report["passed"] is True
    assert report["score"] == 42.0

    mirror = final / config["task"]["slug"]
    assert (mirror / "artifacts" / "generated" / "test_train.py").is_file()
    assert (mirror / "pomo" / "POMO" / "CVRPEnv.py").is_file()


def test_score_parser_uses_last_augmentation_line():
    text = "AUGMENTATION SCORE: 1.0\nNO-AUG SCORE: 9.0\nAUGMENTATION SCORE: 2.5\n"
    assert gates.parse_score(text) == 2.5


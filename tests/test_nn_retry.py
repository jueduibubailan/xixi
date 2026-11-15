"""A rejected batch is retried once when on_invalid_plans=retry."""

from __future__ import annotations

import json
from pathlib import Path

from vrpar.config import load_config
from vrpar.llm.base import LLMResult
from vrpar.llm.mock import MockBackend
from vrpar.pipeline.orchestrator import start

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "problems" / "mvmoe_ovrpl_n50" / "task.yaml"


def test_a_rejected_batch_is_retried_once(scratch: Path, monkeypatch):
    monkeypatch.chdir(ROOT)
    calls = {"nn": 0}
    original = MockBackend.run

    def flaky(self, prompt, cwd, log_path, timeout_s=60):
        if "[STAGE:nn_improve]" in prompt:
            calls["nn"] += 1
            if calls["nn"] == 1:
                # Write no plan file at all: the batch is rejected for its count.
                return LLMResult(
                    text="empty batch",
                    payload={"status": "ok"},
                    returncode=0,
                    backend="mock",
                    log_path=str(log_path),
                )
        return original(self, prompt, cwd, log_path, timeout_s)

    monkeypatch.setattr(MockBackend, "run", flaky)

    results = scratch / "results"
    final = scratch / "final"
    config = load_config(
        TASK,
        cwd=ROOT,
        overrides=[
            "llm.backend=mock",
            "run.setup=false",
            "nn_improve.rounds=1",
            "nn_improve.on_invalid_plans=retry",
            f"workspace.results_dir={results}",
            f"workspace.final_dir={final}",
        ],
    )
    result = start(config, results_dir=results)
    assert result["success"], result
    assert calls["nn"] == 2, "the invalid batch must be retried exactly once"

    run_dir = Path(result["run_dir"])
    ledger = json.loads((run_dir / "artifacts" / "nn_improve" / "plans.json").read_text())
    assert len(ledger["batches"][0]["plans"]) == 12
    assert any("batch rejected; asking the agent" in line for line in
               (run_dir / "logs" / "pipeline.log").read_text().splitlines())

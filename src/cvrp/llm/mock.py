"""Offline backend.

``llm.backend: mock`` swaps the agent for a deterministic stub so the whole
pipeline (stages, gates, executor, grader, web UI) can be exercised without a
Claude Code session.

Everything the stub emits comes from the **problem**:

* the analysis payload from ``problem.yaml:mock.analysis`` (a JSON file shipped
  with the problem, so the offline analysis is real variant knowledge rather than
  placeholders);
* the first-cut solver from ``problem.yaml:mock.template`` -- a small but
  genuinely runnable program, so the acceptance contract and the problem's own
  feasibility checker are exercised for real instead of being stubbed away.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from cvrp.llm.base import LLMBackend, LLMResult
from cvrp.problems import ProblemPlugin


def _stage_of(prompt: str) -> str:
    for marker in (
        "[STAGE:analysis]",
        "[STAGE:code_generation]",
        "[STAGE:code_revise]",
        "[STAGE:feasibility]",
    ):
        if marker in prompt:
            return marker.split(":")[1].rstrip("]")
    return "unknown"


class MockBackend(LLMBackend):
    name = "mock"

    def __init__(self, llm_config: dict | None = None, problem: ProblemPlugin | None = None):
        self.cfg = dict(llm_config or {})
        self.problem = problem

    # -- helpers ----------------------------------------------------------- #

    def _generated_dir(self) -> str:
        if self.problem is None:
            return "artifacts/generated"
        return str((self.problem.spec.get("generated") or {}).get("dir") or "artifacts/generated").rstrip("/")

    def _mock_spec(self) -> dict:
        if self.problem is None:
            return {}
        return dict(self.problem.spec.get("mock") or {})

    def _analysis_payload(self) -> dict:
        mock_spec = self._mock_spec()
        raw = mock_spec.get("analysis")
        if raw and self.problem is not None:
            path = Path(str(raw))
            if not path.is_absolute():
                path = self.problem.root / path
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        # Minimal, honest placeholder: enough to satisfy the analysis gate's shape
        # checks, and explicitly flagged as generic so nobody mistakes it for a
        # real analysis of their variant.
        return {
            "problem_type": "unknown",
            "full_name": "unconfigured problem",
            "summary": "the mock backend found no mock.analysis payload for this problem; "
                       "add one under problems/<slug>/ so offline runs describe the real variant",
            "constraints": [{"id": "?", "name": "unconfigured", "rule": "no constraints declared"}],
            "state_variables": [{"name": "unconfigured", "shape": "n/a", "update": "n/a"}],
            "masking_rules": [{"name": "unconfigured", "condition": "n/a"}],
            "feasibility_checklist": ["declare mock.analysis in problem.yaml", "then re-run"],
            "objective": "unconfigured",
            "output_contract": ["unconfigured"],
            "pomo_modification_plan": [{"file": "unconfigured", "change": "unconfigured"}],
        }

    def _emit_solver(self, cwd: Path) -> list[str]:
        target = Path(cwd) / self._generated_dir()
        target.mkdir(parents=True, exist_ok=True)
        template = self.problem.mock_template if self.problem is not None else None
        if template is None or not Path(template).is_file():
            body = (
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--path')\n"
                "parser.add_argument('--iteration', type=int, default=0)\n"
                "parser.add_argument('--save-solution')\n"
                "args = parser.parse_args()\n"
                "print('the initial process is successful, the initial cost is 0.0')\n"
                "print('the process is successful, the best cost is 0.0')\n"
            )
        else:
            body = Path(template).read_text(encoding="utf-8")
        program = self.problem.program if self.problem is not None else "run.py"
        (target / program).write_text(body, encoding="utf-8")
        (target / "README.md").write_text(
            "# Generated solver (mock backend)\n\n"
            "This placeholder is emitted by `llm.backend: mock` from the problem's\n"
            "`mock.template`. With the real backend the code-generation agent writes\n"
            "the full package here; see `problem.yaml:generated.files` for the list.\n",
            encoding="utf-8",
        )
        return sorted(path.name for path in target.iterdir())

    # -- one turn ---------------------------------------------------------- #

    def run(self, prompt: str, cwd: Path, log_path: Path, timeout_s: int = 60) -> LLMResult:
        started = time.time()
        cwd = Path(cwd)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stage = _stage_of(prompt)

        if stage == "analysis":
            payload = self._analysis_payload()
            target = cwd / "artifacts" / "analysis" / "analysis.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            (target.parent / "plan.md").write_text(
                "# Run plan (mock)\n\n"
                f"1. synthesize the generated package under `{self._generated_dir()}/`\n"
                "2. compile, smoke-run, full-run\n"
                "3. verify feasibility with the problem's own checker\n",
                encoding="utf-8",
            )
        elif stage == "code_generation":
            payload = {"written": self._emit_solver(cwd), "mock": True}
        elif stage == "code_revise":
            payload = {"round": 0, "root_cause": "mock: nothing to fix", "changed_files": []}
            target = cwd / "artifacts" / "revisions" / "round-0.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "# Revision round 0 (mock)\n\nNo error was reported, so no code was changed.\n",
                encoding="utf-8",
            )
        else:
            payload = {"mock": True, "stage": stage}

        text = f"mock turn complete for stage {stage}\n\n```json\n{json.dumps(payload, indent=2)}\n```\n"
        log_path.write_text(text, encoding="utf-8")
        return LLMResult(
            text=text,
            payload=payload,
            returncode=0,
            duration_s=time.time() - started,
            backend=self.name,
            log_path=str(log_path),
        )

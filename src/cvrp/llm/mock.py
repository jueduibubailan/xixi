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

# --------------------------------------------------------------------------- #
# the offline deliverable's shape
# --------------------------------------------------------------------------- #

#: POMO's own training budget. The mock reproduces it verbatim so the engine's
#: correspondence gate really compares values on an offline run.
POMO_TRAINER_PARAMS = """\
    'problem_size': {n},
    'pomo_size': {pomo},
    'epochs': 8100,
    'train_episodes': 10 * 1000,
    'train_batch_size': 64,
    'logging': {{
        'model_save_interval': 500,
        'img_save_interval': 500,
        'log_image_params_1': {{
            'json_foldername': 'log_image_style',
            'filename': 'style_cvrp_20.json',
        }},
        'log_image_params_2': {{
            'json_foldername': 'log_image_style',
            'filename': 'style_loss_1.json',
        }},
    }},
    'model_load': {{
        'enable': False,  # the CVRP100 checkpoint is the starting point
        # 'path': CHECKPOINT_DIR,  # directory path of pre-trained model and log files saved.
        # 'epoch': 30500,  # epoch version of pre-trained model to laod.
    }},
"""

POMO_TESTER_PARAMS = """\
    'problem_size': {n},
    'pomo_size': {pomo},
    'test_episodes': 10 * 1000,
    'test_batch_size': 1000,
    'aug_factor': 8,
    'aug_batch_size': 1000 * 8,
    'logging': {{
        'model_save_interval': 500,
        'img_save_interval': 500,
    }},
"""


def _mock_module(role: str, source: str, n: int, pomo: int) -> str:
    """A stub standing in for an adapted baseline module.

    It exists so the offline deliverable has the right *shape*: the file name, the
    correspondence line, and the symbol a reader would look for. It deliberately
    contains no solver logic -- `run.py` is the executable artifact offline, and the
    real backend replaces these bodies with the adapted baseline code.
    """
    class_name = role[:-3] if role.endswith(".py") else role
    return (
        f"# baseline-correspondence: {source}\n"
        '"""Offline stub emitted by `llm.backend: mock`.\n\n'
        f"Stands in for the adapted `{source}`. The code-generation agent replaces this\n"
        'body with the real adaptation; only the deliverable shape is modelled here.\n"""\n\n'
        f"PROBLEM_SIZE = {n}\n"
        f"POMO_SIZE = {pomo}\n\n\n"
        f"class {class_name}:\n"
        f'    """Placeholder for the adapted {source}."""\n\n'
        "    def __init__(self, *args, **kwargs):\n"
        "        self.args = args\n"
        "        self.kwargs = kwargs\n\n"
        "    def __repr__(self):\n"
        f'        return "{class_name}(mock)"\n'
    )


def _mock_train_entry(source: str, n: int, pomo: int) -> str:
    """A training entry point with POMO's parameter block, and no side effects.

    Importable (the import probe runs it) and honest about being a stub: the training
    loop is behind `__main__`, so nothing is trained when the file is merely read.
    """
    return (
        "# baseline-correspondence: {source}\n"
        '"""Offline stand-in for the training entry point."""\n\n'
        "import argparse\n\n"
        "PROBLEM_SIZE = {n}\n"
        "POMO_SIZE = {pomo}\n\n"
        "trainer_params = {{\n"
        "{params}"
        "}}\n\n"
        "def main():\n"
        '    parser = argparse.ArgumentParser(description="CVRP training (mock backend)")\n'
        "    parser.parse_args()\n"
        '    print("training is not run by the offline backend (problem_size=%d)" % PROBLEM_SIZE)\n'
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    ).format(source=source, n=n, pomo=pomo, params=POMO_TRAINER_PARAMS.format(n=n, pomo=pomo))


def _mock_test_entry(source: str, n: int, pomo: int) -> str:
    """A test entry point that loads the checkpoint from a resolved path."""
    return (
        "# baseline-correspondence: {source}\n"
        '"""Offline stand-in for the test entry point."""\n\n'
        "import argparse\n"
        "import os\n\n"
        "PROBLEM_SIZE = {n}\n"
        "POMO_SIZE = {pomo}\n\n"
        "def _find_checkpoints_dir():\n"
        "    here = os.path.dirname(os.path.abspath(__file__))\n"
        "    for _ in range(6):\n"
        "        candidate = os.path.join(here, 'checkpoints')\n"
        "        if os.path.isdir(candidate):\n"
        "            return candidate\n"
        "        here = os.path.dirname(here)\n"
        "    return 'checkpoints'\n\n"
        "tester_params = {{\n"
        "{params}"
        "}}\n"
        "tester_params['model_load'] = {{'path': _find_checkpoints_dir(), 'epoch': 30500}}\n\n"
        "def main():\n"
        '    parser = argparse.ArgumentParser(description="CVRP testing (mock backend)")\n'
        "    parser.parse_args()\n"
        '    print("testing is not run by the offline backend (problem_size=%d)" % PROBLEM_SIZE)\n'
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    ).format(source=source, n=n, pomo=pomo, params=POMO_TESTER_PARAMS.format(n=n, pomo=pomo))


def _mock_readme(program: str, correspondence: dict, n: int) -> str:
    rows = ["| file | POMO source |", "|---|---|"]
    for name, source in correspondence.items():
        rows.append(f"| `{name}` | `{source}` |" if source else f"| `{name}` | (none) |")
    return (
        "# Generated CVRPTW deliverable (mock backend)\n\n"
        "Emitted by `llm.backend: mock`. With the real backend the code-generation\n"
        "agent adapts the POMO baseline and writes these files; only the deliverable\n"
        "shape is modelled offline.\n\n"
        "## Baseline correspondence\n\n" + "\n".join(rows) + "\n\n"
        f"## Commands\n\n```\npython {program} --path <instance.json> --iteration 100\n"
        f"python train_n{n}.py\npython test_n{n}.py\n```\n"
    )


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
        payload: dict | None = None
        if raw and self.problem is not None:
            path = Path(str(raw))
            if not path.is_absolute():
                path = self.problem.root / path
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
        if payload is not None:
            # The shipped payload is written for the problem's default size. When a
            # task asks for a different scale, overwrite the size fields rather than
            # leaving an n=20 analysis in an n=100 run -- the gate would pass and the
            # offline run would describe the wrong problem.
            if self.problem is not None:
                payload["problem_size"] = self.problem.problem_size
                payload["pomo_size"] = self.problem.scale.effective_pomo_size
                payload["aug_factor"] = self.problem.scale.aug_factor
            return payload
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
        """Write a deliverable that satisfies the generation contract offline.

        With a real backend the code-generation agent produces the whole package. The
        mock has to produce the *shape* as well, or it would be rejected by the
        engine's own correspondence gate: every file adapted from POMO is named after
        the problem, starts with its `# baseline-correspondence:` line, the training
        entry point keeps POMO's own parameter block (so the gate's value checks are
        exercised for real), and nothing extra is written.
        """
        target = Path(cwd) / self._generated_dir()
        target.mkdir(parents=True, exist_ok=True)
        problem = self.problem
        program = problem.program if problem is not None else "run.py"
        correspondence = problem.correspondence if problem is not None else {}
        n = problem.problem_size if problem is not None else 20
        pomo = problem.scale.effective_pomo_size if problem is not None else n
        tag = problem.problem_tag if problem is not None else "CVRP"

        written: list[str] = []
        for name, source in correspondence.items():
            if name in (program, "README.md"):
                continue  # both are written below, and have no POMO source
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name.startswith("train_"):
                path.write_text(_mock_train_entry(source, n, pomo), encoding="utf-8")
            elif name.startswith("test_"):
                path.write_text(_mock_test_entry(source, n, pomo), encoding="utf-8")
            else:
                role = name[len(tag) :] if name.startswith(tag) else name
                path.write_text(_mock_module(role, source, n, pomo), encoding="utf-8")
            written.append(name)

        template = problem.mock_template if problem is not None else None
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
        (target / program).write_text(body, encoding="utf-8")
        written.append(program)

        (target / "README.md").write_text(_mock_readme(program, correspondence, n), encoding="utf-8")
        written.append("README.md")
        return sorted(written)

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

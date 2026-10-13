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
        "[STAGE:retrieval]",
        "[STAGE:analysis]",
        "[STAGE:code_generation]",
        "[STAGE:code_error]",
        "[STAGE:code_revise]",
        "[STAGE:feasibility]",
        "[STAGE:update]",
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
            # The gate requires the retrieval selection to be answered explicitly. The
            # offline backend does not judge relevance, so it says exactly that rather
            # than inventing a use for a record it never read.
            payload["related_knowledge"] = {
                "used": [],
                "rejected": [],
                "note": (
                    "offline backend (`llm.backend: mock`): the knowledge base is not "
                    "consulted; this block exists so the gate and the handoff are exercised"
                ),
            }
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

    # -- records stages ----------------------------------------------------- #

    def _records_config(self) -> dict:
        if self.problem is None:
            return {}
        return dict(self.problem.spec.get("records") or {})

    def _record_config(self) -> dict:
        """The config a records-layer helper needs, built from what the mock was given.

        `make_backend` hands the backend the run id and the project root, so the daily
        helpers can resolve `records/main` and `records/temp` the same way the
        orchestrator does.
        """
        config: dict = {"_project_root": str(self.cfg.get("_project_root") or Path.cwd())}
        if self.cfg.get("_run_id"):
            config["_run_id"] = str(self.cfg["_run_id"])
        merged = self._records_config()
        if merged:
            config["records"] = merged
        return config

    def _main_records_dir(self) -> Path:
        """The knowledge base, resolved the same way the orchestrator resolves it."""
        from cvrp.records import main_dir

        return main_dir(self._record_config())

    def _emit_retrieval(self, cwd: Path) -> dict:
        """Offline retrieval: select whatever the knowledge base actually holds.

        The mock cannot judge relevance, so it selects the files verbatim and says so.
        The point of doing it at all is that the *stage* is exercised end to end offline:
        its gate, the related-record handoff, and analysis's `related_knowledge` block all
        run for real instead of being skipped.
        """
        from cvrp.records import knowledge_files, tag_of

        root = cwd / "artifacts" / "records"
        root.mkdir(parents=True, exist_ok=True)
        tag = tag_of(self.problem)
        files = []
        try:
            files = knowledge_files(self._record_config())
        except Exception:
            files = []

        parsing = {
            "problem_type": tag.upper(),
            "variant_score": "offline stub",
            "concepts": ["mock"],
            "constraints": [],
            "objective": "",
            "keywords": [tag],
            "novelty": "not assessed by the offline backend",
        }
        (root / "description_parsing.json").write_text(
            json.dumps(parsing, indent=2), encoding="utf-8"
        )

        if not files:
            payload = {
                "problem": tag,
                "empty": True,
                "note": "offline backend: the knowledge base holds no records yet",
                "selected": [],
                "rejected": [],
            }
        else:
            selected = []
            for path in files:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
                (root / Path(path).name).write_text(text, encoding="utf-8")
                selected.append(
                    {
                        "source": f"main/{Path(path).parent.name}/{Path(path).name}",
                        "area": Path(path).parent.name,
                        "why": "offline backend selects every available record verbatim",
                        "reuse": ["selected verbatim"],
                        "sections": "all",
                    }
                )
            payload = {
                "problem": tag,
                "empty": False,
                "selected": selected,
                "rejected": [],
                "notes": "offline backend: relevance was not judged; every record was copied",
            }
        (root / "retrieval.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _emit_error_narrative(self, cwd: Path) -> dict:
        """Offline narration: describe the scene from the report the executor wrote."""
        target = cwd / "artifacts" / "runs"
        target.mkdir(parents=True, exist_ok=True)
        report = {}
        for candidate in (target / "s3_run_report.json", cwd / "artifacts" / "runs" / "s3_run_report.json"):
            if candidate.is_file():
                try:
                    report = json.loads(candidate.read_text(encoding="utf-8"))
                except ValueError:
                    report = {}
                break
        findings = report.get("findings") or {}
        passed = bool(report.get("passed"))
        payload = {
            "round": 0,
            "verdict": "passed" if passed else "failed",
            "what_failed": (
                "no gate failed; the static checks reported drift"
                if passed
                else f"step {report.get('failed_step') or 'unknown'} failed"
            ),
            "first_own_frame": None,
            "root_cause_category": "none" if passed else (report.get("error_class") or "unknown"),
            "evidence": [str(report.get("error_message") or "")[:400]] if not passed else [],
            "blast_radius": "not assessed by the offline backend",
            "next_action": "see the executor's revision_hint",
            "confidence": "low",
            "uncertainty": "the offline backend does not read the generated code",
            "mock": True,
            "findings_issues": len(findings.get("issues") or []),
        }
        (target / "s3_error_narrative.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        (target / "s3_error_narrative.md").write_text(
            "# Error narration (mock)\n\n"
            f"- verdict: **{payload['verdict']}**\n"
            f"- what failed: {payload['what_failed']}\n"
            f"- next action: {payload['next_action']}\n",
            encoding="utf-8",
        )
        return payload

    def _emit_main_records(self, cwd: Path) -> dict:
        """Offline update: write the three knowledge files for this problem."""
        from cvrp.records import read_run_records, tag_of

        tag = tag_of(self.problem)
        root = cwd / "records" / "main"
        for area in ("skills", "notes", "attempts"):
            (root / area).mkdir(parents=True, exist_ok=True)

        records_dir = self.cfg.get("_records_dir")
        run_id = str(self.cfg.get("_run_id") or "run")
        found: dict = {}
        if run_id:
            try:
                found = read_run_records(self._record_config(), run_id)
            except Exception:
                found = {}
        del records_dir

        # Offline records, written in the real convention: a `## Contents` block first
        # (the merge guard refuses a file without one -- it would be a stub, not a record),
        # and `_global_skill.md` holding only what is true for any problem.
        (root / "skills" / "_global_skill.md").write_text(
            "# Global skill (offline stub)\n\n"
            "> Scope: **general**. Written by `llm.backend: mock`; it contains no measured\n"
            "> findings, only the pipeline's own invariants.\n\n"
            "## Contents\n\n"
            "- `Verification playbook` — the gate order *(general)*\n\n"
            "## Verification playbook\n\n"
            "- Five gates run in order: `py_compile`, `import_probe`, `correspondence`,\n"
            "  `smoke_run`, `full_run`.\n"
            "- Reproduce one stage with `cvrp stage <name>` rather than re-running the run.\n",
            encoding="utf-8",
        )
        (root / "skills" / f"{tag}_skill.md").write_text(
            f"# Skill — {tag} (offline stub)\n\n"
            f"> Scope: **problem-specific**. Written by `llm.backend: mock`, which does not\n"
            f"> exercise the real solver; treat every claim as unverified.\n\n"
            "## Contents\n\n"
            "- `Verification playbook` — which gate to reproduce *(problem-specific)*\n"
            "- `Proven fixes` — empty until a real run fills it *(problem-specific)*\n\n"
            "## Verification playbook\n\n"
            "- The executor runs the generated program with this project's interpreter.\n"
            "- Five gates run in order: `py_compile`, `import_probe`, `correspondence`,\n"
            "  `smoke_run`, `full_run`.\n\n"
            "## Proven fixes\n\n"
            "_Nothing yet: this file was written by `llm.backend: mock`, which does not\n"
            "exercise the real solver._\n",
            encoding="utf-8",
        )
        (root / "notes" / f"{tag}_notes.md").write_text(
            f"# Notes — {tag} (offline stub)\n\n"
            f"> Scope: **problem-specific**. Written by `llm.backend: mock`.\n\n"
            "## Contents\n\n"
            "- `Identity` — tag and scale *(problem-specific)*\n"
            "- `Traps` — empty until a real run fills it *(problem-specific)*\n\n"
            "## Identity\n\n"
            f"- problem tag: `{tag}`\n"
            f"- scale: n={self.problem.problem_size if self.problem else '?'}\n\n"
            "## Traps\n\n"
            "- None recorded: the offline backend does not run the real solver.\n",
            encoding="utf-8",
        )
        blocks = [
            f"# Attempts — {tag} (offline stub)\n",
            "> Scope: **problem-specific**. Newest last; find one with"
            " `grep -n '^## '`.\n",
            "## Contents\n",
            "- `Blocks` — one section per run, appended *(problem-specific)*\n",
            "## Blocks\n",
            f"## {run_id} · n={self.problem.problem_size if self.problem else '?'} · offline",
            "- task: run with `llm.backend: mock`",
            f"- records found: {', '.join(sorted(found)) or 'none'}",
            "- outcome: the deterministic gates and the contract were exercised for real",
            "- lessons: none from an offline run; see a real backend's attempt block",
        ]
        (root / "attempts" / f"{tag}_attempts.md").write_text(
            "\n".join(blocks) + "\n", encoding="utf-8"
        )
        return {
            "status": "ok",
            "summary": "offline knowledge stub written",
            "files": [
                "records/main/skills/_global_skill.md",
                f"records/main/skills/{tag}_skill.md",
                f"records/main/notes/{tag}_notes.md",
                f"records/main/attempts/{tag}_attempts.md",
            ],
            "mock": True,
        }

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
        elif stage == "retrieval":
            payload = self._emit_retrieval(cwd)
        elif stage == "code_generation":
            payload = {"written": self._emit_solver(cwd), "mock": True}
        elif stage == "code_error":
            payload = self._emit_error_narrative(cwd)
        elif stage == "update":
            payload = self._emit_main_records(cwd)
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

"""The published bundle must be runnable, not just readable.

`workspace/final/<slug>/` is what a user receives. It has to carry the runtime payload
the generated code imports (`pomo/`, including `POMO/utils/utils.py` and the plotting
style JSONs), mirror the worktree layout so those imports resolve, and still leave
`artifacts/generated/` holding exactly the declared deliverable -- the correspondence
gate applies to the generated directory, not to the bundle.

The strongest check here runs the bundled `run.py` in a subprocess from the bundle root,
which is the user's first move. Everything else (`__pycache__`, a training `result/`
tree) must not travel.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cvrp.problems import load_problem
from tests.conftest import PACKAGE_PROBLEMS
from tests.test_pipeline_offline import TASK_YAML

PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")


def _export_spec() -> dict:
    return PROBLEM.spec["resources"]["export"]


@pytest.fixture()
def project(scratch: Path) -> Path:
    from tests.conftest import write_project

    return write_project(scratch / "project", TASK_YAML, problem="cvrptw", with_checkpoint=True)


def _export_spec() -> dict:
    return PROBLEM.spec["resources"]["export"]


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #


def test_the_problem_declares_what_the_export_carries():
    spec = _export_spec()
    assert spec["include"] == ["pomo"], "the toolchain must travel with the code"
    assert "checkpoints" not in spec["include"], (
        "`checkpoints/` is the user's drop box for weights they trained; the export must "
        "not fill it with a reference weight nobody asked for"
    )
    assert spec["mirror_root"] == "."
    assert "result" in spec["exclude"]
    assert "__pycache__" in spec["exclude"]


def test_the_bundled_payload_is_the_runtime_import_surface():
    """`pomo/` has to contain everything the generated code imports."""
    pomo = PROBLEM.root / "pomo"
    assert (pomo / "CVRProblemDef.py").is_file()
    for name in ("CVRPEnv.py", "CVRPModel.py", "CVRPTrainer.py", "CVRPTester.py"):
        assert (pomo / "POMO" / name).is_file()
    # the toolchain the entry points and the trainer/tester import
    assert (pomo / "POMO" / "utils" / "utils.py").is_file()
    styles = pomo / "POMO" / "utils" / "log_image_style"
    assert (styles / "style_cvrp_20.json").is_file()
    assert (styles / "style_loss_1.json").is_file()


# --------------------------------------------------------------------------- #
# the produced bundle
# --------------------------------------------------------------------------- #


def _run_export(project: Path, monkeypatch) -> Path:
    from cvrp.config import load_config
    from cvrp.pipeline.orchestrator import start

    monkeypatch.chdir(project)
    config = load_config(project / "task.yaml", cwd=project)
    result = start(config, results_dir=project / "results")
    assert result["success"], result["reason"]
    return Path(result["run_dir"])


def test_the_export_mirrors_the_worktree_and_bundles_pomo(project: Path, monkeypatch):
    _run_export(project, monkeypatch)
    bundle = project / "workspace" / "final" / "tiny"

    generated = bundle / "artifacts" / "generated"
    assert generated.is_dir(), "the generated files must keep their worktree-relative path"
    assert (generated / "run.py").is_file()

    bundled = bundle / "pomo"
    assert (bundled / "CVRProblemDef.py").is_file()
    assert (bundled / "POMO" / "utils" / "utils.py").is_file()
    assert (bundled / "POMO" / "utils" / "log_image_style" / "style_cvrp_20.json").is_file()

    # the generated directory itself is unchanged: the bundle is not extra generated code
    declared = set(PROBLEM.correspondence)
    present = {path.name for path in generated.glob("*.py")}
    assert present <= declared, f"unexpected files in the deliverable: {present - declared}"

    # nothing from a build or a training run
    assert not any("__pycache__" in str(path) for path in bundle.rglob("*"))
    assert not (bundle / "pomo" / "POMO" / "result").exists()


def test_the_export_readme_explains_how_to_run_it(project: Path, monkeypatch):
    _run_export(project, monkeypatch)
    readme = project / "workspace" / "final" / "tiny" / "artifacts" / "generated" / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "## Running this bundle" in text
    assert "self-contained" in text
    assert "train_n20.py" in text and "test_n20.py" in text
    # and it says which payload was bundled
    assert "`pomo`" in text


def test_the_bundled_program_runs_from_the_bundle_root(project: Path, monkeypatch):
    """The user's first move: cd into the bundle and run the program.

    The mock's `run.py` is self-contained, so this proves the *layout* works (the
    program is found, the instance resolves, both contract lines print). The import
    surface is covered by `test_the_bundled_payload_is_the_runtime_import_surface`
    and by the real-task runs.
    """
    run_dir = _run_export(project, monkeypatch)
    bundle = project / "workspace" / "final" / "tiny"
    instances = run_dir / "artifacts" / "runs"

    # the instance the run scored on
    solution = json.loads((instances / "best_solution.json").read_text(encoding="utf-8"))
    assert solution["routes"]

    instance_dir = project / "instances" / "data" / "cvrptw"
    candidates = sorted(instance_dir.glob("n20/*.json"))
    assert candidates, "the fixture must generate instances"

    proc = subprocess.run(
        [
            sys.executable,
            str(bundle / "artifacts" / "generated" / "run.py"),
            "--path",
            str(candidates[0]),
            "--iteration",
            "5",
        ],
        capture_output=True,
        text=True,
        cwd=str(bundle),
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "the initial process is successful, the initial cost is" in proc.stdout
    assert "the process is successful, the best cost is" in proc.stdout

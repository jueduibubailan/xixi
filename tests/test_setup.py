"""Tests for `workspace.setup` and the nested/recursive import probe.

Two related guarantees:

* the task's declared setup commands actually run, are idempotent, and target the
  **same interpreter the executor uses** -- an install that lands in another venv is
  the classic "setup succeeded, smoke run still reports ModuleNotFoundError";
* a deliverable that mirrors the baseline tree (`pomo/POMO/CVRPEnv.py`) is imported
  under its real dotted path, so the probe still catches a broken tree.
"""

from __future__ import annotations

from pathlib import Path

from cvrp.executor.contract import import_probe
from cvrp.run.setup import run_setup, setup_commands

# --------------------------------------------------------------------------- #
# workspace.setup
# --------------------------------------------------------------------------- #


def test_setup_commands_reads_both_shapes():
    assert setup_commands({}) == []
    assert setup_commands({"workspace": {}}) == []
    assert setup_commands({"workspace": {"setup": "echo hi"}}) == ["echo hi"]
    assert setup_commands({"workspace": {"setup": ["a", "b"]}}) == ["a", "b"]
    # blank entries are not commands
    assert setup_commands({"workspace": {"setup": ["a", "  "]}}) == ["a"]


def test_setup_nothing_declared_is_a_no_op(scratch: Path):
    result = run_setup({"_project_root": str(scratch), "workspace": {}})
    assert result.ran is False
    assert result.ok


def test_setup_runs_once_and_then_skips(scratch: Path, monkeypatch):
    import sys

    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("cvrp.run.setup.subprocess.run", fake_run)
    config = {"_project_root": str(scratch), "workspace": {"setup": [f"{sys.executable} -c pass"]}}

    first = run_setup(config)
    assert first.ran and first.ok
    assert len(seen) == 1

    second = run_setup(config)
    assert second.ran is False
    assert "already satisfied" in second.skipped_reason

    # force re-runs regardless of the fingerprint
    third = run_setup(config, force=True)
    assert third.ran and len(seen) == 2


def test_changed_commands_invalidate_the_fingerprint(scratch: Path, monkeypatch):
    import sys

    monkeypatch.setattr(
        "cvrp.run.setup.subprocess.run",
        lambda argv, **kwargs: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    config = {"_project_root": str(scratch), "workspace": {"setup": [f"{sys.executable} -c pass"]}}
    assert run_setup(config).ran is True
    assert run_setup(config).ran is False

    config["workspace"]["setup"] = [f"{sys.executable} -c pass", f"{sys.executable} -c pass"]
    assert run_setup(config).ran is True


def test_setup_failures_are_reported_not_raised(scratch: Path, monkeypatch):
    def failing(argv, **kwargs):
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "resolution impossible"})()

    monkeypatch.setattr("cvrp.run.setup.subprocess.run", failing)
    import sys

    result = run_setup({"_project_root": str(scratch), "workspace": {"setup": [f"{sys.executable} -c pass"]}})
    assert result.ran is True
    assert result.ok is False
    assert "resolution impossible" in result.failures[0]
    # a failed run must not leave a fingerprint behind, or it would never retry
    assert not (scratch / ".cvrp" / "setup.json").exists()


def test_a_command_that_is_not_on_path_is_reported(scratch: Path):
    result = run_setup(
        {"_project_root": str(scratch), "workspace": {"setup": ["definitely-not-a-binary --flag"]}}
    )
    assert result.ok is False
    assert "not on PATH" in result.failures[0]


def test_setup_targets_the_running_environment(scratch: Path, monkeypatch):
    """`uv pip install` must see VIRTUAL_ENV, or it installs somewhere else."""
    import sys

    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("cvrp.run.setup.subprocess.run", fake_run)
    run_setup({"_project_root": str(scratch), "workspace": {"setup": ["uv pip install nothing"]}})

    env = captured["env"]
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        assert env["VIRTUAL_ENV"] == sys.prefix
    assert captured["cwd"] == str(scratch)


def test_a_plain_python_command_is_resolved_to_this_interpreter(scratch: Path, monkeypatch):
    """A bare `python -m pip install x` must mean *our* python."""
    import sys

    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("cvrp.run.setup.subprocess.run", fake_run)
    run_setup({"_project_root": str(scratch), "workspace": {"setup": ["python -m pip install x"]}})
    assert Path(captured["argv"][0]).resolve() == Path(sys.executable).resolve()


# --------------------------------------------------------------------------- #
# the import probe on a nested deliverable
# --------------------------------------------------------------------------- #


def _nested_package(scratch: Path, files: dict[str, str]) -> Path:
    worktree = scratch / "worktrees" / "code_error"
    generated = worktree / "artifacts" / "generated"
    for relative, body in files.items():
        target = generated / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return worktree


def test_import_probe_walks_a_mirrored_baseline_tree(scratch: Path):
    """The deliverable keeps the baseline's layout, so imports are dotted paths."""
    worktree = _nested_package(
        scratch,
        {
            "pomo/CVRProblemDef.py": "def get_random_problems():\n    return None\n",
            "pomo/POMO/__init__.py": "",
            "pomo/POMO/utils/utils.py": "def create_logger():\n    return None\n",
            "pomo/POMO/CVRPEnv.py": (
                "from pomo.CVRProblemDef import get_random_problems\n"
                "from pomo.POMO.utils.utils import create_logger\n"
                "assert get_random_problems and create_logger\n"
            ),
            "run.py": "print('ok')\n",
        },
    )
    result = import_probe(scratch, worktree)
    assert result.passed, result.message


def test_import_probe_catches_a_broken_tree(scratch: Path):
    """A nested module that imports something not shipped must fail, by name."""
    worktree = _nested_package(
        scratch,
        {
            "pomo/POMO/CVRPEnv.py": "from pomo.POMO.CVRPProblemDef import missing\n",
        },
    )
    result = import_probe(scratch, worktree)
    assert not result.passed
    assert result.error_class == "missing_helper"
    assert "pomo.POMO.CVRPEnv" in result.message


# --------------------------------------------------------------------------- #
# the staged baseline must actually be importable
# --------------------------------------------------------------------------- #


def test_the_staged_baseline_imports_with_the_documented_bootstrap():
    """Run the real thing: torch + POMO, the way a generated `run.py` must.

    This is the check the mock backend cannot make, because its solver never imports
    the baseline. It pins down the requirement that `run.py` needs **three** sys.path
    entries, which is exactly what the baseline entry script's two relative inserts
    amount to from inside `pomo/POMO/`:
    the worktree root (for `pomo.POMO.<X>`), `pomo/` (for the bare
    `from CVRProblemDef import ...` that `CVRPEnv.py` uses), and `pomo/POMO/` itself
    (for `from CVRPEnv import ...` in the tester and trainer).
    """
    import subprocess
    import sys

    from tests.conftest import PACKAGE_PROBLEMS

    pomo = PACKAGE_PROBLEMS / "cvrptw" / "pomo"
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(pomo.parent)!r})\n"
        f"sys.path.insert(0, {str(pomo)!r})\n"
        f"sys.path.insert(0, {str(pomo / 'POMO')!r})\n"
        "import torch\n"
        "from pomo.POMO.CVRPEnv import CVRPEnv\n"
        "from pomo.POMO.CVRPModel import CVRPModel\n"
        "from pomo.POMO.CVRPTester import CVRPTester\n"
        "from pomo.CVRProblemDef import get_random_problems, augment_xy_data_by_8_fold\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(pomo.parent),
        timeout=900,
    )
    assert proc.returncode == 0, (
        "the staged baseline does not import in this environment, so the generated "
        f"solver cannot run either.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok" in proc.stdout

"""Tests for the import gate and the baseline-helper trap.

Two failures motivated these:

* the shipped POMO baseline imports its helpers as
  ``from CVRP.POMO.utils.utils import ...`` (the upstream repository is named
  ``CVRP``), which cannot resolve in a worktree where the baseline is staged as
  ``pomo/``;
* the generated package can therefore be missing (or fail to reach) a helper
  module that the trainer, the tester and both entry points all need.

Without a dedicated gate both cases surface as "the generated code crashed" inside
the smoke run, which names neither the module nor the missing helper.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import PACKAGE_PROBLEMS, baseline_of
from vrpar.executor.contract import import_probe, py_compile
from vrpar.problems import load_problem

PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")


def _worktree_with(scratch: Path, files: dict[str, str]) -> Path:
    worktree = scratch / "worktrees" / "code_error"
    generated = worktree / "artifacts" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (generated / name).write_text(body, encoding="utf-8")
    return worktree


def test_import_probe_passes_for_a_self_sufficient_package(scratch: Path):
    worktree = _worktree_with(
        scratch,
        {
            "env.py": "VALUE = 1\n",
            "run.py": "import env\nprint(env.VALUE)\n",
        },
    )
    assert py_compile(scratch, worktree).passed
    result = import_probe(scratch, worktree)
    assert result.passed, result.message


def test_import_probe_flags_a_missing_helper(scratch: Path):
    """A module that imports a file nobody copied over must fail here, by name."""
    worktree = _worktree_with(
        scratch,
        {
            "trainer.py": "from utils import create_logger  # not copied into the package\n",
        },
    )
    # syntax is fine, so py_compile is happy -- exactly the confusing case
    assert py_compile(scratch, worktree).passed

    result = import_probe(scratch, worktree)
    assert not result.passed
    assert result.error_class == "missing_helper"
    assert "trainer" in result.message
    assert "utils" in result.message


def test_import_probe_flags_the_upstream_package_prefix(scratch: Path):
    """The exact trap: `from CVRP.POMO.utils.utils import *` copied verbatim."""
    worktree = _worktree_with(
        scratch,
        {"Tester.py": "from CVRP.POMO.utils.utils import *\n"},
    )
    result = import_probe(scratch, worktree)
    assert not result.passed
    assert "CVRP" in result.message


def test_import_probe_accepts_a_bootstrapped_path(scratch: Path):
    """Option 1 from the prompt: bootstrap sys.path, then import the real helper."""
    pomo = baseline_of(PROBLEM) / "pomo"
    worktree = _worktree_with(
        scratch,
        {
            "trainer.py": (
                "import os, sys\n"
                f"sys.path.insert(0, {str(pomo.parent)!r})\n"
                "from pomo.POMO.utils.utils import create_logger, copy_all_src\n"
                "assert create_logger and copy_all_src\n"
            ),
        },
    )
    result = import_probe(scratch, worktree)
    assert result.passed, result.message


def test_import_probe_accepts_a_copied_helper(scratch: Path, tmp_path_factory=None):
    """Option 2: copy the helper next to the package and import it locally."""
    helper = (baseline_of(PROBLEM) / "pomo" / "POMO" / "utils" / "utils.py").read_text(
        encoding="utf-8"
    )
    worktree = _worktree_with(
        scratch,
        {
            "utils.py": helper,
            "trainer.py": "from utils import create_logger\nassert create_logger\n",
        },
    )
    result = import_probe(scratch, worktree)
    assert result.passed, result.message


def test_import_probe_warns_but_passes_on_a_non_import_error(scratch: Path):
    """Import-time side effects are not this gate's business."""
    worktree = _worktree_with(
        scratch,
        {"boom.py": "raise RuntimeError('configure me first')\n"},
    )
    result = import_probe(scratch, worktree)
    assert result.passed
    assert "boom" in result.message
    assert "not a dependency problem" in result.message


def test_import_probe_reports_missing_artifacts(scratch: Path):
    result = import_probe(scratch, scratch / "nowhere")
    assert not result.passed
    assert result.error_class == "missing_artifacts"


def test_the_baseline_helper_is_reachable_as_shipped(scratch: Path):
    """The shipped baseline must be importable without the agent doing anything."""
    pomo = baseline_of(PROBLEM) / "pomo"
    worktree = _worktree_with(
        scratch,
        {
            "probe.py": (
                "import sys\n"
                f"sys.path.insert(0, {str(pomo.parent)!r})\n"
                "import pomo.POMO.utils.utils as u\n"
                "assert u.create_logger and u.copy_all_src\n"
            )
        },
    )
    result = import_probe(scratch, worktree)
    assert result.passed, result.message


def test_spec_declares_the_helper_module():
    """`utils.py` is imported from the baseline in place -- never a deliverable.

    It used to be listed as an expected generated file, which taught the agent to
    copy it. The deliverable is the five edited baseline files plus the entry
    points, so the list must not name a helper the variant does not change.
    """
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    files = (plugin.spec.get("generated") or {}).get("files") or []
    assert "utils.py" not in files
    assert "CVRPTWEnv.py" in files

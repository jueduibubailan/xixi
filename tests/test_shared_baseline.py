"""The baseline is part of the project, and every problem shares the one copy.

POMO used to be duplicated inside every bundle (`problems/<name>/seed/pomo` plus a 16 MB
checkpoint), which meant adding a problem copied 15.7 MB. It now lives once at the project
root under `baseline/`, and a bundle carries only its own notes. These tests pin the three
properties that make that safe:

* there is exactly one copy on disk;
* every problem resolves to it (the seeder and the checker agree);
* the notes path a prompt quotes exists in the worktree it describes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import PACKAGE_BASELINE, PACKAGE_PROBLEMS, baseline_of
from vrpar.problems import load_problem
from vrpar.run.worktree import seed_worktree

PROBLEM_NAMES = sorted(
    path.name
    for path in PACKAGE_PROBLEMS.iterdir()
    if path.is_dir() and (path / "task.yaml").is_file()
)


def test_the_shipped_baseline_is_one_copy():
    """One POMO tree and one checkpoint, at the project root."""
    assert (PACKAGE_BASELINE / "pomo" / "CVRProblemDef.py").is_file()
    assert (PACKAGE_BASELINE / "pomo" / "POMO" / "CVRPEnv.py").is_file()
    assert (PACKAGE_BASELINE / "checkpoints" / "checkpoint-30500.pt").is_file()


def test_no_bundle_carries_its_own_copy():
    """A bundle must not duplicate the baseline: that is what the shared directory is for."""
    for name in PROBLEM_NAMES:
        bundle = PACKAGE_PROBLEMS / name
        for duplicated in ("pomo", "checkpoints"):
            assert not (bundle / "seed" / duplicated).exists(), f"{name}/seed/{duplicated}"


def test_there_is_exactly_one_checkpoint_in_the_project():
    """The 16 MB weights exist once. Two copies is the bug this layout removed."""
    found = sorted(
        path
        for path in (PACKAGE_PROBLEMS.parent).rglob("checkpoint-30500.pt")
        if ".test-tmp" not in str(path) and "results" not in str(path)
    )
    assert len(found) == 1, [str(path) for path in found]
    assert found[0].parent.parent == PACKAGE_BASELINE


@pytest.mark.parametrize("name", PROBLEM_NAMES)
def test_every_problem_resolves_the_shared_baseline(name: str):
    """`resources.baseline_dir` must land on the shared directory, with both subdirectories."""
    problem = load_problem(PACKAGE_PROBLEMS / name, slug=name)
    baseline = baseline_of(problem)
    assert baseline == PACKAGE_BASELINE, (name, baseline)
    resources = problem.spec.get("resources") or {}
    for sub in resources.get("baseline_subdirs") or []:
        assert (baseline / str(sub)).is_dir(), f"{name}: {sub}"


@pytest.mark.parametrize("name", PROBLEM_NAMES)
def test_the_notes_path_a_prompt_quotes_exists_in_the_worktree(name: str, scratch: Path):
    """The bug this pins: the prompt said `seed/README.baseline.md` while the file was
    flattened to the worktree root, so an agent was told to read a path that did not exist.

    The notes are now staged from the problem directory at exactly the path the prompts
    quote, and the shared baseline no longer decides where that path points.
    """
    problem = load_problem(PACKAGE_PROBLEMS / name, slug=name)
    notes = str((problem.spec.get("resources") or {}).get("notes") or "")
    assert notes, f"{name} declares no resources.notes"

    worktree = seed_worktree(scratch / "run", "analysis", problem=problem)
    assert (worktree / notes).is_file(), f"{name}: {notes} missing from the worktree"
    # and the source really is the problem, not the shared baseline
    assert (problem.root / notes).is_file()
    assert not (baseline_of(problem) / notes).exists()


@pytest.mark.parametrize("name", PROBLEM_NAMES)
def test_the_worktree_gets_the_shared_baseline(name: str, scratch: Path):
    """Staging must produce the layout the correspondence table addresses."""
    problem = load_problem(PACKAGE_PROBLEMS / name, slug=name)
    worktree = seed_worktree(scratch / "run", "analysis", problem=problem)
    assert (worktree / "pomo" / "POMO" / "CVRPEnv.py").is_file()
    assert (worktree / "checkpoints").is_dir()

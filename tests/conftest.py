"""Shared test fixtures.

Tests do not use pytest's ``tmp_path``: this harness' process temp directory is
not always writable, so the fixtures create their scratch space **inside the
project** instead. Everything under ``<project>/.test-tmp`` is disposable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_ROOT = PROJECT_ROOT / ".test-tmp"
#: The problem bundles: `problems/<name>/{task.yaml, seed/, grader/}`. Kept as the name a
#: test imports because it is what the test body wants -- the *problem*, not where it lives.
PACKAGE_PROBLEMS = PROJECT_ROOT / "problems"
#: The engine's own package, for the loader tests.
LOADER_PACKAGE = PROJECT_ROOT / "src" / "vrpar" / "problems"
#: The shared baseline every problem adapts (POMO + the CVRP100 checkpoint). It lives at
#: the project root, not inside a bundle, so a scratch project needs its own copy.
PACKAGE_BASELINE = PROJECT_ROOT / "baseline"


@pytest.fixture()
def scratch(request) -> Path:
    """A per-test writable directory that lives inside the project."""
    safe = request.node.name.replace("/", "_").replace("\\", "_").replace(":", "_")
    target = SCRATCH_ROOT / safe
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def baseline_of(problem) -> Path:
    """The directory ``resources.baseline_dir`` resolves to, for a loaded problem.

    Mirrors `vrpar.run.worktree._resolve_baseline`: the problem directory first, then the
    project root. The shared baseline lives at the project root, so most problems resolve
    there; a problem that ships its own baseline still wins. Tests go through here rather
    than hardcoding a path, so moving the baseline is a one-line change.
    """
    raw = str((problem.spec.get("resources") or {}).get("baseline_dir") or ".")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    for base in (Path(problem.root), PROJECT_ROOT):
        resolved = base / candidate
        if resolved.is_dir():
            return resolved
    return Path(problem.root) / candidate


def install_problem(project: Path, slug: str = "cvrptw") -> Path:
    """Copy a shipped problem bundle into ``<project>/problems/<slug>/``.

    Tests do this rather than pointing the config at the shipped directory, because a stage
    worktree must contain the problem's baseline and its grader. Copying the whole bundle
    makes the scratch project self-contained, which is exactly the situation a user's
    project is in.
    """
    source = PACKAGE_PROBLEMS / slug
    if not source.is_dir():
        raise FileNotFoundError(f"no such shipped problem: {source}")
    target = Path(project) / "problems" / slug
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)

    # The baseline is shared by every problem, so it does not travel inside the bundle: a
    # scratch project gets its own copy. The checkpoint is excluded -- it is 16 MB, and the
    # tests that need a file there write a stub (`with_checkpoint=True`).
    shared = Path(project) / "baseline"
    if PACKAGE_BASELINE.is_dir() and not shared.is_dir():
        shutil.copytree(
            PACKAGE_BASELINE,
            shared,
            ignore=shutil.ignore_patterns("checkpoints", "__pycache__"),
        )
    return target


def write_project(
    project: Path,
    task_yaml: str,
    *,
    problem: str = "cvrptw",
    with_baseline: bool = True,
    with_checkpoint: bool = False,
) -> Path:
    """Create a minimal but complete project the engine can run inside."""
    project = Path(project)
    (project / "configs").mkdir(parents=True, exist_ok=True)
    (project / "task.yaml").write_text(task_yaml, encoding="utf-8")
    install_problem(project, problem)

    if with_baseline:
        pomo = project / "baseline" / "pomo"
        pomo.mkdir(parents=True, exist_ok=True)
        # `install_problem` already copied the real tree; only write a stub when there is
        # none, so a test that imports POMO still gets the real files.
        if not (pomo / "CVRProblemDef.py").is_file():
            (pomo / "CVRProblemDef.py").write_text("# pomo stub\n", encoding="utf-8")
        (project / "baseline" / "checkpoints").mkdir(parents=True, exist_ok=True)
    if with_checkpoint:
        checkpoint = project / "baseline" / "checkpoints" / "checkpoint-30500.pt"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(b"stub")
    return project


@pytest.fixture(autouse=True)
def _scratch_root() -> None:
    """Make sure the scratch root exists for anything that still asks for temp."""
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)

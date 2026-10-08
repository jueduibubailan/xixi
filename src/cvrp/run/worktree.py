"""Per-stage working copies.

Each stage runs in its own directory under ``<run_dir>/worktrees/<stage>/``,
seeded from the problem's baseline resources, so agents can read the reference
implementation and the notes but can never write back into the read-only source.

The layout mirrors the *same relative paths* the run directory uses, so the
orchestrator can copy stage outputs out (and earlier stages' outputs in) without
any path translation::

    worktrees/<stage>/
    ├─ CVPR.md                       # generated instruction file (think CLAUDE.md)
    ├─ pomo/  checkpoints/           # the problem's baseline resources
    ├─ BASELINE_NOTES.md             # the problem's notes file, if it declares one
    ├─ data/                         # instances + artifact inputs (read-only)
    │  ├─ instances/<problem>/*      # the problem's generated data
    │  └─ artifacts/analysis/...     # earlier stages' outputs
    ├─ artifacts/                    # <-- the agent writes here
    │  ├─ analysis/  generated/  revisions/  runs/
    └─ .cvrp/stage.json              # resolved stage config
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from cvrp.problems import ProblemPlugin

SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".idea", ".venv"}

ARTIFACT_SUBDIRS = ("analysis", "generated", "revisions", "runs")


def _copy_tree(src: Path, dst: Path, skip: set[str] | None = None) -> None:
    """Copy ``src`` into ``dst``, ignoring VCS/cache directories."""
    skip = skip or SKIP_DIR_NAMES
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name in skip:
            continue
        target = dst / entry.name
        if entry.is_dir():
            _copy_tree(entry, target, skip)
        else:
            shutil.copy2(entry, target)


def _resolve_baseline(problem: ProblemPlugin | None, raw: str) -> Path:
    """Resolve ``resources.baseline_dir``.

    Search order: the project root (so a problem can point at a shared
    ``./baseline``), then the problem directory (so a problem can ship its payload
    next to its spec, which is what the shipped ``cvrptw`` example does).
    """
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    candidates: list[Path] = []
    project_root = (problem.spec or {}).get("_project_root") if problem else None
    if project_root:
        candidates.append(Path(str(project_root)) / path)
    if problem is not None:
        candidates.append(problem.root / path)
    candidates.append(Path.cwd() / path)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _baseline_dir(problem: ProblemPlugin | None) -> Path | None:
    if problem is None:
        return None
    raw = (problem.spec.get("resources") or {}).get("baseline_dir")
    if not raw:
        return None
    return _resolve_baseline(problem, str(raw))


def _baseline_entries(problem: ProblemPlugin | None) -> list[str]:
    """Subdirectories plus loose files the worktree must contain."""
    if problem is None:
        return []
    resources = dict(problem.spec.get("resources") or {})
    entries = [str(item) for item in (resources.get("baseline_subdirs") or [])]
    notes = resources.get("notes")
    if notes:
        entries.append(str(notes))
    return entries


def seed_worktree(
    run_dir: Path,
    stage_name: str,
    problem: ProblemPlugin | None = None,
    baseline_dir: Path | None = None,
    instances_dir: Path | None = None,
    artifact_inputs: list[str] | None = None,
    reset: bool = False,
) -> Path:
    """Create (or refresh) the working copy for ``stage_name``.

    Args:
        run_dir: the run directory.
        stage_name: stage identifier; also the directory name.
        problem: the loaded problem plugin; supplies the baseline resources and
            tells us which of them to restore after a reset.
        baseline_dir: explicit baseline root (defaults to
            ``problem.spec.resources.baseline_dir``).
        instances_dir: the problem's data directory, staged under ``data/instances``.
        artifact_inputs: run-relative artifact paths to expose read-only under
            ``data/`` so the agent can read earlier stages' output.
        reset: wipe the worktree first, preserving the agent's own ``artifacts/``
            tree so a revise round can diff against what it produced.
    """
    root = Path(run_dir) / "worktrees" / stage_name
    if reset:
        preserved: list[tuple[Path, Path]] = []
        own_artifacts = root / "artifacts"
        if own_artifacts.is_dir():
            staging = Path(run_dir) / "worktrees" / f".{stage_name}.keep"
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(own_artifacts, staging)
            preserved.append((staging, own_artifacts))
        shutil.rmtree(root, ignore_errors=True)
        for source, target in preserved:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            shutil.rmtree(source, ignore_errors=True)

    first_time = not root.exists()
    for sub in ("artifacts", "data", ".cvrp", "checkpoints"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for sub in ARTIFACT_SUBDIRS:
        (root / "artifacts" / sub).mkdir(parents=True, exist_ok=True)

    baseline = Path(baseline_dir) if baseline_dir is not None else _baseline_dir(problem)
    if baseline is not None and baseline.is_dir():
        if first_time:
            _copy_tree(baseline, root)
        # A reset may have removed the baseline; restore whatever it declared.
        for name in _baseline_entries(problem):
            source = baseline / name
            target = root / name
            if source.exists() and not target.exists():
                if source.is_dir():
                    _copy_tree(source, target)
                else:
                    shutil.copy2(source, target)

    if instances_dir is not None and Path(instances_dir).is_dir():
        target = root / "data" / "instances"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        _copy_tree(Path(instances_dir), target)

    for relative in artifact_inputs or []:
        source = Path(run_dir) / relative
        target = root / "data" / relative
        if not source.exists():
            continue
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        if source.is_dir():
            _copy_tree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    return root


def write_stage_manifest(
    worktree: Path,
    stage_config: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> Path:
    """Drop the resolved stage config inside the worktree for the agent to read."""
    path = Path(worktree) / ".cvrp" / "stage.json"
    payload = {"stage": stage_config}
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# freshness baseline
# --------------------------------------------------------------------------- #
#
# A stage worktree is *reused* across runs and is seeded from the run directory, so
# a previously successful stage leaves its artifacts in place. That makes "does the
# artifact exist?" the wrong question: an agent that crashed -- or never started,
# because the CLI is missing -- would look successful, and the previous round's
# result would be carried forward as this round's evidence.
#
# Timestamps are not a reliable answer either (a worktree can be recreated, and
# seeding preserves mtimes through `copy2`). So the declared artifact paths are
# snapshotted by *content* immediately before the agent runs, and afterwards we ask
# which of them actually changed.

BASELINE_FILENAME = ".cvrp/inputs.json"
_HASH_CHUNK = 1 << 20


def _hash_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()[:32]


def snapshot_paths(base: Path, relatives: list[str]) -> dict[str, str]:
    """Content snapshot of the given paths, keyed relative to ``base``.

    A directory expands to its files. Entries are skipped when unreadable rather
    than raising: a snapshot must never be the reason a run fails.
    """
    base = Path(base)
    snapshot: dict[str, str] = {}
    for relative in relatives:
        target = base / relative
        if not target.exists():
            continue
        entries = [target] if target.is_file() else [item for item in target.rglob("*") if item.is_file()]
        for entry in entries:
            try:
                key = str(entry.relative_to(base)).replace("\\", "/")
            except ValueError:
                continue
            try:
                snapshot[key] = _hash_file(entry)
            except OSError:
                snapshot[key] = "unreadable"
    return snapshot


def write_baseline(worktree: Path, relatives: list[str]) -> Path:
    """Snapshot the declared outputs inside ``worktree`` before an agent turn."""
    payload = {
        "generated_at": time.time(),
        "paths": list(relatives),
        "files": snapshot_paths(worktree, relatives),
    }
    path = Path(worktree) / BASELINE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_baseline(worktree: Path) -> dict[str, str]:
    """The snapshot written by :func:`write_baseline` (empty when absent)."""
    path = Path(worktree) / BASELINE_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = payload.get("files")
    return files if isinstance(files, dict) else {}


def changed_since_snapshot(
    worktree: Path,
    relatives: list[str],
    baseline: dict[str, str],
) -> list[str]:
    """Declared output *groups* whose contents differ from the baseline snapshot.

    ``relatives`` are the stage's declared outputs, and each is judged as a whole:
    a directory counts as changed when it gained a file, lost one, or when any
    file's hash differs. That granularity matters. Comparing single files would
    report "this one file is unchanged" for a stage that produced nothing at all,
    which is exactly the false pass this mechanism exists to prevent.

    An empty baseline reports *nothing* as changed, on purpose: it means no
    snapshot was taken, and in that case the caller's field-level checks stay in
    charge rather than this helper failing the stage for the wrong reason.
    """
    if not baseline:
        return []
    current = snapshot_paths(worktree, relatives)
    changed: list[str] = []
    for relative in relatives:
        prefix = relative.rstrip("/") + "/"
        before = {key: value for key, value in baseline.items() if key == relative or key.startswith(prefix)}
        after = {key: value for key, value in current.items() if key == relative or key.startswith(prefix)}
        if before != after:
            changed.append(relative)
    return changed


def untouched_outputs(
    worktree: Path,
    relatives: list[str],
    baseline: dict[str, str],
) -> list[str]:
    """Declared outputs that are byte-identical to the pre-turn snapshot.

    These are the candidates for "the agent wrote nothing": a pre-existing
    artifact from an earlier run that this attempt never touched. A path that
    *changed* is fresh by definition; a path that did not exist before and does
    not exist now is absent, which is the caller's own check to make.
    """
    if not baseline or not relatives:
        return []
    current = snapshot_paths(worktree, relatives)
    untouched: list[str] = []
    for relative in relatives:
        prefix = relative.rstrip("/") + "/"
        before = {key: value for key, value in baseline.items() if key == relative or key.startswith(prefix)}
        after = {key: value for key, value in current.items() if key == relative or key.startswith(prefix)}
        if before and before == after:
            untouched.append(relative)
    return untouched


def collect_artifacts(run_dir: Path, worktree: Path, relatives: list[str]) -> list[str]:
    """Copy declared artifacts from the worktree into the run directory.

    Agents write under ``worktrees/<stage>/``; the pipeline reads from
    ``<run_dir>/``. Copy-only, so a later stage can never mutate an earlier
    stage's record.

    A source that does not exist is skipped rather than treated as "replace the
    target with nothing": the target may legitimately hold files written by the
    executor (which writes to the run directory directly), and wiping it would
    destroy evidence.
    """
    copied: list[str] = []
    for relative in relatives:
        source = Path(worktree) / relative
        target = Path(run_dir) / relative
        if source.is_dir():
            if not any(source.iterdir()):
                continue  # nothing to copy; leave whatever is already there
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            _copy_tree(source, target)
            copied.append(relative)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative)
    return copied


def exists_artifact(worktree: Path, run_dir: Path, relative: str) -> bool:
    return (Path(worktree) / relative).exists() or (Path(run_dir) / relative).exists()


def reset_generated(run_dir: Path, worktree: Path) -> None:
    """Refresh the agent's ``artifacts/generated`` from the canonical copy."""
    source = Path(run_dir) / "artifacts" / "generated"
    target = Path(worktree) / "artifacts" / "generated"
    if not source.is_dir():
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    _copy_tree(source, target)

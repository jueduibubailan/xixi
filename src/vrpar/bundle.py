"""Per-problem bundles: the `problems/<name>/` directory, CORAL-example style.

A problem is a directory carrying three things and nothing else:

```
problems/<name>/
├─ task.yaml      the problem statement, its mechanisms, and the pipeline it runs
├─ seed/          the starting code an agent edits (reference baseline, weights, generator)
└─ grader/        the checker and scorer
```

That shape is deliberate. The engine never names a variant, so everything a variant knows
lives here, and adding one is a directory rather than a change under `src/vrpar/`. It is also
the shape CORAL's `examples/<name>/` uses (task.yaml + seed/ + grader/), which is what makes
a bundle readable by a human who knows that project.

The `task.yaml` doubles as a runnable task: `vrpar start problems/<name>/task.yaml` runs it
against itself, and a separate task file can point at the bundle with `problem.dir:`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: Files that make a directory a bundle rather than an arbitrary folder.
REQUIRED = ("task.yaml",)


class BundleError(Exception):
    """A bundle is missing, incomplete, or inconsistent."""


def is_bundle(path: Path) -> bool:
    """Is this directory a problem bundle?

    Signalled by the task file, not by a `problem.yaml`: the bundle's spec *is* the task
    file, which is the whole point of the shape.
    """
    path = Path(path)
    return path.is_dir() and all((path / name).is_file() for name in REQUIRED)


def find_bundle(root: Path, name: str) -> Path:
    """Locate `problems/<name>` under `root`, tolerating case in the directory name.

    Case-insensitive on purpose: the file-name tag is uppercase (`CVRPTWEnv.py`) while the
    directory reads better lowercase (`problems/cvrptw/`), and both spellings should find
    the same bundle rather than producing a confusing "not found".
    """
    problems = Path(root) / "problems"
    if not problems.is_dir():
        raise BundleError(f"no problems/ directory under {root}")
    wanted = name.lower()
    for candidate in sorted(problems.iterdir()):
        if not candidate.is_dir():
            continue
        if candidate.name.lower() == wanted and is_bundle(candidate):
            return candidate
    available = sorted(p.name for p in problems.iterdir() if p.is_dir() and is_bundle(p))
    raise BundleError(
        f"no problem bundle named {name!r} under {problems}; available: {', '.join(available) or 'none'}"
    )


def list_bundles(root: Path) -> list[dict[str, Any]]:
    """Every bundle under `problems/`, with the little bit a CLI wants to print."""
    problems = Path(root) / "problems"
    out: list[dict[str, Any]] = []
    if not problems.is_dir():
        return out
    for candidate in sorted(problems.iterdir()):
        if not candidate.is_dir() or not is_bundle(candidate):
            continue
        spec = read_task_file(candidate / "task.yaml")
        task = spec.get("task") or {}
        problem = spec.get("problem") or {}
        grader = spec.get("grader") or {}
        out.append(
            {
                "dir": str(candidate),
                "name": candidate.name,
                "slug": str(problem.get("slug") or candidate.name),
                "tag": str(problem.get("tag") or candidate.name.upper()),
                "task_name": str(task.get("name") or candidate.name),
                "seed": str(candidate / "seed"),
                "grader": str(grader.get("entrypoint") or "grader/grader.py"),
                "has_seed": (candidate / "seed").is_dir(),
                "has_grader": (candidate / "grader").is_dir(),
            }
        )
    return out


def read_task_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise BundleError(f"task file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BundleError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleError(f"{path} must contain a mapping at the top level")
    return data


def expand_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """Replace the `default` shorthands with the real tables.

    Two shorthands keep a task file honest and short:

    * `stages: default` -> `vrpar.config.STAGES` (the seven stages, with roles, prompt keys
      and timeouts);
    * a missing `loop:` -> `vrpar.config.DEFAULT_LOOP`.

    Written as an explicit expansion rather than a merge-time default so that
    `config.resolved.json` still shows the reviewer exactly which stages ran.
    """
    from vrpar.pipeline.spec import DEFAULT_LOOP, STAGES

    out = dict(spec)
    stages = out.get("stages")
    if stages in (None, "default"):
        out["stages"] = [dict(stage) for stage in STAGES]
    if not out.get("loop"):
        out["loop"] = dict(DEFAULT_LOOP)
    return out


def bundle_of(task_file: Path) -> Path | None:
    """The bundle a task file *is*, when it lives in one.

    `problems/<name>/task.yaml` is both the problem's spec and a runnable task, so pointing
    `vrpar start` at it needs no other configuration. Requires `seed/` next to it: a task
    file can legitimately sit inside a problem directory without being the problem's own
    spec (the test suite writes such files), and treating those as bundles would replace a
    perfectly good `problem.yaml` with a missing file.
    """
    task_file = Path(task_file).resolve()
    parent = task_file.parent
    if task_file.name == "task.yaml" and is_bundle(parent) and (parent / "seed").is_dir():
        return parent
    return None


def derive_problem_block(bundle: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """The `problem:` block a task file needs to select this bundle."""
    problem = dict(spec.get("problem") or {})
    slug = str(problem.get("slug") or bundle.name)
    return {"slug": slug, "dir": str(bundle)}

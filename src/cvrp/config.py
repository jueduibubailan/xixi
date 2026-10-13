"""Task configuration: YAML loading, defaults merge, dotlist overrides.

Mirrors CORAL's config ergonomics (``key=value`` dotlist overrides on the
command line) without pulling CORAL in as a hard dependency.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from cvrp.bundle import expand_defaults
from cvrp.pipeline.spec import DEFAULT_LOOP, STAGES

DEFAULT_CONFIG_NAME = "configs/default.yaml"


class ConfigError(RuntimeError):
    """Raised when a task config is missing required structure."""


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge; lists are replaced wholesale, not concatenated."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _coerce(text: str) -> Any:
    """Parse a dotlist value the way YAML would, so ``10`` is an int."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def apply_overrides(config: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """Apply ``a.b.c=value`` overrides onto a nested dict (in place)."""
    for raw in overrides:
        if "=" not in raw:
            raise ConfigError(f"malformed override (expected key=value): {raw!r}")
        dotted, _, value = raw.partition("=")
        keys = [part for part in dotted.split(".") if part]
        if not keys:
            raise ConfigError(f"malformed override key: {raw!r}")
        cursor: dict[str, Any] = config
        for key in keys[:-1]:
            nxt = cursor.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[key] = nxt
            cursor = nxt
        cursor[keys[-1]] = _coerce(value.strip())
    return config


def _derive_root(task_file: Path, cwd: Path | None) -> Path:
    """Pick the directory that relative config paths resolve against.

    The natural anchor is the directory you invoked ``cvrp`` from, which is what
    makes ``cvrp start task.yaml`` and ``cvrp start configs/tasks/cvrptw.yaml``
    behave identically. When the task file sits outside the invocation directory
    (someone ran ``cvrp start /elsewhere/task.yaml``) the task file's own
    directory is the only sensible anchor.
    """
    if cwd is not None:
        working = Path(cwd).expanduser().resolve()
        try:
            task_file.relative_to(working)
            return working
        except ValueError:
            pass
    return task_file.parent


def load_config(
    task_path: str | Path,
    defaults_path: str | Path | None = DEFAULT_CONFIG_NAME,
    overrides: list[str] | None = None,
    project_root: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Load ``task.yaml`` merged on top of ``configs/default.yaml``.

    Args:
        task_path: path to the task yaml.
        defaults_path: global defaults, resolved against the project root.
        overrides: ``key=value`` dotlist overrides from the CLI.
        project_root: explicit override for the anchor directory.
        cwd: the invocation directory; defaults to the process CWD.
    """
    task_file = Path(task_path).expanduser().resolve()
    if project_root:
        root = Path(project_root).expanduser().resolve()
    else:
        invocation = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
        root = _derive_root(task_file, invocation)

    config: dict[str, Any] = {}
    if defaults_path:
        candidate = Path(defaults_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            config = _read_yaml(candidate)

    config = _deep_merge(config, _read_yaml(task_file))
    config = apply_overrides(config, list(overrides or []))

    config["_task_path"] = str(task_file)
    config["_task_dir"] = str(task_file.parent)
    config["_project_root"] = str(root)

    # A bundle carries its own problem spec: `problems/<name>/task.yaml` is both the
    # problem statement and a runnable task, so there is no separate `problem.yaml` to
    # look for. A task file elsewhere can point at a bundle with `problem.dir`, whose spec
    # then fills in everything the task did not state itself.
    _adopt_bundle(config, task_file, root)

    config = expand_defaults(config)
    validate_config(config)
    attach_problem(config)
    return config


def _adopt_bundle(config: dict[str, Any], task_file: Path, root: Path) -> None:
    """Merge a bundle's problem spec into the task config, task keys winning.

    Detecting the bundle from the task file comes first, and it is authoritative: if the
    file *is* `problems/<name>/task.yaml`, that directory is the problem, whatever any
    other key says. Only when the task file is not itself a bundle does an explicit
    `problem.dir` (or a bare bundle name) get consulted, so a task can point at a bundle
    from anywhere.
    """
    from cvrp.bundle import bundle_of, find_bundle, read_task_file

    bundle: Path | None = bundle_of(task_file)
    if bundle is None:
        explicit = str((config.get("problem") or {}).get("dir") or "").strip()
        if explicit:
            candidate = Path(explicit).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            if (candidate / "task.yaml").is_file():
                bundle = candidate
            elif candidate.is_dir() and (candidate / "problem.yaml").is_file():
                # the older shape: a directory with `problem.yaml` is the spec, and the
                # task file that pointed here has already said everything else it needs
                return
            else:
                # a bare name ("cvrptw") is also accepted
                try:
                    bundle = find_bundle(root, explicit)
                except Exception:  # noqa: BLE001 - no bundle by that name; keep the spec
                    bundle = None
    if bundle is None:
        return

    spec = read_task_file(bundle / "task.yaml")
    bundled_problem = dict(spec.get("problem") or {})
    if not bundled_problem:
        return
    # The bundle's own task file *is* the spec, so it wins over anything a task file said
    # about the same keys: the old layout's `src/cvrp/problems/<slug>` paths must not
    # override where the bundle actually lives. A root-level task.yaml that pointed at the
    # legacy location would otherwise silently keep loading it.
    is_own_spec = bundle == Path(task_file).resolve().parent
    merged_problem = dict(bundled_problem)
    if not is_own_spec:
        merged_problem.update(
            {k: v for k, v in (config.get("problem") or {}).items() if not k.startswith("_")}
        )
    merged_problem["dir"] = str(bundle)
    config["problem"] = merged_problem
    del is_own_spec
    # Anything else the bundle's task file declares that this task left out: the loop, the
    # workspace defaults, the grader arguments. The task's own values always win.
    for key, value in spec.items():
        if key in {"task", "problem", "stages", "loop"}:
            continue
        if key not in config or config[key] in (None, {}, []):
            config[key] = value
    if config.get("stages") in (None, "default"):
        config["stages"] = spec.get("stages", "default")
    if not config.get("loop") and spec.get("loop"):
        config["loop"] = spec["loop"]


def _explicit_problem_root(raw: str, config: dict[str, Any]) -> Path | None:
    """The problem directory an explicit `problem.dir` names, or None if it names nothing.

    Both layouts are accepted: a bundle (`task.yaml` + `seed/`) and the older shape (a
    directory carrying `problem.yaml`). Returning None means "not an explicit directory
    after all", so the caller falls back to a search.
    """
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        for anchor in (
            config.get("_project_root"),
            config.get("_task_dir"),
            Path.cwd(),
        ):
            if anchor:
                resolved = Path(str(anchor)) / candidate
                if resolved.is_dir():
                    candidate = resolved
                    break
    if not candidate.is_dir():
        return None
    if (candidate / "task.yaml").is_file() or (candidate / "problem.yaml").is_file():
        return candidate
    return None


def attach_problem(config: dict[str, Any]) -> dict[str, Any]:
    """Load the problem plugin named by ``problem.slug`` and attach it.

    The plugin is exposed twice on purpose:

    * ``config["_problem"]`` -- the live object (hooks, prompts, resources);
    * ``config["problem"]["_spec_*"]`` -- flattened copies of the parts the
      executor's :class:`~cvrp.executor.contract.Contract` needs, so that a
      *serialized* config (``config.resolved.json``) still describes the CLI and
      the acceptance contract without the plugin on disk.
    """
    from cvrp.problems import ProblemError, find_problem_dir, load_problem, scale_from_config

    # Only the keys the *task* set are the user's overrides; anything the plugin
    # spec carries (prompt paths, resource paths, per-variant data parameters)
    # must not leak in, or it would shadow the plugin and confuse `cvrp validate`.
    user_overrides = {
        key: value for key, value in dict(config.get("problem") or {}).items() if not key.startswith("_")
    }
    slug = str(user_overrides.get("slug") or "").strip()
    if not slug:
        raise ConfigError(
            "config is missing problem.slug; it selects the directory under problems/ "
            "that describes the VRP variant (spec, prompts, checks, instances)"
        )

    lookup = dict(config)
    lookup["problem"] = user_overrides
    try:
        scale = scale_from_config(config)
        # An explicit `problem.dir` is taken at its word: it is either the bundle that
        # supplied the spec, or a legacy directory carrying `problem.yaml`. Searching first
        # would silently prefer a same-named directory elsewhere (the engine's own packaged
        # problems), which is exactly the confusion this indirection exists to remove.
        explicit_dir = str(user_overrides.get("dir") or "").strip()
        root = _explicit_problem_root(explicit_dir, config) if explicit_dir else None
        if root is None:
            root = find_problem_dir(slug, lookup)
        plugin = load_problem(root, slug=slug, scale=scale)
    except ProblemError as exc:
        raise ConfigError(str(exc)) from exc
    # The resolved scale is what every later stage derives from: the data
    # generator's sizes, the generated file names, the dimensions the code
    # generation agent is told about, and the executor's instance selection.
    config["scale"] = {
        "problem_size": scale.problem_size,
        "pomo_size": scale.effective_pomo_size,
        "aug_factor": scale.aug_factor,
    }

    # `problem` = the task's own keys + resolved paths. The plugin's full spec
    # stays on the live object, which is the only thing the engine reads.
    merged = dict(user_overrides)
    merged["slug"] = slug
    merged["dir"] = str(plugin.root)
    merged["name"] = plugin.name
    merged["size"] = scale.problem_size
    merged["_spec_program"] = {
        **{k: v for k, v in (plugin.spec.get("program") or {}).items()},
        "program_relpath": plugin.program_relpath,
    }
    merged["_spec_cli"] = plugin.cli
    merged["_spec_data"] = plugin.data
    merged["_spec_resources"] = dict(plugin.spec.get("resources") or {})
    merged["_spec_scorer"] = dict(plugin.spec.get("scorer") or {})
    merged["_spec_generated"] = dict(plugin.spec.get("generated") or {})
    # Give the plugin the anchors it needs to resolve its own relative paths
    # (notably `resources.baseline_dir`) without re-deriving them.
    plugin.spec["_project_root"] = str(root)
    plugin.spec["_task_dir"] = str(config.get("_task_dir") or root)
    config["problem"] = merged
    config["_problem"] = plugin
    return config


# --------------------------------------------------------------------------- #
# validation / accessors
# --------------------------------------------------------------------------- #

#: The pipeline tables live in `cvrp.pipeline.spec`, a leaf module, so both this config and
#: the bundle loader can read them while a config is still being built. Re-exported here
#: because `cvrp.config.STAGES` is the name the rest of the project (and its tests) use.

#: Kept for the older, looser shape: the minimal stage list a hand-written config needs.
DEFAULT_STAGES: list[dict[str, Any]] = [
    {"name": stage["name"], "kind": stage["kind"], "produces": list(stage["produces"])}
    for stage in STAGES
]

__all__ = [
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_LOOP",
    "DEFAULT_STAGES",
    "STAGES",
    "attach_problem",
    "expand_defaults",
    "get_stage",
    "loop_config",
    "task_slug",
]


def validate_config(config: dict[str, Any]) -> None:
    missing = [key for key in ("task", "stages", "problem") if key not in config]
    if missing:
        raise ConfigError(f"config is missing required section(s): {', '.join(missing)}")
    problem = config["problem"]
    if not isinstance(problem, dict) or not str(problem.get("slug") or "").strip():
        raise ConfigError(
            "problem.slug is required: it selects the directory under problems/ that "
            "describes the VRP variant (spec, prompts, checks, instance generator)"
        )
    stages = config["stages"]
    if not isinstance(stages, list) or not stages:
        raise ConfigError("stages must be a non-empty list")
    seen: set[str] = set()
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ConfigError(f"stages[{index}] must be a mapping")
        name = stage.get("name")
        if not name:
            raise ConfigError(f"stages[{index}] is missing 'name'")
        if name in seen:
            raise ConfigError(f"duplicate stage name: {name}")
        seen.add(name)
        kind = stage.get("kind", "agent")
        if kind not in {"agent", "executor"}:
            raise ConfigError(f"stages[{index}].kind must be 'agent' or 'executor'")
    task = config["task"]
    if not isinstance(task, dict) or not task.get("name"):
        raise ConfigError("task.name is required")

    # `scale` was validated while loading the problem (that is what renders the
    # spec); here we only re-check the resolved result is coherent.
    scale = config.get("scale")
    if scale is not None:
        if not isinstance(scale, dict):
            raise ConfigError("scale must be a mapping")
        size = scale.get("problem_size")
        if not isinstance(size, int) or size < 1:
            raise ConfigError(f"scale.problem_size must be a positive integer, got {size!r}")
        pomo = scale.get("pomo_size")
        if pomo is None:
            # `scale.pomo_size` is optional: it defaults to the problem size.
            pomo = size
            scale["pomo_size"] = size
        if not isinstance(pomo, int) or pomo < 1:
            raise ConfigError(f"scale.pomo_size must be a positive integer, got {pomo!r}")
        if pomo > size:
            raise ConfigError(
                f"scale.pomo_size ({pomo}) cannot exceed scale.problem_size ({size})"
            )


def task_slug(config: dict[str, Any]) -> str:
    task = config.get("task", {})
    slug = task.get("slug") or task.get("name") or "task"
    keep = [ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(slug)]
    return "".join(keep).strip("-").lower() or "task"


def project_root(config: dict[str, Any]) -> Path:
    return Path(config["_project_root"])


def resolve_path(config: dict[str, Any], value: str | Path) -> Path:
    """Resolve a config path.

    Resolution order, first hit wins:

    1. absolute paths, unchanged;
    2. **relative to the task file's own directory** -- so a task under
       ``configs/tasks/`` can write ``../../baseline`` and mean the project root;
    3. relative to the project root (the invocation directory);
    4. inside the loaded problem directory, so a problem can ship its own
       baseline, notes and data next to its spec;
    5. inside the installed package (``src/cvrp/prompts/...``).

    Without 4 and 5 a missing template would render an *empty* stage prompt: the
    run still "succeeds", with an agent that was told nothing. Silently
    degrading is never acceptable here.
    """
    if not value:
        return project_root(config) / "_missing_"
    path = Path(value).expanduser()
    if path.is_absolute():
        return path

    task_dir = Path(config.get("_task_dir") or project_root(config))
    package_dir = Path(__file__).resolve().parent

    candidates = [task_dir / path]
    root_candidate = project_root(config) / path
    if root_candidate not in candidates:
        candidates.append(root_candidate)

    problem = config.get("_problem")
    if problem is not None:
        problem_candidate = Path(problem.root) / path
        if problem_candidate not in candidates:
            candidates.append(problem_candidate)

    stripped = Path(*[part for part in path.parts if part != ".."])
    candidates.extend(_package_fallbacks(package_dir, stripped))

    for candidate in candidates:
        if candidate.exists():
            return candidate
    # Nothing exists: report the project-relative location the config asked for.
    return root_candidate


def _package_fallbacks(package_dir: Path, relative: Path) -> list[Path]:
    """Candidate package-relative locations for a config path."""
    fallbacks = [package_dir / relative]
    parts = relative.parts
    for index, part in enumerate(parts):
        if part == "cvrp":
            # e.g. src/cvrp/prompts/x.md -> <package>/prompts/x.md
            fallbacks.append(package_dir.joinpath(*parts[index + 1 :]))
            break
    fallbacks.append(package_dir.joinpath(*parts[-2:]) if len(parts) >= 2 else package_dir)
    return fallbacks


def get_stage(config: dict[str, Any], name: str) -> dict[str, Any]:
    for stage in config.get("stages", []):
        if stage.get("name") == name:
            return stage
    raise ConfigError(f"unknown stage: {name}")


def stage_names(config: dict[str, Any]) -> list[str]:
    return [stage["name"] for stage in config.get("stages", [])]


def loop_config(config: dict[str, Any]) -> dict[str, Any]:
    loop = config.get("loop") or {}
    return {
        "on": list(loop.get("on") or ["code_error", "code_revise"]),
        "until": loop.get("until", "contract_passed"),
        "max_rounds": int(loop.get("max_rounds", 5)),
    }

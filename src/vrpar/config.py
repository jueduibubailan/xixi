"""Task configuration: YAML loading, defaults merge, dotlist overrides.






Supports ``key=value`` dotlist overrides on the


command line without requiring an extra config library.


"""






from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

from vrpar.bundle import expand_defaults
from vrpar.pipeline.spec import DEFAULT_LOOP, STAGES

DEFAULT_CONFIG_NAME = "configs/default.yaml"


#: The environment layer: model runtime, workspace paths, run toggles. Kept out of


#: `task.yaml` so a task file describes the problem and nothing else.


LITELLM_CONFIG_NAME = "litellm_config.yaml"


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






    The natural anchor is the directory you invoked ``vrpar`` from, which is what


    makes ``vrpar start task.yaml`` and ``vrpar start configs/tasks/cvrptw.yaml``


    behave identically. When the task file sits outside the invocation directory


    (someone ran ``vrpar start /elsewhere/task.yaml``) the task file's own


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






    # The environment layer sits between the shipped defaults and the task. Two places


    # are consulted, lowest precedence first: the project root, then the task file's own


    # directory -- so `experiments/n50/task.yaml` is configured by


    # `experiments/n50/litellm_config.yaml`, and the project-wide file stays a fallback.


    for litellm_path in _litellm_layers(task_file, root):


        config = _deep_merge(config, _read_yaml(litellm_path))






    task_config = _read_yaml(task_file)


    # A bundle may carry its own environment layer (its grading budget, or a model it


    # needs). It sits above the project's layer and below the task file, so a task can


    # still override it. Resolved from the bundle, not from the task file's directory:


    # `task.yaml` at the project root can point at `problems/<name>` and inherit it.


    for layer in _bundle_environment_layers(task_config, task_file, root):


        config = _deep_merge(config, _read_yaml(layer))






    config = _deep_merge(config, task_config)


    config = apply_overrides(config, list(overrides or []))






    config["_task_path"] = str(task_file)


    config["_task_dir"] = str(task_file.parent)


    config["_project_root"] = str(root)






    # A bundle carries its own problem spec: `problems/<name>/task.yaml` is both the


    # problem statement and a runnable task, so there is no separate `problem.yaml` to


    # look for. A task file elsewhere can point at a bundle with `problem.dir`, whose spec


    # then fills in everything the task did not state itself.


    _adopt_bundle(config, task_file, root)






    # After the bundle merge: the bundle's spec carries `{slug}`/`{tag}` too, so


    # expanding earlier would let the raw placeholders come back.


    config = expand_placeholders(config)






    config = expand_defaults(config)


    validate_config(config)


    attach_problem(config)


    return config










#: The only placeholders the configuration may use for the problem's identity. They are


#: expanded from `task.slug`, so no config has to hardcode an abbreviation. `{n}` and


#: `{problem}` are left alone: they belong to the scale renderer.


IDENTITY_PLACEHOLDER = re.compile(r"\{(slug|tag)\}")


def derive_tag(slug: str) -> str:


    """`cvrpltw_n50` -> `CVRPLTW50`: the prefix generated file names carry.






    The size marker keeps its number but loses its `n`/`-`/`_`, so two sizes of the same


    variant get distinct tags without the config having to spell either of them out.


    """


    base = re.sub(r"[-_]n?\d+$", "", str(slug)) or str(slug)


    tag = "".join(ch for ch in base.upper() if ch.isalnum())


    return tag or "PROBLEM"


def expand_placeholders(config: dict[str, Any]) -> dict[str, Any]:


    """Expand `{slug}` and `{tag}` throughout the config from `task.slug`."""


    slug = str((config.get("task") or {}).get("slug") or "").strip()


    if not slug:


        if "{slug}" in repr(config) or "{tag}" in repr(config):


            raise ConfigError(


                "the configuration uses {slug}/{tag}, but task.slug is missing: add "


                "`task.slug` (it also names the run directory)"


            )


        return config






    mapping = {"slug": slug, "tag": derive_tag(slug)}










    def walk(value: Any) -> Any:


        if isinstance(value, str):


            return IDENTITY_PLACEHOLDER.sub(lambda m: mapping[m.group(1)], value)


        if isinstance(value, dict):


            return {key: walk(item) for key, item in value.items()}


        if isinstance(value, list):


            return [walk(item) for item in value]


        return value






    return walk(config)


def _litellm_layers(task_file: Path, root: Path) -> list[Path]:


    """The `litellm_config.yaml` files that apply, lowest precedence first.






    The project root's file is the baseline for every task; the task file's sibling wins


    over it. A task that lives in the project root therefore sees the file only once.


    """


    layers: list[Path] = []


    root_file = Path(root) / LITELLM_CONFIG_NAME


    if root_file.is_file():


        layers.append(root_file)


    sibling = Path(task_file).parent / LITELLM_CONFIG_NAME


    if sibling.is_file() and sibling.resolve() != root_file.resolve():


        layers.append(sibling)


    return layers


def _bundle_environment_layers(


    task_config: dict[str, Any], task_file: Path, root: Path


) -> list[Path]:


    """`litellm_config.yaml` files shipped by the bundle this task belongs to.






    Returns at most one path. A missing file is not an error: the bundle simply inherits the


    project's environment layer.


    """


    from vrpar.bundle import bundle_of






    bundle = bundle_of(task_file)


    if bundle is None:


        explicit = str((task_config.get("problem") or {}).get("dir") or "").strip()


        if explicit:


            candidate = Path(explicit).expanduser()


            if not candidate.is_absolute():


                candidate = root / candidate


            if candidate.is_dir():


                bundle = candidate


    if bundle is None:


        return []


    layer = Path(bundle) / LITELLM_CONFIG_NAME


    return [layer] if layer.is_file() else []


def _adopt_bundle(config: dict[str, Any], task_file: Path, root: Path) -> None:


    """Merge a bundle's problem spec into the task config, task keys winning.






    Detecting the bundle from the task file comes first, and it is authoritative: if the


    file *is* `problems/<name>/task.yaml`, that directory is the problem, whatever any


    other key says. Only when the task file is not itself a bundle does an explicit


    `problem.dir` (or a bare bundle name) get consulted, so a task can point at a bundle


    from anywhere.


    """


    from vrpar.bundle import bundle_of, find_bundle, read_task_file






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
    is_own_spec = bundle == Path(task_file).resolve().parent
    merged_problem = dict(bundled_problem)
    if not is_own_spec:
        merged_problem.update(
            {k: v for k, v in (config.get("problem") or {}).items() if not k.startswith("_")}
        )
    merged_problem.setdefault("slug", str((spec.get("task") or {}).get("slug") or bundle.name))
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


















#: What an analysis run may decide from the problem statement alone.


#:


#: Deliberately narrow: identity plus scale. The *structure* of the deliverable


#: (`generated.*`), the CLI contract and the stage table are template/engine decisions --


#: letting a model infer them would make the correspondence gate unverifiable.


PROBLEM_SPEC_FIELDS = ("name",)  # identity (slug/tag) comes from `task:`


PROBLEM_SPEC_SCALE_FIELDS = ("problem_size", "pomo_size", "aug_factor")


def validate_problem_spec(spec: dict[str, Any] | None) -> list[str]:


    """Range-check the fields analysis is allowed to decide. Returns human-readable issues."""


    if not spec:


        return ["problem_spec is empty"]


    issues: list[str] = []


    scale = spec.get("scale") or {}


    size = 0


    if "problem_size" in scale:


        try:


            size = int(scale["problem_size"])


        except (TypeError, ValueError):


            issues.append("problem_spec.scale.problem_size is not an integer")


            size = 0


        if not 1 <= size <= 1000:


            issues.append(f"problem_spec.scale.problem_size={size} is outside 1..1000")


    if size:


        try:


            pomo = int(scale.get("pomo_size") or size)


        except (TypeError, ValueError):


            issues.append("problem_spec.scale.pomo_size is not an integer")


            pomo = size


        if pomo > size:


            issues.append(f"problem_spec.scale.pomo_size={pomo} exceeds problem_size={size}")


    try:


        aug = int(scale.get("aug_factor") or 8)


    except (TypeError, ValueError):


        issues.append("problem_spec.scale.aug_factor is not an integer")


    else:


        if aug not in (1, 2, 4, 8):


            issues.append(f"problem_spec.scale.aug_factor={aug} is not one of 1/2/4/8")


    return issues


def apply_problem_spec(


    config: dict[str, Any], spec: dict[str, Any] | None


) -> tuple[dict[str, Any], list[str]]:


    """Merge an analysis-decided spec into `config` and rebuild the problem plugin.






    Only `PROBLEM_SPEC_FIELDS` and `PROBLEM_SPEC_SCALE_FIELDS` are read, and a slug is


    accepted only when a bundle by that name actually exists -- an invented name would


    otherwise rebuild the plugin against a directory that is not there. Returns the


    (possibly new) config and the issues worth telling the operator about.


    """


    issues = validate_problem_spec(spec)


    if not spec or issues:


        # All or nothing: half-applying a spec whose scale is wrong is harder to reason


        # about than ignoring it and saying why.


        return config, issues






    # Shallow on purpose: the live plugin object is not deep-copyable, and every part we


    # touch (problem/scale/grader) is rebuilt as a fresh dict below.


    merged = {key: value for key, value in config.items() if key != "_problem"}


    problem = dict(merged.get("problem") or {})






    slug = str(spec.get("slug") or "").strip()


    if slug and slug != problem.get("slug"):


        from vrpar.problems import find_problem_dir






        candidate = dict(merged)



        # Probe by name only: an explicit `dir` would answer for the old problem and



        # make every slug look valid.



        candidate["problem"] = {



            **{k: v for k, v in problem.items() if k != "dir"},



            "slug": slug,



        }


        try:


            find_problem_dir(slug, candidate)


        except Exception:  # noqa: BLE001 - any lookup failure means "keep the current one"


            issues.append(


                "problem_spec.slug='{}' has no bundle; keeping '{}'".format(slug, problem.get("slug"))


            )


            slug = ""


    if slug:


        problem["slug"] = slug


        problem.pop("dir", None)


    for field in PROBLEM_SPEC_FIELDS[1:]:


        value = str(spec.get(field) or "").strip()


        if value:


            problem[field] = value


    merged["problem"] = problem






    scale_spec = dict(spec.get("scale") or {})


    scale = dict(merged.get("scale") or {})


    for field in PROBLEM_SPEC_SCALE_FIELDS:


        value = scale_spec.get(field)


        if value not in (None, ""):


            try:


                scale[field] = int(value)


            except (TypeError, ValueError):


                pass


    if scale:


        merged["scale"] = scale






    instance_size = (spec.get("grader") or {}).get("instance_size")


    if instance_size not in (None, ""):


        try:


            grader = dict(merged.get("grader") or {})


            grader["instance_size"] = int(instance_size)


            merged["grader"] = grader


        except (TypeError, ValueError):


            issues.append("problem_spec.grader.instance_size is not an integer")






    # Nothing actually moved (e.g. the only field was a slug we refused): hand the caller



    # its own config back rather than an equivalent copy.



    if not (



        problem != dict(config.get("problem") or {})



        or scale != dict(config.get("scale") or {})



        or (merged.get("grader") or {}) != (config.get("grader") or {})



    ):



        return config, issues








    merged = attach_problem(merged)



    return merged, issues


def attach_problem(config: dict[str, Any]) -> dict[str, Any]:


    """Load the problem plugin named by ``problem.slug`` and attach it.






    The plugin is exposed twice on purpose:






    * ``config["_problem"]`` -- the live object (hooks, prompts, resources);


    * ``config["problem"]["_spec_*"]`` -- flattened copies of the parts the


      executor's :class:`~vrpar.executor.contract.Contract` needs, so that a


      *serialized* config (``config.resolved.json``) still describes the CLI and


      the acceptance contract without the plugin on disk.


    """


    from vrpar.problems import ProblemError, find_problem_dir, load_problem, scale_from_config






    # Only the keys the *task* set are the user's overrides; anything the plugin


    # spec carries (prompt paths, resource paths, per-variant data parameters)


    # must not leak in, or it would shadow the plugin and confuse `vrpar validate`.


    user_overrides = {


        key: value for key, value in dict(config.get("problem") or {}).items() if not key.startswith("_")


    }


    if not str(user_overrides.get("name") or "").strip():


        title = str((config.get("task") or {}).get("name") or "").strip()


        if title:


            user_overrides["name"] = title


    slug = str(user_overrides.get("slug") or "").strip()


    if not slug:


        # A bundled task names its own problem; a thin task may leave `problem.slug` out


        # and rely on `task.slug` (the identity the placeholders expand from).


        slug = str((config.get("task") or {}).get("slug") or "").strip()


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






#: The pipeline tables live in `vrpar.pipeline.spec`, a leaf module, so both this config and


#: the bundle loader can read them while a config is still being built. Re-exported here


#: because `vrpar.config.STAGES` is the name the rest of the project (and its tests) use.






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


    # A task file only carries the statement now, so `task.slug` is usually absent: the


    # problem's slug names the run (stable across the analysis step), the title is last.


    slug = (


        task.get("slug")


        or (config.get("problem") or {}).get("slug")


        or task.get("name")


        or "task"


    )


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


    5. inside the installed package (``src/vrpar/prompts/...``).






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


        if part == "vrpar":


            # e.g. src/vrpar/prompts/x.md -> <package>/prompts/x.md


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



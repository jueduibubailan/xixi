"""Problem plugins: what turns this pipeline from "a CVRPTW generator" into a
generator for *any* VRP variant.

A **problem** is a directory under ``problems/<slug>/`` (or any path given by
``task.yaml:problem.dir``) containing:

```
problems/<slug>/
├─ problem.yaml      # the spec: prompts, CLI flags, data generation, scorer, layout
├─ prompts/*.md      # the stage prompts (what S1 must analyse, what S2 must build)
├─ checks.py         # optional: verify() / extract_best_cost() / score() / build_dataset()
├─ instances.py      # optional: the instance synthesizer referenced by spec.data.generator
├─ problem_context.md# optional: extra "Available inputs" text for the generated CVPR.md
└─ examples/         # optional: sample instances shipped with the problem
```

Everything problem-specific lives there. The engine (``cvrp.pipeline``,
``cvrp.executor``, ``cvrp.web``) never mentions a variant by name.

A ``checks.py`` module may define any of:

| function | contract |
|---|---|
| ``build_dataset(out_dir, spec, force=False) -> list`` | synthesize the problem's data; called when the data dir is empty |
| ``verify(instance_path, solution_path, spec) -> dict`` | independent feasibility verification of a dumped solution |
| ``extract_best_cost(payload, spec) -> float or None`` | how the solver's JSON summary encodes its score |
| ``score(best_cost, verification, spec) -> float`` | map a verified result onto the run score |
| ``revision_hints(spec) -> dict[str, str]`` | error-class to fix suggestion, merged over the generic hints |
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

# Where problem directories are looked up when a config does not name one.
PROBLEMS_ROOT = "problems"

# Default acceptance-contract regexes. A problem inherits these unless its
# program prints something else; the default shape is the one inherited from AFL
# (see DESIGN.md), which is why it is the default at all.
DEFAULT_BEST_REGEX = r"the process is successful, the best cost is (\d+(?:\.\d+)?)"
DEFAULT_INITIAL_REGEX = r"the initial process is successful, the initial cost is (\d+(?:\.\d+)?)"


class ProblemError(RuntimeError):
    """Raised when a problem directory is missing or malformed."""


@dataclass
class ProblemPlugin:
    """A loaded problem: its spec plus the optional hooks from ``checks.py``."""

    slug: str
    root: Path
    spec: dict[str, Any]
    checks_module: Any = None
    _hooks: dict[str, Callable[..., Any]] = field(default_factory=dict)

    # -- identity ---------------------------------------------------------- #

    @property
    def name(self) -> str:
        return str(self.spec.get("name") or self.slug)

    @property
    def kind(self) -> str:
        return str(self.spec.get("kind") or "vrp")

    # -- prompt / role resolution ------------------------------------------ #

    def prompt(self, key: str) -> str:
        """Absolute path of a prompt declared in ``spec.prompts`` (or ``''``)."""
        if not key:
            return ""
        raw = (self.spec.get("prompts") or {}).get(key)
        if not raw:
            return ""
        path = Path(str(raw))
        return str(path if path.is_absolute() else self.root / path)

    def agent_context(self) -> str:
        """Extra "Available inputs" text injected into every stage instruction.

        ``problem.yaml:agent_context`` may be a path to a file (the usual case,
        and the only readable option once a task is distributed) or a literal
        block of text. Both are resolved against the problem directory.
        """
        raw = str(self.spec.get("agent_context") or "").strip()
        if raw:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self.root / raw
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
            return raw
        fallback = self.root / "problem_context.md"
        return fallback.read_text(encoding="utf-8").strip() if fallback.is_file() else ""

    # -- program / CLI contract -------------------------------------------- #

    @property
    def program(self) -> str:
        return str((self.spec.get("generated") or {}).get("program") or "run.py")

    @property
    def program_relpath(self) -> str:
        base = str((self.spec.get("generated") or {}).get("dir") or "artifacts/generated")
        return f"{base.rstrip('/')}/{self.program}"

    @property
    def generated_paths(self) -> list[str]:
        """Run-relative paths the generation stage is expected to produce."""
        generated = self.spec.get("generated") or {}
        base = str(generated.get("dir") or "artifacts/generated").rstrip("/")
        paths = [f"{base}/{name}" for name in (generated.get("files") or [])]
        program = self.program_relpath
        if program not in paths:
            paths.append(program)
        return paths

    @property
    def required_paths(self) -> list[str]:
        """The subset of generated paths without which the contract cannot even run."""
        generated = self.spec.get("generated") or {}
        required = list(generated.get("required") or [])
        if not required:
            required = [self.program]
        return required

    @property
    def cli(self) -> dict[str, Any]:
        return dict(self.spec.get("cli") or {})

    def cli_args(self, instance: Path | None, iterations: int | None, solution_out: Path | None) -> list[str]:
        """Build the argv tail for one program invocation."""
        cli = self.cli
        args: list[str] = []
        path_flag = cli.get("path_arg", "--path")
        if instance is not None and path_flag:
            args.extend([str(path_flag), str(instance)])
        iter_flag = cli.get("iteration_arg", "--iteration")
        if iterations is not None and iter_flag:
            args.extend([str(iter_flag), str(iterations)])
        save_flag = cli.get("save_solution_arg", "--save-solution")
        if solution_out is not None and save_flag:
            args.extend([str(save_flag), str(solution_out)])
        args.extend(str(extra) for extra in (cli.get("extra_args") or []))
        return args

    # -- scorer ------------------------------------------------------------ #

    @property
    def direction(self) -> str:
        return str((self.spec.get("scorer") or {}).get("direction") or "maximize")

    @property
    def score_kind(self) -> str:
        return str((self.spec.get("scorer") or {}).get("kind") or "inverse_cost")

    @property
    def score_scale(self) -> float:
        try:
            return float((self.spec.get("scorer") or {}).get("scale") or 1.0)
        except (TypeError, ValueError):
            return 1.0

    def score(self, best_cost: float | None, verification: dict[str, Any] | None = None) -> float:
        """Score a *verified* result. Problem hook wins over the generic kinds."""
        hook = self._hooks.get("score")
        if hook is not None:
            return float(hook(best_cost, verification or {}, self.spec))
        if best_cost is None:
            return 0.0
        kind = self.score_kind
        scale = self.score_scale
        if kind in ("inverse_cost", "minimize"):
            if best_cost <= 0:
                return 0.0
            return round(scale / float(best_cost), 6)
        if kind == "identity":
            return round(float(best_cost) * scale, 6)
        if kind == "binary":
            return round(scale, 6)
        return round(float(best_cost) * scale, 6)

    # -- data -------------------------------------------------------------- #

    @property
    def data(self) -> dict[str, Any]:
        return dict(self.spec.get("data") or {})

    @property
    def data_dir_name(self) -> str:
        return str(self.data.get("dir") or f"instances/data/{self.slug}")

    @property
    def data_generator(self) -> str:
        return str(self.data.get("generator") or "")

    @property
    def instance_glob(self) -> str:
        """Glob (relative to the data dir) that finds one instance to run on."""
        return str(self.data.get("instance_glob") or "**/*.json")

    def artifact_inputs(self, stage_name: str = "") -> list[str]:
        """Run-relative files copied into a stage worktree as read-only inputs.

        Two shapes are accepted, and both exist because they answer different
        questions:

        * ``data.artifact_inputs: [...]`` -- a flat list applied to every stage;
        * ``data.stage_inputs: {stage: [...]}`` -- per-stage lists, which is what
          you want when a stage should only see its own subset.
        """
        data = self.data
        per_stage = dict(data.get("stage_inputs") or {})
        chosen = per_stage.get(stage_name) if stage_name else None
        if chosen is None:
            chosen = data.get("artifact_inputs") or []
        return [str(item) for item in chosen]

    def data_ready(self, data_dir: Path) -> bool:
        marker = self.data.get("ready_marker")
        if not marker:
            marker = "manifest.json"
        if marker == "none":
            return True
        return (Path(data_dir) / str(marker)).exists()

    def build_dataset(self, out_dir: Path, force: bool = False) -> list[Any]:
        """Synthesize the problem's data using the configured generator."""
        hook = self._hooks.get("build_dataset")
        if hook is not None:
            return list(hook(out_dir, self.spec, force))
        target = self.data_generator
        if not target:
            return []
        parts = target.split(":")
        module_path = parts[0]
        function_name = parts[1] if len(parts) > 1 else "build_dataset"
        module = _load_file(module_path if Path(module_path).is_absolute() else self.root / module_path)
        function = getattr(module, function_name, None)
        if function is None:
            raise ProblemError(f"data.generator {target!r} has no callable {function_name!r}")
        return list(function(out_dir, self.spec, force))

    # -- verification ------------------------------------------------------ #

    def verify(self, instance_path: Path, solution_path: Path) -> dict[str, Any]:
        """Independently verify a dumped solution (never trust the generated code).

        The result is normalised so a problem's hook cannot break the engine's
        contract by returning a dict without ``feasible``: any missing key is
        filled in, and an empty hook result is treated as "not verified" rather
        than as a pass.
        """
        hook = self._hooks.get("verify")
        if hook is None:
            return _default_verify(instance_path, solution_path)
        raw = hook(instance_path, solution_path, self.spec)
        result = dict(raw) if isinstance(raw, dict) else {}
        if not result:
            result = {
                "feasible": False,
                "violations": [
                    f"checks.py:verify() for problem {self.slug!r} returned no result; "
                    "treating the solution as unverified"
                ],
            }
        result.setdefault("skipped", False)
        result.setdefault("violations", [] if result.get("feasible") else ["unspecified violation"])
        result.setdefault("checks", {})
        result.setdefault("feasible", False)
        return result

    def extract_best_cost(self, payload: dict[str, Any] | None) -> float | None:
        """Read the score out of a solution dump. Problem hook wins."""
        hook = self._hooks.get("extract_best_cost")
        if hook is not None:
            return hook(payload or {}, self.spec)
        if not isinstance(payload, dict):
            return None
        for key in ("best_cost", "cost", "score", "objective", "value"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    # -- revision hints ---------------------------------------------------- #

    def revision_hints(self) -> dict[str, str]:
        hook = self._hooks.get("revision_hints")
        if hook is None:
            return {}
        return {str(k): str(v) for k, v in dict(hook(self.spec)).items()}

    # -- mock / offline ---------------------------------------------------- #

    @property
    def mock_template(self) -> Path | None:
        raw = (self.spec.get("mock") or {}).get("template")
        if not raw:
            return None
        path = Path(str(raw))
        return path if path.is_absolute() else self.root / path


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def _load_file(path: Path) -> Any:
    """Import a python file by path, without touching ``sys.path``."""
    path = Path(path)
    if not path.is_file():
        raise ProblemError(f"module not found: {path}")
    spec = importlib.util.spec_from_file_location(f"cvrp_problem_{path.stem}_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        raise ProblemError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOOK_NAMES = ("build_dataset", "verify", "extract_best_cost", "score", "revision_hints")


def load_problem(problem_dir: str | Path, slug: str | None = None) -> ProblemPlugin:
    """Load a problem directory into a :class:`ProblemPlugin`."""
    root = Path(problem_dir)
    if not root.is_dir():
        raise ProblemError(f"problem directory not found: {root}")
    spec_file = root / "problem.yaml"
    if not spec_file.is_file():
        raise ProblemError(f"problem.yaml not found in {root}")
    with spec_file.open("r", encoding="utf-8") as handle:
        spec = yaml.safe_load(handle) or {}
    if not isinstance(spec, dict):
        raise ProblemError(f"problem.yaml root must be a mapping: {spec_file}")

    checks_module = None
    checks_file = root / str(spec.get("checks") or "checks.py")
    if checks_file.is_file():
        try:
            checks_module = _load_file(checks_file)
        except Exception as exc:  # surface a bad plugin loudly, not silently
            raise ProblemError(f"failed to load {checks_file}: {exc}") from exc

    hooks: dict[str, Callable[..., Any]] = {}
    if checks_module is not None:
        for hook_name in HOOK_NAMES:
            candidate = getattr(checks_module, hook_name, None)
            if callable(candidate):
                hooks[hook_name] = candidate

    resolved_slug = str(slug or spec.get("slug") or root.name)
    return ProblemPlugin(slug=resolved_slug, root=root, spec=spec, checks_module=checks_module, _hooks=hooks)


def find_problem_dir(slug: str, config: dict[str, Any] | None = None) -> Path:
    """Locate a problem directory by slug.

    Search order:

    1. an explicit ``problem.dir`` (resolved against the project root, then the
       task file's directory, then the caller's CWD) -- this is how a problem can
       live outside ``problems/``;
    2. ``<project>/problems/<slug>``;
    3. the problems shipped inside the package.
    """
    config = config or {}
    explicit = (config.get("problem") or {}).get("dir")
    candidates: list[Path] = []
    if explicit:
        raw = Path(str(explicit)).expanduser()
        if raw.is_absolute():
            candidates.append(raw)
        else:
            for anchor in (config.get("_project_root"), config.get("_task_dir"), Path.cwd()):
                if anchor:
                    candidates.append(Path(str(anchor)) / raw)
    root = config.get("_project_root")
    if root:
        candidates.append(Path(str(root)) / PROBLEMS_ROOT / slug)
    candidates.append(Path(__file__).resolve().parent / slug)
    for candidate in candidates:
        # A directory only counts as a problem if it actually carries a spec.
        # Without this check a stray directory (for example one holding only a
        # problem's baseline payload) would shadow a perfectly good problem and
        # fail later with a confusing "problem.yaml not found".
        if candidate.is_dir() and (candidate / "problem.yaml").is_file():
            return candidate
    raise ProblemError(
        f"no problem directory for slug {slug!r}; looked in: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def list_problems(config: dict[str, Any] | None = None) -> list[str]:
    """Slugs visible from the project root and from the package."""
    config = config or {}
    found: set[str] = set()
    roots = []
    if config.get("_project_root"):
        roots.append(Path(str(config["_project_root"])) / PROBLEMS_ROOT)
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        if root.is_dir():
            found.update(entry.name for entry in root.iterdir() if (entry / "problem.yaml").is_file())
    return sorted(found)


def _default_verify(instance_path: Path, solution_path: Path) -> dict[str, Any]:
    """Generic fallback: the dump must exist and be parseable JSON.

    Problems are expected to ship a real ``verify``; this exists so that a
    brand-new problem is still runnable (with a clear "unverified" warning rather
    than a crash) while its checks.py is being written.
    """
    if not Path(solution_path).is_file():
        return {
            "feasible": False,
            "skipped": False,
            "violations": [f"solution file missing: {solution_path}"],
            "checks": {},
            "note": "no problem-specific verify() was found; only the file's presence was checked",
        }
    try:
        payload = json.loads(Path(solution_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "feasible": False,
            "skipped": False,
            "violations": [f"solution JSON unreadable: {exc}"],
            "checks": {},
        }
    return {
        "feasible": True,
        "skipped": False,
        "violations": [],
        "checks": {"json_parseable": True},
        "solution_keys": sorted(payload) if isinstance(payload, dict) else [],
        "note": (
            "verified only that the dump is valid JSON -- add checks.py:verify() to this "
            "problem for real feasibility checking"
        ),
    }


__all__ = [
    "DEFAULT_BEST_REGEX",
    "DEFAULT_INITIAL_REGEX",
    "ProblemError",
    "ProblemPlugin",
    "find_problem_dir",
    "list_problems",
    "load_problem",
]

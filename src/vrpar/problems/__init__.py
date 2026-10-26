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
├─ problem_context.md# optional: extra "Available inputs" text for the generated VRPAR.md
└─ examples/         # optional: sample instances shipped with the problem
```

Everything problem-specific lives there. The engine (``vrpar.pipeline``,
``vrpar.executor``, ``vrpar.web``) never mentions a variant by name.

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
import sys
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
    scale: "ScaleParams" = field(default_factory=lambda: ScaleParams())
    required_sizes: list[int] = field(default_factory=list)
    tag: str = ""

    # -- scale ------------------------------------------------------------- #

    @property
    def problem_tag(self) -> str:
        """The identifier used in generated file names (`CVRPTW`)."""
        return self.tag or problem_tag(self.name)

    @property
    def problem_size(self) -> int:
        """Number of *customers* the solver is built for (the "n" of the task).

        Instances carry ``problem_size + 1`` nodes because index 0 is the depot,
        which matches the ``DIMENSION`` header of a Solomon/VRPLIB file. So an
        n=50 task consumes a ``DIMENSION : 51`` instance, and n=100 consumes
        ``DIMENSION : 101``.
        """
        return int(self.scale.problem_size)

    def specs(self) -> dict[str, Any]:
        """The spec with ``{n}``/``{pomo}``/``{aug}``/``{problem}`` substituted."""
        return render_spec(self.spec, self.scale, self.problem_tag)

    def render(self, value: Any) -> Any:
        """Substitute the scale and problem placeholders in any value."""
        return render_spec(value, self.scale, self.problem_tag)

    # -- identity ---------------------------------------------------------- #

    @property
    def name(self) -> str:
        return str(self.spec.get("name") or self.slug)

    @property
    def kind(self) -> str:
        return str(self.spec.get("kind") or "vrp")

    # -- prompt / role resolution ------------------------------------------ #

    def prompt(self, key: str) -> str:
        """Absolute path of a prompt, problem-specific or engine default.

        A problem may override any stage prompt through ``prompts:`` in its own
        ``task.yaml``; when it does not, the engine's shared pack under
        ``src/vrpar/prompts/`` is used. That is what lets a new problem be a directory of
        ``task.yaml`` + ``seed/`` + ``grader/`` instead of carrying nine near-identical
        prompt files.

        ``key`` may be either the stage name (``analysis``) or the pack's basename
        (``s1_analysis``): the first reads well in a task file, the second is what the
        shared pack is named after, and accepting both keeps one lookup instead of two.
        """
        if not key:
            return ""
        raw = (self.spec.get("prompts") or {}).get(key)
        if raw:
            path = Path(str(raw))
            return str(path if path.is_absolute() else self.root / path)
        stem = PROMPT_KEY_ALIASES.get(key, key)
        # Two ways a problem customises a prompt, tried in order: an explicit path in its
        # own `prompts:` block, or a file in the conventional `prompts/` directory next to
        # its spec, named after the pack basename (`prompts/s1_analysis.md`). Anything it
        # does not override comes from the shared pack.
        for candidate in (self.root / "prompts" / f"{stem}.md", DEFAULT_PROMPTS / f"{stem}.md"):
            if candidate.is_file():
                return str(candidate)
        fallback = DEFAULT_PROMPTS / f"{key}.md"
        return str(fallback) if fallback.is_file() else ""

    def agent_context(self) -> str:
        """Extra "Available inputs" text injected into every stage instruction.

        ``problem.yaml:agent_context`` may be a path to a file (the usual case,
        and the only readable option once a task is distributed) or a literal
        block of text. Both are resolved against the problem directory, and both
        get the scale placeholders substituted, so the context can name the task's
        problem size without the file hardcoding it.
        """
        raw = str(self.spec.get("agent_context") or "").strip()
        if raw:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self.root / raw
            if candidate.is_file():
                return self.render(candidate.read_text(encoding="utf-8")).strip()
            return str(self.render(raw)).strip()
        fallback = self.root / "problem_context.md"
        if fallback.is_file():
            return str(self.render(fallback.read_text(encoding="utf-8"))).strip()
        return ""

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

    @property
    def correspondence(self) -> dict[str, str | None]:
        """Deliverable file -> the POMO source it is adapted from (``None`` = none).

        This is what makes "the generated code is one-to-one with POMO" checkable:
        the executor's correspondence gate walks this table, looks for the
        ``# baseline-correspondence:`` first line the generation prompt requires, and
        rejects extra ``.py`` files the table does not mention.
        """
        raw = (self.spec.get("generated") or {}).get("correspondence") or {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str | None] = {}
        for name, source in raw.items():
            # Render defensively: the spec handed to a plugin is already rendered,
            # but a test may load one that is not.
            key = str(self.render(name))
            text = str(source) if source not in (None, "") else ""
            out[key] = str(self.render(text)).strip() or None
        return out

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

    def problem_inputs(self, stage_name: str = "") -> list[str]:
        """Problem-relative paths copied into a stage worktree as read-only inputs.

        Same two shapes as :meth:`artifact_inputs`, but resolved against the *problem*
        directory instead of the run directory. The analysis stage uses it to read the
        bundle's ``grader/`` and decide whether the instances are produced by code, by a
        written description, or not specified at all.
        """
        data = self.data
        raw = data.get("problem_inputs")
        if not raw:
            return []
        if isinstance(raw, dict):
            chosen = raw.get(stage_name) if stage_name else None
            return [str(item) for item in (chosen or [])]
        return [str(item) for item in raw]

    def data_ready(self, data_dir: Path) -> bool:
        """Is the data this *task* needs already on disk?

        The marker alone is not enough: one problem serves several tasks (n=20,
        n=50, n=100) out of a shared data directory, so "manifest.json exists"
        says nothing about *which* sizes exist. Without the size check a task
        asking for n=50 would find the n=20 manifest, skip generation, and then
        run on the wrong instances -- or fail confusingly further down.
        """
        marker = self.data.get("ready_marker")
        if not marker:
            marker = "manifest.json"
        if marker == "none":
            return True
        path = Path(data_dir) / str(marker)
        if not path.exists():
            return False
        needed = set(self.required_sizes)
        if not needed:
            return True
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return True  # an opaque marker: not our business to interpret
        entries = payload.get("instances")
        if not isinstance(entries, list):
            return True
        present: set[int] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                size = int(entry.get("problem_size") or 0)
            except (TypeError, ValueError):
                continue
            relative = entry.get("json")
            # A manifest entry is a promise, not proof: the file has to be there.
            # A hand-deleted or partially-copied data directory would otherwise
            # count as ready and fail later, inside the executor.
            if relative and (Path(data_dir) / str(relative)).is_file():
                present.add(size)
        return needed.issubset(present)

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

#: The engine's shared prompt pack. A problem overrides individual prompts by naming
#: them under ``prompts:`` in its own task file; everything else comes from here, so a
#: new problem directory holds only what is actually specific to it.
DEFAULT_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

#: Stage name -> the shared pack's basename. The reverse direction is how a task file can
#: name a prompt by stage (`analysis:`) rather than by file (`s1_analysis:`).
PROMPT_KEY_ALIASES = {
    "analysis": "s1_analysis",
    "codegen": "s2_codegen",
    "code_generation": "s2_codegen",
    "code_review": "s4_code_review",
    "revise": "s4_revise",
    "code_revise": "s4_revise",
}


def _load_file(path: Path) -> Any:
    """Import a python file by path, with its own directory importable.

    The directory is added to ``sys.path`` for the duration of the import so a bundle's
    grader can be self-contained — `grader/grader.py` importing `cvrptw_gen` from the file
    next to it — without asking every problem to be an installable package. It is removed
    again so one problem's module names cannot shadow another's.
    """
    path = Path(path)
    if not path.is_file():
        raise ProblemError(f"module not found: {path}")
    folder = str(path.parent)
    spec = importlib.util.spec_from_file_location(
        f"vrpar_problem_{path.stem}_{abs(hash(str(path)))}", path
    )
    if spec is None or spec.loader is None:
        raise ProblemError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, folder)
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == folder:
            sys.path.pop(0)
        elif folder in sys.path:
            sys.path.remove(folder)
    return module


HOOK_NAMES = ("build_dataset", "verify", "extract_best_cost", "score", "revision_hints")


# --------------------------------------------------------------------------- #
# scale: the instance size a task asks for
# --------------------------------------------------------------------------- #

#: The value substituted when a task does not declare a scale at all. It matches
#: the long-standing n=20 default, so a config written before scale existed keeps
#: producing exactly the same spec -- and therefore the same file names, data
#: directory and dimensions.
DEFAULT_PROBLEM_SIZE = 20
DEFAULT_AUG_FACTOR = 8


@dataclass(frozen=True)
class ScaleParams:
    """How large the problem is, for one task.

    The point of this being a first-class value is that ``n`` is written **once**,
    in the task file, and everything derived from it -- generated file names
    (``train_n{n}.py``), the instance directory (``n{n}/``), the dimensions handed
    to the code-generation agent, and the data generator's ``sizes`` -- follows.
    """

    problem_size: int = DEFAULT_PROBLEM_SIZE
    pomo_size: int | None = None
    aug_factor: int = DEFAULT_AUG_FACTOR

    @property
    def effective_pomo_size(self) -> int:
        """POMO's start-node count. Defaults to the problem size (the POMO paper's
        setting); a task may override it for a different rollout policy."""
        return int(self.pomo_size or self.problem_size)

    def placeholders(self) -> dict[str, str]:
        return {
            "n": str(self.problem_size),
            "problem_size": str(self.problem_size),
            "pomo": str(self.effective_pomo_size),
            "pomo_size": str(self.effective_pomo_size),
            "aug": str(self.aug_factor),
            "aug_factor": str(self.aug_factor),
            "dimension": str(self.problem_size + 1),
        }


def scale_from_config(config: dict[str, Any] | None) -> ScaleParams:
    """Read ``task.yaml:scale`` (with fallbacks) into :class:`ScaleParams`.

    Accepted spellings, in order of precedence:

    * ``scale: {problem_size: 50, pomo_size: 50, aug_factor: 8}`` -- the documented
      form;
    * ``problem: {size: 50}`` and ``data: {sizes: [50]}`` -- tolerated so an existing
      config that already names a size keeps working;
    * nothing at all -- ``n`` stays at :data:`DEFAULT_PROBLEM_SIZE`.
    """
    config = config or {}
    raw = config.get("scale") or {}
    if not isinstance(raw, dict):
        raise ProblemError(f"task.scale must be a mapping, got {type(raw).__name__}")

    def _pick_int(*candidates: Any) -> int | None:
        for value in candidates:
            if value is None or value == "" or isinstance(value, bool):
                continue
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ProblemError(f"scale values must be integers, got {value!r}") from exc
        return None

    data = config.get("data") if isinstance(config.get("data"), dict) else {}
    sizes = data.get("sizes") if isinstance(data, dict) else None
    first_size = sizes[0] if isinstance(sizes, (list, tuple)) and sizes else None
    problem_block = config.get("problem") if isinstance(config.get("problem"), dict) else {}

    size = _pick_int(raw.get("problem_size"), raw.get("n"), problem_block.get("size"), first_size)
    problem_size = size if size is not None else DEFAULT_PROBLEM_SIZE
    if problem_size < 1:
        raise ProblemError(f"scale.problem_size must be >= 1, got {problem_size}")

    pomo_size = _pick_int(raw.get("pomo_size"), raw.get("pomo"))
    if pomo_size is not None and pomo_size < 1:
        raise ProblemError(f"scale.pomo_size must be >= 1, got {pomo_size}")
    if pomo_size is not None and pomo_size > problem_size:
        raise ProblemError(
            f"scale.pomo_size ({pomo_size}) cannot exceed scale.problem_size ({problem_size}): "
            "POMO starts one rollout per node, so it cannot start more than there are nodes"
        )

    aug = _pick_int(raw.get("aug_factor"), raw.get("aug"))
    aug_factor = aug if aug is not None else DEFAULT_AUG_FACTOR
    if aug_factor < 1:
        raise ProblemError(f"scale.aug_factor must be >= 1, got {aug_factor}")

    return ScaleParams(problem_size=problem_size, pomo_size=pomo_size, aug_factor=aug_factor)


#: Placeholders the spec may use. Anything else is left untouched, so unrelated
#: braces in a spec value cannot be mangled -- and no existing spec contains any.
SCALE_KEYS = ("problem_size", "pomo_size", "aug_factor", "dimension", "n", "pomo", "aug")

#: Placeholders derived from the problem itself rather than from the task's scale.
PROBLEM_KEYS = ("problem", "slug")

#: How many substitution passes to allow. Two keys can reference each other
#: (``dimension`` is derived), so one pass is not always enough; the cap stops a
#: pathological self-reference from looping forever.
_RENDER_PASSES = 5


def problem_tag(name: str) -> str:
    """The identifier a problem uses in generated file names (``CVRPTW``).

    Derived from a display name: uppercase, letters and digits only.
    ``"CVRPTW-on-POMO"`` -> ``"CVRPTWONPOMO"`` -- so a spec that wants a short tag
    (``CVRPTW``) should declare ``tag:`` explicitly. That is what
    :func:`_derive_tag` prefers.
    """
    tag = "".join(ch for ch in str(name).upper() if ch.isalnum())
    return tag or "CVRP"


def _derive_tag(spec: dict[str, Any], scale: ScaleParams, fallback: str) -> str:
    """Pick the generated-file tag: explicit ``tag``, else ``variant_label``, else slug.

    ``tag`` is the documented way; the other two are conveniences so a new problem
    does not have to declare it to get a usable name.
    """
    for key in ("tag", "variant_label", "slug"):
        raw = spec.get(key)
        if not raw:
            continue
        text = str(render_spec(str(raw), scale))
        # `variant_label` is often "CVRPTW-on-POMO": keep the part before the first
        # separator, because that is the variant name and the rest is a strategy note.
        head = text.replace("_", "-").split("-")[0]
        tag = problem_tag(head)
        if tag and tag != "CVRP":
            return tag
    return problem_tag(fallback)


def render_spec(value: Any, scale: ScaleParams | None = None, tag: str = "") -> Any:
    """Substitute scale and problem placeholders throughout a spec.

    Only the keys in :data:`SCALE_KEYS` (plus :data:`PROBLEM_KEYS` when a ``tag`` is
    given) are replaced. A spec value may reference the placeholders at any depth,
    and non-string scalars are copied through unchanged, so this is safe to run over
    an entire ``problem.yaml``.
    """
    placeholders = (scale or ScaleParams()).placeholders()
    if tag:
        placeholders = {**placeholders, "problem": tag, "slug": tag.lower()}
        keys = SCALE_KEYS + PROBLEM_KEYS
    else:
        keys = SCALE_KEYS

    if isinstance(value, dict):
        return {key: render_spec(item, scale, tag) for key, item in value.items()}
    if isinstance(value, list):
        return [render_spec(item, scale, tag) for item in value]
    if isinstance(value, tuple):
        return tuple(render_spec(item, scale, tag) for item in value)
    if not isinstance(value, str):
        return value

    text = value
    for _ in range(_RENDER_PASSES):
        replaced = text
        for key in keys:
            replaced = replaced.replace("{" + key + "}", placeholders[key])
        if replaced == text:
            break
        text = replaced
    return text


def load_problem(
    problem_dir: str | Path,
    slug: str | None = None,
    scale: ScaleParams | None = None,
    required_sizes: list[int] | None = None,
) -> ProblemPlugin:
    """Load a problem directory into a :class:`ProblemPlugin`.

    ``scale`` substitutes the spec's ``{n}``/``{pomo}``/``{aug}`` placeholders and
    is carried on the plugin so prompt rendering, the data generator and the
    executor all agree on the instance size.

    ``required_sizes`` says which instance sizes must be present for the data
    directory to count as ready. It defaults to the task's own problem size.
    """
    root = Path(problem_dir)
    if not root.is_dir():
        raise ProblemError(f"problem directory not found: {root}")
    # Two layouts, one loader: a bundle puts its spec in `task.yaml` under `problem:`,
    # the older shape keeps a `problem.yaml` beside its prompts and checker.
    spec_file = root / "problem.yaml"
    if spec_file.is_file():
        with spec_file.open("r", encoding="utf-8") as handle:
            spec = yaml.safe_load(handle) or {}
    else:
        bundle_file = root / "task.yaml"
        if not bundle_file.is_file():
            raise ProblemError(
                f"no spec in {root}: expected either problem.yaml or a bundle's task.yaml"
            )
        with bundle_file.open("r", encoding="utf-8") as handle:
            bundle = yaml.safe_load(handle) or {}
        spec = dict(bundle.get("problem") or {}) if isinstance(bundle, dict) else {}
        if not spec:
            raise ProblemError(f"{bundle_file} has no `problem:` block to use as a spec")
        # A bundle's own task file is the authority on everything the problem declares;
        # the caller's task file has already been merged on top by `config`.
        spec.setdefault("slug", root.name)
    if not isinstance(spec, dict):
        raise ProblemError(f"the problem spec must be a mapping: {root}")

    # Render before anything reads the spec: the checks module is handed the same
    # rendered mapping, so a hook that reads ``spec["data"]["sizes"]`` sees the
    # task's size rather than the template's. The problem tag comes from the spec's
    # own ``tag``/``variant_label``, hence the two-step render.
    effective_scale = scale or ScaleParams()
    tag = _derive_tag(spec, effective_scale, root.name)
    spec = render_spec(spec, effective_scale, tag)

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
    sizes = [int(size) for size in (required_sizes or [effective_scale.problem_size])]
    return ProblemPlugin(
        slug=resolved_slug,
        root=root,
        spec=spec,
        checks_module=checks_module,
        _hooks=hooks,
        scale=effective_scale,
        required_sizes=sizes,
        tag=tag,
    )


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
    # The bundles shipped with the project, so a task may name a problem without pointing
    # at its directory. Checked after the project's own, so a project copy always wins.
    for anchor in (config.get("_project_root"), Path.cwd()):
        if anchor:
            candidates.append(Path(str(anchor)) / "problems" / slug)
    candidates.append(Path(__file__).resolve().parent / slug)
    for candidate in candidates:
        # A directory counts as a problem if it carries a spec, in either layout: a bundle
        # (`task.yaml` plus `seed/`, the shape a new problem is written in) or the older
        # standalone `problem.yaml`. Without this check a stray directory -- one holding
        # only a problem's baseline payload, say -- would shadow a good one and fail later
        # with a confusing message from the loader.
        if not candidate.is_dir():
            continue
        if (candidate / "problem.yaml").is_file() or (candidate / "task.yaml").is_file():
            return candidate
    raise ProblemError(
        f"no problem directory for slug {slug!r}; looked in: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def list_problems(config: dict[str, Any] | None = None) -> list[str]:
    """Problem directories visible from the project root and from the package.

    A directory counts when it carries a spec in either layout: a bundle (`task.yaml`) or
    the older standalone `problem.yaml`. Both the project's `problems/` and the engine's own
    copy are scanned, so a project sees the shipped problems as well as its own.
    """
    config = config or {}
    found: set[str] = set()
    roots: list[Path] = []
    if config.get("_project_root"):
        roots.append(Path(str(config["_project_root"])) / PROBLEMS_ROOT)
        roots.append(Path(str(config["_project_root"])) / "problems")
    roots.append(Path.cwd() / PROBLEMS_ROOT)
    roots.append(Path.cwd() / "problems")
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            if (entry / "problem.yaml").is_file() or (entry / "task.yaml").is_file():
                found.add(entry.name)
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

"""Scaffold a new VRP-variant example.

``cvrp new-problem <slug>`` produces a *complete* problem directory whose
placeholders already satisfy every contract the engine relies on:

* ``problem.yaml`` -- a spec whose paths are correct out of the box;
* ``prompts/*.md`` -- the variant description embedded, with the rest of the
  contract already written;
* ``checks.py`` -- ``verify`` / ``build_dataset`` / ``score`` hooks, with TODO
  markers exactly where variant knowledge has to go;
* ``instances.py`` -- a generator stub that fails loudly instead of emitting
  unsolvable data;
* ``problem_context.md`` -- what the agents are told about their inputs;
* ``mock_analysis.json`` -- so ``llm.backend=mock`` works immediately.

**Substitution uses ``string.Template`` (``$name``), not ``str.format``.** That is
deliberate: these templates are full of literal braces -- JSON examples, the
engine's own ``{placeholder}`` vocabulary, f-string-ish code -- and ``format``
would either choke on them or require error-prone double-brace escaping. With
``$name``, braces are simply not special, so a template can contain both
``{"routes": [...]}`` and ``{failed_step}`` without ambiguity.

The scaffold is deliberately *not* a solver: it is the shape of the answer, and
the TODOs mark the three places only the variant author can fill in (feasibility
check, data, scoring).
"""

from __future__ import annotations

import string
from pathlib import Path

PROBLEM_YAML = """# $name problem spec.
#
# Read by `cvrp.problems.load_problem`. This file is the only place this variant
# is described; the engine (pipeline/executor/web) never names a variant.

slug: $slug
name: "$name"
kind: vrp
variant_label: "$variant_label"
checks: checks.py

prompts:
  analysis: prompts/s1_analysis.md
  analysis_judge: prompts/s1_analysis_judge.md
  codegen: prompts/s2_codegen.md
  codegen_judge: prompts/s2_codegen_judge.md
  error_analyse: prompts/s4_error_analyse.md
  revise: prompts/s4_revise.md

agent_context: problem_context.md

generated:
  dir: artifacts/generated
  program: run.py
  required: [run.py]
  modules: []
  entries: {}
  files:
    - run.py
    - README.md
  dimensions: {}

cli:
  path_arg: "--path"
  iteration_arg: "--iteration"
  save_solution_arg: "--save-solution"
  extra_args: []

program:
  best_regex: 'the process is successful, the best cost is (\\d+(?:\\.\\d+)?)'
  initial_regex: 'the initial process is successful, the initial cost is (\\d+(?:\\.\\d+)?)'
  expected_lines:
    - "the initial process is successful, the initial cost is {cost}"
    - "the process is successful, the best cost is {cost}"
  accepts: ["--path", "--iteration", "--save-solution"]

data:
  dir: instances/data/$slug
  instance_glob: "**/*.json"
  ready_marker: manifest.json
  generator: instances.py:build_dataset
  sizes: [20]
  count_per_size: 3
  seed: 1234

resources:
  # TODO: point this at the reference implementation the agents must modify.
  # Relative paths resolve against the project root.
  baseline_dir: $baseline
  baseline_subdirs: []
  notes: BASELINE_NOTES.md

acceptance:
  require_solution_dump: true
  solution_key: routes

scorer:
  # inverse_cost: score = scale / best_cost   (minimisation)
  # identity:     score = scale * best_cost   (maximisation)
  # binary:       score = scale on any verified result
  kind: inverse_cost
  scale: 1.0
  direction: maximize

mock:
  analysis: mock_analysis.json
  # template: mock_solver.py     # add one to make `llm.backend=mock` runnable
"""

ANALYSIS_PROMPT = """# S1 - Problem analysis (analysis agent)

You are the **analysis stage**. Nothing downstream can recover from a vague
analysis, so your output is a machine-readable contract, not prose.

Problem under analysis: **$name** (`$variant_label`).

> The engine substitutes `{lower_snake_case}` placeholders into this file before
> the agent sees it. The vocabulary lives in
> `src/cvrp/pipeline/stages.py::stage_variables`; the useful ones here are
> `{task_description}`, `{baseline_notes}`, `{checkpoint}`, `{generated_dir}`,
> `{problem_name}` and `{worktree}`.

## The variant you must analyse

$task_description

## What you are given

- The instances under `data/instances/` - the exact data the synthesized solver
  will be run on. Read at least one of them.
- The baseline notes file staged in this worktree - the authoritative description
  of the baseline and of the acceptance contract.
- The baseline sources staged in this worktree (listed under "Available inputs").

## What to produce

Write **`artifacts/analysis/analysis.json`** with these keys (all required):

- `problem_type`, `full_name`, `summary`
- `constraints`: one entry per rule, each with a precise predicate
- `state_variables`: every new per-step or per-instance quantity the baseline
  needs, with shape and update rule
- `masking_rules`: **the core deliverable** - each entry has `name`, `condition`
  (the exact predicate that makes an action illegal), `tensor`, `shape`, and any
  `exception` that keeps the rollout from deadlocking
- `feasibility_checklist`: independently checkable statements, verifiable from the
  instance data alone
- `objective`, `output_contract`
- `checkpoint_compatibility`: what must not change for the pretrained weights to
  keep loading
- `run_plan`, `open_questions`

## Rules that decide whether this stage is accepted

1. **One rule per constraint.** A constraint that can make an action illegal must
   appear in `masking_rules`, or the gate fails.
2. **Exceptions must be explicit.** Name the action that stays legal when every
   other action is masked, and which tensor carries the exception.
3. **Feasibility must be checkable by a third party** from the instance data
   alone.
4. **The plan must name real symbols** that exist in the staged baseline.
5. **Never change the pretrained module's input dimension.**
6. **No instance-specific numbers.**
7. Write `artifacts/analysis/plan.md` with the same plan in narrative form.
"""

CODEGEN_PROMPT = """# S2 - Code generation (code generation agent)

You are the **code generation stage**. Generate a **package of cooperating
modules** that modifies the staged baseline for the variant below.

Problem: **$name** (`$variant_label`).

> Engine placeholders available here: `{generated_dir}`, `{generated_file_lines}`,
> `{dimension_lines}`, `{program}`, `{program_path}`, `{cli_accepts}`,
> `{contract_lines}`, `{checkpoint}`, `{baseline_notes}`.

## The variant

$task_description

## Read first

1. `data/artifacts/analysis/analysis.json` - the contract from S1. Its
   `masking_rules`, `state_variables` and `pomo_modification_plan` are binding.
2. The baseline notes file staged in this worktree - the baseline map and the
   exact acceptance contract.
3. The staged baseline sources (listed under "Available inputs"). Read before
   writing.
4. `data/instances/` - the schema you must consume.

## What to produce, inside `{generated_dir}/`

{generated_file_lines}
{generated_entry_lines}

`README.md` must list the files, how to train, how to test, and what was
implemented versus deferred.

## Hard requirements

1. **The checkpoint must still load.** Keep the pretrained module's input
   dimensions unchanged, load with `load_state_dict(..., strict=False)`, and
   report skipped keys in your summary.
2. **New information enters the decoder context, not the encoder.**
3. **Masking is built as the logical OR of the rules named in `analysis.json`**,
   with every declared exception respected. One helper per rule.
4. **`{program}` is the acceptance surface.** It must accept `{cli_accepts}`,
   print exactly:
{contract_lines}
   and, when the save-solution flag is given, write its best solution as JSON in
   the shape documented in `problem_context.md`.
5. **CPU-capable.**
6. **No instance-specific constants.**
7. **No new dependencies.**

## Scale for this run

{dimension_lines}
"""

ANALYSIS_JUDGE = """# S1 judge - audit the analysis

You are the **analysis auditor**. Falsify the analysis, do not praise it. Check,
in order, and stop at the first defect: coverage (every constraint has a mask),
soundness (no mask depends on unavailable state), deadlock-freedom (some action
stays legal), checkpoint safety (the pretrained input dimension is unchanged),
verifiability of the feasibility checklist, and grounding (named symbols exist).

Then answer with **exactly one line**:

```
jud1: <pass|fail> :: <defect 1> ; <defect 2> :: <the single most important fix>
```
"""

CODEGEN_JUDGE = """# S2 judge - audit the generated package

You are the **code auditor**. Check, in order: presence of the files declared
under `generated.files`; runnability of `{program}` against `{cli_accepts}` and
the contract lines under `program.expected_lines`; checkpoint compatibility; mask
composition (all rules from `analysis.json`, and every declared exception
respected); the domain semantics from `{baseline_notes}`; absence of hardcoded
instance constants; determinism.

Then answer with **exactly one line**:

```
jud1: <pass|fail> :: <issue 1> ; <issue 2> :: <the single most important fix>
```
"""

ERROR_ANALYSE = """# S4a - Error attribution (revise agent, step 1)

You are the **error analyst**. Explain the failure precisely; do not fix it yet.

- Failing step: `{failed_step}`
- Error class: `{error_class}`
- Executor hint: `{revision_hint}`
- Evidence: `{run_report_path}`

```
{error_message}
```

Find the first frame that belongs to the generated package, name the root cause
category (shape / masking / checkpoint / contract / interface / resource /
compile), point at the file and function, and state the smallest sufficient fix.

**Exactly one line**, no extra line breaks:

```
jud1: <category> :: <file:function> :: <why it broke> :: <smallest fix>
```
"""

REVISE = """# S4 - Revision round {round_index} (revise agent)

You are the **revision stage**. Make the smallest change that makes the failure
stop, without breaking what already worked.

## What failed

- Failing step: `{failed_step}`
- Error class: `{error_class}`
- Attribution: `{revision_hint}`
- Evidence: `{run_report_path}`

```
{error_message}
```

## Rules

1. Change only what the failure requires; keep file names and signatures stable.
2. Never relax the acceptance contract or delete the solution dump.
3. Do not weaken masking to make the run pass - the feasibility check will send
   it straight back.
4. Reproduce before you finish:
   `python {program_path} {cli_accepts} {cli_iteration_flag} 5`
5. Write `artifacts/revisions/round-{round_index}.md` with the root cause, the
   files changed, and the smoke command you ran with its output.

The expected solution-dump shape is documented in `problem_context.md`.
"""

PROBLEM_CONTEXT = """Available inputs in this worktree:

- the baseline sources staged from the reference implementation (see the
  "Available inputs" section above);
- `data/instances/` - the generated instance data for this variant;
- the baseline notes file - the baseline map, the domain semantics the pipeline
  verifies against, and the acceptance contract.

Solution dump expected from the save-solution flag:

```json
{"routes": [[0, 3, 7, 0], [0, 5, 0]], "cost": 12.34}
```

Adapt this shape to your variant and keep this file in sync: the pipeline's
checker reads exactly what you describe here.
"""

CHECKS = '''"""$name problem plugin: the inference-time checks used by the pipeline.

Loaded by ``cvrp.problems.load_problem``. Everything here must be *independent* of
the generated solver: it re-implements the semantics and recomputes the score
from the instance data, because "no traceback" is not "correct".

Three TODOs are the whole job of writing a new variant:
  1. ``verify``          -- decide whether a dumped solution is feasible
  2. ``build_dataset``   -- synthesize or fetch the instance data
  3. ``score``           -- map a verified result onto the run score (optional;
                            ``problem.yaml:scorer`` covers the common cases)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def verify(instance_path: str | Path, solution_path: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Decide whether the solution satisfies every constraint.

    TODO: implement the real check. Return::

        {"feasible": bool, "skipped": False, "violations": [str], "checks": {name: bool}}

    The engine treats ``feasible=False`` as a hard failure when
    ``grader.args.require_feasible`` is set, so a wrong ``True`` here is the one
    mistake that silently corrupts every score.
    """
    solution_file = Path(solution_path)
    if not solution_file.is_file():
        return {
            "feasible": False,
            "skipped": False,
            "violations": [f"solution file missing: {solution_file}"],
            "checks": {},
        }
    try:
        payload = json.loads(solution_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "feasible": False,
            "skipped": False,
            "violations": [f"solution JSON unreadable: {exc}"],
            "checks": {},
        }
    # TODO: replay the routes/assignment against `instance_path` and check every
    # constraint. Until then this is explicitly reported as a placeholder, so the
    # run does not claim feasibility it never verified.
    return {
        "feasible": False,
        "skipped": False,
        "violations": ["checks.py:verify() is still the scaffold placeholder"],
        "checks": {"scaffold_placeholder": False},
        "note": "implement verify() in problems/$slug/checks.py",
        "solution_keys": sorted(payload) if isinstance(payload, dict) else [],
    }


def extract_best_cost(payload: dict[str, Any], spec: dict[str, Any]) -> float | None:
    """Read the score out of a solution dump."""
    for key in ("cost", "best_cost", "objective", "score", "value"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def build_dataset(out_dir: Path, spec: dict[str, Any], force: bool = False) -> list[Any]:
    """Synthesize the instance data."""
    from cvrp.problems.$slug_module.instances import build_dataset as _build

    return _build(out_dir=Path(out_dir), spec=spec, force=force)


def revision_hints(spec: dict[str, Any]) -> dict[str, str]:
    """Error-class specific repair advice for the revise agent."""
    return {
        "contract_missing": (
            "The program must print the two expected lines with exactly the wording in "
            "problem.yaml:program.expected_lines."
        ),
        "shape_error": (
            "Tensor shapes drifted between the environment and the model; re-read the shape "
            "table in the generation prompt and align the new tensors."
        ),
    }
'''

INSTANCES = '''"""$name instance synthesis.

TODO: implement a real generator. The one rule that matters: **do not emit data
that no solver can satisfy.** Build a reference/serve order or schedule first,
derive the per-instance constraints from it, and self-check before writing - an
unsolvable instance makes the pipeline report a solver failure for a data problem.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def generate_instance(problem_size: int = 20, seed: int = 0) -> dict[str, Any]:
    """TODO: build one instance with `problem_size + 1` nodes (index 0 = depot)."""
    raise NotImplementedError(
        "$slug: implement generate_instance() in problems/$slug/instances.py"
    )


def build_dataset(
    out_dir: Path,
    spec: dict[str, Any] | None = None,
    force: bool = False,
    **_ignored: Any,
) -> list[dict[str, Any]]:
    """Generate and write the instance set; idempotent unless ``force``."""
    params = dict(spec or {})
    out_dir = Path(out_dir)
    manifest = out_dir / "manifest.json"
    if manifest.is_file() and not force:
        cached = json.loads(manifest.read_text(encoding="utf-8"))
        if cached.get("count"):
            return []

    sizes = [int(value) for value in (params.get("sizes") or [20])]
    count = int(params.get("count_per_size") or 3)
    seed = int(params.get("seed") or 1234)
    instances = [
        generate_instance(problem_size=size, seed=seed + size * 100 + offset)
        for size in sizes
        for offset in range(count)
    ]
    for instance in instances:
        size = int(instance["problem_size"])
        folder = out_dir / f"n{size}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{instance['name']}.json").write_text(
            json.dumps(instance, indent=2), encoding="utf-8"
        )
    manifest.write_text(
        json.dumps(
            {
                "problem": "$slug",
                "count": len(instances),
                "instances": [
                    {"name": item["name"], "problem_size": item["problem_size"]}
                    for item in instances
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return instances


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize $name instances")
    parser.add_argument("--out", type=str, default="instances/data/$slug")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    written = build_dataset(
        Path(args.out),
        {"count_per_size": args.count, "seed": args.seed},
        force=args.force,
    )
    for item in written:
        print(f"wrote {item['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

MOCK_ANALYSIS = {
    "problem_type": "TODO",
    "full_name": "TODO",
    "summary": "Scaffold placeholder produced by `cvrp new-problem`. Replace this file with a "
               "real offline analysis of the variant, or delete mock.analysis from problem.yaml.",
    "constraints": [{"id": "TODO", "name": "TODO", "rule": "TODO"}],
    "state_variables": [{"name": "TODO", "shape": "TODO", "update": "TODO"}],
    "masking_rules": [{"name": "TODO", "condition": "TODO", "exception": "TODO"}],
    "feasibility_checklist": ["TODO", "TODO"],
    "objective": "TODO",
    "output_contract": [
        "the initial process is successful, the initial cost is {cost}",
        "the process is successful, the best cost is {cost}",
    ],
    "pomo_modification_plan": [{"tool": "TODO", "file": "TODO", "change": "TODO"}],
    "checkpoint_compatibility": {
        "checkpoint": "TODO",
        "encoder_input_dim_must_stay": 2,
        "strategy": "load_state_dict(strict=False)",
        "risk": "TODO",
    },
    "run_plan": ["TODO"],
    "open_questions": ["TODO"],
}

PROMPT_FILES = {
    "s1_analysis.md": ANALYSIS_PROMPT,
    "s1_analysis_judge.md": ANALYSIS_JUDGE,
    "s2_codegen.md": CODEGEN_PROMPT,
    "s2_codegen_judge.md": CODEGEN_JUDGE,
    "s4_error_analyse.md": ERROR_ANALYSE,
    "s4_revise.md": REVISE,
}


def _module_name(slug: str) -> str:
    """The dotted package path a scaffolded problem will be importable under."""
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in slug)
    if safe[:1].isdigit():
        safe = f"p_{safe}"
    return f"cvrp.problems.{safe}"


def _fill(template: str, variables: dict[str, str]) -> str:
    """``$name`` substitution that ignores braces entirely.

    ``string.Template`` is used instead of ``str.format`` precisely because these
    templates contain literal braces everywhere (JSON examples, engine
    placeholders, f-string-ish code) and must not be escaped.
    """
    return string.Template(template).substitute(SafeDict(variables))


class SafeDict(dict):
    """A mapping that leaves unknown ``$names`` untouched."""

    def __missing__(self, key: str) -> str:
        return "$" + key


def scaffold_problem(
    slug: str,
    name: str,
    target: Path,
    description: str = "",
    variant_label: str = "",
    baseline: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Write a complete, contract-satisfying problem directory."""
    target = Path(target)
    if target.exists() and force:
        import shutil

        shutil.rmtree(target)
    prompts_dir = target / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    description = (description or "").strip() or (
        "TODO: describe the variant here. What makes it different from plain CVRP? "
        "Which new constraint changes which decision, and what stays legal? "
        "This text is handed verbatim to the analysis and code-generation agents."
    )
    variables = {
        "slug": slug,
        "name": name,
        "variant_label": variant_label or slug,
        "task_description": description,
        "slug_module": _module_name(slug).rsplit(".", 1)[-1],
        "baseline": baseline or "./baseline",
    }

    written: list[Path] = []

    def _write(path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        written.append(path)

    _write(target / "problem.yaml", _fill(PROBLEM_YAML, variables))
    for filename, body in PROMPT_FILES.items():
        _write(prompts_dir / filename, _fill(body, variables))
    _write(target / "checks.py", _fill(CHECKS, variables))
    _write(target / "instances.py", _fill(INSTANCES, variables))
    _write(target / "problem_context.md", _fill(PROBLEM_CONTEXT, variables))
    _write(target / "__init__.py", f'"""{name} problem plugin."""\n')
    import json as _json

    _write(target / "mock_analysis.json", _json.dumps(MOCK_ANALYSIS, indent=2) + "\n")
    return written


__all__ = ["scaffold_problem"]

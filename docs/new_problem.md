# Adding a new problem

The engine names no problem, but it does name the **baseline**: every problem is solved by
adapting **POMO's CVRP solver**. A new problem is therefore a directory that says *what to
change about POMO*, plus the checker that decides whether the change worked.

> **Sizes are a task setting, not a problem directory.** A task declares the instance size
> once:
>
> ```yaml
> scale:
>   problem_size: 50      # customers; an instance's DIMENSION is this + 1
>   pomo_size: 50         # optional; defaults to problem_size
>   aug_factor: 8         # optional; defaults to 8
> ```
>
> and the spec's `{n}`, `{pomo}` and `{aug}` placeholders are substituted with it before
> anything reads the spec. That is how one `problems/cvrptw/` serves n=20, n=50 and n=100 —
> see `task.yaml`, `task_n50.yaml`, `task_n100.yaml`.

## 0. Copy the shipped bundle

There is no generator any more: the shipped `cvrptw` bundle is the template.

```bash
cp -r problems/cvrptw problems/myproblem
$EDITOR problems/myproblem/task.yaml
uv run vrpar validate problems/myproblem/task.yaml
uv run vrpar start    problems/myproblem/task.yaml llm.backend=mock
```

The offline run is the point: it exercises analysis, generation, the executor, the
revise loop and the grader **without a model**, so a wiring mistake surfaces
before a real run costs you one.

## 1. What a bundle contains

```
problems/<name>/
├─ task.yaml    the problem statement, the pipeline settings, and this problem's hooks
├─ seed/        this problem's notes (`README.baseline.md`) — also the bundle marker
├─ grader/      the checker, the scorer and the instance generator
└─ context.md   extra "Available inputs" text appended to every stage instruction
```

The baseline is **part of the project, not of the bundle**. POMO and the CVRP100 checkpoint
live once at the project root:

```
baseline/
├─ pomo/            CVRProblemDef.py + POMO/**
└─ checkpoints/     checkpoint-30500.pt
```

`resources.baseline_dir: "baseline"` resolves there — the problem directory first, then the
project root — and its *contents* are staged at the worktree root, so the agent sees `pomo/`
and `checkpoints/` exactly as the prompts describe them. `seed/` holds only this problem's
notes, which are staged at the path `resources.notes` quotes.

That means a new problem costs a few kilobytes instead of 15.7 MB, and it also means a
project needs the `baseline/` directory: copying `problems/<name>/` on its own is not enough.

## 2. The five things to edit in `task.yaml`

### `problem:` — identity and hooks

```yaml
problem:
  slug: "myproblem"
  tag: "MYPROBLEM"                 # generated files are named after this
  checks: grader/myproblem_grader.py
```

`checks` names the module holding `verify()`, `score()`, `extract_best_cost()`,
`revision_hints()` and `build_dataset()`. Its directory is put on `sys.path` while it loads,
so it may import the generator sitting next to it.

### `task.description` — the problem statement

This is the *only* semantic input the analysis stage gets. There is no knowledge base and no
retrieval: what you write here, plus the instances and the baseline notes, is what the plan
is derived from. Write the constraints as predicates, not as prose.

### `generated:` — the deliverable's shape

```yaml
generated:
  files: [...]
  correspondence:
    MYPROBLEMEnv.py: "pomo/POMO/CVRPEnv.py"
```

`files` must list **everything** the agent produces, `run.py` included: anything present in
`artifacts/generated/` but not listed counts as extra generated code and fails the gate.
`correspondence` maps each file to the baseline file it was adapted from, and its paths are
relative to the **worktree root** (where the baseline's contents land), not to the bundle.

### `data:` — where instances come from

```yaml
data:
  dir: instances/data/myproblem
  instance_glob: "n{n}/*.json"
  generator: "grader/myproblem_gen.py:build_dataset"
```

The generator receives `(out_dir, spec, force)` and writes the instances **before** any agent
runs. They are read-only to the agent: they are what the run is graded on.

### `grader:` — how a run is judged

`grader.iterations` is the budget for the full run. The grader's verdict is **recorded, not
blocking**: a run succeeds when the code runs. A solver that runs cleanly but scores badly
is reported as such rather than hidden.

## 3. The plan

The analysis stage writes `analysis.json` (the contract) and `plan.md` (the narrative). Its
`run_plan` is the **work list** the generation stage follows: concrete steps, each naming a
`file`, `symbol`, `command` or `check`, so the generator has something to act on rather than a
paragraph to interpret.

Nothing verifies the code against that list. The run's contract is that the program runs —
whether it implements every planned step is not checked, and a code base that runs but skips
a planned feature will be reported as a success. Write the plan as good instructions, not as
a specification you expect to be audited.

## 4. The hooks the engine calls

Defined in `vrpar.problems.HOOK_NAMES`; define whichever you need, with these exact
signatures:

```python
def build_dataset(out_dir, spec, force=False) -> list
def verify(instance_path, solution_path, spec) -> dict
def extract_best_cost(payload, spec) -> float | None
def score(best_cost, verification, spec) -> float
def revision_hints(spec) -> dict[str, str]
```

`verify()` is the one that matters. Return `feasible: False` with human-readable
`violations`; that text is what the revise stage reads, so it should name the offending
value. Without it the engine falls back to `_default_verify`, which only checks that the dump
exists and parses — a run would then report success for a wrong answer.

## 5. Keeping the baseline honest

Two things keep the deliverable an *adaptation* rather than a rewrite:

- `generated.correspondence` — every file must map to a real baseline file, and the
  correspondence gate checks the declared first line (`# baseline-correspondence: ...`);
- the prompts under `src/vrpar/prompts/` and `src/vrpar/roles/`, which state the POMO
  invariants in one voice: five files keep their names, the encoder input stays two
  coordinates, new information enters the decoder context, the checkpoint must still load.

If you change a prompt, change it there — not in a copy under the problem directory. A problem
*can* override one prompt by placing `prompts/<basename>.md` next to its `task.yaml`, but that
is for wording a stage differently, not for changing the invariants.

## 6. Where to look when something goes wrong

| Symptom | Look at |
|---|---|
| `analysis gate failed` | `artifacts/analysis/analysis.json`; every required field must be non-empty, and every `run_plan` entry needs an anchor |
| `code generation gate failed` | `artifacts/generated/`; the file set and the correspondence lines |
| `acceptance contract not satisfied` | `artifacts/runs/s3_run_report.json`; the failing step and its stderr tail |
| `grader reports the solution infeasible` | recorded only — the run still succeeded if it ran and followed the plan |

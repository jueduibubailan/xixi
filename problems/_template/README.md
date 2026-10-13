# How to add a new problem

Copy this directory to `problems/<your-problem>/`, edit three blocks, run it. Nothing under
`src/cvrp/` changes: the engine never names a variant, so a new problem is a directory.

```bash
cp -r problems/_template problems/myproblem
$EDITOR problems/myproblem/task.yaml

uv run cvrp validate problems/myproblem/task.yaml            # static check
uv run cvrp start    problems/myproblem/task.yaml llm.backend=mock \
                     records.dir=./records-smoke workspace.results_dir=./results-myproblem
```

The `llm.backend=mock` run is the point of the offline payloads: it exercises the whole
pipeline -- analysis, generation, the executor's five steps, the checker, the revise loop --
without a model, so you find out that your problem is wired up before you spend a real run.

## What is required, and what breaks without it

| Piece | Required? | What happens without it |
|---|---|---|
| `task.yaml` | **Yes** | The directory is not a problem at all. `cvrp start` reports `no problem.yaml or bundle task.yaml here`. |
| `grader/` with a `verify()` hook | **Effectively yes** | The engine falls back to `_default_verify`, which only checks that the dump exists and parses as JSON. The contract can pass while the answer is *wrong*, and the report says `no problem-specific verify() was found`. |
| a generator: `build_dataset()` in `checks`, or `data.generator: file:function` | **Yes, to run** | Neither present means `build_dataset()` returns `[]`: no evaluation instances, and the full run has nothing to execute on. |
| `seed/` | No | A problem solved from scratch does not need one. With no seed, leave `generated.correspondence` empty so the "one-to-one with the seed" gate has nothing to compare. |
| `mock_analysis.json` + `mock_solver.py` | No | Read only by `llm.backend=mock`. A real run never touches them. Delete the `mock:` block if you do not want offline self-checks. |
| `context.md` | No | Extra "Available inputs" text appended to every stage instruction. |

So the honest short answer to *"do I only need a task?"*: **a task gets you a recognised
problem; a task plus a grader gets you a result you can trust.** The `seed/` is what you add
when the answer is "adapt this existing code" rather than "write it from scratch".

## The three blocks to edit

### 1. `problem:` -- the identity and the hooks

`slug`, `name`, `tag`. The `tag` is what generated files are named after (`COUNTEREnv.py`).

```yaml
problem:
  slug: "myproblem"
  tag: "MYPROBLEM"
  checks: grader/myproblem_grader.py     # where verify/score/build_dataset live
```

### 2. `generated:` -- the deliverable's shape

`files` must list **everything** the agent is expected to produce, `run.py` included:
anything present in `artifacts/generated/` but not listed counts as extra generated code and
fails the gate. `correspondence` maps each file to the baseline file it was adapted from, or
`null` for a file written from scratch. Every path in `correspondence` is relative to the
**worktree root**, which is where `resources.baseline_dir`'s *contents* land -- not to
`seed/` itself.

### 3. `data:` and `grader:` -- where instances come from and how a run is judged

`data.generator` names `file:function` inside the bundle. The function receives
`(out_dir, spec, force)` and writes instances under `data.dir`, then `data.instance_glob`
(`n{n}/*.json`) finds them. `grader.iterations` is how long the full run gets.

## The hooks the engine looks for

Defined in `cvrp.problems.HOOK_NAMES`; define whichever you need. The engine's exact call
signatures are worth copying verbatim:

```python
def build_dataset(out_dir, spec, force=False) -> list
def verify(instance_path, solution_path, spec) -> dict
def extract_best_cost(payload, spec) -> float | None
def score(best_cost, verification, spec) -> float
def revision_hints(spec) -> dict[str, str]
```

`verify()` is the one that matters. Return `feasible: False` with human-readable
`violations`: that text is what the revise stage reads, so it should name the offending
value, not merely state that something is wrong.

## Two engine-side names that are still POMO-specific

Worth knowing before they confuse you:

* `analysis.pomo_modification_plan` is a **required** field of the analysis schema
  (`cvrp/pipeline/gates.py`), even for a problem with no POMO baseline. Treat it as
  `modification_plan`.
* `masking_rules` is required too, and its entries need `name` and `condition`. For a
  problem with no state to mask, phrase the *correctness* conditions there -- which is what
  the template's payload does.

Both are fixed engine-side today. They are the last place the engine names a variant, and
they are the thing to change if you want the pipeline to be truly variant-neutral.

## Files here

```
_template/
├─ task.yaml                 the statement, the mechanisms, and the problem's hooks
├─ context.md                extra "Available inputs" text for every stage
├─ mock_analysis.json        an analysis payload for `llm.backend=mock`
├─ mock_solver.py            a runnable first-cut solver for `llm.backend=mock`
├─ grader/
│   └─ template_grader.py    verify / score / extract_best_cost / revision_hints / build_dataset
└─ seed/
    └─ README.md             when and how to add a baseline
```

The example problem itself (sum a list of integers, and their squares) is deliberately
trivial: every mechanism stays visible and nothing hides behind domain complexity. Replace
`verify()` and `build_dataset()` with your own definition of correct, and the rest is
plumbing you can leave alone.

# Adding a new VRP variant

> **Sizes are a task setting, not a problem directory.** A task declares the
> instance size once:
>
> ```yaml
> scale:
>   problem_size: 50      # customers; an instance's DIMENSION is this + 1
>   pomo_size: 50         # optional; defaults to problem_size
>   aug_factor: 8         # optional; defaults to 8
> ```
>
> and the spec's `{n}`, `{pomo}` and `{aug}` placeholders are substituted with it
> before anything reads the spec. That is how one `problems/cvrptw/` serves n=20,
> n=50 and n=100 — see `task.yaml`, `task_n50.yaml`, `task_n100.yaml`. A problem
> that wants to be size-parameterised writes those placeholders where the size
> belongs (entry-point file names, `data.sizes`, `data.instance_glob`,
> `generated.dimensions`) and reads the size from the spec in its generator
> instead of hardcoding it. A problem that is inherently one size can ignore the
> placeholders entirely; nothing breaks, because a task without a `scale` block
> renders `{n}` as 20.

The engine is variant-agnostic. A variant is a directory under `problems/<slug>/`,
and `task.yaml:problem.slug` selects it. Nothing under `src/cvrp/{pipeline,
executor,web}` mentions a variant by name — if you find yourself editing those to
add a variant, something has gone wrong and the `tests/test_problems.py`
regression net is where to look first.

## 0. Scaffold

```bash
uv run cvrp new-problem cvrptw-dr \
    --name "CVRPTW with drones" \
    --variant-label "CVRPTW-DR-on-POMO" \
    --from "Each vehicle carries drones. A drone has a battery budget instead of
            a capacity, must return to its truck or the depot before the battery
            is exhausted, and may serve exactly one customer per sortie."

uv run cvrp problems      # confirm it is visible
```

You get:

```
problems/cvrptw-dr/
├─ problem.yaml           # correct-by-construction spec; edit the TODOs
├─ prompts/
│  ├─ s1_analysis.md      # the variant description is already embedded
│  ├─ s1_analysis_judge.md
│  ├─ s2_codegen.md       # the variant description is already embedded
│  ├─ s2_codegen_judge.md
│  ├─ s4_error_analyse.md
│  └─ s4_revise.md
├─ checks.py              # verify / build_dataset / extract_best_cost / revision_hints
├─ instances.py           # generator stub that fails loudly
├─ problem_context.md     # what the agents are told about their inputs
├─ mock_analysis.json     # so `llm.backend=mock` runs immediately
└─ __init__.py
```

## 1. Describe the generated package

Edit `problem.yaml`. The parts that matter:

```yaml
generated:
  dir: artifacts/generated
  program: run.py                 # the entry point the contract executes
  required: [run.py]              # missing these fails the gate
  modules: [CVRPDRProblemDef.py, CVRPDREnv.py, ...]   # documentation for the agent
  entries: {train: train_n20.py, test: test_n20.py}
  files: [...]                    # missing these is only a warning
  dimensions: {encoder_embedding: 128, ...}           # quoted into the prompt

cli:                              # how the program is invoked; null = flag not used
  path_arg: "--path"
  iteration_arg: "--iteration"
  save_solution_arg: "--save-solution"

program:
  best_regex: '...'               # what stdout must match
  initial_regex: '...'
  expected_lines:                 # quoted into the prompt and the contract
    - "the initial process is successful, the initial cost is {cost}"
    - "the process is successful, the best cost is {cost}"

resources:                        # copied into every stage worktree
  baseline_dir: ./baseline
  baseline_subdirs: [pomo, checkpoints]
  notes: BASELINE_NOTES.md

scorer:
  kind: inverse_cost              # inverse_cost | identity | binary
  direction: maximize
```

## 2. Implement the feasibility check — `checks.py:verify`

**This is the most important file in the variant.** The engine trusts it
completely: `feasible: true` is what turns "the program ran" into "the program is
correct". Write it against the instance data only, never against the generated
code.

```python
def verify(instance_path, solution_path, spec):
    # 1. read the instance and the dumped solution
    # 2. recompute whatever the solver claims (cost, travel time, load ...)
    # 3. replay every route / assignment against the variant's constraints
    # 4. return the normalised result
    return {
        "feasible": bool,
        "skipped": False,
        "violations": ["customer 7: battery 0.02 < 0.15 needed to return"],
        "checks": {"battery_budget": True, "one_customer_per_sortie": False},
    }
```

Returning an empty dict, or an error dict, is treated as **not verified** — never
as a pass. The `cvrptw` example (`problems/cvrptw/checks.py`) is a complete worked
implementation: distance matrix, route replay, capacity, time windows, single
service, depot round trip, and a claim-vs-recomputed cost comparison.

`build_dataset(out_dir, spec, force)` is usually a thin delegate to
`instances.py`; `extract_best_cost(payload, spec)` only needs overriding if your
solution dump does not use one of the conventional keys (`cost`, `best_cost`,
`objective`, `score`, `value`).

## 3. Generate data that is solvable — `instances.py`

The one rule that matters: **never emit an instance no solver can satisfy.**

Naively sampling constraints produces exactly that. The `cvrptw` generator shows
the technique that works: build a canonical feasible schedule *first*, derive the
per-customer time windows from the arrival times that schedule induces, then
self-check by replaying it and refuse to write the instance if the replay fails.

```python
def generate_instance(problem_size=20, seed=0):
    ...
    check = _self_check(instance)
    if not check["feasible"]:
        raise RuntimeError(f"generated an infeasible instance: {check['violations']}")
    instance["meta"]["reference_cost"] = check["cost"]
    return instance
```

`meta.reference_cost` costs you nothing and gives every later reader a sanity
anchor for "is this score plausible".

## 4. Write the prompts

`s1_analysis.md` and `s2_codegen.md` already carry the variant description you
passed with `--from`. What to adjust:

- **S1** must ask for the *masking* rules, not just a constraint list — see
  `problems/cvrptw/prompts/s1_analysis.md`. That is the part later stages act on.
- **S2** must state what may and may not change in the baseline. If a checkpoint
  must stay loadable, say which module's input dimension is frozen and that new
  features go into the decoder context.

Prompt placeholders use the engine's vocabulary; the full list is in
`src/cvrp/pipeline/stages.py::stage_variables`. Unknown placeholders render as an
empty string, and `{{...}}` renders a literal brace (useful inside JSON examples).

## 5. Validate and run

```bash
uv run cvrp validate              # every path, prompt, hook and resource
uv run cvrp instances             # build the data
uv run cvrp start task.yaml llm.backend=mock   # offline first: no model needed
uv run cvrp start task.yaml                    # then for real
uv run cvrp ui
```

`validate` is deliberately pedantic: a missing prompt, a `checks.py` without
`verify`, a baseline directory that does not exist, or data that has not been
generated are all reported before you spend a model turn.

## 6. Common mistakes

| symptom | cause |
|---|---|
| `analysis gate failed` | the prompt never asked for `masking_rules`, or asked without requiring an exception rule |
| `code generation gate failed` | `generated.program` does not match the file the agent wrote |
| `contract_missing` | the program prints something other than `program.expected_lines` |
| `contract passed, but ... could not be verified` | the program ignores `cli.save_solution_arg` |
| `feasibility: FAILED` with empty violations | `verify()` returned `{}` or raised; it is treated as not verified |
| every run fails at `py_compile` | the agent is generating a single file while `generated.files` asks for a package (or vice versa) |
| scores look meaningless | `scorer.kind` is wrong for the variant (minimisation vs maximisation) |
| the pipeline "succeeds" with a bad answer | `verify()` returns `feasible: True` without real checks — the one failure mode the engine cannot catch for you |

## 7. What stays shared

Roles (`src/cvrp/roles/*.md`) describe *how* each agent works and are
variant-independent. The three executor gates, the acceptance contract machinery,
the grader, the run-directory layout, the state ledger, the dashboard and the
worktree seeding are all shared and should not need editing for a new variant.

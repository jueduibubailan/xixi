<<<<<<< HEAD
# xixi
=======
# cvrp — VRP-variant code synthesis on top of a neural solver

A staged, multi-agent pipeline that synthesizes a solver for a **VRP variant** by
modifying the input, state and masking of an existing neural baseline. Point it at
a new variant and it will: analyse the variant into a machine-readable contract,
generate the code, run it, attribute any failure, revise, and only export a result
that passes an independent feasibility check.

`cvrptw` is the **first example**, not the subject. The engine never names a
variant; everything variant-specific lives in a *problem directory*:

```
problems/<slug>/
├─ problem.yaml          the spec: prompts, generated layout, CLI flags, data, scoring
├─ prompts/*.md          what each agent stage must do
├─ checks.py             verify() / build_dataset() / score() hooks
├─ instances.py          the instance synthesizer
└─ problem_context.md    what the agents are told about their inputs
```

Built by fusing three codebases:

- **AFL** for the pipeline semantics — analyse, generate, run, attribute, revise.
  Every stage prompt descends from AFL's `describe.py` / `code_generation.py` /
  `complete_code_revise.py`.
- **CORAL** for the orchestration shape — a `task.yaml`, role files, a dashboard,
  a grader as the exit gate — and for the Claude Code runtime that drives agents.
- **POMO** for the baseline being modified (`baseline/pomo/`), including the
  CVRP100 checkpoint the synthesized solver must keep loadable.

## The pipeline

```
S1 analysis          agent      variant -> machine-readable masks/state/plan
S2 code_generation   agent      plan + baseline -> the problem's generated dir
S3 code_error        executor   py_compile -> smoke run -> full run (no LLM)
S4 code_revise       agent      error attribution -> minimal repair  --(loop back to S3)
grader               executor   re-run through the contract, then verify
S5 feasibility       executor   reports the grader's independent verification
export               -          workspace/final/<slug>/ + REPORT.md
```

The loop `S3 -> S4 -> S3` runs until the acceptance contract passes or
`loop.max_rounds` is exhausted. It never silently declares victory: a run either
satisfies the contract *and* passes independent feasibility, or it is reported as
failed with all evidence kept.

## Quick start

```bash
uv sync --extra dev

# (optional) enable the thin CORAL shell
uv pip install -e ../CORAL1

# generate the selected problem's data (also happens automatically on `start`)
uv run cvrp instances

# run the whole pipeline
uv run cvrp start task.yaml

# watch it
uv run cvrp ui               # http://127.0.0.1:8420
```

Driving the agents requires the Claude Code CLI on `PATH` inside the VM, logged in
as the model you want. `task.yaml` ships with `llm.model: deepseek-flash`; change it
in the config, not in the code.

### Offline smoke test

```bash
uv run cvrp start task.yaml llm.backend=mock
```

The mock backend emits the problem's `mock.template` — a small but genuinely
runnable solver — and the problem's `mock.analysis` payload, so the acceptance
contract and the feasibility checker are exercised for real without a model.

## Adding a new variant

This is the main workflow. `docs/new_problem.md` has the full walkthrough.

```bash
# 1. scaffold a complete, contract-satisfying problem directory
uv run cvrp new-problem cvrptw-dr --name "CVRPTW with drones" \
    --from "Drones have a battery budget and no capacity; a drone must return to
            the truck or the depot before its battery runs out."

# 2. point the task at it
#    task.yaml:  problem: {slug: cvrptw-dr}

# 3. check the wiring (config, prompts, hooks, baseline, data)
uv run cvrp validate

# 4. build the data and go
uv run cvrp instances
uv run cvrp start task.yaml
```

The three things you must write for a new variant, all marked `TODO` by the
scaffold:

1. **`checks.py:verify()`** — what "feasible" means, checked independently of the
   generated code. A wrong `True` here is the one mistake that silently corrupts
   every score.
2. **`instances.py:generate_instance()`** — data that is feasible by construction.
   Never emit an instance no solver can satisfy; the pipeline would report a
   solver failure for a data problem.
3. **`prompts/s1_analysis.md` and `prompts/s2_codegen.md`** — the variant
   description, so the agents know what they are building.

`uv run cvrp problems` lists what this project can build.

## Commands

| command | what it does |
|---|---|
| `cvrp start [task.yaml] [k=v ...]` | run the pipeline |
| `cvrp ui [--port 8420]` | dashboard (stages, artifacts, logs, steering) |
| `cvrp doctor [--probe]` | check the runtime prerequisites (agent CLI, routing, data, baseline, prompts) |
| `cvrp stage <name>` | re-run ONE stage of an existing run (debugging) |
| `cvrp new-problem <slug>` | scaffold a new variant under `problems/<slug>/` |
| `cvrp problems` | list the variants this project can build |
| `cvrp status` | stage table for the latest run |
| `cvrp log [-n N]` | tail `logs/pipeline.log` |
| `cvrp show <path>` | print a run artifact |
| `cvrp grader [--json]` | re-run the exit gate on the latest run |
| `cvrp instances [--force]` | (re)generate the problem's data |
| `cvrp validate` | check the config, the problem and every referenced file |
| `cvrp clean [--all]` | remove run directories |

Any `k=v` argument is a dotlist override, CORAL-style:

```bash
uv run cvrp start task.yaml llm.model=sonnet loop.max_rounds=8 run.verbose=true
```

## Layout

```
new/
├─ task.yaml                 # task: description + problem.slug + stages + grader
├─ configs/default.yaml      # global defaults (model, timeouts, budgets)
├─ src/cvrp/
│  ├─ problems/              # the plugin system + the shipped cvrptw example
│  ├─ pipeline/              # orchestrator, stages, gates
│  ├─ executor/              # contract, executor, grader, feasibility entry point
│  ├─ llm/                   # claude_code / coral / mock backends
│  ├─ run/                   # run dirs, state ledger, worktree seeding
│  ├─ web/                   # dashboard
│  └─ roles/                 # engine-shipped agent roles
├─ problems/                 # your variants (new-problem writes here)
├─ baseline/                 # read-only payload: POMO sources, checkpoint, AFL refs
├─ instances/data/<slug>/    # the selected problem's generated data
├─ workspace/final/          # export target
├─ results/                  # one directory per run
├─ tests/                    # offline pipeline, gates, problems, contract, web
└─ docs/
```

## The acceptance contract

A generated program passes when:

1. every `.py` under the problem's generated dir compiles;
2. a short run exits 0;
3. the full run exits 0 and prints the lines declared under
   `program.expected_lines` in the problem's spec;
4. the grader dumps a solution and the **problem's own `verify()`** confirms it is
   feasible; a missing dump fails the run by default
   (`grader.args.require_solution_dump`).

The program name, its CLI flags, the wording of the contract and the meaning of
"feasible" all come from the problem. The engine only enforces the shape.

## Where to change things

| you want to | edit |
|---|---|
| solve a different variant | `uv run cvrp new-problem …`, then `problems/<slug>/` |
| change what the analysis must produce | `problems/<slug>/prompts/s1_analysis.md` |
| change how code is generated | `problems/<slug>/prompts/s2_codegen.md` |
| change the instance data | `problems/<slug>/instances.py` + `problem.yaml:data` |
| change what "feasible" means | `problems/<slug>/checks.py:verify` |
| change scoring | `problem.yaml:scorer` or `checks.py:score` |
| change the exit gate itself | `task.yaml:grader` + `src/cvrp/executor/grader.py` |
| change the dashboard | `src/cvrp/web/` |

See `DESIGN.md` for the concept mapping and the load-bearing decisions,
`docs/run_flow.md` for the stage-by-stage data flow, and `docs/new_problem.md` for
the variant-authoring walkthrough.
>>>>>>> 5d6fb35 (首次上传：初始化 work1 分支)

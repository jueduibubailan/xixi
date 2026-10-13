# cvrp — VRP-variant code synthesis on top of a neural solver

A staged, multi-agent pipeline that synthesizes a solver for a **VRP variant** by adapting an
existing neural baseline. Point it at a new variant and it will: analyse the variant into a
machine-readable contract, generate the code, run it, attribute any failure, revise, and only
export a result that passes an independent feasibility check.

`cvrptw` is the **first example**, not the subject. The engine never names a variant: everything
variant-specific lives in a *problem bundle*, and adding one is a directory rather than a change
under `src/cvrp/`.

```
problems/<name>/
├─ task.yaml      the statement, the mechanisms, and this problem's hooks
├─ seed/          OPTIONAL: the starting code an agent edits
└─ grader/        the checker, the scorer, and the instance generator
```

`task.yaml` doubles as a runnable task, so a problem can be run against itself:

```bash
uv run cvrp start problems/cvrptw/task.yaml
```

Built by fusing three codebases:

- **AFL** for the pipeline semantics — analyse, generate, run, attribute, revise.
- **CORAL** for the orchestration shape — a `task.yaml`, role files, a dashboard, a grader as
  the exit gate — and for the agent runtimes.
- **POMO** for the baseline being adapted, including the CVRP100 checkpoint the synthesized
  solver must keep loadable.

## Bundles

Three ship with the project:

| bundle | what it is |
|---|---|
| `problems/cvrptw` | Capacitated VRP with Time Windows, adapting POMO's CVRP solver |
| `problems/cvrpltw` | CVRP with **Length Limit** and Time Windows — i.e. CVRPTW plus a per-route length bound |
| `problems/_template` | **the template for a new problem**, and a deliberately trivial worked example |

`problems/_template/` is the one to read first: its `task.yaml` marks the three blocks you edit,
its `grader/template_grader.py` is an annotated hook sample, and it runs end to end offline with
no `seed/`, no POMO and no checkpoint — which is the "solved from scratch" case.

## The pipeline

```
retrieval        agent      pick the records this problem's run should see
analysis         agent      variant -> machine-readable state / masks / plan
code_generation  agent      plan + seed -> the problem's generated dir
code_error       executor   py_compile -> import_probe -> correspondence -> smoke -> full run
                 (+ agent)  and, only when there is a failure to report, one narration turn
code_revise      agent      error attribution -> minimal repair  --(loop back to code_error)
feasibility      executor   publishes the grader's independent verdict
update           agent      fold what was learned back into the knowledge base
```

The loop `code_error -> code_revise -> code_error` runs until the acceptance contract passes or
`loop.max_rounds` is exhausted. The pipeline never silently declares victory: a run either
satisfies the contract *and* passes independent feasibility, or it is reported as failed with all
evidence kept.

Note on `code_error`: its core is the deterministic executor, so `kind: executor`; the narration
turn is optional and is **skipped entirely on a clean pass**. The dashboard therefore shows it as
`executor + agent narration` rather than picking one label.

## Quick start

```bash
uv sync --extra dev

# generate the selected problem's data (also happens automatically on `start`)
uv run cvrp instances

# run the whole pipeline
uv run cvrp start task.yaml

# watch it
uv run cvrp ui               # http://127.0.0.1:8420
```

Driving the agents requires the Claude Code CLI on `PATH`, logged in as the model you want.
`task.yaml` ships with `llm.model: deepseek-flash`; change it in the config, not in the code.

### Offline smoke test

```bash
uv run cvrp start task.yaml llm.backend=mock records.dir=./records-smoke \
                 workspace.results_dir=./results-smoke
```

The mock backend emits the problem's `mock.template` — a small but genuinely runnable solver —
and the problem's `mock.analysis` payload, so the acceptance contract and the feasibility
checker are exercised for real without a model.

Pass a scratch `records.dir` and `workspace.results_dir`: an offline run writes to `records/`
by default, and the curated knowledge base is not a scratch space.

## Adding a new problem

```bash
cp -r problems/_template problems/myproblem
$EDITOR problems/myproblem/task.yaml

uv run cvrp validate problems/myproblem/task.yaml
uv run cvrp start    problems/myproblem/task.yaml llm.backend=mock \
                     records.dir=./records-smoke workspace.results_dir=./results-myproblem
```

Edit three blocks in `task.yaml`: `problem:` (identity and hooks), `generated:` (what the
deliverable looks like), and `data:` + `grader:` (where instances come from and how a run is
judged). `docs/new_problem.md` has the full walkthrough; `problems/_template/README.md` has the
short version plus the exact hook signatures.

What each piece buys you:

| piece | required? | without it |
|---|---|---|
| `task.yaml` | **yes** | the directory is not a problem at all |
| `grader/` with `verify()` | **effectively yes** | the engine falls back to "dump exists and parses as JSON": the contract can pass while the answer is *wrong* |
| a generator (`build_dataset()` or `data.generator`) | to run | no evaluation instances, so the full run has nothing to execute |
| `seed/` | no | a problem solved from scratch needs none; leave `generated.correspondence` empty |
| `mock_analysis.json` + `mock_solver.py` | no | only affects `llm.backend=mock` |

## Commands

| command | what it does |
|---|---|
| `cvrp start [task.yaml] [k=v ...]` | run the pipeline |
| `cvrp ui [--port 8420]` | dashboard (stages, artifacts, records, logs, steering) |
| `cvrp doctor [--probe]` | check the runtime prerequisites |
| `cvrp status` | stage table for the latest run |
| `cvrp stages` | the stage table (including each stage's mechanism label) |
| `cvrp stage <name>` | re-run ONE stage of an existing run (debugging) |
| `cvrp log [-n N]` | tail `logs/pipeline.log` |
| `cvrp show <path>` | print a run artifact |
| `cvrp grader [--json]` | re-run the exit gate on the latest run |
| `cvrp instances [--force]` | (re)generate the problem's data |
| `cvrp validate [task.yaml]` | check the config and the problem it selects |
| `cvrp clean [--all]` | remove run directories |
| `cvrp problems` | list the problem bundles this project can build |
| `cvrp new-problem <slug>` | scaffold a problem — see the note below |
| `cvrp version` | print the version |

Any `k=v` argument is a dotlist override, CORAL-style:

```bash
uv run cvrp start task.yaml llm.model=sonnet loop.max_rounds=8 run.verbose=true
```

> **`cvrp new-problem` is behind the bundle refactor.** It still writes the older
> `problem.yaml` + `prompts/` + `checks.py` shape, which `load_problem` still reads but which is
> no longer how a new problem should be written. Prefer copying `problems/_template/`. The
> scaffold is scheduled to be repointed at the bundle shape.

## Layout

```
new/
├─ task.yaml                 # task: description + problem.dir + stages + grader
├─ task_n50.yaml             # thin variants: the same bundle at another scale
├─ task_n100.yaml
├─ configs/default.yaml      # global defaults (model, timeouts, budgets)
├─ src/cvrp/
│  ├─ bundle.py              # what a problem bundle is; `stages: default` expansion
│  ├─ problems/              # the loader + hook contract (no problem data lives here)
│  ├─ pipeline/              # spec (stage table), orchestrator, stages, gates
│  ├─ executor/              # contract, correspondence, runner, grader, feasibility
│  ├─ llm/                   # claude_code / coral / dsh / mock backends
│  ├─ run/                   # run dirs, state ledger, worktree seeding
│  ├─ web/                   # dashboard
│  ├─ prompts/               # the engine's shared prompt pack (a problem may override one)
│  └─ roles/                 # engine-shipped agent roles
├─ problems/                 # the bundles: cvrptw, cvrpltw, _template
├─ instances/data/<slug>/    # generated data (regenerable)
├─ workspace/final/          # export target
├─ results/                  # one directory per run
├─ records/main/             # the durable knowledge base
├─ tests/                    # offline pipeline, gates, bundles, executor, web
└─ docs/
```

## The acceptance contract

A generated program passes when:

1. every `.py` under the problem's generated dir compiles;
2. it imports in isolation, with no third-party import the seed did not already use;
3. every generated file corresponds one-to-one with a declared entry — no invented code;
4. a short run exits 0;
5. the full run exits 0 and prints the lines declared under `program.expected_lines`;
6. the grader dumps a solution and the **problem's own `verify()`** confirms it is feasible; a
   missing dump fails the run by default.

The program name, its CLI flags, the wording of the contract and the meaning of "feasible" all
come from the problem. The engine only enforces the shape.

## Knowledge base

`records/main/` is the durable part: hand-curated skills, notes and attempts, split by scope
(`_global_*.md` for anything reusable, `<tag>_*.md` for one problem). Each run's own evidence
lives under `records/temp/<run-id>/` and `records/related/<run-id>/`, which are not worth
committing.

Only `retrieval` and `analysis` see records, and retrieval never blocks a run: a run with an
empty knowledge base behaves exactly like one with a full one, minus the context.

## Where to change things

| you want to | edit |
|---|---|
| solve a different problem | copy `problems/_template/`, then edit its `task.yaml` |
| change one stage's prompt | drop `prompts/<basename>.md` into your bundle |
| change a stage's mechanism or budget | `src/cvrp/pipeline/spec.py` (or override that stage in `task.yaml`) |
| change how instances are generated | `grader/<name>_gen.py` + `task.yaml:problem.data` |
| change what "feasible" means | `grader/<name>.py:verify` |
| change scoring | `task.yaml:problem.scorer` or the grader's `score` |
| change the exit gate | `task.yaml:grader` + `src/cvrp/executor/` |
| change the dashboard | `src/cvrp/web/` |

See `DESIGN.md` for the concept mapping and the load-bearing decisions, `docs/run_flow.md` for
the stage-by-stage data flow, `docs/new_problem.md` for the variant-authoring walkthrough,
`docs/records.md` for the knowledge base, and `docs/faq.md` for the rest.

## Runtimes

| `llm.backend` | status |
|---|---|
| `claude_code` (default) | verified end to end on a Linux host |
| `coral` | same contract, delegating to CORAL's runtime when importable |
| `mock` | offline: no model, exercises the whole pipeline |
| `dsh` (DeepSeek Harness) | implemented and unit-tested, **not usable on Windows** — the rendered instruction is passed as argv and exceeds the 32767-character command-line limit, so the process is rejected before it starts. It is not the working backend of any shipped bundle. |

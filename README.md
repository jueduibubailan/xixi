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

Common to both tracks:

```bash
uv sync --extra dev

# generate the selected problem's data (also happens automatically on `start`)
uv run cvrp instances

# watch a run
uv run cvrp ui               # http://127.0.0.1:8420
```

Then pick the track that matches your machine. Both drive the same pipeline; they differ only
in which agent runtime launches the model turns.

### Track A — Linux + Claude Code (the verified path)

This is the track the project was developed and run against end to end.

```bash
# the agent runtime
npm install --global @anthropic-ai/claude-code    # provides `claude` on PATH
claude --version
```

Credentials and routing come from the shell. `task.yaml` ships with
`llm.model: deepseek-flash`, which is not an Anthropic model name, so it only works when
`ANTHROPIC_BASE_URL` points at a router that serves it:

```bash
export ANTHROPIC_BASE_URL=https://your-router.example/v1
export ANTHROPIC_AUTH_TOKEN=...        # or ANTHROPIC_API_KEY
```

`llm.inherit_routing_env: true` (the default) is what lets those variables reach the child;
the engine strips only Claude's own session markers, never your routing setup.

```bash
uv run cvrp start task.yaml            # n=20: the fastest end-to-end signal
uv run cvrp doctor                     # checks CLI, routing, data and prompts
```

Config shape:

```yaml
llm:
  backend: "claude_code"
  runtime: "claude_code"
  model: "deepseek-flash"
  claude_bin: "claude"        # or an absolute path
  permission_mode: "auto"     # Claude Code's vocabulary
  inherit_routing_env: true
```

### Track B — Windows + DeepSeek Harness (`dsh`)

`dsh` is a different launcher with different flags and its own sandbox. Four things about it
cost real debugging time, so they are stated up front; the code already handles all four.

```powershell
# 1. the runtime
npm install --global @deepseek-ai/dsh
dsh --version

# 2. the credential -- this is the step people miss
$env:DEEPSEEK_API_KEY = "sk-..."        # must be in the shell that launches `cvrp`
```

`dsh_home` defaults to `.dsh` **inside the run directory**, so each run gets isolated session
state. The consequence worth knowing: your global store at `~/.dsh/.credentials.yaml` is
**not** consulted, and pointing `dsh_home` at `~/.dsh` does not work either, because the
sandbox refuses writes outside the workspace. Export the environment variable instead.

```powershell
uv run cvrp start task.yaml             # n=20 first, not n=100
```

Config shape:

```yaml
llm:
  backend: "dsh"
  runtime: "dsh"
  dsh_bin: "dsh"
  dsh_home: ".dsh"              # run-local: isolated, and inside the workspace
  inherit_routing_env: true     # passes DEEPSEEK_API_KEY through
  timeout_seconds: 3600
  runtime_options:
    profile: "headless"
    permission_mode: "workspace-write"   # DSH's vocabulary, not Claude Code's
    tools_mode: "native"
    patch: []                   # a --patch overlay is how you change the model
```

**The four things that bite on Windows**, and what the engine does about each:

| symptom | cause | how it is handled |
|---|---|---|
| `命令行太长。` ("command line is too long") | on Windows `dsh` resolves to `dsh.CMD`, which runs through `cmd.exe`, whose command line caps at **8191** characters — while a rendered stage instruction is 11k–38k | the instruction is **piped on stdin**, which `headless` documents (`echo "task" \| dsh --profile headless`). The argv is `dsh --profile headless`, 22 characters |
| `error: unknown option '--model'` | `--model` is not a `dsh` flag; the model is a profile setting | the model is never put on the command line. It is recorded in the log header and set through the profile or a `--patch` overlay |
| `sandbox-policy: ... expected "read-only" \| "workspace-write" \| "danger-full-access" but got "auto"`, then `9 entries did not activate` | the two runtimes do not share a `permission_mode` vocabulary. This repo invokes Claude Code with `auto`; DSH accepts only the three values its own validation error names | set DSH's value under `runtime_options.permission_mode`, which takes precedence over `llm.permission_mode`. **Do not leave it at `auto`**: the agent then starts with **no tools at all** and fails later for an unrelated-looking reason |
| `spill-local: EPERM ... mkdtemp '%TEMP%\dsh-spill-XXXXXX'` then `1 entry did not activate` | DSH's file sandbox forbids writes outside the workspace, but its `spill-local` plugin creates a private root under the OS temp dir | the child's `TMPDIR`/`TMP`/`TEMP` are redirected to `<DSH_HOME>/tmp`, inside the run. Disable with `llm.redirect_temp: false` |

Two more differences from Track A, both easy to trip over:

* **the model string differs.** Claude Code takes `deepseek-flash` through a router; DSH has no
  `--model` flag at all, so the value is informational unless you set it in the profile. The
  header line `# model: ...` tells you what the pipeline asked for.
* **`llm.permission_mode` cannot serve both tracks.** Keep `auto` for Track A and put DSH's
  value in `runtime_options.permission_mode`; that is what makes one `task.yaml` usable from
  either platform.

### Offline smoke test (both tracks)

```bash
uv run cvrp start task.yaml llm.backend=mock records.dir=./records-smoke \
                 workspace.results_dir=./results-smoke
```

The mock backend emits the problem's `mock.template` — a small but genuinely runnable solver —
and the problem's `mock.analysis` payload, so the acceptance contract and the feasibility
checker are exercised for real without a model, and without any of the setup above.

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

| `llm.backend` | platform | status |
|---|---|---|
| `claude_code` (default) | Linux | **verified end to end** — the reference track |
| `claude_code` | Windows | implemented, not exercised here; the argv carries the instruction, so very large prompts approach `cmd.exe`'s limit (Track B exists for a reason) |
| `dsh` (DeepSeek Harness) | Windows | **works**, with the setup in Track B: task on stdin, model not on the argv, `runtime_options.permission_mode`, temp inside the run, `DEEPSEEK_API_KEY` exported |
| `dsh` | Linux | implemented; the stdin delivery and flag handling are platform-independent |
| `coral` | any | same contract, delegating to CORAL's runtime when it is importable |
| `mock` | any | offline: no model, exercises the whole pipeline |

The runtime is a property of the task, not of the code: `llm.backend` selects it and nothing
under `src/cvrp/` branches on a variant. Switching a task between Track A and Track B is a
`llm:` edit, and `runtime_options.*` is how a setting that only one runtime understands is
kept out of the other's way.

A preflight would be kinder than the failures above — the credential and the
`permission_mode` value can both be checked before a run starts, instead of surfacing as a
failed agent turn after the first stage has been paid for. That is not implemented yet;
`uv run cvrp doctor` is the closest thing today.

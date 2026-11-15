# vrpar - VRP-variant code synthesis on POMO

A five-stage, agent-driven pipeline. A task describes a VRP variant; the pipeline:

1. **analysis** turns the statement into `artifacts/analysis/plan.md` - scale, file
   names, interface, masking, training/test protocol;
2. **code_generation** adapts POMO into a runnable package;
3. **code_error** generates `test_train.py` at the protocol's budget (search 50x5000
   for plan runs, acceptance 200x5000 for the initial and final runs), trains, saves
   `checkpoint-<epochs>.pt`, then runs the generated final test entry;
4. **code_adjustment** repairs the generated code when a run fails (and implements the
   one neural-network method change a plan asks for);
5. **nn_improve** designs batches of neural-network method plans (one markdown file per
   plan); each is applied by `code_adjustment` and scored by `code_error` at the search
   budget, and the champion is re-run at the acceptance budget before export.

The pipeline itself does not parse the analysis plan. Agents read it and decide the work.
For nn improve it reads only each plan file's front matter (the design note is not parsed).
The pipeline seeds worktrees, checks the mechanical conditions below, records the score,
and exports the final code.

## Install

Requirements:

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)
- an agent runtime on `PATH`: `dsh` (DeepSeek Harness), Claude Code, or `mock`

```bash
uv sync --extra dev
```

`torch` is intentionally not a project dependency: a real run installs it through the
bundle's `workspace.setup` on the first `vrpar start`. The offline path needs no model
runtime and no `torch`:

```bash
uv run vrpar validate problems/mvmoe_ovrpl_n50/task.yaml
uv run vrpar start problems/mvmoe_ovrpl_n50/task.yaml llm.backend=mock run.setup=false
```

For a real `dsh` run, put `DEEPSEEK_API_KEY` in the launching shell (or use the
credential already stored by DSH). For Claude Code, use `claude login` or
`ANTHROPIC_API_KEY`.

## Layout

```
problems/<slug>/
|-- task.yaml              # task: {name, slug, description, tips} only
|-- litellm_config.yaml    # llm/workspace/run/stages/score/loop
|-- grader/                # optional: MVMoE data/generator, read-only input
|-- instance_gen/          # optional: generator code or description, read-only input
|-- seed/                  # optional notes
baseline/pomo/            # one shared POMO tree
src/vrpar/                # engine
docs/prompts_zh/          # Chinese translations of the prompts and roles
problems/reference/       # CVRPLTW reference bundle (description + grader inputs)
```

There is no project-root `task.yaml`. Start a task explicitly:

```bash
uv run vrpar start problems/<slug>/task.yaml
```

`vrpar start` with no task path is an error.

### `grader/` after the cleanup

The old `grader.py` files were removed: scoring now comes from the generated test
entry, not from a bundle checker. A `grader/` directory may still contain useful
read-only inputs:

- `mvmoe_gen.py` - instance loader / MVMoE generator;
- `mvmoe/` - the vendored MVMoE environment;
- `data/*.pkl` - the evaluation instances.

These are inputs for analysis/code generation, not scoring hooks.

## The run

```
analysis        agent   -> artifacts/analysis/plan.md
code_generation agent   -> artifacts/generated/
code_error      agent   -> artifacts/generated/test_train.py + artifacts/runs/<key>/
code_adjustment agent   -> artifacts/revisions/ (repair, or apply one nn-improve plan)
nn_improve      agent   -> artifacts/nn_improve/plans/<id>.md (one file per plan)
```

`<key>` names one execution: `initial`, `initial-search`, `repair-<n>`,
`plan-<id>`, or `final`. Each execution gets its own directory, so the plan runs
never overwrite each other.

`code_error` must leave, under `artifacts/runs/<key>/`:

```
.../test_train.stdout.txt
.../test_train.stderr.txt
.../test_train.exitcode
.../test.stdout.txt
.../test.stderr.txt
.../test.exitcode
```

The agent writes the stdout/stderr/exitcode files; the pipeline writes
`report.json` from the gate result.

The actual test entry is discovered by name: any `test_*.py` under
`artifacts/generated/` that is not `test_train.py`. It follows the
`test_n<n>.py` convention — `n` is replaced by the problem size, e.g.
`test_n50.py` or `test_n100.py` — and the plan pins the exact name; the
pipeline does not hard-code a single size.

Success requires all three:

1. `test_train.py` contains the protocol pins (`acceptance`: 200 and 5000;
   `search`: 50 and 5000);
2. both exit codes are 0;
3. the final test entry stdout contains `AUGMENTATION SCORE: <float>`.

The score is the last `AUGMENTATION SCORE:` value. No feasibility gate, no plan
audit, no `run.py`.

The test data defaults to random instances (`test_data_load.enable = False`,
`test_episodes = 10*1000`) and loads `checkpoint-<epochs>.pt` through
`--model-path`. A `task.tips` entry may pin a fixed test set instead; then the
generated entry loads that set and `test_episodes` equals its size.

## Run directory

```
results/<slug>/<run-id>/
|-- artifacts/analysis/plan.md
|-- artifacts/generated/                 # the current package
|-- artifacts/runs/<key>/                # one directory per execution:
|   |-- test_train.* / test.*            #   captured stdout/stderr/exitcode
|   |-- report.json                      #   the gate result
|   |-- checkpoint-<epochs>.pt           #   the weights that produced the score
|   `-- generated/                       #   the code that produced the score
|-- artifacts/revisions/                 # repair and plan notes
|-- artifacts/nn_improve/
|   |-- plans.json                       # the ledger
|   |-- plans/<id>.md                    # one design note per plan (agent)
|   |-- initial/generated/               # fallback code
|   |-- best/generated/                  # champion
|   `-- winners/<plan-id>/generated/     # label=1 snapshots
|-- logs/pipeline.log
`-- state.json
```

## nn improve

After the initial acceptance run passes, the pipeline searches neural-network method
plans:

- `initial-search` re-runs the initial code at the search budget to get the fixed
  label threshold `initial_search_score`;
- up to `nn_improve.rounds` batches (3 by default), each with
  `strategies_per_batch` strategies and `mutations_per_batch` mutations; every
  plan changes one network method (structure, information flow, loss, training or
  decoding mechanism), not a hyper-parameter value;
- each plan starts from the snapshot the pipeline assigns (the initial code in
  batch 1, label=1 winners in batches 2/3), is applied by `code_adjustment`, and
  is scored by `code_error`;
- a plan that beats `initial_search_score` gets label 1 and its code is kept under
  `winners/`; a better one replaces `best/`;
- a batch with no winner stops the search;
- the champion is re-run at the acceptance budget; if it does not beat the initial
  acceptance score, the initial code is exported instead.

`artifacts/nn_improve/plans.json` is the ledger: `pending`, `running`,
`completed`, `executed`, `failed`, plus `label` (1 = beat the threshold).
Evaluation conditions (test set, seeds, augmentation, POMO size, budget) are fixed,
so plan scores are comparable; plans that change network size are comparable in
score, not in cost.

Each plan is one markdown file under `artifacts/nn_improve/plans/`, with the plan id as
the file name: YAML front matter (`id/batch/kind/target/change/rationale`) plus the
design note (`Change`, `Why`, `Implementation notes`, `Effect/cost`, `Risk`,
`What it does not change`). There is no strategy catalogue: the agent reads the champion
code and designs the methods itself. A plan changes the network (plus the input
representation and masking code that serves it); the tester, the trainer, the entry
scripts and the instance generation are out of scope.

See [`docs/nn_improve.md`](docs/nn_improve.md) for the operator guide;
[`docs/nn_improve_strategies.md`](docs/nn_improve_strategies.md) is a historical note on
the retired human-maintained catalogue.

## Verification

Every execution keeps its weights and the code that produced its score, under
`artifacts/runs/<key>/` (`<key>` = `initial`, `initial-search`, `plan-<id>`, `final`). To
re-check a result later, from that execution's `generated/` directory:

```bash
python test_n50.py --model-path ../checkpoint-50.pt     # a plan (search protocol)
python test_n50.py --model-path ../checkpoint-200.pt    # initial / final (acceptance)
```

The champion's checkpoint is also exported; see below.

## Export

A successful run exports the package the final acceptance approved:

```
workspace/final/<task.slug>/
|-- artifacts/generated/    # champion, or the initial code on fallback
|-- checkpoints/            # checkpoint-<epochs>.pt for the exported package
|-- pomo/                   # full shared baseline, needed by the imports
```

The exporter picks `artifacts/nn_improve/best/generated` when the final acceptance
beat the initial score, and `artifacts/nn_improve/initial/generated` otherwise.
`result/`, caches and samples are excluded.

## Evaluation data

The reference bundle ships `grader/data/vrpltw50_uniform.pkl`; an arbitrary
bundle may not. If a task pins no test set, the generated entry uses random
instances. To use the published MVMoE instances, place them under
`problems/<slug>/grader/data/` and pin them in `task.tips`; see
`problems/reference/` and the MVMoE project.

`instances/`, `results/` and `workspace/` are generated at run time and are not
part of the release checkout.

## Commands

| command | what it does |
|---|---|
| `vrpar start problems/<slug>/task.yaml` | run the pipeline |
| `vrpar start <task> --resume <run-dir>` | continue an interrupted run |
| `vrpar ui` | dashboard; no task required |
| `vrpar status` | latest run |
| `vrpar stages` | stage table |
| `vrpar stage <name>` | replay one stage |
| `vrpar log` | pipeline log tail |
| `vrpar show <path>` | print an artifact |
| `vrpar problems` | list bundles |
| `vrpar validate problems/<slug>/task.yaml` | check config |
| `vrpar doctor` | runtime prerequisites |
| `vrpar clean` | remove run dirs |

Offline run:

```bash
uv run vrpar start problems/mvmoe_ovrpl_n50/task.yaml \
  llm.backend=mock run.setup=false workspace.results_dir=./results-smoke
```

### Resume

```bash
uv run vrpar start <task> --resume <run-dir>
```

`--resume` accepts a path, `results/<slug>/<run-id>`, or a bare `<run-id>`. It
skips the stages that already finished, resets a mid-flight plan to `pending`,
continues the current batch and the remaining batches, then re-runs the champion
acceptance unless it was already attempted. See `docs/nn_improve.md` section 10.

## Config

`task.yaml` only carries the statement. Everything else is in the bundle's
`litellm_config.yaml`. Precedence: built-in defaults < `configs/default.yaml` <
root `litellm_config.yaml` < bundle `litellm_config.yaml` < task file < CLI.

The engine ignores extra keys in `task.yaml`; `scripts/migrate_task_schema.py`
moves old keys to `litellm_config.yaml`.

The current default stage table is defined in `src/vrpar/pipeline/spec.py`.

### `nn_improve`

`configs/default.yaml` carries the search defaults:

```yaml
nn_improve:
  enabled: true
  rounds: 3
  strategies_per_batch: 10
  mutations_per_batch: 2
  max_repairs_per_plan: 1
  space:
    deny: ["scale.problem_size", "scale.pomo_size", "scale.aug_factor"]
  search:     {train_epochs: 50,  train_episodes: 5000}
  acceptance: {train_epochs: 200, train_episodes: 5000}
```

Any key can be overridden on the command line, e.g.
`nn_improve.rounds=1 nn_improve.search.train_epochs=20`.

## Reference template

`problems/reference/` is a CVRPLTW example showing:

- a complete `task.description`;
- `litellm_config.yaml` shape;
- [`plan_outline.md`](problems/reference/plan_outline.md) - what analysis should produce;
- MVMoE data/generator inputs.

It is a template, not a required bundle; copy and adapt it for a new variant.

## Runtime

`llm.backend` selects `claude_code`, `dsh`, or `mock`. `mock` runs the
whole pipeline offline and is what the tests use.

For `dsh`, keep the provider credential in the launching shell
(`DEEPSEEK_API_KEY`); if the inherited DSH home is not writable, the backend uses
a run-local `.dsh` without the credential store.

## Tests

```bash
uv run pytest -q
```

The suite runs offline and needs no model runtime. One baseline-import test is
skipped when `torch` is not installed (the normal state after `uv sync --extra dev`);
`workspace.setup` installs it for a real run.


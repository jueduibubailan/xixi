# vrpar - VRP-variant code synthesis on POMO

A four-stage, agent-driven pipeline. A task describes a VRP variant; the pipeline:

1. **analysis** turns the statement into `artifacts/analysis/plan.md` - scale, file
   names, interface, masking, training/test protocol;
2. **code_generation** adapts POMO into a runnable package;
3. **code_error** generates `test_train.py` (1 epoch x 64 episodes), trains, saves
   `checkpoint-1.pt`, then runs the generated final test entry with random test data;
4. **code_revise** repairs the generated code when a run fails.

The pipeline itself does not parse the plan. Agents read it and decide the work.
The pipeline seeds worktrees, checks the mechanical conditions below, records the
score, and exports the final code.

## Install

Requirements:

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)
- an agent runtime on `PATH`: `dsh` (DeepSeek Harness), Claude Code, or `mock`

```bash
uv sync --extra dev
```

The offline path needs no model runtime:

```bash
uv run vrpar start problems/cvrptw/task.yaml llm.backend=mock
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
problems/reference/       # CVRPLTW reference bundle (description + plan outline)
```

There is no project-root `task.yaml`. Start a task explicitly:

```bash
uv run vrpar start problems/cvrptw/task.yaml
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
code_error      agent   -> test_train.py + final test run, stdout/stderr/exitcode
code_revise     agent   -> repair on failure
```

`code_error` must leave:

```
artifacts/generated/test_train.py
artifacts/generated/test_<n>.py        # the actual final test entry from code_generation
artifacts/runs/test_train.stdout.txt
artifacts/runs/test_train.stderr.txt
artifacts/runs/test_train.exitcode
artifacts/runs/test.stdout.txt
artifacts/runs/test.stderr.txt
artifacts/runs/test.exitcode
```

The actual test entry is discovered by name: any `test_*.py` under
`artifacts/generated/` that is not `test_train.py`. The plan decides the exact
name (`test_50.py`, `test_n50.py`, ...); the pipeline does not assume `test_n`.

Success requires all three:

1. `test_train.py` contains `'epochs': 1` and `'train_episodes': 64`;
2. both exit codes are 0;
3. the final test entry stdout contains `AUGMENTATION SCORE: <float>`.

The score is the last `AUGMENTATION SCORE:` value. No feasibility gate, no plan
audit, no `run.py`.

The final test entry uses `test_episodes = 10*1000` and
`test_data_load.enable = False`, so it generates random test problems. It loads
`checkpoint-1.pt` through `--model-path`.

## Export

A successful run exports:

```
workspace/final/<task.slug>/
|-- artifacts/generated/    # modules, train entry, test entry, test_train.py, README
|-- pomo/                   # full shared baseline, needed by the imports
```

`result/`, caches and samples are excluded.

## Evaluation data

The repository does not ship the MVMoE `*.pkl` evaluation sets: they are large
binary inputs, not required for the generated random test path. If you need the
exact published instances, download them separately or place them under
`problems/<slug>/grader/data/`; see `problems/reference/` and the MVMoE project.

`instances/`, `results/` and `workspace/` are generated at run time and are not
part of the release checkout.

## Commands

| command | what it does |
|---|---|
| `vrpar start problems/<slug>/task.yaml` | run the pipeline |
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
uv run vrpar start problems/cvrptw/task.yaml \
  llm.backend=mock workspace.results_dir=./results-smoke
```

## Config

`task.yaml` only carries the statement. Everything else is in the bundle's
`litellm_config.yaml`. Precedence: built-in defaults < `configs/default.yaml` <
root `litellm_config.yaml` < bundle `litellm_config.yaml` < task file < CLI.

The engine ignores extra keys in `task.yaml`; `scripts/migrate_task_schema.py`
moves old keys to `litellm_config.yaml`.

The current default stage table is defined in `src/vrpar/pipeline/spec.py`.

## Reference template

`problems/reference/` is a CVRPLTW example showing:

- a complete `task.description`;
- `litellm_config.yaml` shape;
- `plan_outline.md` - what analysis should produce;
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


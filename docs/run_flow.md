# Run flow

```
uv run vrpar start problems/<slug>/task.yaml
```

`vrpar start` with no path is an error.

## 0. Preparation

- Load `task.yaml` (only `task:`), the bundle's `litellm_config.yaml`, and the
  project defaults.
- Discover the bundle by the task file's directory (`problems/<slug>/`).
- Seed `baseline/pomo/` and, if present, `instance_gen/` / `grader/` as read-only
  inputs under `data/inputs/`.
- Run `workspace.setup` if declared.

## 1. analysis (agent, no gate)

Writes only `artifacts/analysis/plan.md`. The pipeline checks the file exists but
does not parse it. The plan states the scale, input mode, file names, interface,
masking, test protocol and score line.

Three input modes: description only, generator code, or a written description of
the generator.

## 2. code_generation (agent, no gate)

Reads `plan.md` and writes under `artifacts/generated/`:

- the five adapted POMO modules;
- `train_n<n>.py`;
- `test_<n>.py` (random test data, `test_episodes = 10*1000`, `--model-path`);
- `README.md`.

It does not write `run.py` or `test_train.py`.

## 3. code_error (agent)

Generates `artifacts/generated/test_train.py`:

```python
'epochs': 1
'train_episodes': 64
'train_batch_size': 64
```

Runs it (saving `checkpoint-1.pt`), then runs `test_<n>.py --model-path ...`
with random test data. Captures stdout/stderr/exitcode under `artifacts/runs/`.

## 4. The check

`vrpar.pipeline.gates.check_code_error` requires:

1. `test_train.py` contains `epochs: 1` and `train_episodes: 64`;
2. `test_train.exitcode == 0`;
3. `test.exitcode == 0`;
4. `test.stdout` has `AUGMENTATION SCORE: <float>`.

The score is the last match. On failure the loop enters `code_revise` and returns
to `code_error`, up to `loop.max_rounds`.

## 5. code_revise (agent)

Reads the captured output and the generated code, fixes the error, re-runs
`test_train.py` / `test_<n>.py`, writes `artifacts/revisions/round-<n>.md`. It
does not edit `plan.md`.

## 6. Export

On success:

```
workspace/final/<task.slug>/artifacts/generated/
workspace/final/<task.slug>/pomo/
```

`result/`, `__pycache__` and samples are not exported.

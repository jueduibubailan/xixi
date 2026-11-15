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
- `test_n<n>.py` (random test data, `test_episodes = 10*1000`, `--model-path`);
- `README.md`.

It does not write `run.py` or `test_train.py`.

## 3. code_error (agent)

Generates `artifacts/generated/test_train.py`:

```python
'epochs': 200
'train_episodes': 5000
'train_batch_size': 64
```

Runs it (saving `checkpoint-200.pt`), then runs `test_n<n>.py --model-path ...`
with random test data. Captures stdout/stderr/exitcode under `artifacts/runs/`.

The test set can be specified by `task.tips`; when it is, the entry loads that set
(and `test_episodes` is its size). Otherwise the random protocol above applies.

## 4. The check

`vrpar.pipeline.gates.check_code_error` requires:

1. `test_train.py` contains `epochs: 200` and `train_episodes: 5000`;
2. `test_train.exitcode == 0`;
3. `test.exitcode == 0`;
4. `test.stdout` has `AUGMENTATION SCORE: <float>`.

The score is the last match. On failure the loop enters `code_adjustment` and returns
to `code_error`, up to `loop.max_rounds`.

## 5. code_adjustment (agent)

Reads the captured output (under `data/artifacts/runs/`) and the generated code,
fixes the error with the smallest change, and writes
`artifacts/revisions/round-<n>.md`. The next `code_error` re-runs the training and
the test entry; this stage does not run them itself. It does not edit `plan.md`.
In `plan` mode it implements exactly one neural-network method change from the nn improve
stage.

## 6. nn improve (agent)

Runs up to `nn_improve.rounds` batches (3 by default). Each batch asks for
`strategies_per_batch` strategy plan files plus `mutations_per_batch` mutations; every
plan changes one network method (structure, information flow, loss, training or decoding
mechanism) and is based on the snapshot the pipeline assigns (the initial code in
batch 1, the label=1 winners in batches 2/3). Each plan is one markdown file under
`artifacts/nn_improve/plans/`, with YAML front matter and a design note.

Each plan is then evaluated with the `search` protocol (50×5000): the champion is
restored, `code_adjustment` applies the plan, `code_error` scores it. A plan whose
score beats the fixed `initial_search_score` gets label 1 and its code is saved
under `artifacts/nn_improve/winners/`; a better one also replaces
`artifacts/nn_improve/best/`. A batch with no winner stops the search.

The champion is re-run once with the `acceptance` protocol (200×5000); if it does
not beat the initial code, the initial code is exported instead.

## 7. Export

On success:

```
workspace/final/<task.slug>/artifacts/generated/
workspace/final/<task.slug>/pomo/
```

`result/`, `__pycache__` and samples are not exported.

## 8. Resume

If a run is interrupted (killed, crash, or a blocked state write), continue it:

```bash
uv run vrpar start problems/<slug>/task.yaml --resume results/<slug>/<run-id>
```

It skips the finished stages, resets a mid-flight plan to `pending`, continues the
current batch and the remaining batches, re-runs the champion acceptance unless it
was already attempted, and exports.

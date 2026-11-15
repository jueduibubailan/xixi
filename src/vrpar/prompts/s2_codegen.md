# S2 — Code generation (code generation agent)

You are the code-generation stage. Read `data/artifacts/analysis/plan.md` first. It defines the scale, the file names and the code changes. The baseline is POMO, and
it must stay POMO.

## Content must match the existing style

- Keep the `####...####` banner comments and the surrounding comment density.
- Keep the conventions you find: module-level configuration stays UPPERCASE
  (`PROBLEM_SIZE`, `CUDA_DEVICE_NUM`), dict keys and function arguments stay
  `snake_case` (`problem_size`, `pomo_size`, `aug_factor`), classes stay
  `CamelCase` with the same constructor shape.
- Put new behaviour **inside the existing functions** (`_get_encoding`, `step`,
  `_get_mask`, `load_problems`, `run`, ...). Do not wrap them in new layers,
  manager objects, base classes or decorators.
- Keep docstring/comment language and formatting as they are. Do not add type
  annotations where the baseline has none; do not reformat untouched lines.
- Prefer a form the baseline already shows. Concretely: the tester already carries
  the `load_state_dict(..., strict=False)` alternative in a comment — use that,
  do not invent a loader wrapper.
- Imports between your own modules keep the baseline's shape: they are imported as
  flat modules (`from {problem}ProblemDef import ...`,
  `from {problem}Env import ...`), while untouched helpers come from the staged
  baseline (`from pomo.POMO.utils.utils import ...`).

## Deliverable

Write under `artifacts/generated/`:

- `<TAG>ProblemDef.py`
- `<TAG>Env.py`
- `<TAG>Model.py`
- `<TAG>Trainer.py`
- `<TAG>Tester.py`
- `train_n<n>.py` — the training entry, adapted from POMO's `train_n100.py`
- `test_n<n>.py` — the test entry, adapted from POMO's `test_n100.py`
- `README.md`

Do **not** write `run.py`. Do **not** write `test_train.py`; code_error generates
it.

Keep POMO's training budget (`epochs`, `train_episodes`,
`train_batch_size`) in `train_n<n>.py`:

    epochs: 8100
    train_episodes: 10 * 1000
    train_batch_size: 64

`test_n<n>.py` must keep POMO's test settings:

- follow `plan.md` for the final test data: if the plan names a test set, load
  it; otherwise set `test_data_load.enable = False` and let
  `env.load_problems()` generate random test problems. Use the plan's
  `test_episodes`; if the plan gives none, use the test set's instance count for
  a fixed set and `10*1000` for random problems;
- if the plan names a test file or directory, copy it into
  `artifacts/generated/` and point `test_data_load.filename` at the copy; do not
  reference `data/inputs/`. Implement the loader for the plan's record layout;
  do not assume `.pkl`;
- accept `--model-path` (or `MODEL_PATH`) and load `checkpoint-<epoch>.pt` from
  it;
- print POMO's `AUGMENTATION SCORE: <float>` when done.

The plan is authoritative for which test set to use; the file itself is
authoritative for its record format. The `--model-path` loading and the
`AUGMENTATION SCORE:` line above still apply.

Do not copy `pomo/` into the deliverable; the export step adds it.

## Rules

- The five baseline files keep their roles and names; adapt, do not rewrite.
- No pretrained weights; training starts from random initialisation.
- Keep POMO's style and device chain; only `USE_CUDA` may honour
  `VRPAR_FORCE_CPU`.
- The deliverable must be runnable from its own directory.
- Do not silently drop a masking rule you disagree with: **implement it**, then
  record your disagreement in the final summary.
- Model hyperparameters live in POMO's `model_params`. `train_n<n>.py` and
  `test_n<n>.py` must declare the **same** values, so that a later single-method
  change cannot make the checkpoint and the tester disagree.

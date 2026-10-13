# Attempts — cvrptw

> Scope: **problem-specific**. One block per run, appended, newest last. Blocks are keyed by
> run id, so the fastest way to find one is to grep for it:
> `grep -n '^## ' attempts/cvrptw_attempts.md`. Run ids also appear in
> `records/main/index.json`, which records which files each run contributed.
> A block written by hand rather than by the update stage carries `source: manual run`.

## Contents

- `How to read these` — block layout, keying and retention *(general)*
- `Blocks` — one `## <run_id> · n=… · <outcome>` section per run, appended *(problem-specific)*

## How to read these

Every block opens with `## <run_id> · n=<scale> · <outcome> · rounds=<k> · source: <pipeline|manual run>`
and then states: what was asked, what the generation wrote, what failed (with the evidence),
what was revised, the outcome, and the lessons. Blocks are never rewritten — a later run
appends. The most recent blocks are the most likely to reflect the current code.

## Blocks

## 20261012-231939 · n=100 · FAILED (crash) · rounds=0 · source: manual run

- **task**: `CVRPTW-n100-on-POMO`; generated package executed directly by the user, *not*
  through the pipeline. Command:
  `D:\Anaconda\envs\pomo\python.exe G:\work\pomo\cvrptw-n100\artifacts\generated\train_n100.py`
- **environment**: Windows, conda env `pomo`, CUDA available (`USE_CUDA: True`), torch with
  GPU. The pipeline's own executor uses CPU-only torch, so this path had never been run.
- **crash**: `torch.AcceleratorError: CUDA error: device-side assert triggered` from the
  scatter-gather kernel, reported at `CVRPTWProblemDef.py:109` (`_travel_time`). CUDA
  reports asynchronously, so that line is **not** where the fault is.
- **real fault** (CPU reproduction, same code, `torch 2.14.1+cpu`):
  ```
  CVRPTWProblemDef.py:62  selected_demand = node_demand.gather(dim=1, index=selected[:, None])
  RuntimeError: index 20 is out of bounds for dimension 1 with size 20
  ```
  at `get_random_problems(2, 20)`, i.e. **the first training batch**.
- **root cause**: `_get_time_windows` builds `canonical_order = argsort(rand) + 1`, making
  it 1-based so it can index `depot_node_xy` (which has `problem_size + 1` rows). It is
  then used to index `node_demand`, which has only `problem_size` rows and is 0-based.
  `problem_size` is a legal 1-based id but one past the end of `node_demand`.
- **minimal fix** (verified on CPU, `problem_size` 5/20/100, `batch_size` 2/8):
  ```python
  # canonical_order is 1-based so it can index depot_node_xy; node_demand is 0-based.
  selected_demand = node_demand.gather(dim=1, index=(selected - 1)[:, None]).squeeze(dim=1)
  ```
  With that one line, `get_random_problems` returns well-formed windows and service times.
- **why the pipeline did not catch it**: the gate sequence is
  `py_compile -> import_probe -> correspondence -> smoke_run -> full_run`, and all of them
  passed, because they exercise `run.py` (inference) against the shipped instances.
  `_get_time_windows` lives on the **training-only** path: `CVRPTWTrainer._train_one_batch
  -> CVRPTWEnv.load_problems -> get_random_problems`. Nothing in the pipeline ever calls
  `load_problems` with random instances, so the first time this code ran was on the user's
  machine.
- **secondary observations from the same run** (not the crash):
  - `USE_CUDA: True` and `torch.set_default_tensor_type('torch.cuda.FloatTensor')` were
    taken at face value; the plan had asked for a CPU-gated entry point on the pipeline's
    box. It did not break anything here, but the deliverable's CUDA gate is a host
    property, and the generated code assumed the pipeline's host.
  - `copy_all_src skipped: [Errno 2] ... _ops.py` — the planned `try/except` wrapper was
    implemented correctly and did its job.
  - `lr_scheduler.step()` before `optimizer.step()` warning — inherited from POMO, harmless.
- **outcome**: training never completed; no checkpoint, no cost, nothing measured.
- **lessons**: see `notes/cvrptw_notes.md` -> Traps (the 1-based/0-based split, and the
  fact that a green gate sequence says nothing about the training path), and
  `skills/cvrptw_skill.md` -> Verification playbook (exercise the training path).
- **artifacts**: `G:\work\pomo\cvrptw-n100\artifacts\generated\CVRPTWProblemDef.py`
  (the fault is line 62); the user's console traceback is quoted above.

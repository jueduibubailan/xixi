# S2 - Code generation (code generation agent)

You are the **code generation stage**. AFL solves this by generating one heuristic
function at a time. Here the baseline is a complete neural solver, so your job is the
opposite of a rewrite: **adapt POMO, keeping its structure, its style and its
parameter values.** The contract below is strict precisely because the output has to
load a pretrained network without turning into a different codebase.

Problem: **{problem_name}** (`{variant_label}`).

## Read first

1. `data/artifacts/analysis/analysis.json` -- the contract from S1. Treat its
   `masking_rules`, `state_variables`, and `pomo_modification_plan` as binding. If
   you believe a rule is wrong, still implement the rest, and record the
   disagreement in your final summary.
2. `{baseline_notes}` -- the baseline map, the domain semantics, the deliverable
   rules, and the exact acceptance contract.
3. The baseline sources staged in this worktree (the complete list is in the
   "Available inputs" section below). Read the ones you are about to edit.
4. `data/instances/` -- the instance schema you must consume.

## How to modify the baseline (binding)

### 1. Edit the baseline in place, then export it under the problem's name.

Do your editing inside the staged baseline itself:

```
pomo/CVRProblemDef.py
pomo/POMO/CVRPEnv.py
pomo/POMO/CVRPModel.py
pomo/POMO/CVRPTrainer.py
pomo/POMO/CVRPTester.py
pomo/POMO/train_n100.py
pomo/POMO/test_n100.py
```

Then **write each edited file into `{generated_dir}/` under its new name**: the
problem tag plus the role. For this problem the tag is `{problem}`, so:

```
{generated_dir}/
├─ {problem}ProblemDef.py     <- from pomo/CVRProblemDef.py
├─ {problem}Env.py            <- from pomo/POMO/CVRPEnv.py
├─ {problem}Model.py          <- from pomo/POMO/CVRPModel.py
├─ {problem}Trainer.py        <- from pomo/POMO/CVRPTrainer.py
├─ {problem}Tester.py         <- from pomo/POMO/CVRPTester.py
├─ train_n{n}.py              <- from pomo/POMO/train_n100.py
├─ test_n{n}.py               <- from pomo/POMO/test_n100.py
├─ run.py                     <- the acceptance CLI (no baseline source)
└─ README.md
```

**Every file that comes from the baseline must start with this exact line** (it is
checked mechanically):

```python
# baseline-correspondence: pomo/POMO/CVRPEnv.py
```

Use the source path as it appears in the worktree. `run.py` and `README.md` have no
baseline source, so they must **not** carry the line. Do not copy `utils.py` or any
other unchanged baseline file: `{problem}Trainer.py` and `{problem}Tester.py` import
it from the baseline in place.

Nothing else belongs in `{generated_dir}/`. A `.py` file outside that list is extra
generated code and fails the executor's correspondence gate.

**Do not copy `pomo/` yourself.** The export step bundles the baseline payload
(`pomo/`, which includes `POMO/utils/utils.py` and `POMO/utils/log_image_style/*.json`)
next to `artifacts/generated/`, so the published directory is runnable as-is. Adding
your own copy would be extra generated code, and it would shadow the real one.

### 2. Match the existing style, exactly.

- Keep the `####...####` banner comments and the surrounding comment density.
- Keep the naming conventions you find: module-level configuration stays
  UPPERCASE (`PROBLEM_SIZE`, `CUDA_DEVICE_NUM`), dict keys and function arguments
  stay `snake_case` (`problem_size`, `pomo_size`, `aug_factor`), classes stay
  `CamelCase` with the same constructor shape.
- Put new behaviour **inside the existing functions** (`_get_encoding`, `step`,
  `_get_mask`, `load_problems`, `run`, ...). Do not wrap them in new layers, manager
  objects, base classes, or decorators.
- Keep docstring/comment language and formatting as they are. No type annotations
  where the baseline has none; no reformatting of untouched lines.
- Prefer a form the baseline already shows. Concretely: the tester already carries
  the `load_state_dict(..., strict=False)` alternative in a comment -- use that, do
  not invent a loader wrapper.
- Imports between your own modules keep the baseline's shape: they are imported as
  flat modules (`from {problem}ProblemDef import ...`,
  `from {problem}Env import ...`), while the untouched helpers come from the staged
  baseline (`from pomo.POMO.utils.utils import ...`).

### 3. Keep POMO's parameter values. Do not tune them down.

The training budget and the test budget are **not** yours to choose: copy POMO's
values verbatim into `trainer_params` / `tester_params`.

| where | key | value |
|---|---|---|
| `trainer_params` | `epochs` | `8100` |
| `trainer_params` | `train_episodes` | `10 * 1000` |
| `trainer_params` | `train_batch_size` | `64` |
| `trainer_params` | `model_save_interval` | `500` |
| `trainer_params` | `img_save_interval` | `500` |
| `tester_params` | `test_episodes` | `10 * 1000` |
| `tester_params` | `test_batch_size` | `1000` |
| `tester_params` | `aug_factor` | `8` |

- Only `problem_size` / `pomo_size` change, and only to the task's scale ({n}).
- `_set_debug_mode()` exists to override the budget for a quick local run. Leave it
  exactly as the baseline has it; its small values must never become the defaults.
- Do not silently shrink the budget "so the pipeline is fast". The executor's
  correspondence gate compares these values against POMO's and fails on a mismatch.

### 4. `model_load`: the training entry point must not load a checkpoint.

`CVRPTrainer.py` only calls `torch.load` **inside** `if model_load['enable']:`. POMO's
`train_n100.py` therefore ships `enable: False` with `path`/`epoch` commented out, and
that is exactly what you must keep -- an active relative path is the classic
`FileNotFoundError` (`..\..\checkpoints/checkpoint-30500.pt` resolves outside the
project):

```python
'model_load': {
    'enable': False,  # the CVRP100 checkpoint is the starting point
    # 'path': CHECKPOINT_DIR,  # directory path of pre-trained model and log files saved.
    # 'epoch': 30500,  # epoch version of pre-trained model to laod.
}
```

`path` and `epoch` **must stay commented out** (the gate checks this). Training starts
from scratch and the trainer writes **its own** checkpoints into `result_folder`
(`result/<timestamp>_train_.../checkpoint-<epoch>.pt`).

**The test entry point is the opposite**: `test_n{n}.py` must really load weights. It
reads them from the **`checkpoints/` drop box**, which ships empty: the user runs
`train_n{n}.py`, then puts the weights they want evaluated into `checkpoints/`. So the
resolution is two-level, at run time:

1. **an explicit override** -- `--model-path <dir or .pt file>` on the command line, or
   `MODEL_PATH` in the environment;
2. **`checkpoints/`** -- take the **highest epoch** among `checkpoint*.pt`.

Never hardcode a checkpoint filename or an epoch: the directory is filled by the user.

```python
CHECKPOINT_GLOB = 'checkpoint'          # matches checkpoint*.pt
EPOCH_PREFIX = 'checkpoint-'

def _resolve_model_load():
    """`checkpoints/` is the drop box for weights the user trained.

    The tester rebuilds the filename as `'{path}/checkpoint-{epoch}.pt'`, so a
    directory needs the epoch parsed out of the file name -- not assumed.
    """
    entry = os.environ.get('MODEL_PATH') or _default_checkpoint_dir()
    if entry.endswith('.pt'):
        name = os.path.basename(entry)
        return {'path': os.path.dirname(entry) or '.',
                'epoch': int(name[len(EPOCH_PREFIX):-3])}
    best_epoch = -1
    for name in os.listdir(entry):
        if not (name.startswith(CHECKPOINT_GLOB) and name.endswith('.pt')):
            continue
        try:
            epoch = int(name[len(EPOCH_PREFIX):-3])
        except ValueError:
            continue          # `checkpoint-<not a number>.pt` is not ours: skip it
        if epoch > best_epoch:
            best_epoch = epoch
    return {'path': entry, 'epoch': best_epoch}

tester_params['model_load'] = _resolve_model_load()
```

`_default_checkpoint_dir()` returns the `checkpoints/` directory that sits next to
`artifacts/generated/` (resolved from `__file__`, not written as `'..\\..\\checkpoints'`).
What you must not do:

- hardcode `'epoch': 30500` or any filename -- the directory is user-populated;
- scan `result/` and silently prefer a training artefact: `checkpoints/` is the single
  source of truth for evaluation;
- point `path` at a file and leave `epoch` at a default: the tester concatenates them,
  so both must describe the same file.

### 5. `logging`: keep the baseline's plotting configuration working.

`img_save_interval` and the `log_image_params_*` blocks drive
`util_save_log_image_with_label`, which reads its style file relative to `utils.py`
itself:

```python
log_image_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name, file_name)
```

**The referenced file must exist.** `json_foldername` stays `log_image_style`, and
`filename` must be one of the files actually present in this worktree's
`pomo/POMO/utils/log_image_style/`:

| block | expected `filename` | what it draws |
|---|---|---|
| `log_image_params_1` | `style_cvrp_20.json` | the objective (score) curve |
| `log_image_params_2` | `style_loss_1.json` | the training loss curve |

Note that the baseline's own `train_n100.py` names `style_cvrp_100.json`. That file is
shipped, but it is the **wrong choice here**: its `ylim` is pinned to `10`..`30`, the
window upstream chose for its CVRP-100 benchmark. This task trains from scratch, and its
objective leaves that window immediately (the first-epoch cost is well above 30 for
n=100), so the plot renders as an empty box. Use `style_cvrp_20.json`: its `ylim` is
`min: 0, max: null`, which cannot clip an objective curve.

**The plotted range must match the data.** Each style file carries a `figsize` and a
`ylim`/`xlim` window, and the shipped values are presets, not truths:

```json
{"figsize": {"x": 10, "y": 5},
 "xlim": {"min": null, "max": null},
 "ylim": {"min": 0, "max": null},
 "grid": true}
```

- `null` means "autoscale", which is always safe;
- a **hardcoded** bound is a framing decision. `{"min": 6.12, "max": 6.40}` was chosen
  for a model that resumes at epoch 30500; against a run that starts from scratch it
  plots an **empty window** -- the curve is off-screen and the plot looks broken.
- So: if you tighten a bound, it must contain the values this run actually produces.
  The shipped files are deliberately open: the objective style uses `min: 0` with
  `max: null`, and the loss style leaves **both** ends at `null` so a high first-epoch
  loss is fully visible. Keep them that way unless you have a concrete reason -- and
  then say what range you expect and why.

Do **not** rename the style files, do **not** author new JSON, and do **not** disable
plotting by zeroing the interval. If the baseline has a `log_image_params_2` block
commented out, leave it commented as the baseline does.

## The environment is ready

`torch`, `numpy`, `pytz` and `matplotlib` are installed, and the baseline runs as-is
in this worktree. The published directory gets the same treatment: the export bundles
`pomo/` next to `artifacts/generated/`, so a user can train and evaluate without
installing or locating anything. So:

- **do not** add dependency declarations, `requirements.txt`, or install steps;
- **do not** reimplement anything the baseline already imports;
- **do not** copy `pomo/` into the deliverable -- the export does that;
- if you need a helper, the baseline already has it -- import it.

## What to produce, inside `{generated_dir}/`

| file | contents |
|---|---|
{generated_file_lines}

Entry points:

{generated_entry_lines}

`README.md` must contain a `## Baseline correspondence` table with one row per
deliverable (`file | POMO source`), plus the exact train/test commands, what was
implemented versus deferred, and which checkpoint keys were skipped.

## Hard requirements

1. **The checkpoint must still load.** `{checkpoint}` is a pretrained model. Keep
   the pretrained module's input dimensions unchanged and load it with
   `load_state_dict(..., strict=False)`; report which keys were skipped in your
   summary (`checkpoint_keys_skipped`). If a key mismatch is unavoidable, explain
   why in the summary.
2. **New information enters the decoder context, not the encoder.** Concretely:
   concatenate the new dynamic state bits into the vector the decoder consumes
   before its first linear layer. If that changes the decoder's input width,
   widen only the *first* linear layer that consumes the context and note it as a
   non-loadable key.
3. **Masking is a single tensor.** Build the action mask as the logical OR of the
   individual masks named in `analysis.json`, with any declared exception (e.g.
   the depot) never masked. Implement one helper per mask rule so the revise stage
   can find them.
4. **`{program}` is the acceptance surface.** It must accept:

   ```
   python {program} {cli_accepts}
   ```

   * load the instance,
   * load `{checkpoint}` if present (via an optional flag; skip silently when missing),
   * evaluate the initial solution and print:
{contract_lines}
   * run the improvement/training loop for the iteration budget, keeping the best
     feasible solution,
   * if the save-solution flag is given, write the best solution as JSON there. The
     expected keys are described in this problem's `problem_context.md`.
   * import the adapted modules instead of reimplementing anything: resolve the
     baseline by walking up from its own location to the directory that contains
     `pomo/`, put **three** entries on `sys.path` -- that directory, `pomo/`, and
     `pomo/POMO/` -- then import your exported modules (for example
     `from {problem}Tester import {problem}Tester`). The two `sys.path.insert` lines
     in `test_n100.py` are the same idea; do not hardcode `"../.."`.
5. **CPU-capable.** `torch.device('cuda' if torch.cuda.is_available() else 'cpu')`.
   Everything must run on a CPU-only machine.
6. **No instance-specific constants.** No hardcoded sizes, coordinates, capacities
   or window values. Everything comes from the instance file. The task's size
   (`{n}`) is configuration, not instance data, and belongs in the module-level
   `problem_size` the baseline already reads.
7. **Run it before you finish.** From `{generated_dir}`, execute
   `python {program} {cli_accepts} {cli_iteration_flag} 5`, and confirm
   `python train_n{n}.py` at least constructs its environment and model (interrupt it
   after the first log line rather than running 8100 epochs). Both contract lines must
   print. A generation stage that was never executed is a guess.
8. **Declared semantics are fixed.** Follow the rules in `{baseline_notes}` and the
   analysis; do not introduce a different interpretation (for example a vehicle
   speed, or a different violation test).

## Scale for this run

The task fixes the instance size; the package is built for it.

{scale_lines}

- The training entry point is `train_n{n}.py` and the test entry point is
  `test_n{n}.py`. Apart from the size in the name, mirror the baseline's
  `train_n100.py` / `test_n100.py` structure -- same layout, same parameter blocks,
  same logging calls.
- `problem_size` is **{n}** customers. An instance file carries **{dimension}**
  nodes because index 0 is the depot; that is not the problem size.
- `pomo_size` is **{pomo}**.
- The pretrained checkpoint is size-agnostic: it loads at {n}. What must not change
  is its *embedding* width, not its node count.

Reported dimensions for reference:

{dimension_lines}

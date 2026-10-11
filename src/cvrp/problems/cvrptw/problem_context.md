Available inputs in this worktree:

## This task's scale

- **problem_size (customers) = {n}**
- **depot + customers = {dimension}** -- this is the `DIMENSION` header you will
  see in a `.vrp` instance and the length of every coordinate/demand/window array
- **pomo_size = {pomo}** parallel rollouts
- augmentation factor = {aug}

Every generated file must use these numbers: the training entry point
(`train_n{n}.py`) trains at `problem_size = {n}`, the test entry point
(`test_n{n}.py`) evaluates at `{n}`, and the model/CLI defaults follow. Do **not**
carry over the baseline's `100` (POMO ships `train_n100.py`): the checkpoint is
size-agnostic and loads at {n}, but the scripts you write are not.

Whatever the scale, the solver must still read the instance it is given at
runtime -- never assume the file holds exactly {n} customers beyond using {n} as
the dimension you build the tensors for.

- `pomo/` - the POMO CVRP reference implementation you must modify, not replace.
  - `pomo/CVRProblemDef.py` - data generation and 8-fold augmentation.
  - `pomo/POMO/CVRPEnv.py` - environment: state, masking, `step`, reward.
  - `pomo/POMO/CVRPModel.py` - encoder + POMO decoder.
  - `pomo/POMO/CVRPTrainer.py` / `CVRPTester.py` - REINFORCE training and rollout.
  - `pomo/POMO/train_n100.py` / `test_n100.py` - entry point examples.
  - `pomo/POMO/utils/utils.py` - logging and plotting helpers (`create_logger`,
    `copy_all_src`, `get_result`, ...). The trainer, the tester and both entry
    points import it, so it is part of the baseline, not an optional extra.
- `checkpoints/` - the **drop box** for weights. It ships empty; the user runs
  `train_n{n}.py` and then puts the `checkpoint-*.pt` they want evaluated in here.
  `test_n{n}.py` reads it (highest epoch among `checkpoint*.pt`), so never hardcode a
  filename or an epoch. The pretrained CVRP100 weight stays out of the deliverable: the
  encoder input is what must not change (stays 2-D), not which weight is loaded.
- `BASELINE_NOTES.md` - the baseline map, the time semantics the pipeline verifies
  against, the instance schema, and the acceptance contract.
- `data/instances/cvrptw/n{n}/*.json|*.vrp` - the synthesized n={n} instances. The
  `.json` form is what the solver consumes; the `.vrp` form is the readable mirror.

### Import paths in this layout (read this before touching an import)

The baseline is staged as `pomo/` at the worktree root, and `pomo/` itself has no
`__init__.py`. The upstream repository uses the package prefix `CVRP`, so four
baseline files used to import the helpers as
`from CVRP.POMO.utils.utils import ...` -- that path **does not exist here**. It is
already corrected in the staged baseline, which now reads
`from pomo.POMO.utils.utils import ...`; that works as soon as the **parent of
`pomo/`** is on `sys.path` (implicit namespace packages mean no `__init__.py` is
needed).

So there is nothing for you to repair here. What you must **not** do:

- **do not** copy `utils.py` (or any other unchanged baseline file) into the
  deliverable;
- **do not** rewrite these imports, add `__init__.py` files, or restructure the
  tree -- the baseline's own entry scripts show the bootstrap to follow:

  ```python
  os.chdir(os.path.dirname(os.path.abspath(__file__)))
  sys.path.insert(0, "..")      # for problem_def
  sys.path.insert(0, "../..")   # for utils
  from pomo.POMO.utils.utils import create_logger, copy_all_src
  from CVRPTester import CVRPTester as Tester
  ```

- **do not** leave a `CVRP.`-prefixed import anywhere (it cannot resolve).

`run.py` lives in `artifacts/generated/`, two levels below the worktree root, and the
adapted modules import each other by bare name (`from CVRPTWEnv import ...`). So
resolve the baseline by walking up from `run.py`'s own location to the directory that
contains `pomo/`, then put **three** entries on `sys.path`:

1. that directory (the worktree root) -- so `from pomo.POMO.utils.utils import ...` resolves
   inside the adapted modules;
2. `pomo/` -- because `CVRPEnv.py` does `from CVRProblemDef import ...`, and that
   module lives in `pomo/`, not in `pomo/POMO/`;
3. `pomo/POMO/` -- because `CVRPTester.py` / `CVRPTrainer.py` do
   `from CVRPEnv import ...`.

Those three are exactly what the two `sys.path.insert` lines in `test_n100.py` amount
to from inside `pomo/POMO/` (a script's own directory is already on the path). Do not
hardcode `"../.."`: a copy of the deliverable is not always two levels deep.
Forgetting entry 2 or 3 is the most common way this fails to import.

### What the deliverable may contain

Only this, and nothing else:

```
artifacts/generated/
├─ {problem}ProblemDef.py       <- adapted from pomo/CVRProblemDef.py
├─ {problem}Env.py              <- adapted from pomo/POMO/CVRPEnv.py
├─ {problem}Model.py            <- adapted from pomo/POMO/CVRPModel.py
├─ {problem}Trainer.py          <- adapted from pomo/POMO/CVRPTrainer.py
├─ {problem}Tester.py           <- adapted from pomo/POMO/CVRPTester.py
├─ train_n{n}.py                <- adapted from pomo/POMO/train_n100.py
├─ test_n{n}.py                 <- adapted from pomo/POMO/test_n100.py
├─ run.py                       <- the acceptance CLI (no baseline source)
└─ README.md
```

Every file adapted from POMO starts with
`# baseline-correspondence: <the pomo path above>`; `run.py` and `README.md` do not.
No extra `.py` files, no `requirements.txt`, no `__init__.py` scaffolding, and no
unchanged baseline files copied in.

**The published directory carries more than this, and you do not build it.** The export
step copies the baseline payload next to `artifacts/generated/`:

```
workspace/final/<slug>/
├─ artifacts/generated/      <- the files above
└─ pomo/                     <- the baseline, bundled so the result is runnable
   ├─ CVRProblemDef.py
   └─ POMO/{CVRPEnv,CVRPModel,CVRPTrainer,CVRPTester}.py
      └─ utils/{utils.py, log_image_style/*.json}
```

So **never copy `pomo/` into your deliverable.** Bundling is the export's job, and a
second copy would be extra generated code (the correspondence gate fails it).


Three settings the executor checks mechanically:

- the **training** entry point keeps `model_load` as `{'enable': False}` with
  `path`/`epoch` commented out (it must not load a checkpoint);
- the **test** entry point resolves weights from `checkpoints/` (an explicit
  `--model-path` / `MODEL_PATH` first, then the highest epoch among `checkpoint*.pt`)
  and never hardcodes a filename or `epoch`;
- `trainer_params` / `tester_params` keep POMO's own values (`epochs=8100`,
  `train_episodes=10*1000`, `train_batch_size=64`, `model_save_interval=500`,
  `img_save_interval=500`, `test_episodes=10*1000`, `test_batch_size=1000`,
  `aug_factor=8`). Only `problem_size` / `pomo_size` follow the task scale ({n}).

Solution dump expected from `--save-solution`:

```json
{"routes": [[0, 3, 7, 0], [0, 5, 0], ...], "cost": 12.34}
```

`routes` is a list of node-id lists, each starting and ending at the depot `0`;
`cost` is the total travel distance the solver believes it achieved. The pipeline
recomputes both independently, so a wrong `cost` is reported as a mismatch rather
than silently accepted.

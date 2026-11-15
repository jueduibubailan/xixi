# DESIGN — agent-driven VRP synthesis

## What the engine owns

- config loading and bundle discovery (`problems/<slug>/task.yaml` is the anchor);
- worktree seeding: shared `baseline/pomo/`, plus optional `instance_gen/` and
  `grader/` as read-only inputs;
- instruction rendering (`VRPAR.md`);
- artifact collection, the single mechanical check, score storage and export.

## What the agents own

- `analysis`: the plan, including the scale (`n`), file names, interface, masking.
- `code_generation`: the five adapted POMO modules, `train_n<n>.py`,
  `test_n<n>.py`, `README.md`.
- `code_error`: `test_train.py` (200×5000), the training run and checkpoint, and the
  `test_n` run with random test data.
- `code_adjustment`: the minimal repair on failure (and the applier of one nn-improve plan).
- `nn_improve`: neural-network method designs, one change per plan file, scored against
  the fixed baseline.

The pipeline does not parse the analysis plan and does not audit the code against it.
For nn improve it parses only each plan file's front matter (`id/kind/target/change/...`);
the design note itself is for the implementer and for humans.

## Success

The operator's definition:

1. `test_train.py` contains `epochs: 200` and `train_episodes: 5000`;
2. both entry scripts exit 0;
3. `test_n` stdout contains `AUGMENTATION SCORE: <float>`.

The last score is recorded. There is no feasibility gate, no `run.py` contract and
no plan review.

## Export

`workspace/final/<task.slug>/` contains the generated package and the full
`pomo/` tree it imports. `result/`, caches and samples are excluded.

## Why the old layers went away

The previous pipeline combined a neural-solver acceptance contract, an executor
with five gates, a grader and a feasibility stage. This version keeps only what
the operator asked for: agents generate the code and run the one quick check; the
pipeline checks the score line and exports the code.

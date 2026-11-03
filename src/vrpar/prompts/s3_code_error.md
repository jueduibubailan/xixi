# S3 — Code error / quick train and test (agent)

Read `artifacts/analysis/plan.md` and the generated package under
`artifacts/generated/`.

Your job: generate **one** file, `artifacts/generated/test_train.py`, run it, then
run the final test entry that code_generation wrote with the checkpoint it
produced.

Find the final test entry under `artifacts/generated/`: the `test_*.py` file that
is **not** `test_train.py`. Its name comes from the plan (the convention is
`test_<n>.py`, e.g. `test_50.py`); do not assume the literal name `test_n`.

## `test_train.py`

- Import the generated Trainer (for example
  `from <TAG>Trainer import <TrainerClass>`).
- Reuse the generated `train_n<n>.py` parameters, but override:

      'epochs': 1
      'train_episodes': 64
      'train_batch_size': 64

- Keep POMO's device chain; honour `VRPAR_FORCE_CPU`.
- Run the trainer. POMO saves `checkpoint-1.pt` under its result folder.
- Print `QUICK TRAIN OK` at the end.

Do not modify the other generated files. If `test_train.py` itself has a bug, fix
`test_train.py` only.

## Run and capture

Run from `artifacts/generated/`:

    python test_train.py > <run>/artifacts/runs/test_train.stdout.txt 2> <run>/artifacts/runs/test_train.stderr.txt
    echo $? > <run>/artifacts/runs/test_train.exitcode

Then locate `checkpoint-1.pt` (in POMO's result folder) and run the actual final
test entry, for example:

    python test_50.py --model-path <checkpoint-1.pt> > <run>/artifacts/runs/test.stdout.txt 2> <run>/artifacts/runs/test.stderr.txt
    echo $? > <run>/artifacts/runs/test.exitcode

Use the file you actually found; do not run a literal `test_n.py`.

The final test entry uses random test data (`test_data_load.enable = False`) and
`test_episodes = 10*1000`. Its stdout ends with:

    AUGMENTATION SCORE: <float>

Do not hand-write scores. The pipeline reads the stdout files.

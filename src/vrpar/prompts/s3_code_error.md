# S3 — Code error / quick train and test (agent)

Read `artifacts/analysis/plan.md` and the generated package under
`artifacts/generated/`.

Your job: generate **one** file, `artifacts/generated/test_train.py`, run it, then
run the final test entry that code_generation wrote with the checkpoint it
produced.

Find the final test entry under `artifacts/generated/`: the `test_*.py` file that
is **not** `test_train.py`. Its name comes from the plan (the convention is
`test_n<n>.py`, e.g. `test_n50.py`); do not assume the literal name `test_n`.

This turn's protocol is `{eval_protocol}`: `acceptance` uses
`{eval_epochs}` epochs × `{eval_train_episodes}` episodes. These two numbers come
from the pipeline and override any budget stated elsewhere (including `task.tips`).

## `test_train.py`

- Import the generated Trainer (for example
  `from <TAG>Trainer import <TrainerClass>`).
- Reuse the generated `train_n<n>.py` parameters, but override:

      'epochs': {eval_epochs}
      'train_episodes': {eval_train_episodes}
      'train_batch_size': 64

- Override only those keys; do not rebuild the parameter dictionary, so the
  training entry's seed and optimizer settings survive.
- Keep POMO's device chain; honour `VRPAR_FORCE_CPU`.
- Run the trainer. POMO saves `checkpoint-{eval_epochs}.pt` under its result folder.
- Print `QUICK TRAIN OK` at the end.

Do not modify the other generated files. If `test_train.py` itself has a bug, fix
`test_train.py` only.

## Run and capture

Run from `artifacts/generated/`, writing into the absolute report directory
`{report_dir}` (create it if missing):

    python test_train.py > {report_dir}/test_train.stdout.txt 2> {report_dir}/test_train.stderr.txt

Then write the exit code (bash / PowerShell both accepted):

    echo $? > {report_dir}/test_train.exitcode          # bash
    $LASTEXITCODE | Out-File -Encoding ascii {report_dir}/test_train.exitcode   # PowerShell

Read the file back and confirm it contains one integer.

Then locate `checkpoint-{eval_epochs}.pt` (in POMO's result folder) and run the actual
final test entry, for example:

    python test_n50.py --model-path <checkpoint-{eval_epochs}.pt> > {report_dir}/test.stdout.txt 2> {report_dir}/test.stderr.txt
    echo $? > {report_dir}/test.exitcode

Use the file you actually found; do not run a literal `test_n.py`.

Run the test entry with the test-data setup the generated code actually
implements; do not assume random test data, and do not assume a fixed set either:
follow `plan.md` (and `task.tips` when it specifies a test set). Its stdout ends with:

    AUGMENTATION SCORE: <float>

Do not hand-write scores. The pipeline reads the stdout files.

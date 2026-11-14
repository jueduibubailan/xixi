# S4 — Adjustment (agent)

Mode for this turn: `{adjustment_mode}` (`repair` or `plan`). Read only the section
that matches.

## `repair` mode

You are entered when `test_train.py` or `test_n<n>.py` failed.

Read:

- `{evidence_dir}/test_train.stdout.txt` / `.stderr.txt` / `.exitcode`
- `{evidence_dir}/test.stdout.txt` / `.stderr.txt` / `.exitcode`
- the generated code under `artifacts/generated/`

Fix the error with the smallest change. You may modify the generated modules,
`train_n<n>.py`, `test_n<n>.py` or `test_train.py`. Do not edit
`artifacts/analysis/plan.md`; the plan is not yours to change.

Do not run `test_train.py` or `test_n<n>.py` in this stage: the next `code_error`
does it and produces the score. Write `artifacts/revisions/round-{round_index}.md`
with the root cause and the change. Do not weaken the `{eval_epochs}`×`{eval_train_episodes}`
pins, the `test_episodes` value or the score line.

## `plan` mode

Apply exactly one parameter plan to the current generated package.

- plan id: `{plan_id}`
- plan:

```json
{plan_json}
```

Rules:

1. Change only what `changes` says. Do not "improve" unrelated code, do not rename
   files, do not touch `artifacts/analysis/plan.md`.
2. A model hyperparameter usually appears in both `train_n<n>.py` and
   `test_n<n>.py` (`model_params`). Change both to the same value, or the checkpoint
   and the tester will disagree on `load_state_dict`.
3. Do not change `problem_size`, `pomo_size`, `aug_factor`, file names, the score
   line, the training budget, or anything `task.tips` fixes (seed, batch sizes,
   test set, ...).
4. Do not run training or testing; `code_error` does that next.
5. Write `artifacts/revisions/plan-{plan_id}.md`: file, symbol, old value, new
   value, one line of why.

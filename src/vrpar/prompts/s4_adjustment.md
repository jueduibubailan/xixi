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

Apply exactly one neural-network method change to the current generated package.

- plan id: `{plan_id}`
- design note: `data/artifacts/nn_improve/plans/{plan_id}.md` — read it; it is the
  implementer's spec (what changes, why, what to mirror, the risks)
- plan:

```json
{plan_json}
```

Rules:

1. Implement only the one method change the design note describes, in the model code (and
   the input/mask code that serves it). Do not touch the tester, the trainer, the entry
   scripts, `test_train.py`, `model_params`, the instance generation or
   `artifacts/analysis/plan.md`.
2. A change that adds or reshapes weights must leave the checkpoint loadable/savable under
   the new structure. If old weights cannot load, say why that is fine: every plan
   retrains. Give any new hyper-parameter a default inside the model; never edit
   `model_params`.
3. Do not change `problem_size`, `pomo_size`, `aug_factor`, file names, the score
   line, the training budget, or anything `task.tips` fixes (seed, batch sizes,
   test set, ...). Do not add files or third-party dependencies.
4. Do not run training or testing; `code_error` does that next.
5. Write `artifacts/revisions/plan-{plan_id}.md`: which files/symbols changed, the new
   structure in one sentence, and how to roll back to the old one.

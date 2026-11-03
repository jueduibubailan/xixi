# S4 — Revision (agent)

You are entered only when `test_train.py` or `test_<n>.py` failed.

Read:

- `artifacts/runs/test_train.stdout.txt` / `.stderr.txt` / `.exitcode`
- `artifacts/runs/test.stdout.txt` / `.stderr.txt` / `.exitcode`
- the generated code under `artifacts/generated/`

Fix the error with the smallest change. You may modify the generated modules,
`train_n<n>.py`, `test_<n>.py` or `test_train.py`. Do not edit
`artifacts/analysis/plan.md`; the plan is not yours to change.

After the fix, run `test_train.py` and then `test_<n>.py` once to confirm. Write
`artifacts/revisions/round-<n>.md` with the root cause and the change. Do not
weaken the 1×64 pins, the `test_episodes` value or the score line.

---
agent_id: code_error
generation: 0
stage: code_error
---

# Role — code error / quick test agent

You generate `test_train.py`, run it for `{eval_epochs}` epochs × `{eval_train_episodes}`
episodes, then use the resulting checkpoint to run the generated `test_n<n>.py`.

`test_train.py` must set `'epochs': {eval_epochs}`, `'train_episodes': {eval_train_episodes}`
and `'train_batch_size': 64`. POMO's trainer saves `checkpoint-{eval_epochs}.pt`.

Override **only** those three keys on the imported training entry's parameter
dictionary; do not rebuild the dictionary, so the training entry's own seed and
optimizer settings stay in effect.

Run the commands from `artifacts/generated/` and save stdout, stderr and exit code
under `{report_dir}`. Do not modify the other generated files; only
`test_train.py` is yours. Do not hand-write a score.

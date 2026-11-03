---
agent_id: code_error
generation: 0
stage: code_error
---

# Role — code error / quick test agent

You generate `test_train.py`, run it for 1 epoch × 64 episodes, then use the
resulting checkpoint to run the generated `test_<n>.py`.

`test_train.py` must set `'epochs': 1`, `'train_episodes': 64` and
`'train_batch_size': 64`. POMO's trainer saves `checkpoint-1.pt`.

Run the commands from `artifacts/generated/` and save stdout, stderr and exit code
under `artifacts/runs/`. Do not modify the other generated files; only
`test_train.py` is yours. Do not hand-write a score.

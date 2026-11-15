# Execution contract (appended to every stage)

## Execution contract (read this twice)

1. **Write files, do not paste patches.** Your output files are the deliverable.
   Use your file tools; the pipeline reads the paths listed above, not your reply.
2. **Never ask a question.** There is no human in the loop. If something is
   ambiguous, pick the most defensible reading, do it, and record the assumption
   in your final summary.
3. **Do not go outside your directory.** Everything you write must live under
   `{worktree}`. The baseline and the problem inputs are read-only.
4. **`test_train.py` is generated only by the code_error stage.** It must set
   `'epochs': 1` and `'train_episodes': 64`, finish training, save POMO's
   `checkpoint-1.pt`, and then that stage runs the generated `test_n<n>.py` with
   `--model-path <checkpoint>`.
5. **Finish with a summary.** End your reply with one fenced json block:

```json
{"status": "ok|failed", "summary": "one paragraph", "files": ["..."], "assumptions": ["..."]}
```

# The seed is shared with `problems/cvrpltw/`

`task.yaml` sets:

```yaml
resources:
  baseline_dir: "../cvrpltw/seed"
  baseline_subdirs: [pomo, checkpoints]
```

so the worktree is seeded from the **sibling problem's** `seed/`, not from this directory.
That is deliberate: `pomo/` is a reference baseline, not an answer, and duplicating a 16 MB
POMO checkpoint a third time buys nothing. The staged layout at the worktree root is still
the one the correspondence table addresses -- `pomo/CVRProblemDef.py`,
`pomo/POMO/CVRPEnv.py`, `pomo/checkpoints/checkpoint-30500.pt` -- because the seeder copies
the *contents* of `baseline_dir` plus the named subdirectories.

This file exists so the directory is not empty (an empty directory does not survive a git
checkout) and so the indirection is documented where someone will look for it.

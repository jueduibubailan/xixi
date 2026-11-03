# The seed is shared with `problems/cvrpltw/`

`task.yaml` sets:

```yaml
resources:
  baseline_dir: "baseline"
  baseline_subdirs: [pomo]
```

so the worktree is seeded from the project's shared `baseline/`, not from this
directory. That is deliberate: `pomo/` is a reference baseline, not an answer, and no
weight file is shipped at all -- every run trains its own.

This file exists so the directory is not empty (an empty directory does not survive a
git checkout) and so the indirection is documented where someone will look for it.


This file exists so the directory is not empty (an empty directory does not survive a git
checkout) and so the indirection is documented where someone will look for it.

# Baseline note

The baseline is the shared POMO tree at `baseline/`; this bundle carries only its data
(`grader/`) and its description. Every run seeds a worktree from the shared tree and
trains its own weights — no checkpoint is shipped.

This file exists so the directory is not empty (an empty directory does not survive a
git checkout) and so the indirection is documented where someone will look for it.

# Stage context (appended to every stage)

- Working directory: `{worktree}` (your only writable area).
- Run directory: `{run_dir}` (read-only record of previous stages).
- Declared outputs for this stage:
{artifact_paths}

Environment:
- Python interpreter: {python}.
- Do not install packages; the needed dependencies should already be present.

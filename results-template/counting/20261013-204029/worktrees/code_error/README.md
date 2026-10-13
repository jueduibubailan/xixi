# The seed: the starting code an agent edits

This directory is **optional**. A problem that is solved from scratch does not need one, and
the template ships none -- which is the case worth understanding, because it is the one most
people start from.

## What the engine does with it

`problem.resources.baseline_dir` names the directory whose *contents* are staged into every
stage worktree. Here that is `seed`, so the worktree root ends up holding whatever is in
here. `generated.correspondence` addresses the baseline from that same root:

```yaml
resources:
  baseline_dir: "seed"
generated:
  correspondence:
    COUNTEREnv.py: "pomo/POMO/CVRPEnv.py"     # <- a path relative to the worktree root
```

## When to add one

Add a seed when the answer is "adapt this existing code", which is the usual case for a
research baseline. Then:

1. put the reference implementation here (`seed/<something>/`);
2. list it in `resources.baseline_subdirs` so the worktree seeder copies it;
3. fill `generated.correspondence` so the "one-to-one with the seed" gate can check that
   every generated file was adapted from a real baseline file and none was invented;
4. optionally drop weights or other drop-in payloads in a second subdirectory.

With no seed, leave `correspondence` empty. The gate then has nothing to compare and passes
rather than failing every deliverable -- see `cvrp/executor/correspondence.py`.

## Why this file is here

An empty directory cannot survive a git checkout, and the one thing a template must not do
is look broken right after it is copied. Deleting this file is safe once you add real seed
content.

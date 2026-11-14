# Adding a problem

A problem is a directory under `problems/`:

```
problems/<slug>/
├─ task.yaml
├─ litellm_config.yaml
├─ instance_gen/        # optional
└─ grader/              # optional
```

## task.yaml

Only the statement:

```yaml
task:
  name: ...
  slug: ...
  description: |
    ...
  tips: |
    ...
```

There is no `problem.dir`. The bundle is found by the task file's directory.

## litellm_config.yaml

```yaml
stages: default

score:
  direction: minimize

loop:
  max_rounds: 5

llm:
  backend: claude_code
  model: deepseek-flash

workspace:
  results_dir: ./results
  final_dir: ./workspace/final
```

Optional inputs:

- `instance_gen/` — generator code (`*.py`) and/or a written description (`*.md`);
- `grader/` — checker / generator material, read-only.

`context.md` is not used.

## The three input modes

The analysis agent writes the mode into `plan.md`:

1. **description only** — design generation from `task.description`;
2. **generator code** — import/use it verbatim;
3. **written generation description** — map it to code.

## Run

```bash
uv run vrpar validate problems/<slug>/task.yaml
uv run vrpar start problems/<slug>/task.yaml llm.backend=mock
uv run vrpar start problems/<slug>/task.yaml
```

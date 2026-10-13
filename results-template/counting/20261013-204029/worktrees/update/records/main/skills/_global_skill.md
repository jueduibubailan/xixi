# Global skill (offline stub)

> Scope: **general**. Written by `llm.backend: mock`; it contains no measured
> findings, only the pipeline's own invariants.

## Contents

- `Verification playbook` — the gate order *(general)*

## Verification playbook

- Five gates run in order: `py_compile`, `import_probe`, `correspondence`,
  `smoke_run`, `full_run`.
- Reproduce one stage with `cvrp stage <name>` rather than re-running the run.

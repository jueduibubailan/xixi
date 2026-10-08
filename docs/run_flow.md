# Run flow

Two entry points, one pipeline:

```bash
uv run cvrp start task.yaml      # the pipeline
uv run cvrp ui                   # watch it at http://127.0.0.1:8420
```

`start` accepts dotlist overrides, CORAL-style:

```bash
uv run cvrp start task.yaml llm.model=sonnet loop.max_rounds=8 run.verbose=true
uv run cvrp start task.yaml llm.backend=mock          # no agent session needed
uv run cvrp start task.yaml problem.slug=cvrptw-dr    # a different variant
```

---

## 0. Preparation

```
load_config(task.yaml over configs/default.yaml)      src/cvrp/config.py
├─ dotlist overrides, then validation
└─ attach_problem(): load problems/<slug>/             src/cvrp/problems/__init__.py
   ├─ problem.yaml -> prompts, generated layout, CLI, data, resources, scorer
   ├─ checks.py    -> verify / build_dataset / score / extract_best_cost / revision_hints
   └─ hooks are exposed as cvrp.problems.ProblemPlugin

Orchestrator.__init__                                 src/cvrp/pipeline/orchestrator.py
├─ make results/<slug>/<timestamp>/                   src/cvrp/run/run_dir.py
├─ write config.resolved.json                         (the exact config this run used)
└─ build the LLM backend                              src/cvrp/llm/claude_code.py (or mock)

ensure_instances()
└─ problem.build_dataset(<problem data dir>)          problems/<slug>/instances.py
   skipped when the problem's ready_marker is already present
```

## 1. analysis (agent)

```
seed worktree:  results/<ts>/worktrees/analysis/
                resources.baseline_dir contents + data/instances/
instruction:    CVPR.md  <- role + problems/<slug>/prompts/s1_analysis.md + context + contract
agent turn:     claude -p <CVPR.md> --model deepseek-flash --permission-mode auto
                --output-format stream-json --verbose    (log: logs/analysis.round0.log)
agent writes:   artifacts/analysis/analysis.json
                artifacts/analysis/plan.md
collect:        -> results/<ts>/artifacts/analysis/*
gate:           src/cvrp/pipeline/gates.py::gate_analysis
                every constraint has a mask, the checklist is checkable, the plan names real symbols
fail ->         run ends as FAILED (nothing downstream recovers from a vague analysis)
```

## 2. code_generation (agent)

```
seed worktree:  worktrees/code_generation/ + data/artifacts/analysis/analysis.json
instruction:    role + problems/<slug>/prompts/s2_codegen.md
                (the file list, the CPU/checkpoint rules and the CLI contract are
                 quoted from problem.yaml, not hardcoded)
agent writes:   <generated.dir>/  (per problem.yaml:generated.files)
collect:        -> results/<ts>/<generated.dir>/
gate:           gate_generated(problem=...)
                generated.program is required; generated.files are warnings
```

## 3. code_error (executor — no LLM)

```
seed worktree:  worktrees/code_error/ + the generated package + data/instances/
gate 1:         py_compile every .py under the generated dir
gate 2:         <program> <cli.path_arg> <instance> <cli.iteration_arg> 5        (smoke)
gate 3:         <program> ... <cli.iteration_arg> <iterations>                  (full)
contract:       exit 0  AND  stdout matches problem.yaml:program.initial_regex
                AND stdout matches problem.yaml:program.best_regex
output:         artifacts/runs/s3_run_report.json   (+ s3_*.stdout.txt)
                {passed, failed_step, error_class, error_message, revision_hint}
```

`error_class` ∈ {timeout, contract_missing, initial_invalid, torch_load_error,
shape_error, import_error, syntax_error, index_error, memory_error, ...}.
`revision_hint` is the class-specific instruction the revise prompt reuses; the
problem can override it per class via `checks.py:revision_hints`.

## 4. code_revise (agent) — the loop

```
trigger:        only when gate 3 failed and rounds remain (loop.max_rounds, default 5)
seed worktree:  worktrees/code_revise/ + data/artifacts/{analysis,generated,runs,revisions}
brief file:     artifacts/revisions/round-N-brief.json
turn 1:         problems/<slug>/prompts/s4_error_analyse.md
                -> one line: category :: file:function :: cause :: fix
turn 2:         problems/<slug>/prompts/s4_revise.md
                -> edit the generated package minimally, then run the smoke command
agent writes:   <generated.dir>/*  (patched)
                artifacts/revisions/round-N.md
        --->    back to stage 3
```

## 5. grader (executor)

```
runs the generated program once more, with the problem's save-solution flag
        -> artifacts/runs/best_solution.json
then hands that file to  problem.verify(instance, solution)   problems/<slug>/checks.py
output: artifacts/runs/grader_result.json
        {grader, problem, direction, score, feasible, best_cost, details}
score:  problem.yaml:scorer  (inverse_cost | identity | binary) or checks.py:score
        0.0 on any contract failure, or on an infeasible/unverified solution
```

## 6. feasibility (executor stage)

```
input:  the grader's own verification (details.feasibility)
output: artifacts/runs/s5_feasibility.json  {feasible, skipped, violations, checks, cost}
```

It is deliberately *not* a re-check: one verification, one source of truth. The
stage exists so the verification is a first-class, greppable artifact in the run
directory and shows up on the dashboard as a stage rather than a footnote.

## 7. export

```
success = contract passed AND feasibility feasible AND grader feasible
          (a skipped verification fails the run unless
           grader.args.require_solution_dump is false)
export :  results/<ts>/<generated.dir>/ -> workspace/final/<slug>/
report :  results/<ts>/REPORT.md
          (problem, stages, analysis summary, executor evidence, feasibility, paths)
```

---

## The dashboard

`uv run cvrp ui` serves a single-page app over `results/`:

| endpoint | contents |
|---|---|
| `GET /` | the page |
| `GET /api/health` | task, slug, model, backend, CORAL status, stage names |
| `GET /api/status` | `state.json` (status, round, costs, stage records, history) |
| `GET /api/stages` | the stage table with artifact presence |
| `GET /api/runs` | every run under `results/` |
| `GET /api/artifacts?dir=artifacts` | file listing |
| `GET /api/file?path=...` | file contents (path-traversal refused) |
| `GET /api/logs`, `GET /api/log?name=&tail=` | log list and tails |
| `GET /api/grader` | grader bundle + S3 report + S5 result |
| `POST /api/steer` | queue an operator instruction for a stage |
| `GET /api/events` | SSE, pushes on every `state.json` change |

**Steering**: an instruction queued from the dashboard is stored in
`state.json:instruction_queue` and consumed by the next matching stage, where it
appears as an "Operator instructions" section in `CVPR.md`. Targets: `analysis`,
`code_generation`, `code_revise`, or any stage.

---

## Failure modes and what you will see

| symptom | where to look | meaning |
|---|---|---|
| `config error: problem.slug is required` | `task.yaml` | the task does not name a variant |
| `no problem directory for slug …` | the error's "looked in" list | `problem.slug` / `problem.dir` is wrong |
| `analysis gate failed` | `logs/analysis.round0.log`, `artifacts/analysis/analysis.json` | the model did not produce usable masks/plan |
| `code generation gate failed` | `logs/code_generation.round0.log` | `generated.program` was not written |
| `code_error: FAILED at py_compile` | `artifacts/runs/s3_run_report.json` | syntax error; class `syntax_error` |
| `code_error: FAILED at smoke_run` / `full_run` | same + `s3_full_run.stdout.txt` | runtime crash; class in the report |
| `contract_missing` | `s3_full_run.stdout.txt` | printed the wrong text for `program.expected_lines` |
| `revision budget exhausted` | `artifacts/revisions/round-*.md` | 5 rounds did not converge; evidence is kept |
| `contract passed, but ... could not be verified` | `logs/pipeline.log` | the run is deliberately failed, not trusted |
| `feasibility: FAILED` | `artifacts/runs/s5_feasibility.json` | either a real violation, or `verify()` returned nothing |

## Inspecting a run from the shell

```bash
uv run cvrp problems                     # which variants this project can build
uv run cvrp status                       # stage table for the latest run
uv run cvrp log -n 200                   # tail logs/pipeline.log
uv run cvrp log --name code_error.round1.log
uv run cvrp show artifacts/analysis/analysis.json
uv run cvrp show artifacts/runs/s3_run_report.json
uv run cvrp grader --json                # re-run the exit gate
```

## Adding a variant

See `docs/new_problem.md`. Short version:

```bash
uv run cvrp new-problem <slug> --name "<Variant name>" --from "<description>"
# edit problems/<slug>/{problem.yaml,checks.py,instances.py,prompts/}
# set problem.slug in task.yaml
uv run cvrp validate && uv run cvrp instances && uv run cvrp start task.yaml
```

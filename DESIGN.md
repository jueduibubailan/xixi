# DESIGN — how AFL, CORAL and POMO became a variant-agnostic engine

This document is the "why" behind the layout. If you only read one thing, read §1
(the concept mapping) and §4 (the decisions that are load-bearing).

---

## 1. Concept mapping

| Concern | In CORAL | In AFL | In `cvrp` |
|---|---|---|---|
| Work unit | an agent that owns a lane and iterates forever | a pipeline pass over one `.vrp` file | a **stage** with a declared artifact contract |
| What is being solved | a task (`task.yaml`) | one VRP variant, hardcoded in the prompts | a **problem plugin** (`problems/<slug>/`) |
| Driving the model | `coral.agent.builtin.claude_code` spawns `claude -p` | direct OpenAI SDK calls | `cvrp.llm.claude_code.ClaudeCodeBackend`, delegating to CORAL's runtime when importable |
| Instruction to the agent | generated `CORAL.md` + a per-agent role file | one long f-string per function | generated `CVPR.md` + a per-stage role file + the problem's prompt |
| Shared state | `.coral/public/` (attempts, notes, skills, roles) | `vrp_meta.json` | `results/<task>/<ts>/{state.json, artifacts/, logs/, worktrees/}` |
| Feedback loop | grader daemon scores every commit; heartbeat prompts react | `run_generated_code` → `revise_code` | executor gates → revision stage → re-run, bounded by `loop.max_rounds` |
| Objective function | `grader` (any score) | "did it run and print a cost" | contract + the problem's `verify()` + `problem.yaml:scorer` |
| Self-check | heartbeat `reflect` / `consolidate` | `describe_judgement` / `code_judgement` | `gate_*` predicates per stage, plus judge prompts in the problem |
| UI | Starlette dashboard over `.coral/` | none | Starlette dashboard over `results/` (own API, own static assets) |

### What was deliberately *not* carried over

* **The grader daemon.** CORAL's daemon decouples long grades from agent turns
  across many parallel agents. This pipeline is sequential, so grading is a
  function call (`cvrp.executor.grader.grade_run`).
* **Heartbeats and islands.** Both are search-diversification mechanisms for a
  competitive pool. The contract here is a *sequence*, and the only diversity knob
  that matters is "did the code run".
* **`git` branches per agent.** Each stage gets a directory copy instead: the
  artifact contract is explicit, so there is no need to diff a tree to find out
  what a stage produced.

---

## 2. Why the stages are shaped this way

### S1 analysis — machine-readable, not prose

AFL asks the model for a description and a constraint list in brackets, and the
next stage reads that prose. That works while the next stage is another LLM call
with the same context, and breaks the moment two stages disagree about what a
"constraint" is.

Here S1 emits JSON against a fixed shape, and a gate rejects it unless every
constraint has a matching masking rule, the exception that prevents deadlock is
stated, the feasibility checklist is checkable from the instance alone, and every
plan entry names a symbol that exists in the staged baseline.

The prompt asks for `masking_rules` rather than "constraints" on purpose: the mask
is the only place a constraint becomes operational in a rollout.

### S2 code generation — a package, not a function

AFL generates eight functions in one file. A neural baseline is several modules
with a shared tensor contract, so the problem declares the file list and the
prompt quotes it. The gate treats the declared `files` as *expected* and
`program` as *required*: the contract needs an entry point, not a complete
package, and turning missing files into hard failures would make round 1 fail for
cosmetic reasons.

### S3 executor — the only stage without a model

The most important structural choice. In AFL, "run the code" happens inline in
`main.py` and the error string is passed straight to the revise prompt. Here it is
a first-class stage that runs three gates in increasing cost (compile → short run →
full run), classifies the failure into a stable `error_class`, keeps stdout/stderr
tails and the exact command, adds a class-specific `revision_hint`, and **never
edits code**. The problem can override the hints for its own error classes.

Keeping it model-free means the pipeline can distinguish "the agent is confused"
from "the code is broken", and it makes the failure taxonomy greppable across runs.

### S4 revise — attribute, then repair

AFL's `revise_code` does exactly this (one attribution call, one repair call) and
it is a genuinely good idea: the repair call gets a short, focused instruction
instead of a traceback and a vague hope. `cvrp` keeps the two-step structure and
adds "reproduce before you finish", because a revision that ends without executing
the code has a high chance of producing a second failure.

### Grader + feasibility — the adversary

Nothing in AFL checks that a solution is feasible; it checks that the program
printed a number. A neural solver can easily print a beautifully small cost for an
infeasible solution (masks weakened to stop a crash). So the grader re-runs the
program, then hands the dumped solution to the **problem's own** `verify()`, which
is written independently of the generated code. `require_solution_dump: true` is
the default: a run that cannot be verified is reported as failed, not as passing.

The split matters: `cvrp.executor.grader` owns *how a run is scored*, the problem
owns *what a valid answer means*.

---

## 3. Data flow

```
problems/<slug>/{problem.yaml, prompts/, checks.py, instances.py}
        |                                   |
        |  (spec: prompts, generated layout, CLI, scorer)
        |                                   |
        v                                   v
   CVPR.md (role + prompt + context)   instances/data/<slug>/*
        |                                   |
        v                                   v
   worktrees/<stage>/  --agent edits-->  artifacts/generated/*
        |                                   |
        +--- S3 executor (contract + CLI) --+
        |                                   |
        +--- S4 revise <-- error report ----+
        |                                   |
        v                                   v
   grader → --save-solution → problem.verify() → s5_feasibility.json
        |
        v
   workspace/final/<slug>/ + REPORT.md
```

Directory roles inside a stage worktree:

| path | writable by the agent | contents |
|---|---|---|
| baseline dirs (`pomo/`, `checkpoints/`) | no | copied from `resources.baseline_dir` |
| `data/instances/` | no | the problem's generated data |
| `data/artifacts/` | no | earlier stages' outputs (the inputs it may read) |
| `artifacts/` | **yes** | the agent's declared outputs |
| `CVPR.md`, `.cvrp/stage.json` | no | generated instruction + resolved stage config |

`cvrp.run.worktree.collect_artifacts` copies `artifacts/` out into the run
directory and leaves it there. `cvrp.executor.contract.stage_search_paths` defines
the read order everywhere else: `worktree/data` → `worktree` → run dir.

---

## 4. Decisions worth knowing about

1. **All variant knowledge lives in a problem directory.** `problem.yaml` supplies
   prompts, generated layout, CLI flags, data parameters, resources and scoring;
   `checks.py` supplies the hooks; `instances.py` supplies the data. The engine
   reads them through `cvrp.problems.ProblemPlugin`. This is what makes "add a new
   example" a scaffolding + editing task instead of a refactor.
2. **The acceptance contract is textual.** `the process is successful, the best
   cost is <float>` is inherited from AFL. It is ugly but it survives arbitrary
   solver internals, which a structured protocol would not. It is *configurable
   per problem* (`program.best_regex`), so a stricter format can be adopted.
3. **The prompt vocabulary is substituted, and unknown placeholders become empty.**
   A prompt that still reads `{failed_step}` would be handed to an agent that
   cannot resolve it, and the mistake would only surface as a confusing run much
   later. Leaving a visible trace was rejected in favour of failing quietly-fast;
   the trade-off is that a typo in a placeholder is silent, which is why the
   scaffold prints the vocabulary and `cvrp validate` checks that every declared
   prompt exists.
4. **Executors write to the run directory; agents write to their worktree.** The
   first draft collected executor output from the worktree too, which silently
   deleted the run report (collecting replaces its target). The rule is now
   explicit in `stage_code_error` / `stage_feasibility`.
5. **Instances are feasible by construction, and that is a feature.** Sampling
   windows independently of reachability produced instances no solver could
   satisfy, which makes a pipeline report a *solver* failure for a *data* problem.
   The `cvrptw` generator builds a canonical service order first, derives windows
   from its arrival times, self-checks, and refuses to emit an infeasible instance.
6. **The horizon is derived, not fixed.** `data.horizon: null` means "size it from
   the canonical schedule". A fixed 3.0 is fine for n=20 on a unit square and wrong
   for anything larger; a too-small value raises instead of producing bad data.
7. **An empty hook result is not a pass.** A problem's `verify()` returning `{}` is
   normalised to `feasible: False` with an explicit violation. The engine cannot
   verify feasibility for a variant, so it must never be talked into assuming it.
8. **The grader is a function, but it is still called a grader.** Keeping the name
   and the `ScoreBundle` shape means `task.yaml:grader.entrypoint` reads like
   CORAL's and the same object could be dropped into CORAL's entrypoint resolver.
9. **CORAL is optional and detected, not depended on.** `importlib.find_spec` plus
   a graceful fallback. A hard dependency on a checkout with no wheel on the index
   would make `uv sync` fail for a benefit the flag list already makes equivalent.
10. **The mock backend emits real artifacts.** A stub that prints fake costs would
    make the contract and the feasibility checker meaningless offline, so the mock
    reads `mock.template` and `mock.analysis` from the problem. That is also why
    `cvrp start … llm.backend=mock` is the recommended first run for a new variant:
    it exercises the engine and the problem's own checks before any model spend.
11. **No `omegaconf`.** CORAL uses it for config merging; dotlist overrides are ~30
    lines over PyYAML (`cvrp.config.apply_overrides`). Dropping it removed a
    package with a numpy build dependency from the install path.

---

## 5. Where the seams are

| if you want | change | it will not break |
|---|---|---|
| a different VRP variant | `uv run cvrp new-problem …`, then `problems/<slug>/` | the engine |
| a different baseline model | `resources.baseline_dir` + the S2 prompt | the stage machinery |
| a parallel revision search | run N `code_revise` worktrees and pick the best by grader score | the artifact contract |
| a stronger acceptance test | `program.best_regex`, `checks.py:verify` | the stages |
| a different data source | `problem.yaml:data.generator` or `checks.py:build_dataset` | the executor |
| CORAL-native scoring | `grader.entrypoint: cvrp.executor.grader:CodeGrader` from a CORAL `task.yaml` | nothing; it is the same class |

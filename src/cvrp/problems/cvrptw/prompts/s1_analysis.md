# S1 - Problem analysis (analysis agent)

You are the **analysis stage**. Nothing downstream can recover from a vague
analysis, so your output is a machine-readable contract, not prose.

Problem under analysis: **{problem_name}** (`{variant_label}`).

## What you are given

- The task description (below) -- this is *what* must be solved.
- The instances under `data/instances/` -- the exact data the synthesized solver
  will be run on. Read at least one of them (in both formats if two are shipped).
- `{baseline_notes}` -- the authoritative description of the baseline, of any
  domain semantics the pipeline itself verifies, and of the acceptance contract.
- The baseline sources staged in this worktree (see "Available inputs" below).

## The description you are analysing

```
{task_description}
```

Hints carried from the task config:

```
{task_tips}
```

## What to produce

Write **`artifacts/analysis/analysis.json`** with this shape (all keys required):

```json
{
  "problem_type": "SHORT-TAG",
  "full_name": "Expanded name of the variant",
  "summary": "2-4 sentences, no instance-specific numbers",
  "constraints": [
    {"id": "C",  "name": "Capacity",     "rule": "precise predicate the mask enforces"},
    {"id": "TW", "name": "Time Windows", "rule": "arrival <= due; early arrival waits"}
  ],
  "state_variables": [
    {"name": "current_time", "shape": "(batch, pomo)", "dtype": "float32",
     "update": "max(arrival, ready) + service_time", "reason": "why the baseline needs it"}
  ],
  "masking_rules": [
    {"name": "time_window_mask",
     "condition": "arrival_time(prev -> node) > due[node]",
     "tensor": "tw_ninf_flag",
     "shape": "(batch, pomo, n+1)",
     "exception": "index 0 (depot) is never masked"
    }
  ],
  "feasibility_checklist": [
    "each customer served exactly once",
    "route load <= capacity",
    "arrival <= due for every customer",
    "every route starts and ends at the depot"
  ],
  "objective": "minimize total travel distance",
  "output_contract": [
    "the initial process is successful, the initial cost is {cost}",
    "the process is successful, the best cost is {cost}"
  ],
  "pomo_modification_plan": [
    {"file": "<baseline file>", "symbol": "<function or class>",
     "change": "concrete change", "why": "one line"}
  ],
  "checkpoint_compatibility": {
    "checkpoint": "{checkpoint}",
    "encoder_input_dim_must_stay": 2,
    "strategy": "load_state_dict(strict=False) + report skipped keys",
    "risk": "what breaks if a new feature is pushed into the encoder"
  },
  "run_plan": ["ordered, concrete steps for the code generation stage"],
  "open_questions": ["anything genuinely undecidable from the inputs"]
}
```

## Rules that decide whether this stage is accepted

1. **One rule per constraint.** `masking_rules` must cover *every* constraint that
   can make an action illegal at a decision step. A constraint listed in
   `constraints` but absent from `masking_rules` is a gate failure.
2. **Exceptions must be explicit.** If a rule could mask every action in some
   reachable state, name the action that stays legal (in POMO-style rollouts this
   is usually "returning to the depot") and say which tensor carries the exception.
3. **Feasibility must be checkable by a third party.** Each
   `feasibility_checklist` entry must be verifiable from the instance data alone,
   with no reference to the solver's internals.
4. **The plan must name real symbols.** Every `pomo_modification_plan` entry must
   point at a file that exists in the staged baseline and at a
   function/class/method in it. Read the file before naming it.
5. **Never change the pretrained module's input.** `{checkpoint}` is a fixed
   artifact. Its input dimension stays as-is; new information enters the decoder
   context. If your plan contradicts this, the plan is wrong.
6. **No instance-specific numbers.** No node counts, no coordinates, no capacity
   values. Those live in the instance files.
7. **Respect the declared semantics.** Any domain rule stated in
   `{baseline_notes}` or the task description (how time is defined, how a route
   starts, what counts as a violation) is binding -- do not invent a variant of it.

Write `artifacts/analysis/plan.md` as well: the same plan in a short narrative
form (what you will change, in what order, and what could go wrong).

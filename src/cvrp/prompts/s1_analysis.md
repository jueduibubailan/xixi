# S1 - Problem analysis (analysis agent)

You are the **analysis stage**. Nothing downstream can recover from a vague
analysis, so your output is a machine-readable contract, not prose.

Problem under analysis: **{problem_name}** (`{variant_label}`).

## This task's scale

The task fixes the instance size. Everything you plan must be expressed for it,
and the generated training/testing entry points must be named and sized
accordingly:

{scale_lines}

## What you are given

- The task description (below) -- this is *what* must be solved.
- The instances under `data/instances/` -- the exact data the synthesized solver
  will be run on. Read at least one of them (in both formats if two are shipped).
  Confirm its `DIMENSION` equals {dimension} (depot + {n} customers) before you
  write the plan.
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

Write **`artifacts/analysis/analysis.json`** with this shape (all keys required). The
example values below are **placeholders that show the shape** — do not copy them, and do not
infer the problem from them:

```json
{
  "problem_type": "SHORT-TAG",
  "full_name": "Expanded name of the variant",
  "summary": "2-4 sentences, no instance-specific numbers",
  "constraints": [
    {"id": "<ID>", "name": "<constraint name>", "rule": "<the precise predicate a mask must enforce>"}
  ],
  "state_variables": [
    {"name": "<state variable>", "shape": "(batch, pomo)", "dtype": "float32",
     "update": "<how it changes in one step>", "reason": "why the baseline needs it"}
  ],
  "masking_rules": [
    {"name": "<mask rule>",
     "condition": "<the predicate that makes an action illegal>",
     "tensor": "<which tensor carries it>",
     "shape": "(batch, pomo, n+1)",
     "exception": "<the action that stays legal no matter what>"}
  ],
  "feasibility_checklist": [
    "<a property an independent checker can verify from the instance and the solution>"
  ],
  "objective": "<what is minimized or maximized, and in what units>",
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
  "problem_size": {n},
  "pomo_size": {pomo},
  "open_questions": ["anything genuinely undecidable from the inputs"]
}
```

**Where the real content comes from.** The task description above, and the `# Related
records` block the retrieval stage selected. Those are the authority on this problem's
semantics — its constraints, its state, its masks, its feasibility rules. This template
defines only the *shape* of your answer.

- Do **not** copy any example value from the template into your answer. If you find
  yourself writing a constraint because the template showed one, stop.
- If neither the description nor the records decides something you need, put it in
  `open_questions` rather than guessing. A named unknown is useful; an invented rule is not.
- The `output_contract` strings above are the one exception: they are the pipeline's own
  acceptance surface and are quoted exactly as they must appear.

`problem_size` is the number of **customers** ({n} here), not the instance
`DIMENSION` ({dimension}); the generated package is built for it. Anything you
plan that assumes a different size is a gate failure.

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
4. **The plan must name real symbols, in the existing files.** Every
   `pomo_modification_plan` entry must point at one of the five baseline files the
   variant may touch (`pomo/CVRProblemDef.py`, `pomo/POMO/CVRPEnv.py`,
   `pomo/POMO/CVRPModel.py`, `pomo/POMO/CVRPTrainer.py`, `pomo/POMO/CVRPTester.py`)
   and at a function/class/method that exists in it. Read the file before naming it.
   The file set and the file names are fixed: never plan to rename a baseline file,
   to add a new module, or to reimplement a component the baseline already has.
5. **Never change the pretrained module's input.** `{checkpoint}` is a fixed
   artifact. Its input dimension stays as-is; new information enters the decoder
   context. If your plan contradicts this, the plan is wrong.
6. **No instance-specific numbers.** No node counts, no coordinates, no capacity
   values in the *rules* you write -- those live in the instance files. The one
   number that is allowed, and required, is this task's scale: `problem_size` = {n}
   with `pomo_size` = {pomo}, because the generated entry points are built for it.
7. **Respect the declared semantics.** Any domain rule stated in
   `{baseline_notes}` or the task description (how time is defined, how a route
   starts, what counts as a violation) is binding -- do not invent a variant of it.
8. **Stay inside POMO.** The variant is an adaptation of the staged baseline, not a
   new solver. Do not plan a different encoder/decoder family, a new training
   regime, or a new module layout; the environment and the checkpoint are already in
   place and the deliverable must keep the baseline's shape.

Write `artifacts/analysis/plan.md` as well: the same plan in a short narrative
form (what you will change, in what order, and what could go wrong).

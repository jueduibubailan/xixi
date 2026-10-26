# S1 - Problem analysis (analysis agent)

You are the **analysis stage**. Nothing downstream can recover from a vague analysis, so your
output is a machine-readable contract, not prose. Two files come out of this stage and both
are used literally:

- `artifacts/analysis/analysis.json` — the contract the generation stage builds from;
- `artifacts/analysis/plan.md` — the same plan as a **detailed** narrative (specified
  below): the next agent reads only this file and works from it.

There is no knowledge base and no retrieval stage in this pipeline: **the problem description,
the instances and the baseline are the only inputs.** Nothing else will arrive later.

Problem under analysis: **{problem_name}** (`{variant_label}`).

## The baseline is POMO

Every problem this pipeline solves is solved by **adapting POMO's CVRP solver**.

In the *project* it lives in the shared baseline directory `baseline/` (`baseline/pomo/` and
`baseline/checkpoints/`), one copy for every problem. The pipeline stages it into **this
worktree**, where you therefore see `pomo/` and `checkpoints/` (the entries listed as
`{resource_subdirs}`), with the pretrained weights at `{checkpoint}`. **Do not look for a
`baseline/` directory in the worktree — it does not exist there; what you see is its contents
flattened to the root.**

Your plan is a plan of changes to that code — not a design for a new solver. These invariants
are fixed and bind your plan:

1. **The five baseline files keep their names**, and nothing new is added:
   `pomo/CVRProblemDef.py`, `pomo/POMO/CVRPEnv.py`, `pomo/POMO/CVRPModel.py`,
   `pomo/POMO/CVRPTrainer.py`, `pomo/POMO/CVRPTester.py`. Do not plan to rename a file, add a
   module, or reimplement a component the baseline already has.
2. **The pretrained module's input stays at two coordinates.** `{checkpoint}` is fixed.
   Anything new — a clock, a running load, a route length — enters the **decoder context**,
   never the encoder.
3. **The checkpoint must still load**: `load_state_dict(..., strict=False)`, with the skipped
   keys named.
4. **Style, banners and the `Parameters` dict follow the baseline.**
5. **A "variant" is a change of constraint or objective on top of POMO**, never a different
   encoder/decoder family, training regime or module layout.

If your plan contradicts any of these, the plan is wrong — not the invariant.

## This task's scale

The task fixes the instance size. Everything you plan must be expressed for it, and the
generated training/testing entry points must be named and sized accordingly:

{scale_lines}

## Run environment: GPU first, CPU as the fallback

**Run exactly the way POMO does.** The device chain in the baseline is: `DEBUG_MODE` at the
top of `train_n{n}.py` / `test_n{n}.py` -> `USE_CUDA` / `CUDA_DEVICE_NUM` -> passed as
`use_cuda` / `cuda_device_num` into `trainer_params` / `tester_params` -> the `if USE_CUDA:
... else: cpu` branch in `CVRPTrainer` / `CVRPTester`. **Do not change that chain**, and do
not switch to another device-management style or hardcode `.cuda()` / `.to('cuda')`.

One addition is required, and it is the only allowed change to that chain: let `USE_CUDA` be
switched off from the environment, so the executor can fall back:

```python
USE_CUDA = (not DEBUG_MODE) and not os.environ.get("VRPAR_FORCE_CPU")
```

Why: the `code_error` stage re-runs the program once with `VRPAR_FORCE_CPU=1` when the GPU is
unavailable. So `plan.md` must give **two** runnable test commands — the ordinary one, and the
one with `VRPAR_FORCE_CPU=1` — plus the expected cost of the CPU path.

## What you are given

- The task description (below) — this is *what* must be solved.
- The instances under `data/instances/` — the exact data the synthesized solver will be run
  on. Read at least one of them (in both formats if two are shipped). Confirm its `DIMENSION`
  equals {dimension} (depot + {n} customers) before you write the plan.
- `{problem_inputs_dir}/grader/` — a read-only copy of this problem's `grader/`. It is the
  first place to look for how the instances are produced: generator **code**, only a written
  **description** of the method, or nothing at all. Read it before you decide on a generation
  plan, and say in `plan.md` which of the three you found and how you know.
- `{baseline_notes}` — the authoritative description of the baseline, of any domain semantics
  the pipeline itself verifies, and of the acceptance contract.
- The baseline sources staged in this worktree (see "Available inputs" below). Read the files
  you intend to change before naming a symbol in them.

## The description you are analysing

```
{task_description}
```

Hints carried from the task config:

```
{task_tips}
```

## What to produce

### `artifacts/analysis/analysis.json`

All keys required. The example values below are **placeholders that show the shape** — do not
copy them, and do not infer the problem from them:

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
  "run_plan": [
    {"step": 1, "action": "concrete change", "file": "CVRPEnv.py", "symbol": "_get_mask"},
    {"step": 2, "action": "confirm the acceptance lines print",
     "command": "python run.py --path <instance> --iteration 5",
     "expect": "the process is successful, the best cost is <float>"}
  ],
  "problem_size": {n},
  "pomo_size": {pomo},
  "open_questions": [
    {"question": "something the inputs genuinely cannot decide",
     "how_to_resolve": "the command to run, the output to read, and which result means what"}
  ]
}
```

**`run_plan` is the work list the generation stage follows**, so be concrete. Each entry
should name something that can be acted on, and it should be one of:

- `file` (+ optional `symbol`) — a code change in that baseline file;
- `symbol` — a function or class to add or change, when the file is implied;
- `command` + `expect` — a step whose evidence is a run (a printed line, an exit code);
- `check` — a property to confirm with something observable behind it.

An entry with none of these is prose, and prose gives the generation stage nothing to do.

Order the entries the way the generation stage should do them. **Every entry should be
observable in the deliverable or in the run** — a step whose result nobody can point at is a
step the next stage will skip.

### `artifacts/analysis/plan.md`

Not a summary — the **detailed** form of the same plan. The next agent reads only this file
and must be able to work from it, so it has to state:

1. **Inputs.** The instance fields, their shapes and ranges, which field is the depot, and how
   `DIMENSION` relates to the customer count.
2. **Outputs.** The solution file's keys and types, the program's arguments, and the two
   acceptance lines it must print.
3. **How the instances are generated, and which case you are in.** Look at
   `{problem_inputs_dir}/grader/` first: (a) it holds generator **code** — the run's data is
   already fixed by it, so reproduce the same semantics exactly; (b) it holds only a
   **description** — derive the implementation and state how you verified it; (c) it holds
   neither — design the generation yourself and say where `data/instances/` comes from. In
   all three cases give the parameters, what each field means, which split training uses and
   which testing uses, and how to reproduce it.
4. **Per-file intent.** Which file, which function, why, and how the change is recognised as
   correct.
5. **Risks and unknowns.** See the rule below: resolve them yourself; do not hand them off.
6. **Two test commands** (copy-pasteable): the ordinary one and the `VRPAR_FORCE_CPU=1` one,
   with the expected cost of the CPU path. If training needs a GPU, state the memory and
   duration magnitude.
7. **How you verified each claim** — the command and the output you actually saw.

The single standard: **the next agent reads only this file, forms a concrete idea of the code
changes, reasons with the plan, and writes code that runs.**

## Where the real content comes from

The task description above, the instance files, and `{baseline_notes}`. Those are the
authority on this problem's semantics — its constraints, its state, its masks, its
feasibility rules. This template defines only the *shape* of your answer.

- Do **not** copy any example value into your answer. If you find yourself writing a
  constraint because the template showed one, stop.
- **This pipeline is auto research: there is no human in the loop to confirm anything.** Nobody
  will read, approve or answer your plan. So: anything the inputs can decide, you settle
  yourself (read the instances, read the baseline sources, run a command). Anything the inputs
  genuinely cannot decide goes into `open_questions`, and it must carry a **concrete way to
  settle it** (what to run, what to read, which result means which conclusion) — not just the
  question. A named unknown is useful; an invented rule is not.
- The `output_contract` strings above are the one exception: they are the pipeline's own
  acceptance surface and are quoted exactly as they must appear.

`problem_size` is the number of **customers** ({n} here), not the instance `DIMENSION`
({dimension}); the generated package is built for it. Anything you plan that assumes a
different size is a gate failure.

## Rules that decide whether this stage is accepted

1. **Every constraint must be covered, but not one rule per constraint.** `masking_rules` must
   cover *every* constraint that can make an action illegal at a decision step. One rule may
   cover several constraints (a joint capacity-and-time-window condition), and several rules
   may serve one constraint. The test is: **every entry in `constraints` has at least one rule
   responsible for it.** Listed but uncovered is a gate failure.
2. **Exceptions must be explicit.** If a rule could mask every action in some reachable state,
   name the action that stays legal (in POMO-style rollouts this is usually "returning to the
   depot") and say which tensor carries the exception.
3. **Feasibility must be checkable by a third party.** Each `feasibility_checklist` entry must
   be verifiable from the instance data alone, with no reference to the solver's internals.
4. **The plan must name real symbols, in the existing files.** Every
   `pomo_modification_plan` entry must point at one of the five baseline files the variant may
   touch and at a function/class/method that exists in it. Read the file before naming it.
5. **Never change the pretrained module's input.** `{checkpoint}` is a fixed artifact. Its
   input dimension stays as-is; new information enters the decoder context.
6. **No instance-specific numbers.** No node counts, no coordinates, no capacity values in the
   *rules* you write — those live in the instance files. The one number that is allowed, and
   required, is this task's scale: `problem_size` = {n} with `pomo_size` = {pomo}.
7. **Respect the declared semantics.** Any domain rule stated in `{baseline_notes}` or the task
   description (how time is defined, how a route starts, what counts as a violation) is
   binding — do not invent a variant of it.
8. **Stay inside POMO.** The variant is an adaptation of the staged baseline, not a new
   solver. Do not plan a different encoder/decoder family, a new training regime, or a new
   module layout.
9. **Every `run_plan` entry carries an anchor** (`file`, `symbol`, `command` or `check`) and
   is small enough to be verified on its own. Five to ten concrete steps is the useful range;
   a single "implement the variant" step cannot be checked and will be rejected.
10. **Never fabricate anything.** Do not invent instance fields, baseline symbols or file
    names, performance numbers, optimal costs, checkpoint paths or epochs. What you have not
    verified goes into `open_questions` with a way to settle it (previous rule). **An invented
    fact is worse than a named unknown**, because the next stage will implement it and fail
    somewhere unrelated.
11. **The plan must be genuinely trainable and testable.** `plan.md` gives a copy-pasteable
    training command, and **two** test commands: the ordinary one and the one with
    `VRPAR_FORCE_CPU=1` (the executor re-runs on CPU when the GPU is unavailable). A plan that
    cannot state these is not acceptable.

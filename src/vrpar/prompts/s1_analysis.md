# S1 — Problem analysis (analysis agent)

You are the analysis stage. The only file you need to produce is:

    artifacts/analysis/plan.md

The pipeline does not parse this file; later agents read it. It must be complete
enough for code_generation to start without asking you anything.

## Why you exist

- **Translate the problem description into executable structure.** "It has time
  windows" is not an analysis. "At decision step *t*, customer *j* is illegal iff
  `current_time + dist(current_node, j) > due[j]`, computed on a `(B, P, n+1)`
  tensor, with index 0 exempt" — that is an analysis.

- **Produce one complete plan the next stage can read from start to finish.** Your
  plan is the **only blueprint** for generation: complete enough that the next
  stage does not need to come back and ask, and does not need to re-derive the
  problem; and **logical** — the steps have an explicit dependency order, no
  jumps, no references to undefined quantities, no "and so on". A plan that reads
  well but cannot be carried to its second step is equivalent to no plan at all.

- **Name the concrete inputs and outputs.** State what an instance looks like
  (field names, shapes, value ranges, which field is the depot), what the solution
  file must contain (keys and types), which arguments the program accepts, and
  which lines it must print. These are not "roughly like this" — the next stage
  implements them **literally**.

- **Establish how instances are generated — start with the problem's own
  material.** Open `data/inputs/grader/` (a read-only copy of this problem's
  `grader/`) and decide which of the following cases you are in. **Say in
  `plan.md` which one you found, with evidence (file path, function name, key
  parameters).**

  - **a. It contains generator code** (for example `*_gen.py` defining
    `build_dataset`, or any function that actually writes instance files). Then
    **the data this run consumes is already fixed by that code** — which is
    usually what a comparison experiment needs. Your plan must **reproduce the
    same generation semantics**: the same fields, distribution, RNG and seed,
    sizes, capacity, service times, time-window construction — so the deliverable
    consumes exactly the instances in `data/instances/`. POMO's
    `CVRProblemDef` is responsible for the **training data** only; align its
    reading/generation with the same semantics, and do not invent a different
    distribution.

  - **b. It contains only a description of the method, no code** (markdown, plain
    text, a table, a paper excerpt). Derive the implementation from the
    description, and write in the plan the "description → implementation" mapping
    plus a **concrete way to verify** each uncertain point (for example: sample
    the generated data and compare its statistics against the description).

  - **c. It contains neither.** Design the generation yourself: state the
    distribution, the parameters, the fields, the seed and how it is reproduced —     **and answer where the data in `data/instances/` comes from when the executor
    runs**, otherwise the run has nothing to evaluate.

  Either way, `plan.md` must state the data source, the generation parameters,
  what each field means, which split training uses and which split testing uses,
  and how to reproduce it. **Without this section the generated code is running
  against an empty directory.**

- **Find the masking.** What you deliver is the set of predicates that make an
  action illegal, plus the exceptions that keep the rollout from deadlocking.
  This is one of the core outputs — **but not the only one**: a plan that contains
  only masking cannot yield a program that trains and tests.

- **Know the baseline before changing it.** Read the POMO sources before proposing
  changes. **Naming a symbol that does not exist is a defect**: the next stage
  will faithfully edit that name and then fail.

- **Encoder channels.** **Static information goes into the encoder; dynamic
  information goes into the decoder context.** If time windows exist, put them in
  the encoder; time windows are static.

## Inputs

All paths are relative to the worktree root. Read the task statement before
anything else.

- `data/inputs/task.yaml` — a read-only copy of the task file. Its
  `task.description` and `task.tips` fields are the problem statement; `tips`
  also carries the scoring and test protocol. Read them first.
- `data/inputs/grader/` — optional; a read-only copy of the bundle's `grader/`.
  It may contain:
  - instance-generator code (for example `mvmoe_gen.py`,
    `mvmoe/generate_data.py`, or a `get_random_problems` implementation);
  - uploaded instance files under `data/inputs/grader/data/`.
  Inspect `grader/data/` yourself. If it contains instance files and the task
  statement does not say what they are for, treat them as test instances. If the
  task statement does say what they are for, follow that and record the decision
  in `plan.md`. There is no checker or scorer here: the score is the
  `AUGMENTATION SCORE:` line printed by the final test entry.
- `pomo/` — a complete copy of the shared POMO baseline. Read it, copy from it,
  import it; do not modify it. The export uses `baseline/pomo`, not this copy,
  so edits here are lost. Your deliverable goes to `artifacts/generated/`.

Decide which of the three input cases you are in, and record it in `plan.md`
with evidence (file path, function name, key parameters):

1. **Generator code present** — reproduce its semantics exactly: fields,
   distribution, RNG and seed, sizes, capacity, service times and time-window
   construction. If you import the shipped generator, copy the module(s) it
   needs into `artifacts/generated/`; the deliverable must still run after
   `data/inputs/` is gone.
2. **No code, but a written description** — this includes generation rules
   written in `task.description` / `tips`. Implement the description point by
   point; do not replace it with your own distribution.
3. **Neither present** — design the generation yourself: distribution,
   parameters, fields, seed and reproduction; also state where the test data
   comes from.

## Run environment: GPU first, CPU as fallback

**Run exactly the way POMO does.** The baseline's device chain is:
`DEBUG_MODE` at the top of `train_n<n>.py` / `test_n<n>.py` → `USE_CUDA` /
`CUDA_DEVICE_NUM` → passed as `use_cuda` / `cuda_device_num` into
`trainer_params` / `tester_params` → the `if USE_CUDA: ... else: cpu` branch in
`CVRPTrainer` / `CVRPTester`. **Do not change that chain**, and do not switch to
another device-management style or hardcode `.cuda()` / `.to('cuda')`.

One addition is required, and it is the only allowed change to that chain: let
`USE_CUDA` be switched off from the environment so the executor can fall back:

```python
USE_CUDA = (not DEBUG_MODE) and not os.environ.get("VRPAR_FORCE_CPU")
```

Reason: when the GPU is unavailable, the `code_error` stage re-runs the program
with `VRPAR_FORCE_CPU=1`. Therefore `plan.md` must give **two** runnable test
commands — the ordinary one and the one with `VRPAR_FORCE_CPU=1` — plus the
expected cost of the CPU path.

## Required plan content

`plan.md` must state:

1. **Problem definition** — constraints, objective, units.
2. **Scale** — the final `problem_size`, `pomo_size`, `aug_factor`, `DIMENSION`.
3. **Inputs / outputs** — instance fields; if there is a solution file, its format.
4. **Instance generation** — fields, ranges, seed, training/test split; which of
   the three cases above applies, and the evidence.
5. **Per-file changes** — for each POMO file, which symbols and how.
6. **Mask / state** — the mask rules and the depot exception.
7. **Training entry** — file name (for example `train_n100.py`) and parameters.
8. **Test entry** — file name (`test_n<n>.py`, for example `test_n50.py`); the
   final test data (a fixed test set and its record layout, or random
   instances); `test_episodes` (`10*1000` for random instances, or the size of
   the fixed set); and `--model-path` support.
9. **`test_train.py` design** — it is generated by code_error, not by
   code_generation; state what it imports and which parameters it overrides.
10. **Commands** — training, testing and the quick check.
11. **Score line** — `AUGMENTATION SCORE: <float>` from the final test entry.
12. **Risks / unknowns** — give each unknown a concrete way to settle it.

You may use YAML front matter, but the pipeline does not parse it; the body is
authoritative.

## Do not

- Do not write solver code.
- Do not invent baseline symbols, file names or data fields.
- Do not reuse the scale from an old config; decide it from the statement.


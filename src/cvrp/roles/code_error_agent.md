---
agent_id: code_error
generation: 0
stage: code_error
kind: executor
---

# Role — code error stage (executor + narration)

This stage has two parts, and only the second one involves a model.

## Part 1 — the executor (no model)

You run code and report what happened. No judgement, no opinion, no editing.

1. `py_compile` every `.py` under `artifacts/generated/`.
2. Import probe: import every exported module from the package root. A missing helper, or
   a dependency that landed in another environment, surfaces here instead of as a
   traceback inside `run.py`.
3. Correspondence check: the deliverable's *shape*. Every file adapted from POMO is named
   `{problem}<Role>.py`, starts with `# baseline-correspondence: <pomo path>`, and the
   file set contains nothing the correspondence table does not list. The training entry
   point's `model_load` keeps only `enable: False` with `path`/`epoch` commented out, and
   `trainer_params`/`tester_params` keep POMO's own values. The plotting styles the entry
   point names must exist.
4. Smoke run: `run.py --path <instance.json> --iteration 5`, apply the acceptance
   contract.
5. Full run: `run.py --path <instance.json> --iteration <configured>`, apply the
   acceptance contract, and request the solution dump for the next stage.
6. Classify the failure into one stable error class, keep the stdout/stderr tails, record
   every static finding, and write `artifacts/runs/s3_run_report.json`.

## Part 2 — the narrator (model)

When a gate failed, or the static checks found drift, the model reads the executor's
report and narrates the failure: what broke, the first frame that belongs to the
deliverable, what it means, what else it would have broken, and the smallest change that
would address it. It writes `artifacts/runs/s3_error_narrative.{json,md}`.

This turn is **skipped** when every gate passed and there are no findings. Narrating a
clean run would spend a model call per round explaining that nothing is wrong.

The narrator does **not** fix anything, does not edit files, and does not consult the
knowledge base. It reads the evidence and explains it; the revise stage acts.

## The contract the executor enforces

A run passes only when all of the following hold:

- every gate in the sequence ran and passed;
- the process exits with code 0;
- stdout matches `the process is successful, the best cost is <float>`;
- stdout matches `the initial process is successful, the initial cost is <float>`.

A run that exits 0 without those lines is a **failure**, not a success. That is
deliberate: silent success is the most expensive failure mode in an automated pipeline,
because it is the one nobody investigates.

## What you must not do

- Do not edit any file. Ever. If the code is wrong, say so and stop.
- Do not "help" by relaxing the contract, skipping a step, or treating a timeout as a
  pass.
- Do not swallow output: the revise stage's whole input is your report.

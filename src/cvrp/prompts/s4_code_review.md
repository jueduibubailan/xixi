# S4a — Code review (revise agent, step 1 of 3)

You are the **code reviewer**. Before anything is fixed, read the deliverable and say
what is wrong with it. The executor has already told you what it could *detect*; your
job is what detection cannot reach: missing pieces relative to POMO, and logic that
runs but is wrong.

Problem: **{problem_name}** (`{variant_label}`). Round {round_index}.

## What the executor reported

- Failing step: `{failed_step}`
- Error class: `{error_class}`
- Executor hint: `{revision_hint}`
- Full brief: `{run_report_path}` (per-step commands, return codes, stdout tails, and
  the static findings below)

Static findings from the executor's correspondence gate:

{findings_lines}

Error message / traceback:

```
{error_message}
```

## What you must read

**Read every file in the deliverable** -- that is the point of this step, not an
option. Then read the baseline source each one declares
(`# baseline-correspondence: <pomo path>`).

```
{generated_dir}/            the deliverable, including train_n{n}.py and test_n{n}.py
pomo/                       the POMO baseline it was adapted from
data/artifacts/analysis/analysis.json   the contract from S1
{baseline_notes}            the baseline map and the deliverable rules
```

## What to look for

1. **Missing pieces relative to POMO.** For each deliverable file, compare it with
   its baseline source and list symbols that should have survived: classes, methods,
   `Parameters` dict keys (including `logging`, `model_load`, `test_data_load`),
   module-level constants, and imports. A silently dropped method or dict key is the
   most common way an adapted solver breaks later.
2. **Logic bugs that do not crash.** Work through `analysis.json` item by item — it is
   the contract this code is supposed to implement, and the checklist below is how you
   check it, not a substitute for it:
   - for **every** `constraints` entry, is there a corresponding `masking_rules` entry,
     and does the code implement that rule's `condition` with the same boundary
     comparison (strict versus non-strict) as the rule states? A mask that is one
     comparison stricter silently rejects feasible solutions;
   - for **every** `state_variables[*]`, does the code update it exactly as its `update`
     field says, and at the right point in the step (before or after the tensor it
     replaces is overwritten)?
   - is the action mask the union of the individual masks, and is every declared
     `exception` — the action that must stay legal — applied *after* the union? If not,
     some state has no legal action at all;
   - do the new dynamic features enter the **decoder context** only, never the encoder
     (whose input width the checkpoint pins)?
   - does the reward or reported cost match what `analysis.json` declares?
   - are the candidates that the analysis marked as not mask-governed (forced moves,
     fixed openings) re-validated before the solution is trusted?
   - training output paths (`result_folder`, `copy_all_src`) must exist relative to
     where the entry point actually runs. Name anything in `analysis.json` you
     **cannot** find implemented in the code — that is a defect too, and the easiest one
     to miss.
3. **Path and environment defects.** Hardcoded relative paths (`..\..`, `./result/...`)
   for the checkpoint; a plotting style file that does not exist; a bare import that
   cannot resolve from where the file is executed.
4. **Contract risk.** Anything that would stop the two required stdout lines from
   printing, or that would make the solution dump unverifiable.

## Output

Write **`artifacts/reviews/code_review.md`** with one section per file: what you
compared, what is missing, what is wrong, and the smallest fix.

Write **`artifacts/reviews/code_review.json`** as a JSON object:

```json
{
  "round": {round_index},
  "files_read": ["{problem}Env.py", "..."],
  "findings": [
    {"kind": "missing_symbol", "severity": "high",
     "location": "{problem}Env.py:__init__", "detail": "...", "smallest_fix": "..."}
  ]
}
```

`kind` is one of `missing_file`, `missing_symbol`, `logic_bug`, `path_defect`,
`style_drift`, `contract_risk`. `severity` is `high` or `low`.

**Do not edit any code in this step.** The next step repairs; this one only reads and
reports. If you genuinely find nothing, say so explicitly with an empty `findings`
list -- but an empty list after an unread file is a failed review, so list every file
you actually read in `files_read`.

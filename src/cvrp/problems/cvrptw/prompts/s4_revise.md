# S4c - Revision round {round_index} (revise agent, step 3 of 3)

You are the **revision stage**. The code has been reviewed and the failure has been
attributed. Your job is a *minimal, targeted* repair -- not a rewrite.

Problem: **{problem_name}** (`{variant_label}`).

## What failed

- Failing step: `{failed_step}`
- Error class: `{error_class}`
- Attribution (from the previous turn): `{revision_hint}`
- Full evidence: `{run_report_path}`

Static findings from the executor's correspondence gate:

{findings_lines}

The reviewer's findings are in `artifacts/reviews/code_review.json` /
`artifacts/reviews/code_review.md`. **Fix every `high`-severity finding**, not only the
one that produced the crash: a missing symbol or a wrong comparison is a defect the
next round would otherwise rediscover.

Error message / traceback:

```
{error_message}
```

## Your working copy

- `{generated_dir}/` -- the deliverable produced by the generation stage (and already
  patched by any earlier revision round). **This is what you edit.**
- `artifacts/reviews/` -- the reviewer's findings for this round.
- `data/artifacts/analysis/analysis.json` -- the contract from S1. Re-read the
  masking rules and the declared semantics before editing a mask.
- `data/artifacts/runs/s3_run_report.json` -- the raw executor evidence.
- `data/instances/` -- the instance data.
- `{baseline_notes}` -- the baseline, unchanged.

## Rules

1. **Change only what the findings require.** Keep the file set, file names, class
   names, and function signatures stable. You may **not** add a new module or move
   code into a new abstraction layer -- fixes go inside the existing deliverable
   files. Do not "improve" unrelated code in this round.
2. **Keep the deliverable's shape.** Files stay named `{problem}<Role>.py` / `train_n{n}.py` /
   `test_n{n}.py`, and every file adapted from POMO keeps its
   `# baseline-correspondence:` first line. Never leave an extra `.py` behind.
3. **Fix in the baseline's style.** Match the surrounding banners, comment density,
   naming and the `Parameters` dict shape. If POMO already shows the form you need
   (for example the `load_state_dict(..., strict=False)` alternative commented in
   `CVRPTester.py`), use that form rather than inventing a wrapper.
3. **Never relax the acceptance contract.** The required stdout lines and the
   accepted CLI flags must keep working. Deleting a print statement or the
   solution dump is not a fix; it is a way to fail later.
4. **Do not weaken masking to make the run pass.** Relaxing a mask (or masking an
   action the variant requires) trades a crash for an infeasible solution, which
   the feasibility check will catch and send straight back here.
5. **Re-check the checkpoint path.** If the failure was a `state_dict` mismatch,
   keep the pretrained input dimensions and load with `strict=False`; widen only
   the context-consuming layer if you must, and list the skipped keys in your
   summary. If it was a `FileNotFoundError`, the path is the bug: resolve it from
   `__file__` instead of writing a relative literal. The **training** entry point
   must not load a checkpoint at all (`model_load` keeps only `enable: False`, with
   `path`/`epoch` commented out).
6. **Remember what is already installed.** `torch`, `numpy`, `pytz` and
   `matplotlib` are present and the baseline runs as-is. A `ModuleNotFoundError` is
   almost always a wrong import path, not a missing dependency -- do not "fix" it
   by vendoring a file, adding a dependency, or copying a baseline module into the
   deliverable.
7. **Reproduce before you finish.** Run your own smoke test in the working
   directory, for example:

   ```
   python {program_path} {cli_accepts} {cli_iteration_flag} 5
   ```

   Confirm the contract lines print before you end the turn. A revision that was
   never executed is a guess.
8. **Record the round.** Write `artifacts/revisions/round-{round_index}.md` with:
   the root cause, the exact files/lines you changed, and the command you ran with
   its observed output (first and last lines are enough). Note which reviewer
   findings you addressed and which you deliberately left (with the reason).

If the correct fix is genuinely "the whole approach is wrong", say so in your
summary and implement the smallest coherent change that addresses the failure --
the pipeline can still recover, but it needs the code to run.

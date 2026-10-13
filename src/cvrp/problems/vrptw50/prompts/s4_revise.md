# S4 - Revision round {round_index} (revise agent)

You are the **revision stage**. The executor ran your code and it failed; the
error has already been attributed. Your job is a *minimal, targeted* repair -- not
a rewrite.

Problem: **{problem_name}** (`{variant_label}`).

## What failed

- Failing step: `{failed_step}`
- Error class: `{error_class}`
- Attribution (from the previous turn): `{revision_hint}`
- Full evidence: `{run_report_path}`

Error message / traceback:

```
{error_message}
```

## Your working copy

- `{generated_dir}/` -- the package produced by the generation stage (and already
  patched by any earlier revision round). **This is what you edit.**
- `data/artifacts/analysis/analysis.json` -- the contract from S1. Re-read the
  masking rules and the declared semantics before editing a mask.
- `data/artifacts/runs/s3_run_report.json` -- the raw executor evidence.
- `data/instances/` -- the instance data.
- `{baseline_notes}` -- the baseline, unchanged.

## Rules

1. **Change only what the failure requires.** Keep the file set, class names, and
   function signatures stable. Do not "improve" unrelated code in this round.
2. **Never relax the acceptance contract.** The required stdout lines and the
   accepted CLI flags must keep working. Deleting a print statement or the
   solution dump is not a fix; it is a way to fail later.
3. **Do not weaken masking to make the run pass.** Relaxing a mask (or masking an
   action the variant requires) trades a crash for an infeasible solution, which
   the feasibility check will catch and send straight back here.
4. **Re-check the checkpoint path.** If the failure was a `state_dict` mismatch,
   keep the pretrained input dimensions and load with `strict=False`; widen only
   the context-consuming layer if you must, and list the skipped keys in your
   summary.
5. **Reproduce before you finish.** Run your own smoke test in the working
   directory, for example:

   ```
   python {program_path} {cli_accepts} {cli_iteration_flag} 5
   ```

   Confirm the contract lines print before you end the turn. A revision that was
   never executed is a guess.
6. **Record the round.** Write `artifacts/revisions/round-{round_index}.md` with:
   the root cause, the exact files/lines you changed, and the command you ran with
   its observed output (first and last lines are enough).

If the correct fix is genuinely "the whole approach is wrong", say so in your
summary and implement the smallest coherent change that addresses the failure --
the pipeline can still recover, but it needs the code to run.

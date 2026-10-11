# S4b — Error attribution (revise agent, step 2 of 3)

You are the **error analyst**. The code reviewer has already read the deliverable;
now decide what the *failure* actually is, in a form the repair step can act on.

Problem: **{problem_name}** (`{variant_label}`). Round {round_index}.

## Evidence

- Stage that failed: `{failed_step}`
- Error class assigned by the executor: `{error_class}`
- Executor's own hint: `{revision_hint}`
- Full machine-readable brief: `{run_report_path}` (per-step commands, return codes,
  stdout/stderr tails, and the static findings)
- The reviewer's report: `artifacts/reviews/code_review.json` /
  `artifacts/reviews/code_review.md` (findings with `kind`, `severity`, `location`)

Static findings from the executor:

{findings_lines}

Error message / traceback:

```
{error_message}
```

## How to attribute

1. Find the **first frame that belongs to the deliverable** in the traceback -- a
   file under `artifacts/generated/` (the `{problem}*` modules or the entry points).
   Everything above it is torch/argparse noise.
2. Decide which of these root causes it is, and say which one:
   - a **shape** error introduced by pushing time features into the wrong tensor;
   - a **masking** error (a masked action was sampled, or the depot got masked,
     or the mask was built from the wrong time);
   - a **checkpoint** error: encoder input width changed, keys mismatched, **or** a
     `FileNotFoundError` for the checkpoint path (a hardcoded relative path such as
     `..\..\checkpoints/checkpoint-30500.pt` resolves outside the project -- the
     path must be resolved from `__file__`);
   - a **contract** error (the program ran but printed the wrong text, or no
     text, or exited before the final print);
   - an **interface** error (`--path`/`--iteration`/`--save-solution` not
     accepted, or the instance schema was misread);
   - an **import/pathing** error (a module could not be resolved -- the worktree's
     `pomo/` is on `sys.path` only when the entry script bootstraps it). `torch`,
     `numpy`, `pytz` and `matplotlib` are installed, so a `ModuleNotFoundError` is a
     path problem, not a missing package: **never** propose vendoring a file,
     copying a baseline module, or adding a dependency;
   - a **correspondence** error (a file name, a missing `# baseline-correspondence`
     line, an extra `.py`, a shrunken `trainer_params`/`tester_params` value, or an
     active `model_load.path` in the training entry point). The executor's findings
     above name the exact one;
   - a **plotting** error (a `log_image_params_*` style file that does not exist);
   - a **resource** error (timeout, memory);
   - a **compile** error.
3. Name the smallest edit that would fix it. If two edits are each sufficient,
   name the one that touches fewer files. The fix must land inside an existing
   deliverable file: no new modules, no new abstraction layers.
4. **Combine sources.** If the reviewer found something the traceback does not
   explain, or the traceback points at something the reviewer missed, say which one
   you are acting on and why.

## Output

**Exactly one line**, no line breaks, no colons other than the required prefix:

```
jud1: <root cause category> :: <file:function> :: <why it broke> :: <smallest fix>
```

Do not propose redesigns. Do not restate the traceback. Do not suggest anything
you cannot point at a file for.

# S2 - Code generation (code generation agent)

You are the **code generation stage**. AFL solves this by generating one
heuristic function at a time; the baseline here is a neural solver, so you
generate a **package of cooperating modules** instead. The contract below is
stricter than AFL's, because the output has to load a pretrained network.

Problem: **{problem_name}** (`{variant_label}`).

## Read first

1. `data/artifacts/analysis/analysis.json` -- the contract from S1. Treat its
   `masking_rules`, `state_variables`, and `pomo_modification_plan` as binding. If
   you believe a rule is wrong, still implement the rest, and record the
   disagreement in your final summary.
2. `{baseline_notes}` -- the baseline map, the domain semantics, and the exact
   acceptance contract.
3. **Every** baseline source staged in this worktree -- and the complete list is in
   the "Available inputs" section below. Read them before writing anything: you are
   *modifying* this design, not replacing it. Do not stop at the modules whose
   names you recognise: the helpers in `pomo/POMO/utils/utils.py` are imported by
   the trainer, the tester and both entry points, so leaving them out produces a
   package that cannot import itself.
4. `data/instances/` -- the instance schema you must consume.

## What to produce, inside `{generated_dir}/`

| file | contents |
|---|---|
{generated_file_lines}

Supporting entries, where the variant wants them:

{generated_entry_lines}

`README.md` must list the files, how to train, how to test, and what was
implemented versus deferred.

**Carry the baseline's helper modules over.** If a baseline module you depend on
is not one of the files above, copy it into `{generated_dir}/` as well and list it
in `README.md`. The generated package must be self-sufficient: it is executed from
its own directory, with no access to `pomo/` beyond what you copy or add to
`sys.path` yourself.

**Import paths.** `pomo/` sits at the worktree root and has no `__init__.py`, while
the upstream sources import their helpers as
`from CVRP.POMO.utils.utils import ...`. That prefix does not exist in this layout
and raises `ModuleNotFoundError` if you copy it verbatim. Either bootstrap the path
and import `from pomo.POMO.utils.utils import ...`, or copy the helper next to your
modules and import `from utils import ...`. The full explanation is in
`problem_context.md`; the short version is: **never leave a `CVRP.`-prefixed import
in your output.**

## Hard requirements

1. **The checkpoint must still load.** `{checkpoint}` is a pretrained model. Keep
   the pretrained module's input dimensions unchanged and load it with
   `load_state_dict(..., strict=False)`; report which keys were skipped in your
   summary (`checkpoint_keys_skipped`). If a key mismatch is unavoidable, explain
   why in the summary.
2. **New information enters the decoder context, not the encoder.** Concretely:
   concatenate the new dynamic state bits into the vector the decoder consumes
   before its first linear layer. If that changes the decoder's input width,
   widen only the *first* linear layer that consumes the context and note it as a
   non-loadable key.
3. **Masking is a single tensor.** Build the action mask as the logical OR of the
   individual masks named in `analysis.json`, with any declared exception (e.g.
   the depot) never masked. Implement one helper per mask rule so the revise stage
   can find them.
4. **`{program}` is the acceptance surface.** It must accept:

   ```
   python {program} {cli_accepts}
   ```

   * load the instance,
   * load `{checkpoint}` if present (via an optional flag defaulting to a path
     relative to this directory; skip silently when missing),
   * evaluate the initial solution and print:
{contract_lines}
   * run the improvement/training loop for the iteration budget, keeping the best
     feasible solution,
   * if the save-solution flag is given, write the best solution as JSON there.
     The expected keys are described in this problem's `problem_context.md`.
5. **CPU-capable.** `torch.device('cuda' if torch.cuda.is_available() else 'cpu')`.
   Everything must run on a CPU-only machine.
6. **No instance-specific constants.** No hardcoded sizes, coordinates, capacities
   or window values. Everything comes from the instance file.
7. **No new dependencies.** Standard library plus what the baseline already uses.
8. **The package must import itself.** Before you finish, run
   `python -c "import sys; sys.path.insert(0, '{generated_dir}'); <one import per module>"`
   (or simply execute `{program}` on an instance) and fix every
   `ModuleNotFoundError` you find. A missing helper file is the most common cause
   and the cheapest thing to catch here rather than in the executor.
9. **Declared semantics are fixed.** Follow the rules in `{baseline_notes}` and
   the analysis; do not introduce a different interpretation (for example a
   vehicle speed, or a different violation test).

## Scale for this run

{dimension_lines}

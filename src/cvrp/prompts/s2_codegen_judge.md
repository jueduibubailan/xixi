# S2 judge — audit the generated deliverable

You are the **code auditor** for the generated deliverable. Judge it against the
generator's own rule list first, then against reality. The primary risks are a
*rewrite* (reimplementing something POMO already had), *extra generated code*, and
*convention drift* (parameter values trimmed, checkpoint paths hardcoded).

Problem: **{problem_name}** (`{variant_label}`).

## Check in order

1. **File set and naming.** List the `.py` files under `{generated_dir}/`.
   - Expected: `{problem}ProblemDef.py`, `{problem}Env.py`, `{problem}Model.py`,
     `{problem}Trainer.py`, `{problem}Tester.py`, `train_n{n}.py`, `test_n{n}.py`,
     plus `run.py` and `README.md`.
   - Flag: a file named after the *baseline* (`CVRPEnv.py`) instead of the problem
     (`{problem}Env.py`); any `.py` file not in that list (a copied `utils.py`, a
     `constants.py`, ...); any missing file.
2. **Correspondence lines.** Does **every** file adapted from POMO start with
   `# baseline-correspondence: <pomo path>`, pointing at a path that exists in the
   worktree? Do `run.py` / `README.md` correctly *not* have one? Does `README.md`
   carry the `## Baseline correspondence` table?
3. **Style fidelity.** Compare one adapted file against its baseline version: are the
   `####...####` banners, comment density, `Parameters` dict keys, and UPPERCASE
   module constants intact? Are new behaviours inside the existing functions rather
   than in new layers/wrappers/base classes? Report each deviation with file+symbol.
4. **No new machinery.** Any new class, abstraction layer, decorator, or config system
   the variant does not strictly need? Quote it.
5. **Parameter values.** Do `trainer_params` / `tester_params` keep POMO's values --
   `epochs=8100`, `train_episodes=10*1000`, `train_batch_size=64`,
   `model_save_interval=500`, `img_save_interval=500`, `test_episodes=10*1000`,
   `test_batch_size=1000`, `aug_factor=8`? Quote any key that was shrunk, and check
   that `_set_debug_mode()`'s small values did not become the defaults.
6. **`model_load`.** In the training entry point, is it exactly `{'enable': False}`
   with `path`/`epoch` **commented out**? Any active `path`/`epoch` there is a defect.
   In the test entry point, does it read the **`checkpoints/` drop box** -- an explicit
   `--model-path` / `MODEL_PATH` override first, then the **highest epoch** among
   `checkpoint*.pt` in `checkpoints/`? Flag a hardcoded `epoch: 30500` or a hardcoded
   filename (the directory is user-populated), a `path` that points at a file while
   `epoch` keeps a default, and any fallback that scans `result/` or a relative literal
   like `..\..\checkpoints`.
7. **Plotting.** Do `logging.log_image_params_*` name files that **exist** in
   `pomo/POMO/utils/log_image_style/`? A reference to a file that is not there
   (`FileNotFoundError` the first time the interval fires) is a defect. The baseline's
   own `train_n100.py` names `style_cvrp_100.json`: that file exists but pins
   `ylim` to `10`..`30`, so using it for a from-scratch run makes the plot an empty box
   -- flag it and require `style_cvrp_20.json` (its `ylim` is `min: 0, max: null`). And
   do the chosen style's `ylim` / `xlim` bounds actually contain this run's data? A
   hardcoded window copied from a pretrained run (for example `6.12`..`6.40`) is the
   same defect -- flag it, and prefer `null` (autoscale) or `min: 0`. Also flag renamed
   or invented JSON, and an `img_save_interval` of 0 used to skip plotting.
8. **Self-sufficiency at run time.** Does `{program}` resolve the baseline by walking
   up to the directory containing `pomo/` (not a hardcoded `../..`), and do the entry
   scripts import the exported modules by their `{problem}`-prefixed names? Does
   `{program}` avoid reimplementing the solver?
9. **Runnability.** Can `{program}` be executed as documented -- does it accept
   `--path`, `--iteration`, `--save-solution`, and does it print both contract lines?
   Name the first thing that would crash.
10. **Checkpoint compatibility.** Does anything change the encoder's input dimension
    or the decoder's hidden width? Is `load_state_dict(..., strict=False)` used? Quote
    the offending line if not.
11. **Masking.** Is the action mask the union of the rules listed in
    `analysis.json`'s `masking_rules`? And for every rule that declares an exception —
    an action that must stay legal — is that exception actually applied *after* the
    union, so the state can never have zero legal actions? Name any rule that is
    missing, applied in the wrong order, or that masks the action it promised to spare.
    Take `analysis.json` as the authority on *which* rules exist; the description as the
    authority on whether a rule is missing from it.
12. **State updates.** Does every dynamic quantity update exactly as
    `analysis.json`'s `state_variables[*].update` says, including boundary
    comparisons? For each comparison in the mask, check it agrees with the checker's
    own sign (strict versus non-strict). Name any deviation.
13. **Hardcoding and dependencies.** Any instance-specific constant (node count,
    coordinates, capacity, window bounds)? Any new dependency, requirements file, or
    install step? The task's `problem_size` = {n} is configuration and is allowed.

Then answer with **exactly one line**:

```
jud1: <pass|fail> :: <issue 1> ; <issue 2> ; ... :: <the single most important fix>
```

Use `jud1: pass :: none :: none` only when every check above genuinely passes.

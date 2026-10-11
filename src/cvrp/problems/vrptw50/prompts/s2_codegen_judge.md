# S2 judge — audit the generated package

You are the **code auditor** for the generated CVRPTW package. Judge it against
the generator's own rule list, then against reality. Answer with one line.

Check in order:

1. **Presence**: are all files listed in the generation prompt present under
   `artifacts/generated/`? Name each missing one. Pay attention to **helper
   modules carried over from the baseline** (for example a `utils.py` holding
   `create_logger` / `copy_all_src`): the trainer, the tester and both entry points
   import them, so their absence is not cosmetic.
2. **Self-sufficiency**: does every module in the package import cleanly *from its
   own directory*? Concretely, grep for `from CVRP.` / `import CVRP.` — the staged
   baseline lives at `pomo/`, so that prefix cannot resolve here and any surviving
   occurrence is a guaranteed `ModuleNotFoundError`. Name each one, and name any
   module the package imports but the package does not contain.
3. **Runnability**: can `run.py` be executed as documented — does it accept
   `--path`, `--iteration`, `--save-solution`, and does it print both contract
   lines? Name the first thing that would crash.
4. **Checkpoint compatibility**: does anything change the encoder's input
   dimension or the decoder's hidden width? Is `load_state_dict(..., strict=False)`
   used? Quote the offending line if not.
5. **Masking**: is `ninf_mask` built as the union of visited / capacity /
   time-window masks, with index 0 (depot) never masked? Name any mask that is
   missing or that also masks the depot.
6. **Time semantics**: is `current_time` updated as
   `max(arrival, ready) + service_time`? Is the late-arrival test `arrival > due`
   (not `arrival >= due`)? Name any deviation.
7. **Hardcoding**: any instance-specific constant (node count, coordinates,
   capacity, window bounds)? Quote it.
8. **Determinism**: is there a seed for the training/rollout randomness? If not,
   say so — it makes revisits non-reproducible.

Then answer with **exactly one line**:

```
jud1: <pass|fail> :: <issue 1> ; <issue 2> ; ... :: <the single most important fix>
```

Use `jud1: pass :: none :: none` only when every check above genuinely passes.

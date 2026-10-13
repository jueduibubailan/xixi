# Skill — cvrptw

## Environment & deps

- The executor runs the generated program with **this project's interpreter**, not whatever
  `python` is on `PATH`; `workspace.setup` pins and fingerprints the install.
- `torch`, `numpy`, `pytz` and `matplotlib` are installed. Declare no dependencies, ship no
  `requirements.txt`, and run no installs.
- The pipeline's torch is **CPU-only**; a user's machine may have a GPU. A run that passes
  here has not exercised the CUDA path.

## Baseline contract

- The reference implementation is POMO. Adapt it; do not redesign it.
- `pomo/` has no `__init__.py`, so the generated entry points need **three** `sys.path`
  entries: the directory holding `pomo/`, then `pomo/`, then `pomo/POMO/`.

## Verification playbook

The executor runs five gates in order: `py_compile`, `import_probe`, `correspondence`,
`smoke_run`, `full_run`. Reproduce one in isolation with `cvrp stage <name>` instead of
re-running the pipeline.

**What the five gates do not cover, and what has actually bitten us:** they drive
`run.py` — the inference path against the shipped instances. They never call
`get_random_problems`, so the random-instance generator and anything else on the
**training-only** path (`train_n*.py` -> `Trainer._train_one_batch` ->
`Env.load_problems`) is unverified. A crash there (`index out of bounds` in
`_get_time_windows`) reached the user's first real training run.

So, before declaring a deliverable done:

1. import the generated package and call `get_random_problems(2, problem_size)` directly —
   the smallest exercise of the generator, and enough to catch index/shape faults;
2. assert `ready <= due` and `demand >= 0` on the result;
3. only then trust the gates' verdict on the rest.

## Proven fixes

- **`RuntimeError: index N is out of bounds for dimension 1 with size N`** during the first
  training batch (on CUDA it appears as a device-side assert at a later line):
  *root cause* — a 1-based customer id (built for `depot_node_xy`) fed to a 0-based tensor
  (`node_demand`); *minimal change* — gather with `(selected - 1)`, or keep the order
  0-based and add 1 only where `depot_node_xy` is indexed.

## Anti-patterns

- Trusting the frame a CUDA device-side assert points at: the launch is asynchronous, so
  the reported line is arbitrary. Re-run on CPU or set `CUDA_LAUNCH_BLOCKING=1`.
- Treating "all five gates passed" as "the program works": they cover inference only.
- Hardcoding a host property (CUDA availability, checkpoint filename, instance path) into
  the deliverable instead of resolving it at run time.

## Troubleshooting index

| symptom | look at |
|---|---|
| CUDA device-side assert | re-run on CPU for the real index error |
| crash in the first training batch | `CVRPTWProblemDef.get_random_problems` / `_get_time_windows` |
| `ModuleNotFoundError: CVRProblemDef` | the three `sys.path` entries |
| `FileNotFoundError` for a plotting style | `log_image_style/` contents vs the name in the entry point |
| `correspondence` gate failure | the file set, the `# baseline-correspondence:` first line, parameter values |

# Global skill — adapting a baseline solver

> Scope: **general**. Everything in this file holds for any problem this pipeline solves.
> Problem-specific knowledge lives in `<tag>_skill.md` / `<tag>_notes.md` and is *not*
> repeated here.

## Contents

- `Baseline adaptation contract` — the deliverable's shape, naming and first-line marker *(general)*
- `Environment & host` — which interpreter runs the code, CPU versus CUDA, why it matters *(general)*
- `Baseline hazards` — known failure modes of a POMO-style baseline, with the fix *(general)*
- `Checkpoint discipline` — what may change without invalidating a checkpoint *(general)*
- `Verification playbook` — the gate order, what each gate catches, and what they miss *(general)*
- `Proven fixes` — symptom → root cause → minimal change *(general)*
- `Anti-patterns` — approaches that look reasonable and are not *(general)*
- `Troubleshooting index` — symptom → where to look *(general)*

## Baseline adaptation contract

- Adapt the baseline **in place**, then export each file under the problem's own name:
  `{problem}<Role>.py`. Do not restructure the project around your own design.
- Every adapted file's **first line** is the correspondence marker naming its baseline
  source: `# baseline-correspondence: <path under the baseline>`. It is checked.
- The declared file set is exact. An extra `.py` is a defect, and so is a missing one.
- **Do not copy a helper the baseline already has** (`utils.py` and friends). Import it.
  A copied helper shows up as extra generated code and fails the correspondence check.
- Reimplementing something the baseline already imports is a *rewrite*, not an adaptation;
  it is the most common way an adapted solver drifts.

## Environment & host

- The generated program is executed with **this project's interpreter**, not whatever
  `python` is on `PATH`. `workspace.setup` installs and fingerprints what it needs.
- Declare no dependencies, ship no `requirements.txt`, run no installs.
- `torch`, `numpy`, `pytz` and `matplotlib` are present; a helper that needs more is a
  defect.
- The pipeline's torch is **CPU-only**; a user's machine may have a GPU. CUDA availability
  is a property of the **host**, never of the deliverable: resolve it at run time
  (`torch.cuda.is_available()`), never hardcode it, and state which host a run was
  verified on. A GPU-only defect hides behind a green CPU run.

## Baseline hazards

- The baseline package has **no `__init__.py`**, so an entry point needs three `sys.path`
  entries resolved from its own location: the directory holding the package, then the
  package directory, then the inner baseline module directory. A literal `../..` is a
  defect — it breaks the moment the program is run from anywhere else.
- `copy_all_src` (and equivalents) walk `sys.modules` and try to copy modules that have no
  file on disk (`_ops.py`, `_classes.py` under torch ≥ 2). Wrap the call in `try/except`;
  a training run must not die in a convenience helper.
- `torch.set_default_tensor_type` is deprecated and, on a CPU-only build, raises for the
  CUDA type. Gate it.
- A scheduler step before an optimizer step warns and skips the first learning rate value.
  It is inherited from the baseline; do not "fix" it as part of a variant.

## Checkpoint discipline

- A pretrained checkpoint pins the **encoder input width**. Feeding a new feature into the
  encoder changes that width and makes every encoder weight unloadable — that is the one
  change that must not happen.
- New features belong in the **decoder context**. Widening the first decoder context layer
  costs exactly one non-loadable key; say so, and say why it is the smaller risk.
- Load with `load_state_dict(..., strict=False)` and **report the skipped keys**. A
  silently skipped key is a silently untrained layer.
- Resolve weights at run time, in this order: an explicit override (`--model-path` /
  environment variable), then the newest checkpoint the user produced, then the pretrained
  fallback. Never a hardcoded filename or epoch.

## Verification playbook

The executor runs five gates in order:

| gate | catches |
|---|---|
| `py_compile` | syntax errors, in every `.py` |
| `import_probe` | missing helpers, unresolvable imports, wrong interpreter |
| `correspondence` | file set, naming, first-line markers, parameter values, plotting styles |
| `smoke_run` | the acceptance contract on a tiny budget |
| `full_run` | the contract at the configured budget, plus the solution dump |

Reproduce one gate in isolation with `cvrp stage <name>` instead of re-running everything.

**What the gates do not cover, and what has actually bitten us:** they drive the inference
entry point against the shipped instances. A training-only path — the random instance
generator, `load_problems`, anything reached only from `train_*.py` — is **never
executed**, so a defect there reaches the user on their first training run (measured: an
`index out of bounds` in the instance generator, on the first batch). Before declaring a
deliverable done:

1. import the generated package and call its random-instance generator directly with a
   tiny batch — the smallest exercise of that path, and enough to catch index/shape faults;
2. assert the obvious invariants of what it returns (non-negative demands, lower bound not
   above upper bound, whatever the problem's own ordering requires);
3. only then trust the gates' verdict on everything else.

## Proven fixes

- **`RuntimeError: index N is out of bounds for dimension D with size N`**, typically on
  the first training batch; on CUDA it appears as `CUDA error: device-side assert
  triggered` reported at a *later, unrelated* line.
  *Root cause* — an identifier that is 1-based (built to index a depot-prefixed array) fed
  to a 0-based tensor. *Minimal change* — subtract 1 at that one call site, or keep the
  order 0-based and add 1 only where the depot-prefixed array is indexed.

## Anti-patterns

- **Relaxing a constraint to make the program run.** Weakening a mask, widening a bound or
  skipping a check converts a visible failure into a silent wrong answer, which is the
  expensive kind.
- **Trusting the frame a CUDA device-side assert points at.** The launch is asynchronous,
  so the reported line is arbitrary. Re-run on CPU for the real index error, or set
  `CUDA_LAUNCH_BLOCKING=1`.
- **Hardcoding a host property** — CUDA availability, an absolute path, a checkpoint
  filename — into the deliverable.
- **Reading "all gates passed" as "the program works."** They cover the inference path.

## Troubleshooting index

| symptom | look at |
|---|---|
| CUDA device-side assert | re-run on CPU for the real index error |
| crash on the first training batch | the random instance generator, not the model |
| unknown module name for a baseline symbol | the three `sys.path` entries |
| `FileNotFoundError` on a plotting style | the style directory contents vs the name in the entry point |
| `correspondence` gate failure | file set, first-line markers, parameter values |
| a weight that will not load | was the encoder input width changed? |

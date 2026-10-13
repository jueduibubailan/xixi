# Skill — cvrptw

> Scope: **problem-specific**. The cross-problem procedure — baseline adaptation rules, the
> environment and host, baseline hazards, checkpoint discipline, the gate order and what it
> misses, general proven fixes and anti-patterns — is in **`skills/_global_skill.md`** and is
> deliberately not repeated here. Read that one too.

## Contents

- `Baseline choice` — which reference implementation this problem adapts *(problem-specific)*
- `Deliverable specifics` — the file set and the parameters this problem's gates pin *(problem-specific)*
- `Pitfalls measured on this problem` — read before changing masks or the clock *(problem-specific)*
- `Troubleshooting index` — symptom → where to look, for this problem *(problem-specific)*

## Baseline choice

- The reference implementation is **POMO**, and specifically its CVRP solver: the variant
  changes the *environment* (an extra state, extra masks, a wider decoder context) and
  leaves the encoder alone so the pretrained weights still load.
- Adapt it; do not redesign it. The rollout, the REINFORCE update and the shared POMO
  baseline stay as the baseline wrote them — the variant lives in the environment.

## Deliverable specifics

- Entry points are `train_n{n}.py` / `test_n{n}.py`, following the baseline's own naming.
- `pomo_size == problem_size` is **required**: POMO's multi-start pins rollout `p` to start
  at customer `p + 1`, so the two sizes cannot differ.
- Checkpoints are a user-populated drop box. `test_n{n}.py` resolves weights at run time
  (`--model-path` / environment override, then the newest `checkpoint*.pt` the user
  produced, then the pretrained fallback) and the training entry point must **not** load a
  checkpoint at all: `model_load` keeps `enable: False` with `path`/`epoch` commented out.
- The plotting styles this problem ships are `style_cvrp_20.json` (objective) and
  `style_loss_1.json` (loss). `style_cvrp_100.json` is named by the baseline's own
  `train_n100.py` but **is not shipped** — copying that line raises `FileNotFoundError` at
  the first `img_save_interval`. See `notes/cvrptw_notes.md` for the framing of the bounds.

## Pitfalls measured on this problem

- The window generator must be exercised directly before the deliverable is called done:
  it is training-only, so no gate touches it, and a defect there reached the user's first
  training run (see `notes/cvrptw_notes.md` -> Traps, and `attempts/` for the run).
- The capacity mask uses the normalized convention (load starts at 1.0) while the checker
  works in raw units. The tolerance must be chosen so a route can never exceed capacity in
  the checker's units — do not copy the baseline's `load + eps < demand` verbatim.
- The 8-fold augmentation is applied to coordinates only. Time windows and service times
  must be **repeated**, never transformed: all eight transforms are isometries, so the
  windows derived from distances stay valid as they are.

## Troubleshooting index

| symptom | look at |
|---|---|
| `index out of bounds` on the first training batch | `CVRPTWProblemDef._get_time_windows` id base |
| a rollout with no legal action | the depot exemption is applied *after* the mask union |
| a feasible instance reported infeasible | `arrival >= due` written where the checker uses `>` |
| a weight that will not load | was the encoder input width changed? |
| `FileNotFoundError` on a plotting style | the style names shipped above |

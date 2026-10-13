# Notes — cvrptw

> Scope: **problem-specific**. Facts about CVRPTW itself. The cross-problem procedure is in
> `skills/_global_skill.md` and is not repeated here.

## Contents

- `Identity` — what the tag covers, and which facts are scale-dependent *(problem-specific)*
- `Semantics` — how time advances, what "travel time" is, what the units mean *(problem-specific)*
- `Constraints → masking` — each constraint next to the mask that enforces it *(problem-specific)*
- `Feasibility checklist` — what a checker must verify, in the problem's own terms *(problem-specific)*
- `Traps` — read before touching a mask or the clock *(problem-specific)*
- `Open questions` — undecided semantics and unmeasured risks *(problem-specific)*

## Identity

- problem tag: `cvrptw`; `n` is per task (20 / 50 / 100), instance arrays are `n + 1` long.
- one problem serves several scales, so procedure lives in `skills/` and scale-specific
  facts belong in the attempt that measured them.

## Semantics

- travel time **is** Euclidean distance; there is no vehicle speed and no time matrix.
- `arrival = current_time + dist(current_node, candidate)`.
- `current_time = max(arrival, ready) + service`; the depot has service 0 and selecting it
  resets the clock.
- lateness (`arrival > due`) is masked, never repaired afterwards. The comparison is
  **strict**: `arrival == due` is legal.
- the depot window is `[0, horizon]`; `horizon` scales the generator's canonical schedule
  end (`HORIZON_SCALER = 1.5`), so horizon is a *derived* quantity, not a constant.
- normalized capacity convention: the environment's load starts at `1.0` and the loader
  divides `node_demand` by the instance's own capacity; the checker works in raw units.
- measured on the shipped n=100 instances: `min(due - dist(depot, j)) = 0.496` and no
  customer's direct depot trip leaves the horizon, so every customer is reachable from the
  depot and a depot return can always be followed by serving anything remaining.

## Constraints → masking

| id | constraint | enforced by |
|---|---|---|
| C1 | every customer served exactly once | `visited_mask` |
| C2 | route load ≤ vehicle capacity (normalized: ≤ 1.0) | `capacity_mask` |
| C3 | `arrival ≤ due`; early arrival is legal and waits | `time_window_mask` |
| C4 | service completion ≤ `horizon` | `horizon_mask` |
| C5 | every route starts and ends at the depot | `finished_termination_rule` + the depot being always selectable |

Composition order inside `step()`: `visited` → `capacity` → `time_window` → `horizon` →
**force index 0 (the depot) back to 0** → existing `finished` reopen last (so termination
still wins). The forced reopen is what makes the state deadlock-free: a customer whose
window has closed can never make the state unsolvable, because returning to the depot is
always legal and resets the clock.

`pomo_start_moves_bypass_mask`: the first two selections of every rollout are forced by the
baseline (`selected_count` 0 → depot, 1 → customer `p + 1`) and do **not** consult the mask.
That is POMO's multi-start design, and it is safe on the shipped data, but the run path must
re-validate the final solution before dumping it.

## Feasibility checklist

1. every customer id `1..n` appears exactly once across the union of all routes (no
   duplicates, none missing);
2. every route is a non-empty list whose first and last element is 0 and which contains no
   other 0;
3. for each route, the sum of the raw demands of its customers is ≤ capacity (the checker's
   tolerance is `1e-6` in raw units; the mask's margin is chosen to satisfy it);
4. replaying a route from node 0 with `now = 0`: for each customer in order,
   `now += euclid(previous, node)` — a violation if `now > due`; then
   `now = max(now, ready) + service`;
5. the service completion at the last customer of every route is ≤ `horizon`;
6. the cost in the dump equals the recomputed sum of Euclidean leg lengths.

## Traps

### `arrival > due` is strict, and `>=` is a different problem

The late-arrival test is a **strict** comparison: arriving exactly at `due` is feasible. A
mask written as `arrival >= due` silently forbids the boundary and makes the generated
solver miss feasible solutions the shipped instances actually contain. Check the sign when
comparing a mask against the checker — they must agree, and the checker uses `>`.

### An action marked "always legal" must actually never be masked

The depot is exempt from the time-window and horizon masks and is forced back to 0 after the
constraints are unioned. Drop that line and any state whose clock has passed a horizon has
**no legal action at all** — the rollout cannot continue, and the failure appears far from
the cause. Whenever a mask rule declares an exception, verify the code applies it *after*
the union, not before.

### A 1-based index used against a 0-based tensor crashes the first training batch

`_get_time_windows` in the generated `CVRPTWProblemDef.py` builds
`canonical_order = argsort(rand) + 1` — 1-based, because it is also used to index
`depot_node_xy` (`problem_size + 1` rows) and to scatter into `arrivals`. It then fed the
same 1-based values to `node_demand.gather(...)`, and `node_demand` has only
`problem_size` rows. Result: `index 20 is out of bounds for dimension 1 with size 20`.

- On **CUDA** this surfaces as `torch.AcceleratorError: CUDA error: device-side assert
  triggered` reported at a *later, unrelated* line (`_travel_time`), because the launch is
  asynchronous. Do not trust the reported frame for a device-side assert; re-run on CPU to
  get the real index error, or set `CUDA_LAUNCH_BLOCKING=1`.
- The fix is one line: gather with `(selected - 1)`, or keep the order 0-based and add 1
  only at the point of indexing `depot_node_xy`.
- Measured on the shipped n=100 instances and on the (patched) generator, the
  depot-reachability invariant holds: `min(due - dist(depot, j)) > 0` for all customers.

### A green gate sequence says nothing about the training path

The executor exercises `run.py` (inference) against the shipped instances. The training
entry point is never invoked, so `load_problems -> get_random_problems` — the code that
builds windows for random training instances — is **unverified** by the pipeline. A defect
there reaches the user on their first `train_n*.py` invocation.

### CUDA availability is a property of the host, not of the deliverable

The generated entry point read `USE_CUDA = torch.cuda.is_available()`. On the pipeline's
box that is False (CPU-only torch); on the user's box it is True. Both are legitimate; the
consequence is that a GPU-only defect can hide behind a green CPU run, and a CPU-only
assumption can break on a GPU box. State which host a run was verified on.

## Open questions

- Whether the training path should be smoke-tested with a tiny `train_episodes` budget as
  part of the executor's gates (see `skills/cvrptw_skill.md` -> Verification playbook).
- Whether the generator's window construction should be validated against the shipped
  instances' distribution (widths, horizon ratio), not only for internal consistency.

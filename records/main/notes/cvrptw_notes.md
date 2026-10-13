# Notes — cvrptw

## Identity

- problem tag: `cvrptw`; `n` is per task (20 / 50 / 100), instance arrays are `n + 1` long.
- one problem serves several scales, so procedure lives in `skills/` and scale-specific
  facts belong in the attempt that measured them.

## Semantics (as implemented by the generated package for n=100)

- travel time **is** Euclidean distance; there is no vehicle speed and no time matrix.
- `arrival = current_time + dist(current_node, candidate)`.
- `current_time = max(arrival, ready) + service`; the depot has service 0 and selecting it
  resets the clock.
- lateness (`arrival > due`) is masked, never repaired afterwards.
- the depot window is `[0, horizon]`; `horizon` scales the generator's canonical schedule
  end (`HORIZON_SCALER = 1.5`), so horizon is a *derived* quantity, not a constant.
- normalized capacity convention: the environment's load starts at `1.0` and the loader
  divides `node_demand` by the instance's own capacity; the checker works in raw units.

## Traps

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

# Available inputs in this worktree

This problem is CVRPLTW at **n = 100** on **MVMoE's own instances**, so that a result here can
be compared with MVMoE's published numbers. The instance set is `grader/data/`'s
`vrpltw100_uniform.pkl` converted to JSON: 1000 instances, unchanged in value. Do not
regenerate them differently -- same distribution is not the same thing as the same data.

- `data/instances/n100/*.json` -- the evaluation instances, read-only
- `artifacts/analysis/analysis.json` -- the contract S1 produced; treat it as binding
- `artifacts/generated/` -- the deliverable you write
- `artifacts/runs/` -- the executor's report, written after each attempt

## What an instance contains

```json
{"name": "...", "problem_size": 100,
 "depot_xy": [[x, y]],
 "node_xy": [[x, y], ... 50 entries],
 "node_demand": [1..9, ... 50],
 "capacity": 50.0,
 "route_limit": 3.0,
 "service_times": [0.2, ... 50],
 "time_windows": [[start, end], ... 50],
 "horizon": 3.0}
```

Node index `0` is the depot; customers are `1..50`, and `node_demand[k]` belongs to customer
`k + 1`. Travel time equals Euclidean distance (speed 1), so "arrival" and "distance" are the
same quantity up to the clock carried along a route.

## The three numbers that pin MVMoE's convention

| value | meaning |
|---|---|
| capacity **50** | demand is `randint(1, 10)` -> 1..9, so a route holds several customers |
| route limit **3.0** | total Euclidean length of a route, depot legs included |
| service time **0.2** | added at every customer after the wait |

The depot window is `[0, 3.0]`. Because speed is 1, "return by 3.0" and "length <= 3.0" are
tightly coupled but not identical: waiting at a customer consumes the clock without adding
length, so a route can be short and still late, or long and still early.

## The contract

The program must print

```
the initial process is successful, the initial cost is <float>
the process is successful, the best cost is <float>
```

and, with `--save-solution <file>`, write `{"routes": [[0, ...], ...], "cost": <float>}`.
Every route starts and ends at `0`.

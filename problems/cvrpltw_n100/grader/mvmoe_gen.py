"""MVMoE-compatible CVRPLTW instance generation (n = 100).

Ported from `Routing-MVMoE-main/envs/VRPLTWEnv.py`:

    get_random_problems(batch_size, problem_size, normalized=True)      # lines 356-411
    __init__: self.depot_start, self.depot_end = 0., 3.                 # line 83
              self.speed = 1.0                                          # line 82

The generated distribution is the point of this bundle: it is what the published MVMoE
numbers were measured on, so it is reproduced here rather than approximated. What is *not*
ported is their model -- the network stays POMO.

Their conventions, all of which this module keeps:

  * depot and customers uniform in [0, 1]^2;
  * vehicle capacity 50 at n = 100 (`demand_scaler`), demand `randint(1, 10)` -> 1..9;
  * a **constant** route limit of 3.0 (the "L" of CVRPLTW);
  * a **constant** service time of 0.2;
  * depot window [0, 3.0];
  * time windows from "Learning to Delegate for Large-scale Vehicle Routing" (NeurIPS 2021):
        travel     = ||node - depot||            (speed 1, so this is also time)
        a, b       = travel, 3.0 - travel - 0.2
        center     = (a - b) * rand + b
        half_width = (0.2 / 2 - 3.0 / 3) * rand + 3.0 / 3      = 1.0 - 0.9 * rand
        start, end = clamp(center -/+ half_width, 0, 3.0)
  * instances that violate "depot -> node -> depot must fit in the working day" are
    rejected and redrawn. MVMoE redraws the whole batch; redrawing one instance at a time is
    the same distribution and keeps the seed meaningful per instance.
"""

from __future__ import annotations

import json
import math
import pickle
import random
from pathlib import Path
from typing import Any

#: MVMoE's constants for n = 100 (`envs/VRPLTWEnv.py`).
DEPOT_START = 0.0
DEPOT_END = 3.0
SERVICE_TIME = 0.2
CAPACITY = 50.0
ROUTE_LIMIT = 3.0
SPEED = 1.0
PROBLEM_SIZE = 100

#: `demand_scaler` by problem size in their code; only n=100 is used here, kept for clarity.
DEMAND_SCALER = {20: 30.0, 50: 40.0, 100: 50.0, 200: 70.0}

ROUND_EPS = 1e-5


def _travel(depot: tuple[float, float], node: tuple[float, float]) -> float:
    """Euclidean distance divided by the (unit) speed: both distance and time."""
    return math.dist(depot, node) / SPEED


def generate_instance(seed: int, size: int = PROBLEM_SIZE) -> dict[str, Any]:
    """One instance, drawn the way MVMoE draws it.

    Retries with `seed + 1` when the draw is infeasible, mirroring their regenerate-on-
    invalid loop. That loop is not a detail: an instance whose windows cannot even be
    served by a dedicated round trip would be unsolvable, and the paper's numbers never
    include one.
    """
    attempt = 0
    while True:
        instance = _draw(seed + attempt, size)
        if _round_trip_feasible(instance):
            instance["seed"] = seed
            return instance
        attempt += 1
        if attempt > 10_000:  # pragma: no cover - a broken formula, not a data problem
            raise RuntimeError(f"could not draw a feasible instance from seed {seed}")


def _draw(seed: int, size: int) -> dict[str, Any]:
    rng = random.Random(seed)
    depot = (rng.random(), rng.random())
    nodes = [(rng.random(), rng.random()) for _ in range(size)]

    service = SERVICE_TIME
    starts: list[float] = []
    ends: list[float] = []
    for node in nodes:
        travel = _travel(depot, node)
        a = DEPOT_START + travel
        b = DEPOT_END - travel - service
        center = (a - b) * rng.random() + b
        half = (service / 2 - DEPOT_END / 3) * rng.random() + DEPOT_END / 3
        starts.append(min(max(center - half, DEPOT_START), DEPOT_END))
        ends.append(min(max(center + half, DEPOT_START), DEPOT_END))

    demand = [float(rng.randint(1, 9)) for _ in range(size)]
    return {
        "name": f"cvrpltw100_{seed:05d}",
        "problem_size": size,
        "depot_xy": [list(depot)],
        "node_xy": [list(node) for node in nodes],
        "node_demand": demand,
        "capacity": CAPACITY,
        "route_limit": ROUTE_LIMIT,
        "service_times": [service] * size,
        "time_windows": [[start, end] for start, end in zip(starts, ends)],
        "horizon": DEPOT_END,
        "best_known_cost": None,
        "meta": {
            "seed": seed,
            "source": "mvmoe-formula",
            "generator": "grader/mvmoe_gen.py",
            "speed": SPEED,
            "semantics": "travel time == euclidean distance; early arrival waits; late arrival infeasible",
        },
    }


def _round_trip_feasible(instance: dict[str, Any]) -> bool:
    """MVMoE's own validity test: depot -> node -> depot must fit inside [0, depot_end].

    Their line 398:
        max(0 + travel, tw_start) + service + travel > depot_end  -> invalid
    """
    depot = tuple(instance["depot_xy"][0])
    for node_xy, window, service in zip(
        instance["node_xy"], instance["time_windows"], instance["service_times"]
    ):
        travel = _travel(depot, tuple(node_xy))
        arrive = max(DEPOT_START + travel, window[0])
        if arrive + service + travel > DEPOT_END + ROUND_EPS:
            return False
    return True


# --------------------------------------------------------------------------------------
# MVMoE's shipped test set
# --------------------------------------------------------------------------------------


def load_mvmoe_pkl(path: str | Path) -> list[dict[str, Any]]:
    """Convert MVMoE's `vrpltw100_uniform.pkl` into this bundle's instance dicts.

    The file is a plain pickle (not a torch checkpoint) holding a list of 8-tuples:
    `(depot_xy, node_xy, node_demand, capacity, route_limit, service_time, tw_start, tw_end)`
    with `depot_xy` shaped `[[x, y]]`, the rest per-customer lists. Reading it directly is
    what makes an evaluation here comparable with the published numbers: same 1000
    instances, not merely the same distribution.
    """
    with Path(path).open("rb") as handle:
        raw = pickle.load(handle, encoding="latin1")

    instances: list[dict[str, Any]] = []
    for index, record in enumerate(raw):
        depot_xy, node_xy, demand, capacity, route_limit, service, tw_start, tw_end = record
        instances.append(
            {
                "name": f"vrpltw100_{index:05d}",
                "problem_size": len(node_xy),
                "depot_xy": [[float(depot_xy[0][0]), float(depot_xy[0][1])]],
                "node_xy": [[float(x), float(y)] for x, y in node_xy],
                "node_demand": [float(value) for value in demand],
                "capacity": float(capacity),
                "route_limit": float(route_limit),
                "service_times": [float(value) for value in service],
                "time_windows": [
                    [float(start), float(end)] for start, end in zip(tw_start, tw_end)
                ],
                "horizon": float(DEPOT_END),
                "best_known_cost": None,
                "meta": {
                    "seed": index,
                    "source": "mvmoe-pkl",
                    "generator": "grader/data/vrpltw100_uniform.pkl",
                    "semantics": "travel time == euclidean distance; early arrival waits; late arrival infeasible",
                },
            }
        )
    return instances


# --------------------------------------------------------------------------------------
# the dataset the engine builds
# --------------------------------------------------------------------------------------


def build_dataset(out_dir: str | Path, spec: dict[str, Any], force: bool = False) -> list[Any]:
    """Write the evaluation instances. Called once, before any agent runs.

    `data.source` picks where they come from:

      * `mvmoe`   -- convert the shipped pkl (default, and the reason this bundle exists);
      * `generate`-- draw them with the formulas above, for when the pkl is not shipped.

    Either way the files are cached behind `manifest.json`: a rerun is cheap and, more
    importantly, the exact instances a result was measured on stay on disk.
    """
    out_dir = Path(out_dir)
    params = dict(spec.get("data") or {})
    size = int((params.get("sizes") or [PROBLEM_SIZE])[0])
    count = int(params.get("count_per_size") or 1000)
    seed = int(params.get("seed") or 3333)
    source = str(params.get("source") or "mvmoe")

    manifest_path = out_dir / "manifest.json"
    if not force and manifest_path.is_file():
        cached = _read_json(manifest_path) or {}
        if len(cached.get("instances") or []) >= count:
            return list(cached.get("instances") or [])

    pkl_path = _resolve(Path(__file__).resolve().parent.parent, str(params.get("pkl") or ""))
    if source == "mvmoe" and pkl_path is not None and pkl_path.is_file():
        instances = load_mvmoe_pkl(pkl_path)[:count]
    else:
        instances = [generate_instance(seed + offset, size) for offset in range(count)]
        if source == "mvmoe":
            # Loud, because silently generating a lookalike set would make the comparison
            # meaningless while still producing plausible numbers.
            print(
                f"[cvrpltw_n100] WARNING: {pkl_path} not found; generated {count} instances "
                f"from the same formulas instead. Results are NOT comparable to MVMoE."
            )

    folder = out_dir / f"n{size}"
    folder.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for instance in instances:
        path = folder / f"{instance['name']}.json"
        path.write_text(json.dumps(instance, indent=2), encoding="utf-8")
        entries.append(
            {
                # `json` is the key the engine reads when it decides whether a task's data
                # is ready (`ProblemPlugin.data_ready`); writing only `path` makes a
                # fully-populated data directory look empty.
                "json": str(path.relative_to(out_dir)).replace("\\", "/"),
                "path": str(path.relative_to(out_dir)).replace("\\", "/"),
                "problem_size": instance["problem_size"],
                "name": instance["name"],
            }
        )
    manifest_path.write_text(
        json.dumps(
            {
                "problem": "cvrpltw_n100",
                "source": "mvmoe-pkl" if (source == "mvmoe" and pkl_path and pkl_path.is_file()) else "generated",
                "instances": entries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return entries


def _resolve(base: Path, raw: str) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

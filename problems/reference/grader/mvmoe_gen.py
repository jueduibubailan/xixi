"""MVMoE's CVRPLTW instances: read the shipped pkl, or generate them with MVMoE's own code.

Why this module is thin: the generation **must** be MVMoE's, not a lookalike. The evaluation
set is what the published numbers were measured on, so either it is their shipped
`vrpltw50_uniform.pkl`, or it is produced by their `get_random_problems` running from
`mvmoe/VRPLTWEnv.py`. There is no third path -- an earlier pure-Python port was removed
because it redrew per instance where upstream redraws the whole batch, which changes the RNG
stream and therefore the instances.

Both paths end in :func:`load_mvmoe_pkl`, so the JSON the executor reads has one shape.
The pkl path needs only the standard library; the generator path needs `torch`.
"""

from __future__ import annotations

import importlib.util
import json
import pickle
import random
from pathlib import Path
from typing import Any

#: Upstream defaults, kept so a generated instance and a shipped one carry the same metadata.
PROBLEM_SIZE = 50
DEPOT_START = 0.0
DEPOT_END = 3.0
SERVICE_TIME = 0.2
CAPACITY = 40.0
ROUTE_LIMIT = 3.0
SPEED = 1.0

#: The vendored upstream files (see `mvmoe/README.md`).
UPSTREAM_DIR = Path(__file__).resolve().parent / "mvmoe"

__all__ = [
    "build_dataset",
    "generate_with_mvmoe",
    "load_mvmoe_pkl",
    "seed_everything",
    "PROBLEM_SIZE",
    "DEPOT_END",
]


# --------------------------------------------------------------------------------------
# upstream, loaded as a file (no package __init__, no torch import at module import time)
# --------------------------------------------------------------------------------------


def _load_upstream_env_class() -> Any:
    """Import `mvmoe/VRPLTWEnv.py` by path and return its `VRPLTWEnv` class."""
    path = UPSTREAM_DIR / "VRPLTWEnv.py"
    if not path.is_file():
        raise ImportError(
            "the vendored MVMoE env is missing: {}. Restore it from the upstream project or "
            "use `data.source: mvmoe` with the shipped pkl.".format(path)
        )
    module_name = "cvrpltw{}_mvmoe_VRPLTWEnv".format(PROBLEM_SIZE)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VRPLTWEnv


def seed_everything(seed: int) -> None:
    """The seeding order `mvmoe/utils.py:seed_everything` uses (L84-91)."""
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def generate_with_mvmoe(
    count: int,
    size: int = PROBLEM_SIZE,
    seed: int = 3333,
    work_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run upstream `generate_dataset` and read the result back.

    This mirrors `mvmoe/generate_data.py`: seed, build the env, let *its* `generate_dataset`
    draw (including its whole-batch redraw of infeasible instances), then convert the pkl.
    The one deliberate deviation is the CPU pin: upstream switches to CUDA tensors when a GPU
    is present, and CPU/CUDA draws do not share an RNG stream -- the same seed would produce
    different instances on different machines.
    """
    import torch

    env_class = _load_upstream_env_class()
    seed_everything(seed)
    # Deliberately *not* calling `torch.set_default_tensor_type('torch.cuda.FloatTensor')`
    # the way upstream's `generate_data.py` does: that switches the draw to CUDA when a GPU
    # is present, and CPU/CUDA do not share an RNG stream. Leaving the default alone keeps
    # the draw on CPU float32, which is what makes a seed reproducible across machines.

    base = Path(work_dir) if work_dir is not None else Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    tmp_pkl = base / ".mvmoe-n{}-seed{}.pkl".format(size, seed)

    env = env_class(problem_size=size, pomo_size=size, device=torch.device("cpu"))
    try:
        env.generate_dataset(count, size, str(tmp_pkl))
        return load_mvmoe_pkl(tmp_pkl, generator="mvmoe/generate_data.py")
    finally:
        try:
            tmp_pkl.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------------------
# the one instance shape both paths produce
# --------------------------------------------------------------------------------------


def load_mvmoe_pkl(
    path: str | Path,
    generator: str = "grader/data/vrpltw50_uniform.pkl",
) -> list[dict[str, Any]]:
    """Convert MVMoE's dataset pkl into this bundle's instance dicts.

    The file is a plain pickle (not a torch checkpoint) holding a list of 8-tuples:
    `(depot_xy, node_xy, node_demand, capacity, route_limit, service_time, tw_start, tw_end)`
    with `depot_xy` shaped `[[x, y]]`, the rest per-customer lists. Reading it directly is
    what makes an evaluation here comparable with the published numbers: the same instances,
    not merely the same distribution.
    """
    with Path(path).open("rb") as handle:
        raw = pickle.load(handle, encoding="latin1")

    instances: list[dict[str, Any]] = []
    for index, record in enumerate(raw):
        depot_xy, node_xy, demand, capacity, route_limit, service, tw_start, tw_end = record
        size = len(node_xy)
        instances.append(
            {
                "name": "vrpltw{}_{:05d}".format(size, index),
                "problem_size": size,
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
                    "source": "mvmoe-pkl" if generator.endswith(".pkl") else "mvmoe-upstream",
                    "generator": generator,
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

      * `mvmoe`    -- the shipped pkl (default), or upstream's generator when it is absent;
      * `generate` -- upstream's generator, always.

    Either way the files are cached behind `manifest.json`: a rerun is cheap and, more
    importantly, the exact instances a result was measured on stay on disk.
    """
    out_dir = Path(out_dir)
    params = dict(spec.get("data") or {})
    size = int((params.get("sizes") or [PROBLEM_SIZE])[0])
    count = int(params.get("count_per_size") or 1000)
    seed = int(params.get("seed") or 3333)
    source = str(params.get("source") or "mvmoe")

    pkl_path = _resolve(Path(__file__).resolve().parent.parent, str(params.get("pkl") or ""))
    use_pkl = source == "mvmoe" and pkl_path is not None and pkl_path.is_file()
    generator = "mvmoe-pkl" if use_pkl else "mvmoe-upstream"

    manifest_path = out_dir / "manifest.json"
    if not force and manifest_path.is_file():
        cached = _read_json(manifest_path) or {}
        same_generator = str(cached.get("generator") or "mvmoe-pkl") == generator
        if same_generator and len(cached.get("instances") or []) >= count:
            return list(cached.get("instances") or [])

    if use_pkl:
        instances = load_mvmoe_pkl(pkl_path)[:count]
    else:
        if source == "mvmoe":
            # Loud, because a missing pkl means this run is no longer the published set.
            print(
                "[cvrpltw_n50] WARNING: {} not found; generating {} instances with MVMoE's "
                "own code (same procedure, different draws).".format(pkl_path, count)
            )
        instances = generate_with_mvmoe(count, size, seed, work_dir=out_dir / ".mvmoe-tmp")

    folder = out_dir / "n{}".format(size)
    folder.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for instance in instances:
        path = folder / "{}.json".format(instance["name"])
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
                "problem": "cvrpltw_n50",
                "source": "mvmoe-pkl" if use_pkl else "mvmoe-upstream",
                "generator": generator,
                "device": "cpu",
                "instances": entries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return entries


def _resolve(base: Path, raw: str) -> Path | None:
    """Resolve `data.pkl`, which is relative to the *problem* directory."""
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

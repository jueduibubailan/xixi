"""The `cvrpltw_n100` bundle must stay a faithful port of MVMoE's instances.

Three promises are worth guarding, because a silent drift in any of them would make a
comparison against MVMoE's published numbers meaningless while still producing plausible
output:

* the evaluation set really is MVMoE's 1000 instances, with their capacity, route limit and
  service time -- not a lookalike;
* the fallback generator draws from the same formulas (`envs/VRPLTWEnv.py`), including the
  depot-to-customer-to-depot validity test;
* the checker enforces all three constraints of the variant: capacity, the route length
  limit, and the time windows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import PACKAGE_PROBLEMS
from vrpar.problems import load_problem

BUNDLE = PACKAGE_PROBLEMS / "cvrpltw_n100"


@pytest.fixture(scope="module")
def plugin():
    return load_problem(BUNDLE, slug="cvrpltw_n100")


@pytest.fixture(scope="module")
def grader(plugin):
    return plugin.checks_module


@pytest.fixture(scope="module")
def mvmoe(plugin):
    """The bundle's generator, loaded by `grader.py` under a bundle-unique name.

    Reached through the plugin rather than `sys.modules`: both bundles ship a file called
    `mvmoe_gen.py`, and a shared entry made whichever loaded second win.
    """
    return plugin.checks_module._mvmoe


def _instance(**overrides) -> dict:
    """A tiny hand-made instance so the checker can be probed one constraint at a time."""
    instance = {
        "name": "probe",
        "problem_size": 3,
        "depot_xy": [[0.0, 0.0]],
        "node_xy": [[0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
        "node_demand": [1.0, 1.0, 1.0],
        "capacity": 50.0,
        "route_limit": 3.0,
        "service_times": [0.2, 0.2, 0.2],
        "time_windows": [[0.0, 3.0], [0.0, 3.0], [0.0, 3.0]],
        "horizon": 3.0,
    }
    instance.update(overrides)
    return instance


def test_the_shipped_test_set_is_mvmoes(mvmoe):
    """1000 instances, n=50, and MVMoE's three constants -- read from the shipped pkl."""
    instances = mvmoe.load_mvmoe_pkl(BUNDLE / "grader/data/vrpltw100_uniform.pkl")
    assert len(instances) == 1000
    first = instances[0]
    assert first["problem_size"] == 100
    assert first["capacity"] == pytest.approx(50.0)
    assert first["route_limit"] == pytest.approx(3.0)
    assert all(value == pytest.approx(0.2) for value in first["service_times"])
    # the first record's depot, straight out of the pkl
    assert first["depot_xy"][0][0] == pytest.approx(0.16739286482334137)


def test_the_built_dataset_comes_from_the_pkl(scratch: Path):
    """Built through the config, because the size (`{n}` in `data.sizes`) comes from it."""
    from vrpar.config import load_config

    problem = load_config(BUNDLE / "task.yaml")["_problem"]
    entries = problem.build_dataset(scratch / "instances", force=True)
    assert len(entries) == 1000
    assert entries[0]["path"].startswith("n100/")
    manifest = json.loads((scratch / "instances" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "mvmoe-pkl"


def test_the_fallback_generator_keeps_mvmoes_constants(mvmoe):
    instance = mvmoe.generate_instance(seed=7)
    assert instance["problem_size"] == 100
    assert instance["capacity"] == pytest.approx(50.0)
    assert instance["route_limit"] == pytest.approx(3.0)
    assert all(value == pytest.approx(0.2) for value in instance["service_times"])
    # every generated instance passes MVMoE's own validity test
    assert mvmoe._round_trip_feasible(instance)


def test_generation_is_deterministic(mvmoe):
    assert mvmoe.generate_instance(seed=11) == mvmoe.generate_instance(seed=11)


def test_the_checker_accepts_a_feasible_solution(grader):
    instance = _instance()
    routes = [[0, 1, 2, 3, 0]]
    verdict = grader.check_solution(instance, routes)
    assert verdict["feasible"], verdict["violations"]
    assert verdict["checks"]["route_length_respected"]
    assert verdict["cost"] == pytest.approx(0.6)


def test_the_checker_rejects_exceeding_capacity(grader):
    instance = _instance(node_demand=[40.0, 40.0, 40.0])
    verdict = grader.check_solution(instance, [[0, 1, 2, 3, 0]])
    assert not verdict["feasible"]
    assert any("capacity" in item for item in verdict["violations"])


def test_the_checker_rejects_exceeding_the_route_limit(grader):
    """The limit is 3.0 and the depot legs count: a route that ends far out is illegal."""
    instance = _instance(node_xy=[[2.0, 0.0], [2.6, 0.0], [2.9, 0.0]])
    verdict = grader.check_solution(instance, [[0, 1, 2, 3, 0]])
    assert not verdict["feasible"]
    assert any("route_limit" in item for item in verdict["violations"])


def test_the_checker_rejects_a_late_arrival(grader):
    instance = _instance(time_windows=[[0.0, 0.05], [0.0, 3.0], [0.0, 3.0]])
    verdict = grader.check_solution(instance, [[0, 1, 2, 3, 0]])
    assert not verdict["feasible"]
    assert any("arrival" in item for item in verdict["violations"])


def test_the_checker_rejects_a_duplicate_and_a_missing_customer(grader):
    verdict = grader.check_solution(_instance(), [[0, 1, 1, 0], [0, 2, 0]])
    assert not verdict["feasible"]
    assert any("more than once" in item for item in verdict["violations"])


def test_the_mock_solver_produces_a_verifiable_solution(scratch: Path, plugin, grader, mvmoe):
    """End to end on a real MVMoE instance: the shipped heuristic must pass the checker."""
    import subprocess
    import sys

    instances = mvmoe.load_mvmoe_pkl(BUNDLE / "grader/data/vrpltw100_uniform.pkl")
    instance_path = scratch / "i.json"
    solution_path = scratch / "s.json"
    instance_path.write_text(json.dumps(instances[0]), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(BUNDLE / "mock_solver.py"),
            "--path",
            str(instance_path),
            "--iteration",
            "1",
            "--save-solution",
            str(solution_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    import re

    program = plugin.spec["program"]
    assert re.search(program["initial_regex"], proc.stdout), proc.stdout
    assert re.search(program["best_regex"], proc.stdout), proc.stdout

    verdict = grader.verify(instance_path, solution_path, plugin.spec)
    assert verdict["feasible"], verdict["violations"][:5]


def test_the_bundle_declares_n100_and_shares_the_pomo_seed(plugin):
    """The scale lives in the task file, so read it through the config, not the plugin.

    `load_problem` alone has no task file and falls back to n=20 -- the trap the `scale:`
    block in `task.yaml` exists to close.
    """
    from vrpar.config import load_config

    config = load_config(BUNDLE / "task.yaml")
    assert config["_problem"].problem_size == 100
    assert config["scale"]["problem_size"] == 100
    assert plugin.spec["tag"] == "CVRPLTW100"
    assert plugin.spec["data"]["source"] == "mvmoe"
    # POMO lives once at the project root and every problem shares it, so this
    # bundle carries no POMO and no checkpoint of its own
    assert plugin.spec["resources"]["baseline_dir"] == "baseline"

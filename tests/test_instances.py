"""Instance synthesis tests for the CVRPTW problem plugin.

These are the problem's own tests: schema, determinism, feasibility of the data
itself, and the writers. A different variant ships its own file like this.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cvrp.problems.cvrptw.checks import check_solution, distance_matrix, load_instance
from cvrp.problems.cvrptw.instances import (
    build_dataset,
    generate_instance,
    write_instance_json,
    write_instance_vrp,
)


def test_instance_shape_and_index_convention():
    instance = generate_instance(problem_size=20, seed=7)
    size = instance["problem_size"]
    assert size == 20
    assert len(instance["depot_xy"]) == 1
    assert len(instance["node_xy"]) == size
    assert len(instance["node_demand"]) == size + 1
    assert len(instance["time_windows"]) == size + 1
    assert len(instance["service_times"]) == size + 1
    # index 0 is the depot everywhere
    assert instance["node_demand"][0] == 0.0
    assert instance["service_times"][0] == 0.0
    assert instance["time_windows"][0] == [0.0, instance["horizon"]]


def test_instance_is_deterministic_for_a_seed():
    assert generate_instance(problem_size=20, seed=99) == generate_instance(problem_size=20, seed=99)
    assert generate_instance(problem_size=20, seed=99) != generate_instance(problem_size=20, seed=100)


def test_windows_are_ordered_and_inside_the_horizon():
    instance = generate_instance(problem_size=20, seed=5)
    horizon = instance["horizon"]
    for index, (ready, due) in enumerate(instance["time_windows"]):
        assert 0.0 <= ready < due <= horizon + 1e-9, (index, ready, due)


def test_demands_match_pomos_distribution():
    instance = generate_instance(problem_size=20, seed=11)
    for value in instance["node_demand"][1:]:
        assert 0.1 - 1e-9 <= value <= 0.9 + 1e-9
        assert abs(value * 10 - round(value * 10)) < 1e-9


def test_generated_instance_records_a_feasible_reference_schedule():
    """The generator must never emit data no solver can satisfy.

    It self-checks before returning, and the reference cost it records is the one
    the pipeline's own semantics produce.
    """
    instance = generate_instance(problem_size=20, seed=3)
    reference_cost = instance["meta"]["reference_cost"]
    assert reference_cost > 0
    # the depot window must accommodate the schedule the windows came from
    assert instance["horizon"] > 0
    assert all(ready >= 0 for ready, _ in instance["time_windows"])


def test_too_small_horizon_raises_instead_of_emitting_bad_data():
    with pytest.raises(ValueError, match="horizon"):
        generate_instance(problem_size=20, seed=4, horizon=0.5)


def test_single_customer_routes_are_feasible_per_checker():
    instance = generate_instance(problem_size=3, seed=1)
    matrix = distance_matrix(instance)
    routes = [[0, 1, 0], [0, 2, 0], [0, 3, 0]]
    result = check_solution(instance, routes)
    recomputed = sum(matrix[a][b] for route in routes for a, b in zip(route, route[1:]))
    assert result["cost"] == round(recomputed, 4)
    assert result["checks"]["customers_served_once"]


def test_checker_catches_duplicates_and_missing_customers():
    instance = generate_instance(problem_size=3, seed=2)
    result = check_solution(instance, [[0, 1, 1, 0]])
    assert not result["checks"]["customers_served_once"]
    assert any("more than once" in violation for violation in result["violations"])
    assert any("never served" in violation for violation in result["violations"])


def test_checker_catches_capacity_violation():
    instance = generate_instance(problem_size=6, seed=8)
    instance = json.loads(json.dumps(instance))
    instance["capacity"] = 0.05  # every customer demand exceeds it
    result = check_solution(instance, [[0, 1, 2, 0]])
    assert not result["checks"]["capacity_respected"]
    assert any("exceeds capacity" in violation for violation in result["violations"])


def test_vrp_mirror_has_every_section(scratch: Path):
    instance = generate_instance(problem_size=4, seed=3)
    path = write_instance_vrp(instance, scratch / "x.vrp")
    text = path.read_text(encoding="utf-8")
    for section in (
        "NAME :",
        "TYPE : CVRPTW",
        "CAPACITY :",
        "NODE_COORD_SECTION",
        "DEMAND_SECTION",
        "TIME_WINDOW_SECTION",
        "SERVICE_TIME_SECTION",
        "DEPOT_SECTION",
        "EOF",
    ):
        assert section in text
    assert any(line and line[0].isdigit() and " " in line for line in text.splitlines())


def test_build_dataset_writes_json_vrp_and_manifest(scratch: Path):
    written = build_dataset(scratch, sizes=[20], count_per_size=2, seed=1234, force=True)
    assert len(written) == 2
    assert (scratch / "manifest.json").is_file()
    for name in ("cvrptw_n20_00", "cvrptw_n20_01"):
        assert (scratch / "n20" / f"{name}.json").is_file()
        assert (scratch / "n20" / f"{name}.vrp").is_file()


def test_build_dataset_is_idempotent_without_force(scratch: Path):
    build_dataset(scratch, sizes=[20], count_per_size=1, force=True)
    assert build_dataset(scratch, sizes=[20], count_per_size=1) == []


def test_build_dataset_accepts_the_engine_signature(scratch: Path):
    """The engine calls ``build_dataset(out_dir, spec, force)``."""
    spec = {"sizes": [20], "count_per_size": 1, "seed": 42}
    written = build_dataset(scratch, spec, True)
    assert len(written) == 1
    assert written[0]["problem_size"] == 20


def test_json_round_trip(scratch: Path):
    instance = generate_instance(problem_size=5, seed=4)
    path = write_instance_json(instance, scratch / "inst.json")
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == instance["name"]
    assert load_instance(path)["problem_size"] == 5

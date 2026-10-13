"""The template bundle must stay honest: it is what a new problem is copied from.

Three things are worth guarding, because each is a documented promise the README makes:

* the example problem's checker really rejects a wrong answer -- which is precisely what a
  problem loses when it ships no `grader/`;
* the bundle loads and generates instances with **no** `seed/` and an empty correspondence
  table, which is the "solved from scratch" case the README claims works;
* its offline payloads satisfy the analysis schema, so `llm.backend=mock` still runs after
  somebody edits the template.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cvrp.problems import load_problem
from tests.conftest import PACKAGE_PROBLEMS

TEMPLATE = PACKAGE_PROBLEMS / "_template"


@pytest.fixture(scope="module")
def plugin():
    return load_problem(TEMPLATE, slug="counting")


@pytest.fixture(scope="module")
def grader(plugin):
    return plugin.checks_module


def _instance(path: Path, numbers: list[int]) -> Path:
    path.write_text(
        json.dumps({"name": "t", "problem_size": len(numbers), "numbers": numbers}),
        encoding="utf-8",
    )
    return path


def test_the_bundle_loads_without_a_seed(plugin):
    assert plugin.slug == "counting"
    assert plugin.problem_tag == "COUNTER"
    assert (TEMPLATE / "grader").is_dir()
    # the template deliberately has no baseline: that is the case it documents
    assert plugin.spec["generated"]["correspondence"] == {"run.py": None, "README.md": None}


def test_the_checker_accepts_the_right_answer(scratch: Path, grader):
    instance = _instance(scratch / "i.json", [3, 1, 4])
    solution = scratch / "s.json"
    solution.write_text(
        json.dumps({"total": 8, "squares": [9, 1, 16], "cost": 0.0}), encoding="utf-8"
    )
    verdict = grader.verify(instance, solution, {})
    assert verdict["feasible"], verdict["violations"]
    assert verdict["checks"] == {"total_correct": True, "squares_correct": True}


def test_the_checker_rejects_a_wrong_total(scratch: Path, grader):
    """The whole point of shipping a grader: a plausible dump is not a correct one."""
    instance = _instance(scratch / "i.json", [3, 1, 4])
    solution = scratch / "s.json"
    solution.write_text(
        json.dumps({"total": 9, "squares": [9, 1, 16], "cost": 0.0}), encoding="utf-8"
    )
    verdict = grader.verify(instance, solution, {})
    assert not verdict["feasible"]
    assert any("expected 8" in item for item in verdict["violations"])


def test_the_checker_rejects_reordered_squares(scratch: Path, grader):
    """Arithmetically right, answer wrong: order is part of the contract."""
    instance = _instance(scratch / "i.json", [3, 1, 4])
    solution = scratch / "s.json"
    solution.write_text(
        json.dumps({"total": 8, "squares": [1, 9, 16], "cost": 0.0}), encoding="utf-8"
    )
    verdict = grader.verify(instance, solution, {})
    assert not verdict["feasible"]
    assert any("squares" in item for item in verdict["violations"])


def test_a_missing_solution_is_infeasible_not_a_crash(scratch: Path, grader):
    instance = _instance(scratch / "i.json", [1, 2])
    verdict = grader.verify(instance, scratch / "absent.json", {})
    assert verdict["feasible"] is False
    assert verdict["skipped"] is False


def test_scoring_has_the_engines_signature_and_rewards_a_zero_cost(scratch: Path, grader):
    """The engine calls `score(best_cost, verification, spec)`; a two-argument hook breaks."""
    assert grader.score(0.0, {}, {}) == 1.0
    assert grader.score(3.0, {}, {}) == pytest.approx(0.25)
    assert grader.score(None, {}, {}) == 0.0


def test_instances_are_generated_deterministically(scratch: Path, plugin):
    first = plugin.build_dataset(scratch / "a")
    second = plugin.build_dataset(scratch / "b")
    assert first and len(first) == len(second)
    for left, right in zip(first, second):
        assert left["name"] == right["name"]
    # `n{size}/*.json` is what `data.instance_glob` promises
    size = int(plugin.problem_size)
    assert (scratch / "a" / f"n{size}").is_dir()
    assert (scratch / "a" / "manifest.json").is_file()


def test_the_offline_analysis_payload_satisfies_the_schema(plugin):
    """`llm.backend=mock` must keep working after somebody edits the template."""
    payload = json.loads((TEMPLATE / "mock_analysis.json").read_text(encoding="utf-8"))
    for field in (
        "problem_type",
        "constraints",
        "masking_rules",
        "state_variables",
        "feasibility_checklist",
        "objective",
        "pomo_modification_plan",
    ):
        assert payload.get(field), f"mock analysis is missing {field}"
    assert len(payload["feasibility_checklist"]) >= 2
    for rule in payload["masking_rules"]:
        assert rule.get("name") and rule.get("condition")
    for item in payload["pomo_modification_plan"]:
        assert item.get("file") or item.get("symbol")


def test_the_offline_solver_prints_the_contract_lines(scratch: Path, plugin):
    """The mock solver is a real program: run it and read what it prints."""
    import subprocess
    import sys

    instance = _instance(scratch / "i.json", [2, 5])
    solution = scratch / "out.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(TEMPLATE / "mock_solver.py"),
            "--path",
            str(instance),
            "--iteration",
            "1",
            "--save-solution",
            str(solution),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    import re

    best = plugin.spec["program"]["best_regex"]
    initial = plugin.spec["program"]["initial_regex"]
    assert re.search(initial, proc.stdout), proc.stdout
    assert re.search(best, proc.stdout), proc.stdout
    dumped = json.loads(solution.read_text(encoding="utf-8"))
    assert dumped["total"] == 7
    assert dumped["squares"] == [4, 25]


def test_the_template_points_at_its_own_hooks(plugin):
    """The two paths the engine resolves inside the bundle, spelled as relative paths."""
    assert plugin.spec["checks"] == "grader/template_grader.py"
    assert plugin.spec["data"]["generator"].endswith(":build_dataset")
    assert (TEMPLATE / plugin.spec["checks"]).is_file()

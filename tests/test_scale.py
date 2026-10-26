"""Tests for the task scale (`scale.problem_size`) and what follows from it.

One problem directory serves n=20, n=50 and n=100. The number is declared once, in
the task file, and everything derived from it must agree: the generated
entry-point file names, the instance directory, the dimensions the agents are told,
and the instance the executor actually runs. The two failure modes these tests
protect against are both silent:

* a spec still naming ``train_n20.py`` while the task asks for n=50 -- the gate
  then demands a file the agent has no reason to write;
* a data directory holding n=20 instances being used to score an n=50 task --
  everything "passes" and the number means nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import PACKAGE_PROBLEMS
from vrpar.executor.contract import instance_problem_size, require_instance_size
from vrpar.problems import (
    DEFAULT_PROBLEM_SIZE,
    ProblemError,
    ScaleParams,
    load_problem,
    render_spec,
    scale_from_config,
)

PROBLEM_DIR = PACKAGE_PROBLEMS / "cvrptw"


# --------------------------------------------------------------------------- #
# scale parsing
# --------------------------------------------------------------------------- #


def test_scale_defaults_to_the_historical_size():
    """A task written before `scale` existed must keep behaving exactly as before."""
    scale = scale_from_config({})
    assert scale.problem_size == DEFAULT_PROBLEM_SIZE
    assert scale.effective_pomo_size == DEFAULT_PROBLEM_SIZE
    assert scale.aug_factor == 8


def test_scale_reads_the_documented_block():
    scale = scale_from_config({"scale": {"problem_size": 50, "pomo_size": 50, "aug_factor": 4}})
    assert (scale.problem_size, scale.effective_pomo_size, scale.aug_factor) == (50, 50, 4)


def test_pomo_size_defaults_to_the_problem_size():
    assert ScaleParams(problem_size=100).effective_pomo_size == 100
    assert ScaleParams(problem_size=100, pomo_size=25).effective_pomo_size == 25


def test_scale_is_tolerated_from_older_spellings():
    assert scale_from_config({"data": {"sizes": [50]}}).problem_size == 50
    assert scale_from_config({"problem": {"size": 100}}).problem_size == 100
    # the documented block wins over the tolerated spellings
    assert (
        scale_from_config({"scale": {"problem_size": 50}, "data": {"sizes": [20]}}).problem_size == 50
    )


@pytest.mark.parametrize(
    "config",
    [
        {"scale": {"problem_size": 0}},
        {"scale": {"problem_size": -5}},
        {"scale": {"problem_size": 10, "pomo_size": 20}},
        {"scale": {"problem_size": 10, "aug_factor": 0}},
        {"scale": {"problem_size": "many"}},
        {"scale": [50]},
    ],
)
def test_bad_scales_are_rejected(config):
    with pytest.raises(ProblemError):
        scale_from_config(config)


# --------------------------------------------------------------------------- #
# spec rendering
# --------------------------------------------------------------------------- #


def test_render_substitutes_and_leaves_other_braces_alone():
    spec = {
        "entries": {"train": "train_n{n}.py"},
        "sizes": ["{n}"],
        "dimension": "{dimension}",
        "unrelated": "{cost}",
        "number": 7,
        "nested": [{"pomo": "{pomo}"}],
    }
    rendered = render_spec(spec, ScaleParams(problem_size=50))
    assert rendered["entries"]["train"] == "train_n50.py"
    assert rendered["sizes"] == ["50"]
    assert rendered["dimension"] == "51"  # 50 customers + depot
    assert rendered["unrelated"] == "{cost}"  # not a scale placeholder
    assert rendered["number"] == 7
    assert rendered["nested"][0]["pomo"] == "50"


@pytest.mark.parametrize("size", [20, 50, 100])
def test_one_spec_serves_every_size(size: int):
    plugin = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=size))
    generated = plugin.spec["generated"]
    assert generated["entries"]["train"] == f"train_n{size}.py"
    assert generated["entries"]["test"] == f"test_n{size}.py"
    assert f"train_n{size}.py" in generated["files"]
    assert generated["dimensions"]["problem_size"] == str(size)
    assert generated["dimensions"]["pomo_size"] == str(size)
    # the widths that must stay checkpoint-compatible are not templated
    assert generated["dimensions"]["encoder_embedding"] == 128
    assert generated["dimensions"]["decoder_hidden"] == 128
    assert plugin.instance_glob == f"n{size}/*.json"
    assert plugin.data["sizes"] == [str(size)]
    assert plugin.problem_size == size
    assert plugin.required_sizes == [size]


def test_generated_paths_follow_the_scale():
    plugin = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=100))
    paths = plugin.generated_paths
    assert "artifacts/generated/train_n100.py" in paths
    assert "artifacts/generated/test_n100.py" in paths
    assert not any("n20" in path for path in paths)


def test_agent_context_states_the_scale():
    plugin = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=50))
    context = plugin.agent_context()
    assert "problem_size (customers) = 50" in context
    assert "train_n50.py" in context
    # the DIMENSION placeholder must be rendered, not left as braces
    assert "{dimension}" not in context
    assert "depot + customers = 51" in context
    # and it must not still claim the default size
    assert "customers) = 20" not in context


def test_prompts_carry_the_scale(scratch: Path):
    """The rendered instruction must name the size, not a template or a stale n."""
    from vrpar.pipeline.stages import stage_variables

    plugin = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=100))
    variables = stage_variables(
        {"name": "analysis", "kind": "agent"},
        {"task": {"name": "t"}, "llm": {}, "problem": {}, "_problem": plugin},
        scratch / "wt",
        scratch / "run",
    )
    assert variables["problem_size"] == "100"
    assert variables["problem_size_plus_depot"] == "101"
    assert "problem_size (customers) = 100" in variables["scale_lines"]
    assert "train_n100.py" in variables["generated_entry_lines"]


def test_scale_flows_from_the_task_file(scratch: Path):
    """End to end through config loading, which is where the tasks differ."""
    from vrpar.config import load_config

    body = (
        "task:\n  name: t\n  slug: t\n"
        "problem:\n  slug: cvrptw\n  dir: " + json.dumps(str(PROBLEM_DIR)) + "\n"
        "scale:\n  problem_size: 50\n"
        "stages:\n  - name: analysis\n    kind: agent\n"
    )
    task = scratch / "task_n50.yaml"
    task.write_text(body, encoding="utf-8")
    config = load_config(task, cwd=scratch)
    assert config["scale"]["problem_size"] == 50
    assert config["problem"]["size"] == 50
    assert config["_problem"].instance_glob == "n50/*.json"
    assert config["problem"]["_spec_generated"]["entries"]["train"] == "train_n50.py"

    # and the resolved config still records it for the executor
    assert config["problem"]["_spec_data"]["sizes"] == ["50"]


# --------------------------------------------------------------------------- #
# instances at scale
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("size", [20, 50, 100])
def test_the_generator_produces_feasible_instances_at_every_size(size: int):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cvrptw_instances", PROBLEM_DIR / "grader" / "cvrptw_gen.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    instance = module.generate_feasible_instance(problem_size=size, seed=1234 + size * 100)
    assert instance["problem_size"] == size
    assert len(instance["node_xy"]) == size
    assert len(instance["time_windows"]) == size + 1
    check = module._self_check(instance)
    assert check["feasible"], check["violations"]


def test_large_instances_get_wider_windows():
    """Feasibility at n=100 requires more slack than n=20; the defaults must say so."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("cvrptw_instances2", PROBLEM_DIR / "grader" / "cvrptw_gen.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    small = module._slack_settings({"problem_size": 20})
    large = module._slack_settings({"problem_size": 100})
    assert large["base_slack"] > small["base_slack"]
    assert large["slack_growth"] > small["slack_growth"]


# --------------------------------------------------------------------------- #
# the size guard
# --------------------------------------------------------------------------- #


def test_instance_problem_size_reads_the_field_and_falls_back_to_shape(scratch: Path):
    with_field = scratch / "a.json"
    with_field.write_text(json.dumps({"problem_size": 50, "node_xy": [[0, 0]] * 50}), encoding="utf-8")
    assert instance_problem_size(with_field) == 50

    shape_only = scratch / "b.json"
    shape_only.write_text(json.dumps({"node_xy": [[0, 0]] * 100}), encoding="utf-8")
    assert instance_problem_size(shape_only) == 100

    assert instance_problem_size(scratch / "missing.json") is None


def test_the_size_guard_passes_on_the_right_size(scratch: Path):
    path = scratch / "n50.json"
    path.write_text(json.dumps({"problem_size": 50}), encoding="utf-8")
    assert require_instance_size([path], 50) is None
    # no expectation -> no opinion
    assert require_instance_size([path], None) is None


def test_the_size_guard_fails_on_the_wrong_size(scratch: Path):
    """The silent-fallback failure: n=50 task, n=20 data on disk."""
    path = scratch / "n20.json"
    path.write_text(json.dumps({"problem_size": 20}), encoding="utf-8")
    result = require_instance_size([path], 50, name="instance_size")
    assert result is not None and not result.passed
    assert result.error_class == "wrong_instance_size"
    assert "n=50" in result.message
    assert "n20.json=n20" in result.message


def test_data_ready_is_size_aware(scratch: Path):
    """A manifest for another size must not count as ready."""
    data_dir = scratch / "data"
    (data_dir / "n20").mkdir(parents=True)
    instance = data_dir / "n20" / "cvrptw_n20_00.json"
    instance.write_text(json.dumps({"problem_size": 20}), encoding="utf-8")
    manifest = {
        "count": 1,
        "sizes": [20],
        "instances": [{"name": "cvrptw_n20_00", "problem_size": 20, "json": "n20/cvrptw_n20_00.json"}],
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    as_20 = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=20))
    as_50 = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=50))
    assert as_20.data_ready(data_dir)
    assert not as_50.data_ready(data_dir)


def test_data_ready_checks_the_file_exists(scratch: Path):
    """A manifest entry is a promise, not proof."""
    data_dir = scratch / "data"
    data_dir.mkdir()
    manifest = {
        "count": 1,
        "sizes": [20],
        "instances": [{"name": "gone", "problem_size": 20, "json": "n20/gone.json"}],
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plugin = load_problem(PROBLEM_DIR, slug="cvrptw", scale=ScaleParams(problem_size=20))
    assert not plugin.data_ready(data_dir)

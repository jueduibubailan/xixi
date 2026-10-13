"""The stage table's two labels, and why they are not the same field.

`kind` is the execution mechanism the orchestrator dispatches on; `does` is the label the
dashboard shows. They differ for exactly one stage, `code_error`, whose core is a
deterministic executor and which *also* spends one model turn narrating a failure. These
tests pin that difference so a future edit cannot quietly relabel a mechanism -- which is
what "just change executor to agent" would have done.
"""

from __future__ import annotations

from pathlib import Path

from cvrp.pipeline.spec import DEFAULT_LOOP, STAGES

#: Stages that are one model turn. Everything else is deterministic engine code.
AGENT_STAGES = {"retrieval", "analysis", "code_generation", "code_revise", "update"}
EXECUTOR_STAGES = {"code_error", "feasibility"}


def test_every_stage_says_both_what_it_is_and_what_it_does():
    for stage in STAGES:
        assert stage.get("kind") in {"agent", "executor"}, stage["name"]
        assert stage.get("does"), f"{stage['name']} has no human-readable label"


def test_the_mechanism_is_still_the_thing_the_orchestrator_dispatches_on():
    kinds = {stage["name"]: stage["kind"] for stage in STAGES}
    for name in AGENT_STAGES:
        assert kinds[name] == "agent", name
    for name in EXECUTOR_STAGES:
        assert kinds[name] == "executor", name


def test_code_error_is_the_one_stage_whose_label_differs_from_its_mechanism():
    """Its executor runs deterministically, then one agent turn narrates the failure.

    So neither label alone is true: `executor` hides the model turn, `agent` hides the
    deterministic core and the fact that a clean pass calls no model at all.
    """
    kind_to_default_label = {"agent": "agent turn", "executor": "executor"}
    differing = [
        stage["name"]
        for stage in STAGES
        if stage["does"] != kind_to_default_label[stage["kind"]]
    ]
    assert differing == ["code_error"]
    labels = {stage["name"]: stage["does"] for stage in STAGES}
    assert labels["code_error"] == "executor + agent narration"


def test_the_deterministic_executor_still_lists_its_steps():
    """`code_error` is dispatched to `run_checks`, so dropping the steps would be a lie."""
    code_error = next(stage for stage in STAGES if stage["name"] == "code_error")
    assert code_error["steps"] == [
        "py_compile",
        "import_probe",
        "correspondence",
        "smoke_run",
        "full_run",
    ]


def test_feasibility_is_pure_engine_code_and_has_no_role_file():
    """It reads the grader's verdict; it is never a model turn.

    It used to carry `role: src/cvrp/roles/feasibility_agent.md`, a file nothing ever read.
    """
    feasibility = next(stage for stage in STAGES if stage["name"] == "feasibility")
    assert feasibility["kind"] == "executor"
    assert "role" not in feasibility
    assert not (Path("src/cvrp/roles") / "feasibility_agent.md").exists()


def test_the_narrator_keeps_the_role_file_it_actually_reads():
    """`_narrate_error` reuses the `code_error` stage config, so this entry is live."""
    code_error = next(stage for stage in STAGES if stage["name"] == "code_error")
    assert code_error["role"] == "src/cvrp/roles/code_error_agent.md"
    assert Path(code_error["role"]).is_file()


def test_every_role_named_by_a_stage_exists():
    for stage in STAGES:
        role = stage.get("role")
        if role:
            assert Path(role).is_file(), f"{stage['name']} names a missing role: {role}"


def test_the_loop_still_drives_the_executor_and_the_reviser():
    assert DEFAULT_LOOP["on"] == ["code_error", "code_revise"]
    assert DEFAULT_LOOP["until"] == "contract_passed"

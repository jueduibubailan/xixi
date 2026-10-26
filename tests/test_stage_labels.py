"""The stage table's two labels, and why they are not the same field.

`kind` is the execution mechanism the orchestrator dispatches on; `does` is the label the
dashboard shows. They differ for exactly one stage, `code_error`, whose core is a
deterministic executor and which *also* spends one model turn checking the generated code
against the plan. These tests pin that difference so a future edit cannot quietly relabel a
mechanism.
"""

from __future__ import annotations

from pathlib import Path

from vrpar.pipeline.spec import DEFAULT_LOOP, STAGES

#: Stages that are one model turn. Everything else is deterministic engine code.
AGENT_STAGES = {"analysis", "code_generation", "code_revise"}
EXECUTOR_STAGES = {"code_error", "feasibility"}


def test_the_pipeline_is_five_stages_without_a_memory_layer():
    names = [stage["name"] for stage in STAGES]
    assert names == [
        "analysis",
        "code_generation",
        "code_error",
        "code_revise",
        "feasibility",
    ]
    # the knowledge-base stages are gone, and must not come back by accident
    assert "retrieval" not in names
    assert "update" not in names


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


def test_no_stage_label_disagrees_with_its_mechanism():
    """`does` is a plain label again: no stage runs an agent turn *and* engine code.

    `code_error` used to be the exception -- a deterministic executor plus one model turn
    checking the plan. That turn is gone, so its label went back to `executor`.
    """
    kind_to_default_label = {"agent": "agent turn", "executor": "executor"}
    differing = [
        stage["name"]
        for stage in STAGES
        if stage["does"] != kind_to_default_label[stage["kind"]]
    ]
    assert differing == []
    labels = {stage["name"]: stage["does"] for stage in STAGES}
    assert labels["code_error"] == "executor"


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


def test_code_error_declares_the_run_report_and_nothing_else():
    """One output: the executor's report. The plan check used to add a second."""
    code_error = next(stage for stage in STAGES if stage["name"] == "code_error")
    assert code_error["produces"] == ["artifacts/runs/s3_run_report.json"]


def test_feasibility_is_pure_engine_code_and_has_no_role_file():
    """It reads the grader's verdict; it is never a model turn."""
    feasibility = next(stage for stage in STAGES if stage["name"] == "feasibility")
    assert feasibility["kind"] == "executor"
    assert "role" not in feasibility
    assert not (Path("src/vrpar/roles") / "feasibility_agent.md").exists()


def test_code_error_is_pure_engine_code_and_has_no_role_file():
    """It runs `run_checks` and reports; a role file here would be read by nobody.

    The removed plan check was the only reader.
    """
    code_error = next(stage for stage in STAGES if stage["name"] == "code_error")
    assert code_error["kind"] == "executor"
    assert "role" not in code_error
    assert not (Path("src/vrpar/roles") / "code_error_agent.md").exists()


def test_every_role_named_by_a_stage_exists():
    for stage in STAGES:
        role = stage.get("role")
        if role:
            assert Path(role).is_file(), f"{stage['name']} names a missing role: {role}"


def test_every_prompt_key_resolves_to_a_shipped_file():
    pack = Path("src/vrpar/prompts")
    for stage in STAGES:
        key = stage.get("prompt_key")
        if key:
            assert (pack / f"{key}.md").is_file(), f"{stage['name']} -> {key}.md"


def test_the_loop_still_drives_the_executor_and_the_reviser():
    assert DEFAULT_LOOP["on"] == ["code_error", "code_revise"]
    assert DEFAULT_LOOP["until"] == "contract_passed"



"""Gate tests: a stage only advances when its artifacts are actually usable."""

from __future__ import annotations

import json
from pathlib import Path

from cvrp.pipeline import gates
from cvrp.problems import load_problem
from cvrp.run.run_dir import write_json
from tests.conftest import PACKAGE_PROBLEMS

PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")


def _write_analysis(run_dir: Path, payload: dict) -> None:
    write_json(run_dir / gates.ANALYSIS_REL, payload)


def _good_analysis() -> dict:
    return {
        "problem_type": "CVRPTW",
        "full_name": "CVRPTW",
        "summary": "capacity plus time windows",
        "constraints": [{"id": "C", "name": "Capacity", "rule": "load <= capacity"}],
        "state_variables": [
            {"name": "current_time", "shape": "(B,P)", "update": "max(arrival, ready) + service"}
        ],
        "masking_rules": [{"name": "tw", "condition": "arrival > due"}],
        "feasibility_checklist": ["each customer once", "load <= capacity"],
        "objective": "minimize distance",
        "output_contract": ["a", "b"],
        "pomo_modification_plan": [{"file": "pomo/POMO/CVRPEnv.py", "change": "add mask"}],
        "checkpoint_compatibility": {"encoder_input_dim_must_stay": 2},
    }


def test_analysis_gate_accepts_a_complete_payload(scratch: Path):
    _write_analysis(scratch, _good_analysis())
    result = gates.gate_analysis(scratch)
    assert result.passed, result.issues
    assert not result.warnings


def test_analysis_gate_rejects_missing_mask(scratch: Path):
    payload = _good_analysis()
    payload["masking_rules"] = []
    _write_analysis(scratch, payload)
    result = gates.gate_analysis(scratch)
    assert not result.passed
    assert any("masking_rules" in issue for issue in result.issues)


def test_analysis_gate_rejects_unusable_checklist(scratch: Path):
    payload = _good_analysis()
    payload["feasibility_checklist"] = ["only one"]
    _write_analysis(scratch, payload)
    assert not gates.gate_analysis(scratch).passed


def test_analysis_gate_warns_about_encoder_width(scratch: Path):
    payload = _good_analysis()
    payload["checkpoint_compatibility"] = {"encoder_input_dim_must_stay": 4}
    _write_analysis(scratch, payload)
    result = gates.gate_analysis(scratch)
    assert result.passed
    assert result.warnings


def test_generated_gate_requires_the_problem_entry_program(scratch: Path):
    generated = scratch / "artifacts" / "generated"
    generated.mkdir(parents=True)
    assert not gates.gate_generated(scratch, problem=PROBLEM).passed

    (generated / "run.py").write_text("print('hi')", encoding="utf-8")
    result = gates.gate_generated(scratch, problem=PROBLEM)
    assert result.passed, result.issues
    # the rest of the package is a warning, not a blocker
    assert any("CVRPTWEnv.py" in warning for warning in result.warnings)


def test_generated_gate_falls_back_to_engine_defaults(scratch: Path):
    """With no problem loaded the gate still needs *some* entry program."""
    generated = scratch / "artifacts" / "generated"
    generated.mkdir(parents=True)
    (generated / "run.py").write_text("x", encoding="utf-8")
    assert gates.gate_generated(scratch).passed


def test_expected_generated_files_splits_required_from_expected():
    required, expected = gates.expected_generated_files(PROBLEM)
    assert required == ("run.py",)
    assert "CVRPTWEnv.py" in expected
    assert "run.py" not in expected


def test_run_report_gate_reads_the_executor_report(scratch: Path):
    assert not gates.gate_run_report({}).passed
    assert not gates.gate_run_report(
        {"passed": False, "failed_step": "full_run", "error_class": "shape_error"}
    ).passed
    assert gates.gate_run_report({"passed": True, "best_cost": 3.2}).passed


def test_feasibility_gate_treats_skip_as_a_warning(scratch: Path):
    assert not gates.gate_feasibility({}).passed
    skipped = gates.gate_feasibility({"skipped": True, "feasible": False})
    assert skipped.passed
    assert skipped.warnings
    failed = gates.gate_feasibility({"feasible": False, "violations": ["customer 4 arrived late"]})
    assert not failed.passed
    assert failed.issues == ["customer 4 arrived late"]


def test_gate_result_serialises(scratch: Path):
    payload = gates.gate_analysis(scratch).as_dict()
    assert json.dumps(payload)
    assert payload["gate"] == "analysis"

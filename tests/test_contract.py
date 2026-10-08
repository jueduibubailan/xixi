"""Acceptance-contract tests: parsing, classification, and the two gates."""

from __future__ import annotations

from pathlib import Path

from cvrp.executor.contract import Contract, classify_error, condense, find_instances


def test_contract_parses_both_lines():
    contract = Contract()
    stdout = (
        "loading instance\n"
        "the initial process is successful, the initial cost is 12.5\n"
        "the process is successful, the best cost is 9.75\n"
    )
    initial, best = contract.parse_costs(stdout)
    assert initial == 12.5
    assert best == 9.75


def test_contract_missing_line_yields_none():
    contract = Contract()
    initial, best = contract.parse_costs("the process is successful, the best cost is nope\n")
    assert initial is None
    assert best is None


def test_contract_from_config_reads_grader_block():
    config = {
        "grader": {
            "timeout": 42,
            "args": {
                "program_file": "artifacts/generated/run.py",
                "contract_regex": r"BEST=(\d+)",
                "require_feasible": False,
            },
        }
    }
    contract = Contract.from_config(config)
    assert contract.timeout_s == 42
    assert contract.require_feasible is False
    assert contract.parse_costs("BEST=7")[1] == 7.0


def test_classify_error_picks_the_specific_class():
    assert classify_error("ModuleNotFoundError: No module named 'torch'") == "import_error"
    assert classify_error("SyntaxError: invalid syntax") == "syntax_error"
    assert classify_error("Error(s) in loading state_dict for CVRPModel") == "torch_load_error"
    assert classify_error("the request exceeded 900s and was killed") == "timeout"
    assert classify_error("Initial solution is not valid.") == "initial_invalid"
    assert classify_error("RuntimeError: The size of tensor a must match") == "shape_error"
    assert classify_error("") == ""


def test_classify_error_specific_beats_generic():
    text = "Traceback (most recent call last):\n  File x\nIndexError: index out of range"
    assert classify_error(text) == "index_error"


def test_condense_keeps_both_ends():
    text = "A" * 5000 + "B" * 5000
    result = condense(text, limit=200)
    assert result.startswith("A")
    assert result.endswith("B")
    assert "[truncated]" in result


def test_find_instances_prefers_the_size_folder(scratch: Path):
    folder = scratch / "n20"
    folder.mkdir(parents=True)
    (folder / "a.json").write_text("{}", encoding="utf-8")
    (scratch / "manifest.json").write_text("{}", encoding="utf-8")
    found = find_instances(scratch, 20)
    assert len(found) == 1
    assert found[0].name == "a.json"

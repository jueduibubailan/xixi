"""The pipeline every task runs.

Four stages, one straight line:

    analysis -> code_generation -> code_error -> code_adjustment

Agents do the work.  The pipeline itself only seeds worktrees, renders the
instruction file, checks the three things the operator asked for (the two
training pins, the exit code and the score line) and prints the score.
"""

from __future__ import annotations

from typing import Any

STAGES: list[dict[str, Any]] = [
    {
        "name": "analysis",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/analysis_agent.md",
        "prompt_key": "s1_analysis",
        "produces": ["artifacts/analysis/plan.md"],
        "timeout_seconds": 3600,
    },
    {
        "name": "code_generation",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/code_generation_agent.md",
        "prompt_key": "s2_codegen",
        "produces": ["artifacts/generated/"],
        "timeout_seconds": 3600,
    },
    {
        "name": "code_error",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/code_error_agent.md",
        "prompt_key": "s3_code_error",
        "produces": [
            "artifacts/generated/test_train.py",
            "artifacts/runs/",
        ],
        "timeout_seconds": 14400,
    },
    {
        "name": "code_adjustment",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/code_adjustment_agent.md",
        "prompt_key": "s4_adjustment",
        "produces": ["artifacts/revisions/"],
        "timeout_seconds": 10800,
    },
    {
        "name": "nn_improve",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/nn_improve_agent.md",
        "prompt_key": "s5_nn_improve",
        "produces": ["artifacts/nn_improve/"],
        "timeout_seconds": 3600,
    },
]

#: Which run-relative artifacts each stage may read.  Replaces the old
#: ``_inputs_for`` derivation: code_error now runs many times for different plans,
#: so "everything the previous stage produced" no longer describes its inputs.
STAGE_INPUTS: dict[str, list[str]] = {
    "analysis": [],
    "code_generation": ["artifacts/analysis/plan.md"],
    "code_error": ["artifacts/analysis/plan.md", "artifacts/generated"],
    "code_adjustment": [
        "artifacts/analysis/plan.md",
        "artifacts/generated",
        "artifacts/runs",
        "artifacts/nn_improve/plans",
    ],
    "nn_improve": [
        "artifacts/analysis/plan.md",
        "artifacts/generated",
        "artifacts/runs",
        "artifacts/nn_improve",
    ],
}

DEFAULT_LOOP: dict[str, Any] = {"max_rounds": 3}
DEFAULT_NN_LOOP: dict[str, Any] = {"rounds": 3}

__all__ = ["DEFAULT_LOOP", "DEFAULT_NN_LOOP", "STAGE_INPUTS", "STAGES"]

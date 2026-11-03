"""The pipeline every task runs.

Four stages, one straight line:

    analysis -> code_generation -> code_error -> code_revise

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
            "artifacts/runs/test_train.stdout.txt",
            "artifacts/runs/test_train.stderr.txt",
            "artifacts/runs/test_train.exitcode",
            "artifacts/runs/test.stdout.txt",
            "artifacts/runs/test.stderr.txt",
            "artifacts/runs/test.exitcode",
        ],
        "timeout_seconds": 3600,
    },
    {
        "name": "code_revise",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/code_revise_agent.md",
        "prompt_key": "s4_revise",
        "produces": ["artifacts/revisions/"],
        "timeout_seconds": 3600,
    },
]

DEFAULT_LOOP: dict[str, Any] = {"max_rounds": 5}

__all__ = ["DEFAULT_LOOP", "STAGES"]

"""The pipeline every problem runs, spelled out once.

Kept in its own module so both `vrpar.config` (which re-exports it) and `vrpar.bundle`
(which expands `stages: default`) can import it without a cycle: the bundle loader needs
the table while the config is still being built.

Five stages, one straight line:

    analysis -> code_generation -> code_error -> code_revise -> feasibility

There is no retrieval stage and no knowledge base: a run is solved from the problem
description alone. `code_error` runs the generated code and reports what happened; the revise
loop repeats until the code runs cleanly. A run succeeds when it runs -- whether the code
matches the analysis plan is not checked.
"""

from __future__ import annotations

from typing import Any

#: The five stages, with their mechanisms, roles, prompt keys and wall-clock budgets.
#:
#: Two fields look similar and are deliberately different:
#:
#: * ``kind`` is the **execution mechanism** the orchestrator dispatches on -- ``agent`` for
#:   a stage that is one model turn, ``executor`` for a stage that runs deterministic code.
#:   It is not a label.
#: * ``does`` is the **human-readable label** the dashboard shows.
#:
#: Adding a field rather than overriding ``kind`` also keeps `config.resolved.json` honest:
#: a reviewer reading it sees the real mechanism, not a relabelled one.
#:
#: `prompt_key` names a file in the engine's shared pack (`src/vrpar/prompts/`), which a
#: problem may override by placing a file of the same name in its own `prompts/` directory.
STAGES: list[dict[str, Any]] = [
    {
        "name": "analysis",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/analysis_agent.md",
        "prompt_key": "s1_analysis",
        # `plan.md` is a declared output too: the code generation stage reads it, so the
        # seeder must stage it into that worktree alongside the JSON.
        "produces": ["artifacts/analysis/analysis.json", "artifacts/analysis/plan.md"],
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
        "kind": "executor",
        "does": "executor",
        # No `role` and no model turn: the stage runs `run_checks` and reports. A role file
        # here would be a dead file: nothing would read it.
        "produces": ["artifacts/runs/s3_run_report.json"],
        "steps": ["py_compile", "import_probe", "correspondence", "smoke_run", "full_run"],
        "timeout_seconds": 5400,
    },
    {
        "name": "code_revise",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/vrpar/roles/code_revise_agent.md",
        "prompt_key": "s4_revise",
        "produces": ["artifacts/revisions/", "artifacts/reviews/"],
        "timeout_seconds": 3600,
    },
    {
        "name": "feasibility",
        "kind": "executor",
        "does": "executor",
        # No `role`: this stage is never a model turn. It reads the grader's own verdict and
        # publishes it. A role file here would be a dead file.
        "produces": ["artifacts/runs/s5_feasibility.json"],
        "timeout_seconds": 1800,
    },
]

#: The loop the executor drives: run, revise on failure, up to `max_rounds`.
DEFAULT_LOOP: dict[str, Any] = {
    "on": ["code_error", "code_revise"],
    "until": "contract_passed",
    "max_rounds": 5,
}

__all__ = ["DEFAULT_LOOP", "STAGES"]

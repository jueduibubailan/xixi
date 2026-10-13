"""The pipeline every problem runs, spelled out once.

Kept in its own module so both `cvrp.config` (which re-exports it) and `cvrp.bundle` (which
expands `stages: default`) can import it without a cycle: the bundle loader needs the table
while the config is still being built.

A problem's `task.yaml` asks for this with `stages: default`. Writing it here instead of
repeating ~140 lines per problem is what keeps a bundle down to `task.yaml` + `seed/` +
`grader/`.
"""

from __future__ import annotations

from typing import Any

#: The seven stages, with their mechanisms, roles, prompt keys and wall-clock budgets.
#:
#: Two fields look similar and are deliberately different:
#:
#: * ``kind`` is the **execution mechanism** the orchestrator dispatches on -- ``agent`` for
#:   a stage that is one model turn, ``executor`` for a stage that runs deterministic code.
#:   It is not a label.
#: * ``does`` is the **human-readable label** the dashboard shows. It exists because
#:   `code_error` is neither: its core is the deterministic executor (`run_checks`: compile,
#:   import probe, correspondence, smoke run, full run), and *after* it, only when there is
#:   something to report, it spends one model turn narrating the failure. On a clean pass no
#:   model is called at all. Labelling that stage `executor` hides half of what it does;
#:   labelling it `agent` would be false, and `kind` cannot carry both meanings.
#:
#: Adding a field rather than overriding ``kind`` also keeps `config.resolved.json` honest:
#: a reviewer reading it sees the real mechanism, not a relabelled one.
#:
#: `prompt_key` names a file in the engine's shared pack (`src/cvrp/prompts/`), which a
#: problem may override by placing a file of the same name in its own `prompts/` directory.
STAGES: list[dict[str, Any]] = [
    {
        "name": "retrieval",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/cvrp/roles/retrieval_agent.md",
        "prompt_key": "s0_retrieval",
        "produces": ["artifacts/records/retrieval.json"],
        "timeout_seconds": 1800,
    },
    {
        "name": "analysis",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/cvrp/roles/analysis_agent.md",
        "prompt_key": "s1_analysis",
        "produces": ["artifacts/analysis/analysis.json"],
        "timeout_seconds": 3600,
    },
    {
        "name": "code_generation",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/cvrp/roles/code_generation_agent.md",
        "prompt_key": "s2_codegen",
        "produces": ["artifacts/generated/"],
        "timeout_seconds": 3600,
    },
    {
        "name": "code_error",
        "kind": "executor",
        "does": "executor + agent narration",
        # Read only on the narration turn, which `_narrate_error` runs by reusing this stage
        # config with `prompt_key: error_narrate`. The deterministic executor itself needs no
        # role file, so this entry looks unused in a clean pass -- it is not.
        "role": "src/cvrp/roles/code_error_agent.md",
        "produces": ["artifacts/runs/s3_run_report.json"],
        "steps": ["py_compile", "import_probe", "correspondence", "smoke_run", "full_run"],
        "timeout_seconds": 5400,
    },
    {
        "name": "code_revise",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/cvrp/roles/code_revise_agent.md",
        "produces": ["artifacts/revisions/", "artifacts/reviews/"],
        "timeout_seconds": 3600,
    },
    {
        "name": "feasibility",
        "kind": "executor",
        "does": "executor",
        # No `role`: this stage is never a model turn. It reads the grader's own verdict and
        # publishes it. A role file here would be a dead file, and there used to be one.
        "produces": ["artifacts/runs/s5_feasibility.json"],
        "timeout_seconds": 1800,
    },
    {
        "name": "update",
        "kind": "agent",
        "does": "agent turn",
        "role": "src/cvrp/roles/update_agent.md",
        "prompt_key": "s7_update",
        "produces": ["records/main"],
        "timeout_seconds": 1800,
    },
]

#: The loop the executor drives: try, revise on failure, up to `max_rounds`.
DEFAULT_LOOP: dict[str, Any] = {
    "on": ["code_error", "code_revise"],
    "until": "contract_passed",
    "max_rounds": 5,
}

__all__ = ["DEFAULT_LOOP", "STAGES"]

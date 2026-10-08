"""Stage runtime: render the instruction file, run one agent turn.

Stages are declarative in ``task.yaml``; this module is the only place that knows
how a stage's config turns into an agent invocation. It mirrors CORAL's shape -- a
role file plus an instruction file the agent reads -- while keeping the turn
strictly sequential.

There is nothing variant-specific here. Which prompt a stage uses comes from the
loaded problem plugin (``problem.yaml:prompts``), and the *content* of the
instruction is the problem's own prompts plus variables the plugin declares.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cvrp.config import resolve_path
from cvrp.executor.contract import agent_failure_hint
from cvrp.llm.base import LLMBackend, LLMResult, make_backend
from cvrp.problems import ProblemPlugin
from cvrp.run.run_dir import append_log, read_text
from cvrp.run.worktree import write_baseline, write_stage_manifest

INSTRUCTION_FILENAME = "CVPR.md"

# The placeholder vocabulary: `{lower_snake_case}`. Deliberately narrow so that
# fenced JSON in a prompt (`{"routes": ...}`) is never mistaken for a placeholder.
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")

EXECUTION_CONTRACT = """
## Execution contract (read this twice)

1. **Write files, do not paste patches.** Your output files are the deliverable.
   Use your file tools; the pipeline reads the paths listed above, not your reply.
2. **Never ask a question.** There is no human in the loop. If something is
   ambiguous, pick the most defensible reading, do it, and record the assumption
   in the summary you write.
3. **Do not go outside your directory.** Everything you write must live under
   `{worktree}`. The baseline, the checkpoints and the data are read-only inputs.
4. **Respect the acceptance contract.** The generated program must accept
   `{cli_accepts}` and print, on stdout, exactly:
{contract_lines}
   It must also accept `{save_solution_flag} <file.json>` and write its best
   solution there, so feasibility can be verified independently of your own
   validation code. The expected shape of that file is described in the problem
   prompt above.
5. **Finish with a summary.** End your reply with one fenced json block:

```json
{"status": "ok|failed", "summary": "one paragraph", "files": ["..."], "assumptions": ["..."]}
```
""".strip()


@dataclass
class StageOutcome:
    stage: str
    ok: bool
    worktree: Path
    result: LLMResult | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    log_path: Path | None = None
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    #: When this agent turn started. Used to tell whether the agent actually wrote
    #: its declared artifacts, or whether it left a stale copy from a previous run
    #: in place. See `gates.supplied_this_round`.
    started_at: float = 0.0


def _render(template: str, variables: dict[str, str]) -> str:
    """Substitute every declared ``{placeholder}`` in ``template``.

    Two things happen in a single pass:

    1. ``{{...}}`` collapses to ``{...}``, so a prompt author can write a literal
       brace (usually inside a JSON example) with the usual escaping;
    2. every remaining ``{lower_snake_case}`` is replaced from ``variables``.

    An *unknown* placeholder becomes an empty string rather than being left in
    place: a prompt that still reads ``{failed_step}`` would be handed to an agent
    that cannot resolve it, and the mistake would only surface as a confusing run
    much later.

    Note the order in the pattern: the escaped alternative comes first, so
    ``{{failed_step}}`` yields the literal ``{failed_step}`` rather than being
    substituted.
    """
    combined = re.compile(r"\{\{([^{}]*)\}\}|\{([a-z][a-z0-9_]*)\}")

    def _dispatch(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return "{" + match.group(1) + "}"
        value = variables.get(match.group(2))
        return "" if value is None else str(value)

    return combined.sub(_dispatch, template)


# Mapping from stage name to the key the problem uses for that prompt.
STAGE_PROMPT_KEYS = {
    "analysis": "analysis",
    "code_generation": "codegen",
    "code_revise": "revise",
}


def stage_prompt_path(stage_config: dict[str, Any], config: dict[str, Any]) -> Path | None:
    """Resolve a stage's prompt: explicit path first, otherwise the plugin's."""
    explicit = stage_config.get("prompt")
    if explicit:
        return resolve_path(config, explicit)
    problem: ProblemPlugin | None = config.get("_problem")
    if problem is None:
        return None
    key = stage_config.get("prompt_key") or STAGE_PROMPT_KEYS.get(stage_config["name"], "")
    raw = problem.prompt(key) if key else ""
    return Path(raw) if raw else None


def stage_variables(
    stage_config: dict[str, Any],
    config: dict[str, Any],
    worktree: Path,
    run_dir: Path,
    extra_variables: dict[str, str] | None = None,
) -> dict[str, str]:
    """The placeholder vocabulary shared by every problem's prompts."""
    problem: ProblemPlugin | None = config.get("_problem")
    task = config.get("task") or {}
    llm = config.get("llm") or {}

    generated = problem.spec.get("generated", {}) if problem else {}
    modules = list(generated.get("modules") or [])
    entries = dict(generated.get("entries") or {})
    dimensions = dict(generated.get("dimensions") or {})
    program_spec = (problem.spec.get("program") or {}) if problem else {}
    accepted = list(program_spec.get("accepts") or [])
    expected_lines = list(program_spec.get("expected_lines") or [])
    resources = dict(problem.spec.get("resources") or {}) if problem else {}
    data = problem.data if problem else {}
    generated_dir = str(generated.get("dir") or "artifacts/generated")

    variables = {
        "stage_name": stage_config["name"],
        "task_name": task.get("name", "task"),
        "task_description": str(task.get("description", "")).strip(),
        "task_tips": str(task.get("tips", "")).strip(),
        "worktree": str(worktree),
        "run_dir": str(run_dir),
        "model": str(llm.get("model", "")),
        "artifact_paths": "\n".join(f"- `{item}`" for item in (stage_config.get("produces") or [])),
        # `cost` is intentionally a *literal* placeholder: the contract text tells
        # the agent which line to print, and the placeholder is part of that line.
        "cost": "{cost}",
        # problem identity
        "problem_slug": problem.slug if problem else "",
        "problem_name": problem.name if problem else "",
        "variant_label": str((problem.spec.get("variant_label") if problem else "") or ""),
        "problem_context": problem.agent_context() if problem else "",
        # generated package
        "generated_dir": generated_dir,
        "program": problem.program if problem else "run.py",
        "program_path": problem.program_relpath if problem else "artifacts/generated/run.py",
        "generated_modules": ", ".join(modules),
        "generated_module_lines": "\n".join(f"  - `{name}`" for name in modules),
        "generated_entry_lines": "\n".join(f"  - `{name}` ({role})" for role, name in entries.items()),
        "generated_file_lines": "\n".join(f"  - `{name}`" for name in (generated.get("files") or [])),
        "generated_file_count": str(len(generated.get("files") or [])),
        "dimension_lines": "\n".join(f"  - {key} = {value}" for key, value in dimensions.items()),
        "dimensions": json.dumps(dimensions, indent=2),
        # the program's interface
        "cli_accepts": " ".join(accepted) if accepted else "(no arguments)",
        "cli_path_flag": str((problem.cli.get("path_arg") if problem else "--path") or "(none)"),
        "cli_iteration_flag": str((problem.cli.get("iteration_arg") if problem else "--iteration") or "(none)"),
        "save_solution_flag": str(
            (problem.cli.get("save_solution_arg") if problem else "--save-solution") or "--save-solution"
        ),
        "contract_lines": "\n".join(f"   - `{line}`" for line in expected_lines)
        or "   - (see the problem prompt for the exact wording)",
        "best_regex": str(program_spec.get("best_regex") or ""),
        "initial_regex": str(program_spec.get("initial_regex") or ""),
        # data
        "data_dir": str(data.get("dir") or ""),
        "instance_glob": str(data.get("instance_glob") or ""),
        "data_parameter_lines": "\n".join(
            f"  - {key} = {value}" for key, value in data.items() if key not in {"dir", "generator", "artifact_inputs", "ready_marker", "instance_glob"}
        ),
        # resources
        "baseline_dir": str(resources.get("baseline_dir") or "baseline"),
        "baseline_notes": str(resources.get("notes") or "BASELINE_NOTES.md"),
        "checkpoint": str(resources.get("checkpoint") or ""),
        "resource_subdirs": ", ".join(str(item) for item in (resources.get("baseline_subdirs") or [])),
        # generic execution facts
        "python": "the interpreter running this project (`uv run` venv)",
        "instructions_file": INSTRUCTION_FILENAME,
    }
    variables.update(extra_variables or {})
    return variables


def render_instruction(
    stage_config: dict[str, Any],
    config: dict[str, Any],
    worktree: Path,
    run_dir: Path,
    instructions: list[str] | None = None,
    extra_variables: dict[str, str] | None = None,
) -> str:
    """Assemble the per-stage instruction file the agent reads."""
    role_path = resolve_path(config, stage_config.get("role", ""))
    prompt_path = stage_prompt_path(stage_config, config)
    role_text = read_text(role_path, max_chars=40_000) if role_path.is_file() else ""
    template_text = read_text(prompt_path, max_chars=80_000) if prompt_path and prompt_path.is_file() else ""

    variables = stage_variables(stage_config, config, worktree, run_dir, extra_variables)

    sections: list[str] = [
        f"[STAGE:{stage_config['name']}]",
        f"# CVPR instruction - stage `{stage_config['name']}`",
        "<!-- generated by cvrp; edit the role and problem prompts, not this file -->",
    ]
    if role_text:
        sections.append("# Your role\n\n" + role_text.strip())
    if template_text:
        sections.append("# Task for this stage\n\n" + _render(template_text, variables).strip())
    else:
        sections.append(
            "# Task for this stage\n\n"
            f"_(no prompt resolved for stage `{stage_config['name']}`; "
            f"declare it under `prompts:` in the problem's problem.yaml)_"
        )
    sections.append("# Context\n\n" + _render(_context_block(stage_config), variables).strip())
    if instructions:
        joined = "\n".join(f"- {item}" for item in instructions)
        sections.append("# Operator instructions (from the dashboard)\n\n" + joined)
    sections.append(_render(EXECUTION_CONTRACT, variables))
    return "\n\n".join(sections).strip() + "\n"


def _context_block(stage_config: dict[str, Any]) -> str:
    return "\n".join(
        [
            "- Working directory: `{worktree}` (this is your only writable area).",
            "- Run directory: `{run_dir}` (read-only record of previous stages).",
            "- Declared outputs for this stage:",
            "{artifact_paths}",
            "",
            "Available inputs:",
            "{problem_context}",
            "",
            "Environment:",
            "- Python interpreter: {python}.",
            "- Do not install packages; everything needed should already be present.",
        ]
    )


def run_agent_stage(
    stage_config: dict[str, Any],
    config: dict[str, Any],
    run_dir: Path,
    worktree: Path,
    backend: LLMBackend | None = None,
    instructions: list[str] | None = None,
    round_index: int = 0,
    extra_variables: dict[str, str] | None = None,
) -> StageOutcome:
    """Render the instruction file and execute one agent turn for the stage."""
    started = time.time()
    stage_name = stage_config["name"]

    instruction = render_instruction(
        stage_config, config, worktree, run_dir, instructions=instructions, extra_variables=extra_variables
    )
    instruction_path = Path(worktree) / INSTRUCTION_FILENAME
    instruction_path.write_text(instruction, encoding="utf-8")

    write_stage_manifest(
        Path(worktree),
        stage_config,
        extra={
            "run_dir": str(run_dir),
            "round": round_index,
            "instruction_file": INSTRUCTION_FILENAME,
            "problem": (config.get("problem") or {}).get("slug"),
        },
    )

    log_path = Path(run_dir) / "logs" / f"{stage_name}.round{round_index}.log"
    append_log(Path(run_dir) / "logs" / "pipeline.log", f"stage {stage_name} round {round_index}: agent turn")

    # Snapshot the declared outputs *before* the turn. The worktree is reused
    # across runs, so this is what lets the gate tell "the agent wrote it" from
    # "it was already there" -- see `cvrp.run.worktree.write_baseline`.
    declared = [str(item) for item in (stage_config.get("produces") or [])]
    write_baseline(Path(worktree), declared)

    llm_cfg = dict(config.get("llm") or {})
    backend = backend or make_backend(llm_cfg)
    timeout_s = int(stage_config.get("timeout_seconds") or llm_cfg.get("timeout_seconds") or 3600)

    result = backend.run(prompt=instruction, cwd=Path(worktree), log_path=log_path, timeout_s=timeout_s)

    payload = result.payload or {}
    notes: list[str] = []
    if not result.ok:
        notes.append(result.error or f"agent exited with code {result.returncode}")
        # A failed *turn* is usually an environment problem. `result.error` is only a
        # summary line, so the diagnosis reads the log tail too -- the auth/403 detail
        # lives in the stream-json lines, not in the summary. Without this the run
        # reads as "the generated code crashed" when no code was ever generated.
        diagnosis_text = " ".join(
            part
            for part in (result.error, result.text, read_text(log_path, max_chars=20_000))
            if part
        )
        hint = agent_failure_hint(diagnosis_text)
        if hint:
            notes.append(hint)
    if payload.get("status") == "failed":
        notes.append(str(payload.get("summary") or "agent reported failure"))
    if not payload:
        notes.append("agent reply had no json summary block (artifacts are still authoritative)")

    return StageOutcome(
        stage=stage_name,
        ok=result.ok,
        worktree=Path(worktree),
        result=result,
        payload=payload,
        log_path=log_path,
        duration_s=time.time() - started,
        notes=notes,
        started_at=started,
    )


def stage_manifest(worktree: Path) -> dict[str, Any]:
    path = Path(worktree) / ".cvrp" / "stage.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

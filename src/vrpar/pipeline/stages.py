"""Stage runtime: render the instruction file and run one agent turn.

The engine does not parse the analysis plan.  Agents read it, decide the problem
size, the generated file names and the code changes.  This module only supplies
the shared vocabulary and the mechanical execution contract.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vrpar.config import resolve_path
from vrpar.executor.contract import agent_failure_hint
from vrpar.llm.base import LLMBackend, LLMResult, make_backend
from vrpar.problems import ProblemPlugin
from vrpar.run.run_dir import append_log, read_text
from vrpar.run.worktree import write_baseline, write_stage_manifest

INSTRUCTION_FILENAME = "VRPAR.md"
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")

EXECUTION_CONTRACT = """
## Execution contract (read this twice)

1. **Write files, do not paste patches.** Your output files are the deliverable.
   Use your file tools; the pipeline reads the paths listed above, not your reply.
2. **Never ask a question.** There is no human in the loop. If something is
   ambiguous, pick the most defensible reading, do it, and record the assumption
   in your final summary.
3. **Do not go outside your directory.** Everything you write must live under
   `{worktree}`. The baseline and the problem inputs are read-only.
4. **`test_train.py` is generated only by the code_error stage.** It must set
   `'epochs': 1` and `'train_episodes': 64`, finish training, save POMO's
   `checkpoint-1.pt`, and then that stage runs the generated `test_<n>.py` with
   `--model-path <checkpoint>`.
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
    started_at: float = 0.0


def _render(template: str, variables: dict[str, str]) -> str:
    combined = re.compile(r"\{\{([^{}]*)\}\}|\{([a-z][a-z0-9_]*)\}")

    def _dispatch(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return "{" + match.group(1) + "}"
        value = variables.get(match.group(2))
        return "" if value is None else str(value)

    return combined.sub(_dispatch, template)


STAGE_PROMPT_KEYS = {
    "analysis": "s1_analysis",
    "code_generation": "s2_codegen",
    "code_error": "s3_code_error",
    "code_revise": "s4_revise",
}


def stage_prompt_path(stage_config: dict[str, Any], config: dict[str, Any]) -> Path | None:
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
    problem: ProblemPlugin | None = config.get("_problem")
    task = config.get("task") or {}
    llm = config.get("llm") or {}
    task_slug = str(task.get("slug") or "").strip()
    tag = "".join(ch for ch in task_slug.upper() if ch.isalnum()) or "PROBLEM"

    variables = {
        "stage_name": stage_config["name"],
        "task_name": task.get("name", "task"),
        "task_slug": task.get("slug", ""),
        "task_description": str(task.get("description", "")).strip(),
        "task_tips": str(task.get("tips", "")).strip(),
        "worktree": str(worktree),
        "run_dir": str(run_dir),
        "model": str(llm.get("model", "")),
        "artifact_paths": "\n".join(f"- `{item}`" for item in (stage_config.get("produces") or [])),
        "plan_path": "artifacts/analysis/plan.md",
        "generated_dir": "artifacts/generated",
        "test_train_path": "artifacts/generated/test_train.py",
        "test_entry_path": "artifacts/generated/test_<n>.py",
        "score_line_example": "AUGMENTATION SCORE: 43.1234",
        "problem_slug": problem.slug if problem else task_slug,
        "problem_name": str(task.get("name") or task_slug),
        "variant_label": task_slug,
        "problem": tag,
        "problem_inputs_dir": "data/inputs",
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
    role_path = resolve_path(config, stage_config.get("role", ""))
    prompt_path = stage_prompt_path(stage_config, config)
    role_text = read_text(role_path, max_chars=40_000) if role_path.is_file() else ""
    template_text = read_text(prompt_path, max_chars=80_000) if prompt_path and prompt_path.is_file() else ""

    variables = stage_variables(stage_config, config, worktree, run_dir, extra_variables)

    sections: list[str] = [
        f"[STAGE:{stage_config['name']}]",
        f"# VRPAR instruction - stage `{stage_config['name']}`",
        "<!-- generated by vrpar; edit the role and problem prompts, not this file -->",
    ]
    if role_text:
        sections.append("# Your role\n\n" + _render(role_text, variables).strip())
    if template_text:
        sections.append("# Task for this stage\n\n" + _render(template_text, variables).strip())
    else:
        sections.append(
            "# Task for this stage\n\n"
            f"_(no prompt resolved for stage `{stage_config['name']}`)_"
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
            "- Working directory: `{worktree}` (your only writable area).",
            "- Run directory: `{run_dir}` (read-only record of previous stages).",
            "- Declared outputs for this stage:",
            "{artifact_paths}",
            "",
            "Environment:",
            "- Python interpreter: {python}.",
            "- Do not install packages; the needed dependencies should already be present.",
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
    path = Path(worktree) / ".vrpar" / "stage.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


__all__ = [
    "INSTRUCTION_FILENAME",
    "PLACEHOLDER_RE",
    "STAGE_PROMPT_KEYS",
    "StageOutcome",
    "render_instruction",
    "run_agent_stage",
    "stage_manifest",
    "stage_prompt_path",
    "stage_variables",
]


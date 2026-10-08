"""Execution of generated code and the acceptance contract.

Three gates, matching ``task.yaml:stages[code_error].steps``:

1. ``py_compile`` -- every ``.py`` under ``artifacts/generated`` compiles.
2. ``smoke_run``  -- a short run (few iterations) returns 0 and prints the contract.
3. ``full_run``   -- the real run returns 0, prints the contract, and its solution
   passes the independent feasibility check.

Nothing here calls an LLM. This module *is* the "code error agent": it observes,
classifies, and reports -- it never edits code.

Everything variant-specific (the program name, its CLI flags, the exact wording
the acceptance contract matches) comes from the loaded **problem plugin**, not
from this module. Adding a new VRP variant must not require editing this file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cvrp.problems import DEFAULT_BEST_REGEX, DEFAULT_INITIAL_REGEX

DEFAULT_BEST_RE = DEFAULT_BEST_REGEX
DEFAULT_INITIAL_RE = DEFAULT_INITIAL_REGEX
MAX_CAPTURE = 20_000

# Ordered: the first pattern that matches wins, so put the specific before the generic.
ERROR_SIGNATURES: tuple[tuple[str, str], ...] = (
    # An agent that could not authenticate, or whose model name was rejected. This
    # is an environment problem, not a code problem, and it must not be reported as
    # "the generated code crashed".
    (
        "agent_auth_error",
        r"Failed to authenticate|authentication_failed|403 Request not allowed|"
        r"unrecognized_model|Invalid API key|invalid_api_key|401 Unauthorized",
    ),
    ("timeout", r"exceeded .*s and was killed|timed out|TimeoutExpired"),
    ("contract_missing", r"cannot find 'the process is successful|contract regex|no best cost"),
    ("initial_invalid", r"Initial solution is not valid|initial process is not successful"),
    ("nan_or_inf", r"nan|inf detected|cost is nan"),
    ("torch_load_error", r"Error\(s\) in loading state_dict|size mismatch for|Unexpected key\(s\)"),
    ("memory_error", r"out of memory|CUDA out of memory|MemoryError"),
    ("import_error", r"ModuleNotFoundError|ImportError|No module named"),
    ("syntax_error", r"SyntaxError|IndentationError|TabError"),
    ("assertion_error", r"AssertionError\b|^\s*assert\s"),
    ("index_error", r"IndexError|index out of range"),
    ("key_error", r"KeyError"),
    ("type_error", r"TypeError|unsupported operand"),
    ("value_error", r"ValueError"),
    ("file_not_found", r"FileNotFoundError|No such file or directory"),
    # `shape_error` sits last among the specific classes because torch's wording
    # ("must match the size of tensor") is broad enough to swallow other
    # exception types. A `SyntaxError` from a .py file is reported as a
    # SyntaxError, never as a shape problem.
    ("shape_error", r"size of tensor|shape mismatch|expand\(|dimension out of range"),
)


def agent_failure_hint(text: str) -> str:
    """A tailored explanation when the *agent* failed, not the generated code.

    Returns an empty string when the text looks like ordinary model output. This
    exists because the most confusing class of failure is an environment problem
    dressed up as a code problem: the log says "Failed to authenticate ... 403",
    the stage produced nothing, and the gate reports a missing artifact.
    """
    lowered = (text or "").lower()
    if not lowered:
        return ""
    if "unrecognized_model" in lowered or "failed to authenticate" in lowered or "403" in lowered:
        return (
            "the agent could not authenticate, or its model name was rejected. With a "
            "non-Anthropic model (deepseek-flash included) the CLI needs a router: export "
            "ANTHROPIC_BASE_URL, or set it under llm.env in the task config. Run "
            "`uv run cvrp doctor` to see where the traffic is currently going."
        )
    if "401" in lowered or "invalid api key" in lowered or "invalid_api_key" in lowered:
        return (
            "the agent's credentials were rejected. Check that the CLI is logged in for the "
            "account you expect, or set ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN."
        )
    if "command not found" in lowered or "no such file or directory" in lowered:
        return "the agent CLI could not be launched; run `uv run cvrp doctor`."
    return ""


@dataclass
class StepResult:
    """One gate's outcome."""

    name: str
    passed: bool
    returncode: int | None = None
    duration_s: float = 0.0
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error_class: str = ""
    message: str = ""
    initial_cost: float | None = None
    best_cost: float | None = None
    solution_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "passed": self.passed,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 3),
            "command": self.command,
            "error_class": self.error_class,
            "message": self.message,
        }
        if self.initial_cost is not None:
            payload["initial_cost"] = self.initial_cost
        if self.best_cost is not None:
            payload["best_cost"] = self.best_cost
        if self.solution_path:
            payload["solution_path"] = self.solution_path
        if self.stdout:
            payload["stdout_tail"] = self.stdout[-MAX_CAPTURE:]
        if self.stderr:
            payload["stderr_tail"] = self.stderr[-MAX_CAPTURE:]
        return payload


@dataclass
class Contract:
    """The textual acceptance contract.

    Sources, in order of precedence:

    1. ``task.yaml:grader.args`` (contract_regex / initial_regex / program_file /
       require_feasible);
    2. the problem plugin's ``problem.yaml:program`` section (what its solver
       prints, what it is called);
    3. the generic defaults inherited from AFL.
    """

    best_regex: str = DEFAULT_BEST_RE
    initial_regex: str = DEFAULT_INITIAL_RE
    program: str = "artifacts/generated/run.py"
    timeout_s: int = 900
    require_feasible: bool = True
    # argv construction, filled from the problem spec; empty means "no instance
    # argument" / "no iteration argument", which is legal for a variant whose
    # program takes neither.
    path_flag: str | None = "--path"
    iteration_flag: str | None = "--iteration"
    save_solution_flag: str | None = "--save-solution"
    extra_args: tuple[str, ...] = ()
    problem_slug: str = ""

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Contract":
        grader = config.get("grader") or {}
        args = grader.get("args") or {}
        program_spec = (config.get("problem") or {}).get("_spec_program") or {}
        cli = (config.get("problem") or {}).get("_spec_cli") or {}

        def pick(*values: Any, default: Any = None) -> Any:
            for value in values:
                if value is not None:
                    return value
            return default

        return cls(
            best_regex=str(
                pick(args.get("contract_regex"), program_spec.get("best_regex"), default=DEFAULT_BEST_RE)
            ),
            initial_regex=str(
                pick(args.get("initial_regex"), program_spec.get("initial_regex"), default=DEFAULT_INITIAL_RE)
            ),
            program=str(pick(args.get("program_file"), program_spec.get("program_relpath"), default="artifacts/generated/run.py")),
            timeout_s=int(pick(grader.get("timeout"), default=900)),
            require_feasible=bool(pick(args.get("require_feasible"), default=True)),
            path_flag=pick(cli.get("path_arg"), default="--path"),
            iteration_flag=pick(cli.get("iteration_arg"), default="--iteration"),
            save_solution_flag=pick(cli.get("save_solution_arg"), default="--save-solution"),
            extra_args=tuple(str(item) for item in (cli.get("extra_args") or ())),
            problem_slug=str(pick((config.get("problem") or {}).get("slug"), default="")),
        )

    # -- parsing ----------------------------------------------------------- #

    def parse_costs(self, stdout: str) -> tuple[float | None, float | None]:
        initial = re.search(self.initial_regex, stdout)
        best = re.search(self.best_regex, stdout)
        initial_cost = float(initial.group(1)) if initial else None
        best_cost = float(best.group(1)) if best else None
        return initial_cost, best_cost

    def cli_args(
        self,
        instance: Path | None = None,
        iteration: int | None = None,
        solution_out: Path | None = None,
    ) -> list[str]:
        """Build the argv tail for one program invocation.

        Each flag is optional: a problem that takes no instance file sets
        ``cli.path_arg: null`` and gets no ``--path`` at all.
        """
        args: list[str] = []
        if instance is not None and self.path_flag:
            args.extend([str(self.path_flag), str(instance)])
        if iteration is not None and self.iteration_flag:
            args.extend([str(self.iteration_flag), str(iteration)])
        if solution_out is not None and self.save_solution_flag:
            args.extend([str(self.save_solution_flag), str(solution_out)])
        args.extend(self.extra_args)
        return args


def classify_error(text: str) -> str:
    """Map a traceback/stderr blob to a stable error class."""
    lowered = text or ""
    for name, pattern in ERROR_SIGNATURES:
        if re.search(pattern, lowered, re.IGNORECASE):
            return name
    return "unknown_error" if lowered.strip() else ""


def condense(text: str, limit: int = 4000) -> str:
    """Keep the part of a traceback that a revise agent actually needs."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...\n[truncated]\n...\n" + text[-limit // 2 :]


def python_executable() -> str:
    return sys.executable or "python"


def locate_artifact(candidates: list[Path], relative: str) -> Path | None:
    """Find a run-relative artifact across a stage's directory candidates.

    A stage worktree houses three different things at once: the agent's own
    writable output (``artifacts/``), read-only inputs staged from earlier stages
    (``data/artifacts/``), and the canonical run directory the orchestrator owns.
    Every reader therefore has to look in a few places rather than assume one.
    """
    for base in candidates:
        if base is None:
            continue
        target = Path(base) / relative
        if target.exists():
            return target
    return None


def stage_search_paths(run_dir: Path, worktree: Path | None = None) -> list[Path]:
    """Search order for artifacts referenced during a stage."""
    paths: list[Path] = []
    if worktree is not None:
        paths.extend([Path(worktree) / "data", Path(worktree)])
    paths.append(Path(run_dir))
    return paths


# --------------------------------------------------------------------------- #
# the three gates
# --------------------------------------------------------------------------- #


def py_compile(run_dir: Path, worktree: Path, generated_rel: str = "artifacts/generated") -> StepResult:
    """Compile every python file the agent produced."""
    target = locate_artifact(stage_search_paths(run_dir, worktree), generated_rel)
    if target is None or not target.is_dir():
        return StepResult(
            name="py_compile",
            passed=False,
            error_class="missing_artifacts",
            message=f"{generated_rel} does not exist -- the code generation stage produced nothing",
        )
    files = sorted(str(p) for p in target.rglob("*.py"))
    if not files:
        return StepResult(
            name="py_compile",
            passed=False,
            error_class="missing_artifacts",
            message=f"no .py files under {target}",
        )
    cmd = [python_executable(), "-m", "py_compile", *files]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(worktree))
    return StepResult(
        name="py_compile",
        passed=proc.returncode == 0,
        returncode=proc.returncode,
        duration_s=time.time() - started,
        command=cmd[:6],
        stdout=proc.stdout[-MAX_CAPTURE:],
        stderr=proc.stderr[-MAX_CAPTURE:],
        error_class="" if proc.returncode == 0 else classify_error(proc.stderr),
        message="" if proc.returncode == 0 else condense(proc.stderr, 1500),
    )


def run_program(
    run_dir: Path,
    worktree: Path,
    contract: Contract,
    instance: Path | None,
    iteration: int | None,
    name: str,
    extra_args: list[str] | None = None,
    solution_out: Path | None = None,
) -> StepResult:
    """Run the generated program once and apply the contract.

    The argv tail comes from :meth:`Contract.cli_args`, i.e. from the problem's
    own CLI spec. ``solution_out`` asks the program to dump its best solution as
    JSON; a program that ignores that flag only loses the independent feasibility
    check, never the contract itself.
    """
    program = locate_artifact(stage_search_paths(run_dir, worktree), contract.program)
    if program is None or not program.is_file():
        return StepResult(
            name=name,
            passed=False,
            error_class="missing_program",
            message=(
                f"program not found: {contract.program} (searched: "
                + ", ".join(str(base) for base in stage_search_paths(run_dir, worktree))
                + ")"
            ),
        )

    cmd = [python_executable(), str(program)]
    cmd.extend(contract.cli_args(instance=instance, iteration=iteration, solution_out=solution_out))
    cmd.extend(extra_args or [])

    started = time.time()
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            # Run from the directory that holds the program so its sibling
            # modules are importable (sys.path[0] is the script's directory, and
            # a generated package may also rely on the CWD).
            cwd=str(program.parent),
            timeout=contract.timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return StepResult(
            name=name,
            passed=False,
            returncode=124,
            duration_s=time.time() - started,
            command=cmd,
            stdout=stdout[-MAX_CAPTURE:],
            error_class="timeout",
            message=f"program exceeded {contract.timeout_s}s",
        )

    initial_cost, best_cost = contract.parse_costs(proc.stdout)
    combined = f"{proc.stdout}\n{proc.stderr}"
    error_class = "" if proc.returncode == 0 else classify_error(combined)
    passed = proc.returncode == 0 and best_cost is not None
    message = ""
    if proc.returncode != 0:
        message = condense(proc.stderr or proc.stdout, 2000)
    elif best_cost is None:
        error_class = "contract_missing"
        message = (
            "program exited 0 but never printed the acceptance line "
            f"(expected to match {contract.best_regex!r}); stdout tail:\n{condense(proc.stdout, 1000)}"
        )
    return StepResult(
        name=name,
        passed=passed,
        returncode=proc.returncode,
        duration_s=time.time() - started,
        command=cmd,
        stdout=proc.stdout[-MAX_CAPTURE:],
        stderr=proc.stderr[-MAX_CAPTURE:],
        error_class=error_class,
        message=message,
        initial_cost=initial_cost,
        best_cost=best_cost,
        solution_path=str(solution_out) if solution_out is not None and Path(solution_out).is_file() else "",
    )


def find_instances(instances_dir: Path, size: int | None = None, pattern: str = "**/*.json") -> list[Path]:
    """Find instance files for the run.

    ``pattern`` is the problem's ``data.instance_glob``; when ``size`` is given we
    prefer a size-named subdirectory first (``n20/``, ``20/``, ``size-20/``), which
    is the convention the shipped generators use. The name ``manifest.json`` is
    never treated as an instance.
    """
    root = Path(instances_dir)
    if not root.is_dir():
        return []

    def _glob(base: Path, glob_pattern: str) -> list[Path]:
        if not base.is_dir():
            return []
        found = sorted(path for path in base.glob(glob_pattern) if path.is_file())
        return [path for path in found if path.name != "manifest.json"]

    if size is not None:
        for name in (f"n{size}", str(size), f"size-{size}"):
            found = _glob(root / name, pattern)
            if found:
                return found

    for base in (root, root / "data" / "instances", root / "instances"):
        found = _glob(base, pattern)
        if found:
            return found
    return [path for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json" and path.suffix in {".json", ".txt", ".vrp", ".csv"}]


def write_run_report(run_dir: Path, payload: dict[str, Any], relative: str = "artifacts/runs/s3_run_report.json") -> Path:
    target = Path(run_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def save_stdout(run_dir: Path, name: str, text: str) -> Path:
    target = Path(run_dir) / "artifacts" / "runs" / f"{name}.stdout.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text or "", encoding="utf-8")
    return target


def locate_python() -> str:
    """Prefer the project venv's interpreter when the pipeline runs under uv."""
    venv = Path(sys.prefix)
    for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("python") or python_executable()

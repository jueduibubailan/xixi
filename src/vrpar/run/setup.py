"""Run the task's `workspace.setup` commands before the first stage.

Why this exists rather than a note in a README: the executor runs the generated
program with the interpreter that runs the pipeline (`sys.executable`), so a
dependency the baseline needs but this project does not declare makes every code
error stage fail with an `import_error` that looks like the agent's fault. The
task config is the right place to say how to obtain it, and this module is what
turns that declaration into an action.

Two properties matter:

* **idempotent** -- the commands are re-run only when their text changes, tracked
  by a fingerprint file under the project's ``.vrpar`` directory. A plain
  ``uv pip install torch`` is a fast no-op once satisfied, so re-running is safe,
  but not re-running keeps each `vrpar start` quick.
* **loud on failure, but not fatal** -- setup that cannot run (no ``uv`` on PATH,
  no network) is reported in the pipeline log and in the run's diagnostics. The
  pipeline then proceeds, so the executor's own error is what the report shows.
  Silently degrading would be worse: the failure would move somewhere confusing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETUP_FILENAME = "setup.json"
DEFAULT_TIMEOUT_S = 3600


@dataclass
class SetupResult:
    """What happened for one `workspace.setup` block."""

    ran: bool
    skipped_reason: str = ""
    #: One entry per step; each entry lists the alternatives that were tried.
    commands: list[list[str]] | None = None
    failures: list[str] | None = None

    @property
    def ok(self) -> bool:
        return not self.failures

    def lines(self) -> list[str]:
        if not self.ran:
            return [f"setup: skipped ({self.skipped_reason})"]
        out = [f"setup: ran {len(self.commands or [])} command(s)"]
        out.extend(f"  $ {' | '.join(step)}" for step in (self.commands or []))
        out.extend(f"  ! {failure}" for failure in (self.failures or []))
        return out


def setup_commands(config: dict[str, Any]) -> list[list[str]]:
    """The resolved steps, or an empty list when nothing is declared.

    A step is a list of **alternatives tried in order**: the first one that exits 0
    wins, and the step only counts as failed when every alternative failed. That is
    how a task says "install the CUDA wheel, and fall back to the CPU wheel when the
    download is unavailable" -- a single install line cannot express it, because a
    failing command would otherwise be reported as a broken environment even when the
    fallback succeeded.
    """
    workspace = config.get("workspace") or {}
    raw = workspace.get("setup")
    if not raw:
        return []
    if isinstance(raw, str):
        return [[raw]]
    if not isinstance(raw, (list, tuple)):
        return []
    steps: list[list[str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)):
            alternatives = [str(alt).strip() for alt in item if str(alt).strip()]
        else:
            alternatives = [str(item).strip()] if str(item).strip() else []
        if alternatives:
            steps.append(alternatives)
    return steps


def _fingerprint_file(config: dict[str, Any]) -> Path:
    root = Path(str(config.get("_project_root") or Path.cwd()))
    return root / ".vrpar" / SETUP_FILENAME


def _python_executable_for(runner: str) -> str:
    """Resolve the interpreter a setup command should target.

    ``uv`` and ``uv pip`` need no help, but a plain ``python``/``pip`` inside a
    setup line would otherwise hit whatever is first on PATH instead of the
    environment the executor will use.
    """
    if runner in {"uv", "uvx"}:
        return shutil.which(runner) or runner
    return shutil.which(runner) or runner


def run_setup(
    config: dict[str, Any],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    force: bool = False,
    log: Any | None = None,
) -> SetupResult:
    """Execute ``workspace.setup`` once per fingerprint. Never raises."""
    commands = setup_commands(config)
    if not commands:
        return SetupResult(ran=False, skipped_reason="nothing declared")

    marker = _fingerprint_file(config)
    fingerprint = json.dumps(
        {"commands": commands, "python": sys.executable, "executable": sys.executable},
        sort_keys=True,
    )
    if not force:
        try:
            cached = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
            return SetupResult(
                ran=False,
                skipped_reason=f"already satisfied (see {marker})",
                commands=commands,
            )

    failures: list[str] = []
    for step in commands:
        detail = "no alternative ran"
        for command in step:
            ok, reason = _run_command(command, config=config, timeout_s=timeout_s, log=log)
            if ok:
                detail = ""
                break
            detail = reason
            if len(step) > 1 and callable(log):
                log(f"  setup: {command} failed ({reason}); trying the next alternative")
        if detail:
            label = " / ".join(step) if len(step) > 1 else step[0]
            failures.append(f"{label}: {detail}")

    if not failures:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps({"fingerprint": fingerprint, "commands": commands}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # the marker is an optimisation, not a requirement
    return SetupResult(ran=True, commands=commands, failures=failures)



def _run_command(
    command: str,
    *,
    config: dict[str, Any],
    timeout_s: int,
    log: Any | None,
) -> tuple[bool, str]:
    """Run one setup command. Returns ``(ok, detail)`` and never raises."""
    argv = _as_argv(command)
    if not argv:
        return False, "empty command"
    resolved = _python_executable_for(argv[0])
    if resolved and resolved != argv[0] and Path(resolved).is_absolute():
        argv[0] = resolved
    if not (Path(argv[0]).is_absolute() or shutil.which(argv[0])):
        return False, f"{argv[0]!r} is not on PATH"
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(config.get("_project_root") or Path.cwd()),
            timeout=timeout_s,
            env=_setup_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"exceeded {timeout_s}s"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (" | ".join(tail[-3:]) if tail else f"exit code {proc.returncode}")
    if callable(log):
        log(f"  setup ok in {time.time() - started:.1f}s: {command}")
    return True, ""


def _setup_env() -> dict[str, str]:
    """Environment for a setup command.

    ``VIRTUAL_ENV`` is kept when the pipeline itself runs inside one, so that a
    ``uv pip install`` targets the **same interpreter the executor will use**.
    Without it, uv picks a venv by discovery (or none at all) and the dependency can
    land somewhere the executor never looks -- which is how a "successful" install
    is followed by an `import_error`.
    """
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    # Same reason as the executor: the parent decodes UTF-8, so children emit UTF-8.
    # Without it, `uv pip install` progress output is decoded as cp936 on a Chinese
    # Windows host, the decode fails in a reader thread and the install log is lost.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        env["VIRTUAL_ENV"] = sys.prefix
    return env


def _as_argv(command: str) -> list[str]:
    """Split a setup line. Kept deliberately simple and predictable.

    ``shlex.split`` would be wrong on Windows (a backslash is a path separator, not
    an escape), and every command we care about -- ``uv pip install x``, ``python -m
    pip install x`` -- has no quoting needs.
    """
    return [part for part in str(command).split() if part]


__all__ = ["SETUP_FILENAME", "SetupResult", "run_setup", "setup_commands"]

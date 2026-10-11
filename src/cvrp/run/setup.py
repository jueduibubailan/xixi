"""Run the task's `workspace.setup` commands before the first stage.

Why this exists rather than a note in a README: the executor runs the generated
program with the interpreter that runs the pipeline (`sys.executable`), so a
dependency the baseline needs but this project does not declare makes every code
error stage fail with an `import_error` that looks like the agent's fault. The
task config is the right place to say how to obtain it, and this module is what
turns that declaration into an action.

Two properties matter:

* **idempotent** -- the commands are re-run only when their text changes, tracked
  by a fingerprint file under the project's ``.cvrp`` directory. A plain
  ``uv pip install torch`` is a fast no-op once satisfied, so re-running is safe,
  but not re-running keeps each `cvrp start` quick.
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
    commands: list[str] | None = None
    failures: list[str] | None = None

    @property
    def ok(self) -> bool:
        return not self.failures

    def lines(self) -> list[str]:
        if not self.ran:
            return [f"setup: skipped ({self.skipped_reason})"]
        out = [f"setup: ran {len(self.commands or [])} command(s)"]
        out.extend(f"  $ {command}" for command in (self.commands or []))
        out.extend(f"  ! {failure}" for failure in (self.failures or []))
        return out


def setup_commands(config: dict[str, Any]) -> list[str]:
    """The resolved command list, or an empty list when nothing is declared."""
    workspace = config.get("workspace") or {}
    raw = workspace.get("setup")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _fingerprint_file(config: dict[str, Any]) -> Path:
    root = Path(str(config.get("_project_root") or Path.cwd()))
    return root / ".cvrp" / SETUP_FILENAME


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
    for command in commands:
        argv = _as_argv(command)
        resolved = _python_executable_for(argv[0]) if argv else argv
        if resolved and resolved != argv[0] and Path(resolved).is_absolute():
            argv[0] = resolved
        if argv and not (Path(argv[0]).is_absolute() or shutil.which(argv[0])):
            failures.append(f"{command}: {argv[0]!r} is not on PATH")
            continue
        started = time.time()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                cwd=str(config.get("_project_root") or Path.cwd()),
                timeout=timeout_s,
                env=_setup_env(),
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{command}: exceeded {timeout_s}s")
            continue
        except OSError as exc:
            failures.append(f"{command}: {exc}")
            continue
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = " | ".join(tail[-3:]) if tail else f"exit code {proc.returncode}"
            failures.append(f"{command}: {detail}")
        elif callable(log):
            log(f"  setup ok in {time.time() - started:.1f}s: {command}")

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


def _setup_env() -> dict[str, str]:
    """Environment for a setup command.

    ``VIRTUAL_ENV`` is kept when the pipeline itself runs inside one, so that a
    ``uv pip install`` targets the **same interpreter the executor will use**.
    Without it, uv picks a venv by discovery (or none at all) and the dependency can
    land somewhere the executor never looks -- which is how a "successful" install
    is followed by an `import_error`.
    """
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
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

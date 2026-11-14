"""End-to-end proof that the router reaches the agent subprocess.

The unit tests check the environment policy in isolation. This one runs the real
launcher against a fake `claude` executable and inspects what the child process was
actually given — which is the only thing that decides whether a non-Anthropic model
name works.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from vrpar.llm.claude_code import ClaudeCodeBackend

#: A stand-in for the CLI: it records its environment and emits one stream-json
#: result line, which is the minimum the launcher needs to parse a turn.
SHIM = """\
import json, os, sys

record = {
    "argv": sys.argv[1:],
    "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
    "api_key": os.environ.get("ANTHROPIC_API_KEY"),
    "nested_marker": os.environ.get("CLAUDECODE"),
}
with open(os.environ["SHIM_OUT"], "w", encoding="utf-8") as handle:
    json.dump(record, handle)

print(json.dumps({"type": "result", "subtype": "success", "result": "shim ok"}))
"""


@pytest.fixture()
def shim(scratch: Path) -> Path:
    """Create an executable `claude` stand-in and return its path."""
    script = scratch / "claude_shim.py"
    script.write_text(SHIM, encoding="utf-8")

    if os.name == "nt":
        launcher = scratch / "claude.cmd"
        launcher.write_text(
            f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="ascii"
        )
    else:
        launcher = scratch / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def _run(scratch: Path, shim: Path, monkeypatch: pytest.MonkeyPatch, **llm_config) -> dict:
    out = scratch / "shim_env.json"
    monkeypatch.setenv("SHIM_OUT", str(out))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://router.local:4000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDECODE", "1")

    backend = ClaudeCodeBackend(
        {"model": "deepseek-flash", "claude_bin": str(shim), "permission_mode": None, **llm_config}
    )
    result = backend.run(
        prompt="hello",
        cwd=scratch,
        log_path=scratch / "logs" / "turn.log",
        timeout_s=60,
    )
    assert result.returncode == 0, result.error
    return json.loads(out.read_text(encoding="utf-8"))


def test_router_and_key_reach_the_child_process(scratch: Path, shim: Path, monkeypatch: pytest.MonkeyPatch):
    record = _run(scratch, shim, monkeypatch)
    assert record["base_url"] == "http://router.local:4000"
    assert record["api_key"] == "sk-test"
    # the nested-session marker must NOT reach it
    assert record["nested_marker"] is None
    assert "--model" in record["argv"]
    assert "deepseek-flash" in record["argv"]


def test_isolation_mode_removes_the_router(scratch: Path, shim: Path, monkeypatch: pytest.MonkeyPatch):
    record = _run(scratch, shim, monkeypatch, inherit_routing_env=False)
    assert record["base_url"] is None
    assert record["api_key"] is None


def test_explicit_env_overrides_the_router(scratch: Path, shim: Path, monkeypatch: pytest.MonkeyPatch):
    record = _run(
        scratch,
        shim,
        monkeypatch,
        inherit_routing_env=False,
        env={"ANTHROPIC_BASE_URL": "http://explicit:9"},
    )
    assert record["base_url"] == "http://explicit:9"

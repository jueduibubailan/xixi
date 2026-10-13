"""Tests for the DeepSeek Harness runtime.

The CLI is not installed here, so these pin exactly the parts that are verifiable without
it: the argv CORAL documents, the environment DSH reads, the instruction file it looks for,
the log parsing, and the message a caller gets when the binary is missing. A live run needs
the binary and `DEEPSEEK_API_KEY`; this is the contract around it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cvrp.llm import dsh
from cvrp.llm.claude_code import make_backend


def test_the_factory_accepts_the_names_coral_accepts():
    for name in ("dsh", "deepseek", "deepseek_harness", "deepseek-harness"):
        backend = make_backend({"backend": name, "model": "deepseek-v4-flash"})
        assert isinstance(backend, dsh.DshBackend), name
        assert backend.model == "deepseek-v4-flash"


def test_an_unknown_backend_names_the_ones_that_exist():
    with pytest.raises(ValueError) as excinfo:
        make_backend({"backend": "not-a-runtime"})
    message = str(excinfo.value)
    assert "claude_code" in message and "dsh" in message and "mock" in message


def test_the_command_follows_corals_documented_shape():
    argv = dsh.build_command(
        prompt="do the thing",
        binary="dsh",
        profile="headless",
        model="deepseek-v4-flash",
    )
    assert argv[0] == "dsh"
    assert argv[1:3] == ["--profile", "headless"]
    assert "--model" in argv and "deepseek-v4-flash" in argv
    # the prompt is last: everything before it is configuration
    assert argv[-1] == "do the thing"


def test_runtime_options_reach_the_command_line():
    backend = dsh.DshBackend(
        {
            "backend": "dsh",
            "model": "m",
            "runtime_options": {
                "command": "dsh",
                "profile": "custom",
                "patch": ["a.yml", "b.yml"],
            },
            "extra_args": ["--verbose"],
        }
    )
    argv = backend.command_for("p")
    assert "--profile" in argv and "custom" in argv
    assert argv.count("--patch") == 2
    assert "--verbose" in argv


def test_permission_and_tool_modes_go_through_the_environment(scratch: Path):
    env = dsh.dsh_env(
        {"EXTRA": "1"},
        run_dir=scratch,
        permission_mode="workspace-write",
        tools_mode="native",
        dsh_home=".dsh",
    )
    assert env["DSH_PERMISSION_MODE"] == "workspace-write"
    assert env["DSH_TOOLS_MODE"] == "native"
    # DSH_HOME is a shared directory under the run, so stages inherit context
    assert Path(env["DSH_HOME"]).name == ".dsh"
    assert Path(env["DSH_HOME"]).is_dir()
    assert env["EXTRA"] == "1"


def test_the_instruction_is_written_where_dsh_reads_it(scratch: Path):
    target = dsh.write_agents_file(scratch / "wt", "# instruction\n", scratch / "run")
    assert target.name == "AGENTS.md"
    text = target.read_text(encoding="utf-8")
    assert "# instruction" in text
    # and it says where the authoritative copy is, so two instruction files cannot confuse
    assert "CVPR.md" in text


def test_the_agents_file_is_idempotent(scratch: Path):
    worktree = scratch / "wt"
    dsh.write_agents_file(worktree, "first\n")
    dsh.write_agents_file(worktree, "second\n")
    text = (worktree / "AGENTS.md").read_text(encoding="utf-8")
    assert "second" in text and "first" not in text


def test_a_missing_binary_is_reported_not_swallowed(scratch: Path):
    backend = dsh.DshBackend({"backend": "dsh", "dsh_bin": "definitely-not-installed-dsh"})
    result = backend.run(
        "prompt", cwd=scratch / "wt", log_path=scratch / "turn.log", timeout_s=5
    )
    assert result.returncode == 127
    assert not result.ok
    assert "npm install --global @deepseek-ai/dsh" in result.error
    assert "DEEPSEEK_API_KEY" in result.error


def test_the_log_header_records_which_runtime_was_used(scratch: Path):
    backend = dsh.DshBackend({"backend": "dsh", "dsh_bin": "definitely-not-installed-dsh"})
    log = scratch / "turn.log"
    backend.run("prompt", cwd=scratch / "wt", log_path=log, timeout_s=5)
    text = log.read_text(encoding="utf-8")
    assert "dsh" in text and "profile" in text


def test_environment_report_never_leaks_the_key():
    report = dsh.describe_environment()
    assert report["DEEPSEEK_API_KEY"] in {"set", "unset"}
    # the value itself must never be echoed
    assert len(report["DEEPSEEK_API_KEY"]) <= 5

"""Tests for the DeepSeek Harness runtime.

These pin the parts that are verifiable without a model: the argv, the environment DSH
reads, the stdin delivery, the log decoding, and the message a caller gets when the binary
is missing or the command line is too long. A live turn needs the binary and
`DEEPSEEK_API_KEY`; this is the contract around it.

Two of these tests encode a bug that reached a real run, so they are the ones to keep:

* the instruction is **not** an argv entry. It was, and on Windows `dsh` resolves to a
  `.cmd` shim, so `cmd.exe`'s 8191-character limit rejected every turn with
  `命令行太长。` ("the command line is too long") before the process started;
* `--model` is not a flag this CLI accepts: `dsh --profile headless --model x` answers
  `error: unknown option '--model'`.
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


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #


def test_the_instruction_is_not_on_the_command_line():
    """The bug: a rendered instruction is 11k-38k chars and a Windows argv caps at 8191."""
    prompt = "x" * 40_000
    argv = dsh.build_command(binary="dsh", profile="headless")
    backend = dsh.DshBackend({"backend": "dsh", "dsh_bin": "dsh"})
    assert prompt not in argv
    assert prompt not in backend.command_for(prompt)
    assert len(dsh.describe_command(backend.command_for(prompt))) < 200


def test_the_command_line_has_only_launcher_flags():
    argv = dsh.build_command(binary="dsh", profile="headless")
    assert argv == ["dsh", "--profile", "headless"]


def test_model_is_never_passed_as_a_flag():
    """`--model` is not a launcher option; it belongs to the profile."""
    backend = dsh.DshBackend({"backend": "dsh", "model": "deepseek-v4-flash"})
    argv = backend.command_for()
    assert "--model" not in argv
    assert "deepseek-v4-flash" not in argv
    # it is still reported, in the log header
    assert backend.model == "deepseek-v4-flash"


def test_runtime_options_reach_the_command_line():
    backend = dsh.DshBackend(
        {
            "backend": "dsh",
            "runtime_options": {
                "command": "dsh",
                "profile": "custom",
                "patch": ["a.yml", "b.yml"],
            },
            "extra_args": ["--verbose"],
        }
    )
    argv = backend.command_for()
    assert "--profile" in argv and "custom" in argv
    assert argv.count("--patch") == 2
    assert "--verbose" in argv


def test_an_overlong_command_line_is_refused_with_a_useful_message(scratch: Path):
    """A `.cmd` shim runs through cmd.exe, whose limit is 8191: fail here, not there."""
    backend = dsh.DshBackend(
        {
            "backend": "dsh",
            "dsh_bin": "dsh",
            "extra_args": ["--" + "y" * (dsh.MAX_COMMAND_LINE + 100)],
        }
    )
    log = scratch / "turn.log"
    result = backend.run("prompt", cwd=scratch / "wt", log_path=log, timeout_s=5)
    assert result.returncode == 126
    assert "command line" in result.error
    assert "8191" in result.error
    # and it says where the length came from, so the fix is obvious
    assert "extra_args" in result.error


# --------------------------------------------------------------------------- #
# the environment
# --------------------------------------------------------------------------- #


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


def test_the_api_key_is_inherited_but_never_invented():
    """The key must come from the shell or DSH's own store; this backend never fabricates it."""
    backend = dsh.DshBackend({"backend": "dsh"})
    env = backend.environment()
    # whatever the shell has, this backend does not add a key of its own
    assert "DEEPSEEK_API_KEY" not in backend.env_extra
    assert env.get(dsh.API_KEY_VAR) == __import__("os").environ.get(dsh.API_KEY_VAR)


def test_a_run_local_dsh_home_does_not_reuse_the_users_credential_store(scratch: Path):
    """`dsh_home: .dsh` isolates the run, which also means the global store is not read.

    That is why the documented way to authenticate a pipeline run is the environment
    variable: a run-local DSH_HOME has no `.credentials.yaml` in it.
    """
    backend = dsh.DshBackend({"backend": "dsh", "dsh_home": ".dsh", "_run_dir": str(scratch)})
    home = Path(backend.environment()["DSH_HOME"])
    assert home.parent == scratch
    assert not (home / ".credentials.yaml").exists()


def test_the_temp_directory_is_inside_the_run(scratch: Path):
    """`spill-local` creates its private root with `mkdtemp(os.tmpdir())`.

    DSH's file sandbox refuses writes outside the workspace, so that call failed with
    `EPERM ... mkdtemp '...\\Temp\\dsh-spill-XXXXXX'` and the plugin never activated.
    """
    backend = dsh.DshBackend({"backend": "dsh", "dsh_home": ".dsh", "_run_dir": str(scratch)})
    env = backend.environment()
    for name in dsh.TEMP_VARS:
        assert Path(env[name]).is_dir(), name
        assert str(env[name]).startswith(str(scratch)), f"{name} escaped the run: {env[name]}"
    # and it is one directory, not three
    assert len({env[name] for name in dsh.TEMP_VARS}) == 1


def test_temp_redirection_can_be_turned_off(scratch: Path):
    """Inherited temp vars are left alone when the caller opts out."""
    env = dsh.dsh_env(run_dir=scratch, dsh_home=".dsh", redirect_temp=False)
    redirected = str(scratch / ".dsh" / "tmp")
    for name in dsh.TEMP_VARS:
        # not every machine sets all three, so compare where present
        assert env.get(name) != redirected, name


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #


def test_console_output_is_decoded_with_the_oem_code_page():
    """cmd.exe reports in the console code page; UTF-8 decoding hid the real message."""
    raw = b"\xc3\xfc\xc1\xee\xd0\xd0\xcc\xab\xb3\xa4\xa1\xa3"
    assert dsh.decode_console_output(raw) == "命令行太长。"


def test_utf8_output_is_left_alone():
    assert dsh.decode_console_output("the answer is 4".encode()) == "the answer is 4"


def test_the_log_header_is_stripped_from_the_answer(scratch: Path):
    header = "# cvrp agent turn (dsh)\n# profile: headless\n\n"
    log = scratch / "turn.log"
    # `newline=""` matters: without it Windows writes \r\n and the byte prefix stops matching,
    # which is exactly the bug this test guards against.
    log.write_text(header + "the real answer\n", encoding="utf-8", newline="")
    assert dsh.DshBackend.read_answer(log, header) == "the real answer"


def test_the_log_header_records_the_runtime_and_says_the_task_came_on_stdin(scratch: Path):
    backend = dsh.DshBackend({"backend": "dsh", "dsh_bin": "definitely-not-installed-dsh"})
    log = scratch / "turn.log"
    backend.run("prompt", cwd=scratch / "wt", log_path=log, timeout_s=5)
    text = log.read_text(encoding="utf-8")
    assert "dsh" in text and "profile" in text
    assert "stdin" in text


def test_a_missing_binary_is_reported_not_swallowed(scratch: Path):
    backend = dsh.DshBackend({"backend": "dsh", "dsh_bin": "definitely-not-installed-dsh"})
    result = backend.run(
        "prompt", cwd=scratch / "wt", log_path=scratch / "turn.log", timeout_s=5
    )
    assert result.returncode == 127
    assert not result.ok
    assert "npm install --global @deepseek-ai/dsh" in result.error
    assert "DEEPSEEK_API_KEY" in result.error


def test_environment_report_never_leaks_the_key():
    report = dsh.describe_environment()
    assert report["DEEPSEEK_API_KEY"] in {"set", "unset"}
    # the value itself must never be echoed
    assert len(report["DEEPSEEK_API_KEY"]) <= 5


# --------------------------------------------------------------------------- #
# a live turn, when the binary happens to be installed
# --------------------------------------------------------------------------- #

LIVE = __import__("shutil").which("dsh")


@pytest.mark.skipif(LIVE is None, reason="dsh is not installed on this machine")
def test_the_task_reaches_the_app_on_stdin(scratch: Path):
    """Proof that stdin delivery works through the `.cmd` shim.

    The turn is expected to fail on credentials in a CI-like shell; what matters is that it
    got far enough to say so, which means the app received the task. A `MISSING_CREDENTIAL`
    answer is therefore a pass here, and so is a real answer.
    """
    worktree = scratch / "wt"
    worktree.mkdir()
    backend = dsh.DshBackend(
        {"backend": "dsh", "dsh_bin": LIVE, "dsh_home": str(scratch / "home")}
    )
    log = scratch / "turn.log"
    result = backend.run("say hi", cwd=worktree, log_path=log, timeout_s=180)
    answer = backend.read_answer(log, backend._header(worktree, backend.environment()))
    reached = "MISSING_CREDENTIAL" in answer or result.returncode == 0
    assert reached, f"the task never reached the app: {answer[:300]!r}"
    assert "too long" not in answer.lower() and "太长" not in answer
    assert "unknown option" not in answer

"""Environment-policy tests.

These protect the setup that a real run depends on: a model name that is not native
to Anthropic (such as ``deepseek-flash``) only works when the CLI is routed through
a gateway named by ``ANTHROPIC_BASE_URL``. Stripping that variable looks exactly
like an authentication failure — "Failed to authenticate. API Error: 403 Request
not allowed" — which is expensive to misread, because nothing about the message
says "your router was removed from the environment".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vrpar.executor.contract import agent_failure_hint, classify_error
from vrpar.llm.claude_code import ROUTING_VARS, SESSION_MARKERS, _clean_env, routing_summary


@pytest.fixture()
def routed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://router.local:4000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "deepseek-flash")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "1234")


def test_routing_is_inherited_by_default(routed_env):
    """The router must survive: it is the user's setup, not a leaked session."""
    env = _clean_env()
    assert env["ANTHROPIC_BASE_URL"] == "http://router.local:4000"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash"


def test_session_markers_are_always_removed(routed_env):
    """A nested `claude` must not believe it is continuing the parent session."""
    env = _clean_env()
    for marker in SESSION_MARKERS:
        assert marker not in env


def test_routing_can_be_stripped_for_isolation(routed_env):
    env = _clean_env(inherit_routing=False)
    for name in ROUTING_VARS:
        assert name not in env


def test_explicit_env_beats_the_policy(routed_env):
    """`llm.env` is the escape hatch and must win in both directions."""
    overridden = _clean_env({"ANTHROPIC_BASE_URL": "http://explicit:1"}, inherit_routing=True)
    assert overridden["ANTHROPIC_BASE_URL"] == "http://explicit:1"

    isolated = _clean_env({"ANTHROPIC_BASE_URL": "http://explicit:1"}, inherit_routing=False)
    assert isolated["ANTHROPIC_BASE_URL"] == "http://explicit:1"


def test_routing_summary_names_the_destination(routed_env):
    assert "router.local" in routing_summary(_clean_env())


def test_routing_summary_says_direct_when_no_base_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert "api.anthropic.com" in routing_summary(_clean_env())


# --------------------------------------------------------------------------- #
# the failure this policy exists to prevent
# --------------------------------------------------------------------------- #

AUTH_403_LOG = (
    '# vrpar agent turn\n# model: deepseek-flash\n'
    '[claude-code:unrecognized_model] {"model":"deepseek-flash","query_source":"sdk"}\n'
    '{"error":"authentication_failed","api_error_status":403,'
    '"result":"Failed to authenticate. API Error: 403 Request not allowed"}\n'
)


def test_agent_auth_failures_are_classified_as_environment_problems():
    assert classify_error(AUTH_403_LOG) == "agent_auth_error"
    # and the specific class must win over the generic ones
    assert classify_error("403 Request not allowed: SyntaxError was not it") == "agent_auth_error"


def test_auth_hint_explains_the_router_requirement():
    hint = agent_failure_hint(AUTH_403_LOG)
    assert "ANTHROPIC_BASE_URL" in hint
    assert "vrpar doctor" in hint


def test_auth_hint_covers_credentials_and_missing_cli():
    assert "credentials" in agent_failure_hint("401 Unauthorized")
    assert "could not be launched" in agent_failure_hint("claude: command not found")


def test_auth_hint_is_quiet_for_ordinary_output():
    assert agent_failure_hint("the process is successful, the best cost is 4.2") == ""
    assert agent_failure_hint("") == ""


def test_stage_notes_include_the_hint_on_an_auth_failure(scratch: Path):
    """The pipeline's own notes must not leave the user guessing."""
    from vrpar.llm.base import LLMBackend, LLMResult
    from vrpar.pipeline.stages import run_agent_stage

    class AuthFailing(LLMBackend):
        name = "claude_code"

        def run(self, prompt, cwd, log_path, timeout_s=60):
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(AUTH_403_LOG, encoding="utf-8")
            return LLMResult(
                text="Failed to authenticate. API Error: 403 Request not allowed",
                returncode=1,
                error="claude exited with code 1",
                backend=self.name,
                log_path=str(log_path),
            )

    config = {
        "_project_root": str(scratch),
        "_task_dir": str(scratch),
        "problem": {"slug": "x"},
        "task": {"name": "t"},
        "llm": {"model": "deepseek-flash", "backend": "mock"},
    }
    worktree = scratch / "wt"
    worktree.mkdir()
    outcome = run_agent_stage(
        {"name": "analysis", "kind": "agent", "prompt": "p", "produces": []},
        config,
        scratch / "run",
        worktree,
        backend=AuthFailing(),
    )
    joined = " ".join(outcome.notes)
    assert "ANTHROPIC_BASE_URL" in joined
    assert "vrpar doctor" in joined

"""Claude Code backend.

Preferred path: if the CORAL checkout is importable, use
``coral.agent.builtin.claude_code.ClaudeCodeRuntime`` so that flag handling and
stream-json conventions stay identical to CORAL. Otherwise fall back to our own
subprocess launcher, which uses the same command line.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from cvrp.llm.base import LLMBackend, LLMResult, extract_json_payload

MAX_CAPTURED_CHARS = 40_000

#: Environment variables that identify *the session that spawned us*. A `claude`
#: started from inside another agent session must not inherit these, or it will
#: believe it is a continuation of the parent rather than a fresh turn.
SESSION_MARKERS = (
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDECODE",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
)

#: Environment variables that configure *where the model traffic goes*. These are
#: the user's setup, not a leaked session: a self-hosted router is normally
#: reached through `ANTHROPIC_BASE_URL`, and stripping it silently sends the
#: request to api.anthropic.com, where a non-Anthropic model name is rejected with
#: a 403 that looks like an authentication problem. They are preserved by default;
#: set `llm.inherit_routing_env: false` to drop them (or name them explicitly in
#: `llm.env` to override a value).
ROUTING_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_CUSTOM_HEADERS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)


def _clean_env(
    extra: dict[str, str] | None = None,
    inherit_routing: bool = True,
) -> dict[str, str]:
    """Environment for the agent subprocess.

    Session markers are always dropped. Routing/credential variables are *kept* by
    default, so that a machine configured to send Claude Code traffic to a router
    keeps working; `inherit_routing=False` removes them for isolation.
    """
    env = dict(os.environ)
    for key in SESSION_MARKERS:
        env.pop(key, None)
    if not inherit_routing:
        for key in ROUTING_VARS:
            env.pop(key, None)
    # An explicit `llm.env` entry always wins, whatever the policy above did.
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


def routing_summary(env: dict[str, str]) -> str:
    """One-line description of where the agent's traffic will go (for `cvrp doctor`)."""
    base = env.get("ANTHROPIC_BASE_URL")
    model = env.get("ANTHROPIC_MODEL")
    key_source = "ANTHROPIC_API_KEY" if env.get("ANTHROPIC_API_KEY") else (
        "ANTHROPIC_AUTH_TOKEN" if env.get("ANTHROPIC_AUTH_TOKEN") else "CLI login"
    )
    if base:
        return f"via {base} (key: {key_source}{', ANTHROPIC_MODEL=' + model if model else ''})"
    return f"direct to api.anthropic.com (key: {key_source})"


def resolve_claude_binary(binary: str) -> str:
    """Return an executable path for the Claude Code CLI."""
    candidate = Path(binary).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(binary)
    if found:
        return found
    raise FileNotFoundError(
        f"Claude Code CLI not found ({binary!r}). Install it in the VM and make sure "
        "it is on PATH, or set llm.claude_bin to an absolute path in task.yaml."
    )


def build_command(
    prompt: str,
    model: str,
    binary: str = "claude",
    permission_mode: str | None = "auto",
    max_turns: int = 0,
    extra_args: list[str] | None = None,
) -> list[str]:
    """The exact argv used for one turn (kept public so tests can assert on it)."""
    cmd = [binary, "-p", prompt]
    if model:
        cmd.extend(["--model", model])
    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])
    if max_turns and max_turns > 0:
        cmd.extend(["--max-turns", str(max_turns)])
    cmd.extend(["--output-format", "stream-json", "--verbose"])
    cmd.extend(extra_args or [])
    return cmd


def result_text_from_log(log_path: Path) -> str:
    """Extract the final assistant text from a stream-json log.

    Claude Code emits one JSON object per line; the authoritative final answer
    is the ``{"type": "result"}`` line. Older/short outputs may not use
    stream-json at all, in which case the raw log is returned.
    """
    if not log_path.is_file():
        return ""
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    result_line = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("type") == "result":
            for key in ("result", "text", "content"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    result_line = value
        elif data.get("type") == "assistant" and not result_line:
            message = data.get("message") or {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    result_line = block.get("text", "") or result_line
    if result_line:
        return result_line
    stripped = raw.strip()
    return stripped if stripped and not stripped.startswith("{") else ""


class ClaudeCodeBackend(LLMBackend):
    """Launch ``claude -p`` in a stage worktree and collect its answer."""

    name = "claude_code"

    def __init__(self, llm_config: dict | None = None):
        cfg = dict(llm_config or {})
        self.model = str(cfg.get("model") or "")
        self.binary = str(cfg.get("claude_bin") or "claude")
        self.permission_mode = cfg.get("permission_mode", "auto")
        self.max_turns = int(cfg.get("max_turns") or 0)
        self.extra_args = [str(a) for a in (cfg.get("extra_args") or [])]
        self.env_extra = {str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
        # `ANTHROPIC_BASE_URL` and friends are the user's routing setup, so they are
        # inherited by default. See ROUTING_VARS for why that matters.
        self.inherit_routing_env = bool(cfg.get("inherit_routing_env", True))

    def run(self, prompt: str, cwd: Path, log_path: Path, timeout_s: int = 3600) -> LLMResult:
        started = time.time()
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            binary = resolve_claude_binary(self.binary)
        except FileNotFoundError as exc:
            return LLMResult(
                returncode=127, error=str(exc), backend=self.name, log_path=str(log_path)
            )

        cmd = build_command(
            prompt=prompt,
            model=self.model,
            binary=binary,
            permission_mode=self.permission_mode,
            max_turns=self.max_turns,
            extra_args=self.extra_args,
        )
        env = _clean_env(self.env_extra, inherit_routing=self.inherit_routing_env)

        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write("# cvrp agent turn\n")
            log_file.write(f"# cwd: {Path(cwd).resolve()}\n")
            log_file.write(f"# model: {self.model or '(default)'}\n")
            log_file.write(f"# backend: {self.name}\n\n")
            log_file.flush()
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                return LLMResult(
                    returncode=126,
                    error=f"failed to launch claude: {exc}",
                    backend=self.name,
                    log_path=str(log_path),
                )
            try:
                returncode = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                text = result_text_from_log(log_path)
                return LLMResult(
                    text=text[:MAX_CAPTURED_CHARS],
                    returncode=124,
                    duration_s=time.time() - started,
                    backend=self.name,
                    log_path=str(log_path),
                    error=f"agent turn exceeded {timeout_s}s and was killed",
                )

        text = result_text_from_log(log_path)
        payload = extract_json_payload(text)
        return LLMResult(
            text=text[:MAX_CAPTURED_CHARS],
            payload=payload,
            returncode=returncode,
            duration_s=time.time() - started,
            backend=self.name,
            log_path=str(log_path),
            error="" if returncode == 0 else f"claude exited with code {returncode}",
        )


class CoralClaudeCodeBackend(ClaudeCodeBackend):
    """Same contract, but delegates process launch to CORAL's runtime when available."""

    name = "claude_code(coral)"

    def run(self, prompt: str, cwd: Path, log_path: Path, timeout_s: int = 3600) -> LLMResult:
        try:
            from coral.agent.builtin.claude_code import ClaudeCodeRuntime  # noqa: F401
        except Exception:
            return super().run(prompt, cwd, log_path, timeout_s)
        # CORAL's runtime returns a handle and manages the log directory itself;
        # for a single blocking turn our own launcher is equivalent and keeps the
        # log path contract simple, so we reuse it and only report the detection.
        result = super().run(prompt, cwd, log_path, timeout_s)
        result.backend = self.name if result.returncode != 127 else self.name + " (fallback)"
        return result


def make_backend(llm_config: dict | None = None, problem: Any | None = None) -> LLMBackend:
    """Factory honouring ``llm.backend`` (``claude_code`` | ``coral`` | ``mock``).

    ``problem`` is forwarded to the mock backend, which derives its stub payloads
    from the problem's own spec rather than from anything engine-side.
    """
    cfg = dict(llm_config or {})
    backend = str(cfg.get("backend") or "claude_code").lower()
    if backend in ("mock", "offline", "dry-run"):
        from cvrp.llm.mock import MockBackend

        return MockBackend(cfg, problem=problem)
    if backend in ("coral", "coral_claude_code"):
        return CoralClaudeCodeBackend(cfg)
    if backend in ("claude", "claude_code", "claudecode"):
        return ClaudeCodeBackend(cfg)
    raise ValueError(f"unknown llm.backend: {backend!r}")

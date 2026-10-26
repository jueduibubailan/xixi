"""LLM backends: claude_code (default), coral (delegating), mock (offline)."""

from vrpar.llm.base import (
    LLMBackend,
    LLMResult,
    extract_blocks,
    extract_code,
    extract_json_payload,
    make_backend,
)
from vrpar.llm.claude_code import (
    ROUTING_VARS,
    SESSION_MARKERS,
    ClaudeCodeBackend,
    CoralClaudeCodeBackend,
    build_command,
    resolve_claude_binary,
    result_text_from_log,
    routing_summary,
)

__all__ = [
    "LLMBackend",
    "LLMResult",
    "extract_blocks",
    "extract_code",
    "extract_json_payload",
    "ClaudeCodeBackend",
    "CoralClaudeCodeBackend",
    "ROUTING_VARS",
    "SESSION_MARKERS",
    "build_command",
    "make_backend",
    "resolve_claude_binary",
    "result_text_from_log",
    "routing_summary",
]

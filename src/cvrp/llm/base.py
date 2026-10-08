"""LLM backend abstraction.

A backend runs *one agent turn*: it is handed a fully-rendered prompt plus a
working directory, and returns whatever the agent said. It never touches the
filesystem outside that working directory -- agents write their own artifacts,
the orchestrator only reads the declared paths afterwards.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMResult:
    """Outcome of one agent turn."""

    text: str = ""
    payload: dict[str, Any] | None = None          # last ```json fenced block, parsed
    returncode: int = 0
    duration_s: float = 0.0
    backend: str = ""
    log_path: str = ""
    error: str = ""
    raw_blocks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.error

    def summary(self, limit: int = 400) -> str:
        head = " ".join(self.text.split())[:limit]
        return f"[{self.backend}] rc={self.returncode} {self.duration_s:.1f}s {head}"


class LLMBackend:
    """Protocol-ish base class. Subclasses implement :meth:`run`."""

    name = "base"

    def run(self, prompt: str, cwd: Path, log_path: Path, timeout_s: int) -> LLMResult:
        raise NotImplementedError


def make_backend(llm_config: dict[str, Any] | None = None, problem: Any | None = None) -> LLMBackend:
    """Factory honouring ``llm.backend`` (``claude_code`` | ``coral`` | ``mock``).

    Defined here as a lazy wrapper so callers can depend on ``cvrp.llm.base``
    alone without creating an import cycle with the backend modules.
    """
    from cvrp.llm.claude_code import make_backend as _make_backend

    return _make_backend(llm_config, problem=problem)


# --------------------------------------------------------------------------- #
# parsing helpers shared by all backends
# --------------------------------------------------------------------------- #

FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)


def extract_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``(language, body)`` for every fenced block in ``text``."""
    return [(match.group(1).strip().lower(), match.group(2).strip()) for match in FENCE_RE.finditer(text)]


def extract_code(text: str, preferred: tuple[str, ...] = ("python", "py", "")) -> str | None:
    """Best-effort last python block, mirroring AFL's ``extract_code``."""
    blocks = extract_blocks(text)
    for language, body in reversed(blocks):
        if language in preferred:
            return body
    return blocks[-1][1] if blocks else None


def extract_json_payload(text: str) -> dict[str, Any] | None:
    """Last ```json block, or the last balanced ``{...}`` object, parsed."""
    for language, body in reversed(extract_blocks(text)):
        if language in ("json", "json5", "jsonc"):
            parsed = _try_json(body)
            if parsed is not None:
                return parsed
    for language, body in reversed(extract_blocks(text)):
        parsed = _try_json(body)
        if parsed is not None:
            return parsed
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    parsed = _try_json(text[start : index + 1])
                    if parsed is not None:
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def _try_json(body: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None

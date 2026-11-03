"""Score extraction for the post-training final-test run."""

from __future__ import annotations

import re

AUG_SCORE_RE = re.compile(r"AUGMENTATION SCORE:\s*([-+]?\d+(?:\.\d+)?)")


def parse_score(stdout: str) -> float | None:
    """Return the last ``AUGMENTATION SCORE:`` value, or ``None``."""
    matches = AUG_SCORE_RE.findall(stdout or "")
    return float(matches[-1]) if matches else None


__all__ = ["AUG_SCORE_RE", "parse_score"]

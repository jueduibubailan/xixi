"""The only gate in the pipeline: did code_error produce a score?

The operator's success condition is deliberately mechanical:

1. ``test_train.py`` contains ``epochs: 1`` and ``train_episodes: 64``;
2. ``test_train.py`` exited 0;
3. the final test entry (``test_*.py``) exited 0;
4. that test entry stdout contains an ``AUGMENTATION SCORE:`` line.

The score is the last such line.  No feasibility, no plan audit, no code review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRAIN_PIN_EPOCHS = re.compile(r"['\"]epochs['\"]\s*:\s*1\b")
TRAIN_PIN_EPISODES = re.compile(r"['\"]train_episodes['\"]\s*:\s*64\b")
AUG_SCORE_RE = re.compile(r"AUGMENTATION SCORE:\s*([-+]?\d+(?:\.\d+)?)")


@dataclass
class TestCheck:
    passed: bool
    score: float | None = None
    train_exit: int | None = None
    test_exit: int | None = None
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.passed:
            return f"score={self.score}"
        return "FAILED - " + "; ".join(self.issues[:4])


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _exit_code(path: Path) -> int | None:
    text = _read(path).strip().splitlines()
    if not text:
        return None
    try:
        return int(text[-1].strip())
    except ValueError:
        return None


def parse_score(text: str) -> float | None:
    matches = AUG_SCORE_RE.findall(text or "")
    return float(matches[-1]) if matches else None


def check_code_error(run_dir: Path) -> TestCheck:
    """Check the artifacts code_error was asked to leave behind."""
    run_dir = Path(run_dir)
    generated = run_dir / "artifacts" / "generated"
    runs = run_dir / "artifacts" / "runs"
    issues: list[str] = []

    test_train = generated / "test_train.py"
    body = _read(test_train)
    if not body:
        issues.append("artifacts/generated/test_train.py is missing")
    else:
        if not TRAIN_PIN_EPOCHS.search(body):
            issues.append("test_train.py must set 'epochs': 1")
        if not TRAIN_PIN_EPISODES.search(body):
            issues.append("test_train.py must set 'train_episodes': 64")

    train_exit = _exit_code(runs / "test_train.exitcode")
    test_exit = _exit_code(runs / "test.exitcode")
    if train_exit is None:
        issues.append("artifacts/runs/test_train.exitcode is missing")
    elif train_exit != 0:
        issues.append(f"test_train.py exited {train_exit}")
    if test_exit is None:
        issues.append("artifacts/runs/test.exitcode is missing")
    elif test_exit != 0:
        issues.append(f"the final test entry exited {test_exit}")

    stdout = _read(runs / "test.stdout.txt")
    score = parse_score(stdout)
    if score is None:
        issues.append("the final test entry stdout has no 'AUGMENTATION SCORE:' line")

    return TestCheck(
        passed=not issues,
        score=score,
        train_exit=train_exit,
        test_exit=test_exit,
        issues=issues,
    )


def check_report(check: TestCheck) -> dict[str, Any]:
    return {
        "passed": check.passed,
        "score": check.score,
        "train_exit": check.train_exit,
        "test_exit": check.test_exit,
        "issues": list(check.issues),
        "warnings": list(check.warnings),
    }


__all__ = ["AUG_SCORE_RE", "TestCheck", "check_code_error", "check_report", "parse_score"]

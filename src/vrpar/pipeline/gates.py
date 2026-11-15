"""The only gate in the pipeline: did code_error produce a score?

The operator's success condition is deliberately mechanical:

1. ``test_train.py`` contains the protocol's ``epochs`` and ``train_episodes`` pins;
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

DEFAULT_PINS = {"epochs": 200, "train_episodes": 5000}
TRAIN_PIN_TEMPLATE = r"['\"]{name}['\"]\s*:\s*{value}\b"
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


def pin_patterns(pins: dict[str, Any] | None = None) -> dict[str, tuple[int, re.Pattern[str]]]:
    merged = dict(DEFAULT_PINS)
    merged.update({key: value for key, value in (pins or {}).items() if value is not None})
    return {
        name: (int(value), re.compile(TRAIN_PIN_TEMPLATE.format(name=name, value=int(value))))
        for name, value in merged.items()
        if name in {"epochs", "train_episodes"}
    }


def check_code_error(
    run_dir: Path,
    report_dir: str = "artifacts/runs",
    pins: dict[str, Any] | None = None,
) -> TestCheck:
    """Check the artifacts one code_error execution was asked to leave behind."""
    run_dir = Path(run_dir)
    generated = run_dir / "artifacts" / "generated"
    runs = run_dir / report_dir
    issues: list[str] = []

    test_train = generated / "test_train.py"
    body = _read(test_train)
    if not body:
        issues.append(f"{generated.name}/test_train.py is missing")
    else:
        for name, (value, pattern) in pin_patterns(pins).items():
            if not pattern.search(body):
                issues.append(f"test_train.py must set '{name}': {value}")

    train_exit = _exit_code(runs / "test_train.exitcode")
    test_exit = _exit_code(runs / "test.exitcode")
    if train_exit is None:
        issues.append(f"{report_dir}/test_train.exitcode is missing")
    elif train_exit != 0:
        issues.append(f"test_train.py exited {train_exit}")
    if test_exit is None:
        issues.append(f"{report_dir}/test.exitcode is missing")
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


def check_report(
    check: TestCheck,
    *,
    protocol: str | None = None,
    plan_id: str | None = None,
    pins: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "passed": check.passed,
        "score": check.score,
        "train_exit": check.train_exit,
        "test_exit": check.test_exit,
        "issues": list(check.issues),
        "warnings": list(check.warnings),
        "protocol": protocol,
        "plan_id": plan_id,
        "pins": dict(pins or DEFAULT_PINS),
    }


__all__ = [
    "AUG_SCORE_RE",
    "DEFAULT_PINS",
    "TestCheck",
    "check_code_error",
    "check_report",
    "parse_score",
    "pin_patterns",
]

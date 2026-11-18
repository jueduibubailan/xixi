"""Captured stdout must be read the way the stage actually wrote it.

The ``code_error`` stage agent captures a child's output with a shell redirect. On a
Windows host that shell is often Windows PowerShell 5.1, whose ``>`` writes UTF-16LE;
the acceptance gate hard-coded UTF-8 and reported a missing score for a run that had
printed it. These tests pin both the decoder and the gate contract.
"""

from __future__ import annotations

from pathlib import Path

from vrpar.pipeline import gates
from vrpar.run.run_dir import decode_text, read_text

SCORE_STDOUT = (
    "[2026-11-18 08:22:23] OVRPLTester.py(90) :  *** Test Done *** \n"
    "[2026-11-18 08:22:23] OVRPLTester.py(91) :  NO-AUG SCORE: 6.9286 \n"
    "[2026-11-18 08:22:23] OVRPLTester.py(92) :  AUGMENTATION SCORE: 6.7579 \n"
)


def _run_with_stdout(scratch: Path, stdout_bytes: bytes) -> Path:
    """A minimal run directory that satisfies every other acceptance condition."""
    run = scratch / "run"
    generated = run / "artifacts" / "generated"
    generated.mkdir(parents=True)
    (generated / "test_train.py").write_text(
        "PARAMS = {'epochs': 200, 'train_episodes': 5000}\n", encoding="utf-8"
    )
    reports = run / "artifacts" / "runs" / "final"
    reports.mkdir(parents=True)
    (reports / "test_train.exitcode").write_text("0\n", encoding="ascii")
    (reports / "test.exitcode").write_text("0\n", encoding="ascii")
    (reports / "test.stdout.txt").write_bytes(stdout_bytes)
    return run


def test_decode_text_reads_utf16_with_bom() -> None:
    assert decode_text(SCORE_STDOUT.encode("utf-16")) == SCORE_STDOUT


def test_decode_text_reads_utf8_with_bom() -> None:
    assert decode_text(SCORE_STDOUT.encode("utf-8-sig")) == SCORE_STDOUT


def test_decode_text_falls_back_to_utf8_without_bom() -> None:
    assert decode_text(SCORE_STDOUT.encode("utf-8")) == SCORE_STDOUT


def test_read_text_handles_a_utf16_file(scratch: Path) -> None:
    target = scratch / "test.stdout.txt"
    target.write_text(SCORE_STDOUT, encoding="utf-16")
    assert "AUGMENTATION SCORE: 6.7579" in read_text(target)


def test_gate_accepts_utf16_stdout(scratch: Path) -> None:
    run = _run_with_stdout(scratch, SCORE_STDOUT.encode("utf-16"))
    check = gates.check_code_error(run, report_dir="artifacts/runs/final")
    assert check.passed, check.issues
    assert check.score == 6.7579


def test_gate_still_rejects_a_missing_score_line(scratch: Path) -> None:
    no_score = SCORE_STDOUT.replace("AUGMENTATION SCORE: 6.7579", "no score here")
    run = _run_with_stdout(scratch, no_score.encode("utf-16"))
    check = gates.check_code_error(run, report_dir="artifacts/runs/final")
    assert not check.passed
    assert any("AUGMENTATION SCORE" in issue for issue in check.issues)

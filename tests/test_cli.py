"""CLI discovery and doctor checks that do not need an agent runtime."""

from __future__ import annotations

from pathlib import Path

from vrpar import cli

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "problems" / "mvmoe_ovrpl_n50" / "task.yaml"


def test_problems_lists_bundles_without_a_task(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    assert cli.main(["problems"]) == 0
    out = capsys.readouterr().out
    assert "mvmoe_ovrpl_n50" in out
    assert "reference" in out


def test_doctor_accepts_the_offline_backend(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    code = cli.main(["doctor", str(TASK), "llm.backend=mock"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "agent CLI: not needed" in out
    assert "baseline : OK" in out
    assert "prompts  : OK" in out

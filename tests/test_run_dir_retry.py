"""JSON writes survive a reader holding the file open (Windows)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vrpar.run.run_dir import write_json


def test_write_json_retries_a_locked_target(scratch: Path, monkeypatch):
    target = scratch / "state.json"
    calls = {"n": 0}
    real_replace = os.replace

    def flaky(source, destination):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "Access is denied", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky)

    write_json(target, {"status": "running", "plans": {"total": 1}})

    assert calls["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "running"
    assert not (scratch / "state.json.tmp").exists()


def test_write_json_reports_a_permanent_lock(scratch: Path, monkeypatch):
    target = scratch / "state.json"

    def always_locked(source, destination):
        raise PermissionError(13, "Access is denied", str(destination))

    monkeypatch.setattr(os, "replace", always_locked)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(OSError, match="could not replace"):
        write_json(target, {"status": "running"})

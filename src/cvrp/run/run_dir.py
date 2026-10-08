"""Run directory layout.

Every ``cvrp start`` creates::

    results/<task-slug>/<YYYYmmdd-HHMMSS>/
    ├─ config.resolved.yaml     # exactly what this run used
    ├─ state.json               # append-only stage ledger (single source of truth)
    ├─ artifacts/
    │  ├─ analysis/             # S1 outputs  (analysis.json, plan.md)
    │  ├─ generated/            # S2/S4 outputs (the synthesized POMO solver)
    │  ├─ revisions/            # S4 per-round attribution + diffs
    │  └─ runs/                 # S3 run_report.json, S5 feasibility.json, raw stdout
    ├─ checkpoints/             # trained / fine-tuned weights produced by the run
    ├─ logs/                    # per-stage agent logs + pipeline.log
    └─ worktrees/               # per-stage working copies seeded from baseline/
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SUB_DIRS = (
    "artifacts/analysis",
    "artifacts/generated",
    "artifacts/revisions",
    "artifacts/runs",
    "checkpoints",
    "logs",
    "worktrees",
)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def make_run_dir(results_dir: Path, slug: str, run_id: str | None = None) -> Path:
    run_id = run_id or timestamp()
    run_dir = Path(results_dir) / slug / run_id
    counter = 1
    while run_dir.exists():
        run_dir = Path(results_dir) / slug / f"{run_id}-{counter}"
        counter += 1
    for sub in SUB_DIRS:
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    return run_dir


def latest_run_dir(results_dir: Path, slug: str | None = None) -> Path | None:
    results = Path(results_dir)
    if not results.is_dir():
        return None
    task_dirs = [results / slug] if slug else [p for p in results.iterdir() if p.is_dir()]
    candidates: list[Path] = []
    for task_dir in task_dirs:
        if not task_dir.is_dir():
            continue
        candidates.extend(p for p in task_dir.iterdir() if p.is_dir() and (p / "state.json").is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_id_of(run_dir: Path) -> str:
    return run_dir.name


# --------------------------------------------------------------------------- #
# tiny json / text helpers used across the pipeline
# --------------------------------------------------------------------------- #


def write_json(path: Path, payload: Any, default: Any = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=default)
    os.replace(tmp, path)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def append_log(path: Path, message: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {message}\n")


def read_text(path: Path, max_chars: int = 200_000) -> str:
    path = Path(path)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[TRUNCATED: original length={len(text)} chars]"
    return text

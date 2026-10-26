"""Pipeline state ledger.

``state.json`` is the single source of truth for both the orchestrator and the
web UI. The orchestrator appends one record per stage transition; the UI only
ever reads. Keeping it append-only means an interrupted run is diagnosable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vrpar.run.run_dir import read_json, write_json

STATE_FILE = "state.json"

# stage status values
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass
class StageRecord:
    name: str
    kind: str = "agent"
    status: str = PENDING
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "attempts": self.attempts,
            "message": self.message,
            "artifacts": list(self.artifacts),
        }
        if self.started_at:
            payload["started_at"] = self.started_at
            payload["duration_s"] = round(
                (self.finished_at or time.time()) - self.started_at, 2
            )
        if self.error:
            payload["error"] = self.error
        return payload


class RunState:
    """Mutable view over ``state.json``."""

    def __init__(self, run_dir: Path, config: dict[str, Any]):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / STATE_FILE
        self.config = config
        existing = read_json(self.path, default=None) or {}
        self.data: dict[str, Any] = {
            "run_id": self.run_dir.name,
            "task": (config.get("task") or {}).get("name", "task"),
            "task_slug": (config.get("task") or {}).get("slug", ""),
            "started_at": existing.get("started_at") or time.time(),
            "finished_at": existing.get("finished_at"),
            "status": existing.get("status") or "running",
            "round": int(existing.get("round", 0)),
            "best_cost": existing.get("best_cost"),
            "initial_cost": existing.get("initial_cost"),
            "stages": existing.get("stages") or {},
            "history": existing.get("history") or [],
            "instruction_queue": existing.get("instruction_queue") or [],
        }
        for stage in config.get("stages", []):
            self.data["stages"].setdefault(
                stage["name"],
                StageRecord(name=stage["name"], kind=stage.get("kind", "agent")).as_dict(),
            )

    # -- persistence ------------------------------------------------------- #

    def save(self) -> None:
        write_json(self.path, self.data)

    def record_event(self, kind: str, **fields: Any) -> None:
        self.data["history"].append({"at": time.time(), "kind": kind, **fields})
        # keep the ledger bounded so state.json stays readable in the UI
        if len(self.data["history"]) > 2000:
            self.data["history"] = self.data["history"][-1000:]
        self.save()

    # -- stage transitions -------------------------------------------------- #

    def start_stage(self, name: str, note: str = "") -> None:
        record = self.data["stages"].setdefault(name, {"name": name, "status": PENDING})
        record.update(
            {
                "status": RUNNING,
                "started_at": time.time(),
                "attempts": int(record.get("attempts", 0)) + 1,
                "message": note or record.get("message", ""),
                "error": "",
            }
        )
        self.record_event("stage_start", stage=name, note=note)

    def finish_stage(
        self,
        name: str,
        status: str = DONE,
        message: str = "",
        artifacts: list[str] | None = None,
        error: str = "",
    ) -> None:
        record = self.data["stages"].setdefault(name, {"name": name, "status": PENDING})
        started_at = record.get("started_at") or time.time()
        finished_at = time.time()
        record.update(
            {
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                # Mirrored onto the record so readers (REPORT.md, the dashboard)
                # do not have to recompute it from two timestamps.
                "duration_s": round(finished_at - started_at, 2),
                "message": message or record.get("message", ""),
                "artifacts": artifacts or record.get("artifacts", []),
                "error": error,
            }
        )
        self.record_event("stage_finish", stage=name, status=status, message=message)
        self.save()

    def set_stage_message(self, name: str, message: str) -> None:
        record = self.data["stages"].setdefault(name, {"name": name, "status": PENDING})
        record["message"] = message
        self.save()

    def stage(self, name: str) -> dict[str, Any]:
        return self.data["stages"].setdefault(name, {"name": name, "status": PENDING})

    def stage_status(self, name: str) -> str:
        return str(self.stage(name).get("status", PENDING))

    # -- run level ---------------------------------------------------------- #

    def set_round(self, round_index: int) -> None:
        self.data["round"] = int(round_index)
        self.save()

    def set_costs(self, initial_cost: float | None, best_cost: float | None) -> None:
        if initial_cost is not None:
            self.data["initial_cost"] = initial_cost
        if best_cost is not None:
            self.data["best_cost"] = best_cost
        self.save()

    def finish(self, status: str = "completed") -> None:
        self.data["status"] = status
        self.data["finished_at"] = time.time()
        self.save()

    def queue_instruction(self, text: str, target_stage: str | None = None) -> None:
        self.data["instruction_queue"].append(
            {"at": time.time(), "text": text, "target_stage": target_stage, "consumed": False}
        )
        self.save()

    def consume_instructions(self, target_stage: str) -> list[str]:
        """Hand back queued human instructions for a stage (steering channel)."""
        picked: list[str] = []
        for entry in self.data["instruction_queue"]:
            if entry.get("consumed"):
                continue
            target = entry.get("target_stage")
            if target in (None, "", target_stage):
                entry["consumed"] = True
                picked.append(str(entry.get("text", "")))
        if picked:
            self.save()
        return picked

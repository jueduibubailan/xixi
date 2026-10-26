"""JSON API for the vrpar dashboard.

Read-only over the run directory, plus one write channel (``POST /api/steer``)
that appends an operator instruction to ``state.json`` for the next stage to
consume -- the same idea as CORAL's steering queue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrpar.config import task_slug
from vrpar.run.run_dir import latest_run_dir, read_json
from vrpar.run.state import RunState

TEXT_SUFFIXES = {".md", ".txt", ".log", ".json", ".py", ".yaml", ".yml", ".csv"}
MAX_TEXT_BYTES = 400_000


class RunContext:
    """Where the dashboard reads from."""

    def __init__(self, config: dict[str, Any], root: Path):
        self.config = config
        self.root = Path(root)
        workspace = config.get("workspace") or {}
        results = Path(str(workspace.get("results_dir") or "./results"))
        self.results_dir = results if results.is_absolute() else self.root / results
        self.slug = task_slug(config)

    def latest(self) -> Path | None:
        return latest_run_dir(self.results_dir, self.slug) or latest_run_dir(self.results_dir)

    def resolve(self, run: str | None) -> Path | None:
        if run:
            candidate = Path(run)
            if not candidate.is_absolute():
                candidate = self.results_dir / self.slug / run
            if candidate.is_dir() and (candidate / "state.json").is_file():
                return candidate
            return None
        return self.latest()

    def all_runs(self) -> list[dict[str, Any]]:
        if not self.results_dir.is_dir():
            return []
        runs: list[dict[str, Any]] = []
        for task_dir in sorted(self.results_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            for run_dir in sorted(task_dir.iterdir(), reverse=True):
                state_file = run_dir / "state.json"
                if not state_file.is_file():
                    continue
                state = read_json(state_file, default={}) or {}
                runs.append(
                    {
                        "task": task_dir.name,
                        "run_id": run_dir.name,
                        "path": str(run_dir),
                        "status": state.get("status"),
                        "best_cost": state.get("best_cost"),
                        "started_at": state.get("started_at"),
                        "finished_at": state.get("finished_at"),
                        "round": state.get("round"),
                    }
                )
        return runs

    def safe_path(self, run_dir: Path, relative: str) -> Path | None:
        """Resolve ``relative`` under ``run_dir``, refusing traversal."""
        if not relative:
            return None
        candidate = (Path(run_dir) / relative).resolve()
        try:
            candidate.relative_to(Path(run_dir).resolve())
        except ValueError:
            return None
        return candidate


def _state_of(run_dir: Path) -> dict[str, Any]:
    return read_json(Path(run_dir) / "state.json", default={}) or {}


def _json(payload: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #


async def get_health(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    return _json(
        {
            "ok": True,
            "task": (context.config.get("task") or {}).get("name"),
            "slug": context.slug,
            "model": (context.config.get("llm") or {}).get("model"),
            "backend": (context.config.get("llm") or {}).get("backend"),
            "run_dir": str(run_dir) if run_dir else None,
            "coral": request.app.state.coral,
            "stages": [s["name"] for s in context.config.get("stages", [])],
        }
    )


async def get_status(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    if run_dir is None:
        return _json({"exists": False, "message": "no run yet - start one with `uv run vrpar start task.yaml`"})
    state = _state_of(run_dir)
    state["exists"] = True
    state["run_dir"] = str(run_dir)
    return _json(state)


async def get_runs(request: Request) -> Response:
    context: RunContext = request.app.state.context
    return _json({"runs": context.all_runs(), "current": str(context.latest() or "")})


async def get_stages(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    if run_dir is None:
        return _json({"stages": []})
    state = _state_of(run_dir)
    stage_records = state.get("stages") or {}
    payload: list[dict[str, Any]] = []
    for stage_config in context.config.get("stages", []):
        name = stage_config["name"]
        record = dict(stage_records.get(name) or {"name": name, "status": "pending"})
        # `kind` is the execution mechanism, `does` is the label the UI shows. They differ
        # only for `code_error`, which is a deterministic executor that also spends one
        # model turn narrating a failure. Both are sent so the payload stays honest.
        record["kind"] = stage_config.get("kind", "agent")
        record["does"] = stage_config.get("does") or record["kind"]
        record["produces"] = stage_config.get("produces") or []
        present: list[str] = []
        # Read the stage's own declared outputs. There used to be a second table here, which
        # drifted from `produces` and listed conditional files as if they were always
        # produced -- so a healthy run could show them as missing.
        for relative in stage_config.get("produces") or []:
            if (run_dir / relative).exists():
                present.append(relative)
        record["present"] = present
        payload.append(record)
    return _json({"run_dir": str(run_dir), "round": state.get("round"), "stages": payload})


async def get_artifacts(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    if run_dir is None:
        return _json({"entries": []})
    base = request.query_params.get("dir") or "artifacts"
    target = context.safe_path(run_dir, base)
    if target is None or not target.is_dir():
        return _json({"entries": [], "error": f"not a directory: {base}"}, status=404)
    entries: list[dict[str, Any]] = []
    for entry in sorted(target.rglob("*")):
        if entry.is_dir():
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        entries.append(
            {
                "path": str(entry.relative_to(run_dir)).replace("\\", "/"),
                "size": size,
                "suffix": entry.suffix,
            }
        )
        if len(entries) >= 400:
            break
    return _json({"entries": entries})


async def get_file(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    relative = request.query_params.get("path") or ""
    if run_dir is None:
        return _json({"error": "no run"}, status=404)
    target = context.safe_path(run_dir, relative)
    if target is None or not target.is_file():
        return _json({"error": f"not a file: {relative}"}, status=404)
    if target.stat().st_size > MAX_TEXT_BYTES:
        return _json({"error": "file too large to display", "size": target.stat().st_size}, status=413)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _json({"error": str(exc)}, status=500)
    return _json({"path": relative, "text": text})


async def get_logs(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    if run_dir is None:
        return _json({"logs": []})
    logs_dir = run_dir / "logs"
    items: list[dict[str, Any]] = []
    if logs_dir.is_dir():
        for entry in sorted(logs_dir.iterdir()):
            if entry.is_file():
                items.append(
                    {"name": entry.name, "size": entry.stat().st_size, "path": f"logs/{entry.name}"}
                )
    return _json({"logs": items})


async def get_log(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    name = request.query_params.get("name") or "pipeline.log"
    if run_dir is None:
        return _json({"error": "no run"}, status=404)
    target = context.safe_path(run_dir, f"logs/{name}")
    if target is None or not target.is_file():
        return _json({"error": f"no such log: {name}"}, status=404)
    text = target.read_text(encoding="utf-8", errors="replace")
    tail = int(request.query_params.get("tail") or 0)
    if tail > 0:
        text = "\n".join(text.splitlines()[-tail:])
    return _json({"name": name, "text": text[-MAX_TEXT_BYTES:]})


async def get_grader(request: Request) -> Response:
    context: RunContext = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    if run_dir is None:
        return _json({"exists": False})
    bundle = read_json(run_dir / "artifacts" / "runs" / "grader_result.json", default=None)
    s3 = read_json(run_dir / "artifacts" / "runs" / "s3_run_report.json", default=None)
    s5 = read_json(run_dir / "artifacts" / "runs" / "s5_feasibility.json", default=None)
    return _json({"exists": bool(bundle or s3), "grader": bundle, "run_report": s3, "feasibility": s5})


async def post_steer(request: Request) -> Response:
    context: RunContext = request.app.state.context
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json({"error": "invalid json body"}, status=400)
    text = str(body.get("text") or "").strip()
    if not text:
        return _json({"error": "text is required"}, status=400)
    stage = body.get("stage") or None
    run_dir = context.resolve(body.get("run"))
    if run_dir is None:
        return _json({"error": "no run to steer"}, status=404)
    state = RunState(run_dir, context.config)
    state.queue_instruction(text, stage)
    return _json({"ok": True, "queued": text, "target_stage": stage})

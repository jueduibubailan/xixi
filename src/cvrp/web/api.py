"""JSON API for the cvrp dashboard.

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

from cvrp.config import task_slug
from cvrp.run.run_dir import latest_run_dir, read_json
from cvrp.run.state import RunState

TEXT_SUFFIXES = {".md", ".txt", ".log", ".json", ".py", ".yaml", ".yml", ".csv"}
MAX_TEXT_BYTES = 400_000

# artifact locations, used by /api/stages to attach statuses
STAGE_ARTIFACTS = {
    "retrieval": [
        "artifacts/records/retrieval.json",
        "artifacts/records/description_parsing.json",
    ],
    "analysis": ["artifacts/analysis/analysis.json", "artifacts/analysis/plan.md"],
    "code_generation": ["artifacts/generated"],
    "code_error": [
        "artifacts/runs/s3_run_report.json",
        "artifacts/runs/s3_full_run.stdout.txt",
        "artifacts/runs/s3_error_narrative.json",
    ],
    "code_revise": ["artifacts/revisions"],
    "feasibility": ["artifacts/runs/s5_feasibility.json"],
    "update": ["records/main"],
}


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
        return _json({"exists": False, "message": "no run yet - start one with `uv run cvrp start task.yaml`"})
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
    records = state.get("stages") or {}
    payload: list[dict[str, Any]] = []
    for stage_config in context.config.get("stages", []):
        name = stage_config["name"]
        record = dict(records.get(name) or {"name": name, "status": "pending"})
        record["kind"] = stage_config.get("kind", "agent")
        record["produces"] = stage_config.get("produces") or []
        present: list[str] = []
        for relative in STAGE_ARTIFACTS.get(name, stage_config.get("produces") or []):
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


async def get_records(request: Request) -> Response:
    """The knowledge base, plus this run's own record buckets.

    Two sources, deliberately. `records/main/` lives in the *project*, not in the run --
    it is what the next project reads -- so it is listed here even when the dashboard is
    pointed at a run that predates it, and a fresh checkout with no runs still shows a
    knowledge base if one exists. `temp/` and `related/` are per-run and come from the run
    directory, where `safe_path` already applies.

    `from_this_run` is the answer to "what did *this* run contribute?": the update stage's
    merge appends into shared files, so without it a reader cannot tell a file this run
    wrote from one an earlier run left behind.
    """
    context: RunContext = request.app.state.context
    config = context.config

    from cvrp.records import knowledge_inventory, main_dir, records_root, tag_of

    main_root = main_dir(config)
    payload: dict[str, Any] = {
        "records_dir": str(records_root(config)),
        "main_dir": str(main_root),
        "main": [],
        "run": [],
        "areas": {},
        "problem": tag_of(config.get("_problem")),
    }

    try:
        inventory = knowledge_inventory(config, config.get("_problem"))
    except Exception as exc:  # noqa: BLE001 - the dashboard must never 500 on this
        inventory = []
        payload["error"] = f"could not read the knowledge base: {exc}"

    run_dir = context.resolve(request.query_params.get("run"))
    contributed: set[str] = set()
    if run_dir is not None:
        index = read_json(main_root / "index.json", default={}) or {}
        for entry in index.get("runs") or []:
            if str(entry.get("run_id")) == run_dir.name:
                contributed = {str(name) for name in (entry.get("files") or [])}

    for item in inventory:
        area = str(item.get("area") or "")
        raw = str(item.get("path") or "")
        # Only the knowledge base proper, as decided: `main/<area>/<name>`. The records
        # root also holds `index.json` (which attributes files to runs) and the per-run
        # areas, and listing them here would blur what the panel is showing.
        if not raw.startswith("main/") or raw == "main/index.json":
            continue
        relative = raw[len("main/") :]
        payload["areas"][area] = payload["areas"].get(area, 0) + 1
        payload["main"].append(
            {
                "path": item.get("path"),
                "area": area,
                "bytes": item.get("bytes"),
                "headings": item.get("headings") or [],
                "from_this_run": bool(relative and relative in contributed),
            }
        )

    if run_dir is not None:
        # `temp/` and `related/` are run-scoped by directory name, not nested in the run:
        # the records layer owns them under the project's records root. The run directory
        # keeps only the copy the stages produced (`records/main`), so it is the fallback
        # source rather than the primary one.
        from cvrp.records import related_dir, temp_dir

        sources = (
            ("temp", temp_dir(config, run_dir.name)),
            ("related", related_dir(config, run_dir.name)),
            # What the retrieval stage wrote inside the run: the excerpts analysis read.
            # `retrieval.json` itself is skipped here because `related/` already lists it.
            ("retrieval", run_dir / "artifacts" / "records"),
        )
        for bucket, folder in sources:
            if not folder.is_dir() and bucket != "retrieval":
                folder = run_dir / "records" / bucket
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                if bucket == "retrieval" and path.name == "retrieval.json":
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                # The bucket is known from the tab and the run from the query, so the
                # listed path is the bare file name -- which is exactly what
                # `origin=run` expects to be joined onto `temp/<run_id>`.
                payload["run"].append({"bucket": bucket, "path": path.name, "bytes": size})
    return _json(payload)


async def get_record_file(request: Request) -> Response:
    """Serve one file from the knowledge base.

    `safe_path` is deliberately *not* reused: it confines callers to the run directory,
    and this endpoint exists precisely to reach outside it. The confinement here is its
    own: resolve, then require the result to stay under **`records/main/`** -- the
    knowledge base proper, which is also all the panel is willing to list. Only markdown
    and JSON are served, because those are the only things the update stage may write.

    Paths are accepted with or without the `main/` prefix, since the listing reports
    `main/<area>/<name>` while a caller may reasonably send just `<area>/<name>`.

    `origin=run` switches the confined root to this run's `records/temp/<run_id>` and
    `records/related/<run_id>` directories, which is where the per-run evidence lives --
    same idea, different root, and still no path outside the named root is served.
    """
    context: RunContext = request.app.state.context
    requested = (request.query_params.get("path") or "").replace("\\", "/").lstrip("/")
    relative = requested[len("main/") :] if requested.startswith("main/") else requested
    if not relative or Path(relative).suffix.lower() not in {".md", ".json"}:
        return _json({"error": f"not a readable record: {requested!r}"}, status=404)

    from cvrp.records import main_dir, related_dir, temp_dir

    if (request.query_params.get("origin") or "main") == "run":
        run_dir = context.resolve(request.query_params.get("run"))
        if run_dir is None:
            return _json({"error": "no run"}, status=404)
        roots = [
            temp_dir(context.config, run_dir.name),
            related_dir(context.config, run_dir.name),
            run_dir / "artifacts" / "records",
        ]
        if not any(root.is_dir() for root in roots):
            roots = [run_dir / "records" / "temp", run_dir / "records" / "related"]
    else:
        roots = [main_dir(context.config)]

    for root in roots:
        root = root.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            size = candidate.stat().st_size
            if size > MAX_TEXT_BYTES:
                return _json(
                    {"error": "file too large to display", "size": size}, status=413
                )
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return _json({"error": str(exc)}, status=500)
            return _json({"path": requested, "text": text})
    return _json({"error": f"not a file: {relative}"}, status=404)


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

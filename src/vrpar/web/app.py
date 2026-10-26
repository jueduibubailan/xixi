"""Starlette app factory for the vrpar dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from vrpar.compat.coral_adapter import coral_info
from vrpar.web import api
from vrpar.web.events import sse_endpoint

STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: dict[str, Any], root: Path, results_dir: Path | None = None) -> Starlette:
    """Build the dashboard app.

    Args:
        config: the resolved task config the dashboard describes.
        root: project directory (relative paths in ``config`` resolve here).
        results_dir: override for ``workspace.results_dir``.
    """
    context = api.RunContext(config, Path(root))
    # Relative paths in the config resolve against the project root, and the orchestrator
    # sets this anchor on every config it runs. The dashboard reads the same paths, so it
    # has to establish the same anchor -- otherwise `./results` would resolve against the
    # process's working directory and the panel would show the wrong run.
    config.setdefault("_project_root", str(Path(root)))
    if results_dir is not None:
        context.results_dir = Path(results_dir)

    async def index(_: Request) -> Response:
        page = STATIC_DIR / "index.html"
        if page.is_file():
            return FileResponse(page)
        return Response("dashboard assets missing", status_code=500)

    async def static_file(request: Request) -> Response:
        name = request.path_params["name"]
        target = (STATIC_DIR / name).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return JSONResponse({"error": "invalid path"}, status_code=400)
        if not target.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(target)

    routes = [
        Route("/", index),
        Route("/api/health", api.get_health),
        Route("/api/status", api.get_status),
        Route("/api/runs", api.get_runs),
        Route("/api/stages", api.get_stages),
        Route("/api/artifacts", api.get_artifacts),
        Route("/api/file", api.get_file),
        Route("/api/logs", api.get_logs),
        Route("/api/log", api.get_log),
        Route("/api/grader", api.get_grader),
        Route("/api/steer", api.post_steer, methods=["POST"]),
        Route("/api/events", sse_endpoint),
        Route("/static/{name}", static_file),
    ]

    app = Starlette(routes=routes)
    app.state.context = context
    app.state.coral = coral_info().describe()
    return app

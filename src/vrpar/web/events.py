"""SSE endpoint: push state changes to the dashboard.

Rather than a filesystem watcher (CORAL's FileWatcher), we diff ``state.json`` on
a short interval. State is written atomically and is small, so this is both
simpler and sufficient; the UI also polls as a backstop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from starlette.requests import Request
from starlette.responses import StreamingResponse

from vrpar.run.run_dir import read_json

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
MAX_EVENTS = 200


def _fingerprint(run_dir: Path | None) -> str:
    if run_dir is None:
        return "none"
    state_file = Path(run_dir) / "state.json"
    if not state_file.is_file():
        return "missing"
    try:
        stat = state_file.stat()
    except OSError:
        return "error"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


async def event_stream(run_dir: Path | None) -> AsyncIterator[bytes]:
    last = ""
    idle = 0.0
    sent = 0
    while sent < MAX_EVENTS:
        current = _fingerprint(run_dir)
        if current != last:
            last = current
            idle = 0.0
            payload: dict[str, Any] = {"fingerprint": current}
            if run_dir is not None:
                payload["state"] = read_json(Path(run_dir) / "state.json", default={})
            yield f"event: state\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
            sent += 1
        else:
            idle += POLL_SECONDS
            if idle >= HEARTBEAT_SECONDS:
                idle = 0.0
                yield b": ping\n\n"
        await asyncio.sleep(POLL_SECONDS)


async def sse_endpoint(request: Request) -> StreamingResponse:
    context = request.app.state.context
    run_dir = context.resolve(request.query_params.get("run"))
    return StreamingResponse(
        event_stream(run_dir),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

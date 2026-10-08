"""Web dashboard (Starlette + SSE, no build step)."""

from cvrp.web.app import create_app

__all__ = ["create_app"]

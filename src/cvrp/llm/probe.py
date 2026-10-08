"""Preflight probe for the agent's model endpoint.

`cvrp doctor` can check that the `claude` CLI exists and *describe* where its
traffic will go, but neither tells you whether the endpoint actually answers. A
router that is not running, a model name it does not serve, or a missing API key
all surface the same way three stages later: the agent writes nothing and the gate
reports a missing artifact.

This module sends one minimal Anthropic Messages request to the resolved endpoint
and reports the HTTP status and body. It is deliberately tiny and dependency-free
(``urllib`` only) so it can run before anything is installed or authenticated.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 20.0
ANTHROPIC_VERSION = "2023-06-01"


def _auth_headers(env: dict[str, str]) -> dict[str, str]:
    """Headers Claude Code would send, in its own precedence order."""
    headers = {
        "content-type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    token = env.get("ANTHROPIC_AUTH_TOKEN")
    api_key = env.get("ANTHROPIC_API_KEY")
    if token:
        # A gateway authenticates the bearer token; Claude Code prefers it.
        headers["authorization"] = f"Bearer {token}"
    else:
        headers["x-api-key"] = api_key or ""
    if env.get("ANTHROPIC_CUSTOM_HEADERS"):
        for line in str(env["ANTHROPIC_CUSTOM_HEADERS"]).splitlines():
            if ":" in line:
                name, _, value = line.partition(":")
                headers[name.strip()] = value.strip()
    return headers


def probe_model_endpoint(
    env: dict[str, str],
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Send one minimal Messages request and describe what came back.

    Returns a dict with ``ok``, ``status``, ``endpoint``, ``detail`` and, when the
    endpoint answered, a short ``body``. Never raises: a probe that throws is worse
    than a probe that reports.
    """
    base = str(env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 4,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=_auth_headers(env),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2000).decode("utf-8", "replace")
            return {
                "ok": True,
                "status": response.status,
                "endpoint": url,
                "model": model,
                "detail": "endpoint answered and accepted the model name",
                "body": body[:600],
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(2000).decode("utf-8", "replace")
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.code,
            "endpoint": url,
            "model": model,
            "detail": _explain_status(exc.code),
            "body": body[:600],
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status": None,
            "endpoint": url,
            "model": model,
            "detail": f"could not reach the endpoint: {exc.reason}",
            "body": "",
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "ok": False,
            "status": None,
            "endpoint": url,
            "model": model,
            "detail": f"probe failed: {exc}",
            "body": "",
        }


def _explain_status(status: int) -> str:
    if status == 401:
        return (
            "the endpoint rejected the credential. Set ANTHROPIC_AUTH_TOKEN (gateways) "
            "or ANTHROPIC_API_KEY, or log the CLI in."
        )
    if status == 403:
        return (
            "authenticated but refused: usually a model name the endpoint does not "
            "serve, or a key without access to it. Check the router's model_list entry "
            "and the upstream provider's real model id."
        )
    if status == 404:
        return "the endpoint has no /v1/messages route; the base URL is probably wrong."
    if status == 400:
        return "the request was rejected as malformed (often an unknown model name)."
    if status == 429:
        return "rate limited or out of quota."
    if status >= 500:
        return "the endpoint answered with a server error; the router or upstream is unhappy."
    return "unexpected status"


def print_probe(env: dict[str, str], model: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """Run :func:`probe_model_endpoint` and format it for the `doctor` output."""
    result = probe_model_endpoint(env, model, timeout=timeout)
    lines = [
        f"probe     : POST {result['endpoint']} (model {model!r})",
        f"            -> {'OK' if result['ok'] else 'FAILED'}"
        + (f" HTTP {result['status']}" if result["status"] else "")
        + f" - {result['detail']}",
    ]
    if result.get("body"):
        body = " ".join(str(result["body"]).split())[:300]
        lines.append(f"            body: {body}")
    return bool(result["ok"]), "\n".join(lines)


__all__ = ["ANTHROPIC_VERSION", "DEFAULT_TIMEOUT", "print_probe", "probe_model_endpoint"]

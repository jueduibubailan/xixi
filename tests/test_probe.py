"""Tests for the preflight model-endpoint probe.

The probe exists because "the router is not running", "the router does not serve
that model" and "the key is wrong" all reach the pipeline as the same symptom: the
agent writes nothing and a gate reports a missing artifact. The probe turns that
into one HTTP status, before a run starts.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vrpar.llm.probe import ANTHROPIC_VERSION, _auth_headers, probe_model_endpoint

#: What the fake endpoint does per request. Set by each test.
BEHAVIOUR: dict = {"status": 200, "body": {"ok": True}, "seen": {}}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        BEHAVIOUR["seen"] = {
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "payload": json.loads(raw or b"{}"),
        }
        body = json.dumps(BEHAVIOUR["body"]).encode("utf-8")
        self.send_response(BEHAVIOUR["status"])
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence the test output
        return


@pytest.fixture()
def endpoint() -> str:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_probe_reports_success_and_uses_the_messages_route(endpoint: str):
    BEHAVIOUR.update(status=200, body={"content": [{"type": "text", "text": "pong"}]})
    result = probe_model_endpoint(
        {"ANTHROPIC_BASE_URL": endpoint, "ANTHROPIC_AUTH_TOKEN": "sk-token"}, "deepseek-flash"
    )
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["endpoint"] == f"{endpoint}/v1/messages"
    seen = BEHAVIOUR["seen"]
    assert seen["path"] == "/v1/messages"
    assert seen["payload"]["model"] == "deepseek-flash"
    # a gateway is authenticated with the bearer token, not x-api-key
    assert seen["headers"]["authorization"] == "Bearer sk-token"
    assert seen["headers"]["anthropic-version"] == ANTHROPIC_VERSION


def test_probe_accepts_a_base_url_that_already_ends_with_v1(endpoint: str):
    BEHAVIOUR.update(status=200, body={})
    result = probe_model_endpoint({"ANTHROPIC_BASE_URL": f"{endpoint}/v1"}, "m")
    assert result["endpoint"] == f"{endpoint}/v1/messages"
    assert result["ok"] is True


def test_probe_explains_a_403(endpoint: str):
    BEHAVIOUR.update(status=403, body={"error": {"message": "Request not allowed"}})
    result = probe_model_endpoint({"ANTHROPIC_BASE_URL": endpoint, "ANTHROPIC_API_KEY": "k"}, "bad-model")
    assert result["ok"] is False
    assert result["status"] == 403
    assert "model name" in result["detail"]
    assert "Request not allowed" in result["body"]


def test_probe_explains_a_401(endpoint: str):
    BEHAVIOUR.update(status=401, body={"error": "no key"})
    result = probe_model_endpoint({"ANTHROPIC_BASE_URL": endpoint}, "m")
    assert result["ok"] is False
    assert "credential" in result["detail"]


def test_probe_explains_an_unreachable_endpoint():
    result = probe_model_endpoint({"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"}, "m", timeout=2)
    assert result["ok"] is False
    assert result["status"] is None
    assert "could not reach" in result["detail"]


def test_probe_never_raises_on_a_bad_base_url():
    result = probe_model_endpoint({"ANTHROPIC_BASE_URL": "not-a-url"}, "m", timeout=2)
    assert result["ok"] is False
    assert result["detail"]


def test_auth_headers_prefer_the_bearer_token():
    headers = _auth_headers({"ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k"})
    assert headers["authorization"] == "Bearer t"
    assert "x-api-key" not in headers

    headers = _auth_headers({"ANTHROPIC_API_KEY": "k"})
    assert headers["x-api-key"] == "k"
    assert "authorization" not in headers


def test_auth_headers_include_custom_headers():
    headers = _auth_headers({"ANTHROPIC_CUSTOM_HEADERS": "X-Session-Id: circle_packing\nX-Other: 1"})
    assert headers["X-Session-Id"] == "circle_packing"
    assert headers["X-Other"] == "1"

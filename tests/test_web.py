"""Dashboard API tests (Starlette TestClient, no server needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tests.conftest import write_project
from vrpar.config import load_config
from vrpar.run.run_dir import write_json
from vrpar.web.app import create_app

TASK_YAML = """
task:
  name: "Web test"
  slug: "web"
  description: "ui test task"

problem:
  slug: "cvrptw"

llm:
  backend: "mock"
  model: "mock"

stages:
  - name: analysis
    kind: agent
    produces: ["artifacts/analysis/analysis.json"]
  - name: code_error
    kind: executor
    produces: ["artifacts/runs/s3_run_report.json"]

workspace:
  results_dir: "./results"
  final_dir: "./workspace/final"
"""


@pytest.fixture()
def client(scratch: Path) -> TestClient:
    project = write_project(scratch / "project", TASK_YAML, problem="cvrptw")
    run_dir = project / "results" / "web" / "20250101-000000"
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "artifacts" / "analysis").mkdir(parents=True)
    write_json(
        run_dir / "state.json",
        {
            "run_id": "20250101-000000",
            "task": "Web test",
            "status": "running",
            "round": 2,
            "best_cost": 7.5,
            "initial_cost": 9.0,
            "stages": {
                "analysis": {"name": "analysis", "status": "done", "attempts": 1, "duration_s": 3.5}
            },
            "history": [],
        },
    )
    (run_dir / "artifacts" / "analysis" / "analysis.json").write_text(
        '{"problem_type": "CVRPTW"}', encoding="utf-8"
    )
    (run_dir / "logs" / "pipeline.log").write_text("line one\nline two\n", encoding="utf-8")

    config = load_config(project / "task.yaml", cwd=project)
    app = create_app(config, project)
    return TestClient(app)


def test_health_reports_problem_and_stages(client: TestClient):
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert payload["slug"] == "web"
    assert payload["stages"] == ["analysis", "code_error"]
    assert "coral" in payload


def test_status_reads_the_latest_run(client: TestClient):
    payload = client.get("/api/status").json()
    assert payload["exists"] is True
    assert payload["best_cost"] == 7.5
    assert payload["round"] == 2


def test_stages_marks_present_artifacts(client: TestClient):
    payload = client.get("/api/stages").json()
    by_name = {stage["name"]: stage for stage in payload["stages"]}
    assert by_name["analysis"]["status"] == "done"
    assert "artifacts/analysis/analysis.json" in by_name["analysis"]["present"]
    assert by_name["code_error"]["status"] == "pending"


def test_runs_lists_the_run(client: TestClient):
    payload = client.get("/api/runs").json()
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["task"] == "web"


def test_file_endpoint_serves_artifacts(client: TestClient):
    payload = client.get("/api/file", params={"path": "artifacts/analysis/analysis.json"}).json()
    assert "CVRPTW" in payload["text"]


def test_file_endpoint_refuses_traversal(client: TestClient):
    response = client.get("/api/file", params={"path": "../../../task.yaml"})
    assert response.status_code == 404
    assert "error" in response.json()


def test_artifacts_listing(client: TestClient):
    payload = client.get("/api/artifacts").json()
    paths = [entry["path"] for entry in payload["entries"]]
    assert "artifacts/analysis/analysis.json" in paths


def test_logs_endpoints(client: TestClient):
    listing = client.get("/api/logs").json()
    assert any(item["name"] == "pipeline.log" for item in listing["logs"])
    payload = client.get("/api/log", params={"name": "pipeline.log", "tail": 1}).json()
    assert payload["text"].strip() == "line two"


# --------------------------------------------------------------------------- #
# the knowledge-base panel
# --------------------------------------------------------------------------- #
def test_steer_queues_an_instruction(client: TestClient):
    response = client.post("/api/steer", json={"text": "keep encoder at 2 dims", "stage": "analysis"})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    state = json.loads(
        (
            Path(client.app.state.context.results_dir) / "web" / "20250101-000000" / "state.json"
        ).read_text(encoding="utf-8")
    )
    queued = state["instruction_queue"]
    assert queued and queued[0]["text"] == "keep encoder at 2 dims"
    assert queued[0]["target_stage"] == "analysis"


def test_steer_rejects_empty_text(client: TestClient):
    assert client.post("/api/steer", json={"text": "   "}).status_code == 400


def test_grader_endpoint_is_empty_before_grading(client: TestClient):
    assert client.get("/api/grader").json()["exists"] is False


def test_index_page_is_served(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "vrpar" in response.text

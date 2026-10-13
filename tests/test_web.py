"""Dashboard API tests (Starlette TestClient, no server needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cvrp.config import load_config
from cvrp.run.run_dir import write_json
from cvrp.web.app import create_app
from tests.conftest import write_project

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


def _write_knowledge_base(client: TestClient, *, run_id: str = "20250101-000000") -> Path:
    """A knowledge base plus an index entry attributing it to a run.

    Written in the current convention: one general record, one problem-specific one, each
    opening with its own `## Contents`.
    """
    config = client.app.state.context.config
    from cvrp.records import main_dir

    root = main_dir(config)
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "_global_skill.md").write_text(
        "# Global skill\n\n## Contents\n\n- `Baseline hazards` — before adapting *(general)*\n\n"
        "## Baseline hazards\n\n- the package has no `__init__.py`\n",
        encoding="utf-8",
    )
    (root / "skills" / "cvrptw_skill.md").write_text(
        "# Skill — cvrptw\n\n## Contents\n\n"
        "- `Proven fixes` — read before changing a mask *(problem-specific)*\n\n"
        "## Proven fixes\n\n- use style_cvrp_20.json\n",
        encoding="utf-8",
    )
    (root / "notes" / "cvrptw_notes.md").write_text(
        "# Notes — cvrptw\n\n## Contents\n\n- `Traps` — read first *(problem-specific)*\n\n"
        "## Traps\n\n- style_cvrp_100.json is not shipped\n",
        encoding="utf-8",
    )
    write_json(
        root / "index.json",
        {"problem": "cvrptw", "runs": [{"run_id": run_id, "outcome": "SUCCESS",
                                        "files": ["skills/cvrptw_skill.md"]}]},
    )
    return root


def test_records_endpoint_is_empty_but_valid_without_a_knowledge_base(client: TestClient):
    """A fresh project has no records; the panel must render, not 500."""
    payload = client.get("/api/records").json()
    assert payload["main"] == []
    assert payload["run"] == []
    assert payload["records_dir"]
    assert payload["main_dir"]


def test_records_endpoint_lists_the_knowledge_base(client: TestClient):
    _write_knowledge_base(client)
    payload = client.get("/api/records").json()
    paths = {item["path"] for item in payload["main"]}
    assert "main/skills/cvrptw_skill.md" in paths
    assert "main/notes/cvrptw_notes.md" in paths
    assert payload["areas"]["skills"] == 2, "the general record and the scoped one both live in skills/"
    scoped = next(
        entry for entry in payload["main"] if entry["path"] == "main/skills/cvrptw_skill.md"
    )
    general = next(
        entry for entry in payload["main"] if entry["path"] == "main/skills/_global_skill.md"
    )
    # The record's own contents list travels with the listing, so the panel can show what
    # is inside without opening the file.
    assert "Proven fixes" in " ".join(scoped["toc"])
    assert "Baseline hazards" in " ".join(general["toc"])
    assert "problem-specific" in " ".join(scoped["toc"]), "the entry states its scope"
    # and the two tiers are distinguishable without reading anything
    assert scoped["tier"] == "scoped"
    assert general["tier"] == "general"
    assert payload["tiers"] == {"scoped": 2, "general": 1}


def test_records_endpoint_marks_what_this_run_contributed(client: TestClient):
    """With shared files, "what did this run add?" is the only way to read the panel."""
    _write_knowledge_base(client)
    payload = client.get("/api/records").json()
    by_path = {item["path"]: item["from_this_run"] for item in payload["main"]}
    assert by_path["main/skills/cvrptw_skill.md"] is True, "this run wrote it"
    assert by_path["main/notes/cvrptw_notes.md"] is False, "this run did not"
    assert by_path["main/skills/_global_skill.md"] is False, "no entry claims it"


def test_records_endpoint_lists_this_runs_record_buckets(client: TestClient):
    """`temp/` and `related/` are run-scoped *directories* under the records root."""
    config = client.app.state.context.config
    from cvrp.records import main_dir, related_dir, temp_dir

    run_id = "20250101-000000"
    (temp_dir(config, run_id)).mkdir(parents=True, exist_ok=True)
    (temp_dir(config, run_id) / "code_error.json").write_text('{"record": "code_error"}', encoding="utf-8")
    (related_dir(config, run_id)).mkdir(parents=True, exist_ok=True)
    (related_dir(config, run_id) / "retrieval.json").write_text('{"empty": true}', encoding="utf-8")
    del main_dir

    payload = client.get("/api/records").json()
    buckets = {item["bucket"] for item in payload["run"]}
    assert buckets == {"temp", "related"}
    assert any(item["path"].endswith("code_error.json") for item in payload["run"])

    item = next(entry for entry in payload["run"] if entry["bucket"] == "temp")
    served = client.get(
        "/api/record-file", params={"path": item["path"], "origin": "run"}
    ).json()
    assert "code_error" in served["text"]


def test_record_file_serves_a_knowledge_file(client: TestClient):
    _write_knowledge_base(client)
    payload = client.get("/api/record-file", params={"path": "skills/cvrptw_skill.md"}).json()
    assert "Proven fixes" in payload["text"]


def test_record_file_refuses_to_leave_the_knowledge_base(client: TestClient):
    """`/api/file` cannot reach the knowledge base, and this cannot escape it."""
    _write_knowledge_base(client)
    for attempt in (
        "../task.yaml",
        "../../task.yaml",
        "skills/../../../../task.yaml",
        "skills/../../../../etc/passwd",
        "/etc/passwd",
        "..",
    ):
        response = client.get("/api/record-file", params={"path": attempt})
        assert response.status_code == 404, attempt


def test_record_file_serves_only_markdown_and_json(client: TestClient):
    _write_knowledge_base(client)
    root = client.app.state.context.config
    from cvrp.records import main_dir

    (main_dir(root) / "skills" / "sneaky.py").write_text("print(1)\n", encoding="utf-8")
    response = client.get("/api/record-file", params={"path": "skills/sneaky.py"})
    assert response.status_code == 404


def test_record_file_reports_a_missing_file(client: TestClient):
    _write_knowledge_base(client)
    response = client.get("/api/record-file", params={"path": "skills/nope.md"})
    assert response.status_code == 404


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
    assert "cvrp" in response.text

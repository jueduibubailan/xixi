"""Dashboard payloads: the score cards and the nn improve plan table."""

from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from vrpar.web.app import create_app


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run(scratch: Path) -> Path:
    results = scratch / "results"
    run = results / "demo" / "20260101-000000"
    _write(
        run / "state.json",
        {
            "run_id": "20260101-000000",
            "task": "demo",
            "task_slug": "demo",
            "status": "completed",
            "round": 1,
            "score": 41.0,
            "initial_score": 42.0,
            "initial_search_score": 42.0,
            "best_score": 41.5,
            "best_plan_id": "b1-s01",
            "final_score": 41.0,
            "nn_round": 1,
            "plans": {"total": 2, "completed": 2, "winners": 1, "pending": 0},
            "stages": {"analysis": {"name": "analysis", "status": "done"}},
            "score_history": [{"round": 1, "score": 42.0}],
        },
    )
    _write(
        run / "artifacts" / "nn_improve" / "plans.json",
        {
            "version": 1,
            "direction": "minimize",
            "initial_score": 42.0,
            "initial_search_score": 42.0,
            "best_score": 41.5,
            "best_plan_id": "b1-s01",
            "final_score": 41.0,
            "batches": [
                {
                    "batch": 1,
                    "protocol": "search",
                    "entry_best": 42.0,
                    "plans": [
                        {
                            "id": "b1-s01",
                            "kind": "strategy",
                            "title": "embedding 128→256",
                            "base_plan_id": None,
                            "status": "completed",
                            "label": 1,
                            "score": 41.5,
                            "delta_initial": -0.5,
                            "delta_best": -0.5,
                        },
                        {
                            "id": "b1-s02",
                            "kind": "mutation",
                            "title": "head_num 8→16",
                            "base_plan_id": None,
                            "status": "completed",
                            "label": 0,
                            "score": 42.0,
                            "delta_initial": 0.0,
                            "delta_best": 0.5,
                        },
                    ],
                }
            ],
        },
    )
    _write(
        run / "artifacts" / "runs" / "code_error_report.json",
        {"passed": True, "score": 41.0, "protocol": "acceptance"},
    )
    return run


def _client(scratch: Path) -> TestClient:
    results = scratch / "results"
    config = {
        "task": {"name": "demo", "slug": "demo"},
        "problem": {"slug": "demo"},
        "workspace": {"results_dir": str(results)},
        "stages": [
            {"name": "analysis", "kind": "agent", "produces": ["artifacts/analysis/plan.md"]},
            {"name": "nn_improve", "kind": "agent", "produces": ["artifacts/nn_improve/"]},
        ],
    }
    app = create_app(config, scratch, results_dir=results)
    return TestClient(app)


def test_plans_endpoint_returns_the_ledger(scratch: Path):
    _run(scratch)
    payload = _client(scratch).get("/api/plans").json()
    assert payload["exists"] is True
    assert payload["best_plan_id"] == "b1-s01"
    assert payload["best_score"] == 41.5
    assert payload["final_score"] == 41.0
    assert payload["batches"][0]["plans"][0]["status"] == "completed"
    assert [winner["id"] for winner in payload["winners"]] == ["b1-s01"]


def test_score_endpoint_reports_initial_best_final(scratch: Path):
    _run(scratch)
    payload = _client(scratch).get("/api/score").json()
    assert payload["initial"] == 42.0
    assert payload["initial_search"] == 42.0
    assert payload["best"] == 41.5
    assert payload["best_plan_id"] == "b1-s01"
    assert payload["final"] == 41.0
    assert payload["current"] == 41.0
    assert payload["report"]["protocol"] == "acceptance"


def test_status_endpoint_carries_the_plan_summary(scratch: Path):
    _run(scratch)
    payload = _client(scratch).get("/api/status").json()
    assert payload["initial_score"] == 42.0
    assert payload["best_score"] == 41.5
    assert payload["plans"]["completed"] == 2


def test_health_lists_the_configured_stages(scratch: Path):
    _run(scratch)
    payload = _client(scratch).get("/api/health").json()
    assert payload["stages"] == ["analysis", "nn_improve"]


def test_plans_endpoint_is_empty_without_a_ledger(scratch: Path):
    results = scratch / "results"
    run = results / "demo" / "20260101-000000"
    _write(run / "state.json", {"run_id": "x", "status": "running", "stages": {}})
    payload = _client(scratch).get("/api/plans").json()
    assert payload["exists"] is False
    assert payload["batches"] == []


def test_dashboard_serves_the_new_panels(scratch: Path):
    _run(scratch)
    client = _client(scratch)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="plans"' in page.text
    assert 'id="v-final"' in page.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "loadPlans" in script.text

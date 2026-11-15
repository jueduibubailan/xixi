"""The nn improve ledger: plan files, schema, labels, base assignment, transitions."""

from __future__ import annotations

from pathlib import Path

import yaml

from vrpar.nn_improve import plans


def _write_plan(root: Path, plan: dict, body: str = "# plan\n") -> Path:
    directory = root / "artifacts" / "nn_improve" / "plans"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{plan['id']}.md"
    path.write_text(
        "---\n" + yaml.safe_dump(plan, sort_keys=False) + "---\n\n" + body,
        encoding="utf-8",
    )
    return path


def _plan(batch: int, index: int, kind: str) -> dict:
    prefix = "s" if kind == "strategy" else "m"
    return {
        "id": f"b{batch}-{prefix}{index + 1:02d}",
        "batch": batch,
        "kind": kind,
        "title": f"method {index}",
        "target": "CVRPModel.py::Model.__init__",
        "change": "replace the dense block with a routed block",
        "rationale": "why",
        "source_plan_ids": [],
    }


def _write_batch(root: Path, batch: int = 1, strategies: int = 10, mutations: int = 2) -> dict:
    for index in range(strategies):
        _write_plan(root, _plan(batch, index, "strategy"))
    for index in range(mutations):
        _write_plan(root, _plan(batch, index, "mutation"))
    payload, issues = plans.read_plan_files(root, batch)
    assert issues == [], issues
    return payload


def _validate(raw, *, batch=1, assignments=None, previous_winners=None, deny=()):
    return plans.validate_batch(
        raw,
        batch=batch,
        assignments=assignments if assignments is not None else [None] * len(raw["plans"]),
        strategies_per_batch=10,
        mutations_per_batch=2,
        previous_winners=previous_winners or [],
        deny=deny,
    )


def test_direction_and_min_delta():
    assert plans.better(11.9, 12.0) is True
    assert plans.better(12.1, 12.0) is False
    assert plans.better(12.1, 12.0, "maximize") is True
    assert plans.improved(11.99, 12.0, "minimize", min_delta=0.05) is False
    assert plans.improved(11.90, 12.0, "minimize", min_delta=0.05) is True


def test_assign_bases_first_batch_is_initial():
    assert plans.assign_bases(1, [], 12) == [None] * 12


def test_assign_bases_cycles_winners_best_first():
    winners = [{"id": "b1-s02", "score": 11.8}, {"id": "b1-s01", "score": 11.9}]
    assert plans.assign_bases(2, winners, 4) == ["b1-s02", "b1-s01", "b1-s02", "b1-s01"]


def test_read_plan_files_orders_strategies_then_mutations(scratch: Path):
    payload = _write_batch(scratch)
    assert [plan["kind"] for plan in payload["plans"]][:2] == ["strategy", "strategy"]
    assert [plan["kind"] for plan in payload["plans"]][-2:] == ["mutation", "mutation"]
    assert payload["plans"][0]["id"] == "b1-s01"
    assert payload["plans"][-1]["id"] == "b1-m02"
    assert payload["plans"][0]["change"].startswith("replace the dense block")


def test_read_plan_files_rejects_missing_front_matter(scratch: Path):
    directory = scratch / "artifacts" / "nn_improve" / "plans"
    directory.mkdir(parents=True)
    (directory / "b1-s01.md").write_text("# no front matter\n", encoding="utf-8")
    _, issues = plans.read_plan_files(scratch, 1)
    assert any("front matter" in issue for issue in issues)


def test_read_plan_files_rejects_a_batch_mismatch(scratch: Path):
    plan = _plan(1, 0, "strategy")
    plan["batch"] = 2
    directory = scratch / "artifacts" / "nn_improve" / "plans"
    directory.mkdir(parents=True)
    (directory / "b1-s01.md").write_text(
        "---\n" + yaml.safe_dump(plan, sort_keys=False) + "---\n", encoding="utf-8"
    )
    _, issues = plans.read_plan_files(scratch, 1)
    assert any("batch must be 1" in issue for issue in issues)


def test_read_plan_files_rejects_an_id_that_differs_from_the_stem(scratch: Path):
    plan = _plan(1, 0, "strategy")
    plan["id"] = "b1-s99"
    directory = scratch / "artifacts" / "nn_improve" / "plans"
    directory.mkdir(parents=True)
    (directory / "b1-s01.md").write_text(
        "---\n" + yaml.safe_dump(plan, sort_keys=False) + "---\n", encoding="utf-8"
    )
    _, issues = plans.read_plan_files(scratch, 1)
    assert any("file stem" in issue for issue in issues)


def test_validate_accepts_a_well_formed_batch(scratch: Path):
    payload = _write_batch(scratch)
    normalised, issues = _validate(payload)
    assert issues == []
    assert len(normalised) == 12
    assert all(plan["status"] == plans.PENDING for plan in normalised)
    assert normalised[0]["base_plan_id"] is None


def test_validate_rejects_a_bare_value_change(scratch: Path):
    payload = _write_batch(scratch)
    payload["plans"][0]["change"] = "128 -> 256"
    _, issues = _validate(payload)
    assert any("bare value" in issue for issue in issues)


def test_validate_rejects_a_denied_target(scratch: Path):
    payload = _write_batch(scratch)
    payload["plans"][0]["target"] = "scale.problem_size"
    _, issues = _validate(payload, deny=["scale.problem_size"])
    assert any("outside the search space" in issue for issue in issues)


def test_validate_rejects_the_wrong_count(scratch: Path):
    payload = _write_batch(scratch, strategies=9)
    _, issues = _validate(payload)
    assert any("expected 12" in issue for issue in issues)


def test_validate_rejects_an_unknown_source_plan(scratch: Path):
    payload = _write_batch(scratch, batch=2)
    for item in payload["plans"]:
        item["source_plan_ids"] = ["b1-s01"]
    _, issues = _validate(payload, batch=2, previous_winners=[{"id": "b1-s01"}])
    assert issues == []

    payload["plans"][0]["source_plan_ids"] = ["b9-s01"]
    _, issues = _validate(payload, batch=2, previous_winners=[{"id": "b1-s01"}])
    assert any("not a label=1 plan" in issue for issue in issues)


def test_state_machine_and_labels(scratch: Path):
    ledger = plans.empty_ledger(initial_search_score=12.0)
    record = plans.start_batch(ledger, 1, protocol="search", entry_best=12.0)
    normalised, issues = _validate(_write_batch(scratch))
    assert issues == []
    record["plans"].extend(normalised)

    first = plans.next_pending(ledger)
    assert first is not None and first["id"] == "b1-s01"
    plans.mark_running(first, report_dir="artifacts/runs/plan-b1-s01")
    assert first["status"] == plans.RUNNING

    plans.mark_result(
        ledger,
        first,
        passed=True,
        evaluated=True,
        score=11.5,
        protocol="search",
        report_dir="artifacts/runs/plan-b1-s01",
    )
    assert first["status"] == plans.COMPLETED
    assert first["label"] == 1
    assert first["delta_initial"] == -0.5

    second = plans.next_pending(ledger)
    assert second is not None and second["id"] == "b1-s02"
    plans.mark_running(second, report_dir="artifacts/runs/plan-b1-s02")
    plans.mark_result(
        ledger,
        second,
        passed=False,
        evaluated=True,
        score=None,
        protocol="search",
        report_dir="artifacts/runs/plan-b1-s02",
    )
    assert second["status"] == plans.EXECUTED
    assert second["label"] == 0


def test_winners_are_sorted_by_score(scratch: Path):
    ledger = plans.empty_ledger(initial_search_score=12.0)
    record = plans.start_batch(ledger, 1, protocol="search", entry_best=12.0)
    normalised, _ = _validate(_write_batch(scratch))
    record["plans"].extend(normalised)
    record["plans"][0].update({"status": plans.COMPLETED, "label": 1, "score": 11.7})
    record["plans"][1].update({"status": plans.COMPLETED, "label": 1, "score": 11.9})
    record["plans"][2].update({"status": plans.COMPLETED, "label": 0, "score": 12.2})
    picked = plans.winners(ledger)
    assert [plan["id"] for plan in picked] == ["b1-s01", "b1-s02"]


def test_summary_counts(scratch: Path):
    ledger = plans.empty_ledger(initial_search_score=12.0)
    record = plans.start_batch(ledger, 1, protocol="search", entry_best=12.0)
    normalised, _ = _validate(_write_batch(scratch))
    record["plans"].extend(normalised)
    record["plans"][0]["status"] = plans.COMPLETED
    record["plans"][1]["status"] = plans.RUNNING
    summary = plans.summarise(ledger)
    assert summary["total"] == 12
    assert summary["completed"] == 1
    assert summary["running"] == 1
    assert summary["pending"] == 10

"""The nn improve ledger: schema, labels, base assignment, transitions."""

from __future__ import annotations

from vrpar.nn_improve import plans


def _payload(batch: int = 1, strategies: int = 10, mutations: int = 2) -> dict:
    items = []
    for index in range(strategies):
        items.append(
            {
                "id": f"b{batch}-s{index + 1:02d}",
                "kind": "strategy",
                "strategy_id": "s01",
                "title": f"embedding {index}",
                "target": "CVRPModel.py::Model.__init__",
                "changes": [{"param": "embedding_dim", "from": 128, "to": 256 + index}],
                "rationale": "why",
                "expected": "lower",
                "novelty": "",
                "source_plan_ids": [],
            }
        )
    for index in range(mutations):
        items.append(
            {
                "id": f"b{batch}-m{index + 1:02d}",
                "kind": "mutation",
                "strategy_id": None,
                "title": f"mutation {index}",
                "target": "CVRPModel.py::Model.__init__",
                "changes": [{"param": "head_num", "from": 8, "to": 16}],
                "rationale": "why",
                "expected": "lower",
                "novelty": "outside the catalogue",
                "source_plan_ids": [],
            }
        )
    return {"batch": batch, "plans": items}


def _validate(raw, *, batch=1, assignments=None, previous_winners=None, deny=(), strict=False):
    return plans.validate_batch(
        raw,
        batch=batch,
        assignments=assignments if assignments is not None else [None] * len(raw["plans"]),
        strategies_per_batch=10,
        mutations_per_batch=2,
        previous_winners=previous_winners or [],
        deny=deny,
        strict_strategies=strict,
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


def test_validate_accepts_a_well_formed_batch():
    normalised, issues = _validate(_payload())
    assert issues == []
    assert len(normalised) == 12
    assert all(plan["status"] == plans.PENDING for plan in normalised)
    assert normalised[0]["base_plan_id"] is None


def test_validate_rejects_a_mutation_without_novelty():
    raw = _payload()
    raw["plans"][-1]["novelty"] = ""
    _, issues = _validate(raw)
    assert any("novelty" in issue for issue in issues)


def test_validate_rejects_two_changes():
    raw = _payload()
    raw["plans"][0]["changes"].append({"param": "head_num", "from": 8, "to": 16})
    _, issues = _validate(raw)
    assert any("exactly one" in issue for issue in issues)


def test_validate_rejects_a_denied_parameter():
    raw = _payload()
    raw["plans"][0]["changes"][0]["param"] = "problem_size"
    _, issues = _validate(raw, deny=["scale.problem_size"])
    assert any("outside the search space" in issue for issue in issues)


def test_validate_rejects_the_wrong_count():
    _, issues = _validate(_payload(strategies=9))
    assert any("expected 12" in issue for issue in issues)


def test_validate_batch_two_requires_winner_sources():
    raw = _payload(batch=2)
    _, issues = _validate(raw, batch=2, previous_winners=[{"id": "b1-s01"}])
    assert any("winning source plans" in issue for issue in issues)

    for item in raw["plans"]:
        item["source_plan_ids"] = ["b1-s01"]
    normalised, issues = _validate(
        raw, batch=2, assignments=["b1-s01"] * 12, previous_winners=[{"id": "b1-s01"}]
    )
    assert issues == []
    assert normalised[0]["base_plan_id"] == "b1-s01"


def test_state_machine_and_labels():
    ledger = plans.empty_ledger(initial_search_score=12.0)
    record = plans.start_batch(ledger, 1, protocol="search", entry_best=12.0)
    normalised, issues = _validate(_payload())
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


def test_winners_are_sorted_by_score():
    ledger = plans.empty_ledger(initial_search_score=12.0)
    record = plans.start_batch(ledger, 1, protocol="search", entry_best=12.0)
    normalised, _ = _validate(_payload())
    record["plans"].extend(normalised)
    record["plans"][0].update({"status": plans.COMPLETED, "label": 1, "score": 11.7})
    record["plans"][1].update({"status": plans.COMPLETED, "label": 1, "score": 11.9})
    record["plans"][2].update({"status": plans.COMPLETED, "label": 0, "score": 12.2})
    picked = plans.winners(ledger)
    assert [plan["id"] for plan in picked] == ["b1-s01", "b1-s02"]


def test_summary_counts():
    ledger = plans.empty_ledger(initial_search_score=12.0)
    record = plans.start_batch(ledger, 1, protocol="search", entry_best=12.0)
    normalised, _ = _validate(_payload())
    record["plans"].extend(normalised)
    record["plans"][0]["status"] = plans.COMPLETED
    record["plans"][1]["status"] = plans.RUNNING
    summary = plans.summarise(ledger)
    assert summary["total"] == 12
    assert summary["completed"] == 1
    assert summary["running"] == 1
    assert summary["pending"] == 10

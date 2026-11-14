"""Evaluation protocols: defaults and overrides."""

from __future__ import annotations

from vrpar.nn_improve.budget import eval_spec, nn_config


def test_default_protocols():
    config: dict = {}
    search = eval_spec(config, "search")
    acceptance = eval_spec(config, "acceptance")
    assert (search.epochs, search.train_episodes) == (50, 5000)
    assert (acceptance.epochs, acceptance.train_episodes) == (200, 5000)
    assert search.pins == {"epochs": 50, "train_episodes": 5000}
    assert acceptance.pins == {"epochs": 200, "train_episodes": 5000}


def test_overrides_and_train_epochs_alias():
    config = {
        "nn_improve": {
            "search": {"train_epochs": 20, "train_episodes": 500},
            "acceptance": {"epochs": 200, "train_episodes": 5000, "timeout_seconds": 999},
        }
    }
    search = eval_spec(config, "search")
    assert (search.epochs, search.train_episodes) == (20, 500)
    acceptance = eval_spec(config, "acceptance")
    assert acceptance.timeout_seconds == 999


def test_nn_config_defaults():
    nn = nn_config({})
    assert nn["enabled"] is True
    assert nn["rounds"] == 3
    assert nn["strategies_per_batch"] == 10
    assert nn["mutations_per_batch"] == 2
    assert nn["max_repairs_per_plan"] == 1
    assert nn["deny"] == []


def test_nn_config_reads_the_deny_list():
    nn = nn_config({"nn_improve": {"space": {"deny": ["scale.problem_size"]}}})
    assert nn["deny"] == ["scale.problem_size"]

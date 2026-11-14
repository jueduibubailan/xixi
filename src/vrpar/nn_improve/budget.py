"""The evaluation protocols: search (ranking) vs acceptance (the official score)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SEARCH = "search"
ACCEPTANCE = "acceptance"

DEFAULT_SEARCH = {"epochs": 50, "train_episodes": 5000, "timeout_seconds": 14400}
DEFAULT_ACCEPTANCE = {"epochs": 200, "train_episodes": 5000, "timeout_seconds": 14400}


@dataclass(frozen=True)
class EvalSpec:
    protocol: str
    epochs: int
    train_episodes: int
    timeout_seconds: int

    @property
    def pins(self) -> dict[str, int]:
        return {"epochs": int(self.epochs), "train_episodes": int(self.train_episodes)}


def _block(config: dict[str, Any], protocol: str) -> dict[str, Any]:
    nn = config.get("nn_improve") or {}
    raw = dict(nn.get(protocol) or {})
    defaults = DEFAULT_SEARCH if protocol == SEARCH else DEFAULT_ACCEPTANCE
    merged = dict(defaults)
    # A bundle may spell the keys either way; `epochs` wins when both are present.
    if "train_epochs" in raw and "epochs" not in raw:
        raw["epochs"] = raw.pop("train_epochs")
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def eval_spec(config: dict[str, Any], protocol: str = ACCEPTANCE) -> EvalSpec:
    protocol = SEARCH if protocol == SEARCH else ACCEPTANCE
    block = _block(config, protocol)
    return EvalSpec(
        protocol=protocol,
        epochs=int(block.get("epochs") or (50 if protocol == SEARCH else 200)),
        train_episodes=int(block.get("train_episodes") or 5000),
        timeout_seconds=int(block.get("timeout_seconds") or 14400),
    )


def nn_config(config: dict[str, Any]) -> dict[str, Any]:
    """The nn_improve block with defaults filled in (see the plan's §11)."""
    nn = dict(config.get("nn_improve") or {})
    nn.setdefault("enabled", True)
    nn.setdefault("rounds", 3)
    nn.setdefault("strategies_per_batch", 10)
    nn.setdefault("mutations_per_batch", 2)
    nn.setdefault("one_change_per_plan", True)
    nn.setdefault("label_baseline", "initial-search")
    nn.setdefault("min_delta", 0.0)
    nn.setdefault("strict_strategies", False)
    nn.setdefault("strategies_file", "")
    nn.setdefault("max_repairs_per_plan", 1)
    nn.setdefault("on_invalid_plans", "fail")
    nn.setdefault("stop_when_no_winner", True)
    nn.setdefault("base_pool_size", 10)
    nn.setdefault("space", {})
    nn["rounds"] = int(nn["rounds"])
    nn["strategies_per_batch"] = int(nn["strategies_per_batch"])
    nn["mutations_per_batch"] = int(nn["mutations_per_batch"])
    nn["max_repairs_per_plan"] = int(nn["max_repairs_per_plan"])
    nn["min_delta"] = float(nn["min_delta"])
    deny = (nn.get("space") or {}).get("deny") or []
    nn["deny"] = list(deny)
    return nn


__all__ = [
    "ACCEPTANCE",
    "DEFAULT_ACCEPTANCE",
    "DEFAULT_SEARCH",
    "EvalSpec",
    "SEARCH",
    "eval_spec",
    "nn_config",
]

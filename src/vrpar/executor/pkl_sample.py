"""Dump a few raw records from a problem's `.pkl` so the analysis stage can read the layout.

The engine deliberately interprets nothing here: MVMoE's datasets come in different
arities (4/5/7/8 fields depending on the variant), and which field means what is the
analysis stage's call. All this module does is `pickle.load` and pretty-print the first
`limit` records with their *shape*, never their meaning.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

DEFAULT_LIMIT = 50


def _shape(value: Any) -> str:
    """A shape label, e.g. ``list[50] of list[2]`` or ``float``."""
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (list, tuple)):
            return "list[{}] of list[{}]".format(len(value), len(value[0]))
        return "list[{}]".format(len(value))
    return type(value).__name__


def sample(path: str | Path, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Return a JSON-able description of the first `limit` records in `path`."""
    source = Path(path)
    with source.open("rb") as handle:
        records = pickle.load(handle)
    records = list(records)
    head = records[: max(1, limit)]
    arity = len(head[0]) if head else 0
    return {
        "file": source.name,
        "records": len(records),
        "fields": arity,
        "field_shapes": [_shape(item) for item in head[0]] if head else [],
        "sample": [
            [
                round(value, 6) if isinstance(value, float) else value
                for value in (record[:arity] if arity else record)
            ]
            for record in head[:3]
        ],
    }


def dump(path: str | Path, out_path: str | Path, limit: int = DEFAULT_LIMIT) -> Path:
    """Write `sample(path, limit)` to `out_path` as JSON and return the path."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sample(path, limit), indent=2), encoding="utf-8")
    return target

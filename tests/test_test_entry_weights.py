"""How `test_n{n}.py` finds its weights.

`checkpoints/` is a **drop box**: it ships empty, the user trains, then puts the
`checkpoint-*.pt` they want evaluated in there. The test entry point must therefore read
the directory rather than a fixed filename -- and it must hand POMO a `(path, epoch)`
pair that describes **one** file, because `CVRPTester` rebuilds the name as
`'{path}/checkpoint-{epoch}.pt'`.

The resolver below is the implementation the generation prompt specifies. It lives here
as well so the rule is executable: if the prompt's rule changes, this test is what says
so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import PACKAGE_PROBLEMS, baseline_of
from vrpar.problems import load_problem

EPOCH_PREFIX = "checkpoint-"
CHECKPOINT_GLOB = "checkpoint"
PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")


def resolve_model_load(entry: str) -> dict:
    """The two-level resolution: explicit override, then the highest epoch in a dir."""
    if entry.endswith(".pt"):
        name = os.path.basename(entry)
        return {"path": os.path.dirname(entry) or ".", "epoch": int(name[len(EPOCH_PREFIX):-3])}
    best_epoch = -1
    for name in os.listdir(entry):
        if not (name.startswith(CHECKPOINT_GLOB) and name.endswith(".pt")):
            continue
        try:
            epoch = int(name[len(EPOCH_PREFIX):-3])
        except ValueError:
            continue  # a `checkpoint-something.pt` that is not an epoch: not ours
        if epoch > best_epoch:
            best_epoch = epoch
    return {"path": entry, "epoch": best_epoch}


def _drop_box(scratch: Path, names: list[str]) -> Path:
    box = scratch / "checkpoints"
    box.mkdir(parents=True, exist_ok=True)
    for name in names:
        (box / name).write_bytes(b"")
    return box


def test_the_highest_epoch_wins(scratch: Path):
    """Several weights in the drop box: evaluate the most trained one."""
    box = _drop_box(scratch, ["checkpoint-500.pt", "checkpoint-30500.pt", "checkpoint-2.pt"])
    assert resolve_model_load(str(box)) == {"path": str(box), "epoch": 30500}


def test_non_matching_entries_are_skipped(scratch: Path):
    """Only `checkpoint<epoch>.pt` counts; anything else must not be picked."""
    box = _drop_box(
        scratch,
        ["checkpoint-100.pt", "notes.txt", "style_cvrp_20.json", "checkpoint-bad.pt", "best.pt"],
    )
    assert resolve_model_load(str(box)) == {"path": str(box), "epoch": 100}


def test_an_explicit_pt_file_supplies_its_own_epoch_and_parent(scratch: Path):
    """`--model-path a/b/checkpoint-7.pt` must describe that exact file."""
    box = _drop_box(scratch, ["checkpoint-30500.pt"])
    resolved = resolve_model_load(str(box / "checkpoint-7.pt"))
    assert resolved == {"path": str(box), "epoch": 7}
    # the tester's concatenation reconstructs the file that was pointed at
    rebuilt = os.path.join(resolved["path"], f"checkpoint-{resolved['epoch']}.pt")
    assert rebuilt == str(box / "checkpoint-7.pt")


def test_an_empty_drop_box_yields_no_epoch(scratch: Path):
    """Documented behaviour: no extra fallback, so the epoch stays unusable.

    The user's decision: do not add a friendly error layer here. The tester's own
    `FileNotFoundError` for `'{path}/checkpoint-{epoch}.pt'` is the signal. This test
    pins that the resolver does not invent an epoch (which would silently load the
    wrong file if one happened to exist).
    """
    box = _drop_box(scratch, [])
    assert resolve_model_load(str(box)) == {"path": str(box), "epoch": -1}


def test_a_missing_drop_box_raises(scratch: Path):
    """Not silently tolerated: a wrong path must fail loudly."""
    with pytest.raises(FileNotFoundError):
        resolve_model_load(str(scratch / "nope"))


# --------------------------------------------------------------------------- #
# the spec and the prompts must describe this rule
# --------------------------------------------------------------------------- #


def test_the_problem_declares_the_drop_box_and_the_glob():
    resources = PROBLEM.spec["resources"]
    assert resources["checkpoint_dir"] == "checkpoints"
    assert resources["checkpoint_glob"] == "checkpoint*.pt"
    # the reference filename is documentation only: it must not be bundled
    assert "checkpoints" not in resources["export"]["include"]


def test_the_prompts_forbid_a_hardcoded_epoch_or_filename():
    codegen = Path(PROBLEM.prompt("codegen")).read_text(encoding="utf-8")
    assert "drop box" in codegen
    assert "Never hardcode a checkpoint filename or an epoch" in codegen
    assert "highest epoch" in codegen
    # the glob is spelled out as a literal: the agent context is not rendered with the
    # stage vocabulary, so a placeholder there would reach the agent as `{...}`
    assert "checkpoint*.pt" in codegen
    assert "checkpoint_glob" not in codegen
    # the old three-tier rule is gone
    assert "os.walk" not in codegen
    assert "pretrained fallback" not in codegen

    context = PROBLEM.agent_context()
    assert "checkpoint*.pt" in context
    assert "checkpoint_glob" not in context


def test_the_plotting_styles_cover_the_shipped_set():
    """The set the prompt reasons about must actually be present, and none may clip.

    `style_cvrp_100.json` used to be the trap: missing from the payload, and upstream's
    copy pinning `ylim` to `10..30`. It now mirrors `style_cvrp_20.json`, so copying the
    baseline's own `train_n100.py` line is safe.
    """
    styles = baseline_of(PROBLEM) / "pomo" / "POMO" / "utils" / "log_image_style"
    for name in (
        "style_cvrp_20.json",
        "style_loss_1.json",
        "style_cvrp_100.json",
        "style_tsp_20.json",
    ):
        assert (styles / name).is_file(), f"{name} must ship with the baseline payload"

    objective = json.loads((styles / "style_cvrp_20.json").read_text(encoding="utf-8"))
    trained = json.loads((styles / "style_cvrp_100.json").read_text(encoding="utf-8"))
    assert objective["ylim"]["max"] is None, "the objective style must not clip the curve"
    assert trained == objective, (
        "style_cvrp_100.json must mirror style_cvrp_20.json: upstream's preset pins "
        "ylim to 10..30 and renders an empty figure for a from-scratch run"
    )
    # the loss style must stay fully autoscaled too
    loss = json.loads((styles / "style_loss_1.json").read_text(encoding="utf-8"))
    assert loss["ylim"] == {"min": None, "max": None}

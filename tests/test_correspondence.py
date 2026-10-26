"""Tests for the deterministic deliverable checks.

Three requirements are checkable without a model, so they are checked here rather
than trusted to the generation prompt:

* the deliverable is **one-to-one with POMO** -- every file adapted from the baseline
  declares its source, and the declared source exists;
* there is **no extra generated code** -- a `.py` file outside the declared set is a
  failure, because "the baseline plus an entry point" is the whole strategy;
* two settings are **not the agent's to choose** -- the training entry point must not
  load a checkpoint (an active relative path is the classic `FileNotFoundError`), and
  `trainer_params`/`tester_params` must keep POMO's values rather than a trimmed
  budget that finishes quickly.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import PACKAGE_PROBLEMS, baseline_of
from vrpar.executor.correspondence import (
    CORRESPONDENCE_LINE,
    POMO_TESTING_PARAMS,
    POMO_TRAINING_PARAMS,
    check_correspondence,
    declared_correspondence,
)
from vrpar.problems import load_problem

PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")


def _baseline() -> Path:
    """The baseline directory, resolved the way the seeder resolves it.

    The baseline is shared by every problem and lives at the project root, so `baseline_of`
    tries the problem directory first and the project root second. Reading the spec keeps the
    test in step with the engine instead of hardcoding a path.
    """
    return baseline_of(PROBLEM)


def _styles() -> Path:
    """The plotting-style directory inside the shared baseline."""
    return _baseline() / "pomo" / "POMO" / "utils" / "log_image_style"


TRAIN_PARAMS = """\
# baseline-correspondence: pomo/POMO/train_n100.py
trainer_params = {
    'problem_size': 20,
    'pomo_size': 20,
    'epochs': 8100,
    'train_episodes': 10 * 1000,
    'train_batch_size': 64,
    'logging': {'model_save_interval': 500, 'img_save_interval': 500},
    'model_load': {
        'enable': False,  # the CVRP100 checkpoint is the starting point
        # 'path': CHECKPOINT_DIR,  # directory path of pre-trained model and log files saved.
        # 'epoch': 30500,  # epoch version of pre-trained model to laod.
    }
}
"""

TEST_PARAMS = """\
# baseline-correspondence: pomo/POMO/test_n100.py
tester_params = {
    'problem_size': 20,
    'pomo_size': 20,
    'test_episodes': 10 * 1000,
    'test_batch_size': 1000,
    'aug_factor': 8,
    'aug_batch_size': 1000 * 8,
    'logging': {'model_save_interval': 500, 'img_save_interval': 500},
}
"""


def _deliverable(scratch: Path, files: dict[str, str], extra: dict[str, str] | None = None) -> Path:
    """Write a deliverable; `files` values are bodies, `extra` are unlisted files."""
    worktree = scratch / "worktrees" / "code_error"
    generated = worktree / "artifacts" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    for name, body in {**files, **(extra or {})}.items():
        target = generated / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return worktree


def _body(source: str | None, params: str = "") -> str:
    first = f"{CORRESPONDENCE_LINE} {source}\n" if source else ""
    return first + params


def _good_deliverable(scratch: Path, extra: dict[str, str] | None = None) -> Path:
    """A minimal deliverable that satisfies the whole table."""
    files = {}
    for name, source in PROBLEM.correspondence.items():
        if source is None:
            # a non-POMO deliverable: no correspondence line, valid syntax either way
            files[name] = "" if name.endswith(".md") else "# not adapted from POMO\n"
            continue
        if name.startswith("train_"):
            files[name] = TRAIN_PARAMS
        elif name.startswith("test_"):
            files[name] = TEST_PARAMS
        else:
            files[name] = _body(source, "")
    return _deliverable(scratch, files, extra)


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #


def test_a_conforming_deliverable_passes(scratch: Path):
    worktree = _good_deliverable(scratch)
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert report.passed, report.issues
    assert report.checked["delivered"]


def test_declared_correspondence_reads_the_first_lines(scratch: Path):
    path = scratch / "x.py"
    path.write_text(f"# a comment\n{CORRESPONDENCE_LINE} pomo/POMO/CVRPEnv.py\n", encoding="utf-8")
    assert declared_correspondence(path) == "pomo/POMO/CVRPEnv.py"

    path.write_text("# nothing here\n", encoding="utf-8")
    assert declared_correspondence(path) is None


# --------------------------------------------------------------------------- #
# one-to-one
# --------------------------------------------------------------------------- #


def test_a_missing_deliverable_is_a_failure(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "CVRPTWModel.py").unlink()
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("CVRPTWModel.py is missing" in issue for issue in report.issues)


def test_a_missing_correspondence_line_is_a_failure(scratch: Path):
    """Without the line, the link to POMO cannot be verified."""
    worktree = _good_deliverable(scratch)
    target = worktree / "artifacts" / "generated" / "CVRPTWEnv.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("baseline-correspondence" in issue and "CVRPTWEnv.py" in issue for issue in report.issues)


def test_a_wrong_correspondence_target_is_a_failure(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "CVRPTWEnv.py").write_text(
        f"{CORRESPONDENCE_LINE} pomo/POMO/CVRPModel.py\n", encoding="utf-8"
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("declares" in issue and "generated.correspondence" in issue for issue in report.issues)


def test_extra_generated_code_is_a_failure(scratch: Path):
    """The whole point of the strategy: the file set does not grow."""
    worktree = _good_deliverable(scratch, extra={"utils.py": "def helper():\n    return 1\n"})
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("utils.py is not part of the deliverable" in issue for issue in report.issues)


def test_a_baseline_named_copy_is_reported_as_extra(scratch: Path):
    """`CVRPEnv.py` next to `CVRPTWEnv.py` is exactly what must not ship."""
    worktree = _good_deliverable(scratch, extra={"CVRPEnv.py": "VALUE = 1\n"})
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("CVRPEnv.py is not part of the deliverable" in issue for issue in report.issues)


def test_run_py_and_readme_need_no_correspondence_line(scratch: Path):
    worktree = _good_deliverable(scratch)
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert report.passed, report.issues
    # they are declared with a null source, so a line would be wrong -- but their
    # absence is what the table expects, hence no issue above
    assert "run.py" in PROBLEM.correspondence
    assert PROBLEM.correspondence["run.py"] is None


# --------------------------------------------------------------------------- #
# trainer/tester conventions
# --------------------------------------------------------------------------- #


def test_training_model_load_with_an_active_path_is_a_failure(scratch: Path):
    """The exact crash: an active relative checkpoint path in training."""
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        f"{CORRESPONDENCE_LINE} pomo/POMO/train_n100.py\n"
        "trainer_params = {\n"
        "    'model_load': {'enable': True, 'path': '..\\\\..\\\\checkpoints', 'epoch': 30500},\n"
        "}\n",
        encoding="utf-8",
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    joined = " ".join(report.issues)
    assert "enable" in joined
    assert "must stay commented out" in joined


def test_training_without_the_commented_lines_warns(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        f"{CORRESPONDENCE_LINE} pomo/POMO/train_n100.py\n"
        "trainer_params = {\n"
        "    'epochs': 8100, 'train_episodes': 10 * 1000, 'train_batch_size': 64,\n"
        "    'model_save_interval': 500, 'img_save_interval': 500,\n"
        "    'model_load': {'enable': False},\n"
        "}\n",
        encoding="utf-8",
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert report.passed, report.issues
    assert any("model_load['path']" in warning for warning in report.warnings)


def test_a_shrunk_training_budget_is_a_failure(scratch: Path):
    """POMO's values, not a budget that happens to finish quickly."""
    worktree = _good_deliverable(scratch)
    shrunk = TRAIN_PARAMS.replace("'epochs': 8100", "'epochs': 5").replace(
        "'train_batch_size': 64", "'train_batch_size': 8"
    )
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(shrunk, encoding="utf-8")
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    joined = " ".join(report.issues)
    assert "'epochs'] = 5" in joined
    assert "train_batch_size" in joined


def test_a_shrunk_test_budget_is_a_failure(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "test_n20.py").write_text(
        TEST_PARAMS.replace("'test_batch_size': 1000", "'test_batch_size': 10"),
        encoding="utf-8",
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("test_batch_size" in issue for issue in report.issues)


def test_the_logging_intervals_are_found_where_pomo_puts_them(scratch: Path):
    """`model_save_interval`/`img_save_interval` live under `logging`."""
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        TRAIN_PARAMS.replace("'img_save_interval': 500", "'img_save_interval': 1"),
        encoding="utf-8",
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("img_save_interval" in issue for issue in report.issues)


def test_the_expected_values_are_pomos_own():
    """Guard the constants themselves: they are the spec's reference values."""
    assert POMO_TRAINING_PARAMS["epochs"] == "8100"
    assert POMO_TRAINING_PARAMS["train_episodes"] == "10*1000"
    assert POMO_TRAINING_PARAMS["train_batch_size"] == "64"
    assert POMO_TESTING_PARAMS["test_episodes"] == "10*1000"
    assert POMO_TESTING_PARAMS["test_batch_size"] == "1000"
    assert POMO_TESTING_PARAMS["aug_factor"] == "8"


def test_a_task_may_override_a_parameter(scratch: Path):
    """`generated.expected_params` is the documented escape hatch."""
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        TRAIN_PARAMS.replace("'epochs': 8100", "'epochs': 100"), encoding="utf-8"
    )
    assert not check_correspondence(scratch, worktree, PROBLEM).passed

    spec = dict(PROBLEM.spec)
    spec["generated"] = {**spec["generated"], "expected_params": {"epochs": "100"}}
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    plugin.spec = spec
    report = check_correspondence(scratch, worktree, plugin)
    assert report.passed, report.issues


# --------------------------------------------------------------------------- #
# spec wiring
# --------------------------------------------------------------------------- #


def test_the_correspondence_table_is_complete_and_real():
    table = PROBLEM.correspondence
    files = set(PROBLEM.spec["generated"]["files"])
    assert set(table) == files, "the table and the file list must agree"

    baseline = _baseline()
    for name, source in table.items():
        if source is None:
            assert name in {"run.py", "README.md"}
            continue
        # The table addresses the baseline the way the worktree lays it out, so the path is
        # used as-is: `resources.baseline_dir` already points at the directory that holds
        # `pomo/`, which is exactly what the generated code's first line names.
        assert (baseline / source).is_file(), f"{name} -> {source} is not in the baseline"


def test_the_deliverable_is_named_after_the_problem():
    tag = PROBLEM.problem_tag
    assert tag == "CVRPTW"
    for name in PROBLEM.spec["generated"]["files"]:
        if name in {"run.py", "README.md"} or name.startswith(("train_", "test_")):
            continue
        assert name.startswith(tag), f"{name} is not named after {tag}"


def test_the_plotting_styles_ship_with_the_baseline():
    """`utils.py` reads these relative to itself, so they must be in the payload."""
    styles = _styles()
    assert (styles / "style_cvrp_20.json").is_file()
    assert (styles / "style_loss_1.json").is_file()


# --------------------------------------------------------------------------- #
# plotting styles
# --------------------------------------------------------------------------- #


def _with_style(filename: str) -> str:
    """The training entry's params with a chosen style filename."""
    block = (
        "'logging': {'model_save_interval': 500, 'img_save_interval': 500,\n"
        "            'log_image_params_1': {'json_foldername': 'log_image_style',\n"
        f"                                  'filename': {filename!r}}},\n"
        "        },"
    )
    return TRAIN_PARAMS.replace(
        "'logging': {'model_save_interval': 500, 'img_save_interval': 500},",
        block,
    )


def test_a_named_style_file_that_exists_passes(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        _with_style("style_cvrp_20.json"), encoding="utf-8"
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert report.passed, report.issues
    assert report.checked["log_image_style"]


def test_a_named_style_file_that_does_not_exist_fails(scratch: Path):
    """A style name that is not shipped is a `FileNotFoundError` in the middle of training.

    `util_save_log_image_with_label` opens the file itself, so this would fail at the
    first `img_save_interval` -- far too late to be useful.
    """
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        _with_style("style_cvrp_999.json"), encoding="utf-8"
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    joined = " ".join(report.issues)
    assert "style_cvrp_999.json" in joined
    assert "FileNotFoundError" in joined


def test_the_baseline_style_names_are_shipped_and_do_not_clip(scratch: Path):
    """Both names the baseline uses must resolve, and neither may pin an upper bound.

    `style_cvrp_100.json` used to be a trap two different ways: it was missing from the
    payload (a `FileNotFoundError` mid-training), and the upstream copy pins `ylim` to
    `10..30`, which clips a from-scratch run whose objective starts far above that. It is
    now shipped with the same open bounds as `style_cvrp_20.json`.
    """
    worktree = _good_deliverable(scratch)
    for name in ("style_cvrp_100.json", "style_cvrp_20.json"):
        (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
            _with_style(name), encoding="utf-8"
        )
        report = check_correspondence(scratch, worktree, PROBLEM)
        assert report.passed, (name, report.issues)
        assert not any("pins an upper bound" in warning for warning in report.warnings), name


def test_a_pinned_upper_bound_is_flagged(scratch: Path):
    """A hardcoded `max` clips the curve into an empty-looking figure, not an error."""
    style_dir = _styles()
    victim = style_dir / "style_cvrp_20.json"
    original = victim.read_text(encoding="utf-8")
    victim.write_text(
        '{"figsize": {"x": 10, "y": 5}, "ylim": {"min": 0, "max": 30}, "grid": true}',
        encoding="utf-8",
    )
    try:
        worktree = _good_deliverable(scratch)
        (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
            _with_style("style_cvrp_20.json"), encoding="utf-8"
        )
        report = check_correspondence(scratch, worktree, PROBLEM)
    finally:
        victim.write_text(original, encoding="utf-8")

    assert report.passed, report.issues  # a warning, not a failure
    assert any("pins an upper bound" in warning for warning in report.warnings)


def test_a_style_block_without_a_filename_fails(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        TRAIN_PARAMS.replace(
            "'logging': {'model_save_interval': 500, 'img_save_interval': 500},",
            "'logging': {'model_save_interval': 500, 'img_save_interval': 500,\n"
            "            'log_image_params_1': {'json_foldername': 'log_image_style'},\n"
            "        },",
        ),
        encoding="utf-8",
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert not report.passed
    assert any("has no 'filename'" in issue for issue in report.issues)


def test_a_style_folder_outside_log_image_style_only_warns(scratch: Path):
    worktree = _good_deliverable(scratch)
    (worktree / "artifacts" / "generated" / "train_n20.py").write_text(
        _with_style("style_cvrp_20.json").replace("'log_image_style'", "'styles'"),
        encoding="utf-8",
    )
    report = check_correspondence(scratch, worktree, PROBLEM)
    assert report.passed, report.issues
    assert any("log_image_style" in warning for warning in report.warnings)


def test_the_shipped_objective_style_autoscales_its_y_axis():
    """The lesson from this change: a hardcoded window plots an empty figure.

    `style_cvrp_20.json` used to pin `ylim` to a range taken from a pretrained run
    (6.12..6.40). Against a run that starts from scratch the curve is off-screen, so the
    objective style autoscales instead.
    """
    import json

    styles = _styles()
    objective = json.loads((styles / "style_cvrp_20.json").read_text(encoding="utf-8"))
    loss = json.loads((styles / "style_loss_1.json").read_text(encoding="utf-8"))

    # the keys `utils.py` reads, with nothing missing
    for config in (objective, loss):
        assert set(config) >= {"figsize", "xlim", "ylim", "grid"}
        assert set(config["figsize"]) >= {"x", "y"}
        assert set(config["ylim"]) >= {"min", "max"}
        assert set(config["xlim"]) >= {"min", "max"}
        assert config["figsize"]["x"] >= 10  # a readable plot for a long run

    assert objective["ylim"]["min"] == 0, "the objective curve starts near zero"
    assert objective["ylim"]["max"] is None, "autoscale: the best cost is not known ahead"
    # the loss window is fully open: a run that starts from scratch has no useful
    # lower bound, and pinning one (the payload once carried `min: 10`) hides the
    # early, high-loss epochs.
    assert loss["ylim"]["min"] is None
    assert loss["ylim"]["max"] is None

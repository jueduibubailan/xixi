"""Deterministic checks that the deliverable really matches POMO.

Three of the task's requirements are checkable without a model, so they are checked
here rather than trusted to the generation prompt:

* **one-to-one with POMO** -- every deliverable declares which baseline file it came
  from (``# baseline-correspondence: <pomo path>``), the declared source exists, and
  the file is named after the problem (``CVRPTWEnv.py``, not ``CVRPEnv.py``);
* **no extra generated code** -- a ``.py`` file the table does not mention is a
  failure, because "the deliverable is the baseline plus an entry point" is the
  whole point of the strategy;
* **the two settings that are pure convention drift** -- the training entry point's
  ``model_load`` must leave ``path``/``epoch`` commented out (POMO only loads when
  ``enable`` is true, and a wrong relative path is the classic crash), and the
  training/test parameters must match POMO's own values rather than being trimmed
  down to something that finishes quickly.

The check is intentionally text/AST based: it inspects shipped source without
executing it, so it can run before the smoke run and cannot be defeated by an
import-time side effect.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cvrp.executor.contract import StepResult, locate_artifact, stage_search_paths


def _baseline_root(problem: Any | None) -> Path | None:
    """Where `pomo/...` lives, as the correspondence table addresses it.

    Resolved from `resources.baseline_dir` with the same rules the worktree seeder uses, so
    the checker and the seeder cannot disagree about what "the staged baseline" is. A bundle
    keeps its baseline in `seed/`; looking for `pomo/...` under the problem root instead of
    under that directory reported every deliverable as pointing outside the baseline.
    """
    if problem is None:
        return None
    raw = str(
        ((getattr(problem, "spec", {}) or {}).get("resources") or {}).get("baseline_dir") or ""
    )
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path(getattr(problem, "root", ".")) / candidate
        if candidate.is_dir():
            return candidate
    root = getattr(problem, "root", None)
    return Path(root) if root else None


CORRESPONDENCE_LINE = "# baseline-correspondence:"
_CORRESPONDENCE_RE = re.compile(r"^#\s*baseline-correspondence:\s*(\S+)\s*$")

#: POMO's own parameter values, by the key names its entry scripts use. A task may
#: declare overrides under ``generated.expected_params``; anything it does not name
#: is required to match POMO.
POMO_TRAINING_PARAMS: dict[str, str] = {
    "epochs": "8100",
    "train_episodes": "10*1000",
    "train_batch_size": "64",
    "model_save_interval": "500",
    "img_save_interval": "500",
}
POMO_TESTING_PARAMS: dict[str, str] = {
    "test_episodes": "10*1000",
    "test_batch_size": "1000",
    "aug_factor": "8",
}


@dataclass
class CorrespondenceReport:
    """What the check found: hard failures, plus everything worth telling the agent."""

    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.issues

    def message(self) -> str:
        parts = []
        if self.issues:
            parts.append("correspondence failures:\n  - " + "\n  - ".join(self.issues))
        if self.warnings:
            parts.append("notes:\n  - " + "\n  - ".join(self.warnings))
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# small source-level helpers
# --------------------------------------------------------------------------- #


def declared_correspondence(path: Path) -> str | None:
    """The baseline path a deliverable declares on its first line, if any."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
        match = _CORRESPONDENCE_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def _assigned_dict_literal(source: str, name: str) -> dict[str, Any] | None:
    """The literal dict assigned to ``name`` (``trainer_params = {...}``), if any.

    Only literal values are read; a computed value is reported as unknown rather than
    guessed at.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        if isinstance(node.value, ast.Dict):
            return _literal_dict(node.value)
    return None


def _literal_dict(node: ast.Dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if isinstance(value, ast.Constant):
            out[key.value] = value.value
        elif isinstance(value, ast.BinOp):
            out[key.value] = ast.unparse(value)
        elif isinstance(value, ast.Dict):
            out[key.value] = _literal_dict(value)
        else:
            out[key.value] = ast.unparse(value)
    return out


def _commented_out(source: str, key: str) -> bool:
    """Is ``'key'`` present but only inside a comment?"""
    pattern = re.compile(r"#\s*['\"]" + re.escape(key) + r"['\"]\s*:")
    return bool(pattern.search(source))


def _pomo_value(name: str) -> str:
    return POMO_TRAINING_PARAMS.get(name) or POMO_TESTING_PARAMS.get(name) or ""


def _describe_value(value: Any) -> str:
    return ast.unparse(ast.parse(str(value), mode="eval").body) if isinstance(value, str) else repr(value)


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #


def check_correspondence(
    run_dir: Path,
    worktree: Path,
    problem: Any | None,
    generated_rel: str = "artifacts/generated",
) -> CorrespondenceReport:
    """Run every deterministic deliverable check and collect the findings."""
    report = CorrespondenceReport()
    roots = stage_search_paths(run_dir, worktree)
    target = locate_artifact(roots, generated_rel)
    if target is None or not target.is_dir():
        report.issues.append(f"{generated_rel} does not exist -- nothing to check")
        return report

    table: dict[str, str | None] = {}
    if problem is not None and hasattr(problem, "correspondence"):
        table = dict(problem.correspondence or {})
    report.checked["declared"] = sorted(table)

    expected = {name for name, source in table.items() if source}
    delivered = sorted(path.name for path in target.glob("*.py") if path.name != "__init__.py")
    report.checked["delivered"] = delivered

    # 1. every declared deliverable is present, and its declared source exists
    for name, source in sorted(table.items()):
        path = target / name
        if not path.is_file():
            report.issues.append(f"{name} is missing (declared in generated.correspondence)")
            continue
        if source is None:
            continue
        if declared_correspondence(path) is None:
            report.issues.append(
                f"{name} has no `{CORRESPONDENCE_LINE} {source}` first line, so the "
                "one-to-one link to POMO cannot be verified"
            )

    # 2. no extra generated code
    for name in delivered:
        if name not in table:
            report.issues.append(
                f"{name} is not part of the deliverable: extra generated code "
                "(the baseline plus run.py is the whole file set)"
            )

    # 3. each declared link points at the entry the table names, and it exists
    if problem is not None:
        pomo_root = _baseline_root(problem)
        for name, source in sorted(table.items()):
            if source is None or name not in delivered:
                continue
            declared = declared_correspondence(target / name)
            if declared is None:
                continue  # already reported above
            if declared != source:
                report.issues.append(
                    f"{name} declares `{declared}` but generated.correspondence says `{source}`"
                )
            if pomo_root is not None and not (pomo_root / source).is_file():
                report.issues.append(f"{name} points at {source}, which is not in the staged baseline")

    # 4. naming: a deliverable named after the problem, and derivable from its source
    tag = getattr(problem, "problem_tag", "") if problem is not None else ""
    if tag:
        for name in expected:
            if name.startswith("train_") or name.startswith("test_"):
                continue
            if not name.startswith(tag):
                report.warnings.append(
                    f"{name} does not start with the problem tag {tag!r}; the naming rule "
                    f"is {{problem}}<Role>.py"
                )

    # 5. the entry points' conventions
    _check_entry_params(target, table, problem, report)
    _check_model_load(target, table, report)
    _check_plot_styles(target, table, problem, report)
    return report


def _check_plot_styles(
    target: Path,
    table: dict[str, str | None],
    problem: Any | None,
    report: CorrespondenceReport,
) -> None:
    """The plotting styles the training entry point names must exist, and must fit.

    `util_save_log_image_with_label` opens the file itself
    (``os.path.join(os.path.dirname(utils.__file__), folder_name, file_name)``), so a
    name that is not shipped is a ``FileNotFoundError`` at the first
    ``img_save_interval`` -- long after the executor has stopped looking.

    Existence is not enough, though. A style file also carries `ylim`/`xlim`, and a
    pinned upper bound clips the curve: upstream's CVRP-100 preset pins `ylim` to
    `10..30`, which is right for its benchmark and wrong for a from-scratch run whose
    objective starts far above it. That renders an empty figure rather than an error, so
    it has to be flagged statically.
    """
    style_root = None
    if problem is not None:
        # Relative to the baseline directory, which is where the generated code will look
        # too: `utils.py` resolves the style next to itself, and the deliverable imports it
        # from the staged baseline.
        baseline = _baseline_root(problem)
        if baseline is not None:
            candidate = baseline / "pomo" / "POMO" / "utils" / "log_image_style"
            if candidate.is_dir():
                style_root = candidate
    if style_root is None:
        return
    available = sorted(path.name for path in style_root.glob("*.json"))
    report.checked["log_image_style"] = available

    for name in sorted(table):
        if not name.startswith("train_"):
            continue
        path = target / name
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        params = _assigned_dict_literal(source, "trainer_params") or {}
        logging_params = params.get("logging") if isinstance(params.get("logging"), dict) else {}
        for key in ("log_image_params_1", "log_image_params_2"):
            block = logging_params.get(key)
            if not isinstance(block, dict):
                continue
            folder = str(block.get("json_foldername") or "")
            filename = str(block.get("filename") or "")
            if not filename:
                report.issues.append(f"{name}: logging[{key!r}] has no 'filename'")
                continue
            if folder and folder != "log_image_style":
                report.warnings.append(
                    f"{name}: logging[{key!r}]['json_foldername'] = {folder!r}; the shipped "
                    "styles live in 'log_image_style'"
                )
            if filename not in available:
                report.issues.append(
                    f"{name}: logging[{key!r}] names {filename!r}, which is not in "
                    f"log_image_style/ (available: {', '.join(available) or 'none'}) -- "
                    "plotting would raise FileNotFoundError at the first img_save_interval"
                )
                continue
            _check_style_bounds(name, key, filename, style_root / filename, report)


def _check_style_bounds(
    entry_name: str,
    key: str,
    filename: str,
    path: Path,
    report: CorrespondenceReport,
) -> None:
    """Flag a pinned upper bound, which silently clips the plotted curve."""
    try:
        style = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report.warnings.append(f"{entry_name}: could not read {filename} to check its bounds")
        return
    if not isinstance(style, dict):
        return
    for axis in ("ylim", "xlim"):
        window = style.get(axis)
        if not isinstance(window, dict):
            continue
        upper = window.get("max")
        if upper is None:
            continue  # autoscaled: cannot clip
        report.warnings.append(
            f"{entry_name}: logging[{key!r}] uses {filename}, whose {axis}.max = {upper!r} "
            "pins an upper bound -- a from-scratch run can exceed it and the curve gets "
            "clipped (an empty-looking figure rather than an error). Prefer `null`."
        )


def _check_entry_params(
    target: Path,
    table: dict[str, str | None],
    problem: Any | None,
    report: CorrespondenceReport,
) -> None:
    """`trainer_params` / `tester_params` must keep POMO's own values."""
    overrides = {}
    if problem is not None:
        raw = (problem.spec.get("generated") or {}).get("expected_params") or {}
        if isinstance(raw, dict):
            overrides = {str(k): str(v) for k, v in raw.items()}

    for name in sorted(table):
        if name.startswith("train_"):
            params_name, baseline = "trainer_params", POMO_TRAINING_PARAMS
        elif name.startswith("test_"):
            params_name, baseline = "tester_params", POMO_TESTING_PARAMS
        else:
            continue
        path = target / name
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        params = _assigned_dict_literal(source, params_name)
        if params is None:
            report.warnings.append(f"{name}: could not read {params_name} as a literal dict")
            continue
        # An override only applies to the parameter set it names; a key the baseline
        # keeps nested under `logging` is looked up there too.
        logging_params = params.get("logging") if isinstance(params.get("logging"), dict) else {}
        expected_values = {
            **baseline,
            **{key: value for key, value in overrides.items() if key in baseline},
        }
        for key, expected_value in expected_values.items():
            if key in params:
                actual = params[key]
            elif key in logging_params:
                actual = logging_params[key]
            else:
                report.issues.append(
                    f"{name}: {params_name} is missing {key!r} (POMO has {expected_value})"
                )
                continue
            actual_text = str(actual).replace(" ", "")
            expected_text = str(expected_value).replace(" ", "")
            if actual_text != expected_text:
                report.issues.append(
                    f"{name}: {params_name}[{key!r}] = {actual!r}, but POMO's value is "
                    f"{expected_value} -- the training budget must match the baseline"
                )


def _check_model_load(
    target: Path,
    table: dict[str, str | None],
    report: CorrespondenceReport,
) -> None:
    """The training entry point must not try to load a checkpoint at all.

    POMO's `train_n100.py` ships `enable: False` with `path`/`epoch` commented out,
    and only the tester loads weights. Setting `enable: True` with a relative path is
    the classic `FileNotFoundError` this check exists to prevent.
    """
    for name in sorted(table):
        if not name.startswith("train_"):
            continue
        path = target / name
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        params = _assigned_dict_literal(source, "trainer_params") or {}
        model_load = params.get("model_load") if isinstance(params.get("model_load"), dict) else None
        if model_load is None:
            report.warnings.append(f"{name}: trainer_params has no literal 'model_load' block")
            continue
        if model_load.get("enable") is not False:
            report.issues.append(
                f"{name}: trainer_params['model_load']['enable'] must be False "
                "(training starts fresh; only the tester loads the checkpoint)"
            )
        for key in ("path", "epoch"):
            if key in model_load:
                report.issues.append(
                    f"{name}: model_load[{key!r}] is active -- it must stay commented out "
                    "(a wrong relative checkpoint path crashes training before epoch 1)"
                )
            elif not _commented_out(source, key):
                report.warnings.append(
                    f"{name}: model_load[{key!r}] is absent; POMO keeps the commented line "
                    "as documentation -- consider preserving it"
                )


def correspondence_step(
    run_dir: Path,
    worktree: Path,
    problem: Any | None = None,
    generated_rel: str = "artifacts/generated",
    name: str = "correspondence",
) -> tuple[StepResult, CorrespondenceReport]:
    """Wrap :func:`check_correspondence` as an executor step."""
    report = check_correspondence(run_dir, worktree, problem, generated_rel)
    step = StepResult(
        name=name,
        passed=report.passed,
        error_class="" if report.passed else "correspondence",
        message="" if report.passed and not report.warnings else report.message(),
    )
    return step, report


__all__ = [
    "CORRESPONDENCE_LINE",
    "POMO_TESTING_PARAMS",
    "POMO_TRAINING_PARAMS",
    "CorrespondenceReport",
    "check_correspondence",
    "correspondence_step",
    "declared_correspondence",
]

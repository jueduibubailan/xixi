"""The nn improve loop helpers: gate pins and snapshot/restore mechanics."""

from __future__ import annotations

from pathlib import Path

from vrpar.pipeline.gates import check_code_error
from vrpar.run.worktree import prune_generated, restore_generated, snapshot_tree


def _run_with_pins(scratch: Path, epochs: int) -> Path:
    run = scratch / "run"
    generated = run / "artifacts" / "generated"
    generated.mkdir(parents=True)
    (generated / "test_train.py").write_text(
        "trainer_params = {\n    'epochs': %d,\n    'train_episodes': 5000,\n}\n" % epochs,
        encoding="utf-8",
    )
    report = run / "artifacts" / "runs" / "plan-x"
    report.mkdir(parents=True)
    (report / "test_train.exitcode").write_text("0\n", encoding="utf-8")
    (report / "test.exitcode").write_text("0\n", encoding="utf-8")
    (report / "test.stdout.txt").write_text(" AUGMENTATION SCORE: 12.3000 \n", encoding="utf-8")
    return run


def test_gate_accepts_the_search_pins_and_rejects_the_acceptance_pins(scratch: Path):
    run = _run_with_pins(scratch, epochs=50)
    search = check_code_error(run, "artifacts/runs/plan-x", {"epochs": 50, "train_episodes": 5000})
    assert search.passed is True
    assert search.score == 12.3

    acceptance = check_code_error(run, "artifacts/runs/plan-x", {"epochs": 200, "train_episodes": 5000})
    assert acceptance.passed is False
    assert any("epochs" in issue for issue in acceptance.issues)


def test_gate_reads_the_reported_subdirectory(scratch: Path):
    run = _run_with_pins(scratch, epochs=50)
    missing = check_code_error(run, "artifacts/runs/other", {"epochs": 50, "train_episodes": 5000})
    assert missing.passed is False
    assert any("exitcode is missing" in issue for issue in missing.issues)


def test_snapshot_prunes_training_artifacts_and_restores(scratch: Path):
    source = scratch / "source"
    source.mkdir()
    (source / "Model.py").write_text("# model\n", encoding="utf-8")
    (source / "checkpoint-50.pt").write_text("weights", encoding="utf-8")
    (source / "result").mkdir()
    (source / "result" / "log.txt").write_text("log", encoding="utf-8")

    snapshot = scratch / "snapshot"
    snapshot_tree(source, snapshot)
    assert (snapshot / "Model.py").is_file()
    assert not (snapshot / "checkpoint-50.pt").exists()
    assert not (snapshot / "result").exists()

    worktree = scratch / "worktree"
    (worktree / "artifacts").mkdir(parents=True)
    (worktree / "artifacts" / "generated").mkdir()
    (worktree / "artifacts" / "generated" / "stale.py").write_text("stale", encoding="utf-8")
    restore_generated(worktree, snapshot)
    assert (worktree / "artifacts" / "generated" / "Model.py").is_file()
    assert not (worktree / "artifacts" / "generated" / "stale.py").exists()


def test_prune_generated_is_safe_on_a_missing_directory(scratch: Path):
    prune_generated(scratch / "nope")

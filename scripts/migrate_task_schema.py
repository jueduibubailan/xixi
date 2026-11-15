"""Move the pre-1.1 task-file keys into each bundle's ``litellm_config.yaml``.

The current layout keeps only ``task: {name, slug, description, tips}`` in
``task.yaml``. Older bundles mixed the environment and budget into the task file;
this script moves those keys next to it and removes the fields the pipeline no
longer reads.

Run it from the repository root:

    uv run python scripts/migrate_task_schema.py
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBLEMS = ROOT / "problems"

KEEP_TASK = ("name", "slug", "description", "tips")
MOVE_KEYS = (
    "problem",
    "scale",
    "stages",
    "llm",
    "workspace",
    "run",
    "grader",
    "loop",
    "score",
    "mock",
)


def load(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    count = 0
    for task_path in sorted(PROBLEMS.glob("*/task.yaml")):
        bundle = task_path.parent
        task_file = load(task_path)
        env_path = bundle / "litellm_config.yaml"
        env = load(env_path)

        task = task_file.get("task") or {}
        task_block = {key: task[key] for key in KEEP_TASK if key in task}

        # Move old task-level config keys into the environment layer.
        for key in MOVE_KEYS:
            if key in task_file and key not in env:
                env[key] = task_file[key]

        # Remove fields the new pipeline does not read.
        for key in ("grader", "scale", "program", "cli", "acceptance", "scorer", "mock", "problem"):
            env.pop(key, None)

        env.setdefault("stages", "default")
        score = env.get("score") if isinstance(env.get("score"), dict) else {}
        score.setdefault("direction", "minimize")
        env["score"] = score
        loop = env.get("loop") if isinstance(env.get("loop"), dict) else {}
        env["loop"] = {"max_rounds": int(loop.get("max_rounds") or 5)}
        env.setdefault("llm", {})
        env.setdefault("workspace", {})
        env.setdefault("run", {})

        task_path.write_text(
            yaml.safe_dump({"task": task_block}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        env_path.write_text(
            yaml.safe_dump(env, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

        for name in ("context.md", "mock_analysis.json", "mock_solver.py"):
            stale = bundle / name
            if stale.is_file():
                stale.unlink()
        count += 1

    # The root task is gone: every task lives in problems/<slug>/.
    root_task = ROOT / "task.yaml"
    if root_task.is_file():
        root_task.unlink()

    root_env_path = ROOT / "litellm_config.yaml"
    root_env = load(root_env_path)
    for key in ("problem", "grader", "scale"):
        root_env.pop(key, None)
    root_env.setdefault("stages", "default")
    root_env.setdefault("llm", {})
    root_env.setdefault("workspace", {})
    root_env.setdefault("run", {})
    root_env_path.write_text(
        yaml.safe_dump(root_env, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    print(f"migrated {count} task files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

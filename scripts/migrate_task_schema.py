from pathlib import Path
import yaml

ROOT = Path(r"G:\work\new")
PROBLEMS = ROOT / "problems"

KEEP_TASK = ("name", "slug", "description", "tips")
MOVE_KEYS = ("problem", "scale", "stages", "llm", "workspace", "run", "grader", "loop", "score", "mock")

def load(p):
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}

for task_path in sorted(PROBLEMS.glob("*/task.yaml")):
    bdir = task_path.parent
    task_file = load(task_path)
    env_path = bdir / "litellm_config.yaml"
    env = load(env_path)

    task = task_file.get("task") or {}
    task_block = {k: task[k] for k in KEEP_TASK if k in task}

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

    task_path.write_text(yaml.safe_dump({"task": task_block}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    env_path.write_text(yaml.safe_dump(env, sort_keys=False, allow_unicode=True), encoding="utf-8")

    for name in ("context.md", "mock_analysis.json", "mock_solver.py"):
        p = bdir / name
        if p.is_file():
            p.unlink()

# Root task is gone: every task lives in problems/<slug>/.
root_task = ROOT / "task.yaml"
if root_task.is_file():
    root_task.unlink()

root_env = load(ROOT / "litellm_config.yaml")
for key in ("problem", "grader", "scale"):
    root_env.pop(key, None)
root_env.setdefault("stages", "default")
root_env.setdefault("llm", {})
root_env.setdefault("workspace", {})
root_env.setdefault("run", {})
(ROOT / "litellm_config.yaml").write_text(yaml.safe_dump(root_env, sort_keys=False, allow_unicode=True), encoding="utf-8")

print(f"migrated {len(list(PROBLEMS.glob('*/task.yaml')))} task files")

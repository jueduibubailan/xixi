"""Where records live, and what they are called.

Three areas, deliberately separate:

```
<records dir>/                     default `./records`, project-local
├─ temp/<run_id>/                  one file per agent, written during the run
├─ related/<run_id>/               what retrieval selected for this run
└─ main/                           the cross-run knowledge base
   ├─ skills/_global_skill.md      ★general: holds for any problem
   ├─ skills/<tag>_skill.md        problem-specific procedure
   ├─ notes/<tag>_notes.md         problem-specific facts
   ├─ attempts/<tag>_attempts.md   problem-specific run blocks
   └─ index.json
```

Why three: the *shape* of the knowledge differs. `temp/` is raw evidence from one run and
is disposable. `related/` is a per-run selection, so two runs of the same problem cannot
overwrite each other's context. `main/` is the only thing that outlives a run, and it is
the only thing retrieval reads.

Naming follows the **problem tag** (`CVRPTW` -> `cvrptw_skill.md`), not the task, because
one problem serves several scales (n=20/50/100) and the procedural knowledge transfers
between them.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# the record kinds
# --------------------------------------------------------------------------- #

#: The five per-run records, in pipeline order. The value is the file stem.
RECORD_KINDS: dict[str, str] = {
    "problem_description": "problem_description",  # engine, at run start
    "description_parsing": "description_parsing",  # retrieval agent, step 1
    "new_problem_code": "new_problem_code",  # code generation agent
    "code_error": "code_error",  # code error stage (facts + model narration)
    "code_revise": "code_revise",  # code revise agent
}

#: `main/` subdirectories, in the order a reader should consider them.
MAIN_AREAS = ("skills", "notes", "attempts")

GLOBAL_SKILL = "_global_skill.md"


def is_general(name: str | Path) -> bool:
    """Is this a cross-problem record?

    Two tiers live side by side in `main/`: `_global_*.md` holds knowledge that transfers
    to any problem this pipeline solves (baseline adaptation, the host environment, the
    gate order), while `<tag>_*` holds what only that problem's runs know. The distinction
    lives in the file name so both the retrieval prompt and a human can separate the tiers
    without reading anything.
    """
    return Path(str(name)).name.startswith("_global")


def tier_of(name: str | Path) -> str:
    return "general" if is_general(name) else "scoped"


def area_for(kind: str) -> str:
    """Which `main/` area a record kind feeds."""
    if kind in {"problem_description", "description_parsing"}:
        return "notes"
    if kind in {"new_problem_code", "code_revise"}:
        return "skills"
    return "attempts"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #

DEFAULT_RECORDS_DIR = "./records"


def records_root(config: dict[str, Any] | None = None) -> Path:
    """Resolve the records directory.

    Deliberately **not** `config.resolve_path`: that helper also searches inside the
    problem directory and the installed package, and this very module lives at
    `<package>/records`. Resolving `./records` through it would place the knowledge base
    inside the source tree, where it would shadow the project directory and travel with
    the package. A records directory belongs to the project (or to an absolute path the
    task names), and nowhere else.
    """
    config = config or {}
    spec = config.get("records") or {}
    raw = Path(str(spec.get("dir") or DEFAULT_RECORDS_DIR)).expanduser()
    if raw.is_absolute():
        return raw
    for anchor in (config.get("_project_root"), config.get("_task_dir")):
        if anchor:
            return Path(str(anchor)) / raw
    return Path.cwd() / raw


def subdir(config: dict[str, Any] | None, name: str, default: str) -> str:
    spec = (config or {}).get("records") or {}
    return str(spec.get(name) or default)


def temp_dir(config: dict[str, Any] | None, run_id: str) -> Path:
    return records_root(config) / subdir(config, "temp", "temp") / run_id


def related_dir(config: dict[str, Any] | None, run_id: str) -> Path:
    return records_root(config) / subdir(config, "related", "related") / run_id


def main_dir(config: dict[str, Any] | None) -> Path:
    return records_root(config) / subdir(config, "main", "main")


def keep_attempts(config: dict[str, Any] | None) -> int:
    try:
        return max(1, int(((config or {}).get("records") or {}).get("keep_attempts") or 20))
    except (TypeError, ValueError):
        return 20


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #


def tag_of(problem: Any) -> str:
    """The lowercase tag used in file names (`cvrptw`)."""
    if problem is None:
        return "problem"
    raw = str(getattr(problem, "problem_tag", "") or getattr(problem, "slug", "") or "problem")
    return _safe(raw).lower()


def skill_name(tag: str) -> str:
    return f"{_safe(tag).lower()}_skill.md"


def notes_name(tag: str) -> str:
    return f"{_safe(tag).lower()}_notes.md"


def attempts_name(tag: str) -> str:
    return f"{_safe(tag).lower()}_attempts.md"


def main_paths(problem: Any) -> dict[str, str]:
    """Relative paths inside `main/` for one problem, keyed by area."""
    tag = tag_of(problem)
    return {
        "skills": skill_name(tag),
        "notes": notes_name(tag),
        "attempts": attempts_name(tag),
    }


def _safe(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text)).strip("-")
    return cleaned or "problem"


# --------------------------------------------------------------------------- #
# record payloads
# --------------------------------------------------------------------------- #


def record_file(kind: str, run_id: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The `(filename, payload)` for one record kind.

    A single file per kind per run: the update agent reads the run's records as a set,
    and multiple files per kind would only make ordering ambiguous.
    """
    stem = RECORD_KINDS.get(kind, _safe(kind))
    body = {"record": kind, "run_id": run_id, "written_at": now_iso(), **payload}
    return f"{stem}.json", body


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_text(path: Path, max_chars: int = 20_000) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= max_chars else text[:max_chars] + "\n…(truncated)\n"

"""Reading and writing the records, including the merge into `main/`.

The update agent produces records in its own worktree, exactly like every other agent,
and this module is what moves them into the canonical knowledge base. That keeps the
"agents only write inside their worktree" rule intact, and it keeps the merge auditable:
the merge is code, and code can be tested.

Merging is **append + dedupe**, never overwrite. A knowledge base that a single bad run
can erase is worse than no knowledge base: the notes that took three rounds to earn would
be gone, and nobody would notice.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from cvrp.records.layout import (
    GLOBAL_SKILL,
    MAIN_AREAS,
    keep_attempts,
    main_dir,
    main_paths,
    read_json,
    read_text,
    tag_of,
    tier_of,
    write_json,
)

DEFAULT_HEADER = "# {area} — {tag}\n\n"


# --------------------------------------------------------------------------- #
# reading the knowledge base
# --------------------------------------------------------------------------- #


def knowledge_inventory(config: dict[str, Any], problem: Any) -> list[dict[str, Any]]:
    """Every file in `main/`, with a short preview. The retrieval stage's input.

    Returns an empty list when the knowledge base does not exist yet -- the first run of
    a new project has nothing to reuse, and that has to be a normal state rather than an
    error.
    """
    root = main_dir(config)
    inventory: list[dict[str, Any]] = []
    if not root.is_dir():
        return inventory
    for area in MAIN_AREAS:
        folder = root / area
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            text = read_text(path, max_chars=8000)
            inventory.append(
                {
                    "area": area,
                    "path": f"main/{area}/{path.name}",
                    "bytes": path.stat().st_size,
                    # Which tier, and what the record says is in it: both are what a
                    # reader needs to decide whether to open it at all.
                    "tier": tier_of(path.name),
                    "toc": toc(path.read_text(encoding="utf-8", errors="replace")),
                    "headings": _headings(text),
                    "preview": _preview(text),
                }
            )
    index = root / "index.json"
    if index.is_file():
        inventory.append(
            {
                "area": "index",
                "path": "main/index.json",
                "bytes": index.stat().st_size,
                "headings": [],
                "preview": read_text(index, max_chars=2000),
            }
        )
    return inventory


def knowledge_files(config: dict[str, Any]) -> list[Path]:
    """Every markdown file in `main/` (what retrieval reads in full)."""
    root = main_dir(config)
    if not root.is_dir():
        return []
    return sorted(path for area in MAIN_AREAS for path in (root / area).glob("*.md"))


#: `## Contents`, plus the Chinese spelling, plus an inline description on the heading.
CONTENTS_RE = re.compile(r"^##\s+(?:contents|内容索引)\b[^\n]*$", re.IGNORECASE | re.MULTILINE)


def _contents_block(text: str) -> str | None:
    """The lines between the `## Contents` heading and the next `## ` heading."""
    match = CONTENTS_RE.search(text)
    if match is None:
        return None
    rest = text[match.end() :]
    stop = re.search(r"^##\s+", rest, re.MULTILINE)
    return rest if stop is None else rest[: stop.start()]


def toc(text: str) -> list[str]:
    """The record's `## Contents` entries (`- ...` lines only).

    The convention exists so a reader -- a retrieval agent, an analysis turn, a human --
    can decide in one glance which sections are worth loading, rather than guessing from
    headings and pulling in prose it does not need. Every record is expected to open with
    it; `toc_missing` is what the update gate reports on.
    """
    body = _contents_block(text)
    if body is None:
        return []
    return [line.strip() for line in body.splitlines() if line.strip().startswith("-")]


def toc_missing(text: str) -> bool:
    """Does this record fail to open with a usable table of contents?

    A heading with no entries counts as missing: it satisfies a reader looking for the
    section and then tells them nothing.
    """
    return not toc(text)


def toc_of_file(path: Path) -> list[str]:
    return toc(read_text(path, max_chars=200_000))


def key_lines(path: Path, limit: int = 400) -> list[str]:
    """The headings + first line of each section: enough for a retrieval index entry."""
    lines = [line.rstrip() for line in read_text(path, max_chars=200_000).splitlines()]
    out: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith("#"):
            out.append(line)
            if index + 1 < len(lines) and lines[index + 1].strip():
                out.append("   " + lines[index + 1].strip()[:120])
        if len(out) >= limit:
            break
    return out


def _headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith("#")][:30]


def _preview(text: str, limit: int = 400) -> str:
    body = " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))
    return body[:limit]


# --------------------------------------------------------------------------- #
# writing the per-run records
# --------------------------------------------------------------------------- #


def write_run_record(
    config: dict[str, Any],
    run_id: str,
    kind: str,
    payload: dict[str, Any],
) -> Path:
    """Write one `temp/<run_id>/<kind>.json`."""
    from cvrp.records.layout import record_file, temp_dir

    filename, body = record_file(kind, run_id, payload)
    return write_json(temp_dir(config, run_id) / filename, body)


def read_run_records(config: dict[str, Any], run_id: str) -> dict[str, dict[str, Any]]:
    """Every record of one run, keyed by kind (missing kinds simply absent)."""
    from cvrp.records.layout import RECORD_KINDS, temp_dir

    folder = temp_dir(config, run_id)
    found: dict[str, dict[str, Any]] = {}
    if not folder.is_dir():
        return found
    for kind, stem in RECORD_KINDS.items():
        path = folder / f"{stem}.json"
        if path.is_file():
            found[kind] = read_json(path)
    return found


# --------------------------------------------------------------------------- #
# the merge into main/
# --------------------------------------------------------------------------- #


def merge_into_main(
    config: dict[str, Any],
    problem: Any,
    source: Path,
    run_id: str,
    outcome: str = "",
) -> dict[str, Any]:
    """Merge a worktree's `main/` into the canonical knowledge base.

    `source` is the update agent's worktree records directory. Only the three areas are
    merged; `index.json` is rebuilt here so every entry points at a real run.
    """
    target = main_dir(config)
    tag = tag_of(problem)
    report: dict[str, Any] = {"merged": [], "created": [], "skipped": [], "tag": tag}

    for area in MAIN_AREAS:
        folder = Path(source) / area
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            destination = target / area / path.name
            if path.name == GLOBAL_SKILL:
                destination = target / "skills" / GLOBAL_SKILL
            incoming = path.read_text(encoding="utf-8", errors="replace")
            if not incoming.strip():
                report["skipped"].append(f"{area}/{path.name} (empty)")
                continue
            # A record opens with a `## Contents` block. Something that does not is not a
            # record -- the offline backend's stub, a scratch note, an agent's stray file --
            # and appending it would graft a second, header-less document onto the real one
            # (which is exactly what happened once: a mock run appended
            # "# Notes — cvrptw (offline stub)" to the curated notes).
            if toc_missing(incoming):
                report["skipped"].append(f"{area}/{path.name} (no `## Contents` block)")
                continue
            existed = destination.is_file()
            if existed:
                merged = append_unique(destination.read_text(encoding="utf-8"), incoming)
                destination.write_text(merged, encoding="utf-8")
                report["merged"].append(f"{area}/{path.name}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(incoming.strip() + "\n", encoding="utf-8")
                report["created"].append(f"{area}/{path.name}")

    update_index(config, problem, run_id, outcome)
    return report


def append_unique(existing: str, incoming: str) -> str:
    """Merge the sections of `incoming` into `existing`, keyed by their heading.

    A knowledge file is a list of sections, so the section is the unit that is either
    already known or new. Two rules make that safe:

    * **a repeated heading is not a new section.** Keying on the whole block let a second
      document append itself: a mock run wrote its own `_global_skill.md`, whose headings
      matched the curated file's, and the result held two `## Verification playbook`
      sections. A heading that already exists now absorbs the genuinely new lines from its
      namesake; a heading that does not exist is added as a new section.
    * **the document title is not a section.** Two records of the same kind differ in their
      title line ("# Skill — cvrptw" vs "# Skill — cvrptw (offline stub)"). The established
      title wins and only the body is merged, so a stub cannot rename a curated record.
    """
    sections: dict[str, str] = {}
    order: list[str] = []
    for block in _blocks(existing):
        key = _key_of(block)
        if key and key not in sections:
            order.append(key)
        sections[key] = block

    for block in _blocks(incoming):
        key = _key_of(block)
        if key not in sections:
            order.append(key)
            sections[key] = block.strip()
            continue
        sections[key] = _merge_section(sections[key], block)

    return "\n\n".join(sections[key].strip() for key in order).rstrip() + "\n"


#: Every document has exactly one title, so it gets one key no matter what it says. Keying
#: it by its text would let `# Skill — cvrptw (offline stub)` pass as a second document.
TITLE_KEY = "#"


def _key_of(block: str) -> str:
    heading = _heading_of(block)
    if heading.startswith("# ") and not heading.startswith("## "):
        return TITLE_KEY
    return heading


def _merge_section(existing: str, incoming: str) -> str:
    """`existing`, plus the lines of `incoming` it does not already have.

    The heading line is the key that got us here, so it is never merged as content — not
    for a section (whose `## Name` would otherwise be duplicated inside itself) and not for
    the document title (which would let a stub rename a curated record).
    """
    head, *body_lines = existing.splitlines()
    have = {_normalise(line) for line in body_lines if line.strip()}
    extra = [
        line
        for line in incoming.splitlines()[1:]
        if line.strip() and _normalise(line) not in have
    ]
    if not extra:
        return existing
    return "\n".join([head, *body_lines, *extra])


def _heading_of(block: str) -> str:
    first = block.strip().splitlines()[0] if block.strip() else ""
    return first if first.startswith("#") else ""


def _blocks(text: str) -> list[str]:
    """Split a record into its document title and one block per section.

    A block starts at a heading. `##` and deeper always start one; a `# ` line starts one
    only when a block is already open, so the document title stands alone instead of
    swallowing the first section (which is what made the `## Contents` heading of an
    appended document land inside its title).
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        starts_block = line.startswith("##") or (line.startswith("# ") and current)
        if starts_block and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [block for block in blocks if block.strip()]


def _normalise(block: str) -> str:
    return re.sub(r"\s+", " ", block).strip().lower()


# --------------------------------------------------------------------------- #
# index + retention
# --------------------------------------------------------------------------- #


def update_index(
    config: dict[str, Any],
    problem: Any,
    run_id: str,
    outcome: str = "",
) -> Path:
    """Record which run contributed which file, so retrieval can order by recency.

    Written *inside* `main/` rather than at the records root: it is part of the knowledge
    base (it lists the files that make it up), so copying or sharing `main/` carries the
    provenance with it. The dashboard reads it from there to mark which files the run it
    is displaying actually contributed.
    """
    from cvrp.records.layout import now_iso

    target = main_dir(config) / "index.json"
    index = read_json(target) or {"problem": tag_of(problem), "runs": []}
    runs = [entry for entry in (index.get("runs") or []) if entry.get("run_id") != run_id]
    runs.append({"run_id": run_id, "outcome": outcome, "at": now_iso(), "files": describe(config)})
    index["problem"] = tag_of(problem)
    index["runs"] = runs[-max(keep_attempts(config) * 2, 40) :]
    index["updated_at"] = now_iso()
    return write_json(target, index)


def describe(config: dict[str, Any]) -> list[str]:
    root = main_dir(config)
    if not root.is_dir():
        return []
    return [
        f"{area}/{path.name}"
        for area in MAIN_AREAS
        if (root / area).is_dir()
        for path in sorted((root / area).glob("*.md"))
    ]


def prune_attempts(config: dict[str, Any], problem: Any) -> int:
    """Keep only the newest `keep_attempts` run blocks in the attempts file."""
    path = main_dir(config) / "attempts" / main_paths(problem)["attempts"]
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = _blocks(text)
    preamble = [block for block in blocks if not _is_run_block(block)]
    runs = [block for block in blocks if _is_run_block(block)]
    limit = keep_attempts(config)
    if len(runs) <= limit:
        return 0
    kept = preamble + runs[-limit:]
    path.write_text("\n\n".join(block.strip() for block in kept).rstrip() + "\n", encoding="utf-8")
    return len(runs) - limit


def _is_run_block(block: str) -> bool:
    first = block.strip().splitlines()[0] if block.strip() else ""
    return bool(re.match(r"^#{2,3}\s+\d{8}-\d{6}", first))


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))

"""Tests for the cross-run records layer.

Four properties carry the feature:

* **the layout is three separate areas** -- per-run evidence, per-run selection, and the
  durable knowledge base -- because a run must not be able to overwrite another run's
  context, and the knowledge base must outlive every run;
* **the merge is append + dedupe**, never overwrite: knowledge earned over three rounds
  must not be erasable by one bad run;
* **an empty knowledge base is normal**, not an error, but it must be *stated*. The first
  run of a new problem has nothing to reuse and the pipeline must still complete;
* **retrieval hands analysis its selection, and the gate checks the selection was used.**
  Knowledge that never reaches the plan is not knowledge.
"""

from __future__ import annotations

import json
from pathlib import Path

from cvrp.problems import load_problem
from cvrp.records import (
    RECORD_KINDS,
    append_unique,
    attempts_name,
    knowledge_files,
    knowledge_inventory,
    main_dir,
    merge_into_main,
    notes_name,
    prune_attempts,
    rank,
    read_run_records,
    records_root,
    related_dir,
    skill_name,
    temp_dir,
    tokens,
    write_run_record,
)
from cvrp.records.store import key_lines
from tests.conftest import PACKAGE_PROBLEMS

PROBLEM = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")


def _config(root: Path, **records_settings) -> dict:
    settings = {"dir": str(root / "records")}
    settings.update(records_settings)
    return {"_project_root": str(root), "_task_dir": str(root), "records": settings}


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #


def test_the_three_areas_are_separate(scratch: Path):
    config = _config(scratch)
    assert records_root(config) == scratch / "records"
    assert main_dir(config) == scratch / "records" / "main"
    assert temp_dir(config, "r1") == scratch / "records" / "temp" / "r1"
    assert related_dir(config, "r1") == scratch / "records" / "related" / "r1"
    # two runs never share a related directory
    assert related_dir(config, "r1") != related_dir(config, "r2")


def test_the_records_dir_is_absolute_or_project_relative(scratch: Path):
    """A task may point several projects at one knowledge base."""
    shared = scratch / "shared-records"
    assert records_root({"_project_root": str(scratch), "records": {"dir": str(shared)}}) == shared


def test_the_records_dir_is_not_resolved_into_the_package(scratch: Path):
    """`cvrp/records` is a Python package; the knowledge base must not land inside it.

    Resolving `./records` through the general path helper would find the package
    directory first (it is on the same search order), put the knowledge base in the source
    tree, and ship it with the package.
    """
    config = _config(scratch)
    resolved = records_root(config)
    assert resolved == scratch / "records"
    assert "src" not in resolved.parts


def test_file_names_follow_the_problem_tag(scratch: Path):
    assert skill_name("cvrptw") == "cvrptw_skill.md"
    assert notes_name("cvrptw") == "cvrptw_notes.md"
    assert attempts_name("cvrptw") == "cvrptw_attempts.md"
    # the tag is normalised, so a problem tag with punctuation still names a real file
    assert skill_name("CVRPTW") == "cvrptw_skill.md"
    assert skill_name("cvrp/tw") == "cvrp-tw_skill.md"


def test_the_five_record_kinds_are_declared():
    assert set(RECORD_KINDS) == {
        "problem_description",
        "description_parsing",
        "new_problem_code",
        "code_error",
        "code_revise",
    }


# --------------------------------------------------------------------------- #
# per-run records
# --------------------------------------------------------------------------- #


def test_a_run_record_round_trips(scratch: Path):
    config = _config(scratch)
    write_run_record(config, "r1", "problem_description", {"description": "solve CVRPTW"})
    found = read_run_records(config, "r1")
    assert set(found) == {"problem_description"}
    assert found["problem_description"]["description"] == "solve CVRPTW"
    assert found["problem_description"]["run_id"] == "r1"
    assert found["problem_description"]["written_at"]


def test_missing_run_records_are_absent_not_an_error(scratch: Path):
    assert read_run_records(_config(scratch), "never-ran") == {}


# --------------------------------------------------------------------------- #
# the merge
# --------------------------------------------------------------------------- #


def test_merge_creates_then_appends(scratch: Path):
    config = _config(scratch)
    header = "# Skill\n\n## Contents\n\n- `Proven fixes` — before a mask *(problem-specific)*\n\n"
    first = scratch / "run1" / "records" / "main"
    (first / "skills").mkdir(parents=True)
    (first / "skills" / "cvrptw_skill.md").write_text(
        header + "## Proven fixes\n\n- gate X needed Y\n", encoding="utf-8"
    )
    report = merge_into_main(config, PROBLEM, scratch / "run1" / "records" / "main", "run1")
    assert report["created"] == ["skills/cvrptw_skill.md"]

    second = scratch / "run2" / "records" / "main"
    (second / "skills").mkdir(parents=True)
    (second / "skills" / "cvrptw_skill.md").write_text(
        header + "## Proven fixes\n\n- gate X needed Y\n\n## New trap\n\n- Z is not shipped\n",
        encoding="utf-8",
    )
    report = merge_into_main(config, PROBLEM, second, "run2")
    assert report["merged"] == ["skills/cvrptw_skill.md"]

    text = (main_dir(config) / "skills" / "cvrptw_skill.md").read_text(encoding="utf-8")
    assert text.count("gate X needed Y") == 1, "the duplicate section must not be appended twice"
    assert "Z is not shipped" in text, "the new section must be appended"


def test_append_unique_compares_content_not_whitespace():
    existing = "# A\n\nalpha beta\n"
    incoming = "# A\n\n  alpha   beta  \n\n# B\n\ngamma\n"
    merged = append_unique(existing, incoming)
    assert merged.count("alpha") == 1
    assert "gamma" in merged


def test_a_repeated_heading_never_produces_a_second_section():
    """The failure that made this rule necessary: a second document appended whole.

    A mock run wrote its own `_global_skill.md`, whose headings matched the curated file's.
    Keying sections on their whole text treated the overlap as new material, and the result
    had two `## Verification playbook` sections and two document headers.
    """
    existing = (
        "# Global skill\n\n## Contents\n\n- `Playbook` — the gates *(general)*\n\n"
        "## Playbook\n\n- five gates, in order\n"
    )
    incoming = (
        "# Global skill (offline stub)\n\n## Contents\n\n- `Playbook` — the gates *(general)*\n\n"
        "## Playbook\n\n- five gates, in order\n- reproduce one with `cvrp stage`\n"
    )
    merged = append_unique(existing, incoming)
    assert merged.count("## Playbook") == 1, merged
    assert "five gates, in order" in merged
    assert "reproduce one with `cvrp stage`" in merged, "the genuinely new line still lands"
    assert "offline stub" not in merged, "a repeated document is not a second document"


def test_merge_ignores_empty_files(scratch: Path):
    config = _config(scratch)
    source = scratch / "run" / "records" / "main"
    (source / "notes").mkdir(parents=True)
    (source / "notes" / "cvrptw_notes.md").write_text("   \n", encoding="utf-8")
    report = merge_into_main(config, PROBLEM, source, "run")
    assert report["skipped"] == ["notes/cvrptw_notes.md (empty)"]
    assert not (main_dir(config) / "notes" / "cvrptw_notes.md").exists()


def test_an_empty_worktree_main_is_not_an_error(scratch: Path):
    config = _config(scratch)
    report = merge_into_main(config, PROBLEM, scratch / "nothing" / "main", "run")
    assert report["created"] == [] and report["merged"] == []


def test_the_index_records_which_run_contributed(scratch: Path):
    config = _config(scratch)
    source = scratch / "run" / "records" / "main"
    (source / "notes").mkdir(parents=True)
    (source / "notes" / "cvrptw_notes.md").write_text("# Notes\n\n- a fact\n", encoding="utf-8")
    merge_into_main(config, PROBLEM, source, "run-a", outcome="SUCCESS")
    merge_into_main(config, PROBLEM, source, "run-b", outcome="FAILED")

    index = json.loads((main_dir(config) / "index.json").read_text(encoding="utf-8"))
    runs = {entry["run_id"]: entry for entry in index["runs"]}
    assert set(runs) == {"run-a", "run-b"}
    assert runs["run-a"]["outcome"] == "SUCCESS"
    assert runs["run-b"]["outcome"] == "FAILED"
    # re-running the same id replaces its entry rather than duplicating it
    merge_into_main(config, PROBLEM, source, "run-a", outcome="SUCCESS")
    again = json.loads((main_dir(config) / "index.json").read_text(encoding="utf-8"))
    assert len([r for r in again["runs"] if r["run_id"] == "run-a"]) == 1


def test_attempts_are_pruned_to_the_keep_limit(scratch: Path):
    config = _config(scratch, keep_attempts=3)
    target = main_dir(config) / "attempts" / "cvrptw_attempts.md"
    target.parent.mkdir(parents=True)
    blocks = "\n\n".join(
        f"## 2026010{i}-120000 · n=20 · offline\n\n- run {i}\n" for i in range(1, 7)
    )
    target.write_text("# Attempts\n\n" + blocks, encoding="utf-8")

    removed = prune_attempts(config, PROBLEM)
    assert removed == 3
    text = target.read_text(encoding="utf-8")
    assert text.count("## 2026") == 3
    assert "run 6" in text and "run 4" in text
    assert "run 1" not in text


# --------------------------------------------------------------------------- #
# two tiers, and a navigable record
# --------------------------------------------------------------------------- #


def test_general_records_are_recognised_by_name():
    from cvrp.records.layout import is_general, tier_of

    assert is_general("_global_skill.md")
    assert is_general("_global_attempts.md")
    assert not is_general("cvrptw_skill.md")
    assert tier_of("_global_skill.md") == "general"
    assert tier_of("cvrptw_notes.md") == "scoped"


def test_the_contents_block_is_extracted():
    from cvrp.records.store import toc, toc_missing

    text = (
        "# Notes — x\n\n## Contents\n\n"
        "- `Semantics` — how time advances *(problem-specific)*\n"
        "- `Traps` — read first *(problem-specific)*\n\n"
        "## Semantics\n\n- a rule\n"
    )
    entries = toc(text)
    assert len(entries) == 2
    assert "Semantics" in entries[0] and "problem-specific" in entries[0]
    assert not toc_missing(text)


def test_the_contents_extraction_survives_spellings_and_misses():
    from cvrp.records.store import toc, toc_missing

    # case, an inline description on the heading, and the Chinese spelling
    for heading in ("## Contents", "## CONTENTS", "## Contents of this record", "## 内容索引"):
        assert toc(f"# T\n\n{heading}\n\n- `A` — why\n"), heading
    # a heading with no entries tells a reader nothing, so it counts as missing
    assert toc_missing("# T\n\n## Contents\n\n## Real\n\n- x\n")
    assert toc_missing("# T\n\n## Identity\n\n- no contents block\n")
    assert toc_missing("")


def test_the_inventory_carries_tier_and_contents(scratch: Path):
    config = _config(scratch)
    root = main_dir(config)
    (root / "skills").mkdir(parents=True)
    (root / "skills" / "_global_skill.md").write_text(
        "# Global\n\n## Contents\n\n- `Hazards` — before adapting *(general)*\n", encoding="utf-8"
    )
    (root / "skills" / "cvrptw_skill.md").write_text(
        "# Scoped\n\n## Contents\n\n- `Fixes` — before a mask *(problem-specific)*\n", encoding="utf-8"
    )
    by_name = {item["path"]: item for item in knowledge_inventory(config, PROBLEM)}
    general = by_name["main/skills/_global_skill.md"]
    scoped = by_name["main/skills/cvrptw_skill.md"]
    assert general["tier"] == "general" and general["toc"]
    assert scoped["tier"] == "scoped" and scoped["toc"]


def test_the_first_llm_retrieval_does_not_require_a_why(scratch: Path):
    """`source` is what makes a selection checkable; the justification is good practice."""
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    (worktree / "artifacts" / "records").mkdir(parents=True)
    (worktree / "artifacts" / "records" / "retrieval.json").write_text(
        json.dumps(
            {
                "empty": False,
                "general": [{"source": "main/skills/_global_skill.md"}],
                "selected": [{"source": "main/notes/cvrptw_notes.md"}],
            }
        ),
        encoding="utf-8",
    )
    assert gates.gate_retrieval(scratch, worktree).passed


def test_the_merge_refuses_a_file_that_is_not_a_record(scratch: Path):
    """A stub without a contents block must not be grafted onto the real record.

    This is not hypothetical: the offline backend's stub was appended to the curated notes,
    producing a file with two `# Notes` headers — the curated one and
    `# Notes — cvrptw (offline stub)`. `append_unique` compares sections, and a second
    document's sections are all "new", so the guard has to be about the document itself.
    """
    config = _config(scratch)
    source = scratch / "run" / "records" / "main"
    (source / "notes").mkdir(parents=True)
    (source / "notes" / "cvrptw_notes.md").write_text(
        "# Notes — cvrptw (offline stub)\n\n## Traps\n\n- nothing recorded\n", encoding="utf-8"
    )
    report = merge_into_main(config, PROBLEM, source, "run")
    assert report["created"] == [] and report["merged"] == []
    assert report["skipped"] == ["notes/cvrptw_notes.md (no `## Contents` block)"]
    assert not (main_dir(config) / "notes" / "cvrptw_notes.md").exists()

    # the same file with a contents block is a record, and merges normally
    (source / "notes" / "cvrptw_notes.md").write_text(
        "# Notes — cvrptw\n\n## Contents\n\n- `Traps` — read first *(problem-specific)*\n\n"
        "## Traps\n\n- nothing recorded\n",
        encoding="utf-8",
    )
    report = merge_into_main(config, PROBLEM, source, "run")
    assert report["created"] == ["notes/cvrptw_notes.md"]


def test_the_update_gate_reports_a_missing_contents_block(scratch: Path):
    """A record nobody can navigate is reported, but never fails the run."""
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    folder = worktree / "records" / "main" / "notes"
    folder.mkdir(parents=True)
    (folder / "cvrptw_notes.md").write_text("# Notes\n\n## Traps\n\n- a fact\n", encoding="utf-8")

    result = gates.gate_update(scratch, worktree)
    assert result.passed, result.issues
    assert any("Contents" in warning for warning in result.warnings)

    (folder / "cvrptw_notes.md").write_text(
        "# Notes\n\n## Contents\n\n- `Traps` — read first *(problem-specific)*\n\n"
        "## Traps\n\n- a fact\n",
        encoding="utf-8",
    )
    assert gates.gate_update(scratch, worktree).warnings == []


# --------------------------------------------------------------------------- #
# the knowledge base
# --------------------------------------------------------------------------- #


def test_an_absent_knowledge_base_lists_nothing(scratch: Path):
    config = _config(scratch)
    assert knowledge_files(config) == []
    assert knowledge_inventory(config, PROBLEM) == []


def test_the_inventory_and_key_lines_describe_the_files(scratch: Path):
    config = _config(scratch)
    target = main_dir(config) / "notes" / "cvrptw_notes.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Notes\n\n## Traps\n\n- the style file is not shipped\n", encoding="utf-8")

    files = knowledge_files(config)
    assert [path.name for path in files] == ["cvrptw_notes.md"]

    inventory = knowledge_inventory(config, PROBLEM)
    assert inventory[0]["area"] == "notes"
    assert "## Traps" in inventory[0]["headings"]
    assert "style file" in inventory[0]["preview"]

    lines = key_lines(target)
    assert any("## Traps" in line for line in lines)


# --------------------------------------------------------------------------- #
# retrieval ranking
# --------------------------------------------------------------------------- #


def test_tokens_drop_stopwords_and_keep_domain_words():
    got = tokens("Please solve the problem of the CVRPTW with time windows and capacity")
    assert "cvrptw" in got and "capacity" in got and "windows" in got
    assert "the" not in got and "please" not in got


def test_ranking_prefers_lexical_overlap(scratch: Path):
    relevant = scratch / "cvrptw_notes.md"
    relevant.write_text("# Notes\n\n- time windows and capacity interact\n", encoding="utf-8")
    unrelated = scratch / "tsp_notes.md"
    unrelated.write_text("# Notes\n\n- a tour through cities\n", encoding="utf-8")

    ranked = rank([unrelated, relevant], "solve CVRPTW with time windows and capacity")
    assert ranked[0]["path"] == relevant
    assert "windows" in ranked[0]["matched"]
    assert ranked[1]["score"] < ranked[0]["score"]


def test_ranking_is_deterministic_and_tolerates_zero_overlap(scratch: Path):
    empty = scratch / "other.md"
    empty.write_text("# Other\n\n- nothing in common\n", encoding="utf-8")
    first = rank([empty], "completely unrelated words")
    second = rank([empty], "completely unrelated words")
    assert first == second
    # a zero score is still a candidate: the agent reads everything regardless
    assert first[0]["score"] == 0
    assert first[0]["matched"] == []


# --------------------------------------------------------------------------- #
# the knowledge reaching the code
# --------------------------------------------------------------------------- #


def test_the_retrieval_gate_rejects_a_silent_empty_selection(scratch: Path):
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    target = worktree / "artifacts" / "records" / "retrieval.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"empty": True}), encoding="utf-8")
    result = gates.gate_retrieval(scratch, worktree)
    assert not result.passed
    assert any("without a `note`" in issue for issue in result.issues)


def test_the_retrieval_gate_accepts_a_stated_empty_base(scratch: Path):
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    target = worktree / "artifacts" / "records" / "retrieval.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"empty": True, "note": "first run for this problem"}), encoding="utf-8"
    )
    assert gates.gate_retrieval(scratch, worktree).passed


def test_the_retrieval_gate_accepts_a_selection_without_a_why(scratch: Path):
    """`why` is good practice; only `source` is required to make a selection checkable."""
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    target = worktree / "artifacts" / "records" / "retrieval.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"empty": False, "selected": [{"source": "main/notes/x.md"}]}),
        encoding="utf-8",
    )
    assert gates.gate_retrieval(scratch, worktree).passed


def test_the_retrieval_gate_rejects_a_selection_without_a_source(scratch: Path):
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    target = worktree / "artifacts" / "records" / "retrieval.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"empty": False, "selected": [{"why": "vibes"}]}),
        encoding="utf-8",
    )
    result = gates.gate_retrieval(scratch, worktree)
    assert not result.passed
    assert any("no `source`" in issue for issue in result.issues)


def test_the_update_gate_requires_a_non_empty_knowledge_file(scratch: Path):
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    (worktree / "records" / "main" / "notes").mkdir(parents=True)
    (worktree / "records" / "main" / "notes" / "cvrptw_notes.md").write_text("", encoding="utf-8")
    result = gates.gate_update(scratch, worktree)
    assert not result.passed
    assert any("empty" in issue for issue in result.issues)

    (worktree / "records" / "main" / "notes" / "cvrptw_notes.md").write_text(
        "# Notes\n\n- a fact\n", encoding="utf-8"
    )
    assert gates.gate_update(scratch, worktree).passed


def test_a_justified_selection_still_passes(scratch: Path):
    """The `why` requirement is gone too: `source` is the only thing the gate asks for."""
    from cvrp.pipeline import gates

    worktree = scratch / "wt"
    target = worktree / "artifacts" / "records" / "retrieval.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"empty": False, "selected": [{"source": "main/notes/x.md"}]}),
        encoding="utf-8",
    )
    assert gates.gate_retrieval(scratch, worktree).passed


# --------------------------------------------------------------------------- #
# related_knowledge is reference material, never a contract
# --------------------------------------------------------------------------- #


def test_the_recorded_death_of_the_word_matching_gate():
    """The payload that killed a real 460-second analysis now passes.

    The agent wrote `run_plan: declare no dependencies and run no installs` where the plan
    said `Declare no dependencies and run no installs:`. A colon and a word order were
    enough for the old gate to reject a complete, schema-valid analysis and stop the run
    before code generation ever started. The fixture is that payload, trimmed to the keys
    the gate reads.
    """
    from cvrp.pipeline.gates import _advise_related_knowledge

    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "analysis_real_n100.json").read_text(encoding="utf-8")
    )
    warnings: list[str] = []
    _advise_related_knowledge(payload, warnings)
    assert warnings == [], warnings

    # and the shape that broke it is still in there, so the fixture keeps testing it
    actions = [str(item.get("action", "")) for item in payload["related_knowledge"]["used"]]
    assert any(action.startswith("run_plan:") for action in actions)
    assert payload["run_plan"], "the plan the actions were compared against"


def test_an_analysis_that_ignores_the_selection_passes():
    """Retrieved records are reference, so ignoring them is no longer detectable -- or fatal."""
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge(
        {
            "related_knowledge": {
                "used": [{"source": "main/notes/cvrptw_notes.md", "influence": "changed the order"}],
                "rejected": [],
            },
            "pomo_modification_plan": [{"file": "x.py", "change": "something unrelated"}],
        },
        warnings,
    )
    assert warnings == []


def test_a_missing_related_knowledge_block_is_fine():
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge({}, warnings)
    assert warnings == []


def test_an_empty_related_knowledge_block_is_fine():
    from cvrp.pipeline.gates import _advise_related_knowledge

    for block in ({}, {"used": []}, {"used": [], "rejected": [], "note": ""}):
        warnings: list[str] = []
        _advise_related_knowledge({"related_knowledge": block}, warnings)
        assert warnings == [], block


def test_a_bare_object_in_used_only_warns():
    """Even an entry with nothing in it is advice, not a failure."""
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge({"related_knowledge": {"used": [{}]}}, warnings)
    assert any("no `source`" in warning for warning in warnings)
    assert any("no `influence`" in warning for warning in warnings)


def test_the_legacy_action_field_is_accepted_as_influence():
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge(
        {"related_knowledge": {"used": [{"source": "main/notes/x.md", "action": "used to be action"}]}},
        warnings,
    )
    assert warnings == []


def test_a_rejection_without_a_reason_only_warns():
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge(
        {"related_knowledge": {"used": [], "note": "empty base", "rejected": [{"source": "x"}]}},
        warnings,
    )
    assert any("no `reason`" in warning for warning in warnings)


def test_a_source_listed_twice_is_reported():
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge(
        {
            "related_knowledge": {
                "used": [{"source": "main/notes/x.md", "influence": "a"}],
                "rejected": [{"source": "main/notes/x.md", "reason": "b"}],
            }
        },
        warnings,
    )
    assert any("both used and rejected" in warning for warning in warnings)


def test_a_related_knowledge_block_that_is_not_an_object_only_warns():
    from cvrp.pipeline.gates import _advise_related_knowledge

    warnings: list[str] = []
    _advise_related_knowledge({"related_knowledge": "I read three records"}, warnings)
    assert any("not an object" in warning for warning in warnings)


def test_an_oversized_analysis_only_warns():
    from cvrp.pipeline.gates import _warn_if_oversized

    warnings: list[str] = []
    _warn_if_oversized({"summary": "x" * 120_000}, warnings)
    assert any("consider trimming" in warning for warning in warnings)

    warnings = []
    _warn_if_oversized({"summary": "small"}, warnings)
    assert warnings == []


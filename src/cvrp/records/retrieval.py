"""Deterministic pre-selection for the retrieval stage.

The retrieval agent reads the whole knowledge base -- that is its job, and it is the only
stage that gets to. What it cannot do reliably is *enumerate*: a large `main/` would
either flood its context or tempt it into skimming. So this module does the mechanical
part: it scores every file against the problem description and hands the agent a ranked
candidate list with reasons.

Two properties matter:

* **it never decides relevance on its own.** Everything above the cut is listed as a
  candidate whether or not the agent ultimately uses it, and the agent may pull in a file
  this module ranked low (it read them all).
* **the scoring is explainable.** A candidate carries the tokens that matched, so the
  agent -- and a human reading `retrieval.json` later -- can see *why* it was surfaced.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cvrp.records.layout import read_text
from cvrp.records.store import key_lines

#: Words that appear in almost every task and therefore carry no signal.
STOPWORDS = frozenset(
    """
    a an the and or of to in for with without on at by from is are be as it its this that
    your you we our their his her they them then than so such not no nor if else when while
    problem problems description code run runs running new generate generation agent agents
    please must should need needs needed use using used make makes made also into over under
    each every all any some more most less least between within about above below
    """.split()
)

#: Domain words that are strong signals when the description mentions them.
DOMAIN_HINTS = (
    "cvrp",
    "vrptw",
    "vrp",
    "tw",
    "time",
    "window",
    "windows",
    "capacity",
    "demand",
    "depot",
    "vehicle",
    "vehicles",
    "route",
    "routing",
    "distance",
    "service",
    "horizon",
    "mask",
    "masking",
    "pomo",
    "checkpoint",
    "encoder",
    "decoder",
    "rollout",
    "augmentation",
    "feasible",
    "feasibility",
    "infeasible",
    "solution",
    "cost",
    "score",
    "numpy",
    "torch",
    "training",
    "epoch",
    "loss",
)

TOKEN_RE = re.compile(r"[A-Za-z0-9_+-]{3,}")


def tokens(text: str) -> list[str]:
    """Lowercased, de-duplicated, stopword-free tokens (order preserved)."""
    seen: dict[str, None] = {}
    for raw in TOKEN_RE.findall(str(text)):
        token = raw.lower()
        if token in STOPWORDS or len(token) < 3:
            continue
        seen.setdefault(token, None)
    return list(seen)


def score_file(path: Path, description_tokens: set[str]) -> dict[str, Any]:
    """Score one knowledge file against the description's vocabulary."""
    text = read_text(path, max_chars=200_000)
    body_tokens = set(tokens(text))
    headings = [line for line in text.splitlines() if line.startswith("#")]
    heading_tokens: set[str] = set()
    for heading in headings:
        heading_tokens.update(tokens(heading))

    overlap = sorted(description_tokens & body_tokens)
    heading_overlap = sorted(description_tokens & heading_tokens)
    domain_hits = sorted({token for token in description_tokens if token in DOMAIN_HINTS} & body_tokens)

    # Headings are cheap signal: a section literally named after a term the description
    # uses is more relevant than a passing mention deep in a paragraph.
    score = len(overlap) + 2 * len(heading_overlap) + len(domain_hits)
    return {
        "path": path,
        "score": score,
        "matched": overlap[:24],
        "matched_headings": heading_overlap[:12],
        "domain_hits": domain_hits[:12],
        "bytes": path.stat().st_size,
    }


def rank(files: list[Path], description: str) -> list[dict[str, Any]]:
    """Rank knowledge files for a description, best first (deterministic order)."""
    description_tokens = set(tokens(description))
    scored = [score_file(path, description_tokens) for path in files]
    return sorted(scored, key=lambda item: (-item["score"], str(item["path"])))


def candidates(config: dict[str, Any], description: str, limit: int | None = None) -> list[dict[str, Any]]:
    """`(ranked candidates, inventory summary)` for the retrieval prompt.

    The inventory is returned alongside so the agent can see files that scored zero --
    it read them anyway, and a zero score means "no lexical overlap", not "irrelevant".
    """
    from cvrp.records.store import knowledge_files

    files = knowledge_files(config)
    ranked = rank(files, description)
    if limit is None:
        limit = int(((config or {}).get("records") or {}).get("max_candidates") or 8)
    for item in ranked:
        item["path"] = str(item["path"])
        item["key_lines"] = key_lines(Path(item["path"]))
    return ranked[:limit]


def inventory(config: dict[str, Any]) -> list[dict[str, Any]]:
    """A file-per-line view of the whole knowledge base, for the agent's context."""
    from cvrp.records.store import knowledge_files

    out: list[dict[str, Any]] = []
    for path in knowledge_files(config):
        text = read_text(path, max_chars=2000)
        out.append(
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "headings": [line.strip() for line in text.splitlines() if line.startswith("#")][:20],
            }
        )
    return out


__all__ = [
    "DOMAIN_HINTS",
    "candidates",
    "inventory",
    "rank",
    "score_file",
    "tokens",
]

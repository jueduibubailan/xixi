# S7 — Update (update agent, last stage)

You are the **update stage**. The run is over; the verdict is known. Your job is to turn
what happened into knowledge the *next* project can use — and to do it without erasing
what earlier runs already earned.

Problem: **{problem_name}** (`{variant_label}`). Round {round_index}.

## What you read, in this order

1. **This run's records** — the primary source, all five:
   - `records/temp/{run_id}/problem_description.json` — what was asked
   - `.../description_parsing.json` — how the retrieval stage read it
   - `.../new_problem_code.json` — what the generation stage built
   - `.../code_error.json` — every gate result, plus the model's narration of the failure
   - `.../code_revise.json` — the root cause and what was changed
2. **The final evidence** — `REPORT.md`, `artifacts/runs/s3_run_report.json`,
   `artifacts/runs/grader_result.json`, `artifacts/runs/s5_feasibility.json`.
3. **The existing knowledge base** — `records/main/`, so you append rather than repeat.
   Read it; do not rewrite it.
4. **Not** the generated code and **not** the POMO baseline: you are the recorder, not a
   reviewer. The revise stage already read the code.

## What you write, into `records/main/`

Write only the three files for **this** problem (`{problem}` tag), at these exact paths:

```
records/main/skills/_global_skill.md        knowledge that holds for ANY problem
records/main/skills/{problem}_skill.md      procedure specific to this problem
records/main/notes/{problem}_notes.md       facts about this problem
records/main/attempts/{problem}_attempts.md this run, one block
```

### Deciding general versus problem-specific — the first thing you do

For **every** piece of knowledge you are about to write, ask:

> *If tomorrow the pipeline is given a different problem — a different variant, a different
> constraint family, a different baseline — does this still hold, word for word?*

- **Yes** (it is about the environment, the baseline's own hazards, the checkpoint
  discipline, the gate order, a debugging technique, a symptom → fix pair that any
  adaptation of this baseline would hit) → `skills/_global_skill.md`.
- **No, it needs a noun changed to hold** → it is problem-specific. Put it in
  `{problem}_skill.md` / `{problem}_notes.md`. Needing one noun changed is *exactly* the
  test; do not "generalize" it by writing the noun in the abstract and filing it globally.
- **Both**, because one paragraph mixes them → split it into two entries. A mixed section
  filed globally imports this problem's assumptions into every future run.

Rules for the general file:

- it must **never name this problem's tag**, its constraint names, or its measured numbers;
- write about "the baseline", "the constraint", "the dynamic state", not about this
  variant's instances;
- a general entry earns its place by being actionable elsewhere — "be careful" is not.

### Every record opens with `## Contents`

The first `##` section of every file you write is a table of contents, immediately under
the title:

```markdown
# Notes — {problem}

> Scope: **problem-specific** (or **general**). One line on who should read this.

## Contents

- `Semantics` — how time advances, and which units the model works in *(problem-specific)*
- `Traps` — read before touching a mask *(problem-specific)*

## Semantics
...
```

Four rules, all checked by the reviewer:

1. it must be the first `##` section, and its entries must be `-` bullets;
2. each line is `` - `Section name` — when a reader should load it (scope) `` — say *when to
   read it*, not what the heading already says;
3. if you add or remove a section, update this list in the same write. A stale contents
   list is worse than none;
4. a record with no contents list cannot be navigated, and the update gate reports it as a
   warning. Do not make a reader load the whole file to find out it was irrelevant.

### `{problem}_skill.md` — procedure (this problem)

Sections, each a list of actionable statements (not a narrative): which baseline this
problem adapts · the deliverable specifics and the parameter values the gates pin ·
pitfalls measured on this problem · troubleshooting index. Anything that is not tied to
this problem belongs in `_global_skill.md` instead.

### `{problem}_notes.md` — facts

Identity · objective & scoring · semantics (precise rules, e.g. how time advances) ·
**constraints, each paired with the mask that enforces it** · **feasibility checklist** in
the checker's own terms · instance schema · scale · acceptance contract · **Traps** (things
that looked fine and were not) · open questions.

These are the sections that let a later analysis skip re-deriving the problem. If you
learned a boundary comparison, a unit convention or a reachability invariant, it belongs
here — a note that only describes the problem is worth much less than one that states a
checkable rule.

### `{problem}_attempts.md` — this run, one block

Open the file with a contents list that explains the block layout and how to find one
(`grep -n '^## '`), not with a list of run ids: those go stale immediately.

```markdown
## {run_id} · n={n} · <SUCCESS|FAILED> · rounds=<r>
- task / scale / problem tag / run_dir (so it can be traced back)
- problem description: what was asked (short; the full text is in the run's records)
- parsing: problem_type, key concepts, novelty
- generation: files written, checkpoint keys skipped, anything deferred
- error: failed step, error class, and the narration of what actually went wrong
- revise: root cause, what changed, which review findings were left and why
- outcome: gates reached, grader score, feasible, best cost
- lessons: what the next run should do differently
- artifacts: paths to REPORT.md and the run report
```

## Rules

1. **Append, never overwrite.** The engine merges your files into the knowledge base with
   de-duplication, so write the *new* material. Re-stating an existing section wastes a
   merge and adds nothing.
2. **Only write what this run demonstrated.** A failed run produces an `attempts` block
   and, at most, anti-patterns — it does not produce a "proven fix".
3. **No run-specific numbers as general truths.** `n=100`, a particular cost, or a
   timestamped path belongs in `attempts`, not in `skills`/`notes`, unless the number is a
   constraint (for example the checkpoint's embedding width).
4. **Attribute every claim.** Say which run and which gate it came from, so a later reader
   can tell a measured fact from an inference.
5. **Never write outside `records/main/`.** Do not touch the deliverable, the run
   directory, or anything under `pomo/`.
6. **Non-empty files only.** An empty or placeholder file fails the gate.

## Output

End with the JSON summary on the last line:

```json
{"status": "ok", "summary": "one line", "files": ["records/main/..."],
 "new_sections": ["skills: Proven fixes entry for <symptom>"], "confidence": "high|low"}
```

# Records: how this pipeline remembers

A run produces evidence. Most of it is worth nothing after the report is written; a few
lines are worth the next project's time. This layer is what decides which is which, and
it is the only part of the pipeline whose output outlives a run.

---

## 1. Three areas, three lifetimes

```
records/                         `records.dir` (default `./records`, project-local)
├─ temp/<run_id>/                one file per agent, written during the run
│  ├─ problem_description.json   the engine, at run start
│  ├─ description_parsing.json   retrieval agent, step 1
│  ├─ new_problem_code.json      code generation agent
│  ├─ code_error.json            code error stage (facts + model narration)
│  └─ code_revise.json           code revise agent
├─ related/<run_id>/             what retrieval selected for this run
│  ├─ retrieval.json
│  └─ <copied excerpts>.md
└─ main/                         ★ the knowledge base -- survives every run
   ├─ skills/_global_skill.md    procedure that holds for ANY problem  (general)
   ├─ skills/<tag>_skill.md      procedure for this problem            (scoped)
   ├─ notes/<tag>_notes.md       facts about this problem              (scoped)
   ├─ attempts/<tag>_attempts.md one block per run                     (scoped)
   └─ index.json                 which run contributed what
```

Three areas because the lifetimes differ. `temp/` is disposable evidence. `related/` is
per-run, so two runs of the same problem cannot overwrite each other's context. `main/`
is the only thing retrieval reads, and the only thing worth committing.

`<tag>` is the problem's tag (`CVRPTW` → `cvrptw`), not the task slug: one problem serves
several scales (n=20/50/100) and the procedure transfers between them.

`records.dir` may be an **absolute path**, which is how several projects share one
knowledge base.

---

## 2. Who reads what

| stage | `main/` | `related/` | `temp/` | writes |
|---|---|---|---|---|
| `retrieval` (agent) | **reads all** | — | `problem_description` | `related/<run>/`, `description_parsing` |
| `analysis` (agent) | — | **reads all** (inlined into its instruction) | — | `analysis.json` + `related_knowledge`, `plan.md` |
| `code_generation` (agent) | — | — | — | `new_problem_code` |
| `code_error` (executor + narration) | — | — | — | `s3_run_report.json`, `s3_error_narrative.{json,md}`, `code_error` |
| `code_revise` (agent) | — | — | `code_error` | `code_revise` |
| `update` (agent) | **reads all** (to append, not repeat) | — | **reads all** | `main/…` |

**Only two stages read knowledge, and that is the design.** The generation, error and
revise stages are driven by what the code actually does when it runs, plus the analysis
contract. Historical prose in their prompts would make those prompts longer and invite
them to follow a previous run instead of this run's evidence.

The instruction block for the two reader stages (`# Related records`) is rendered by the
**engine**, not requested from a prompt template, so a template edit cannot make a stage
skip its reading.

---

## 3. How knowledge reaches the code

It travels through exactly one carrier: the analysis stage.

`retrieval` selects, the engine inlines the selection into the analysis instruction, and
`analysis` does its own job with that material in front of it. The records are
**reference, not a contract**:

```json
"related_knowledge": {
  "used":     [{"source": "main/notes/cvrptw_notes.md",
                "applies_because": "same problem tag",
                "influence": "made me put the plotting rule before the training loop"}],
  "rejected": [{"source": "main/attempts/cvrptw_attempts.md",
                "reason": "those runs were n=100; this task is n=20"}],
  "note": "anything else worth saying"
}
```

The whole block is **optional**, and nothing in it is compared against the plan. `used`
records what a record changed (`influence`, or `action` as a legacy alias) — free text,
because knowledge legitimately shapes wording, ordering and thresholds without becoming a
plan item. Missing `source`/`influence`, an unknown source, or a source listed as both
used and rejected are reported as **warnings** in the run report; none of them fail a run.

Why it is this loose: an earlier version required every `used[*].action` to be findable as
literal text in `run_plan` or `pomo_modification_plan`. A complete, schema-valid analysis
was rejected because the agent wrote

```
run_plan: declare no dependencies and run no installs
```

where the plan said

```
Declare no dependencies and run no installs: …
```

One colon. That turn took 460 seconds, was thrown away, and stopped the run before code
generation ever started. A gate may check that something was *said*, never that it was
said in the expected words — and "did the knowledge reach the plan?" is not even the right
question, since the plan is allowed to be unchanged.

The retrieval side keeps one requirement, and it is about silence rather than content: an
empty result must be *stated* (`{"empty": true, "note": …}`), because "I looked and there
was nothing" is information while a missing file is not. Finding nothing never blocks the
run.

---

## 3b. Two tiers, and a record you can navigate

The knowledge base mixes two kinds of knowledge, and the file name says which is which:

| tier | file | holds | who should read it |
|---|---|---|---|
| **general** | `_global_*.md` | what holds for *any* problem the pipeline is given: adapting a baseline, the host environment, baseline hazards, checkpoint discipline, the gate order, symptom → fix pairs | every run, whoever the problem |
| **scoped** | `<tag>_*.md` | what only this problem's runs know: its semantics, its constraints and masks, its traps, its measured numbers | runs of that problem |

The test for the general tier is deliberately blunt: *if the problem changed tomorrow,
would this still hold word for word?* Needing one noun changed means it is scoped — do not
"generalize" it by writing the noun in the abstract, because that imports this problem's
assumptions into every future run.

**Every record opens with a `## Contents` block**, listing its sections as `-` bullets with
a note on *when to read each one* and whether it is general or problem-specific:

```markdown
## Contents

- `Semantics` — how time advances and what the units are *(problem-specific)*
- `Traps` — read this before touching a mask *(problem-specific)*
```

That block is what makes the base usable rather than merely present: retrieval hands the
contents lists to the retrieval agent, and the dashboard shows them, so a reader can decide
what to load instead of pulling prose it does not need. The update gate reports a missing
contents block as a **warning** — the content may be excellent, and a run is never lost to a
heading.

## 4. What each knowledge file holds

### `skills/<tag>_skill.md` — procedure

Environment & deps · Baseline contract · Deliverable shape · Required conventions ·
Verification playbook · **Proven fixes** (`symptom → root cause → minimal change`) ·
Anti-patterns · Troubleshooting index. The problem-independent parts also go to
`skills/_global_skill.md`.

### `notes/<tag>_notes.md` — facts

Identity · objective & scoring · semantics · constraints & masking · instance schema ·
scale · acceptance contract · **Traps** · open questions.

### `attempts/<tag>_attempts.md` — evidence

One block per run, `## <run_id> · n=… · <outcome> · rounds=…`, giving what was asked, what
was parsed, what was generated, what failed (with the narration), what was revised, the
outcome, the lessons, and where the artifacts live. `keep_attempts` (default 20) bounds
the file.

---

## 5. How the update stage writes

The agent writes `records/main/` **inside its own worktree**, like every other agent, and
the orchestrator merges the result (`records.merge_into_main`). Merging in code is what
makes the write auditable and, more importantly, safe:

- **append + dedupe**, never overwrite. Sections are compared by normalised content, so
  restating a known fact changes nothing while new material is appended;
- an empty file is skipped rather than creating an empty knowledge file;
- `index.json` is rebuilt with the run id, outcome and timestamp, so retrieval can order
  by recency and a reader can trace any claim to the run it came from.

A knowledge base a single bad run could erase is worse than none: the notes that took
three rounds to earn would be gone and nobody would notice.

---

## 6. The first run of a new problem

There is nothing to retrieve. That is a normal state, **not** a failure — but it must be
stated:

```json
{"problem": "cvrptw", "empty": true, "note": "first run for this problem"}
```

The retrieval gate rejects a *silent* empty selection. "I looked and found nothing" is
information; a missing file is not.

---

## 7. Reusing a knowledge base in a new project

```yaml
# task.yaml in the new project
records:
  dir: "/path/to/an/earlier/project/records"   # absolute: share across projects
```

or copy `records/main/` into the new project's `records/`. Retrieval then starts from
whatever the earlier project learned. `records/temp/` and `records/related/` are per-run
and are git-ignored; `records/main/` is the part worth keeping.

---

## 8. Viewing it in the dashboard

`uv run cvrp ui` serves the records browser alongside the run view. The **Files** card has
three tabs:

| tab | source | endpoint |
|---|---|---|
| `Artifacts` | the run's `artifacts/` tree | `GET /api/artifacts`, `GET /api/file` |
| `Knowledge base` | the project's `records/main/` | `GET /api/records`, `GET /api/record-file` |
| `This run` | this run's `records/temp/<run_id>/` and `records/related/<run_id>/` | same, with `origin=run` |

Two details worth knowing:

- **`records/main/` is not inside the run**, so it needs its own endpoints. `/api/file` is
  confined to the run directory on purpose (`safe_path`) and cannot reach the knowledge
  base; `/api/record-file` is confined to `records/main/` (or, with `origin=run`, to that
  run's `temp` and `related` directories) and serves only `.md` and `.json`.
- **`[new]`** marks a file this run actually contributed. The update stage appends into
  shared per-problem files, so without that badge a reader cannot tell a file this run
  wrote from one an earlier run left behind. It comes from `main/index.json`, which records
  the file list each run produced.

The stage list itself also reports what the records layer did, so the pipeline view is
informative without opening anything:

```
retrieval   done   retrieval: selected 3 record(s) (attempts 1, notes 1, skills 1)
update      done   update: created 0, merged 3 (attempts, notes, skills)
```

## 8b. Running an offline smoke test without polluting the base

The offline backend's update stage writes plausible-looking records that **no run
verified**. Merged into a curated base they are noise at best and misleading at worst, so a
smoke run must point the knowledge base somewhere disposable:

```bash
uv run cvrp start task.yaml llm.backend=mock records.dir=./records-smoke
rm -rf records-smoke
```

Two guards also live in the merge itself, because a curated base has to survive a bad writer
rather than trust it:

- a file without a `## Contents` block is **not a record** and is skipped, not appended
  (a stub once grafted a second, header-less document onto the curated notes);
- a **repeated heading is not a new section** — its genuinely new lines merge into the
  existing section. Before this, a second document's headings appended themselves and a
  record ended up with two `## Verification playbook` sections and two document titles;
- the **document title is not a section**: it gets one key whatever it says, and its text is
  never merged as content, so a stub cannot rename a curated record.

## 9. Inspecting it from the command line

```bash
ls records/main/{skills,notes,attempts}/
cat records/main/index.json                          # which run wrote what
cat records/temp/<run_id>/code_error.json            # this run's failure evidence
cat records/related/<run_id>/retrieval.json          # what was selected, and why
grep -l "Related records" results/*/*/worktrees/*/CVPR.md   # only retrieval + analysis
```

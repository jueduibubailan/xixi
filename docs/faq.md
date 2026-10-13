# FAQ

### Why is the deliverable named `CVRPTWEnv.py` instead of `CVRPEnv.py`?

The deliverable is *named after the problem*, and the link to POMO is kept explicit
instead of by filename:

```
artifacts/generated/
├─ CVRPTWProblemDef.py   # baseline-correspondence: pomo/CVRProblemDef.py
├─ CVRPTWEnv.py          # baseline-correspondence: pomo/POMO/CVRPEnv.py
├─ CVRPTWModel.py        # baseline-correspondence: pomo/POMO/CVRPModel.py
├─ CVRPTWTrainer.py      # baseline-correspondence: pomo/POMO/CVRPTrainer.py
├─ CVRPTWTester.py       # baseline-correspondence: pomo/POMO/CVRPTester.py
├─ train_n{n}.py         # baseline-correspondence: pomo/POMO/train_n100.py
├─ test_n{n}.py          # baseline-correspondence: pomo/POMO/test_n100.py
├─ run.py                # (no baseline source)
└─ README.md             # (no baseline source)
```

Every file adapted from POMO starts with `# baseline-correspondence: <pomo path>`, and
`problem.yaml:generated.correspondence` is the authoritative table. That is what makes
"one-to-one with POMO" a *checkable* property rather than a claim.

The same rule forbids **extra generated code**: a `.py` file the table does not list is
a failure, `utils.py` is imported from the baseline in place rather than copied, and the
file set does not grow.

### What is the executor's `correspondence` gate?

A static check that runs before anything is executed (see `steps` in `task.yaml`). It can
fail a stage on its own, and it reports *every* finding -- failures and drift alike -- in
`artifacts/runs/s3_run_report.json` under `findings`, so the revise agent can fix all of
it in one round. It verifies:

| check | verdict |
|---|---|
| every declared deliverable exists | fail |
| every file adapted from POMO carries its `# baseline-correspondence:` line, pointing at the declared source | fail |
| a declared POMO source does not exist in the staged baseline | fail |
| a `.py` file outside the table (extra generated code) | fail |
| the training entry point's `model_load` is `enable: False` with `path`/`epoch` commented out | fail |
| `trainer_params` / `tester_params` keep POMO's values (below) | fail |
| a deliverable name does not start with the problem tag | warning |

The parameter values it pins, because they are conventions rather than choices:

```
epochs=8100, train_episodes=10*1000, train_batch_size=64,
model_save_interval=500, img_save_interval=500,
test_episodes=10*1000, test_batch_size=1000, aug_factor=8
```

Only `problem_size` / `pomo_size` follow the task's scale. A task that genuinely needs
different values declares them under `generated.expected_params`.

### Where does `test_n{n}.py` get its weights?

From the **`checkpoints/` drop box**, which ships empty. The workflow is:

```bash
# 1. train -- the trainer writes its own weights under result/
cd artifacts/generated && python train_n20.py

# 2. move what you want evaluated into the drop box
cp result/<timestamp>_train_.../checkpoint-<epoch>.pt ../checkpoints/

# 3. evaluate -- test_* picks the highest epoch it finds there
python test_n20.py
```

Resolution is two-level, at run time:

1. an explicit `--model-path <dir or .pt file>` (or `MODEL_PATH`);
2. the **highest epoch** among `checkpoint*.pt` in `checkpoints/`.

Two consequences worth knowing:

* **Nothing hardcodes a filename or an epoch.** That is the point of scanning: you decide
  what goes in the drop box, and shipping or renaming a checkpoint never requires editing
  generated code.
* **The pretrained CVRP100 weight is not part of this pipeline's output.** This project
  does not distribute `checkpoint-30500.pt` in the bundle; `train_n{n}.py` starts from
  scratch (`model_load.enable: False`), and evaluation reads the drop box. If you want a
  zero-shot comparison, put that file in `checkpoints/` yourself -- it will be picked by
  the same scan.

Implementation detail that is easy to get wrong: POMO's `CVRPTester` rebuilds the path as
`'{path}/checkpoint-{epoch}.pt'`, so a directory needs the epoch **parsed out of the file
name**, and a `.pt` file must come with its own directory as `path`. Pointing `path` at a
file while leaving `epoch` at a default produces a path that does not exist.

### What ends up in `workspace/final/<slug>/`?



More than the generated files -- and that is deliberate, so the published directory runs
without hunting for its dependencies:

```
workspace/final/<slug>/
├─ artifacts/generated/          the 9 generated files
│  ├─ CVRPTWProblemDef.py  CVRPTWEnv.py  CVRPTWModel.py
│  ├─ CVRPTWTrainer.py  CVRPTWTester.py
│  ├─ train_n{n}.py  test_n{n}.py  run.py  README.md
└─ pomo/                         bundled runtime payload
   ├─ CVRProblemDef.py
   └─ POMO/{CVRPEnv,CVRPModel,CVRPTrainer,CVRPTester}.py
      └─ utils/{utils.py, log_image_style/*.json}
```

Two reasons for this exact shape:

* **It must mirror the worktree.** The generated code imports POMO's toolchain as
  `pomo.…` and its own modules by bare name, which only resolves when the directory
  holding `pomo/` is the root the program runs from. A flat copy would be unrunnable --
  `ModuleNotFoundError` on the first import.
* **`pomo/` is not "extra generated code".** The correspondence gate still applies to
  `artifacts/generated/` only, which holds exactly the nine declared files. Bundling
  happens in the export step, driven by `resources.export` in `problem.yaml`, and the
  generation prompt forbids the agent from copying `pomo/` itself.

`checkpoints/` is deliberately **not** bundled (16 MB, only useful for zero-shot
evaluation); copy it in yourself if you want that. The export also appends a
`## Running this bundle` section to the generated `README.md` with the exact commands.

### Does the pipeline remember anything between runs?

Yes -- that is what `records/` is for, and it is the only output that outlives a run.
`records/temp/<run_id>/` holds one record per agent (the problem description, the parsed
description, what was generated, the error evidence plus a model narration of it, and what
was revised). `records/related/<run_id>/` holds what the retrieval stage selected for this
run. `records/main/` is the durable part: `skills/<tag>_skill.md`,
`notes/<tag>_notes.md`, `attempts/<tag>_attempts.md`, plus an index of which run
contributed what.

Exactly two stages read knowledge -- `retrieval` reads `records/main/` in full, and
`analysis` reads what retrieval selected, as **reference material**: retrieved records
shape wording, ordering and thresholds, and the analysis may also ignore them entirely.
Nothing about the selection is compared against its plan, and a missing or thin
`related_knowledge` block is reported as advice rather than failing the run. The generation,
error and revise stages deliberately read none of it: they work from the analysis contract
and from what the code actually does when it runs.

The base has two tiers: `_global_*.md` holds what applies to any problem, and `<tag>_*.md` what applies to one. Every record opens with a `## Contents` list so a reader can see what is inside before loading it. Set `records.dir` to an absolute path to share one knowledge base across projects. `uv run cvrp ui` has a **Files** card whose *Knowledge base* tab browses `records/main/` and marks this run's contributions with `[new]`; the *This run* tab shows the per-run `temp/` and `related/` buckets. See [docs/records.md](records.md).

### Where do the plotting style files live, and what do their bounds mean?

`pomo/POMO/utils/log_image_style/` -- shipped inside the problem payload **and** in every
export (all of `pomo/` travels with the bundle). `utils.py` reads them relative to its
**own** location:

```python
log_image_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name, file_name)
```

so `trainer_params['logging']['log_image_params_1']` must name a file that is actually
there -- and it must **frame this run's data**.

The shipped files are:

| file | `ylim` | role |
|---|---|---|
| `style_cvrp_20.json` | `min: 0`, `max: null` | the objective curve -- cannot clip it |
| `style_cvrp_100.json` | `min: 0`, `max: null` | identical to `style_cvrp_20.json`; safe to reference |
| `style_loss_1.json` | `null`, `null` | the loss curve -- fully autoscaled |
| `style_tsp_20.json` | `3.83`, `3.88` | TSP preset -- pinned, irrelevant to CVRP |

`style_cvrp_100.json` deserves a note, because it is the file POMO's own `train_n100.py`
names. Upstream ships it with `ylim` pinned to `10`..`30`, the window it chose for its
CVRP-100 benchmark; a copy of this project originally carried that pin (and briefly did
not carry the file at all). Both were wrong for the same reason: this pipeline trains
from scratch, so the objective starts far above 30 once the scale grows (this problem's
n=100 instances cost around 46, n=50 around 15.5), and a pinned window renders an
**empty box rather than an error**. It is now shipped as an exact copy of
`style_cvrp_20.json`, so referencing either name is safe.

The general rule: `null` means autoscale and is always safe; a number is a framing
decision that must contain the values this run actually produces. A pinned *upper* bound
is the dangerous kind, so the executor's correspondence gate warns about any
`ylim.max`/`xlim.max` that is not `null`. Renaming a style file, authoring a new one, or
zeroing `img_save_interval` to skip plotting are all defects.

### Who reviews the code for missing files and logic bugs?

The revise agent, as the *first* of its three turns:

1. **`s4_code_review.md`** -- read every file of the deliverable, compare it with the POMO
   source it declares, and write `artifacts/reviews/code_review.{md,json}` with findings
   (`missing_file`, `missing_symbol`, `logic_bug`, `path_defect`, `style_drift`,
   `contract_risk`). No code changes in this turn.
2. **`s4_error_analyse.md`** -- attribute the failure, combining the traceback with those
   findings.
3. **`s4_revise.md`** -- repair, fixing every high-severity finding rather than only the
   one that crashed.

The executor cannot do step 1: it has no model, and it can only run code and parse files.
That is exactly why the review pass exists as its own turn.

### `ModuleNotFoundError: No module named 'CVRProblemDef'` (or `'CVRPEnv'`)

The baseline's modules import each other by **bare name** and therefore need three
`sys.path` entries, which is what `run.py` must set up and what POMO's own
`test_n100.py` achieves with two relative inserts from inside `pomo/POMO/`:

| entry | needed for |
|---|---|
| the worktree root (the directory holding `pomo/`) | `from pomo.POMO.CVRPTester import ...` |
| `pomo/` | `CVRPEnv.py`'s `from CVRProblemDef import ...` |
| `pomo/POMO/` | the tester/trainer's `from CVRPEnv import ...` |

Forgetting the second or third is the usual cause. `run.py` must resolve these by
walking up from its own location, not with a hardcoded `"../.."`, because the
exported copy is not always two levels deep.

### How do I solve n=50 or n=100 instead of n=20?

Declare the size in the task file — it is the single place `n` is written:

```yaml
scale:
  problem_size: 50     # customers; an instance's DIMENSION is this + 1
```

Everything derived from it follows: the generated entry points become
`train_n50.py` / `test_n50.py`, instances are generated under
`instances/data/cvrptw/n50/`, the agents are told `problem_size = 50`, and the
executor selects an n=50 instance. Ready-made examples ship with the project:

```bash
uv run cvrp instances task_n50.yaml                 # synthesize n50 data
uv run cvrp start task_n50.yaml llm.backend=mock    # offline self-check
uv run cvrp start task_n50.yaml                     # real run (n=50)
uv run cvrp start task_n100.yaml                    # n=100
```

Things worth knowing:

- The instance `DIMENSION` is `problem_size + 1` (index 0 is the depot), so n=50
  consumes `DIMENSION : 51` files — the shape of `vrp/vrptw/50/1.vrp` — and n=100
  consumes `DIMENSION : 101`.
- The shipped checkpoint loads unchanged at every size (its encoder input is 2-D
  and its attention is over nodes). It was trained on CVRP100, so n=100 is where
  it contributes most and n=50 is a zero-shot transfer.
- Several sizes coexist in one data directory: `manifest.json` records all of them
  and a task only regenerates the size it needs.
- If the data on disk is the wrong size, the executor **fails** with
  `error_class: wrong_instance_size` instead of quietly scoring another problem.
- Budget: n=50 is roughly 2.5x and n=100 roughly 5x the per-iteration cost of n=20
  on CPU. The n50/n100 task files raise `grader.timeout` accordingly.

### `ModuleNotFoundError` for a POMO helper, or `from CVRP.POMO.utils.utils import ...` fails

Two things are going on, and both are fixed in the shipped spec.

**The import path.** Upstream POMO imports its helpers as
`from CVRP.POMO.utils.utils import ...`, because the upstream repository is named
`CVRP`. In a stage worktree the baseline is staged as `pomo/` at the worktree root
(with no `__init__.py`), so that prefix cannot resolve. The four baseline files
that used it (`CVRPTrainer.py`, `CVRPTester.py`, `train_n100.py`, `test_n100.py`)
now import `from pomo.POMO.utils.utils import ...`, which works once the **parent
of `pomo/`** is on `sys.path` (implicit namespace packages mean no `__init__.py` is
needed). Details are in the problem's `problem_context.md` and in
`BASELINE_NOTES.md` §1.0.

**The missing file.** `utils.py` was not listed under `generated.files` /
`generated.modules`, so the code-generation agent had no reason to carry it over —
even though the trainer, the tester and both entry points import it. It is declared
now, and the S2 prompt states the rule explicitly ("carry the baseline's helper
modules over; the package must be self-sufficient"), as does the S2 judge prompt.

**Its dependencies were undeclared too.** `pomo/POMO/utils/utils.py` imports
`pytz`, `numpy` and `matplotlib`; upstream POMO declares none of them. They are now
dependencies of this project, so the baseline is importable out of the box. `torch`
is deliberately not: the generated solver needs it, and installing it belongs in
`task.yaml:workspace.setup`.

**Why you now find out immediately.** The executor gained an `import_probe` gate
between `py_compile` and the smoke run: it imports each generated module in its own
subprocess, from the package directory. A missing helper or an unresolvable prefix
fails there with `error_class: missing_helper` and a message naming the module and
the missing name, instead of surfacing as a traceback inside `run.py`.

### `Failed to authenticate. API Error: 403 Request not allowed`

Two different things produce this, and they need different fixes.

**(a) The router was removed from the environment.** `deepseek-flash` is **not a
native Anthropic model name**. It only works when the Claude Code CLI sends its
traffic to a router (LiteLLM, an internal proxy, …) named by
`ANTHROPIC_BASE_URL`. Older versions of `cvrp` stripped that variable from the
agent environment as a "nested agent" precaution, so the CLI fell back to
`api.anthropic.com`, presented a router-era key there, and got a 403 for a model
Anthropic does not know.

Fixed: `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, the credential variables and the
proxy variables are now **inherited by default**; only genuine *session* markers
(`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, …) are removed. To run isolated anyway:

```yaml
llm:
  inherit_routing_env: false
  env: {ANTHROPIC_BASE_URL: "http://your-router:4000", ANTHROPIC_AUTH_TOKEN: "..."}
```

**(b) The router answered, but refused.** The 403 body is the one to read — it
comes from the upstream provider, not from the CLI. Common causes:

- the model name the router forwards (`litellm_params.model`) is not the
  provider's real model id. For DeepSeek the API serves `deepseek-chat` and
  `deepseek-reasoner`; `deepseek-flash` is a *display* name, so a
  `model_list` entry must map your alias onto a real id;
- the provider key is missing from the **router's** environment. With LiteLLM,
  `api_key: "os.environ/ANTHROPIC_AUTH_TOKEN"` is resolved inside the `litellm`
  process, so exporting the token in your shell after starting the proxy changes
  nothing;
- the gateway is not actually running. `cvrp` never starts a gateway — it only
  consumes one. If `ANTHROPIC_BASE_URL` points at a port nothing listens on, the
  CLI reports a connection failure, not a 403.

Answer all three with one command:

```bash
uv run cvrp doctor --probe
```

```
routing  : via http://127.0.0.1:4000 (key: ANTHROPIC_AUTH_TOKEN, ANTHROPIC_MODEL=deepseek-flash)
probe    : POST http://127.0.0.1:4000/v1/messages (model 'deepseek-flash')
           -> OK HTTP 200 - endpoint answered and accepted the model name
```

A `FAILED` line carries the HTTP status and the response body, so you can tell
"nothing is listening" from "the router answered 403 for that model name" without
touching the agent.

### My run failed at a stage and the report did not say why

Newly generated reports have a **`## Why it failed`** section with the full stage
error, plus a **`## Diagnostics`** block that lists every declared artifact
(present or `MISSING`), whether each stage's `CVPR.md` was rendered and what it
contains, and the tails of the pipeline and agent logs. If you are looking at a
report written before that existed:

```bash
uv run cvrp doctor                       # is the runtime even able to run?
uv run cvrp status                       # the stage table, with the failure message
uv run cvrp log -n 200                   # pipeline.log: the gate's own reason
uv run cvrp show logs/analysis.round0.log   # what the agent did or did not do
```

### `analysis gate failed`

Two causes, in order of likelihood:

1. **the agent never ran.** The `claude` CLI was not on the path of the process
   `uv run` spawns, so the turn returned `rc=127` and wrote nothing. Run
   `uv run cvrp doctor` — it checks exactly this and says what to fix.
2. **the agent ran but produced nothing usable.** It wrote no
   `artifacts/analysis/analysis.json`, wrote invalid JSON, or omitted a required
   field (`masking_rules`, `feasibility_checklist`, `pomo_modification_plan`, ...).
   The gate names the missing field, and the agent log shows its reasoning.

A third case used to exist and no longer does: a *previously* successful
`analysis.json` sitting in the reused worktree, which made a silent agent look
successful. See below.

### Why did a stage pass even though the agent did nothing?

That was a real bug, now fixed. Stage worktrees are **reused** across runs, so a
previously successful stage leaves its artifacts in place. The gate used to ask
only "is the artifact present and complete?", which a left-over file satisfies
trivially — so a crashed agent (or a missing CLI) was reported as a pass, and the
previous round's result was carried forward as the current one.

The gates now compare against a **content snapshot taken immediately before the
agent runs** (`cvrp.run.worktree.write_baseline`). A stage fails when *none* of its
declared outputs changed during the attempt; a partial update (a revision round
that patches one file) still counts as work. Timestamps are deliberately not used:
a worktree can be recreated, and seeding preserves mtimes through `copy2`, so an
mtime check reports a stale file as fresh. Regression coverage is in
`tests/test_freshness.py`.

### How do I re-run just the stage that failed?

```bash
uv run cvrp stage analysis                     # on the latest run
uv run cvrp stage analysis --run 20261006-191954
uv run cvrp stage code_error --task configs/tasks/cvrptw.yaml
```

A replay updates that stage's record in `state.json` and appends a `stage_replay`
event, but does **not** re-export or write a new REPORT.md — run
`uv run cvrp start` when you want a graded, exported result. The stage's worktree,
its rendered `CVPR.md` and its logs are all still there from the failed attempt,
which is usually what you want to inspect first.

### How do I solve a different VRP variant?

```bash
uv run cvrp new-problem <slug> --name "<Variant name>" --from "<one paragraph>"
```

That scaffolds a complete problem directory; you then edit three things
(`checks.py:verify`, `instances.py:generate_instance`, and the S1/S2 prompts) and
point `task.yaml:problem.slug` at it. `docs/new_problem.md` is the full
walkthrough. `cvrp problems` lists the variants this project can build.

### Where is `cvrptw` hardcoded?

It isn't, anywhere in the engine. It is only the first *example*:
`src/cvrp/problems/cvrptw/` (shipped with the package) and the `problem.slug` in
`task.yaml`. `tests/test_problems.py` scaffolds a second, unrelated problem and
runs it through the same machinery specifically to keep it that way.

### `claude` is not found

`cvrp` shells out to the Claude Code CLI. Install it in the VM and log in as the
model you want, or point the config at an absolute path:

```bash
uv run cvrp start task.yaml llm.claude_bin=/usr/local/bin/claude
```

`uv run cvrp validate` reports the model and backend it will use. To run the whole
pipeline without an agent session, use `llm.backend=mock`.

### Which model is used?

`task.yaml:llm.model`, currently `deepseek-flash`. It is passed straight to
`claude --model`, so whatever the CLI accepts works. One model is used for every
agent stage; per-stage models are not implemented (add a `model` key to a stage and
read it in `cvrp/pipeline/stages.py::run_agent_stage` if you need that).

### Is my API key stored anywhere?

No. The VM's Claude Code holds its own credentials, and `cvrp` never reads or
writes a key. `llm.env` exists only to pass extra environment variables to the
agent process. (`baseline/afl/` contains the upstream AFL files verbatim, and those
do carry a hardcoded key from the original authors — they are reference only and
are never imported. See `baseline/BASELINE_NOTES.md`.)

### Why does the run fail when the program exits 0?

Because exit 0 is not the contract. The program must print the lines under
`program.expected_lines` in the problem's spec, and (unless
`grader.args.require_solution_dump: false`) dump a solution that the problem's own
`verify()` accepts. "No crash, no verification" is the failure mode this pipeline
exists to catch.

### `feasibility: FAILED` but `violations` is empty

`verify()` returned an empty dict, or raised. The engine normalises that to
`feasible: False` with an explicit note, because it cannot verify a variant's
feasibility on its own and must never assume it. Implement
`problems/<slug>/checks.py:verify`.

### What if I don't write `checks.py` at all?

The run is still possible, but the verification degrades to "the dump is valid
JSON" and the report says so explicitly. `cvrp validate` warns about it. For
anything you intend to trust, implement `verify`.

### Can I use bigger instances?

Yes, but size them in the problem spec (data parameters belong to the problem):

```bash
uv run cvrp instances --force instances.data.sizes=[50]
```

Watch the horizon: `data.horizon: null` derives it from the canonical schedule,
which is what keeps larger instances feasible. A fixed horizon that is too small
raises instead of emitting an infeasible instance.

### The pipeline keeps looping. How do I stop it?

`loop.max_rounds` (default 5) is a hard bound:

```bash
uv run cvrp start task.yaml loop.max_rounds=2
```

Every round leaves evidence in `artifacts/revisions/`, and the run still writes a
REPORT.md when the budget runs out.

### A revision round made things worse

Read `artifacts/revisions/round-N.md` — the contract requires the revise agent to
record the root cause, the files it touched, and the smoke command it ran. If it
traded a crash for an infeasible solution, `s5_feasibility.json` names the violated
constraint.

### It says "CORAL not importable". Do I care?

No, it is informational. Enable the shell with `uv pip install -e ../CORAL1` (from
`new/`), after which `cvrp validate` reports the CORAL version and runtime status.
The adapter only uses CORAL's Claude Code runtime; the pipeline never needs CORAL's
manager, daemon or dashboard.

### Where is the code that finally solved the problem?

`workspace/final/<slug>/` (a copy of the generated directory from the successful
run), and the full history stays under `results/<slug>/<timestamp>/`.

### `uv run` fails on a locked-down Windows VM

Two environment issues show up on hardened Windows images, both unrelated to
`cvrp`:

* `Failed to initialize cache at ...\uv\cache` — set `UV_CACHE_DIR` to a writable
  directory (for example inside the project).
* pytest's `tmp_path` failing with `WinError 5` — the tests here do not use it;
  they use the `scratch` fixture, which writes under `<project>/.test-tmp`.

### The dashboard shows "no runs"

Run `uv run cvrp start …` first. The UI reads `results/`, and `cvrp clean` deletes
it (`cvrp clean --all`). Start the UI from the project root so that
`configs/default.yaml` resolves; otherwise pass the task file explicitly
(`uv run cvrp ui task.yaml`).

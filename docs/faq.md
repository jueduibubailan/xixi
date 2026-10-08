# FAQ

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

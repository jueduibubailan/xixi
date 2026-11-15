# 提示词改动方案（不删原文，只增加和修改）

> ⚠ 历史文档：本文记录的 s5 版本（参数 plan / `batch-<n>.json` / 两张策略表）已被取代。
> 当前 s5 要求 agent 自己设计神经网络方法，每个 plan 一个
> `artifacts/nn_improve/plans/<id>.md`；`{nn_strategies}` 与 `strategies_file` 已删除。
> 以 `src/vrpar/prompts/s5_nn_improve.md` 的实际内容为准。

> 更新：`s5` 的「参数修改策略」一节后来按
> [`nn_improve_prompt_additions.md`](nn_improve_prompt_additions.md) v5 换成了
> 两张表（s01–s10 / m01–m10），本文 §9 保留的是当时的初版全文；
> 以 `src/vrpar/prompts/s5_nn_improve.md` 的实际内容为准。

> 中文对照版：[`nn_improve_prompts_zh.md`](nn_improve_prompts_zh.md)
> （仅阅读用；项目里实际使用的提示词仍是英文）。

> 原则：
> 1. **不删除任何原有句子**；只做「改名」「追加段落」「改写个别句子」。
> 2. 两个文件是**改名**（文件名变，内容保留）：`code_revise_agent.md` → `code_adjustment_agent.md`，
>    `s4_revise.md` → `s4_adjustment.md`。
> 3. `s1_analysis.md` 与 `analysis_agent.md` **完全不动**（你的第 3 条）。
> 4. 下面每条都标注「原 → 新」，新文件给全文。

---

## 0. 新增的模板变量（`stages.py::stage_variables`）

| 变量 | 值 | 用在哪 |
|---|---|---|
| `{eval_epochs}` | 50 或 200 | s3、s4、code_error 角色 |
| `{eval_train_episodes}` | 5000 | s3、s4、code_error 角色 |
| `{eval_protocol}` | `search` / `acceptance` | s3 |
| `{report_dir}` | **输出目录绝对路径**，如 `<worktree>/artifacts/runs/plan-b1-s01` | s3 |
| `{evidence_dir}` | **只读证据目录绝对路径**，如 `<worktree>/data/artifacts/runs/plan-b1-s01` | s4 |
| `{adjustment_mode}` | `plan` / `repair` | s4、code_adjustment 角色 |
| `{plan_id}` | 如 `b1-s01` | s4 |
| `{plan_json}` | 该 plan 的 JSON（缩进 2） | s4 |
| `{base_assignments}` | 12 行“第 i 个槽位 → base”的清单 | s5 |
| `{nn_batch}` | 1/2/3 | s5 |
| `{strategies_per_batch}` / `{mutations_per_batch}` | 10 / 2 | s5 |
| `{nn_strategies}` | 策略清单文件内容（追加在表后），空时为空串 | s5 |
| `{round_index}` | repair 轮次 | s4 |

渲染注意：`_render` 只替换 `{小写标识符}`；JSON 示例的键都带引号，不会被替换。
若将来需要字面量花括号，写 `{{...}}`。

---

## 1. `roles/analysis_agent.md` — 不动

## 2. `roles/code_generation_agent.md` — 只改一行路径

**原（第 10 行）**

```
You modify POMO according to the variant described in
`artifacts/analysis/plan.md`. There is no human to ask in this stage.
```

**新**

```
You modify POMO according to the variant described in
`data/artifacts/analysis/plan.md`. There is no human to ask in this stage.
```

其余原样保留。

## 3. `roles/code_error_agent.md` — 改数字为变量，加一条 seed 约束

**原**

```
You generate `test_train.py`, run it for 200 epochs × 5000 episodes, then use the
resulting checkpoint to run the generated `test_n<n>.py`.

`test_train.py` must set `'epochs': 200`, `'train_episodes': 5000` and
`'train_batch_size': 64`. POMO's trainer saves `checkpoint-200.pt`.

Run the commands from `artifacts/generated/` and save stdout, stderr and exit code
under `artifacts/runs/`. Do not modify the other generated files; only
`test_train.py` is yours. Do not hand-write a score.
```

**新**

```
You generate `test_train.py`, run it for `{eval_epochs}` epochs × `{eval_train_episodes}`
episodes, then use the resulting checkpoint to run the generated `test_n<n>.py`.

`test_train.py` must set `'epochs': {eval_epochs}`, `'train_episodes': {eval_train_episodes}`
and `'train_batch_size': 64`. POMO's trainer saves `checkpoint-{eval_epochs}.pt`.

Override **only** those three keys on the imported training entry's parameter
dictionary; do not rebuild the dictionary, so the training entry's own seed and
optimizer settings stay in effect.

Run the commands from `artifacts/generated/` and save stdout, stderr and exit code
under `{report_dir}`. Do not modify the other generated files; only
`test_train.py` is yours. Do not hand-write a score.
```

## 4. `roles/code_adjustment_agent.md` — 由 `code_revise_agent.md` 改名

原文全部保留，标题与 `agent_id` 改，新增 `{adjustment_mode}` 与 plan 模式一句，`200×5000` 改变量。

**原**

```
---
agent_id: code_revise
generation: 0
stage: code_revise
---

# Role — revision agent

You repair a failing `test_train.py` / `test_n<n>.py` run.

Read the captured stdout/stderr/exit code and the generated code. Make the smallest
change that fixes the error. You may edit the generated modules, the training
entry, the test entry or `test_train.py`. Do not edit `plan.md`.

Keep the 200×5000 training pins, `test_episodes = 10*1000` and the
`AUGMENTATION SCORE:` output intact. After the fix, re-run `test_train.py` and
`test_n<n>.py`, and write `artifacts/revisions/round-<n>.md`.
```

**新**

```
---
agent_id: code_adjustment
generation: 0
stage: code_adjustment
---

# Role — adjustment agent

You are the repair/apply stage. `{adjustment_mode}` tells you which case this turn is:
`repair` (minimal fix of a failing run) or `plan` (apply one parameter plan from
the nn_improve stage).

You repair a failing `test_train.py` / `test_n<n>.py` run, or you apply the single
change a plan asks for.

Read the captured stdout/stderr/exit code and the generated code. Make the smallest
change that fixes the error, or the one change the plan names. You may edit the
generated modules, the training entry, the test entry or `test_train.py`.
Do not edit `plan.md`.

Keep the `{eval_epochs}`×`{eval_train_episodes}` training pins, `test_episodes` and the
`AUGMENTATION SCORE:` output intact. Do not run the training yourself; the next
`code_error` turn runs it. Write `artifacts/revisions/round-<n>.md` in `repair` mode
and `artifacts/revisions/plan-<id>.md` in `plan` mode.
```

> 说明：原文"After the fix, re-run `test_train.py` and `test_n<n>.py`"这句是**改写**成
> "Do not run the training yourself"，不是删除。原因：`code_error` 紧接着就会跑；
> adjustment 里再跑一次等于每次修复多花约 40 分钟。

## 5. `prompts/s1_analysis.md` — 不动

## 6. `prompts/s2_codegen.md` — 只追加一条（不删原文）

在 `## Rules` 末尾追加：

```markdown
- Model hyperparameters live in POMO's `model_params`. `train_n<n>.py` and
  `test_n<n>.py` must declare the **same** values, so that a later single-parameter
  change cannot make the checkpoint and the tester disagree.
```

其余原样保留。

## 7. `prompts/s3_code_error.md` — 全文（保留原句，改数字/路径）

```markdown
# S3 — Code error / quick train and test (agent)

Read `artifacts/analysis/plan.md` and the generated package under
`artifacts/generated/`.

Your job: generate **one** file, `artifacts/generated/test_train.py`, run it, then
run the final test entry that code_generation wrote with the checkpoint it
produced.

Find the final test entry under `artifacts/generated/`: the `test_*.py` file that
is **not** `test_train.py`. Its name comes from the plan (the convention is
`test_n<n>.py`, e.g. `test_n50.py`); do not assume the literal name `test_n`.

This turn's protocol is `{eval_protocol}`: `acceptance` uses
`{eval_epochs}` epochs × `{eval_train_episodes}` episodes. These two numbers come
from the pipeline and override any budget stated elsewhere (including `task.tips`).

## `test_train.py`

- Import the generated Trainer (for example
  `from <TAG>Trainer import <TrainerClass>`).
- Reuse the generated `train_n<n>.py` parameters, but override:

      'epochs': {eval_epochs}
      'train_episodes': {eval_train_episodes}
      'train_batch_size': 64

- Override only those keys; do not rebuild the parameter dictionary, so the
  training entry's seed and optimizer settings survive.
- Keep POMO's device chain; honour `VRPAR_FORCE_CPU`.
- Run the trainer. POMO saves `checkpoint-{eval_epochs}.pt` under its result folder.
- Print `QUICK TRAIN OK` at the end.

Do not modify the other generated files. If `test_train.py` itself has a bug, fix
`test_train.py` only.

## Run and capture

Run from `artifacts/generated/`, writing into the absolute report directory
`{report_dir}` (create it if missing):

    python test_train.py > {report_dir}/test_train.stdout.txt 2> {report_dir}/test_train.stderr.txt

Then write the exit code (bash / PowerShell both accepted):

    echo $? > {report_dir}/test_train.exitcode          # bash
    $LASTEXITCODE | Out-File -Encoding ascii {report_dir}/test_train.exitcode   # PowerShell

Read the file back and confirm it contains one integer.

Then locate `checkpoint-{eval_epochs}.pt` (in POMO's result folder) and run the actual
final test entry, for example:

    python test_n50.py --model-path <checkpoint-{eval_epochs}.pt> > {report_dir}/test.stdout.txt 2> {report_dir}/test.stderr.txt
    echo $? > {report_dir}/test.exitcode

Use the file you actually found; do not run a literal `test_n.py`.

Run the test entry with the test-data setup the generated code actually
implements; do not assume random test data, and do not assume a fixed set either:
follow `plan.md` (and `task.tips` when it specifies a test set). Its stdout ends with:

    AUGMENTATION SCORE: <float>

Do not hand-write scores. The pipeline reads the stdout files.
```

## 8. `prompts/s4_adjustment.md` — 由 `s4_revise.md` 改名（全文）

原文的 repair 段落全部保留，新增 plan 段落。

```markdown
# S4 — Adjustment (agent)

Mode for this turn: `{adjustment_mode}` (`repair` or `plan`). Read only the section
that matches.

## `repair` mode

You are entered when `test_train.py` or `test_n<n>.py` failed.

Read:

- `{evidence_dir}/test_train.stdout.txt` / `.stderr.txt` / `.exitcode`
- `{evidence_dir}/test.stdout.txt` / `.stderr.txt` / `.exitcode`
- the generated code under `artifacts/generated/`

Fix the error with the smallest change. You may modify the generated modules,
`train_n<n>.py`, `test_n<n>.py` or `test_train.py`. Do not edit
`artifacts/analysis/plan.md`; the plan is not yours to change.

Do not run `test_train.py` or `test_n<n>.py` in this stage: the next `code_error`
does it and produces the score. Write `artifacts/revisions/round-{round_index}.md`
with the root cause and the change. Do not weaken the `{eval_epochs}`×`{eval_train_episodes}`
pins, the `test_episodes` value or the score line.

## `plan` mode

Apply exactly one parameter plan to the current generated package.

- plan id: `{plan_id}`
- plan:

```json
{plan_json}
```

Rules:

1. Change only what `changes` says. Do not "improve" unrelated code, do not rename
   files, do not touch `artifacts/analysis/plan.md`.
2. A model hyperparameter usually appears in both `train_n<n>.py` and
   `test_n<n>.py` (`model_params`). Change both to the same value, or the checkpoint
   and the tester will disagree on `load_state_dict`.
3. Do not change `problem_size`, `pomo_size`, `aug_factor`, file names, the score
   line, the training budget, or anything `task.tips` fixes (seed, batch sizes,
   test set, ...).
4. Do not run training or testing; `code_error` does that next.
5. Write `artifacts/revisions/plan-{plan_id}.md`: file, symbol, old value, new
   value, one line of why.
```

## 9. `prompts/s5_nn_improve.md` — 新文件（全文）

```markdown
# S5 — NN improve (agent)

You propose the parameter plans for batch `{nn_batch}`. You do **not** change code
and you do **not** run training.

## Inputs

- `data/artifacts/analysis/plan.md`
- `data/artifacts/generated/` — the current champion package
- `data/artifacts/nn_improve/plans.json` — every plan so far, with score and label
- `data/artifacts/runs/` — the captured output of each execution

## Output

One file only: `artifacts/nn_improve/batch-{nn_batch}.json`

```json
{"batch": {nn_batch}, "plans": []}
```

Each plan object:

```json
{
  "id": "b{nn_batch}-s01",
  "kind": "strategy",
  "strategy_id": null,
  "title": "embedding 128→256",
  "target": "CVRPModel.py::Model.__init__",
  "changes": [{"param": "embedding_dim", "from": 128, "to": 256}],
  "rationale": "why this should lower the cost",
  "expected": "lower AUGMENTATION SCORE",
  "novelty": "",
  "source_plan_ids": []
}
```

## How many, and on what base

- `{strategies_per_batch}` `kind=strategy` plans and `{mutations_per_batch}`
  `kind=mutation` plans, in this order.
- Exactly **one** entry in `changes` per plan.
- The base of each slot is fixed by the pipeline:

{base_assignments}

  The i-th object in `plans` uses the i-th base. Do not invent another base.
- Batch 1: every plan changes one parameter of the initial code; no plan may
  depend on another plan of the same batch.
- Batches 2 and 3: each plan extends a base that already won. Prefer to keep
  pushing the parameters the winning plans already changed, in the same direction
  (e.g. 128 → 256 → 384). Record the winning plan(s) in `source_plan_ids`.
- A mutation is an idea that is **not** in the strategy catalogue below. Set
  `strategy_id` to null and explain in one sentence in `novelty` why it is outside
  the catalogue.

## Forbidden

- `problem_size`, `pomo_size`, `aug_factor`, file names, the score line format.
- The training budget (`epochs`, `train_episodes`).
- Anything `data/inputs/task.yaml`'s `task.tips` fixes (seed, batch sizes, test
  set, ...).
- Multi-parameter plans: one plan, one change.

## 参数修改策略（由项目维护者补全）

<!-- OWNER: 在此填写允许的参数修改策略清单，每条建议写成
     `- id: s01 | 目标: 文件::符号 | 说明: ...`。
     留空时：agent 自由探索，strategy 不校验 strategy_id；变异只需写 novelty。 -->

{nn_strategies}
```

## 10. `roles/nn_improve_agent.md` — 新文件（全文）

```markdown
---
agent_id: nn_improve
generation: 0
stage: nn_improve
---

# Role — nn improve agent

You are the search designer. You read the champion code and the history of
executed plans, and you propose the next batch of parameter plans. You never edit
the generated package and never run training: `code_adjustment` applies a plan and
`code_error` executes it.

Your only deliverable is `artifacts/nn_improve/batch-{nn_batch}.json`, one plan per
slot, each with exactly one parameter change, each on the base the pipeline
assigns. Every plan must be attributable: one change, one measured effect.
```

## 11. 改动汇总

| 文件 | 改名 | 追加 | 改写 | 删除 |
|---|---|---|---|---|
| `roles/analysis_agent.md` | — | — | — | — |
| `roles/code_generation_agent.md` | — | — | 1 行路径 | — |
| `roles/code_error_agent.md` | — | seed 约束 1 段 | 数字改变量、路径 | — |
| `roles/code_adjustment_agent.md` | 是 | 模式说明 | 标题、agent_id、pin 变量 | — |
| `prompts/s1_analysis.md` | — | — | — | — |
| `prompts/s2_codegen.md` | — | 1 条规则 | — | — |
| `prompts/s3_code_error.md` | — | 协议段、双 shell、回读确认 | 数字改变量、路径、checkpoint 名 | — |
| `prompts/s4_adjustment.md` | 是 | plan 模式全文 | 标题、进入条件、"不要重跑" | — |
| `prompts/s5_nn_improve.md` | 新 | 全文 | — | — |
| `roles/nn_improve_agent.md` | 新 | 全文 | — | — |

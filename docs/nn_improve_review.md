# 现状复核 + nn improve 改造方案

> ⚠ 历史文档：本文成文于改造前，里面的「参数 plan / `batch-<n>.json` / 策略清单」都已被
> 取代。当前实现：每个 plan 一个 `artifacts/nn_improve/plans/<id>.md`，方法由 agent
> 自己设计，`strategies_file` / `{nn_strategies}` 已删除。本文件仅存档。

> 本文分两部分：**A. 代码现在真正在做什么**（只描述事实，附文件与行号）；
> **B. 你的抽象流程与代码的差异、我不理解的地方、以及改造方案**。
>
> 结论先行：你的流程主干（analysis → codegen → 跑 → 报错就修 → 循环 → 出分）
> 与代码**一致**；但新增 nn improve 所依赖的 6 个前提在当前代码里**不存在**，
> 另有 5 处你的描述与代码/物理事实**冲突**，必须先拍板，否则实现出来一定是错的。

---

# A. 当前代码的真实工作流

## A.1 入口

```
uv run vrpar start problems/<slug>/task.yaml [key=value ...]
```

- `task.yaml` **只**放 `task: {name, slug, description, tips}`（`problems/*/task.yaml`）。
  环境、阶段表、循环次数都在同目录的 `litellm_config.yaml`，再叠 `configs/default.yaml`
  与 CLI 覆盖（`src/vrpar/config.py:243` `load_config`）。
- `stages: default` 会被展开成 `src/vrpar/pipeline/spec.py:16` 的 4 个阶段
  （`src/vrpar/bundle.py:131` `expand_defaults`）。
- 启动前跑 `workspace.setup`（`src/vrpar/run/setup.py`），例如装 torch；按指纹去重，
  失败只记日志不终止。
- 建 run 目录 `results/<slug>/<YYYYmmdd-HHMMSS>/`，含 `artifacts/{analysis,generated,revisions,runs}`、
  `logs/`、`worktrees/`、`state.json`（`src/vrpar/run/run_dir.py:27`）。

## A.2 四个阶段到底谁在干活

关键事实：**流水线自己从不执行 python 代码**（除了 `workspace.setup`）。
每个阶段只是「渲染一份 `VRPAR.md` → 调一次 LLM agent CLI → agent 在 worktree 里
自己写文件、自己跑命令 → 流水线回来只读文件」。证据：

- `src/vrpar/pipeline/stages.py:186` `run_agent_stage`：唯一动作是 `backend.run(prompt, cwd=worktree, ...)`。
- `src/vrpar/llm/base.py:45`：backend 只执行「一个 agent turn」。
- `src/vrpar/executor/contract.py`（`py_compile`/`run_program`/`import_probe`）
  已经退化，只被 `stages.py:18` 用来生成失败提示（`agent_failure_hint`）。

| 阶段 | 执行者 | 产出 | 流水线的校验 |
|---|---|---|---|
| analysis | agent | `artifacts/analysis/plan.md` | **只判断文件存在**（`orchestrator.py:155`），不解析内容 |
| code_generation | agent | `artifacts/generated/` 下 5 个模块 + `train_n<n>.py` + `test_n<n>.py` + `README.md` | 只看 agent 进程 `rc==0`（`orchestrator.py:175`）；目录空了只打日志 |
| code_error | agent | **自己写** `test_train.py`，自己跑训练+测试，把 stdout/stderr/exitcode 写到 `artifacts/runs/` | 正则 + 退出码 + 分数行（`gates.py:62`） |
| code_revise | agent | 改代码 + 写 `artifacts/revisions/round-<n>.md` | `outcome.ok or note.is_file()`（`orchestrator.py:240`） |

## A.3 每个阶段跑在哪个目录

- `worktrees/<stage>/` 是 agent 唯一可写区；`pomo/` 是共享 baseline 的拷贝。
- 前人产物以**只读**形式放进 `data/`：`data/artifacts/<...>`、`data/inputs/{task.yaml,grader,instance_gen}`
  （`src/vrpar/run/worktree.py:517`、`:481`）。
- 阶段开始前会先删掉 worktree 里的 `artifacts/generated`，再从 `data/artifacts/generated`
  物化一份（`orchestrator.py:131` `_materialise_deliverable`）——所以 agent 永远在
  「上一轮收敛后的代码」上改。
- 阶段结束 `collect_artifacts` 把声明的产物拷回 run 目录（`worktree.py:855`）。

## A.4 循环与判定

`Orchestrator.run()`（`src/vrpar/pipeline/orchestrator.py:253`）：

```
analysis → 无 plan.md 就整体失败
code_generation → rc!=0 就整体失败
for round in 1..loop.max_rounds:        # 默认 3，cvrptw bundle 覆盖成 5
    code_error(round)
    if passed: break
    if round == max: 失败
    code_revise(round, report)
成功 → export → REPORT.md
```

判定（`src/vrpar/pipeline/gates.py:62`）恰好四条，全机械：

1. `artifacts/generated/test_train.py` 里正则命中 `'epochs': 200` 与 `'train_episodes': 5000`；
2. `test_train.exitcode == 0`；
3. `test.exitcode == 0`；
4. `test.stdout.txt` 里有 `AUGMENTATION SCORE: <float>`，取**最后一处**。

分数 = 最后那条 `AUGMENTATION SCORE`；写进 `state.json.score` + `score_history`
（`src/vrpar/run/state.py:157`）。注意：没有「历史最好」，`score` 就是**最后一次**的分数。

## A.5 分数是什么

`baseline/pomo/POMO/CVRPTester.py:89,127`：

```python
self.logger.info(" AUGMENTATION SCORE: {:.4f} ".format(aug_score_AM.avg))
aug_score = -max_aug_pomo_reward.float().mean()   # 负号把 reward 变回正的代价
```

即 **aug score 是「代价」（路径长度），越小越好**；`configs/default.yaml` 也写着
`score: {direction: minimize}`，`mvmoe_ovrpl_n50/task.yaml:22` 原文是
"objective is to minimise the total open-route distance"。

且 `aug_score` 是「对 `aug_factor` 个对称变换 + `pomo_size` 个起点取 max」后的均值
（`CVRPTester.py:118-127`）——**调大 aug_factor 或 pomo_size，分数会机械变好，与网络无关**。

## A.6 一次真实运行长什么样

`results/mvmoe_ovrpl_n50/20261103-183526/`：

- analysis 776s，code_generation 566s，code_error 第一次失败（agent 写了 `epochs: 1`,
  `train_episodes: 64`，还漏了分数行），code_revise 332s，code_error 第二次通过，
  164s，score 12.2709。
- 该次 `test_train.py` 实际是 `epochs: 1 / train_episodes: 64`，**不是 200×5000**；
  训练 1 个 episode 用了约 0.01 分钟，测试 10000 episodes 约 1 分钟。

## A.7 WebUI

- `vrpar ui` 是**独立进程**（`cli.py:120`），只读 run 目录 + 一个写通道
  `POST /api/steer`（往 `state.json` 塞一条人工指令，下一阶段消费）。
- 分数面板（`web/static/app.js:83`）：`v-status`/`v-round`/`v-best`/`v-initial`/`v-score`/`v-feasible`。
  其中 **`v-initial` 永远是 `—`**（硬编码），`v-best` 只是把当前分数再显示一遍，
  `v-score` 只读**最后一次** `code_error_report.json`（`web/api.py:241`）。
  也就是说：现在**没有** initial score、没有 best score、没有历史曲线。

## A.8 当前代码里已经存在但没用的东西

| 机制 | 状态 |
|---|---|
| `worktree.py:745` `changed_since_snapshot` / `:805` `untouched_outputs` / `:923` `reset_generated` / `:915` `exists_artifact` | **定义了，从未被调用**（`write_baseline` 会写快照，但没人读） |
| `loop.on` / `loop.until`（`config.py:2033`） | 配置里存在，orchestrator **只用了 `max_rounds`** |
| `orchestrator.py:29` 的 `read_baseline` 导入 | 未使用 |
| `src/vrpar/executor/*` 的 5 道 gate | 已废弃 |

---

# B. 你的流程 vs 代码：不一致清单

图例：✅ 一致；⚠️ 需要澄清；❌ 冲突（照字面实现会错）。

| # | 你的描述 | 代码/事实 | 判定 |
|---|---|---|---|
| 1 | 「跑 epochs=200, train_episodes=5000 的 **train_test.py**」 | 文件名是 **`test_train.py`**；而且它**由 code_error agent 自己生成**，不是 code_generation 产出的 | ⚠️ 名称 + 归属 |
| 2 | 「能跑通 → 把 aug 得分输出到 WebUI」 | 流水线不推分；WebUI 读文件。且当前只显示「最后一次」分数 | ⚠️ |
| 3 | 「得分**高**则 label=1，低则 0」 | aug score 是**代价，越低越好** | ❌ 方向反了 |
| 4 | 「code revise 针对 error 修改，然后循环」 | 一致；但 `max_rounds` 由配置决定（默认 3，cvrptw 是 5） | ✅ |
| 5 | 「code generation 按 plan 生成代码」 | 一致（7 个文件，不是 5 个） | ✅ |
| 6 | 「不管什么样的问题都能接收」 | 整条流水线**只服务「改写 POMO 解 VRP 变体」**：导出必带 `pomo/`，分数行固定 `AUGMENTATION SCORE`，codegen 角色写明「baseline 是完整的神经求解器」 | ❌ 范围夸大 |
| 7 | 「用户输入一个新的问题描述」 | 入口是磁盘上的 `problems/<slug>/task.yaml` + bundle 目录；WebUI 没有新建任务的入口 | ⚠️ |
| 8 | nn improve 要维护「10+2 个 plan 的 已执行/未执行/正在执行/已完成 + label」 | 现在**没有任何 plan 账本**；流水线刻意不解析 agent 产物 | ❌ 缺基础设施 |
| 9 | 「每个 plan 执行后比分数」 | 现在每个 plan 没有独立执行记录，`code_error_report.json` 每次被覆盖 | ❌ 缺记录 |
| 10 | 「3 次循环 = 最多 36 个 plan」 | 12×3=36 自洽；但需要明确是否「10+2 全部执行」 | ⚠️ |
| 11 | 「变异 plan 不是提示词里说到的内容」 | 策略段**留空**时，「提示词里说到的内容」是空集，变异无从定义 | ❌ 逻辑依赖 |
| 12 | 「比刚开始未进入 nn improve 的第一次得分高」 | 默认测试数据是**每次重新随机生成**（`test_data_load.enable=False`，无 seed） | ❌ 分数不可比 |

## B.1 必须提出的 5 个硬冲突

### ❌ C1. 分数方向反了（最严重）

`CVRPTester.py:127` + `configs/default.yaml:23` + `task.yaml:22` 三处都证明：
**分数是代价，越小越好**。你的 2.2 写「比第一次的得分**高**则 label=1，**低**则 0」，
照字面实现会系统性地挑选**更差**的网络，36 个 plan 跑完只会越来越差。

必须改成方向感知：

```python
better = (new < ref) if direction == "minimize" else (new > ref)
label  = 1 if better else 0
```

并且把 `direction` 从 `config.score.direction` 读，而不是写死。

> 如果你的本意是「分数已经被取负了」或者「aug score 越大越好」，请直接说，
> 我按你的定义实现；但当前仓库里的证据全部指向 minimize。

### ❌ C2. 测试数据每次随机，36 个 plan 的分数不可比

`test_data_load.enable = False` 时，`CVRPEnv.load_problems()` 每次 `torch.rand` 重新采样，
**全仓库没有任何 seed**（`baseline/pomo/CVRProblemDef.py:6` 只有 `torch.rand`）。
于是 10000 条随机算例上，两个完全相同的网络也会差零点几；label 会被噪声翻转。

好消息：`problems/mvmoe_ovrpl_n50/grader/data/ovrpl50_uniform.pkl`（1000 条固定算例）
就在仓库里，而 task.yaml 第 54-59 行也把它称为 "authoritative evaluation set"，
只是 tips 又要求用随机数据——**task.yaml 自己前后矛盾**。

建议：nn improve 搜索阶段固定一组测试算例（固定 pkl 或固定 seed），
最终交付再跑一次随机 10000 的验收。需要你确认「允许引入 seed / 固定测试集」。

### ❌ C3. aug_factor / pomo_size 是「机械提分」的作弊维度

aug score = 对 `aug_factor` 个变换 + `pomo_size` 个起点取 max 后的均值。
把 `aug_factor` 从 8 调到 16、把 `pomo_size` 调到 n，分数必然变好，
但网络本身没有任何改进。若这两个参数进入搜索空间，搜索一定会收敛到「调大它们」。

建议：搜索空间**排除** `problem_size` / `pomo_size` / `aug_factor` / 文件名 / 分数行格式，
只允许动网络结构与训练超参。需要你确认。

### ❌ C4. 每个 plan 必须从同一个基线出发，否则 label 无意义

你描述的是「plan1 → 改代码 → 跑 → 打标 → plan2 → …」串行。如果 plan2 直接在
plan1 改坏的代码上继续改，36 个 plan 是**累积漂移**，不是 36 次独立实验，
label 无法归因。

当前代码没有回滚机制（`_materialise_deliverable` 只会从 `data/` 复制）。
必须加：`best/` 快照 + 每个 plan 执行前从 `best/` 重置 + 变差/报错就丢弃。

### ❌ C5. 36 次 200×5000 的训练，时间上不成立

用这次真实运行的数据估算：1 次 optimizer step ≈ 0.6s（`epochs:1, train_episodes:64`）。
200×5000 下 step 数 = 200 × 5000/64 ≈ **15,625**，约 **2.6 小时/次**（GPU）。
36 次 ≈ **94 小时纯训练**，再加 36 个 agent turn（每次 5–13 分钟）。
而 `code_error` 的 stage timeout 是 10800s（3 小时），单次就已贴着上限。

必须二选一（或组合）：
- (a) 搜索阶段用短预算（如 20 epochs × 500 episodes，约 1.5 分钟/次），
      最终对冠军做一次 200×5000 验收；
- (b) 接受 4 天以上的机时，并把 stage timeout 调大。

> 这决定 gates.py 里写死的 `200`/`5000` 正则要不要参数化。

## B.2 必须提出的 7 个设计缺口

### G1. 「已执行 / 未执行 / 正在执行 / 已完成」谁维护？

agent 是**无状态**的：每个阶段一次 turn，它不会记得上一轮打过什么标签。
而 2.4「把第一个未执行的 plan 改为正在执行」是一个**机械状态转移**，
必须由 orchestrator 做，不能交给 LLM。所以：

- **plan 的内容**（标题、改哪个参数、怎么改）→ nn_improve agent 写；
- **plan 的状态与 label**（pending/running/executed/completed/failed、1/0、score）→ 流水线写。

我建议再加一个 `failed` 状态：你的四态里没有「跑挂了」的位置，
而跑挂的 plan 既不是「已执行」也不该算「已完成」。

### G2. 「已修改」是另一套标签，放哪？

第 3 条要求 code_adjustment 修完 error 后打「已修改」。这和 plan 的四态不是一套
（一个是修复任务，一个是参数实验）。建议：

- `artifacts/revisions/index.json`：`{round, kind: "repair", status: "fixed", error_class, files, summary}`；
- UI 的 revision 面板显示「已修改」；
- **什么时候算 fixed**：agent 改完就 fixed，还是下一次 code_error 跑绿才 fixed？
  我倾向后者（否则「已修改」是自我声明）。需要你确认。

### G3. error 和 running plan 同时存在时，code_adjustment 先干哪个？

第 3 条说它「接收 error 信息和 nn improve 的 plan」，但没说优先级。
建议：**先修复再应用 plan**（跑不起来的代码没法评估 plan 的效果），
且修复与 plan 的改动写进同一个 worktree，revision 记录里注明。

### G4. label 的参照系：固定「进入 nn improve 前」还是「当前最好」？

- 字面（2.2）：固定为**第一次的得分**（`initial_score`），全流程不变；
- 但 4 又说「若比历史最好的分数要好，则更新 best score」（当前最好）。

两者可以并存，但**含义不同**：第 2 批的一个 plan 可能比第 1 批最好还好、却仍不如
initial（如果 initial 恰好运气好），label=0 但它其实值得保留。
建议：`label` 按字面用固定 initial；`best`/组合时用「比当前 best 好」；
两个字段都存（`label`, `delta_vs_best`）。需要你确认是否接受这种双轨。

### G5. 没有 label=1 的 plan 怎么办？全部都是 1 怎么办？

第 2 批是「把 label=1 的方式组合」。若第 1 批 12 个全是 0（很常见，因为噪声 + 小改动），
第 2 批就没有组合素材。需要兜底策略：
- 全 0 → 取分数最好的 k 个继续（探索），或直接跳到「变异」；
- 全 1 → 全部组合会互相冲突，需要选 top-k。

### G6. 「组合」的合并语义没定义

两个 label=1 的 plan 可能改同一个参数（embed_dim 128→256 与 128→192）。
合并规则？我建议：同参数族冲突时取分数更高的那个；跨族的直接叠加；
由 agent 生成合并 plan 时显式写出 `source_plan_ids`，流水线可校验不冲突。

### G7. 变异 plan 与空策略段的循环依赖

「变异 = 不在提示词策略里的想法」。策略段留空（第 5 条）时：
- 若严格校验，所有 plan 都「不在策略里」，全是变异；
- 若宽松校验，变异与普通 plan 无区别，2.2 的设计失效。

建议：给 plan 加 `kind`（`strategy` / `mutation`）与 `strategy_id`，
策略清单用**机器可读**格式（YAML 列表，带 id），这样：
- 留空时 → `validation: lenient`，变异靠 `novelty` 文字说明；
- 你补全后 → 流水线可以机械校验 `strategy_id` 是否存在、变异是否真的不在清单里。

## B.3 现存 bug（会直接影响新流程）

1. **`<run>` 占位符从未被替换**：`prompts/s3_code_error.md:35` 写
   `python test_train.py > <run>/artifacts/runs/...`，`stage_variables` 里没有 `run`，
   渲染出来就是字面量 `<run>`（已在 `results/.../worktrees/code_error/VRPAR.md:63` 复现）。
   shell 会把它当成重定向，命令直接报错，agent 只能自己改道。
   新流程里 exitcode 是状态机的输入，这种不确定性必须消掉。
2. **code_revise 读不到错误日志**：`prompts/s4_revise.md:7` 让它读
   `artifacts/runs/...`，但证据被 stage 到 `data/artifacts/runs/...`
   （`worktree.py:517`），worktree 的 `artifacts/runs/` 开始时是空的。
3. **s2 与角色文件路径不一致**：`prompts/s2_codegen.md:3` 说 `data/artifacts/analysis/plan.md`，
   `roles/code_generation_agent.md:10` 说 `artifacts/analysis/plan.md`，同一份指令里两个路径。
4. **执行契约自相矛盾**：`stages.py:35` 规定「所有写入必须在 worktree 内」，
   而 s3 直接让 agent 往 `<run>/artifacts/runs/` 写。
5. **阶段成功判定过松**：code_generation 只要 agent 退出 0 就算过，
   即使一个文件都没写（`orchestrator.py:175`）。
6. **旧 run 的 stage 名**：`state.json` 以 stage 名做 key，改名后旧 run 在 UI 里
   会显示 code_adjustment=pending、code_revise 消失。

---

# C. 改造方案

## C.1 目标流程

```
analysis ─► code_generation
              │
              ▼
      ┌─ code_error (acceptance: 200×5000) ──┐
      │        │ 失败                        │
      │        ▼                             │
      │   code_adjustment(mode=repair)  ──────┘   ← 打「已修改」
      │        │ 成功 → initial_score, 存 best 快照
      ▼        ▼
  ┌──────── nn_improve 3 批 ────────┐
  │  每批：10 个 strategy + 2 个 mutation │
  │  for plan in 这一批:                   │
  │     pending → running (2.4)           │
  │     从 best 快照重置代码               │
  │     code_adjustment(mode=plan)          │
  │     code_error(eval=search)            │
  │     有错 → code_adjustment(repair) → 重跑 │
  │     通过 → completed, label, Δbest      │
  │     不通过 → failed, 回滚               │
  └───────────────────────────────────────┘
              │
              ▼
      冠军 → code_error(acceptance) 复核 → 导出 best 代码
```

## C.2 阶段表（`pipeline/spec.py`）

```python
STAGES = [
    {"name": "analysis",       ...},   # 不动
    {"name": "code_generation",...},   # 不动
    {"name": "code_error",     ...},   # 不动（可多次调用）
    {"name": "code_adjustment", ...},   # 由 code_revise 改名，双模式
    {"name": "nn_improve",     ...},   # 新增：只产 plan，不改代码、不跑训练
]
```

同时把 `_inputs_for()`（按表顺序推输入）换成**显式输入表** `STAGE_INPUTS`，
因为 code_error 现在会在不同上下文被调用多次（baseline / 每个 plan / 最终复核），
「前一个阶段的 produces」已经不能表达它。

新角色/提示词文件：

```
src/vrpar/roles/nn_improve_agent.md
src/vrpar/prompts/s5_nn_improve.md          # 含「参数修改策略（留空）」段
src/vrpar/roles/code_adjustment_agent.md     # 由 code_revise_agent.md 改名+扩写
src/vrpar/prompts/s4_adjustment.md           # 由 s4_revise.md 改名+双模式
```

`PROMPT_KEY_ALIASES` 加：`"nn_improve": "s5_nn_improve"`, `"code_adjustment": "s4_adjustment"`，
并保留 `"code_revise"` → `"s4_adjustment"` 兼容。

## C.3 账本：`artifacts/nn_improve/plans.json`

```jsonc
{
  "version": 1,
  "direction": "minimize",
  "initial_score": 12.2709,
  "best_score": 11.9821,
  "batches": [
    {
      "batch": 1,
      "protocol": "search",
      "entry_best": 12.2709,
      "plans": [
        {
          "id": "b1-s01",
          "kind": "strategy",              // strategy | mutation
          "strategy_id": "s01",            // 策略段留空时为 null
          "title": "encoder embedding 128→256",
          "target": "CVRPModel.py::Model.__init__.embedding_dim",
          "change": "embedding_dim: 128 -> 256",
          "rationale": "...",
          "expected": "更低的路径长度",
          "source_plan_ids": [],           // 组合型 plan 才有
          "novelty": "",                   // 仅 mutation 必填
          "status": "completed",           // pending|running|executed|completed|failed
          "label": 1,                      // 1|0|null
          "score": 11.9821,
          "delta_best": -0.2888,
          "execution": {
            "run": "artifacts/runs/plan-b1-s01.json",
            "started_at": 0, "finished_at": 0,
            "repairs": 0
          }
        }
      ]
    }
  ]
}
```

**所有权**：agent 只写 `batch-<n>.json`（纯内容）；orchestrator 负责
`plans.json` 的状态/label/score。这样 2.4「第一个未执行 → 正在执行」是确定性代码，
不是提示词祈祷。

## C.4 状态机

```
            ┌──────────┐   pipeline 选中第一个
            │ pending  │──────────────┐
            └──────────┘              ▼
                                 ┌─────────┐
              code_adjustment ───►│ running │
                                 └────┬────┘
                      ┌───────────────┼────────────────┐
                      ▼               ▼                ▼
              code_error 通过    code_error 失败   agent 崩/超时
                      │               │                │
                      ▼               ▼                ▼
              ┌───────────┐    ┌───────────┐    ┌──────────┐
              │ completed │    │ executed  │    │  failed  │
              │ label 0/1 │    │ label 0   │    │ label 0  │
              └───────────┘    └───────────┘    └──────────┘
                      │               │                │
                      └───────────────┴────────────────┘
                                      │
                             分数更好 → 更新 best 快照
```

- `executed` = 代码改过、跑过，但没通过（你原话里的「已执行」）；
- `completed` = 跑通且读到分数（你原话里的「已完成」）；
- `failed` = 修复预算耗尽仍不可运行（新增，否则挂掉的 plan 无处安放）。

## C.5 快照与回滚（C4 的解法）

```
artifacts/nn_improve/
├─ plans.json
├─ batch-1.json / batch-2.json / batch-3.json     # agent 原始产物
├─ best/
│  ├─ meta.json          # {plan_id, score, protocol, at}
│  └─ generated/         # 冠军代码
└─ results/<plan_id>/generated/                    # 可选，审计用
```

- 每次 code_error 通过且更好 → 用 `_copy_tree_filtered` 原子替换 `best/generated`。
- 每个 plan 开跑前 → 用 `best/generated` 覆盖 worktree 的 `artifacts/generated`
  （`reset_generated` 已有类似实现，`worktree.py:923`，改成从 `best/` 取即可）。
- plan 变差或失败 → 不写回，直接丢弃。**这样每一批的 12 次实验都是独立同基线**。
- 最终导出 `best/generated`，而不是 `artifacts/generated`（现在导出的是最后一次的）。

## C.6 打分、比较、公平性

| 问题 | 方案 |
|---|---|
| 方向 | `direction = config.score.direction`，默认 minimize；`better(a,b)` 统一封装 |
| 基线 | `label` vs `initial_score`（字面）；`best` 用滚动最好；两个都入库 |
| 噪声 | 搜索阶段固定测试集/seed（用 `grader/data/*.pkl` 或 `--seed`），并以 `min_delta` 抑制抖动的 label |
| 机械提分 | 搜索空间 deny `scale.*`、文件名、分数行 |
| 预算 | `eval.protocol=search`（如 20×500）用于排序；`acceptance`（200×5000）只用于 initial 与冠军复核 |
| 协议混用 | 报告里记录 `protocol`；**不同 protocol 的分数不允许比较**，`best_score` 只存 acceptance 口径 |

## C.7 「参数修改策略」段怎么留空

你要「留空，我后续补全」。最稳的做法是**两处都留，互相覆盖**：

1. `src/vrpar/prompts/s5_nn_improve.md` 里固定一段：

```markdown
## 参数修改策略（由项目维护者补全）

<!-- OWNER: 在此填写允许的修改策略清单。
     每条策略写成 `- id: s01 | 目标: CVRPModel.py::embedding_dim | 说明: ...`
     留空时：agent 自由探索，plan 的 kind=strategy 不校验 strategy_id。 -->

{nn_strategies}
```

2. 配置 `nn_improve.strategies_file: ""`：为空时 `{nn_strategies}` 渲染成
   `（暂未提供，按你的判断提出修改。）`；你之后把清单写进任意 md/yaml，
   填上路径即可，**不需要改代码**。

变异 plan 的定义随之变成：
- 策略清单为空 → `kind=mutation` 必须给 `novelty`（为什么它不属于任何已知策略）；
- 策略清单非空 → 流水线校验 `strategy_id` 必须在清单内，且 mutation 的 `strategy_id` 必须为 null。

## C.8 配置新增

```yaml
nn_improve:
  enabled: true
  rounds: 3                 # 3 批
  strategies_per_batch: 10
  mutations_per_batch: 2
  label_baseline: initial   # initial | best
  min_delta: 0.0            # 小于该幅度的提升不算 label=1
  max_repairs_per_plan: 1
  on_invalid_plans: fail    # fail | retry
  strategies_file: ""       # 留空 = 提示词空段
  space:
    allow: ["model.*", "trainer.*", "decoder.*", "encoder.*"]
    deny:  ["scale.problem_size", "scale.pomo_size", "scale.aug_factor"]
  eval:
    protocol: search
    train_epochs: 20
    train_episodes: 500
    test_episodes: 1000
    test_seed: 1234
  final:
    protocol: acceptance
```

`loop.max_rounds` 仍然管「报错-修复」循环；nn improve 的轮次独立，避免两个循环抢一个数字。

## C.9 WebUI / API

- `state.json` 增：`initial_score`、`best_score`、`nn_round`、`plans_done`、`plans_total`。
- `/api/score` 改为返回 `{initial, best, current, history:[{batch, plan_id, score, label, protocol}]}`；
  新增 `/api/plans` 返回 plans.json 摘要。
- 首页卡片改成：`status / initial score / best score / current score / nn round / plans`。
- 新增 Plans 面板（表格）：id、kind、标题、状态色块（未执行/正在执行/已执行/已完成/已修改）、
  label 1/0、score、Δbest。
- 文件树自动出现 `nn_improve/`，无需额外改动。

## C.10 兼容与迁移

| 项 | 处理 |
|---|---|
| 旧 run 的 `code_revise` | `RunState` / `get_stages` 里做别名映射，读到 `code_revise` 就挂到 `code_adjustment` 行上，UI 不出现幽灵阶段 |
| CLI `vrpar stage` | `REPLAYABLE_STAGES` 加 `code_adjustment`、`nn_improve`，保留 `code_revise` 作为别名 |
| `index.html` 的 steer 下拉框 | 改成从 `/api/health` 的 stages 动态生成，避免又硬编码一份 |
| mock backend | 增加 `[STAGE:nn_improve]`、`[STAGE:code_adjustment]` 分支，离线 e2e 才能跑 |
| `loop.on` 默认值 | 目前还写着 code_revise，一并改；或者干脆删掉这两个死配置 |
| 文档 | README/DESIGN/run_flow/faq 四份都要改阶段名与流程 |

## C.11 测试计划

新增（全部离线，mock backend）：

1. `test_plan_state_machine`：pending → running 只会取第一个未执行；completed/failed 迁移正确。
2. `test_label_direction`：minimize 下 11.9 < 12.0 → label=1；maximize 反之。
3. `test_best_snapshot_rollback`：plan A 变好写 best；plan B 变差 → 代码回到 A。
4. `test_plans_schema`：缺 `target`、`kind=mutation` 缺 `novelty` 时报错。
5. `test_offline_nn_improve`：3 批 × 2 plan，验证 36 上限、best 导出、plans.json 结构。
6. `test_stage_rename_compat`：旧 state.json 仍能渲染。

## C.12 分阶段落地

| 阶段 | 内容 | 风险 | 估时 |
|---|---|---|---|
| P0 | 修 3 个既有路径 bug（`<run>`、`data/artifacts/runs`、s2 路径）；改名 + 别名 + mock + 文档 | 低，纯机械 | 0.5d |
| P1 | plans 账本 + 双层循环 + 快照/回滚 + 每 plan 独立 report + state 的 initial/best | 中，orchestrator 要重构 | 2d |
| P2 | nn_improve 角色/提示词 + 空策略段 + 配置 + schema 校验 | 低 | 1d |
| P3 | API/WebUI 分数与 plans 面板 | 低 | 1d |
| P4 | 公平性（seed/固定测试集）+ 双协议预算 + 最终 acceptance 复核 | 中，要看你选的预算 | 0.5d |
| P5（可选） | 让流水线自己跑 test_train/test（用 `executor/contract.run_program`），agent 只写代码 | 中；换来确定性 exitcode/超时 | 1–2d |

> P5 值得认真考虑：现在 exitcode 是 agent 用 shell 写的（Windows 上 `echo $?` 还未必可用），
> 36 次实验的状态机完全建立在这个不可靠输入上。P5 之后，状态机的输入是
> subprocess 的真实 returncode，`code_error` 也不再需要烧一个 LLM turn。

## C.13 文件级改动清单

| 文件 | 动作 |
|---|---|
| `src/vrpar/pipeline/spec.py` | 改名 code_revise→code_adjustment；新增 nn_improve；加 `DEFAULT_NN_LOOP` |
| `src/vrpar/pipeline/orchestrator.py` | 重构 `run()` 为双层循环；`_inputs_for`→显式表；新增 `stage_nn_improve`、`stage_code_adjustment(mode)`；best 快照/回滚；导出 best |
| `src/vrpar/pipeline/stages.py` | `STAGE_PROMPT_KEYS` 改名+新增；`stage_variables` 增 `plan_id`/`plan_json`/`adjustment_mode`/`nn_strategies`；修 `EXECUTION_CONTRACT` 与 s3 的矛盾 |
| `src/vrpar/pipeline/gates.py` | pins 参数化（acceptance/search 两套）；report 增 `protocol`/`plan_id` |
| `src/vrpar/run/state.py` | 增 `initial_score`/`best_score`/`nn_round`/`set_best`；旧 stage 别名 |
| `src/vrpar/run/worktree.py` | `reset_generated` 支持 source 参数；`best` 快照 helper |
| `src/vrpar/problems/__init__.py` | `PROMPT_KEY_ALIASES` 增两项 |
| `src/vrpar/config.py` | `nn_improve` 默认值；`loop_config` 去掉/落实死配置 |
| `src/vrpar/llm/mock.py` | 两个新 stage 的 mock 产物 |
| `src/vrpar/cli.py` | `STAGE_CHOICES`、`REPLAYABLE_STAGES` |
| `src/vrpar/web/api.py` | `/api/plans`、`/api/score` 扩展 |
| `src/vrpar/web/static/{index.html,app.js,style.css}` | 分数卡 + Plans 面板 + 动态 steer 选项 |
| `src/vrpar/roles/*`、`src/vrpar/prompts/*` | 改名 2 个、新增 2 个、修 3 处路径 |
| `tests/*` | 见 C.11 |
| `README.md`/`DESIGN.md`/`docs/*` | 同步 |

---

# D. 需要你拍板的问题（按重要性排序）

**阻断级**

1. **分数方向**：确认 `AUGMENTATION SCORE` 是「越小越好」吗？2.2 的「高=label 1」是笔误，
   还是你的项目里分数另有定义（比如取负）？
2. **测试可比性**：允许把 nn improve 的搜索固定在同一个测试集/seed 上吗？
   （`mvmoe_ovrpl_n50/task.yaml` 自带 1000 条 pkl，正好可用；现在 tips 要求随机数据。）
3. **训练预算**：36 次都跑 200×5000（≈94 小时）？还是搜索用短预算 + 冠军验收？
4. **回滚**：每个 plan 从「当前最好」重新开始（我强烈建议），还是串行累积？
5. **搜索空间**：`aug_factor` / `pomo_size` 允许改吗？（我认为必须禁止，否则分数作弊。）

**设计级**

6. label 参照系：固定 initial，还是滚动 best？（我建议 label 用 initial，best 用滚动）
7. 12 个 plan 是否全部执行（10+2）？36 这个数字是否 = 3×12？
8. 一个 label=1 都没有时怎么办？全 1 时选哪些组合？
9. 「组合」遇到同参数冲突时的合并规则？
10. `code_adjustment` 的 error 与 plan 优先级（我建议先修复后应用 plan）？
11. 「已修改」是 agent 改完就打，还是跑绿才算？
12. 最终导出「最好的那份」还是「最后一份」？

**确认级**

13. 文件名到底是 `test_train.py`（现仓库）还是 `train_test.py`（你的描述）？
14. 入口仍然是 `problems/<slug>/task.yaml` 文件，还是要在 WebUI 里加一个「输入问题描述」的框？
15. 「不管什么样的问题都能接收」：是仍限定「POMO 可改写的 VRP 变体」，还是真的要支持任意任务？
    （后者要重写 analysis/codegen 的角色设定、分数行、导出结构。）
16. 变异 plan 在策略段为空时怎么判定「不在策略里」？
17. 是否接受把 code_error 的执行从 agent 改为流水线 subprocess（P5）？

---

# E. 我明确不理解的地方

1. **「一次生成 10 种修改 plan 以及两种变异 plan」与「3 次循环最多 36 个」的关系**：
   12×3=36 是吻合的，但「最多」意味着可能更少。什么情况下会少生成？
2. **「将标签为 1 的方式进行组合」**：组合是「参数取并集」，还是「把两个方案的思想合成
   一个新方案」？前者可机械校验，后者只能靠 agent。
3. **「每执行一种……将这个 plan 后面打上已执行、未执行、正在执行的标签」**：
   「未执行」是初始状态（我的理解），还是某一轮之后被跳过的 plan 也要回写成「未执行」？
4. **「code error agent 生成分数」**：分数是测试脚本打印的，agent 只是搬运。
   你是想强调「由 code_error 阶段负责产出分数」，还是想让 code_error **计算**分数？
5. **「重新生成代码」**（2.1）：是 code_adjustment 按 plan **打补丁**，还是像 code_generation
   那样**重写整个包**？前者安全，后者会覆盖掉已跑通的实现。
6. **「最后得到新的代码」**：交付物是「冠军代码 + pomo/」，还是要把 36 个 plan 的记录一起交付？

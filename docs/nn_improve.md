# nn improve 使用手册

> 这份文档面向使用者：它怎么跑、产物在哪、怎么补策略段、出问题怎么看。
> 设计说明与决策记录在 [`nn_improve_plan.md`](nn_improve_plan.md)。

## 1. 它做什么

`analysis → code_generation → code_error` 第一次跑通之后，流水线进入 nn improve：

1. 用 `search` 协议（默认 50 epochs × 5000 episodes）给当前代码打一个基线分
   `initial_search_score`；
2. 最多 3 批，每批让 `nn_improve` agent 给出 10 个 strategy + 2 个 mutation plan；
3. 每个 plan 都在流水线指定的**基底快照**上只改一条神经网络方法，由 `code_adjustment` 落地、
   `code_error` 打分；
4. 分数比固定基线好的 plan 打 label 1，其代码存进 `winners/`；更好的成为冠军
   `best/`；
5. 一批里没有任何 label 1，搜索停止；
6. 最后冠军用 `acceptance` 协议（200 epochs × 5000 episodes）复核，
   **不如初始代码就回退导出初始代码**。

## 2. 两个协议

| 协议 | 默认预算 | 用途 |
|---|---|---|
| `search` | 50 × 5000 | 给 36 个 plan 排序；label 的固定门槛来自同一协议的初始代码 |
| `acceptance` | 200 × 5000 | 初始代码的官方分、冠军的最终复核 |

两个协议的分数**不能互相比较**。原始需求里的 `initial` 是 acceptance 口径，
`initial_search` 是为了让 50×5000 的 plan 有一个同口径门槛而额外跑的。

配置位置：`configs/default.yaml` 的 `nn_improve.search` / `nn_improve.acceptance`，
或某个 bundle 的 `litellm_config.yaml`，或命令行：

```bash
uv run vrpar start problems/<slug>/task.yaml \
  nn_improve.search.train_epochs=20 nn_improve.rounds=1
```

## 3. 目录与账本

```
artifacts/nn_improve/
├─ plans/b1-s01.md … b1-m02.md                   # agent 写的 plan（每个 plan 一个文件）
├─ plans.json                                   # 流水线维护的唯一账本
├─ initial/generated/                           # 第一次验收通过的代码（回退用）
├─ best/{meta.json, generated/}                 # 当前冠军
└─ winners/<plan_id>/{meta.json, generated/}    # 每个 label=1 的代码快照
```

`plans.json` 顶层：

```jsonc
{
  "direction": "minimize",
  "initial_score": 11.80,          // acceptance 口径
  "initial_search_score": 12.10,   // search 口径，label 门槛
  "best_score": 11.92,
  "best_plan_id": "b2-s03",
  "final_score": 11.74,            // 冠军 acceptance 复核；回退时为 null
  "batches": [ ... ]
}
```

每个 plan 的关键字段：

| 字段 | 含义 |
|---|---|
| `kind` | `strategy` / `mutation` |
| `change` | 一句话的方法改动（不是参数列表） |
| `base_plan_id` | 它建立在哪个快照上；第 1 批为 `null`（initial） |
| `status` | `pending` → `running` → `completed` / `executed` / `failed` |
| `label` | 1 = 比 `initial_search_score` 好；0 = 没比过 |
| `score` | 该 plan 的 search 分 |
| `delta_best` | 相对进入该 plan 前的冠军分 |

状态含义：

- `pending` 未执行；`running` 正在执行；
- `completed` 跑通且拿到分数；
- `executed` 跑过但没通过（重试预算耗尽）；
- `failed` agent 没能产出可运行的包。

## 4. 基底与保存规则

- 第 1 批：12 个 plan 全部以 **initial 代码** 为基底，各改一条，互不影响；
- 第 2/3 批：把此前所有 `label=1` 的节点按分数升序取前 10，按槽位循环分配；
  mutation 排最后两槽；
- 只有 `label=1` 的代码会被保存；`label=0` 或失败的直接丢弃；
- 冠军（`best/`）只在分数更好时替换。

## 5. 方法由 agent 自己生成

`nn_improve` 不再有"参数修改策略"清单，也不要求 agent 复刻任何已发表方法（可以参考，
但要针对当前冠军代码重新设计、不要照搬）。agent 读冠军代码、`artifacts/analysis/plan.md`、
`plans/` 里的历史 plan 与 `plans.json` 的分数/标签，自己设计下一批**神经网络方法**
（网络结构、信息流、输入表示、masking、模型内部机制），每个 plan 一个文件：

```
artifacts/nn_improve/plans/b<batch>-s01.md   # front matter + 6 节设计说明
```

**改动范围**：只改网络，以及为它服务的输入表示/mask 代码。测试器、训练器、入口脚本、
`test_train.py`、`model_params`、实例生成方式都不在范围内（这一点写在提示词里，pipeline
不做代码比对）。`repair` 轮不受此限制。

front matter 是流水线的机读部分：

```yaml
---
id: b1-s01
batch: 1
kind: strategy
base: initial
title: 两轮迭代编码
target: CVRPModel.py::CVRP_Encoder.forward
change: 编码器由单次前向改为共享权重的两轮迭代
rationale: 为什么这套机制能降低代价
---
```

流水线只校验：文件数（10+2，按 id 排序 strategy 在前）、`id`/`batch`/`kind`/`title`/
`target`/`change`/`rationale` 必填、`change` 不是纯数值、`title/target/change` 不含
`space.deny` 里的禁用词。纯调参 plan（只换一个数）会被拒。正文（6 节）不解析，给
`code_adjustment` 与人工阅读用。

想影响 agent 的设计方向，用仪表盘的 steer 通道（写一条指令，下一阶段消费），或直接改
`src/vrpar/prompts/s5_nn_improve.md`。`strategies_file` / `strict_strategies` 已删除。

## 6. 常见行为

| 现象 | 原因 / 处理 |
|---|---|
| 第 1 批结束就停 | 12 个 plan 都没有比 `initial_search_score` 好；看 `plans.json` 的 `delta_initial` |
| `final_score` 是 null | 冠军 acceptance 复核不如 initial，已回退导出 initial |
| 某个 plan `executed` / `failed` | 看 `artifacts/runs/plan-<id>/report.json` 的 `issues`；`code_adjustment` 的 revision 文件在 `artifacts/revisions/` |
| 批次被拒 | 日志里有 `invalid plan:`；`on_invalid_plans: retry` 会重问一次 |
| 导出的是初始代码 | 没有冠军，或冠军复核没过；`REPORT.md` 有 `final score` |

### 权重与代码保留（方便事后验证）

每次执行（`initial` / `initial-search` / `plan-<id>` / `final`）结束后，pipeline 会保留：

```
artifacts/runs/<key>/
├─ checkpoint-<epochs>.pt     # 打分那次训练出来的权重（repair 重跑会覆盖，只留最终那次）
└─ generated/                 # 打分那次跑过的代码
```

`winners/<id>/meta.json`、`best/meta.json` 里有 `checkpoint` 指针；导出目录
`workspace/final/<slug>/checkpoints/` 里还有冠军的 checkpoint。想复核某个 plan，直接用它
自己的 `generated/` + `checkpoint-*.pt` 跑 `test_n<n>.py --model-path ...`。

## 7. 仪表盘

`uv run vrpar ui --port 8420`，打开后：

- 顶部卡片：`initial (200)`、`best (search)`、`final (200)`、`nn round`、`plans x/y`；
- `NN improve` 面板：按批次列出每个 plan 的状态色块、label、score、Δbest；
- `Files` 面板里能直接打开 `artifacts/nn_improve/plans.json` 与 `plans/<id>.md`。

## 8. 成本

默认 `search` 50×5000 ≈ 40 分钟/plan（按仓库实测 0.6 秒/step 估），36 个 plan 约 24 小时，
加 initial 与冠军两次 acceptance（各约 2.6 小时）≈ **30–36 小时**。
想快就调：

```bash
nn_improve.search.train_epochs=20   # 每 plan 约 16 分钟
nn_improve.strategies_per_batch=5   # 每批 7 个
nn_improve.rounds=1                 # 只跑一批
```

## 9. 排错顺序

1. `logs/pipeline.log` 搜索 `batch` / `plan ` / `champion`；
2. `artifacts/nn_improve/plans.json` 看状态与分数；
3. `artifacts/runs/plan-<id>/` 看 `report.json`、stdout/stderr、exitcode；
4. `artifacts/revisions/plan-<id>.md` 看 agent 声称改了什么；
5. 如果 agent 的改动和声称不符，`code_error` 的失败会进 repair 轮。

### `PermissionError: state.json`（Windows）

`os.replace` 覆盖 `state.json` 时，如果 `vrpar ui` 的轮询/SSE 或编辑器正好在读它，
Windows 会拒绝替换。`run_dir.write_json` 现在会重试约 5 秒并清理只读属性；
仍失败时抛 `could not replace ... (the file is probably open in another process)`。
长时间跑时尽量不要用编辑器打开 `state.json`；UI 同时开着没问题。

## 10. 中断后恢复

进程被杀、机器重启、或写状态时被打断，都可以从 run 目录继续：

```bash
uv run vrpar start problems/<slug>/task.yaml \
  --resume results/<slug>/<run-id>
```

它会：

1. 跳过 analysis / code_generation / acceptance / initial-search（这些产物已在磁盘上）；
2. 把崩溃时处于 `running` 的 plan 重置为 `pending`（每个 plan 开跑前都会从基底快照恢复代码，
   重跑是安全的）；
3. 从当前批次继续，跑完剩余 plan，再进入后续批次；
4. 如果 `final_attempted` 还没置位，跑冠军 acceptance 复核；已跑过就不再重复；
5. 导出并写 `REPORT.md`。

前提：`artifacts/analysis/plan.md`、`artifacts/generated/`、
`artifacts/nn_improve/{initial,best,plans.json}` 与 `plans/` 还在。缺账本时会明确报错，
提示你重新起一次 run。

> 兼容性：本次改造前的旧 run 里，plan 是 `batch-*.json`、账本里是 `changes`，没有
> `plans/<id>.md`。**旧 run 不能 resume**，请把它跑完或重新起一次 run。

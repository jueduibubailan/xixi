# nn improve 改造方案 v9（最终版）

> ⚠ 历史文档：v9 的「参数 plan / `batch-<n>.json`」已被取代。当前实现是：每个 plan 一个
> `artifacts/nn_improve/plans/<id>.md`（front matter + 设计说明），方法由 agent 自己
> 设计，`s5_nn_improve.md` 里的人类策略清单与 `strategies_file` 已删除。本文件仅存档。

> 本版按你第 10 轮回答修正：阶段名 `code_judgement` → **`code_adjustment`（Adjustment agent）**。
> 提示词不删任何原有内容，只做改名 / 追加 / 改写，全文见 [`nn_improve_prompts.md`](nn_improve_prompts.md)。
> 回复"确认"即开工。

---

## 0. 决策台账

| 来源 | 决定 | 代码规则 |
|---|---|---|
| R2-2 | 预算 | `search = 50×5000`，`acceptance = 200×5000` |
| R2-3 + R4-4 | 执行者 | agent 在本机跑（不引入 P5） |
| R2-4 + R4-4 | label 门槛 | 固定 `initial_search_score`（同一份 initial 代码跑 50×5000） |
| R2-6 + R5-2 + R6-3 | 基底 | 第 1 批全用 initial；第 2/3 批按赢家分数升序 `i mod k` 循环 |
| R2-7 | 变异校验 | 宽松：只要求 `novelty` 非空 |
| R2-8 | 范围 | 任意 VRP 变体 |
| R2-9 | 冲突规则 | 同参数取分数更好、不同参数叠加（兜底） |
| R2-11 + R4-5 | 全 0 | 终止搜索；已有冠军仍跑 final acceptance |
| R4-6 | 回退 | final 不如 initial → 导出 initial |
| R5-3 | 代码保存 | 只保存 label=1 的快照 |
| R6-2 + R8-2 | 测试集 | **流水线不生成、不固定**；默认随机；**tips 若指定则按 tips** |
| R8-3 | s1 | **不改**（不加任何路径映射） |
| R9-2 | 提示词 | **不删原文**；`code_revise_agent.md` / `s4_revise.md` 仅改名并追加，见 prompts 文档 |
| R10-2 | 阶段命名 | 原「判定 agent」统一改名 **Adjustment agent**：`code_adjustment` / `code_adjustment_agent.md` / `s4_adjustment.md` / `{adjustment_mode}` |

---

## 1. 测试集：默认随机，tips 可以指定

**流水线不做任何测试集实现**（不生成 pkl、不读写 `test_data_load`）。
现有机制已经同时支持两种模式，本次**只在文档里补一句**，不改默认行为：

- **默认**（tips 没提）：`test_n<n>.py` 用 `test_data_load.enable = False`，
  `test_episodes = 10*1000`，每次随机生成测试数据——**保持现状**；
- **tips 指定时**（如 `problems/reference/task.yaml` 的第 2 条）：
  agent 按 tips 加载上传的 `vrpltw50_uniform.pkl`，`test_episodes` 用该集合条数。

提示词已有的两处已经覆盖这个分支，不需要改：

- `s2_codegen.md`：`if the plan names a test set, load it; otherwise ... random`，
  并写明"fixed set 用集合条数，random 用 `10*1000`"；
- `s3_code_error.md`：`do not assume random test data`。

### 1.1 文档改动是"补一句"，不是"改写"

`problems/reference/README.md` 第 66-68 行、`docs/run_flow.md`、`docs/faq.md` 里
"随机 10*1000"的描述**保留原样**，只在旁边**新增**一句：

> 测试集可以由 `task.tips` 指定；指定时按 tips 加载（例如固定 pkl 与其条数），
> 未指定时保持上面的随机测试协议。

### 1.2 一个连带影响（只告知，不改）

默认随机模式下，36 个 plan 的分数不在同一批算例上，靠近门槛的 label 可能被采样噪声翻转。
**只有在 tips 指定了固定测试集时，label 才是确定可比的**（reference 那份就是）。
要压噪声可以设 `nn_improve.min_delta`（默认 0），或在 tips 里要求测试入口加 seed —— 都归你控制。

### 1.3 关于"tips 固定的项"

tips 里固定了什么（seed、`train_batch_size`、`test_batch_size` 等），流水线**不需要知道**，
也不该硬编码进 `space.deny`。所以：

- `space.deny` 只放任务无关的硬约束：`scale.problem_size` / `scale.pomo_size` /
  `scale.aug_factor` / 文件名 / 分数行格式；
- 提示词里加一条通用规则："**不得修改 `task.tips` 中固定的任何值**"，
  由 agent 读 tips 自行遵守。

---

## 2. "让 agent 跑" = 在你本机训练

- 编排器起一个 agent 进程（`dsh`/`claude`），`cwd = worktrees/<stage>/`，用本机 Python 与 GPU；
  LLM 推理可能在远端，shell / 文件 / 训练都在本机。
- 训练发生在 agent 的一次 turn 里：50×5000 ≈ 40 分钟，200×5000 ≈ 2.6 小时；turn 被杀则本次丢失。
- 流水线 turn 结束后才读 `exitcode` 与 `test.stdout.txt`。

3 个修补（不引入 P5）：
1. `<run>` 占位符（已复现为字面量）→ 绝对路径 `{report_dir}`；
2. 提示词给 bash / PowerShell 两种写 exitcode 的方式，并要求回读确认；
3. gate 里 exitcode 缺失 = 失败，但把 `*** Training Done ***` / `AUGMENTATION SCORE`
   摘要写进 `report.json` 供 repair 用。

---

## 3. 基底分配与代码保存（A/B/C）

```python
def assign_bases(batch, winners):
    if batch == 1:
        return [None] * 12                     # A：全部以 initial 为基底
    ordered = sorted(winners, key=lambda p: p["score"])[:10]
    if not ordered:
        return []
    return [ordered[i % len(ordered)]["id"] for i in range(12)]  # B：循环分配
```

- 第 1 批：12 个 plan 各在 initial 上改 1 条，互不影响；
- 第 2/3 批：label=1 的节点按分数升序取前 10，第 i 个 plan 落在第 `i mod k` 个赢家上；
  两个 mutation 排最后两槽；"优先推赢家改过的参数"写进提示词（C：软提示）。

```
plan 跑完：
  label=1 → winners/<id>/{meta.json, generated/}
             且 score < best_score → 更新 best/generated
  label=0 / 失败 → 不保存，丢弃
保存前清理 result/、checkpoint-*.pt、__pycache__、*.pyc
```

## 4. 流程

```
analysis ──► plan.md
   ▼
code_generation ──► generated/
   ▼
┌─ 验收循环（acceptance 200×5000）──► 失败 ──► code_adjustment(repair) ──┐
│   通过：initial_score；快照 initial/generated                          │
└────────────────────────────────────────────────────────────────────────┘
   ▼
initial-search（同一份代码，50×5000）──► initial_search_score
   ▼
┌─ 第 1 批：12 plan，基底全为 initial ──────────────────────────────────┐
│  nn_improve(1) → batch-1.json                                         │
│  for plan in 12：                                                     │
│    pending → running；从基底快照恢复                                   │
│    code_adjustment(plan) → code_error(search) → 打分                   │
│    label=1 → 存 winners/<id>；更好 → best                              │
│    label=0/失败 → 丢弃（失败先 repair 1 次）                            │
│  全 0 → 终止                                                           │
└───────────────────────────────────────────────────────────────────────┘
   ▼
┌─ 第 2/3 批：基底 = label=1 按分数升序取前 10，循环分配 ────────────────┐
│  每批 12 个；全 0 → 终止                                               │
└───────────────────────────────────────────────────────────────────────┘
   ▼
有冠军 → final（best 代码，acceptance 200×5000）
         final < initial_score → 导出 initial；否则导出 best
         无冠军 → 跳过 final，直接导出 initial
```

## 5. 数据布局

```
artifacts/analysis/plan.md
artifacts/generated/                      # 当前执行的工作副本
artifacts/runs/{initial, initial-search, plan-<id>, final}/
artifacts/revisions/{plan-<id>.md, round-<n>.md, index.json}
artifacts/nn_improve/
  batch-1.json / batch-2.json / batch-3.json     # agent 写
  plans.json                                     # 流水线写
  initial/generated/
  best/{meta.json, generated/}
  winners/<plan_id>/{meta.json, generated/}      # 只存 label=1
```

`plans.json` 每个 plan：

```jsonc
{
  "id": "b2-s03", "kind": "strategy", "strategy_id": null,
  "title": "head_num 8→16", "target": "CVRPModel.py::Model.__init__.head_num",
  "changes": [{"param": "head_num", "from": 8, "to": 16}],
  "base_plan_id": "b1-s02",
  "status": "completed", "label": 1,
  "score": 11.9214, "protocol": "search",
  "delta_initial": -0.1795, "delta_base": -0.0500, "delta_best": 0.0000,
  "execution": {"report_dir": "artifacts/runs/plan-b2-s03", "repairs": 0}
}
```

## 6. 阶段表

```python
STAGES = [
    {"name": "analysis",        "kind": "agent", "prompt_key": "s1_analysis",
     "produces": ["artifacts/analysis/plan.md"], "timeout_seconds": 3600},
    {"name": "code_generation", "kind": "agent", "prompt_key": "s2_codegen",
     "produces": ["artifacts/generated/"], "timeout_seconds": 3600},
    {"name": "code_error",      "kind": "agent", "prompt_key": "s3_code_error",
     "produces": ["artifacts/generated/test_train.py", "artifacts/runs/"],
     "timeout_seconds": 14400},
    {"name": "code_adjustment", "kind": "agent", "prompt_key": "s4_adjustment",
     "produces": ["artifacts/revisions/"], "timeout_seconds": 10800},
    {"name": "nn_improve",      "kind": "agent", "prompt_key": "s5_nn_improve",
     "produces": ["artifacts/nn_improve/"], "timeout_seconds": 3600},
]

STAGE_INPUTS = {
    "analysis":        [],
    "code_generation": ["artifacts/analysis/plan.md"],
    "code_error":      ["artifacts/analysis/plan.md", "artifacts/generated"],
    "code_adjustment": ["artifacts/analysis/plan.md", "artifacts/generated",
                        "artifacts/runs", "artifacts/nn_improve"],
    "nn_improve":      ["artifacts/analysis/plan.md", "artifacts/generated",
                        "artifacts/runs", "artifacts/nn_improve"],
}
```

## 7. 删除清单

| # | 位置 | 删什么 |
|---|---|---|
| D1 | `roles/code_revise_agent.md` | **仅改名** → `code_adjustment_agent.md`；原文逐句保留，只改标题/`agent_id` 并追加（见 prompts 文档 §4） |
| D2 | `prompts/s4_revise.md` | **仅改名** → `s4_adjustment.md`；原有 repair 段落逐句保留，另加 plan 模式（见 §8） |
| D3 | `pipeline/orchestrator.py` | `_inputs_for()` |
| D4 | `pipeline/orchestrator.py` | 未使用的 `read_baseline` 导入 |
| D5 | `orchestrator.py::REPLAYABLE_STAGES` | `code_revise` 项（旧名留别名 1 版） |
| D6 | `pipeline/spec.py` | `code_revise` 阶段项 |
| D7 | `pipeline/gates.py` | `TRAIN_PIN_EPOCHS` / `TRAIN_PIN_EPISODES` 硬编码正则 |
| D8 | `config.py::loop_config` | `on` / `until` 死配置 |
| D9 | `run/worktree.py` | `untouched_outputs()`、`exists_artifact()` |
| D10 | `executor/score.py` + `executor/__init__.py` 的 re-export | 与 `gates.parse_score` 重复 |
| D11 | `web/static/index.html` | steer 的 4 个硬编码 option；footer 旧四阶段 |
| D12 | `web/static/app.js` | `v-initial="—"` 硬编码；`loadScore` 覆盖 `v-best` |
| D13 | `scripts/update_tips.py` | 旧 tips 整段 |
| D14 | `llm/mock.py` | `[STAGE:code_revise]` 分支 |
| D15 | `tests/test_pipeline_offline.py` | 旧断言 |
| D16 | `README.md`/`DESIGN.md`/`docs/run_flow.md`/`docs/faq.md` | 四阶段、"last score" 的描述（测试集段落**不改**，只在旁边补一句，见 §1.1） |

不删 `executor/contract.py`；`problems/*/task.yaml` 的 tips 不改。

## 8. 修改清单

| # | 文件 | 改什么 |
|---|---|---|
| M1 | `pipeline/spec.py` | 5 阶段表、`STAGE_INPUTS`、`DEFAULT_NN_LOOP`、`code_error` timeout 14400 |
| M2 | `pipeline/orchestrator.py` | 双层循环；`_assign_bases/_restore_from/_save_winner/_update_best/_prune_generated/_run_plan/_acceptance_loop/_final_confirm`；按子目录收集；导出 best/initial |
| M3 | `pipeline/stages.py` | 两个新 prompt_key；变量 `adjustment_mode/plan_json/plan_id/base_plan_id/nn_strategies/nn_batch/eval_epochs/eval_train_episodes/eval_protocol/report_dir/evidence_dir`；修契约与 s3 矛盾 |
| M4 | `pipeline/gates.py` | `check_code_error(run_dir, report_key, pins)`；report 带 protocol/pins/plan_id |
| M5 | `run/state.py` | initial/search/best/final/nn_round/plans；`set_initial/set_best/set_final`；旧 stage 别名 |
| M6 | `run/worktree.py` | `seed_worktree(generated_source=)`；`reset_generated(source)`；启用 `changed_since_snapshot` |
| M7 | `run/run_dir.py` | `SUB_DIRS` 加 `artifacts/nn_improve` |
| M8 | `problems/__init__.py` | `PROMPT_KEY_ALIASES` 加两项 |
| M9 | `config.py` | `nn_improve` 默认块 |
| M10 | `web/api.py` | `/api/plans`；`/api/score` 扩展；`/api/health` 返回 stages |
| M11 | `web/static/{app.js,index.html,style.css}` | 分数卡 + Plans 表格 + 动态 steer |
| M12 | `llm/mock.py` | 新阶段 mock |
| M13 | `cli.py` | stage choices |
| M14 | `roles/code_generation_agent.md` | 只修路径（`data/artifacts/analysis/plan.md`） |
| M15 | `roles/code_error_agent.md` | 预算 `{eval_*}`、`checkpoint-<epochs>.pt`、`{report_dir}`、两种 shell 写法 |
| M16 | `prompts/s2_codegen.md` | 只追加一条 `model_params` 一致性规则（不删原文，见 prompts 文档 §6） |
| M17 | `prompts/s3_code_error.md` | `<run>` → `{report_dir}`；预算 `{eval_*}`；只覆盖三个预算键、不重建参数字典 |
| M18 | `problems/*/litellm_config.yaml` | `nn_improve` 块；`code_error` timeout 14400 |
| M19 | `problems/reference/README.md`、`docs/run_flow.md`、`docs/faq.md` | **只在测试集段落旁补一句**"测试集可以由 tips 指定；未指定时保持随机协议"，不删原文 |

> 提示词逐段内容（含 `s3`/`s4`/`s5` 全文、两个角色全文、变量表）见
> [`nn_improve_prompts.md`](nn_improve_prompts.md)。原则：**不删任何原有句子**，
> 只做「改名 / 追加段落 / 改写个别句子」。

**不动**：`roles/analysis_agent.md`、`prompts/s1_analysis.md`（按你的第 3 条）。

## 9. 新增清单

```
src/vrpar/nn_improve/__init__.py
src/vrpar/nn_improve/plans.py        # schema + 状态机 + label + 基底分配
src/vrpar/nn_improve/budget.py       # protocol → pins / timeout
src/vrpar/roles/nn_improve_agent.md
src/vrpar/roles/code_adjustment_agent.md
src/vrpar/prompts/s4_adjustment.md
src/vrpar/prompts/s5_nn_improve.md
tests/test_nn_plans.py
tests/test_nn_loop.py
tests/test_budget.py
docs/nn_improve.md
```

## 10. 提示词要点

- `s5_nn_improve`：只写 `batch-<n>.json`；10+2；每 plan 一条；
  输入给 `{base_assignments}` 与 winners 表（分数升序）；优先延续赢家改过的参数（软提示）；
  **禁止**改：`scale.*`、文件名、分数行、训练预算、以及 **`task.tips` 里固定的任何值**；
  末尾固定 `## 参数修改策略（由项目维护者补全）` + `{nn_strategies}`。
- `s4_adjustment`：`{adjustment_mode}` 两套；plan 模式只改 1 条、写 `plan-<id>.md`；
  repair 模式最小修复、写 `round-<n>.md`；不在 adjustment 里重跑训练；同样禁止改上述项。
- `s3_code_error`：`{report_dir}` 绝对路径；预算以 `{eval_epochs}`/`{eval_train_episodes}` 为准；
  `checkpoint-<epochs>.pt`；bash/PowerShell 两种 exitcode 写法；只改 `test_train.py`；
  测试数据按 `plan.md` / tips / 生成代码的实际实现执行（**不假定随机，也不假定固定**）。
- `s1_analysis`、`s2_codegen`：s2 只追加 `model_params` 一致性规则；测试集分支保持原样。

## 11. 配置

```yaml
nn_improve:
  enabled: true
  rounds: 3
  strategies_per_batch: 10
  mutations_per_batch: 2
  one_change_per_plan: true
  label_baseline: initial-search
  min_delta: 0.0
  strict_strategies: false
  strategies_file: ""
  max_repairs_per_plan: 1
  on_invalid_plans: fail
  stop_when_no_winner: true
  base_selection: top-winners
  base_pool_size: 10
  space:
    allow: ["model.*", "trainer.*", "encoder.*", "decoder.*"]
    deny:  ["scale.problem_size", "scale.pomo_size", "scale.aug_factor"]
  search:     {protocol: search,     train_epochs: 50,  train_episodes: 5000}
  acceptance: {protocol: acceptance, train_epochs: 200, train_episodes: 5000}
```

## 12. WebUI

- 卡片：`status / initial(200) / initial-search(50) / best / final / nn round / plans x-y`
- Plans 面板：按批次分组；id、kind、标题、base_plan_id、状态色块、label、score、Δbase、Δinitial
- Revisions：repair（已修改）/ plan（已应用）
- steer 下拉框由 `/api/health.stages` 动态生成

## 13. 测试

1. `test_nn_plans.py`：schema、`changes` 长度 1、base 必须是 label=1、状态迁移、label 方向
2. `test_nn_plans.py`：第 1 批 base 全 null；第 2 批按分数升序循环分配
3. `test_nn_loop.py`：3 批；全 0 终止；只存 label=1；best 更新；final 回退
4. `test_budget.py`：50/5000 与 200/5000 → pins 与 gate 一致
5. `test_gates.py`：两种 pins 各自通过/拒绝
6. `test_stage_rename.py`：旧 state.json 别名
7. 回归：改写后的 `test_pipeline_offline.py` 必须绿

## 14. 工期与机时

| 阶段 | 内容 | 估时 |
|---|---|---|
| P0 | 3 个路径 bug + 改名/别名 + mock + 文档 | 0.5d |
| P1 | `plans.py` + 双层循环 + 基底/快照 + 按子目录收集 + state | 2d |
| P2 | 两个新提示词/角色 + 配置 | 1d |
| P3 | API/WebUI | 1d |
| P4 | 冠军验收/回退 + 测试 + 端到端 | 1d |
| | **合计** | **≈5.5 天** |

机时：2.6h（initial）+ 0.65h（initial-search）+ 36×约 50min + 2.6h（final）≈ **36h**。

---

**回复"确认"即从 P0 开始。**

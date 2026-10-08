# BASELINE_NOTES — POMO (CVRP) 与 AFL 参考实现

本目录是**只读基线**。每次 `cvrp start` 会把它复制到 `results/<task>/<ts>/worktrees/` 下，
agent 只在副本里改代码。**永远不要直接改这里的文件**（改副本，或者改真正需要改的上游）。

```
# 本 problem 自带的基线载荷（随 problem 一起分发，自包含）
src/cvrp/problems/cvrptw/
├─ pomo/                            # POMO 官方 CVRP 实现（要在此基础上改造）
│  ├─ CVRProblemDef.py              # 随机实例生成 + 8 折数据增强
│  └─ POMO/
│     ├─ CVRPEnv.py                 # 环境：状态、step、masking
│     ├─ CVRPModel.py               # 网络：encoder + POMO decoder
│     ├─ CVRPTrainer.py             # 训练循环（REINFORCE + shared baseline）
│     ├─ CVRPTester.py              # 推理/测试（多起点 rollout + 8 折）
│     ├─ train_n100.py              # 训练入口样例
│     ├─ test_n100.py               # 测试入口样例
│     └─ utils/utils.py             # 日志/绘图工具
├─ checkpoints/checkpoint-30500.pt  # POMO 在 CVRP100 上训练好的权重（必须能加载）
└─ BASELINE_NOTES.md                # 你正在读的这个文件

baseline/                           # 项目级共享载荷（其他变种可以指向它）
├─ pomo/  checkpoints/  BASELINE_NOTES.md
└─ afl/                             # AFL 的 agentic prompt 实现（只作参考，不参与运行）
   ├─ describe.py  describe_judgement.py
   ├─ code_generation.py  code_judgement.py
   └─ complete_code_revise.py  main.py
```

> 两份 `checkpoint-30500.pt` 是同一个文件：`baseline/checkpoints/` 是共享副本，
> `src/cvrp/problems/cvrptw/checkpoints/` 让 problem 目录可以独立分发。
> 见 `problem.yaml:resources.baseline_dir`——它先按**项目根**解析，再按 **problem 目录**解析。
>
> `baseline/afl/` 里带有 AFL 原作者硬编码的 DeepSeek API key（原样保留，便于对照）。
> 本项目运行时不读取任何 key：`cvrp` 只通过已登录的 Claude Code CLI 调用模型。

## 1. POMO CVRP 的现状（改造前）

### 1.1 数据（`CVRProblemDef.py`）

```python
def get_random_problems(batch_size, problem_size):
    depot_xy  = torch.rand(batch_size, 1, 2)              # (B, 1, 2)
    node_xy   = torch.rand(batch_size, problem_size, 2)   # (B, n, 2)
    node_demand = torch.randint(1, 10, (batch_size, problem_size)) / 10.  # (B, n)
    return depot_xy, node_xy, node_demand
```

- 坐标在单位正方形 `[0,1]^2`，需求在 `[0.1, 0.9]`，容量固定 `50`。
- `augment_xy_data_by_8_fold` 做 8 折对称增强（x 翻转 / y 翻转 / 对角交换）。

### 1.2 环境（`CVRPEnv.py`）

| 状态 | 形状 | 含义 |
|---|---|---|
| `depot_node_xy` | (B, n+1, 2) | depot 在 index 0，客户 1..n |
| `depot_node_demand` | (B, n+1) | depot 需求为 0 |
| `selected_count` | int | 已选步数 |
| `current_node` | (B, P) | 当前节点 |
| `load` | (B, P) | 当前载重 |
| `visited_ninf_flag` | (B, P, n+1) | 已访问掩码 |
| `ninf_mask` | (B, P, n+1) | **送进 decoder 的最终 mask**（-inf 表示禁止） |
| `finished` | (B, P) | 是否全部客户已服务 |

关键方法：

- `_get_encoding()`：只把 `depot_node_xy` 展平后交给 encoder。
  **→ 这就是"encoder 输入维度不得变"的原因：checkpoint 的 encoder 权重吃 2 维。**
- `_get_logit_mask(...)` / `_update_ninf_mask()`：把 `visited` 与容量超限的客户置 `-inf`，策略网络只在合法动作上取样。
- `step(selected)`：更新 load / visited / finished，`current_node = selected`。

### 1.3 网络（`CVRPModel.py`）

- `encoder`：3 层 MLP（`2 → 128 → 128 → 128`）+ RNN 式 multi-head attention（POMO 的 `POMO_Encoder`）。
- `decoder`：`context` = `graph_embedding` ⊕ `remaining_capacity` ⊕ `current_node_embedding`。
  **→ 新状态（current_time、时间窗余量）应当在这一层拼接，而不是进 encoder。**
- `pre_forward` 返回 `(log_p, prob, _)`，`prob` 已经过 mask，非法动作为 0。

### 1.4 训练/测试（`CVRPTrainer.py` / `CVRPTester.py`）

- 训练：每个实例采 `P` 条 rollout（POMO 多起点），用组内均值作共享基线，`loss = -(reward - baseline) * log_p`。
- 测试：`aug_factor=8` 增强 + greedy rollout，报 `best cost`。
- 现有入口只打印分数，**没有** 本流水线要求的文案契约。

## 2. 必须改造的点（CVRPTW）

| # | 位置 | 改动 | 不能动的东西 |
|---|---|---|---|
| 1 | `ProblemDef` | 生成 `time_windows` (n+1,2) 与 `service_times` (n+1,)，支持从 json 读实例 | 不要改 `get_random_problems` 的签名（训练时仍会用它做随机实例） |
| 2 | `Env.__init__/load_problems` | 接收并保存 `time_windows` / `service_times` | `depot_node_xy` 的形状与语义 |
| 3 | `Env` 动态状态 | 新增 `current_time` (B,P)；新增 `visited_ninf_flag` 之外的 `tw_ninf_flag` | `ninf_mask` 的最终语义与形状 (B,P,n+1) |
| 4 | `Env._get_encoding()` | **不动** | ← checkpoint 兼容的生命线 |
| 5 | `Model.pre_forward` | 把 `current_time`、`tw_slack`（窗开后余量/窗闭余量）拼到 decoder 的 context | decoder 的 hidden 维度必须保持 128（否则旧权重全废） |
| 6 | `Env.step` | 更新 `current_time = max(arrival, ready) + service`；到达晚于 due 视为非法 | — |
| 7 | reward | 与原 CVRP 一致：`-总路程`（含回 depot） | — |
| 8 | 入口脚本 | 打印 `the initial process is successful, the initial cost is {x}` 和 `the process is successful, the best cost is {x}` | 文案必须逐字符一致（流水线用正则提取） |

## 3. 时间语义（流水线会按这个校验，改动不得偏离）

- 时间 = 欧氏距离，**没有车速**。
- `arrive = current_time + dist(prev, node)`。
- 若 `arrive < tw[node][0]`：等待，`start = tw[node][0]`；成本不变，但时间被推迟。
- 若 `arrive > tw[node][1]`：**该动作非法**，必须被 mask。
- `current_time = start + service_time[node]`（depot 的 service_time 为 0）。
- 车辆可以随时回 depot：回 depot 的动作**永远合法**（这是避免 rollout 走进死路的兜底）。
- 回 depot 时把 `current_time` 重置为 0、`load` 重置为容量、载重相关 mask 重置。
- 终点：所有客户被访问后，所有 rollout 结束在 depot。

## 4. 实例文件格式（`instances/data/cvrptw/n20/inst_*.json`）

```json
{
  "name": "cvrptw_n20_00",
  "problem_size": 20,
  "capacity": 30.0,
  "horizon": 3.0,
  "depot_xy": [[0.5, 0.5]],
  "node_xy": [[x, y], ...],
  "node_demand": [0.0, d1, ..., d20],
  "time_windows": [[0.0, 3.0], [e1, l1], ...],
  "service_times": [0.0, 0.05, ...],
  "best_known_cost": null
}
```

- 数组长度都是 `n+1`，index 0 是 depot（demand=0，service=0）。
- 同时导出同名的 `.vrp`（人可读 / 给 analysis agent 看），字段与上面一一对应。
- `best_known_cost` 留给后续跑出更好解时回填，不参与评分。

## 5. 验收契约

生成代码通过的标准（`cvrp.executor.contract` 实现，`task.yaml:grader` 配置）：

1. `python -m py_compile` 全部通过；
2. 短跑（n=20, 少量迭代）`returncode == 0`；
3. 全跑 `returncode == 0` 且 stdout 命中：
   - `the process is successful, the best cost is <float>`
   - `the initial process is successful, the initial cost is <float>`
4. 通过 `cvrp.executor.feasibility` 的独立校验（时间窗/容量/单访问/depot 往返）。

## 6. AFL 参考实现的用法

`baseline/afl/` 是**参考**，不是依赖，也**不会被任何代码 import**。

> ⚠️ 这些文件里带有原作者硬编码的 DeepSeek API key（原样保留、未改动，便于与上游对照）。
> 本项目的运行时代码不使用它：`cvrp` 只通过已登录的 Claude Code CLI 调用模型，
> 不读取任何 key。若要长期保留该目录，建议先把 key 换成
> `os.getenv("DEEPSEEK_API_KEY")`，或直接删除 `baseline/afl/`。

对应关系：

| AFL | 本流水线 |
|---|---|
| `describe.py` `build_prompt_part1/2` | `src/cvrp/prompts/s1_analysis.md` |
| `describe_judgement.py` | `src/cvrp/prompts/s1_analysis_judge.md` |
| `code_generation.py` `build_prompt` | `src/cvrp/prompts/s2_codegen.md` |
| `code_judgement.py` | `src/cvrp/prompts/s2_codegen_judge.md` |
| `complete_code_revise.py` `code_analyse` | `src/cvrp/prompts/s4_error_analyse.md` |
| `complete_code_revise.py` `revise_prompt1` | `src/cvrp/prompts/s4_revise.md` |
| `main.py` 的 `run_generated_code` 正则 | `src/cvrp/executor/contract.py` |

AFL 里唯一值得原样继承的设计是 **"先生成、再 judge、judge 不过就拿 judge 意见重生成"**：
本流水线把它放在每个 agent 阶段内部（`Stage.internal_judge`），而不是只放在末尾。

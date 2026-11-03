# CVRPLTW analysis plan — 参考结构

analysis agent 的**唯一产物**是：

```text
artifacts/analysis/plan.md
```

pipeline 不解析它；code_generation / code_error / code_revise 会读它。下面是一个
合格的 plan 应覆盖的内容。`<...>` 由 agent 根据题目决定。

---

## 0. 结论

- 输入模式：`description` / `generator` / `description+generator`
- 最终规模：`problem_size = <n>`，`pomo_size = <n>`，`aug_factor = 8`
- `DIMENSION = <n+1>`
- 生成文件前缀：`<TAG>`
- 训练入口：`train_n<n>.py`
- 测试入口：`test_n<n>.py`
- 快速训练检查：`test_train.py`（由 code_error 生成，不由 codegen 生成）

## 1. 问题与约束

- 目标：最小化总行驶距离。
- C：每条路线 demand 之和 <= capacity。
- L：每条路线长度（含 depot 两端）<= `max_route_length`。
- TW：到达 <= due；早到等待；服务完成时间 =
  `max(arrival, ready) + service_time`。
- 每个客户恰好服务一次；路线从 depot 出发并回到 depot。
- 返回 depot 永远合法。

## 2. 实例与数据来源

- 字段：`problem_size`、`capacity`、`horizon`、`depot_xy`、`node_xy`、
  `node_demand`、`time_windows`、`service_times`、`max_route_length`。
- 输入模式判断依据：
  - `data/inputs/instance_gen/*.py` 存在 -> generator；
  - 只有 `*.md` -> description；
  - 都没有 -> 自己设计。
- 训练数据：POMO `ProblemDef.get_random_problems` 的扩展。
- 测试数据：`test_n` 使用 `test_data_load.enable = False` 随机生成，
  `test_episodes = 10*1000`。

## 3. 逐文件修改

- `<TAG>ProblemDef.py`：生成带 `max_route_length` / time windows 的实例。
- `<TAG>Env.py`：新增时钟、累计路线长度、剩余容量；新增 L 的 mask；
  TW 的 mask；depot 例外。
- `<TAG>Model.py`：encoder 输入保持坐标 + 需求 + 静态时间窗字段；
  动态时钟/长度进入 decoder context。
- `<TAG>Trainer.py`：保持 POMO 训练预算。
- `<TAG>Tester.py`：保持 POMO 测试预算，支持 `--model-path`。
- `train_n<n>.py`：POMO 的 8100 / 10*1000 参数。
- `test_n<n>.py`：`test_episodes = 10*1000`；
  `test_data_load.enable = False`；`--model-path`。
- `README.md`：如何训练/测试。

## 4. Mask / 状态

- `capacity_mask`：demand 超限。
- `time_mask`：`arrival > due`。
- `length_mask`：`current_length + dist(current, j) + dist(j, depot) >
  max_route_length`。
- depot 永远合法。
- 状态：`current_time`、`current_load`、`current_route_length`。

## 5. 训练 / 测试协议

```bash
cd artifacts/generated
python train_n<n>.py
python test_n<n>.py --model-path <checkpoint-1.pt>
```

`test_train.py` 由 code_error 生成：

```python
'epochs': 1
'train_episodes': 64
'train_batch_size': 64
```

## 6. 分数

`test_n` 的 stdout 最后出现：

```text
AUGMENTATION SCORE: <float>
```

pipeline 取最后一个匹配。`test_n` 和 `test_train` 的 exitcode 必须为 0。

## 7. 风险 / 未知

- 时间窗与长度上限的组合是否会让 depot 之外的客户全部不可达；
- `max_route_length` 的取值是否允许至少一条可行路线；
- 训练 1 epoch 是否能稳定产生 checkpoint；
- POMO 的 `test_n` 是否支持 `--model-path` 与随机测试数据。

# CVRPLTW 任务描述参考模板

这个目录给出一个可参考的 **CVRPLTW** problem bundle。它展示：

- `task.yaml` 里问题描述应该怎么写；
- `litellm_config.yaml` 应该放什么；
- 一个合格的 `plan.md` 应该覆盖哪些内容；
- 目录应该怎么放，命令怎么跑。

> 当前引擎的约束：`task.yaml` 的 `task:` 块只有
> `name` / `slug` / `description` / `tips`。不要写 `problem.dir`、`scale`、
> `generated`、`grader` 等字段。bundle 由目录 `problems/<slug>/` 识别。

---

## 1. 目录

```
problems/cvrpltw/
├─ task.yaml
├─ litellm_config.yaml
├─ grader/                 # 可选：checker / generator，只读输入
└─ instance_gen/           # 可选：生成器代码或生成文字描述，只读输入
```

没有 `context.md`。

---

## 2. 启动

```bash
# 真实 agent
uv run vrpar start problems/cvrpltw/task.yaml

# 离线检查配置与流水线（不需要模型）
uv run vrpar start problems/cvrpltw/task.yaml llm.backend=mock
```

`vrpar start` 不带参数会直接报错。

---

## 3. 流水线会检查什么

`code_error` 阶段必须留下：

```text
artifacts/generated/test_train.py
artifacts/runs/test_train.stdout.txt
artifacts/runs/test_train.stderr.txt
artifacts/runs/test_train.exitcode
artifacts/runs/test_n.stdout.txt
artifacts/runs/test_n.stderr.txt
artifacts/runs/test_n.exitcode
```

成功条件：

1. `test_train.py` 含 `epochs: 1` 和 `train_episodes: 64`；
2. 两个 exitcode 都是 0；
3. `test_n` stdout 出现 `AUGMENTATION SCORE: <float>`。

分数取最后一个 `AUGMENTATION SCORE:`。

`test_n<n>.py` 使用 POMO 的 `test_episodes = 10*1000`，
`test_data_load.enable = False`，自己随机生成测试数据，并通过
`--model-path` 加载 `test_train` 保存的 `checkpoint-1.pt`。

---

## 4. 描述问题的写法

建议按下面顺序写 `task.description`：

1. **一句话定义**：CVRPLTW 是什么（CVRPTW + 每路线长度上限）。
2. **实例**：depot、n 个客户、坐标、需求、容量、时间窗、服务时间、
   `max_route_length` 是每个实例自带的。
3. **约束**：逐条写成可检查的谓词，不要只写“要满足时间窗”。
4. **目标**：最小化总行驶距离。
5. **解的形式**：每条路线是客户索引序列，depot 隐含在两端。
6. **与 POMO 的关系**：适配 POMO 的五个文件，不换网络结构。
7. **测试协议**：`test_n` 随机测试数据，分数行是
   `AUGMENTATION SCORE`。

`tips` 只放补充约束，不要把完整题目重复一遍。

---

## 5. 文件

- [`cvrpltw_task.yaml`](cvrpltw_task.yaml) — 可直接参考/复制的 task 描述
- [`cvrpltw_litellm_config.yaml`](cvrpltw_litellm_config.yaml) — 环境与预算
- [`cvrpltw_plan_outline.md`](cvrpltw_plan_outline.md) — analysis 应产出的 plan 结构

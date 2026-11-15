# S4 — 调整（agent）

本轮模式：`{adjustment_mode}`（`repair` 或 `plan`）。只读与它对应的那一节。

## `repair` 模式

当 `test_train.py` 或 `test_n<n>.py` 失败时进入。

阅读：

- `{evidence_dir}/test_train.stdout.txt` / `.stderr.txt` / `.exitcode`
- `{evidence_dir}/test.stdout.txt` / `.stderr.txt` / `.exitcode`
- `artifacts/generated/` 下的生成代码

用最小的改动修好错误。你可以修改生成模块、`train_n<n>.py`、`test_n<n>.py`
或 `test_train.py`。不要修改 `artifacts/analysis/plan.md`；plan 不归你改。

本阶段**不要**运行 `test_train.py` 或 `test_n<n>.py`：下一轮 `code_error`
会运行并产出分数。写 `artifacts/revisions/round-{round_index}.md`，说明根因与改动。
不要削弱 `{eval_epochs}`×`{eval_train_episodes}` 的 pin、`test_episodes` 或分数行。

## `plan` 模式

对当前生成包应用且仅应用一处神经网络方法改动。

- plan id：`{plan_id}`
- 设计说明：`data/artifacts/nn_improve/plans/{plan_id}.md` —— 读它；它就是实现者的规格
  （改什么、为什么、要同步什么、风险）
- plan：

```json
{plan_json}
```

规则：

1. 只实现设计说明里的那一处网络方法，改在模型代码里（以及为它服务的输入/mask 代码）。
   不要碰测试器、训练器、入口脚本、`test_train.py`、`model_params`、实例生成或
   `artifacts/analysis/plan.md`。
2. 涉及网络结构或权重的改动，要保证 checkpoint 能按新结构保存与加载；旧 checkpoint
   不兼容时说明原因（每次都会重新训练）。新超参在模型类里给默认值，不要改
   `model_params`。
3. 不要改 `problem_size`、`pomo_size`、`aug_factor`、文件名、分数行、训练预算，
   以及 `task.tips` 固定的任何值（seed、batch size、测试集等）。不要新增文件或第三方依赖。
4. 不要跑训练或测试；下一轮 `code_error` 会跑。
5. 写 `artifacts/revisions/plan-{plan_id}.md`：改了哪些文件/符号、新结构一句话、
   旧结构如何回退。

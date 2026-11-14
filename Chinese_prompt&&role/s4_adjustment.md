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

对当前生成包应用且仅应用一个参数 plan。

- plan id：`{plan_id}`
- plan：

```json
{plan_json}
```

规则：

1. 只改 `changes` 说的那一处。不要"顺手优化"无关代码，不要改文件名，不要碰
   `artifacts/analysis/plan.md`。
2. 模型超参通常同时出现在 `train_n<n>.py` 和 `test_n<n>.py`（`model_params`）。
   两处必须改成同一个值，否则 checkpoint 与 tester 在 `load_state_dict` 上对不上。
3. 不要改 `problem_size`、`pomo_size`、`aug_factor`、文件名、分数行、训练预算，
   以及 `task.tips` 固定的任何值（seed、batch size、测试集等）。
4. 不要跑训练或测试；下一轮 `code_error` 会跑。
5. 写 `artifacts/revisions/plan-{plan_id}.md`：文件、符号、旧值、新值，以及一行原因。

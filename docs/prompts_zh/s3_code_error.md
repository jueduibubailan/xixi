# S3 — 代码错误 / 快速训练与测试（agent）

阅读 `artifacts/analysis/plan.md` 和 `artifacts/generated/` 下生成的包。

你的任务：生成**一个**文件 `artifacts/generated/test_train.py`，运行它，
然后用它产出的 checkpoint 运行 code_generation 写好的最终测试入口。

在 `artifacts/generated/` 下找到最终测试入口：那个**不是** `test_train.py` 的 `test_*.py`。
名字来自 plan（约定是 `test_n<n>.py`，例如 `test_n50.py`）；不要假定它就是字面的 `test_n`。

本轮的评测协议是 `{eval_protocol}`：`acceptance` 使用 `{eval_epochs}` 轮 ×
`{eval_train_episodes}` 个 episode。这两个数字来自流水线，覆盖其它任何地方
（包括 `task.tips`）写的预算。

## `test_train.py`

- 导入生成的 Trainer（例如 `from <TAG>Trainer import <TrainerClass>`）。
- 复用生成的 `train_n<n>.py` 参数，但覆盖：

      'epochs': {eval_epochs}
      'train_episodes': {eval_train_episodes}
      'train_batch_size': 64

- 只覆盖这三个键；不要重建参数字典，这样训练入口的 seed 与优化器设置才能保留。
- 保持 POMO 的设备链；遵循 `VRPAR_FORCE_CPU`。
- 运行 trainer。POMO 会在它的结果目录下保存 `checkpoint-{eval_epochs}.pt`。
- 结束时打印 `QUICK TRAIN OK`。

不要修改其它生成文件。如果 `test_train.py` 自身有 bug，只修 `test_train.py`。

## 运行与捕获

在 `artifacts/generated/` 下运行，把输出写进绝对路径的报告目录 `{report_dir}`
（不存在就创建）：

    python test_train.py > {report_dir}/test_train.stdout.txt 2> {report_dir}/test_train.stderr.txt

然后写退出码（bash 与 PowerShell 都接受）：

    echo $? > {report_dir}/test_train.exitcode          # bash
    $LASTEXITCODE | Out-File -Encoding ascii {report_dir}/test_train.exitcode   # PowerShell

写完回读该文件，确认里面只有一个整数。

然后找到 `checkpoint-{eval_epochs}.pt`（在 POMO 的结果目录），运行真正的最终测试入口，例如：

    python test_n50.py --model-path <checkpoint-{eval_epochs}.pt> > {report_dir}/test.stdout.txt 2> {report_dir}/test.stderr.txt
    echo $? > {report_dir}/test.exitcode

用你实际找到的文件；不要运行字面的 `test_n.py`。

用生成代码实际实现的测试数据设置来运行测试入口；**不要假定是随机数据，
也不要假定是固定集合**：按 `plan.md` 走，当 `task.tips` 指定了测试集时按 tips 走。
它的 stdout 以这一行结尾：

    AUGMENTATION SCORE: <float>

不要手写分数。流水线读的是 stdout 文件。

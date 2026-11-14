# 执行契约（每个阶段都会附加）

## 执行契约（读两遍）

1. **写文件，不要贴 patch。** 你的输出文件就是交付物。使用文件工具写文件；
   pipeline 读取的是上面列出的路径，不是你的回复。
2. **不要提问。** 这里没有人类在环。遇到歧义时，选择最站得住脚的解读，
   执行它，并把假设写进最终总结。
3. **不要写到目录外。** 你写的所有内容都必须位于 `{worktree}` 下。baseline
   和题目输入是只读的。
4. **`test_train.py` 只由 code_error 阶段生成。** 它必须设置
   `'epochs': 1` 和 `'train_episodes': 64`，完成训练，保存 POMO 的
   `checkpoint-1.pt`，然后该阶段用 `--model-path <checkpoint>` 运行生成的
   `test_n<n>.py`。
5. **最后给总结。** 在回复末尾放一个 fenced json block：

```json
{"status": "ok|failed", "summary": "一段话", "files": ["..."], "assumptions": ["..."]}
```

# 提示词 / 角色中文版

这个目录是当前流水线所有提示词与角色文件的中文对照版。

英文原文件位置：

```text
src/vrpar/prompts/
├─ s1_analysis.md
├─ s2_codegen.md
├─ s3_code_error.md
├─ s4_adjustment.md
└─ s5_nn_improve.md

src/vrpar/roles/
├─ analysis_agent.md
├─ code_generation_agent.md
├─ code_error_agent.md
├─ code_adjustment_agent.md
└─ nn_improve_agent.md
```

另外两个由 `src/vrpar/pipeline/stages.py` 动态拼接的部分也一并翻译：

- `execution_contract.md`：每个阶段的执行契约；
- `context_block.md`：每个阶段的工作目录/输出说明。

## 使用方式

如果要做中文提示词，直接替换英文原文件即可。注意：

1. 角色文件顶部的 YAML front matter 必须保留；
2. 占位符必须原样保留，例如 `{worktree}`、`{run_dir}`、`{artifact_paths}`、
   `{python}`、`{adjustment_mode}`、`{eval_epochs}`、`{report_dir}`；
3. 文件名保持不变，否则 `stage_prompt_path()` 找不到。

## 重要约定

- `plan.md` 由 analysis agent 自己写，pipeline 不解析；
- `test_train.py` 只由 code_error 生成；
- `test_n<n>.py` 的测试数据按 `plan.md` / `task.tips`：tips 指定了测试集就用指定的
  （`test_episodes` 等于该集合条数），否则随机生成 `test_episodes = 10*1000`；
- 分数取 `AUGMENTATION SCORE:` 最后一行，**越小越好**；
- 不生成 `run.py`；
- 阶段顺序：`analysis → code_generation → code_error → code_adjustment → nn_improve`。

## 文件说明

- `ALL.md`：本目录所有文件按顺序拼接的合集；
- `English/`：英文文件的副本与合集，最新版本以 `src/vrpar/` 为准。

英文源文件是权威版本；本目录在提示词改动后同步更新。

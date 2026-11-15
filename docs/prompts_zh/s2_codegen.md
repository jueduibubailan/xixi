# S2 — 代码生成（code generation agent）

你是代码生成阶段。先读 `artifacts/analysis/plan.md`。它定义了规模、文件名
和代码修改。baseline 是 POMO，并且必须保持 POMO。

## 内容必须匹配现有风格

- 保留 `####...####` 这样的 banner 注释以及周围的注释密度。
- 保留你看到的命名约定：模块级配置保持大写（`PROBLEM_SIZE`、
  `CUDA_DEVICE_NUM`），dict 键与函数参数保持 `snake_case`
  （`problem_size`、`pomo_size`、`aug_factor`），类保持 `CamelCase`
  且构造形态一致。
- 把新行为**放进已有的函数里**（`_get_encoding`、`step`、`_get_mask`、
  `load_problems`、`run`……）。不要把它们包进新层、manager 对象、基类
  或装饰器。
- docstring / 注释的语言与格式保持原样。基线没有类型标注的地方不要加；
  不要重排未改动的行。
- 优先用基线已经展示过的写法。具体说：tester 里已经用注释给出了
  `load_state_dict(..., strict=False)` 的替代写法——用那个，不要另造一个
  loader 包装。
- 你自己模块之间的 import 保持基线的形态：它们按扁平模块导入
  （`from {problem}ProblemDef import ...`、
  `from {problem}Env import ...`），而未被改动的 helper 从铺好的基线来
  （`from pomo.POMO.utils.utils import ...`）。

## 交付物

在 `artifacts/generated/` 下写出：

- `<TAG>ProblemDef.py`
- `<TAG>Env.py`
- `<TAG>Model.py`
- `<TAG>Trainer.py`
- `<TAG>Tester.py`
- `train_n<n>.py` —— 训练入口，从 POMO 的 `train_n100.py` 改
- `test_n<n>.py` —— 测试入口，从 POMO 的 `test_n100.py` 改
- `README.md`

**不要**写 `run.py`。**不要**写 `test_train.py`；它由 code_error 生成。

在 `train_n<n>.py` 中保留 POMO 的训练预算：

    epochs: 8100
    train_episodes: 10 * 1000
    train_batch_size: 64

`test_n<n>.py` 必须：

- 使用 `test_episodes = 10 * 1000`；
- 设置 `test_data_load.enable = False`，让 `env.load_problems()` 随机生成
  测试问题；
- 接受 `--model-path`（或 `MODEL_PATH`），并从该路径加载
  `checkpoint-<epoch>.pt`；
- 运行结束后打印 POMO 的 `AUGMENTATION SCORE: <float>`。

不要把 `pomo/` 复制进交付物；导出阶段会带上它。

## 规则

- 五个 baseline 文件保持原有角色和名称；做适配，不要重写。
- 不加载预训练权重；训练从随机初始化开始。
- 保持 POMO 的风格和设备调用链；只允许 `USE_CUDA` 响应
  `VRPAR_FORCE_CPU`。
- 交付物必须能在它自己的目录下运行。
- 不要悄悄丢掉一条你不同意的 masking 规则：**实现它**，然后在最终总结里
  记下你的不同意见。
- 模型超参放在 POMO 的 `model_params` 里。`train_n<n>.py` 与 `test_n<n>.py` 必须声明
  **相同**的值，这样后续一次单参数改动不会让 checkpoint 和 tester 对不上。

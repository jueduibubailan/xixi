# baseline 说明

本 bundle 不使用共享 `baseline/pomo` 之外的额外种子内容：生成的代码会从
`baseline/pomo`（由 `task.yaml` 之外的引擎默认值提供）复制 POMO 骨架，再改造成
SSTRPVST 的 encoder/decoder。

参考解不随 bundle 提供。要算 gap，请在同一份
`grader/data/sstrp_n25_uniform.pkl` 上自行求解：
- $n\le 25$：用 Gurobi 解论文模型 (1)–(30)（时限 4 h）得最优；
- 更大规模：实现论文的贪心插入 + ALNS，或直接用本 bundle 的 POMO 解做相对比较。

注意：论文表 3 的数字来自**未公开**的实例，不可直接作为本 bundle 的基准。

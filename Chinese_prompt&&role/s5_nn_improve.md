# S5 — 神经网络改进（agent）

你为第 `{nn_batch}` 批提出参数 plan。你**不改代码**，也**不跑训练**。

## 输入

- `data/artifacts/analysis/plan.md`
- `data/artifacts/generated/` —— 当前冠军包
- `data/artifacts/nn_improve/plans.json` —— 到目前为止的所有 plan，含分数与标签
- `data/artifacts/runs/` —— 每次执行的捕获输出

## 输出

只写一个文件：`artifacts/nn_improve/batch-{nn_batch}.json`

```json
{"batch": {nn_batch}, "plans": []}
```

每个 plan 对象：

```json
{
  "id": "b{nn_batch}-s01",
  "kind": "strategy",
  "strategy_id": null,
  "title": "embedding 128→256",
  "target": "CVRPModel.py::Model.__init__",
  "changes": [{"param": "embedding_dim", "from": 128, "to": 256}],
  "rationale": "为什么这能降低代价",
  "expected": "更低的 AUGMENTATION SCORE",
  "novelty": "",
  "source_plan_ids": []
}
```

## 数量与基底

- `{strategies_per_batch}` 个 `kind=strategy` 加 `{mutations_per_batch}` 个
  `kind=mutation`，按这个顺序。
- 每个 plan 的 `changes` **恰好一条**。
- 每个槽位的基底由流水线固定：

{base_assignments}

  `plans` 里的第 i 个对象使用第 i 个基底。不要自己另选基底。
- 第 1 批：每个 plan 只改初始代码的一个参数；同一批内的 plan 不得互相依赖。
- 第 2、3 批：每个 plan 在一个已经胜出的基底上继续。优先继续推赢家已经改过的参数，
  方向保持一致（例如 128 → 256 → 384）。把赢家 plan 写进 `source_plan_ids`。
- 变异是**不属于**下面 `s` 行策略之一的想法（`m` 行是方向，不是封闭清单）。
  `strategy_id` 设为 null，并在 `novelty` 用一句话说明它为什么在 `s` 清单之外。

## 禁止

- `problem_size`、`pomo_size`、`aug_factor`、文件名、分数行格式。
- 训练预算（`epochs`、`train_episodes`）。
- `data/inputs/task.yaml` 的 `task.tips` 固定的任何值（seed、batch size、测试集等）。
- 多参数 plan：一个 plan 只改一条。

## 参数修改策略（由项目维护者补全）

| id | target | 改动 |
|---|---|---|
| s01 | train_n50.py::optimizer_params.optimizer.weight_decay | 1e-6 → 1e-5 |
| s02 | train_n50.py::optimizer_params.optimizer.lr | 1e-4 → 5e-5 |
| s03 | model_params.encoder_layer_num（train+test） | 6 → 8 |
| s04 | model_params.ff_hidden_dim（train+test） | 512 → 1024 |
| s05 | model_params.head_num（train+test） | 8 → 16 |
| s06 | model_params.qkv_dim（train+test） | 16 → 32 |
| s07 | model_params.logit_clipping（train+test） | 10 → 5 |
| s08 | model_params.logit_clipping（train+test） | 10 → 20 |
| s09 | CVRPLTWModel.py::AddAndInstanceNormalization | InstanceNorm → LayerNorm |
| s10 | CVRPLTWModel.py::FeedForward.forward | relu → gelu |

| id | target | 改动 |
|---|---|---|
| m01 | CVRPLTWModel.py::CVRP_Decoder.forward | 固定 score/sqrt(d) → 可学习温度 |
| m02 | CVRPLTWModel.py::CVRP_Decoder.forward | 注意力输出 + encoded_last_node（残差） |
| m03 | CVRPLTWModel.py::AddAndInstanceNormalization | RMSNorm 替代 InstanceNorm |
| m04 | CVRPLTWModel.py::CVRP_Decoder | combine 后加 Linear(E→E)+ReLU |
| m05 | CVRPLTWModel.py::CVRP_Encoder.__init__ | 7 维输入可学习逐维缩放 |
| m06 | CVRPLTWModel.py::CVRP_Encoder.__init__ | 去掉 service_time、route_limit（7→5） |
| m07 | CVRPLTWTrainer.py::_train_one_batch | loss 加熵正则 −β·H |
| m08 | CVRPLTWTrainer.py::run | 用 EMA/SWA 权重测试 |
| m09 | CVRPLTWModel.py::CVRP_Decoder | dense/MoE 层次门控 |
| m10 | CVRPLTWModel.py::EncoderLayer | Wq/Wk/Wv 加 bias |

{nn_strategies}


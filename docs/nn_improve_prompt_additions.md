# 提示词增加方案 v5：调参表 + 变异表（同格式，无「预期」列）

> ⚠ 历史文档：这里的两张表已从 `s5_nn_improve.md` 删除，人类维护的策略清单不再使用；
> 现在由 agent 自己设计方法，每个 plan 一个 `artifacts/nn_improve/plans/<id>.md`。
> 本文件仅存档。

> 范围：只改 `src/vrpar/prompts/s5_nn_improve.md` 的最后一节
> `## 参数修改策略（由项目维护者补全）`。
> 其它提示词、角色、s4、s1 都不动；只放两张同格式的表，不写说明文字。

---

## 1. 替换后的整节内容

````markdown
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
````

- 第一张表是调参（`s01`–`s10`），第二张表是变异方向（`m01`–`m10`），格式相同。
- `（train+test）` 表示 `train_n50.py` 与 `test_n50.py` 的 `model_params` 要同步改。
- 末尾只留一行 `{nn_strategies}`，作为你以后用 `strategies_file` 追加的入口；
  不配时渲染为空。

---

## 2. 需要同步的两处小代码

### 2.1 `src/vrpar/pipeline/stages.py`

```python
"nn_strategies": "（暂未提供，按你的判断提出修改。）",
```

改成：

```python
"nn_strategies": "",
```

### 2.2 `src/vrpar/pipeline/orchestrator.py::_strategies_text`

真实运行时 `{nn_strategies}` 由这个方法提供。没配 `strategies_file` 时它原本也返回
"（暂未提供…）"，会盖掉上面的空串；改为返回 `""`：

```python
if not raw:
    return ""
...
return ""
```

两处都改完，表后不会再多出一句。

---

## 3. 同步中文包

`Chinese_prompt&&role/s5_nn_improve.md` 的最后一节替换成同样两张表；
`ALL.md` 用现有脚本重新拼接。

---

## 4. 不改的东西

- `s5` 的其它段落（Inputs / Output / Forbidden / How many…）；
- `s4_adjustment.md`、`nn_improve_agent.md`、`analysis_agent.md`、`s1_analysis.md`；
- `plans.py` 的 schema（`target` 仍是字符串，两处 `model_params` 用 `+` 连写）；
- `strategies_file` 机制保留。

---

## 5. 确认后做的事

1. 替换 `s5_nn_improve.md` 最后一节；
2. `stages.py` 的 `nn_strategies` 默认值改 `""`；
3. 同步 `Chinese_prompt&&role/s5_nn_improve.md` 与 `ALL.md`；
4. 跑 `pytest -q` + 一次离线 e2e，确认渲染后没有占位符残留、两张表能进 prompt。

---

## 6. 已执行记录

- `src/vrpar/prompts/s5_nn_improve.md`：最后一节换成两张表；另把上面那句
  "A mutation is an idea that is **not** in the strategy catalogue below" 改写为
  "not one of the `s` strategies below (the `m` rows are directions…)"，
  否则与 `m` 表自相矛盾。
- `src/vrpar/pipeline/stages.py`：`nn_strategies` 默认值改为 `""`。
- `src/vrpar/pipeline/orchestrator.py::_strategies_text`：未配置 `strategies_file`
  时返回 `""`。
- `Chinese_prompt&&role/s5_nn_improve.md` 与 `English/s5_nn_improve.md`：同步；
  两个 `ALL.md` 重新拼接。
- 验证：`pytest -q` → 77 passed；实际 e2e 的 `worktrees/nn_improve/VRPAR.md`
  里能看到两张表、无"暂未提供"、无残留 `{nn_*}` 占位符。

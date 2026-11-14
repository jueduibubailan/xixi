# NN improve 调参方案与变异方案

> 依据：
> - `D:\PyProject\Routing-MVMoE-main`（MVMoE，多任务 MoE 求解器）
> - `new/workspace/final/cvrpltw_reference/artifacts/generated`（本流水线生成的 CVRPLTW n50 包）
>
> 目标：给 `nn_improve` agent 一份可执行的策略清单（10 条/批）和变异清单（2 条/批）。
> 所有 `target` 都写成 `文件::符号`，方便 `code_adjustment` 精确落地。

---

## 1. 两个网络的对比

| 维度 | MVMoE | 生成包（CVRPLTW-on-POMO） |
|---|---|---|
| 任务 | 多任务，16 个 VRP 变体 | 单任务 CVRPLTW n=50 |
| 编码器 | 6 层 MHA + **MoE FFN** | 6 层 MHA + 普通 FFN |
| FFN / 专家 | 4 个 expert，`topk=2`，noisy gating，`T=1.0`，MLP(hidden=512, ReLU) | 单个 `FeedForward`：Linear→ReLU→Linear，hidden=512 |
| MoE 位置 | 可配 `expert_loc`：`Enc0..Enc5`、`Dec`；decoder 可用 MoE 或 dense | 无 |
| 路由 | `routing_level` = node / instance / problem；`routing_method` = input_choice / expert_choice / soft_moe / random；Light 版还有 hierarchical gating | 无 |
| 辅助损失 | `aux_loss`（`cv_squared` load balancing，默认系数 1e-2）加到 REINFORCE loss | 无，只有 `advantage * log_prob` |
| 归一化 | `norm` = batch / batch_no_track / instance / layer / rezero / none；`norm_loc` = norm_first / norm_last | 写死 `InstanceNorm1d(affine=True)`，post-LN |
| 输入特征 | depot 2 维；node 5 维（xy, demand, tw_start, tw_end） | depot 7 维；node 7 维（xy, demand, tw_start/3, tw_end/3, service/3, route_limit/3） |
| Decoder query | `Wq_last(embedding + 4)`：load, current_time, length, open | `Wq_last(embedding + 3)`：load, current_time/3, length/3 |
| 打分 | single-head key + `sqrt(embedding_dim)` 缩放 + `tanh` clip 10 | 完全相同 |
| 规模 | 128 / 6 层 / 8 头 / qkv 16 / ff 512 | **完全相同** |
| 训练 | POMO REINFORCE + MoE aux；Adam `1e-4`，wd `1e-6`，MultiStepLR `[4501]` | POMO REINFORCE；Adam `1e-4`，wd `1e-6`，MultiStepLR `[8001, 8051]` |
| 测试 | 固定 pkl，aug 8，argmax | 固定 pkl（tips 指定），aug 8，argmax，1000 条 |

**结论**：生成包在骨架、维度、解码器打分上与 MVMoE 的同源部分几乎一致；差别集中在
**FFN 是否 MoE、是否有辅助损失、归一化是否可配、训练调度**。对单任务 CVRPLTW，
MoE 的收益通常来自多任务共享，单任务早期不建议直接上；先做"参数级"的搜索性价比最高。

---

## 2. 读代码时发现的三点

### 2.1 LR 调度在 200 epoch 下不会衰减（**不改，按你的要求**）

`train_n50.py` 是 `epochs: 200`，但 `scheduler.milestones` 是 `[8001, 8051]`（POMO 8100-epoch 的配置）。
`MultiStepLR` 在 200 epoch 内不会触发衰减。

这条**只记录、不进入策略清单**：`train_n50.py::optimizer_params.scheduler.milestones` 保持不变。
下面的所有策略都不碰 `scheduler.*`。

### 2.2 两个输入维度几乎是常数

7 维输入里，`service_time` 恒为 0.2、`route_limit` 恒为 3.0（每个实例都一样），
`route_limit` 还被逐节点复制。它们占了 2/7 的输入宽度，但几乎不携带信息。
可以做一个"去掉这两维"的变异版本来验证。

### 2.3 `test_train.py` 的 200/5000 是硬编码

导出的这份 `test_train.py` 把 `epochs/train_episodes/checkpoint-200.pt` 写死了。
新版流水线的 `s3` 提示词已经改成按 `{eval_epochs}` 渲染，下一次跑会自动适配；
这份历史包不用改，但做 plan 时别把它当模板。

---

### 2.4 这些参数/改动在网络里的位置

| 参数/改动 | 代码位置 | 在算什么 | 改了会怎样 |
|---|---|---|---|
| `weight_decay` | `CVRPLTWTrainer` 的 Adam | 每步把权重往 0 拉一点（L2） | 正则更强，抑制过大权重 |
| `lr` | 同上 | 每步更新幅度 | 更小更稳，但 200 epoch 可能更慢 |
| `encoder_layer_num` | `CVRP_Encoder.layers` | 堆几个 Transformer block（MHA + FFN + 两次 Add&Norm） | 更深、容量更大、更慢 |
| `ff_hidden_dim` | `FeedForward` 的 W1/W2 | 每个 block 的 MLP 宽度（E→ff→E） | 更宽、参数更多 |
| `head_num` | `EncoderLayer`/`CVRP_Decoder` 的 Wq/Wk/Wv | 注意力分几个头 | 更多子空间，总宽 = head×qkv |
| `qkv_dim` | 同上 | 每头多少维 | 每头更宽，总宽 = head×qkv |
| `logit_clipping` | `CVRP_Decoder.forward` | `clip * tanh(score/√E)` 的幅值上限 | 小 → 分布更平；大 → 更尖 |
| `norm` 类型 | `AddAndInstanceNormalization` | 对哪个维度做归一化 | InstanceNorm 跨节点；LayerNorm 跨特征 |
| 激活函数 | `FeedForward.forward` | 隐层 `F.relu` | GELU 更平滑，ReLU 更稀疏 |

**形状相容性**：`encoder_layer_num`、`ff_hidden_dim`、`head_num`、`qkv_dim`、`logit_clipping`
都写在 `train_n50.py` 与 `test_n50.py` 的 `model_params` 里，checkpoint 的权重形状由它们决定；
只改一处会导致 `load_state_dict` 报错。所以这几条策略都要求"两处同步"。

---

## 3. 策略清单（已内置在 `s5_nn_improve.md`）

下面的表**已经写进** `src/vrpar/prompts/s5_nn_improve.md` 的最后一节，不需要再建
`strategies_file`。本节保留为"为什么这么选"的说明；如果你要替换或追加清单，
再使用 `nn_improve.strategies_file`（内容会追加在 `{nn_strategies}` 处）。

### 3.1 第 1 批：10 个 strategy（按优先级）

| id | target | 改动 | 为什么 | 预期 | 风险 |
|---|---|---|---|---|---|
| `s01` | `train_n50.py::optimizer_params.optimizer.weight_decay` | `1e-6` → `1e-5` | 正则化，抑制过拟合 | 小幅提升，最稳的一条 | 过强会欠拟合 |
| `s02` | `train_n50.py::optimizer_params.optimizer.lr` | `1e-4` → `5e-5` | 降低后期震荡 | 小幅提升 | 200 epoch 内可能学得慢 |
| `s03` | `train_n50.py::model_params.encoder_layer_num`（`test_n50.py` 同步） | `6` → `8` | 增加编码器深度，n=50 的时空约束更复杂 | +0.5%～1% | 参数/显存 +33%，更慢 |
| `s04` | `train_n50.py::model_params.ff_hidden_dim`（`test_n50.py` 同步） | `512` → `1024` | FFN 容量翻倍 | +0.3%～0.8% | 显存上升 |
| `s05` | `train_n50.py::model_params.head_num`（`test_n50.py` 同步） | `8` → `16` | 更多注意力头，qkv 总宽 128→256，combine 回 128 | 小幅提升 | 头多但 qkv 窄，可能退化 |
| `s06` | `train_n50.py::model_params.qkv_dim`（`test_n50.py` 同步） | `16` → `32` | 每个头更宽，总宽 256 | 小幅提升 | 与 s05 同向，别同时上 |
| `s07` | `train_n50.py::model_params.logit_clipping`（`test_n50.py` 同步） | `10` → `5` | 更强的 logit 饱和，减少极端选择 | 可能更稳 | 可能限制表达 |
| `s08` | `train_n50.py::model_params.logit_clipping`（`test_n50.py` 同步） | `10` → `20` | 更弱的饱和，选择更尖锐 | 与 s07 反向对照 | 训练可能不稳 |
| `s09` | `CVRPLTWModel.py::AddAndInstanceNormalization.__init__` | `InstanceNorm1d` → `LayerNorm`（归一化维度从 N 改到 E） | LayerNorm 在 RL 里通常更稳 | 中等提升 | 需改 transpose 逻辑，写错会全崩 |
| `s10` | `CVRPLTWModel.py::FeedForward.forward` | `F.relu` → `F.gelu` | 更平滑的梯度 | 小幅 | 几乎无 |

> 说明：`s03`–`s08` 都同时存在于 `train_n50.py` 与 `test_n50.py` 的 `model_params`，
> 必须两处改成同一个值（新版 `s4` 提示词已明确这条），否则 checkpoint 与 tester 对不上。

### 3.2 第 2/3 批的候选（不放进第 1 批）

| id | target | 改动 | 为什么 |
|---|---|---|---|
| `s11` | `train_n50.py::model_params.sqrt_embedding_dim`（两处） | `128**0.5 ≈ 11.31` → `8.0` | 收紧 logit 尺度，等价于降低解码温度 |
| `s12` | `train_n50.py::model_params.sqrt_embedding_dim`（两处） | `11.31` → `16.0` | 与 s11 反向，放松 logit 尺度 |
| `s13` | `CVRPLTWModel.py::EncoderLayer.forward` | 由 post-LN 改为 pre-LN（`norm_loc=norm_first` 的写法） | 更深的网络里 pre-LN 通常更稳 |
| `s14` | `CVRPLTWModel.py::CVRP_Decoder.__init__.Wq_last` | 输入维度 `embedding+3` → `embedding+4`，把 `route_limit` 作为 query 属性 | 把路线长度上限显式喂给解码器（当前只在节点特征里） |
| `s15` | `train_n50.py::optimizer_params.optimizer.weight_decay` | `1e-5` → `1e-4` | 如果 s01 赢了，沿同方向继续推 |
| `s16` | `train_n50.py::optimizer_params.optimizer.lr` | `5e-5` → `2e-5` | 如果 s02 赢了，沿同方向继续推 |

第 2/3 批的实际内容由赢家决定：例如 `s01` + `s03` 都赢，就沿"更强正则 + 更深的编码器"继续推
（`weight_decay 1e-5→1e-4`、`encoder_layer_num 8→10` 等）。

---

## 4. 变异方案（不在策略清单里，2 条/批）

变异是"清单之外的思路"，必须写 `novelty` 说明。下面按风险从低到高排：

| id | target | 改动 | 为什么 | 风险 |
|---|---|---|---|---|
| `m01` | `CVRPLTWModel.py::CVRP_Decoder.forward` | 把固定的 `score / sqrt(embedding_dim)` 换成可学习的 `nn.Parameter` 温度 | 让模型自己学 logit 尺度，替代人工常数 | 低 |
| `m02` | `CVRPLTWModel.py::CVRP_Decoder.forward` | `mh_atten_out = mh_atten_out + encoded_last_node`（残差） | 解码器多一条恒等通路，缓解深网退化 | 低 |
| `m03` | `CVRPLTWModel.py::AddAndInstanceNormalization.__init__` | 用 RMSNorm 替代 InstanceNorm | 更轻、更稳 | 中 |
| `m04` | `CVRPLTWModel.py::CVRP_Decoder.__init__/forward` | combine 之后加一层 `Linear(embedding→embedding)+ReLU` 再算单头注意力 | 增强解码器非线性 | 中 |
| `m05` | `CVRPLTWModel.py::CVRP_Encoder.__init__` | 给 7 维输入加一个可学习的 `nn.Parameter(7)` 逐维缩放 | 让模型自己决定哪些特征重要 | 低 |
| `m06` | `CVRPLTWModel.py::CVRP_Encoder.__init__` | 去掉 `service_time`、`route_limit` 两维（7→5），与 MVMoE 的输入对齐 | 这两维几乎是常数，减参 | 中 |
| `m07` | `CVRPLTWTrainer.py::_train_one_batch` | loss 加熵正则 `- beta * entropy`（beta 1e-3） | 鼓励探索，防早熟 | 中 |
| `m08` | `CVRPLTWTrainer.py::run` | 训练后期对权重做 EMA/SWA 再用 EMA 权重测试 | 平滑权重，通常 +0.2%～0.5% | 中 |
| `m09` | `CVRPLTWModel.py::CVRP_Decoder` | 借鉴 MVMoE-Light 的 hierarchical gating：dense 与一个小 MoE 二选一 | 引入条件计算 | 高（接近重写，违背"adapt"约束，慎用） |
| `m10` | `CVRPLTWModel.py::EncoderLayer` | 给 Wq/Wk/Wv 加 bias | 极小的表达增强 | 低 |

**每批建议的 2 个变异**：
- 第 1 批：`m01`（温度）+ `m02`（解码器残差）——都在解码器，风险低、可解释；
- 第 2 批：`m05`（特征缩放）+ `m06`（去掉常数维）——都是输入侧，和策略侧的深度/宽度互补；
- 第 3 批：`m07`（熵正则）+ `m08`（EMA）——训练侧，用来榨最后一点。

---

## 5. 不应该动的项

- `problem_size` / `pomo_size` / `aug_factor`：改了分数不可比，且 `pomo_size` 牵连 POMO 多起点；
- `optimizer_params.scheduler.*`：**按你的要求保持不变**（含 `milestones` 与 `gamma`）；
- `train_batch_size`（tips 固定 64）、`test_batch_size`（tips 固定 1000）；
- 训练/测试 seed（tips 固定 2023 / 2024）；
- 测试集（tips 指定 `vrpltw50_uniform.pkl`）；
- 文件名、分数行格式、训练预算（`epochs`/`train_episodes` 由协议给）。

---

## 6. 怎么用

1. 把 §3.1 的表保存成 `problems/cvrpltw_reference/nn_strategies.md`；
2. 在 bundle 的 `litellm_config.yaml` 加：

   ```yaml
   nn_improve:
     strategies_file: "problems/cvrpltw_reference/nn_strategies.md"
     strict_strategies: false   # 先宽松；清单稳定后可改 true
   ```

3. 跑一次小预算冒烟（见 `docs/nn_improve.md` §8），确认 agent 能把表里的 `target` 转成
   `changes`，并且 `code_adjustment` 只改一处；
4. 第 2 批开始，流水线会把 label=1 的节点按分数升序分配给新 plan，agent 应沿赢家方向继续。

---

## 7. 一句话总结

生成包和 MVMoE 的骨架/维度几乎一样，差在 **MoE FFN、辅助损失、归一化可配性、训练调度**。
对单任务 CVRPLTW，先做 **weight_decay / lr / 深度 / 宽度 / 归一化** 的参数搜索，
比直接上 MoE 划算；`scheduler.*` 按要求保持原样；MoE 类改动只放在变异里、且要评估"是否还算 adapt POMO"。

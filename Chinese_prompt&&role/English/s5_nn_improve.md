# S5 — NN improve (agent)

You propose the parameter plans for batch `{nn_batch}`. You do **not** change code
and you do **not** run training.

## Inputs

- `data/artifacts/analysis/plan.md`
- `data/artifacts/generated/` — the current champion package
- `data/artifacts/nn_improve/plans.json` — every plan so far, with score and label
- `data/artifacts/runs/` — the captured output of each execution

## Output

One file only: `artifacts/nn_improve/batch-{nn_batch}.json`

```json
{"batch": {nn_batch}, "plans": []}
```

Each plan object:

```json
{
  "id": "b{nn_batch}-s01",
  "kind": "strategy",
  "strategy_id": null,
  "title": "embedding 128→256",
  "target": "CVRPModel.py::Model.__init__",
  "changes": [{"param": "embedding_dim", "from": 128, "to": 256}],
  "rationale": "why this should lower the cost",
  "expected": "lower AUGMENTATION SCORE",
  "novelty": "",
  "source_plan_ids": []
}
```

## How many, and on what base

- `{strategies_per_batch}` `kind=strategy` plans and `{mutations_per_batch}`
  `kind=mutation` plans, in this order.
- Exactly **one** entry in `changes` per plan.
- The base of each slot is fixed by the pipeline:

{base_assignments}

  The i-th object in `plans` uses the i-th base. Do not invent another base.
- Batch 1: every plan changes one parameter of the initial code; no plan may
  depend on another plan of the same batch.
- Batches 2 and 3: each plan extends a base that already won. Prefer to keep
  pushing the parameters the winning plans already changed, in the same direction
  (e.g. 128 → 256 → 384). Record the winning plan(s) in `source_plan_ids`.
- A mutation is an idea that is **not** one of the `s` strategies below (the `m`
  rows are directions, not a closed list). Set `strategy_id` to null and explain in
  one sentence in `novelty` why it is outside the `s` catalogue.

## Forbidden

- `problem_size`, `pomo_size`, `aug_factor`, file names, the score line format.
- The training budget (`epochs`, `train_episodes`).
- Anything `data/inputs/task.yaml`'s `task.tips` fixes (seed, batch sizes, test
  set, ...).
- Multi-parameter plans: one plan, one change.

## nn modification strategy (to be added by the project maintainer)

## Parameter tuning plan

| id | target | change |
|---|---|---|
| s01 | train_n50.py::optimizer_params.optimizer.weight_decay | 1e-6 → 1e-5 |
| s02 | train_n50.py::optimizer_params.optimizer.lr | 1e-4 → 5e-5 |
| s03 | model_params.encoder_layer_num (train + test) | 6 → 8 |
| s04 | model_params.ff_hidden_dim (train + test) | 512 → 1024 |
| s05 | model_params.head_num (train + test) | 8 → 16 |
| s06 | model_params.qkv_dim (train + test) | 16 → 32 |
| s07 | model_params.logit_clipping (train + test) | 10 → 5 |
| s08 | model_params.logit_clipping (train + test) | 10 → 20 |
| s09 | CVRPLTWModel.py::AddAndInstanceNormalization | InstanceNorm → LayerNorm |
| s10 | CVRPLTWModel.py::FeedForward.forward | relu → gelu |

## Mutated plan

| id | target | change |
|---|---|---|
| m01 | CVRPLTWModel.py::CVRP_Decoder.forward | fixed score/sqrt(d) → learnable temperature |
| m02 | CVRPLTWModel.py::CVRP_Decoder.forward | attention output + encoded_last_node (residual) |
| m03 | CVRPLTWModel.py::AddAndInstanceNormalization | RMSNorm instead of InstanceNorm |
| m04 | CVRPLTWModel.py::CVRP_Decoder | add Linear(E→E)+ReLU after combine |
| m05 | CVRPLTWModel.py::CVRP_Encoder.__init__ | learnable per-feature scale on the 7 inputs |
| m06 | CVRPLTWModel.py::CVRP_Encoder.__init__ | drop service_time and route_limit (7→5) |
| m07 | CVRPLTWTrainer.py::_train_one_batch | add entropy bonus −β·H to the loss |
| m08 | CVRPLTWTrainer.py::run | test with EMA/SWA weights |
| m09 | CVRPLTWModel.py::CVRP_Decoder | dense/MoE hierarchical gating |
| m10 | CVRPLTWModel.py::EncoderLayer | bias on Wq/Wk/Wv |

{nn_strategies}


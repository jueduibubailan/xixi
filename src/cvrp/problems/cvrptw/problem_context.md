Available inputs in this worktree:

- `pomo/` - the POMO CVRP reference implementation you must modify, not replace.
  - `pomo/CVRProblemDef.py` - data generation and 8-fold augmentation.
  - `pomo/POMO/CVRPEnv.py` - environment: state, masking, `step`, reward.
  - `pomo/POMO/CVRPModel.py` - encoder + POMO decoder.
  - `pomo/POMO/CVRPTrainer.py` / `CVRPTester.py` - REINFORCE training and rollout.
  - `pomo/POMO/train_n100.py` / `test_n100.py` - entry point examples.
  - `pomo/POMO/utils/utils.py` - logging and plotting helpers.
- `checkpoints/` - `checkpoint-30500.pt`, trained by POMO on CVRP100. The
  synthesized solver must keep loading it (encoder input stays 2-D).
- `BASELINE_NOTES.md` - the baseline map, the time semantics the pipeline verifies
  against, the instance schema, and the acceptance contract.
- `data/instances/cvrptw/n20/*.json|*.vrp` - the synthesized n=20 instances. The
  `.json` form is what the solver consumes; the `.vrp` form is the readable mirror.

Solution dump expected from `--save-solution`:

```json
{"routes": [[0, 3, 7, 0], [0, 5, 0], ...], "cost": 12.34}
```

`routes` is a list of node-id lists, each starting and ending at the depot `0`;
`cost` is the total travel distance the solver believes it achieved. The pipeline
recomputes both independently, so a wrong `cost` is reported as a mismatch rather
than silently accepted.

Available inputs in this worktree:

- `pomo/` - the POMO CVRP reference implementation you must modify, not replace.
  - `pomo/CVRProblemDef.py` - data generation and 8-fold augmentation.
  - `pomo/POMO/CVRPEnv.py` - environment: state, masking, `step`, reward.
  - `pomo/POMO/CVRPModel.py` - encoder + POMO decoder.
  - `pomo/POMO/CVRPTrainer.py` / `CVRPTester.py` - REINFORCE training and rollout.
  - `pomo/POMO/train_n100.py` / `test_n100.py` - entry point examples.
  - `pomo/POMO/utils/utils.py` - logging and plotting helpers (`create_logger`,
    `copy_all_src`, `get_result`, ...). The trainer, the tester and both entry
    points import it, so it is part of the baseline, not an optional extra.
- `checkpoints/` - `checkpoint-30500.pt`, trained by POMO on CVRP100. The
  synthesized solver must keep loading it (encoder input stays 2-D).
- `BASELINE_NOTES.md` - the baseline map, the time semantics the pipeline verifies
  against, the instance schema, and the acceptance contract.
- `data/instances/cvrptw/n20/*.json|*.vrp` - the synthesized n=20 instances. The
  `.json` form is what the solver consumes; the `.vrp` form is the readable mirror.

### Import paths in this layout (read this before copying a baseline file)

The baseline is staged as `pomo/` at the worktree root, and `pomo/` itself has no
`__init__.py`. The upstream repository instead uses the package prefix
`CVRP`, so four baseline files import the helpers as
`from CVRP.POMO.utils.utils import ...` - that path **does not exist here** and
raises `ModuleNotFoundError` if you copy it verbatim.

Two ways to make it work, both acceptable:

1. keep the helpers where they are and bootstrap the path in your entry script
   (`sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pomo"))`),
   then `from pomo.POMO.utils.utils import create_logger, copy_all_src`
   (implicit namespace packages mean no `__init__.py` is needed);
2. or copy the helper file next to your own modules and import it as
   `from utils import create_logger, copy_all_src`.

Whichever you choose, your generated package must be importable **from its own
directory**, because that is how the pipeline runs it.

Solution dump expected from `--save-solution`:

```json
{"routes": [[0, 3, 7, 0], [0, 5, 0], ...], "cost": 12.34}
```

`routes` is a list of node-id lists, each starting and ending at the depot `0`;
`cost` is the total travel distance the solver believes it achieved. The pipeline
recomputes both independently, so a wrong `cost` is reported as a mismatch rather
than silently accepted.

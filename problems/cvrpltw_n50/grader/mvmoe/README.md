# MVMoE — vendored instance generation

Upstream project: **Routing-MVMoE** (`D:\PyProject\Routing-MVMoE-main`), MIT License
(see `LICENSE`, Copyright (c) 2023 Jianan).

These files are copied **verbatim**. They are here because this bundle's evaluation set is
MVMoE's, and a comparison is only meaningful if the instances come from MVMoE's own code
rather than from a re-implementation of it.

| file | upstream path | why |
|---|---|---|
| `VRPLTWEnv.py` | `envs/VRPLTWEnv.py` | `get_random_problems` (L356-411) builds the instances; `generate_dataset` (L334-342) pickles them; `load_dataset` (L344-354) reads them back |
| `generate_data.py` | `generate_data.py` | the reference entry point: `seed_everything` -> `get_env` -> `generate_dataset` -> sanity `load_dataset` |
| `utils.py` | `utils.py` | `seed_everything` (L84-91) is the seeding order the datasets were produced with |
| `LICENSE` | `LICENSE` | MIT attribution |

`envs/__init__.py` is **not** vendored: it does `from .CVRPEnv import *` for all sixteen
environments, which would pull in ~300 KB of code this bundle never runs. `VRPLTWEnv.py`
imports only `dataclasses`, `torch`, `os`, `pickle` and `numpy`, so it loads on its own.

## How it is driven

`grader/mvmoe_gen.py` loads `VRPLTWEnv.py` **by file path** (`importlib.util`), so there is
no package `__init__` to satisfy, and it reproduces `generate_data.py`'s sequence:

```python
seed_everything(seed)                       # utils.seed_everything, same order
torch.set_default_tensor_type('torch.FloatTensor')
env = VRPLTWEnv(problem_size=size, pomo_size=size, device=torch.device('cpu'))
env.generate_dataset(count, size, tmp_pkl)  # upstream, including its whole-batch redraw
load_mvmoe_pkl(tmp_pkl)                     # back into this bundle's instance dicts
```

## Deviations (deliberate, and only these)

1. **CPU is forced.** Upstream `generate_data.py` switches the default tensor type to CUDA
   when a GPU is available, and CPU and CUDA draws do not share an RNG stream — the same seed
   would produce different instances. This bundle pins CPU so a run is reproducible on any
   machine, and records `"device": "cpu"` in `manifest.json`.
2. **The output is converted.** Upstream writes `vrpltw50_uniform.pkl`; this bundle converts
   the same 8-tuples into its JSON instance format (`load_mvmoe_pkl`), which is what the
   executor and the grader read. The numbers are copied, not recomputed.
3. **No re-implementation remains.** The earlier pure-Python port of `get_random_problems`
   was removed: it redrew per instance where upstream redraws the whole batch, so its seed
   stream — and therefore its instances — differed from MVMoE's.

## When these files are used

| `data.source` | `data.pkl` present | what runs |
|---|---|---|
| `mvmoe` | yes | the shipped pkl, converted (the default; this is what published numbers were measured on) |
| `mvmoe` | no | the vendored generator above |
| `generate` | — | the vendored generator above |

The `pkl` path needs only the standard library. The generator path needs `torch`.

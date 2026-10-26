"""Hooks and instance generation for CVRPLTW at n = 100 (MVMoE-compatible).

`grader.py` holds the hooks the engine looks for; `mvmoe_gen.py` holds the instance
generation ported from `Routing-MVMoE-main/envs/VRPLTWEnv.py`. Both live in this package so
the bundle stays self-contained: the engine puts this directory on `sys.path` while it
loads `grader.py`, which is what lets that file `import mvmoe_gen`.
"""

"""Hooks and instance generation for the template problem.

The engine loads `template_grader.py` as a module by path and reads its hooks by name. This
file exists so the directory is a package in the same sense `problems/cvrptw/grader/` is,
which keeps the two bundles' layouts identical.
"""

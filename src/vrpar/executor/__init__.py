"""Executor: run generated code, apply the acceptance contract, verify feasibility.

No LLM is involved anywhere in this package, and nothing here is variant-specific:
the program name, its CLI flags, the acceptance wording and the feasibility check
all come from the loaded problem plugin.
"""

from vrpar.executor.contract import (
    Contract,
    StepResult,
    classify_error,
    condense,
    find_instances,
    py_compile,
    run_program,
)
from vrpar.executor.feasibility import distance_matrix, verify_solution_file
from vrpar.executor.grader import CodeGrader, grade_run
from vrpar.executor.runner import (
    classify_signature,
    load_run_report,
    run_checks,
    run_report_summary,
)

__all__ = [
    "Contract",
    "StepResult",
    "classify_error",
    "classify_signature",
    "condense",
    "find_instances",
    "py_compile",
    "run_program",
    "distance_matrix",
    "verify_solution_file",
    "CodeGrader",
    "grade_run",
    "load_run_report",
    "run_checks",
    "run_report_summary",
]

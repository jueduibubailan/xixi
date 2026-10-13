# How evaluation instances are generated

The instances this problem is graded on come from `../../grader/cvrpltw_gen.py`
(declared as `problem.data.generator` in `task.yaml`), driven by the parameters in
`problem.data`. They are generated **before** any agent runs and are read-only to
it: changing the generator changes nothing about how a solution is scored.

At training time the generated solver builds its own random instances, so the
window-construction rules in that file are also the specification of what the
training code must produce. Read it before writing that code.

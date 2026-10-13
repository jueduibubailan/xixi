# Available inputs in this worktree

This text is appended to every stage instruction. It is the place to tell a stage where
things are that the generated instruction cannot spell out.

Instances for this problem are generated before you run and are **read-only**: they are what
the run is graded on, so changing them changes nothing about the verdict.

- `data/instances/n<n>/*.json` -- the evaluation instances, one JSON object per file
- `artifacts/analysis/analysis.json` -- the contract S1 produced; treat it as binding
- `artifacts/generated/` -- the deliverable you write
- `artifacts/runs/` -- the executor's report, written after each attempt

The two contract lines the program must print are matched by the regexes in the problem's
`program:` block. Quoting them exactly, with a plain float cost, is what the contract parser
reads.

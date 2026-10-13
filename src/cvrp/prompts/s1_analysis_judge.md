# S1 judge — audit the analysis

You are the **analysis auditor**. You have just been shown an `analysis.json` for a
synthesis task. Your job is to falsify it, not to praise it.

Check, in this order, and stop at the first category where you find a defect:

1. **Coverage**: does every entry of `constraints` have a corresponding
   `masking_rules` entry whose `condition` actually enforces it? Name the missing
   pairs. Also name any constraint the task description states that the analysis does
   **not** list at all — comparing the contract against the description is part of
   this job, not just against itself.
2. **Soundness**: does any mask's `condition` reference a quantity that the environment
   does not have at that decision step? The only quantities available are those the
   analysis itself lists under `state_variables`; name the rule and the quantity it
   assumes.
3. **Deadlock**: is there a state from which every action is masked — in particular, is
   there a required target that some rule can make permanently unselectable? If so,
   which mask must be relaxed, and how?
4. **Checkpoint safety**: does the plan keep the encoder input dimension at the width
   this task's `checkpoint_compatibility.checkpoint` requires, and push new features
   into the decoder? If it proposes anything else, say exactly which
   `pomo_modification_plan` entry is incompatible with that checkpoint.
5. **Verifiability**: is every `feasibility_checklist` entry checkable from the instance
   JSON alone? Quote any entry that is not.
6. **Grounding**: does the plan name symbols that actually exist under `pomo/`?
   If you cannot confirm a symbol, say so.

Then answer with **exactly one line**, no line breaks, no extra colons:

```
jud1: <verdict> :: <defect 1> ; <defect 2> ; ... :: <the single most important fix>
```

If you find nothing, answer `jud1: pass :: no defects found :: none`. Do not
invent defects to look thorough — a false positive costs the run a whole round.

# The calibration journal

The single most transferable practice in this suite is not any command — it is
**treating your delegate fleet as something you measure**, in writing, with
dates, and letting the measurements override the marketing.

## Why the tables in the skills are not your tables

Model quality per role changes with every release. Quota tiers differ per
account. Free-pool congestion differs per hour. A model that is excellent at
implementation may be lenient at attack-path review; the reverse also happens.
The skills in this repo ship with example tables so you can see the *shape* of
the decision — but the numbers that should drive your dispatch decisions are
the ones you measured on your own account, your own repos, your own weeks.

## The format

Keep one table per family, in that family's runtime reference file, appended —
never rewritten — one row per run:

```markdown
| date | model | role | outcome |
|---|---|---|---|
| 2026-08-04 | family/model-x | review | 7 findings on a skills-consistency diff, 7/7 verified real |
| 2026-08-05 | family/model-y | review | ~6 items, 2 actionable (both minor, both real); 1 misread a fixture, 2 judged intended behavior as bugs |
| 2026-08-06 | family/model-x | review | 3 findings on a wire contract, 3/3 real — incl. the round's only live defect, which two paid legs and a human reviewer all called the opposite way |
```

The **outcome column carries verified hit rates**, not impressions. "Found 7
things" is useless; "7 findings, 7/7 verified real" and "6 items, 2
actionable" are calibration data. Verify every finding before you count it.

## Rules for writing entries

- **n=1 proves nothing.** Free pools especially are flaky *by documented
  design* — a hang has at least three independent causes (prompt-transport
  bugs, congestion, genuine model failure) and one observation cannot separate
  them. Run each arm at least twice; prefer a sweep to a pair of anecdotes.
  A measured cautionary tale: two single-sample tests produced a confident
  causal theory ("MCP config blocks startup") that a proper repeated sweep
  destroyed in twenty minutes — the documented prompt-size bug had been right
  all along.
- **Record the retraction when you were wrong.** The most valuable entries in
  a mature journal are the corrections: "this file used to claim X; measured
  again on DATE, X was noise, the real cause was Y." They are the only thing
  that stops the same wrong conclusion being re-derived by the next session.
- **Date everything.** An undated measurement is a rumor. A dated one is a
  snapshot someone can decide to re-verify.
- **Tier does not predict yield.** The standing headline from the journal this
  practice was extracted from: a free model found the only HIGH on a diff
  where two paid legs returned nothing usable, and the smaller of two free
  models had the better verified hit rate on review work. Do not drop a leg
  because a "stronger" reviewer is already running, and do not pick by
  parameter count — pick by measured hit rate per role, and by structural
  properties (context length) where they're load-bearing.
- **Separate the model from the transport.** "The model returned nothing" and
  "the launcher ate the output" look identical from outside. Before writing a
  failure row, check the transport diagnostics in the family's runtime file
  (where does the log stop? what does the token accounting claim? does the
  output file exist?). Many "model is down" entries are launcher bugs.
- **A wrapper's pass/fail summary is not evidence.** Verify against the raw
  log. A measured incident: three straight "failures" nearly got a model
  written off as headless-incapable; the raw logs showed two had actually
  succeeded and the wrapper's simplified matching had misread them.

## What the journal buys you

- **Dispatch decisions become table lookups** instead of vibes: LOW-risk
  mechanical sweep → cheapest family with an acceptable measured hit rate;
  HIGH-risk review → the two families whose measured strengths differ most.
- **Role-fit surprises surface early.** The lenient-at-attack-paths /
  strong-at-consistency split between two families was invisible until paired
  rounds on the same targets made it undeniable — after which review briefs
  could be routed to each leg's measured strength.
- **Model releases stop being disruptions.** A new version gets a few
  journal rows in its first week and either earns its place in the tables or
  doesn't.

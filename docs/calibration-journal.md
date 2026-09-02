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
- **Count the SOLE findings, not the agreements.** The number that justifies a
  leg is how often it was the only one to reach a real defect — agreement is
  nearly free and nearly uninformative. Measured over three consecutive 4-leg
  rounds on one stack: every round had at least one lead-verified defect found
  by exactly ONE leg, and it was a different leg each time. In the sharpest
  round, three of four legs each contributed a finding no other leg reached,
  while the fourth agreed with everyone and was independently wrong on the one
  item it answered alone. A 3-of-4 majority is not evidence — if you are
  tempted to trim a leg, trim by its sole-finding record, and expect the score
  to move between rounds rather than settle. (Held again on a fourth round the
  next day: three legs each sole-found something real, the fourth found nothing
  and was the only leg wrong on the item it answered alone.)
- **A single-document freeze produces "unowned obligation" false positives.**
  When the reviewed change is one document of a contract set, a leg sees only
  that document and the prior patchset — so an obligation the sibling document
  assigns reads as unassigned. Measured: a strong leg raised MAJOR that a named
  implementation task had no person owner; the spec really does not name one,
  and the sibling plan under review the same hour assigned it to two people by
  name. The finding was true of the artifact and false of the contract. This is
  not the leg's error and more context in the prompt is not always the fix —
  pasting every sibling turns a review into a reading assignment. **The
  mitigation is the lead's:** before accepting any finding of the shape "X has
  no owner / no evidence / is undefined", check the siblings yourself. Expect
  this class on every plan/spec set that splits normative text across changes.
- **Verify the evidence STEP, not just the conclusion.** A leg can reach a
  defensible conclusion and invent the check it claims to have run. Measured: a
  free leg presented a specific `grep` as its "Verification" for one item; no
  such call appears in its log, and that command form would have been denied by
  its read-only config. The conclusion was sound — derivable from text it really
  had read — which is exactly what makes this hard to catch, because spot-checking
  the *conclusion* passes. A fabricated provenance is worse than a wrong answer:
  a wrong answer gets refuted, while an invented citation gets promoted into your
  review and then into a document someone cites later. When a leg names a command
  or a file:line as its evidence, grep the log for the command and open the file.
  If the run produces no log you can grep, you cannot score that leg on evidence
  quality at all — only on whether its conclusions survive your own checks.

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

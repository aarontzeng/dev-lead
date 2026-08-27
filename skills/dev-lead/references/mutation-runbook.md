# Mutation-proofing runbook

Read this **before Phase 2 verification** of any round that adds a
regression test. `dev-lead`'s Phase 2 names the step; this file is the step.

A new test that passes proves nothing until you have watched it *fail*
against the un-fixed code. Everything below is the ways that check quietly
lies — **every clause was paid for with a live false result**, i.e. a run
where the harness reported something that was not true and a human nearly
believed it. Ordered roughly by how early in a round they bite.

> [!WARNING]
> The single most destructive item is the third one: `git restore` /
> `git checkout --` on an uncommitted tree destroys the only copy of the
> implementation. Commit first, always — including your own edits.

- **Write the REAL commit message at checkpoint time — never "WIP".** The
  checkpoint is a commit that exists for a while on a branch a human may
  push at any moment. Measured: a user pushed mid-round, a "WIP checkpoint"
  was reviewed and merged, and the amend that later replaced its message
  created a duplicate change in the review system that could never be
  pushed. Write the real message, amend as findings land. Corollary: after
  any push you did not make yourself, `git fetch` and check whether your
  local commit still exists remotely before amending it.
- **Commit the delegate's work BEFORE any mutation.** A
  `git checkout <file>` to "restore the mutation" on an uncommitted tree
  restores BASE and destroys the only copy of the implementation —
  measured; recovered only because the delegate's session rollout held the
  patches. This applies to the **lead's own edits** exactly as much, and to
  **every file the mutation script will restore**, not just the one it
  mutates.
- **Never `&&`-chain the test run into the verdict echo.** A killed mutant
  makes the test binary exit NON-ZERO — that is the success signal — so
  `out=$(run) && echo "$verdict"` silently drops the report for exactly the
  runs that mattered. Capture, then report unconditionally.
- **`git restore` on an untracked path FAILS and leaves the file exactly
  as mutated** — and that error is precisely what habitual `2>/dev/null`
  suppression eats, which is how a deliberately-broken parser got one
  commit from shipping. Tracked files: `git restore`. Untracked: manual
  revert. Every restore is followed by a marker check that tolerates
  grep's exit-1-on-zero-matches: `[ "$(grep -c MUTATION <file>)" -eq 0 ]`.
- **A timeout kills the restore, and the mutant stays.** A loop repeating
  one mutation hit the harness's time limit and the `cp` restoring the
  file was simply never reached. **The marker check belongs in its own
  command**, not as the tail of the one that mutates — the tail is exactly
  what a timeout removes.
- **A mutant that dies SOMETIMES has not been killed.** Measured: a
  race-window test caught its mutant two runs in five and looked green
  every single-run. To fix it, hold the window open by construction — give
  the test a callback the subject must pass through (a gate, a hook, an
  injected clock) and block inside it. The same mutation then died 2 of 2.
- **Two shapes of "passes for the wrong reason", both live in one
  change.** (a) An assertion placed AFTER something else already failed the
  object — the guard under test never ran, so deleting it changed nothing.
  (b) A precondition that fails EARLIER than the target — breaking a
  directory to break step 2 broke step 1 instead, and the test proved
  something about a path it was not aiming at. The question for every new
  assertion is not "is this true" but "what is the FIRST thing that would
  make this fail, and is it the thing I mean".
- **A test filter matching zero tests exits 0 with no failures —
  indistinguishable from a surviving mutant.** Parse the harness's test
  count and report INVALID when it is < 1.
- **A STALE BINARY after a failed build reads exactly like a survivor.**
  Measured: a mutation did not compile, the harness checked only that the
  test binary existed, and the previous build's binary ran the unmutated
  code — zero failures. Remove the binary before every build; treat
  "binary missing after build" as BUILD BROKE, not as a test result.
- **Restoring the SOURCE does not restore the BINARY, and the stale one is
  the mutant.** Measured: after the last mutation of a round the source
  was restored, the marker check passed, `git status` was clean — and the
  next full-suite run used the binary still built from the mutated tree,
  so a correct tree reported a failure. This variant makes good code look
  red and invites you to "fix" something that was never broken. Rebuild as
  part of the restore; never read a suite result you did not rebuild for.
- **Never write a test count into a commit message you have not just seen
  green.** A message asserting a green suite is exactly the claim a later
  reader will not re-check.
- **A mutation that fails to COMPILE still triggers cleanup, and cleanup
  is where the work dies.** Measured — by a lead, one hour after writing
  the commit-first rule: the build broke, `git restore <file>` went in
  reflexively, and it erased an uncommitted fix round on that file. State
  it as a property of the COMMAND: `git restore` / `git checkout --` is
  never routine cleanup. Run `git status` on the path first, every time.
- **Prefer a test that asserts SUCCESS despite a distractor.** A test
  whose assertion is "it failed" is weak whenever several causes share
  that outcome. Live example: a test asserting an operation ended in an
  error state passed with and without the fix, because a second unrelated
  defect reached the same state by another route. Rewritten so the
  distractor is the ONLY obstacle between input and a green result, the
  same mutation died immediately.
- **A SURVIVED verdict is a lead finding, not a shrug**: first suspect the
  harness (mutation not applied, wrong filter, stale binary), then decide
  equivalent-vs-real with evidence.
- **Revert the fixes ONE AT A TIME when a round shipped more than one.**
  Measured: reverting two fixes together turned one test red, which reads
  as "covered". Reverting individually showed the second fix had NO test
  that killed it — the other fix in the same round supplied the value
  before the code under test was reached. A combined revert cannot tell
  "both covered" from "one covered twice".
- **One mutation does not clear a round that shipped two DIFFERENT KINDS
  of test.** A behavioural test and a static/structural guard (an AST sweep,
  an import-graph rule, a "no call site may…" check) answer different
  questions, so they need different mutations — and the behavioural one can
  leave the guard's predicate untouched. Measured: a round shipped a two-user
  database test plus an AST guard reading "no function may query this table
  without a user identity in scope". Deleting the `user_id` filter killed the
  database test and the guard **passed** — the enclosing function still took
  `user_id` for an unrelated query, so the predicate was still satisfied. The
  guard was not broken; it guards a different proposition than the round's
  prose claimed. Mutate each test with the shape IT forbids (for the guard: add a
  fresh violating function, then a renamed copy in another file), and when the
  two disagree, the honest fix is to write the measured boundary into the
  guard's own docstring — the divergence IS the finding.
- **Mutate the EXECUTION site, not the wiring site.** Killing a mutation
  by deleting the config/registration that feeds a feature proves only
  that some test notices the config is missing. Re-mutate the code path
  that CONSUMES the config, leaving the config intact — that is what makes
  behavioral assertions fire.
- **A property you claim IN PROSE must be enforced BY the artifact.**
  Measured: a doc and a commit message both said a PoC "verifies the hash
  against the RFC test vectors before reporting a timing" — the check had
  been run once by hand and never committed, so a rebuild proved nothing.
  Before writing "X is verified", grep the artifact for the thing that
  does the verifying; if it is not there, add it or do not claim it.
- **Self-consistent checks prove nothing.** A timed loop comparing
  `expect` against `got` — both produced by the same function — agrees
  with itself even when totally broken. A real known-answer test needs an
  answer from OUTSIDE the code, and each branch needs its own vector.
- **Never quote agreement to more digits than the measurement's own
  spread.** A throughput number re-run three times swung 3.5% while the
  text claimed a model matched it to 0.003% — one run's luck. Re-run any
  figure a conclusion leans on, state the spread, and let the claim be
  "lands inside the measured range" when that is all the data supports.


## Retrospective mutation: run the mutant against the OLD test

Forward mutation asks "does my new test fail when I break the code?" That
proves the test is connected to something. It does NOT prove the test is
STRONGER than the one it replaced, and when a review round tells you an
existing assertion is weak, that is the question actually on the table.

So when a finding says a test is weak, unfalsifiable, or checks the wrong
thing, run the mutant TWICE — once against the fix, once against the version
the finding was written about:

```bash
# 1. the mutant, against the NEW test: must fail
<apply mutant>; <run test>; echo "new exit=$?"          # expect non-zero

# 2. the SAME mutant, against the OLD test: if it passes, the finding is proven
git show "$BEFORE":path/to/test > /tmp/old_test
<swap in /tmp/old_test>; <run it>; echo "old exit=$?"   # expect zero
```

Two things come out of this that nothing else gives you:

- **It converts a reviewer's opinion into a measurement.** "Your verdict logic
  is unfalsifiable" is a claim you can accept or dispute. "Give the account a
  wrong password and the previous version prints ALLOWED, ALLOWED, then 'ACL
  mitigation holds', exit 0" is not disputable, and it belongs in the commit
  message verbatim — a future reader inherits the evidence, not the assertion.
- **It separates a strengthened test from a rearranged one.** Measured in the
  same round: a test was changed to pin which SIDE of a config a subject
  appears on. Forward mutation killed it, so it looked strengthened. Only the
  retrospective run showed the previous version ALSO failed that mutant — the
  real gap was one table row over, where the old version passed and the new one
  failed naming `sysid='10'`. Without step 2, the wrong mutant would have
  "confirmed" a fix that missed the finding.

Pick the mutant from the FINDING's trigger, not from the code you touched. A
mutant chosen from your own diff tests the change; a mutant chosen from the
finding tests the claim. When they differ, the finding's is the one that
settles the round.

Cost is one extra test run. Skip it only when the finding is about code that
had no previous test at all — there is no old version to measure against.

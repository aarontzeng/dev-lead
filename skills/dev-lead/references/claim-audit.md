# The claim audit: prose that no test executes

Read this from `dev-lead` Phase 2, step 2, when the change adds or edits
statements -- comments, docs, commit messages, log lines. The skill keeps
the command and the question it asks; this file keeps the three shapes of
false statement the step exists for, what the audit's count does and does
not mean, and the two shapes no filter can flag.

The helper lives in the SUITE's tree while cwd is the target repo, so resolve
it first (the same snippet the skill uses):

```bash
DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
```

**Writing a NEW statement? Name what would make it false.** The grep rule
above catches a sentence that went stale; it cannot catch one that was false
on arrival, and mutating the code under test does not reach it either — that
sentence is not in anything the suite executes (measured: 20 mutants died in a
round whose three false sentences all survived). A lint rule *over* prose is
executable and can be mutation-proofed; the prose it judges is not. Run
`python3 "$DEV_LEAD/scripts/claim-audit.py" "$WORKTREE" "$BASE...HEAD"` and
answer its question per hit: **if this sentence were false, which test goes
red?** Naming a test that runs nearby is not an answer — the assertion has to
fail on THIS claim being false.

**Writing a statement that is TRUE NOW? Ask whether it can go false with
nobody editing this file.** The two rules above cover a sentence you made
stale by editing its neighbour, and a sentence that was false on arrival.
Neither reaches the third shape: true when written, untouched by any later
diff, and false anyway because the WORLD moved. That happens when a durable
document cites a fact scoped to something outside it — a review vote, "the
current patchset", "nobody has reviewed this yet", "the newest release", a
count of open items.

Measured, one session: a normative document argued that another document
must not be the tie-breaker partly because "its current patchset carries an
Owner -1". Uploading the next patchset of that document outdated the vote, so
the sentence was false within the hour — self-invalidating, in a file meant
to outlive the review that produced it. In the same round a four-family
review found the same file resting its ONLY recorded owner acceptance on
"change N, patch set 10 carries a +1", linked to a host that had since been
decommissioned: a perishable fact behind a dead link, in the document that
had just declared such facts invalid. And an operational gate — whether an
operator may enable a mode — read "current-patchset review", so it changed
when the review tool changed rather than when the design or the software did.

**The test is MONOTONICITY, not volatility**, and getting this wrong makes
the rule worse than not having it. "Change N is merged" is volatile in
the sense that it was once untrue — but it can only go from false to true, so
citing it is safe. "PS4 carries a +1" goes from true to false. In the same
round a leg applied the rule mechanically and flagged every "merged" as a
perishable fact; acting on that would have deleted correct sentences. Ask
which DIRECTION the sentence can flip, not whether it can.

The fix is almost never to delete the fact — it is to cite the durable thing
the perishable one was evidence for. An acceptance is durable; the vote that
expressed it is not. A merged change is durable; the patchset that became it
is not. Record "Owner X accepted on DATE (change NNNNN)", not "PS10 carries
their +1".

**It is an attention cue, not a control.** It verifies nothing and exits 0
either way; a silent run means "nothing matched the noun list", NOT "the prose
is anchored", and it is never evidence that claims were checked.

**Commit what the audit changed, before the target is frozen.** A downgrade
or a new test is a working-tree edit at this point, and the next phase freezes
and reviews an exact `HEAD` commit while the merge gate fast-forwards that
committed branch. Anything left uncommitted here is reviewed by nobody and
merged nowhere — a successful audit silently losing its own fix. So: resolve
the hits, re-run the suite, amend or extend the checkpoint commit, and only
then freeze.

**A test this step adds is a new regression test, and step 2's
mutation-proofing already ran before it existed.** Re-running the suite is
not that check: it shows the test passes, not that it would fail if the
claim were false. So mutation-proof any test the audit produced, after
committing it and before freezing. This is not bookkeeping — the question
being answered is *which test goes red?*, and a vacuous test is the same
wrong answer as naming one that runs nearby, just written down instead of
asserted.

For the measurement, run the BARE revision at **both** checkpoints:
`python3 "$DEV_LEAD/scripts/claim-audit.py" "$WORKTREE" "$BASE"`, once before
the prose pass and once after. Do not compare it against the `$BASE...HEAD`
audit run — that form audits prose **and commit messages**, while the bare
form audits prose only (no commits in a worktree range), so the count falls
by the excluded class alone. Measured: a range with one commit-message claim
and no prose edit whatsoever reports `hits=1` ranged and `hits=0` bare. Two
different input classes are not a before and an after.

A drop then means a sentence was downgraded. **An unchanged count is not
evidence of no value** — this step offers two outcomes, and pinning the
premise with a test leaves the claim standing and still matching. So record
one line per round: did any hit lead to a downgrade or to a new test? That,
not the number alone, is what says whether the step earns its place.

Two shapes no filter can flag, and no prompt can force either — a review leg
from another family is what catches them, so raise them there rather than
here: a **right conclusion resting on a wrong mechanism** (a doc said two rows "return the same shape, so this is not
an existence oracle" — both rows really did share those fields, but a third
field differed; the conclusion was right and the stated reason was not, and a
wrong mechanism gets reused as a premise by whoever reads it next), and a
**proxy written up as the property** (64 cores and 112 GiB free were measured
and true, and became "feasibility is not the obstacle" — the attempt hard-reset
the host; capacity is not feasibility).

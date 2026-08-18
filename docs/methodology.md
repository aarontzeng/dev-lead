# Methodology: why the workflow is shaped this way

The skills carry the *how* — commands, flags, traps. This document carries the
*why*, so that when a CLI changes under you (they all do), you can rebuild the
how from first principles.

If you have not seen a run's shape yet, read [workflow.md](workflow.md) first:
it maps the phases, the gates between them, and the adapters. This document
argues for each of those choices in turn.

## 1. The cross-family rule

**No change merges reviewed only by its own model family.** The implementer's
family never reviews its own change; HIGH-risk changes take two reviewers from
two *other* families.

The reasoning is not ceremony. A second context of the same model is a fresh
look — it lacks the author-context bias — but it still shares the model's
training, and therefore its blind spots: the API it habitually misremembers,
the concurrency pattern it always believes is safe, the error path it never
imagines. Measured across real rounds, reviewers from different families
produced findings with **near-zero overlap** — each family caught a class the
others passed. Convergence (two families independently reporting the same root
cause) is the strongest confirmation signal there is; disjoint coverage is the
ordinary case and is exactly why the second leg pays.

Corollaries:

- **The family is a property of the served model, not the CLI.** One runtime
  adapter can serve several families (agy serves Gemini and Claude pools;
  opencode serves DeepSeek, Nemotron, and stealth models; grok serves Grok; cursor serves whatever model you pin, six families wide), and adapters can
  silently substitute the tier you asked for. Account the rule against the
  *verified served model's* family — the run log records adapter, served
  model, and family as three separate fields.
- A model whose family is deliberately undisclosed (stealth models in free
  pools) can never *satisfy* the rule — it might secretly share any family.
  Fine as an additional pair of eyes; never the accounting leg.
- When one model is scarce, spend it on **review**, not implementation.
  Implementation has substitutes; the review gate is the leverage point.
- The lead's own verification shares the lead's blind spots. When lead and
  implementer are the same family, the review *must* be cross-family.

## 2. Roles and briefs: diversity beyond the model

Family diversity is the accounting rule. **Brief diversity is the coverage
rule.** When you run two or three review legs, give each a different job —
measured on live rounds, identically-briefed legs buy redundancy while
differently-briefed legs each found the only instance of their own class:

- **sequences** — walk a concrete path to a wrong outcome ("an attacker does
  X, then Y — trace it"). Best given to the leg measured strongest at
  execution-path reasoning.
- **challenge** — is this the right approach at all? What assumption has
  nobody questioned? What does this choice cost later? Do *not* phrase it as
  "find defects" — that collapses it back into the first role.
- **consistency** — does this contradict a document already accepted, or
  itself?
- **is it still true** — for anything amended repeatedly, which text is dead
  but still reads as live? Append-only docs create this hazard by
  construction, and no other role looks for it.

**Write your own findings down BEFORE the legs fire.** The overlap number is
the whole evidence base for brief diversity, and a lead who reads three reports
and then recalls what it "already knew" will produce a flattering one every
time. Seal the lead's list into the run log first — including the candidates
you checked and rejected, with the evidence that refuted them. Measured on a
three-leg round: one sealed lead finding was independently reproduced by
exactly one leg, principal findings overlapped on one item out of nine, and the
single strongest finding came from the free leg on a diff two paid legs had
already read. None of those three numbers survives being reconstructed
afterwards.

A model can be wrong for one role and right for another. A "fast" tier that
underperforms on defect-hunting produced the deepest finding of a five-round
sequence when given the challenge brief. Blanket "model X is unfit for review"
claims deserve a measurement per *role* before they're trusted.

## 3. Machine-enforced boundaries

Prefer a boundary the model *cannot* cross to one it is asked not to cross:

- Read-only reviewers get **permission configs / sandboxes** that deny writes
  and deny shell except whitelisted git reads — not a "please be read-only"
  sentence.
- No-push is enforced by omission: `git push` simply isn't on any allow-list.
- Write delegates work in **isolated git worktrees**, never the main checkout.

Instruction-level rules still matter as the second layer: a delegate that
inherits a global "never push" instruction refuses even phrasings the
allow-list never anticipated. Neither layer is strictly stronger —
instruction-level survives novel commands, machine-level survives a model that
misreads its instructions. Use both; state destructive-adjacent rules in the
task prompt anyway, at zero cost.

Where a machine boundary has a deliberate hole (e.g. allowing a test runner
that executes repo-supplied code), *say so honestly* in the skill, state what
residual risk it creates, and make that risk observable — snapshot
`refs/remotes` before dispatch and diff it at handoff, so an accidental push
surfaces as a delta instead of being assumed not to have happened.

## 4. Evidence gates

**"Read it and found nothing" and "never opened the file" produce
byte-identical output.** A review without an evidence gate cannot be
distinguished from a review that never happened — a measured incident: a
format-compliant all-HOLDS verdict returned in three minutes, having cited
nothing.

The gate that makes the difference visible:

- Per file opened: its **line count AND the verbatim text of its last line**.
  The count is guessable; the text is not.
- Per claim: a `file:line` plus the quoted code that decides it.
- `NOT REACHED` is explicitly acceptable for anything unchecked. `HOLDS`
  without a quote is the one unacceptable answer.

Size the gate to what *you* cannot check. On code, making the reviewer
re-derive facts is how wrong ones get caught. On a *document* (design, ADR,
spec), most mechanical facts were verified by the author before writing —
demanding re-derivation turns a document read into a repo sweep that blows
timeouts for nothing. There, ask instead: what is stated as fact but
unverifiable? What would a reader who stops at section N wrongly conclude?
Which claim is the author's own inference rather than a citation? Keep the
unguessable anchor either way.

## 5. Bounded properties

An unbounded review property cannot converge. Measured: four rounds on a
best-effort parser, each round legitimately finding one more unhandled nesting
level, forever — because the reviewer was judging an approximation against an
unbounded spec ("handles anything the renderer can produce").

Any check that answers a question about the world through a **proxy** — a
regex standing in for a parser, "is this session alive" via a socket flag —
is approximation-shaped. Declare its scope in the code's own docstring:
exactly what it detects, what it deliberately does not, and why the omission
is a design decision. Then review against the boundary. Write the boundary
**before** dispatching the review, not after the unbounded finding comes back
— the lead always knows the boundary; the reviewer is the one who doesn't.

## 6. Verification is the lead's job, and order matters

Nothing a delegate self-reports is evidence. The lead:

1. Inspects the working tree directly (`git status --short`, `git diff`) —
   *before* any ranged diff. A `$BASE..HEAD` range on an uncommitted tree is
   empty and reads as a clean scope check while the whole change sits
   unexamined: a measured false green.
2. Re-runs the test suite itself.
3. Makes the checkpoint commit (with a real message, never "WIP" — checkpoint
   commits outlive their author's intentions the moment someone pushes
   mid-round).
4. Mutation-proofs every new regression test: watch it **fail** against the
   un-fixed code, then pass against the fix. The catalogue of ways this
   quietly lies — stale binaries, combined reverts masking uncovered fixes,
   assertions that fire after an earlier failure already decided the outcome,
   zero-matching test filters that exit green — lives in `dev-lead` Phase 2,
   every clause paid for with a live false result.
5. Verifies every review finding against the code before acting, and holds
   **rejections to the same evidence standard as findings** — a correctly
   rejected false positive and a wrongly dismissed real bug otherwise leave an
   identical run log.
6. Anchors the round's **prose**, not just its code. Mutation-proofing works for
   step 4 because a test executes; a sentence does not, so no mutant of the code
   under test can make a false comment or doc line fail. The exception is worth
   chasing rather than noting: a prose claim *about behaviour* becomes testable
   the moment you assert the behaviour it describes — which is what question 1
   is really asking for. Measured: a round that killed 20
   mutants shipped three false sentences, and all three were caught by the review
   leg instead.

   `scripts/claim-audit.py` surfaces the phrasings that have shipped false and
   asks one question per hit — **if this were false, which test goes red?**
   Naming a test that merely runs nearby is not an answer; the assertion has to
   fail on the claim being false.

   **It is an attention cue, not a control, and the distinction is load-bearing.**
   It verifies nothing, exits 0 either way, and a silent run means "no added line
   matched the noun list" — *not* "the prose is anchored". It must never be cited
   as evidence that claims were checked, and must never justify less scrutiny in
   the review leg. An earlier draft of this section called it a Loop-layer
   mechanism closing a Loop-layer gap; a three-family review panel called that a
   category error, and it was right — an un-gated prompt whose output only a
   reader can judge is Graph-layer work, and reclassifying it does not make it an
   oracle.

   Because that leaves it unable to prove its own worth by argument, it prints
   `hits=<n>`. Record that number before and after the prose pass. If it never
   drops across a run of rounds, the step has never caused an edit and should be
   deleted on that evidence — the same standard §4 applies to everything else.

## 7. Review targets are frozen and spans are pinned

- Review a **committed** state in a directory nothing else touches. A reviewer
  reads the working tree, not your commit: a measured round ran mutation
  testing in the same worktree mid-review, and the reviewer opened a CRITICAL
  on a deliberately-broken file it was never meant to see. One directory per
  reviewer; no lead activity inside it.
- The base of a topic branch is `git merge-base <target> HEAD`, never the
  target branch name. They stop being the same commit the moment the target
  advances — and then the "review" also covers the reversal of everything the
  target gained meanwhile, silently.
- The two range spellings look identical and are not: `git log A..B` is
  "commits in B not in A" (what you want); `git diff A..B` means
  `git diff A B` — **not** merge-base. Use `git diff A...B` or pin `$BASE`
  once and use it everywhere. Nothing errors when you get this wrong; the
  contamination surfaces only when findings are checked file-by-file against
  the real diff.
- Before launching: `git diff --stat "$BASE" HEAD` — the file list must match
  the change under review.

## 8. Bounded rounds and stop conditions

Iteration is where quality comes from, but unbounded iteration is where
budgets die. Default: three implementation rounds (initial + two fix rounds).
Fix-round prompts quote each verified finding **verbatim, with why it is real
and what fix is required** — this shape fixes everything first-try in live
runs; "address the review" has no track record.

Stop and report (instead of looping) when:

- the round cap is reached with verified HIGH findings still open;
- a fix round introduces a *new* HIGH finding (fix churn — the spec or the
  delegate is wrong for the task);
- the same finding survives two fix rounds (the prompt is failing to
  transmit it — fix it directly);
- the same finding *category* keeps reopening against approximation-shaped
  code (fix the property's boundary, not the code — §5).

## 9. The human merge gate

The lead assembles a verdict from the run log — rounds, findings and their
fates, test results, diff stat against `$BASE` — and presents it. Merge
happens on explicit approval; push is human-only in every mode, with no
exceptions. Re-verify the target branch's *identity* at the gate, not just
its cleanliness: humans rebase main checkouts mid-round, merged commits come
back with new SHAs, and `--ff-only` refusing is the guard working — never
switch to `--no-ff` to get past it.

## 10. Write it down as it happens

Keep one run log per run, appended as events happen, never reconstructed
afterward: dispatch decision and why, per-round task file / delegate / commits
/ test results / findings with verified-rejected-fixed status (and for each
rejection, the evidence), stop-condition hits, final verdict. The report to
the user is written *from* the log; an interrupted run resumes *from* the log
plus the preserved worktree.

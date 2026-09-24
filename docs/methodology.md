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
  The rule cuts the other way too: **the same family reached by a different
  provider path is still the same family, not a new leg.** `opencode-go/grok-4.6`
  is xAI exactly as cursor's `cursor-grok-4.6` is; `opencode-go/gpt-5.6-luna`
  shares GPT's blind spots with codex's `gpt-5.6-terra`; the paid
  `opencode-go/muse-spark-*-contributor` is the same Meta model as the free
  `opencode/muse-spark-*-contributor-free`. Because these arrive through a
  different CLI, a different provider and a different price list, they are
  easy to book as a third family at accounting time. They are not. Family is
  the model lineage; the path it came down is irrelevant. (Measured 2026-09-15
  on the opencode go plan, where two of the 22 newly callable models were
  exactly these look-alikes.)
- **But family is the model you actually DISPATCHED this round — not the
  adapter's catalogue either.** The same fact cuts two ways and only one
  inference is sound. A multi-family adapter can collide with itself (above);
  it does NOT follow that catalogue overlap is collision. `cursor` alone can
  serve xAI, OpenAI, Anthropic, Google, Moonshot and Composer, so scoring
  families by what an adapter *could* serve would mark nearly every candidate
  as a clash and the rule would collapse into refusing everything. Account the
  model that ran. Corollary worth saying out loud: **"the cursor leg" and "the
  grok leg" are shorthand, and they stop being true the moment the model
  changes** — at accounting time, read the round's model id. (Raised
  2026-09-16 by a peer, correcting another peer who had read a catalogue
  overlap as a collision. The wrong reading is the more tempting one because
  it looks stricter, and a rule that refuses more feels safer.)
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
- **falsifiability** — can each test here actually FAIL? Walk every path from
  entry to `exit 0` and name the input that reaches it. This is NOT the
  challenge role: challenge asks whether the approach is right, and a green
  suite answers it "yes" just as convincingly whether or not the suite can
  fail. Ask it of guards and gates especially — the ones whose value is
  entirely "it would have caught X".

  Measured, one round, three legs on the same frozen commit: only the leg
  briefed this way found any of it, and it found five — a gate whose two
  required-positive checks reported ALLOWED when the login was refused, a
  probe that read an EOF as an allow, a range where every commit was skipped
  yet the run still printed its success line, an unchecked `docker run` that
  let probes pass against a broker the commit never configured, and a
  direction assertion that only covered the first table row. The
  sequences-briefed leg returned HOLDS on all seven properties it was given
  and was right about every one of them; it simply was not asked this. **The
  brief decided the yield, not the model.**

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

**A frame is a measured default, per adapter — not a style.** Before 0.6.30
the suite's adversarial frame was tested on every review leg against that
leg's plain skill brief: three frozen targets with known answers, three runs
per arm, same model and effort. It lifted three legs (4→7, 3→8, 9 from 7 for
the companion prompt it replaced), did nothing measurable for a fourth (11→12,
slower) and made the strongest one slightly worse (17→15 of 18, slower): a
frame's structure is scaffolding, and a leg that needs none pays for it in
attention. Two lessons from building it: **an exclusion instruction silently
cuts recall** ("an inherited defect is not a finding" dropped real findings a
label would have kept — label, lower the severity, never exclude); and **a
per-claim HOLDS/BROKEN list is what stops a lenient leg rubber-stamping** —
the plain brief answered broken claims "HOLDS". Re-measure when a model
changes; the runtime files' calibration tables carry the per-leg numbers.

**Lens diversity pays most where the change is large and multi-language.**
Measured on one 22-change stack (compiled code, SQL inside it, a web client,
scripts, docs; four legs, four lenses): 7 blockers, **6 found by exactly one
leg**, and every family contributed at least one no other leg found. On a
text-only documentation batch the same extra legs mostly added noise. A
hypothesis from two rounds, not a rule — but it says where a fourth leg earns
its cost.

**Tell a leg what it must not re-report.** A "known, do not re-report" block
listing the lead's already-verified items, measured across four packets in one
round, produced zero duplicates — and left the legs' attention for new ground.

**Two packet shapes that hide regressions from a diff-only brief:**
- A *rebase* patch set can silently revert content: a measured rebase put back
  an old date, an old owner and dropped several table rows; a three-model
  review passed it, and only an interdiff-against-history check caught it. On
  any rebase, have one leg compare the old patch set's content with the new
  one's net of the parent's changes (`git range-diff`) and list every removed
  line the new parent does not explain.
- For a topic of several changes, include the **already-merged** siblings'
  current text, not only the open ones: a stale cross-reference lived in a
  sibling that had merged, outside every leg's packet.

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

**A brief cannot widen the boundary, and one that tries kills the leg.** The
allow-listed slice above is narrow, differs per family, and is set by the
runtime — so asking for anything outside it does not produce a weaker report,
it produces no report: the leg dies on the first denied command with a single
line of output. Measured 2026-08-19: two legs of one four-leg round were lost
that way, both to a *shared* prompt header that said "you MAY compile and run
small throwaway programs". One died on `ssh` under a plan-mode permission
check, one on `npx` with no network. Both looked exactly like model failures
until the logs were read. The header was the bug: it granted, in prose, a
capability only one of the four runtimes had. Keep the shared header free of
anything family-specific, and put capability statements in the per-leg brief
where they can be checked against that family's runtime note. When a question
genuinely needs measuring, building the measurement yourself and handing the
leg the tool plus its output is usually cheaper than granting anyone anything.

**Check which mechanism is actually holding, because a flag named `--sandbox`
may not be one.** Measured on two hosts, 2026-09-15: an agy review leg launched
exactly as this suite prescribes (`--mode plan --sandbox --add-dir <frozen>`)
read `/etc/hostname` and listed a home directory on one, and on the other also
WROTE a file into `/tmp` — all outside `--add-dir`, the write confirmed from a
separate shell rather than from the leg's own account.

Confinement turned out to be obtainable, and it takes two settings rather than
the one the first write-up named. The file tool is governed by
`allowNonWorkspaceAccess`, which was simply set true here; turning it off denies
the out-of-scope read while the in-scope one still works. That alone is not
enough, because an allow-list entry names a COMMAND and never a path, so a bare
`command(cat)` walks past it and reaches the whole filesystem — a path-scoped
`read_file(<worktree>/**)` sitting beside one is decoration. With the unscoped
readers removed as well, every out-of-scope route tried came back denied,
including the write that had succeeded on the other host, and the leg still read
its target and ran `git log`.

The lesson is not about one CLI's flag. It is that a boundary has to be
**demonstrated on this machine, in the posture you actually launch with** —
both directions, the denial and the still-works — because the first two
write-ups of this were each internally coherent and each wrong: one blamed the
platform, one concluded nothing enforced it at all and implicitly told the
reader not to try. The first bullet above says reviewers
"deny shell except whitelisted git reads" — true where the list says so, and
this is the reminder to verify that rather than infer it from a flag name. What
did hold on both hosts is the frozen target plus the before/after bracket, the
mechanism §7 already requires.

**A delegate's account of its OWN constraints is not evidence either — and it
is the most convincing wrong answer you will get.** §6 says nothing a delegate
self-reports about its WORK is evidence; this is the same rule one level down,
about what it says it is allowed to do. Measured 2026-09-15: a cursor leg given
identical flags and an identical brief minutes apart executed a shell command
once — returning a host string it could not have guessed — and the next time
declined, writing "Ask mode cannot execute shell" and helpfully adding that no
permission error had occurred because nothing had been attempted. Both runs
reported success. The second answer was a model demurring while phrasing it as
a fact about the runtime, and it was on its way into a data file as a measured
capability.

So: fill a capability claim only from OBSERVED EXECUTION — the command visible
in stderr, a permission decision in the log, or output obtainable no other way
— never from asking the delegate what it can do. And probe a cell until it is
stable, because instability is itself the answer: a capability that appears
half the time is not "available", and recording either half would be a
measurement of one run rather than of the runtime.

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

The same ambiguity reaches the lead one level up, in how a leg's *clearances*
get counted. A positive finding carries its own check: go look, and the looking
settles it. A negative carries nothing on its face, so it is worth exactly what
its method is worth — and the gate below is what tells the two apart.
Measured 2026-08-19, one four-leg round, both kinds in the same round:

- **Evidenced.** A leg asked whether a change was stale ran
  `git log BASE..origin/master` and `git diff --name-only` on both sides,
  reported the two file sets as disjoint, and concluded NOT STALE. Checkable,
  checked, counted.
- **Unsupported.** A leg asked to cross-check identifiers marked a reference
  object EXACT MATCH because the parent object's *name* matched on both sides,
  never noticing its three child fields had been renamed; the same run marked a
  `digest` field EXACT MATCH because the sha256 *format* string matched, never
  asking whether the surrounding closed schema permitted that field at all.
  Both were real defects, independently confirmed by two other legs and by the
  merged schema. Counted as evidence, those rows would have argued *against*
  the correct finding.

An unsupported negative is **no coverage**, and belongs in the round-up as
that — not as agreement.

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

### A refusal is not evidence until you know WHY it refused

"It was denied" and "the boundary I am testing denied it" are different
claims, and the first is what the run actually shows you. Measured 2026-09-15:
a session probing whether a delegate could read outside its workspace got a
clean denial — from a launch that had omitted the very flag that DECLARES the
workspace. With no workspace at all, a denial proves nothing about where the
workspace ends. It re-ran in the real dispatch posture and only then had a
result.

So a negative needs two things the positive does not: the **exact posture you
actually launch with**, and a **positive control** showing the thing still
works where it should.

Refined 2026-09-15 by a session that followed this rule and was still misled:
the control must be in the same *conditions*, NOT in the same prompt. It put
the in-scope and out-of-scope actions in one brief; the out-of-scope one was
denied, the denial killed the run, and it attributed the failure to the
in-scope action. The runtime prints one generic sentence for both (`a tool
required the "read_file" permission … auto-denied`) and never names which
action tripped it. **A signal that cannot distinguish the two cases is not
evidence for either** — which is the same defect as a discarded stderr or a
pipeline reporting `head`'s status. Run the control as its own round. The session's re-run did both — denied
outside, and inside it read the file, ran `git log`, and reported a function's
line range from a thousand-line source. Without the second half a "secure"
configuration and a broken one look identical.

Same family as "test whether it can FAIL" in §6, one step earlier: there the
question is whether a test can go red, here it is whether a red means what you
think.

### A release does not reach a reader until their CACHE moves

Measured 2026-09-16, the day this repo shipped four releases. The plugin is
read from `~/.claude/plugins/cache/dev-lead/dev-lead/<version>/`, one
directory per version, and a session resolves the newest it happens to have.
On the maintainer's own host that was 0.6.5 while master was 0.6.9; another
session was on 0.6.4. The `opencode-go` guidance written that morning had 8
occurrences in the marketplace checkout and 4 in the cache both sessions were
actually reading — **half of a day's releases were invisible to everyone,
including their author.**

The scripts happened to be byte-identical across that gap, so nothing ran
stale; that was luck, not design. Two consequences worth keeping:

- **Resolve tooling from the git checkout when there is one**, because it is
  the copy that moves with the release:
  `DEV_LEAD=${DEV_LEAD_ROOT:-$HOME/.claude/plugins/marketplaces/dev-lead}`,
  falling back to the newest cache directory only when that path is absent.
- **A release note is a claim about what readers will see, and it is wrong by
  default.** Say which version carries a change and that a session must pick
  up the new cache to see it; do not assume a peer who greps "the plugin" is
  reading what you just pushed.

### A guard that fires is worth nothing if the next step does not read it

Measured on this repo 2026-09-16, by its maintainer. A release edit asserted
on a document's structure, the assert was correct, it fired, and the edit did
not apply. The release was then committed with `git add -A` and shipped — so
a commit message claiming the edit had landed went out over a tree where it
had not. The guard did its whole job; nothing downstream consulted it.

`git add -A` is what severed the two. It stages *whatever is there*, which
makes "what I changed" independent of "what I verified" — and the gap is
invisible precisely when an edit silently did nothing. Stage the paths the
edit claimed to touch, or check the exit status before staging; and when a
commit message asserts an outcome, that assertion is a claim like any other
and belongs to the verify-before-you-say rule, not to the prose.

The correction shipped as its own release naming the false sentence, rather
than folding the missing row into the next commit quietly. A commit message
is part of the record: a wrong one gets corrected in the record.

### A success is not evidence until you know which LAYER produced it

The positive-side twin of the rule above, measured the same day. A lead
probing model availability read `opencode models`: all 27 `opencode-go/`
entries listed, both `opencode-go/muse-spark-*-contributor` models among them. Every call to
those two then failed — `This model collects data ... requires explicit opt
in` — because the workspace's training-consent switch was off. The listing
was true; it was produced by the catalogue, which does not consult the
permission layer that gates a run. So the green came from a layer that could
not have known the answer.

The negative rule asks for the exact posture and a positive control. The
positive rule asks one question: **which layer said yes, and is it the layer
that decides?** A catalogue says "exists"; a permission check says "allowed";
only a completed call says "runs". Probe at the layer whose answer you need
— for availability, that is one real call — and never read a lower layer's
yes as the higher layer's.

### A suppressed error makes a failed query look like an empty answer

Measured 2026-09-15, and it cost a false report to three people. A session
checked whether releases existed upstream with
`git fetch origin --tags --quiet 2>/dev/null`, read the refs afterwards, saw an
old tag, and reported the releases missing. The fetch had failed — intermittent
DNS — and `2>/dev/null` had thrown away the only sentence that said so. A
failed lookup and a successful lookup that found nothing produced byte-identical
evidence.

The same run carried the smaller version of it: `... | head -15; echo exit=$?`
reports `head`'s status, not the command's, with no `pipefail` set.

**Stated generally, because it happened three times in one day to three
different people:** after `cmd | filter`, both `&&` and `$?` read the FILTER's
status, never `cmd`'s — and `head`, `tail` and `grep -q` are exactly what we
reach for to keep output short, so the trap rides along with the habit. The
three, all 2026-09-15: a `git worktree remove` that failed with Permission
denied while `… | head -3 && echo "removed cleanly"` printed success — written
by the author of this very rule, hours after writing it; a
`python3 -m venv … | tail -2 && pip install … && echo "INSTALL_OK"` that
printed INSTALL_OK on a host with no `ensurepip`, where neither `pip` nor the
package binary existed; and the fetch above. Set `pipefail`, or put the status
check on the command rather than after the pipe.

**And note how all of them were actually caught, because it bounds what review
can do here.** Four instances were logged that day — the three above plus a
cleanup `rm -rf` that failed against a read-only leftover and silently polluted
the next experiment — and **not one was spotted by reading the line.** Each
surfaced downstream as something impossible: an import that failed after a
green install, a tag present on the remote but missing locally, Permission
denied printing underneath "removed cleanly", a second run whose output carried
errors from a state nobody had set up. Looking harder at the pipeline is not
the countermeasure; `pipefail` is, and so is treating a downstream impossibility
as a signal about the step before it rather than a puzzle in itself. (The
opencode review flow has its own instance of this — a digest that survived a
missing file because `sha256sum`'s failure exited through a pipe.)

**Never discard stderr on a command whose SILENCE you intend to interpret**,
and read the exit status of the thing you actually care about. A query that did
not happen is the most convincing possible "nothing there": it has no error to
argue with. When the answer matters, ask the authoritative source directly —
here, `git ls-remote <full URL>`, or a throwaway clone — rather than reading
your own side's cache and calling it the world.

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
   **A surviving mutant is sometimes a message about the CODE, not the tests
   (measured 2026-09-07).** A gate was written as `all(v.tag == AGENT for v in
   qualifying) and len({v.username for v in qualifying}) < 2`. Four of five
   mutants died; `all()` → `any()` lived. The reflex is to write the missing
   test — but there is none to write: the upstream system emits one row per
   account, so in every reachable state the distinct-count is the row count,
   and with one row `all` and `any` are the same function. It was an EQUIVALENT
   MUTANT, and the only fixture that could have killed it was a shape the
   server cannot produce.

   Shipping the line with a note would have been defensible and was the wrong
   call. The survivor was evidence that a reader could not tell which of two
   readings was meant — the code did not say what rule it implemented. It was
   rewritten as the two questions the rule actually asks (`signed_by_a_person`
   / `agent_accounts`), the ambiguity disappeared, and the re-run went 5/5.
   So: when a mutant survives, ask **"is this untestable, or is it unclear?"**
   before reaching for a fixture. Untestable-and-clear earns a comment saying
   why; untestable-because-unclear is a rewrite. A test invented purely to kill
   an equivalent mutant pins a state that cannot occur, and the next reader
   believes it can.

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
   `hits=<n>`. Record it before the prose pass and again after, using the BARE
   revision both times: corrections are uncommitted at that point and a
   two-endpoint range cannot see them, while the two forms audit different input
   classes — ranged covers prose and commit messages, bare covers prose only, so
   comparing one against the other shows a fall caused by the excluded class
   rather than by any edit. Whatever the audit changed is committed before the
   target is frozen, or it is reviewed by nobody and merged nowhere.

   The number alone is not the verdict. A hit has two legitimate resolutions —
   downgrade the sentence, or pin its premise with a test — and only the first
   moves the count; a pinned claim stands and keeps matching. Counting an
   unchanged total as "no value" would score one of the two prescribed successes
   as zero. So the round records whether any hit produced *either* outcome, and
   the step is deleted only if neither ever happens — the same standard §4
   applies to everything else.

**Grade a runtime-semantics claim by running it, not by reading it.** Three
shapes measured on one peer's rounds, each settled in minutes on a throwaway
instance and each wrong in at least one direction when judged from source:
- a query a leg called "does not match" in fact **failed to parse**, so every
  save rolled back — a worse bug than the one reported;
- two container-orchestration claims (what a profile builds, what `down`
  stops) were false on the installed version, and a third was true and turned
  out to be an authority bypass;
- "no test covers fix X" was false once — settle it by mutating X and
  watching a test go red, never by grep.
Likewise **introduced vs inherited**: settle it mechanically (`git show
<base>:path`, `git blame`) even when the brief asked the leg to, because a leg
mislabels it in both directions.

### Where the suite cannot see: verifying on a real runtime

Some acceptance criteria are not testable by the suite at all — accessibility
focus order, large-type layout, anything whose observable is pixels. There a
green suite proves only that the code declares the right props; the lead has
to go look. Two things learned doing that on an iOS simulator, 2026-08-30:

- **The runtime you are inspecting may be showing you stale layout.** Changing
  the system text size while the app is running (`simctl ui <dev> content_size`)
  leaves React Native's cached text measurements from BEFORE the change. Every
  string on screen renders with its lower half cut off — a perfect imitation of
  a catastrophic clipping bug, on a screen that is in fact fine. Relaunch the
  app after changing any environment setting and re-read before you believe a
  visual defect. This was one screenshot away from a fabricated finding.
- **"Wraps instead of clipping" is not the same as "passes".** The same run
  found a row that had been fixed from clipping to wrapping, and the wrap
  crushed its left column to one character per line. The acceptance clause
  ("no clipping at 200%") was satisfied on its letter while the result was
  unusable. When you write a visual acceptance clause, name the failure you
  do NOT want, not the mechanism you happen to fear.

State plainly which cells of a device matrix you did NOT reach. A matrix
reported as "run" with three uncovered cells is worse than no matrix, because
the next person reads the checkmarks and not the caveat.

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
- **A packet frozen at an old parent produces "file missing" findings.** When
  the change sits many commits behind its target branch, files the change
  cites exist on the target but not in the frozen tree; measured twice in one
  round, and one became a wrong -1. Say in the brief how far the parent is
  behind, or have the leg check `git merge-base` before it claims absence.
- **A diff cannot show a binary's content.** Two legs claimed an image had not
  been regenerated because the range diff said nothing; compare blob ids
  (`git rev-parse <base>:<path>` against `<head>:<path>`) instead. The suite's
  review frames carry this rule.
- **Never create a worktree from the plugin's own marketplace clone.** A plugin
  update re-clones that directory, and every worktree made from it loses its
  `.git` at once — measured: every frozen target of a running experiment broke
  mid-session. Freeze from a standalone clone of the repository under review.

### A new parameter inherits every rule the old parameters already had

Three times on one change, in one day: a rule existed, something new was added
beside it, and the rule was not extended to cover it.

- 0.5.3 added a guard requiring a provider prefix, and wrote it as "starts with
  THE prefix" — correct for the one provider in front of the author.
- The fix for that added `--run-dir`, and the script's existing rule *refuse a
  caller value this template will never consume* was not applied to it, so
  `--run-dir` vanished silently on the adapters that read their brief another
  way. A review leg found it.
- The test written for the new guard was then checked only for the case it was
  written for, and a mutant survived.

The reflex that fails here is testing the new thing: "does `--run-dir` work?"
Yes, it did. Nobody asked the other question, which is cheap and mechanical:
**list the rules that already apply to the parameters beside this one, and walk
them.** Refusal on an unconsumed value; quoting; a per-delivery-mode branch;
a place in the emitted order. Each existing parameter is a worked example of
what the new one owes.

This is the same shape as the copies rule in dev-lead Phase 2 ("Changing a
statement OR A PREDICATE? grep for its copies BEFORE you edit") — fixing one
copy and missing its sibling — and the same remedy applies: where you find
yourself adding a fourth branch, prefer one shared helper that cannot be
half-applied.

### A grep hit is where a STRING is, not where a PROBLEM is

Measured 2026-09-15, owned by the session that made it: a proposal named three
line numbers as defective. Two of them were already correct — they matched the
grep because the searched string appears in a data column there, not because
anything on those lines was wrong. The actual defect was in a SUMMARY sentence
that the grep also hit, and in a second sentence that a reader would have to
notice by meaning rather than by string.

Two consequences, and the second is the expensive one. A grep-seeded audit
tends to report its hits as its findings, so correct lines get "fixed". And it
cannot see the defect that carries no hit at all — a sentence saying "this
model" instead of naming it. Where the hits and the defects diverge, they
usually diverge in the same direction: the structured rows are right and the
prose conclusion is wrong, and **the conclusion is what people remember.** Read
the hits; then read what is around them for the claim they support.

### A finding has three states, and "unverified" is one of them

The rule above runs one way: nothing enters the plan unverified. It has to run
the other way too — **nothing is recorded as FALSE unverified either.** Every
synthesis is three-state:

| state | means |
|---|---|
| `confirmed` | the lead reproduced it against the code |
| `falsified` | the lead reproduced the *rebuttal* against the code |
| `unverified` | nobody checked — say why: quota, timeout, a dead leg |

Measured 2026-09-15 on another repo: a synthesis step computed survival as
`falsified === 0 && holds >= 2`, so when the verifier legs never ran at all —
quota — **228 findings with zero votes each came out as `survives: false`**,
their `why` arrays empty, and the report read as "reviewed, none stood up"
when the truth was "not reviewed". Nothing in the tooling lied; the two-state
bucket had no cell for *unknown*, so unknown fell into the nearest one.

This is the repo's own recurring failure — unknown treated as settled — and it
is worth naming here because the two-state shape is the natural one to write.
An `unverified` count belongs in its own paragraph of the report, never folded
into either verdict, and a round whose verifiers did not run is reported as a
round that did not verify, whatever its findings table looks like.

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

**"Nothing to change" is a result, and a run of finds makes it harder to
report.** Measured as a mood rather than a defect, 2026-09-15: two sessions
spent a day in which almost every check turned something up, and both noticed
the same pull — a stretch of real findings manufactures pressure to produce the
next one, and the cheapest way to satisfy that pressure is to harden something
that was never broken. The tell is a check with no failure case behind it,
which is the decorative-guard shape §4 already names; this is where it comes
from. A concern raised, checked in under a minute, and closed with "the tooling
already refuses this" is worth saying out loud, precisely because it produces
no diff to show for it.

**A free-pool leg that dies gets one retry, then a different model.** Free legs
fail often enough to need a stated policy rather than a judgement call each
time: measured 2026-09-14, `opencode/muse-spark-1.3-contributor-free` returned
0 bytes twice in one round (first a rate-limit error, then silence), leaving
that round with three legs; 2026-09-15, an OpenRouter Nemotron leg returned a
server error. Retry once — transient congestion is the common case and a
plain rerun usually clears it. If it fails again, switch to another free model
rather than spending the round's wall-clock on one queue, and **keep the family
label honest**: swapping model changes the model, not the cross-family
accounting, so say in the report which leg was substituted, by what, and why.
A round that finishes with three legs is a three-leg round and is reported as
one.

## 9. A person approves the result; the lead lands it

**Who approves is configuration** (`merge_gate.mode` in the roster, 0.6.20):
`user` — this section as written — or `lead`, where a FULLY GREEN verdict is
its own approval and the lead lands it, reporting what qualified. The rest of
this section is unchanged by that setting, and so is what counts as green: an
unanswered review round, a failing test, an open blocker or an unverified
finding goes to the person in BOTH modes, and a project contract that is
stricter still wins. Read the mode with `roster.py show`; do not assume it.

The lead assembles a verdict from the run log — rounds, findings and their
fates, test results, diff stat against `$BASE` — and presents it. What the
human approves is that result: the verdict and the diff. Once approved,
merging and pushing are the lead's to do in the same run, without a second
ask (changed 2026-09-11 — the per-push question had been answered yes every
time and protected nothing the verdict did not); the lead pushes only to the
ref the project's contract names and reports it at once. Delegates never
push, in every mode — that is a machine-enforced boundary per adapter, not
a courtesy. Re-verify the target branch's *identity* at the gate, not just
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

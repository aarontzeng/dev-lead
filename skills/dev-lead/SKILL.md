---
name: dev-lead
description: Orchestrate a feature or PR end-to-end as the lead — dispatch implementation to a delegate family based on task risk, model capability, and remaining quota; run bounded adversarial-review rounds with cross-family reviewers; verify everything independently; and hold the merge gate. Use when the user wants a whole feature/fix delegated and supervised, rather than a single implement or review pass.
---

# Dev lead: dispatch → implement → adversarial rounds → merge gate

This is the orchestration layer over the eight family skills:
`{claude,codex,agy,opencode}-implement` and
`{claude,codex,agy,opencode}-adversarial-review`. It adds no new mechanics —
it decides who does what, how many rounds, and when to stop. Read the
underlying skill before invoking it; every operational gotcha (sandbox
allow-lists, workspace traps, silent-death modes, status commands that lie)
lives there and is not repeated here.

The lead role itself is portable: all six CLIs can read the same skills
directory (symlink the others' skills paths to Claude's), and the
`claude-implement`/`claude-adversarial-review` skills supply the missing
direction — Claude as a worker for a codex or agy lead. A codex-led run has
been completed end-to-end (the interactive codex CLI as lead, briefed by a
pilot doc, ran capability probes first and delivered through the merge gate).
The distinction that makes this work: **capability is a property of the
RUNTIME, not the brand** — a companion's restricted sandbox may be unable to
commit in a worktree while the same vendor's interactive CLI under an
approved elevated runner passes the gate. What does not port automatically is
host-specific tooling (harness-tracked background tasks, subagent APIs) — a
foreign lead substitutes its own process management.

**Host capability gate — check BEFORE accepting the lead role.** The lead
must be able to run the suite and execute mutations ITSELF; that is the
non-delegable half of the job. A host whose sandbox cannot bind the sockets
the tests need, or cannot lock a worktree's shared git index, can still be a
fine implementer — but as lead it would have to delegate its own
verification, which collapses the independence the role exists to provide.
If the host cannot run the repo's tests and mutations natively, it does not
lead that repo. An explicitly approved, scoped, reproducible elevated runner
counts as host capability when it covers the required suite, mutation
commands, and local git checkpoint operations — record the exact command
class and approval scope in the run log; never silently substitute elevation
for a failed default sandbox.

The lead is this session's model — a skill cannot switch it, so the user
picks the tier before invoking. Do not trust a system prompt's "you are
powered by X" line when lead identity matters (attribution, quota
accounting): read the transcript's per-message model field, which is the
source of truth. Lead work splits into long-context bookkeeping/verification
(the wall-clock bulk) and a few concentrated-judgment moments (dispatch,
finding verification, merge verdict); on a mid-tier session, an optional
frontier-tier subagent at the Phase-3 verdict check buys judgment exactly
where it pays.

## Phase 0 — Intake (no delegation yet)

Refuse to dispatch until the task has:

1. **Acceptance criteria** concrete enough to verify mechanically.
2. **Scope**: the files/directories a correct change should touch.
3. **Risk class** — decides everything downstream:
   - **HIGH**: auth, money, concurrency, data loss, notification delivery,
     anything with an offline/partial-failure path.
   - **MEDIUM**: behavior changes with test coverage, bounded refactors.
   - **LOW**: mechanical changes, test-only, docs-adjacent code.
   - **A repo plan's own dated risk assessment overrides this table** when it
     is more specific — the table is a prior; the plan author saw the actual
     change. (Measured: a change touched notification delivery — table says
     HIGH — but the plan's dated assessment said MEDIUM with reasons; the
     plan was right and delegation worked.)

Survey the code yourself first — **premise-check the task's factual claims**.
The measured incident: a task said "remove the duplicate query" about a query
that was a deliberate safety re-check; the delegate faithfully removed it. A
wrong premise dispatched is a wrong feature implemented. If the repo has its
own plan discipline, the plan document precedes dispatch.

**Premortem — for HIGH risk, and for any task that deletes or disables
existing behavior.** Before writing the task brief, run one prompt against
the plan: *"Assume this change shipped and failed — we are in the
postmortem. List the scenarios we forgot to consider."* This is the
premise-check systematized, and it attacks the one axis nothing downstream
covers: every later gate (adversarial review, mutation testing,
cross-family) reads a DIFF, and a diff structurally cannot show what the
plan wrongly left out or wrongly marked deletable. Two measured shapes of
that blind spot:

- A plan marked live code as "dead code, delete" — the diff just shows an
  unreferenced block removed; no reviewer has grounds to object. The
  premortem's top-ranked failure was the exact thing that block handled.
- Two operations each individually correct composed into a broken runtime
  behavior (a credential read and a rotated-credential write, each
  OS-prompt-gated, produced a double biometric prompt per login). Three
  review legs passed it, because the defect lived in the composition at
  runtime, not in any hunk. "Imagine the user's complaint after ship"
  reaches it; reading the diff harder does not.

Deletion-shaped tasks get the premortem even at MEDIUM: removing behavior
is where a wrong premise is most expensive and least reviewable. LOW-risk
mechanical work skips it — this is one prompt, not a new phase.

Premortem output is hypotheses, not findings. Verify each named scenario
against the actual code before it shapes the brief; what cannot be
verified goes into the task brief labeled as an unverified assumption for
the delegate and reviewers to check — never silently absorbed as a
requirement. Skipping that verification step turns this into an anxiety
generator that bloats every brief with speculative hardening.

## Phase 1 — Dispatch

**Probe availability first, cheaply.** Each family's runtime file has its
probe: agy has an AUTH_OK one-liner (stale logs describe past runs; the probe
is the only current answer); codex has no reliable quota API, so attempt and
treat a quota error as "unavailable this run"; opencode needs no credential,
but congestion is its availability axis — a cheap one-liner answering in
seconds means go.

Implementer selection (adapt the tiers to your account's catalogue and your
calibration journal — see
[`docs/calibration-journal.md`](../../docs/calibration-journal.md)):

| Situation | Implementer |
|---|---|
| LOW, mechanical sweep, nobody waiting | free pool — zero quota, runs tests natively; congestion makes turnaround unpredictable, so only when wall-clock is cheap |
| LOW, mechanical sweep, time matters | cheapest capable *paid* tier |
| LOW/MEDIUM, spec is clear | cheapest capable paid tier |
| MEDIUM, needs design judgment | a mid/high paid tier; or a second-pool model whose quota is otherwise idle |
| Reasoning-hard delegated work (a fix round failed on reasoning, not integration) | the frontier tier, maximum effort |
| HIGH risk or ambiguous spec | **the lead implements directly** — delegation adds a supervision layer exactly where supervision is hardest |
| Preferred delegate unavailable | another family. All families down → lead implements |

**Switching families mid-run rewrites the task prompt, not just the
command.** The delegates' test policies differ and are not interchangeable:
one family may only be allowed three pinned test-command spellings (any other
spelling silently kills the run with zero output), another runs whatever the
repo needs, a third needs a per-worktree permission config written before
dispatch. Reusing a task file verbatim across a family switch produces a
silent, output-less death that reads like a code problem and is a prompt
problem. Re-read the target skill's task-prompt section on every switch.

**The worktree is missing everything the suite needs that git does not track,
and the delegate will not tell you — it will quietly use something else.**
Worktree isolation is usually discussed as "what is shared" (databases, ports,
the `.git` index). The failure that actually bites is the opposite category:
the virtualenv, the `.env`, the built assets, the fixture cache. They are
gitignored, so they exist only in the main checkout, and a pinned test command
naming any of them cannot run in the worktree at all.

Measured: a lead pinned `cd backend && source venv/bin/activate && pytest …`
with a baseline of "2649 passed". `venv/` is gitignored. The delegate found the
activate script absent, fell back to the login shell's `python`, and reported
**2511 passed / 137 skipped** as its green. Nothing lied — but a different
interpreter's green and the repo's green are not the same claim, and 137 skips
is what missing extras look like when nobody compares the totals. The lead's
own re-run in the worktree then failed at *collection* until `.env` was copied
in, which no test output would have explained.

So, before dispatch: run the pinned command in the worktree yourself, provision
what it needs, and say in the task prompt which interpreter to use. And when a
delegate reports a suite total, **compare it to your baseline digit for
digit** — a total that differs by more than the tests you added is a different
environment, not a different result.

**Matching totals do not clear it, though — the same test can pass there and
fail here.** Measured 2026-08-30: a delegate reported "3 passed" for the three
tests the lead was measuring as failed on the same commit, with identical suite
totals. The sandbox has no network; the test's mock was unbound, so the real
function underneath reached out, raised, and the code returned a fallback the
assertion accepted. No error, no skip, nothing for a totals diff to catch. When
a round turns on specific tests, **re-run those tests yourself** rather than
diffing counts — and treat the divergence as a finding in its own right, since
a test whose verdict depends on the runner having network is a test hitting
live network.

**Never provision a shared directory by SYMLINKING the lead's copy into the
delegate's worktree.** It looks like the cheap answer for a 700-package
`node_modules` and it puts the lead's own installation inside the delegate's
blast radius. Measured 2026-08-30, twice in one day, from a single symlink:

- The delegate's `git add` swept the LINK into its feature commit —
  `.gitignore`'s `node_modules/` (trailing slash) matches directories, not
  symlinks — and the merge then replaced the real directory with a
  self-pointing broken link in the main checkout.
- Both delegates independently judged the link "broken" and replaced it with
  their own install. The `rm -rf` went THROUGH the link and gutted the LEAD's
  `node_modules`: 645 of 693 entries left as empty directories, `.bin` empty.
  Nothing in git was lost, and nothing announced itself either — it surfaced
  an hour later as a launcher that could not find its own binary.

Give each worktree a real directory: its own `npm ci`/`uv sync` (slow, always
correct), or a hardlink copy (`cp -al`) if the ecosystem tolerates it —
separate directory entries, so a delete cannot reach back. And note that this
is not just an efficiency trade: two delegates that "fixed" the link both
produced test runs against an install the lead never verified, which is the
same class of false green as the interpreter mismatch above.

**"Provision" is not "copy the real one."** The untracked file the suite wants
is very often the one holding every credential the project has, and a write
delegate has its whole worktree inside its sandbox — so copying it in hands a
third-party model your API keys, your database URL, and, in the repo this was
measured on, a **broker** key that moves real money. Twenty-two credential keys
in one `.env`. Never that.

The split that keeps both halves honest:

- **Delegate worktree** — the minimum sanitized config, and nothing that is
  secret. Better still, let the delegate mint its own: measured on the same
  run, the delegate hit the missing config, set a throwaway
  `SECRET_KEY='test-only-<slug>'` inline, and completed 2511 tests without ever
  needing a real value. The safe path is not a compromise here; it is what
  actually happened, unprompted.
- **The lead's own verification run** — the real file is fine. The lead already
  holds these credentials; using them is not an exposure. This is the run whose
  total is authoritative anyway.

If the suite genuinely cannot start without a real secret, that is a finding
about the repo's test setup, not a reason to ship the secret into a sandbox.

Reviewer — the cross-family rule is absolute (implementer's family never
reviews its own change). Review is the leverage point, so it gets the
strongest tier available:

| Implementer | Reviewer(s) |
|---|---|
| Gemini family | GPT, Claude, or a named free-pool model |
| GPT family | Gemini, Claude, or a named free-pool model |
| named free-pool model (DeepSeek/Nemotron/…) | GPT, Gemini, or Claude — all cross-family by construction |
| stealth free-pool model (family undisclosed) | cross-family is UNVERIFIABLE — any reviewer might secretly share its family. Prefer a named-family implementer when accounting matters; otherwise take two reviewers from two different KNOWN families |
| the lead itself | any family that is not the lead's. Never review the lead's work with the lead's own family — even through a different CLI |
| HIGH risk, any implementer | **two independent reviewers from two families** — measured: two families independently converging on the same root cause was itself the strongest signal the finding was real |
| MEDIUM risk, when a free leg is available | **take the second reviewer anyway.** Measured: on a MEDIUM change, two families each returned 2 real defects with zero overlap. Convergence is the strong signal when it happens; disjoint coverage is the ordinary case, and a zero-quota second leg costs only wall-clock. Run them concurrently against the same frozen commit |
| Re-review of a prescriptive fix round | a mid tier is enough — the changes follow written findings; save the frontier tier for open-ended hunts |
| **Owner standing ruling (2026-08-30, this account)** | **default WIDE, not minimum**: beyond the accounting leg, take the free/cheap extra legs by default — a Laguna (OpenRouter :free) or free-pool leg, plus a cheap GPT tier (e.g. luna) challenge leg — whenever wall-clock allows. The owner asked "why only two legs" twice in one day; the minimum table above is the floor, not this account's default. Distinct briefs per leg, as always |

**When you spend a second or third leg, change the BRIEF, not just the
model.** The table above is the accounting rule, not the coverage rule.
Measured: three legs over one design ruling, each given a deliberately
different job — one walked execution sequences, one was asked to challenge
whether the approach was right at all, one audited for text that was
superseded but still read as live. Their principal findings overlapped zero
percent, and each leg found the only instance of its own class. The roles
worth splitting, in the order they tend to pay: **sequences**, **challenge**
(never phrased as "find defects"), **consistency**, **is-it-still-true**, and
**falsifiability** — can each test here actually FAIL? Give that last one to a
leg whenever the change ADDS a guard or a gate, because a green suite is
equally convincing whether or not the suite is able to go red (measured: on one
three-leg round it was the only lens that found anything, and it found five;
see [`docs/methodology.md`](../../docs/methodology.md) §2).

Quota economics: when one model is scarce, spend it on **review**, not
implementation — review leverage is higher, and implementation has more
substitutes.

## Phase 2 — Rounds (bounded)

`ROUNDS_MAX = 3` implementation rounds by default (R1 + two fix rounds); the
user can set it at invocation. Each round:

1. **Implement/fix** in the worktree (same worktree across rounds, new
   commits on top). R2+ task prompts quote each verified finding **verbatim,
   with why it is real and what fix is required** — this shape fixed
   everything first-try in live runs; vague "address the review" has no track
   record.

   **Background the WORK, not the launch.** Whatever the host uses to notify
   you that a background task finished must be wrapped around the thing that
   actually takes the minutes. A foreground CLI (`agy`, `opencode run`,
   `codex exec`) already is that thing — hand it over directly and never add
   an inner `&`/`nohup … &`, which makes the host task exit at launch and
   discards the notification. A launcher that returns immediately (the codex
   companion's `task --background`) needs a second backgrounded step that
   blocks until the job is terminal; each runtime file names its own waiter.

   Measured 2026-08-25, one session: three consecutive rounds finished 14, 20
   and 40+ minutes before the lead noticed, every time only because the human
   asked — and two of those were delegate refusals the lead had explicitly
   asked for and should have acted on at once. The delegate was not slow; the
   lead was not listening. In the same session an `agy` leg launched with an
   inner `&` reported "completed" in under a second and had actually died.

   Do not substitute a hand-rolled poll loop between turns. It is a live task
   the user can interrupt, and an interrupted poll is indistinguishable from
   the job ending.

   **Dispatching a leg to a SUBAGENT inverts that last rule — say so in the
   prompt.** A subagent driving an external CLI is a *leaf*: nothing will wake
   it when the CLI exits, because the host's completion notification goes to
   the session that spawned it, not to the spawned one. So a leaf that ends
   its turn to "wait for the monitor" is not waiting — it has silently
   abandoned the run, and only a human noticing gets it back. For a leaf the
   blocking poll the lead must avoid is exactly the right answer, provided it
   lives *inside a single tool call* (`while kill -0 $PID 2>/dev/null; do
   sleep 15; done`, generous per-call timeout, another such call immediately
   if the first times out) rather than across turns. Spell this out in the
   dispatch prompt; the review skills describe how to *launch*, and a leaf
   that follows only that stops one step early.

   Measured 2026-08-31, one session: across three 4-leg rounds this cost five
   manual re-prompts — opencode, codex and cursor legs each ended a turn on
   "waiting for the background monitor", and one leg needed two nudges before
   it stopped reaching for the host's monitor tool and wrote a real loop. Two
   phrases predict the failure and belong in the prompt as explicit
   prohibitions: *"I'll report once the notification arrives"* and *"still
   running, continuing to wait"*. Neither is a valid end state for a leaf.

   **Measured effective 2026-09-01, for SUBAGENT dispatch specifically**: a
   4-leg round run through four subagents, each prompt opening with the leaf
   paragraph above, took **zero** manual re-prompts — every leg blocked to
   completion on its own (160 s to ~14 min). Same lead, same four adapters,
   same host as the five-nudge session. Carry the paragraph into the prompt;
   do not assume the skills alone reach a leaf.

   **But the stronger move is to not create a leaf at all.** A peer lead ran
   two 4-leg rounds the same day with zero re-prompts and no leaf paragraph
   anywhere, because it launched each CLI directly as a backgrounded job from
   its own session: the host's completion notification then comes back to the
   session that can act on it, and this failure mode cannot arise. Reserve
   subagent dispatch for when you actually need the leg's tool-call traffic
   kept out of your context; when you don't, dispatch directly. Do not read
   the measurement above as "the paragraph is what makes 4-leg rounds work" —
   it is what makes them work *once you have already chosen* the shape that
   can strand a leg.
2. **Lead verifies independently** — never from the delegate's self-report.
   Order matters when the work comes back uncommitted (some sandboxes cannot
   commit in a worktree at all): FIRST inspect the working tree itself
   (`git status --short`, `git diff` — a `$BASE...HEAD` range on an
   uncommitted tree is empty and reads as a clean scope check, a measured
   false green), re-run the tests, THEN the lead stages in-scope paths and
   makes the checkpoint commit, and only then do ranged checks,
   commit-message hygiene, and **mutation-proof every new regression test**.

   **Changing a statement OR A PREDICATE? grep for its copies BEFORE you
   edit.** The same sentence usually lives in three places — the implementation
   comment, the header/API doc, and the operator-facing log line — and fixing
   the one you happen to be in leaves the others contradicting it. Measured:
   the same correction was made three times across three review rounds, each
   round catching one more surviving copy. Tests never catch this class.
   `grep -rn` a distinctive phrase from the sentence you are about to change,
   fix every hit in the same edit, and say in the commit how many there were.

   **A wrong test predicate copies exactly the same way, and the rule used to
   miss it because it only said "statement".** Measured, a later round: a probe
   decided its verdict with `"Permissions Violation" not in r`. That is wrong —
   a REJECTED LOGIN also lacks that string, so it reported ALLOWED — and the
   expression appeared four times in one file plus once in a sibling. Fixing
   the two instances review named would have left twelve required-positive
   checks with the same hole. Here the rule's own line, "tests never catch this
   class", was literally true: every suite was green with all four copies
   broken, because the copies WERE the suite.

   So before changing any decision expression — a verdict, a guard, a
   comparison a test's result turns on — `grep -rn` the expression itself, not
   just its surrounding prose. Then prefer collapsing the copies onto one
   function over correcting each: the count is what makes the next occurrence
   impossible, and correcting N copies leaves N places for the next person to
   fix N-1 of.

   **A working-tree restore invalidates every verification behind it,
   including the commit message you already wrote.** Measured 2026-08-30: a
   `git checkout --` (aimed at a concurrent agent's stray edit) also reverted
   the lead's own uncommitted fix; the checkpoint commit then landed with a
   message describing behavior that was no longer in the tree. Nothing
   downstream catches that — review reads the diff, and the diff was honest
   about what it contained; only the prose was wrong. So after ANY restore,
   stash, or reset in the worktree, re-read the diff against the message,
   re-run the suite, and re-run the mutations, all against the **committed**
   tree. Related, from the repo side: never `git checkout` to undo your own
   uncommitted work while another agent shares the checkout.

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
   "#10302 PS10 carries a +1", linked to a host that had since been
   decommissioned: a perishable fact behind a dead link, in the document that
   had just declared such facts invalid. And an operational gate — whether an
   operator may enable a mode — read "current-patchset review", so it changed
   when the review tool changed rather than when the design or the software did.

   **The test is MONOTONICITY, not volatility**, and getting this wrong makes
   the rule worse than not having it. "Change 10943 is merged" is volatile in
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

   Mutation mechanics live in
   **[`references/mutation-runbook.md`](references/mutation-runbook.md)** —
   read it before this step. Every clause in it was paid for with a live
   false result: a harness that reported a mutant killed when it was not, a
   restore that destroyed the implementation, a stale binary that made
   correct code look broken. The three that bite earliest: **commit before
   any mutation** (a restore on an uncommitted tree erases the work),
   **never `&&`-chain the test run into the verdict echo** (a killed mutant
   exits non-zero — that IS the success signal), and **put the marker check
   in its own command** (a timeout kills the restore, and the mutant stays).

3. **Adversarial review** per the reviewer table, against the same immutable
   `$BASE` — which is `git merge-base <target-branch> HEAD`, NOT the target
   branch itself (see [`docs/methodology.md`](../../docs/methodology.md) §7;
   the review skills carry the pre-launch
   `git diff --stat "$BASE" HEAD` guard). **The reviewed directory
   must be FROZEN for the whole run** — a detached worktree at the exact
   commit, that nothing else touches. A reviewer reads the working tree, not
   your commit: a measured round ran mutation testing in the same worktree
   mid-review and the reviewer opened a CRITICAL on a mutated, non-compiling
   file. One directory per reviewer, no lead activity inside it. Use the
   tested helpers rather than hand-rolling this — it is the repo's
   highest-risk shell, and every bug ever found in it was in a hand-rolled
   copy:

   ```bash
   # The helpers live in the SUITE's tree; cwd is the TARGET repo. A bare
   # `scripts/…` resolves against the target and exits 127 — which lands you
   # in the hand-rolled copy this paragraph just warned about.
   DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
   for helper in freeze-target.sh verify-target.sh; do
     [ -x "$DEV_LEAD/scripts/$helper" ] || { echo "dev-lead root unresolved (no $helper) — set DEV_LEAD_ROOT to your checkout"; exit 1; }
   done

   REVIEW_HEAD=$("$DEV_LEAD/scripts/freeze-target.sh" "$REPO" "$SHA" "$FROZEN_DIR")
   "$DEV_LEAD/scripts/verify-target.sh" "$FROZEN_DIR" "$REVIEW_HEAD"   # before AND after
   ```

   **Tear the frozen directories down only after every leg has actually
   returned, and read the leg's own exit line rather than the wrapper's.**
   A background launcher usually runs with its cwd inside the frozen
   directory; removing that directory out from under a still-open shell
   makes the wrapper fail on something unrelated — measured, a `pwd` after
   teardown produced `getcwd: cannot access parent directories` and the
   harness reported the task as exit 1, while the leg's own line in the
   same output read `exit=0` and its report was complete. A leg declared
   dead by its wrapper's exit code is the same class of mistake as a leg
   declared empty by one grep: the signal you read was not the signal you
   wanted.

   Findings are
   hypotheses: verify each against the code, reject false ones explicitly,
   record **every** finding in the run log. **A rejection carries evidence of
   the same grade the finding needed** — the `file:line` that refutes it, or
   a command actually run. "I judged it wrong" is not a rejection; it is an
   unaudited veto at the one step where the lead reviews nobody but itself.
   **What the lead fixes directly never gets reviewed, and that is where the
   next defect is.** The fix round runs after the legs have returned, so the
   lead's own edits — the ones the skill explicitly encourages for small,
   precisely diagnosed defects — enter the merge with zero cross-family eyes on
   them. Measured: in one round the only BROKEN verdict any leg returned was
   about a test the LEAD had written after the delegate finished, and it was
   right. Either fold lead fixes into a re-review, or at minimum hold them to
   the mutation standard you hold the delegate's work to, and say in the run
   log which parts of the final diff no leg ever saw.

4. **Route**: no verified blocking findings → merge gate. Verified findings →
   next round. Small, precisely diagnosed defects (a fake test, a stray
   trailer) the lead fixes directly in-place — a delegation round for a
   one-line fix costs more than it protects.

**Stop conditions** (report instead of looping):

- Cap reached with verified HIGH findings open → keep the worktree, present
  the findings history, let the user decide. Do not merge.
- A fix round introduces a **new** HIGH finding (fix churn) → the spec or the
  delegate is wrong for this task; lead takes over or stops.
- The same finding survives two fix rounds → the task prompt is failing to
  transmit it; lead fixes it directly.
- The same finding CATEGORY keeps reopening against approximation-shaped code
  → the review property is unbounded, not the code unfixable. Declare the
  approximation's scope in the docstring and re-scope the review to it
  (measured: four rounds of one-more-nesting-level findings converged to a
  real in-scope catch and then an approve, the round after the boundary was
  written). **Write the boundary before the review is FIRED, not after it
  comes back** — the lead always knows the boundary; the reviewer is the one
  who does not. It applies to more than parsers: any check that answers a
  question about the world through a proxy is approximation-shaped and needs
  its scope stated.

## Phase 3 — Merge gate

Assemble the verdict from the run log: rounds used, implementer/reviewers per
round, findings (verified/rejected/fixed) with their fate, final test
results, diff stat against `$BASE`.

**Re-verify the target branch's IDENTITY at the gate, not just its
cleanliness.** Measured: the user rebased the main checkout mid-round; merged
versions of earlier commits came back with new SHAs, `--ff-only` refused —
that refusal is the guard working; never switch to `--no-ff` to get past it.
Rebase the branch onto the moved target and re-run the ff. The human owns the
main checkout; assume it moves.

- **Default (`merge-gate=user`)**: present the verdict and the diff summary;
  merge (fast-forward) and tear down the worktree only after the user says
  proceed. The human approves the diff, not the intention.
- **`auto-merge`** — only when the user explicitly granted it at invocation
  for this run: on a fully green verdict, merge, tear down, and report what
  was merged and why it qualified. Any non-green condition falls back to the
  default gate. The grant is per-run, never remembered.
- If the session model is not the strongest available, an optional
  independent verdict check: spawn a frontier-tier subagent with the run log
  and the final diff, asking only "does anything here disqualify a merge?" —
  a cheap second judgment exactly at the decision that is hardest to walk
  back.
- Push is human-only in every mode. No exceptions.

## Run log

Keep one per run (`mktemp -d`, e.g. `$RUN_DIR/run-log.md`), appended as
events happen, not reconstructed afterward: dispatch decision and why; per
round — task file path, delegate, commits, test results, review findings with
verified/rejected/fixed status **and, for each rejection, the evidence that
refutes it**; stop-condition hits; the final verdict. The final report to the
user is written from this log. If the run is interrupted, the log plus the
preserved worktree is the resume state.

Record every delegate dispatch as three separate fields, because they
diverge and the cross-family accounting reads only the last one:

- `runtime_adapter`: claude / codex / agy / opencode — the CLI driven
- `served_model`: the model that actually answered — VERIFIED per the
  runtime file (some adapters silently substitute tiers), not the id you
  passed
- `model_family`: Claude / GPT / Gemini / DeepSeek / Nemotron / unknown —
  what the reviewer-pairing rule is checked against; `unknown` (stealth
  models) can never satisfy it

## What this is not

Not a way to run more agents for their own sake — a task small enough for one
direct implementation should get one (the implement skills say the same). The
workflow earns its cost when the change is big enough that independent
implementation and cross-family review genuinely reduce risk, or when the
user wants the feature produced while the session works on something else.

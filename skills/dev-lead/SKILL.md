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

**When you spend a second or third leg, change the BRIEF, not just the
model.** The table above is the accounting rule, not the coverage rule.
Measured: three legs over one design ruling, each given a deliberately
different job — one walked execution sequences, one was asked to challenge
whether the approach was right at all, one audited for text that was
superseded but still read as live. Their principal findings overlapped zero
percent, and each leg found the only instance of its own class. The roles
worth splitting, in the order they tend to pay: **sequences**, **challenge**
(never phrased as "find defects"), **consistency**, **is-it-still-true**
(see [`docs/methodology.md`](../../docs/methodology.md) §2).

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
2. **Lead verifies independently** — never from the delegate's self-report.
   Order matters when the work comes back uncommitted (some sandboxes cannot
   commit in a worktree at all): FIRST inspect the working tree itself
   (`git status --short`, `git diff` — a `$BASE...HEAD` range on an
   uncommitted tree is empty and reads as a clean scope check, a measured
   false green), re-run the tests, THEN the lead stages in-scope paths and
   makes the checkpoint commit, and only then do ranged checks,
   commit-message hygiene, and **mutation-proof every new regression test**.

   **Changing a statement? grep for its copies BEFORE you edit.** The same
   sentence usually lives in three places — the implementation comment, the
   header/API doc, and the operator-facing log line — and fixing the one you
   happen to be in leaves the others contradicting it. Measured: the same
   correction was made three times across three review rounds, each round
   catching one more surviving copy. Tests never catch this class. `grep -rn`
   a distinctive phrase from the sentence you are about to change, fix every
   hit in the same edit, and say in the commit how many there were.

   **Writing a NEW statement? Name what would make it false.** The grep rule
   above catches a sentence that went stale; it cannot catch one that was false
   on arrival, and mutating the code under test does not reach it either — that
   sentence is not in anything the suite executes (measured: 20 mutants died in a
   round whose three false sentences all survived). A lint rule *over* prose is
   executable and can be mutation-proofed; the prose it judges is not. Run
   `python3 "$DEV_LEAD/scripts/claim-audit.py" "$WORKTREE" "$BASE...HEAD"` and
   answer both of its questions per hit. It is a worklist, not a gate — it exits
   0 either way, and a legitimate absolute costs seconds to dismiss. Two shapes
   no filter can flag, so ask them yourself: a **right conclusion resting on a
   wrong mechanism** (a doc said two rows "return the same shape, so this is not
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

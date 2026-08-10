---
name: codex-adversarial-review
description: Run a read-only Codex challenge review of a git diff through the Codex companion plugin, then verify each finding against the code. Use when the user asks for an adversarial or challenge review from Codex/GPT, wants a second opinion on a design or implementation, or needs the review gate after codex-implement.
---

# Codex adversarial review

Use the companion's built-in `adversarial-review` command, not a hand-written
Codex CLI call. The companion runs the review with a `read-only` sandbox and
tracks it as a job. This is a challenge review: inspect chosen boundaries,
assumptions, failure modes, and trade-offs, not only local defects.

## Before the first run of a session

Read **`references/codex-runtime.md`** (same directory). It holds the
family-level mechanics shared with `codex-implement` — companion resolution,
the `--background` launcher-output trap, `status` lying about liveness, the
lossy-pipe report loss, report recovery from session rollouts, sandbox
limits, and the model/effort plumbing. Every item was paid for with an
incident; this file assumes them and covers only what makes a run a
*review*.

## Establish an immutable review target

Run from the target repository or implementation worktree. Capture the exact
base SHA and require a clean review target before launch:

```bash
git status --short
BASE=$(git rev-parse HEAD~1)
REVIEW_HEAD=$(git rev-parse HEAD)
```

For a worktree created by an implement skill, use its recorded `BASE`. For a
single committed change use `HEAD~1`. Do not review a moving or partially
staged implementation; finish and commit first.

**For a multi-commit topic branch, `BASE` is the merge-base — never the
branch it targets.** `BASE=$(git merge-base origin/main "$REVIEW_HEAD")`.
Writing `BASE=origin/main` is the natural move and it is wrong the moment the
target has advanced past the branch point: the range then carries the
author's change *plus the reversal of everything the target gained
meanwhile*, so the delegate reviews other people's commits as if they were
the author's.

The same trap hides in the diff spelling, because the two ranges look
identical: `git log A..B` is genuinely "commits in B not in A" and is the
range you want, but `git diff A..B` means `git diff A B` — **not**
merge-base. Use `git diff A...B` (three dots) or diff against the captured
`BASE`.

Nothing errors, and the HEAD assertion below still passes — it proves you
launched at the right commit, not that the delegate read the right span.
Measured: a target 10 commits ahead of the branch point turned a 3-file
review into one that also read two other teams' files. Confirm the span
before launching, not after:

```bash
git diff --stat "$BASE" "$REVIEW_HEAD"    # file list must match the change under review
```

## Launch one tracked read-only job

Resolve the newest installed plugin without hardcoding a home or version:

```bash
REVIEW_WORKTREE=/absolute/path/to/target-worktree
SCRIPT=$(ls -d "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs \
  2>/dev/null | sort -V | tail -1)
[ -n "$SCRIPT" ] || { echo "codex plugin not installed"; exit 1; }

cd "$REVIEW_WORKTREE" && \
  [ "$(git rev-parse HEAD)" = "$REVIEW_HEAD" ] && \
  node "$SCRIPT" adversarial-review \
    --background --base "$BASE" --scope branch --model <model> "$FOCUS"
```

The identity assertion between the `cd` and the launch is not decoration —
measured: a launch composed without the `cd` (a stale shell cwd pointing at a
DIFFERENT repo) started a real, billed review of the wrong tree, caught only
by a human noticing before it finished. An in-command HEAD check makes a
wrong target fail loud in milliseconds instead.

Launch under the host's own background mechanism with output redirected to a
file, per the runtime's launcher rules (`--background` does not guarantee a
prompt return; a lossy pipe can destroy the only copy of the report).

## Model choice

The review path takes `--model` only; depth otherwise comes from the user's
global config (runtime file has the plumbing). Pick per your calibration
journal, with two measured priors:

- Review is the highest-leverage step — when quota allows, spend the
  strongest tier here rather than on implementation.
- **Match the model to the ROLE.** A fast tier that underperforms at defect
  hunting produced the deepest finding of a five-round sequence when given
  the *challenge* brief ("is this the right approach at all, what assumption
  is unexamined") — while a stronger model on a sequence brief returned an
  under-evidenced HOLDS on the same question. When you spend multiple legs,
  change the brief, not just the model; identically-briefed legs buy
  redundancy, differently-briefed legs' findings overlapped zero percent.

Omitting `--model` inherits the user's codex-config default — pass it
explicitly rather than inheriting silently.

The companion enforces `read-only`; do not add any bypass flag. After the job
completes, verify the review itself changed nothing:

```bash
test "$(git rev-parse HEAD)" = "$REVIEW_HEAD"
test -z "$(git status --porcelain)"
```

If either check fails, stop and report the unexpected mutation before
trusting the review output.

## Write a useful focus prompt

Supply focus text with all of the following:

- Declare first-party, pre-merge review; do not use third-party attack
  framing.
- Require read-only behavior: no edits, commits, pushes, or recursive CLI
  invocation.
- Name the claimed properties and the files that implement them — and when
  the code is a **declared approximation** (a heuristic, a parser-shaped
  regex, a best-effort masker), the property must state its BOUNDARY, not
  just its intent. An unbounded property cannot converge — each round
  legitimately finds one more case, forever (see
  `docs/methodology.md` §5). The boundary belongs in the code's own
  docstring too, so the reviewer checks declared-vs-actual instead of
  declared-vs-infinite.
- Ask to falsify each property under concurrency, partial failure, retries,
  stale state, malformed input, and boundary conditions relevant to the
  change.
- Require each finding to include a trigger, observable consequence,
  severity, and `file:line`; ask which claims still hold.
- State fixes already made so the review targets the current diff.
- **Ask what the tests do not enumerate**, in those words: "name any property
  claimed above that no test actually exercises, and any test that passes for
  the wrong reason." Measured: that one line produced the most valuable
  output of its round — a coverage note ("your tests only cover the A→B
  transition, never A→∅ or ∅→B") that explained why two real defects had
  survived a mutation-tested, fully green suite.
- Forbid praise and generic summaries.
- Tell it NOT to query MCP/memory tools: "the diff and this focus text are
  the complete context." The reviewer inherits the user's full codex config
  and, measured, spends its opening turns on tool discovery it then
  discards; this line skips that for free.

When Codex implemented the change, this review is a fresh context but not
model diversity — it does not satisfy the cross-family rule **at any risk
level** (`docs/methodology.md` §1, repo root). Use it as a supplement; the
gate reviewer must be non-GPT, and never present two Codex jobs as
independent models.

## Watching the run and reading the report

All in the runtime file: liveness is log mtime (never `status`), the two
delivery modes, the ~20-minute patience rule around wait tools, truncated
captured messages, and the interim-message grep that rescues findings from
"empty" rounds. Follow them; do not re-learn them.

## Verify and report

Treat Codex output as hypotheses. Verify every finding against the code and,
when possible, an executable trigger. Report findings faithfully, identify
false positives explicitly, and separate evidence discovered by the host from
the reviewer's claims. This skill is review-only: do not apply fixes unless
the user asks.

Pairing rule across the suite: the reviewer must come from a different model
family than whatever implemented the change. When quota is tight, spend the
scarce model on review rather than implementation — review leverage is
higher.

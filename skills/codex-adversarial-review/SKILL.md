---
name: codex-adversarial-review
description: Run a read-only Codex challenge review of a git diff with `codex exec` and the suite's adversarial framing (the Codex companion's adversarial-review is the fallback), then verify each finding against the code. Use when the user asks for an adversarial or challenge review from Codex/GPT, wants a second opinion on a design or implementation, or needs the review gate after codex-implement.
---

# Codex adversarial review

Run the review as `codex exec` in a `read-only` sandbox, with the lead's lens
wrapped in the suite's adversarial framing
([`references/adversarial-framing.md`](references/adversarial-framing.md)) and
the effort set per run. Since 0.6.28 this is the default: in an equivalence
test on three frozen targets with known answers (codex-runtime.md, 2026-09-23)
it matched or beat the companion's `adversarial-review` on known-answer
recall and false positives, read the base revision in every run where the
companion did in fewer than half, and — unlike the companion — takes the
effort the roster or triage asks for. The companion stays as the fallback
below. This is a challenge review: inspect chosen boundaries, assumptions,
failure modes, and trade-offs, not only local defects.

## Before the first run of a session

Read **[`references/codex-runtime.md`](references/codex-runtime.md)** (same
directory). It holds the
family-level mechanics shared with `codex-implement` — companion resolution,
the `--background` launcher-output trap, `status` lying about liveness, the
lossy-pipe report loss, report recovery from session rollouts, sandbox
limits, and the model/effort plumbing. Every item was paid for with an
incident; this file assumes them and covers only what makes a run a
*review*.

## Establish an immutable review target

**One frozen directory per reviewer, at the exact commit, that nothing else
touches — no lead activity inside it.** Not "whenever possible": a reviewer
reads the WORKING TREE, not your commit. Measured — a round ran mutation
testing in the same worktree mid-review and the reviewer opened a CRITICAL on
a mutated, non-compiling file it was never meant to see.

Freeze it with the suite's tested helpers instead of hand-rolling the shell;
every bug ever found in this step was in a hand-rolled copy. The suite-root
resolver and both calls — `freeze-target.sh` to create it, `verify-target.sh`
before AND after the run — are in
[dev-lead Phase 2](../dev-lead/SKILL.md), and the reasoning is in
[methodology.md](../../docs/methodology.md) §7. Everything below assumes
`$REVIEW_TARGET_DIR` is that frozen directory and `$REVIEW_HEAD` is the SHA it
was frozen at.

Capture the exact base SHA and require a clean review target before launch:

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

## Launch the review: `codex exec` with the suite's framing

Compose it with `leg-cmd.sh`, which emits both steps chained with `&&`: the
builder wraps the brief (the LENS, in `$RUN_DIR/prompt.md`) in the framing,
then `codex exec` reads the framed prompt from stdin:

```bash
DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
eval "$("$DEV_LEAD/scripts/leg-cmd.sh" codex review --model <model> --effort <tier> \
  --base "$BASE" --target "$REVIEW_TARGET_DIR" --run-dir "$RUN_DIR")" > "$RUN_DIR/review.log" 2>&1
```

What it runs, spelled out (for a lead without the script):

```bash
set -euo pipefail
python3 "$DEV_LEAD/scripts/codex-review-prompt.py" --base "$BASE" --target "$REVIEW_TARGET_DIR" \
  < "$RUN_DIR/prompt.md" > "$RUN_DIR/framed-prompt.md"
"$DEV_LEAD/scripts/verify-target.sh" "$REVIEW_TARGET_DIR" "$REVIEW_HEAD"
rm -f "$RUN_DIR/review.md"
rc=0
codex exec -C "$REVIEW_TARGET_DIR" -s read-only -m "$MODEL" -c "model_reasoning_effort=$TIER" \
  -o "$RUN_DIR/review.md" -- - < "$RUN_DIR/framed-prompt.md" > "$RUN_DIR/review.log" 2>&1 || rc=$?
"$DEV_LEAD/scripts/verify-target.sh" "$REVIEW_TARGET_DIR" "$REVIEW_HEAD"
[ "$rc" -eq 0 ] || { echo "codex exec exited $rc; see review.log" >&2; exit 1; }
used=$(sed -n 's/^reasoning effort: //p' "$RUN_DIR/review.log" | head -1)
[ "$used" = "$TIER" ] || { echo "effort used '$used', wanted '$TIER'" >&2; exit 1; }
[ -s "$RUN_DIR/review.md" ] || { echo "no review written" >&2; exit 1; }
```

- `$RUN_DIR` lives outside the frozen worktree; `$BASE`, `$REVIEW_HEAD` and
  `$REVIEW_TARGET_DIR` are set as above. Quote `$MODEL`: an unquoted `<…>`
  placeholder is a shell redirection.
- The builder ([`scripts/codex-review-prompt.py`](../../scripts/codex-review-prompt.py)) substitutes in one pass, so a
  `{{HEAD}}` inside the lens stays literal; it refuses an empty lens, an
  unknown placeholder or an unreadable HEAD, and the `&&` stops the paid run.
- The prompt goes on stdin (`-- -`): no argv length cap, a prompt that starts
  with `-` stays text (a `-s workspace-write` first line did not change the
  sandbox; codex-cli 0.156.1), and codex never waits on an inherited stdin.
- `-C` must name a directory inside a git repository; outside one codex
  prints `Not inside a trusted directory …` and exits without reviewing.
- `review.md` holds the final message only; `review.log` holds the tool calls
  and the `reasoning effort:` header — the only record of the effort that
  actually ran, and where "did it read the base revision" is checked.
- `codex exec review --base` is not a substitute: it refuses a custom prompt
  together with `--base` (codex-cli 0.156.0), so neither lens nor framing
  can ride on it. Inlining the diff into the prompt showed no gain.

The report ends with a plain `Verdict: approve` or `Verdict: needs-attention`
line, which [`scripts/leg-log-check.sh`](../../scripts/leg-log-check.sh) accepts; check it before reading.

### Fallback: the companion's `adversarial-review`

Use it only when `codex exec` is unavailable, and say so in the report. It
supplies its own framing and tracks the run as a job, but **it has no effort
flag**: the review runs at `model_reasoning_effort` from the machine's
`~/.codex/config.toml`, whatever the roster or triage asked for. Read that
value and report it; do not assert one.

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

**Running this leg as a subagent? You are a leaf — block, do not "wait".**
Nothing will wake you when the job finishes; ending your turn on "waiting for
the notification" abandons the review. Poll to terminal inside a single tool
call, and issue another such call immediately if it times out
([dev-lead Phase 2](../dev-lead/SKILL.md)).

## Model choice

The exec path takes both `--model` and the effort: use the roster's review
leg, raised to triage's `effort` for the change when it gives one (the
table only raises). The companion fallback takes `--model` only and runs at
the machine's config effort (runtime file has the plumbing). Pick per your
calibration journal, with two measured priors:

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

Both paths run `read-only` (`-s read-only` on exec; the companion enforces
it); do not add any bypass flag. After the run, verify the review itself
changed nothing — `verify-target.sh` in the block above, or by hand:

```bash
test "$(git rev-parse HEAD)" = "$REVIEW_HEAD"
test -z "$(git status --porcelain)"
```

If either check fails, stop and report the unexpected mutation before
trusting the review output.

- **Materializing evidence the frozen tree does not contain** — another
  revision's copy, a merge-base baseline — has cross-family rules on where it
  goes and what guarantees it carries:
  [`docs/materializing-evidence.md`](../../docs/materializing-evidence.md).
  Never write scratch files into the frozen target; a leg that genuinely cannot
  keep them elsewhere declares those exact paths to `verify-target.sh` instead.

## Write a useful focus prompt

The framing already tells the reviewer to read every touched file at the
base revision with `nl -ba`, to stay read-only (no tests, builds, MCP or
external tools), to separate introduced from inherited defects, to check
whether a later check mitigates a hazard, to answer each posed claim with
HOLDS / BROKEN / NOT REACHED, and to end with a plain verdict line. The lens
you write supplies the rest (on the companion fallback, include all of it):

- Declare first-party, pre-merge review; do not use third-party attack
  framing.
- Require read-only behavior: no edits, commits, pushes, or recursive CLI
  invocation.
- Name the claimed properties and the files that implement them — and when
  the code is a **declared approximation** (a heuristic, a parser-shaped
  regex, a best-effort masker), the property must state its BOUNDARY, not
  just its intent. An unbounded property cannot converge — each round
  legitimately finds one more case, forever (see
  [`docs/methodology.md`](../../docs/methodology.md) §5). The boundary
  belongs in the code's own docstring too, so the reviewer checks
  declared-vs-actual instead of declared-vs-infinite.
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
- Evidence gate with unguessable anchors (per file: line count + verbatim
  last line; per claim: quoted code; `NOT REACHED` acceptable,
  HOLDS-without-quote not). A `file:line` alone is not
  this: a fabricated citation costs a reviewer nothing, which is why the
  anchor has to be something it could not have guessed without reading.
- **Tell it to read files with `nl -ba`, not bare `sed -n`.** Measured
  2026-09-06: the same account went from 43/50 citations exact to 35/35 on
  the next round, and the only change was this line in the preamble. Every
  earlier miss was a 2-4 line shift in the one or two files it had read
  without line numbers and counted by eye. Costs a word; buys a citation
  round you do not have to re-resolve.
- Forbid praise and generic summaries.
- Tell it NOT to query MCP/memory tools: "the diff and this focus text are
  the complete context." The reviewer inherits the user's full codex config
  and, measured, spends its opening turns on tool discovery it then
  discards; this line skips that for free.

When Codex implemented the change, this review is a fresh context but not
model diversity — it does not satisfy the cross-family rule **at any risk
level** ([`docs/methodology.md`](../../docs/methodology.md) §1). Use it as a
supplement; the gate reviewer must be non-GPT, and never present two Codex
jobs as independent models.

## Watching the run and reading the report

The exec path runs in the foreground of whatever backgrounds it: its exit
status, `review.md` and the `reasoning effort:` line in `review.log` are the
whole record, and a large target can take 10–25 minutes at xhigh (measured,
2026-09-23). The companion fallback is a tracked job with its own traps — all
in the runtime file: liveness is log mtime (never `status`), the two
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

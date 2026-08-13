---
name: cursor-adversarial-review
description: Run a read-only challenge review through Cursor's CLI (cursor-agent) in ask mode. Use when the user asks for a review via Cursor, or when the cross-family rule needs a reviewer family the standalone CLIs cannot field — one paid adapter serving GPT, Claude, Grok, Kimi, Composer, and auto.
---

# Adversarial review via `cursor-agent` (ask mode)

The value of this leg is **family width through one adapter**: pin the model
per round and the same CLI can be a GPT, Claude, Grok, or Kimi reviewer —
whichever family the cross-family accounting still allows. The model flag IS
the accounting decision; `auto` and `composer-*` never satisfy the rule
(runtime file, accounting rules).

## Before the first run of a session

Read **[`references/cursor-runtime.md`](references/cursor-runtime.md)** (same
directory). The three measured postures matter more than anything else in
it: bare `-p` writes files without asking, `--mode plan` headless returns
empty output every time, and `--mode ask` is the one posture that both
refuses writes and delivers a report. The skill below assumes those three
facts; do not re-derive them at dispatch time.

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
## Launch one ask-mode review run

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cursor-review.XXXXXX")
# Write the focus prompt to "$RUN_DIR/prompt.md" in its own FOREGROUND step,
# with the pinned unified diff (BASE → REVIEW_HEAD) embedded in a fence.
# The prompt rides argv on this CLI (no prompt-file flag) — keep it lean,
# diff first, and let the reviewer read full files itself from the tree.

cd "$REVIEW_TARGET_DIR" && \
  [ "$(git rev-parse HEAD)" = "$REVIEW_HEAD" ] && \
  cursor-agent -p --mode ask --trust \
    --model '<family-pinned-model>' \
    --output-format text \
    "$(cat "$RUN_DIR/prompt.md")" > "$RUN_DIR/review.out" 2>&1
```

Launch under the host's background mechanism with output redirected to a
file. Role-specific choices:

- **`--mode ask` is load-bearing** (measured: refuses writes, still
  reports). Never `--mode plan` here — measured twice returning empty
  stdout on exit 0, which reads as a completed run that found nothing.
- **`--model` is the accounting decision.** Pin an explicit family-bearing
  model (`gpt-…`, `claude-…`, `cursor-grok-…`, `kimi-…`) chosen for the
  round's cross-family needs; record adapter, flag, and `request_id` in the
  run log. `auto`/`composer-*` are extra eyes only.
- **Never** `-f`, `--yolo`, or `--approve-mcps` on a review run.
- An empty `review.out` is a FAILED run on this adapter, not an empty
  verdict — the one measured way to produce it is the forbidden plan mode,
  so treat silence as a launch bug and inspect before re-running.

Verify the target again after the run:

```bash
git -C "$REVIEW_TARGET_DIR" rev-parse HEAD        # must equal $REVIEW_HEAD
git -C "$REVIEW_TARGET_DIR" status --porcelain=v1 # must be empty
```

## Writing the focus prompt

Same red-team discipline as every family — first-party pre-merge framing,
numbered claimed properties with boundaries, falsify-don't-confirm, trigger +
observable consequence + severity + `file:line` per finding, state fixes
already made, ask what the tests do not enumerate, forbid praise. Adapter
specifics: state that ask mode cannot run or edit anything, so evidence is
quoted file content, and any confirmation needing execution must be named as
an exact command for the lead to run; forbid MCP and web tools — the tree,
the embedded diff, and the prompt are the complete context.

## Verify and report

Treat output as hypotheses; verify every finding against the frozen tree
before relaying it, and separate host-verified evidence from the reviewer's
claims. The pairing rule reads the **served model's family**, not this
adapter: a cursor-served Claude model must not review Claude-implemented
work, a cursor-served Grok model must not review the grok adapter's work,
and so on — cross-family accounting follows the model you pinned. Append
each run's verified hit rate to the runtime file's calibration journal.
This skill is review-only: do not apply fixes unless the user asks.

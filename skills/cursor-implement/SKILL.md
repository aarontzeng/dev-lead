---
name: cursor-implement
description: Dispatch an implementation task to Cursor's CLI (cursor-agent) in an isolated worktree, with the model pinned to whichever family the round's cross-family accounting needs. Use when the user asks Cursor to implement something, or to keep another family's quota free for review.
---

# Implement via `cursor-agent`

A paid-pool write leg whose family is **whatever model you pin** — a
cursor-served Codex tier produces GPT-family work, a cursor-served Sonnet
produces Claude-family work. The cross-family rule reads the served model's
family: the reviewer for this leg's output must come from a different family
than the one you pinned here, and `auto`/`composer-*` output (family
unknown/undisclosed) must be reviewed by TWO named families to be safe —
cheaper to just pin a named model when the work will need a gate.

## Before the first run of a session

Read **[`references/cursor-runtime.md`](../cursor-adversarial-review/references/cursor-runtime.md)**.
What bites the write role: bare `-p` already has full write and shell access
(measured — no `--force` needed, and none of `--force`/`--yolo`/
`--approve-mcps` may appear anyway), the prompt rides argv with an
unverified size cliff, and quota exhaustion is loud (`ActionRequiredError`,
exit 1) so a dead dispatch tells you immediately.

## Worktree, snapshot, then dispatch

Every write leg gets its own worktree, without exception — same rule, same
helpers, same reasons as the other families (the CLI's own `--worktree`
under `~/.cursor/worktrees` is not used; the suite's helpers own directory
lifecycle):

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cursor-implement.XXXXXX")   # FIRST — the snapshot lands here
BASE=$(git rev-parse HEAD)   # from a clean checkout, recorded before anything
WORKTREE=../<repo>-cursor-<short-task-slug>
git worktree add -b cursor/<short-task-slug> "$WORKTREE" "$BASE"
# The helper lives in the SUITE's tree, cwd is the TARGET repo — a bare
# `scripts/…` resolves against the target and exits 127.
DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
[ -x "$DEV_LEAD/scripts/snapshot-refs.sh" ] || { echo "dev-lead root unresolved — set DEV_LEAD_ROOT to your checkout"; exit 1; }
"$DEV_LEAD/scripts/snapshot-refs.sh" save "$WORKTREE" "$RUN_DIR/remote-refs.before"   # push-detection baseline
```

The snapshot is the no-push evidence on this adapter: the shell tool is
fully available in the write posture, so the rule is instruction level —
state it in the task prompt, then let the fail-closed ref check at handoff
prove what happened either way.

## Launch

```bash
# Write the task prompt to "$RUN_DIR/task.md" in its own FOREGROUND step.
cd "$WORKTREE" && \
  cursor-agent -p --trust \
    --model '<family-pinned-model>' \
    --output-format text \
    "$(cat "$RUN_DIR/task.md")" > "$RUN_DIR/impl.out" 2>&1
```

- Bare `-p` IS the write posture (measured: creates files, runs shell, no
  prompting) — that is exactly why this launch happens only inside a
  dedicated worktree, and why the review leg never uses it.
- **`--model` pinned and recorded** — the run log must name adapter, model
  flag, and `request_id`; the reviewer-family decision downstream depends
  on it.
- Task prompt must state: local commits only, never push, list what was not
  finished rather than improvising around it.

## Handoff

```bash
"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" "$RUN_DIR/remote-refs.before" || exit 1
git -C "$WORKTREE" status --porcelain=v1
git -C "$WORKTREE" diff "$BASE" --stat
```

The helper exits nonzero on a remote-ref delta, and `|| exit 1` makes that
failure terminal for this handoff. Do not replace it with a second snapshot
and a raw `diff`, which can be noticed but accidentally continued past.

Whether cursor-agent leaves the worktree committed or dirty is UNVERIFIED —
expect either; the lead's own verification pass (build, tests, real code
path) is the merge gate regardless, followed by a reviewer from a
**different model family** than the one pinned above.

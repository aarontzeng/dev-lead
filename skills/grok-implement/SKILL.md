---
name: grok-implement
description: Dispatch an implementation task to xAI's Grok Build CLI (Grok family) in an isolated worktree. Use when the user asks Grok/Grok Build to implement something, or when the paid pools' other families are reserved for review and a Grok write leg keeps the cross-family accounting clean.
---

# Implement via `grok` (Grok Build)

A paid-pool write leg, tier peer of `codex-implement` and `agy-implement`.
Its output is **Grok-family** work: the review gate for anything it writes
must come from a different model family — that is the suite's headline
cross-family rule, and it is the main reason to spend this pool on
implementation at all (it frees the GPT/Gemini/Claude legs to review).

## Before the first run of a session

Read **[`references/grok-runtime.md`](../grok-adversarial-review/references/grok-runtime.md)**.
The items that bite the write role hardest: the machine's `config.toml` may
carry a global always-approve default (state the posture explicitly, never
inherit it), the sandbox is kernel-gated and silently unenforced on old
kernels, and the family has **zero calibration-journal rows** — first
dispatches are calibration runs, verified accordingly.

## Worktree, snapshot, then dispatch

Every write leg gets its own worktree, without exception — same rule, same
helpers, same reasons as the other families:

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/grok-implement.XXXXXX")   # FIRST — the snapshot lands here
BASE=$(git rev-parse HEAD)   # from a clean checkout, recorded before anything
WORKTREE=../<repo>-grok-<short-task-slug>
git worktree add -b grok/<short-task-slug> "$WORKTREE" "$BASE"
# The helper lives in the SUITE's tree, cwd is the TARGET repo — a bare
# `scripts/…` resolves against the target and exits 127.
DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
[ -x "$DEV_LEAD/scripts/snapshot-refs.sh" ] || { echo "dev-lead root unresolved — set DEV_LEAD_ROOT to your checkout"; exit 1; }
"$DEV_LEAD/scripts/snapshot-refs.sh" save "$WORKTREE" "$RUN_DIR/remote-refs.before"   # push-detection baseline
```

The snapshot matters MORE here than on agy: grok's shell tool is
unrestricted inside its permission mode, so the no-push rule is
**instruction level** on this adapter (state it in the task prompt), and the
refs snapshot diffed at handoff is what turns "surely it didn't push" into
evidence, either way.

## Launch

```bash
# Write the task prompt to "$RUN_DIR/task.md" in its own FOREGROUND step.
cd "$WORKTREE" && \
  grok --prompt-file "$RUN_DIR/task.md" \
    -m grok-4.6 \
    --effort medium \
    --permission-mode bypassPermissions \
    --sandbox workspace \
    --disallowed-tools "Agent" \
    --no-memory --verbatim \
    --max-turns 80 \
    --output-format plain > "$RUN_DIR/impl.out" 2>&1
```

- **`--permission-mode bypassPermissions` is stated, not inherited** — the
  config default may already say the same thing, and that is exactly why the
  flag is on the command line: a run log must show its posture (runtime
  file, config trap).
- **`--sandbox workspace`** confines writes to the worktree + temp dirs on
  kernels that enforce it (documented); on older kernels it is a warned
  no-op and the worktree boundary + refs snapshot are the real containment.
- **`--disallowed-tools "Agent"`** — one delegate, one worktree; subagents
  inherit always-approve and answer to nobody's plan (runtime file).
- Effort: `medium` for routine implementation; spend `high` on review legs
  first when the weekly pool is tight — review leverage is higher.

## Handoff

Diff the refs snapshot, verify the worktree, then the lead reviews and
commits:

```bash
"$DEV_LEAD/scripts/snapshot-refs.sh" save "$WORKTREE" "$RUN_DIR/remote-refs.after"
diff "$RUN_DIR/remote-refs.before" "$RUN_DIR/remote-refs.after"   # any delta = a push happened; stop and report
git -C "$WORKTREE" status --porcelain=v1
git -C "$WORKTREE" diff "$BASE" --stat
```

Whether grok leaves work committed or uncommitted in the worktree is
UNVERIFIED (runtime file) — expect either, and treat the lead's own
verification pass as the merge gate regardless: build/tests run by the lead,
in the worktree, before anything is called done. The change then goes to a
reviewer from a **different model family** — never a Grok reviewer for a
Grok implementation.

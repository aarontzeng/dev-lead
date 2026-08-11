---
name: claude-adversarial-review
description: Run an adversarial (red-team) review through headless Claude Code (`claude -p`, plan mode) as the review leg. Use when a non-Claude family implemented the change and Claude is the chosen cross-family reviewer, or when a foreign lead (codex/agy) needs Claude as its review worker.
---

# Adversarial review via headless Claude (`claude -p`)

Claude as the review leg of the cross-family workflow. Use it when the
implementer was GPT, Gemini, or a named free-pool model — never to review
Claude's own work (a second Claude context is a fresh look, not model
diversity; that is the one pairing that forfeits the point).

## Before the first run of a session

Read **[`references/claude-runtime.md`](references/claude-runtime.md)** (same
directory). It holds the
family mechanics shared with `claude-implement` — invocation shapes, the
MCP-stack trim, the patience calibration (five minutes of silence is normal,
not a hang), the headless-plan-mode trap, and the instruction-layer
inheritance property. This file assumes them and covers only the review
role.

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

## Run it

Capture `REVIEW_HEAD` **when you decide what to review** — at commit/freeze
time, not at launch time. Captured immediately before the launch assertion,
the check compares HEAD against itself and can only pass: a tautology, not a
guard. Captured at decision time, it catches everything that moved the tree
in between (the measured wrong-target class: a stale cwd, another agent's
commit, your own mutation testing).

```bash
REVIEW_HEAD=$(git -C "$REVIEW_TARGET_DIR" rev-parse HEAD)   # at freeze time

# ... later, at launch:
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/claude-review.XXXXXX")
# Write the prompt to "$RUN_DIR/prompt.md" in its own step.

cd "$REVIEW_TARGET_DIR" && \
  [ "$(git rev-parse HEAD)" = "$REVIEW_HEAD" ] && \
  claude -p "$(cat "$RUN_DIR/prompt.md")" --permission-mode plan --model <tier> \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
    > "$RUN_DIR/review.out" 2>&1
```

- **`--permission-mode plan`** makes it read-only; the runtime file's
  headless-plan-mode note applies — end the prompt with "deliver the review
  as your final text message; do not write a plan file, do not attempt to
  exit plan mode, do not ask for approval."
- **Trim the MCP stack** (the two flags above) — without them the delegate
  loads every configured tool server before reading a line of code, and the
  startup silence reads as a hang from outside.
- **Launch under the host's background mechanism** with a generous timeout
  (15m+). Budget 5–15 minutes of near-total silence; the process ending is
  the completion signal (runtime file).
- Plan mode is behavioral, not machine-enforced — bracket the run:

```bash
git -C "$REVIEW_TARGET_DIR" rev-parse HEAD        # before, and again after —
git -C "$REVIEW_TARGET_DIR" status --porcelain=v1 # -C so it's the TARGET, not your cwd
```

**Give it a diff or a named file list, never a bare worktree path.** "Review
this worktree" spends the delegate's first minutes discovering scope — the
slowest, least valuable thing it can do. Name the commit range and the
files. That range's base is the **merge-base**
(`BASE=$(git merge-base <target> HEAD)`), never the target branch name; name
the chain with `..` and read the diff with `...` (the two-dot/three-dot trap
is measured and documented in
[`docs/methodology.md`](../../docs/methodology.md) §7). Pre-launch guard:

```bash
git diff --stat "$BASE" HEAD    # file list must match the change under review
```

## Writing the prompt

The flag makes it read-only; **only the prompt makes it adversarial**. A
bare "review this diff" gets a summary with compliments, worth nothing as a
gate. Same discipline as every review leg:

- First-party pre-merge framing; falsify, don't confirm.
- Numbered claimed properties, each demanding HOLDS or BROKEN; bounded
  properties for approximation-shaped code.
- A trigger, observable consequence, severity, and `file:line` per finding.
- State fixes already made, so the round attacks current code.
- **Ask what the tests do not enumerate**, in those words.
- Evidence gate with unguessable anchors (per file: line count + verbatim
  last line; per claim: quoted code; `NOT REACHED` acceptable,
  HOLDS-without-quote not).
- Forbid praise, generic summaries, and recursive delegation ("do not invoke
  claude, codex, agy, opencode, or any review script").
- Forbid running tests/builds; findings that need execution name the exact
  command and expected result — the lead runs it.

## Reporting back

Relay findings faithfully; verify each against the code before acting;
record the hit rate in your calibration journal; hold rejections to the same
evidence standard as findings. Review-only — no fixes unless asked.

---
name: claude-implement
description: Delegate a well-scoped implementation to headless Claude Code (`claude -p`, acceptEdits) in an isolated git worktree, then verify independently and review cross-family before merge. Use when a foreign lead (codex/agy) needs Claude as its implementation worker, or when a Claude lead wants a separate Claude process with its own cwd and context.
---

# Delegate implementation to headless Claude, then verify

Claude as the implementation leg. From a codex or agy lead this is the
primary way to put the Claude family on the implementer side; from a Claude
lead, prefer in-session subagents unless the delegate genuinely needs its
own working directory, permission mode, or model (runtime file).

## Before the first run of a session

Read **[`../claude-adversarial-review/references/claude-runtime.md`](../claude-adversarial-review/references/claude-runtime.md)**
— the
invocation shapes, measured `acceptEdits` behavior, patience calibration,
and the instruction-layer inheritance property are there and assumed here.

## Worktree, always

```bash
BASE=$(git rev-parse HEAD)   # from a clean checkout, recorded before anything
git worktree add -b claude/<short-task-slug> ../<repo>-claude-<short-task-slug> "$BASE"
```

Verify against that exact SHA later — never against a moving branch name.

## Run it

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/claude-implement.XXXXXX")
# Write the task prompt to "$RUN_DIR/task.md" in its own step.

cd "$WORKTREE" && claude -p "$(cat "$RUN_DIR/task.md")" \
  --permission-mode acceptEdits --model <tier> \
  > "$RUN_DIR/impl.out" 2>&1
```

Measured properties of this exact shape (runtime file has the detail):
`acceptEdits` covers file writes and shell/git in one flag — it can `git
add`, `git commit`, and **run the repo's test suite natively** (no pinned
spellings, contrast agy); the working directory is the shell's cwd; commit
messages come out clean when the instruction layer forbids trailers. Do
**not** pass `--dangerously-skip-permissions`.

Launch under the host's background mechanism, generous timeout (20m+ — and
long silences are normal, not hangs).

## Writing the task prompt

Identical discipline to every implement skill:

- **Premise preflight**: compare every factual claim in the task against
  current code, tests, and ADRs; STOP and report the contradiction instead
  of editing if any premise is wrong.
- Exact acceptance criteria, files/directories in scope, the worktree's
  absolute path as working root.
- **Name the existing tests/mocks whose seams the change moves**; **anchor
  every new test to a position** ("class `TestX`, immediately after
  `test_y`").
- **Ask it to run the test suite itself** and report honest counts — this
  delegate can, and self-testing is its feedback loop. The lead re-runs
  everything afterward regardless.
- Git rules verbatim: MAY `git add`/`git commit` on the worktree branch;
  NEVER push, reset, checkout/clean, or touch another branch; plain-English
  commit messages, no AI-authorship trailers. (This delegate inherits the
  user's global instruction layer — measured refusing a push it was
  explicitly asked to make — but state the rules anyway: belt and braces.)
- Point it at the repo's own `CLAUDE.md`/`AGENTS.md` sections that govern
  the change rather than re-explaining conventions — it already reads them.
- No recursive delegation ("do not invoke claude, codex, agy, opencode, or
  any review script").

## After it finishes: verify before anything is trusted

The same lead sequence as every implement skill, none of it optional:

1. `git status --short`, `git log "$BASE"..HEAD --oneline`,
   `git diff "$BASE"...HEAD` — scope verified, not assumed; commit-message
   hygiene checked. (If the delegate left work uncommitted, inspect the
   working tree FIRST — a ranged diff on an uncommitted tree is empty and
   reads as a false green — then the lead stages and makes the checkpoint
   commit.)
2. **Run the full suite yourself** in the worktree.
3. **Mutation-proof every new regression test** (commit first; the full
   mechanics are in
   [`dev-lead/references/mutation-runbook.md`](../dev-lead/references/mutation-runbook.md)).
4. **Cross-family adversarial review** — Claude implemented, so the reviewer
   is GPT, Gemini, or a named free-pool model. Never another Claude context,
   and not a stealth model whose family might be Claude.
5. Merge gate: user sees the diff and verified findings; fast-forward and
   tear down only on explicit approval. Push stays human-only, always.

The fix-round loop (findings quoted verbatim, same worktree, new commit,
re-verify) is `dev-lead` Phase 2 — this skill adds nothing to it.

## What this is not

Not a replacement for in-session subagents on a Claude lead (those are
cheaper and integrated), and not a way to skip review — it moves who types
the code, not who is accountable for it being correct.

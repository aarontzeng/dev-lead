---
name: codex-implement
description: Delegate a well-scoped implementation to OpenAI Codex through its Claude Code companion plugin in an isolated git worktree, then independently verify it with tests and adversarial review before a user-approved merge. Use when the user wants Codex to implement a bounded change, wants a second model's implementation attempt, or wants a safe Codex implementation-and-review workflow.
---

# Delegate implementation to Codex, then verify

Use the Codex companion's `task --write` runtime. It gives the implementation
session `workspace-write`; it is not a permission to modify the main checkout.
Create the worktree yourself, keep the task inside it, and retain merge
authority.

The review gate for work implemented here is a **non-GPT reviewer, at every
risk level** — the cross-family rule
([`docs/methodology.md`](../../docs/methodology.md) §1) has no LOW-risk
exemption. A `codex-adversarial-review` pass over
Codex's own work is a fresh context, **not** model diversity: legitimate as
an *additional* supplement, never as the gate. HIGH-risk work takes two
reviewers from two non-GPT families.

## Before the first run of a session

Read **`../codex-adversarial-review/references/codex-runtime.md`**. It holds
the family-level mechanics shared by both codex roles — companion
resolution, the `--background` launcher-output trap, `status` lying about
liveness, report recovery from session rollouts, what the sandboxes can and
cannot do, and the model/effort plumbing. This file assumes them and covers
only what makes a run an *implementation*.

## Preconditions

Use this only for a bounded change with acceptance criteria. Do not delegate a
vague investigation, a destructive migration, production operations, or a task
whose premise has not been checked against the current code.

Before creating a worktree:

```bash
git status --short
git branch --show-current
BASE=$(git rev-parse HEAD)
```

Stop if the target checkout is dirty, the target branch is unclear, or another
agent already owns the same files. Record the exact `BASE`; never replace it
with a moving branch name during verification.

## Create the isolation boundary

```bash
git worktree add -b codex/<short-task-slug> ../<repo>-codex-<short-task-slug> "$BASE"
```

Worktrees isolate tracked working files, not databases, ports, credentials,
absolute paths, or the shared `.git` directory. Do not run two writers in the
same worktree. Serialize tests that share a schema or service, or use an
isolated test resource. Do not pass `--add-dir` unless the extra writable
directory is necessary and explicitly in scope.

## Prepare a premise-checked task

Create a session-specific prompt file with `mktemp -d`; do not use predictable
shared files under `/tmp`:

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/codex-implement.XXXXXX")
TASK_FILE="$RUN_DIR/task.md"
```

The prompt must include:

1. Exact acceptance criteria and allowed files/directories.
2. Governing agent-instruction files (`AGENTS.md`/`CLAUDE.md`), ADRs, and
   target tests to read first.
3. A **premise preflight**: compare every factual claim in the task with
   current code, ADRs, and tests. If any premise conflicts, stop before
   editing and report the contradiction with evidence.
4. Required tests — with two shapes measured to matter across delegate
   families (the weakness is the delegate role, not the model): **name the
   existing tests/mocks whose seams the change moves** (the lead greps the
   touched symbols' test-side patches before dispatch; a delegate told where
   the seams are fixes them, one left to discover them breaks them — 18
   broken existing tests in one field test), and **anchor every new test to a
   position** ("class `TestX`, immediately after `test_y`") — a test placed
   outside its class parses as a nested function and is silently never
   collected; only a mutation check catches that after the fact.
5. Tell the delegate to LEAVE its work uncommitted and say so in its report —
   in a worktree it cannot commit anyway (the shared `.git` index is outside
   its sandbox; see below), and asking for a commit it cannot make wastes a
   denied attempt. The LEAD makes the checkpoint commit after verifying.
   State the standing rules verbatim: never push, never reset/clean, never
   alter another branch, no AI-authorship trailers.
6. No recursive delegation to Codex, other CLIs, or review scripts.

Use a fresh thread for a new task. Resolve the companion per the runtime
file, then:

```bash
cd "$WORKTREE" && node "$SCRIPT" task \
  --background --write --fresh --model <model> --effort high --prompt-file "$TASK_FILE"
```

Model and effort (verified against companion source at 1.0.6: `--effort`
accepts `none|minimal|low|medium|high|xhigh` — the flag's maximum is spelled
`xhigh`). Adapt tiers to your account's catalogue; the shape of the decision:

| tier / effort | when |
|---|---|
| mid tier + `high` | **default** — balanced for well-scoped delegated work |
| frontier tier + `xhigh` | escalation: a fix round failed on reasoning grounds (not test-harness integration), or the domain is concurrency/auth/partial-failure |
| fast tier + `medium` | cheap mechanical sweeps (renames, comment/doc passes) |

Measured caveat before escalating effort: in live rounds the delegate's
failures were test-harness *integration* (broken seams, an uncollected test),
which more reasoning effort does not fix — the lead's verification does.
Escalate effort for reasoning failures, not integration failures.

The effort ladder above the flag (global-config inheritance, and when
omitting `--effort` is a legitimate, deliberate act) is in the runtime file.
Launch under the host's own background mechanism with output to a file, per
the runtime's launcher rules. Do not use
`--dangerously-bypass-approvals-and-sandbox`; `workspace-write` is the
intended write boundary.

Two sandbox limits from the runtime file shape this role directly: the
delegate **cannot commit from a git worktree** (structural — expect the work
back UNCOMMITTED, and say so in the task prompt so it doesn't waste a denied
attempt; the lead commits as the first verification step) and **cannot bind
AF_UNIX sockets** (pre-brief socket-based tests as an environment limit —
run what it can, list what it could not, never claim unrun tests as proven —
or the delegate burns its run discovering it). Lost reports are recoverable
from session rollouts — runtime file.

## Verify before review

The work comes back UNCOMMITTED (see above), so the order matters: a
`$BASE..HEAD` diff on an uncommitted tree shows NOTHING and reads as a clean
scope check while the entire change sits unexamined in the working tree — a
measured false green.

```bash
cd "$WORKTREE"
git status --short          # 1. the actual change set, file by file
git diff                    # 2. the actual content (HEAD == BASE here)
# 3. re-run the relevant tests yourself, against this uncommitted state
git add <only in-scope paths>            # 4. the LEAD stages, deliberately
git commit                  # 5. checkpoint commit — REQUIRED before any
                            #    mutation testing or review (git restore
                            #    can only restore what is committed)
git log --oneline "$BASE"..HEAD          # 6. now the ranged checks are real
git diff --check "$BASE"...HEAD
```

Confirm the diff is within scope and the commit carries no AI-authorship
trailer. Do not accept the implementation session's report as evidence for
any of it.

For every new regression test, prove it protects the change: run it on the
base commit or apply a minimal mutation that removes the fix, and confirm it
fails; then restore HEAD and confirm it passes. If this is impractical, state
why and do not call the test a regression proof.

## Review and merge gate

Run the **cross-family** review — Codex implemented, so the gate reviewer is
`claude-`, `agy-`, or a named-family `opencode-adversarial-review` — from
the worktree against `$BASE`, with the changed properties named explicitly.
Use a fresh review job after implementation has stopped; never review a
moving diff. A `codex-adversarial-review` pass may run *in addition* (a
fresh context finds real things), but it never substitutes for the non-GPT
gate. High-risk changes take two non-GPT families.

Verify each finding against code or an executable reproduction. Report the
verified findings, rejected false positives, test results, exact base SHA,
and the complete diff scope to the user. Only after the user explicitly
approves may you fast-forward merge and remove the worktree. Never push.

When verified findings require fixes, iterate in the SAME worktree: launch a
follow-up task that quotes each verified finding verbatim, with why it is
real and what fix is required, and commits on top — then re-run the full
verification (tests, mutation proof for new regression tests, fresh review of
the new diff). Measured across families: findings-quoted-verbatim follow-up
tasks fixed everything on the first try. Small, precisely diagnosed defects
you find during verification are cheaper to fix directly than to delegate a
third round for. When quota is tight, spend Codex on the review gate and
delegate implementation to cheaper pools — review leverage is higher.

## Do not use this when direct implementation is cheaper

For a tiny change, writing and supervising this task costs more than
implementing it directly. This workflow earns its cost when a separate
implementation attempt or an independent review boundary materially reduces
risk.

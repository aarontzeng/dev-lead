---
name: codex-implement
description: Delegate a well-scoped implementation to OpenAI Codex through its Claude Code companion plugin in an isolated git worktree, then independently verify it with tests and adversarial review before a user-approved merge. Use when the user wants Codex to implement a bounded change, wants a second model's implementation attempt, or wants a safe Codex implementation-and-review workflow.
---

# Delegate implementation to Codex, then verify

Use the Codex companion's `task --write` runtime. It gives the implementation
session `workspace-write`; it is not a permission to modify the main checkout.
Create the worktree yourself, keep the task inside it, and retain merge
authority.

This skill pairs with `codex-adversarial-review`. A fresh Codex review is an
independent context, **not** model diversity. For auth, concurrency, money,
notification delivery, data loss, or other silent-failure paths, also require
a reviewer from a different model family.

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

Use a fresh thread for a new task. Resolve the installed companion
dynamically; the plugin version and home directory vary by machine:

```bash
SCRIPT=$(ls -d "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs \
  2>/dev/null | sort -V | tail -1)
[ -n "$SCRIPT" ] || { echo "codex plugin not installed"; exit 1; }

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

The effort ladder above the flag: codex itself goes higher than `xhigh`
(`max`, `ultra`) but only via the user's global config
(`~/.codex/config.toml`, `model_reasoning_effort`) — omitting `--effort`
entirely inherits it. Deliberate omission only; say so in the run log,
because the resulting effort then depends on machine state, not the command
line. Passing the flag by default is what keeps an implement-cheap /
review-deep split stable while the user tunes their global freely.

`--background` MAY create a tracked companion job and return a job ID — but
do not rely on it: measured on the review path, the same flag fell into
launcher-output mode, streamed everything through the launching command, and
never returned until killed. **Launch under the host's own background
mechanism with stdout/stderr going to a file, always.** If a job ID appears
and a job log exists, the companion's `status`/`result` work; if not, the
launcher's output file IS the delivery channel. Do not use
`--dangerously-bypass-approvals-and-sandbox`; `workspace-write` is the
intended write boundary.

## What the delegate's sandbox cannot do (measured, twice)

- **It can never commit from a git WORKTREE.** The worktree's real `.git`
  lives under the main repo's path, outside `workspace-write`, so
  `index.lock` fails with EPERM. This is structural, not a delegate error:
  expect the work back UNCOMMITTED, and commit it yourself as the first
  verification step. An honest delegate reports exactly this; both live runs
  did.
- **It cannot bind AF_UNIX sockets**, so socket-based tests fail at setup
  inside the sandbox. Pre-brief this in the task prompt (name it as an
  environment limit, tell the delegate to run what it can, list what it could
  not, and never claim unrun tests as proven) — otherwise the delegate burns
  its run discovering it, or worse, soft-pedals the gap.
- **If the report is lost** (no job log — the `--background` task path may
  record nothing), recover it from `~/.codex/sessions/<date>/rollout-*.jsonl`:
  the final `agent_message` payload is the report, and `custom_tool_call`
  inputs contain the exact patch texts if the working tree itself is ever
  damaged.

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

Run `codex-adversarial-review` from the worktree against `$BASE`, with the
changed properties named explicitly. Use a fresh review job after
implementation has stopped; never review a moving diff. For high-risk
changes, add the required different-family review.

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

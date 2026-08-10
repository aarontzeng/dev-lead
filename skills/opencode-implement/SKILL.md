---
name: opencode-implement
description: Delegate a well-scoped implementation to the OpenCode CLI's free model pool in an isolated git worktree with a machine-enforced no-push permission config, then verify independently before merge. Use for LOW-risk mechanical work where the free pool is the cheapest capable tier, when paid quota should be preserved, or when the user asks to let opencode implement something.
---

# Delegate implementation to opencode, then verify

The free pool's implement niche: **cheapest capable tier for LOW-risk,
well-specified work** — it costs no quota at all, and it **runs the test
suite natively** (bash is allowed under the write config; there is no
allow-list spelling to pin). The trade-off is best-effort capacity:
congestion is normal, so give it the tasks nobody is waiting on.

## Before the first run of a session

Read **`../opencode-adversarial-review/references/opencode-runtime.md`**.
The two permission traps (zero-commit binding, last-match-wins), the
congestion retry loop, the model catalogue and the stealth-model
family-unknown caveat, and the audit log lines all live there and are
assumed here.

## Worktree + config, always

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/opencode-implement.XXXXXX")   # FIRST — snapshots below land here
BASE=$(git rev-parse HEAD)   # from a clean checkout, recorded before anything
WORKTREE=../<repo>-opencode-<short-task-slug>
git worktree add -b opencode/<short-task-slug> "$WORKTREE" "$BASE"
git -C "$WORKTREE" for-each-ref refs/remotes > "$RUN_DIR/remote-refs.before"
```

Then write the write-role config into the worktree as `opencode.json` —
a fresh worktree cannot already have one, but if the repo *tracks* an
`opencode.json` of its own, set it aside first and restore at teardown
(same collision note as the review skill). Wildcard FIRST — last match
wins; see runtime:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": {
      "*": "allow",
      "git push*": "deny",
      "git reset*": "deny",
      "git checkout*": "deny",
      "git clean*": "deny",
      "git worktree*": "deny"
    },
    "edit": "allow"
  }
}
```

No-push machine-enforced (measured: the deny fires, the model is told,
reports MACHINE-DENIED, and continues — no silent death), tests and file
edits free. The instruction layer is the belt on top — measured: a free-pool
delegate refused a push by quoting the user's global no-push rule before the
machine layer ever saw it. The config file stays untracked; never stage it,
remove at teardown.

On the FIRST run, confirm the log's `projectID=` is a hash, not `global` —
`global` means the config (including the push deny) silently is not in force
(runtime, trap 1). The worktree has commits so this should never happen;
checking costs one grep.

## Run it

```bash
# RUN_DIR was created in the worktree step above.
# Write the task prompt to "$RUN_DIR/task.md" in its own FOREGROUND step.

cd "$WORKTREE" && opencode run --print-logs --log-level INFO \
  -m opencode/<free-model> \
  < "$RUN_DIR/task.md" > "$RUN_DIR/impl.log" 2>&1
```

**The task prompt goes on STDIN, not in argv** — an argv prompt over ~1–2 KB
hangs before the session is created, silently and in every model. See the
runtime file for the measured size table and how to tell this stall apart
from genuine congestion.

Background launch, generous timeout (20m+), retry across models on
congestion per the runtime loop.

**Model choice: the fast terminal-tuned named model by default** — the
read/grep/edit/bash/test loop is many short turns, which rewards few active
parameters per token; it was also the fastest responder measured. The
huge-context named model earns its place only when the task genuinely needs
to hold a lot at once (a sweep across a whole subsystem). A stealth model
when the task needs more judgment — remembering its family is unknown, so
the review leg must then come from a KNOWN family that also isn't the
lead's.

## Writing the task prompt

Identical discipline to the other implement skills — premise preflight (STOP
on a wrong premise, report with evidence), exact scope and acceptance
criteria, the worktree's absolute path as working root, **name the existing
tests/mocks whose seams the change moves**, **anchor every new test to a
position** ("class X, immediately after test_y"), no recursive delegation.
This family's specifics:

- **Tests: ask it to run the suite itself** and report honest counts — the
  config permits any test command, no pinned spelling. The lead re-runs
  everything afterward regardless; delegate self-testing is its feedback
  loop, not the verification.
- Git rules to state verbatim: MAY `git add`/`git commit` on the worktree
  branch, plain-English messages, no AI-authorship trailers, NEVER
  push/reset/checkout/clean (the config enforces it; saying it saves a
  denied attempt).

## After it finishes: verify before anything is trusted

The same lead sequence as every implement skill, none of it optional:

1. `git status --short`, `git log "$BASE"..HEAD --oneline`,
   `git diff "$BASE"...HEAD` — scope verified, not assumed; expect
   `?? opencode.json` (yours) and nothing else untracked; check commit
   messages for trailers.
2. **Push check**:
   `git for-each-ref refs/remotes | diff "$RUN_DIR/remote-refs.before" -` —
   any delta is a stop-and-report incident.
3. **Run the full suite yourself** in the worktree — the delegate's counts
   are claims (a measured delegate self-reported 150 on a 445-test suite;
   the code was fine, the number was not).
4. **Mutation-proof every new regression test** (commit the work first;
   editor-tool mutations, not sed; capture-then-report, never `&&`-chain the
   verdict — the full mechanics live in `dev-lead` Phase 2 and apply
   verbatim).
5. Cross-family adversarial review. A NAMED-family implementer makes every
   other family cross-family by construction. A stealth-model implementer
   makes cross-family UNVERIFIABLE — so when family accounting matters,
   either implement with a named model in the first place or take two
   reviewers from two different KNOWN families.
6. Merge gate: user sees the diff and verified findings; fast-forward and
   tear down (including the untracked `opencode.json`) only on explicit
   approval. Push stays human-only, always.

The fix-round loop (findings quoted verbatim, same worktree, new commit,
re-verify including new mutations) is `dev-lead` Phase 2 — this skill adds
nothing to it.

## What this is not

Not a way to make big changes free. Congestion makes turnaround
unpredictable, and a task worth real supervision belongs with a delegate
whose capacity is paid for. Give this family the mechanical sweeps, the
bounded refactors, the second implementation attempt — the work that is
cheap to redo if the queue eats a run.

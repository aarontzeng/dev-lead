---
name: agy-implement
description: Delegate an implementation task to a headless Antigravity (`agy`) session in write mode, inside an isolated git worktree, then verify the result with a cross-family review pass before it merges. Use when the user wants a second model family's implementation attempt, wants to offload a well-scoped coding task, or explicitly asks to let agy implement something.
---

# Delegate implementation to agy, then verify

Complements `agy-adversarial-review` (review-only intent via `--mode plan`).
This skill goes the other direction: agy **writes code**, so the safety comes
from three stacked boundaries — worktree isolation, the sandbox with a
targeted allow-list, and mandatory review before anything reaches the main
branch.

## Before the first run of a session

Read **`../agy-adversarial-review/references/agy-runtime.md`**. It holds the
family-level mechanics shared by both agy roles — the permission allow-list,
the `--add-dir` workspace trap, the silent-death mode, auth diagnosis, and
the model catalogue. This file assumes you know them and covers only what
makes a run an *implementation*.

Two runtime facts this role depends on directly:

- The **write set** is the read-only git rules plus `unsandboxed(git add)`,
  `unsandboxed(git commit)`, and the pinned test-runner spellings (a ruled
  exception — the runtime file carries the ruling and what it costs).
  `push`, `reset`, `checkout`, `clean` and `worktree` are deliberately
  absent, which keeps no-push machine-enforced everywhere except inside the
  test-runner process. Never reach for `--dangerously-skip-permissions` to
  unblock something.
- **A run that returns nothing did not necessarily do nothing** — an
  auto-denied permission kills the run with zero output while edits made
  before the denial are already on disk. The forensic commands are in the
  runtime file; run them after every failed, empty, or timed-out run.

## Why a worktree, always

`agy` runs with `--mode accept-edits` — it can create, edit, and delete
files. Two agents editing the same tree at once is a documented failure
class. There is no version of this skill that skips the worktree.

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/agy-implement.XXXXXX")   # FIRST — the snapshot below lands here
BASE=$(git rev-parse HEAD)   # from a clean checkout, recorded before anything
WORKTREE=../<repo>-agy-<short-task-slug>
git worktree add -b agy/<short-task-slug> "$WORKTREE" "$BASE"
scripts/snapshot-refs.sh save "$WORKTREE" "$RUN_DIR/remote-refs.before"   # push-detection baseline
```

Verify against that exact SHA later — never against the moving branch name.
The remote-refs snapshot exists because of the test-runner ruling: inside
that process the no-push rule is instruction-level only (runtime file), and
this baseline is what the handoff check diffs against to make the residual
risk observable instead of assumed away.

## Run it

```bash
# RUN_DIR was created in the worktree step above.
# Write the task prompt to "$RUN_DIR/task.md" in its own step.

agy -p "$(cat "$RUN_DIR/task.md")" \
    --model <gemini-tier> \
    --mode accept-edits \
    --sandbox \
    --add-dir "$WORKTREE" \
    --effort high \
    --disable-slash-commands \
    --print-timeout 20m0s
```

Role-specific choices:

- **`--mode accept-edits`** is the write mode and the whole point.
- **`--add-dir "$WORKTREE"`** — the worktree, never the main checkout.
- **`--print-timeout 20m0s`** — implementations run longer than reviews.
- **Model** — your account's working Gemini tier. If the CLI exposes a
  second family's pool on separate quota, that pool's strong model is a
  legitimate implementer too (diversity does not constrain the implementer —
  it constrains whoever reviews it afterward). Beware silently-downgrading
  tier ids; verify from the log which model actually served (runtime file).

## Writing the task prompt

- **Premise preflight** (this exact clause would have prevented a measured
  failure): instruct agy to first compare every factual claim in the task
  against the current code, tests, and ADRs, and to STOP and report the
  contradiction instead of editing if any premise is wrong. The incident: a
  task said "remove the duplicate query" when the second query was a
  deliberate last-moment safety re-check; the delegate faithfully removed
  it, and only the next review round caught the regression. The task
  author's description of the code is a hypothesis, not a fact.
- **State the task precisely**: acceptance criteria, exact files/directories
  in scope, and the absolute worktree path as the working root.
- **Git rules to state verbatim**: MAY `git add`/`git commit` on the
  worktree branch; NEVER push, reset, checkout/clean, or touch another
  branch (the allow-list enforces this; saying it avoids wasted denied
  attempts). Plain-English commit messages, no AI-authorship trailers.
- **Point it at the repo's own standards** — the governing
  `CLAUDE.md`/`AGENTS.md` for the touched area, not conventions it invents.
- **Tests: PIN the exact command, because only the allow-listed spellings
  exist.** Any other spelling (`.venv/bin/pytest`, `uv run pytest`,
  `make test`) is auto-denied and **kills the whole run with zero output** —
  the model never gets to "handle" the denial. The task prompt states: "run
  tests ONLY as `python3 -m pytest <paths>`; no other test/build command
  exists for you." Non-pytest repos stay on the old division of labor —
  delegate writes, lead runs — until their runner earns its own deliberate
  allow-list ruling. The lead re-runs the suite after handoff regardless:
  self-testing is the delegate's feedback loop, not the verification.
- **List the existing tests and mock seams the change is expected to
  break.** The single largest measured failure class — 18 broken existing
  tests across two rounds — was the delegate changing a queried behavior
  without seeing which existing tests mock that seam. Before dispatch, grep
  the touched functions for their test-side mocks and patches, and name them
  in the prompt: "these N tests mock `X.y`; your change moves that seam, so
  update them as part of the task."
- **Anchor every NEW test to a position, not just a file**: "add it to class
  `TestX`, immediately after `test_y`". The measured incident: a
  class-method-shaped test landed outside its class, parsed as a nested
  function, and was NEVER COLLECTED — green suite, zero protection, caught
  only by the lead's mutation check.
- **No recursive delegation**: "do not invoke agy, other CLIs, or any review
  script."

## After it finishes: verify before anything is trusted

1. In the worktree: `git log "$BASE"..HEAD --oneline`,
   `git diff "$BASE"...HEAD`, `git status --short`. Scope discipline is
   verified, not assumed. Check commit messages for AI-authorship trailers —
   an amend is free while the branch is local. Then the push check the
   test-runner ruling depends on:
   `scripts/snapshot-refs.sh check "$WORKTREE" "$RUN_DIR/remote-refs.before"` —
   any delta means something pushed during the run: a stop-and-report
   incident, never a shrug. (A successful `git push` updates the
   remote-tracking ref, which this catches; a review-system push
   (`refs/for/*`) does not create one, so the check is a tripwire for the
   common case, not an alibi — which is why the allow-list still excludes
   `git push` itself.)
2. **Run the test suite yourself**, in the worktree, regardless of what agy
   self-reported.
3. **Mutation-check every new regression test**: run it against the pre-fix
   code and confirm it FAILS; restore, confirm it passes. A test that passes
   against the code it claims to guard is a mirror, not a test — the
   measured case was a test whose own harness re-patched the mock it
   depended on, so it exercised nothing. If mutation-checking is
   impractical, say so and do not call the test a regression proof.
4. Review — see "The loop" below.
5. Merge (fast-forward from the worktree branch) and tear down the worktree
   **only after the user has seen the diff and the verified findings and
   said to proceed**. Push stays human-only, always.

## The loop (implement → review → fix → re-verify)

1. **R1**: agy implements in the worktree (this skill).
2. **Review gate**: a reviewer from a *different model family* than the
   implementer. Two contexts of the same family are a fresh look, not model
   diversity. When quota is tight, spend the scarce model on review.
3. **Verify findings yourself** before acting — measured hit rates run well
   below 100%, and acting on a wrong finding writes a wrong fix. Hold your
   own rejections to the same evidence standard.
4. **R2**: a new task in the SAME worktree, new commit on top. Quote each
   verified finding verbatim, with why it is real and what fix is required.
   R2 tasks written this way have fixed all findings on the first try; vague
   "address the review" tasks have no track record.
5. **Re-verify** (steps 1–3, including mutation checks on R2's new tests).
   Small, precisely diagnosed defects you find yourself are yours to fix
   directly — a third delegation round for a one-line fix costs more than it
   protects.
6. Merge gate as above.

## What this skill is not

Not a way to skip review — it moves *who types the code*, not *who is
accountable for it being correct*. For a tiny change, writing and
supervising the task costs more than implementing directly; do that instead.

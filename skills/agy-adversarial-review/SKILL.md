---
name: agy-adversarial-review
description: Run an adversarial (red-team) review through the Antigravity CLI (`agy`, Gemini family). Use when the user asks for an adversarial review from Gemini/Antigravity, wants a second model family's opinion, or when another family's quota is tight and a different reviewer is needed. Pick a reviewer whose blind spots differ from the author's.
---

# Adversarial review via `agy` (Antigravity CLI)

`agy` is an independent path to a different model family — the reason to use
it is **model diversity**, not convenience. A reviewer that shares the
author's training shares the author's blind spots, so the value of this skill
comes entirely from the reviewer being something other than the model that
wrote the code.

Pairing rule across the suite: **the reviewer must come from a different
model family than whatever implemented the change.** When quota is tight,
spend the scarce model on review rather than implementation — review leverage
is higher.

## Before the first run of a session

Read **[`references/agy-runtime.md`](references/agy-runtime.md)** (same
directory). It holds the
family-level mechanics shared with `agy-implement` — the permission
allow-list this CLI needs before it can run `git` at all, the `--add-dir`
workspace trap, the silent-death mode, the auth diagnosis, and the model
catalogue. Every item in it was paid for with an incident; this file assumes
you know them and covers only what makes a run a *review*.

The one-line version of the three that bite hardest: the allow-list is
required or every git command is auto-denied; `--add-dir` is mandatory
because the headless workspace root is agy's own scratch dir; and a run that
returns nothing did not necessarily do nothing.

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

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/agy-review.XXXXXX")
# Write the prompt to "$RUN_DIR/prompt.md" in its own step.
# Confirm this Gemini pair is in the account catalogue; if not, replace BOTH
# values with one available suffix pair. The first 3.7 Flash run is calibration.
AGY_MODEL=gemini-3.7-flash-high
AGY_EFFORT=high

agy -p "$(cat "$RUN_DIR/prompt.md")" \
    --model "$AGY_MODEL" \
    --mode plan \
    --sandbox \
    --add-dir "$REVIEW_TARGET_DIR" \
    --effort "$AGY_EFFORT" \
    --print-timeout 10m0s
```

**`--disable-slash-commands` is deliberately absent here, and adding it back
silently removes plan mode.** The CLI says so — `warning: --mode plan has no
effect while slash command expansion is disabled` — and measured, with both
flags the delegate created a file it was asked to create; with `--mode plan`
alone it refused and returned a plan instead. The pair leaves a review run
with NO behavioral no-write layer at all while the prompt and this file still
claim one. Keep paths in the prompt from starting a line with `/` instead;
the implement role keeps the flag (runtime file: `accept-edits` survives it).

Role-specific choices in that command:

- **`--mode plan`** — research and report, never edit. It is a behavioral
  mode, **not a security boundary**, so the no-write intent is stated again
  in the prompt and verified in the repo afterward. It is also the layer
  `--disable-slash-commands` silently cancels (above) — never pass both.
- **`--print-timeout 10m0s`** — a real review of a few files takes 5–10
  minutes and the CLI default (5m0s) cuts it off mid-flight.
- **Gemini model/effort pair** — the current review default is
  `gemini-3.7-flash-high` with `high`, only when the account catalogue offers
  it. The suffix and effort must match exactly: `-medium` needs `medium` and
  `-low` needs `low`. Verify from the log which model actually served; this
  new model has no calibration rows, so its first verified use is not a gate
  merely because it is newer.
- **Model family** — the CLI may expose a second family's pool (e.g. Claude)
  on separate quota, a legitimate cross-family reviewer for anything that
  family did NOT write. That is a different template: remove `AGY_EFFORT`
  and `--effort` entirely for Claude models (see runtime).

Verify the TARGET before launching — `git -C "$REVIEW_TARGET_DIR" rev-parse
HEAD` must equal `$REVIEW_HEAD` (a wrong-target launch from a stale cwd is
measured in the codex twin of this skill; here the same mistake is a wrong
`--add-dir`, which is why the check names the directory explicitly).
Because plan mode is behavioral rather than enforced, bracket the run:

```bash
git -C "$REVIEW_TARGET_DIR" rev-parse HEAD        # before, and again after —
git -C "$REVIEW_TARGET_DIR" status --porcelain=v1 # -C, or you bracket the
                                                  # lead's own cwd, not the target
```

**Verifying the target is not verifying the SPAN.** The check above proves
you launched at the right commit; it says nothing about which range the
prompt tells the delegate to read. For a topic branch that range is the
merge-base — never the target branch name — and the two spellings look
identical but are not: `git log A..B` is "commits in B not in A" (what you
want), but `git diff A..B` means `git diff A B`, **not** merge-base.
Measured: a prompt naming a 2-commit chain but reading it with a two-dot
diff, while the target was 10 commits past the branch point, handed the
delegate two other teams' files as part of the author's change — nothing
errored, both HEAD assertions passed, and the contamination surfaced only
when findings were checked file-by-file against the real diff.

Pin the base once and hand the delegate that, or use three dots:

```bash
BASE=$(git merge-base origin/main HEAD)
git diff --stat "$BASE" HEAD          # file list must match the change under review
```

Any changed `HEAD` or unexpected dirty-state delta is a **failed safety
check** — preserve the worktree and inspect before trusting or relaying a
single line of the review output.

## Writing the prompt

The same discipline as any red-team review, and it matters more than the
model:

- **Tell it to falsify, not to confirm — but frame it as first-party work.**
  Open with "this is a routine pre-merge correctness review of our own code
  by the team that wrote it", then "for each property below, say whether it
  HOLDS or is BROKEN, and if broken, exactly how." Avoid "RED TEAM" and
  "attack" as the opening register — see the refusal gotcha below.
- **List the claims explicitly.** Quote the properties the code says it has,
  and ask for each by name. Vague "review this" gets vague output; a
  numbered claim list gets numbered rebuttals.
- **Name the attack surface** you already worry about — crash points, partial
  writes, integer overflow, concurrent paths. It will find more, but not if
  the prompt reads like a code-style request.
- **Demand a trigger and a `file:line` per finding**, and ask it to say in
  one line which claims survived.
- **State the review-only intent in the prompt as well as the flags**, and
  forbid recursive CLI invocation.
- **Forbid running tests/builds, and give the impulse somewhere to go**: "if
  confirming a finding requires execution, report it as a finding that names
  the exact command and the result that would confirm it — we run it." Note
  the runtime's honest caveat: if your allow-list contains a test runner
  (globally — there is no per-role scoping), a disobedient reviewer can
  actually execute it, while every *other* build command dies silently with
  zero output. Both outcomes are wrong; the prompt line stays mandatory.
- **Never write a brief that tells this leg to RUN anything — not even
  `git show`.** Under `--sandbox`, shell is denied; headless has nowhere to
  prompt for the `unsandboxed` permission, so the call is auto-denied and
  the run ends with **zero bytes** — the same shape as a silent death, on a
  leg that was working fine. Measured: a brief whose first instruction was
  "實際用 git 指令比對，不要用推測" returned nothing at all, with
  `a tool required the "unsandboxed" permission that headless mode cannot
  prompt for`. That is the lead's authoring bug, not a leg failure.

  So this leg's brief may only say **"read these files."** When the review
  needs material the working tree does not contain — another revision's
  section, a sibling change's version of the same file, a merge-base copy —
  **the lead materializes it first, into `$RUN_DIR`, and grants it with a
  second `--add-dir`** (the flag is repeatable):

  ```bash
  set -o pipefail   # git show's failure must not be masked by sed's success
  git -C "$REPO" show "$OTHER_REV":path/to/file.md \
    | sed -n '/^## Section/,/^## Next/p' > "$RUN_DIR/parent-section.md"
  [ -s "$RUN_DIR/parent-section.md" ] \
    || { echo "materialized evidence is empty: wrong rev, wrong path, or the heading moved" >&2; exit 1; }
  sha256sum "$RUN_DIR"/*.md > "$RUN_DIR/evidence.sha256"

  agy -p "$(cat "$RUN_DIR/prompt.md")" --model <gemini-tier> --mode plan --sandbox \
      --add-dir "$REVIEW_TARGET_DIR" --add-dir "$RUN_DIR" --effort high --print-timeout 15m0s

  sha256sum -c "$RUN_DIR/evidence.sha256"   # evidence unchanged — run this
                                            # alongside the verify-target.sh
                                            # bracket the section above requires
  ```

  **Both post-run checks are required, and the emptiness guard is not
  optional.** A wrong `$OTHER_REV`, a wrong path, or a heading that moved
  leaves `parent-section.md` empty while the pipeline still exits 0
  (without `pipefail` the status is `sed`'s), and the launch below it is
  unconditional — so the leg compares against nothing and reports whatever
  that produces. And because `--add-dir` grants a *writable* directory
  while plan mode is behavioral rather than enforced, the second
  `sha256sum -c` is what makes the evidence as trustworthy as the frozen
  target: without it, a delegate that edited its own comparison input
  would still pass `verify-target.sh`, which only ever looks at
  `$REVIEW_TARGET_DIR`.

  **Do not write these files into the frozen target.** The suite ships no
  ignore rule for them, so an untracked scratch file there shows up in
  `git status --porcelain` and `verify-target.sh` refuses to certify the
  directory — its own message tells you to discard the run's conclusions.
  A lead whose personal global gitignore happens to cover the name will
  not see this and will ship a procedure that fails for everyone else.
  Keeping the material in `$RUN_DIR` leaves the frozen target byte-clean
  and still readable by the delegate.

  Then tell the delegate to compare the working-tree file against the
  named `$RUN_DIR` copies. Re-running with a materialized brief turned the
  same zero-byte leg into a full byte-level comparison.
- **Ask what the tests do not enumerate**, in those words (see
  [`docs/methodology.md`](../../docs/methodology.md) — measured as the
  highest-yield sentence in the prompt across every family).
- **Forbid praise explicitly.** Without it the default register drifts to
  summarizing and complimenting.
- **Size the evidence gate to what YOU cannot check** — and on a DOCUMENT
  (ADR, spec, contract) that is a different list than on code. Demanding
  re-derivation of facts the author already verified turns a document read
  into a repo sweep that blows the print timeout with zero output (measured).
  Keep the unguessable anchor; ask instead what is stated-but-unverifiable,
  what a reader stopping mid-document would wrongly conclude, and which
  claims are the author's own inference.
- **This leg's measured strength is ATTACK PATHS — brief it on sequences.**
  In paired rounds against a free-pool leg on the same targets, this leg
  found the bearer credential an intermediary defeats, the single-slot race,
  and the rate-limit pre-check that bounded nothing — each with a concrete
  sequence — while the other leg passed all three. The mirror also held: the
  other leg caught document-consistency defects this one passed. Phrase
  properties for this leg as "an attacker does X, walk it" and route "does
  this contradict document Y" to a consistency-strong leg. (Your own
  calibration journal may differ — measure.)
- **Give every claim an evidence gate with an unguessable anchor.** This
  family is where the motivating incident happened: a format-compliant
  all-HOLDS verdict in three minutes, having cited nothing. Require per file
  its line count AND the verbatim last line; per claim a `file:line` plus the
  quoted code; state that `NOT REACHED` is acceptable and HOLDS-without-quote
  is not.

## Reporting back

- Relay findings **faithfully** — do not soften or drop them.
- **Verify every finding against the code yourself before acting.** It is
  confidently wrong sometimes: in the run that motivated this skill, six of
  eight findings were real (two severe and genuinely subtle), one was wrong
  on its own premise, one was real but astronomically unreachable. Say
  plainly which you confirmed, which you reject, and why — and hold a
  rejection to the same evidence standard the finding was held to.
- Review-only: do not apply fixes in the same breath unless asked.

## The refusal gotcha

**Red-team framing can trip a refusal.** "You are a RED TEAM reviewer…
falsify claims" over safety-critical code (flight control, medical) has come
back as a refusal — the model reads it as an attack on a third party.
Reframe as what it actually is: a pre-merge correctness review of first-party
code by its own team, with HOLDS/BROKEN verdicts per property. The findings
are the same; only the framing changes.

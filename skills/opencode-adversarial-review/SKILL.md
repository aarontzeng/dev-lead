---
name: opencode-adversarial-review
description: Run an adversarial (red-team) review through the OpenCode CLI's free model pool (DeepSeek, Nemotron, stealth models) under a machine-enforced read-only permission config. Use when a HIGH-risk change needs a second cross-family reviewer without spending paid quota, or as the cheap extra pair of eyes beside a primary review.
---

# Adversarial review via `opencode` (free pool)

The value here is an **additional model family at zero quota cost**: DeepSeek
and Nemotron are neither GPT, Gemini, nor Claude, so this is the cheapest way
to satisfy the cross-family rule's second reviewer on HIGH-risk work. The
trade-off is best-effort capacity — congestion is normal, so this leg is the
*additional* opinion or the unhurried gate, never the time-critical one.

## Before the first run of a session

Read **[`references/opencode-runtime.md`](references/opencode-runtime.md)**
(same directory). It holds the
family mechanics — the free-model catalogue and the stealth-model
family-unknown caveat, the two silent permission traps (zero-commit project
binding, last-match-wins ordering), congestion behavior, and the audit log
lines. This file assumes them and covers only the review role.

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

## The read-only boundary is a config file, not a mode

**If the target already has an `opencode.json`, save it first** — writing
ours over a project's real config and then deleting "ours" at teardown
destroys the project's file:

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/opencode-review.XXXXXX")   # created here; every later step reuses it
[ -f "$REVIEW_TARGET_DIR/opencode.json" ] && \
  mv "$REVIEW_TARGET_DIR/opencode.json" "$RUN_DIR/opencode.json.orig"
# ... run ...; at teardown, restore:
#   mv "$RUN_DIR/opencode.json.orig" "$REVIEW_TARGET_DIR/opencode.json"
```

(The frozen worktree above does NOT make this step optional: it is a checkout
of the same repo, so a project that tracks its own `opencode.json` has one
there too. The backup is what stops teardown deleting it.)

Then write this as `opencode.json` in the target (wildcard FIRST — last
match wins; see runtime):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": {
      "*": "deny",
      "git status*": "allow",
      "git log*": "allow",
      "git diff*": "allow",
      "git show*": "allow",
      "git rev-parse*": "allow"
    },
    "edit": "deny"
  }
}
```

Call this what it is: a **machine-enforced guardrail against a fallible
delegate, not a security boundary against an adversarial one.** File edits
denied, shell denied except five git reads (`git branch*` is deliberately
NOT on the list — `git branch -d` writes refs, and
`rev-parse --abbrev-ref HEAD` answers the read-only question) — stronger
than a behavioral plan mode, and stronger than opencode's own plan agent
(whose edit-deny a shared config can override and whose bash is
unrestricted — measured; see runtime). But command wildcards match command
*prefixes*, not effects: `git diff --output=<path>` and `git log
--output=<path>` write files while matching the allowed patterns. The
threat model here is an honest model taking a lazy shortcut — which the
denials catch — and the post-run bracket below is what catches everything
else; a hostile-input review needs an OS-level read-only sandbox instead.
Verify what actually governs before spending a run: `opencode debug agent`
prints the merged rule array in evaluation order (runtime file). Use the
default agent with this config. A denied call comes back to the model as a
message, so an over-curious reviewer reports MACHINE-DENIED and keeps
reviewing instead of dying.

The config file itself will appear as untracked in the target — and that is a
**known conflict with `verify-target.sh`, which takes no whitelist and refuses
to certify any dirty directory** (`usage: verify-target.sh <dir> <expected-sha>`).
This leg cannot simply move the file out: the config must sit at the project
root to bind. Until the helper learns an expected-paths argument, do **not** hand-roll a
substitute by comparing `git status --porcelain=v1` against an expected set:
a modified scaffold keeps the same entry (`?? opencode.json` stays
`?? opencode.json`), and this leg's own allow-list reaches
`git diff --output=<path>`, so the reviewer can rewrite the config while the
status line never moves. Instead hold a digest of every path you placed there
and check it after the run, then remove/restore the scaffolding and let
`verify-target.sh` certify a genuinely clean directory:

Hold the digest in the lead, never in the target:

```bash
SCAFFOLD_SHA=$(sha256sum "$REVIEW_TARGET_DIR"/{opencode.json,REVIEW-CLAIMS.md} | sha256sum)
```

After the run, compare it, then remove the claims file and restore any original
config — so that the certification step this skill already requires runs against
a genuinely clean directory rather than an excused one.
See [`docs/materializing-evidence.md`](../../docs/materializing-evidence.md)
for the cross-family rules this is the one documented exception to.

## Run it

Capture `REVIEW_HEAD` **when you freeze the target**, not immediately before
the launch assertion — captured at launch time the check compares HEAD
against itself and can only pass (see the same note in
`claude-adversarial-review`).

```bash
REVIEW_HEAD=$(git -C "$REVIEW_TARGET_DIR" rev-parse HEAD)   # at freeze time

# ... later, at launch (RUN_DIR was created in the boundary step above):
# Write the prompt to "$RUN_DIR/prompt.md" in its own FOREGROUND step.

cd "$REVIEW_TARGET_DIR" && \
  [ "$(git rev-parse HEAD)" = "$REVIEW_HEAD" ] && \
  opencode run --print-logs --log-level INFO \
    -m opencode/<free-model> \
    < "$RUN_DIR/prompt.md" > "$RUN_DIR/review.log" 2>&1
```

- **"In its own step" means its own FOREGROUND step.** Measured: a prompt
  file written by a heredoc inside a backgrounded compound command raced the
  read in a second background command — opencode received 168 bytes of a
  5 KB prompt and, sensibly, asked what the session was for. It looks
  exactly like a model ignoring instructions. Compare the size of what you
  piped against the size of what you wrote before believing anything about
  the run.
- **The prompt goes on STDIN, not in argv.** An argv prompt over ~1–2 KB
  hangs before the session is even created — `message=init` then silence,
  forever, in every model (measured; size sweep in the runtime file).
- **Put the claims IN a file inside the repo, not outside it.** Anything
  outside the project trips `permission=external_directory`, which
  auto-rejects headless, and the model treats the refusal as fatal
  (measured: died in seven seconds having read nothing). State the repo
  root explicitly in the prompt ("your cwd is EXACTLY <path>; use relative
  paths") — the same run later hallucinated a neighboring absolute path and
  died identically.
- **Four terminal failures look alike in the log and are not.** Ending with
  `auto-rejecting` is the permission wall — YOUR bug; fix the prompt and
  rerun. `Streaming response failed: [502]/[503] … ResourceExhausted` or
  `queue is full` is the provider out of capacity. A clean exit with
  `tokens.output=0` USUALLY means nothing was produced — but not always;
  see the retraction below. `Error: unknown certificate verification error`
  after a `> build · <model>` line is TLS failing before any token —
  transport; plain rerun. Check before editing anything:
  `grep -c auto-rejecting`, `grep -E '50[0-9]\]'`,
  `grep -c 'certificate verification'`,
  `grep -o 'tokens.output=[0-9]*' | tail -1`.

  **Retraction worth keeping: `tokens.output=0` is a broken accounting
  metric, not a verdict.** Measured: two runs with that fingerprint were
  genuinely empty; a third with the identical fingerprint contained a
  complete review with quoted evidence, verified 4/4 against the real
  files' line counts and last lines.

  **The header grep is a ONE-WAY signal, and reading it as two-way cost a
  round.** A hit (`grep -c '^## '`, or your prompt's required section
  headers) does mean the review is real regardless of the token counter.
  **A zero does NOT mean the run was empty.** Measured: a leg returned a
  full report — a citation table, four findings, two of them that round's
  only catches — written entirely in bold labels and tables with no
  markdown headings at all, so the header count was 0. On that count alone
  it was called a silent-empty run, reported as such to the user, and its
  findings were nearly dropped. Nothing in the prompt had required `##`.

  So the discard decision needs more than one signal. In cost order:

  ```bash
  wc -c "$RUN_DIR/review.log"                              # a real report is rarely tiny
  grep -o 'step=[0-9]*' "$RUN_DIR/review.log" | tail -1    # loop depth: did it work at all?
  grep -cE '^#{1,4} |^\*\*|^\| |NOT REACHED|HOLDS|BROKEN' "$RUN_DIR/review.log"
  grep -v '^timestamp=' "$RUN_DIR/review.log" | tail -200  # then just read the tail
  ```

  The last line is what actually settles it. A 150 KB log whose final
  screen is `git status` output is empty; one whose final screen is prose
  carrying `file:line` citations is not — and no counter substitutes for
  looking. Reading 200 lines is cheaper than re-running the leg, and much
  cheaper than telling the user a leg produced nothing when it did.
- **The tell that it is the provider and not your prompt: did this SHAPE
  work recently?** Measured: three consecutive failures on a
  six-property prompt made the shape look like the cause — but five reviews
  earlier the same day used exactly that shape and all returned full
  reports; the pool had simply degraded that afternoon. A prompt that
  worked in the morning did not become malformed by evening.
- **Congestion is provider-wide; switching MODELS inside a bad window does
  not help** — the free pool is one queue. Wait, or route to a different
  FAMILY.
- **Hand the delegate a merge-base range, never `origin/main..HEAD`** — this
  leg is the most exposed of all, because the prompt tells the model which
  git command to read the diff with and you never see the output. Pin it:

  ```bash
  BASE=$(git merge-base origin/main HEAD)
  git diff --stat "$BASE" HEAD        # file list must match the change under review
  ```
- Launch in the background with a generous timeout (10m+; budget 40m under
  congestion — a measured healthy run went silent 10+ minutes mid-generation
  and then delivered a full report at ~31 minutes). Treat a missing
  `evaluated permission=` log line as "never really ran".
- **Model: the named families are the accounting-valid reviewers.** A
  stealth model (family undisclosed by design) is an ADDITIONAL pair of
  eyes, never the leg that satisfies the cross-family rule — and never
  review a stealth model's work with itself. Between the named models,
  choose by measured hit rate and by structural properties (a huge-context
  model earns its place when the review must hold a whole subsystem at
  once); tier and parameter count did NOT predict finding yield in
  measurement.
- First run in a fresh worktree: confirm the log's `projectID=` is a hash,
  not `global` — `global` means the read-only config silently isn't loaded
  and the boundary does not exist (runtime, trap 1).

Bracket the run regardless (belt and braces — the config is the boundary,
this is the proof):

```bash
git -C "$REVIEW_TARGET_DIR" rev-parse HEAD        # before, and again after —
git -C "$REVIEW_TARGET_DIR" status --porcelain=v1 # -C so it's the TARGET, not your cwd
```

## Writing the prompt

Same red-team discipline as the other review skills — it matters more than
the model. The short list, with this family's specifics:

- First-party pre-merge framing; falsify, don't confirm; numbered claimed
  properties; a declared approximation gets a BOUNDED property; trigger +
  observable consequence + severity + `file:line` per finding; state fixes
  already made; forbid praise and generic summaries.
- **Ask what the tests do not enumerate** — the highest-yield sentence in
  the prompt on every family (see
  [`docs/methodology.md`](../../docs/methodology.md)).
- State the read-only intent in the prompt as well as the config, and forbid
  recursive delegation.
- Tell it NOT to run tests or builds — the config machine-blocks them, but
  saying so converts a wasted denied attempt into the useful artifact: "if
  confirming a finding requires execution, name the exact command and
  expected result; we run it."
- Tell it not to call MCP tools if the target repo wires any — the diff and
  prompt are the complete context.
- **This family's measured strength is DOCUMENT AND SPEC CONSISTENCY; it is
  the LENIENT one on attack paths.** In paired rounds it alone caught a
  design silently contradicting two accepted documents, a key reused across
  protocols with no domain separator, and a test whose name claimed more
  than its body proved — while passing attack-path defects another family
  caught. Ask this leg "does this contradict document Y", "is this claim
  supported by its cited evidence"; do not rely on it alone to decide
  whether an attack works. (Measure your own split — this is one journal's
  data.)
- **Size the evidence gate to what YOU cannot check** (documents get the
  outsider-questions gate, not the re-derivation gate — see methodology).
- **Give every claim an evidence gate with an unguessable anchor**: per
  file, line count AND verbatim last line; per claim, `file:line` plus the
  quoted code; `NOT REACHED` acceptable, HOLDS-without-quote not.

## A long review eats its own prompt — the ask lives in a FILE

Measured: a review that read nine files came back having lost the numbered
claim list to its own context compaction — the honest model stopped and said
so; a less careful one reconstructs the claims and answers a question you
never asked. Reordering the prompt does not help (compaction drops the
original user message as a unit). The defense:

- **Put the claim list in a file inside the review directory** (e.g.
  `REVIEW-CLAIMS.md`) and tell the model to re-read it whenever it needs the
  wording. A file survives compaction because it can be read again; a prompt
  cannot. Say the file plus `opencode.json` are the expected untracked
  entries, or your own tree-dirty check will fire on your scaffolding.
- **Give a reading budget for big files** ("read only lines 2150–2560 of the
  test file") — context is the resource being exhausted.
- **Authorize a partial answer explicitly**: "if you run low on context,
  stop and emit the verdicts you can support with quotes, mark the rest NOT
  REACHED."

## Reporting back

Same contract as every review leg: relay findings faithfully; verify each
against the code before acting (and record the measured hit rate in your
calibration journal — it is this family's calibration data); hold rejections
to the same evidence standard; review-only, no fixes unless asked.

The standing headline from measurement: **model tier did not predict finding
yield** — a free leg found the only HIGH on a diff where two paid legs
returned nothing usable. Do not drop this leg on the grounds that a stronger
reviewer is already running.

---
name: grok-adversarial-review
description: Run a read-only challenge review through xAI's Grok Build CLI (Grok family). Use when the user asks for an adversarial or second-opinion review from Grok, or when the cross-family rule needs a reviewer family that GPT/Gemini/Claude/DeepSeek legs cannot provide — a paid-pool leg, tier peer of codex and agy.
---

# Adversarial review via `grok` (Grok Build)

The value of this leg is a **sixth accounting family**: Grok (xAI) is none of
GPT/Gemini/Claude/DeepSeek/Nemotron, so it can satisfy the cross-family rule
when the other paid families are already spent on authorship. It is a paid
weekly pool — schedule it like the codex leg's quota, never like the free
pool's best-effort capacity.

## Before the first run of a session

Read **[`references/grok-runtime.md`](references/grok-runtime.md)** (same
directory). It holds the family-level mechanics — the always-approve config
trap, the three read-only layers and which one is actually load-bearing, the
old-kernel sandbox no-op, the prompt-file rule, the measured quota
fingerprint — plus the UNVERIFIED list this young family still carries.
**This family has zero calibration-journal rows**: until the first verified
hit rates land, treat its verdicts as one more pair of eyes, not the gate
leg, and record every run.

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
## Launch one machine-bounded read-only run

The boundary is the headless built-in tool allowlist plus explicit MCP denial,
not trust: no shell tool, no edit tools, no MCP calls, no subagents. The diff
is embedded in the prompt because a delegate without a shell cannot run git
(and must not need to).

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/grok-review.XXXXXX")
# Write the focus prompt to "$RUN_DIR/prompt.md" in its own FOREGROUND step,
# with the pinned unified diff (BASE → REVIEW_HEAD) embedded in a fence —
# same pattern as the agy leg, same reason: the reviewer reads files but
# cannot and must not derive the span itself.

cd "$REVIEW_TARGET_DIR" && \
  [ "$(git rev-parse HEAD)" = "$REVIEW_HEAD" ] && \
  grok --prompt-file "$RUN_DIR/prompt.md" \
    -m grok-4.6 \
    --effort high \
    --sandbox read-only \
    --tools "read_file,grep,list_dir" \
    --disallowed-tools "Agent" \
    --deny 'MCPTool(*)' \
    --no-memory --disable-web-search --verbatim \
    --max-turns 40 \
    --output-format plain > "$RUN_DIR/review.out" 2>&1
```

Launch under the host's background mechanism with output redirected to a
file. Role-specific choices:

- **`--tools "read_file,grep,list_dir"`** is the load-bearing read-only
  layer (headless-only allowlist; runtime file explains why plan mode is
  not). `--sandbox read-only` rides along for free on kernels that can
  enforce it and warns-and-continues on ones that cannot — check which
  happened before trusting it (runtime, UNVERIFIED 1).
- **`--disallowed-tools "Agent"`** is mandatory: subagents are documented as
  exempt from edit gates and inherit the account's always-approve default.
  It is also the suite's only machine-enforced no-recursive-delegation.
- **`--deny 'MCPTool(*)'`** is mandatory: `--tools` limits built-in tools,
  but MCP tools remain separately available. Grok matches their names as
  `server__tool` (not `mcp__server__tool`), and a deny rule wins over any
  account allow rule or always-approve setting; see the runtime reference.
- **`-m grok-4.6` explicitly** — never inherit the catalogue default
  silently; `grok models` before the run, and verify the served model from
  the output once UNVERIFIED 5 is captured.
- **Quota failure is loud and immediate** (measured; fingerprint in the
  runtime file). On hitting it, re-route the round to another family — do
  not retry into a weekly budget.

Verify the target again after the run:

```bash
git -C "$REVIEW_TARGET_DIR" rev-parse HEAD        # must equal $REVIEW_HEAD
git -C "$REVIEW_TARGET_DIR" status --porcelain=v1 # must be empty
```

## Writing the focus prompt

Same red-team discipline as every family — first-party pre-merge framing,
numbered claimed properties with boundaries, falsify-don't-confirm, trigger +
observable consequence + severity + `file:line` per finding, state fixes
already made, ask what the tests do not enumerate, forbid praise. Two
grok-specific lines:

- Tell it that it has **no shell**: evidence is quoted file content at
  `file:line`, and if confirming a finding would require running something,
  it must name the exact command and expected result for the lead to run.
- Tell it not to call MCP or web tools; the tree, the embedded diff, and the
  prompt are the complete context (web search is disabled at the flag level
  too).

## Verify and report

Treat output as hypotheses; verify every finding against the frozen tree
before relaying it, and separate host-verified evidence from the reviewer's
claims. Pairing rule across the suite: the reviewer must come from a
different model family than whatever implemented the change — Grok reviewing
Grok-implemented work satisfies nothing, exactly as with every other family.
This skill is review-only: do not apply fixes unless the user asks. Append
the run's verified hit rate to the runtime file's calibration journal — this
family's first rows are the whole reason its early runs exist.

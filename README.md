# dev-lead

**Cross-model delegation and adversarial review for CLI coding agents.**

A skill suite that turns a fleet of coding CLIs — Claude Code, OpenAI Codex,
Google Antigravity (Gemini), and OpenCode's free model pool — into a
disciplined development workflow: one agent leads, others implement, and
**no change ever merges reviewed only by its own model family**.

```
                        ┌─────────────┐
                        │  dev-lead   │  orchestration: dispatch, rounds,
                        │ (any lead)  │  verification, merge gate
                        └──────┬──────┘
       ┌───────────────┬──────┴───────┬────────────────┐
   ┌───▼───┐       ┌───▼───┐      ┌───▼───┐       ┌────▼─────┐
   │claude │       │ codex │      │  agy  │       │ opencode │   4 families
   ├───────┤       ├───────┤      ├───────┤       ├──────────┤
   │implement      │implement     │implement      │implement │   × 2 roles
   │adv-review     │adv-review    │adv-review     │adv-review│
   └───────┘       └───────┘      └───────┘       └──────────┘
```

## Why

Every coding model has blind spots, and a reviewer that shares the author's
training shares the author's blind spots. Running a *second context* of the
same model is a fresh look, not model diversity. This suite makes the
cross-family rule structural:

- **The reviewer never comes from the implementer's family.** GPT implemented →
  Gemini, Claude, or a named free-pool model reviews. High-risk work takes two
  reviewers from two different families.
- **Boundaries are machine-enforced where possible** — read-only permission
  configs, sandboxes, and allow-lists, not "please don't edit anything."
- **Nothing is trusted on self-report.** The lead re-runs tests itself,
  mutation-proofs every new regression test, and verifies every review finding
  before acting on it.
- **The merge gate is human.** Agents commit locally; pushing is always a
  person's decision.

## What's in the box

| Skill | Role |
|---|---|
| `dev-lead` | The orchestration layer: intake → dispatch → bounded review rounds → merge gate |
| `claude-implement` / `claude-adversarial-review` | Claude Code as a headless delegate (`claude -p`) |
| `codex-implement` / `codex-adversarial-review` | OpenAI Codex via its Claude Code companion plugin |
| `agy-implement` / `agy-adversarial-review` | Google Antigravity CLI (Gemini + a separate Claude pool) |
| `opencode-implement` / `opencode-adversarial-review` | OpenCode's free pool (DeepSeek, Nemotron, …) — zero quota cost |

Each family also carries a **runtime reference**
(`skills/<family>-adversarial-review/references/<family>-runtime.md`) holding
its operational mechanics: permission traps, silent failure modes, auth
diagnosis, model catalogues. Every item in those files was paid for with a
real incident, and each is dated so you can judge freshness.

The lead role is portable: all four CLIs can read the same skills directory
(symlink the others' skills paths to it), so a Codex or Gemini lead can follow
the same playbook and delegate to Claude via `claude-implement`.

## The discipline (short version)

The full version is [docs/methodology.md](docs/methodology.md). The parts
people most often get wrong:

1. **Freeze the review target.** Review a committed SHA in a directory nothing
   else touches. The base of a topic branch is the **merge-base**, never the
   target branch name — and `git diff A..B` is *not* merge-base semantics
   (`git log A..B` is; use `git diff A...B` or pin `$BASE`).
2. **Evidence gates with unguessable anchors.** A reviewer that "found
   nothing" and a reviewer that never opened the file produce identical
   output. Require per file: line count *and* the verbatim last line; per
   claim: `file:line` plus the quoted code. `NOT REACHED` is an acceptable
   verdict; `HOLDS` without a quote is not.
3. **Bounded properties.** An unbounded claim ("handles any input") can never
   converge — every round legitimately finds one more case, forever. Declare
   the approximation's scope in the code and review against the boundary.
4. **Mutation-proof regression tests.** A new test that passes proves nothing
   until you've watched it *fail* against the un-fixed code. The catalogue of
   ways this goes subtly wrong lives in `dev-lead` Phase 2 — stale binaries,
   combined reverts, tests that pass for the wrong reason.
5. **Ask what the tests do not enumerate** — in those words. It reliably
   produces the highest-value review output: the state-space regions no test
   covers, and the tests that pass for the wrong reason.
6. **Give parallel reviewers different briefs, not just different models.**
   Sequences, challenge ("is this the right approach at all"), consistency,
   staleness — measured on real rounds, differently-briefed legs' findings
   overlap near zero percent.

## Install

As a Claude Code plugin (recommended):

```bash
# from a marketplace that carries it, or locally:
claude plugin install /path/to/dev-lead
```

Or copy/symlink the skills directly:

```bash
ln -s /path/to/dev-lead/skills/* ~/.claude/skills/
```

To let the other CLIs act as leads or find these files, share the tree:

```bash
ln -s ~/.claude/skills ~/.codex/skills
ln -s ~/.claude/skills ~/.gemini/antigravity-cli/skills
```

### Prerequisites

You need at least two families installed for the cross-family rule to mean
anything. Each family's runtime reference lists its one-time setup (permission
allow-lists, config schemas). The suite assumes two standing rules that you
should adopt in your own agent instructions if you don't have them already:

- **Push is human-only.** Agents commit locally and report the hash.
- **No AI-authorship trailers** in commit messages (adjust to your team's
  policy).

## The calibration journal

The model tables in these skills ship with *structure*, not *your numbers*.
Which model is best at which role changes with every release, every quota
tier, and every codebase. [docs/calibration-journal.md](docs/calibration-journal.md)
describes the practice this suite is really about: measure your own delegates,
date every entry, and never conclude from n=1 — this pool is flaky by design,
and single observations cannot distinguish a broken model from a congested
queue from your own prompt bug.

## Provenance and status

Extracted from a working multi-CLI setup where this workflow shipped real
production changes through implement → cross-family review → mutation-proofed
merge cycles. Dates on measured claims are when they were observed; treat
anything version-pinned (CLI flags, sandbox behavior, model catalogues) as a
snapshot to re-verify, not gospel. Pull requests with *measured* corrections —
what you observed, when, on what version — are the most valuable kind.

## License

MIT

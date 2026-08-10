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
   │claude │       │ codex │      │  agy  │       │ opencode │   4 runtime
   ├───────┤       ├───────┤      ├───────┤       ├──────────┤   adapters
   │implement      │implement     │implement      │implement │
   │adv-review     │adv-review    │adv-review     │adv-review│   × 2 roles
   └───────┘       └───────┘      └───────┘       └──────────┘
```

**Adapters are not families.** The four columns are *runtime adapters* —
which CLI you drive. The cross-family rule is accounted in *model
families* — whose training produced the output — and one adapter can serve
several: agy exposes both Gemini and Claude pools, opencode serves DeepSeek,
Nemotron, and stealth models whose family is undisclosed. Every dispatch
records three things separately: the adapter, the model actually served
(some adapters silently substitute — the runtime files show how to verify),
and the family that model belongs to. The family column is the one the
review rule reads.

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

Symlink the skills (works today, no plugin machinery):

```bash
git clone https://github.com/aarontzeng/dev-lead ~/dev-lead
ln -s ~/dev-lead/skills/* ~/.claude/skills/
```

Keep the clone — several skills refer to `docs/methodology.md` and
`docs/calibration-journal.md` **at the repo root**, which a bare
`skills/*` symlink does not carry. (Relative `../../docs` paths through a
symlink resolve against the link's location, not its target, so the skills
name those files by repo location instead and assume the clone exists.)

As a Claude Code plugin: `claude --plugin-dir ~/dev-lead` loads it for a
session (`claude plugin install` pulls from marketplaces only — a local
path is not an accepted argument; verify against `claude plugin install
--help` on your version). Plugin loading keeps the repo layout, so the
`docs/` references resolve.

To let the other CLIs act as leads or find these files, point each CLI's
skills/context location at the same tree — the exact path is
version-dependent (Codex has documented `$HOME/.agents/skills` as its skill
location, with symlinked folders supported; older setups used
`~/.codex/skills`; check your CLI's current docs):

```bash
ln -s ~/.claude/skills "$HOME/.agents/skills"            # codex (verify per your version)
ln -s ~/.claude/skills ~/.gemini/antigravity-cli/skills  # agy
```

Or skip symlinks entirely: drop [templates/AGENTS.md](templates/AGENTS.md)
into your project root (see Portability below).

### Prerequisites

You need at least two families installed for the cross-family rule to mean
anything. Each family's runtime reference lists its one-time setup (permission
allow-lists, config schemas). The suite assumes two standing rules that you
should adopt in your own agent instructions if you don't have them already:

- **Push is human-only.** Agents commit locally and report the hash.
- **No AI-authorship trailers** in commit messages (adjust to your team's
  policy).

## Portability: which agents can actually use this

Three tiers, honestly labeled:

**Tier 1 — Claude Code: first-class.** Plugin install, automatic skill
discovery and invocation, frontmatter parsed. Everything works out of the
box.

**Tier 2 — shell-capable CLI agents (codex, agy, opencode, and peers):
supported, and field-proven.** The SKILL.md files are plain Markdown and
every mechanism inside them is bash/git — nothing requires a Claude API. A
codex-led run has completed the full workflow end-to-end by reading these
files from a shared directory. Two ways in:

- symlink your CLI's skills/context path at this repo's `skills/` tree, or
- drop [templates/AGENTS.md](templates/AGENTS.md) into your project root
  (codex and opencode read `AGENTS.md` natively) — it tells a foreign agent
  where the skills live, which role it holds, and what to read first.

One caveat in this tier: the codex family skills drive codex through its
**Claude Code companion plugin** by default (job tracking, managed
sandboxes). On a machine without Claude Code, use the **raw-CLI fallback**
documented in
[skills/codex-adversarial-review/references/codex-runtime.md](skills/codex-adversarial-review/references/codex-runtime.md)
— same workflow, honestly-listed reduced guarantees.

**Tier 3 — IDE-embedded agents (Cursor, Copilot, and peers): the
methodology, not the workflow.** The core motion here — launch a headless
delegate in the background, wait 5–40 minutes, harvest results from logs,
orchestrate across worktrees — is not the shape of an IDE agent's
interaction model. For these tools, [docs/methodology.md](docs/methodology.md)
and [docs/calibration-journal.md](docs/calibration-journal.md) are worth
wiring into your rules files as reading material (the cross-family
principle, evidence gates, and mutation discipline apply to *any* review
regardless of who runs it); the eight delegate skills are not usable as
written, and this suite deliberately does not contort itself to change
that.

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

CI is `python3 scripts/lint.py` (stdlib only — run it locally before a PR).
Each check guards an invariant this repo has actually shipped a violation
of; the script's docstring names which. If you add an invariant, add its
check, and mutation-test it: break the invariant, watch the check fire,
restore.

## License

MIT

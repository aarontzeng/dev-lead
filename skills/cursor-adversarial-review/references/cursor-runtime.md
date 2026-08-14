# `cursor-agent` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for Cursor's CLI (`cursor-agent`), shared
by `cursor-adversarial-review` and `cursor-implement`. Measured against
`cursor-agent 2026.08.11-e8db854` on 2026-08-13 with live probes (this
account had usable quota on integration day — unlike the grok adapter, the
load-bearing behaviors here were watched happening, not read about).

## Position in the fleet

A **paid pool** (Cursor subscription) and the fleet's widest single adapter:
one CLI serving **six families** — GPT (Codex/GPT-5.x tiers), Claude (Opus /
Sonnet / Fable tiers), Grok (4.5/4.6 at several efforts), **Kimi
(Moonshot)**, Cursor's in-house **Composer**, and `auto` (server-side
routing, family unknown at dispatch). That width is the value: when a
round's authorship has already spent two families, this adapter can usually
still field a reviewer from a third — without installing another CLI.

Accounting rules this width forces (the family column is what the
cross-family rule reads):

- **A pinned frontier model accounts as its real family** — `cursor` +
  `claude-opus-5-thinking-high` is a Claude-family leg, exactly as if the
  claude adapter had served it.
- **`auto` never satisfies the rule** — the served model is chosen
  server-side and the headless JSON does not name it (measured, below).
  Extra pair of eyes only.
- **`composer-*` never satisfies the rule either** — Cursor's own model,
  but its pretraining lineage is undisclosed, which is the same hazard the
  stealth-model rule exists for: it might share any family's blind spots.
  Extra eyes; never the accounting leg.

## Resolving the CLI, the account, and the catalogue

```bash
command -v cursor-agent || { echo "cursor-agent not installed"; exit 1; }
cursor-agent --version    # measured against: 2026.08.11-e8db854
cursor-agent status       # login identity; refuse to dispatch logged-out
cursor-agent models       # the catalogue is per-account and changes without a CLI update
```

Model ids take bracket parameters — `'composer-2.5[fast=true]'` is measured
working; the help shows `'claude-opus-4-8[context=1m,effort=high,fast=false]'`.
Quote them: brackets are glob characters to the shell.

**Data-handling marker:** catalogue entries suffixed `(NO ZDR)` are served
without zero-data-retention. Do not put confidential or customer code
through those entries; prefer ZDR-covered tiers for anything sensitive.

## The three measured postures (2026-08-13, composer-2.5, scratch git repos)

1. **`-p` alone is a full-access write posture.** The help says print mode
   "has access to all tools, including write and shell" and it means it:
   told to create a file, it created the file and answered, no prompt, exit
   0, dirty tree. This is the implement posture — and the reason the review
   skill never launches bare `-p`.
2. **`--mode plan` is unusable headless.** Writes were blocked (good), but
   stdout came back **empty** — exit 0 and a 1-byte output, twice, including
   on a pure-analysis prompt with no write ask at all. A review leg whose
   report never arrives is a silent-death mode; never pair `--mode plan`
   with `-p`.
3. **`--mode ask` is the review posture.** A defect-hunting prompt returned
   a real finding with `file:line` on stdout; a write attempt was refused in
   words ("I'm in **Ask mode**, so I can't create or edit files") with no
   file created and a clean tree. Whether the edit tools are removed or
   declined is UNVERIFIED — treat ask mode as one layer and keep the
   post-run bracket, as everywhere else.

## Headless mechanics

- The prompt rides **argv** — there is no prompt-file flag in this version.
  Interpolate from a file (`"$(cat "$RUN_DIR/prompt.md")"`) so quoting and
  CJK survive; whether this CLI has an argv size cliff like opencode's ~2 KB
  hang is UNVERIFIED, so keep headless prompts lean and put the bulk (the
  embedded diff) early.
- `--output-format json` (with `-p`) returns `result`, `session_id`,
  `request_id`, token usage — and **no served-model field** (measured). It is
  this suite's only audit format for either role: redirect stdout to a per-run
  `.json` file and stderr to a separate `.err` file, then preserve the JSON object.
  `result` is the human-readable report; `session_id` and `request_id` bind
  it to the dispatch. A missing `request_id` is an audit gap, not a value to
  invent. The [Cursor output-format reference](https://cursor.com/docs/cli/reference/output-format)
  documents the JSON fields; pin `--model` explicitly because a stronger
  served-model verification path remains UNVERIFIED.
- `--trust` is required for headless runs in fresh directories — every
  probe used it; without it a workspace-trust prompt has nowhere to go.
- Flags that must never appear on a review launch: `-f`/`--force`,
  `--yolo`, `--approve-mcps`. The first two are allow-everything, the third
  hands the run to whatever MCP servers the account has wired.
- Built-in `--worktree` exists (`~/.cursor/worktrees/…`) — unused here; the
  suite's own freeze/worktree helpers own directory lifecycle in both roles.
- Global permission rules live in `~/.cursor/cli-config.json`
  (`permissions.allow`/`deny`, e.g. `Shell(ls)`). Their full syntax, a
  per-project override file, and `--sandbox enabled`'s actual semantics are
  all UNVERIFIED — none is load-bearing in the skills yet.

## Quota: how this leg fails (measured 2026-08-13)

```text
ActionRequiredError: You've hit your usage limit Get Cursor Pro for more
Agent usage, unlimited Tab, and more.
```

Exit code 1, immediately, identical in `text` and `json` output formats —
**loud**, same schedulable property as the grok leg and the opposite of the
free pool's silent empty runs. Measured the day the integration probes
themselves exhausted the account's trial quota, which also dates a caveat:
the catalogue and postures above were measured on a pre-Pro account, and
quota width (not behavior) is expected to change with the plan.

## Calibration journal

| date | model | role | outcome |
|---|---|---|---|
| 2026-08-13 | cursor/composer-2.5[fast] | probe | 5 integration probes: write-posture, plan-silent-empty ×2, ask-mode review (1 seeded defect, 1/1 found at file:line), json shape — not a scored review round |

First real review rounds append here, per the journal format — verified hit
rates, not impressions.

## UNVERIFIED — capture on upcoming sessions

1. Ask-mode enforcement class: tool removed vs behaviorally declined
   (instruct-then-inspect with a hostile-ish prompt, clean tree either way).
2. Argv prompt size cliff, if any (size sweep like opencode's).
3. Permission-rule syntax and whether a project-level config overrides the
   global one (the opencode ordering trap has a cousin here somewhere).
4. `--sandbox enabled` semantics and platform floor.
5. Served-model verification (session transcript under `~/.cursor/chats/`?).

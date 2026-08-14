# `grok` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for xAI's Grok Build CLI (`grok`), shared
by `grok-adversarial-review` and `grok-implement`. It sits under the review
skill's directory for the same reason the other families' runtime files do.

**Status: integrated 2026-08-13 against `grok 1.0.3`, before any billed run
had completed on this account** — the quota wall arrived first (see below).
Items marked *measured* were observed live; items marked *documented* come
from the CLI's own bundled user guide (`~/.grok/docs/user-guide/`, same
version) and its `--help` text; items in the UNVERIFIED list at the bottom
are neither, and must be captured on the first quota'd session before this
family takes a gate-relevant leg. That split is deliberate: this file refuses
to assert what nobody here has watched happen.

## Position in the fleet

Grok is a **paid pool, tier peer of codex and agy** — not a free-pool leg.
Access rides on an xAI/X subscription (SuperGrok-class plans share a weekly
usage pool, visible in the account's Usage tab), so treat quota the way the
codex leg treats its five-hour and weekly limits: a budget you schedule, not
best-effort capacity you shrug at. The family it serves is **Grok (xAI)** —
a sixth accounting family, disjoint from GPT/Gemini/Claude/DeepSeek/Nemotron,
which is the whole reason this adapter earns a slot: one more legitimate
cross-family reviewer, valuable exactly when the paid legs' families are
already spent on authorship.

## Resolving the CLI and the served model

```bash
command -v grok || { echo "grok not installed"; exit 1; }
grok --version          # measured against: grok 1.0.3 (1a29d5bc12) [stable]
grok models             # cached catalogue + default; read it before every run
```

`grok models` prints the login identity's catalogue (measured 2026-08-13:
one model, `grok-4.6`, default). Pass `-m` explicitly anyway — the suite's
rule is that inheriting a default silently is how a run log ends up unable to
say which model reviewed the change. The account's catalogue and quota tier
change with the subscription, not the CLI version — re-read after any plan
change.

## The always-approve config trap (measured 2026-08-13)

This machine's `~/.grok/config.toml` carries:

```toml
[ui]
permission_mode = "always-approve"
```

That is a **global bypass-permissions default** — the TUI onboarding writes
it, and every headless run inherits it unless overridden. The same class of
trap as opencode's project-config-overrides-agent-defaults: the boundary you
assumed was someone else's config away. Both role skills therefore pass their
permission posture explicitly on every launch and never rely on the config
default. When diagnosing a run that did something it shouldn't have, read
this file FIRST.

## Read-only layers, and what each one is actually worth

Three mechanisms exist. They are not equivalent and they fail differently:

1. **`--sandbox read-only`** — kernel-enforced (documented: Landlock on
   Linux, Seatbelt on macOS; child-process network blocked via seccomp on
   Linux). The strongest layer of any adapter in this suite **when it
   applies** — and it silently doesn't on older kernels: Landlock needs
   Linux ≥ 5.13, and the documented behavior for a built-in profile that
   cannot apply is *warn and continue without enforcement*. The host this
   integration was written on runs 5.4, so here the flag is a no-op with a
   warning. Pass it anyway (free where it works), but never count it as the
   boundary until the run log's warning-or-silence has been checked on YOUR
   host (UNVERIFIED: the warning's exact text).
2. **`--tools "read_file,grep,list_dir"` plus `--deny 'MCPTool(*)'`** — a
   headless-only built-in allowlist, paired with a permission denial for the
   separate MCP tool class. No shell tool at all, so no git, no redirection,
   no test runs; the MCP denial closes the path that the built-in allowlist
   does not cover. The [official permission reference](https://docs.x.ai/build/features/permissions)
   documents that `MCPTool(...)` uses Grok's `server__tool` name form and
   that deny wins over allow; `MCPTool(*)` therefore denies every configured
   MCP server even under always-approve. This pair is the load-bearing review
   boundary on hosts where the sandbox cannot enforce itself.
3. **Plan mode** — an edit gate at the approval layer. The bundled guide is
   unusually honest about its two holes (documented, 1.0.3): bash commands
   are not inspected for file writes, and **subagents are not covered by the
   parent's plan-mode gate — they inherit the parent's permission mode,
   always-approve included**. Given trap-free layer 2 exists for reviews,
   plan mode is not load-bearing in either role skill.

Because of hole 2 above, **`--disallowed-tools "Agent"` is mandatory in
every review launch** — it is also the only machine enforcement of the
suite's no-recursive-delegation rule on any adapter, so use it.

## Headless mechanics

- **Prompt from a file: `--prompt-file "$RUN_DIR/prompt.md"`.** This flag is
  why the argv traps measured on other families (the ~2 KB hang, the
  leading-`-` parse, CJK mangling) have no grok chapter — the prompt never
  transits argv. Write the file in its own foreground step, same as
  everywhere else in the suite.
- `--output-format json` for machine-parsed results; `plain` when a human
  reads the log. `--json-schema` exists for structured findings
  (UNVERIFIED).
- `--max-turns N` bounds a runaway loop; headless-only.
- `--effort` accepts `none`..`max` in principle, but levels are per-model
  menus — which levels `grok-4.6` advertises is UNVERIFIED.
- `--no-memory` on every delegate run: the CLI has cross-session memory, and
  a reviewer that remembers the last session's premises is contaminated in
  exactly the way `--fresh` exists to prevent on codex.
- `--disable-web-search` on review legs — the diff and the prompt are the
  complete context, same rule as every other family.
- `--verbatim` sends the prompt without preprocessing; use it, prompts here
  are full of paths and fenced code.
- Headless `-p`/`--prompt-file` does **not** create a worktree from
  `--worktree` (documented in `--help`) — irrelevant to this suite, whose
  frozen targets and write worktrees are made by its own tested helpers.

## Quota: how this leg fails (measured 2026-08-13)

```text
You’ve reached your free Grok Build usage limit for now. Get SuperGrok for
much higher limits, or try again later: https://grok.com/supergrok?...
Error: You’ve reached your free Grok Build usage limit for now. ...
```

Exit code 1, message on both stdout and stderr, within seconds of launch.
**Loud and immediate** — the opposite failure shape from the free pool's
silent zero-output runs, and the property that makes this leg schedulable:
a dead grok leg tells you, so the round can re-route to another family
instead of waiting on a log that will never grow. Do not silent-retry; the
pool is a weekly budget, not congestion.

## Calibration journal

| date | model | role | outcome |
|---|---|---|---|

No rows yet. The first quota'd sessions ARE the calibration sessions — append
verified hit rates per run, per the journal's format rules, before this
family's rows are cited in any dispatch decision.

## UNVERIFIED — capture on the first quota'd session, in this order

1. The old-kernel sandbox warning's exact text (launch with
   `--sandbox read-only` on a pre-5.13 kernel; grep the log).
2. Whether `--tools "read_file,grep,list_dir"` alone leaves any write path
   (probe: instruct a file write, verify refusal + clean tree).
3. `grok-4.6`'s advertised `--effort` menu.
4. How the served model is recorded in `--output-format json` (the
   verify-served-model step every family requires).
5. First real review: findings count, verified hit rate → journal row one.

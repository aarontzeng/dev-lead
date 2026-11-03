# dev-lead-panel

An optional Claude Code side pane for dev-lead. It shows:

- **Quota**: each delegate CLI's windows (5h, weekly, monthly) from dev-lead's
  `scripts/quota-snapshot.py`, every 60 seconds, with stale readings dimmed.
- **Agents**: this session and every peer session `ListAgents` reports, every 15
  seconds (● running, ○ idle, ◆ a session on this machine, ☁ cloud).
- **dev-lead legs**: the review and implement legs running as this user (adapter,
  role, model, effort, age, and the working directory on Linux and macOS; Windows
  shows no directory), from `scripts/legs.py`, which never
  prints a command line. Every 15 seconds; every 60 on Windows.

Type `/quota` to open the pane and refresh it now.

It is a separate plugin because it only runs in Claude Code (dev-lead's skills also
serve codex, agy, cursor and opencode) and because it polls in the background:
installing it is the opt-in.

## Install

Needs the `dev-lead` plugin from the same marketplace (the pane runs its newest
installed release's `quota-snapshot.py` and reads its `data/families.json`).

```
/plugin install dev-lead-panel@dev-lead
```

## Options

Set under `/config` (or `pluginConfigs["dev-lead-panel"].options` in settings.json):

| Option | Default | Meaning |
|---|---|---|
| `quota_alert_pct` | `0` | Mark a fresh window red at or above this % used. `0` = no mark: a threshold is your own rule, dev-lead sets none. |
| `show_agents` | `true` | `false` skips the `ListAgents` poll and hides the section. |

Each poll lets `quota-snapshot.py --refresh` wake agy, cursor and opencode-go when
their cache is past its TTL (see `quota-sources.json` in dev-lead's plugin data
directory).

## Known limits

- It finds dev-lead through the plugin cache (`~/.claude/plugins/cache/dev-lead/dev-lead/<version>`),
  newest numeric version first. A symlinked or `--plugin-dir` dev-lead leaves no cache entry,
  and the pane then shows the lookup error in place of the quota rows; `DEV_LEAD_ROOT` is not read.
- The agents section parses `ListAgents`' text; a format change there shows up as an empty list.

## Tests

```
claude plugin test plugins/dev-lead-panel
python3 -m pytest -q plugins/dev-lead-panel/scripts/test_legs.py
```

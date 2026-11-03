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

## Getting numbers on a new host

Only codex is asked directly. claude, cursor, agy and opencode-go are read from
caches that something else writes, so a fresh host shows them as **NO DATA**
("no cache on this host"). Run **`/dev-lead:quota-setup`**: it shows a plan, and
on your yes it

- marks claude and cursor `refresh: fetch`, so dev-lead's `quota-fetch.py` reads
  this host's own Claude Code / Cursor login and asks that vendor's usage endpoint
  (the token goes only there and is never written anywhere);
- gives agy a status line that keeps its cache, when agy has none of its own;
- installs the opencode Go usage plugin when opencode is there;
- marks agy and opencode-go `refresh: tmux_spawn` when tmux is installed.

It never replaces a refresh mode or an agy status line you already have. Each poll
then lets `quota-snapshot.py --refresh` refresh whatever is past its TTL (see
`quota-sources.json` in dev-lead's plugin data directory). A row that is old shows
**STALE** with the reason under it.

## Known limits

- `/dev-lead:quota-setup` is not available on Windows yet (it says so and changes nothing);
  a Windows host still needs aadev's statusline scripts or a hand-written `quota-sources.json`.

- It finds dev-lead through the plugin cache (`~/.claude/plugins/cache/dev-lead/dev-lead/<version>`),
  newest numeric version first. A symlinked or `--plugin-dir` dev-lead leaves no cache entry,
  and the pane then shows the lookup error in place of the quota rows; `DEV_LEAD_ROOT` is not read.
- The agents section parses `ListAgents`' text; a format change there shows up as an empty list.
- `/quota` says "refreshed" whenever it started a snapshot run, also when that run then
  failed; the failure shows in the pane, not in the command's answer.
- When another plugin already owns `/quota`, the pane still opens and polls, but the
  `/quota not registered` notice races the first quota refresh: it may never show, or
  show again after the rows arrive.
- If writing the pane's error itself fails, the timer-driven refresh leaves an unhandled
  rejection (the same holds for the agents and legs polls).
- Not covered by tests: the seq checks that keep an older quota answer from landing
  over a newer one (process.run's 150 s timeout ends a run before the four-tick takeover
  can start a second one, so no test can reach two live runs), a window exactly at the
  alert threshold, a stale window above it, and the exact 15 s agents interval.

## Tests

```
claude plugin test plugins/dev-lead-panel
python3 -m pytest -q plugins/dev-lead-panel/scripts/test_legs.py
```

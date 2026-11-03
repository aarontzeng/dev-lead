---
name: quota-setup
description: Set this host up so that the dev-lead quota pane shows numbers for claude, cursor, agy and opencode-go instead of NO DATA or STALE. Use when the pane says "run /dev-lead:quota-setup", or the user asks why a CLI's quota is missing.
---

# Quota setup: give the quota pane something to read

The pane (dev-lead-panel) reads caches that other things write; on a host
with none of them it shows NO DATA. [`quota-setup.py`](../../scripts/quota-setup.py) sets them up.
It only ADDS: a refresh mode the host already chose, or an agy status line the
person already has, is never replaced.

## Procedure

```bash
# newest installed release, by number (no `sort -V`: stock macOS sort lacks it)
DEV_LEAD=${DEV_LEAD_ROOT:-$(python3 -c 'import glob,os,re;d=glob.glob(os.path.expanduser("~/.claude/plugins/cache/dev-lead/dev-lead/*"));d=[p for p in d if re.fullmatch(r"\d+(\.\d+)*",os.path.basename(p))];print(max(d,key=lambda p:[int(x) for x in os.path.basename(p).split(".")]) if d else "")')}
[ -f "$DEV_LEAD/scripts/quota-setup.py" ] || { echo "dev-lead root unresolved — set DEV_LEAD_ROOT to your checkout"; exit 1; }
python3 "$DEV_LEAD/scripts/quota-setup.py"
```

1. **Show the plan.** Run it without `--apply` and show the user every line.
   Say in plain words what `--apply` would change, especially:
   - `claude` / `cursor` `refresh: fetch` means dev-lead's `quota-fetch.py`
     will read this host's Claude Code / Cursor login and ask that vendor's
     usage endpoint every few minutes while the pane is open. The token goes
     only to the vendor's own endpoint and is never written anywhere.
   - `agy install` sets agy's status line (agy had none).
   - `opencode-go install` copies the Go usage plugin into opencode's plugins.
2. **Ask before applying.** Only on the user's yes, run it again with
   `--apply` and show what it printed (`done:` lines, backups).
3. **Then:** the pane fills in on its next poll (about a minute), or at once
   with `/quota`. agy and opencode caches need tmux to refresh on their own;
   without it they update while their CLI is open.

Not on Windows yet: the script says so and changes nothing.

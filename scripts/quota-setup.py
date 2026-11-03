#!/usr/bin/env python3
"""Set a host up so that the quota pane (dev-lead-panel) has numbers for every CLI it has.

Usage: quota-setup.py [--apply] [--json]

Without --apply it only prints the plan. With --apply it does it:

  claude       "refresh": "fetch" on the claude source: quota-snapshot then runs
               quota-fetch.py, which reads this host's Claude Code login and asks
               Anthropic's usage endpoint (no statusline needed).
  cursor       the same, with the Cursor login, when cursor-agent or a Cursor
               login is on this host.
  agy          when agy is on PATH: its status line is set to dev-lead's
               agy-statusline.py (copied to ~/.config/dev-lead/, a stable path),
               but ONLY when agy has no status line yet -- a person's own is never
               replaced, the plan says what it has to write instead. And
               "refresh": "tmux_spawn" when tmux is there.
  opencode-go  when opencode is on PATH: the Go usage plugin is installed with
               install-opencode-go-usage.sh unless ~/.config/opencode/plugins/go-usage
               is already there, and "refresh": "tmux_spawn" when tmux is there.

The host's sources file is dev-lead's own quota-sources.json in its plugin
data directory (where quota-snapshot.py looks, before the shipped data/ file).
A missing one starts from the shipped file. A source that already has a
"refresh" keeps it: setup only adds. Every file it changes is copied to
<file>.bak.<time> first.

Not yet on Windows (the plan says so and --apply does nothing there); codex
needs nothing (quota-snapshot asks `codex app-server` directly).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHIPPED = ROOT / "data" / "quota-sources.json"
LOCAL_NAME = "quota-sources.json"
IS_WINDOWS = os.name == "nt"


def data_dir():
    plugin = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin and Path(plugin).name.startswith("dev-lead-") and Path(plugin).name != "dev-lead-panel" \
            and not Path(plugin).name.startswith("dev-lead-panel-"):
        return Path(plugin)
    return Path.home() / ".claude" / "plugins" / "data" / "dev-lead-dev-lead"


def agy_settings_path():
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def agy_script_dest():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dev-lead" / "agy-statusline.py"


def opencode_plugin_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "plugins" / "go-usage"


def cursor_login_present():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    ide = (Path.home() / "Library" / "Application Support" / "Cursor" if sys.platform == "darwin"
           else base / "Cursor") / "User" / "globalStorage" / "state.vscdb"
    return (base / "cursor" / "auth.json").is_file() or ide.is_file()


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError("%s is not a JSON object" % path)
    return doc


def _backup(path):
    if path.exists():
        dest = path.with_name(path.name + ".bak." + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, dest)
        return dest
    return None


def _write_json(path, doc, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def plan():
    """[(source, action, detail)] and the sources document to write (or None when nothing changes)."""
    steps = []
    local = data_dir() / LOCAL_NAME
    try:
        sources = _read_json(local) if local.is_file() else _read_json(SHIPPED)
    except (OSError, ValueError) as exc:
        return [("config", "error", "cannot read %s (%s)" % (local if local.is_file() else SHIPPED, exc))], None, None
    if not (isinstance(sources.get("sources"), list)
            and all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in sources["sources"])):
        return [("config", "error", "%s has no list of named sources; fix or remove it first"
                 % (local if local.is_file() else SHIPPED))], None, None
    by_name = {s["name"]: s for s in sources["sources"]}
    wanted = {}
    tmux = shutil.which("tmux") is not None

    wanted["claude"] = "fetch"
    if shutil.which("cursor-agent") or cursor_login_present():
        wanted["cursor"] = "fetch"
    else:
        steps.append(("cursor", "skip", "no cursor-agent and no Cursor login on this host"))

    agy_action = None
    if shutil.which("agy"):
        settings = agy_settings_path()
        try:
            current = _read_json(settings) if settings.is_file() else {}
        except (OSError, ValueError) as exc:
            current = None
            steps.append(("agy", "error", "cannot read %s (%s); not touched" % (settings, exc)))
        if current is not None:
            line = current.get("statusLine")
            dest = agy_script_dest()
            if not line:
                agy_action = ("install", dest)
                steps.append(("agy", "install", "status line -> python3 %s (agy has none)" % dest))
            elif isinstance(line, dict) and str(dest) in str(line.get("command", "")):
                agy_action = ("refresh-script", dest)
                steps.append(("agy", "keep", "status line is already dev-lead's (the script copy is refreshed)"))
            else:
                steps.append(("agy", "keep", "agy already has its own status line; it must write the quota to "
                              "/tmp/gemini/statusline-quota-cache.json (aadev's does), or point it at %s" % dest))
        if tmux:
            wanted["agy"] = "tmux_spawn"
    else:
        steps.append(("agy", "skip", "agy is not on PATH"))

    oc_install = False
    if shutil.which("opencode"):
        if opencode_plugin_dir().exists():
            steps.append(("opencode-go", "keep", "Go usage plugin already at %s" % opencode_plugin_dir()))
        else:
            oc_install = True
            steps.append(("opencode-go", "install", "Go usage plugin -> %s" % opencode_plugin_dir()))
        if tmux:
            wanted["opencode-go"] = "tmux_spawn"
    else:
        steps.append(("opencode-go", "skip", "opencode is not on PATH"))
    if not tmux and ("agy" in by_name or "opencode-go" in by_name) and (shutil.which("agy") or shutil.which("opencode")):
        steps.append(("tmux", "note", "tmux is not installed: agy/opencode caches update only while their CLI is open"))

    changed = False
    for name, mode in wanted.items():
        src = by_name.get(name)
        if src is None:
            steps.append((name, "skip", "no %s source in the sources file" % name))
            continue
        if "refresh" in src:
            steps.append((name, "keep", "refresh already %r" % src["refresh"]))
            continue
        src["refresh"] = mode
        changed = True
        steps.append((name, "refresh", "refresh: %s" % mode))
    return steps, (sources if changed or not local.is_file() else None), {"agy": agy_action, "opencode": oc_install}


def apply(sources, extra):
    done = []
    if extra["agy"]:
        what, dest = extra["agy"]
        settings = agy_settings_path()
        current = _read_json(settings) if settings.is_file() else {}
        if what == "install" and current.get("statusLine"):      # re-checked at write time
            done.append("agy got a status line of its own meanwhile; left alone")
        else:
            src = (HERE / "agy-statusline.py").read_bytes()
            if not dest.exists() or dest.read_bytes() != src:
                bak = _backup(dest)
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd, tmp = tempfile.mkstemp(prefix="." + dest.name + ".", dir=str(dest.parent))
                with os.fdopen(fd, "wb") as fh:
                    fh.write(src)
                os.chmod(tmp, 0o700)
                os.replace(tmp, dest)
                done.append("copied agy-statusline.py to %s%s" % (dest, " (backup %s)" % bak if bak else ""))
            if what == "install":
                bak = _backup(settings)
                current["statusLine"] = {"type": "command", "command": "python3 %s" % dest, "enabled": True}
                _write_json(settings, current)
                done.append("agy status line set in %s%s" % (settings, " (backup %s)" % bak if bak else ""))
    if extra["opencode"]:
        sys.stdout.flush()                        # the installer writes to the same terminal
        rc = subprocess.run(["bash", str(HERE / "install-opencode-go-usage.sh")], check=False).returncode
        done.append("install-opencode-go-usage.sh exited %d" % rc)
    if sources is not None:
        local = data_dir() / LOCAL_NAME
        bak = _backup(local)
        _write_json(local, sources)
        done.append("wrote %s%s" % (local, " (backup %s)" % bak if bak else ""))
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="do it (default: print the plan only)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)
    if IS_WINDOWS:
        print("quota-setup: not available on Windows yet; nothing was changed")
        return 0
    steps, sources, extra = plan()
    if args.json:
        print(json.dumps({"steps": [{"source": s, "action": a, "detail": d} for s, a, d in steps],
                          "writes_sources": sources is not None}, ensure_ascii=False))
    else:
        for s, a, d in steps:
            print("%-12s %-8s %s" % (s, a, d))
    if any(src == "config" and act == "error" for src, act, _ in steps):
        return 1
    if not args.apply:
        if not args.json:
            print("\n(plan only; run again with --apply to do it)")
        return 0
    for line in apply(sources, extra):
        print("done: " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

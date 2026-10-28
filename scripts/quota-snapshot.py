#!/usr/bin/env python3
"""Read-only quota collector: one JSON shape over five differently-kept sources.

    python3 quota-snapshot.py [--source NAME]... [--config PATH] [--now ISO8601]
                              [--codex-bin PATH] [--timeout SECONDS]
                              [--refresh] [--tmux-bin PATH] [--refresh-wait S]

Four statusline caches and one CLI protocol each keep their own units (used vs
remaining, percent vs fraction, seconds vs ISO) and fail in their own way. This
reads them all and prints ONE object on stdout:

    {"schema_version": 1, "generated_at": "<ISO UTC>", "records": [...]}

one record per quota pool. The sources, their kinds and their TTLs come from
data/quota-sources.json; --config, DEV_LEAD_QUOTA_SOURCES, or a
quota-sources.json in dev-lead's plugin data directory replaces it (in that
order);
references/quota-sources.md under skills/dev-lead gives the record shape and
its closed vocabularies, who writes each source, what was verified and what
was not. Nothing consumes the output yet.

Native Windows (added 0.6.89; the POSIX behaviour above is unchanged): the shipped sources
are data/quota-sources.windows.json (all five sources; ${TEMP} and ${LOCALAPPDATA}
expand in a path), --refresh: a tmux_spawn source is refused (no tmux, no fcntl) and a spawn_hidden
source (agy, cursor, opencode-go; opt-in in a host's own config) starts the CLI hidden and ends
that process, see refresh_hidden; codex's pipe is
read by a thread instead of a selector, and `codex` is looked up through PATHEXT (never in
the current directory). Known limits there: the cache file's owner is not checked (no uid
on NTFS); it is still refused when it is a symlink, not regular or over 1 MiB, but there is
no O_NOFOLLOW, so a symlink swapped in between the lstat and the open is caught only by
the device/inode comparison; the codex process tree is ended with the system's `taskkill /T /F`
while the launcher still runs (a launcher that exited and left a descendant is not reached: that
needs a Job Object; terminate() is the fallback), and the reader thread's pipe is left open, not closed under it, when the
thread has not ended 2 s after the process did; the pipe's binary mode is the one io.FileIO
sets on the descriptor Popen hands it (not asserted here); none of this has run on Windows
in this repo's own tests, only the simulation in test_quota_snapshot_windows.

Contract:
  * Exit 0 whenever the object was printed, even if every source failed (the
    failures are inside the records). Exit 2 for a usage or config error, with
    one line on stderr and nothing on stdout.
  * It never runs a model and never reads a credential. It starts `codex
    app-server` and reaps it itself. Opt-in twice over -- with --refresh, AND
    on a host whose config marks the source `"refresh": "tmux_spawn"` -- it
    also starts `agy`, `cursor-agent --trust` or `opencode --standalone` when that cache is missing,
    older than its TTL, more than 60 s in the future, or has an mtime that is
    not a usable time, detached in a tmux
    server of its own, until the CLI rewrites its cache, then kills that
    server (see refresh_tui). It never types a key, and never touches the
    person's own tmux server or a TUI they have open. It starts in an
    empty ~/.cache/dev-lead/quota-tui (anything found there is set aside
    first, since --trust would trust it; but while a private server of an
    earlier run may still be running, a directory that is not empty is left
    alone and the run is refused); afterwards that directory is moved
    aside and emptied (a directory that is not the one checked is put back
    untouched, or renamed quota-tui.foreign.*), and a fresh one takes its
    place. One refresh runs at a time (a lock); a process
    of the same user interfering is out of scope.
  * Output is a whitelist. A field of a source document that is not named in
    the record shape (account ids, plan names, balances, raw error bodies) is
    never copied, and a problem line says what is wrong with a bad value, not
    what it was. The exceptions are all constrained: a limit id or a window
    status that is a bare token, an HTTP status, a JSON-RPC error code.
  * No invented values. A value that is missing, a boolean, a string, not finite
    or outside its range is reported as null with `limit_state` unknown and a
    problem; it is never clamped, zeroed, or filled from another pool.
  * Freshness comes from the SOURCE's own time. Reading an old cache again never
    makes it fresh.
"""
from __future__ import annotations

import argparse
import errno
import json
import math
import os
import queue
import re
import selectors
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

from _common import ROOT, declared_version

try:
    import fcntl
except ImportError:                               # native Windows has no fcntl: tmux_spawn is refused there
    fcntl = None
try:
    import msvcrt                                 # Windows only: the single-flight lock of spawn_hidden
except ImportError:
    msvcrt = None

IS_WINDOWS = sys.platform == "win32"

SCHEMA_VERSION = 1
DEFAULT_CONFIG = ROOT / "data" / ("quota-sources.windows.json" if IS_WINDOWS else "quota-sources.json")
CONFIG_ENV = "DEV_LEAD_QUOTA_SOURCES"
LOCAL_CONFIG_NAME = "quota-sources.json"   # in dev-lead's plugin data dir, beside roster.json
PLUGIN_NAME = "dev-lead"
MAX_CACHE_BYTES = 1024 * 1024
MAX_CODEX_BYTES = 8 * 1024 * 1024       # everything the server may write before we give up
CLOCK_SKEW_SECONDS = 60
FIVE_HOUR_MINUTES = 300
WEEKLY_MINUTES = 10080

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,32}")
_LIMIT_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TS = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?"
    r"(Z|z|[+-]\d{2}(?::?\d{2})?)", re.ASCII)


class UsageError(Exception):
    """A bad command line or config: exit 2, one line on stderr."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


class Context:
    def __init__(self, now, codex_bin, timeout, version, refresh=False, tmux_bin="tmux",
                 refresh_wait=15.0):
        self.now = now
        self.codex_bin = codex_bin
        self.timeout = timeout
        self.version = version
        self.refresh = refresh
        self.tmux_bin = tmux_bin
        self.refresh_wait = refresh_wait
        self.refresh_problems = {}


# ------------------------------------------------------------------ values ----
def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def bad_number(value, hi):
    """Why `value` is not a usable number in [0, hi], or None when it is.

    A bool is an int in Python, so it is refused by name; NaN and the
    infinities survive json.loads and are refused as not finite.
    """
    if isinstance(value, bool):
        return "is a boolean"
    if value is None:
        return "is missing"
    if not isinstance(value, (int, float)):
        return "is not a number"
    if isinstance(value, float) and not math.isfinite(value):
        return "is not finite"
    if value < 0 or value > hi:
        return "is out of range"
    return None


def parse_ts(value):
    """An ISO 8601 string with an explicit zone as aware UTC, else None.

    datetime.fromisoformat takes neither a trailing Z nor a fraction of other
    than 3 or 6 digits before Python 3.11, and a zone-less stamp would be a
    guess, so this reads the shape itself.
    """
    if not isinstance(value, str):
        return None
    m = _TS.fullmatch(value)
    if not m:
        return None
    year, month, day, hour, minute, second, frac, zone = m.groups()
    try:
        if zone in ("Z", "z"):
            offset = timedelta(0)
        else:
            digits = zone[1:].replace(":", "")
            hours, minutes = int(digits[:2]), int(digits[2:4] or 0)
            if hours > 23 or minutes > 59:
                return None                       # timedelta would carry +00:90 into +01:30
            offset = timedelta(hours=hours, minutes=minutes)
            if zone[0] == "-":
                offset = -offset
        micro = int((frac or "0").ljust(6, "0")[:6])
        local = datetime(int(year), int(month), int(day), int(hour), int(minute),
                         int(second), micro, tzinfo=timezone(offset))
        return local.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def from_epoch(value):
    """Unix SECONDS (or a file mtime) as aware UTC, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def fmt_ts(moment):
    moment = moment.astimezone(timezone.utc)
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
        moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)


def errno_name(exc):
    return errno.errorcode.get(exc.errno, "OSError")


def status_problem(status):
    """The problem line for a window status that is not "ok".

    The status text is copied only when it looks like a bare token: it comes
    from a document this script does not control.
    """
    if status is None:
        return "status is missing"
    if isinstance(status, str) and _SAFE_TOKEN.fullmatch(status):
        return "status is %s, not ok" % status
    return "status is not ok"


def _decode_cache(raw):
    """A cache file's text: strict UTF-8, except on Windows, where PowerShell's UTF8 writes a BOM."""
    return raw.decode("utf-8-sig" if IS_WINDOWS else "utf-8")


def _not_ours(st):
    """True when `st` belongs to another user. Windows has no uid (st_uid is always 0 there), so
    nothing is ever refused on that ground: the owner checks are a POSIX defence only."""
    return hasattr(os, "geteuid") and st.st_uid != os.geteuid()


def _is_abs(value):
    """os.path.isabs(), except that on Windows a drive or a UNC share is required: before Python 3.13
    isabs() is also true for \\quota, which depends on the current drive."""
    return PureWindowsPath(value).is_absolute() if os.name == "nt" else os.path.isabs(value)


def _windows_dir(name):
    """The environment variable `name` when it is a fully qualified path, else None."""
    value = os.environ.get(name, "")
    return value if value and _is_abs(value) else None


# ----------------------------------------------------------------- records ----
def expand_path(raw):
    """A leading ~ and ${XDG_CACHE_HOME}; the latter only when set, non-empty and absolute.

    On Windows also ${TEMP} and ${LOCALAPPDATA} (the environment variable when it is a fully
    qualified path, else ~\\AppData\\Local\\Temp / ~\\AppData\\Local: a fixed place, nothing is
    probed or written) and a leading ~\\."""
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    cache = xdg if xdg and _is_abs(xdg) else os.path.join(home, ".cache")
    path = raw.replace("${XDG_CACHE_HOME}", cache)
    if IS_WINDOWS:
        fallback = os.path.join(home, "AppData", "Local")
        path = path.replace("${TEMP}", _windows_dir("TEMP") or os.path.join(fallback, "Temp"))
        path = path.replace("${LOCALAPPDATA}", _windows_dir("LOCALAPPDATA") or fallback)
        if path.startswith("~\\"):
            path = home + path[1:]
    if path == "~" or path.startswith("~/"):
        path = home + path[1:]
    return path


def new_record(src, ctx, pool_id, family, scope_status):
    return {
        "source": src["name"],
        "kind": src["kind"],
        "adapter": src["adapter"],
        "billing_provider": src["billing_provider"],
        "pool_id": pool_id,
        "family": family,
        "scope_status": scope_status,
        "support_level": KINDS[src["kind"]]["support_level"],
        "read_status": "ok",
        "freshness": "unknown",
        "observed_at": fmt_ts(ctx.now),
        "source_updated_at": None,
        "source_time_basis": None,
        "windows": [],
        "problems": [],
    }


def fail(records, status, problem):
    for rec in records:
        rec["read_status"] = status
        rec["problems"].append(problem)
    return records


def stamp(rec, ctx, ttl, updated, basis):
    """Record the source's own time and judge freshness against the TTL.

    A time up to CLOCK_SKEW_SECONDS ahead of ours is clock skew between two
    honest clocks and counts as age 0; beyond that the clock, not the data,
    is what is wrong, so freshness is not claimed.
    """
    if updated is None:
        rec["problems"].append("no usable source timestamp, so freshness is unknown")
        return
    rec["source_updated_at"] = fmt_ts(updated)
    rec["source_time_basis"] = basis
    age = (ctx.now - updated).total_seconds()
    if age < -CLOCK_SKEW_SECONDS:
        rec["problems"].append("clock: source time is more than 60 s in the future")
    elif age <= ttl:
        rec["freshness"] = "fresh"
    else:
        rec["freshness"] = "stale"


def absent_window(rec, label, kind, minutes, why="window is missing"):
    rec["problems"].append("%s: %s" % (label, why))
    rec["windows"].append({
        "kind": kind, "duration_minutes": minutes, "used_pct": None,
        "remaining_pct": None, "resets_at": None, "limit_state": "unknown",
    })


def add_window(rec, ctx, label, kind, minutes, value, resets_raw, field, *,
               fraction=False, parse=parse_ts, blocked=None, bad_duration=False):
    """Append one window built from a source value; nothing is repaired.

    `value` is USED percent 0-100, or with `fraction` the REMAINING fraction
    0-1. `blocked` is a reason the window's state cannot be trusted (a status
    other than ok); it demotes available to unknown but not exhausted.
    `bad_duration` says the caller could not read the window's length (and
    already said so). Like a reset that is present but unreadable, it makes
    the state unknown, exhausted included; a reset that is absent does not.
    """
    problems = rec["problems"]
    used = remaining = None
    reason = bad_number(value, 1 if fraction else 100)
    if reason:
        problems.append("%s: %s %s" % (label, field, reason))
    elif fraction:
        remaining = value * 100
        used = 100 - remaining
    else:
        used = value
        remaining = 100 - value

    resets = None
    unplaced = bad_duration
    if resets_raw is not None:
        resets = parse(resets_raw)
        if resets is None:
            unplaced = True
            problems.append("%s: resets_at is not a valid timestamp" % label)

    if blocked:
        # named whatever the state: an exhausted window still says its status was not ok
        problems.append("%s: %s" % (label, blocked))
    if used is None:
        state = "unknown"
    elif unplaced:
        # a window whose end or length cannot be read may be over already, so
        # the number is kept and no state is asserted, as for a past reset
        state = "unknown"
    elif resets is not None and resets < ctx.now:
        # the number describes a window that has already ended; it is not zero
        state = "unknown"
        problems.append("%s: resets_at is in the past, the window has ended" % label)
    elif used >= 100:
        state = "exhausted"
    elif blocked:
        state = "unknown"
    else:
        state = "available"

    rec["windows"].append({
        "kind": kind,
        "duration_minutes": minutes,
        "used_pct": used,
        "remaining_pct": remaining,
        "resets_at": fmt_ts(resets) if resets is not None else None,
        "limit_state": state,
    })


# ------------------------------------------------------------- cache files ----
def read_cache(path):
    """(document, mtime, status, problem); status is None when the file was read.

    lstat comes first and decides before anything is opened: a symlink, a
    non-regular file, a file another user owns or one over 1 MiB is refused.
    The open then uses O_NOFOLLOW | O_NONBLOCK and is checked against the
    lstat result, so a file swapped in between neither follows a link nor
    blocks on a FIFO; the owner is checked again on the open descriptor, which
    is the only place a chown in between shows. Only the final component is
    refused as a link: a symlinked parent directory is followed.
    """
    def refused(why):
        return None, None, "refused", why

    try:
        before = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None, None, "missing", "cache file does not exist"
    except OSError as exc:
        return None, None, "error", "cannot stat the cache file (%s)" % errno_name(exc)
    if stat.S_ISLNK(before.st_mode):
        return refused("cache file is a symlink")
    if not stat.S_ISREG(before.st_mode):
        return refused("cache file is not a regular file")
    if _not_ours(before):
        return refused("cache file is not owned by the current user")
    if before.st_size > MAX_CACHE_BYTES:
        return refused("cache file is larger than 1 MiB")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) \
        | getattr(os, "O_BINARY", 0)              # Windows only: no newline or Ctrl-Z translation
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None, None, "missing", "cache file does not exist"
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return refused("cache file is a symlink")
        return None, None, "error", "cannot open the cache file (%s)" % errno_name(exc)
    with os.fdopen(fd, "rb") as fh:
        after = os.fstat(fh.fileno())
        if not stat.S_ISREG(after.st_mode) or \
                (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            return refused("cache file changed while it was being opened")
        if _not_ours(after):
            return refused("cache file is not owned by the current user")
        raw = fh.read(MAX_CACHE_BYTES + 1)
    if len(raw) > MAX_CACHE_BYTES:
        return refused("cache file is larger than 1 MiB")
    try:
        doc = json.loads(_decode_cache(raw))
    except (ValueError, RecursionError):
        return None, None, "parse_error", "cache file is not valid JSON"
    if not isinstance(doc, dict):
        return None, None, "parse_error", "cache file is not a JSON object"
    return doc, after.st_mtime, None, None


def load(src, records):
    """The cache document and its mtime, or None after marking every record failed."""
    doc, mtime, status, problem = read_cache(expand_path(src["path"]))
    if status:
        fail(records, status, problem)
        return None, None
    return doc, mtime


# ---------------------------------------------------------- claude / cursor ----
def collect_claude(src, ctx):
    rec = new_record(src, ctx, "claude:subscription", "Claude", "known")
    doc, mtime = load(src, [rec])
    if doc is None:
        return [rec]
    spec = (("five_hour", "five_hour", FIVE_HOUR_MINUTES),
            ("seven_day", "weekly", WEEKLY_MINUTES))
    if not any(key in doc for key, _, _ in spec):
        return fail([rec], "parse_error", "neither five_hour nor seven_day is present")
    stamp(rec, ctx, src["ttl_seconds"], from_epoch(mtime), "mtime")
    for key, kind, minutes in spec:
        entry = doc.get(key)
        if isinstance(entry, dict):
            add_window(rec, ctx, key, kind, minutes, entry.get("utilization"),
                       entry.get("resets_at"), "utilization")
        else:
            absent_window(rec, key, kind, minutes)
    # a tighter per-model weekly limit may sit beside seven_day; it is not read,
    # but a reader is told it is there. Only a limit-shaped object counts:
    # seven_day_breakdown ({as_of, rows, window_started_at}) is a usage report.
    if any(key.startswith("seven_day_") and isinstance(value, dict) and "utilization" in value
           for key, value in doc.items()):
        rec["problems"].append("per-model weekly limits are present and not read")
    return [rec]


def collect_cursor(src, ctx):
    rec = new_record(src, ctx, "cursor:unified", None, "unknown")
    why = _refresh_problem(src, ctx)
    if why:
        rec["problems"].append(why)
    doc, mtime = load(src, [rec])
    if doc is None:
        return [rec]
    if not (is_int(doc.get("schema")) and doc["schema"] == 2):
        return fail([rec], "unsupported", "cache schema is not 2")
    stamp(rec, ctx, src["ttl_seconds"], from_epoch(mtime), "mtime")
    unified = doc.get("unified")
    if unified is True:
        add_window(rec, ctx, "weekly", "weekly", WEEKLY_MINUTES, doc.get("weekly_pct"),
                   doc.get("weekly_reset"), "weekly_pct")
    elif unified is False:
        fail([rec], "unsupported", "legacy billing-cycle plan, no weekly window")
    else:
        fail([rec], "parse_error", "unified is not a boolean")
    return [rec]


# ----------------------------------------------------------- TUI refresh ----
# The agy and Cursor caches are written only by their CLIs' interactive status
# lines, and an idle TUI never rewrites them (measured 2026-10-19). A TUI that
# STARTS does: agy writes its quota at once, even at its folder-trust prompt
# (4 s on the work host), and `cursor-agent --trust` re-fetches a cache 300 s
# or older (6 s). So a refresh starts the CLI in a tmux server of its own
# (`tmux -L <private socket>`), never the person's server, waits for the
# cache to change, and kills that server. No key is typed into anything, and
# no model is called.
REFRESH_TMUX_SPAWN = "tmux_spawn"
REFRESH_SPAWN_HIDDEN = "spawn_hidden"             # Windows: the same idea without tmux (see refresh_hidden)
REFRESH_MODES = (REFRESH_TMUX_SPAWN, REFRESH_SPAWN_HIDDEN)
# cursor-agent wrote its cache 27 s after a hidden start on Windows (measured 2026-10-28 by a peer): the
# caller's --refresh-wait is raised to this where it is shorter (the 60 s cap still applies to the option)
WINDOWS_MIN_WAIT = {"cursor_usage_cache": 45.0}
REFRESH_COMMANDS = {
    "agy_quota_cache": ("agy", ["agy"]),
    "cursor_usage_cache": ("cursor", ["cursor-agent", "--trust"]),
    # opencode 2: the go-usage plugin polls 5 s after its server loads it and writes
    # usage.json itself (it reads the Go key; this script never does). --standalone
    # is required: a plain TUI attaches to opencode's background service, whose plugin
    # is already loaded and polls only every 10 minutes, so a refresh would wait for
    # nothing (measured 2.1.4: 30 s without it, 5-6 s with it, and no service is left).
    # A bare `opencode serve` does not load the plugin at all (measured by a peer).
    "opencode_go_cache": ("opencode", ["opencode", "--standalone"]),
}


class _TimedOut:
    """tmux did not answer in time: what it was asked to do may or may not have happened."""


TMUX_TIMED_OUT = _TimedOut()


def _tmux_timeout():
    # DEV_LEAD_QUOTA_TMUX_TIMEOUT is a test seam, like DEV_LEAD_LAUNCH
    try:
        seconds = float(os.environ.get("DEV_LEAD_QUOTA_TMUX_TIMEOUT", ""))
    except ValueError:
        return 10.0
    return seconds if 0 < seconds <= 60 else 10.0


def _tmux(ctx, *args):
    """One tmux command: True when it succeeded, False when it ran and failed, None if tmux
    cannot run, TMUX_TIMED_OUT (which is truthy) when it did not answer in time.

    TMUX and TMUX_PANE are dropped from its environment: a collector started
    inside the person's tmux must not hand that server's address to anything
    the private server runs.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    try:
        done = subprocess.run([ctx.tmux_bin, *args], capture_output=True, text=True,
                              timeout=_tmux_timeout(), stdin=subprocess.DEVNULL, env=env)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return TMUX_TIMED_OUT
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _server_gone(ctx, socket):
    """True only when tmux itself says there is no server behind `socket`.

    list-sessions fails for other reasons with a server still alive (a socket it cannot connect to:
    Permission denied; "no sessions" in a teardown window), so a bare non-zero status proves nothing.
    The two answers that do prove it, from tmux 3.x: "no server running on <path>" and
    "error connecting to <path> (No such file or directory)". A timeout, a missing tmux or any
    other failure is not proof.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    # the ENOENT text is strerror(), translated in other locales: ask for untranslated messages only.
    # LC_ALL=C would also make the character set ASCII, and tmux refuses to run without a UTF-8 one.
    env.pop("LANGUAGE", None)
    if env.get("LC_ALL"):
        env["LC_CTYPE"] = env.pop("LC_ALL")          # LC_ALL would override LC_MESSAGES below
    env["LC_MESSAGES"] = "C"
    try:
        done = subprocess.run([ctx.tmux_bin, "-L", socket, "-f", os.devnull, "list-sessions"],
                              capture_output=True, text=True, timeout=_tmux_timeout(),
                              stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    err = done.stderr or ""
    return done.returncode != 0 and ("no server running" in err
                                     or ("error connecting to" in err and "No such file or directory" in err))


def _mtime_or_none(path):
    try:
        return os.lstat(path).st_mtime_ns
    except OSError:
        return None


def refresh_tui(ctx, kind, path):
    """Start the kind's CLI in a private tmux server until its cache changes; None, or why not.

    One refresh at a time on a host: the working directory is shared and what a run
    leaves in it is cleared afterwards, so a second collector (or a second source)
    waits its turn by being refused ("another refresh is running"), never by running
    beside the first."""
    label = REFRESH_COMMANDS[kind][0]
    if IS_WINDOWS or fcntl is None:
        return ("%s refresh is not supported on Windows (tmux_spawn needs tmux and POSIX file locks; "
                "spawn_hidden is the Windows mode)" % label)
    base = os.path.join(os.path.expanduser("~"), ".cache", "dev-lead")
    lock_path = os.path.join(base, "quota-tui.lock")
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        st = os.lstat(base)
        if not stat.S_ISDIR(st.st_mode) or _not_ours(st):
            return "%s refresh skipped: ~/.cache/dev-lead is not a plain directory of the user's" % label
        os.chmod(base, 0o700)                     # a looser mode would let another user swap the lock file
        lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        return "%s refresh failed: cannot create its lock (%s)" % (label, errno_name(exc))
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            on_disk = os.lstat(lock_path)
            held = os.fstat(lock)
        except OSError:
            return "%s refresh skipped: another refresh is running" % label
        if (on_disk.st_dev, on_disk.st_ino) != (held.st_dev, held.st_ino):
            return "%s refresh skipped: its lock file was replaced" % label    # a lock on a file nobody else sees
        return _refresh_locked(ctx, kind, path, base)
    finally:
        os.close(lock)                            # closing releases the lock


def _inspect_workdir(label, workdir):
    """(identity, crowded, problem) for the working directory as it is now.

    Makes it if it is missing and sets its mode to 0700, so that a directory made earlier with
    a looser mode (or one the reset could not recreate) is usable. `problem` is the record line
    when it is not a plain directory of the user's or cannot be listed."""
    try:
        os.makedirs(workdir, mode=0o700, exist_ok=True)
        st = os.lstat(workdir)
    except OSError as exc:
        return None, False, "%s refresh failed: cannot create its working directory (%s)" % (
            label, errno_name(exc))
    if not stat.S_ISDIR(st.st_mode) or _not_ours(st):
        return None, False, "%s refresh skipped: its working directory is not a plain directory of the user's" % label
    try:
        os.chmod(workdir, 0o700)                  # an older, looser mode would let someone else add to it
        crowded = bool(os.listdir(workdir))
    except OSError as exc:
        return None, False, "%s refresh failed: cannot list its working directory (%s)" % (
            label, errno_name(exc))
    return (st.st_dev, st.st_ino), crowded, None


def _refresh_locked(ctx, kind, path, base):
    """refresh_tui's body, with the lock held.

    The working directory is a fixed one of dev-lead's own
    (~/.cache/dev-lead/quota-tui), and it must be a real directory the user
    owns, not a link: `cursor-agent --trust` trusts it, so a link would trust
    its target. The private server reads no config file (`-f /dev/null`: the
    person's ~/.tmux.conf could run commands against their own server), and it
    is killed on every path, including a failure to start. Success is judged
    after the kill, so a write that lands while the server stops still counts.
    """
    label, command = REFRESH_COMMANDS[kind]
    workdir = os.path.join(base, "quota-tui")
    _sweep_leftovers(base)
    identity, crowded, problem = _inspect_workdir(label, workdir)
    if problem:
        return problem
    if crowded:
        # neither CLI writes into it by itself (measured), so what is there was put there: by
        # a hook that wrote after the last reset (measured 2026-10-19: the cursor-agent worker
        # outlives the server, and a session hook wrote .remember/ 0.3 s after the cache), or by
        # someone else. --trust would trust it, so it is set aside exactly as after a run and
        # the CLI starts in a fresh empty directory; if that does not leave an empty one, the
        # run is refused. Not while an earlier run's private server may still be running
        stray = _stray_server()
        if stray:
            # a run that could not stop its server keeps the directory (the CLI may still be
            # writing there) and the socket: do not move the directory under it, or start a second CLI
            return "%s refresh skipped: its working directory is not empty and %s" % (label, stray)
        _reset_workdir(workdir, identity)
        identity, crowded, problem = _inspect_workdir(label, workdir)
        if problem:
            return problem
        if crowded:
            return "%s refresh skipped: its working directory is not empty" % label
    socket = "dev-lead-quota-%d-%s" % (os.getpid(), label)
    before = _mtime_or_none(path)
    started = _tmux(ctx, "-L", socket, "-f", os.devnull, "new-session", "-d", "-s", "quota",
                    "-x", "200", "-y", "50", "-c", workdir, *command)
    exited_early = False
    try:
        if started:
            # changed AND whole: the agy status line writes with a plain `>`,
            # so the mtime moves at the truncate, before the JSON is in;
            # killing the CLI then would leave half a file
            deadline = time.monotonic() + ctx.refresh_wait
            while time.monotonic() < deadline and not _rewritten(path, before):
                time.sleep(0.25)
    finally:
        stopped = True
        if started is not None:
            # -f here too: kill-server starts no server, so it reads no config
            # (tmux 3.2a), but nothing is left to that
            # `is True`: a kill that timed out (TMUX_TIMED_OUT is truthy) proves nothing
            killed = _tmux(ctx, "-L", socket, "-f", os.devnull, "kill-server") is True
            if not killed and started is True:
                # the CLI may have ended by itself (a crash, a refused connection): its one-session
                # server is gone with it and kill-server answers "no server running". tmux saying
                # so is proof the server is stopped; a timeout, a permission error or any other answer proves nothing.
                gone = _server_gone(ctx, socket)
                exited_early = gone
                killed = gone
            # a start that RAN and failed made no server, so a failed kill is expected; a
            # start that timed out may have made one, so only a successful kill proves it gone
            stopped = killed or started is False
            if killed:
                # only after a kill that worked: a socket is the one way to reach a server
                # that would not stop, and a CLI that may still run may still be writing
                _remove_socket(socket)
                if started is not False:
                    _reset_workdir(workdir, identity)
    if started is None:
        return "%s refresh skipped: tmux is not available" % label
    if not stopped:
        return "%s refresh failed: its private tmux server could not be stopped" % label
    if not started:
        return "%s refresh failed: tmux could not start %s" % (label, command[0])
    if _rewritten(path, before):
        return None
    if exited_early:
        return ("%s refresh failed: %s exited before it wrote its cache (its tmux server was already gone); "
                "run it once in a terminal to see why" % (label, command[0]))
    return "%s refresh failed: %s did not rewrite its cache within %g s" % (
        label, command[0], ctx.refresh_wait)


_GONE = ".gone."


def _clear_dir(path, identity=None):
    """Empty the directory at `path`, if it is the one meant; True when it was.

    The directory is OPENED without following a link, and what was opened must be a
    directory of the user's (and, when `identity` is given, that very device and inode).
    Its entries are then removed with the process standing IN it (fchdir), so every
    name resolves against the opened inode and not against `path`, which another
    process could have swapped since. shutil.rmtree does the walking below that point
    (its descriptor-based walk does not follow links). An entry that cannot be removed
    (a tree deeper than the recursion limit) is left, never raised."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or _not_ours(st):
            return False
        if identity is not None and (st.st_dev, st.st_ino) != identity:
            return False
        try:
            here = os.open(".", os.O_RDONLY)
        except OSError:
            return False
        try:
            os.fchdir(fd)
            for name in os.listdir("."):
                try:
                    if stat.S_ISDIR(os.lstat(name).st_mode):
                        shutil.rmtree(name)
                    else:
                        os.unlink(name)
                except (OSError, RecursionError):
                    pass
        except OSError:
            pass
        finally:
            try:
                os.fchdir(here)
            except OSError:
                try:
                    os.chdir(os.path.expanduser("~"))     # the old cwd is gone or unreadable; the
                except OSError:                           # collector uses absolute paths from here on
                    pass
            os.close(here)
        return True
    finally:
        os.close(fd)


def _sweep_leftovers(base):
    """Delete what an earlier run could not (a residue tree too deep to delete in one go)."""
    try:
        names = [n for n in os.listdir(base) if n.startswith("quota-tui" + _GONE)]
    except OSError:
        return
    for name in names:
        entry = os.path.join(base, name)
        if _clear_dir(entry):
            try:
                os.rmdir(entry)
            except OSError:
                pass


def _reset_workdir(workdir, identity):
    """Replace the used working directory by a fresh empty one; the old one is deleted.

    Measured 2026-10-19 on macOS: cursor-agent runs the user's Claude Code session
    hooks in its workspace, and a memory plugin's SessionStart hook made `.remember/`
    there; left in place, it would stop the CLI from starting in an empty directory (a
    refresh also calls this when it finds the directory crowded, for a hook that wrote
    after the last reset, unless a private server of an earlier run may still be running). The directory is first MOVED aside (one rename of the final path component,
    which moves a link rather than following it), and only emptied if what is there is
    still the directory that was checked before the start (_clear_dir, bound to its
    device and inode): otherwise it is put back untouched. A fresh empty directory takes
    its place first, so a tree too deep to delete (left under its own name for the next
    run to try) never blocks the next run.
    """
    # unique per call: two sources refresh in one process, and a tree the first could not delete
    # sits under its name, so a name per pid would make the second rename fail and block the next run
    aside = "%s%s%d.%s" % (workdir, _GONE, os.getpid(), os.urandom(4).hex())
    try:
        os.rename(workdir, aside)
    except OSError:
        return
    try:
        os.mkdir(workdir, 0o700)
    except OSError:
        pass                                      # the next run makes it
    if _clear_dir(aside, identity):
        try:
            os.rmdir(aside)
        except OSError:
            pass                                  # something is left in it: the next run sweeps it
        return
    # not the directory that was checked (swapped for a link or another directory): hand it
    # back untouched, replacing the fresh empty one made above. If that cannot be done (the
    # fresh one could not be made, or cannot be removed) it is renamed out of the namespace the
    # sweep deletes from: it is somebody else's, and the sweep deletes whatever it finds there.
    try:
        try:
            os.rmdir(workdir)
        except FileNotFoundError:
            pass
        os.rename(aside, workdir)
    except OSError:
        try:
            os.rename(aside, "%s.foreign.%d.%s" % (workdir, os.getpid(), os.urandom(4).hex()))
        except OSError:
            pass


def _tmux_socket_dir():
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", "tmux-%d" % os.geteuid())


_PRIVATE_SOCKET = re.compile(r"dev-lead-quota-[0-9]{1,10}-(?:agy|cursor|opencode)\Z")


def _stray_server():
    """Why an earlier run's private server cannot be ruled out, or None when it can.

    A socket of this collector's naming in tmux's socket directory: the run that could not
    stop its server keeps it, and so does a collector that died during the wait. A directory
    that cannot be read is not an answer either; only one that is not there is (no tmux
    server was ever started under that user and TMUX_TMPDIR)."""
    try:
        names = sorted(n for n in os.listdir(_tmux_socket_dir()) if _PRIVATE_SOCKET.match(n))
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return "tmux's socket directory cannot be read (%s)" % errno_name(exc)
    return ("a private tmux server of an earlier run may still be running (%s)" % names[0]) if names else None


def _remove_socket(name):
    """Unlink the private server's socket file, which tmux 3.2a leaves behind (measured)."""
    path = os.path.join(_tmux_socket_dir(), name)
    try:
        st = os.lstat(path)
        if stat.S_ISSOCK(st.st_mode) and not _not_ours(st):
            os.unlink(path)
    except OSError:
        pass


def _rewritten(path, before):
    """The cache's mtime moved since `before` and it now parses as a JSON object."""
    if _mtime_or_none(path) == before:
        return False
    try:
        with open(path, "rb") as fh:
            return isinstance(json.loads(_decode_cache(fh.read(MAX_CACHE_BYTES + 1))), dict)
    except (OSError, ValueError, RecursionError):
        return False


# Windows (spawn_hidden): no tmux, so the CLI is started with no window and a console of its own,
# and ended by terminating THAT process only. Measured on Windows 11 by a peer, 2026-10-28:
# agy.exe rewrote its cache 9 s after the start, `opencode --standalone` about 10 s, and cursor-agent
# 27 s (node.exe + index.js; it needs a real console, so its standard handles are never redirected:
# redirected, it falls into print mode and exits). None exits by itself. Starting a .cmd/.ps1 shim
# would end the shell, not the CLI, so only a real executable is started, cursor-agent through the
# newest versions\ directory the way its own launcher picks it.
_CURSOR_VERSION_DIR = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d{2})-(\d{2})-(\d{2}))?-[a-f0-9]+\Z")
_WINDOWS_SHIMS = (".cmd", ".bat", ".ps1")
_REPARSE_POINT = 0x400                            # FILE_ATTRIBUTE_REPARSE_POINT: a junction or a link


def _spawn_hidden(argv, cwd):
    """Start argv with no window and a console of its own. Its standard handles are not redirected and
    are not inherited (close_fds), so it cannot write into this collector's output."""
    return subprocess.Popen(argv, cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def _cursor_agent_command():
    """([node.exe, index.js, --trust], None) from the newest versions\\ directory, or (None, why)."""
    local = _windows_dir("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    root = os.path.join(local, "cursor-agent", "versions")
    try:
        names = os.listdir(root)
    except OSError:
        return None, "cursor refresh skipped: cursor-agent's versions directory was not found"
    found = []
    for name in names:
        m = _CURSOR_VERSION_DIR.match(name)
        if m:
            found.append((tuple(int(g) if g else 0 for g in m.groups()), name))
    for _key, name in sorted(found, reverse=True):
        node, index = (os.path.join(root, name, f) for f in ("node.exe", "index.js"))
        if os.path.isfile(node) and os.path.isfile(index):
            return [node, index, "--trust"], None
    return None, "cursor refresh skipped: no cursor-agent version with node.exe and index.js was found"


def _windows_refresh_argv(kind):
    """(argv, None) for the kind's CLI on Windows, or (None, why not)."""
    label, command = REFRESH_COMMANDS[kind]
    if kind == "cursor_usage_cache":
        return _cursor_agent_command()
    exe = _windows_which(command[0])
    if exe is None:
        return None, "%s refresh skipped: %s was not found on PATH" % (label, command[0])
    if os.path.splitext(exe)[1].lower() in _WINDOWS_SHIMS:
        return None, "%s refresh skipped: %s is a shell shim, not an executable" % (label, command[0])
    return [exe] + command[1:], None


def _windows_clear(workdir):
    """Empty `workdir`: a link or junction is removed itself, never followed."""
    try:
        names = os.listdir(workdir)
    except OSError:
        return
    for name in names:
        full = os.path.join(workdir, name)
        try:
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE_POINT:
                try:
                    os.unlink(full)
                except OSError:
                    os.rmdir(full)
            elif stat.S_ISDIR(st.st_mode):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.unlink(full)
        except (OSError, RecursionError):
            pass


def _windows_workdir(label, workdir):
    """None when `workdir` is a plain, empty directory of dev-lead's own, else why not. The CLI is
    started trusting it (cursor-agent --trust), so it must not be a link or junction to somewhere else."""
    try:
        os.makedirs(workdir, exist_ok=True)
        st = os.lstat(workdir)
    except OSError as exc:
        return "%s refresh failed: cannot create its working directory (%s)" % (label, errno_name(exc))
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) \
            or getattr(st, "st_file_attributes", 0) & _REPARSE_POINT:
        return "%s refresh skipped: its working directory is not a plain directory" % label
    _windows_clear(workdir)                       # what an earlier run or a hook left (cursor-agent runs session hooks)
    try:
        if os.listdir(workdir):
            return "%s refresh skipped: its working directory is not empty" % label
    except OSError as exc:
        return "%s refresh failed: cannot list its working directory (%s)" % (label, errno_name(exc))
    return None


def _end_process(proc):
    """Terminate the process this script started, and only it. True when it is gone."""
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
    return True


def refresh_hidden(ctx, kind, path):
    """Windows: start the kind's CLI hidden until its cache changes; None, or why not.

    One refresh at a time on a host, by an exclusive byte-range lock on a file of dev-lead's own
    (msvcrt.locking, which the system releases when the process dies, so no stale lock)."""
    label = REFRESH_COMMANDS[kind][0]
    if not IS_WINDOWS or msvcrt is None:
        return "%s refresh: spawn_hidden is the Windows mode (use tmux_spawn elsewhere)" % label
    base = os.path.join(os.path.expanduser("~"), ".cache", "dev-lead")
    try:
        os.makedirs(base, exist_ok=True)
        lock = os.open(os.path.join(base, "quota-tui.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    except OSError as exc:
        return "%s refresh failed: cannot create its lock (%s)" % (label, errno_name(exc))
    try:
        try:
            msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
        except OSError:
            return "%s refresh skipped: another refresh is running" % label
        return _hidden_locked(ctx, kind, path, base)
    finally:
        os.close(lock)                            # closing releases the lock


def _hidden_locked(ctx, kind, path, base):
    label = REFRESH_COMMANDS[kind][0]
    argv, why = _windows_refresh_argv(kind)
    if why:
        return why
    workdir = os.path.join(base, "quota-tui")
    problem = _windows_workdir(label, workdir)
    if problem:
        return problem
    wait = max(ctx.refresh_wait, WINDOWS_MIN_WAIT.get(kind, 0.0))
    before = _mtime_or_none(path)
    try:
        proc = _spawn_hidden(argv, workdir)
    except OSError as exc:
        return "%s refresh failed: cannot start %s (%s)" % (label, os.path.basename(argv[0]), errno_name(exc))
    exited_early = False
    try:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not _rewritten(path, before):
            if proc.poll() is not None:
                exited_early = True
                break
            time.sleep(0.25)
    finally:
        stopped = _end_process(proc)
        _windows_clear(workdir)
    if not stopped:
        return "%s refresh failed: %s could not be stopped" % (label, os.path.basename(argv[0]))
    if _rewritten(path, before):                  # judged after the stop: a write that lands as it ends counts
        return None
    if exited_early:
        return ("%s refresh failed: %s exited before it wrote its cache; run it once in a terminal to see why"
                % (label, os.path.basename(argv[0])))
    return "%s refresh failed: %s did not rewrite its cache within %g s" % (label, os.path.basename(argv[0]), wait)


def refresh_sources(sources, ctx, when):
    """{source name: problem} after refreshing every opted-in, stale TUI cache.

    Runs before the collection clock is read (main), so a cache rewritten here
    is never "in the future" of the records. A cache is refreshed when it is
    missing, older than its TTL, more than CLOCK_SKEW_SECONDS ahead of `when`,
    or has an mtime that is not a usable time.
    """
    problems = {}
    for src in sources:
        mode = src.get("refresh")
        if mode not in REFRESH_MODES:
            continue
        path = expand_path(src["path"])
        mtime = _mtime_or_none(path)
        updated = from_epoch(mtime / 1e9) if mtime is not None else None
        if updated is not None:
            age = (when - updated).total_seconds()
            if -CLOCK_SKEW_SECONDS <= age <= src["ttl_seconds"]:
                continue                          # fresh already: nothing to start
        why = (refresh_hidden if mode == REFRESH_SPAWN_HIDDEN else refresh_tui)(ctx, src["kind"], path)
        if why:
            problems[src["name"]] = why
    return problems


def _refresh_problem(src, ctx):
    return ctx.refresh_problems.get(src["name"])


# --------------------------------------------------------------------- agy ----
def collect_agy(src, ctx):
    # two pools, and a missing window of one is NEVER read from the other: the
    # statusline renderer falls back across pools, which would report one pool's
    # headroom as the other's
    pools = (("agy:gemini", "Gemini", "known", "gemini"),
             ("agy:3p", None, "unknown", "3p"))
    windows = (("5h", "five_hour", FIVE_HOUR_MINUTES), ("weekly", "weekly", WEEKLY_MINUTES))
    recs = [new_record(src, ctx, pool_id, family, scope) for pool_id, family, scope, _ in pools]
    why = _refresh_problem(src, ctx)
    if why:
        for rec in recs:
            rec["problems"].append(why)
    doc, mtime = load(src, recs)
    if doc is None:
        return recs
    keys = ["%s-%s" % (prefix, suffix) for _, _, _, prefix in pools for suffix, _, _ in windows]
    if not any(key in doc for key in keys):
        return fail(recs, "parse_error", "no gemini-/3p- window key is present")
    for rec, (_, _, _, prefix) in zip(recs, pools):
        stamp(rec, ctx, src["ttl_seconds"], from_epoch(mtime), "mtime")
        for suffix, kind, minutes in windows:
            key = "%s-%s" % (prefix, suffix)
            entry = doc.get(key)
            if isinstance(entry, dict):
                # reset_in_seconds is ignored: it ages from the moment it was written
                add_window(rec, ctx, key, kind, minutes, entry.get("remaining_fraction"),
                           entry.get("reset_time"), "remaining_fraction", fraction=True)
            else:
                absent_window(rec, key, kind, minutes)
    return recs


# ------------------------------------------------------------- opencode go ----
def collect_opencode_go(src, ctx):
    rec = new_record(src, ctx, "opencode-go", None, "unknown")
    why = _refresh_problem(src, ctx)
    if why:
        rec["problems"].append(why)
    doc, _mtime = load(src, [rec])
    if doc is None:
        return [rec]
    if not (is_int(doc.get("schema")) and doc["schema"] == 1):
        return fail([rec], "unsupported", "cache schema is not 1")

    # fetched_at and usage are the last SUCCESSFUL read and survive later
    # failures, so freshness follows fetched_at and an old success goes stale
    # on its own while attempted_at keeps moving
    fetched = doc.get("fetched_at")
    updated = parse_ts(fetched)
    if fetched is not None and updated is None:
        rec["problems"].append("fetched_at is not a valid timestamp")
    stamp(rec, ctx, src["ttl_seconds"], updated, "writer" if updated else None)

    error = doc.get("error")
    if error is not None:
        http = doc.get("http_status") if is_int(doc.get("http_status")) else None
        no_key = error == "no opencode-go key"
        rec["read_status"] = "auth_error" if no_key or http in (401, 403) else "network_error"
        # the error text itself is a raw body from the writer and is not copied
        if no_key:
            detail = "no opencode-go key"
        elif http is not None and 100 <= http <= 599:
            detail = "HTTP %d" % http
        else:
            detail = "no HTTP status"
        rec["problems"].append("latest read attempt failed (%s)" % detail)

    usage = doc.get("usage")
    if isinstance(usage, dict):
        for key, kind, minutes in (("rolling", "five_hour", FIVE_HOUR_MINUTES),
                                   ("weekly", "weekly", WEEKLY_MINUTES),
                                   ("monthly", "monthly", None)):
            entry = usage.get(key)
            if not isinstance(entry, dict):
                absent_window(rec, key, kind, minutes)
                continue
            status = entry.get("status")
            add_window(rec, ctx, key, kind, minutes, entry.get("percent"),
                       entry.get("resetsAt"), "percent",
                       blocked=None if status == "ok" else status_problem(status))
    elif error is None:
        fail([rec], "parse_error", "usage is missing although the latest read succeeded")
    elif usage is not None:
        rec["problems"].append("usage is not an object")
    return [rec]


# ------------------------------------------------------------------- codex ----
class _LineReader:
    """Newline-delimited JSON off a pipe, read against a deadline.

    A blocking readline() waits forever on a server that wrote half a line and
    stalled, so this selects with the time left and splits lines itself.
    """

    def __init__(self, fd):
        os.set_blocking(fd, False)
        self.fd = fd
        self.buf = b""
        self.total = 0
        self.sel = selectors.DefaultSelector()
        self.sel.register(fd, selectors.EVENT_READ)

    def close(self):
        self.sel.close()

    @staticmethod
    def _response(line, awaited):
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            return None                          # a non-JSON line is skipped
        # `True == 1` in Python; a JSON-RPC id is an int. A message carrying a
        # method is a notification or a server request, not our response.
        if isinstance(msg, dict) and "method" not in msg \
                and is_int(msg.get("id")) and msg["id"] == awaited:
            return msg
        return None

    def response(self, awaited, deadline):
        """(message, None), or (None, why) on EOF, deadline or too much output."""
        while True:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                msg = self._response(line, awaited)
                if msg is not None:
                    return msg, None
            left = deadline - time.monotonic()
            if left <= 0:
                return None, "no reply within the timeout"
            if not self.sel.select(left):
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                return None, "read from codex failed"
            if not chunk:
                msg = self._response(self.buf, awaited) if self.buf else None
                self.buf = b""
                return (msg, None) if msg is not None else (None, "codex closed the stream")
            self.total += len(chunk)
            if self.total > MAX_CODEX_BYTES:
                return None, "codex wrote too much before replying"
            self.buf += chunk


class _ThreadLineReader:
    """_LineReader's interface for Windows, where selectors cannot wait on a pipe.

    A daemon thread blocks in os.read and queues what it gets; response() waits on the queue
    against the same deadline. The thread counts what it read and stops at MAX_CODEX_BYTES, so
    the queue holds at most that much; it also ends on EOF or an error, and the end is
    remembered, so every later response() sees it again (as _LineReader's read does, except
    after the cap: the exchange ends at its first error, so a second wait never happens).
    _reap ends the process, then close() waits for the thread, and the pipe is only closed
    once the thread is gone: a descriptor number is never read after it was reused."""

    def __init__(self, fd):
        self.fd = fd
        self.buf = b""
        self.ended = None                        # b"" (EOF), OSError (a failed read) or _TOO_MUCH
        self.read = 0                            # bytes the thread has read
        self.chunks = queue.Queue()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    _TOO_MUCH = object()

    def _pump(self):
        total = 0
        while True:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                self.chunks.put(OSError)
                return
            total += len(chunk)
            self.read = total
            if total > MAX_CODEX_BYTES:
                self.chunks.put(self._TOO_MUCH)
                return
            self.chunks.put(chunk)
            if not chunk:
                return

    def close(self, wait=2.0):
        """True when the thread is gone (it ends once the process has)."""
        self.thread.join(wait)
        return not self.thread.is_alive()

    _response = staticmethod(_LineReader._response)

    def response(self, awaited, deadline):
        """(message, None), or (None, why) on EOF, deadline or too much output."""
        while True:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                msg = self._response(line, awaited)
                if msg is not None:
                    return msg, None
            if self.ended is not None:
                if self.ended is OSError:
                    return None, "read from codex failed"
                if self.ended is self._TOO_MUCH:
                    return None, "codex wrote too much before replying"
                msg = self._response(self.buf, awaited) if self.buf else None
                self.buf = b""
                return (msg, None) if msg is not None else (None, "codex closed the stream")
            left = deadline - time.monotonic()
            if left <= 0:
                return None, "no reply within the timeout"
            try:
                chunk = self.chunks.get(timeout=left)
            except queue.Empty:
                continue
            if chunk is OSError or chunk is self._TOO_MUCH or not chunk:
                self.ended = chunk
            else:
                self.buf += chunk


def _windows_which(name):
    """The absolute path of `name` found in the PATH directories with the PATHEXT extensions (codex is a
    codex.cmd shim there and Popen searches neither), or None. The current directory is never searched
    (shutil.which does that first on Windows, and so does CreateProcess for a bare name): an empty or
    relative PATH entry and the current directory itself are skipped, and the caller always starts the
    returned absolute path, never the bare name. A name with a directory part is the caller's own choice
    and is used as given."""
    if os.path.dirname(name):
        return name
    here = os.getcwd()
    exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    # a name that already carries a PATHEXT extension is looked for as it is, as shutil.which does
    candidates = [name] if os.path.splitext(name)[1].upper() in [e.upper() for e in exts] else [name + e for e in exts]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not _is_abs(entry):                   # "" and "." are not absolute
            continue
        try:
            if os.path.samefile(entry, here):    # the same directory under another spelling (a junction, 8.3, \\?\)
                continue
        except OSError:
            continue                             # an entry that cannot be opened has nothing to start
        for candidate in candidates:
            full = os.path.join(entry, candidate)
            if os.path.isfile(full):
                return full
    return None


def _send(proc, message):
    data = (json.dumps(message) + "\n").encode("utf-8")
    fd = proc.stdin.fileno()
    while data:
        data = data[os.write(fd, data):]


def _reap(proc, reader):
    """End the process THIS script started, and only it: no pid is computed or signalled.

    SIGTERM, up to 2 s of waiting, then SIGKILL: this is the time reaping adds
    after the exchange's own deadline.
    """
    gone = True
    if IS_WINDOWS:
        _stop(proc)                              # a thread may be blocked reading the pipe: the process ends first
        gone = reader.close() if reader is not None else True
    elif reader is not None:
        reader.close()
    for pipe in (proc.stdin, proc.stdout):
        if pipe is proc.stdout and not gone:
            _LEAKED.append((proc, reader))       # kept for the life of the process: a finalizer closing the descriptor
            continue                             # under the blocked read could hand its number to another file
        try:
            pipe.close()
        except OSError:
            pass
    _stop(proc)


_LEAKED = []        # (proc, reader) whose reader thread was still reading when the process was reaped


def _finish():
    """Leave without running finalizers when a reader thread is still blocked in a read: closing its pipe at
    interpreter exit would block on Windows. The output is flushed first; exit 1 when that fails."""
    if _LEAKED:
        try:
            sys.stdout.flush()
            code = 0
        except (OSError, ValueError):
            code = 1
        os._exit(code)


def _entry():
    """main() with the same guarantee on a failed write, an interrupt or any other escape."""
    try:
        return main()
    except BaseException:
        if _LEAKED:
            try:
                traceback.print_exc()
            finally:
                os._exit(1)
        raise


def _kill_tree(proc):
    """Windows: end the child AND what it started. codex is a codex.cmd shim there; terminate() ends the
    cmd.exe, and the node process under it would keep running (and keep the pipe open). The pid is the one
    Popen returned for the process this script started."""
    root = _windows_dir("SystemRoot") or "C:\\Windows"
    try:                                         # the system's own taskkill, never one found by a bare-name search
        subprocess.run([os.path.join(root, "System32", "taskkill.exe"), "/PID", str(proc.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass                                     # terminate() below is the fallback


def _stop(proc):
    if proc.poll() is None:
        if IS_WINDOWS:
            _kill_tree(proc)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _rpc_problem(msg):
    error = msg.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if is_int(code):
        return "codex returned a JSON-RPC error (code %d)" % code
    return "codex returned a JSON-RPC error"


def codex_exchange(ctx):
    """(result, status, problem): the rate-limits reply, or why there is none.

    ctx.timeout bounds the exchange; reaping the process adds at most about 2 s (on Windows up to
    about 9: taskkill 5, the reader thread's join 2, then terminate's wait 2).
    """
    deadline = time.monotonic() + ctx.timeout
    codex_bin = ctx.codex_bin
    if IS_WINDOWS:
        codex_bin = _windows_which(codex_bin)
        if codex_bin is None:
            return None, "missing", "codex binary not found"
    argv = [codex_bin, "app-server", "--listen", "stdio://"]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return None, "missing", "codex binary not found"
    except OSError as exc:
        return None, "error", "cannot start codex (%s)" % errno_name(exc)
    reader = None
    try:
        reader = (_ThreadLineReader if IS_WINDOWS else _LineReader)(proc.stdout.fileno())
        _send(proc, {"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "dev_lead_quota", "version": ctx.version}}})
        msg, why = reader.response(1, deadline)
        if msg is None:
            return None, "network_error", why
        if msg.get("error") is not None:
            return None, "error", _rpc_problem(msg)
        _send(proc, {"method": "initialized"})
        _send(proc, {"id": 2, "method": "account/rateLimits/read", "params": {}})
        msg, why = reader.response(2, deadline)
        if msg is None:
            return None, "network_error", why
        if msg.get("error") is not None:
            return None, "error", _rpc_problem(msg)
        if not isinstance(msg.get("result"), dict):
            return None, "parse_error", "the reply carries no result object"
        return msg["result"], None, None
    except OSError:
        return None, "network_error", "the connection to codex broke"
    finally:
        _reap(proc, reader)


def codex_snapshots(result):
    """([(limit_id, snapshot)], skipped).

    `rateLimitsByLimitId` is the map of every pool and `rateLimits` repeats one
    entry of it, so the map is used when it has entries and `rateLimits` only
    when it has none; both together would count a pool twice.
    """
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        items = list(by_id.items())
    else:
        single = result.get("rateLimits")
        if isinstance(single, dict):
            limit_id = single.get("limitId")
            items = [(limit_id if isinstance(limit_id, str) and limit_id else "codex", single)]
        else:
            items = []
    good = [(i, s) for i, s in items if isinstance(s, dict) and _LIMIT_ID.fullmatch(i)]
    return good, len(items) - len(good)


def collect_codex(src, ctx):
    result, status, problem = codex_exchange(ctx)
    if status:
        return fail([new_record(src, ctx, "codex:codex", "GPT", "known")], status, problem)
    snapshots, skipped = codex_snapshots(result)
    if not snapshots:
        return fail([new_record(src, ctx, "codex:codex", "GPT", "known")],
                    "parse_error", "the reply carries no usable rate limit snapshot")
    records = []
    for limit_id, snap in snapshots:
        rec = new_record(src, ctx, "codex:" + limit_id, "GPT", "known")
        records.append(rec)
        if skipped:
            rec["problems"].append("%d snapshot(s) skipped: unusable limit id or shape" % skipped)
        if snap.get("primary") is None and snap.get("secondary") is None:
            # an ok record with no window would read as "no limit"
            fail([rec], "parse_error", "the snapshot reports neither a primary nor a secondary window")
            continue
        # the data was read just now, so its time is ours
        stamp(rec, ctx, src["ttl_seconds"], ctx.now, "live")
        for label in ("primary", "secondary"):
            window = snap.get(label)
            if window is None:
                # not listed, and named: one available window must not read as
                # headroom when the other one was not reported
                rec["problems"].append("%s: window not reported, only the other is known" % label)
                continue
            if not isinstance(window, dict):
                absent_window(rec, label, "other", None, "window is not an object")
                continue
            minutes = window.get("windowDurationMins")
            bad_duration = not is_int(minutes) or minutes <= 0
            if bad_duration:
                rec["problems"].append("%s: windowDurationMins is not a positive integer" % label)
                minutes = None
            # primary is not always the 5-hour window, so the duration decides
            kind = {FIVE_HOUR_MINUTES: "five_hour", WEEKLY_MINUTES: "weekly"}.get(minutes, "other")
            add_window(rec, ctx, label, kind, minutes, window.get("usedPercent"),
                       window.get("resetsAt"), "usedPercent", parse=from_epoch,
                       bad_duration=bad_duration)
    return records


KINDS = {
    "codex_app_server": {"support_level": "official", "collect": collect_codex, "path": False},
    "claude_usage_cache": {"support_level": "private_cache", "collect": collect_claude, "path": True},
    "agy_quota_cache": {"support_level": "tui_only", "collect": collect_agy, "path": True},
    "cursor_usage_cache": {"support_level": "private_cache", "collect": collect_cursor, "path": True},
    "opencode_go_cache": {"support_level": "private_cache", "collect": collect_opencode_go, "path": True},
}


def collect(src, ctx):
    try:
        return KINDS[src["kind"]]["collect"](src, ctx)
    except Exception as exc:                      # one broken source must not lose the others
        rec = new_record(src, ctx, src["name"], None, "unknown")
        return fail([rec], "error", "internal error (%s)" % type(exc).__name__)


# ------------------------------------------------------------------ config ----
def load_config(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError("cannot read config %s (%s)" % (path, errno_name(exc)))
    except (ValueError, RecursionError):
        raise UsageError("config %s is not valid JSON" % path)
    if not isinstance(doc, dict) or not (is_int(doc.get("schema_version"))
                                         and doc["schema_version"] == SCHEMA_VERSION):
        raise UsageError("config %s: schema_version must be %d" % (path, SCHEMA_VERSION))
    sources = doc.get("sources")
    if not isinstance(sources, list) or not sources:
        raise UsageError("config %s: sources must be a non-empty list" % path)
    seen = set()
    for index, src in enumerate(sources, 1):
        where = "config %s: source #%d" % (path, index)
        if not isinstance(src, dict):
            raise UsageError("%s is not an object" % where)
        for field in ("name", "kind", "adapter", "billing_provider"):
            if not isinstance(src.get(field), str) or not src[field]:
                raise UsageError("%s: %s must be a non-empty string" % (where, field))
        ttl = src.get("ttl_seconds")
        # an int is compared, never converted: float(10 ** 400) raises
        # OverflowError, so an int gets the range a finite float has
        if is_int(ttl):
            bad_ttl = not 0 < ttl <= sys.float_info.max
        elif isinstance(ttl, float):
            bad_ttl = not (math.isfinite(ttl) and ttl > 0)
        else:
            bad_ttl = True
        if bad_ttl:
            raise UsageError("%s: ttl_seconds must be a positive finite number" % where)
        if src["kind"] not in KINDS:
            raise UsageError("%s: unknown kind %s" % (where, " ".join(src["kind"].split())))
        if KINDS[src["kind"]]["path"] and (not isinstance(src.get("path"), str) or not src["path"]):
            raise UsageError("%s: kind %s needs a path" % (where, src["kind"]))
        if src["name"] in seen:
            raise UsageError("%s: duplicate name %s" % (where, " ".join(src["name"].split())))
        if "refresh" in src and not (src["refresh"] in REFRESH_MODES
                                     and src["kind"] in REFRESH_COMMANDS):
            raise UsageError("%s: refresh is only %s, and only on %s" % (
                where, " or ".join(REFRESH_MODES), " or ".join(sorted(REFRESH_COMMANDS))))
        seen.add(src["name"])
    return sources


def local_config():
    """dev-lead's own plugin data dir copy of the sources file, if there is one.

    The same directory as roster.json (scripts/roster.py roster_path): a
    CLAUDE_PLUGIN_DATA that is another plugin's is ignored. This is where a host
    opts in to `"refresh": "tmux_spawn"`; the shipped data/ file never does.
    """
    plugin = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin and Path(plugin).name.startswith(PLUGIN_NAME + "-"):
        base = Path(plugin)
    else:
        base = Path(os.path.expanduser("~")) / ".claude" / "plugins" / "data" / "dev-lead-dev-lead"
    path = base / LOCAL_CONFIG_NAME
    return str(path) if os.path.isfile(path) else None


def parse_args(argv):
    parser = _Parser(prog="quota-snapshot", description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", action="append", default=[], metavar="NAME",
                        help="run only this source (repeatable); default all")
    parser.add_argument("--config", metavar="PATH",
                        help="sources file; default $%s, else %s in dev-lead's plugin data "
                             "directory, else data/quota-sources.json" % (CONFIG_ENV, LOCAL_CONFIG_NAME))
    parser.add_argument("--now", metavar="ISO8601", help="override the clock (tests)")
    parser.add_argument("--codex-bin", default="codex", metavar="PATH")
    parser.add_argument("--timeout", type=float, default=20.0, metavar="SECONDS",
                        help="bounds the codex exchange; reaping the process adds at "
                             "most about 2 s (default 20)")
    parser.add_argument("--refresh", action="store_true",
                        help="for a source whose config sets refresh to tmux_spawn (agy, cursor, opencode-go) "
                             "and whose cache is missing, older than its TTL, more than 60 s "
                             "in the future or of an unusable mtime, start that CLI (cursor-agent "
                             "with --trust; opencode with --standalone) detached in a private tmux server until it rewrites "
                             "the cache, then kill that server; it never types a key and never "
                             "touches your own tmux server; one refresh runs at a time, and "
                             "the empty directory it starts in is moved aside and emptied "
                             "afterwards (a directory that is not empty at the start is "
                             "set aside the same way, unless a private server of an earlier "
                             "run may still be running)")
    parser.add_argument("--tmux-bin", default="tmux", metavar="PATH")
    parser.add_argument("--refresh-wait", type=float, default=15.0, metavar="SECONDS",
                        help="how long a started CLI gets to rewrite its cache (default 15)")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise UsageError("--timeout must be a positive number")
        if not math.isfinite(args.refresh_wait) or not 0 < args.refresh_wait <= 60:
            raise UsageError("--refresh-wait must be a number of seconds in (0, 60]")
        if args.now is None:
            now = datetime.now(timezone.utc)
        else:
            now = parse_ts(args.now)
            if now is None:
                raise UsageError("--now must be an ISO 8601 time with a zone, e.g. 2026-10-19T01:00:00Z")
        raw_config = args.config or os.environ.get(CONFIG_ENV) or local_config() or str(DEFAULT_CONFIG)
        sources = load_config(expand_path(raw_config))
        known = [src["name"] for src in sources]
        for name in args.source:
            if name not in known:
                raise UsageError("unknown --source %s (known: %s)" % (
                    " ".join(name.split()), ", ".join(known)))
    except UsageError as exc:
        print("quota-snapshot: %s" % " ".join(str(exc).split()), file=sys.stderr)
        return 2

    try:
        _plugin, _data, declared, semver = declared_version(ROOT)
    except (OSError, ValueError, RecursionError):
        declared = semver = None                  # an unreadable manifest costs the version only
    # Only a short X.Y.Z is sent. That bounds the initialize message, and the
    # bound is what makes the blocking stdin write safe: the messages stay a
    # few hundred bytes, which a pipe holds even if codex never reads.
    version = declared if semver is not None and len(declared) <= 32 else "unknown"
    ctx = Context(now, args.codex_bin, args.timeout, version, refresh=args.refresh,
                  tmux_bin=args.tmux_bin, refresh_wait=args.refresh_wait)
    chosen = [src for src in sources if not args.source or src["name"] in args.source]
    if args.refresh:
        ctx.refresh_problems = refresh_sources(chosen, ctx, now)
        if args.now is None:
            # read the clock again: the records are judged after the refresh
            now = ctx.now = datetime.now(timezone.utc)
    records = []
    for src in chosen:
        records.extend(collect(src, ctx))
    out = {"schema_version": SCHEMA_VERSION, "generated_at": fmt_ts(now), "records": records}
    sys.stdout.write(json.dumps(out, indent=2, allow_nan=False) + "\n")
    _finish()
    return 0


if __name__ == "__main__":
    sys.exit(_entry())

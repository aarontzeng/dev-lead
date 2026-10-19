#!/usr/bin/env python3
"""Read-only quota collector: one JSON shape over five differently-kept sources.

    python3 quota-snapshot.py [--source NAME]... [--config PATH] [--now ISO8601]
                              [--codex-bin PATH] [--timeout SECONDS]

Four statusline caches and one CLI protocol each keep their own units (used vs
remaining, percent vs fraction, seconds vs ISO) and fail in their own way. This
reads them all and prints ONE object on stdout:

    {"schema_version": 1, "generated_at": "<ISO UTC>", "records": [...]}

one record per quota pool. The sources, their kinds and their TTLs come from
data/quota-sources.json (--config or DEV_LEAD_QUOTA_SOURCES replaces it);
references/quota-sources.md under skills/dev-lead gives the record shape and
its closed vocabularies, who writes each source, what was verified and what
was not. Nothing consumes the output yet.

Contract:
  * Exit 0 whenever the object was printed, even if every source failed (the
    failures are inside the records). Exit 2 for a usage or config error, with
    one line on stderr and nothing on stdout.
  * It never runs a model and never reads a credential. The only process it
    starts is `codex app-server`, and it reaps that one itself.
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
import re
import selectors
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _common import ROOT, declared_version

SCHEMA_VERSION = 1
DEFAULT_CONFIG = ROOT / "data" / "quota-sources.json"
CONFIG_ENV = "DEV_LEAD_QUOTA_SOURCES"
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
    def __init__(self, now, codex_bin, timeout, version):
        self.now = now
        self.codex_bin = codex_bin
        self.timeout = timeout
        self.version = version


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


# ----------------------------------------------------------------- records ----
def expand_path(raw):
    """A leading ~ and ${XDG_CACHE_HOME}; the latter only when set, non-empty and absolute."""
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    cache = xdg if xdg and os.path.isabs(xdg) else os.path.join(home, ".cache")
    path = raw.replace("${XDG_CACHE_HOME}", cache)
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
    if before.st_uid != os.geteuid():
        return refused("cache file is not owned by the current user")
    if before.st_size > MAX_CACHE_BYTES:
        return refused("cache file is larger than 1 MiB")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
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
        if after.st_uid != os.geteuid():
            return refused("cache file is not owned by the current user")
        raw = fh.read(MAX_CACHE_BYTES + 1)
    if len(raw) > MAX_CACHE_BYTES:
        return refused("cache file is larger than 1 MiB")
    try:
        doc = json.loads(raw.decode("utf-8"))
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


# --------------------------------------------------------------------- agy ----
def collect_agy(src, ctx):
    # two pools, and a missing window of one is NEVER read from the other: the
    # statusline renderer falls back across pools, which would report one pool's
    # headroom as the other's
    pools = (("agy:gemini", "Gemini", "known", "gemini"),
             ("agy:3p", None, "unknown", "3p"))
    windows = (("5h", "five_hour", FIVE_HOUR_MINUTES), ("weekly", "weekly", WEEKLY_MINUTES))
    recs = [new_record(src, ctx, pool_id, family, scope) for pool_id, family, scope, _ in pools]
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
    if reader is not None:
        reader.close()
    for pipe in (proc.stdin, proc.stdout):
        try:
            pipe.close()
        except OSError:
            pass
    if proc.poll() is None:
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

    ctx.timeout bounds the exchange; reaping the process adds at most about 2 s.
    """
    deadline = time.monotonic() + ctx.timeout
    argv = [ctx.codex_bin, "app-server", "--listen", "stdio://"]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return None, "missing", "codex binary not found"
    except OSError as exc:
        return None, "error", "cannot start codex (%s)" % errno_name(exc)
    reader = None
    try:
        reader = _LineReader(proc.stdout.fileno())
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
        seen.add(src["name"])
    return sources


def parse_args(argv):
    parser = _Parser(prog="quota-snapshot", description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", action="append", default=[], metavar="NAME",
                        help="run only this source (repeatable); default all")
    parser.add_argument("--config", metavar="PATH",
                        help="sources file; default $%s or data/quota-sources.json" % CONFIG_ENV)
    parser.add_argument("--now", metavar="ISO8601", help="override the clock (tests)")
    parser.add_argument("--codex-bin", default="codex", metavar="PATH")
    parser.add_argument("--timeout", type=float, default=20.0, metavar="SECONDS",
                        help="bounds the codex exchange; reaping the process adds at "
                             "most about 2 s (default 20)")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise UsageError("--timeout must be a positive number")
        if args.now is None:
            now = datetime.now(timezone.utc)
        else:
            now = parse_ts(args.now)
            if now is None:
                raise UsageError("--now must be an ISO 8601 time with a zone, e.g. 2026-10-19T01:00:00Z")
        raw_config = args.config or os.environ.get(CONFIG_ENV) or str(DEFAULT_CONFIG)
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
    ctx = Context(now, args.codex_bin, args.timeout, version)
    records = []
    for src in sources:
        if not args.source or src["name"] in args.source:
            records.extend(collect(src, ctx))
    out = {"schema_version": SCHEMA_VERSION, "generated_at": fmt_ts(now), "records": records}
    sys.stdout.write(json.dumps(out, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fetch one usage reading and write it as the cache quota-snapshot.py reads.

Usage: quota-fetch.py {claude|cursor} --out <cache path>

This is the half of the quota pane that DOES read a credential, kept out of
quota-snapshot.py (which never does) so that the collector's contract stays
what it was. quota-snapshot runs it only for a source a host marked
`"refresh": "fetch"` (scripts/quota-setup.py writes that), and only when the
cache is missing or older than its TTL.

  claude  the Claude Code login's OAuth token: CLAUDE_CODE_OAUTH_TOKEN, the
          macOS Keychain item "Claude Code-credentials", or
          ~/.claude/.credentials.json (claudeAiOauth.accessToken).
          GET https://api.anthropic.com/api/oauth/usage; only its five_hour /
          seven_day / seven_day_* windows (utilization, resets_at) are stored,
          which is what collect_claude reads.
  cursor  the Cursor login's access token: ~/.config/cursor/auth.json
          (cursor-agent) or the Cursor IDE's state.vscdb.
          Connect RPCs GetSandUsageStatus / GetPlanInfo / GetCurrentPeriodUsage;
          stored as {"schema": 2, "unified": ..., "weekly_pct", "weekly_reset", "plan"},
          which collect_cursor reads. A plan that is not unified is stored as
          {"schema": 2, "unified": false} (the collector reports it unsupported).

Contract:
  * The token is sent to its own vendor's endpoint over HTTPS and nowhere else
    (a redirect is never followed); it is never printed, logged or written to
    disk, and this script puts it in no environment or argv (a
    CLAUDE_CODE_OAUTH_TOKEN the person exported is theirs, and is read from
    there; it is not passed on to the Keychain lookup). Only the fields the
    collector reads are stored, never the whole answer.
  * One fetch ends within about WHOLE_FETCH seconds, all requests together:
    no request starts and no body read continues past it (one read in flight
    can overrun it by its socket timeout). Not on Windows yet: it refuses.
  * Exit 0 when the cache was written; 1 when it was not (one line on stderr
    naming the step, never a token or a response body); 2 for a usage error.
  * The cache is written atomically (a private temporary file in the same
    directory, then a rename), mode 0600, both through the directory's
    descriptor. The directory is created 0700 when it is missing; an existing
    one is refused unless it is a directory of the user's (not a link); one the
    group or others could reach is made 0700 first (which also zeroes a POSIX
    ACL's mask), and every directory above it must be root's or the user's and
    not writable by group or others unless sticky -- /tmp is shared, and a
    directory someone else can rename could be used to steer the write.
  * NODE_EXTRA_CA_CERTS (a company CA that Claude Code itself is given) is
    trusted in addition to the system store; HTTPS_PROXY is honoured.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

TIMEOUT = 10
WHOLE_FETCH = 20         # every request of one fetch together; quota-snapshot waits a little longer
MAX_BODY = 1 << 20
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CURSOR_RPC = "https://api2.cursor.sh/aiserver.v1.DashboardService/"
IS_WINDOWS = os.name == "nt"


class FetchError(Exception):
    """A step failed; the message names the step and never carries a secret."""


# ------------------------------------------------------------------ HTTPS ----
def _ssl_context():
    ctx = ssl.create_default_context()
    extra = os.environ.get("NODE_EXTRA_CA_CERTS")
    if extra:
        try:
            ctx.load_verify_locations(cafile=extra)
        except (OSError, ssl.SSLError):
            pass                                  # the system store still applies
    return ctx


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the Authorization header would go with it to whatever host it names
    (review 2026-11-03). A 3xx then surfaces as an HTTPError, i.e. a failed fetch."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DEADLINE = [None]          # monotonic time the whole fetch must end by (main sets it)


def _request(url, headers, data=None):
    """(status, parsed JSON object or None). Never raises for an HTTP status; never follows a redirect."""
    if not url.startswith("https://"):
        raise FetchError("refusing a non-HTTPS URL")
    timeout = TIMEOUT
    if _DEADLINE[0] is not None:
        timeout = min(TIMEOUT, _DEADLINE[0] - time.monotonic())
        if timeout <= 0:
            raise FetchError("ran out of time")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            status, chunks, size = resp.status, [], 0
            while size <= MAX_BODY:
                if _DEADLINE[0] is not None and time.monotonic() > _DEADLINE[0]:
                    raise FetchError("ran out of time")
                # read1: what has arrived, not "fill 16 KiB" -- a body that trickles a byte at a time
                # would otherwise keep one read going past the deadline (review 2026-11-03)
                chunk = resp.read1(16384) if hasattr(resp, "read1") else resp.read(16384)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError("request failed (%s)" % type(exc).__name__) from None
    if len(raw) > MAX_BODY:
        return status, None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, None
    return status, doc if isinstance(doc, dict) else None


# ------------------------------------------------------------------ claude ----
def _claude_token():
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if tok:
        return tok
    if sys.platform == "darwin":
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
            out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                 capture_output=True, text=True, timeout=10, check=False, env=env).stdout
            tok = (json.loads(out).get("claudeAiOauth") or {}).get("accessToken") if out.strip() else None
            if isinstance(tok, str) and tok:
                return tok
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            pass
    path = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
    try:
        with open(path, encoding="utf-8") as fh:
            tok = (json.load(fh).get("claudeAiOauth") or {}).get("accessToken")
    except (OSError, ValueError, AttributeError):
        tok = None
    if isinstance(tok, str) and tok:
        return tok
    raise FetchError("no Claude Code login found (CLAUDE_CODE_OAUTH_TOKEN, the Keychain or ~/.claude/.credentials.json)")


def _usable_claude(doc):
    def window(key):
        entry = doc.get(key)
        return (isinstance(entry, dict) and isinstance(entry.get("utilization"), (int, float))
                and not isinstance(entry.get("utilization"), bool))
    return window("five_hour") or window("seven_day")


def fetch_claude():
    status, doc = _request(CLAUDE_USAGE_URL, {
        "Accept": "application/json",
        "Authorization": "Bearer " + _claude_token(),
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.76",
    })
    if status != 200:
        raise FetchError("usage endpoint answered HTTP %s" % status)
    if doc is None or not _usable_claude(doc):
        raise FetchError("usage endpoint answered without a five_hour or seven_day utilization")
    return _claude_windows(doc)


def _claude_windows(doc):
    """Only what collect_claude reads: the five_hour / seven_day / seven_day_* windows, each reduced to
    utilization and resets_at (a null window stays null). Nothing else in the answer reaches the disk."""
    out = {}
    for key, value in doc.items():
        if not isinstance(key, str) or not (key in ("five_hour", "seven_day") or key.startswith("seven_day_")):
            continue
        if value is None:
            out[key] = None
        elif isinstance(value, dict) and "utilization" in value:
            entry = {}
            used = value.get("utilization")
            if used is None or (isinstance(used, (int, float)) and not isinstance(used, bool)):
                entry["utilization"] = used
            if "resets_at" in value and (value["resets_at"] is None or isinstance(value["resets_at"], str)):
                entry["resets_at"] = value["resets_at"]
            out[key] = entry
    return out


# ------------------------------------------------------------------ cursor ----
def _cursor_state_db():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Cursor", "User", "globalStorage", "state.vscdb")
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, "Cursor", "User", "globalStorage", "state.vscdb")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, "Cursor", "User", "globalStorage", "state.vscdb")


def _cursor_token():
    if IS_WINDOWS:
        # the same places, in the same order, as aadev's cursor statusline.ps1 looks
        env = os.environ.get
        candidates = [os.path.join(d, *tail) for d, tail in (
            (env("XDG_CONFIG_HOME"), ("cursor", "auth.json")),
            (env("USERPROFILE"), (".config", "cursor", "auth.json")),
            (env("APPDATA"), ("cursor", "auth.json")),
            (env("APPDATA"), ("Cursor", "auth.json")),
            (env("LOCALAPPDATA"), ("cursor", "auth.json")),
            (env("LOCALAPPDATA"), ("Cursor", "auth.json"))) if d]
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        candidates = [os.path.join(base, "cursor", "auth.json")]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                tok = json.load(fh).get("accessToken")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(tok, str) and tok:
            return tok
    db = _cursor_state_db()
    if os.path.isfile(db):
        try:
            import sqlite3
            uri = "file:" + urllib.request.pathname2url(db) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                row = con.execute("SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'").fetchone()
            finally:
                con.close()
            if row and isinstance(row[0], str) and row[0]:
                return row[0]
        except Exception:                         # noqa: BLE001 -- any sqlite failure is "not found here"
            pass
    raise FetchError("no Cursor login found (cursor auth.json or the Cursor IDE's state.vscdb)")


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def fetch_cursor():
    token = _cursor_token()

    def rpc(name):
        status, doc = _request(CURSOR_RPC + name, {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }, data=b"{}")
        return doc if status == 200 else None

    sand = rpc("GetSandUsageStatus")
    if sand is None:
        raise FetchError("Cursor GetSandUsageStatus did not answer")
    if sand.get("isUnified") is not True:
        return {"schema": 2, "unified": False}
    plan = sand.get("cursorPlanName") if isinstance(sand.get("cursorPlanName"), str) else ""
    pct = _number(sand.get("usagePercent"))
    reset = sand.get("nextResetTimestampUtc") if isinstance(sand.get("nextResetTimestampUtc"), str) else ""
    info = rpc("GetPlanInfo")
    if isinstance(info, dict) and isinstance(info.get("planInfo"), dict):
        pi = info["planInfo"]
        if not plan and isinstance(pi.get("planName"), str):
            plan = pi["planName"]
        if not reset:
            ms = _number(pi.get("includedUsageResetsAt"))
            if ms:
                import datetime
                reset = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    period = rpc("GetCurrentPeriodUsage")
    if isinstance(period, dict) and isinstance(period.get("planUsage"), dict):
        pu = period["planUsage"]
        got = _number(pu.get("totalPercentUsed"))
        if got is None:
            got = _number(pu.get("autoPercentUsed"))
        if got is not None:
            pct = got
    if pct is None:
        raise FetchError("Cursor answered no weekly usage percentage")
    return {"schema": 2, "unified": True, "plan": plan,
            "weekly_pct": pct * 100 if pct <= 1 else pct, "weekly_reset": reset}


# ------------------------------------------------------------------- write ----
def _ancestors_safe(real_parent):
    """Every directory from / down to real_parent (already resolved) is owned by root or the user, and
    nobody else can write it unless it is sticky (as /tmp is): then nobody else can rename or replace the
    next component between the check and the write (review 2026-11-03)."""
    uid = os.getuid()
    parts = real_parent.split(os.sep)
    for i in range(1, len(parts) + 1):
        here = os.sep + os.path.join(*parts[1:i]) if i > 1 else os.sep
        st = os.lstat(here)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, uid):
            return False
        if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
            return False
    return True


def _open_private_dir(directory):
    """A descriptor on the cache directory, created 0700 when missing, refused unless it is a directory of
    the user's (not a link) that nobody else can write, reached through directories nobody else can steer."""
    real_parent = os.path.realpath(os.path.dirname(directory))
    name = os.path.basename(directory)
    if not _ancestors_safe(real_parent):
        raise FetchError("a directory above the cache can be changed by another user")
    pfd = os.open(real_parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=pfd)
        except FileExistsError:
            pass
        try:
            dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except OSError:
            raise FetchError("the cache directory is not a plain directory") from None
    finally:
        os.close(pfd)
    st = os.fstat(dfd)
    if st.st_uid != os.getuid():
        os.close(dfd)
        raise FetchError("the cache directory is owned by another user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        # ours but open to the group (umask 002 makes /tmp/claude 775): make it 0700. That also zeroes a
        # POSIX ACL's mask, so a named-user ACL entry cannot let anyone else write here (review 2026-11-03).
        try:
            os.fchmod(dfd, 0o700)
            st = os.fstat(dfd)
        except OSError:
            pass
    if stat.S_IMODE(st.st_mode) & 0o077:
        os.close(dfd)
        raise FetchError("the cache directory can be reached by others and could not be made private")
    return dfd


def write_cache(path, doc):
    """Atomically, 0600: a new private file in the verified directory, renamed over the cache, both by
    the directory's descriptor so the directory cannot be swapped under the write."""
    data = json.dumps(doc).encode("utf-8")
    if IS_WINDOWS:
        # no owner/mode checks or dir_fd there yet: refuse rather than write by path into a directory
        # nobody checked (review 2026-11-03); quota-setup does not offer fetch on Windows either
        raise FetchError("not supported on Windows yet")
    dfd = _open_private_dir(os.path.dirname(os.path.abspath(path)))
    final = os.path.basename(path)
    tmp = ".quota-fetch.%s.tmp" % os.urandom(8).hex()
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, final, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


FETCHERS = {"claude": fetch_claude, "cursor": fetch_cursor}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", choices=sorted(FETCHERS))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if not os.path.isabs(os.path.expanduser(args.out)):
        print("quota-fetch: --out must be an absolute path", file=sys.stderr)
        return 2
    _DEADLINE[0] = time.monotonic() + WHOLE_FETCH
    try:
        write_cache(os.path.expanduser(args.out), FETCHERS[args.source]())
    except FetchError as exc:
        print("quota-fetch %s: %s" % (args.source, exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("quota-fetch %s: cannot write the cache (%s)" % (args.source, type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

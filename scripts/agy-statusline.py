#!/usr/bin/env python3
"""A minimal agy (Antigravity CLI) status line that keeps dev-lead's agy quota cache.

agy hands its status-line command a JSON object on stdin; when that object
carries `quota`, this stores it at the path quota-snapshot.py reads
(<tmp>/gemini/statusline-quota-cache.json, <tmp> being /tmp, or %TEMP% on
Windows) and prints one short line: the model and the Gemini 5 h / weekly use.

Installed by quota-setup.py, only on a host whose agy has no status line of
its own (a person's own status line is never replaced; aadev's already writes
the same cache). The cache is written atomically, mode 0600, through the
descriptor of a directory the user owns that nobody else can write (created
0700 when missing), reached through directories nobody else can steer; a
directory that fails that is left alone and nothing is written. It never
fails the status line: any error, bad input included, prints the bare line.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile

IS_WINDOWS = os.name == "nt"


def cache_path():
    base = os.environ.get("TEMP") if IS_WINDOWS else "/tmp"
    return os.path.join(base or tempfile.gettempdir(), "gemini", "statusline-quota-cache.json")


def _ancestors_safe(real_parent):
    """Every directory from / down is root's or the user's and not writable by others unless sticky
    (the same rule as quota-fetch.py: kept in step by hand, this file is copied out alone)."""
    uid = os.getuid()
    parts = real_parent.split(os.sep)
    for i in range(1, len(parts) + 1):
        st = os.lstat(os.sep + os.path.join(*parts[1:i]) if i > 1 else os.sep)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, uid):
            return False
        if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
            return False
    return True


def store(quota, path):
    data = json.dumps(quota).encode("utf-8")
    directory = os.path.dirname(path)
    if IS_WINDOWS:                                # no directory checks there yet: write nothing
        return
    real_parent = os.path.realpath(os.path.dirname(directory))
    if not _ancestors_safe(real_parent):
        return
    pfd = os.open(real_parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.mkdir(os.path.basename(directory), 0o700, dir_fd=pfd)
        except FileExistsError:
            pass
        dfd = os.open(os.path.basename(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    finally:
        os.close(pfd)
    try:
        st = os.fstat(dfd)
        if st.st_uid != os.getuid():
            return
        if stat.S_IMODE(st.st_mode) & 0o077:      # ours: made 0700, which also zeroes an ACL mask
            try:
                os.fchmod(dfd, 0o700)
                st = os.fstat(dfd)
            except OSError:
                return
        if stat.S_IMODE(st.st_mode) & 0o077:
            return
        tmp = ".agy-statusline.%s.tmp" % os.urandom(8).hex()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, os.path.basename(path), src_dir_fd=dfd, dst_dir_fd=dfd)
        except OSError:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
    finally:
        os.close(dfd)


def _pct(entry):
    """Used share of a window: agy reports `remaining_fraction` (0..1)."""
    if isinstance(entry, dict):
        value = entry.get("remaining_fraction")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            return "%d%%" % round((1 - value) * 100)
    return "?"


def main():
    try:
        line = render(sys.stdin.buffer.read())
    except Exception:                             # noqa: BLE001 -- the status line never fails
        line = "agy"
    try:
        print(line)
    except OSError:
        pass
    return 0


def render(raw):
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    model = doc.get("model")
    name = model.get("display_name") if isinstance(model, dict) else model
    line = str(name or "agy")
    quota = doc.get("quota")
    if isinstance(quota, dict) and quota:
        try:
            store(quota, cache_path())
        except Exception:                         # noqa: BLE001 -- never break the status line
            pass
        line += "  5h %s  wk %s" % (_pct(quota.get("gemini-5h")), _pct(quota.get("gemini-weekly")))
    return line


if __name__ == "__main__":
    sys.exit(main())

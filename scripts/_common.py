"""What the scripts next to this file share.

Not a program. Each script is still run as `python3 scripts/<name>.py`, which
puts this directory first on sys.path, so `import _common` works from any of
them; roster.py, which leg-cmd.sh loads by file path, adds the directory
itself. Added 0.6.49 to replace four copies of the git spawn, three of the
manifest read and three of the family inference.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def semver(text):
    """(major, minor, patch), or None if `text` is not exactly X.Y.Z.

    A non-string is None too, not a TypeError: a manifest can say
    `"version": 7`, and every caller treats None as "not a version"."""
    if not isinstance(text, str):
        return None
    m = VERSION_RE.match(text)
    return tuple(int(g) for g in m.groups()) if m else None


def run_git(repo, *args, timeout=None, text=False, errors=None):
    """The one place a script spawns git: `git -C <repo> <args>`, output captured.

    Returns the CompletedProcess; what a non-zero status MEANS is the caller's
    (lint reads None, triage raises its input error, claim-audit exits 2), so
    the mapping stays where its contract is documented. OSError (no git, an
    unusable directory) and subprocess.TimeoutExpired propagate for the same
    reason.
    """
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=text, errors=errors, timeout=timeout)


def read_manifest(root):
    """(path, data, problem) for <root>/.claude-plugin/plugin.json.

    `data` is the parsed object or None; `problem` is "missing", "invalid JSON:
    ..." or None. Callers that only need to know the file is unreadable, and
    that check_manifest already said so, test `data is None`.
    """
    path = Path(root) / ".claude-plugin" / "plugin.json"
    if not path.is_file():
        return path, None, "missing"
    try:
        return path, json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return path, None, "invalid JSON: %s" % exc


def declared_version(root):
    """(path, data, declared, version): the manifest's `version` as written and
    as semver, with `data` None when the manifest could not be read at all."""
    path, data, _problem = read_manifest(root)
    declared = data.get("version") if isinstance(data, dict) else None
    return path, data, declared, semver(declared)


def infer_family(serves, leg):
    """The family a leg belongs to, or None when it cannot be told.

    An explicit `family` on the leg wins. Otherwise an adapter that serves
    exactly one family is that family, and the leg may omit the key; one that
    serves several (cursor, opencode, agy) cannot be guessed at.
    """
    if isinstance(leg, dict) and leg.get("family"):
        return leg["family"]
    serves = list(serves or [])
    return serves[0] if len(serves) == 1 else None

#!/usr/bin/env python3
"""Refuse an opencode review launch whose target has no read-only opencode.json.

opencode has no read-only mode: the lock-down config at the project root is
the whole boundary, and without it `opencode run` starts as the build agent
with edit and bash open. leg-cmd.sh puts this check at the head of the
emitted command, so it runs against the tree the leg will really use.

    check-opencode-lockdown.py <target-dir>

This is an ALLOW-list of the one shape the review skill documents, not a list of
known ways to reopen a door: opencode keeps the LAST matching rule, merges later
config sources after the project file, and has aliases and array forms of the
same permissions, so a deny-list is never finished. Exit 0 only when ALL hold:
  - <target> is the root of a git checkout with a commit (opencode binds a
    project's config only there; with zero commits the log says projectID=global
    and every rule is silently ignored; in a subdirectory the file is not read);
  - <target>/opencode.json is a regular file whose top-level keys are only
    "$schema" and "permission", and permission has only the keys edit,
    external_directory and bash, plus write/patch/apply_patch/shell if "deny";
  - edit is "deny";
  - external_directory is "deny", or an object whose FIRST rule is "*": "deny"
    and whose other rules are "deny" or an "allow" of a literal absolute path
    (2+ segments, none empty, "." or "..", no wildcard but a trailing "/*");
  - bash is "deny", or an object whose FIRST rule is "*": "deny" and whose other
    rules are "deny" or an "allow" of one of READ_ONLY_BASH (the skill's list);
  - no opencode.jsonc, no .opencode entry in the target, and no OPENCODE_CONFIG*
    or OPENCODE_DISABLE* variable in the environment (sources merged after, or
    switches that drop, this file).
Known limit: a managed config the host administrator installs outside the target
(/etc/opencode) is merged after the project file and is not read here.
Exit 1 with the reason otherwise.
"""
import json
import os
import re
import subprocess
import sys

TEMPLATE = "skills/opencode-adversarial-review/SKILL.md (the opencode.json block under \"The read-only boundary\")"

# The skill's own allow list: five read-only git commands (a trailing * matches their options,
# so `git diff --output=<path>` is the documented limit of this boundary, see the SKILL).
READ_ONLY_BASH = frozenset(("git status*", "git log*", "git diff*", "git show*", "git rev-parse*"))
EXTRA_DENY_ONLY = ("write", "patch", "apply_patch", "shell")   # aliases of edit / bash
_SEGMENT = re.compile(r"[A-Za-z0-9_.@+~ -]+\Z")


def _literal_path(key):
    """An absolute path of 2+ literal segments, optionally ending in /*: no empty, "." or ".." segment
    (they normalize to a broader path: "/././*" is "/*"), no other wildcard."""
    parts = key.split("/")
    if parts[0] != "":
        return False
    parts = parts[1:]
    if parts and parts[-1] == "*":
        parts = parts[:-1]
    return len(parts) >= 2 and all(p not in (".", "..") and _SEGMENT.match(p) for p in parts)


def _rules(rule, allowed):
    """Why `rule` is not a deny-by-default rule list, or None. `allowed(key)` says which allow keys pass."""
    if rule == "deny":
        return None
    if not isinstance(rule, dict) or next(iter(rule.items()), None) != ("*", "deny"):
        return "must be \"deny\" or an object whose first rule is \"*\": \"deny\""
    for key, value in list(rule.items())[1:]:
        if value == "deny":
            continue
        if value != "allow" or not allowed(key):
            return "rule %r: %r is not an allowed rule" % (key, value)
    return None


def problem(target):
    path = os.path.join(target, "opencode.json")
    if os.path.islink(path) or not os.path.isfile(path):
        return "%s is missing or not a regular file" % path
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return "cannot read %s: %s" % (path, exc)
    top = subprocess.run(["git", "-C", target, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    commit = subprocess.run(["git", "-C", target, "rev-parse", "--verify", "-q", "HEAD^{commit}"],
                            capture_output=True, text=True)
    if top.returncode != 0 or commit.returncode != 0 \
            or os.path.realpath(top.stdout.strip()) != os.path.realpath(target):
        return ("%s is not the root of a git checkout with a commit; opencode reads opencode.json only there "
                "(projectID=global otherwise)" % target)
    if not isinstance(cfg, dict):
        return "%s: the config must be a JSON object" % path
    extra = sorted(set(cfg) - {"$schema", "permission"})
    if extra:
        return "%s: unexpected top-level key(s) %s; only \"$schema\" and \"permission\" are allowed" % (path, extra)
    perm = cfg.get("permission")
    if not isinstance(perm, dict):
        return "%s has no permission block" % path
    extra = sorted(set(perm) - {"edit", "external_directory", "bash"} - set(EXTRA_DENY_ONLY))
    if extra:
        return "%s: unexpected permission key(s) %s" % (path, extra)
    if perm.get("edit") != "deny":
        return "%s: permission.edit must be \"deny\"" % path
    why = _rules(perm.get("external_directory"), _literal_path)
    if why:
        return "%s: permission.external_directory %s (allow rules: literal absolute paths)" % (path, why)
    why = _rules(perm.get("bash"), lambda k: k in READ_ONLY_BASH)
    if why:
        return "%s: permission.bash %s (allow rules: %s)" % (path, why, ", ".join(sorted(READ_ONLY_BASH)))
    for key in EXTRA_DENY_ONLY:
        if key in perm and perm[key] != "deny":
            return "%s: permission.%s is an alias of edit/bash and must be \"deny\" if present" % (path, key)
    for extra in ("opencode.jsonc", ".opencode"):
        if os.path.lexists(os.path.join(target, extra)):
            return "%s is merged after opencode.json and could reopen a door; remove it from the review target" % os.path.join(target, extra)
    for var in sorted(os.environ):
        if os.environ[var] and (var.startswith("OPENCODE_CONFIG") or var.startswith("OPENCODE_DISABLE")):
            return "%s is set and changes which config opencode reads; unset it for the review leg" % var
    return None


def main(argv):
    if len(argv) != 2:
        print("usage: check-opencode-lockdown.py <target-dir>", file=sys.stderr)
        return 2
    why = problem(argv[1])
    if why:
        print("check-opencode-lockdown: %s\n  without it the leg runs as the build agent with edit and bash open; "
              "write the lock-down config first: %s" % (why, TEMPLATE), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

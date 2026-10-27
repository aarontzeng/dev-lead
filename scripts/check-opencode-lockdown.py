#!/usr/bin/env python3
"""Refuse an opencode review launch whose target has no read-only opencode.json.

opencode has no read-only mode: the lock-down config at the project root is
the whole boundary, and without it `opencode run` starts as the build agent
with edit and bash open. leg-cmd.sh puts this check at the head of the
emitted command, so it runs against the tree the leg will really run in.

    check-opencode-lockdown.py <target-dir>

Exit 0 when <target>/opencode.json is a regular file whose permission block has
  edit: "deny"
  external_directory: "deny" (or an object whose FIRST rule is "*": "deny")
  bash: "deny" (or an object whose FIRST rule is "*": "deny")
Exit 1 with the reason otherwise. It checks the shape that closes the three
doors, not every allow-list entry: the file is the lead's own scaffold.
"""
import json
import os
import sys

TEMPLATE = "skills/opencode-adversarial-review/SKILL.md (the opencode.json block under \"The read-only boundary\")"


def _denied(rule):
    if rule == "deny":
        return True
    return isinstance(rule, dict) and next(iter(rule.items()), None) == ("*", "deny")


def problem(target):
    path = os.path.join(target, "opencode.json")
    if os.path.islink(path) or not os.path.isfile(path):
        return "%s is missing or not a regular file" % path
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return "cannot read %s: %s" % (path, exc)
    perm = cfg.get("permission") if isinstance(cfg, dict) else None
    if not isinstance(perm, dict):
        return "%s has no permission block" % path
    if perm.get("edit") != "deny":
        return "%s: permission.edit must be \"deny\"" % path
    if not _denied(perm.get("external_directory")):
        return "%s: permission.external_directory must be \"deny\" (or an object starting with \"*\": \"deny\")" % path
    if not _denied(perm.get("bash")):
        return "%s: permission.bash must be \"deny\" or an object starting with \"*\": \"deny\"" % path
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

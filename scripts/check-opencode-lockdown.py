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
and nothing can append a later rule that reopens a door. opencode keeps the LAST
matching rule, merges later config sources after the project file, and maps the
aliases write/patch/apply_patch to edit and shell to bash, so the check also refuses
  - a later alias key that is not "deny",
  - an allow rule in a bash or external_directory object that is not a literal
    prefix (a pattern such as "**" or "?*" matches everything),
  - any agent.*.permission block in the file,
  - a target that is not a git checkout with a commit (opencode binds a project's
    config only then: with zero commits the log says projectID=global and every
    rule in the file is silently ignored),
  - an opencode.jsonc or .opencode/opencode.json in the target, or OPENCODE_CONFIG /
    OPENCODE_CONFIG_CONTENT in the environment (sources merged after this file).
Exit 1 with the reason otherwise. The allow list of the file is the lead's own scaffold.
"""
import json
import os
import re
import subprocess
import sys

TEMPLATE = "skills/opencode-adversarial-review/SKILL.md (the opencode.json block under \"The read-only boundary\")"


ALIASES = ("write", "patch", "apply_patch", "shell")
# a literal prefix, optionally ending in one "*": "git status*", "/ctx/*"; not "**", "?*" or "/*"
_BASH_ALLOW = re.compile(r"[A-Za-z][^*?\[\]{}]*\*?\Z")
_PATH_ALLOW = re.compile(r"/[^*?\[\]{}/][^*?\[\]{}]*\*?\Z")


def _denied(rule, allow_ok):
    if rule == "deny":
        return True
    if not isinstance(rule, dict) or next(iter(rule.items()), None) != ("*", "deny"):
        return False
    return all(v in ("allow", "deny", "ask") and (v != "allow" or allow_ok.match(k))
               for k, v in list(rule.items())[1:])


def problem(target):
    path = os.path.join(target, "opencode.json")
    if os.path.islink(path) or not os.path.isfile(path):
        return "%s is missing or not a regular file" % path
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return "cannot read %s: %s" % (path, exc)
    git = subprocess.run(["git", "-C", target, "rev-parse", "--verify", "-q", "HEAD^{commit}"],
                         capture_output=True, text=True)
    if git.returncode != 0:
        return "%s is not a git checkout with a commit; opencode ignores opencode.json there (projectID=global)" % target
    perm = cfg.get("permission") if isinstance(cfg, dict) else None
    if not isinstance(perm, dict):
        return "%s has no permission block" % path
    if perm.get("edit") != "deny":
        return "%s: permission.edit must be \"deny\"" % path
    if not _denied(perm.get("external_directory"), _PATH_ALLOW):
        return ("%s: permission.external_directory must be \"deny\" (or an object starting with \"*\": \"deny\" "
                "whose allow rules are literal paths)" % path)
    if not _denied(perm.get("bash"), _BASH_ALLOW):
        return ("%s: permission.bash must be \"deny\" or an object starting with \"*\": \"deny\" "
                "whose allow rules are literal command prefixes" % path)
    for key in ALIASES:
        if key in perm and perm[key] != "deny":
            return "%s: permission.%s is an alias of edit/bash and must be \"deny\" if present" % (path, key)
    for name, agent in (cfg.get("agent") or {}).items():
        if isinstance(agent, dict) and "permission" in agent:
            return "%s: agent.%s.permission is applied after the top-level rules and could reopen them" % (path, name)
    for extra in ("opencode.jsonc", os.path.join(".opencode", "opencode.json")):
        if os.path.lexists(os.path.join(target, extra)):
            return "%s is merged after opencode.json and could reopen a door; remove it from the review target" % os.path.join(target, extra)
    for var in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT"):
        if os.environ.get(var):
            return "%s is set and is merged after the project file; unset it for the review leg" % var
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

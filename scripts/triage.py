#!/usr/bin/env python3
"""Rule-based Gerrit review triage for dev-lead (standard library only)."""
import argparse
import hashlib
import json
import math
import os
import posixpath
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import roster
from _common import infer_family, run_git


ROOT = Path(__file__).resolve().parent.parent
RISKS = ("LOW", "MEDIUM", "HIGH")
RISK_VALUE = {name: number for number, name in enumerate(RISKS)}
# SKILL.md's reviewer table requires two independent families for HIGH and
# says to take the second reviewer for MEDIUM; LOW keeps one review leg.
MIN_REVIEW_LEGS = {"HIGH": 2, "MEDIUM": 2, "LOW": 1}
ROOT_KEYS = {
    "version", "me", "lens_classes",
    "lenses", "delta_triggers", "risk_default", "move_check",
    "small_delta_lines", "order",
}
# Only `change` reads Gerrit: a rules file used for `scope` alone -- a team
# with no Gerrit, say -- may leave these out. When present they are checked.
CHANGE_KEYS = ("gerrit", "clones", "gateways")
# Optional: without it, review legs carry no effort and behave as before.
# wording_globs (0.6.58): the paths a fix round may change and still be sized
# as wording only; without it, the documentation suffixes below.
OPTIONAL_ROOT_KEYS = {"effort", "wording_globs"}
# An abstract effort ladder. The table names a tier; each adapter's own
# spelling of it comes from data/launch.json, never from personal rules.
EFFORT_LADDER = ("low", "medium", "high", "xhigh", "max")
EFFORT_RANK = {name: number for number, name in enumerate(EFFORT_LADDER)}
# cursor lists `-fast` twins of each tier (launch.json); the twin is kept.
EFFORT_SUFFIX = re.compile(r"-(%s)(-fast)?$" % "|".join(EFFORT_LADDER))
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class InputError(Exception):
    pass


class Problems:
    def __init__(self):
        self.errors = []

    def error(self, path, message):
        self.errors.append("triage: %s: %s" % (path, message))


def _is_comment_key(key):
    return str(key).startswith("_comment")


def public_keys(value):
    return [key for key in value if not _is_comment_key(key)]


def public_map(value):
    """Return a map's usable entries, never its reserved comment keys."""
    return {key: item for key, item in value.items()
            if not _is_comment_key(key) and not str(key).startswith("_")}


def _map_items(value, path, problems):
    """Yield public map entries and reject reserved-looking non-comments."""
    for key, item in value.items():
        if _is_comment_key(key):
            continue
        if str(key).startswith("_"):
            problems.error("%s.%s" % (path, key), "unknown key %r" % key)
            continue
        yield key, item


def triage_path():
    explicit = os.environ.get("DEV_LEAD_TRIAGE")
    if explicit:
        return Path(explicit)
    return roster.roster_path().parent / "triage.json"


def records_path():
    """The per-rules triage record directory shared with leg-cmd.sh."""
    return triage_path().parent / "triage-records"


def _record_path(head):
    return records_path() / (head + ".json")


def _recorded_at():
    return datetime.now(timezone.utc).isoformat()


def _write_record(output, head, mode):
    path = _record_path(head)
    output["record"] = str(path)
    document = dict(output)
    document.update({"recorded_at": _recorded_at(), "mode": mode})
    try:
        roster.atomic_write(path, json.dumps(document, sort_keys=True) + "\n")
    except OSError as exc:
        raise InputError("triage: cannot write record %s: %s" % (path, exc))
    return document


def _unmatched_files(config, files):
    """Paths covered by neither a lens rule nor a delta-trigger rule."""
    globs = [rule["glob"] for rule in config["lenses"]]
    globs.extend(rule["glob"] for rule in config["delta_triggers"])
    return sorted(path for path in files if not any(glob_matches(glob, path) for glob in globs))


def glob_regex(pattern):
    """Compile the deliberately path-aware glob language used by triage.json."""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("glob must be a non-empty string")
    basename = "/" not in pattern
    pieces, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                pieces.append(".*")
                index += 2
            else:
                pieces.append("[^/]*")
                index += 1
        elif char == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(char))
            index += 1
    return re.compile("^" + "".join(pieces) + "$"), basename


def glob_matches(pattern, path):
    compiled, basename = glob_regex(pattern)
    return bool(compiled.fullmatch(posixpath.basename(path) if basename else path))


def _unknown_keys(value, allowed, path, problems):
    if not isinstance(value, dict):
        return
    for key in public_keys(value):
        if key not in allowed:
            problems.error("%s.%s" % (path, key), "unknown key %r" % key)


def _check_glob(value, path, problems):
    if not isinstance(value, str):
        problems.error(path, "must be a path glob string")
        return
    try:
        glob_regex(value)
    except ValueError as exc:
        problems.error(path, str(exc))


def _check_regex(value, path, problems):
    if not isinstance(value, str):
        problems.error(path, "must be a regex string")
        return
    try:
        re.compile(value)
    except re.error as exc:
        problems.error(path, "invalid regex: %s" % exc)


def _roster_lens_classes(problems):
    path = roster.roster_path()
    doc, error = roster.load_roster(path)
    if error:
        problems.error("roster", error)
        return set()
    roster_problems = roster.validate(doc)
    for error in roster_problems.errors:
        problems.error("roster", error)
    try:
        by_lens = doc["rounds"]["r1"]["review"]["opencode"]["by_lens"]
    except (KeyError, TypeError):
        problems.error("lens_classes", "roster r1 opencode review has no by_lens map")
        return set()
    if not isinstance(by_lens, dict):
        problems.error("lens_classes", "roster r1 opencode review by_lens must be an object")
        return set()
    return set(public_map(by_lens))


def _check_effort_table(table, classes, problems):
    """Every risk x lens-class cell, each an abstract tier on EFFORT_LADDER."""
    if not isinstance(table, dict):
        problems.error("effort", "must be an object of risk -> {class: tier}")
        return
    _unknown_keys(table, set(RISKS), "effort", problems)
    for risk in RISKS:
        row = table.get(risk)
        if not isinstance(row, dict):
            problems.error("effort.%s" % risk, "required: an object of class -> tier")
            continue
        _unknown_keys(row, classes, "effort.%s" % risk, problems)
        adapters = {name for name in roster.data()[0] if not str(name).startswith("_")}
        for lens_class in sorted(classes):
            path = "effort.%s.%s" % (risk, lens_class)
            cell = row.get(lens_class)
            if isinstance(cell, dict):
                # {"default": tier, "<adapter>": tier}: a per-adapter override.
                _unknown_keys(cell, adapters | {"default"}, path, problems)
                if "default" not in cell:
                    problems.error(path + ".default", "required: one of %s" % ", ".join(EFFORT_LADDER))
                for key, value in cell.items():
                    if not str(key).startswith("_") and value not in EFFORT_LADDER:
                        problems.error("%s.%s" % (path, key), "must be one of %s" % ", ".join(EFFORT_LADDER))
                    elif key != "default" and not str(key).startswith("_") \
                            and isinstance(cell.get("default"), str) and cell["default"] in EFFORT_RANK \
                            and EFFORT_RANK[value] < EFFORT_RANK[cell["default"]]:
                        # An override raises one adapter; the table is a floor.
                        problems.error("%s.%s" % (path, key), "must not be below the cell's default %r"
                                       % cell["default"])
            elif cell not in EFFORT_LADDER:
                problems.error(path, "required: a tier (%s) or {\"default\": tier, <adapter>: tier}"
                               % ", ".join(EFFORT_LADDER))


def validate(doc):
    problems = Problems()
    if not isinstance(doc, dict):
        problems.error("(root)", "triage must be a JSON object")
        return problems
    _unknown_keys(doc, ROOT_KEYS | set(CHANGE_KEYS) | OPTIONAL_ROOT_KEYS, "(root)", problems)
    for key in ROOT_KEYS:
        if key not in doc:
            problems.error(key, "required")
    if type(doc.get("version")) is not int or doc.get("version") != 1:
        problems.error("version", "must be 1")
    me = doc.get("me")
    if not isinstance(me, list) or not me or not all(isinstance(item, str) and item for item in me):
        problems.error("me", "must be a non-empty list of identifiers")
    gerrit = doc.get("gerrit")
    if "gerrit" not in doc:
        pass
    elif not isinstance(gerrit, dict):
        problems.error("gerrit", "must be an object")
    else:
        _unknown_keys(gerrit, {"ssh", "port"}, "gerrit", problems)
        if not isinstance(gerrit.get("ssh"), str) or not SSH_DESTINATION.fullmatch(gerrit.get("ssh", "")):
            problems.error("gerrit.ssh", "must be user@host (letters, digits, . _ -; neither part may start with -)")
        if type(gerrit.get("port")) is not int:
            problems.error("gerrit.port", "must be an int")
    for name, values, absolute in (("clones", doc.get("clones"), True), ("gateways", doc.get("gateways"), False)):
        if name not in doc:
            continue
        if not isinstance(values, dict):
            problems.error(name, "must be an object")
            continue
        for project, value in _map_items(values, name, problems):
            if not isinstance(project, str) or not project or not isinstance(value, str) or not value:
                problems.error("%s.%s" % (name, project), "must map non-empty strings")
            elif absolute and not os.path.isabs(os.path.expanduser(value)):
                problems.error("%s.%s" % (name, project), "must be an absolute path")
    lens_classes = doc.get("lens_classes")
    roster_classes = _roster_lens_classes(problems)
    if not isinstance(lens_classes, dict):
        problems.error("lens_classes", "must be an object")
        lens_classes = {}
        public_lens_classes = {}
    else:
        for lens, lens_class in _map_items(lens_classes, "lens_classes", problems):
            if not isinstance(lens, str) or not lens or not isinstance(lens_class, str):
                problems.error("lens_classes.%s" % lens, "lens and class must be non-empty strings")
            elif lens_class not in roster_classes:
                problems.error("lens_classes.%s" % lens, "class %r is not in roster opencode by_lens" % lens_class)
        public_lens_classes = public_map(lens_classes)
    lenses = doc.get("lenses")
    if not isinstance(lenses, list):
        problems.error("lenses", "must be a list")
    else:
        for index, rule in enumerate(lenses):
            path = "lenses[%s]" % index
            if not isinstance(rule, dict):
                problems.error(path, "must be an object")
                continue
            _unknown_keys(rule, {"glob", "lenses", "why"}, path, problems)
            _check_glob(rule.get("glob"), path + ".glob", problems)
            names = rule.get("lenses")
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                problems.error(path + ".lenses", "must be a list of lens names")
            else:
                for name in names:
                    if name not in public_lens_classes:
                        problems.error(path + ".lenses", "unknown lens %r" % name)
            if "why" in rule and not isinstance(rule["why"], str):
                problems.error(path + ".why", "must be a string")
    triggers = doc.get("delta_triggers")
    if not isinstance(triggers, list):
        problems.error("delta_triggers", "must be a list")
    else:
        allowed = {"glob", "added_regex", "path_only", "risk", "lenses", "flag", "why"}
        for index, rule in enumerate(triggers):
            path = "delta_triggers[%s]" % index
            if not isinstance(rule, dict):
                problems.error(path, "must be an object")
                continue
            _unknown_keys(rule, allowed, path, problems)
            _check_glob(rule.get("glob"), path + ".glob", problems)
            if "added_regex" in rule:
                _check_regex(rule["added_regex"], path + ".added_regex", problems)
            if rule.get("path_only") is not True and "added_regex" not in rule:
                problems.error(path, "needs added_regex or path_only: true")
            if "path_only" in rule and type(rule["path_only"]) is not bool:
                problems.error(path + ".path_only", "must be true or false")
            if rule.get("risk") not in RISKS:
                problems.error(path + ".risk", "must be LOW, MEDIUM, or HIGH")
            if "lenses" in rule:
                if not isinstance(rule["lenses"], list) or not all(isinstance(name, str) for name in rule["lenses"]):
                    problems.error(path + ".lenses", "must be a list of lens names")
                else:
                    for name in rule["lenses"]:
                        if name not in public_lens_classes:
                            problems.error(path + ".lenses", "unknown lens %r" % name)
            for key in ("flag", "why"):
                if key in rule and not isinstance(rule[key], str):
                    problems.error(path + "." + key, "must be a string")
    if doc.get("risk_default") not in RISKS:
        problems.error("risk_default", "must be LOW, MEDIUM, or HIGH")
    if "effort" in doc:
        _check_effort_table(doc["effort"], set(roster_classes), problems)
    move = doc.get("move_check")
    if not isinstance(move, dict):
        problems.error("move_check", "must be an object")
    else:
        _unknown_keys(move, {"naming"}, "move_check", problems)
        names = move.get("naming")
        if not isinstance(names, list):
            problems.error("move_check.naming", "must be a list")
        else:
            for index, rule in enumerate(names):
                path = "move_check.naming[%s]" % index
                if not isinstance(rule, dict):
                    problems.error(path, "must be an object")
                    continue
                _unknown_keys(rule, {"glob", "regex"}, path, problems)
                _check_glob(rule.get("glob"), path + ".glob", problems)
                _check_regex(rule.get("regex"), path + ".regex", problems)
    if type(doc.get("small_delta_lines")) is not int or doc.get("small_delta_lines", -1) < 0:
        problems.error("small_delta_lines", "must be an int >= 0")
    if "wording_globs" in doc:
        wording = doc["wording_globs"]
        if not isinstance(wording, list):
            problems.error("wording_globs", "must be a list of globs")
        else:
            for index, glob in enumerate(wording):
                _check_glob(glob, "wording_globs[%s]" % index, problems)
    order = doc.get("order")
    if not isinstance(order, list):
        problems.error("order", "must be a list")
    else:
        for index, rule in enumerate(order):
            path = "order[%s]" % index
            if not isinstance(rule, dict):
                problems.error(path, "must be an object")
                continue
            _unknown_keys(rule, {"owner", "gateway", "rank"}, path, problems)
            for key in ("owner", "gateway"):
                if key in rule and (not isinstance(rule[key], str) or not rule[key]):
                    problems.error(path + "." + key, "must be a non-empty string")
            if type(rule.get("rank")) is not int:
                problems.error(path + ".rank", "must be an int")
    return problems


def load_config(path):
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise InputError("triage: %s: file not found" % path)
    except OSError as exc:
        raise InputError("triage: %s: %s" % (path, exc))
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("triage: %s: invalid JSON: %s" % (path, exc))
    problems = validate(doc)
    if problems.errors:
        raise InputError("\n".join(problems.errors))
    return doc, raw, path


def rules_identity(doc, raw, path, as_of_patch_set=None):
    identity = {"version": doc["version"], "sha256": hashlib.sha256(raw).hexdigest(), "path": str(path)}
    if as_of_patch_set is not None:
        identity["as_of_patch_set"] = int(as_of_patch_set)
    return identity


def _decoded(value):
    return value.decode("utf-8", errors="surrogateescape")


# The two calls that leave the machine. Neither had a bound until 0.6.48: a
# Gerrit host that accepts the TCP connection and never answers, or a fetch
# stalled mid-pack, hung the patrol for good (lint's ls-remote has had 10 s
# all along). The values are generous because a fetch of a large change is
# legitimately slow; TRIAGE_TIMEOUT_SECS overrides both, for a slow network
# and for the test that exercises the bound.
SSH_TIMEOUT_SECS = 120
FETCH_TIMEOUT_SECS = 600


def _timeout(default):
    override = os.environ.get("TRIAGE_TIMEOUT_SECS")
    if not override:
        return default
    try:
        return float(override)
    except ValueError:
        raise InputError("TRIAGE_TIMEOUT_SECS: must be a number of seconds, got %r" % override)


def _git(repo, *args, timeout=None):
    try:
        result = run_git(repo, *args, timeout=timeout)
    except OSError as exc:
        raise InputError("git: %s" % exc)
    except subprocess.TimeoutExpired:
        raise InputError("git: %s timed out after %gs" % (args[0] if args else "command", timeout))
    if result.returncode:
        message = _decoded(result.stderr).strip() or _decoded(result.stdout).strip() or "git failed"
        raise InputError("git: %s" % message)
    return _decoded(result.stdout)


def _object_exists(repo, revision):
    try:
        result = run_git(repo, "cat-file", "-e", revision + "^{commit}")
    except OSError as exc:
        raise InputError("git: %s" % exc)
    return result.returncode == 0


def _ensure_revision(repo, change, patch_set, revision):
    if _object_exists(repo, revision):
        return
    ref = "refs/changes/%02d/%s/%s" % (int(change) % 100, change, patch_set)
    try:
        # --no-write-fetch-head (git 2.29+): the clone may be a person's own
        # working clone, and FETCH_HEAD is theirs -- a patrol that runs
        # `git fetch ... && use FETCH_HEAD` must never see it overwritten.
        # --refmap= stops a configured fetch refspec from storing the ref (a
        # clone that maps refs/changes/* would otherwise gain one), and
        # --no-tags / --no-recurse-submodules keep tags and submodules out.
        # The only thing a triage run adds to a clone is objects.
        _git(repo, "fetch", "--no-write-fetch-head", "--refmap=", "--no-tags", "--no-recurse-submodules",
             "origin", ref, timeout=_timeout(FETCH_TIMEOUT_SECS))
    except InputError as exc:
        raise InputError("git: could not fetch revision %s from %s: %s" % (revision, ref, exc))
    if not _object_exists(repo, revision):
        raise InputError("git: revision %s is unavailable after fetching %s" % (revision, ref))


def _identity_matches(person, identifiers):
    if not isinstance(person, dict):
        return False
    return any(person.get(key) in identifiers for key in ("username", "email", "name"))


def _numeric_time(value):
    """The value as a finite epoch time, or None when Gerrit gave us junk."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except OverflowError:
            return None
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def _time(value):
    """Order Gerrit's mixed JSON timestamps, junk first.

    Junk sorts BEFORE every real time on purpose: an unparsable createdOn must
    never win `max` and become "my last upload", which would hide every later
    negative vote.
    """
    numeric = _numeric_time(value)
    if numeric is not None:
        return (0, numeric)
    return (-1, repr(value))


def _after(left, right):
    return _time(left) > _time(right)


def _number(value):
    return str(value.get("number") if isinstance(value, dict) else value)


def _patch_sets(change, as_of=None):
    entries = change.get("patchSets") or []
    if not isinstance(entries, list):
        raise InputError("change: patchSets must be a list")
    current = change.get("currentPatchSet")
    current_number = _number(current) if current is not None else None
    if isinstance(current, dict) and current_number:
        entries = list(entries) + [current]
    unique = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("number") is not None:
            number = _number(entry)
            if number not in unique:
                unique[number] = dict(entry)
            else:
                # Gerrit repeats currentPatchSet with overlapping data. Keep
                # the patchSets entry authoritative and fill only its gaps.
                for key, value in entry.items():
                    if key not in unique[number]:
                        unique[number][key] = value
    # A patch set whose number is not digits is tolerated in the list (it is
    # simply not in the span; see _later_patch_set), but it can never be THE
    # current one, and until 0.6.48 the int() calls below let such a number
    # out as a ValueError traceback instead of this message.
    numeric = [key for key in unique if key.isdigit()]
    if current_number is None:
        current_number = max(numeric, key=int) if numeric else None
    if as_of is not None:
        try:
            current_number = str(int(as_of))
        except (TypeError, ValueError):
            raise InputError("change: --as-of-ps must be a patch set number")
        if current_number not in unique:
            raise InputError("change: patch set %s is missing from patchSets" % current_number)
        unique = {key: value for key, value in unique.items()
                  if key.isdigit() and int(key) <= int(current_number)}
    if not current_number or current_number not in unique:
        raise InputError("change: current patch set is missing from patchSets")
    if not current_number.isdigit():
        raise InputError("change: current patch set number must be digits, got %r" % current_number)
    change["patchSets"] = list(unique.values())
    change["currentPatchSet"] = unique[current_number]
    return unique, unique[current_number]


def _parent(repo, patch_set):
    parents = patch_set.get("parents") or []
    if parents:
        parent = parents[0]
        if isinstance(parent, dict):
            parent = parent.get("id") or parent.get("revision") or parent.get("commit")
        if isinstance(parent, str) and parent:
            return parent
    revision = patch_set.get("revision")
    if not revision:
        raise InputError("change: patch set has no revision")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", revision).strip().split()
    return parents[1] if len(parents) > 1 else EMPTY_TREE


def _status_kind(entry):
    status = entry.get("status", "") if isinstance(entry, dict) else ""
    return status[:1]


def _paths_for(entry):
    paths = [entry["old"]]
    if entry["new"] != entry["old"]:
        paths.append(entry["new"])
    return paths


def _hunk_lines(patch):
    """Return only content lines, preserving CR bytes before each newline."""
    lines, in_hunk = [], False
    for raw_line in patch.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if not in_hunk:
            if line.startswith("@@"):
                in_hunk = True
            continue
        if line.startswith("\\ No newline at end of file"):
            continue
        lines.append(line)
    return lines


def _patch_lines(patch):
    useful = _hunk_lines(patch)
    added = [line[1:] for line in useful if line.startswith("+")]
    removed = [line[1:] for line in useful if line.startswith("-")]
    return useful, added, removed


def _has_binary_header(patch):
    """Recognise Git's binary marker only where Git can emit metadata."""
    in_hunk = False
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk and re.fullmatch(r"Binary files .* differ", line):
            return True
    return False


REGULAR_MODES = ("100644", "100755")


def _raw_diff(repo, before, after, paths=()):
    """`git diff --raw -z` entries: status, paths, modes and blob ids.

    The raw form carries what name-status drops: a mode change (chmod, a
    symlink, a submodule) and the blob ids a binary change is only visible
    through. R and C both carry two paths.
    """
    args = ["diff", "--no-ext-diff", "--no-textconv", "--find-renames", "--raw", "-z", "--no-abbrev",
            before, after]
    if paths:
        args = ["--literal-pathspecs"] + args + ["--"] + list(paths)
    fields = _git(repo, *args).split("\0")
    entries, index = [], 0
    while index < len(fields) - 1:
        meta = fields[index]
        index += 1
        if not meta.startswith(":"):
            continue
        old_mode, new_mode, old_blob, new_blob, status = meta[1:].split(" ", 4)
        if status[0] in "RC":
            old, new = fields[index], fields[index + 1]
            index += 2
        else:
            old = new = fields[index]
            index += 1
        entries.append({"status": status, "old": old, "new": new, "old_mode": old_mode, "new_mode": new_mode,
                        "old_blob": old_blob, "new_blob": new_blob})
    return entries


def _diff_entries(repo, parent, revision):
    entries = []
    for entry in _raw_diff(repo, parent, revision):
        old, new = entry["old"], entry["new"]
        if new in ("/COMMIT_MSG", "/MERGE_LIST", "COMMIT_MSG", "MERGE_LIST"):
            continue
        patch = _git(repo, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                     "--unified=0", "--format=", parent, revision, "--", *_paths_for({"old": old, "new": new}))
        useful, added, removed = _patch_lines(patch)
        binary = _has_binary_header(patch)
        # The patch's own lines identify a text change; a mode change and a
        # binary change have none, so the signature names them explicitly.
        signature = ["mode %s %s" % (entry["old_mode"], entry["new_mode"])] + useful
        if binary:
            signature.append("blob %s %s" % (entry["old_blob"], entry["new_blob"]))
        entry.update({"signature": "\n".join(signature), "added": added, "removed": removed, "binary": binary})
        entries.append(entry)
    return entries


def _mode_flags(pairs):
    """A mode or file-type change has no hunk lines, so a small delta read by
    eye would show nothing. Renames are left to the move checks; a deletion
    has no mode left to judge."""
    flags = []
    for before, entry in pairs:
        if entry["status"][0] in "RD":
            continue
        old_mode = before["new_mode"] if before is not None else entry.get("old_mode")
        new_mode = entry.get("new_mode")
        if old_mode not in (None, "000000") and old_mode != new_mode:
            flags.append("%s: file mode changed %s -> %s" % (entry["new"], old_mode, new_mode))
        elif new_mode not in REGULAR_MODES:
            flags.append("%s: not a regular file (mode %s)" % (entry["new"], new_mode))
    return flags


def _delta(previous, current):
    """Current PS entries that changed from the last-voted PS's own patch."""
    previous_by_path = {entry["new"]: entry for entry in previous}
    chosen, pairs, matched_previous = [], [], set()
    for entry in current:
        before = previous_by_path.get(entry["new"]) or previous_by_path.get(entry["old"])
        if before is not None:
            matched_previous.add(before["new"])
        if before is None or before["signature"] != entry["signature"] or _status_kind(before) != _status_kind(entry):
            chosen.append(entry)
            pairs.append((before, entry))
    # A patch can drop a previous patch set's direct change entirely.  There is
    # no current per-PS entry in that case, but it is still a delta the reviewer
    # must see (usually a revert), represented as a deletion at the old path.
    for entry in previous:
        if entry["new"] not in matched_previous and entry["new"] not in {item["new"] for item in current}:
            removed = dict(entry)
            removed["status"] = "D"
            chosen.append(removed)
            pairs.append((entry, removed))
    return chosen, pairs


def _add_line_data(data, path, added, removed):
    current_added, current_removed = data.setdefault(path, ([], []))
    current_added.extend(added)
    current_removed.extend(removed)


def _delta_lines(pairs):
    data = {}
    for before, after in pairs:
        old_added = Counter(before["added"] if before else [])
        old_removed = Counter(before["removed"] if before else [])
        new_added, new_removed = Counter(after["added"]), Counter(after["removed"])
        added, removed = [], []
        for line, count in (new_added - old_added).items():
            added.extend([line] * count)
        for line, count in (new_removed - old_removed).items():
            removed.extend([line] * count)
        for line, count in (old_added - new_added).items():
            removed.extend([line] * count)
        for line, count in (old_removed - new_removed).items():
            added.extend([line] * count)
        entry = after or before
        if entry.get("old") and entry["old"] != entry["new"]:
            _add_line_data(data, entry["old"], [], removed)
            _add_line_data(data, entry["new"], added, [])
        else:
            _add_line_data(data, entry["new"], added, removed)
    return data


def _between_lines(repo, before_revision, after_revision, entries, moves=()):
    """Actual hunk lines between two patch sets, limited to their delta files.

    Comparing each patch-set's *patch* identifies the files that matter, but a
    line first added by the earlier patch set becomes context in the later one.
    It is not thereby removed from the review delta.  Diffing the two resulting
    trees gives the hunk lines that actually changed between those reviews.
    A file moved between the two trees is diffed as its rename pair: removed
    lines belong to the old path, added lines to the new one.
    """
    moved = {}
    for pair in moves:
        moved[pair["old"]] = moved[pair["new"]] = pair
    data, done = {}, set()
    for entry in entries:
        pair = next((moved[path] for path in _paths_for(entry) if path in moved), None)
        if pair is not None:
            if id(pair) in done:
                continue
            done.add(id(pair))
            paths, removed_at, added_at = _paths_for(pair), pair["old"], pair["new"]
        else:
            paths, removed_at, added_at = _paths_for(entry), entry["new"], entry["new"]
        patch = _git(repo, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                     "--unified=0", "--format=", before_revision, after_revision, "--", *paths)
        _useful, added, removed = _patch_lines(patch)
        _add_line_data(data, removed_at, [], removed)
        _add_line_data(data, added_at, added, [])
    return data


def _tree_text(repo, revision, path):
    """The blob at that path, raw (no textconv), or None when there is none:
    a missing path, a directory or a submodule. Decided by git's exit code."""
    try:
        result = run_git(repo, "cat-file", "blob", "%s:%s" % (revision, path))
    except OSError as exc:
        raise InputError("git: %s" % exc)
    if result.returncode:
        return None
    return _decoded(result.stdout)


def _tree_has(repo, revision, path):
    """Whether the revision carries that path, decided by git's exit code.

    `git show` has at least three ways of saying no ("does not exist in",
    "is outside repository", "exists on disk, but not in"); matching its prose
    turned an unresolvable link into an abort.
    """
    if path in ("", "."):
        return True
    try:
        result = run_git(repo, "cat-file", "-e", "%s:%s" % (revision, path))
        if result.returncode == 0:
            return True
        listing = run_git(repo, "--literal-pathspecs", "ls-tree", str(revision), "--", path)
    except OSError as exc:
        raise InputError("git: %s" % exc)
    return listing.returncode == 0 and bool(listing.stdout.strip())


URL = re.compile(r"https?://(?:[^\s<>()\[\]\"']|\([^\s<>()\[\]\"']*\))+")


def _without_inline_code(line, code=None):
    kept, index = [], 0
    while True:
        start = line.find("`", index)
        if start < 0:
            kept.append(line[index:])
            return "".join(kept)
        kept.append(line[index:start])
        end_marker = start
        while end_marker < len(line) and line[end_marker] == "`":
            end_marker += 1
        marker = line[start:end_marker]
        end = line.find(marker, end_marker)
        if end < 0:
            kept.append(line[start:])
            return "".join(kept)
        if code is not None:
            code.append(line[start:end + len(marker)])
        index = end + len(marker)


def _visible_markdown(text, code=None):
    """Drop fenced blocks and inline code before extracting links or URLs.
    Given a list as `code`, append what was dropped as code to it."""
    lines, fence, indented, blank = [], None, False, True
    for raw_line in text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        token = line.lstrip(" \t")
        indent = len(line[:len(line) - len(token)].expandtabs(4))
        if fence is not None:
            character, length = fence
            run = len(token) - len(token.lstrip(character))
            if indent <= 3 and run >= length and token[run:].strip(" \t\r") == "":
                fence = None
            elif code is not None:
                code.append(line)
            continue
        if not token.strip(" \t\r"):
            blank = True
            continue
        # An indented code block opens only after a blank line: the same indent
        # under a list item is a continuation, and a link that does not resolve
        # there must still be flagged.
        if (indent >= 4 or line.startswith("\t")) and (indented or blank):
            indented, blank = True, False
            if code is not None:
                code.append(line)
            continue
        indented, blank = False, False
        start = re.match(r"(`{3,}|~{3,})", token) if indent <= 3 else None
        if start:
            marker = start.group(1)
            fence = (marker[0], len(marker))
            if code is not None:
                code.append(line)
            continue
        lines.append(_without_inline_code(line, code))
    return "\n".join(lines)


def _trimmed_url(value):
    """Drop sentence punctuation, but keep a parenthesis the URL itself opened."""
    while value:
        stripped = value.rstrip(".,;:!?]}>\"'")
        if stripped.endswith(")") and stripped.count("(") < stripped.count(")"):
            stripped = stripped[:-1]
        if stripped == value:
            return value
        value = stripped
    return value


def _urls(text):
    found = set()
    for value in URL.findall(_visible_markdown(text)):
        value = _trimmed_url(value)
        if value:
            found.add(value)
    return found


def _markdown_targets(text):
    """Yield balanced Markdown link destinations from visible Markdown."""
    index = 0
    while True:
        start = text.find("](", index)
        if start < 0:
            return
        cursor, depth, quote = start + 2, 1, None
        while cursor < len(text) and depth:
            character = text[cursor]
            if quote is not None:
                if character == quote and text[cursor - 1] != "\\":
                    quote = None
            elif character in ("\"", "'") and text[cursor - 1].isspace():
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            cursor += 1
        if depth == 0:
            yield text[start + 2:cursor - 1]
            index = cursor
        else:
            index = start + 2


def _relative_targets(text):
    for target in _markdown_targets(_visible_markdown(text)):
        target = target.strip()
        if target.startswith("<"):
            closing = target.find(">")
            target = target[1:closing] if closing >= 0 else target[1:]
        else:
            target = re.split(r"\s+(?=[\"'(])", target, maxsplit=1)[0]
        if not target or target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        target = target.split("#", 1)[0].split("?", 1)[0]
        if target:
            yield target


def _move_checks(config, repo, previous_revision, current_revision, entries):
    flags = []
    naming = config["move_check"]["naming"]
    for entry in entries:
        old_text = _tree_text(repo, previous_revision, entry["old"])
        new_text = _tree_text(repo, current_revision, entry["new"])
        if old_text is None or new_text is None:
            flags.append("%s: cannot read moved content" % entry["new"])
            continue
        if entry.get("old_mode") != entry.get("new_mode"):
            flags.append("%s: file mode changed %s -> %s" % (entry["new"], entry.get("old_mode"), entry.get("new_mode")))
        elif entry.get("new_mode") not in REGULAR_MODES:
            flags.append("%s: moved entry is not a regular file (mode %s)" % (entry["new"], entry.get("new_mode")))
        elif not _moved_unchanged(entry, old_text, new_text):
            flags.append("%s: content differs after relative-link-depth normalisation" % entry["new"])
        old_urls, new_urls = _urls(old_text), _urls(new_text)
        if old_urls != new_urls:
            added, removed = sorted(new_urls - old_urls), sorted(old_urls - new_urls)
            flags.append("%s: absolute URLs changed (added: %s; removed: %s)" %
                         (entry["new"], ", ".join(added) or "none", ", ".join(removed) or "none"))
        for target in _relative_targets(new_text):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(entry["new"]), target))
            if not _tree_has(repo, current_revision, resolved):
                flags.append("%s: relative link %s does not resolve" % (entry["new"], target))
        for rule in naming:
            if glob_matches(rule["glob"], entry["new"]) and not re.fullmatch(rule["regex"], entry["new"]):
                flags.append("%s: naming regex does not match" % entry["new"])
    return flags


def _tree_renames(repo, previous_revision, current_revision, entries):
    """Rename pairs between the two patch-set TREES, and whether they are all of it.

    Gerrit patch sets are amends of one another on the same base, so a file
    moved between them is "added at A" in one patch set's own diff and "added
    at B" in the next: the per-patch-set entries never show a rename. Diffing
    the two trees over the delta's paths lets git pair A with B. The pairs are
    returned even when other files changed too, so a move inside a rework is
    still counted and checked as a move.
    """
    paths = []
    for entry in entries:
        for path in _paths_for(entry):
            if path not in paths:
                paths.append(path)
    if not paths:
        return [], False
    pairs, only_renames = [], True
    for entry in _raw_diff(repo, previous_revision, current_revision, paths):
        if entry["status"][0] == "R":
            pairs.append(entry)
        else:
            only_renames = False
    return pairs, only_renames and bool(pairs)


MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
# A fix round that changes only these is wording: the lead's own read, no leg.
# Comments inside code are NOT detected as wording -- a code file's change is
# at least the tested rung -- because telling a comment from code per language
# is a guess, and a wrong guess would wave real code through unreviewed.
# .mdx is left out: it imports and renders components, which is code.
WORDING_SUFFIXES = (".md", ".markdown", ".rst", ".adoc")
# The fix round from which a full round needs a blocker (owner ruling,
# 2026-10-01: after two fix rounds, non-blockers do not open another).
CONVERGENCE_ROUND = 3
LINK_DEPTH = re.compile(r"(\]\(\s*<?)(?:\.\./)+")
REFERENCE_DEPTH = re.compile(r"(?m)^( {0,3}\[[^\]]+\]:[ \t]*<?)(?:\.\./)+")


def _fold_link_depth(path, text):
    """Fold only what a move is expected to change: the ../ depth at the start
    of a Markdown link destination. Anywhere else -- code, a Makefile, YAML,
    Markdown prose -- a changed ../ run is an edit and stays visible."""
    if not path.lower().endswith(MARKDOWN_SUFFIXES):
        return text
    text = LINK_DEPTH.sub(r"\1../", text)
    return REFERENCE_DEPTH.sub(r"\1../", text)


def _markdown_code(text):
    code = []
    _visible_markdown(text, code)
    return code


def _moved_unchanged(entry, old_text, new_text):
    """A moved file whose content is the same up to Markdown link depth, with
    the same regular-file mode: a symlink's target means something else in
    its new directory, and a mode change is a change. Code in Markdown (fenced,
    indented, inline) must match exactly: a link-shaped ../ run there is text
    the move did not have to change."""
    if entry.get("old_mode") != entry.get("new_mode") or entry.get("new_mode") not in REGULAR_MODES:
        return False
    if entry["new"].lower().endswith(MARKDOWN_SUFFIXES) and _markdown_code(old_text) != _markdown_code(new_text):
        return False
    return _fold_link_depth(entry["old"], old_text) == _fold_link_depth(entry["new"], new_text)


def _is_move_only(repo, previous_revision, current_revision, entries):
    if not entries or not all(entry["status"].startswith("R") for entry in entries):
        return False
    for entry in entries:
        old_text = _tree_text(repo, previous_revision, entry["old"])
        new_text = _tree_text(repo, current_revision, entry["new"])
        if old_text is None or new_text is None or not _moved_unchanged(entry, old_text, new_text):
            return False
    return True


def _account_values(person):
    """The identifiers that make two Gerrit records the same account.

    Gerrit may send a username on one vote and only an email on another, and
    two records for one reviewer must not read as two open opinions.
    """
    person = person or {}
    values = {str(person[field]).strip().lower()
              for field in ("username", "email") if person.get(field)}
    if not values and person.get("name"):
        values = {str(person["name"]).strip().lower()}
    return values or {repr(sorted((str(key), str(item)) for key, item in person.items()))}


def _supersedes(approval, patch_set, held, held_patch_set):
    """Whether a vote replaces the one held, ties going to the later patch set."""
    when, other = approval.get("grantedOn", 0), held.get("grantedOn", 0)
    if _after(when, other):
        return True
    if _after(other, when):
        return False
    return _later_patch_set(_number(patch_set), _number(held_patch_set))


def _hold_latest_vote(accounts, approval, patch_set):
    """Keep one vote per account: the latest, merging records as they match."""
    values = _account_values(approval.get("by"))
    matches = [held for held in accounts if held["values"] & values]
    if not matches:
        accounts.append({"values": set(values), "approval": approval, "patch_set": patch_set})
        return
    head = matches[0]
    for other in matches[1:]:
        head["values"] |= other["values"]
        if _supersedes(other["approval"], other["patch_set"], head["approval"], head["patch_set"]):
            head["approval"], head["patch_set"] = other["approval"], other["patch_set"]
        accounts.remove(other)
    head["values"] |= values
    if _supersedes(approval, patch_set, head["approval"], head["patch_set"]):
        head["approval"], head["patch_set"] = approval, patch_set


def _fired(fired, rule, detail):
    fired.append({"rule": rule, "detail": detail})


def _lenses_and_triggers(config, files, delta_lines, fired, *, path_only=False):
    line_data = delta_lines if isinstance(delta_lines, dict) else {}
    matches = {}
    for index, rule in enumerate(config["lenses"]):
        for path in files:
            if glob_matches(rule["glob"], path):
                for name in rule["lenses"]:
                    matches.setdefault(name, set()).add(path)
                _fired(fired, "lenses[%s]" % index, "%s matched %s" % (path, rule["glob"]))
    raised = config["risk_default"]
    flags = []
    for index, rule in enumerate(config["delta_triggers"]):
        file_paths = [path for path in files if glob_matches(rule["glob"], path)]
        line_paths = [path for path in line_data if glob_matches(rule["glob"], path)]
        paths = list(dict.fromkeys(file_paths + line_paths))
        if not paths:
            continue
        fires = rule.get("path_only") is True and bool(file_paths)
        if not path_only and "added_regex" in rule:
            compiled = re.compile(rule["added_regex"])
            fires = fires or any(compiled.search(line)
                                for matched_path in line_paths
                                for line in sum(line_data.get(matched_path, ([], [])), []))
        if not fires:
            continue
        raised = max(raised, rule["risk"], key=lambda item: RISK_VALUE[item])
        for name in rule.get("lenses", []):
            matches.setdefault(name, set()).update(paths)
        if rule.get("flag"):
            flags.append(rule["flag"])
        _fired(fired, "delta_triggers[%s]" % index,
               "%s fired for %s" % (rule.get("why") or rule["glob"], ", ".join(paths)))
    classes = public_map(config["lens_classes"])
    lens_list = [{"name": name, "class": classes[name], "files": sorted(paths)}
                 for name, paths in sorted(matches.items())]
    return lens_list, raised, flags


def _review_legs(lens_classes, config=None, risk_floor=None):
    path = roster.roster_path()
    doc, error = roster.load_roster(path)
    if error:
        raise InputError(error)
    errors = roster.validate(doc).errors
    if errors:
        raise InputError("\n".join(errors))
    review = doc["rounds"]["r1"].get("review") or {}
    wanted = "judgment" if "judgment" in lens_classes else "mechanical"
    _, families = roster.data()
    resolved, skipped = [], []
    for adapter, leg in review.items():
        if str(adapter).startswith("_") or leg is None:
            continue
        if isinstance(leg, dict) and leg.get("gated_on"):
            # A gate on the whole leg (plain or by_lens) has no stand-in: the
            # leg is left out, and named.
            skipped.append("%s (gated: %s)" % (adapter, leg["gated_on"]))
            continue
        chosen, gate = leg, None
        if adapter == "opencode" and isinstance(leg, dict) and "by_lens" in leg:
            chosen, used, gate = roster.resolve_by_lens(leg["by_lens"], wanted)
            if chosen is None and gate:
                raise InputError("roster: opencode by_lens.%s is gated (%s) and no ungated mechanical entry "
                                 "can stand in" % (wanted, gate))
        if not isinstance(chosen, dict) or not chosen.get("model"):
            continue
        family = infer_family(families.get("adapters", {}).get(adapter, {}).get("serves", []), chosen)
        entry = {"adapter": adapter, "model": chosen["model"], "family": family,
                 "_declared_effort": chosen.get("effort")}
        if gate:
            # The roster says this lens's entry is not dispatchable yet; the
            # mechanical entry stands in, and the output says so.
            entry["stands_in_for"] = {"lens": wanted, "gated_on": gate}
            wanted = "%s (%s gated)" % (used, wanted)
        resolved.append(entry)
    for entry in resolved:
        if "effort" in (config or {}):
            floor = _effort_floor(config, risk_floor, lens_classes, entry["adapter"])
            entry.update(_leg_effort(entry, floor))
            # A floor set by a cell's override for this adapter says so, so the
            # output shows why one adapter got more than the default.
            if entry.get("effort_source") == "table" and \
                    _effort_overridden(config, risk_floor, lens_classes, entry["adapter"], floor):
                entry["effort_source"] = "table-override"
        entry.pop("_declared_effort", None)
    if skipped:
        wanted = "%s; left out: %s" % (wanted, ", ".join(skipped))
    return resolved, wanted


def _effort_cell(cell, adapter):
    """A cell is a tier, or {"default": tier, <adapter>: tier}."""
    if isinstance(cell, dict):
        return cell.get(adapter, cell.get("default"))
    return cell


def _effort_overridden(config, risk_floor, lens_classes, adapter, floor):
    """True when an adapter-named override raised this leg's floor: the floor
    from the defaults alone, across the same classes, is lower."""
    if floor not in EFFORT_RANK:
        return False
    defaults_only = _effort_floor(config, risk_floor, lens_classes, None)
    return EFFORT_RANK.get(defaults_only, -1) < EFFORT_RANK[floor]


def _effort_floor(config, risk_floor, lens_classes, adapter=None):
    """The table's tier for this risk floor, the highest across the lens
    classes present; None when there is no table or no concrete risk. With
    no lens at all the mechanical cell applies: that is the class the roster
    legs are chosen by when no judgment lens fired, and returning None would
    give a HIGH change with no matching lens no floor at all."""
    table = config.get("effort")
    if not isinstance(table, dict) or risk_floor not in RISKS:
        return None
    row = table.get(risk_floor) or {}
    cells = {c: _effort_cell(row.get(c), adapter) for c in set(lens_classes) | {"mechanical"}}
    tiers = [cells[c] for c in lens_classes if cells[c] in EFFORT_RANK]
    if not tiers and cells["mechanical"] in EFFORT_RANK:
        tiers = [cells["mechanical"]]
    return max(tiers, key=EFFORT_RANK.get) if tiers else None


def _tier_or_next(wanted, allowed):
    """`wanted`, or the next higher tier that `allowed` contains; None if none."""
    for tier in EFFORT_LADDER[EFFORT_RANK[wanted]:]:
        if tier in allowed:
            return tier
    return None


def _leg_effort(leg, floor):
    """Effort fields for one review leg. Raise-only: the table's floor never
    lowers what the roster (or, for config_only, the machine) would run. A
    word or model variant is emitted only when it is KNOWN to exist -- the
    roster's own, or one of launch.json's effort.examples -- else the leg
    keeps its launch and carries effort_unmet for the lead to decide."""
    launch, _ = roster.data()
    effort = (launch.get(leg["adapter"]) or {}).get("effort") or {}
    # Review legs only: the mechanism that governs the adapter's REVIEW role.
    mechanism = roster.role_mechanism(leg["adapter"], "review") if effort else "none"
    examples = [str(item) for item in effort.get("examples") or []]
    if mechanism == "none":
        return {"effort": None, "effort_in": "none"}
    if mechanism == "model_suffix":
        match = EFFORT_SUFFIX.search(leg["model"])
        if not match:
            return {"effort": None, "effort_in": "model_name",
                    "effort_unmet": {"wanted": floor, "reason": "model id carries no effort suffix"}} if floor else \
                   {"effort": None, "effort_in": "model_name"}
        base, declared, twin = leg["model"][:match.start()], match.group(1), match.group(2) or ""
        if floor is None or EFFORT_RANK[floor] <= EFFORT_RANK[declared]:
            return {"effort": declared, "effort_in": "model_name", "effort_source": "roster"}
        known = {declared} | {m[len(base) + 1:len(m) - len(twin)] for m in examples
                              if m.startswith(base + "-") and m.endswith(twin)
                              and m[len(base) + 1:len(m) - len(twin)] in EFFORT_RANK}
        tier = _tier_or_next(floor, known)
        if tier is None:
            return {"effort": declared, "effort_in": "model_name", "effort_source": "roster",
                    "effort_unmet": {"wanted": floor, "reason": "variant not known for this adapter"}}
        leg["model"] = "%s-%s%s" % (base, tier, twin)
        return {"effort": tier, "effort_in": "model_name", "effort_source": "table"}
    if mechanism == "config_only":
        running = roster._config_effort(effort)
        if isinstance(running, str) and running not in EFFORT_RANK and floor is not None:
            # A setting off the ladder (e.g. a newer "ultra") cannot be ranked;
            # raise-only means keeping it, not replacing it with the table's tier.
            return {"effort": running, "effort_in": "config_only", "effort_source": "roster",
                    "effort_unmet": {"wanted": floor, "reason": "machine effort %r is not on the ladder; kept" % running}}
        declared = running if running in EFFORT_RANK else None
        if floor is None or (declared and EFFORT_RANK[floor] <= EFFORT_RANK[declared]):
            return {"effort": running, "effort_in": "config_only", "effort_source": "roster"}
        return {"effort": floor, "effort_in": "config_only", "effort_source": "table",
                "config_mismatch": {"config": running, "wanted": floor,
                                    "launch": "codex exec -c model_reasoning_effort=%s; state the base "
                                              "revision in the brief (exec does not read it)" % floor}}
    declared = leg.pop("_declared_effort", None)
    if isinstance(declared, str) and declared not in EFFORT_RANK and floor is not None:
        # A provider-specific word off the ladder cannot be ranked; raise-only
        # means keeping it, not replacing it with the table's tier.
        return {"effort": declared, "effort_in": "flag", "effort_source": "roster",
                "effort_unmet": {"wanted": floor, "reason": "roster effort %r is not on the ladder; kept" % declared}}
    if not isinstance(declared, str):
        declared = None
    if floor is None or (declared in EFFORT_RANK and EFFORT_RANK[floor] <= EFFORT_RANK[declared]):
        return {"effort": declared, "effort_in": "flag", "effort_source": "roster"}
    tier = _tier_or_next(floor, set(examples) | ({declared} if declared else set()))
    if tier is None:
        return {"effort": declared, "effort_in": "flag", "effort_source": "roster",
                "effort_unmet": {"wanted": floor, "reason": "effort word not known for this adapter"}}
    return {"effort": tier, "effort_in": "flag", "effort_source": "table"}


def _rows_from(text):
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError("query JSON: invalid JSON: %s" % exc)
        if not isinstance(row, dict):
            # Valid JSON that is not an object (a list, a string) reached
            # `row.get` and left as an AttributeError traceback (2026-09-25).
            raise InputError("query JSON: each line must be a JSON object, got %s" % type(row).__name__)
        if row.get("type") != "stats":
            rows.append(row)
    return rows


def _gerrit_rows(config, *query):
    destination = config["gerrit"]["ssh"]
    # Checked again here, not only in `check`: a destination that starts with
    # "-" is an ssh OPTION (-oProxyCommand=... runs a program), and `--` makes
    # ssh read whatever follows as the destination even if a check is skipped.
    if not isinstance(destination, str) or not SSH_DESTINATION.fullmatch(destination):
        raise InputError("gerrit.ssh: must be user@host")
    command = ["ssh", "-p", str(int(config["gerrit"]["port"])), "--", destination,
               "gerrit", "query", "--format=JSON", *query]
    timeout = _timeout(SSH_TIMEOUT_SECS)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except OSError as exc:
        raise InputError("gerrit query: %s" % exc)
    except subprocess.TimeoutExpired:
        raise InputError("gerrit query: no answer from %s after %gs" % (destination, timeout))
    if result.returncode:
        raise InputError("gerrit query: %s" % (result.stderr.strip() or result.stdout.strip()))
    return _rows_from(result.stdout)


SSH_DESTINATION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*")
QUERY_TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def _change_number(value, what="change"):
    """A Gerrit change number: digits only, so it can be neither a query
    operator nor a crash in the refs/changes arithmetic."""
    text = str(value)
    # isdigit() alone admits Unicode digits: "²" passes it and then fails int().
    if not (text.isascii() and text.isdigit()):
        raise InputError("%s: number must be digits, got %r" % (what, text))
    return text


def _query(config, number, query_json):
    if query_json:
        try:
            text = Path(query_json).read_text(encoding="utf-8")
        except OSError as exc:
            raise InputError("query JSON: %s" % exc)
        rows = _rows_from(text)
    else:
        rows = _gerrit_rows(config, "--current-patch-set", "--patch-sets", "--files",
                            "--all-approvals", "--comments", "--dependencies", "--submit-records",
                            "change:%s" % number)
    wanted = str(number)
    for row in rows:
        if str(row.get("number")) == wanted:
            change = row
            break
    else:
        raise InputError("query JSON: change %s not found" % number)
    if not query_json and change.get("topic"):
        # The topic is the author's free text, and the remote side splits the
        # command line itself; only a plain topic goes into a query.
        if QUERY_TOPIC.fullmatch(str(change["topic"])):
            rows += _gerrit_rows(config, "--current-patch-set", "topic:%s" % change["topic"])
    return change, rows


def _approval_value(approval):
    try:
        return int(str(approval.get("value")).strip())
    except (AttributeError, TypeError, ValueError):
        return None


def _last_vote(change, identifiers):
    found = []
    for patch_set in change.get("patchSets") or []:
        for approval in patch_set.get("approvals") or []:
            if approval.get("type") == "Code-Review" and _identity_matches(approval.get("by"), identifiers):
                found.append((approval.get("grantedOn", 0), patch_set, approval))
    if not found:
        return None, None, None
    # Equal times keep the EARLIEST patch set, on purpose: a vote Gerrit copies
    # forward keeps its original grantedOn, and the patch set it was cast on is
    # the content I reviewed -- a sticky -2 copied across a REWORK must not read
    # as a vote on the current patch set.
    _when, patch_set, approval = max(found, key=lambda item: _time(item[0]))
    if _approval_value(approval) in (None, 0):
        return None, None, None
    voted_on = _number(patch_set)   # digits, or a non-numeric set the span ignores
    return patch_set, approval, {"ps": int(voted_on) if voted_on.isdigit() else voted_on,
                                 "value": approval.get("value")}


def _at_or_after(left, right):
    return _time(left) >= _time(right)


def _later_patch_set(number, replayed):
    """Whether `number` is a patch set uploaded after the replayed one."""
    try:
        return int(number) > int(replayed)
    except (TypeError, ValueError):
        return False


def _as_of_cutoffs(change, as_of):
    """Return distinct replay cutoffs for everyone and for my own actions.

    A replay ends for all accounts when the next patch set after N is uploaded
    (the next one that EXISTS: Gerrit numbering may have gaps). My approval on
    N and my messages from N's upload onward are additionally excluded so the
    replay reflects the decision before I reviewed N, without discarding other
    reviewers' intervening feedback.
    """
    if as_of is None:
        return None
    try:
        number = str(int(as_of))
    except (TypeError, ValueError):
        raise InputError("change: --as-of-ps must be a patch set number")
    entries = list(change.get("patchSets") or [])
    if isinstance(change.get("currentPatchSet"), dict):
        entries.append(change["currentPatchSet"])
    created = None
    for entry in entries:
        if isinstance(entry, dict) and _number(entry) == number:
            created = entry.get("createdOn", created)
    replayed_at = _numeric_time(created)
    next_created = []
    for entry in entries:
        if not isinstance(entry, dict) or "createdOn" not in entry:
            continue
        uploaded = _numeric_time(entry["createdOn"])
        # A cutoff must come AFTER the replayed patch set: a later-numbered one
        # stamped earlier would otherwise erase feedback given on N.
        if (_later_patch_set(_number(entry), number) and uploaded is not None
                and (replayed_at is None or uploaded > replayed_at)):
            next_created.append(entry["createdOn"])
    return {"everyone": min(next_created, key=_time) if next_created else None,
            "mine": created, "patch_set": number}


def _limit_to_as_of(change, patch_sets, cutoffs, identifiers):
    if cutoffs is None:
        return
    everyone, mine = cutoffs["everyone"], cutoffs["mine"]
    everyone = everyone if _numeric_time(everyone) is not None else None
    mine = mine if _numeric_time(mine) is not None else None
    replayed_patch_set = cutoffs["patch_set"]
    for patch_set in patch_sets.values():
        patch_set["approvals"] = [
            approval for approval in patch_set.get("approvals") or []
            if not (everyone is not None and _at_or_after(approval.get("grantedOn", 0), everyone))
            and not (_number(patch_set) == replayed_patch_set
                     and _identity_matches(approval.get("by"), identifiers))
        ]
    change["comments"] = [
        comment for comment in change.get("comments") or []
        if not (everyone is not None and _at_or_after(comment.get("timestamp", 0), everyone))
        and not (_identity_matches(comment.get("reviewer"), identifiers) and mine is not None
                 and _at_or_after(comment.get("timestamp", 0), mine))
    ]


def _order(config, owner, gateway, fired):
    for index, rule in enumerate(config["order"]):
        if ("owner" in rule and rule["owner"] not in (owner.get("username"), owner.get("email"))) or (
                "gateway" in rule and rule["gateway"] != gateway):
            continue
        _fired(fired, "order[%s]" % index, "matched rank %s" % rule["rank"])
        return rule["rank"]
    _fired(fired, "order", "no order rule matched; rank 99")
    return 99


def _ancestor_lists(config, change, rows, query_json):
    by_number = {str(row.get("number")): row for row in rows if isinstance(row, dict) and row.get("number") is not None}
    unmerged, unknown = [], []
    for dependency in change.get("dependsOn") or []:
        if not isinstance(dependency, dict) or dependency.get("number") is None:
            continue
        number = dependency["number"]
        status = dependency.get("status")
        if status is None:
            row = by_number.get(str(number))
            if row is None and not query_json and str(number).isascii() and str(number).isdigit():
                found = _gerrit_rows(config, "change:%s" % number)
                row = next((item for item in found if str(item.get("number")) == str(number)), None)
            status = row.get("status") if isinstance(row, dict) else None
        if status is None:
            unknown.append(number)
        elif status != "MERGED":
            unmerged.append(number)
    return unmerged, unknown


def _topic_members(change, all_changes):
    topic, members = change.get("topic"), []
    for row in all_changes:
        member = row.get("number")
        if topic and row.get("topic") == topic and member is not None and member not in members:
            members.append(member)
    return topic, members


def _line_count(line_data):
    return sum(len(added) + len(removed) for added, removed in line_data.values())


def triage_change(config, raw, path, number, query_json, include_wip, as_of=None):
    missing = [key for key in CHANGE_KEYS if key not in config]
    if missing:
        raise InputError("triage change: %s has no %s (only scope works without them)"
                         % (path, ", ".join(missing)))
    change, all_changes = _query(config, number, query_json)
    identifiers = set(config["me"])
    cutoffs = _as_of_cutoffs(change, as_of)
    patch_sets, current = _patch_sets(change, as_of)
    _limit_to_as_of(change, patch_sets, cutoffs, identifiers)
    if not current.get("revision"):
        raise InputError("change: current patch set has no revision")
    project = change.get("project")
    gateway = config["gateways"].get(project)
    role = "owner" if _identity_matches(change.get("owner"), identifiers) else "reviewer"
    voted_set, vote, vote_summary = _last_vote(change, identifiers)
    fired = []
    if gateway is None:
        _fired(fired, "gateway", "unknown project %s" % project)
    _fired(fired, "role", "%s is %s" % (project, role))
    wip = bool(change.get("wip"))
    current_vote = vote_summary is not None and str(vote_summary["ps"]) == str(current["number"])
    mentioned_after_vote = any(
        _after(comment.get("timestamp", 0), vote.get("grantedOn", 0))
        and not _identity_matches(comment.get("reviewer"), identifiers)
        and any(identifier in comment.get("message", "") for identifier in identifiers)
        for comment in change.get("comments") or []
    ) if vote else False
    owner_fix = False
    if role == "owner":
        uploads = [patch_set for patch_set in patch_sets.values()
                   if _identity_matches(patch_set.get("uploader"), identifiers)]
        upload = max(uploads, key=lambda patch_set: _time(patch_set.get("createdOn", 0))) if uploads else current
        upload_time = upload.get("createdOn", 0)
        accounts = []
        for patch_set in patch_sets.values():
            for approval in patch_set.get("approvals") or []:
                if approval.get("type") != "Code-Review":
                    continue
                if _identity_matches(approval.get("by"), identifiers):
                    continue
                granted = approval.get("grantedOn", 0)
                # A vote whose timestamp does not parse cannot be shown to
                # predate my upload, so it stays a candidate rather than
                # disappearing into "older than everything".
                if _numeric_time(granted) is not None and not _after(granted, upload_time):
                    continue
                _hold_latest_vote(accounts, approval, patch_set)
        negatives = [held["approval"] for held in accounts
                     if (_approval_value(held["approval"]) or 0) < 0]
        comments = [
            comment for comment in change.get("comments") or []
            if not _identity_matches(comment.get("reviewer"), identifiers)
            and _after(comment.get("timestamp", 0), upload_time)
        ]
        owner_fix = bool(negatives or comments)
        if owner_fix:
            _fired(fired, "owner-fix-round", "owner received a negative vote or comment after the last upload")
    skip = None
    if not owner_fix:
        if wip and not include_wip:
            skip = "WIP change (pass --include-wip to triage it)"
        elif role == "reviewer" and current_vote and not mentioned_after_vote:
            skip = "my Code-Review vote is on the current patch set with no later message naming me"
    if skip:
        _fired(fired, "skip", skip)
    rank = _order(config, change.get("owner") or {}, gateway, fired)
    ancestors, unknown_ancestors = _ancestor_lists(config, change, all_changes, query_json)
    topic, members = _topic_members(change, all_changes)
    identity = rules_identity(config, raw, path, current["number"] if as_of is not None else None)
    common = {
        "rules": identity, "change": change.get("number", number), "project": project, "gateway": gateway,
        "role": role, "my_last_vote": vote_summary, "wip": wip, "skip": skip,
        "unmerged_ancestors": ancestors, "unknown_ancestors": unknown_ancestors,
        "topic_members": {"topic": topic, "changes": members, "atomic": False}, "order": rank,
    }
    if as_of is not None:
        common["as_of_patch_set"] = int(current["number"])
    if skip:
        _fired(fired, "legs", "no legs for skip")
        common.update({"ps_kind": None, "delta_files": None, "lenses": None, "risk_floor": None,
                       "legs": "none", "review_legs": [], "unmatched_files": [], "min_review_legs": 0,
                       "flags": None, "fired_rules": fired, "head": current["revision"], "base": None})
        return common
    clone = config["clones"].get(project)
    if not clone:
        raise InputError("triage: clones has no clone for project %s" % project)
    repo = Path(os.path.expanduser(clone))
    if not repo.is_dir():
        raise InputError("triage: clone for %s does not exist: %s" % (project, repo))
    _ensure_revision(repo, number, current["number"], current["revision"])
    current_parent = _parent(repo, current)
    # The record's base (0.6.56): the commit this patch set's diff is taken
    # against -- the first parent of a merge -- as a full sha, so leg-cmd.sh
    # holds a review launch's --base to it as it does for a scope record. A
    # root patch set has no parent commit, and its base is null.
    base = None
    if current_parent != EMPTY_TREE:
        _ensure_revision(repo, number, current["number"], current_parent)
        base = _git(repo, "rev-parse", "--verify", current_parent + "^{commit}").strip()
    current_entries = _diff_entries(repo, current_parent, current["revision"])
    prior = voted_set if voted_set and str(voted_set["number"]) != str(current["number"]) else None
    if prior:
        if not prior.get("revision"):
            raise InputError("change: voted patch set has no revision")
        _ensure_revision(repo, number, prior["number"], prior["revision"])
        prior_parent = _parent(repo, prior)
        if prior_parent != EMPTY_TREE:
            _ensure_revision(repo, number, prior["number"], prior_parent)
        previous_entries = _diff_entries(repo, prior_parent, prior["revision"])
        delta_entries, pairs = _delta(previous_entries, current_entries)
        moves, only_moves = _tree_renames(repo, prior["revision"], current["revision"], delta_entries)
        delta_line_data = _between_lines(repo, prior["revision"], current["revision"], delta_entries, moves)
    else:
        delta_entries, pairs = current_entries, [(None, entry) for entry in current_entries]
        moves, only_moves = [], False
        delta_line_data = _delta_lines(pairs)
    files = [entry["new"] for entry in delta_entries]
    flags = ["%s: binary" % entry["new"] for entry in delta_entries if entry.get("binary")]
    flags.extend(_mode_flags(pairs))
    kinds = {"TRIVIAL_REBASE", "NO_CODE_CHANGE", "NO_CHANGE"}
    # The kind is judged over EVERY patch set since my vote, not the current one
    # alone: vote on PS12, a REWORK PS13, a TRIVIAL_REBASE PS14 is not a carry-over
    # -- the content changed after the vote (a peer's round, 2026-09-24; the
    # rework held the fix for their own blocker). No vote, or a vote on the
    # current patch set, leaves the span at the current patch set, as before.
    span = [current]
    if prior:
        span = sorted((entry for key, entry in patch_sets.items()
                       if _later_patch_set(key, prior["number"])
                       and not _later_patch_set(key, current["number"])),
                      key=lambda entry: int(_number(entry))) or [current]
    reworked = [str(entry.get("number")) for entry in span if entry.get("kind") not in kinds]
    if role == "reviewer" and voted_set is None:
        ps_kind = "new"
    elif not reworked:
        ps_kind = "carry-over"
    else:
        if current.get("kind") in kinds:
            _fired(fired, "ps-kind-span", "current patch set is %s, but PS %s since my vote %s not"
                   % (current.get("kind"), ", ".join(reworked), "is" if len(reworked) == 1 else "are"))
        if only_moves and _is_move_only(repo, prior["revision"], current["revision"], moves):
            ps_kind = "move-only"
            flags.extend(_move_checks(config, repo, prior["revision"], current["revision"], moves))
        else:
            ps_kind = "rework"
            if moves:
                flags.extend(_move_checks(config, repo, prior["revision"], current["revision"], moves))
    _fired(fired, "ps-kind", "patch set is %s" % ps_kind)
    # A hold is not about the diff: my negative vote on an earlier patch set that
    # the current one no longer carries is surfaced whatever the delta, and says
    # when the change has become submittable without it. Any new patch set can
    # drop it -- a rework, or a rebase the label's copy rule does not carry a -1
    # across -- and the legs still follow the content: a trivial rebase needs a
    # new vote, not a new review. Submittable means what Gerrit means: every
    # submit record OK or FORCED, never one OK among several.
    if role == "reviewer" and vote and prior and (_approval_value(vote) or 0) < 0:
        still_held = any(
            approval.get("type") == "Code-Review" and _identity_matches(approval.get("by"), identifiers)
            and (_approval_value(approval) or 0) < 0
            for approval in current.get("approvals") or [])
        if not still_held:
            # Fail closed: a record that is not an object is not an OK one, and
            # a field that is not a list holds no records.
            records = change.get("submitRecords")
            submittable = as_of is None and isinstance(records, list) and bool(records) and all(
                isinstance(record, dict) and record.get("status") in ("OK", "FORCED")
                for record in records)
            hold = ("hold dropped: my %s on PS %s is not on current PS %s%s"
                    % (vote.get("value"), prior["number"], current["number"],
                       " -- the change is SUBMITTABLE without it" if submittable else ""))
            flags.append(hold)
            _fired(fired, "hold-dropped", hold)
    if ps_kind in ("carry-over", "move-only"):
        lenses, risk_floor = [], "inherit"
    else:
        lenses, risk_floor, trigger_flags = _lenses_and_triggers(config, files, delta_line_data, fired)
        flags.extend(trigger_flags)
    if owner_fix:
        review_legs, selected_lens = _review_legs({lens["class"] for lens in lenses}, config, risk_floor)
        legs = "roster"
        _fired(fired, "legs", "owner fix round uses the roster (opencode %s)" % selected_lens)
    elif ps_kind in ("carry-over", "move-only"):
        legs, review_legs = "none", []
        _fired(fired, "legs", "no legs for carry-over or move-only")
    elif prior and risk_floor != "HIGH" and _line_count(delta_line_data) <= config["small_delta_lines"]:
        legs, review_legs = "own-read", []
        _fired(fired, "legs", "small delta (%s lines) after my vote uses own-read" % _line_count(delta_line_data))
    else:
        review_legs, selected_lens = _review_legs({lens["class"] for lens in lenses}, config, risk_floor)
        legs = "roster"
        _fired(fired, "legs", "roster review legs selected (opencode %s; delta %s lines)"
               % (selected_lens, _line_count(delta_line_data)))
    common.update({
        "ps_kind": ps_kind, "delta_files": files, "lenses": lenses, "risk_floor": risk_floor,
        "legs": legs, "review_legs": review_legs,
        "unmatched_files": _unmatched_files(config, files),
        "min_review_legs": MIN_REVIEW_LEGS.get(risk_floor, 0) if legs == "roster" else 0,
        "flags": flags, "fired_rules": fired, "head": current["revision"], "base": base,
    })
    return common


def scope(config, raw, path, files, category, *, delta_lines=None, path_only=True, review_legs=False):
    fired = []
    lenses, risk_floor, _flags = _lenses_and_triggers(
        config, files, delta_lines if delta_lines is not None else {}, fired, path_only=path_only)
    if risk_floor == "HIGH":
        implementer = "HIGH risk or ambiguous spec | **the lead implements directly** — delegation adds a supervision layer exactly where supervision is hardest"
        reviewers = "HIGH risk, any implementer | **two independent reviewers from two families** — measured: two families independently converging on the same root cause was itself the strongest signal the finding was real"
    elif risk_floor == "MEDIUM":
        implementer = "MEDIUM, needs design judgment | a mid/high paid tier; or a second-pool model whose quota is otherwise idle"
        reviewers = "MEDIUM risk, when a free leg is available | **take the second reviewer anyway.** Measured: on a MEDIUM change, two families each returned 2 real defects with zero overlap. Convergence is the strong signal when it happens; disjoint coverage is the ordinary case, and a zero-quota second leg costs only wall-clock. Run them concurrently against the same frozen commit"
    else:
        implementer = "LOW, mechanical sweep, time matters | cheapest capable *paid* tier"
        reviewers = "SKILL.md has no reviewer-table row for LOW risk."
    _fired(fired, "scope", "risk floor %s%s" % (risk_floor, " for " + category if category else ""))
    output = {"rules": rules_identity(config, raw, path), "lenses": lenses, "risk_floor": risk_floor,
              "unmatched_files": _unmatched_files(config, files), "min_review_legs": MIN_REVIEW_LEGS[risk_floor],
              "suggestion": {"implementer": implementer, "reviewers": reviewers},
              "note": "suggestion only; the lead decides, and rules only raise", "fired_rules": fired}
    if review_legs:
        output["review_legs"], _selected_lens = _review_legs(
            {lens["class"] for lens in lenses}, config, risk_floor)
    return output


def _is_wording(config, path):
    globs = config.get("wording_globs")
    if globs is not None:
        return any(glob_matches(glob, path) for glob in globs)
    return path.lower().endswith(WORDING_SUFFIXES)


def _reviewed_record(reviewed):
    """The triage record of the head the previous round reviewed."""
    try:
        return _load_record(reviewed)
    except InputError:
        raise InputError("triage scope: no triage record for the reviewed head %s. A fix round is sized "
                         "against the round that reviewed it; triage that head first, or run scope without "
                         "--fix-of and take the full legs." % reviewed)


def _recorded_rounds(root, head):
    """The highest fix round recorded since ``root``, other than this head's
    own record. The records cannot tell a reviewed head from one amended
    before its review, so both count: the refusal names the way out."""
    highest = 0
    try:
        paths = sorted(records_path().glob("*.json"))
    except OSError:
        return highest
    for path in paths:
        if path.stem == head:
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        fix = document.get("fix") if isinstance(document, dict) else None
        if not isinstance(fix, dict) or fix.get("root") != root:
            continue
        number = fix.get("round")
        if type(number) is int and number > highest:
            highest = number
    return highest


def fix_sizing(config, target, base, head, fix_of, *, evidence=None, blocker=None, range_min=0, fired=None):
    """Size a fix round of the lead's own change by what the fix changed.

    Owner ruling, 2026-10-01 (aadev bb6a3ab): the delta from the head the
    previous round reviewed decides the legs, not the whole range --

      wording  every changed path is documentation (or only the message
               changed): the lead's own read, no leg;
      tested   the delta is within small_delta_lines and the lead states that
               every fixed finding has a test that failed before the fix and a
               mutation the lead killed (``evidence``): one cross-family leg;
      full     anything else, or a delta a delta_triggers rule raises to HIGH:
               the range's own minimum.

    From the third fix round on, a full round needs ``blocker``: after two
    fix rounds non-blocker findings are fixed under the lower rungs or
    recorded as known limits. The delta between the two reviews is computed
    the way ``change`` computes one between patch sets, so an amended or
    rebased commit is compared by its own patch, not by the moved base.
    """
    fired = fired if fired is not None else []
    repo = Path(target)
    try:
        reviewed = _git(repo, "rev-parse", "--verify", fix_of + "^{commit}").strip()
    except InputError:
        raise InputError("triage scope: --fix-of %s is not a commit in %s" % (fix_of, repo))
    if reviewed == head:
        raise InputError("triage scope: --fix-of %s is the HEAD under review; name the head the previous "
                         "round reviewed" % reviewed[:12])
    previous = _reviewed_record(reviewed)
    previous_fix = previous.get("fix") if isinstance(previous.get("fix"), dict) else {}
    earlier = previous_fix.get("round")
    earlier = earlier if type(earlier) is int and earlier > 0 else 0
    # Rounds are counted over every recorded fix round of the change, not along
    # the head named here: naming an older head must not restart the count and
    # step around the convergence gate (two review legs, 0.6.58).
    root = previous_fix.get("root") if isinstance(previous_fix.get("root"), str) else reviewed
    round_number = max(earlier, _recorded_rounds(root, head)) + 1
    previous_base = previous.get("base") or base
    previous_entries = _diff_entries(repo, previous_base, reviewed)
    current_entries = _diff_entries(repo, base, head)
    delta_entries, pairs = _delta(previous_entries, current_entries)
    moves, _only_moves = _tree_renames(repo, reviewed, head, delta_entries)
    delta_lines = _between_lines(repo, reviewed, head, delta_entries, moves)
    delta_files = sorted({path for entry in delta_entries for path in _paths_for(entry)})
    delta_count = _line_count(delta_lines)
    # Only a trigger may lift the fix to the full rung here: a risk_default of
    # MEDIUM or HIGH is the range's floor, already in range_min.
    _lenses, raised, _flags = _lenses_and_triggers(dict(config, risk_default="LOW"), delta_files,
                                                   delta_lines, [])
    unsized = ["%s: binary" % entry["new"] for entry in delta_entries if entry.get("binary")] + _mode_flags(pairs)
    if raised == "HIGH":
        rung, why = "full", "a delta trigger raises the fix itself to HIGH"
    elif unsized:
        rung, why = "full", "a binary or mode change has no lines to size (%s)" % "; ".join(unsized)
    elif all(_is_wording(config, path) for path in delta_files):
        rung, why = "wording", ("only the commit message changed" if not delta_files
                                else "every changed path is documentation")
    elif delta_count <= config["small_delta_lines"] and evidence:
        rung, why = "tested", "%s lines, within small_delta_lines=%s, with tests and killed mutations stated" % (
            delta_count, config["small_delta_lines"])
    elif delta_count <= config["small_delta_lines"]:
        rung, why = "full", ("%s lines is small, but no --fix-tested evidence was given; with it this fix "
                             "takes one leg" % delta_count)
    else:
        rung, why = "full", "%s lines exceeds small_delta_lines=%s" % (delta_count, config["small_delta_lines"])
    if rung == "full" and round_number >= CONVERGENCE_ROUND and not blocker:
        raise InputError(
            "triage scope: fix round %d: after two fix rounds a full round opens only for a blocker (%s). "
            "Fix the remaining findings under the lower rungs (documentation only, or --fix-tested "
            "'<evidence>' within small_delta_lines=%s), record them in the commit message as known limits, "
            "or pass --blocker '<the finding>'. Rounds are counted over every fix round recorded since %s, "
            "whichever head --fix-of names; a head amended before its review still has a record in %s, and "
            "removing that record is how to say it was never reviewed."
            % (round_number, why, config["small_delta_lines"], root[:12], records_path()))
    minimum = {"wording": 0, "tested": min(1, range_min)}.get(rung, range_min)
    _fired(fired, "fix-round", "fix round %d of %s: %s rung (%s); %s leg(s)"
           % (round_number, reviewed[:12], rung, why, minimum))
    return {
        "of": reviewed, "root": root, "round": round_number, "rung": rung, "why": why,
        "delta_files": delta_files, "delta_lines": delta_count,
        "evidence": evidence or None, "blocker": blocker or None,
        "range_min_review_legs": range_min,
    }, minimum


def _scope_range(base_ref, target):
    target = Path(target)
    status = _git(target, "-c", "core.quotePath=false", "status", "--porcelain=v1", "--untracked-files=no")
    renorm = Path(__file__).resolve().parent / "renorm-only.sh"
    try:
        filtered = subprocess.run([str(renorm), "--filter", str(target)], input=status,
                                  capture_output=True, text=True)
    except OSError as exc:
        raise InputError("triage scope: cannot check line-ending renormalization: %s" % exc)
    if filtered.returncode:
        raise InputError("triage scope: cannot check line-ending renormalization: %s"
                         % (filtered.stderr.strip() or "renorm-only.sh failed"))
    dirty = filtered.stdout
    if dirty:
        raise InputError("triage scope: %s has modified or staged tracked files; commit or restore them first"
                         % target)
    head = _git(target, "rev-parse", "--verify", "HEAD").strip()
    base = _git(target, "rev-parse", "--verify", base_ref + "^{commit}").strip()
    entries = _diff_entries(target, base, head)
    pairs = [(None, entry) for entry in entries]
    files = sorted({name for entry in entries for name in _paths_for(entry)})
    return head, base, files, _delta_lines(pairs)


def _load_record(head):
    path = _record_path(head)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError("triage round-check: no record for %s; run triage.py scope --base <base> --target <frozen dir> "
                         "or triage.py change <number> first" % head)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("triage round-check: cannot read record %s: %s" % (path, exc))
    if not isinstance(document, dict):
        raise InputError("triage round-check: record %s must be a JSON object" % path)
    return document


def _read_skips(head):
    path = records_path() / "skips.jsonl"
    rows, unreadable = [], 0
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    unreadable += 1
                    continue
                if not isinstance(entry, dict):
                    unreadable += 1
                    continue
                if entry.get("sha") == head:
                    rows.append(entry)
    except FileNotFoundError:
        return rows, unreadable
    except OSError as exc:
        raise InputError("triage round-check: cannot read skips from %s: %s" % (path, exc))
    return rows, unreadable


def round_check(head_ref, legs, target=None, expect=None):
    repo = Path(target) if target else Path.cwd()
    head = _git(repo, "rev-parse", "--verify", head_ref + "^{commit}").strip()
    record = _load_record(head)
    try:
        risk_floor = record["risk_floor"]
        minimum = record["min_review_legs"]
    except KeyError as exc:
        raise InputError("triage round-check: record %s lacks %s" % (_record_path(head), exc.args[0]))
    families = {entry.get("adapter"): entry.get("family") for entry in record.get("review_legs", [])
                if isinstance(entry, dict) and entry.get("adapter")}
    delivered, failed, distinct = [], [], set()
    checker = Path(__file__).resolve().parent / "leg-log-check.sh"
    _, family_data = roster.data()
    adapters = family_data.get("adapters", {})
    # data/families.json marks some families (unknown, Composer) as never
    # satisfying the cross-family rule; a leg in one is delivered, not counted.
    invalid = {name for name, spec in (family_data.get("families") or {}).items()
               if isinstance(spec, dict) and spec.get("accounting_valid") is False}
    for adapter, declared, _log in legs:
        if declared is not None and declared not in (adapters.get(adapter) or {}).get("serves", []):
            raise InputError("triage round-check: %s does not serve family %r (data/families.json)" % (adapter, declared))
    for adapter, declared, log in legs:
        command = [str(checker), adapter, log]
        if expect is not None:
            command += ["--expect", expect]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            # Families are declared, never guessed from a model name (the
            # roster's rule): a declared family must be one this adapter
            # serves; without one, the record's family for the adapter.
            family = declared if declared is not None else families.get(adapter)
            delivered.append({"adapter": adapter, "family": family, "log": log})
            if family is not None and family not in invalid:
                distinct.add(family)
        else:
            message = result.stderr.strip() or result.stdout.strip() or "leg-log-check failed"
            failed.append({"adapter": adapter, "log": log, "message": message})
    skips, unreadable = _read_skips(head)
    output = {"head": head, "risk_floor": risk_floor, "min_review_legs": minimum,
              "delivered": delivered, "failed": failed, "distinct_families": len(distinct),
              "skips": skips, "skips_unreadable": unreadable}
    if any(item["family"] is None for item in delivered):
        output["family_note"] = "a delivered leg with family null does not count; declare it as adapter:family=log"
    uncounted = sorted({item["family"] for item in delivered if item["family"] in invalid})
    if uncounted:
        output["accounting_note"] = ("delivered but not counted: %s cannot satisfy the cross-family rule "
                                     "(data/families.json accounting_valid false)" % ", ".join(uncounted))
    if isinstance(record.get("fix"), dict):
        # The round's report names the rung it was sized at (owner ruling,
        # 2026-10-01): say what the fix was sized as, not only that it passed.
        output["fix"] = {key: record["fix"].get(key) for key in ("of", "round", "rung", "why")}
    output["ok"] = output["distinct_families"] >= minimum
    return output


def cmd_check(path):
    try:
        load_config(path)
    except InputError as exc:
        print(exc)
        return 1
    return 0


def cmd_init(force):
    destination = triage_path()
    if os.path.lexists(destination) and not force:
        print("triage: %s already exists (pass --force to replace it)" % destination)
        return 1
    template = ROOT / "templates" / "triage.example.json"
    try:
        roster.atomic_write(destination, template.read_text(encoding="utf-8"))
    except OSError as exc:
        print("triage: cannot write %s: %s" % (destination, exc), file=sys.stderr)
        return 2
    print(destination)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="triage.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check")
    check.add_argument("--file")
    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    change = sub.add_parser("change")
    change.add_argument("number")
    change.add_argument("--query-json")
    change.add_argument("--include-wip", action="store_true")
    change.add_argument("--as-of-ps", help=("replay before my own vote and messages on that patch set; "
                                             "those actions are excluded on purpose"))
    scope_parser = sub.add_parser("scope")
    scope_input = scope_parser.add_mutually_exclusive_group(required=True)
    scope_input.add_argument("--files", nargs="+")
    scope_input.add_argument("--base")
    scope_parser.add_argument("--target")
    scope_parser.add_argument("--category")
    scope_parser.add_argument(
        "--fix-of", metavar="REVIEWED_HEAD",
        help="this is a fix round of the lead's own change; size it by the delta from the head the previous "
             "round reviewed (needs --base/--target and that head's triage record)")
    scope_parser.add_argument(
        "--fix-tested", metavar="EVIDENCE",
        help="with --fix-of: each fixed finding's test that failed before the fix, and the mutation killed")
    scope_parser.add_argument(
        "--blocker", metavar="FINDING",
        help="with --fix-of from the third fix round on: the blocker that justifies a full round")
    round_parser = sub.add_parser("round-check")
    round_parser.add_argument("--head", required=True)
    round_parser.add_argument("--leg", action="append",
                              help="adapter[:family]=path-to-review-log (repeat per leg)")
    round_parser.add_argument("--expect", help="regex a delivered review log must contain")
    round_parser.add_argument("--target")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(Path(args.file) if args.file else triage_path())
    if args.cmd == "init":
        return cmd_init(args.force)
    try:
        if args.cmd == "round-check":
            legs, logs = [], set()
            for item in args.leg or []:
                if "=" not in item:
                    raise InputError("triage round-check: --leg must be <adapter>[:<family>]=<log>")
                adapter_model, log = item.split("=", 1)
                adapter, separator, family = adapter_model.partition(":")
                if not adapter or not log or (separator and not family):
                    raise InputError("triage round-check: --leg must be <adapter>[:<family>]=<log>")
                # Two legs never write byte-identical reviews: the same file
                # (a symlink, a hard link, another spelling) or a copy of it is
                # one review, and counting it twice fakes a second family. Only
                # a non-empty regular file is compared by content: two empty
                # logs are two silent deaths, which leg-log-check reports as
                # two failed legs.
                real_log = os.path.realpath(log)
                keys = {("path", real_log)}
                try:
                    if os.path.isfile(log) and os.path.getsize(log) > 0:
                        with open(log, "rb") as handle:
                            keys.add(("sha256", hashlib.sha256(handle.read()).hexdigest()))
                except OSError:
                    pass   # an unreadable log is leg-log-check's to report
                if keys & logs:
                    raise InputError("triage round-check: the same log was given more than once: %s" % real_log)
                logs |= keys
                legs.append((adapter, family if separator else None, log))
            output = round_check(args.head, legs, args.target, args.expect)
            print(json.dumps(output, sort_keys=True))
            return 0 if output["ok"] else 1
        config, raw, path = load_config(triage_path())
        if args.cmd == "change":
            number = _change_number(args.number)
            output = triage_change(config, raw, path, number, args.query_json, args.include_wip, args.as_of_ps)
            _write_record(output, output["head"], "change")
        else:
            if bool(args.base) != bool(args.target):
                raise InputError("triage scope: --base and --target must be given together (or use --files)")
            for flag, value in (("--fix-tested", args.fix_tested), ("--blocker", args.blocker)):
                if value is not None and not args.fix_of:
                    raise InputError("triage scope: %s needs --fix-of" % flag)
                if value is not None and not value.strip():
                    raise InputError("triage scope: %s needs a non-empty statement" % flag)
            if args.fix_of and args.files:
                raise InputError("triage scope: --fix-of sizes a committed range; use --base/--target")
            if args.files:
                output = scope(config, raw, path, args.files, args.category)
                output["record"] = None
            else:
                head, base, files, delta_lines = _scope_range(args.base, args.target)
                output = scope(config, raw, path, files, args.category, delta_lines=delta_lines,
                               path_only=False, review_legs=True)
                output.update({"head": head, "base": base, "files": files})
                if args.fix_of:
                    fix, minimum = fix_sizing(
                        config, args.target, base, head, args.fix_of,
                        evidence=(args.fix_tested or "").strip() or None,
                        blocker=(args.blocker or "").strip() or None,
                        range_min=output["min_review_legs"], fired=output["fired_rules"])
                    output["fix"] = fix
                    output["min_review_legs"] = minimum
                    if minimum == 0:
                        output["review_legs"] = []
                        output["legs"] = "own-read"
                _write_record(output, head, "scope")
    except InputError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

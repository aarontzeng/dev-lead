#!/usr/bin/env python3
"""Rule-based Gerrit review triage for dev-lead (standard library only)."""
import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import roster


ROOT = Path(__file__).resolve().parent.parent
RISKS = ("LOW", "MEDIUM", "HIGH")
RISK_VALUE = {name: number for number, name in enumerate(RISKS)}
ROOT_KEYS = {
    "version", "me", "gerrit", "clones", "gateways", "lens_classes",
    "lenses", "delta_triggers", "risk_default", "move_check",
    "small_delta_lines", "order",
}


class InputError(Exception):
    pass


class Problems:
    def __init__(self):
        self.errors = []

    def error(self, path, message):
        self.errors.append("triage: %s: %s" % (path, message))


def public_keys(value):
    return [key for key in value if not str(key).startswith("_")]


def triage_path():
    explicit = os.environ.get("DEV_LEAD_TRIAGE")
    if explicit:
        return Path(explicit)
    return roster.roster_path().parent / "triage.json"


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
    return set(public_keys(by_lens))


def validate(doc):
    problems = Problems()
    if not isinstance(doc, dict):
        problems.error("(root)", "triage must be a JSON object")
        return problems
    _unknown_keys(doc, ROOT_KEYS, "(root)", problems)
    for key in ROOT_KEYS:
        if key not in doc:
            problems.error(key, "required")
    if type(doc.get("version")) is not int or doc.get("version") != 1:
        problems.error("version", "must be 1")
    me = doc.get("me")
    if not isinstance(me, list) or not me or not all(isinstance(item, str) and item for item in me):
        problems.error("me", "must be a non-empty list of identifiers")
    gerrit = doc.get("gerrit")
    if not isinstance(gerrit, dict):
        problems.error("gerrit", "must be an object")
    else:
        _unknown_keys(gerrit, {"ssh", "port"}, "gerrit", problems)
        if not isinstance(gerrit.get("ssh"), str) or not re.fullmatch(r"[^@\s]+@[^@\s]+", gerrit.get("ssh", "")):
            problems.error("gerrit.ssh", "must be user@host")
        if type(gerrit.get("port")) is not int:
            problems.error("gerrit.port", "must be an int")
    for name, values, absolute in (("clones", doc.get("clones"), True), ("gateways", doc.get("gateways"), False)):
        if not isinstance(values, dict):
            problems.error(name, "must be an object")
            continue
        for project, value in values.items():
            if str(project).startswith("_"):
                continue
            if not isinstance(project, str) or not project or not isinstance(value, str) or not value:
                problems.error("%s.%s" % (name, project), "must map non-empty strings")
            elif absolute and not os.path.isabs(value):
                problems.error("%s.%s" % (name, project), "must be an absolute path")
    lens_classes = doc.get("lens_classes")
    roster_classes = _roster_lens_classes(problems)
    if not isinstance(lens_classes, dict):
        problems.error("lens_classes", "must be an object")
        lens_classes = {}
    else:
        for lens, lens_class in lens_classes.items():
            if str(lens).startswith("_"):
                continue
            if not isinstance(lens, str) or not lens or not isinstance(lens_class, str):
                problems.error("lens_classes.%s" % lens, "lens and class must be non-empty strings")
            elif lens_class not in roster_classes:
                problems.error("lens_classes.%s" % lens, "class %r is not in roster opencode by_lens" % lens_class)
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
                    if name not in lens_classes:
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
                        if name not in lens_classes:
                            problems.error(path + ".lenses", "unknown lens %r" % name)
            for key in ("flag", "why"):
                if key in rule and not isinstance(rule[key], str):
                    problems.error(path + "." + key, "must be a string")
    if doc.get("risk_default") not in RISKS:
        problems.error("risk_default", "must be LOW, MEDIUM, or HIGH")
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


def rules_identity(doc, raw, path):
    return {"version": doc["version"], "sha256": hashlib.sha256(raw).hexdigest(), "path": str(path)}


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise InputError("git: %s" % message)
    return result.stdout


def _object_exists(repo, revision):
    result = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", revision + "^{commit}"],
                            capture_output=True, text=True)
    return result.returncode == 0


def _ensure_revision(repo, change, patch_set, revision):
    if _object_exists(repo, revision):
        return
    ref = "refs/changes/%02d/%s/%s" % (int(change) % 100, change, patch_set)
    _git(repo, "fetch", "origin", ref)
    if not _object_exists(repo, revision):
        raise InputError("git: fetched %s but revision %s is unavailable" % (ref, revision))


def _identity_matches(person, identifiers):
    if not isinstance(person, dict):
        return False
    return any(person.get(key) in identifiers for key in ("username", "email", "name"))


def _time(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return 0


def _after(left, right):
    try:
        return _time(left) > _time(right)
    except TypeError:
        return str(left) > str(right)


def _number(value):
    return str(value.get("number") if isinstance(value, dict) else value)


def _patch_sets(change):
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
            unique[_number(entry)] = entry
    if current_number is None:
        current_number = max(unique, key=lambda item: int(item)) if unique else None
    if not current_number or current_number not in unique:
        raise InputError("change: current patch set is missing from patchSets")
    change["patchSets"] = list(unique.values())
    return unique, unique[current_number]


def _parent(repo, patch_set):
    parents = patch_set.get("parents") or []
    if parents:
        parent = parents[0]
        if isinstance(parent, dict):
            parent = parent.get("id") or parent.get("revision") or parent.get("commit")
        if isinstance(parent, str) and parent:
            return parent
    return _git(repo, "rev-parse", patch_set["revision"] + "^").strip()


def _diff_entries(repo, parent, revision):
    raw = _git(repo, "diff", "--no-ext-diff", "--find-renames", "--name-status", "-z", parent, revision)
    fields = raw.split("\0")
    entries, index = [], 0
    while index < len(fields) - 1:
        status = fields[index]
        index += 1
        if not status:
            continue
        if status[0] in ("R", "C"):
            old, new = fields[index], fields[index + 1]
            index += 2
        else:
            old = new = fields[index]
            index += 1
        if new in ("/COMMIT_MSG", "/MERGE_LIST", "COMMIT_MSG", "MERGE_LIST"):
            continue
        patch = _git(repo, "diff", "--no-ext-diff", "--unified=0", "--format=", parent, revision, "--", new)
        useful = [line for line in patch.splitlines()
                  if not line.startswith(("index ", "@@", "diff --git ", "--- ", "+++ "))]
        added = [line[1:] for line in useful if line.startswith("+") and not line.startswith("+++")]
        removed = [line[1:] for line in useful if line.startswith("-") and not line.startswith("---")]
        entries.append({"status": status, "old": old, "new": new,
                        "signature": "\n".join(useful), "added": added, "removed": removed})
    return entries


def _delta(previous, current):
    """Current PS entries that changed from the last-voted PS's own patch."""
    previous_by_path = {entry["new"]: entry for entry in previous}
    chosen, pairs, matched_previous = [], [], set()
    for entry in current:
        before = previous_by_path.get(entry["new"]) or previous_by_path.get(entry["old"])
        if before is not None:
            matched_previous.add(before["new"])
        if before is None or before["signature"] != entry["signature"] or before["status"] != entry["status"]:
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


def _delta_lines(pairs):
    added, removed = [], []
    for before, after in pairs:
        old_added = Counter(before["added"] if before else [])
        old_removed = Counter(before["removed"] if before else [])
        new_added, new_removed = Counter(after["added"]), Counter(after["removed"])
        for line, count in (new_added - old_added).items():
            added.extend([line] * count)
        for line, count in (new_removed - old_removed).items():
            removed.extend([line] * count)
        for line, count in (old_added - new_added).items():
            removed.extend([line] * count)
        for line, count in (old_removed - new_removed).items():
            added.extend([line] * count)
    return added, removed


def _between_lines(repo, before_revision, after_revision, entries):
    """Actual hunk lines between two patch sets, limited to their delta files.

    Comparing each patch-set's *patch* identifies the files that matter, but a
    line first added by the earlier patch set becomes context in the later one.
    It is not thereby removed from the review delta.  Diffing the two resulting
    trees gives the hunk lines that actually changed between those reviews.
    """
    paths = [entry["new"] for entry in entries]
    if not paths:
        return [], []
    patch = _git(repo, "diff", "--no-ext-diff", "--unified=0", "--format=",
                 before_revision, after_revision, "--", *paths)
    added, removed = [], []
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _tree_text(repo, revision, path):
    result = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (revision, path)],
                            capture_output=True, text=True)
    if result.returncode:
        return None
    return result.stdout


def _tree_has(repo, revision, path):
    return _tree_text(repo, revision, path) is not None


URL = re.compile(r"https?://[^\s)\]>]+")
MARKDOWN_LINK = re.compile(r"\]\(([^)]+)\)")


def _move_checks(config, repo, previous_revision, current_revision, entries):
    flags = []
    naming = config["move_check"]["naming"]
    for entry in entries:
        old_text = _tree_text(repo, previous_revision, entry["old"])
        new_text = _tree_text(repo, current_revision, entry["new"])
        if old_text is None or new_text is None:
            flags.append("%s: cannot read moved content" % entry["new"])
            continue
        normalise = lambda text: re.sub(r"(\.\./)+", "../", text)
        if normalise(old_text) != normalise(new_text):
            flags.append("%s: content differs after relative-link-depth normalisation" % entry["new"])
        if set(URL.findall(old_text)) != set(URL.findall(new_text)):
            flags.append("%s: absolute URL set changed" % entry["new"])
        for target in MARKDOWN_LINK.findall(new_text):
            target = target.strip().strip("<>")
            if not target or target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                continue
            target = target.split("#", 1)[0]
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(entry["new"]), target))
            if not _tree_has(repo, current_revision, resolved):
                flags.append("%s: relative link %s does not resolve" % (entry["new"], target))
        for rule in naming:
            if glob_matches(rule["glob"], entry["new"]) and not re.fullmatch(rule["regex"], entry["new"]):
                flags.append("%s: naming regex does not match" % entry["new"])
    return flags


def _is_move_only(repo, previous_revision, current_revision, entries):
    if not entries or not all(entry["status"].startswith("R") for entry in entries):
        return False
    for entry in entries:
        old_text = _tree_text(repo, previous_revision, entry["old"])
        new_text = _tree_text(repo, current_revision, entry["new"])
        if old_text is None or new_text is None:
            return False
        normalise = lambda text: re.sub(r"(\.\./)+", "../", text)
        if normalise(old_text) != normalise(new_text):
            return False
    return True


def _fired(fired, rule, detail):
    fired.append({"rule": rule, "detail": detail})


def _lenses_and_triggers(config, files, delta_lines, fired, *, path_only=False):
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
        paths = [path for path in files if glob_matches(rule["glob"], path)]
        if not paths:
            continue
        fires = rule.get("path_only") is True if path_only else False
        if not path_only and "added_regex" in rule:
            compiled = re.compile(rule["added_regex"])
            fires = any(compiled.search(line) for line in delta_lines[0] + delta_lines[1])
        if not fires:
            continue
        raised = max(raised, rule["risk"], key=lambda item: RISK_VALUE[item])
        for name in rule.get("lenses", []):
            matches.setdefault(name, set()).update(paths)
        if rule.get("flag"):
            flags.append(rule["flag"])
        _fired(fired, "delta_triggers[%s]" % index,
               "%s fired for %s" % (rule.get("why") or rule["glob"], ", ".join(paths)))
    lens_list = [{"name": name, "class": config["lens_classes"][name], "files": sorted(paths)}
                 for name, paths in sorted(matches.items())]
    return lens_list, raised, flags


def _review_legs(lens_classes):
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
    resolved = []
    for adapter, leg in review.items():
        if str(adapter).startswith("_") or leg is None:
            continue
        chosen = leg
        if adapter == "opencode" and isinstance(leg, dict) and "by_lens" in leg:
            chosen = leg["by_lens"].get(wanted)
        if not isinstance(chosen, dict) or not chosen.get("model"):
            continue
        family = chosen.get("family")
        if not family:
            serves = families.get("adapters", {}).get(adapter, {}).get("serves", [])
            family = serves[0] if len(serves) == 1 else None
        resolved.append({"adapter": adapter, "model": chosen["model"], "family": family})
    return resolved, wanted


def _query(config, number, query_json):
    def rows_from(text):
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputError("query JSON: invalid JSON: %s" % exc)
            if row.get("type") != "stats":
                rows.append(row)
        return rows

    if query_json:
        try:
            text = Path(query_json).read_text(encoding="utf-8")
        except OSError as exc:
            raise InputError("query JSON: %s" % exc)
        rows = rows_from(text)
    else:
        command = ["ssh", "-p", str(config["gerrit"]["port"]), config["gerrit"]["ssh"],
                   "gerrit", "query", "--format=JSON", "--current-patch-set", "--patch-sets",
                   "--files", "--all-approvals", "--comments", "--dependencies", "change:%s" % number]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise InputError("gerrit query: %s" % (result.stderr.strip() or result.stdout.strip()))
        rows = rows_from(result.stdout)
    wanted = str(number)
    for row in rows:
        if str(row.get("number")) == wanted:
            change = row
            break
    else:
        raise InputError("query JSON: change %s not found" % number)
    if not query_json and change.get("topic"):
        topic_command = ["ssh", "-p", str(config["gerrit"]["port"]), config["gerrit"]["ssh"],
                         "gerrit", "query", "--format=JSON", "--current-patch-set",
                         "topic:%s" % change["topic"]]
        result = subprocess.run(topic_command, capture_output=True, text=True)
        if result.returncode:
            raise InputError("gerrit topic query: %s" % (result.stderr.strip() or result.stdout.strip()))
        rows += rows_from(result.stdout)
    return change, rows


def _last_vote(change, identifiers):
    found = []
    for patch_set in change.get("patchSets") or []:
        for approval in patch_set.get("approvals") or []:
            if approval.get("type") == "Code-Review" and _identity_matches(approval.get("by"), identifiers):
                found.append((approval.get("grantedOn", 0), patch_set, approval))
    if not found:
        return None, None, None
    _when, patch_set, approval = max(found, key=lambda item: _time(item[0]))
    return patch_set, approval, {"ps": int(patch_set["number"]), "value": approval.get("value")}


def triage_change(config, raw, path, number, query_json, include_wip):
    change, all_changes = _query(config, number, query_json)
    patch_sets, current = _patch_sets(change)
    current_spec = change.get("currentPatchSet")
    current["revision"] = current.get("revision") or (current_spec.get("revision") if isinstance(current_spec, dict) else None)
    if not current.get("revision"):
        raise InputError("change: current patch set has no revision")
    identifiers = set(config["me"])
    project = change.get("project")
    gateway = config["gateways"].get(project)
    role = "owner" if _identity_matches(change.get("owner"), identifiers) else "reviewer"
    voted_set, vote, vote_summary = _last_vote(change, identifiers)
    fired, flags = [], []
    if gateway is None:
        _fired(fired, "gateway", "unknown project %s" % project)
    _fired(fired, "role", "%s is %s" % (project, role))
    wip = bool(change.get("wip"))
    current_vote = vote_summary is not None and str(vote_summary["ps"]) == str(current["number"])
    mentioned_after_vote = False
    if vote:
        for comment in change.get("comments") or []:
            if (_after(comment.get("timestamp", 0), vote.get("grantedOn", 0))
                    and not _identity_matches(comment.get("reviewer"), identifiers)
                    and any(identifier in comment.get("message", "") for identifier in identifiers)):
                mentioned_after_vote = True
    owner_fix = False
    if role == "owner":
        upload_time = current.get("createdOn", 0)
        negatives = [approval for approval in current.get("approvals") or []
                     if approval.get("type") == "Code-Review" and int(approval.get("value", 0)) < 0
                     and _after(approval.get("grantedOn", 0), upload_time)]
        comments = [comment for comment in change.get("comments") or []
                    if not _identity_matches(comment.get("reviewer"), identifiers)
                    and _after(comment.get("timestamp", 0), upload_time)]
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
    clone = config["clones"].get(project)
    if not clone:
        raise InputError("triage: clones has no clone for project %s" % project)
    repo = Path(clone)
    if not repo.is_dir():
        raise InputError("triage: clone for %s does not exist: %s" % (project, repo))
    _ensure_revision(repo, number, current["number"], current["revision"])
    current_parent = _parent(repo, current)
    current_entries = _diff_entries(repo, current_parent, current["revision"])
    prior = voted_set if voted_set and str(voted_set["number"]) != str(current["number"]) else None
    if prior:
        if not prior.get("revision"):
            raise InputError("change: voted patch set has no revision")
        _ensure_revision(repo, number, prior["number"], prior["revision"])
        previous_entries = _diff_entries(repo, _parent(repo, prior), prior["revision"])
        delta_entries, pairs = _delta(previous_entries, current_entries)
        delta_line_data = _between_lines(repo, prior["revision"], current["revision"], delta_entries)
    else:
        delta_entries, pairs = current_entries, [(None, entry) for entry in current_entries]
        delta_line_data = _delta_lines(pairs)
    files = [entry["new"] for entry in delta_entries]
    kinds = {"TRIVIAL_REBASE", "NO_CODE_CHANGE", "NO_CHANGE"}
    if current.get("kind") in kinds:
        ps_kind = "carry-over"
    elif role == "reviewer" and voted_set is None:
        ps_kind = "new"
    elif prior and _is_move_only(repo, prior["revision"], current["revision"], delta_entries):
        ps_kind = "move-only"
        flags.extend(_move_checks(config, repo, prior["revision"], current["revision"], delta_entries))
    else:
        ps_kind = "rework"
        if prior and delta_entries and all(entry["status"].startswith("R") for entry in delta_entries):
            flags.extend(_move_checks(config, repo, prior["revision"], current["revision"], delta_entries))
    _fired(fired, "ps-kind", "patch set is %s" % ps_kind)
    if ps_kind in ("carry-over", "move-only"):
        lenses, risk_floor = [], "inherit"
    else:
        lenses, risk_floor, trigger_flags = _lenses_and_triggers(config, files, delta_line_data, fired)
        flags.extend(trigger_flags)
    if skip or ps_kind in ("carry-over", "move-only"):
        legs, review_legs = "none", []
        _fired(fired, "legs", "no legs for skip, carry-over, or move-only")
    elif owner_fix:
        legs, review_legs = "roster", _review_legs({lens["class"] for lens in lenses})[0]
        _fired(fired, "legs", "owner fix round uses the roster")
    else:
        line_count = len(delta_line_data[0]) + len(delta_line_data[1])
        if prior and line_count <= config["small_delta_lines"]:
            legs, review_legs = "own-read", []
            _fired(fired, "legs", "small delta (%s lines) after my vote uses own-read" % line_count)
        else:
            review_legs, selected_lens = _review_legs({lens["class"] for lens in lenses})
            legs = "roster"
            _fired(fired, "legs", "roster review legs selected (opencode %s)" % selected_lens)
    owner = change.get("owner") or {}
    rank = 99
    for index, rule in enumerate(config["order"]):
        if ("owner" in rule and rule["owner"] not in (owner.get("username"), owner.get("email"))) or ("gateway" in rule and rule["gateway"] != gateway):
            continue
        rank = rule["rank"]
        _fired(fired, "order[%s]" % index, "matched rank %s" % rank)
        break
    else:
        _fired(fired, "order", "no order rule matched; rank 99")
    dependencies = change.get("dependsOn") or []
    ancestors = [dependency.get("number") for dependency in dependencies if isinstance(dependency, dict)
                 and dependency.get("status") != "MERGED" and dependency.get("number") is not None]
    topic = change.get("topic")
    members = []
    for row in all_changes:
        member = row.get("number")
        if topic and row.get("topic") == topic and member is not None and member not in members:
            members.append(member)
    return {
        "rules": rules_identity(config, raw, path), "change": change.get("number", number),
        "project": project, "gateway": gateway, "role": role, "my_last_vote": vote_summary,
        "wip": wip, "skip": skip, "ps_kind": ps_kind, "delta_files": files, "lenses": lenses,
        "risk_floor": risk_floor, "legs": legs, "review_legs": review_legs, "flags": flags,
        "unmerged_ancestors": ancestors,
        "topic_members": {"topic": topic, "changes": members, "atomic": False},
        "order": rank, "fired_rules": fired,
    }


def scope(config, raw, path, files, category):
    fired = []
    lenses, risk_floor, _flags = _lenses_and_triggers(config, files, ([], []), fired, path_only=True)
    if risk_floor == "HIGH":
        implementer = "HIGH risk or ambiguous spec | the lead implements directly"
        reviewers = "HIGH risk, any implementer | two independent reviewers from two families"
    elif risk_floor == "MEDIUM":
        implementer = "MEDIUM, needs design judgment | a mid/high paid tier; or a second-pool model whose quota is otherwise idle"
        reviewers = "MEDIUM risk, when a free leg is available | take the second reviewer anyway"
    else:
        implementer = "LOW, mechanical sweep, time matters | cheapest capable paid tier"
        reviewers = "Reviewer | a family different from the implementer"
    _fired(fired, "scope", "risk floor %s%s" % (risk_floor, " for " + category if category else ""))
    return {"rules": rules_identity(config, raw, path), "lenses": lenses, "risk_floor": risk_floor,
            "suggestion": {"implementer": implementer, "reviewers": reviewers},
            "note": "suggestion only; the lead decides, and rules only raise", "fired_rules": fired}


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
    scope_parser = sub.add_parser("scope")
    scope_parser.add_argument("--files", nargs="+", required=True)
    scope_parser.add_argument("--category")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(Path(args.file) if args.file else triage_path())
    if args.cmd == "init":
        return cmd_init(args.force)
    try:
        config, raw, path = load_config(triage_path())
        if args.cmd == "change":
            output = triage_change(config, raw, path, args.number, args.query_json, args.include_wip)
        else:
            output = scope(config, raw, path, args.files, args.category)
    except InputError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

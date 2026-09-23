#!/usr/bin/env python3
"""View, check, and edit the dev-lead leg roster.

The roster DECLARES which model and effort each adapter runs. It is not a
default scripts/leg-cmd.sh may assume: that script still requires an explicit
--model, because the model is the cross-family accounting decision. This tool
reads the declaration, checks it against data/families.json and
data/launch.json, and prints the exact flags to append to leg-cmd.sh.

Data files are located from this script's path, never from the cwd — a skill
runs with the target repo as cwd.
"""
import argparse
import copy
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCH_PATH = ROOT / "data" / "launch.json"
FAMILIES_PATH = ROOT / "data" / "families.json"

# A concrete leg. by_lens / top_tier_judgment_only are opencode-review only;
# `when` is fallback only. Anything else that is not a _comment is a typo.
LEG_KEYS = {
    "model", "effort", "effort_in", "effort_config_key", "family",
    "note", "set_on", "why", "fallback", "gated_on", "not",
}
OPENCODE_REVIEW_KEYS = {"by_lens", "top_tier_judgment_only"}
FALLBACK_KEYS = {"when"}
INHERIT_KEYS = {"inherit", "set_on", "why"}
# Who decides that a green verdict may land (Aaron, 2026-09-23). "user" is the
# doctrine the skill has always had: the person approves the verdict and the
# diff, then the lead merges and pushes in the same run. "lead" makes a fully
# green verdict its own approval -- a standing version of the per-run
# `auto-merge` grant, and the per-run grant still exists for a one-off. A
# verdict that is not fully green falls back to the person in BOTH modes; the
# setting moves who approves a clean result, never what counts as clean. A
# repo's own contract still wins where it is stricter (QUANTA, CLAUDE.md).
MERGE_GATE_MODES = ("user", "lead")
MERGE_GATE_KEYS = {"mode", "set_on", "why"}
# Every top-level key the roster may carry. Unknown ones are an ERROR, not a
# shrug: a typo'd block ("mege_gate") would otherwise be accepted silently and
# the lead would run the default while the file says otherwise.
TOP_KEYS = {"version", "rounds", "merge_gate", "provenance"}
EFFORT_IN = {
    "model_suffix": "model_name",
    "flag": "flag",
    "config_only": "config_only",
}

_CACHE = {}


def data():
    if not _CACHE:
        _CACHE["launch"] = json.loads(LAUNCH_PATH.read_text(encoding="utf-8"))
        _CACHE["families"] = json.loads(FAMILIES_PATH.read_text(encoding="utf-8"))
    return _CACHE["launch"], _CACHE["families"]


PLUGIN_NAME = "dev-lead"


def roster_path():
    """The first SET source wins: DEV_LEAD_ROSTER, CLAUDE_PLUGIN_DATA, the home fallback.

    Set, not existing: an explicit path that points nowhere is reported by the
    caller, never quietly swapped for another file.

    CLAUDE_PLUGIN_DATA is only dev-lead's when it names dev-lead's own directory
    (Claude Code names it <plugin>-<marketplace>). Measured 2026-09-22: a lead's
    Bash inherited CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/codex-openai-codex,
    another plugin's, so trusting the variable alone read the wrong directory.
    """
    env = os.environ.get("DEV_LEAD_ROSTER")
    if env:
        return Path(env)
    plugin = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin and Path(plugin).name.startswith(PLUGIN_NAME + "-"):
        return Path(plugin) / "roster.json"
    return Path.home() / ".claude" / "plugins" / "data" / "dev-lead-dev-lead" / "roster.json"


def effort_flag_refusal(adapter, role, effort):
    """Whether leg-cmd.sh would refuse this effort flag. None means it would not.

    Mirrors the effort if/elif in scripts/leg-cmd.sh, which runs before the
    required-value checks. A later "missing required value(s): …" is not an
    effort refusal — codex review with no --effort fails on --base, and codex
    implement with no --effort fails on the {EFFORT} placeholder, after the
    effort mechanism has already accepted the spelling.

    config_only outside applies_to_role falls through every branch: the
    mechanism is not flag, none, or unknown, so the value is neither required
    nor forbidden here.
    """
    launch, _ = data()
    spec = launch[adapter]
    eff = spec["effort"]
    mech = eff["mechanism"]
    effort = effort or ""
    if mech == "model_suffix":
        if effort:
            return "%s puts effort in the MODEL NAME, not a flag" % adapter
    elif mech == "config_only" and role == eff.get("applies_to_role", role):
        if effort:
            return "%s's %s path has no effort control at all" % (adapter, role)
    elif mech == "flag" and not effort:
        return "%s needs --effort" % adapter
    elif mech == "none" and effort:
        return "%s has no effort concept" % adapter
    elif mech not in ("model_suffix", "flag", "config_only", "none"):
        return "%s declares unknown effort mechanism %r" % (adapter, mech)
    return None


class Problems:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, path, msg):
        self.errors.append("roster: %s: %s" % (path, msg))

    def warn(self, path, msg):
        self.warnings.append("roster: %s: warning: %s" % (path, msg))

    def __bool__(self):
        return bool(self.errors)


def _public_keys(obj):
    if not isinstance(obj, dict):
        return []
    return [k for k in obj if not str(k).startswith("_")]


def _serves(adapter):
    _, families = data()
    spec = families.get("adapters", {}).get(adapter) or {}
    return list(spec.get("serves") or [])


def _family_table():
    _, families = data()
    return families.get("families") or {}


def _effort_spec(adapter):
    launch, _ = data()
    return launch[adapter]["effort"]


def _argv_requires_effort(adapter, role):
    """True when leg-cmd.sh would refuse an empty effort for this argv template.

    Same rule as leg-cmd.sh: a `{EFFORT}` placeholder inside any argv token is
    a required value. Not an adapter list — the template is the source.
    """
    launch, _ = data()
    argv = launch[adapter]["role"][role].get("argv") or []
    return any("{EFFORT}" in tok for tok in argv)


def _check_family(family, adapter, path, problems):
    serves = _serves(adapter)
    table = _family_table()
    if family not in serves:
        problems.error(path, "unknown family %r" % (family,))
        return
    spec = table.get(family) or {}
    if spec.get("accounting_valid") is False:
        problems.warn(path, "%s cannot be the accounting leg" % family)


_TRIPLE_QUOTES = (chr(39) * 3, chr(34) * 3)
# what a config_only effort may be before it is written into a TOML file
_EFFORT_WORD = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _toml_lines(text):
    """Split on TOML's newlines only (LF, and CRLF via the trailing CR), keeping
    each terminator. str.splitlines also breaks on \\v, \\f, U+2028 ... and
    readlines() does not, so the reader and the writer once saw different
    lines and --yes could change a line the dry run never reported (cursor
    leg, 2026-09-23). Both now split HERE."""
    parts = text.split("\n")
    out = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def _root_assignments(lines):
    """Walk a TOML file's ROOT table: yield (index, key, raw_value) for each
    root assignment, stopping at the first table header. The one walker both
    the reader and the writer use, so a dry run and --yes can never disagree
    about which line is THE key (agy leg, 2026-09-23: two walkers had drifted).

    A multi-line array is skipped by bracket DEPTH, not by the first `]`
    (an element line `["a"],` closes its own bracket, not the array), and a
    multi-line string by its closing delimiter. raw_value is the text after
    `=`, stripped; a value that continues on later lines is yielded with
    raw_value None so callers treat it as unreadable.
    """
    depth = 0
    closer = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if closer is not None:
            if closer in line:
                closer = None
            continue
        if depth > 0:
            depth += line.count("[") - line.count("]")
            continue
        if line.startswith("["):
            return
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        if name[:1] in ("'", '"') and len(name) > 1 and name[-1] == name[0]:
            name = name[1:-1]
        value = value.strip()
        if value.startswith("["):
            opened = value.count("[") - value.count("]")
            if opened > 0:
                depth = opened
                yield i, name, None
                continue
        opener = value[:3] if value[:3] in _TRIPLE_QUOTES else None
        if opener and value.count(opener) < 2:
            closer = opener
            yield i, name, None
            continue
        yield i, name, value


def _scalar(value):
    """A root value's scalar text, or None when it cannot be read with
    confidence (unterminated, an escape a real parser would resolve, ...)."""
    if value is None:
        return None
    opener = value[:3] if value[:3] in _TRIPLE_QUOTES else None
    if opener:
        end = value.find(opener, 3)
        return value[3:end] if end > 0 else None
    if value[:1] in ("'", '"'):
        quote = value[0]
        i, out = 1, []
        while i < len(value):
            c = value[i]
            if c == "\\" and quote == '"' and i + 1 < len(value):
                nxt = value[i + 1]
                if nxt not in ('"', "\\"):
                    return None            # \\u, \\t, ...: a real parser resolves these
                out.append(nxt)
                i += 2
                continue
            if c == quote:
                return "".join(out)
            out.append(c)
            i += 1
        return None                        # unterminated on this line
    return value.split("#", 1)[0].strip() or None


def read_config_value(path, key):
    """A ROOT-table scalar from a TOML file, or None. Deliberately small, not a
    TOML parser: this reads one key out of a file another tool owns, on Python
    3.10 where tomllib does not exist.

    Review legs built each of these (2026-09-23) and they are pinned: the key
    must match EXACTLY and may be quoted; an inline comment is not part of the
    value; an escaped quote does not end a basic string; a BOM does not hide
    the first key; a key under a [table] is not a root key, but a `[` inside a
    multi-line string or array is not a table header. Anything it cannot read
    confidently is None -- "nothing is known", never a default. A duplicate
    root key returns the FIRST (the file is already invalid TOML).
    """
    try:
        # utf-8-sig: a BOM would otherwise make the first key unrecognisable
        with open(os.path.expanduser(str(path)), encoding="utf-8-sig", newline="") as fh:
            lines = _toml_lines(fh.read())
    except (OSError, UnicodeDecodeError):
        return None
    for _i, name, value in _root_assignments(lines):
        if name == key:
            return _scalar(value)
    return None


def _config_effort(eff):
    """The effort a config_only adapter will actually use on THIS machine, or
    None. The roster can only DECLARE it: the file is shared with other tools
    and other sessions, so nothing here edits it (a peer measured a roster
    saying medium while the machine ran high, 2026-09-23). Comparing is the
    most a check can honestly do."""
    return read_config_value(eff.get("config_file", ""), eff.get("config_key", ""))


def _check_effort(leg, adapter, role, path, problems):
    """Effort key rules. Not the same as the flag refusal for config_only review.

    On the config_only role the value is what the lead expects to READ from
    the config file. It may be stored and is never passed as --effort.
    A role whose argv template contains {EFFORT} must still store effort:
    codex implement is config_only only on the review path, and leg-cmd.sh
    refuses the task launch when {EFFORT} is empty.
    """
    eff = _effort_spec(adapter)
    mech = eff["mechanism"]
    applies = role == eff.get("applies_to_role", role)
    if mech in ("model_suffix", "none"):
        if "effort" in leg:
            if mech == "model_suffix":
                problems.error(path + ".effort",
                               "effort key is not allowed; %s puts effort in the model name" % adapter)
            else:
                problems.error(path + ".effort",
                               "effort key is not allowed; %s has no effort concept" % adapter)
    elif mech == "flag":
        if not leg.get("effort"):
            problems.error(path + ".effort", "missing effort")
    elif mech == "config_only" and applies:
        declared = leg.get("effort")
        in_force = _config_effort(eff)
        if declared and in_force and declared != in_force:
            problems.warn(path + ".effort",
                          "declares %r but %s %s on this machine says %r -- this leg "
                          "will run at %r (pin one per call: `codex exec -c %s=%s`; or "
                          "align this machine: `roster.py config-effort ... --yes`, "
                          "backed up first)"
                          % (declared, eff.get("config_file"), eff.get("config_key"),
                             in_force, in_force, eff.get("config_key"), declared))
    elif mech == "config_only":
        pass
    else:
        problems.error(path + ".effort", "unknown effort mechanism %r" % (mech,))
    # Flag adapters already error above. Codex implement is config_only for the
    # review role only; its implement argv still contains {EFFORT}, and
    # leg-cmd.sh then refuses a plan line that omitted --effort.
    if (_argv_requires_effort(adapter, role) and not leg.get("effort")
            and not any(e.startswith("roster: %s.effort: missing effort" % path)
                        for e in problems.errors)):
        problems.error(path + ".effort", "missing effort")
    if "effort_in" in leg:
        expect = EFFORT_IN.get(mech)
        if leg["effort_in"] != expect:
            problems.error(
                path + ".effort_in",
                "effort_in %r does not match mechanism %s (expected %r)"
                % (leg["effort_in"], mech, expect),
            )


def _check_leg(leg, adapter, role, path, problems, *, fallback=False, opencode_review=False):
    if leg is None:
        return
    if not isinstance(leg, dict):
        problems.error(path, "leg must be null or an object")
        return
    allowed = set(LEG_KEYS)
    if fallback:
        allowed |= FALLBACK_KEYS
    if opencode_review:
        allowed |= OPENCODE_REVIEW_KEYS
    for key in leg:
        if str(key).startswith("_"):
            continue
        if key not in allowed:
            problems.error("%s.%s" % (path, key), "unknown key %r" % (key,))
    container = opencode_review and "by_lens" in leg
    if "top_tier_judgment_only" in leg and opencode_review:
        if not isinstance(leg["top_tier_judgment_only"], dict):
            problems.error(path + ".top_tier_judgment_only", "must be an object")
    if container:
        by_lens = leg["by_lens"]
        if not isinstance(by_lens, dict):
            problems.error(path + ".by_lens", "must be an object")
        else:
            for lens, sub in by_lens.items():
                if str(lens).startswith("_"):
                    continue
                _check_leg(sub, adapter, role, "%s.by_lens.%s" % (path, lens), problems)
        if "family" in leg:
            _check_family(leg["family"], adapter, path + ".family", problems)
        if "effort" in leg or "effort_in" in leg:
            _check_effort(leg, adapter, role, path, problems)
    else:
        if "model" not in leg:
            problems.error(path + ".model", "model required")
        _check_effort(leg, adapter, role, path, problems)
        serves = _serves(adapter)
        if "family" in leg:
            _check_family(leg["family"], adapter, path + ".family", problems)
        elif role == "review" and len(serves) > 1:
            # A fallback too: it is the model that RUNS when it is selected, and
            # on a multi-family adapter it can be another family than its primary
            # (a cursor Grok leg falling back to a Claude model). Without a family
            # the collision checks below cannot see it (found by review, codex).
            problems.error(
                path + ".family",
                "review %s has no family; %s serves %s"
                % ("fallback" if fallback else "leg", adapter, ", ".join(serves)),
            )
    if "fallback" in leg and fallback:
        problems.error(path + ".fallback", "a fallback cannot have its own fallback")
    elif "fallback" in leg:
        _check_leg(leg["fallback"], adapter, role, path + ".fallback", problems, fallback=True)


def _collision_family(adapter, leg):
    """Family a collision check can see.

    An explicit family wins. Otherwise the same inference `_family_of` uses
    when planning: an adapter that serves exactly one family is that family
    even when the leg omits the key. validate() allows that omission.
    """
    if isinstance(leg, dict) and leg.get("family"):
        return leg["family"]
    serves = _serves(adapter)
    if len(serves) == 1:
        return serves[0]
    return None


def _leg_families(adapter, leg, label):
    """One list of (label, family) per alternative that can actually run.

    Lenses of one adapter are alternatives: only one runs, so they are not
    compared with each other. A fallback is an alternative to its own primary,
    and is compared only with other adapters.
    """
    if not isinstance(leg, dict):
        return []
    if "by_lens" in leg and isinstance(leg["by_lens"], dict):
        alts = []
        for lens, sub in leg["by_lens"].items():
            if str(lens).startswith("_") or not isinstance(sub, dict):
                continue
            entries = []
            fam = _collision_family(adapter, sub)
            if fam:
                entries.append(("%s review (%s)" % (adapter, lens), fam))
            fb = sub.get("fallback")
            if isinstance(fb, dict):
                fb_fam = _collision_family(adapter, fb)
                if fb_fam:
                    entries.append(("%s review (%s fallback)" % (adapter, lens), fb_fam))
            alts.append(entries)
        return alts
    entries = []
    fam = _collision_family(adapter, leg)
    if fam:
        entries.append(("%s review" % label, fam))
    fb = leg.get("fallback")
    if isinstance(fb, dict):
        fb_fam = _collision_family(adapter, fb)
        if fb_fam:
            entries.append(("%s review fallback" % label, fb_fam))
    return [entries]


def _collision_errors(review, path):
    grouped = []
    if not isinstance(review, dict):
        return []
    for adapter, leg in review.items():
        if str(adapter).startswith("_") or leg is None:
            continue
        grouped.append(_leg_families(adapter, leg, adapter))
    found = []
    for i in range(len(grouped)):
        for j in range(i + 1, len(grouped)):
            for alt_a in grouped[i]:
                for alt_b in grouped[j]:
                    for label_a, fam_a in alt_a:
                        for label_b, fam_b in alt_b:
                            if fam_a and fam_a == fam_b:
                                found.append(
                                    "roster: %s: %s and %s share family %s"
                                    % (path, label_a, label_b, fam_a)
                                )
    return found


def _nonnull_review_legs(rnd):
    review = rnd.get("review") if isinstance(rnd, dict) else None
    if not isinstance(review, dict):
        return 0
    return sum(1 for k, v in review.items() if not str(k).startswith("_") and v is not None)


def _is_inherit(rnd):
    if not isinstance(rnd, dict) or "inherit" not in rnd:
        return False
    return not [k for k in _public_keys(rnd) if k not in INHERIT_KEYS]


def validate(doc):
    problems = Problems()
    if not isinstance(doc, dict):
        problems.error("(root)", "roster must be a JSON object")
        return problems
    if type(doc.get("version")) is not int or doc.get("version") != 1:
        problems.error("version", "must be 1")
    for key in _public_keys(doc):
        if key not in TOP_KEYS:
            problems.error(key, "unknown top-level key %r (known: %s)"
                           % (key, ", ".join(sorted(TOP_KEYS))))
    _check_merge_gate(doc, problems)
    rounds = doc.get("rounds")
    if not isinstance(rounds, dict):
        problems.error("rounds", "required")
        return problems
    if "r1" not in rounds:
        problems.error("rounds.r1", "required")
    launch, _ = data()
    known = {k for k in launch if not str(k).startswith("_")}

    def check_round(name, rnd, *, require_review):
        path = "rounds.%s" % name
        if not isinstance(rnd, dict):
            problems.error(path, "must be an object")
            return
        if name == "fix" and "inherit" in rnd:
            extra = [k for k in _public_keys(rnd) if k not in INHERIT_KEYS]
            if extra:
                problems.error(path + ".inherit",
                               "inherit round also has %s" % ", ".join(extra))
            if rnd.get("inherit") != "r1":
                problems.error(path + ".inherit",
                               "bad inherit target %r" % (rnd.get("inherit"),))
            return
        for key in _public_keys(rnd):
            if key not in ("implement", "review"):
                problems.error("%s.%s" % (path, key), "unknown role %r" % (key,))
        for role in ("implement", "review"):
            block = rnd.get(role)
            if block is None:
                continue
            if not isinstance(block, dict):
                problems.error("%s.%s" % (path, role), "must be an object")
                continue
            for adapter, leg in block.items():
                if str(adapter).startswith("_"):
                    continue
                leg_path = "%s.%s.%s" % (path, role, adapter)
                if adapter not in known:
                    problems.error(leg_path, "unknown adapter %r" % (adapter,))
                    continue
                if role not in launch[adapter].get("role", {}):
                    problems.error(leg_path, "unknown role %r" % (role,))
                    continue
                opencode_review = adapter == "opencode" and role == "review"
                _check_leg(leg, adapter, role, leg_path, problems,
                           opencode_review=opencode_review)
        if "review" in rnd and isinstance(rnd.get("review"), dict):
            for line in _collision_errors(rnd["review"], path + ".review"):
                problems.errors.append(line)
        if require_review and _nonnull_review_legs(rnd) < 1:
            problems.error(path + ".review", "fix round has no review leg")

    r1 = rounds.get("r1")
    if "r1" in rounds:
        check_round("r1", r1, require_review=False)
    if "fix" in rounds:
        fix = rounds["fix"]
        if _is_inherit(fix) or (isinstance(fix, dict) and "inherit" in fix):
            check_round("fix", fix, require_review=False)
            if isinstance(fix, dict) and fix.get("inherit") == "r1" and isinstance(r1, dict):
                if _nonnull_review_legs(r1) < 1:
                    problems.error("rounds.fix.review", "fix round has no review leg")
        else:
            check_round("fix", fix, require_review=True)
    for key in _public_keys(rounds):
        if key not in ("r1", "fix"):
            problems.error("rounds.%s" % key, "unknown round %r" % (key,))
    return problems


def _check_merge_gate(doc, problems):
    gate = doc.get("merge_gate")
    if gate is None:
        return
    if not isinstance(gate, dict):
        problems.error("merge_gate", "must be an object {mode, why, set_on}")
        return
    for key in _public_keys(gate):
        if key not in MERGE_GATE_KEYS:
            problems.error("merge_gate.%s" % key, "unknown key %r" % (key,))
    mode = gate.get("mode")
    if mode not in MERGE_GATE_MODES:
        problems.error("merge_gate.mode",
                       "must be one of: %s (got %r)" % (", ".join(MERGE_GATE_MODES), mode))
    # why and set_on are REQUIRED, not decoration: `lead` is a standing
    # authorisation to land work without asking, and one that arrived in the
    # file with no reason and no date is exactly the one nobody can audit
    # later. The CLI already refuses to write it; a hand edit must fail too
    # (codex leg, 2026-09-23: {"mode": "lead"} alone passed).
    for key in ("why", "set_on"):
        value = gate.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.error("merge_gate.%s" % key, "required, a non-empty string")
    set_on = gate.get("set_on")
    if isinstance(set_on, str) and set_on.strip():
        try:
            date.fromisoformat(set_on.strip())
        except ValueError:
            problems.error("merge_gate.set_on", "not an ISO date: %r" % (set_on,))


def merge_gate_mode(doc):
    """The configured mode, or the default. Read by the skill at Phase 3.

    A document that `check` would refuse answers "user", never "lead": this
    function is the one an in-process caller reaches for, and a standing
    authorisation must not be readable out of a file the validator rejects
    (agy leg, 2026-09-23).
    """
    if not isinstance(doc, dict) or validate(doc).errors:
        return "user"
    gate = doc.get("merge_gate")
    mode = gate.get("mode") if isinstance(gate, dict) else None
    return mode if mode in MERGE_GATE_MODES else "user"


def load_roster(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "roster: %s: file not found" % path
    except OSError as exc:
        return None, "roster: %s: %s" % (path, exc)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, "roster: %s: invalid JSON: %s" % (path, exc)
    if not isinstance(doc, dict):
        return None, "roster: %s: roster must be a JSON object" % path
    return doc, None


def dumps(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def atomic_write(path, text):
    """Write the symlink target, not the link, or the link becomes a second copy."""
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        mode = stat.S_IMODE(target.stat().st_mode)
    else:
        mode = 0o644
    fd, tmp_name = tempfile.mkstemp(prefix=".roster.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def skeleton():
    return {
        "version": 1,
        "rounds": {
            "r1": {"implement": {}, "review": {}},
            "fix": {"inherit": "r1"},
        },
    }


def _ensure_maps(rnd):
    if "implement" not in rnd or not isinstance(rnd.get("implement"), dict):
        rnd["implement"] = {}
    if "review" not in rnd or not isinstance(rnd.get("review"), dict):
        rnd["review"] = {}


def _break_inherit(rnd, source):
    """Materialise r1 into the fix round, then the caller changes one slot.

    The fix round's own `_` keys stay. implement and review are a deep copy of
    r1, so the fix round differs from r1 only in the slot about to be written,
    and editing the copy does not change r1.
    """
    new = {}
    if isinstance(rnd, dict):
        for key, value in rnd.items():
            if str(key).startswith("_"):
                new[key] = value
    src = source if isinstance(source, dict) else {}
    impl = src.get("implement")
    rev = src.get("review")
    new["implement"] = copy.deepcopy(impl) if isinstance(impl, dict) else {}
    new["review"] = copy.deepcopy(rev) if isinstance(rev, dict) else {}
    return new


def _apply_inherit(doc, why):
    rounds = doc.setdefault("rounds", {})
    old = rounds.get("fix")
    new = {}
    if isinstance(old, dict):
        for key, value in old.items():
            if str(key).startswith("_"):
                new[key] = value
    new["inherit"] = "r1"
    new["set_on"] = date.today().isoformat()
    new["why"] = why if why else "inherit r1"
    rounds["fix"] = new


def _carry_comment_keys(existing, leg):
    """`_` keys are comments and are kept when a leg object is replaced."""
    if isinstance(existing, dict):
        for key, value in existing.items():
            if str(key).startswith("_") and key not in leg:
                leg[key] = value


def _apply_leg(doc, round_name, role, adapter, leg, lens):
    rounds = doc.setdefault("rounds", {})
    rnd = rounds.get(round_name)
    if round_name == "fix" and isinstance(rnd, dict) and "inherit" in rnd and _is_inherit(rnd):
        rnd = _break_inherit(rnd, rounds.get("r1"))
        rounds["fix"] = rnd
    if not isinstance(rnd, dict) or "inherit" in rnd:
        rnd = {"implement": {}, "review": {}}
        rounds[round_name] = rnd
    _ensure_maps(rnd)
    block = rnd[role]
    if lens:
        current = block.get(adapter)
        if not isinstance(current, dict):
            current = {}
        # Keep sibling lenses, comments, and top_tier_judgment_only.
        by_lens = current.get("by_lens")
        if not isinstance(by_lens, dict):
            by_lens = {}
        _carry_comment_keys(by_lens.get(lens), leg)
        by_lens[lens] = leg
        current["by_lens"] = by_lens
        block[adapter] = current
    else:
        _carry_comment_keys(block.get(adapter), leg)
        block[adapter] = leg


def _apply_unset(doc, round_name, role, adapter):
    rounds = doc.setdefault("rounds", {})
    rnd = rounds.get(round_name)
    if round_name == "fix" and isinstance(rnd, dict) and _is_inherit(rnd):
        return "roster: rounds.fix: inherits r1; unset the r1 leg, or set a fix leg"
    if not isinstance(rnd, dict):
        rnd = {"implement": {}, "review": {}}
        rounds[round_name] = rnd
    _ensure_maps(rnd)
    if role not in ("implement", "review"):
        return "roster: rounds.%s.%s: unknown role %r" % (round_name, role, role)
    rnd[role][adapter] = None
    return None


def _commit(path, doc):
    problems = validate(doc)
    if problems.errors:
        for line in problems.warnings:
            print(line)
        for line in problems.errors:
            print(line)
        return 1
    for line in problems.warnings:
        print(line)
    atomic_write(path, dumps(doc))
    return 0


def _load_or_skeleton(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return skeleton(), None
    # A symlink whose target does not exist yet is still the write path.
    if path.is_symlink() and not path.exists():
        return skeleton(), None
    return load_roster(path)


def cmd_path(path):
    state = "exists" if os.path.lexists(path) else "missing"
    print("%s %s" % (path, state))
    return 0


def _effort_handling(adapter, role, leg):
    eff = _effort_spec(adapter)
    mech = eff["mechanism"]
    applies = role == eff.get("applies_to_role", role)
    if mech == "config_only" and applies:
        return "expected; read from %s %s" % (eff["config_file"], eff["config_key"])
    if mech == "model_suffix":
        return "in model name"
    if mech == "none":
        return "none"
    if mech == "flag":
        return "flag %s" % (leg.get("effort") if isinstance(leg, dict) else "")
    if isinstance(leg, dict) and leg.get("effort"):
        return "flag %s" % leg["effort"]
    return "not set"


def _args_for(adapter, role, model, effort_value):
    """Exact text to append to `leg-cmd.sh <adapter> <role>`.

    config_only on its applies_to_role never contributes --effort: leg-cmd.sh
    refuses the flag, and the value is only what to expect in the config file.
    """
    eff = _effort_spec(adapter)
    mech = eff["mechanism"]
    applies = role == eff.get("applies_to_role", role)
    if mech == "config_only" and applies:
        return "--model %s" % model
    # Pass --effort exactly when the argv template requires {EFFORT} and the
    # mechanism does not refuse the flag. Codex implement is config_only only
    # on the review role; its task template still has {EFFORT}.
    if (effort_value and effort_flag_refusal(adapter, role, effort_value) is None
            and _argv_requires_effort(adapter, role)):
        return "--model %s --effort %s" % (model, effort_value)
    return "--model %s" % model


def _format_concrete(adapter, role, leg):
    if not isinstance(leg, dict):
        return "unset"
    parts = []
    if leg.get("model"):
        parts.append("model=%s" % leg["model"])
    if leg.get("family"):
        parts.append("family=%s" % leg["family"])
    parts.append("effort=%s" % _effort_handling(adapter, role, leg))
    fb = leg.get("fallback")
    if isinstance(fb, dict) and fb.get("model"):
        parts.append("fallback=%s" % fb["model"])
    return " ".join(parts)


def _show_leg_lines(adapter, role, leg):
    if leg is None or not isinstance(leg, dict):
        return ["%s: unset" % adapter]
    if "by_lens" in leg and isinstance(leg["by_lens"], dict):
        lines = ["%s:" % adapter]
        for lens, sub in leg["by_lens"].items():
            if str(lens).startswith("_"):
                continue
            if sub is None:
                lines.append("  %s: unset" % lens)
            else:
                lines.append("  %s: %s" % (lens, _format_concrete(adapter, role, sub)))
        return lines
    return ["%s: %s" % (adapter, _format_concrete(adapter, role, leg))]


def _resolved_round(rounds, name):
    rnd = rounds.get(name)
    if name == "fix" and isinstance(rnd, dict) and rnd.get("inherit") == "r1" and _is_inherit(rnd):
        return rounds.get("r1"), True
    return rnd, False


def cmd_show(path, only):
    path = Path(path)
    if not os.path.lexists(path) or (path.is_symlink() and not path.exists()):
        print("roster: no roster file at %s" % path)
        print('create one with: roster.py set <round> <role> <adapter> '
              '--model M [--effort E] [--family F] [--lens L] --why "why"')
        return 0
    doc, err = load_roster(path)
    if err:
        print(err)
        return 1
    # show is what Phase 3 reads, and it does not run check -- so a roster check
    # would refuse must not be shown as if its gate were in force. The WHOLE
    # document, not just the gate block: `version: 2` or a missing rounds.r1
    # makes check exit non-zero while the block itself is fine (cursor leg then
    # codex leg, 2026-09-23).
    gate = doc.get("merge_gate") if isinstance(doc.get("merge_gate"), dict) else {}
    gate_problems = validate(doc)
    if gate_problems.errors:
        print("merge gate: INVALID -- this roster is refused by check, so the "
              "gate is the default (user). Run `roster.py check`:")
        for line in gate_problems.errors:
            print("  " + line)
        mode = "user"
        gate = {}
    else:
        mode = merge_gate_mode(doc)
        print("merge gate: %s%s%s" % (
            mode,
            "" if gate.get("mode") in MERGE_GATE_MODES else " (default; not set in the file)",
            " -- %s" % gate["why"] if gate.get("why") else ""))
    print("  %s" % ("a fully green verdict is its own approval; anything not green still goes to the person"
                    if mode == "lead" else
                    "the person approves the verdict and the diff; the lead lands it in that same run"))
    rounds = doc.get("rounds") if isinstance(doc.get("rounds"), dict) else {}
    if only:
        names = [only]
    else:
        names = [k for k in ("r1", "fix") if k in rounds]
        names += [k for k in rounds if not str(k).startswith("_") and k not in names]
    print(path)
    for name in names:
        rnd, inherited = _resolved_round(rounds, name)
        header = "round %s" % name
        if inherited:
            header += " (inherited from r1)"
        print(header)
        if not isinstance(rnd, dict):
            print("  (missing)")
            continue
        for role in ("implement", "review"):
            block = rnd.get(role)
            if not isinstance(block, dict):
                continue
            print("  %s:" % role)
            for adapter, leg in block.items():
                if str(adapter).startswith("_"):
                    continue
                for line in _show_leg_lines(adapter, role, leg):
                    suffix = " (inherited from r1)" if inherited else ""
                    print("    %s%s" % (line, suffix))
    return 0


def _parse_implement(spec):
    """ADAPTER[=MODEL[:FAMILY]] -> (adapter, model, family, error).

    Model ids may themselves contain a colon (`vendor/model:free`). The tail
    after the last colon is a family only when it is one of that adapter's
    serves. A tail that names some other family in families.json is an error,
    not part of the model id. A tail that is not a family name at all stays
    in the id (`openrouter/...:free`).
    """
    spec = spec.strip()
    if "=" not in spec:
        return spec, None, None, None
    adapter, rest = spec.split("=", 1)
    adapter = adapter.strip()
    serves = _serves(adapter)
    model, family = rest, None
    if ":" in rest:
        head, tail = rest.rsplit(":", 1)
        if tail in serves:
            model, family = head, tail
        elif tail in _family_table():
            return adapter, None, None, (
                "family %s is not served by %s (serves %s)"
                % (tail, adapter, ", ".join(serves))
            )
    if model == "":
        model = None
    return adapter, model, family, None


def _select_review_leg(adapter, leg, lens):
    """Return (concrete leg or None, error or None)."""
    if leg is None:
        return None, "unset"
    if not isinstance(leg, dict):
        return None, "leg must be null or an object"
    if "by_lens" in leg:
        if not isinstance(leg["by_lens"], dict):
            return None, "by_lens must be an object"
        chosen = lens or "mechanical"
        if chosen not in leg["by_lens"]:
            return None, "unset"
        sub = leg["by_lens"][chosen]
        if sub is None:
            return None, "unset"
        if not isinstance(sub, dict):
            return None, "leg must be null or an object"
        return sub, None
    return leg, None


def _family_of(adapter, explicit, leg):
    if explicit:
        if explicit not in _serves(adapter):
            return None, "unknown family %r" % (explicit,)
        return explicit, None
    if isinstance(leg, dict) and leg.get("family"):
        return leg["family"], None
    serves = _serves(adapter)
    if len(serves) == 1:
        return serves[0], None
    if len(serves) == 0:
        return None, "unknown adapter family"
    return None, "family required (%s serves %s); pass ADAPTER=MODEL:FAMILY" % (
        adapter, ", ".join(serves))


def cmd_plan(path, round_name, implement, review, lens):
    doc, err = load_roster(path)
    if err:
        print(err)
        return 1
    problems = validate(doc)
    for line in problems.warnings:
        print(line)
    rounds = doc.get("rounds") if isinstance(doc.get("rounds"), dict) else {}
    rnd, _inherited = _resolved_round(rounds, round_name)
    if not isinstance(rnd, dict):
        problems.error("rounds.%s" % round_name, "missing")
        for line in problems.errors:
            print(line)
        return 1
    launch, _ = data()
    known = {k for k in launch if not str(k).startswith("_")}
    adapter, model_override, family_override, parse_err = _parse_implement(implement)
    reviewers = [part.strip() for part in review.split(",") if part.strip()]
    if not reviewers:
        problems.error("rounds.%s.review" % round_name, "no review leg named")

    impl_path = "rounds.%s.implement.%s" % (round_name, adapter)
    impl_block = rnd.get("implement") if isinstance(rnd.get("implement"), dict) else {}
    roster_impl = impl_block.get(adapter) if isinstance(impl_block, dict) else None
    impl_family = None
    impl_model = None
    impl_override = model_override is not None
    impl_effort = roster_impl.get("effort") if isinstance(roster_impl, dict) else None

    if parse_err:
        problems.error(impl_path, parse_err)
    elif adapter not in known:
        problems.error(impl_path, "unknown adapter %r" % (adapter,))
    elif "implement" not in launch[adapter].get("role", {}):
        problems.error(impl_path, "unknown role 'implement'")
    else:
        if model_override:
            impl_model = model_override
            # Family may still come from the roster entry when the override
            # names a model but not a family.
            source = roster_impl if isinstance(roster_impl, dict) else {}
            impl_family, fam_err = _family_of(adapter, family_override, source)
            if fam_err:
                problems.error(impl_path, fam_err)
        else:
            if roster_impl is None or not isinstance(roster_impl, dict):
                problems.error(impl_path, "unset")
            else:
                impl_model = roster_impl.get("model")
                if not impl_model:
                    problems.error(impl_path, "unset")
                impl_family, fam_err = _family_of(adapter, family_override, roster_impl)
                if fam_err:
                    problems.error(impl_path, fam_err)
        if impl_model:
            # A flag adapter with no stored effort cannot be launched. Other
            # refusals (an effort key on model_suffix / none) are validate() errors.
            # A template containing {EFFORT} (codex implement) is the same hole
            # even when the effort mechanism is not "flag".
            refusal = effort_flag_refusal(adapter, "implement", impl_effort or "")
            if refusal and "needs --effort" in refusal:
                problems.error(impl_path, "missing effort")
            elif _argv_requires_effort(adapter, "implement") and not impl_effort:
                problems.error(impl_path, "missing effort")

    resolved_reviews = []
    rev_block = rnd.get("review") if isinstance(rnd.get("review"), dict) else {}
    for rev in reviewers:
        rev_path = "rounds.%s.review.%s" % (round_name, rev)
        if rev not in known:
            problems.error(rev_path, "unknown adapter %r" % (rev,))
            continue
        if "review" not in launch[rev].get("role", {}):
            problems.error(rev_path, "unknown role 'review'")
            continue
        chosen, sel_err = _select_review_leg(rev, rev_block.get(rev), lens if rev == "opencode" else None)
        if sel_err:
            problems.error(rev_path, sel_err)
            continue
        family, fam_err = _family_of(rev, None, chosen)
        if fam_err:
            problems.error(rev_path, fam_err)
            continue
        if not chosen.get("model"):
            problems.error(rev_path, "unset")
            continue
        resolved_reviews.append((rev, chosen, family))

    if impl_family:
        for rev, _leg, family in resolved_reviews:
            if family == impl_family:
                problems.error(
                    "rounds.%s" % round_name,
                    "%s implement and %s review share family %s" % (adapter, rev, family),
                )
            fb = _leg.get("fallback") if isinstance(_leg, dict) else None
            if isinstance(fb, dict) and fb.get("family") and fb["family"] == impl_family:
                problems.error(
                    "rounds.%s" % round_name,
                    "%s implement and %s review fallback share family %s" % (adapter, rev, impl_family),
                )
    for i, (rev_a, leg_a, fam_a) in enumerate(resolved_reviews):
        families_a = [(rev_a + " review", fam_a)]
        fb = leg_a.get("fallback")
        if isinstance(fb, dict) and fb.get("family"):
            families_a.append((rev_a + " review fallback", fb["family"]))
        for rev_b, leg_b, fam_b in resolved_reviews[i + 1:]:
            families_b = [(rev_b + " review", fam_b)]
            fb_b = leg_b.get("fallback")
            if isinstance(fb_b, dict) and fb_b.get("family"):
                families_b.append((rev_b + " review fallback", fb_b["family"]))
            for label_a, fa in families_a:
                for label_b, fb_fam in families_b:
                    if fa and fa == fb_fam:
                        problems.error(
                            "rounds.%s.review" % round_name,
                            "%s and %s share family %s" % (label_a, label_b, fa),
                        )

    if problems.errors:
        for line in problems.errors:
            print(line)
        return 1

    line = _plan_line("implement", adapter, impl_model, impl_family, impl_effort, impl_override, None)
    print(line)
    for rev, leg, family in resolved_reviews:
        print(_plan_line("review", rev, leg.get("model"), family, leg.get("effort"), False, leg))
    return 0


def _plan_line(role, adapter, model, family, effort_value, override, leg):
    handling = _effort_handling(adapter, role, leg if isinstance(leg, dict) else {"effort": effort_value})
    args = _args_for(adapter, role, model, effort_value if effort_value else None)
    bits = [
        role,
        adapter,
        "model=%s" % model,
        "family=%s" % family,
        "effort=%s" % handling,
    ]
    if override:
        bits.append("override")
    if isinstance(leg, dict) and isinstance(leg.get("fallback"), dict) and leg["fallback"].get("model"):
        bits.append("fallback=%s" % leg["fallback"]["model"])
    bits.append("args=%s" % args)
    return " ".join(bits)


def cmd_check(path):
    doc, err = load_roster(path)
    if err:
        print(err)
        return 1
    problems = validate(doc)
    for line in problems.warnings:
        print(line)
    for line in problems.errors:
        print(line)
    return 1 if problems.errors else 0


def _build_leg(model, effort, family, why):
    leg = {"model": model}
    if effort is not None:
        leg["effort"] = effort
    if family is not None:
        leg["family"] = family
    leg["set_on"] = date.today().isoformat()
    leg["why"] = why
    return leg


def cmd_set(path, args):
    if args.inherit is not None:
        if args.inherit != "r1":
            print("roster: bad inherit target %r" % (args.inherit,))
            return 1
        if args.round != "fix" or args.role or args.adapter or args.model:
            print("roster: set fix --inherit r1 [--why TEXT]")
            return 1
        doc, err = _load_or_skeleton(path)
        if err:
            print(err)
            return 1
        _apply_inherit(doc, args.why)
        return _commit(path, doc)
    if not args.role or not args.adapter or not args.model or not args.why:
        print("roster: set <round> <role> <adapter> --model M [--effort E] "
              "[--family F] [--lens L] --why TEXT")
        return 1
    if args.role not in ("implement", "review"):
        print("roster: rounds.%s.%s: unknown role %r" % (args.round, args.role, args.role))
        return 1
    if args.round not in ("r1", "fix"):
        print("roster: rounds.%s: unknown round %r" % (args.round, args.round))
        return 1
    doc, err = _load_or_skeleton(path)
    if err:
        print(err)
        return 1
    leg = _build_leg(args.model, args.effort, args.family, args.why)
    _apply_leg(doc, args.round, args.role, args.adapter, leg, args.lens)
    return _commit(path, doc)


def cmd_gate(path, args):
    if not args.why:
        print('roster: gate <%s> --why TEXT' % "|".join(MERGE_GATE_MODES))
        return 1
    doc, err = _load_or_skeleton(path)
    if err:
        print(err)
        return 1
    doc["merge_gate"] = {"mode": args.mode, "set_on": date.today().isoformat(),
                         "why": args.why}
    return _commit(path, doc)




def _config_effort_line(lines, key):
    """Index of the ROOT-table line assigning `key`; None when the root has no
    such key; "ambiguous" when its value cannot be read with confidence (it
    spans lines, is unterminated, ...), in which case nothing is written. Uses
    the same walker as read_config_value, so the dry run and --yes agree."""
    for i, name, value in _root_assignments(lines):
        if name == key:
            return i if _scalar(value) is not None else "ambiguous"
    return None


def cmd_config_effort(path, args):
    """Offer, then (only with --yes) make, the machine's config agree with the
    roster's declared effort for a config_only leg.

    Aaron, 2026-09-23: "做，但要先備份並回報前後值". This is the ONE place
    dev-lead writes a user's config file, and only on an explicit yes given in
    /dev-lead:config: the file is shared with the user's interactive CLI and
    with other sessions, which is why no skill RUN ever touches it. Backup
    first (a failed backup is a stop), atomic replace keeping the mode, one
    line changed, read back, and old -> new reported.
    """
    doc, err = load_roster(path)
    if err:
        print(err)
        return 1
    rounds = doc.get("rounds") if isinstance(doc.get("rounds"), dict) else {}
    rnd, _inherited = _resolved_round(rounds, args.round)
    block = rnd.get(args.role) if isinstance(rnd, dict) else None
    leg = block.get(args.adapter) if isinstance(block, dict) else None
    eff = _effort_spec(args.adapter)
    applies = args.role == eff.get("applies_to_role", args.role)
    if eff.get("mechanism") != "config_only" or not applies:
        print("roster: %s %s takes its effort per call, not from a config file; "
              "nothing to write" % (args.adapter, args.role))
        return 1
    if not isinstance(leg, dict) or not leg.get("effort"):
        print("roster: rounds.%s.%s.%s declares no effort; nothing to compare"
              % (args.round, args.role, args.adapter))
        return 1
    declared = leg["effort"]
    # The value is written INTO a TOML file, so it must be a bare word: a quote
    # or a newline would let a roster value inject further settings (codex
    # leg, 2026-09-23: `medium"\nother = "x` became two root assignments).
    # fullmatch, not match: `$` also matches just before a TRAILING newline,
    # so "medium\n" passed and was written (codex leg, 2026-09-23)
    if not _EFFORT_WORD.fullmatch(str(declared)):
        print("refused: declared effort %r is not a plain effort word; nothing written"
              % (declared,))
        return 1
    link = Path(os.path.expanduser(eff["config_file"]))
    # A symlinked config (dotfiles) is edited at its TARGET, so the link
    # survives; replacing the link path would silently detach it (codex leg).
    cfg = Path(os.path.realpath(link))
    key = eff["config_key"]
    in_force = read_config_value(cfg, key)
    print("declared (roster): %s" % declared)
    print("in force (%s %s): %s" % (cfg, key, in_force or "not set"))
    if cfg != link:
        print("note: %s is a symlink; its target %s is the file changed" % (link, cfg))
    if in_force == declared:
        print("already agree; nothing written")
        return 0
    try:
        raw = cfg.read_bytes() if cfg.exists() else b""
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        print("refused: cannot read %s (%s); nothing written" % (cfg, exc))
        return 1
    lines = _toml_lines(text)
    at = _config_effort_line(lines, key)
    if at == "ambiguous":
        print("refused: %s's %s spans lines; edit it by hand. Nothing written." % (cfg, key))
        return 1
    newline = '%s = "%s"\n' % (key, declared)
    if not args.yes:
        print("would change: %s -> %s  (run again with --yes to write; a backup is taken first)"
              % (in_force or "not set", declared))
        return 0
    backup = None
    if cfg.exists():
        # Created EXCLUSIVELY and without following a link, in one open: a
        # check-then-copy left a window in which a symlink planted at the
        # backup's name was followed by copy2 (codex leg, 2026-09-23). The old
        # bytes are written from memory, so nothing is re-opened by name.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        n = 1
        while True:
            backup = cfg.with_name(cfg.name + ".bak." + stamp
                                   + ("" if n == 1 else "-%d" % n))
            try:
                bfd = os.open(str(backup), flags, 0o600)
                break
            except FileExistsError:
                n += 1                    # never overwrite an earlier backup
                if n > 1000:
                    print("refused: backup failed (no free backup name); nothing written")
                    return 1
            except OSError as exc:
                print("refused: backup failed (%s); nothing written" % (exc,))
                return 1
        try:
            with os.fdopen(bfd, "wb") as bfh:
                bfh.write(raw)
                bfh.flush()
                os.fsync(bfh.fileno())
            shutil.copystat(cfg, backup, follow_symlinks=False)
        except OSError as exc:
            print("refused: backup failed (%s); nothing written" % (exc,))
            return 1
    if at is None:
        lines.insert(0, newline)     # a root key must come before any table
    else:
        if lines[at] and not lines[at].endswith("\n"):
            newline = newline.rstrip("\n")
        lines[at] = newline
    mode = (cfg.stat().st_mode & 0o7777) if cfg.exists() else 0o600
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp: a random name created O_EXCL, so nothing pre-planted (a symlink
    # at a guessable `.tmp.<pid>`) can redirect this write (codex leg,
    # 2026-09-23, critical).
    fd, tmpname = tempfile.mkstemp(prefix="." + cfg.name + ".", dir=str(cfg.parent))
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(lines))
        os.chmod(tmp, mode)
        os.replace(tmp, cfg)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        print("refused: write failed (%s); %s is unchanged" % (exc, cfg))
        return 1
    after = read_config_value(cfg, key)
    print("changed: %s -> %s  (%s %s)" % (in_force or "not set", after, cfg, key))
    print("backup: %s" % (backup if backup else "none (the file did not exist)"))
    if after != declared:
        print("WARNING: read back %r, expected %r -- restore from the backup" % (after, declared))
        return 1
    return 0


def cmd_unset(path, args):
    if args.round not in ("r1", "fix"):
        print("roster: rounds.%s: unknown round %r" % (args.round, args.round))
        return 1
    path = Path(path)
    if not path.exists():
        print("roster: %s: file not found" % path)
        return 1
    doc, err = load_roster(path)
    if err:
        print(err)
        return 1
    problem = _apply_unset(doc, args.round, args.role, args.adapter)
    if problem:
        print(problem)
        return 1
    return _commit(path, doc)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="roster.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path")

    p_show = sub.add_parser("show")
    p_show.add_argument("--round", choices=("r1", "fix"))

    p_check = sub.add_parser("check")
    p_check.add_argument("--file")

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--round", required=True, choices=("r1", "fix"))
    p_plan.add_argument("--implement", required=True)
    p_plan.add_argument("--review", required=True)
    p_plan.add_argument("--lens", default="mechanical")

    p_set = sub.add_parser("set")
    p_set.add_argument("round")
    p_set.add_argument("role", nargs="?")
    p_set.add_argument("adapter", nargs="?")
    p_set.add_argument("--model")
    p_set.add_argument("--effort")
    p_set.add_argument("--family")
    p_set.add_argument("--lens")
    p_set.add_argument("--why")
    p_set.add_argument("--inherit")

    p_ce = sub.add_parser("config-effort")
    p_ce.add_argument("round", choices=("r1", "fix"))
    p_ce.add_argument("role")
    p_ce.add_argument("adapter")
    p_ce.add_argument("--yes", action="store_true")

    p_gate = sub.add_parser("gate")
    p_gate.add_argument("mode", choices=MERGE_GATE_MODES)
    p_gate.add_argument("--why")

    p_unset = sub.add_parser("unset")
    p_unset.add_argument("round")
    p_unset.add_argument("role")
    p_unset.add_argument("adapter")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        path = Path(args.file) if args.file else roster_path()
        return cmd_check(path)
    path = roster_path()
    if args.cmd == "path":
        return cmd_path(path)
    if args.cmd == "show":
        return cmd_show(path, args.round)
    if args.cmd == "plan":
        return cmd_plan(path, args.round, args.implement, args.review, args.lens)
    if args.cmd == "gate":
        return cmd_gate(path, args)
    if args.cmd == "config-effort":
        return cmd_config_effort(path, args)
    if args.cmd == "set":
        return cmd_set(path, args)
    if args.cmd == "unset":
        return cmd_unset(path, args)
    parser.error(args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# Emit the exact launch command for one delegate leg.
#
# Why this is a script and not another paragraph: every launch fact it prints
# was already written down in the matching SKILL.md or references/*-runtime.md,
# and leads composed launch commands from recall anyway -- five wrong launches
# in one measured session (2026-09-08), each contradicted by a line already in
# this repo. Telling the lead to read the runtime file is what was already
# being done. So the command comes out of data/launch.json instead.
#
# Usage:
#   leg-cmd.sh <adapter> <role> --model <model> [--effort <e>] [--target <dir>]
#              [--base <ref>] [--prompt-file <f>] [--run-dir <dir>]
#              [--add-dir <dir> ...] [--check]
#
# Not every option applies to every adapter, and one that does not is REFUSED,
# never dropped: --target/--base/--prompt-file only where the adapter's template
# has that slot (--prompt-file: grok; the others read "$RUN_DIR/prompt.md", so
# give them --run-dir), --add-dir only where data/launch.json names the
# adapter's add_dir_flag (cursor).
#
# Prints the command on stdout and the adapter's gotchas on stderr, so
#   eval "$(leg-cmd.sh agy review --model gemini-3.8-flash-medium --target "$T")"
# stays usable while the warnings still reach a human.
set -euo pipefail

die() { echo "leg-cmd: $*" >&2; exit 1; }

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DATA="${DEV_LEAD_LAUNCH:-$HERE/../data/launch.json}"   # override: test seam only
[ -z "${DEV_LEAD_LAUNCH:-}" ] || echo "leg-cmd: launch data overridden by DEV_LEAD_LAUNCH=$DATA" >&2
[ -f "$DATA" ] || die "cannot find data/launch.json at $DATA"

[ $# -ge 2 ] || die "usage: leg-cmd.sh <adapter> <role> --model <model> [--effort <e>] [--target <dir>] [--base <ref>] [--prompt-file <f>] [--run-dir <dir>] [--add-dir <dir> ...] [--check]  (each option only where the adapter takes it; see the header of this script)"
ADAPTER=$1; ROLE=$2; shift 2

MODEL=""; EFFORT=""; TARGET=""; BASE=""; PROMPT_FILE=""; RUN_DIR_ARG=""; CHECK=0; ADD_DIRS=""
# Every value option checks its value is there before `shift 2`: under
# `set -e`, `shift 2` with one argument left exits 1 with NO message, so a
# trailing `--model` used to end the script silently (found 2026-09-25).
needs_value() { [ $# -ge 2 ] || die "$1 needs a value"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --model)       needs_value "$@"; MODEL=$2; shift 2 ;;
    --effort)      needs_value "$@"; EFFORT=$2; shift 2 ;;
    --target)      needs_value "$@"; TARGET=$2; shift 2 ;;
    --base)        needs_value "$@"; BASE=$2; shift 2 ;;
    --prompt-file) needs_value "$@"; PROMPT_FILE=$2; shift 2 ;;
    --run-dir)     needs_value "$@"; RUN_DIR_ARG=$2; shift 2 ;;
    --add-dir)     [ -n "${2:-}" ] || die "--add-dir needs a directory"
                   case "$2" in -*) die "--add-dir: '$2' starts with '-' and would read as a flag; use ./$2" ;; esac
                   case "$2" in *$'\n'*) die "--add-dir: a directory name may not contain a newline" ;; esac
                   ADD_DIRS="$ADD_DIRS$2"$'\n'; shift 2 ;;
    --check)       CHECK=1; shift ;;
    *) die "unknown option: $1" ;;
  esac
done
[ -n "$MODEL" ] || die "--model is required (there is no safe default; the model IS the cross-family accounting decision)"

ADAPTER="$ADAPTER" ROLE="$ROLE" MODEL="$MODEL" EFFORT="$EFFORT" TARGET="$TARGET" \
BASE="$BASE" PROMPT_FILE="$PROMPT_FILE" RUN_DIR_ARG="$RUN_DIR_ARG" \
CHECK="$CHECK" DATA="$DATA" ADD_DIRS="$ADD_DIRS" HERE="$HERE" python3 - <<'PY'
import json, os, shlex, sys

d = json.load(open(os.environ["DATA"]))
a, role = os.environ["ADAPTER"], os.environ["ROLE"]
if a not in d or a.startswith("_"):
    sys.exit("leg-cmd: unknown adapter %r (have: %s)"
             % (a, ", ".join(k for k in d if not k.startswith("_"))))
spec = d[a]
if role not in spec["role"]:
    sys.exit("leg-cmd: adapter %r has no role %r (have: %s)"
             % (a, role, ", ".join(spec["role"])))

eff = spec["effort"]
# The mechanism that governs THIS role -- roster.py's rule, imported rather
# than copied (applies_to_role scopes it; another role follows its argv).
import importlib.util as _ilu_m
_rspec = _ilu_m.spec_from_file_location("dev_lead_roster_mech", os.path.join(os.environ["HERE"], "roster.py"))
_rmod = _ilu_m.module_from_spec(_rspec)
_rspec.loader.exec_module(_rmod)
mech = _rmod.mechanism_for_role(eff, role, spec["role"][role].get("argv"))
effort = os.environ.get("EFFORT", "")

# The whole point: refuse the spellings that were actually got wrong.
if mech == "model_suffix":
    if effort:
        sys.exit("leg-cmd: %s puts effort in the MODEL NAME, not a flag.\n"
                 "  drop --effort and use one of: %s"
                 % (a, ", ".join(eff.get("examples", []))))
elif mech == "config_only":
    if effort:
        sys.exit("leg-cmd: %s's %s path has no effort control at all.\n"
                 "  it reads %s from %s -- read that file and REPORT the value, do not assert one."
                 % (a, role, eff["config_key"], eff["config_file"]))
elif mech in ("flag",) and not effort:
    sys.exit("leg-cmd: %s needs --effort (it becomes %s); examples: %s"
             % (a, (eff.get("flag_by_role") or {}).get(role, eff.get("flag", "--effort")),
                ", ".join(eff.get("examples", []))))
elif mech == "none" and effort:
    sys.exit("leg-cmd: the suite passes no effort for %s %s (its CLI default applies; see "
             "data/launch.json); --model selects the tier" % (a, role))
elif mech not in ("model_suffix", "flag", "config_only", "none"):
    # No silent fall-through: an unrecognised mechanism means the guardrails
    # above did not run, so the command below is unvalidated. Refuse it.
    sys.exit("leg-cmd: %s declares unknown effort mechanism %r -- refusing to "
             "emit an unvalidated command" % (a, mech))

# A model id whose provider prefix is missing is accepted by the CLI and then
# fails SERVER-side -- `UnknownError: Unexpected server error`, step=0, nothing
# read. That is indistinguishable from an outage, and it cost a live 4-leg round
# on 2026-09-14: the leg was declared dead, retried, declared a pool outage,
# substituted with another family, and written up in the journal as a provider
# failure. The identical model worked with no -m at all (it was the default).
#
# What this checks is that a provider is PRESENT, not that it is the default
# one. The first version tested `startswith(model_prefix)`, which treated
# `opencode/` as the ONLY legal provider -- so it refused every id from every
# other provider the same CLI serves (measured 2026-09-15: `opencode models`
# lists 369 openrouter ids, 38 google, 7 opencode -- the guard rejected 98% of
# the catalogue), and it told the caller to fix it by PREPENDING, which yields
# `opencode/openrouter/nvidia/...` -- an id that exists nowhere. Following that
# advice cost three launches and produced exactly the `UnknownError` this guard
# was written to prevent: it manufactured an instance of the failure it exists
# to remove. It also blocked this account's own standing default
# (`openrouter/nvidia/nemotron-3.5-lightning:free`, set 2026-09-14) one day
# after that default was chosen. model_prefix is now only the provider
# SUGGESTED when the id carries no provider at all.
prefix = spec.get("model_prefix")
_m = os.environ["MODEL"]
if prefix and ("/" not in _m or not all(_m.split("/"))):
    _why = ("it has no provider segment" if "/" not in _m
            else "it has an EMPTY path segment (%r)" % _m)
    sys.exit("leg-cmd: %s needs a provider-qualified model id "
             "(<provider>/<model>).\n"
             "  you passed %r -- %s.\n"
             "  e.g. %r, or any id `%s models` lists; ids from other providers "
             "(openrouter/..., google/...) are passed through unchanged.\n"
             "  a bare name is accepted by the CLI and fails server-side as "
             "UnknownError with step=0, which reads like an outage, not like a "
             "bad argument."
             % (a, _m, _why, prefix + _m, spec["cli"]))

r = spec["role"][role]
subst = {"{MODEL}": os.environ["MODEL"], "{EFFORT}": effort,
         "{TARGET}": os.environ.get("TARGET", ""), "{BASE}": os.environ.get("BASE", ""),
         "{PROMPT_FILE}": os.environ.get("PROMPT_FILE", "")}
argv, missing = [], set()
for tok in r["argv"]:
    if tok == "{PROMPT}":
        argv.append('"$(cat "$RUN_DIR/prompt.md")"' if r["prompt_delivery"] == "argv" else tok)
        continue
    for k, v in subst.items():
        if k in tok:
            if not v:
                # The accepted spelling is the FLAG, not the placeholder key:
                # {PROMPT_FILE} -> --prompt-file. Telling the caller to pass
                # --prompt_file earned them "unknown option: --prompt_file" on
                # the very next line, and the failure path is where a wrong
                # instruction costs the most.
                missing.add(k.strip("{}").lower().replace("_", "-"))
            tok = tok.replace(k, v)
    argv.append(tok)
if missing:
    sys.exit("leg-cmd: missing required value(s): %s (pass --%s)"
             % (", ".join(sorted(missing)), " --".join(sorted(missing))))

# A value the caller supplied that this template never consumes is dropped
# silently, and the caller then believes it took effect. For --target that is a
# freeze-discipline hole: the emitted command runs in the lead's cwd, not the
# frozen worktree the lead thinks they pinned.
template = " ".join(r["argv"])
if r.get("prompt_frame"):
    # The framing builder consumes the base and reads HEAD from the target.
    template += " {BASE} {TARGET}"
    lacking = [f for f in ("base", "target") if not os.environ.get(f.upper())]
    if lacking:
        sys.exit("leg-cmd: %s/%s frames its brief with the base revision and the "
                 "frozen target -- pass %s" % (a, role, " ".join("--" + f for f in lacking)))
    # The framed prompt and the review are written under RUN_DIR; inside the
    # frozen target they would dirty the tree the review is certified against.
    _rd, _tg = os.environ.get("RUN_DIR_ARG", ""), os.environ.get("TARGET", "")
    if _rd and _tg:
        _rd, _tg = os.path.realpath(_rd), os.path.realpath(_tg)
        if _rd == _tg or _rd.startswith(_tg.rstrip(os.sep) + os.sep):
            sys.exit("leg-cmd: --run-dir %s is inside --target %s; the review would "
                     "write into the frozen tree -- use a run directory outside it" % (_rd, _tg))
for flag, ph in (("target", "{TARGET}"), ("base", "{BASE}"),
                 ("prompt-file", "{PROMPT_FILE}")):
    val = os.environ.get(flag.replace("-", "_").upper(), "")
    if val and ph not in template:
        sys.exit("leg-cmd: --%s was given but %s/%s has no %s in its template, "
                 "so it would be silently dropped.\n"
                 "  this adapter runs in the CALLER's cwd -- cd into the frozen "
                 "worktree yourself and assert HEAD before launching."
                 % (flag, a, role, ph))

# Same rule as the loop above, different shape: --run-dir has no placeholder to
# look for, because it is consumed only when the brief actually travels through
# $RUN_DIR. An adapter whose prompt_delivery is "prompt_file" reads its brief
# from --prompt-file, so a --run-dir passed there is precisely the silent drop
# that loop exists to refuse. Found by a review leg 2026-09-15, and the shape is
# worth naming: the rule was already here, and the new flag simply was not added
# to it -- the same sibling-miss that put this whole change in review.
if os.environ.get("RUN_DIR_ARG") and r["prompt_delivery"] == "prompt_file":
    sys.exit("leg-cmd: --run-dir was given but %s/%s reads its brief from "
             "--prompt-file, so RUN_DIR would be silently dropped.\n"
             "  give this adapter the brief with --prompt-file instead."
             % (a, role))

# Context outside the target (a peer session, 2026-09-22): a brief that points a
# leg at design docs or a sibling repo needs that directory readable. cursor
# takes `--add-dir` (probed 2026-09-22 in `--mode ask`: a file there was read);
# an adapter without an add_dir_flag REFUSES rather than dropping the value --
# for opencode that matters most, because a read outside its cwd is
# auto-rejected headless and the leg ends with empty output, looking like a
# clean run. Inserted before the prompt, after every flag the template sets.
add_dirs = [x for x in os.environ.get("ADD_DIRS", "").split("\n") if x]
if add_dirs:
    flag = spec.get("add_dir_flag")
    if not flag:
        sys.exit("leg-cmd: --add-dir was given but %s has no add-dir flag in "
                 "data/launch.json, so it would be silently dropped.\n  %s"
                 % (a, spec.get("add_dir_note",
                                "put the context inside the directory the leg runs in.")))
    at = next((i for i, t in enumerate(argv) if t == '"$(cat "$RUN_DIR/prompt.md")"'), len(argv))
    extra = []
    for d_ in add_dirs:
        extra += [flag, d_]
    argv[at:at] = extra

# Quote EVERYTHING the caller supplied. The output is documented for
# `eval "$(leg-cmd.sh ...)"`, so a token carrying a backtick, $(), ; or |
# is executed by the caller -- and quoting only tokens that contain a SPACE
# lets every one of those through. Measured 2026-09-08 on this very script:
# `--model 'x`+chr(96)+'id'+chr(96)+'y'` rendered unquoted.
# The two exceptions are strings this script emits itself and means as shell.
OURS = ('"$(cat "$RUN_DIR/prompt.md")"',)
# A template token under $RUN_DIR (codex review's -o file) is ours too, and must
# expand: single-quoting it would write to a file literally named $RUN_DIR/...
def _emit(t):
    if t in OURS:
        return t
    if t.startswith("$RUN_DIR/") and "'" not in t and '"' not in t:
        return '"%s"' % t
    return shlex.quote(t)
cmd = r.get("cli", spec["cli"]) + " " + " ".join(_emit(t) for t in argv)
frame = r.get("prompt_frame")
if frame:
    # The brief is a LENS: wrap it in this adapter's framing first, then hand
    # the leg the FRAMED prompt, the same way it would have read the brief. The
    # builder refuses an empty lens, an unknown placeholder or an unreadable
    # HEAD, and the && stops the paid run.
    builder = "python3 %s --adapter %s --base %s --target %s" % (
        shlex.quote(os.path.join(os.environ["HERE"], frame)), shlex.quote(a),
        shlex.quote(os.environ.get("BASE", "")), shlex.quote(os.environ.get("TARGET", "")))
    if r.get("prompt_evidence"):
        # A leg that cannot run git reads the diff and the base-side files the
        # builder materializes here -- under RUN_DIR, never in the frozen tree.
        builder += ' --evidence "$RUN_DIR/evidence"'
    if r["prompt_delivery"] == "argv":
        cmd = cmd.replace(OURS[0], '"$(cat "$RUN_DIR/framed-prompt.md")"')
    else:
        cmd += ' < "$RUN_DIR/framed-prompt.md"'
    if "{TARGET}" not in " ".join(r["argv"]):
        # The leg runs in its cwd (opencode). The frame names BASE..HEAD of the
        # --target, so the leg must run THERE, not wherever the lead happens to
        # be: a subshell, so the caller's own cwd is left alone under eval.
        cmd = "( cd %s && %s )" % (shlex.quote(os.environ.get("TARGET", "")), cmd)
    cmd = '%s < "$RUN_DIR/prompt.md" > "$RUN_DIR/framed-prompt.md" && %s' % (builder, cmd)
elif r["prompt_delivery"] == "stdin":
    cmd += ' < "$RUN_DIR/prompt.md"'

w = sys.stderr
print("# adapter %s / role %s   (data/launch.json, verified %s)" % (a, role, spec["verified"]), file=w)
# The effort block is ADAPTER-scoped but often describes ONE role: claude
# declares applies_to_role "review" and carries an implement_note saying the
# implement role passes none (pre-0.6.28 codex was the first such adapter, the
# other way round). Printing the review note under implement told the caller
# the opposite of the command beneath it. This `applies` test is display-only;
# the launch decision above goes through roster.mechanism_for_role.
applies = role == eff.get("applies_to_role", role)
# A config_only adapter's effort comes from a file on THIS machine, so the
# roster can only declare it. Print what is actually in force, or the lead
# reads the roster and believes a number the run will not use (measured on a
# peer's machine 2026-09-23: roster medium, config high, a round ran high).
# The reader is roster.py's, imported rather than copied: two parsers of one
# file drift, and a review leg broke both copies the day they were written.
if eff["mechanism"] == "config_only" and applies:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "dev_lead_roster", os.path.join(os.environ["HERE"], "roster.py"))
    _roster = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_roster)
    _key = eff.get("config_key", "")
    _cfg = eff.get("config_file", "")
    _in_force = _roster.read_config_value(_cfg, _key)
    print("# effort IN FORCE on this machine: %s (%s %s) -- the roster can only "
          "declare it; to pin one per call use `codex exec -c %s=<effort>` "
          "(references/codex-runtime.md); to change the machine, /dev-lead:config "
          "offers `roster.py config-effort` on your explicit yes, with a backup."
          % (_in_force or "not set", os.path.expanduser(_cfg), _key, _key), file=w)
note = eff.get("note", "") if applies else eff.get("implement_note", eff.get("note", ""))
print("# effort: %s -- %s" % (mech, note.split(".")[0]), file=w)
# ADAPTER-wide, and said so: gotchas carry no role field in launch.json, so
# some are review-path facts printed under an implement launch. Labelling
# the scope costs a word; giving them a role costs a data-model change.
for g in spec.get("gotchas", []):
    print("# gotcha (adapter-wide, may not apply to this role): %s" % g, file=w)
if spec.get("not_flags") and applies:
    # Gated on the same accessor: codex's not_flags are review-path facts
    # ("--help" is prompt text to `adversarial-review`, "--effort" to the review
    # path), and printing them under implement contradicted the emitted argv.
    print("# NOT flags on this path (they are parsed as prompt text): %s"
          % " ".join(spec["not_flags"]), file=w)

# The emitted command reads the brief from a path the CALLER owns, and the four
# adapters fail differently when it is empty or missing: agy says `error: empty
# prompt` and cursor `No prompt provided`, both loud; opencode's redirect dies
# before an output file exists, which reads as a path bug; and codex RUNS
# ANYWAY -- its review path has --base/--scope branch to work from, so an empty
# brief returns a plausible general review carrying none of the lens the lead
# asked for. That last one is the expensive shape: a leg that looks like it
# worked is the false green the four-leg method exists to prevent. One assertion
# in front of the command blocks all four mechanically.
#
# Chained with && rather than `exit 1` on purpose: this output is documented for
# `eval "$(leg-cmd.sh ...)"`, and an `exit` inside eval kills the caller's shell
# unconditionally. The chain does not.
#
# Stated exactly, because two review legs caught the author over-claiming it
# (2026-09-15): a blocked brief yields status 1 without exiting, so an ordinary
# interactive shell survives -- but a caller running under `set -e` DOES abort,
# since `eval` is a simple command and errexit sees its non-zero status. (The
# exemption errexit grants to && / || lists does not apply here: it protects the
# list when it is written inline, not an eval whose status is the list's.) That
# is deliberate and correct: in a script, stopping is what a missing brief
# should do. What `exit 1` would have added is killing the shell of the
# interactive caller too, and that is the case this chain exists to avoid.
delivery = r["prompt_delivery"]
brief = (shlex.quote(os.environ["PROMPT_FILE"]) if delivery == "prompt_file"
         else '"$RUN_DIR/prompt.md"')

pre = []
if delivery != "prompt_file":
    run_dir = os.environ.get("RUN_DIR_ARG", "")
    if run_dir:
        # Absolute, because a framed leg that runs in its own cwd reads
        # "$RUN_DIR/framed-prompt.md" after a `cd` into the target.
        pre.append("export RUN_DIR=%s" % shlex.quote(os.path.abspath(run_dir)))
    else:
        # Measured 2026-09-14: a prefix assignment LOOKS right and is not.
        print("# RUN_DIR must be exported on its own line before this command. "
              "A prefix assignment (`RUN_DIR=/p cmd \"$(cat \"$RUN_DIR/...\")\"`) "
              "expands the argument BEFORE the assignment takes effect, so the "
              "leg reads /prompt.md and the failure looks like a path bug in "
              "this script. Pass --run-dir to have the export emitted for you.",
              file=w)
# An UNSET RUN_DIR makes the assertion read `test -s /prompt.md`, which passes
# if that file happens to exist -- the leg then reads the wrong brief and the
# assertion has certified it. Named by a review leg, 2026-09-15. So require the
# variable itself, not only the file.
if delivery == "prompt_file":
    guard, why = "test -s %s" % brief, "brief is empty or missing:"
else:
    guard = 'test -n "$RUN_DIR" && test -s %s' % brief
    why = "RUN_DIR unset, or brief empty/missing:"
pre.append("%s || { echo 'leg-cmd: %s' %s >&2; false; } &&" % (guard, why, brief))
print("\n".join(pre))
print(cmd)

if os.environ.get("CHECK") == "1":
    print("# --check: verify the model string yourself, e.g. "
          "`cursor-agent models`, `opencode models`, `agy models`", file=w)
PY

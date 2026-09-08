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
#              [--base <ref>] [--prompt-file <f>] [--check]
#
# Prints the command on stdout and the adapter's gotchas on stderr, so
#   eval "$(leg-cmd.sh agy review --model gemini-3.8-flash-medium --target "$T")"
# stays usable while the warnings still reach a human.
set -euo pipefail

die() { echo "leg-cmd: $*" >&2; exit 1; }

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DATA="$HERE/../data/launch.json"
[ -f "$DATA" ] || die "cannot find data/launch.json at $DATA"

[ $# -ge 2 ] || die "usage: leg-cmd.sh <adapter> <role> --model <model> [--effort <e>] [--target <dir>] [--base <ref>] [--prompt-file <f>] [--check]"
ADAPTER=$1; ROLE=$2; shift 2

MODEL=""; EFFORT=""; TARGET=""; BASE=""; PROMPT_FILE=""; CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --model)       MODEL=${2:-}; shift 2 ;;
    --effort)      EFFORT=${2:-}; shift 2 ;;
    --target)      TARGET=${2:-}; shift 2 ;;
    --base)        BASE=${2:-}; shift 2 ;;
    --prompt-file) PROMPT_FILE=${2:-}; shift 2 ;;
    --check)       CHECK=1; shift ;;
    *) die "unknown option: $1" ;;
  esac
done
[ -n "$MODEL" ] || die "--model is required (there is no safe default; the model IS the cross-family accounting decision)"

ADAPTER="$ADAPTER" ROLE="$ROLE" MODEL="$MODEL" EFFORT="$EFFORT" TARGET="$TARGET" \
BASE="$BASE" PROMPT_FILE="$PROMPT_FILE" CHECK="$CHECK" DATA="$DATA" python3 - <<'PY'
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
mech = eff["mechanism"]
effort = os.environ.get("EFFORT", "")

# The whole point: refuse the spellings that were actually got wrong.
if mech == "model_suffix":
    if effort:
        sys.exit("leg-cmd: %s puts effort in the MODEL NAME, not a flag.\n"
                 "  drop --effort and use one of: %s"
                 % (a, ", ".join(eff.get("examples", []))))
elif mech == "config_only" and role == eff.get("applies_to_role", role):
    if effort:
        sys.exit("leg-cmd: %s's %s path has no effort control at all.\n"
                 "  it reads %s from %s -- read that file and REPORT the value, do not assert one."
                 % (a, role, eff["config_key"], eff["config_file"]))
elif mech in ("flag",) and not effort:
    sys.exit("leg-cmd: %s needs --effort (it becomes %s); examples: %s"
             % (a, eff["flag"], ", ".join(eff.get("examples", []))))
elif mech == "none" and effort:
    sys.exit("leg-cmd: %s has no effort concept; --model selects the tier" % a)
elif mech not in ("model_suffix", "flag", "config_only", "none"):
    # No silent fall-through: an unrecognised mechanism means the guardrails
    # above did not run, so the command below is unvalidated. Refuse it.
    sys.exit("leg-cmd: %s declares unknown effort mechanism %r -- refusing to "
             "emit an unvalidated command" % (a, mech))

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
                missing.add(k.strip("{}").lower())
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
for flag, ph in (("target", "{TARGET}"), ("base", "{BASE}"),
                 ("prompt-file", "{PROMPT_FILE}")):
    val = os.environ.get(flag.replace("-", "_").upper(), "")
    if val and ph not in template:
        sys.exit("leg-cmd: --%s was given but %s/%s has no %s in its template, "
                 "so it would be silently dropped.\n"
                 "  this adapter runs in the CALLER's cwd -- cd into the frozen "
                 "worktree yourself and assert HEAD before launching."
                 % (flag, a, role, ph))

# Quote EVERYTHING the caller supplied. The output is documented for
# `eval "$(leg-cmd.sh ...)"`, so a token carrying a backtick, $(), ; or |
# is executed by the caller -- and quoting only tokens that contain a SPACE
# lets every one of those through. Measured 2026-09-08 on this very script:
# `--model 'x`+chr(96)+'id'+chr(96)+'y'` rendered unquoted.
# The two exceptions are strings this script emits itself and means as shell.
OURS = ('"$(cat "$RUN_DIR/prompt.md")"',)
cmd = spec["cli"] + " " + " ".join(t if t in OURS else shlex.quote(t) for t in argv)
if r["prompt_delivery"] == "stdin":
    cmd += ' < "$RUN_DIR/prompt.md"'

w = sys.stderr
print("# adapter %s / role %s   (data/launch.json, verified %s)" % (a, role, spec["verified"]), file=w)
print("# effort: %s -- %s" % (mech, eff.get("note", "").split(".")[0]), file=w)
for g in spec.get("gotchas", []):
    print("# gotcha: %s" % g, file=w)
if spec.get("not_flags"):
    print("# NOT flags on this path (they are parsed as prompt text): %s"
          % " ".join(spec["not_flags"]), file=w)
print(cmd)

if os.environ.get("CHECK") == "1":
    print("# --check: verify the model string yourself, e.g. "
          "`cursor-agent models`, `opencode models`, `agy models`", file=w)
PY

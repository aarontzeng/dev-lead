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
#              [--base <ref>] [--prompt-file <f>] [--run-dir <dir>] [--check]
#
# Prints the command on stdout and the adapter's gotchas on stderr, so
#   eval "$(leg-cmd.sh agy review --model gemini-3.8-flash-medium --target "$T")"
# stays usable while the warnings still reach a human.
set -euo pipefail

die() { echo "leg-cmd: $*" >&2; exit 1; }

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DATA="$HERE/../data/launch.json"
[ -f "$DATA" ] || die "cannot find data/launch.json at $DATA"

[ $# -ge 2 ] || die "usage: leg-cmd.sh <adapter> <role> --model <model> [--effort <e>] [--target <dir>] [--base <ref>] [--prompt-file <f>] [--run-dir <dir>] [--check]"
ADAPTER=$1; ROLE=$2; shift 2

MODEL=""; EFFORT=""; TARGET=""; BASE=""; PROMPT_FILE=""; RUN_DIR_ARG=""; CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --model)       MODEL=${2:-}; shift 2 ;;
    --effort)      EFFORT=${2:-}; shift 2 ;;
    --target)      TARGET=${2:-}; shift 2 ;;
    --base)        BASE=${2:-}; shift 2 ;;
    --prompt-file) PROMPT_FILE=${2:-}; shift 2 ;;
    --run-dir)     RUN_DIR_ARG=${2:-}; shift 2 ;;
    --check)       CHECK=1; shift ;;
    *) die "unknown option: $1" ;;
  esac
done
[ -n "$MODEL" ] || die "--model is required (there is no safe default; the model IS the cross-family accounting decision)"

ADAPTER="$ADAPTER" ROLE="$ROLE" MODEL="$MODEL" EFFORT="$EFFORT" TARGET="$TARGET" \
BASE="$BASE" PROMPT_FILE="$PROMPT_FILE" RUN_DIR_ARG="$RUN_DIR_ARG" \
CHECK="$CHECK" DATA="$DATA" python3 - <<'PY'
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
if prefix and "/" not in os.environ["MODEL"]:
    sys.exit("leg-cmd: %s needs a provider-qualified model id "
             "(<provider>/<model>).\n"
             "  you passed %r -- it has no provider segment.\n"
             "  e.g. %r, or any id `%s models` lists; ids from other providers "
             "(openrouter/..., google/...) are passed through unchanged.\n"
             "  a bare name is accepted by the CLI and fails server-side as "
             "UnknownError with step=0, which reads like an outage, not like a "
             "bad argument."
             % (a, os.environ["MODEL"], prefix + os.environ["MODEL"],
                spec["cli"]))

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
# The effort block is ADAPTER-scoped but often describes ONE role: codex
# declares applies_to_role "review" and carries an implement_note saying the
# task path DOES take --effort. Printing the review note under implement told
# the caller that --effort is parsed as prompt text, directly above a command
# passing --effort. Same accessor the guard at the top already uses.
applies = role == eff.get("applies_to_role", role)
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
# `eval "$(leg-cmd.sh ...)"`, and an `exit` inside eval kills the CALLER's
# interactive shell.
delivery = r["prompt_delivery"]
brief = (shlex.quote(os.environ["PROMPT_FILE"]) if delivery == "prompt_file"
         else '"$RUN_DIR/prompt.md"')

pre = []
if delivery != "prompt_file":
    run_dir = os.environ.get("RUN_DIR_ARG", "")
    if run_dir:
        pre.append("export RUN_DIR=%s" % shlex.quote(run_dir))
    else:
        # Measured 2026-09-14: a prefix assignment LOOKS right and is not.
        print("# RUN_DIR must be exported on its own line before this command. "
              "A prefix assignment (`RUN_DIR=/p cmd \"$(cat \"$RUN_DIR/...\")\"`) "
              "expands the argument BEFORE the assignment takes effect, so the "
              "leg reads /prompt.md and the failure looks like a path bug in "
              "this script. Pass --run-dir to have the export emitted for you.",
              file=w)
pre.append("test -s %s || { echo 'leg-cmd: brief is empty or missing:' %s >&2; "
           "false; } &&" % (brief, brief))
print("\n".join(pre))
print(cmd)

if os.environ.get("CHECK") == "1":
    print("# --check: verify the model string yourself, e.g. "
          "`cursor-agent models`, `opencode models`, `agy models`", file=w)
PY

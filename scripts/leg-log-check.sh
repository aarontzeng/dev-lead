#!/usr/bin/env bash
# Decide whether a finished leg's log holds a REVIEW, before anyone reads it
# as a verdict. Exit 0 = a report is there; exit 1 = the leg failed and must be
# reported as a missing leg, never as "no findings".
#
# Why: an opencode leg whose read was refused headless ends with the refusal
# and nothing else, and one that hits its timeout ends with nothing at all. In
# both cases the lead's own wrapper appended "exit=0" (or 124), so the output
# file LOOKED like a completed run. Measured 2026-09-22 by the SITL-bench
# session: twice on 12356 (a read outside the cwd refused) and once on 12157
# (+3601 lines, 20-minute timeout, no output).
#
# POSITIVE evidence, not subtraction. The first version stripped known noise
# and counted what was left; three review legs then built error dumps it
# passed and real reviews it failed, one shape after another (2026-09-22).
# A failure has no fixed shape; a dev-lead review does: every brief requires a
# per-claim verdict word. So a log passes only when its report text carries
# one. An error dump never says HOLDS; a real review that quotes an error
# still does.
#
# Usage: leg-log-check.sh <adapter> <log-file> [--expect <regex>]
#   --expect  what a delivered report must contain (Python regex, multiline).
#             Default: the verdict vocabulary dev-lead briefs demand, plus
#             codex adversarial-review's "Verdict:" line. Pass your own when a
#             brief asks for a different format.
#
# Also: cursor --output-format json is read through its `result` field (one
# launch can write TWO objects -- cursor-runtime.md, 2026-09-04 -- so every
# object is parsed); a known refusal shape (opencode's auto-reject, agy's
# "permission check failed") names the cause when no verdict followed it, and
# is only a WARNING when one did.
set -euo pipefail

usage() { echo "usage: leg-log-check.sh <adapter> <log-file> [--expect <regex>]" >&2; exit 2; }
[ $# -eq 2 ] || [ $# -eq 4 ] || usage
adapter=$1; log=$2; expect=""
if [ $# -eq 4 ]; then
  { [ "$3" = "--expect" ] && [ -n "$4" ]; } || usage
  expect=$4
fi

fail() { echo "leg-log-check: FAILED ($adapter): $*" >&2; exit 1; }

[ -f "$log" ] || fail "no log at $log"
[ -s "$log" ] || fail "the log is empty -- the leg produced nothing"

ADAPTER="$adapter" LOG="$log" EXPECT="$expect" python3 - <<'PY'
import json, os, re, sys

adapter, log = os.environ["ADAPTER"], os.environ["LOG"]
expect = os.environ.get("EXPECT") or r"\b(HOLDS|BROKEN|NOT REACHED|FIXED)\b|^\s*Verdict:"
try:
    verdict = re.compile(expect, re.M)
except re.error as e:
    sys.stderr.write("leg-log-check: --expect is not a valid regex: %s\n" % e)
    sys.exit(2)

raw = open(log, encoding="utf-8", errors="replace").read()
text = re.sub(r"\x1b\[[0-9;]*m", "", raw)

def fail(why):
    sys.stderr.write("leg-log-check: FAILED (%s): %s\n" % (adapter, why))
    sys.exit(1)

# cursor: the report is the `result` of a JSON object. Decode every object in
# the stream (there may be two, possibly after a banner line); no object with
# a result means no review, whatever else the stream says.
if adapter == "cursor":
    dec, i, results = json.JSONDecoder(), 0, []
    while True:
        j = text.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(text, j)
        except ValueError:
            i = j + 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("result"), str):
            results.append(obj["result"])
        i = end
    if not results:
        fail("no JSON object with a `result` in the output -- the leg did not deliver a review")
    if len(results) > 1:
        sys.stderr.write("leg-log-check: WARNING (cursor): %d result objects -- a duplicate "
                         "dispatch; reading the longest\n" % len(results))
    text = max(results, key=len)

# Known refusal shapes, matched as the whole lines the CLIs write, so a review
# that merely mentions the words is not one.
REFUSAL = {
    "opencode": (r"^Error: The user rejected permission to use this specific tool call",
                 r"permission requested: .*; auto-rejecting\s*$"),
    "agy": (r"^permission check failed for (command|unsandboxed)",),
}
refused = None
for pat in REFUSAL.get(adapter, ()):
    m = re.search(pat, text, re.M)
    if m:
        refused = m.group(0)[:100]
        break

hits = len(verdict.findall(text))
if not hits:
    if refused:
        fail("a tool call was refused (%r) and no verdict followed -- usually a read "
             "outside what the leg may read; put the brief and context where it can "
             "and rerun" % refused)
    fail("no verdict in the output (expected /%s/) -- the leg did not deliver a "
         "review; an error or an empty run looks exactly like this" % expect)
if refused:
    sys.stderr.write("leg-log-check: WARNING (%s): a tool call was refused (%r); the "
                     "review was written without it -- check what it could not read\n"
                     % (adapter, refused))
print("leg-log-check: OK (%s): %d verdict mark(s)" % (adapter, hits))
PY

#!/usr/bin/env bash
# Decide whether a finished leg's log holds a REVIEW, before anyone reads it
# as a verdict. Exit 0 = a report is there; exit 1 = the leg failed and must be
# reported as a missing leg, never as "no findings".
#
# Why: an opencode leg whose read was refused headless ends with the refusal
# and nothing else, and one that hits its timeout ends with nothing at all. In
# both cases the lead's own wrapper appended "exit=0" (or 124), so the output
# file LOOKED like a completed run. Measured 2026-09-22 by a peer
# session: twice on one change (a read outside the cwd refused) and once on
# another (+3601 lines, 20-minute timeout, no output).
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
# A verdict word counts only in VERDICT POSITION: at the start of a line or
# right after markup/punctuation (`**HOLDS**`, `## Claim 1 -- HOLDS`,
# `**Status**: **BROKEN**`, `Status: HOLDS`, `| HOLDS |`, `[HOLDS]`,
# `**A. FIXED.**`) -- not after an arbitrary label (`Error: BROKEN pipe`; codex,
# 2026-09-22; only Status/Verdict/Result labels count) and not inside a sentence, where an error
# message can carry it ("review stream BROKEN before ..."; codex re-review,
# 2026-09-22). Still a heuristic, and stated as one: it catches a leg that
# delivered no verdicts, not every error that happens to look like one.
expect = os.environ.get("EXPECT") or (
    r"(?:^[ \t*#>|-]*(?:\w{1,3}[.)])?|[*#|(\[\u2014\u2013]|--|-\s)[ \t*]*"
    r"(HOLDS|BROKEN|NOT[ _-]REACHED|NOT FIXED|FIXED)\b"
    r"|^[ \t*#>|-]*(?:Status|Verdict|Result)\**:[ \t*]*(HOLDS|BROKEN|NOT[ _-]REACHED|NOT FIXED|FIXED)\b"
    r"|^\s*Verdict:\s*(approve|needs-attention)\b")
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
        # A SUCCESSFUL result object only: cursor-runtime.md says certify on
        # `result` AND `request_id`, and the CLI marks success itself -- an
        # error can arrive in a result-shaped object too.
        if (isinstance(obj, dict) and isinstance(obj.get("result"), str)
                and obj.get("request_id") and obj.get("is_error") is not True
                and obj.get("subtype") == "success"):
            results.append(obj["result"])
        i = end
    if not results:
        fail("no successful JSON result object (result + request_id, not is_error) -- the leg did not deliver a review")
    if len(results) > 1:
        sys.stderr.write("leg-log-check: WARNING (cursor): %d result objects -- a duplicate "
                         "dispatch; reading all of them\n" % len(results))
    # all of them: the longest is not necessarily the one carrying the verdicts
    text = "\n".join(results)

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

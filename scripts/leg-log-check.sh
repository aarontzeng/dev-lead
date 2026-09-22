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
# Usage: leg-log-check.sh <adapter> <log-file>
#
# Checks, in order: the file exists and is not empty; (opencode) no tool call
# was refused; enough report text remains once the tool-call trace, log lines
# and the wrapper's own exit= line are removed. The threshold is deliberately
# low (a real report is kilobytes): it catches "nothing", not "short".
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: leg-log-check.sh <adapter> <log-file>" >&2; exit 2; }
adapter=$1; log=$2

fail() { echo "leg-log-check: FAILED ($adapter): $*" >&2; exit 1; }

[ -f "$log" ] || fail "no log at $log"
[ -s "$log" ] || fail "the log is empty -- the leg produced nothing"

ADAPTER="$adapter" LOG="$log" python3 - <<'PY' || exit 1
import os, re, sys

adapter, log = os.environ["ADAPTER"], os.environ["LOG"]
raw = open(log, encoding="utf-8", errors="replace").read()

def fail(why):
    sys.stderr.write("leg-log-check: FAILED (%s): %s\n" % (adapter, why))
    sys.exit(1)

# A refused tool call is FATAL only when no report followed it -- the shape
# measured above. A leg can also be refused one command (its read-only config
# denies most of bash) and still deliver a full review; failing that would
# throw away a real leg, so it passes with a warning instead.
refused = None
if adapter == "opencode":
    refused = next((n for n in ("rejected permission", "auto-rejecting") if n in raw), None)

text = re.sub(r"\x1b\[[0-9;]*m", "", raw)
keep = []
for line in text.splitlines():
    s = line.strip()
    if not s:
        continue
    if s.startswith("timestamp=") or re.match(r"^(INFO|DEBUG|WARN)\b", s):
        continue                        # opencode --print-logs lines
    if s[:1] in "→✱✗⚙$":
        continue                        # tool-call trace
    if re.fullmatch(r"exit=\d+", s):
        continue                        # the lead's own wrapper
    if refused and ("rejected permission" in s or "auto-rejecting" in s):
        continue                        # a refusal is not report text, however many
    keep.append(s)
body = "\n".join(keep)
if len(body) < 400:
    if refused:
        fail("a tool call was refused (%r in the log) and no report followed -- "
             "usually a read outside the cwd; put the brief and context inside the "
             "frozen target and rerun" % refused)
    fail("only %d characters of report text once logs and tool calls are removed "
         "-- the leg did not deliver a review" % len(body))
if refused:
    sys.stderr.write("leg-log-check: WARNING (%s): a tool call was refused (%r); the "
                     "report below was written without that call -- check what it "
                     "could not read\n" % (adapter, refused))
print("leg-log-check: OK (%s): %d characters of report text" % (adapter, len(body)))
PY

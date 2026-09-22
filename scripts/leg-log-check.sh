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
#
# Detected by the LINE SHAPES opencode writes, never by the words anywhere: a
# review that quotes "auto-rejecting" while discussing permissions is not a
# refused run (found by two review legs, 2026-09-22 -- one of their own logs
# tripped the substring version).
REFUSAL = (re.compile(r"^Error: The user rejected permission"),
           re.compile(r"^(timestamp=\S+ |(INFO|WARN|DEBUG|ERROR)\b).*permission requested: .*auto-rejecting"))

text = re.sub(r"\x1b\[[0-9;]*m", "", raw)

# cursor --output-format json: the report is the `result` field. A JSON
# document without one is an error payload, not a review.
stripped_all = text.strip()
if stripped_all.startswith("{"):
    try:
        import json
        doc = json.loads(stripped_all)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        text = str(doc.get("result") or "")

# Noise a failed run is made of: log lines, the tool-call trace (marker + a
# space -- a report line starting "$VAR" is kept), the wrapper's exit=, error
# and traceback lines, a run banner, and one-line JSON payloads.
NOISE = (re.compile(r"^timestamp="),
         re.compile(r"^(INFO|DEBUG|WARN|ERROR|TRACE|FATAL)\b"),
         re.compile(r"^[\u2192\u2731\u2717\u2699$] "),
         re.compile(r"^exit=\d+$"),
         re.compile(r"^(Error|error|Traceback|Caused by)\b"),
         re.compile(r"^at \S.*\(.*:\d+(:\d+)?\)$"),
         re.compile(r'^File ".*", line \d+'),
         re.compile(r"^> \S+ \u00b7 "),
         re.compile(r"^\{.*\}$"))

refused = None
keep = []
for line in text.splitlines():
    s = line.strip()
    if not s:
        continue
    if adapter == "opencode" and any(r.search(s) for r in REFUSAL):
        refused = refused or s[:80]
        continue
    if any(r.search(s) for r in NOISE):
        continue
    keep.append(s)
body = "\n".join(keep)
if len(body) < 400:
    if refused:
        fail("a tool call was refused (%r) and no report followed -- usually a "
             "read outside the cwd; put the brief and context inside the frozen "
             "target and rerun" % refused)
    fail("only %d characters of report text once logs, tool calls and error "
         "noise are removed -- the leg did not deliver a review" % len(body))
if refused:
    sys.stderr.write("leg-log-check: WARNING (%s): a tool call was refused (%r); the "
                     "report below was written without that call -- check what it "
                     "could not read\n" % (adapter, refused))
print("leg-log-check: OK (%s): %d characters of report text" % (adapter, len(body)))
PY

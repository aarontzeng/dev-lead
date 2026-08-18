#!/usr/bin/env python3
"""Surface the assertions a round ADDED to prose, so each one gets anchored.

Phase 2's "Changing a statement? grep for its copies" rule catches a sentence
that went STALE. It cannot catch one that was false ON ARRIVAL, and two measured
shapes do exactly that:

  * A right conclusion resting on a wrong mechanism. Measured 2026-08-17: an
    adapter doc said two rows "return the same shape, so this is not an existence
    oracle". Both rows really do carry the same result and build fields -- so the
    sentence read as supported -- but a third field differs between them. The
    conclusion (safe) was correct; the stated reason was not, and a wrong
    mechanism gets reused as a premise by whoever reads it next.

  * A proxy measured and written up as the property. Same day: 64 cores, AVX-512
    and 112 GiB free were all measured and true, and were written as "feasibility
    is not the obstacle". The actual attempt hard-reset the host. Capacity was
    measured; feasibility was asserted.

Neither is reachable by mutation testing: no mutant kills a sentence, because
sentences are not in anything that executes. 20 mutants died the same day while
all three false sentences survived.

Measured yield on the four commits it was tuned against: 9, 4, 19 and 11 flagged
sentences. It caught two of the three known-false sentences, and surfaced all
three copies of one of them (doc, code comment, commit body) -- which then feeds
the "grep for its copies" rule in the same step. It did NOT catch the third
("feasibility ... is not the obstacle"), and no keyword filter can: that failure
is an inference, not a phrasing. Which is why question 2 below prints whether or
not a pattern fired.

Run against its own introducing commit it flagged five sentences, of which three
were dated citations of past incidents (correctly dismissed) and two were real
errors in that very commit: an over-strong "no mutant can" and a miscounted
"all four copies". Both were found by answering question 1, not by the match.

So this script is NOT a verdict, unlike verify-target.sh -- it always exits 0
when it ran. It only makes the risky sentences impossible to skim past. Judging
them is the lead's job, using the two questions it prints. False positives are
expected and cheap: a legitimate absolute ("never push") costs a few seconds to
dismiss. A missed one costs a review round, or ships.

Python rather than bash because it parses diff hunk headers to report real line
numbers; the bash version of that is fragile and hard to test.

Usage: claim-audit.py <dir> <range>
       claim-audit.py "$WORKTREE" "$BASE...HEAD"
"""
from __future__ import annotations

import re
import subprocess
import sys

# Curated from the sentences that actually shipped false, then NARROWED by
# measurement on four real commits. A bare absolute-word filter was tried first
# and rejected on volume: it produced 16, 13 and 38 hits, and legitimate
# absolutes are everywhere in correct technical prose ("never push", "cannot see
# a modified scaffold"). An output nobody reads is worse than no output -- the
# whole point is that a flagged sentence cannot be skimmed past.
#
# Narrowing it to an absolute QUANTIFYING OVER CODE PATHS cut those to 9, 4 and
# 19 while still catching the real defect ("undetectable by design" -- a
# real-transport test detected it; "Called for EVERY read" -- two paths bypassed
# it). The absolute and the thing it quantifies must appear together.
ABSOLUTE = re.compile(
    r"\b(every|all|any|no|never|always|undetectable|impossible|guaranteed)\b"
    r"[^.;]{0,40}?\b(read|reads|write|writes|call|calls|caller|callers|path|paths|"
    r"case|cases|row|rows|request|requests|branch|branches|mutant|mutants)\b"
    r"|(一律|永遠|完全|絕不|不可能)[^。;]{0,20}?(讀取|寫入|呼叫|路徑|情況|請求)", re.I)
SAMENESS = re.compile(
    r"\b(same|identical|indistinguishable|equivalent|no different|unchanged)\b"
    r"|相同|一樣|無法分辨|等價", re.I)

PROSE_SUFFIX = (".md", ".markdown", ".txt", ".rst", ".adoc")
# Comment openers, deliberately shallow: this decides what to LOOK at, and
# over-including code is harmless while under-including prose is the failure.
COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--|;|\"\"\"|''')")


def git(dir_: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", dir_, *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"claim-audit: git {' '.join(args)} failed: {r.stderr.strip()}\n")
        sys.exit(2)                      # 2 = could not run, distinct from 0 = ran
    return r.stdout


def added_prose(dir_: str, rng: str) -> list[tuple[str, int, str]]:
    """(path, line, text) for prose lines this range added."""
    out: list[tuple[str, int, str]] = []
    path, lineno = None, 0
    for raw in git(dir_, "diff", "--no-color", "--unified=0", rng).splitlines():
        if raw.startswith("+++ b/"):
            path, lineno = raw[6:], 0
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++") and path:
            body = raw[1:]
            if path.endswith(PROSE_SUFFIX) or COMMENT.match(body):
                out.append((path, lineno, body.strip()))
            lineno += 1
    return out


def commit_messages(dir_: str, rng: str) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for sha in git(dir_, "log", "--format=%H", rng).split():
        for i, line in enumerate(git(dir_, "log", "-1", "--format=%B", sha).splitlines(), 1):
            if line.strip():
                out.append((f"commit {sha[:9]}", i, line.strip()))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("Usage: claim-audit.py <dir> <range>\n")
        return 2
    dir_, rng = sys.argv[1], sys.argv[2]
    hits = []
    for path, lineno, text in added_prose(dir_, rng) + commit_messages(dir_, rng):
        kinds = []
        if ABSOLUTE.search(text):
            kinds.append("absolute")
        if SAMENESS.search(text):
            kinds.append("sameness")
        if kinds:
            hits.append((path, lineno, "+".join(kinds), text))

    if not hits:
        print("claim-audit: no absolute or sameness claims added in this range.")
        return 0

    print(f"claim-audit: {len(hits)} added sentence(s) carry a shape that shipped "
          f"false before. Answer BOTH questions for each, then proceed:\n")
    print("  1. If this sentence were false, which test goes red?")
    print("     -> cannot name one: pin the premise with a test, or downgrade the")
    print("        sentence to what was observed. Do not leave it asserting.")
    print("  2. Did I measure the property, or a proxy for it?")
    print("     -> capacity is not feasibility; a passing suite is not coverage.\n")
    width = max(len(f"{p}:{n}") for p, n, _, _ in hits)
    for path, lineno, kind, text in hits:
        print(f"  {f'{path}:{lineno}':<{width}}  [{kind}]  {text[:120]}")
    print(f"\nclaim-audit: {len(hits)} to resolve. This is a worklist, not a "
          f"failure -- exit 0 either way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

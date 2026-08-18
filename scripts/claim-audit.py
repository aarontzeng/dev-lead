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
sentences -- with the ORIGINAL noun list, which was drawn from those same four
commits by one author. Review called that sample overfitted and named misses it
predicted ("all workers", "every packet", "guaranteed zero allocations"); all
three did miss. The list was widened, and the volume cost re-measured across the
eight most recent commits rather than argued: 3/19/0/2/1/1/1/1 became
3/20/0/2/1/1/1/1 -- one added hit.

Review then found the larger gap: only ABSOLUTE-then-noun was matched, so
"Requests always succeed" and "Reads are never retried" -- the ordinary way to
write the claim -- were missed entirely. Both orders now match, and that is the
expensive change: across the eight most recent commits the total went 54 -> 67,
and the worst single commit 21 -> 29. All eight newly flagged sentences on that
commit were read before accepting the trade, and each is a real absolute about
behaviour ("permits an ENTRY, never loosens anything else", "would permit every
child", "the file would always agree with"). So the added volume is recall, not
noise -- but 29 on one commit is close to the volume the original narrowing
existed to avoid, and if a round ever rubber-stamps this list, that number is
where to look first.

None of this is a guarantee: eight commits from one repository cannot show the
pattern holds its volume on prose written differently, and there is no volume
regression test. The bound is still a list, and a noun absent from it is still a
silent miss.

It caught two of the three known-false sentences, and surfaced all
three copies of one of them (doc, code comment, commit body) -- which then feeds
the "grep for its copies" rule in the same step. It did NOT catch the third
("feasibility ... is not the obstacle"), and no keyword filter can: that failure
is an inference, not a phrasing. Which is why question 2 is asked alongside
question 1 on every hit, whichever pattern class produced it.

Known hole, and it is the unlintable shape's own case: a run with NO hits prints
neither question, so the sentence question 2 exists for gets nothing on a clean
range. Closing it means printing the questions unconditionally and accepting the
noise on every silent run; that trade has not been made.

Second known hole, in what it LOOKS at: in a code file a line counts as prose
only if it opens a comment or trails one. A line inside a docstring or a /* */
block whose opener is on an earlier line is not seen. The diff carries one line
of context, which is enough to rejoin a wrapped sentence but not to find an
opener further up, and deciding that a line sits inside a comment needs the
file. So this script cannot read its own module docstring --
including this paragraph. `test_claim_audit_parsing` asserts that gap, so it
cannot close silently while this note goes stale.

Run against its own introducing commit it flagged five sentences, of which three
were dated citations of past incidents (correctly dismissed) and two were real
errors in that very commit: an over-strong "no mutant can" and a miscounted
"all four copies". Both were found by answering question 1, not by the match.

So this script is NOT a verdict, unlike verify-target.sh -- it exits 0 whenever
it ran, hits or no hits, and 2 ONLY when it could not run: wrong arity, no git
on PATH, or a git command that failed. That split is what the exit code means;
undecodable bytes in a diff are replaced rather than raised, so they no longer
escape it. It only makes the risky sentences impossible to skim past. Judging
them is the lead's job, using the two questions it prints. False positives are
expected and cheap: a legitimate absolute ("never push") costs a few seconds to
dismiss. A missed one costs a review round, or ships.

Python rather than bash because it parses diff hunk headers to report real line
numbers; the bash version of that is fragile and hard to test.

Usage: claim-audit.py <dir> <range>
       claim-audit.py "$WORKTREE" "$BASE...HEAD"

A two- or three-dot range is resolved through merge-base, and its commit
messages are audited along with its prose. A BARE REVISION means "the working
tree against that revision" and audits prose only -- there are no commits in
that span, so no commit message is read. (It used to run `git log <rev>`, which
scanned the entire history behind it.)
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
# 19. It keeps "Called for EVERY read" (two paths bypassed it). It does NOT keep
# "undetectable by design" -- "design" is not a code path, so only the rejected
# bare filter ever matched that one; the review leg caught it instead. The
# absolute and the thing it quantifies must appear together.
#
# The noun list is the recall bound, and it is a list, so anything absent is a
# silent miss: "all WORKERS", "every PACKET", "guaranteed zero ALLOCATIONS" all
# sailed through the first version, which had been drawn from one author's four
# commits. Widened below; see the measured cost in this module's docstring.
_L = r"(?<![A-Za-z0-9_])"                # ASCII word start ...
_R = r"(?![A-Za-z0-9_])"                 # ... and end. "\b" counts CJK as a word
                                         # character, so "Every請求" had no
                                         # boundary after "Every" and never matched.
_GAP = r"[^.;!?。；！？]"                 # must not span a sentence end, either script
_ABS_EN = r"(every|all|any|no|never|always|undetectable|impossible|guaranteed)"
_ABS_ZH = r"(一律|永遠|完全|絕不|不可能)"
_NOUN_EN = (r"(read|reads|write|writes|call|calls|caller|callers|path|paths|"
            r"case|cases|row|rows|request|requests|branch|branches|mutant|mutants|"
            r"worker|workers|packet|packets|allocation|allocations|observer|observers|"
            r"handler|handlers|endpoint|endpoints|node|nodes|thread|threads|"
            r"field|fields|record|records|entry|entries|message|messages|"
            r"query|queries|response|responses|input|inputs|client|clients)")
_NOUN_ZH = r"(讀取|寫入|呼叫|路徑|情況|請求)"
# Both script combinations AND both word orders. "Every請求" needs its CJK noun
# written without an ASCII boundary, since the character before it is the
# absolute's own last letter. The reverse order is not thoroughness for its own
# sake: "Requests always succeed" and "Reads are never retried" are the ordinary
# way to write these claims, and matching only absolute-then-noun missed every
# one of them.
_ABS = (_L + _ABS_EN + _R, _ABS_ZH)
_NOUN = (_L + _NOUN_EN + _R, _NOUN_ZH)
ABSOLUTE = re.compile("|".join(
    [a + _GAP + r"{0,40}?" + n for a in _ABS for n in _NOUN]
    + [n + _GAP + r"{0,40}?" + a for a in _ABS for n in _NOUN]
), re.I)
SAMENESS = re.compile(
    _L + r"(same|identical|indistinguishable|equivalent|no different|unchanged)" + _R
    + r"|相同|一樣|無法分辨|等價", re.I)

# A hard wrap splits a sentence mid-flight, so the first half does NOT end in
# terminal punctuation. That is what separates "rejoin this wrapped sentence"
# from "these are two sentences, and the first one was already here".
SENTENCE_END = re.compile(r"[.;!?。；！？][\"')\]]*\s*$")

PROSE_SUFFIX = (".md", ".markdown", ".txt", ".rst", ".adoc")
# Comment openers, deliberately shallow: this decides what to LOOK at, and
# over-including code is harmless while under-including prose is the failure.
COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--|;|\"\"\"|''')")
# By that same rule, a comment TRAILING code is prose too. Requiring the opener
# at line start meant `run()  # every request is accepted` was never looked at.
# Neither side may require whitespace: `run() #every ...` and `run();// every
# ...` are both valid and both were missed. `//` is excluded after a colon so a
# bare URL is not read as a comment.
TRAILING_COMMENT = re.compile(r"\S\s*(#|(?<!:)//)")


def is_prose(path: str, body: str) -> bool:
    """Whether this line is something a claim could live in."""
    return bool(path.endswith(PROSE_SUFFIX) or COMMENT.match(body)
                or TRAILING_COMMENT.search(body))


def unquote_path(p: str) -> str:
    """Decode the C-quoted path form git uses for non-ASCII and specials.

    With core.quotePath at its default, `文檔.md` arrives as
    `"b/\\346\\226\\207\\346\\252\\224.md"`. Requiring a literal `b/` prefix
    made that header match nothing, so the file was skipped in full and the
    range still reported clean -- the silent-miss shape, on a repository whose
    filenames are routinely CJK.
    """
    if not (len(p) > 1 and p.startswith('"') and p.endswith('"')):
        return p
    body, out, i = p[1:-1], bytearray(), 0
    simple = {"n": b"\n", "t": b"\t", "r": b"\r", "a": b"\a", "b": b"\b",
              "f": b"\f", "v": b"\v", '"': b'"', "\\": b"\\"}
    while i < len(body):
        if body[i] != "\\":
            out.extend(body[i].encode()); i += 1
        elif body[i + 1] in "01234567":
            out.append(int(body[i + 1:i + 4], 8)); i += 4
        else:
            out.extend(simple.get(body[i + 1], body[i + 1].encode())); i += 2
    return out.decode("utf-8", "replace")


def git(dir_: str, *args: str) -> str:
    # errors="replace" so one invalid UTF-8 byte in a diff or a commit message
    # degrades that character instead of raising out of the exit-code contract.
    try:
        r = subprocess.run(["git", "-C", dir_, *args],
                           capture_output=True, text=True, errors="replace")
    except OSError as e:                 # no git on PATH, dir_ not usable
        sys.stderr.write(f"claim-audit: cannot run git: {e}\n")
        sys.exit(2)
    if r.returncode != 0:
        sys.stderr.write(f"claim-audit: git {' '.join(args)} failed: {r.stderr.strip()}\n")
        sys.exit(2)                      # 2 = could not run, distinct from 0 = ran
    return r.stdout


def resolve_range(dir_: str, rng: str) -> tuple[str, str | None]:
    """Endpoints the diff and the log provably share.

    "git diff A...B" scans merge-base(A,B)..B, but "git log A...B" lists BOTH
    sides. On a range whose ends are not ancestors, prose added on A was never
    read while A's commit messages were -- so a claim could be reported at a
    line the diff never showed, or missed entirely. Resolving the endpoints once
    removes the disagreement: the diff gets "base tip", the log "base..tip".
    """
    sep = "..." if "..." in rng else (".." if ".." in rng else None)
    if sep:
        a_raw, b_raw = rng.split(sep, 1)
        if not a_raw and not b_raw:      # a bare ".." -- git rejects it too
            sys.stderr.write(f"claim-audit: {rng!r} names no endpoints\n")
            sys.exit(2)
        a, b = a_raw or "HEAD", b_raw or "HEAD"
        # BOTH forms resolve through merge-base. For "A..B" that deviates from
        # git, where diff would compare the tips directly -- but this tool asks
        # "what prose did this range ADD", and against a diverged A the tip
        # comparison answers a different question: a sentence A DELETED shows up
        # as added by B. merge-base..B is the honest span, and for the ancestor
        # case the two are identical anyway.
        return git(dir_, "merge-base", a, b).strip(), b
    return rng, None                     # single rev: diff the worktree, no commits


def added_prose(dir_: str, base: str, tip: str | None) -> list[tuple[str, int, str]]:
    """(path, line, text) for prose lines this range added.

    Headers are recognised only BEFORE the first @@ of a file. Inside a hunk a
    line opening "+++" is content (a "+" marker on text that itself starts
    "++"), and "+++ b/x.md" is a path only in a header. Deciding by position
    rather than by prefix is what stops a prose line beginning "+++" from being
    silently dropped -- taking every later line number in that hunk with it --
    and stops a content line reading "++ b/fake.md" from being adopted as the
    current path.
    """
    out: list[tuple[str, int, str, bool]] = []
    path, lineno, in_hunk = None, 0, False
    # Prefixes forced: under diff.noprefix=true git emits "+++ docs/x.md", which
    # matches no "+++ b/" and loses every path, while a real "b/docs/x.md" would
    # be reported as "docs/x.md".
    #
    # One line of context, not zero: when a wrapped sentence has only its SECOND
    # line edited, -U0 hands over that line alone and the "Every" it belongs to
    # stays invisible, so the claim cannot be reassembled. Context lines come
    # back marked added=False -- they are joinable, never reportable on their
    # own, since this range did not write them.
    args = ["diff", "--no-color", "--no-ext-diff", "--unified=1",
            "--src-prefix=a/", "--dst-prefix=b/", base]
    if tip is not None:
        args.append(tip)
    for raw in git(dir_, *args).splitlines():
        if raw.startswith("diff --git "):
            path, lineno, in_hunk = None, 0, False
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno, in_hunk = (int(m.group(1)) if m else 0), True
        elif not in_hunk:
            if raw.startswith("+++ "):
                p = unquote_path(raw[4:])
                path = p[2:] if p.startswith("b/") else None   # /dev/null -> None
        elif raw.startswith("+") and path:
            body = raw[1:]
            if is_prose(path, body):
                out.append((path, lineno, body.strip(), True))
            lineno += 1
        elif raw.startswith(" ") and path:
            body = raw[1:]
            if is_prose(path, body):
                out.append((path, lineno, body.strip(), False))
            lineno += 1
        elif raw.startswith(" "):
            # --unified=0 asks for no context, but diff.interHunkContext can
            # still merge neighbouring hunks and carry the lines between them.
            # A context line occupies a line in the NEW file, so not counting it
            # shifted every later claim in that hunk -- the same wrong-line
            # failure the header fix above was for.
            lineno += 1
    return out


def commit_messages(dir_: str, base: str, tip: str | None) -> list[tuple[str, int, str, bool]]:
    out: list[tuple[str, int, str, bool]] = []
    if tip is None:                      # worktree diff: the range holds no commits
        return out
    for sha in git(dir_, "log", "--format=%H", f"{base}..{tip}").split():
        for i, line in enumerate(git(dir_, "log", "-1", "--format=%B", sha).splitlines(), 1):
            if line.strip():
                out.append((f"commit {sha[:9]}", i, line.strip(), True))
    return out


def excerpt(text: str, width: int = 120) -> str:
    """A window containing the CLAIM, not the first `width` characters.

    A long sentence whose absolute lands past the cut was displayed with the
    matched phrase chopped off, so the entry showed neutral introductory prose
    and read as a false positive. Neither question can be answered about a
    claim the reader cannot see, and joined wrapped lines are the longest
    entries there are.
    """
    if len(text) <= width:
        return text
    spans = [(m.start(), m.end())
             for m in (ABSOLUTE.search(text), SAMENESS.search(text)) if m]
    if not spans:
        return text[:width] + "…"
    # ABSOLUTE allows 40 characters BETWEEN its two terms, so a match starting
    # comfortably inside the window can still END outside it. A guessed offset
    # from start() cut the noun off the claim; ask where the match actually ends.
    at, end = min(spans)
    if end <= width:
        return text[:width] + "…"
    lead = max(0, at - 30)
    tail = "…" if lead + width < len(text) else ""
    return "…" + text[lead:lead + width] + tail


def classify(text: str) -> list[str]:
    kinds = []
    if ABSOLUTE.search(text):
        kinds.append("absolute")
    if SAMENESS.search(text):
        kinds.append("sameness")
    return kinds


def main() -> int:
    # The flagged sentence is the output, and it can carry any byte git gave us.
    # Under a strict stdout encoding (PYTHONIOENCODING=ascii:strict, some CI
    # containers) printing it raised UnicodeEncodeError and exited 1 -- which
    # would break the exit contract in the same way the decode side did.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable stream
        pass
    if len(sys.argv) != 3:
        sys.stderr.write("Usage: claim-audit.py <dir> <range>\n")
        return 2
    dir_, rng = sys.argv[1], sys.argv[2]
    base, tip = resolve_range(dir_, rng)
    items = added_prose(dir_, base, tip) + commit_messages(dir_, base, tip)
    hits, i = [], 0
    while i < len(items):
        path, lineno, text, added = items[i]
        kinds = classify(text)
        # The join is tried BEFORE accepting a physical-line hit. A hard wrap
        # splits a claim across two lines and the halves can carry DIFFERENT
        # classes -- "Every response from this route" / "is identical for
        # authenticated clients." -- so reporting the added half by itself shows
        # a claim stripped of its predicate and never notices the second class
        # at all. Only ADJACENT lines join, a blank line breaks the run, at
        # least one half must be new, and SENTENCE_END holds this to genuine
        # wraps so a complete standing sentence is still reported on its own.
        if i + 1 < len(items):
            p2, l2, t2, a2 = items[i + 1]
            k2 = classify(t2)
            if (p2 == path and l2 == lineno + 1
                    and (added or a2)            # at least one half is NEW
                    and not (k2 and a2)          # that half reports itself next
                    and not SENTENCE_END.search(text)):   # a wrap, not two sentences
                joined = f"{text} {t2}"
                kj = classify(joined)
                if kj:
                    hits.append((path, lineno, "+".join(kj) + "/wrapped", joined))
                    i += 2
                    continue
        if kinds and added:
            hits.append((path, lineno, "+".join(kinds), text))
        i += 1

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
        print(f"  {f'{path}:{lineno}':<{width}}  [{kind}]  {excerpt(text)}")
    print(f"\nclaim-audit: {len(hits)} to resolve. This is a worklist, not a "
          f"failure -- exit 0 either way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

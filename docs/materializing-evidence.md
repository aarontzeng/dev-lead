# Materializing evidence for a review leg

Every review skill in this suite freezes its target with `freeze-target.sh` and
certifies it with `verify-target.sh` before and after the run. When a review
needs material the frozen tree does not contain — another revision's copy of a
file, a sibling change's version, a merge-base baseline — the lead has to put
that material somewhere the delegate can read. **Where it goes, and what
guarantees it carries, is the same problem for every family**, which is why it
lives here rather than in one skill's references.

Per-family mechanics (how the directory is granted, which commands a brief may
name) stay in that family's skill and runtime file. This page carries the part
that does not vary.

## Where it goes — and how it reaches the delegate

Two separate questions. The first has one answer for every family; the second
does not.

**Never the frozen target**, unless the leg genuinely cannot put it elsewhere.
`verify-target.sh` refuses to certify a directory with any
`git status --porcelain=v1` output; a leg that must keep scaffolding inside the
target declares those exact paths as trailing arguments, which permits them and
nothing else.

Do not rely on a filename "not showing up": the check runs with
`core.excludesFile=/dev/null`, so the operator's **personal** global gitignore
cannot decide what counts as clean. That is not hypothetical — it is why this
page exists. A procedure that dropped `REVIEW-*.md` files into frozen targets
certified fine on the machine that wrote it and would have failed everywhere
else.

**Delivery is family-specific — do not assume `--add-dir`.**

| family | how evidence reaches the delegate |
|---|---|
| `agy` | second `--add-dir "$RUN_DIR/evidence"` (the flag is repeatable) |
| `claude` | the launch cwd is the frozen target; pass absolute paths in the prompt |
| `codex` | the companion runs in the frozen worktree; absolute paths in the focus text |
| `cursor` | `--trust` grants the launch cwd; absolute paths in the prompt |
| `grok` | `--tools` allowlists reads; absolute paths in the prompt file |
| `opencode` | **cannot take out-of-project paths at all** — see below |

`opencode` auto-rejects anything outside the project root
(`permission=external_directory`, fatal headless: measured, died in seven
seconds having read nothing) and has no `--add-dir`. Its evidence therefore has
to live *inside* the project. **Declare those paths to the certification**
rather than excusing them:

```bash
verify-target.sh "$REVIEW_TARGET_DIR" "$REVIEW_HEAD" opencode.json REVIEW-CLAIMS.md
```

A declared path is permitted **by entry, not by content** — `?? opencode.json`
reads the same after a rewrite — and it must still be present, so scaffolding
that vanished mid-run fails too. Pair it with a lead-held digest when the
content matters. Never hand-roll a substitute by comparing porcelain lines
yourself: that is what this feature replaced, and the hand-rolled version could
not see a modified scaffold at all.

**Keep the run's own output directory writable.** Every family redirects its log
into `$RUN_DIR` (`$RUN_DIR/review.log`, `review.out`, `review.json`), and the
prompt file is written there too. Making `$RUN_DIR` itself read-only fails the
shell redirection *before* the delegate launches. Put the evidence in
`$RUN_DIR/evidence/` and freeze only that:

```bash
mkdir -p "$RUN_DIR/evidence"
# … materialize into $RUN_DIR/evidence/ …
chmod -R a-w "$RUN_DIR/evidence"     # not $RUN_DIR — the log still needs writing
```

## The four properties

Check these, not the syntax of the example below:

1. **Whole files by default.** A section extracted by delimiter matching
   cannot be verified by delimiter matching: a fenced example containing
   a line that looks like the closing heading truncates the extraction,
   and an "did it end at the right heading" check then passes. Markdown
   contract pages are mostly fenced blocks. If a range is unavoidable,
   resolve and read its line numbers, then pass explicit `A,Bp` bounds.
2. **Explicit inventory**, not a glob — a `*.md` digest silently ignores
   a `handler.py` you also placed there.
3. **Immutable for the duration**, not merely equal at the endpoints:
   make `$RUN_DIR/evidence` unwritable before launch — **not `$RUN_DIR`
   itself**, which every leg still needs writable for its own log and prompt
   file. Edit → read → restore passes a before/after digest.
4. **Digest held by the lead**, never written into a granted directory,
   and re-checked after the run alongside the `verify-target.sh`
   bracket — which only inspects `$REVIEW_TARGET_DIR`.

```bash
set -o pipefail
EVIDENCE=("$RUN_DIR/evidence/parent-file.md")
git -C "$REPO" show "$OTHER_REV":path/to/file.md > "${EVIDENCE[0]}"
[ -s "${EVIDENCE[0]}" ] || { echo "no evidence: wrong rev or path" >&2; exit 1; }
EVIDENCE_SHA=$(sha256sum "${EVIDENCE[@]}" | sha256sum)   # lead-held, never on disk
chmod -R a-w "$RUN_DIR/evidence"                         # NOT $RUN_DIR: the log needs writing

# `if`, not a bare call + `$?` — a caller's `set -e` would exit before the
# capture and leave $RUN_DIR unwritable (agy-runtime.md, retry section).
if agy -p "$(cat "$RUN_DIR/prompt.md")" --model <gemini-tier> --mode plan --sandbox \
       --add-dir "$REVIEW_TARGET_DIR" --add-dir "$RUN_DIR" --print-timeout 15m0s
then status=0; else status=$?; fi

chmod -R u+w "$RUN_DIR/evidence"
[ "$(sha256sum "${EVIDENCE[@]}" | sha256sum)" = "$EVIDENCE_SHA" ] \
  || { echo "evidence changed during the run — discard this review" >&2; exit 1; }
[ "$status" -eq 0 ] || { echo "agy exited $status — the review did not complete" >&2; exit "$status"; }
```

**The rule these share, worth carrying to any guard in this suite: a
check stored where its subject can modify it is decoration; a guard that
cannot fail on the input it screens is decoration; a check that samples
only the endpoints says nothing about the interval; and cleanup that
runs after a failure will report the cleanup's success as the run's.**
Seven review rounds went into this one paragraph, each fix correct about
the defect in front of it and wrong one level down. Two of the last
three were caught by `references/agy-runtime.md` already saying the
answer — the allow-list prerequisite, and capturing exit status through
an `if`. **Read the runtime file before writing an example here**; this
skill's own opening says to, and a snippet that contradicts it is a bug
no matter how carefully the surrounding prose is argued. Ask where the
record lives, what input would make the check fire, over what window it
holds, whose exit status you are returning — and whether the runtime
file already answered the question.

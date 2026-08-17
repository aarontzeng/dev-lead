#!/usr/bin/env python3
"""Repo invariant linter — zero dependencies, run: python3 scripts/lint.py

Every check here guards against a failure this repo has ACTUALLY shipped
(or a reviewer actually caught), not a hypothetical:

  structure     a family missing its runtime reference (codex shipped without one)
  frontmatter   a SKILL.md whose name/dir disagree would break skill loading
  manifest      plugin.json must stay valid JSON with the required keys
  version       plugin.json's version is the ONLY version a consumer sees, and
                nine commits shipped past v0.1.0 with it unchanged -- a new
                install requirement and a behavior change in all four review
                skills, handed to installed users under the version they had
  links         a placeholder link (https://github.com/ with no repo) shipped once
  paths         a skill runs with the TARGET repo as cwd, so a bare `scripts/…`
                or `docs/…` points at the user's project: freeze-target.sh
                shipped as `scripts/freeze-target.sh` and exited 127 everywhere
                except this repo, where the cwd happened to hide it
  fences        unbalanced ``` renders half a file as code
  mermaid       `;` ends a statement in a sequenceDiagram/classDiagram, so a
                semicolon in message text renders the whole diagram as an
                error box -- docs/workflow.md shipped two
  var-order     $RUN_DIR was used 12 lines before its mktemp, in two skills
  tracked       an environment-global gitignore silently ate templates/AGENTS.md
  sentinel      a skill's prose once allowed same-family review, contradicting
                the suite's headline rule
  frozen        every review skill must state the frozen-target rule, in the
                SAME words -- the four shipped four different ones ("or
                implementation worktree" / "whenever possible" / "preferred"
                / silence) while all four used $REVIEW_TARGET_DIR without
                ever saying where it comes from
  delegates     review findings must keep Grok MCP calls denied and write
                delegates must use the fail-closed remote-ref checker, not a
                raw diff that merely reports a changed snapshot
  audit         Cursor delegate output must preserve its request identity, and
                agy's displayed Gemini model tier must match its effort flag
  families      data/families.json must stay consistent with the skills on
                disk, and a multi-family adapter must SAY so where a lead
                reads it (agy serving both Gemini and Claude is exactly the
                fact that makes adapter != family)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("claude", "codex", "agy", "opencode", "grok", "cursor")
ERRORS = []
#: Things a check could NOT decide. Not failures -- but "all checks passed" is a
#: lie when a check guarded nothing, and silence is how that lie gets believed.
NOTES = []


def err(path, msg):
    ERRORS.append(f"{path}: {msg}")


def note(msg):
    NOTES.append(msg)


def md_files():
    return sorted(
        p for p in ROOT.rglob("*.md")
        if ".git" not in p.parts
    )


def rel(p):
    return p.relative_to(ROOT)


# ---- structure: 4 families x 2 roles + runtime reference + orchestrator ----
def check_structure():
    for fam in FAMILIES:
        for required in (
            ROOT / "skills" / f"{fam}-implement" / "SKILL.md",
            ROOT / "skills" / f"{fam}-adversarial-review" / "SKILL.md",
            ROOT / "skills" / f"{fam}-adversarial-review" / "references" / f"{fam}-runtime.md",
        ):
            if not required.is_file():
                err(rel(required), "missing (breaks the 4-family x 2-role symmetry)")
    if not (ROOT / "skills" / "dev-lead" / "SKILL.md").is_file():
        err("skills/dev-lead/SKILL.md", "missing orchestrator skill")


# ---- frontmatter: name matches directory, description present ----
def check_frontmatter():
    for skill_md in sorted(ROOT.glob("skills/*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            err(rel(skill_md), "no frontmatter block")
            continue
        head = text.split("---", 2)[1]
        m = re.search(r"^name:\s*(\S+)\s*$", head, re.M)
        if not m:
            err(rel(skill_md), "frontmatter has no name:")
        elif m.group(1) != skill_md.parent.name:
            err(rel(skill_md), f"frontmatter name '{m.group(1)}' != directory '{skill_md.parent.name}'")
        if not re.search(r"^description:\s*\S", head, re.M):
            err(rel(skill_md), "frontmatter has no description:")


# ---- manifest ----
def check_manifest():
    manifest = ROOT / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        err(".claude-plugin/plugin.json", "missing")
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(rel(manifest), f"invalid JSON: {e}")
        return
    for key in ("name", "description", "version"):
        if not data.get(key):
            err(rel(manifest), f"missing/empty '{key}'")

    # the explicit skills list must match the skills on disk, both ways --
    # listing them buys determinism, and this check is what stops the list
    # silently drifting when a skill is added or renamed
    listed = {s.rstrip("/").split("/")[-1] for s in data.get("skills", [])}
    on_disk = {p.parent.name for p in ROOT.glob("skills/*/SKILL.md")}
    for missing in sorted(on_disk - listed):
        err(rel(manifest), f"skill '{missing}' exists on disk but is not in the skills list")
    for phantom in sorted(listed - on_disk):
        err(rel(manifest), f"skills list names '{phantom}', which has no skills/{phantom}/SKILL.md")

    # marketplace manifest: this repo is its own marketplace
    mkt = ROOT / ".claude-plugin" / "marketplace.json"
    if not mkt.is_file():
        err(".claude-plugin/marketplace.json", "missing (the repo is its own marketplace)")
        return
    try:
        mdata = json.loads(mkt.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(rel(mkt), f"invalid JSON: {e}")
        return
    for key in ("name", "owner", "description", "plugins"):
        if not mdata.get(key):
            err(rel(mkt), f"missing/empty '{key}'")
    names = {p.get("name") for p in mdata.get("plugins", [])}
    if data.get("name") and data["name"] not in names:
        err(rel(mkt), f"does not list this repo's own plugin '{data['name']}' "
                      f"(lists {sorted(n for n in names if n)}) — "
                      "`claude plugin install <name>@<marketplace>` would fail")


# ---- version: the manifest's version is the only one a consumer sees ----
# Measured failure: nine commits landed after v0.1.0 -- among them the
# DEV_LEAD_ROOT install requirement and one rewritten review-target rule in all
# four review skills -- while plugin.json still said 0.1.0. This repo is its own
# marketplace, so `claude plugin marketplace update` hands those changes to an
# installed user under the version they already have. Nothing else in the tree
# carries a version, so nothing else could have noticed.
#
# Two rules. They fail differently and neither subsumes the other:
#   1. HEAD carries vX.Y.Z  ->  the manifest IN THAT TAG'S TREE must say
#      exactly X.Y.Z. Guards the mechanical slip of tagging a release without
#      bumping, in both directions (a manifest AHEAD of its own tag passes
#      rule 2 and is still a mislabeled release). Read from the tag rather
#      than the working copy, or the bump rule 2 demands on the very next
#      commit gets reported as mislabeling the tag it is moving away from.
#   2. HEAD is past the newest reachable v* tag  ->  plugin.json must be
#      strictly greater than it. This is the drift above, and rule 1 is blind
#      to it: across those nine commits HEAD carried no tag at all.
#
# Rule 2 costs one edit per release cycle -- the first commit after a tag must
# choose the next version. That choice is the point.
#
# Both rules need tags in the tree, and a tagless clone makes them pass
# VACUOUSLY: `git describe` finding nothing leaves nothing to report, so the
# check goes green having guarded nothing. CI's checkout runs at fetch-depth: 0
# for exactly this reason (.github/workflows/ci.yml) -- revert that to the
# default shallow fetch and this check keeps passing while it stops working.
#
# CI is covered. A LOCAL run is not, and that is where it was measured biting:
# 2026-08-15, an agent ran this lint twice in a marketplace clone whose tags had
# never been fetched, read "all checks passed" both times, and committed two
# changes past v0.3.2 with the manifest still declaring 0.3.2. One `git fetch`
# later the same tree failed the same check. The paragraph above had predicted
# it exactly -- prose in the source does not reach whoever is reading stdout.
#
# So a tagless run stays a PASS (a fresh clone is not a defect, and the tagless
# case is deliberately not an error), but it no longer stays SILENT: it emits a
# note saying which rule went unchecked and what to run. The distinction being
# preserved is "this check did not fail" versus "this check did not happen".
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver(text):
    """(major, minor, patch), or None if `text` is not exactly X.Y.Z."""
    m = VERSION_RE.match(text or "")
    return tuple(int(g) for g in m.groups()) if m else None


def _git(*args):
    """stdout, or None when git fails — no tags, no repo, a shallow clone."""
    p = subprocess.run(["git", "-C", str(ROOT), *args],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def check_version():
    manifest = ROOT / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return                                  # check_manifest() reported it
    try:
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except json.JSONDecodeError:
        return                                  # ditto
    version = _semver(declared)
    if version is None:
        err(rel(manifest), f"version '{declared}' is not X.Y.Z — the tag "
                           "comparison below and any consumer's own version "
                           "compare both need semver")
        return

    tagged = [t for t in (_git("tag", "--points-at", "HEAD") or "").splitlines()
              if t.startswith("v") and _semver(t[1:])]
    if tagged:
        for tag in tagged:
            # what that release DECLARES lives in the tag's tree; the working
            # copy has legitimately moved on by the time anyone reads it
            blob = _git("show", f"{tag}:.claude-plugin/plugin.json")
            if blob is None:
                err(rel(manifest), f"tag '{tag}' carries no plugin.json — "
                                   "nothing in that release declares what it is")
                continue
            try:
                shipped = json.loads(blob).get("version")
            except json.JSONDecodeError:
                err(rel(manifest), f"tag '{tag}' carries an unparseable plugin.json")
                continue
            if _semver(shipped) != _semver(tag[1:]):
                err(rel(manifest),
                    f"tag '{tag}' ships a manifest declaring '{shipped}' — "
                    "that release is labelled as something it is not")
        return                                  # rule 2 does not apply to a tag

    released = _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    if not released:
        note("version: no v* tag is reachable from HEAD, so the "
             "'manifest must be ahead of the last release' rule was NOT "
             "checked -- this run guarded nothing. A shallow or tag-less "
             "clone looks identical to a genuinely untagged repo. "
             "`git fetch --tags` and re-run before trusting a green here.")
        return
    previous = _semver(released[1:])
    if previous and version <= previous:
        err(rel(manifest),
            f"version '{declared}' is not ahead of released tag '{released}', "
            "and this commit is past it — `marketplace update` would hand these "
            "changes to an installed user under the version they already have")


# ---- links: relative links resolve; no accidental placeholders ----
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
PLACEHOLDERS = ("](https://github.com/)", "<you>", "TODO", "FIXME")


def check_links():
    for md in md_files():
        text = md.read_text(encoding="utf-8")
        for bad in PLACEHOLDERS:
            if bad in text:
                line = text[: text.index(bad)].count("\n") + 1
                err(rel(md), f"line {line}: placeholder '{bad}' left in")
        for m in LINK_RE.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            resolved = (md.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                line = text[: m.start()].count("\n") + 1
                err(rel(md), f"line {line}: relative link '{target}' does not resolve")


# ---- paths: a suite path must resolve from the SUITE's tree, not the cwd ----
# The skills are read from wherever the suite is installed (plugin cache, a
# clone) while cwd is the repo being worked on. `scripts/freeze-target.sh`
# therefore resolves against the TARGET and exits 127 — which drops the lead
# into the hand-rolled freeze that the same paragraph warns about. Bash call
# sites go through "$DEV_LEAD"; prose uses a relative link, whose target
# check_links() then proves resolvable.
SUITE_DIRS = ("scripts", "docs", "data", "templates")
# `./` and `../` prefixes are consumed BY the match rather than blocked by the
# lookbehind: `./scripts/freeze-target.sh` resolves against the cwd exactly
# like the bare form.
BARE_SUITE_RE = re.compile(
    r"(?<![\w/$.\-])(?:\.{1,2}/)*(?:" + "|".join(SUITE_DIRS) + r")/[\w./\-]+"
)
# Deliberately matches only what check_links() can VALIDATE — its LINK_RE
# target class excludes whitespace too. A wider mask here would hide a
# multiline or titled link from this check while check_links still skipped it,
# leaving that link checked by nobody.
MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)\s]*\)")
DEV_LEAD_RESOLVER = "plugins/cache/dev-lead/dev-lead/*"


def bare_suite_paths(text):
    """(line, token) for each cwd-relative suite path outside a valid link.

    Declared boundaries, so a reader checks declared-vs-actual rather than
    declared-vs-infinite: this cannot tell a suite path from a same-named path
    in the TARGET repo (spell those `"$TARGET/scripts/…"`), it does not mask a
    link label containing nested brackets, and it knows only SUITE_DIRS.
    """
    # a link's target belongs to check_links(); its LABEL may legitimately
    # spell the bare path. Blank links out, preserving line numbering.
    prose = MD_LINK_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return [(prose[: m.start()].count("\n") + 1, m.group(0))
            for m in BARE_SUITE_RE.finditer(prose)]


def check_paths():
    for md in sorted(ROOT.glob("skills/**/*.md")):
        text = md.read_text(encoding="utf-8")
        for line, token in bare_suite_paths(text):
            err(rel(md), f"line {line}: '{token}' resolves against the "
                         "TARGET repo's cwd, not the suite — use "
                         '"$DEV_LEAD/…" in bash, a relative link in prose')
        if "$DEV_LEAD" in text and DEV_LEAD_RESOLVER not in text:
            err(rel(md), "uses $DEV_LEAD but never resolves it — every call "
                         "site degrades to /scripts/… and fails")


# ---- fences ----
def check_fences():
    for md in md_files():
        opens = sum(
            1 for line in md.read_text(encoding="utf-8").splitlines()
            if line.startswith("```")
        )
        if opens % 2:
            err(rel(md), f"odd number of ``` fence lines ({opens})")


# ---- mermaid: a semicolon in message text kills the render ----
# `;` is a STATEMENT SEPARATOR in mermaid, so `A->>B: foo; bar` parses as a
# message plus the garbage statement `bar`, and GitHub renders the whole block
# as an error box. Nothing warns: the markdown is valid, the fence is balanced,
# and the file reads fine in an editor. Measured with mermaid's own parser --
# two occurrences shipped in docs/workflow.md's round-level diagram.
#
# Scope, declared so a reader checks declared-vs-actual rather than
# declared-vs-infinite. AFFECTED, measured against mermaid's parser:
# `sequenceDiagram` messages and notes, and `classDiagram` relation labels
# (`A --> B : text`). NOT affected, also measured: flowchart/graph labels and
# edge labels (quoted, so `;` is literal) and `stateDiagram-v2` transitions,
# which accept `;` in a label that looks identical to a class relation. That
# last one is why the header, not the line shape, decides scope.
#
# This is not a mermaid parser; it knows this one trap. A diagram type added
# to mermaid later is outside what has been measured -- re-probe before
# assuming silence here means safety.
MERMAID_SCOPED = ("sequenceDiagram", "classDiagram")
MERMAID_MSG_RE = re.compile(
    r"^\s*(?:\w+\s*(?:-|=)+[->x)|]*\s*\w+"
    r"|Note\s+(?:over|left of|right of)\s+[^:]+)\s*:(.*)$"
)


def mermaid_risky_messages(text):
    """(line, message_text) for each line whose `;` would break the diagram.

    Only inside a ```mermaid fence whose header is one of MERMAID_SCOPED --
    the same arrow-and-colon shape is harmless in a stateDiagram.
    """
    out, inside, scoped = [], False, False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```mermaid"):
            inside, scoped = True, False
            continue
        if line.startswith("```"):
            inside, scoped = False, False
            continue
        if not inside:
            continue
        stripped = line.strip()
        if not scoped:
            # the header is the FIRST non-blank line of the block; anything
            # else means this block declared some other diagram type
            if stripped:
                scoped = stripped.startswith(MERMAID_SCOPED)
                if not scoped:
                    inside = False      # wrong type -- skip the rest of it
            continue
        m = MERMAID_MSG_RE.match(line)
        if m:
            out.append((i, m.group(1)))
    return out


def check_mermaid():
    for md in md_files():
        for line, msg in mermaid_risky_messages(md.read_text(encoding="utf-8")):
            if ";" in msg:
                err(rel(md), f"line {line}: ';' in a mermaid message ends the "
                             "statement — the whole diagram renders as an "
                             "error box. Use a comma or a dash")


# ---- var-order: a $VAR used before the file's own assignment of it ----
ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})=(?!=)")
USE_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]{2,})\}?")


def bash_blocks(text):
    """Yield (file_line_number, line_text) for lines inside ```bash fences."""
    inside = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```bash"):
            inside = True
            continue
        if line.startswith("```"):
            inside = False
            continue
        if inside:
            yield i, line


def check_var_order():
    for md in md_files():
        text = md.read_text(encoding="utf-8")
        first_assign, first_use = {}, {}
        for lineno, line in bash_blocks(text):
            m = ASSIGN_RE.match(line)
            if m:
                first_assign.setdefault(m.group(1), lineno)
            for m in USE_RE.finditer(line):
                # an assignment line may legitimately use other vars; the
                # var being assigned on this line is not a "use"
                if ASSIGN_RE.match(line) and ASSIGN_RE.match(line).group(1) == m.group(1) \
                        and line.index(m.group(0)) <= line.index("="):
                    continue
                first_use.setdefault(m.group(1), lineno)
        for var, use_line in first_use.items():
            # only vars this file ITSELF assigns — a never-assigned var is a
            # documented external (e.g. $SCRIPT resolved per the runtime file)
            if var in first_assign and use_line < first_assign[var]:
                err(rel(md),
                    f"line {use_line}: ${var} used before its assignment on "
                    f"line {first_assign[var]} — a reader following top-to-bottom fails here")


# ---- helper-arg: a helper call site naming a directory var the file never defines ----
# Measured: the tracked-config flow added to opencode-adversarial-review called
# `verify-target.sh "$DIR" ...` inside a markdown TABLE. check_var_order only
# scans ```bash fences, and it skips never-assigned vars as documented
# externals, so both of its rules looked away and the review caught it instead.
# The frozen-target directory has exactly one name across every skill; a call
# site using any other var is a typo, wherever in the file it sits.
HELPER_CALL_RE = re.compile(
    r"(freeze-target\.sh|verify-target\.sh|snapshot-refs\.sh)\s+\"?\$\{?([A-Z][A-Z0-9_]{2,})\}?")
TARGET_DIR_VAR = "REVIEW_TARGET_DIR"


def check_helper_args():
    for md in md_files():
        for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for helper, var in HELPER_CALL_RE.findall(line):
                if helper == "freeze-target.sh":
                    continue          # freeze takes the SOURCE repo, not the frozen dir
                if var != TARGET_DIR_VAR:
                    err(rel(md),
                        f"line {lineno}: {helper} called with ${var} — the frozen "
                        f"review target is ${TARGET_DIR_VAR} everywhere else")


# ---- tracked: files a contributor's global gitignore might silently eat ----
def check_tracked():
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    for must in ("templates/AGENTS.md", ".claude-plugin/plugin.json",
                 ".github/workflows/ci.yml", "data/families.json",
                 "scripts/freeze-target.sh", "scripts/verify-target.sh",
                 "scripts/snapshot-refs.sh"):
        if must not in out:
            err(must, "not tracked by git (a global gitignore may have silently eaten it)")
    # a helper that lost its +x bit is a helper the skills' call sites cannot run
    for sh in sorted((ROOT / "scripts").glob("*.sh")):
        if not sh.stat().st_mode & 0o111:
            err(rel(sh), "not executable (skills call it directly)")


# ---- sentinel: the headline rule must survive edits in every implement skill ----
CROSS_FAMILY_RE = re.compile(r"cross-family|different (model )?famil", re.I)


def check_sentinels():
    for fam in FAMILIES:
        impl = ROOT / "skills" / f"{fam}-implement" / "SKILL.md"
        if impl.is_file() and not CROSS_FAMILY_RE.search(impl.read_text(encoding="utf-8")):
            err(rel(impl), "no cross-family review requirement stated — the headline rule is gone")
        for role in ("implement", "adversarial-review"):
            skill = ROOT / "skills" / f"{fam}-{role}" / "SKILL.md"
            if skill.is_file() and f"{fam}-runtime.md" not in skill.read_text(encoding="utf-8"):
                err(rel(skill), f"never points at its runtime reference ({fam}-runtime.md)")


# ---- frozen: one review-target rule, identically worded in all four ----
# Measured failure: the four review skills were written in one commit with
# four different formulations of the same rule, and the tested helpers
# (freeze-target.sh / verify-target.sh) arrived later and were wired into the
# ORCHESTRATOR only. Every review skill's description says it can be invoked
# directly -- so a direct invoker read "run from the target repository or
# implementation worktree", never learned about freezing, and used
# $REVIEW_TARGET_DIR which no review skill defines.
#
# The rule text is compared, not merely detected: a sentinel that only asks
# "is the phrase present" cannot see three of four skills drifting.
FROZEN_HEADING = "## Establish an immutable review target"
FROZEN_END = "was frozen at."


def frozen_target_block(text):
    """The shared rule: FROZEN_HEADING through the FROZEN_END sentence.

    Family-specific text may follow it; only the shared prefix is compared,
    so a skill can still say something true about its own runtime.
    Returns None when the section or its terminator is absent.
    """
    start = text.find(FROZEN_HEADING)
    if start < 0:
        return None
    end = text.find(FROZEN_END, start)
    if end < 0:
        return None
    return text[start:end + len(FROZEN_END)]


def check_frozen_target():
    blocks = {}
    for fam in FAMILIES:
        skill = ROOT / "skills" / f"{fam}-adversarial-review" / "SKILL.md"
        if not skill.is_file():
            continue                      # already reported by check_structure
        block = frozen_target_block(skill.read_text(encoding="utf-8"))
        if block is None:
            err(rel(skill), f"no '{FROZEN_HEADING}' section ending in "
                            f"'{FROZEN_END}' — a directly-invoked review skill "
                            "would never learn to freeze its target")
        else:
            blocks[fam] = block
    if len(blocks) < 2:
        return
    ref = sorted(blocks)[0]
    for fam in sorted(blocks):
        if blocks[fam] != blocks[ref]:
            err(f"skills/{fam}-adversarial-review/SKILL.md",
                f"its frozen-target rule differs from {ref}'s — that divergence "
                "is exactly how the suite ended up with four different rules")


# ---- delegates: operational safety rules found by a real review ----
# These are deliberately exact command fragments.  They are not broad prose
# sentinels: each is the machine-enforced form that makes the intended
# boundary real when a lead copies the launch or handoff block.
DELEGATE_GUARDRAILS = {
    "skills/grok-adversarial-review/SKILL.md": {
        "required": ("--deny 'MCPTool(*)'",),
        "forbidden": (),
    },
    "skills/grok-implement/SKILL.md": {
        "required": (
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" '
            '"$RUN_DIR/remote-refs.before" || exit 1',
        ),
        "forbidden": ("remote-refs.after",),
    },
    "skills/cursor-implement/SKILL.md": {
        "required": (
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" '
            '"$RUN_DIR/remote-refs.before" || exit 1',
        ),
        "forbidden": ("remote-refs.after",),
    },
}


def check_delegate_guardrails():
    for name, rules in DELEGATE_GUARDRAILS.items():
        path = ROOT / name
        if not path.is_file():
            continue                          # check_structure() reports it
        text = path.read_text(encoding="utf-8")
        for required in rules["required"]:
            if required not in text:
                err(rel(path), f"missing required delegate guardrail: {required}")
        for forbidden in rules["forbidden"]:
            if forbidden in text:
                err(rel(path), f"unsafe delegate handoff still contains: {forbidden}")


# ---- delegate audit trails: preserve identity and valid Gemini tier pairs ----
CURSOR_AUDIT_OUTPUTS = {
    "skills/cursor-adversarial-review/SKILL.md": "review",
    "skills/cursor-implement/SKILL.md": "impl",
}
AGY_ROLE_SKILLS = (
    "skills/agy-adversarial-review/SKILL.md",
    "skills/agy-implement/SKILL.md",
)
AGY_PAIR_RE = re.compile(
    r"^AGY_MODEL=gemini-[A-Za-z0-9._-]+-(?P<tier>low|medium|high)$\n"
    r"^AGY_EFFORT=(?P<effort>low|medium|high)$",
    re.M,
)


def check_delegate_audit_trails():
    for name, stem in CURSOR_AUDIT_OUTPUTS.items():
        path = ROOT / name
        if not path.is_file():
            continue                          # check_structure() reports it
        text = path.read_text(encoding="utf-8")
        required = (
            "--output-format json",
            f'> "$RUN_DIR/{stem}.json" 2> "$RUN_DIR/{stem}.err"',
            "request_id",
        )
        for fragment in required:
            if fragment not in text:
                err(rel(path), f"missing required audit trail: {fragment}")
        if "--output-format text" in text:
            err(rel(path), "text output drops request_id; use JSON audit output")

    for name in AGY_ROLE_SKILLS:
        path = ROOT / name
        if not path.is_file():
            continue                          # check_structure() reports it
        text = path.read_text(encoding="utf-8")
        pair = AGY_PAIR_RE.search(text)
        if pair is None:
            err(rel(path), "missing AGY_MODEL/AGY_EFFORT Gemini tier pair")
        elif pair.group("tier") != pair.group("effort"):
            err(rel(path), "AGY_MODEL suffix does not match AGY_EFFORT")
        for fragment in ('--model "$AGY_MODEL"', '--effort "$AGY_EFFORT"'):
            if fragment not in text:
                err(rel(path), f"does not use its declared model/effort pair: {fragment}")


# ---- families: the accounting model must match what is on disk ----
def check_families():
    path = ROOT / "data" / "families.json"
    if not path.is_file():
        err("data/families.json", "missing (the cross-family accounting model)")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(rel(path), f"invalid JSON: {e}")
        return

    adapters = data.get("adapters", {})
    families = data.get("families", {})

    # 1. the declared adapters ARE the families on disk — three-way agreement
    #    between this file, lint.py's own tuple, and the skills tree
    if set(adapters) != set(FAMILIES):
        err(rel(path), f"adapters {sorted(adapters)} != skills on disk {sorted(FAMILIES)}")

    # 2. every family an adapter claims to serve must be declared
    for name, spec in adapters.items():
        served = spec.get("serves") or []
        if not served:
            err(rel(path), f"adapter '{name}' serves nothing")
        for fam in served:
            if fam not in families:
                err(rel(path), f"adapter '{name}' serves undeclared family '{fam}'")

    # 3. every declared family must be reachable through some adapter
    reachable = {f for spec in adapters.values() for f in (spec.get("serves") or [])}
    for fam in families:
        if fam not in reachable:
            err(rel(path), f"family '{fam}' is declared but no adapter serves it")

    # 4. the un-accountable case must exist and be marked — if every family
    #    is accounting_valid, the stealth-model hazard has been edited away
    invalid = [f for f, spec in families.items() if not spec.get("accounting_valid", True)]
    if not invalid:
        err(rel(path), "no family is marked accounting_valid:false — the "
                       "stealth-model hazard is missing from the model")
    for fam in invalid:
        if not families[fam].get("why"):
            err(rel(path), f"family '{fam}' is accounting-invalid without a 'why'")

    # 5. a multi-family adapter must SAY so where a lead actually reads it,
    #    or the adapter/family distinction is invisible at the point of use
    for name, spec in adapters.items():
        served = spec.get("serves") or []
        if len(served) < 2:
            continue
        runtime = ROOT / "skills" / f"{name}-adversarial-review" / "references" / f"{name}-runtime.md"
        if not runtime.is_file():
            continue  # already reported by check_structure
        text = runtime.read_text(encoding="utf-8")
        missing = [f for f in served if f != "unknown" and f.lower() not in text.lower()]
        if missing:
            err(rel(runtime),
                f"adapter serves {served} but its runtime file never names {missing} — "
                "a lead reading only this file cannot account the family correctly")


def main():
    for check in (check_structure, check_frontmatter, check_manifest,
                  check_version,
                  check_links, check_paths, check_fences, check_mermaid,
                  check_var_order,
                  check_tracked, check_helper_args, check_sentinels, check_frozen_target,
                  check_delegate_guardrails,
                  check_delegate_audit_trails,
                  check_families):
        check()
    if NOTES:
        # Before the verdict, not after: a note that scrolls past the word
        # "passed" is a note nobody reads.
        print(f"lint: {len(NOTES)} check(s) could not be decided:\n")
        for n in NOTES:
            print(f"  {n}")
        print()
    if ERRORS:
        print(f"FAIL — {len(ERRORS)} problem(s):\n")
        for e in ERRORS:
            print(f"  {e}")
        sys.exit(1)
    if NOTES:
        print("lint: no failures, but see the undecided check(s) above")
    else:
        print("lint: all checks passed")


if __name__ == "__main__":
    main()

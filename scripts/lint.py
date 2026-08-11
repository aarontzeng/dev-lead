#!/usr/bin/env python3
"""Repo invariant linter — zero dependencies, run: python3 scripts/lint.py

Every check here guards against a failure this repo has ACTUALLY shipped
(or a reviewer actually caught), not a hypothetical:

  structure     a family missing its runtime reference (codex shipped without one)
  frontmatter   a SKILL.md whose name/dir disagree would break skill loading
  manifest      plugin.json must stay valid JSON with the required keys
  links         a placeholder link (https://github.com/ with no repo) shipped once
  paths         a skill runs with the TARGET repo as cwd, so a bare `scripts/…`
                or `docs/…` points at the user's project: freeze-target.sh
                shipped as `scripts/freeze-target.sh` and exited 127 everywhere
                except this repo, where the cwd happened to hide it
  fences        unbalanced ``` renders half a file as code
  mermaid       `;` ends a statement in a sequenceDiagram, so a semicolon in
                message text renders the whole diagram as an error box --
                docs/workflow.md shipped two
  var-order     $RUN_DIR was used 12 lines before its mktemp, in two skills
  tracked       an environment-global gitignore silently ate templates/AGENTS.md
  sentinel      a skill's prose once allowed same-family review, contradicting
                the suite's headline rule
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
FAMILIES = ("claude", "codex", "agy", "opencode")
ERRORS = []


def err(path, msg):
    ERRORS.append(f"{path}: {msg}")


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


# ---- mermaid: a semicolon in sequenceDiagram message text kills the render ----
# `;` is a STATEMENT SEPARATOR in mermaid, so `A->>B: foo; bar` parses as a
# message plus the garbage statement `bar`, and GitHub renders the whole block
# as an error box. Nothing warns: the markdown is valid, the fence is balanced,
# and the file reads fine in an editor. Measured with mermaid's own parser --
# two occurrences shipped in docs/workflow.md's round-level diagram.
#
# Scope, declared so a reader checks declared-vs-actual: this covers message
# lines (`A->>B: text`) in ```mermaid blocks that declare `sequenceDiagram`.
# Flowchart labels are quoted and NOT affected. It is not a mermaid parser --
# it knows this one trap. Note text is deliberately included: `Note over A,B:`
# splits on `;` the same way.
MERMAID_MSG_RE = re.compile(r"^\s*(?:\w+\s*(?:-|=)+[->x)]+\s*\w+|Note\s+(?:over|left of|right of)\s+[^:]+)\s*:(.*)$")


def mermaid_sequence_messages(text):
    """(line, message_text) for each message line inside a sequenceDiagram."""
    out, inside, is_seq = [], False, False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```mermaid"):
            inside, is_seq = True, False
            continue
        if line.startswith("```"):
            inside = False
            continue
        if not inside:
            continue
        if line.strip().startswith("sequenceDiagram"):
            is_seq = True
            continue
        if is_seq:
            m = MERMAID_MSG_RE.match(line)
            if m:
                out.append((i, m.group(1)))
    return out


def check_mermaid():
    for md in md_files():
        for line, msg in mermaid_sequence_messages(md.read_text(encoding="utf-8")):
            if ";" in msg:
                err(rel(md), f"line {line}: ';' in a sequenceDiagram message "
                             "ends the statement — the whole diagram renders as "
                             "an error box. Use a comma or a dash")


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
                  check_links, check_paths, check_fences, check_mermaid,
                  check_var_order,
                  check_tracked, check_sentinels, check_families):
        check()
    if ERRORS:
        print(f"FAIL — {len(ERRORS)} problem(s):\n")
        for e in ERRORS:
            print(f"  {e}")
        sys.exit(1)
    print("lint: all checks passed")


if __name__ == "__main__":
    main()

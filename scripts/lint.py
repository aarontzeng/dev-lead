#!/usr/bin/env python3
"""Repo invariant linter — zero dependencies, run: python3 scripts/lint.py

Every check here guards against a failure this repo has ACTUALLY shipped
(or a reviewer actually caught), not a hypothetical:

  structure     a family missing its runtime reference (codex shipped without one)
  frontmatter   a SKILL.md whose name/dir disagree would break skill loading
  manifest      plugin.json must stay valid JSON with the required keys
  links         a placeholder link (https://github.com/ with no repo) shipped once
  fences        unbalanced ``` renders half a file as code
  var-order     $RUN_DIR was used 12 lines before its mktemp, in two skills
  tracked       an environment-global gitignore silently ate templates/AGENTS.md
  sentinel      a skill's prose once allowed same-family review, contradicting
                the suite's headline rule
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


# ---- fences ----
def check_fences():
    for md in md_files():
        opens = sum(
            1 for line in md.read_text(encoding="utf-8").splitlines()
            if line.startswith("```")
        )
        if opens % 2:
            err(rel(md), f"odd number of ``` fence lines ({opens})")


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
                 ".github/workflows/ci.yml"):
        if must not in out:
            err(must, "not tracked by git (a global gitignore may have silently eaten it)")


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


def main():
    for check in (check_structure, check_frontmatter, check_manifest,
                  check_links, check_fences, check_var_order,
                  check_tracked, check_sentinels):
        check()
    if ERRORS:
        print(f"FAIL — {len(ERRORS)} problem(s):\n")
        for e in ERRORS:
            print(f"  {e}")
        sys.exit(1)
    print("lint: all checks passed")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a codex review prompt from the suite's adversarial framing.

Usage: codex-review-prompt.py --base <rev> --target <frozen worktree> < lens > prompt

The companion's `adversarial-review` supplies its own framing; a raw
`codex exec` review does not, so this wraps the lead's lens in
skills/codex-adversarial-review/references/adversarial-framing.md. One pass
over the template, so a `{{HEAD}}` written inside the lens stays literal; an
unknown placeholder in the template, an empty lens or an unresolvable HEAD is
an error, never an empty or half-filled prompt handed to a paid run.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

FRAMING = (Path(__file__).resolve().parent.parent / "skills" / "codex-adversarial-review"
           / "references" / "adversarial-framing.md")


def build(frame, base, head, lens):
    frame = re.sub(r"\A<!--.*?-->\r?\n", "", frame, count=1, flags=re.S)
    values = {"BASE": base, "HEAD": head, "LENS": lens}
    unknown = set(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", frame)) - set(values)
    if unknown:
        raise ValueError("unknown placeholder(s) in the framing: %s" % sorted(unknown))
    return re.sub(r"\{\{(BASE|HEAD|LENS)\}\}", lambda m: values[m.group(1)], frame)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--target", required=True, help="the frozen worktree; HEAD is read from it")
    args = ap.parse_args()
    lens = sys.stdin.read()
    if not lens.strip():
        sys.exit("codex-review-prompt: empty lens on stdin -- refusing to build a prompt with no brief")
    head = subprocess.run(["git", "-C", args.target, "rev-parse", "--verify", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode:
        sys.exit("codex-review-prompt: cannot read HEAD of %s: %s" % (args.target, head.stderr.strip()))
    try:
        text = build(FRAMING.read_text(encoding="utf-8"), args.base, head.stdout.strip(), lens.strip())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        sys.exit("codex-review-prompt: %s" % exc)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a review leg's prompt from that adapter's adversarial framing.

Usage: review-prompt.py --adapter <name> --base <rev> --target <frozen worktree>
                        [--evidence <new dir>] < lens > prompt

The lead's brief is the LENS; this wraps it in
skills/<adapter>-adversarial-review/references/adversarial-framing.md. One pass
over the template, so a `{{HEAD}}` written inside the lens stays literal; an
unknown placeholder, an empty lens or an unresolvable HEAD is an error, never
an empty or half-filled prompt handed to a paid run.

A framing that carries `{{EVIDENCE}}` is for a leg that cannot run commands
(agy in plan mode): --evidence names a directory, outside the frozen target,
that this script creates and fills with what the leg would otherwise have read
through git -- DIFF.patch, base/<path> for every changed path that exists at
the base, BLOBS.txt (mode and blob id on both sides) and COMMITS.txt -- then
makes read-only. The directory must not exist yet, or be empty: evidence left
from another run is exactly the stale input this refuses to hand over. A
submodule (gitlink) has no file content to copy: BLOBS.txt carries its commit
id on each side, and base/ has no entry for it.

BASE is resolved to a commit once, and that hash is what the prompt and every
piece of evidence use: a branch name that moves mid-run would otherwise make the
diff, the commit list and base/ describe different ranges.
"""
import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def framing_path(adapter):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", adapter or ""):
        raise ValueError("not an adapter name: %r" % adapter)
    return SKILLS / ("%s-adversarial-review" % adapter) / "references" / "adversarial-framing.md"


def build(frame, base, head, lens, evidence=None):
    frame = re.sub(r"\A<!--.*?-->\r?\n", "", frame, count=1, flags=re.S)
    values = {"BASE": base, "HEAD": head, "LENS": lens}
    wants = set(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", frame))
    if "EVIDENCE" in wants and evidence is None:
        raise ValueError("this framing needs {{EVIDENCE}}: pass --evidence <new dir outside the target>")
    if evidence is not None:
        if "EVIDENCE" not in wants:
            raise ValueError("--evidence was given but this framing has no {{EVIDENCE}}; "
                             "it would be materialized and never read")
        values["EVIDENCE"] = evidence
    unknown = wants - set(values)
    if unknown:
        raise ValueError("unknown placeholder(s) in the framing: %s" % sorted(unknown))
    return re.sub(r"\{\{(%s)\}\}" % "|".join(values), lambda m: values[m.group(1)], frame)


def _git(target, *args, binary=False):
    r = subprocess.run(["git", "-C", target, *args], capture_output=True)
    if r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.decode(errors="replace").strip()))
    return r.stdout if binary else r.stdout.decode("utf-8", "surrogateescape")


def _tree_entry(target, rev, path):
    """(mode, type, blob) of path at rev, or None when it does not exist there."""
    out = _git(target, "ls-tree", "-z", rev, "--", path)
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, name = rec.split("\t", 1)
        if name == path:
            mode, typ, obj = meta.split()
            return mode, typ, obj
    return None


def check_dest(target, dest):
    """Refuse an evidence directory before anything is written to it."""
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ValueError("--evidence %s already exists and is not an empty directory; "
                         "use a fresh run directory per leg" % dest)
    tgt = os.path.realpath(target)
    real = os.path.realpath(dest)
    if real == tgt or real.startswith(tgt.rstrip(os.sep) + os.sep):
        raise ValueError("--evidence %s is inside the frozen target; it would dirty the "
                         "tree the review is certified against" % dest)


def materialize(target, base, head, dest):
    """Write the evidence a no-command leg reads instead of running git."""
    check_dest(target, dest)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "DIFF.patch").write_bytes(
        _git(target, "diff", "--no-textconv", "--no-ext-diff", "--no-color", base, head, binary=True))
    (dest / "COMMITS.txt").write_text(_git(target, "log", "--format=%H %s", "%s..%s" % (base, head)))
    names = [n for n in _git(target, "diff", "--name-only", "--no-renames", "-z", base, head).split("\0") if n]
    lines = []
    for path in names:
        parts = Path(path).parts
        if Path(path).is_absolute() or ".." in parts:
            raise ValueError("refusing an unsafe path from git: %r" % path)
        old = _tree_entry(target, base, path)
        new = _tree_entry(target, head, path)
        shown = path.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
        lines.append("%s\t%s %s\t%s %s" % (shown, *(old[0::2] if old else ("-", "-")),
                                            *(new[0::2] if new else ("-", "-"))))
        if old and old[1] == "blob":
            out = dest / "base" / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(_git(target, "cat-file", "blob", old[2], binary=True))
    (dest / "BLOBS.txt").write_text(
        "path\tbase mode+blob\thead mode+blob ('-' = absent; mode 160000 = a submodule's "
        "commit, not in base/; \\t \\n \\\\ escaped in paths)\n"
        + "".join(l + "\n" for l in lines), errors="surrogateescape")
    # Read-only for the leg's whole run, not just equal at the endpoints.
    for root, dirs, files in os.walk(dest, topdown=False):
        for f in files:
            os.chmod(os.path.join(root, f), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(root, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
                 | stat.S_IROTH | stat.S_IXOTH)


def main(argv=None, prog="review-prompt"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0])
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--target", required=True, help="the frozen worktree; HEAD is read from it")
    ap.add_argument("--evidence", help="new directory to materialize evidence into (no-command legs)")
    args = ap.parse_args(argv)
    lens = sys.stdin.read()
    if not lens.strip():
        sys.exit("%s: empty lens on stdin -- refusing to build a prompt with no brief" % prog)
    head = subprocess.run(["git", "-C", args.target, "rev-parse", "--verify", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode:
        sys.exit("%s: cannot read HEAD of %s: %s" % (prog, args.target, head.stderr.strip()))
    head = head.stdout.strip()
    base = subprocess.run(["git", "-C", args.target, "rev-parse", "--verify", "--quiet",
                           args.base + "^{commit}"], capture_output=True, text=True)
    if base.returncode:
        sys.exit("%s: --base %s is not a commit in %s" % (prog, args.base, args.target))
    base = base.stdout.strip()
    evidence = os.path.abspath(args.evidence) if args.evidence else None
    # What to remove if this run fails: the directory itself when this run made
    # it, or only its contents when an empty one was handed in.
    made_dir = evidence is not None and not os.path.exists(evidence)
    writing = False
    try:
        text = build(framing_path(args.adapter).read_text(encoding="utf-8"),
                     base, head, lens.strip(), evidence)
        if evidence is not None:
            check_dest(args.target, evidence)
            writing = True
            materialize(args.target, base, head, evidence)
    except (OSError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
        # A half-written evidence directory would make the retry refuse, and
        # reads like evidence to anyone who opens it: remove what this run made.
        if writing and os.path.isdir(evidence):
            for root, dirs, _files in os.walk(evidence):
                os.chmod(root, stat.S_IRWXU)
            if made_dir:
                shutil.rmtree(evidence, ignore_errors=True)
            else:
                for child in os.listdir(evidence):
                    child = os.path.join(evidence, child)
                    shutil.rmtree(child) if os.path.isdir(child) else os.unlink(child)
        sys.exit("%s: %s" % (prog, exc))
    sys.stdout.write(text)


if __name__ == "__main__":
    main()

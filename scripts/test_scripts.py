#!/usr/bin/env python3
"""Tests for the shell helpers — zero dependencies, run: python3 scripts/test_scripts.py

Every test asserts a FAILURE the helper is supposed to catch, not just the
happy path: a helper that silently succeeds on a broken input is exactly the
class of bug these scripts exist to remove (a bracket that checks the wrong
directory still exits 0, and reads as a passing safety check).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
FAILURES = []


def run(*args, **kw):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)


def git(repo, *args):
    return run("git", "-C", str(repo), *args, check=True)


def make_repo(path, commits=1):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    shas = []
    for i in range(commits):
        (path / f"f{i}.txt").write_text(f"content {i}\n")
        git(path, "add", "-A")
        git(path, "commit", "-qm", f"commit {i}")
        shas.append(git(path, "rev-parse", "HEAD").stdout.strip())
    return shas


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# ---------------------------------------------------------------- freeze ----
def test_freeze(tmp):
    repo = tmp / "repo"
    shas = make_repo(repo, commits=3)
    old, head = shas[0], shas[-1]
    freeze = SCRIPTS / "freeze-target.sh"

    dest = tmp / "frozen"
    r = run(freeze, repo, old, dest)
    check("freeze: exits 0 on a valid old commit", r.returncode == 0, r.stderr)
    check("freeze: prints the resolved SHA", r.stdout.strip() == old,
          f"got {r.stdout.strip()!r}")
    check("freeze: worktree really is at that commit (not HEAD)",
          dest.is_dir() and git(dest, "rev-parse", "HEAD").stdout.strip() == old)
    # detached == symbolic-ref fails; a branch would let something advance it
    check("freeze: worktree is detached (no branch can advance it)",
          run("git", "-C", str(dest), "symbolic-ref", "-q", "HEAD").returncode != 0)

    # a branch name that MOVES must be resolved at freeze time, not stored
    r2 = run(freeze, repo, "HEAD", tmp / "frozen-head")
    check("freeze: resolves a moving name to a fixed SHA", r2.stdout.strip() == head)

    # refuses to touch an existing destination
    r3 = run(freeze, repo, old, dest)
    check("freeze: refuses an existing destination", r3.returncode != 0)
    check("freeze: says why", "already exists" in r3.stderr, r3.stderr)

    # unresolvable committish
    r4 = run(freeze, repo, "no-such-ref", tmp / "nope")
    check("freeze: rejects an unresolvable committish", r4.returncode != 0)
    check("freeze: creates nothing on rejection", not (tmp / "nope").exists())

    # not a repo
    plain = tmp / "plain"
    plain.mkdir()
    r5 = run(freeze, plain, "HEAD", tmp / "nope2")
    check("freeze: rejects a non-repo", r5.returncode != 0 and "not a git repo" in r5.stderr)

    return dest, old


# ---------------------------------------------------------------- verify ----
def test_verify(tmp, frozen, sha):
    verify = SCRIPTS / "verify-target.sh"

    r = run(verify, frozen, sha)
    check("verify: passes on an untouched frozen target", r.returncode == 0, r.stderr)

    # THE tautology guard: a wrong expected SHA must fail. If this ever
    # passes, the check is comparing HEAD against itself.
    other = "0" * 40
    r2 = run(verify, frozen, other)
    check("verify: FAILS when the expected SHA does not match", r2.returncode != 0)
    check("verify: names the drift", "HEAD moved" in r2.stderr, r2.stderr)

    # dirty tree — a reviewer reads the working tree, not your commit
    (Path(frozen) / "f0.txt").write_text("mutated by someone else\n")
    r3 = run(verify, frozen, sha)
    check("verify: FAILS on a dirty target even at the right SHA", r3.returncode != 0)
    check("verify: shows what is dirty", "f0.txt" in r3.stderr, r3.stderr)
    (Path(frozen) / "f0.txt").write_text("content 0\n")

    # wrong-directory guard: verifying a DIFFERENT repo that happens to be
    # clean must not pass just because the caller's cwd is fine.
    # NOTE the distinct content: two repos built from identical trees,
    # authors, messages and second-resolution timestamps produce the SAME
    # root-commit SHA, and an earlier version of this test failed for that
    # reason rather than any script defect.
    otherrepo = tmp / "otherrepo"
    otherrepo.mkdir()
    git(otherrepo, "init", "-q")
    git(otherrepo, "config", "user.email", "someone-else@example.invalid")
    git(otherrepo, "config", "user.name", "Someone Else")
    (otherrepo / "different.txt").write_text("a genuinely different tree\n")
    git(otherrepo, "add", "-A")
    git(otherrepo, "commit", "-qm", "unrelated repo")
    assert git(otherrepo, "rev-parse", "HEAD").stdout.strip() != sha
    r4 = run(verify, otherrepo, sha)
    check("verify: checks the DIR it was given, not the cwd", r4.returncode != 0)


# -------------------------------------------------------------- snapshot ----
def test_snapshot(tmp):
    snap = SCRIPTS / "snapshot-refs.sh"
    origin = tmp / "origin.git"
    run("git", "init", "-q", "--bare", str(origin), check=True)
    work = tmp / "work"
    make_repo(work, commits=1)
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "fetch", "-q", "origin")

    run_dir = tmp / "rundir"
    run_dir.mkdir()
    base = run_dir / "remote-refs.before"

    r = run(snap, "save", work, base)
    check("snapshot: save exits 0", r.returncode == 0, r.stderr)
    check("snapshot: baseline file written", base.is_file())

    r2 = run(snap, "check", work, base)
    check("snapshot: check passes when nothing pushed", r2.returncode == 0, r2.stderr)

    # simulate a delegate pushing during the run
    (work / "sneaky.txt").write_text("x\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "sneaky")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "fetch", "-q", "origin")
    r3 = run(snap, "check", work, base)
    check("snapshot: check FAILS after a real push", r3.returncode != 0)
    check("snapshot: says refs changed", "CHANGED" in r3.stderr, r3.stderr)

    # the measured ordering bug: saving into a RUN_DIR that does not exist yet
    r4 = run(snap, "save", work, tmp / "not-created-yet" / "refs.before")
    check("snapshot: save REFUSES a missing output dir (the $RUN_DIR bug)",
          r4.returncode != 0)
    check("snapshot: names the cause", "create RUN_DIR first" in r4.stderr, r4.stderr)

    # check without a baseline must not silently pass
    r5 = run(snap, "check", work, tmp / "no-such-baseline")
    check("snapshot: check FAILS when the baseline is missing", r5.returncode != 0)


# -------------------------------------------------------------- lint paths ----
def test_lint_paths():
    """check_paths()'s predicate, in BOTH directions.

    A path checker earns its line count only if it still fires after a later
    edit. Every must-flag case below is a spelling that resolves against the
    TARGET repo's cwd, and every must-not-flag case is a correct spelling that
    a false positive would push a contributor away from — the second set is
    the one an over-eager regex breaks.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    must_flag = {
        "bare bash call site": 'scripts/freeze-target.sh "$REPO" "$SHA"',
        "bare prose citation": "see `docs/methodology.md` §5",
        "./ prefix": "run ./scripts/snapshot-refs.sh save",
        "../ prefix": "run ../scripts/snapshot-refs.sh save",
        # masked by the link regex but skipped by check_links() -> checked by
        # nobody, until MD_LINK_RE was narrowed to what check_links validates
        "multiline link": "[`docs/x.md`](\n../../docs/x.md)",
    }
    for name, text in must_flag.items():
        check(f"paths: flags {name}", bool(lint.bare_suite_paths(text)),
              f"{text!r} passed unflagged")

    must_not_flag = {
        "$DEV_LEAD call site": '"$DEV_LEAD/scripts/freeze-target.sh" "$REPO"',
        "relative link": "[`docs/methodology.md`](../../docs/methodology.md)",
        "url": "https://github.com/aarontzeng/dev-lead/docs/methodology.md",
        "plugin state path": "$HOME/.claude/plugins/data/codex/state/x",
        "bare directory name": "everything in docs/ is prose",
    }
    for name, text in must_not_flag.items():
        hits = lint.bare_suite_paths(text)
        check(f"paths: passes {name}", not hits, f"{text!r} flagged {hits}")

    # A wrong line number sends the reader to the wrong place, which is how a
    # real hit gets dismissed as noise. The masked link must SPAN LINES or
    # this proves nothing: a single-line link blanks to the same width whether
    # or not the substitution preserves newlines, and the mutation survives.
    hits = lint.bare_suite_paths("[a\nb](c.md)\n\nrun scripts/x.sh")
    check("paths: reports the line number after masking", hits[0][0] == 4,
          f"got {hits[0][0] if hits else 'no hit'}")

    # the guarded tree must actually pass the guard
    r = run(sys.executable, SCRIPTS / "lint.py")
    check("lint: the repo passes its own invariants", r.returncode == 0,
          r.stdout + r.stderr)


def main():
    for script in ("freeze-target.sh", "verify-target.sh", "snapshot-refs.sh"):
        p = SCRIPTS / script
        if not p.is_file():
            print(f"  FAIL missing script: {script}")
            FAILURES.append(script)
        elif not p.stat().st_mode & 0o111:
            print(f"  FAIL not executable: {script}")
            FAILURES.append(script)
    if FAILURES:
        print("\nFAIL — scripts missing or not executable")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        print("freeze-target.sh")
        frozen, sha = test_freeze(tmp)
        print("verify-target.sh")
        test_verify(tmp, frozen, sha)
        print("snapshot-refs.sh")
        test_snapshot(tmp)

    print("lint.py check_paths")
    test_lint_paths()

    if FAILURES:
        print(f"\nFAIL — {len(FAILURES)} test(s) failed")
        sys.exit(1)
    print("\ntest_scripts: all passed")


if __name__ == "__main__":
    main()

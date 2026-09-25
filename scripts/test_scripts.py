#!/usr/bin/env python3
"""Tests for the shell helpers — zero dependencies, run: python3 scripts/test_scripts.py

Every test asserts a FAILURE the helper is supposed to catch, not just the
happy path: a helper that silently succeeds on a broken input is exactly the
class of bug these scripts exist to remove (a bracket that checks the wrong
directory still exits 0, and reads as a passing safety check).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
    # a tracked ignore rule: verify-target must not let the PROJECT's own
    # .gitignore hide an undeclared file either
    (path / ".gitignore").write_text("*.secret\nignored-dir/\n")
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

    # A RELATIVE dest, from a cwd that is not the repo. `git -C "$repo" worktree
    # add` resolves it against $repo while every other line resolves it against
    # the caller's cwd, so before the fix this planted the worktree INSIDE the
    # target repository and left it dirty — and the "refusing to touch it" guard
    # was checking an unrelated path in the cwd, so it never fired. Every other
    # case here passes an absolute dest, which is why it went unseen.
    elsewhere = tmp / "cwd"
    elsewhere.mkdir()
    r = run(freeze, repo, old, "relframe", cwd=str(elsewhere))
    check("freeze: a relative dest lands in the CALLER's cwd",
          (elsewhere / "relframe").is_dir(), r.stderr)
    check("freeze: ...and does NOT plant a worktree in the target repo",
          not (repo / "relframe").exists(), "orphan worktree inside the repo")
    check("freeze: ...leaving the target repo clean",
          run("git", "-C", repo, "status", "--porcelain=v1").stdout == "",
          run("git", "-C", repo, "status", "--porcelain=v1").stdout)

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

    # ---- declared scaffolding: permits an ENTRY, never loosens anything else
    scaffold = Path(frozen) / "opencode.json"
    scaffold.write_text('{"permission":{"edit":"deny"}}\n')

    r6 = run(verify, frozen, sha)
    check("verify: an undeclared scaffold file still fails", r6.returncode != 0, r6.stderr)

    r7 = run(verify, frozen, sha, "opencode.json")
    check("verify: declaring the path certifies the target", r7.returncode == 0, r7.stderr)
    check("verify: says the permission was by entry, not content",
          "not verified by CONTENT" in r7.stderr, r7.stderr)

    # a declared path does not excuse a SECOND stray file
    stray = Path(frozen) / "stray.txt"
    stray.write_text("not declared\n")
    r8 = run(verify, frozen, sha, "opencode.json")
    check("verify: declaring one path does not permit another", r8.returncode != 0)
    check("verify: names the undeclared file", "stray.txt" in r8.stderr, r8.stderr)
    check("verify: does not name the declared one as the problem",
          "opencode.json" not in r8.stderr.split("declared scaffolding")[-1], r8.stderr)
    stray.unlink()

    # declaring a path that is NOT there is a failure too: the setup you
    # certified is not the setup that ran.
    r9 = run(verify, frozen, sha, "opencode.json", "REVIEW-CLAIMS.md")
    check("verify: a declared-but-absent path fails", r9.returncode != 0)
    check("verify: names the absent path", "REVIEW-CLAIMS.md" in r9.stderr, r9.stderr)

    # the SHA guard is not weakened by declaring paths
    r10 = run(verify, frozen, other, "opencode.json")
    check("verify: declared paths do not excuse a moved HEAD", r10.returncode != 0)
    check("verify: still names the drift", "HEAD moved" in r10.stderr, r10.stderr)

    # A tracked file modified in place is never scaffolding. Declaring its path
    # must NOT excuse it — this assertion was inverted in the first version of
    # this test, which passed while proving the opposite of its own comment.
    (Path(frozen) / "f0.txt").write_text("mutated\n")
    r11 = run(verify, frozen, sha, "opencode.json", "f0.txt")
    check("verify: a declared TRACKED path does not excuse a mutation", r11.returncode != 0)
    check("verify: names the mutated tracked file", "f0.txt" in r11.stderr, r11.stderr)
    (Path(frozen) / "f0.txt").write_text("content 0\n")

    # a declared path that is tracked-and-clean is simply absent from porcelain,
    # so it trips the declared-but-absent guard rather than silently passing
    r11b = run(verify, frozen, sha, "opencode.json", "f0.txt")
    check("verify: a clean tracked path cannot be declared as scaffolding", r11b.returncode != 0)
    scaffold.unlink()

    # An untracked DIRECTORY collapses to one "?? scaffold/" entry under the
    # porcelain default, so declaring the directory would permit everything
    # inside it: adding a second file leaves that single entry unchanged.
    # Measured before the --untracked-files=all fix: both calls returned 0.
    subdir = Path(frozen) / "scaffolddir"
    subdir.mkdir()
    (subdir / "a.txt").write_text("one\n")
    r13 = run(verify, frozen, sha, "scaffolddir/")
    check("verify: a bare directory declaration is not accepted", r13.returncode != 0)
    r13b = run(verify, frozen, sha, "scaffolddir/a.txt")
    check("verify: the file inside it can be declared by name", r13b.returncode == 0, r13b.stderr)
    (subdir / "SMUGGLED.txt").write_text("undeclared\n")
    r13c = run(verify, frozen, sha, "scaffolddir/a.txt")
    check("verify: an undeclared sibling in a declared dir is caught", r13c.returncode != 0)
    check("verify: names the smuggled file", "SMUGGLED.txt" in r13c.stderr, r13c.stderr)
    (subdir / "SMUGGLED.txt").unlink()

    # The project's OWN .gitignore hides a file just as effectively as the
    # operator's. Measured before --ignored=matching: declaring scaffold/a.txt
    # certified a tree that also held an undeclared, ignored scaffold/SECRET.
    (subdir / "SMUGGLED.secret").write_text("ignored but present\n")
    r14 = run(verify, frozen, sha, "scaffolddir/a.txt")
    check("verify: an IGNORED undeclared file is still caught", r14.returncode != 0)
    check("verify: names the ignored file", "SMUGGLED.secret" in r14.stderr, r14.stderr)
    r14b = run(verify, frozen, sha, "scaffolddir/a.txt", "scaffolddir/SMUGGLED.secret")
    check("verify: an ignored path CAN be declared", r14b.returncode == 0, r14b.stderr)
    shutil.rmtree(subdir)

    # An IGNORED directory collapses to one "!! ignored-dir/" entry even under
    # --ignored=matching (documented: git does not descend into a directory
    # that itself matches). Admitting "!!" entries in the previous commit
    # therefore reopened the very directory hole that commit closed for "??".
    # Measured: declaring "ignored-dir/" returned 0 both before and after two
    # undeclared children appeared inside it.
    ign = Path(frozen) / "ignored-dir"
    ign.mkdir()
    (ign / "a.txt").write_text("one\n")
    r15 = run(verify, frozen, sha, "ignored-dir/")
    check("verify: an ignored-DIRECTORY declaration is refused", r15.returncode != 0)
    check("verify: says why a trailing slash cannot be declared",
          "ends in '/'" in r15.stderr, r15.stderr)
    r15b = run(verify, frozen, sha, "ignored-dir/a.txt")
    check("verify: a file inside an ignored dir cannot be declared either",
          r15b.returncode != 0, r15b.stderr)
    check("verify: names the collapsed directory entry",
          "ignored-dir/" in r15b.stderr, r15b.stderr)
    shutil.rmtree(ign)

    r12 = run(verify, frozen, sha)
    check("verify: clean again after scaffolding is removed", r12.returncode == 0, r12.stderr)


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
def test_lint_helper_args():
    """The regex must see every written form, and the ROLE must decide the name.

    Three shipped versions of this check were each wrong in a way that looked
    right: it required whitespace after `.sh` (missing the repo's own quoted,
    path-qualified form); it listed snapshot-refs.sh, whose subcommand sits
    where the pattern wants the variable, matching 0 of 12 call sites; and it
    accepted both target names everywhere, which green-lights $FROZEN_DIR
    inside a review skill that never defines it.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    forms = {
        "table shorthand": 'verify-target.sh "$DIR" "$REVIEW_HEAD"',
        "quoted, path-qualified": '"$DEV_LEAD/scripts/verify-target.sh" "$DIR" "$REVIEW_HEAD"',
        "braced var": 'verify-target.sh "${DIR}" "$REVIEW_HEAD"',
    }
    for name, text in forms.items():
        found = lint.HELPER_CALL_RE.findall(text)
        check(f"helper-args: sees the {name} form",
              found == [("verify-target.sh", "DIR")], f"{name}: {found!r}")

    # freeze-target.sh legitimately takes the SOURCE repo, not the frozen dir
    check("helper-args: freeze-target.sh is matched but exempted at the call site",
          lint.HELPER_CALL_RE.findall('freeze-target.sh "$REPO" "$SHA"')
          == [("freeze-target.sh", "REPO")])

    # snapshot-refs.sh save|check <dir> <outfile> — the subcommand sits where
    # the pattern wants the variable, and its <dir> is an implement $WORKTREE,
    # not a frozen review target. Claiming coverage of it was the defect.
    check("helper-args: snapshot-refs.sh is out of scope, not silently unmatched",
          lint.HELPER_CALL_RE.findall(
              '"$DEV_LEAD/scripts/snapshot-refs.sh" save "$WORKTREE" "$OUT"') == [],
          "snapshot-refs.sh must not be in HELPER_CALL_RE at all")
    check("helper-args: and it is absent from the pattern by construction",
          "snapshot-refs" not in lint.HELPER_CALL_RE.pattern)

    # the role decides the name — an allowlist accepting both would pass all
    # four of these, and the middle two are the defect it was meant to catch
    check("helper-args: the lead skill's own name is $FROZEN_DIR",
          lint.LEAD_DIR_VAR == "FROZEN_DIR" and lint.LEG_DIR_VAR == "REVIEW_TARGET_DIR")
    check("helper-args: the lead-skill path is compared as a string, not a Path",
          lint.LEAD_SKILL == "skills/dev-lead/SKILL.md"
          and str(lint.rel(lint.ROOT / lint.LEAD_SKILL)) == lint.LEAD_SKILL,
          "rel() returns a PosixPath; PosixPath == str is always False")


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


# ------------------------------------------------------------ lint mermaid ----
def test_lint_mermaid():
    """check_mermaid()'s predicate, in BOTH directions.

    The must-not-flag set is the load-bearing half, and the stateDiagram case
    is the one that matters: `S1 --> S2 : go; now` has the exact arrow-and-colon
    shape the regex looks for, and mermaid parses it FINE. It is the only case
    here that can tell a header-scoped checker from one whose scope has leaked
    into every diagram type -- a mutation that made scope unconditional
    survived every other case in this list.

    Which types break is measured against mermaid's own parser, not assumed:
    sequenceDiagram and classDiagram break, stateDiagram-v2 and flowchart
    labels do not.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    def flagged(body):
        hits = lint.mermaid_risky_messages(f"```mermaid\n{body}\n```\n")
        return [m for _, m in hits if ";" in m]

    must_flag = {
        "sequence message": "sequenceDiagram\n    A->>B: alpha; beta",
        "sequence dotted reply": "sequenceDiagram\n    A-->>B: alpha; beta",
        "sequence note": "sequenceDiagram\n    Note over A,B: alpha; beta",
        "class relation label": "classDiagram\n    ClassA --> ClassB : has; many",
    }
    for name, body in must_flag.items():
        check(f"mermaid: flags {name}", bool(flagged(body)),
              f"{body!r} passed unflagged")

    must_not_flag = {
        # SAME line shape as a class relation, and mermaid parses it fine --
        # this is the case that kills an unconditional-scope mutation
        "stateDiagram transition": "stateDiagram-v2\n    S1 --> S2 : go; now",
        # quoted, so `;` is literal in both of these
        "flowchart node label": 'flowchart TD\n    A["alpha; beta"] --> B["x"]',
        "flowchart edge label": 'flowchart TD\n    A -->|"alpha; beta"| B',
        "clean sequence message": "sequenceDiagram\n    A->>B: alpha, beta",
        "participant line": "sequenceDiagram\n    participant A as One; Two",
    }
    for name, body in must_not_flag.items():
        hits = flagged(body)
        check(f"mermaid: passes {name}", not hits, f"{body!r} flagged {hits}")

    # a wrong line number sends the reader to the wrong place, which is how a
    # real hit gets dismissed as noise
    found = lint.mermaid_risky_messages(
        "intro\n\n```mermaid\nsequenceDiagram\n    A->>B: ok\n    A->>B: bad; here\n```\n")
    bad = [ln for ln, m in found if ";" in m]
    check("mermaid: reports the offending line", bad == [6], f"got {bad}")

    # check_mermaid() ITSELF, not just its predicate. Exercising only the
    # predicate leaves the check's body untested: a mutation that gutted the
    # `";" in msg` condition passed every test above, because none of them
    # ever called the function that lint actually runs.
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        (fake / "bad.md").write_text(
            "# doc\n\n```mermaid\nsequenceDiagram\n    A->>B: alpha; beta\n```\n")
        (fake / "good.md").write_text(
            "# doc\n\n```mermaid\nflowchart TD\n    A[\"alpha; beta\"] --> B\n```\n")
        real_root, real_errors = lint.ROOT, lint.ERRORS
        try:
            lint.ROOT, lint.ERRORS = fake, []
            lint.check_mermaid()
            found = list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS = real_root, real_errors
    check("mermaid: check_mermaid() reports the bad file", len(found) == 1,
          f"got {found}")
    check("mermaid: check_mermaid() names file and line",
          bool(found) and found[0].startswith("bad.md: line 5:"), f"got {found}")

    # and the guarded tree must actually be clean
    check("mermaid: the repo's own diagrams are clean",
          all(";" not in m
              for p in ROOT_MD
              for _, m in lint.mermaid_risky_messages(p.read_text(encoding="utf-8"))),
          "a shipped diagram still contains a semicolon")


# ------------------------------------------------------------- lint frozen ----
def test_lint_frozen_target():
    """check_frozen_target(): the section must exist AND match, in all four.

    Both halves are load-bearing and fail differently. A missing section is
    the measured defect (claude had none at all); four PRESENT but divergent
    sections is the other measured defect, and a presence-only sentinel is
    blind to it.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    good = ("## Establish an immutable review target\n\nfreeze it. "
            "`$REVIEW_HEAD` is the SHA it was frozen at.\n\n## Run it\n")

    def run_against(bodies):
        """Point lint at a fake skills tree and collect what it reports."""
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            for fam, body in bodies.items():
                d = fake / "skills" / f"{fam}-adversarial-review"
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text(body)
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                lint.check_frozen_target()
                return list(lint.ERRORS)
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors

    allfour = {f: good for f in lint.FAMILIES}
    check("frozen: four identical sections pass", run_against(allfour) == [],
          f"got {run_against(allfour)}")

    missing = dict(allfour); missing["claude"] = "# no such section\n"
    got = run_against(missing)
    check("frozen: flags a skill with no section",
          any("claude" in e and "never learn to freeze" in e for e in got), f"got {got}")

    # the terminator matters: a section that stops early is a truncated rule
    truncated = dict(allfour)
    truncated["agy"] = "## Establish an immutable review target\n\nfreeze it.\n"
    got = run_against(truncated)
    check("frozen: flags a section missing its terminator",
          any("agy" in e for e in got), f"got {got}")

    # THE case a presence-only sentinel cannot see
    drifted = dict(allfour)
    drifted["opencode"] = good.replace("freeze it.", "freeze it whenever possible.")
    got = run_against(drifted)
    check("frozen: flags a section that drifted in wording",
          any("opencode" in e and "differs" in e for e in got), f"got {got}")

    # ALL four truncated is the case the "differs" branch cannot catch: they
    # still agree with each other, so only the terminator proves the whole
    # rule is present rather than just its heading
    headless = {f: "## Establish an immutable review target\n\nfreeze it.\n"
                for f in lint.FAMILIES}
    got = run_against(headless)
    check("frozen: flags four sections that ALL lack the terminator",
          len(got) == len(lint.FAMILIES), f"got {got}")

    # and the real tree must satisfy it
    check("frozen: the repo's four review skills agree",
          run_against({f: (SCRIPTS.parent / "skills" / f"{f}-adversarial-review"
                           / "SKILL.md").read_text(encoding="utf-8")
                       for f in lint.FAMILIES}) == [],
          "the shipped review skills disagree")


# ------------------------------------------------------------ lint pairing ----
def test_lint_pairing_rule():
    """check_pairing_rule(): the prohibition must survive in every REVIEW skill.

    The case that matters is the third one. check_sentinels asks whether the
    words "cross-family" appear; a skill can carry those words while saying
    the opposite, and this test pins that the presence check is blind to it
    and this one is not. Measured 2026-09-12 by the codex leg: three of six
    review skills stated no prohibition, and nothing failed.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    def run_against(bodies):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            for fam, body in bodies.items():
                d = fake / "skills" / f"{fam}-adversarial-review"
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text(body, encoding="utf-8")
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                lint.check_pairing_rule()
                return list(lint.ERRORS)
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors

    good = f"# review\n\nPairing rule: {lint.PAIRING_RULE}.\n"
    allsix = {f: good for f in lint.FAMILIES}
    check("pairing: all six stating the rule pass", run_against(allsix) == [],
          f"got {run_against(allsix)}")

    gone = dict(allsix); gone["claude"] = "# review\n\nnothing about pairing.\n"
    got = run_against(gone)
    check("pairing: flags a review skill that never states it",
          any("claude" in e and "pairing rule" in e for e in got), f"got {got}")

    # THE case check_sentinels cannot see: the phrase is present, the rule is not
    inverted = dict(allsix)
    inverted["opencode"] = ("# review\n\ncross-family review is preferred, but "
                            "the same family may review when quota is tight.\n")
    got = run_against(inverted)
    check("pairing: flags prose that keeps the phrase and drops the rule",
          any("opencode" in e for e in got), f"got {got}")
    check("pairing: and the presence-only sentinel is blind to that same prose",
          lint.CROSS_FAMILY_RE.search(inverted["opencode"]) is not None,
          "CROSS_FAMILY_RE no longer matches — this test's premise is stale")

    # re-wrapping and emphasis must not fire it
    rewrapped = dict(allsix)
    rewrapped["agy"] = ("# review\n\n**the reviewer\nmust  come from a different "
                        "model family\nthan whatever implemented the change.**\n")
    check("pairing: a re-wrapped, bolded rule still matches",
          run_against(rewrapped) == [], f"got {run_against(rewrapped)}")

    # the counterfeit the codex leg landed against the first version: an
    # asterisk inside a word forged the sentence while "*" became a space
    split = dict(allsix)
    split["cursor"] = ("# review\n\nPairing rule: "
                       + lint.PAIRING_RULE.replace("the reviewer", "the*reviewer")
                       + ".\n")
    got = run_against(split)
    check("pairing: an asterisk inside a word cannot forge the sentence",
          any("cursor" in e for e in got), f"got {got}")

    # the emphasis forms that MUST keep passing, or the hardening broke the rule
    for label, body in (
        ("whole sentence bolded", f"# review\n\n**{lint.PAIRING_RULE}.**\n"),
        ("one word bolded",
         "# review\n\nthe **reviewer** must come from a different model family "
         "than whatever implemented the change.\n"),
    ):
        ok = dict(allsix); ok["agy"] = body
        check(f"pairing: {label} still passes", run_against(ok) == [],
              f"got {run_against(ok)}")

    # THE FALSE-ALARM REGRESSION. A round of this check stripped inline code
    # first, and its span pattern paired an escaped tick with a later unmatched
    # one and deleted the compliant sentence between them -- CI failing a
    # correct skill, which is how a linter gets switched off. Nothing may
    # reintroduce that.
    ticks = dict(allsix)
    ticks["opencode"] = ("# review\n\nA literal tick may be escaped as \\`; "
                         + lint.PAIRING_RULE
                         + ". A final unmatched tick: `\n")
    check("pairing: escaped and unmatched ticks do not erase a stated rule",
          run_against(ticks) == [], f"got {run_against(ticks)}")

    # BOUNDARY, pinned deliberately rather than asserted away: a rule that
    # appears only as a code sample PASSES. Two regexes cannot tell a sample
    # from a statement -- an unclosed fence, a four-backtick fence and a
    # double-backtick span each defeated the attempt -- and methodology.md's
    # bounded-properties rule says to declare the scope instead of chasing
    # cases. This test exists so the gap stays a decision, not a surprise.
    sample = dict(allsix)
    sample["grok"] = f"# review\n\nExample only: `{lint.PAIRING_RULE}.`\n"
    check("pairing: a code sample counts (accepted gap, see lint.py)",
          run_against(sample) == [], f"got {run_against(sample)}")

    # and the shipped tree must satisfy it
    check("pairing: the repo's six review skills all state it",
          run_against({f: (SCRIPTS.parent / "skills" / f"{f}-adversarial-review"
                           / "SKILL.md").read_text(encoding="utf-8")
                       for f in lint.FAMILIES}) == [],
          "a shipped review skill does not state the pairing rule")


# --------------------------------------------------------------- lint leaf ----
def test_lint_leaf_rule():
    """check_leaf_rule(): the leaf paragraph must survive in every REVIEW skill.

    Measured 2026-09-13: it was in four of six. claude and grok carried only
    the launch half, so a leg dispatched as a subagent through either would end
    its turn waiting for a notification that never arrives.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    def run_against(bodies):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            for fam, body in bodies.items():
                d = fake / "skills" / f"{fam}-adversarial-review"
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text(body, encoding="utf-8")
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                lint.check_leaf_rule()
                return list(lint.ERRORS)
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors

    good = f"# review\n\n**{lint.LEAF_RULE}**\nThen family-specific prose.\n"
    allsix = {f: good for f in lint.FAMILIES}
    check("leaf: all six carrying it pass", run_against(allsix) == [],
          f"got {run_against(allsix)}")

    gone = dict(allsix); gone["grok"] = "# review\n\nLaunch it in the background.\n"
    got = run_against(gone)
    check("leaf: flags a skill carrying only the launch half",
          any("grok" in e for e in got), f"got {got}")

    # THE case a substring test for "You are a leaf" cannot see
    reworded = dict(allsix)
    reworded["agy"] = good.replace('— block, do not "wait".',
                                   "; block and do not wait.")
    got = run_against(reworded)
    check("leaf: flags a reworded rule that still contains 'You are a leaf'",
          any("agy" in e for e in got), f"got {got}")
    check("leaf: and the phrase really is still present in that prose",
          "You are a leaf" in reworded["agy"],
          "this test's premise is stale")

    check("leaf: the repo's six review skills all carry it",
          run_against({f: (SCRIPTS.parent / "skills" / f"{f}-adversarial-review"
                           / "SKILL.md").read_text(encoding="utf-8")
                       for f in lint.FAMILIES}) == [],
          "a shipped review skill lost the leaf paragraph")


# ------------------------------------------------------------- lint launch ----
def test_lint_launch():
    """check_launch(): the drift-catcher, which shipped with no test of its own.

    lint.py's own comment records why it was rewritten: "a four-leg review
    demonstrated the first version caught only the ONE shape its author had
    mutation-tested against." The rewrite then shipped untested — grep for
    check_launch in this file before 2026-09-13 and there are zero hits — so the
    same failure could recur silently.

    The shapes below are taken from what the shipped skills actually look like
    (`cd "$WORKTREE" && cli ...`, a bare `cli ... \\` with indented
    continuations, a `node "$(...)"` wrapper, --effort with a space, --variant),
    not from what a reader of the checker would imagine — which is the rule
    calibration-journal.md draws from that incident.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    def run_against(body, adapter, role, eff, cli, argv=None):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            d = fake / "skills" / f"{adapter}-{role}"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(body, encoding="utf-8")
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                for lineno, cmdline in lint._launch_commands(
                        d / "SKILL.md", cli):
                    lint._check_effort_spelling(d / "SKILL.md", lineno, cmdline,
                                                adapter, role, eff, argv)
                return list(lint.ERRORS)
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors

    FLAG = {"mechanism": "flag", "flag": "--variant"}
    SUFFIX = {"mechanism": "model_suffix"}
    CONFIG = {"mechanism": "config_only", "applies_to_role": "review",
              "config_key": "k", "config_file": "f"}

    def fence(code):
        return "# doc\n\n```bash\n" + code + "```\n"

    # 1. the bare-command-with-indented-continuations shape, which is what every
    #    shipped launch block looks like and the shape the FIRST version missed
    got = run_against(fence('opencode run --model x \\\n  --print-logs\n'),
                      "opencode", "implement", FLAG, "opencode")
    check("launch: flags a continuation-style launch missing its depth knob",
          any("omits '--variant'" in e for e in got), f"got {got}")

    # 2. `cd "$WORKTREE" && cli ...` — the other shipped shape
    got = run_against(fence('cd "$WORKTREE" && agy -p "x" --effort high\n'),
                      "agy", "implement", SUFFIX, "agy")
    check("launch: flags --effort on a model_suffix adapter behind a cd &&",
          any("model_suffix" in e for e in got), f"got {got}")

    # 3. the node-wrapper shape
    got = run_against(fence('node "$(ls -d ...)" task --effort high\n'),
                      "codex", "review", CONFIG, "node")
    check("launch: flags --effort on a config_only path",
          any("no effort flag at all" in e for e in got), f"got {got}")

    # 4. ...and the SAME adapter's other role must NOT fire: config_only is
    #    role-scoped, and codex's task path really does take --effort
    got = run_against(fence('node "$(ls -d ...)" task --effort high\n'),
                      "codex", "implement", CONFIG, "node", ["task", "--effort", "{EFFORT}"])
    check("launch: does not flag --effort on the role the config does not cover",
          got == [], f"got {got}")

    # 4b. a FLAG scoped to review (claude, 0.6.35): review must carry it, and the
    #     implement role -- no {EFFORT} in its argv -- must not.
    SCOPED = {"mechanism": "flag", "flag": "--effort", "applies_to_role": "review"}
    got = run_against(fence('claude -p --permission-mode plan --model m\n'),
                      "claude", "review", SCOPED, "claude", ["-p", "--model", "{MODEL}", "--effort", "{EFFORT}"])
    check("launch: a review-scoped flag is required on review",
          any("omits '--effort'" in e for e in got), f"got {got}")
    got = run_against(fence('claude -p "$(cat t.md)" --permission-mode acceptEdits --model m --effort high\n'),
                      "claude", "implement", SCOPED, "claude", ["-p", "{PROMPT}", "--model", "{MODEL}"])
    check("launch: ...and refused on the role it does not cover",
          any("passes --effort" in e for e in got), f"got {got}")
    got = run_against(fence('claude -p "$(cat t.md)" --permission-mode acceptEdits --model m\n'),
                      "claude", "implement", SCOPED, "claude", ["-p", "{PROMPT}", "--model", "{MODEL}"])
    check("launch: ...where a launch without it is clean", got == [], f"got {got}")

    # 5. carrying a neighbour's knob ALONGSIDE the right one is still wrong —
    #    the shape a lead produces by copying between legs
    got = run_against(fence('opencode run --variant xhigh --effort high\n'),
                      "opencode", "review", FLAG, "opencode")
    check("launch: flags a neighbouring family's knob even beside the right one",
          any("wrong-flag-name" in e for e in got), f"got {got}")

    # 6. prose is not a launch. The false-positive direction, and the reason the
    #    checker parses fences instead of grepping lines. The sentence has to be
    #    one the fence rule is the ONLY thing saving: a standalone `opencode`
    #    token plus a wrong knob and no right one, so removing the fence
    #    restriction makes it fire. An earlier version of this case wrote
    #    "to opencode;" and passed for the wrong reason — the CLI match needs
    #    the name space-delimited, so it was never a candidate either way, and
    #    the mutant that drops the fence check survived it.
    got = run_against("Do not run opencode with --effort here.\n",
                      "opencode", "review", FLAG, "opencode")
    check("launch: prose mentioning a flag is not a launch", got == [], f"got {got}")

    # 7. a fenced block that is not a launch of THIS cli
    got = run_against(fence('git worktree add -b x ../y "$BASE"\n'),
                      "opencode", "review", FLAG, "opencode")
    check("launch: a fenced non-launch is not inspected", got == [], f"got {got}")

    # 8. the correct command must stay silent, or the check is unusable
    got = run_against(fence('opencode run --model x --variant xhigh\n'),
                      "opencode", "review", FLAG, "opencode")
    check("launch: a correct launch produces nothing", got == [], f"got {got}")

    # and the shipped tree must satisfy it
    real, real_errors = lint.ERRORS, None
    try:
        lint.ERRORS = []
        lint.check_launch()
        check("launch: the shipped skills pass their own launch rule",
              lint.ERRORS == [], f"got {lint.ERRORS}")
    finally:
        lint.ERRORS = real

    # 0.6.28: a role may run another CLI than the adapter's; the review skill's
    # companion FALLBACK must still not pass an effort flag, and a leg-cmd.sh
    # composition line is not a launch -- but a real launch stays checked.
    def codex_review_errors(code):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            (fake / "data").mkdir()
            launch = json.loads((SCRIPTS.parent / "data" / "launch.json").read_text(encoding="utf-8"))
            (fake / "data" / "launch.json").write_text(json.dumps({"codex": launch["codex"]}), encoding="utf-8")
            d = fake / "skills" / "codex-adversarial-review"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(fence(code), encoding="utf-8")
            (fake / "skills" / "codex-implement").mkdir()
            (fake / "skills" / "codex-implement" / "SKILL.md").write_text("# doc\n", encoding="utf-8")
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                lint.check_launch()
                return [e for e in lint.ERRORS if "codex-adversarial-review" in str(e)]
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors
    got = codex_review_errors('node "$SCRIPT" adversarial-review --base "$B" --effort high "$FOCUS"\n')
    check("launch: the companion fallback in the review skill may not pass --effort",
          any("fallback launch passes --effort" in str(e) for e in got), got)
    got = codex_review_errors('eval "$("$DEV_LEAD/scripts/leg-cmd.sh" codex review --model m --effort high)"\n')
    check("launch: a leg-cmd.sh composition line is not checked as a codex launch", got == [], got)
    got = codex_review_errors('codex exec -C "$T" -s read-only -m m -- - < p.md  # leg-cmd.sh codex review\n')
    check("launch: a real codex exec launch mentioning leg-cmd.sh is still checked",
          any("model_reasoning_effort" in str(e) for e in got), got)

    # 0.6.35: applies_to_role must name a role the adapter has.
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        (fake / "data").mkdir()
        launch = json.loads((SCRIPTS.parent / "data" / "launch.json").read_text(encoding="utf-8"))
        bad = json.loads(json.dumps(launch["claude"]))
        bad["effort"]["applies_to_role"] = "reveiw"
        (fake / "data" / "launch.json").write_text(json.dumps({"claude": bad}), encoding="utf-8")
        real_root, real_errors = lint.ROOT, lint.ERRORS
        try:
            lint.ROOT, lint.ERRORS = fake, []
            lint.check_launch()
            got = list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS = real_root, real_errors
    check("launch: an effort scoped to a role the adapter does not have is refused",
          any("scopes its effort to role 'reveiw'" in str(e) for e in got), got)


# --------------------------------------------------- lint delegate guardrails ----
def test_lint_delegate_guardrails():
    """check_delegate_guardrails(): dispatch safety must stay fail-closed.

    These are review findings against real role skills.  A presence-only
    check would miss the unsafe `remote-refs.after` handoff, so exercise both
    required and forbidden forms in a synthetic tree.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    good = {
        "skills/grok-adversarial-review/SKILL.md": "--deny 'MCPTool(*)'\n",
        "skills/grok-implement/SKILL.md":
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" "$RUN_DIR/remote-refs.before" || exit 1\n',
        "skills/cursor-implement/SKILL.md":
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" "$RUN_DIR/remote-refs.before" || exit 1\n',
        "skills/agy-implement/SKILL.md":
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" "$RUN_DIR/remote-refs.before" || exit 1\n',
        "skills/opencode-implement/SKILL.md":
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" "$RUN_DIR/remote-refs.before" || exit 1\n',
    }

    def run_against(files):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            for name, body in files.items():
                path = fake / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                lint.check_delegate_guardrails()
                return list(lint.ERRORS)
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors

    check("delegate: hardened policy passes", run_against(good) == [],
          f"got {run_against(good)}")

    no_mcp_deny = dict(good)
    no_mcp_deny["skills/grok-adversarial-review/SKILL.md"] = "--tools read_file\n"
    got = run_against(no_mcp_deny)
    check("delegate: flags a Grok review without MCP denial",
          any("MCPTool" in e for e in got), f"got {got}")

    raw_after_snapshot = dict(good)
    raw_after_snapshot["skills/grok-implement/SKILL.md"] += "remote-refs.after\n"
    got = run_against(raw_after_snapshot)
    check("delegate: flags a raw refs-after handoff",
          any("remote-refs.after" in e for e in got), f"got {got}")

    non_aborting_check = dict(good)
    non_aborting_check["skills/grok-implement/SKILL.md"] = (
        '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" '
        '"$RUN_DIR/remote-refs.before"\n'
    )
    got = run_against(non_aborting_check)
    check("delegate: flags a ref check that can continue after failure",
          any("|| exit 1" in e for e in got), f"got {got}")

    missing_check = dict(good)
    missing_check["skills/cursor-implement/SKILL.md"] = "git status --short\n"
    got = run_against(missing_check)
    check("delegate: flags a Cursor handoff without fail-closed ref check",
          any("cursor-implement" in e and "snapshot-refs.sh" in e for e in got),
          f"got {got}")

    # agy and opencode shipped the check WITHOUT `|| exit 1` until 2026-09-25:
    # a detected push printed its delta and the handoff carried on. Both are
    # in the table now, so the open form fails lint for every implement skill.
    for skill in ("agy", "opencode"):
        open_form = dict(good)
        open_form[f"skills/{skill}-implement/SKILL.md"] = (
            '"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" '
            '"$RUN_DIR/remote-refs.before"\n'
        )
        got = run_against(open_form)
        check(f"delegate: flags a {skill} ref check that can continue after failure",
              any(f"{skill}-implement" in e and "|| exit 1" in e for e in got),
              f"got {got}")


# ------------------------------------------------ lint delegate audit trails ----
def test_lint_delegate_audit_trails():
    """check_delegate_audit_trails(): preserve run identity and tier pairing."""
    sys.path.insert(0, str(SCRIPTS))
    import lint

    def bash_fence(code):
        return "```bash\n" + code + "```\n"

    good = {
        "skills/cursor-adversarial-review/SKILL.md": (
            "--output-format json\n"
            '"$(cat \"$RUN_DIR/prompt.md\")" > "$RUN_DIR/review.json" 2> "$RUN_DIR/review.err"\n'
            "request_id\n"
        ),
        "skills/cursor-implement/SKILL.md": (
            "--output-format json\n"
            '"$(cat \"$RUN_DIR/task.md\")" > "$RUN_DIR/impl.json" 2> "$RUN_DIR/impl.err"\n'
            "request_id\n"
        ),
        "skills/agy-adversarial-review/SKILL.md": bash_fence(
            "AGY_MODEL=gemini-3.7-flash-high\n"
            '--model "$AGY_MODEL"\n'
        ),
        "skills/agy-implement/SKILL.md": bash_fence(
            "AGY_MODEL=gemini-3.7-flash-high\n"
            '--model "$AGY_MODEL"\n'
        ),
    }

    def run_against(files):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            for name, body in files.items():
                path = fake / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            real_root, real_errors = lint.ROOT, lint.ERRORS
            try:
                lint.ROOT, lint.ERRORS = fake, []
                lint.check_delegate_audit_trails()
                return list(lint.ERRORS)
            finally:
                lint.ROOT, lint.ERRORS = real_root, real_errors

    check("audit: complete JSON and tier policy passes", run_against(good) == [],
          f"got {run_against(good)}")

    # The fixture APPENDS to a valid body instead of replacing it. Replacing it
    # tripped the required-fragments loop three times over (no json, no redirect,
    # no request_id) and the assertion matched one of THOSE errors, so the text
    # rule could be deleted with this case still green. Measured 2026-09-13.
    text_output = dict(good)
    text_output["skills/cursor-adversarial-review/SKILL.md"] = (
        good["skills/cursor-adversarial-review/SKILL.md"] + "--output-format text\n")
    got = run_against(text_output)
    check("audit: flags Cursor text output",
          any("text output drops request_id" in e for e in got), f"got {got}")

    merged_streams = dict(good)
    merged_streams["skills/cursor-implement/SKILL.md"] = (
        "--output-format json\n"
        '"$(cat \"$RUN_DIR/task.md\")" > "$RUN_DIR/impl.json" 2>&1\nrequest_id\n'
    )
    got = run_against(merged_streams)
    check("audit: flags Cursor JSON contaminated by stderr", any("impl.err" in e for e in got),
          f"got {got}")

    missing_request_id = dict(good)
    missing_request_id["skills/cursor-adversarial-review/SKILL.md"] = (
        "--output-format json\n"
        '"$(cat \"$RUN_DIR/prompt.md\")" > "$RUN_DIR/review.json" 2> "$RUN_DIR/review.err"\n'
    )
    got = run_against(missing_request_id)
    check("audit: flags Cursor output with no request identity",
          any("request_id" in e for e in got), f"got {got}")

    stale_effort = dict(good)
    stale_effort["skills/agy-adversarial-review/SKILL.md"] = bash_fence(
        "AGY_MODEL=gemini-3.7-flash-high\nAGY_EFFORT=high\n"
        '--model "$AGY_MODEL"\n--effort "$AGY_EFFORT"\n'
    )
    got = run_against(stale_effort)
    check("audit: flags a reintroduced AGY_EFFORT as redundant",
          any("redundant" in e for e in got), f"got {got}")

    hardcoded_effort = dict(good)
    hardcoded_effort["skills/agy-adversarial-review/SKILL.md"] = bash_fence(
        "AGY_MODEL=gemini-3.7-flash-high\n"
        '--model "$AGY_MODEL"\n--effort high\n'
    )
    got = run_against(hardcoded_effort)
    check("audit: flags a hardcoded --effort flag with no AGY_EFFORT variable",
          any("redundant" in e for e in got), f"got {got}")

    prose_only_effort = dict(good)
    prose_only_effort["skills/agy-adversarial-review/SKILL.md"] = (
        "Do not pass `--effort` — the model suffix IS the effort.\n\n"
        + bash_fence(
            "AGY_MODEL=gemini-3.7-flash-high\n"
            '--model "$AGY_MODEL"\n'
        )
    )
    got = run_against(prose_only_effort)
    check("audit: does NOT flag --effort mentioned only in prose, outside the code fence",
          not any("redundant" in e for e in got), f"got {got}")


# ------------------------------------------------------------- claim audit ----
def test_claim_audit(tmp):
    """claim-audit.py: what it must flag, what it must NOT, and its contract.

    The must-NOT half is the load-bearing one. A bare absolute-word filter was
    rejected on volume (16/13/38 hits on real commits), because an output nobody
    reads defeats the point. If a plain "never"/"cannot" sentence starts matching
    again, this test fails and that regression is visible.
    """
    audit = SCRIPTS / "claim-audit.py"
    repo = tmp / "claims"
    make_repo(repo, commits=1)
    base = git(repo, "rev-parse", "HEAD").stdout.strip()

    (repo / "doc.md").write_text(
        "Both cases return the same shape, so this is safe.\n"        # sameness
        "Called for every read in the adapter.\n"                     # absolute+path
        "Never push to a remote.\n"                                   # absolute, NO path noun
        "The cap cannot be raised by a caller.\n"                     # 'cannot' + 'caller'
        "Feasibility is not the obstacle.\n"                          # unlintable by design
    )
    (repo / "code.py").write_text(
        "# every request carries the key\n"                           # comment, flagged
        "x = 'every request carries the key'\n"                       # code line, not prose
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add prose")

    r = run("python3", audit, repo, f"{base}...HEAD")
    out = r.stdout

    check("claim-audit: exits 0 even with hits (worklist, not a gate)",
          r.returncode == 0, f"rc={r.returncode}")
    check("claim-audit: flags a sameness claim",
          "doc.md:1" in out and "sameness" in out, out)
    check("claim-audit: flags an absolute quantifying over code paths",
          "doc.md:2" in out, out)
    check("claim-audit: does NOT flag a bare absolute with no code-path noun",
          "doc.md:3" not in out, out)
    # line 4 carries 'cannot' AND 'caller'. Without this assertion, restoring
    # 'cannot' to the alternatives would flag it and the suite would still pass
    # — which made this test's own docstring false about what it protects.
    check("claim-audit: does NOT flag 'cannot' even beside a code-path noun",
          "doc.md:4" not in out, out)
    check("claim-audit: flags a comment line in a code file",
          "code.py:1" in out, out)
    check("claim-audit: does NOT flag a non-comment code line",
          "code.py:2" not in out, out)
    check("claim-audit: asks the anchoring question regardless of class",
          "which test goes red" in out, out)
    # question 2 was REMOVED: a three-family panel agreed no prompt defeats
    # proxy-for-property rationalisation, because the model asked is the one
    # that made the substitution. Asserted so it cannot creep back unnoticed.
    check("claim-audit: does NOT ask the property-vs-proxy question",
          "proxy for it" not in out, out)
    check("claim-audit: says a nearby test is not an answer",
          "runs nearby is not an answer" in out or "not an answer" in out, out)
    check("claim-audit: prints a machine-readable hit count",
          "claim-audit: hits=" in out, out)
    # Named for what it asserts, and nothing more. An earlier name claimed this
    # showed question 2 "reporting" the shape; a missing label shows only that
    # nothing matched. Question 2 no longer exists at all — the sentence it was
    # meant for is now the cross-family leg's to catch, and this line records
    # only that no pattern reaches it.
    check("claim-audit: does NOT match the unlintable shape",
          "doc.md:5" not in out, out)

    clean = tmp / "clean"
    make_repo(clean, commits=1)
    cbase = git(clean, "rev-parse", "HEAD").stdout.strip()
    (clean / "plain.md").write_text("This adapter reads the job endpoint.\n")
    git(clean, "add", "-A")
    git(clean, "commit", "-qm", "neutral prose")
    r2 = run("python3", audit, clean, f"{cbase}...HEAD")
    # a silent run must NOT read as "anchored" -- that false confidence is the
    # governance risk the review panel named, so the wording is asserted
    check("claim-audit: silent when nothing risky was added",
          r2.returncode == 0 and "nothing matched" in r2.stdout, r2.stdout)
    check("claim-audit: a silent run denies that it means 'anchored'",
          "NOT 'the prose is anchored'" in r2.stdout, r2.stdout)
    check("claim-audit: a silent run still prints hits=0",
          "claim-audit: hits=0" in r2.stdout, r2.stdout)

    r3 = run("python3", audit, repo)
    check("claim-audit: wrong arity exits 2, distinct from a clean run",
          r3.returncode == 2, f"rc={r3.returncode}")


def test_claim_audit_parsing(tmp):
    """claim-audit.py: the diff parse, the range, and the exit-code contract.

    Every case here is a way the script reported a wrong line, a wrong path, or
    nothing at all — while exiting 0, which reads as a clean run. They were
    found by cross-model review of the commit that introduced the script, and
    each assertion below is the repro that review named.
    """
    audit = SCRIPTS / "claim-audit.py"

    # A hunk body is content, never a header. "+++ emphasis" reaches the diff as
    # "++++ emphasis" and was skipped WITHOUT advancing the counter, while
    # "++ b/fake.md" reached it as "+++ b/fake.md" and was adopted as the path —
    # together they filed the real claim under fake.md:0.
    repo = tmp / "hunk"
    make_repo(repo, commits=1)
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "doc.md").write_text(
        "+++ emphasis, not a diff header\n"
        "++ b/fake.md\n"
        "Called for every read in the adapter.\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "prose that looks like a diff")
    out = run("python3", audit, repo, f"{base}...HEAD").stdout
    check("claim-audit: a '+++' prose line does not shift later line numbers",
          "doc.md:3" in out, out)
    check("claim-audit: a '++ b/path' content line is not read as a header",
          "fake.md" not in out, out)

    # diff.noprefix=true makes git emit "+++ doc.md", which matched no "+++ b/"
    # and lost every path — silently, since a hit-free run is a normal outcome.
    npx = tmp / "noprefix"
    make_repo(npx, commits=1)
    nbase = git(npx, "rev-parse", "HEAD").stdout.strip()
    git(npx, "config", "diff.noprefix", "true")
    (npx / "doc.md").write_text("Called for every read in the adapter.\n")
    git(npx, "add", "-A")
    git(npx, "commit", "-qm", "prose under noprefix")
    out = run("python3", audit, npx, f"{nbase}...HEAD").stdout
    check("claim-audit: reports paths under diff.noprefix=true", "doc.md:1" in out, out)

    # the mirror image: a real path under b/ must not be stripped to its tail
    bx = tmp / "bpath"
    make_repo(bx, commits=1)
    bbase = git(bx, "rev-parse", "HEAD").stdout.strip()
    # noprefix ON is what makes this discriminate: without it the old raw[6:]
    # also produced "b/doc.md" and the assertion passed with the fix reverted.
    git(bx, "config", "diff.noprefix", "true")
    (bx / "b").mkdir()
    (bx / "b" / "doc.md").write_text("Called for every read in the adapter.\n")
    git(bx, "add", "-A")
    git(bx, "commit", "-qm", "prose under a b/ directory")
    out = run("python3", audit, bx, f"{bbase}...HEAD").stdout
    check("claim-audit: a real 'b/' path is not stripped to its tail",
          "b/doc.md:1" in out, out)

    # "git diff A...B" reads merge-base..B, but "git log A...B" reads BOTH
    # sides: the other branch's commit messages were audited although its prose
    # never was, so a claim could be reported from a commit the diff never saw.
    div = tmp / "diverged"
    make_repo(div, commits=1)
    trunk = git(div, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git(div, "checkout", "-q", "-b", "side")
    (div / "side.md").write_text("Called for every write on the side branch.\n")
    git(div, "add", "-A")
    git(div, "commit", "-qm", "side: every request is retried here")
    git(div, "checkout", "-q", trunk)
    (div / "main.md").write_text("Called for every read on the trunk.\n")
    git(div, "add", "-A")
    git(div, "commit", "-qm", "trunk prose")
    out = run("python3", audit, div, f"side...{trunk}").stdout
    check("claim-audit: audits the trunk side of a diverged range",
          "main.md:1" in out, out)
    check("claim-audit: does not audit the other side's prose", "side.md" not in out, out)
    check("claim-audit: does not audit the other side's commit message",
          "every request is retried" not in out, out)

    # text=True raised UnicodeDecodeError before the return code was read, so an
    # undecodable byte exited 1 — neither "ran" (0) nor "could not run" (2).
    bad = tmp / "badbytes"
    make_repo(bad, commits=1)
    ubase = git(bad, "rev-parse", "HEAD").stdout.strip()
    (bad / "doc.md").write_bytes(b"Called for every read \xff in the adapter.\n")
    git(bad, "add", "-A")
    git(bad, "commit", "-qm", "undecodable prose")
    r = run("python3", audit, bad, f"{ubase}...HEAD")
    check("claim-audit: undecodable bytes still exit 0, not 1",
          r.returncode == 0, f"rc={r.returncode} {r.stderr}")
    # and the sentence must SURVIVE the replacement, not be swallowed by it
    check("claim-audit: the undecodable line is still reported",
          "doc.md:1" in r.stdout, r.stdout)

    r = run("python3", audit, bad, f"{ubase}...HEAD",
            env=dict(os.environ, PYTHONIOENCODING="ascii:strict"))
    check("claim-audit: a strict stdout encoding does not break the exit contract",
          r.returncode == 0, f"rc={r.returncode} {r.stderr[-200:]}")

    r = run(sys.executable, audit, bad, f"{ubase}...HEAD",
            env=dict(os.environ, PATH="/nonexistent"))
    check("claim-audit: no git on PATH exits 2, not 1",
          r.returncode == 2, f"rc={r.returncode} {r.stderr}")

    # diff.interHunkContext can merge neighbouring hunks and carry the context
    # lines between them, even under --unified=0. Those lines occupy lines in
    # the new file; not counting them shifted every later claim in the hunk.
    ihc = tmp / "interhunk"
    make_repo(ihc, commits=1)
    ibase = git(ihc, "rev-parse", "HEAD").stdout.strip()
    (ihc / "doc.md").write_text("".join(f"l{i}\n" for i in range(1, 11)))
    git(ihc, "add", "-A")
    git(ihc, "commit", "-qm", "ten lines")
    mid = git(ihc, "rev-parse", "HEAD").stdout.strip()
    body = ["l%d\n" % i for i in range(1, 11)]
    body[0] = "Called for every read here.\n"
    body[9] = "Called for every write here.\n"
    (ihc / "doc.md").write_text("".join(body))
    git(ihc, "add", "-A")
    git(ihc, "commit", "-qm", "two distant claims")
    git(ihc, "config", "diff.interHunkContext", "100")
    out = run("python3", audit, ihc, f"{mid}...HEAD").stdout
    check("claim-audit: context lines still advance the line number",
          "doc.md:1" in out and "doc.md:10" in out, out)

    # a bare ".." names no endpoints; git rejects it and so must this
    r = run("python3", audit, ihc, "..")
    check("claim-audit: a bare '..' exits 2, not a clean run",
          r.returncode == 2, f"rc={r.returncode} {r.stderr}")

    # "A..B" resolves through merge-base too: comparing diverged TIPS reports a
    # sentence the other branch DELETED as one this range added.
    two = tmp / "twodot"
    make_repo(two, commits=1)
    (two / "doc.md").write_text("Called for every read in the adapter.\n")
    git(two, "add", "-A")
    git(two, "commit", "-qm", "seed the claim")
    trunk2 = git(two, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git(two, "checkout", "-q", "-b", "deleter")
    (two / "doc.md").write_text("nothing risky here\n")
    git(two, "add", "-A")
    git(two, "commit", "-qm", "delete the claim")
    git(two, "checkout", "-q", trunk2)
    (two / "other.md").write_text("plain trunk prose\n")
    git(two, "add", "-A")
    git(two, "commit", "-qm", "unrelated trunk work")
    out = run("python3", audit, two, f"deleter..{trunk2}").stdout
    check("claim-audit: a two-dot range does not report the other side's deletion",
          "doc.md" not in out, out)

    # Three shapes the GitHub review leg found, each of which produced NO hit
    # at all — the silent-miss failure, not a wrong line.
    shapes = tmp / "shapes"
    make_repo(shapes, commits=1)
    sbase = git(shapes, "rev-parse", "HEAD").stdout.strip()
    # git C-quotes a non-ASCII path by default, so "+++ b/" matched nothing and
    # the whole file was skipped
    (shapes / "文檔.md").write_text("Called for every read in the adapter.\n")
    # a hard wrap splits the claim; neither physical line carries it
    (shapes / "wrapped.md").write_text(
        "The adapter is documented. Every\nrequest is processed by the handler.\n")
    # a comment that trails code, and a docstring body that opens with prose
    (shapes / "mod.py").write_text(
        '"""Overview\n'
        "Called for every write in the adapter.\n"
        '"""\n'
        "run()  # every request is accepted\n"
    )
    git(shapes, "add", "-A")
    git(shapes, "commit", "-qm", "three shapes")
    out = run("python3", audit, shapes, f"{sbase}...HEAD").stdout
    check("claim-audit: a C-quoted non-ASCII path is decoded, not skipped",
          "文檔.md:1" in out, out)
    check("claim-audit: a claim split by a hard wrap is still caught",
          "wrapped.md:1" in out and "wrapped" in out, out)
    check("claim-audit: a wrapped hit is not also reported as its second line",
          "wrapped.md:2" not in out, out)
    check("claim-audit: a comment trailing code is prose too",
          "mod.py:4" in out, out)
    # KNOWN GAP, asserted so it cannot drift silently: deciding that a line sits
    # inside a docstring needs the file, not the diff. If this ever starts
    # passing, the limitation note in claim-audit.py's docstring is stale.
    check("claim-audit: a docstring BODY line is still missed (known gap)",
          "mod.py:2" not in out, out)

    # Second review pass on the fixes above. Both were reported as still
    # producing a clean audit, and both did.
    ctx = tmp / "ctxjoin"
    make_repo(ctx, commits=1)
    (ctx / "w.md").write_text(
        "The adapter is documented. Every\nitem is logged by the handler.\n")
    # A file NOT touched at all never reaches the diff, so it cannot exercise
    # the context guards. These two do: each has unchanged claim-bearing lines
    # that -U1 pulls in as context beside a real edit.
    (ctx / "ctxclaim.md").write_text(
        "Called for every read in the adapter.\nplain second line\n")
    (ctx / "guard.md").write_text(
        "plain first line\nNothing changes here. Every\n"
        "row is validated by the loader.\nplain last line\n")
    (ctx / "m.py").write_text("run()\n")
    git(ctx, "add", "-A")
    git(ctx, "commit", "-qm", "wrapped base")
    cbase2 = git(ctx, "rev-parse", "HEAD").stdout.strip()
    # only the SECOND line of the wrapped sentence changes; "Every" is context
    (ctx / "w.md").write_text(
        "The adapter is documented. Every\nrequest is processed by the handler.\n")
    # compact trailing markers, valid in Python and JS respectively
    (ctx / "m.py").write_text(
        "run() #every request is accepted\n"
        "run();// every request is accepted\n"
        "url = 'https://x/every/read'\n"
    )
    # edit only line 2, so line 1's standing claim arrives as context
    (ctx / "ctxclaim.md").write_text(
        "Called for every read in the adapter.\nplain second line, edited\n")
    # edit lines 1 and 4, so the unchanged wrapped claim on 2-3 arrives as two
    # ADJACENT context lines — the shape that would join if the guard were gone
    (ctx / "guard.md").write_text(
        "plain first line, edited\nNothing changes here. Every\n"
        "row is validated by the loader.\nplain last line, edited\n")
    git(ctx, "add", "-A")
    git(ctx, "commit", "-qm", "edit one wrapped line, add compact comments")
    out = run("python3", audit, ctx, f"{cbase2}...HEAD").stdout
    check("claim-audit: joins a wrapped claim through an UNCHANGED context line",
          "w.md:1" in out and "wrapped" in out, out)
    check("claim-audit: flags '#' with no space after it",
          "m.py:1" in out, out)
    check("claim-audit: flags '//' with no space before it",
          "m.py:2" in out, out)
    # precision guards: context is joinable, never reportable on its own, and a
    # URL's "//" is not a comment opener
    check("claim-audit: a standing claim on a context line is not reported",
          "ctxclaim.md" not in out, out)
    check("claim-audit: two context lines are not joined into a new claim",
          "guard.md" not in out, out)
    check("claim-audit: a bare URL is not read as a trailing comment",
          "m.py:3" not in out, out)

    # The before/after measurement the skill step prescribes only works if the
    # SECOND run is worktree-aware: the round commits its checkpoint BEFORE the
    # audit, so a correction is an uncommitted edit. A two-endpoint range
    # compares two commits and cannot see it, and the count would not move even
    # after a real downgrade — which made the documented instruction wrong.
    meas = tmp / "measure"
    make_repo(meas, commits=1)
    mbase = git(meas, "rev-parse", "HEAD").stdout.strip()
    (meas / "d.md").write_text("Called for every read in the adapter.\n")
    git(meas, "add", "-A")
    git(meas, "commit", "-qm", "checkpoint, made before the audit")
    before = run("python3", audit, meas, f"{mbase}...HEAD").stdout
    check("claim-audit: the first run counts the hit", "hits=1" in before, before)
    # the lead downgrades the sentence and does NOT commit — the round's state
    (meas / "d.md").write_text("Called for the two reads measured in the adapter.\n")
    ranged = run("python3", audit, meas, f"{mbase}...HEAD").stdout
    check("claim-audit: a two-endpoint range cannot see the uncommitted downgrade",
          "hits=1" in ranged, ranged)
    worktree = run("python3", audit, meas, mbase).stdout
    check("claim-audit: the bare revision sees it, so the count actually moves",
          "hits=0" in worktree, worktree)

    # The two range forms do not audit the same input classes, so they are not a
    # before and an after: ranged reads prose AND commit messages, bare reads
    # prose only. Comparing them reports a fall caused by the excluded class.
    cls = tmp / "classes"
    make_repo(cls, commits=1)
    cbase3 = git(cls, "rev-parse", "HEAD").stdout.strip()
    (cls / "plain.md").write_text("nothing risky in this prose\n")
    git(cls, "add", "-A")
    git(cls, "commit", "-qm", "checkpoint: every read is now cached")
    ranged2 = run("python3", audit, cls, f"{cbase3}...HEAD").stdout
    bare2 = run("python3", audit, cls, cbase3).stdout
    check("claim-audit: a ranged run counts a commit-message claim",
          "hits=1" in ranged2, ranged2)
    check("claim-audit: a bare run counts prose only, so the same state reads 0",
          "hits=0" in bare2, bare2)

    # Seventh connector pass.
    sev = tmp / "seventh"
    make_repo(sev, commits=1)
    (sev / "ins.md").write_text("Called for every read in the adapter.\n")
    git(sev, "add", "-A")
    git(sev, "commit", "-qm", "seventh base")
    ybase = git(sev, "rev-parse", "HEAD").stdout.strip()
    # inserting a neutral line ABOVE a standing claim must not report that claim
    # at the inserted line — the range added no assertion
    (sev / "ins.md").write_text(
        "A neutral heading\nCalled for every read in the adapter.\n")
    # a claim hard-wrapped across THREE lines: no adjacent PAIR holds both terms
    (sev / "three.md").write_text("Every\nauthenticated API\nrequest is handled.\n")
    # uppercase suffixes are valid spellings of the same prose formats
    (sev / "READ.MD").write_text("Called for every write in the adapter.\n")
    (sev / "guide.RST").write_text("Reads are never retried.\n")
    git(sev, "add", "-A")
    git(sev, "commit", "-qm", "seventh change")
    out = run("python3", audit, sev, f"{ybase}...HEAD").stdout
    check("claim-audit: inserting above a standing claim does not report it",
          "ins.md" not in out, out)
    check("claim-audit: reassembles a claim wrapped over three lines",
          "three.md:1" in out, out)
    check("claim-audit: an uppercase .MD suffix is prose", "READ.MD:1" in out, out)
    check("claim-audit: an uppercase .RST suffix is prose", "guide.RST:1" in out, out)

    # a line that asserts TWICE, the second time past the window: search() found
    # only the first match of each class, so the second was tagged but invisible
    twice = tmp / "twice"
    make_repo(twice, commits=1)
    zbase = git(twice, "rev-parse", "HEAD").stdout.strip()
    (twice / "t.md").write_text(
        "Every request is accepted. " + "padding word " * 8
        + " Reads are never retried.\n")
    git(twice, "add", "-A")
    git(twice, "commit", "-qm", "asserted twice")
    out = run("python3", audit, twice, f"{zbase}...HEAD").stdout
    check("claim-audit: a second assertion on the same line stays visible",
          "Every request" in out and "never retried" in out, out)

    # Sixth connector pass.
    six = tmp / "sixth"
    make_repo(six, commits=1)
    (six / "p.md").write_text("The responses are identical\nfor ordinary clients.\n")
    (six / "q.sql").write_text("SELECT 1;\n")
    (six / "s.sh").write_text("echo hi\n")
    # a self-standing claim with an unrelated line above it: joining these would
    # report the claim at the WRONG line. This broke when the join guard was
    # first relaxed for the case below, and nothing had pinned it.
    (six / "sep.md").write_text("an opening line with no claim\nplaceholder\n")
    git(six, "add", "-A")
    git(six, "commit", "-qm", "sixth base")
    xbase = git(six, "rev-parse", "HEAD").stdout.strip()
    # unchanged first half carries sameness; the EDITED second half independently
    # carries an absolute, so the second half reports itself and the sameness
    # plus its subject vanish unless the pair is joined
    (six / "p.md").write_text(
        "The responses are identical\nfor every authenticated client.\n")
    (six / "q.sql").write_text("SELECT 1; -- every row is returned\n")
    # a LONG OPTION is not a comment opener; without that guard this line would
    # be read as prose and flagged, which is how "--" earns its keep quietly
    (six / "s.sh").write_text("run --all paths now\n")
    (six / "sep.md").write_text(
        "an opening line with no claim\nCalled for every read in the adapter.\n")
    git(six, "add", "-A")
    git(six, "commit", "-qm", "sixth change")
    out = run("python3", audit, six, f"{xbase}...HEAD").stdout
    check("claim-audit: joins when the CONTEXT half carries the other class",
          "p.md:1" in out and "absolute+sameness" in out, out)
    check("claim-audit: a trailing '--' comment is prose (SQL, Lua)",
          "q.sql:1" in out, out)
    check("claim-audit: a long option is not read as a trailing comment",
          "s.sh" not in out, out)
    check("claim-audit: a self-standing claim is not dragged onto the line above",
          "sep.md:2" in out and "sep.md:1" not in out, out)

    # a long line where BOTH classes fire and they are far apart: an entry
    # tagged with two classes must not show only one of them
    two = tmp / "twoclass"
    make_repo(two, commits=1)
    tbase = git(two, "rev-parse", "HEAD").stdout.strip()
    (two / "t.md").write_text(
        "The responses are identical. " + "padding word " * 8
        + " Every request is accepted.\n")
    git(two, "add", "-A")
    git(two, "commit", "-qm", "two classes, far apart")
    out = run("python3", audit, two, f"{tbase}...HEAD").stdout
    check("claim-audit: an entry tagged with both classes shows both",
          "identical" in out and "Every request" in out, out)

    # Fifth connector pass. Both against the two fixes above.
    half = tmp / "halves"
    make_repo(half, commits=1)
    (half / "p.md").write_text(
        "One response from this route\nis identical for authenticated clients.\n")
    git(half, "add", "-A")
    git(half, "commit", "-qm", "wrapped predicate base")
    hbase = git(half, "rev-parse", "HEAD").stdout.strip()
    # only the FIRST half changes, and it classifies on its own — the predicate
    # carrying the second class lives on the unchanged line below it
    (half / "p.md").write_text(
        "Every response from this route\nis identical for authenticated clients.\n")
    git(half, "add", "-A")
    git(half, "commit", "-qm", "assert an absolute over an existing predicate")
    out = run("python3", audit, half, f"{hbase}...HEAD").stdout
    check("claim-audit: an added half is joined to its predicate, not reported bare",
          "is identical for authenticated clients." in out, out)
    check("claim-audit: the join picks up the class only the other half carries",
          "absolute+sameness" in out, out)

    # a match can START inside the window and END outside it, because ABSOLUTE
    # allows 40 characters between its two terms
    wide2 = tmp / "widematch"
    make_repo(wide2, commits=1)
    w2base = git(wide2, "rev-parse", "HEAD").stdout.strip()
    (wide2 / "w.md").write_text(
        "word " * 15 + "Every " + "y" * 33
        + " request is fine, and there is more trailing text here past the width\n")
    git(wide2, "add", "-A")
    git(wide2, "commit", "-qm", "a match that ends past the cut")
    out = run("python3", audit, wide2, f"{w2base}...HEAD").stdout
    check("claim-audit: a match ending past the cut is not chopped mid-claim",
          "request" in out, out)

    # Fourth connector pass: the entry must carry the claim. A long sentence
    # whose absolute lands past the cut was shown as neutral lead-in prose, so
    # the hit read as a false positive and neither question could be answered.
    lng = tmp / "longline"
    make_repo(lng, commits=1)
    lbase = git(lng, "rev-parse", "HEAD").stdout.strip()
    pad = ("This paragraph is ordinary introductory prose that carries no claim "
           "whatsoever and simply runs on for a while. ")
    (lng / "long.md").write_text(pad + "requests always succeed.\n")
    (lng / "short.md").write_text("Called for every read in the adapter.\n")
    git(lng, "add", "-A")
    git(lng, "commit", "-qm", "a long claim-bearing line")
    out = run("python3", audit, lng, f"{lbase}...HEAD").stdout
    check("claim-audit: a long line's entry still shows the matched claim",
          "always succeed" in out, out)
    check("claim-audit: a short line is shown whole, unwindowed",
          "Called for every read in the adapter." in out and "…Called" not in out, out)

    # Third connector pass. Reverse word order, and a context half that
    # classifies on its own but is not reportable on its own.
    rev = tmp / "reverse"
    make_repo(rev, commits=1)
    (rev / "s.md").write_text("The responses are identical\nfor ordinary clients.\n")
    (rev / "standing.md").write_text(
        "Called for every read in the adapter.\nplain second line\n")
    git(rev, "add", "-A")
    git(rev, "commit", "-qm", "wrapped sameness base")
    rbase = git(rev, "rev-parse", "HEAD").stdout.strip()
    (rev / "s.md").write_text("The responses are identical\nfor authenticated clients.\n")
    (rev / "standing.md").write_text(
        "Called for every read in the adapter.\nplain second line, edited\n")
    (rev / "rev.md").write_text("Requests always succeed.\nReads are never retried.\n")
    git(rev, "add", "-A")
    git(rev, "commit", "-qm", "reverse order and an edited second half")
    out = run("python3", audit, rev, f"{rbase}...HEAD").stdout
    check("claim-audit: flags a noun-then-absolute claim ('Requests always')",
          "rev.md:1" in out, out)
    check("claim-audit: flags 'Reads are never retried'", "rev.md:2" in out, out)
    check("claim-audit: joins when the CONTEXT half is the one that classifies",
          "s.md:1" in out and "wrapped" in out, out)
    # the guard that keeps that from attributing a standing claim to this range:
    # line 1 ENDS a sentence, so it was not split by a wrap and is not rejoined
    check("claim-audit: a complete standing sentence is not joined to an edit",
          "standing.md" not in out, out)

    # The noun list is the recall bound. These three were named by review as
    # predicted misses of a list tuned on one author's four commits.
    wide = tmp / "wide"
    make_repo(wide, commits=1)
    wbase = git(wide, "rev-parse", "HEAD").stdout.strip()
    (wide / "doc.md").write_text(
        "All workers execute without locks.\n"
        "Every packet is verified before dispatch.\n"
        "Guaranteed zero allocations in the hot loop.\n"
        "Every請求 request is signed.\n"
        # '!' and '！' specifically: '.' and ';' were already excluded, so a
        # fixture built on those cannot tell the widened gap from the old one.
        "Never! The read is in another sentence.\n"
        "完全正確！讀取會被略過\n"
        # the pair the module comment rests on: the same absolute word, once
        # over a code path and once not. Without line 8 asserted, that comment
        # is the unpinned kind of sentence this whole script exists to surface.
        "Undetectable by external observers.\n"
        "Undetectable by design.\n"
        # an ASCII absolute with a CJK noun and nothing else: the earlier
        # fixture matched on its English "request", so it never tested this.
        "Every請求\n"
        "一律 every read\n"
    )
    git(wide, "add", "-A")
    git(wide, "commit", "-qm", "wider nouns")
    out = run("python3", audit, wide, f"{wbase}...HEAD").stdout
    for n, what in ((1, "workers"), (2, "packets"), (3, "allocations")):
        check(f"claim-audit: flags an absolute quantifying over {what}",
              f"doc.md:{n}" in out, out)
    check("claim-audit: an ASCII absolute abutting CJK still matches",
          "doc.md:4" in out, out)
    check("claim-audit: a match does not span '!'", "doc.md:5" not in out, out)
    check("claim-audit: a match does not span a full-width '！'",
          "doc.md:6" not in out, out)
    check("claim-audit: flags an absolute quantifying over observers",
          "doc.md:7" in out, out)
    check("claim-audit: does NOT flag 'undetectable by design', as the comment says",
          "doc.md:8" not in out, out)
    check("claim-audit: an ASCII absolute over a CJK noun matches",
          "doc.md:9" in out, out)
    check("claim-audit: a CJK absolute over an ASCII noun matches",
          "doc.md:10" in out, out)


# ------------------------------------------------------------ lint version ----
def test_lint_version(tmp):
    """check_version(): both rules, and the two things that make them honest.

    This one needs REAL repos — the check reads git tags, so a fake directory
    tree cannot drive it the way the other lint tests are driven.

    Load-bearing cases, in the order they were learned:
      - a manifest AHEAD of the tag on its own commit: rule 2 is satisfied and
        the release is still mislabeled, so rule 1 cannot be dropped
      - 0.10.0 past v0.9.0: a string compare rejects it
      - the working copy bumped past its own tag: rule 1 reads the TAG's tree,
        or the bump rule 2 demands would be reported as mislabeling the tag
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    #: what the LAST run_against() call could not decide -- errors are the
    #: verdict, notes are "this rule never executed", and the difference is the
    #: whole point of the two checks at the end of this function.
    last_notes = []

    def run_against(name, declared, tag=None, commits_after=0,
                    working=None, commit_manifest=True):
        """A real repo, then check_version() with lint.ROOT pointed at it.

        working=X leaves X uncommitted in the working copy after the tag — the
        state of the first post-release commit, mid-edit.
        commit_manifest=False tags a commit whose tree has no manifest at all.
        """
        repo = tmp / "ver" / name
        make_repo(repo, commits=1)
        (repo / ".claude-plugin").mkdir(parents=True)
        mf = repo / ".claude-plugin" / "plugin.json"
        body = lambda v: json.dumps({"name": "x", "description": "x", "version": v})
        mf.write_text(body(declared))
        if commit_manifest:
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "manifest")
        if tag:
            git(repo, "tag", "-a", tag, "-m", tag)
        for i in range(commits_after):
            (repo / f"later{i}.txt").write_text("x\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", f"later {i}")
        if working:
            mf.write_text(body(working))
        real_root, real_errors, real_notes = lint.ROOT, lint.ERRORS, lint.NOTES
        try:
            lint.ROOT, lint.ERRORS, lint.NOTES = repo, [], []
            lint.check_version()
            last_notes[:] = list(lint.NOTES)
            return list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS, lint.NOTES = real_root, real_errors, real_notes

    # rule 1 — HEAD carries a tag, and the tag's own tree is what it declares
    got = run_against("match", "0.2.0", tag="v0.2.0")
    check("version: tagged commit whose manifest matches passes", got == [], f"got {got}")

    got = run_against("behind", "0.1.0", tag="v0.2.0")
    check("version: flags a tag shipping a manifest behind it",
          any("tag 'v0.2.0' ships a manifest declaring '0.1.0'" in e for e in got),
          f"got {got}")

    # rule 2 cannot see this one: 0.3.0 IS ahead of v0.2.0, and the release
    # still goes out labelled v0.2.0 while calling itself 0.3.0
    got = run_against("ahead", "0.3.0", tag="v0.2.0")
    check("version: flags a tag shipping a manifest ahead of it",
          any("tag 'v0.2.0' ships a manifest declaring '0.3.0'" in e for e in got),
          f"got {got}")

    # the state the two rules would deadlock in if rule 1 read the working copy
    got = run_against("dirty", "0.2.0", tag="v0.2.0", working="0.2.1")
    check("version: bumping the working copy on a tagged commit passes",
          got == [], f"got {got}")

    got = run_against("bare", "0.2.0", tag="v0.2.0", commit_manifest=False)
    check("version: flags a tag whose tree has no manifest",
          any("carries no plugin.json" in e for e in got), f"got {got}")

    # rule 2 — HEAD is past the newest tag
    got = run_against("bumped", "0.2.0", tag="v0.1.0", commits_after=3)
    check("version: bumped manifest past a release passes", got == [], f"got {got}")

    # THE measured drift: nine commits past v0.1.0, manifest never bumped
    got = run_against("drifted", "0.1.0", tag="v0.1.0", commits_after=3)
    check("version: flags commits past a release with no bump",
          any("not ahead of released tag 'v0.1.0'" in e for e in got), f"got {got}")

    # a textual compare says "0.10.0" <= "0.9.0" and flags this wrongly
    got = run_against("tenth", "0.10.0", tag="v0.9.0", commits_after=1)
    check("version: 0.10.0 is ahead of v0.9.0 (numeric, not string, compare)",
          got == [], f"got {got}")

    # a tagless clone must be silent, not red -- but see the check's own
    # comment: that silence is why CI checks out at fetch-depth 0
    got = run_against("untagged", "0.2.0")
    check("version: no tags in the tree is silence, not failure", got == [],
          f"got {got}")
    # ...but silence in the ERROR channel is not silence on stdout. Measured
    # 2026-08-15: an agent ran this lint twice in a marketplace clone whose tags
    # had never been fetched, read "all checks passed" both times, and shipped
    # two commits past v0.3.2 with the manifest still declaring 0.3.2. The
    # check's own comment had predicted exactly that -- prose in the source does
    # not reach whoever is reading stdout. A tagless run stays a PASS and stops
    # being SILENT.
    check("version: a tagless run SAYS it guarded nothing",
          any("guarded nothing" in n for n in last_notes), f"notes {last_notes}")

    # The other direction, which is the one that would rot: a run that really
    # did check must not emit the note, or the note becomes wallpaper and the
    # tagless case is invisible again.
    run_against("bumped_quiet", "0.2.0", tag="v0.1.0", commits_after=3)
    check("version: a run that DID check stays quiet", last_notes == [],
          f"notes {last_notes}")

    got = run_against("nonsemver", "0.2", tag="v0.2.0")
    check("version: flags a non-semver manifest version",
          any("not X.Y.Z" in e for e in got), f"got {got}")

    # and the shipped tree must satisfy it
    real = run("git", "-C", str(SCRIPTS.parent), "tag", "--points-at", "HEAD")
    lint.ERRORS = []
    lint.check_version()
    check("version: this repo's own manifest agrees with its tags",
          lint.ERRORS == [], f"{lint.ERRORS} (tags at HEAD: {real.stdout.strip()!r})")
    lint.ERRORS = []


ROOT_MD = sorted(p for p in SCRIPTS.parent.rglob("*.md") if ".git" not in p.parts)


# -------------------------------------------------- lint published version ----
def _origin_repo(tmp, name, tags):
    """A bare `origin` carrying `tags`, plus a clone whose HEAD is on master.

    Real repos again: the check shells out to `git ls-remote`, so a fake tree
    cannot drive it.
    """
    bare = tmp / "pub" / f"{name}.git"
    bare.mkdir(parents=True)
    run("git", "init", "-q", "--bare", str(bare), check=True)
    work = tmp / "pub" / name
    make_repo(work, commits=1)
    git(work, "branch", "-M", "master")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "master")
    for tag in tags:
        git(work, "tag", "-a", tag, "-m", tag)
        git(work, "push", "-q", "origin", tag)
        git(work, "tag", "-d", tag)          # published, deliberately NOT local
    return work


def test_lint_version_moves_with_content(tmp):
    """check_version_moves_with_content(): master never shows two trees under one version.

    Releases are batched, so the per-commit tag that used to make
    check_version()'s rule 2 enforce this is gone. The case this exists for is
    `untagged_twice`: two commits past the last tag, both above it, the second
    at the SAME version as the first -- rule 2 is green on it, and a reader whose
    cache holds that version never sees the second commit.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    notes_seen = []

    def published_repo(name, version):
        """A clone whose origin/master shows `version` -- what a reader installed."""
        work = _origin_repo(tmp, name, [])
        (work / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (work / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "x", "description": "x", "version": version}))
        git(work, "add", "-A")
        git(work, "commit", "-qm", f"release content {version}")
        git(work, "push", "-q", "origin", "master")
        return work

    def change(repo, version=None, commit=True, filename="skill.md"):
        (repo / filename).write_text(f"new text {filename} {version}\n")
        if version is not None:
            (repo / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "x", "description": "x", "version": version}))
        if commit:
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", f"change {version}")

    def lint_it(repo, base=None):
        real = lint.ROOT, lint.ERRORS, lint.NOTES, os.environ.get("LINT_BASE")
        try:
            lint.ROOT, lint.ERRORS, lint.NOTES = repo, [], []
            if base is None:
                os.environ.pop("LINT_BASE", None)
            else:
                os.environ["LINT_BASE"] = base
            lint.check_version_moves_with_content()
            notes_seen[:] = list(lint.NOTES)
            return list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS, lint.NOTES = real[:3]
            if real[3] is None:
                os.environ.pop("LINT_BASE", None)
            else:
                os.environ["LINT_BASE"] = real[3]

    repo = published_repo("unchanged", "0.6.14")
    got = lint_it(repo)
    check("moved: the published tree itself passes", got == [] and notes_seen == [],
          f"got {got} {notes_seen}")

    repo = published_repo("bumped", "0.6.14")
    change(repo, "0.6.15")
    got = lint_it(repo)
    check("moved: a content change that bumps the version passes", got == [], f"got {got}")

    repo = published_repo("same", "0.6.14")
    change(repo, None)
    got = lint_it(repo)
    check("moved: a content change under the published version is flagged",
          any("already shows version '0.6.14'" in e for e in got), f"got {got}")

    repo = published_repo("lower", "0.6.14")
    change(repo, "0.6.13")
    got = lint_it(repo)
    check("moved: ... and so is one that moves the version backwards",
          any("declares '0.6.13'" in e for e in got), f"got {got}")

    repo = published_repo("numeric", "0.6.9")
    change(repo, "0.6.10")
    got = lint_it(repo)
    check("moved: compares numerically, so 0.6.10 is ahead of 0.6.9", got == [], f"got {got}")

    # THE case: two untagged commits past the last tag, the second reusing the
    # first one's version. check_version() is green on it by its own rule.
    repo = published_repo("untagged_twice", "0.6.14")
    git(repo, "tag", "-a", "v0.6.14", "-m", "v0.6.14")
    change(repo, "0.6.15", filename="first.md")
    git(repo, "push", "-q", "origin", "master")
    change(repo, None, filename="second.md")
    real = lint.ROOT, lint.ERRORS, lint.NOTES
    try:
        lint.ROOT, lint.ERRORS, lint.NOTES = repo, [], []
        lint.check_version()
        rule2 = list(lint.ERRORS)
    finally:
        lint.ROOT, lint.ERRORS, lint.NOTES = real
    got = lint_it(repo)
    check("moved: check_version() is green on a reused untagged version "
          "(why this check exists, not a bug in rule 2)", rule2 == [], f"got {rule2}")
    check("moved: the new check catches the reused untagged version",
          any("already shows version '0.6.15'" in e for e in got), f"got {got}")

    # an uncommitted edit to a TRACKED file is what the next commit publishes
    repo = published_repo("dirty", "0.6.14")
    (repo / "f0.txt").write_text("edited, not committed\n")
    got = lint_it(repo)
    check("moved: an uncommitted edit to a tracked file without a bump is flagged",
          any("already shows version" in e for e in got), f"got {got}")
    # ...but an untracked file is not in `git diff`: caught once committed, not before
    repo = published_repo("untracked", "0.6.14")
    change(repo, None, commit=False, filename="brand-new.md")
    got = lint_it(repo)
    check("moved: a new untracked file is not counted until it is committed",
          got == [], f"got {got}")

    # CI's base, not origin/master: the push's `before`
    repo = published_repo("ci_before", "0.6.14")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    change(repo, None)
    git(repo, "push", "-q", "origin", "master")     # origin/master == HEAD now, as in CI
    got = lint_it(repo, base=before)
    check("moved: LINT_BASE (a push's `before`) is the bar, not origin/master",
          any(f"differs from {before}" in e for e in got), f"got {got}")
    got = lint_it(repo)
    check("moved: ... which matters, because origin/master alone is already HEAD",
          got == [], f"got {got}")

    # what cannot be decided is a note, never a silent pass -- except a base CI
    # NAMED that is gone, which is a force-pushed master and fails outright
    got = lint_it(repo, base="0" * 40)
    check("moved: an all-zero LINT_BASE (the push that creates master) is a note, not a pass",
          got == [] and any("all-zero sha" in n for n in notes_seen), f"{got} {notes_seen}")
    for vanished in ("f" * 40, "3b5dea3c1e0d9a8b7c6f5e4d3c2b1a0918273645"):
        got = lint_it(repo, base=vanished)
        check(f"moved: a base CI names that does not resolve fails ({vanished[:7]})",
              any("does not resolve" in e for e in got), f"{got} {notes_seen}")
    # a topic-branch push: CI passes the ref name `origin/master`, not a sha
    branch = published_repo("topic_branch", "0.6.14")
    git(branch, "checkout", "-qb", "topic")
    change(branch, "0.6.15", filename="topic-1.md")
    got = lint_it(branch, base="origin/master")
    check("moved: a branch push is held against origin/master, by ref name",
          got == [], f"{got} {notes_seen}")
    change(branch, None, filename="topic-2.md")
    got = lint_it(branch, base="origin/master")
    check("moved: ... so a second push to the branch needs no second bump",
          got == [], f"{got} {notes_seen}")
    lonely = tmp / "lonely"
    make_repo(lonely, commits=1)
    (lonely / ".claude-plugin").mkdir()
    (lonely / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "x", "version": "0.1.0"}))
    got = lint_it(lonely)
    check("moved: no origin/master and no LINT_BASE is a note",
          got == [] and any("cannot resolve 'origin/master'" in n for n in notes_seen),
          f"{got} {notes_seen}")
    # ...and main() must actually run it: every case above calls the function
    # directly, so a check dropped from main()'s list would stay green here. By
    # object identity, and by running main() -- a name in the source proves
    # nothing (`x if False else y` still contains it).
    check("moved: lint.CHECKS holds check_version_moves_with_content",
          any(c is lint.check_version_moves_with_content for c in lint.CHECKS))
    ran = []
    real_checks = lint.CHECKS
    try:
        lint.CHECKS = tuple((lambda c=c: ran.append(c)) for c in real_checks)
        lint.main()
    except SystemExit:
        pass
    finally:
        lint.CHECKS = real_checks
    check("moved: lint.main() calls every entry of CHECKS, this one included",
          lint.check_version_moves_with_content in ran and len(ran) == len(real_checks),
          f"{len(ran)} of {len(real_checks)}")

    # CI must hand the check its base. Without LINT_BASE a CI run falls back to
    # origin/master, which after actions/checkout IS the pushed HEAD -- the same
    # tree, so the check passes having guarded nothing, and no test above can see
    # that. Read the workflow itself.
    import re as _re
    ci = (SCRIPTS.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lint_step = ci.split("name: Repo invariants", 1)[-1].split("- name:", 1)[0]
    # the VALUE of the env line, not any mention: a comment carrying both
    # expressions must not satisfy this
    value = _re.search(r"^\s*LINT_BASE:\s*(\$\{\{[^}#]*\}\})\s*$", lint_step, _re.M)
    expr = value.group(1) if value else ""
    # the WHOLE expression, not substrings of it: each clause decides which tree
    # a reader could hold, and `github.event.before == github.sha` would keep
    # every substring while making master pushes fall through to origin/master
    expected = ("${{ github.event_name == 'pull_request' && github.event.pull_request.base.sha"
                " || (github.ref == 'refs/heads/master' && github.event.before)"
                " || (startsWith(github.ref, 'refs/tags/') && github.sha)"
                " || 'origin/master' }}")
    check("moved: ci.yml passes LINT_BASE: PR base, master's `before`, a tag's own commit, else origin/master",
          expr == expected and "scripts/lint.py" in lint_step, f"{expr!r}")
    # the tag clause holds a tag against itself, so the Release job must be the
    # one to refuse a tag that is not on master -- before it creates anything
    release_job = ci.split("\n  release:", 1)[-1]
    on_master = release_job.find('git merge-base --is-ancestor "$GITHUB_SHA" origin/master')
    creates = release_job.find('gh release create "$TAG"')
    check("moved: the Release job refuses a tag not on master, before creating the Release",
          0 <= on_master < creates, f"{on_master} {creates}")
    # ...and RUN that step's script, because text and order say nothing about its
    # exit status (`exit 0` in place of `exit 1` would keep both)
    step = release_job.split("- name: A Release is cut from master", 1)[-1]
    script = step.split("run: |", 1)[-1].split("\n      - name:", 1)[0]
    script = "\n".join(line[10:] if line.startswith(" " * 10) else line.strip()
                       for line in script.splitlines())
    guarded = published_repo("release_guard", "0.6.14")
    on_master_sha = git(guarded, "rev-parse", "HEAD").stdout.strip()
    git(guarded, "checkout", "-qb", "side")
    change(guarded, "0.6.15", filename="side.md")
    off_master_sha = git(guarded, "rev-parse", "HEAD").stdout.strip()
    git(guarded, "fetch", "-q", "origin")

    def run_step(sha):
        return subprocess.run(["bash", "-c", script], cwd=guarded, capture_output=True, text=True,
                              env=dict(os.environ, GITHUB_SHA=sha, TAG="v0.6.15"))
    r = run_step(off_master_sha)
    check("moved: the Release step FAILS for a tag whose commit is not on master",
          r.returncode != 0 and "not on master" in r.stdout, r.stdout + r.stderr)
    r = run_step(on_master_sha)
    check("moved: the Release step passes for a tag on master",
          r.returncode == 0, r.stdout + r.stderr)
    # a working manifest with a non-string version is reported, not a crash
    check("moved: _semver treats a non-string as not-a-version",
          lint._semver(7) is None and lint._semver({}) is None and lint._semver(None) is None
          and lint._semver("0.6.15") == (0, 6, 15))
    bare = published_repo("nomanifest_base", "0.6.14")
    first = git(bare, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    got = lint_it(bare, base=first)
    check("moved: a base without a readable manifest is a note",
          got == [] and any("no readable X.Y.Z plugin.json" in n for n in notes_seen),
          f"{got} {notes_seen}")
    for body in ("{not json", "[]", '{"version": 7}', '{"version": {}}', '{"version": ["0.6.14"]}'):
        broken = published_repo(f"broken_base_{abs(hash(body))}", "0.6.14")
        (broken / ".claude-plugin" / "plugin.json").write_text(body)
        git(broken, "add", "-A")
        git(broken, "commit", "-qm", "broken manifest")
        base = git(broken, "rev-parse", "HEAD").stdout.strip()
        (broken / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "x", "description": "x", "version": "0.6.14"}))
        (broken / "f0.txt").write_text("moved on\n")
        got = lint_it(broken, base=base)
        check(f"moved: a base whose manifest is {body!r} is a note, not a pass",
              got == [] and any("no readable X.Y.Z plugin.json" in n for n in notes_seen),
              f"{got} {notes_seen}")
    # a diff that FAILS (neither 0 nor 1) decides nothing
    repo = published_repo("diff_fails", "0.6.14")
    change(repo, None)
    real_run = lint.subprocess.run

    def failing_diff(cmd, *a, **kw):
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: bad object")
        return real_run(cmd, *a, **kw)
    lint.subprocess.run = failing_diff
    try:
        got = lint_it(repo)
    finally:
        lint.subprocess.run = real_run
    check("moved: a failing `git diff` is a note, not a pass",
          got == [] and any("failed" in n for n in notes_seen), f"{got} {notes_seen}")


def test_lint_published_version(tmp):
    """check_version_not_published(): the rule `git describe` structurally cannot enforce.

    The case this exists for is the last one: a manifest that is ahead of every
    tag REACHABLE from HEAD, and equal to one another clone already published.
    check_version() is green on it -- correctly, by its own rule -- and the two
    clones then collide on push. That is not a hypothetical; it happened on
    2026-09-04 and cost two published tags that had to be deleted.
    """
    sys.path.insert(0, str(SCRIPTS))
    import lint

    last_notes = []

    def run_against(name, declared, published):
        repo = _origin_repo(tmp, name, published)
        (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (repo / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "x", "description": "x", "version": declared}))
        real = lint.ROOT, lint.ERRORS, lint.NOTES
        try:
            lint.ROOT, lint.ERRORS, lint.NOTES = repo, [], []
            lint.check_version_not_published()
            last_notes[:] = list(lint.NOTES)
            return list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS, lint.NOTES = real

    got = run_against("ahead", "0.3.41", ["v0.3.40"])
    check("published: a version past the highest published tag passes",
          got == [], f"got {got}")

    got = run_against("equal", "0.3.40", ["v0.3.40"])
    check("published: flags a version already published",
          any("already published as tag 'v0.3.40'" in e for e in got), f"got {got}")

    got = run_against("behind", "0.3.39", ["v0.3.40"])
    check("published: flags a version behind what is published",
          any("BEHIND published tag 'v0.3.40'" in e for e in got), f"got {got}")

    # numeric, not lexical: v0.3.9 < v0.3.10, and a string compare says otherwise
    got = run_against("numeric", "0.3.11", ["v0.3.9", "v0.3.10"])
    check("published: compares numerically, so v0.3.10 outranks v0.3.9",
          got == [], f"got {got}")
    got = run_against("numeric2", "0.3.10", ["v0.3.9", "v0.3.10"])
    check("published: v0.3.10 published is caught by a v0.3.10 manifest",
          any("already published" in e for e in got), f"got {got}")

    # THE case. The published tags are pushed and then deleted locally, so they
    # are unreachable from this HEAD -- exactly the shape of a parallel session's
    # release. check_version() must pass and this check must not.
    repo = _origin_repo(tmp, "unreachable", ["v0.3.40"])
    (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "x", "version": "0.3.40"}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "manifest")
    real = lint.ROOT, lint.ERRORS, lint.NOTES
    try:
        lint.ROOT, lint.ERRORS, lint.NOTES = repo, [], []
        lint.check_version()
        reachable_errors = list(lint.ERRORS)
        lint.ERRORS = []
        lint.check_version_not_published()
        published_errors = list(lint.ERRORS)
    finally:
        lint.ROOT, lint.ERRORS, lint.NOTES = real
    check("published: check_version() is green on an unreachable published tag "
          "(this is why the new check exists, not a bug in the old one)",
          reachable_errors == [], f"got {reachable_errors}")
    check("published: the new check catches what reachability cannot see",
          any("already published" in e for e in published_errors),
          f"got {published_errors}")

    # the release commit itself: HEAD carries the very tag being published, and
    # its manifest must declare that version. A check that fires here cannot pass
    # on the one commit it exists to describe.
    rel_repo = _origin_repo(tmp, "onrelease", ["v0.3.40"])
    (rel_repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (rel_repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "x", "version": "0.3.40"}))
    git(rel_repo, "add", "-A")
    git(rel_repo, "commit", "-qm", "Release 0.3.40")
    git(rel_repo, "tag", "-a", "v0.3.40", "-m", "v0.3.40")
    real = lint.ROOT, lint.ERRORS, lint.NOTES
    try:
        lint.ROOT, lint.ERRORS, lint.NOTES = rel_repo, [], []
        lint.check_version_not_published()
        got = list(lint.ERRORS)
    finally:
        lint.ROOT, lint.ERRORS, lint.NOTES = real
    check("published: silent on the release commit that carries the tag",
          got == [], f"got {got}")

    # THE regression. The block above tags LOCALLY, so it only ever exercised
    # the local half of the carve-out -- and the half it never reached is the
    # one that broke. An --atomic push publishes master and the tag together,
    # GitHub starts a run for each, and the branch-push run's checkout can lack
    # the tag that this check then reads off origin: carve-out misses, check
    # fires, and the release commit reddens its own CI. MEASURED 2026-09-07 on
    # the real v0.4.9 push, reproduced here from a bare origin.
    ci_repo = tmp / "pub" / "ci-branch-run"
    ci_bare = tmp / "pub" / "ci-branch-run.git"
    ci_bare.mkdir(parents=True)
    run("git", "init", "-q", "--bare", str(ci_bare), check=True)
    make_repo(ci_repo, commits=1)
    (ci_repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (ci_repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "x", "version": "0.4.9"}))
    git(ci_repo, "add", "-A")
    git(ci_repo, "commit", "-qm", "Release 0.4.9")
    git(ci_repo, "branch", "-M", "master")
    git(ci_repo, "remote", "add", "origin", str(ci_bare))
    git(ci_repo, "push", "-q", "origin", "master")
    git(ci_repo, "tag", "-a", "v0.4.9", "-m", "v0.4.9")
    git(ci_repo, "push", "-q", "origin", "v0.4.9")
    git(ci_repo, "tag", "-d", "v0.4.9")   # the checkout that has no tag

    def against_ci():
        real = lint.ROOT, lint.ERRORS, lint.NOTES
        try:
            lint.ROOT, lint.ERRORS, lint.NOTES = ci_repo, [], []
            lint.check_version_not_published()
            return list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS, lint.NOTES = real

    check("published: silent on the release commit when the tag is on ORIGIN "
          "but not in the checkout (the branch-push CI run)",
          against_ci() == [], f"got {against_ci()}")

    # ... and the carve-out stays narrow. Both of these once passed only because
    # the check fired on everything; they are what stops it becoming a blanket
    # return. An annotated tag's unpeeled ls-remote line carries the TAG
    # OBJECT's sha, so a lookup that forgets to ask for `^{}` answers "no match"
    # here and this first case would go red again.
    (ci_repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "x", "version": "0.4.9", "e": 1}))
    check("published: an EDITED manifest is not the released tree, tag or no tag",
          any("already published" in e for e in against_ci()), f"got {against_ci()}")
    git(ci_repo, "checkout", "-q", "--", ".claude-plugin/plugin.json")

    git(ci_repo, "commit", "-q", "--allow-empty", "-m", "past the tag")
    check("published: a commit PAST the published tag still collides",
          any("already published" in e for e in against_ci()), f"got {against_ci()}")
    git(ci_repo, "reset", "-q", "--hard", "HEAD~1")

    # honesty: no origin at all must NOT read as an all-clear
    solo = tmp / "pub" / "noremote"
    make_repo(solo, commits=1)
    (solo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (solo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "x", "version": "9.9.9"}))
    real = lint.ROOT, lint.ERRORS, lint.NOTES
    try:
        lint.ROOT, lint.ERRORS, lint.NOTES = solo, [], []
        lint.check_version_not_published()
        got, notes = list(lint.ERRORS), list(lint.NOTES)
    finally:
        lint.ROOT, lint.ERRORS, lint.NOTES = real
    check("published: an unreachable remote reports nothing as an error",
          got == [], f"got {got}")
    check("published: ... and says so, instead of passing silently",
          any("guarded nothing" in n for n in notes), f"got {notes}")


# ------------------------------------------------------------------ release ----
def test_release(tmp):
    """release.sh: every guard, and that it does not publish.

    Each refusal below is a failure that actually happened on 2026-09-04 --
    a colliding bump, a tag pushed ahead of its commit, and a one-line
    annotation that became a one-line release page.
    """
    release = SCRIPTS / "release.sh"
    notes = tmp / "notes.md"
    # The '## ' line is load-bearing: `git tag -a -F` defaults to
    # --cleanup=strip and deletes it, which is how v0.5.1 shipped a release page
    # with none of its four headings. A fixture without a '#' line cannot fail
    # the verbatim assertion below, which is why this defect survived.
    notes.write_text("Title line\n\n## A heading strip would eat\n\n"
                     "- a real bullet\n- and another\n")
    thin = tmp / "thin.md"
    thin.write_text("just one line\n")

    def repo_at(name, declared="0.3.41", tags=("v0.3.40",)):
        work = _origin_repo(tmp, name, list(tags))
        (work / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (work / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "x", "description": "x", "version": declared}))
        # The script locates its repo from BASH_SOURCE, not cwd -- deliberately,
        # so it releases the repo it belongs to and not whatever directory you
        # happen to be standing in. Driving it therefore means putting a copy
        # inside the fixture, exactly where a real one lives.
        (work / "scripts").mkdir(exist_ok=True)
        shutil.copy2(release, work / "scripts" / "release.sh")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "content")
        git(work, "push", "-q", "origin", "master")
        return work

    def call(repo, *args):
        return run("bash", str(repo / "scripts" / "release.sh"), *args)

    check("release: refuses no arguments", call(repo_at("noargs")).returncode != 0)

    r = call(repo_at("thin"), thin)
    check("release: refuses a thin annotation (it becomes the release page)",
          r.returncode != 0 and "fewer than 3" in r.stderr, r.stderr)

    dirty = repo_at("dirty")
    (dirty / "scratch.txt").write_text("uncommitted\n")
    r = call(dirty, notes)
    check("release: refuses a dirty tree", r.returncode != 0 and "dirty" in r.stderr, r.stderr)

    # THE ordering guard: a commit that is not on origin/master yet
    unpushed = repo_at("unpushed")
    (unpushed / "later.txt").write_text("x\n")
    git(unpushed, "add", "-A")
    git(unpushed, "commit", "-qm", "not pushed")
    r = call(unpushed, notes)
    check("release: refuses to tag a commit that is not on origin/master",
          r.returncode != 0 and "not on origin/master" in r.stderr, r.stderr)

    # the collision this whole change exists for
    r = call(repo_at("collide", declared="0.3.40"), notes)
    check("release: refuses a manifest version already published",
          r.returncode != 0 and "already published v0.3.40" in r.stderr, r.stderr)
    check("release: ... and names the next free version",
          "0.3.41" in r.stderr, r.stderr)

    r = call(repo_at("behind", declared="0.3.39"), notes)
    check("release: refuses a manifest version behind what is published",
          r.returncode != 0, r.stderr)

    # numeric compare: 0.3.9 published must not outrank a 0.3.10 manifest
    r = call(repo_at("numeric", declared="0.3.10", tags=("v0.3.9",)), notes)
    check("release: compares numerically, so 0.3.10 releases past v0.3.9",
          r.returncode == 0, r.stderr)

    # happy path
    ok = repo_at("ok")
    r = call(ok, notes)
    check("release: succeeds on a clean, pushed, correctly-bumped master",
          r.returncode == 0, r.stderr)
    check("release: does NOT create a bump commit (rule 2 wants it in content)",
          git(ok, "log", "--oneline", "-1").stdout.split(None, 1)[1].strip() == "content",
          git(ok, "log", "--oneline", "-1").stdout)
    # `git tag -l` also lists v0.3.40: release.sh fetches tags, so the published
    # one comes back locally. What matters is which commit the NEW tag names.
    check("release: tags HEAD with the version the manifest declares",
          git(ok, "rev-list", "-n1", "v0.3.41").stdout.strip()
          == git(ok, "rev-parse", "HEAD").stdout.strip(),
          git(ok, "tag", "-l").stdout)
    check("release: creates an ANNOTATED tag (ci.yml hard-fails on lightweight)",
          git(ok, "cat-file", "-t", "v0.3.41").stdout.strip() == "tag")
    # Byte-equality, not a substring: the old check looked for one bullet and
    # passed against an annotation missing every heading.
    check("release: the annotation is the notes file verbatim",
          git(ok, "cat-file", "tag", "v0.3.41").stdout.split("\n\n", 1)[1]
          == notes.read_text(),
          git(ok, "cat-file", "tag", "v0.3.41").stdout)
    check("release: a '#' heading survives into the annotation",
          "## A heading strip would eat"
          in git(ok, "tag", "-l", "--format=%(contents)", "v0.3.41").stdout)
    check("release: prints an --atomic push and does NOT push it itself",
          "--atomic origin master v0.3.41" in r.stdout, r.stdout)
    check("release: ... so origin still has no such tag",
          git(ok, "ls-remote", "--tags", "origin", "v0.3.41").stdout.strip() == "")


# -------------------------------------------------------- await-codex-job ----
def test_await_codex_job(tmp):
    """The waiter's job is to make the host's 'finished' notification mean the
    JOB finished. Two properties matter and both are checkable without a real
    codex run: it must not hang forever when the job never becomes terminal,
    and it must resolve the plugin's own status command rather than the target
    repo's cwd."""
    await_sh = SCRIPTS / "await-codex-job.sh"

    r = subprocess.run(["bash", "-n", str(await_sh)], capture_output=True, text=True)
    check("await-codex-job.sh parses", r.returncode == 0, r.stderr.strip())

    r = subprocess.run(["bash", str(await_sh)], capture_output=True, text=True)
    check("await-codex-job.sh requires a job id",
          r.returncode != 0, f"rc={r.returncode}")

    # An unknown job must hit the caller's own deadline instead of spinning
    # forever. A waiter that can hang is worse than no waiter: the round never
    # reports and the lead is back to asking "is it done yet".
    env = dict(os.environ, HOME=str(tmp / "empty-home"))
    (tmp / "empty-home").mkdir(exist_ok=True)
    r = subprocess.run(["bash", str(await_sh), "task-nope", str(tmp), "1"],
                       capture_output=True, text=True, timeout=120, env=env)
    check("await-codex-job.sh exits non-zero when the companion is absent",
          r.returncode != 0, f"rc={r.returncode} out={r.stdout.strip()[:80]}")

    # The TIMEOUT path needs its own case: the check above exits early on a
    # missing companion and never reaches the loop, so on its own it lets a
    # mutant that returns 0 after the deadline survive (measured -- it did).
    # Stand up a fake companion that answers `status` forever without ever
    # going terminal, and require a non-zero exit.
    fake_home = tmp / "fake-home"
    comp = fake_home / ".claude/plugins/cache/openai-codex/codex/9.9.9/scripts"
    comp.mkdir(parents=True, exist_ok=True)
    (comp / "codex-companion.mjs").write_text(
        "console.log('- task-forever | running | rescue | Codex Task');\n")
    r = subprocess.run(["bash", str(await_sh), "task-forever", str(tmp), "1"],
                       capture_output=True, text=True, timeout=180,
                       env=dict(os.environ, HOME=str(fake_home)))
    check("await-codex-job.sh exits non-zero when the job never goes terminal",
          r.returncode != 0, f"rc={r.returncode}")
    check("await-codex-job.sh says TIMEOUT rather than failing silently",
          "TIMEOUT" in (r.stderr + r.stdout), (r.stderr + r.stdout)[:120])

    # Exercise the guard instead of grepping for its words. The old assertion
    # was `"failed" in body and "cancelled" in body` — a search of the SOURCE
    # TEXT — and a natural typo survives it: drop the spaces from the case
    # patterns (`*"|failed|"*`) and all four words are still in the file while
    # the pattern can never match the companion's real `- task-x | failed | ...`
    # output. Measured 2026-09-13: full suite green against that mutant.
    for state in ("failed", "cancelled", "error", "completed"):
        term_home = tmp / f"term-home-{state}"
        c = term_home / ".claude/plugins/cache/openai-codex/codex/9.9.9/scripts"
        c.mkdir(parents=True, exist_ok=True)
        (c / "codex-companion.mjs").write_text(
            f"console.log('- task-t | {state} | rescue | Codex Task');\n")
        r = subprocess.run(["bash", str(await_sh), "task-t", str(tmp), "1"],
                           capture_output=True, text=True, timeout=180,
                           env=dict(os.environ, HOME=str(term_home)))
        check(f"await-codex-job.sh treats {state} as terminal",
              r.returncode == 0 and "TERMINAL" in (r.stdout + r.stderr),
              f"rc={r.returncode} out={(r.stdout + r.stderr)[:120]}")

    body = await_sh.read_text()
    check("await-codex-job.sh resolves the companion under $HOME/.claude",
          "plugins/cache/openai-codex/codex" in body)

    # The QUIESCENT fallback, run for real with the windows shortened. It had
    # never been exercised, and that is how a broken size read shipped twice:
    # GNU `stat -c` returned 0 forever on macOS (a flat timer), and the
    # BSD-first `stat -f %z || stat -c %s` that replaced it SUCCEEDS on Linux
    # with filesystem free-space figures, so the log never read as unchanged
    # and the fallback could not fire at all (found 2026-09-25). A fake
    # companion that never goes terminal plus a log nobody writes to must end
    # in QUIESCENT, rc 0, before the deadline that would say TIMEOUT.
    quiet_home = tmp / "quiet-home"
    c = quiet_home / ".claude/plugins/cache/openai-codex/codex/9.9.9/scripts"
    c.mkdir(parents=True, exist_ok=True)
    (c / "codex-companion.mjs").write_text(
        "console.log('- task-q | running | rescue | Codex Task');\n")
    jobs = quiet_home / ".claude/plugins/data/codex-openai-codex/state/s1/jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "task-q.log").write_text("delegate output that then stops\n")
    r = subprocess.run(["bash", str(await_sh), "task-q", str(tmp), "30"],
                       capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, HOME=str(quiet_home),
                                AWAIT_QUIET_SECS="2", AWAIT_POLL_SECS="1"))
    check("await-codex-job.sh treats an unchanged log as QUIESCENT, before the deadline",
          r.returncode == 0 and "QUIESCENT" in r.stdout and "TIMEOUT" not in r.stderr,
          f"rc={r.returncode} out={(r.stdout + r.stderr)[:160]}")
    check("await-codex-job.sh reports the log's real byte count",
          "(32 bytes)" in r.stdout, r.stdout[:160])
    check("await-codex-job.sh reads the size with the one spelling both platforms share",
          'size=$(wc -c < "$log"' in body, "the size read is not `wc -c` any more")


def test_leg_cmd():
    """leg-cmd.sh must reject each spelling that was actually got wrong.

    Not hypothetical: every one of these four was composed by a lead in the
    2026-09-08 session, and each is contradicted by data/launch.json.
    """
    script = SCRIPTS / "leg-cmd.sh"
    # opencode review is framed since 0.6.30: every render names base and target.
    OC = ["--base", "abc", "--target", "/tmp/leg-cmd-frozen"]
    check("leg-cmd: script exists", script.is_file())
    if not script.is_file():
        return

    cases = [
        (["agy", "review", "--model", "gemini-3.8-flash-medium",
          "--effort", "medium", "--target", "/tmp/x"],
         "MODEL NAME", "agy effort belongs in the model name"),
        (["codex", "review", "--model", "gpt-6-luna",
          "--base", "abc", "--target", "/tmp/x"],
         "needs --effort", "codex review takes its effort per call (0.6.28)"),
        (["opencode", "review", "--model", "opencode/x"],
         "needs --effort", "opencode needs --variant"),
        (["cursor", "review", "--model", "kimi-k3-high", "--effort", "medium"],
         "MODEL NAME", "cursor effort belongs in the model name"),
    ]
    # Not a refusal case: the write posture of an ACCEPTED implement launch.
    # Measured 2026-09-13 — data/launch.json's codex implement row was missing
    # --write, and codex-companion.mjs runs `sandbox: request.write ?
    # "workspace-write" : "read-only"`, so the dispatch path dev-lead makes
    # mandatory produced a paid delegate that could not edit a file. The needle
    # table above only covered refusals, and the posture table below omitted
    # codex implement, so nothing failed.
    r = subprocess.run([str(script), "codex", "implement", "--model", "x",
                        "--effort", "high"], capture_output=True, text=True)
    check("leg-cmd: codex implement asks for a WRITE session",
          "--write" in r.stdout, f"stdout={r.stdout!r}")
    check("leg-cmd: codex implement does not resume a prior session",
          "--fresh" in r.stdout, f"stdout={r.stdout!r}")

    for argv, needle, label in cases:
        r = subprocess.run([str(script), *argv], capture_output=True, text=True)
        check(f"leg-cmd: refuses -- {label}", r.returncode != 0,
              f"accepted {argv}")
        check(f"leg-cmd: explains why -- {label}", needle in r.stderr,
              f"stderr={r.stderr!r}")

    # The brief's name follows the role. Every implement skill writes
    # "$RUN_DIR/task.md" and every review skill "$RUN_DIR/prompt.md"; until
    # 0.6.46 the script read prompt.md for both, so a composed implement
    # launch failed its own guard against a brief the skill had written.
    for argv in (["codex", "--model", "m", "--effort", "high"],
                 ["agy", "--model", "gemini-3.8-flash-high", "--target", "/tmp/x"],
                 ["cursor", "--model", "cursor-grok-4.6-medium"],
                 ["opencode", "--model", "opencode/x", "--effort", "high"],
                 ["claude", "--model", "opus"]):
        adapter = argv[0]
        r = subprocess.run([str(script), adapter, "implement", *argv[1:]],
                           capture_output=True, text=True)
        check(f"leg-cmd: {adapter} implement reads the brief the skill writes (task.md)",
              r.returncode == 0 and '"$RUN_DIR/task.md"' in r.stdout
              and "prompt.md" not in r.stdout,
              f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr[-200:]!r}")

    # A value option given LAST with no value. `MODEL=${2:-}; shift 2` under
    # `set -e` exited 1 with nothing on stderr (measured 2026-09-25), so an
    # `eval "$(leg-cmd.sh ... --model)"` composed nothing and said nothing.
    for opt in ("--model", "--effort", "--target", "--base", "--prompt-file", "--run-dir"):
        r = subprocess.run([str(script), "codex", "review", opt],
                           capture_output=True, text=True)
        check(f"leg-cmd: a trailing {opt} is a usage error, not a silent exit",
              r.returncode != 0 and f"{opt} needs a value" in r.stderr,
              f"rc={r.returncode} stderr={r.stderr!r}")

    r = subprocess.run([str(script), "agy", "review", "--model",
                        "gemini-3.8-flash-medium", "--base", "abc", "--target", "/tmp/x"],
                       capture_output=True, text=True)
    check("leg-cmd: renders the correct agy spelling", r.returncode == 0, r.stderr)
    check("leg-cmd: model reaches the command",
          "--model gemini-3.8-flash-medium" in r.stdout, r.stdout)
    check("leg-cmd: does not emit a flag the adapter rejects",
          "--effort" not in r.stdout, r.stdout)

    # The output is documented for `eval "$(leg-cmd.sh ...)"`, so a caller
    # value carrying shell metacharacters must come back quoted. Measured
    # 2026-09-08: quoting only tokens containing a SPACE let a backticked
    # model name through raw, which eval would have executed.
    for argv, needle in (
        (["cursor", "review", "--model", "x`id`y"], "'x`id`y'"),
        (["agy", "review", "--model", "gemini-3.8-flash-medium", "--base", "abc",
          "--target", '/tmp/a";touch /tmp/PWNED;"b'], "touch /tmp/PWNED"),
    ):
        r = subprocess.run([str(script), *argv], capture_output=True, text=True)
        check(f"leg-cmd: quotes a hostile {argv[2]} value", r.returncode == 0, r.stderr)
        check(f"leg-cmd: {argv[2]} metacharacters are inside quotes",
              needle in r.stdout and "'" in r.stdout, r.stdout)

    # The banner must not contradict the command underneath it. codex's effort
    # block and not_flags are ADAPTER-scoped but describe the review path only
    # (applies_to_role: "review"), so an implement launch printed "NOT flags on
    # this path ... --effort" directly above an argv passing --effort high.
    # 0.6.28: codex review is `codex exec` with the suite's framing. The brief is
    # a lens; the builder wraps it and the framed prompt goes in on stdin.
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as rd:
        r = subprocess.run([str(script), "codex", "review", "--model", "gpt-6-luna", "--effort", "xhigh",
                            "--base", "abc123", "--target", td, "--run-dir", rd],
                           capture_output=True, text=True)
    out = r.stdout
    check("leg-cmd: codex review runs codex exec, read-only, with the effort per call",
          r.returncode == 0 and "codex exec -C " in out and "-s read-only" in out
          and "-c model_reasoning_effort=xhigh" in out, r.stdout + r.stderr)
    check("leg-cmd: codex review frames the brief before the paid run, chained",
          "review-prompt.py --adapter codex --base abc123 --target " in out
          and out.index("review-prompt.py") < out.index("codex exec")
          and '> "$RUN_DIR/framed-prompt.md" && codex exec' in out, out)
    check("leg-cmd: codex review reads the framed prompt from stdin after --",
          out.rstrip().endswith('-- - < "$RUN_DIR/framed-prompt.md"'), out)
    check("leg-cmd: codex review's -o file expands $RUN_DIR",
          '-o "$RUN_DIR/review.md"' in out, out)
    with tempfile.TemporaryDirectory() as td:
        inside = Path(td) / "run"
        r = subprocess.run([str(script), "codex", "review", "--model", "gpt-6-luna", "--effort", "high",
                            "--base", "abc", "--target", td, "--run-dir", str(inside)],
                           capture_output=True, text=True)
    check("leg-cmd: a run directory inside the frozen target is refused",
          r.returncode != 0 and "inside --target" in r.stderr and not r.stdout, r.stderr)
    r = subprocess.run([str(script), "codex", "implement", "--model", "x", "--effort", "high"],
                       capture_output=True, text=True, env=dict(os.environ, DEV_LEAD_LAUNCH=str(SCRIPTS.parent / "data" / "launch.json")))
    check("leg-cmd: a DEV_LEAD_LAUNCH override is announced, never silent",
          "launch data overridden by DEV_LEAD_LAUNCH" in r.stderr, r.stderr)
    for missing, argv in (("--target", ["--base", "abc"]), ("--base", ["--target", "/tmp/x"])):
        r = subprocess.run([str(script), "codex", "review", "--model", "gpt-6-luna", "--effort", "high", *argv],
                           capture_output=True, text=True)
        check("leg-cmd: codex review without %s is refused" % missing,
              r.returncode != 0 and missing.lstrip("-") in r.stderr, r.stderr)

    # The framing builder (0.6.28): one pass, refusals before any paid run.
    builder = SCRIPTS / "codex-review-prompt.py"
    sys.path.insert(0, str(SCRIPTS))
    import importlib.util as _ilu
    _bspec = _ilu.spec_from_file_location("codex_review_prompt", builder)
    crp = _ilu.module_from_spec(_bspec)
    _bspec.loader.exec_module(crp)
    frame = "<!-- header {{BASE}} -->\nRange {{BASE}}..{{HEAD}}\n{{LENS}}\n<!-- body comment -->\n"
    built = crp.build(frame, "b0", "h1", "check {{HEAD}} literally")
    check("framing builder: substitutes in one pass (a {{HEAD}} inside the lens stays literal)",
          built == "Range b0..h1\ncheck {{HEAD}} literally\n<!-- body comment -->\n", built)
    for bad in ("{{TARGET}}", "{{base}}", "{{HEAD2}}"):
        try:
            crp.build("x " + bad + " {{LENS}}", "b", "h", "l")
            refused = False
        except ValueError:
            refused = True
        check("framing builder: an unknown placeholder %s is refused" % bad, refused, bad)
    with tempfile.TemporaryDirectory() as td:
        empty = subprocess.run([sys.executable, str(builder), "--base", "b", "--target", td],
                               input="  \n", capture_output=True, text=True)
        check("framing builder: an empty lens is refused before anything runs",
              empty.returncode != 0 and "empty lens" in empty.stderr and not empty.stdout, empty.stderr)
        nohead = subprocess.run([sys.executable, str(builder), "--base", "b", "--target", td],
                                input="lens", capture_output=True, text=True)
        check("framing builder: a target that is not a repository is refused",
              nohead.returncode != 0 and "cannot read HEAD" in nohead.stderr and not nohead.stdout,
              nohead.stderr)
    # BASE must be a commit (0.6.30 resolves it once); HEAD exists even in a depth-1 clone.
    real = subprocess.run([sys.executable, str(builder), "--base", "HEAD", "--target", str(SCRIPTS.parent)],
                          input="my lens", capture_output=True, text=True)
    head = subprocess.run(["git", "-C", str(SCRIPTS.parent), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    check("framing builder: the shipped framing builds, with the target's HEAD and the lens",
          real.returncode == 0 and ("%s..%s" % (head, head)) in real.stdout and "my lens" in real.stdout
          and "{{" not in real.stdout and not real.stdout.startswith("<!--")
          and real.stdout.rstrip().endswith("plain text: no bold, no heading, no backticks."),
          real.stderr + real.stdout[-300:])

    # 0.6.30: one builder for every framed leg, each adapter its own framing.
    gen = SCRIPTS / "review-prompt.py"
    compat = subprocess.run([sys.executable, str(builder), "--base", "HEAD", "--target", str(SCRIPTS.parent)],
                            input="my lens", capture_output=True, text=True)
    direct = subprocess.run([sys.executable, str(gen), "--adapter", "codex", "--base", "HEAD",
                             "--target", str(SCRIPTS.parent)], input="my lens", capture_output=True, text=True)
    check("review-prompt: the codex-review-prompt.py name still builds the codex frame, byte for byte",
          compat.returncode == 0 and compat.stdout == direct.stdout and direct.stdout, compat.stderr)
    built = {}
    for adapter in ("codex", "opencode", "cursor"):
        got = subprocess.run([sys.executable, str(gen), "--adapter", adapter, "--base", "HEAD",
                              "--target", str(SCRIPTS.parent)], input="the lens", capture_output=True, text=True)
        built[adapter] = got.stdout
        check("review-prompt: the shipped %s frame builds" % adapter,
              got.returncode == 0 and "the lens" in got.stdout and "{{" not in got.stdout
              and got.stdout.rstrip().endswith("no backticks.") and not got.stdout.startswith("<!--"),
              got.stderr + got.stdout[-200:])
    # Each adapter reads ITS frame: opencode's names its five git reads; cursor's
    # is the codex text on purpose (its header says so) -- pinned both ways.
    check("review-prompt: the opencode frame is opencode's, not codex's",
          "Your only shell commands are `git status`" in built["opencode"]
          and built["opencode"] != built["codex"])
    check("review-prompt: the cursor frame is the codex frame's text, as its header says",
          built["cursor"] == built["codex"])
    for bad in ("../codex", "Codex", "", "claude"):
        got = subprocess.run([sys.executable, str(gen), "--adapter", bad, "--base", "HEAD",
                              "--target", str(SCRIPTS.parent)], input="lens", capture_output=True, text=True)
        check("review-prompt: adapter %r without a shipped frame is refused" % bad,
              got.returncode != 0 and not got.stdout, got.stderr)
    try:
        crp.build("x {{LENS}}", "b", "h", "l", evidence="/e")
        refused = False
    except ValueError as exc:
        refused = "never read" in str(exc)
    check("review-prompt: --evidence for a frame without {{EVIDENCE}} is refused", refused)

    # The agy frame's evidence: what a leg without a shell reads instead of git.
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        make_repo(repo, commits=2)
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "mv", "f1.txt", "renamed.txt")   # a pure rename: git would list only the new path
        (repo / "f0.txt").write_text("changed\n")
        (repo / "added.txt").write_text("new\n")
        (repo / "sub").mkdir()
        (repo / "sub" / "bin.dat").write_bytes(b"\x00\x01binary")
        (repo / "a\tb.txt").write_text("tabbed\n")
        git(repo, "rm", "-q", ".gitignore")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "the change")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        run_dir = Path(td) / "run"
        run_dir.mkdir()
        ev = run_dir / "evidence"
        nov = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base, "--target", str(repo)],
                             input="lens", capture_output=True, text=True)
        check("review-prompt: the agy frame without --evidence is refused",
              nov.returncode != 0 and "--evidence" in nov.stderr and not nov.stdout, nov.stderr)
        got = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base, "--target", str(repo),
                              "--evidence", str(ev)], input="the lens", capture_output=True, text=True)
        check("review-prompt: the agy frame builds and names the evidence directory",
              got.returncode == 0 and str(ev) in got.stdout and "{{" not in got.stdout
              and ("%s..%s" % (base, head)) in got.stdout, got.stderr)
        check("review-prompt: DIFF.patch is the range's diff",
              (ev / "DIFF.patch").read_bytes()
              == subprocess.run(["git", "-C", str(repo), "diff", "--no-textconv", "--no-ext-diff", "--no-color",
                                 base, head], capture_output=True).stdout)
        check("review-prompt: base/ holds a changed file's BASE bytes",
              (ev / "base" / "f0.txt").read_text() == "content 0\n")
        check("review-prompt: base/ holds a deleted file too, and nothing for an added one",
              (ev / "base" / ".gitignore").is_file() and not (ev / "base" / "added.txt").exists()
              and not (ev / "base" / "sub").exists())
        check("review-prompt: a renamed file's OLD path is materialized (renames are split, not followed)",
              (ev / "base" / "f1.txt").is_file() and (ev / "base" / "f1.txt").read_text() == "content 1\n"
              and "renamed.txt\t- -\t100644 " in (ev / "BLOBS.txt").read_text())
        blobs = (ev / "BLOBS.txt").read_text()
        head_blob = git(repo, "rev-parse", "%s:sub/bin.dat" % head).stdout.strip()
        check("review-prompt: BLOBS.txt carries both sides' ids, '-' where a side is absent",
              ("sub/bin.dat\t- -\t100644 %s" % head_blob) in blobs
              and ".gitignore\t100644 " in blobs and "\t- -\n" in blobs, blobs)
        check("review-prompt: a tab in a path is escaped, so BLOBS.txt keeps its columns",
              "a\\tb.txt\t- -\t100644 " in blobs, blobs)
        check("review-prompt: COMMITS.txt lists the range",
              (ev / "COMMITS.txt").read_text().strip() == "%s the change" % head)
        modes = [os.stat(os.path.join(r, n)).st_mode for r, ds, fs in os.walk(ev) for n in ds + fs] + [ev.stat().st_mode]
        check("review-prompt: the evidence is read-only for the whole run",
              modes and all(not (m & 0o222) for m in modes), [oct(m) for m in modes])
        again = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base, "--target", str(repo),
                                "--evidence", str(ev)], input="lens", capture_output=True, text=True)
        check("review-prompt: evidence that already has content is refused, and left as it was",
              again.returncode != 0 and "fresh run directory" in again.stderr
              and (ev / "DIFF.patch").is_file(), again.stderr)
        inside = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base,
                                 "--target", str(repo), "--evidence", str(repo / "ev")],
                                input="lens", capture_output=True, text=True)
        check("review-prompt: evidence inside the frozen target is refused and nothing is written there",
              inside.returncode != 0 and "inside the frozen target" in inside.stderr
              and not (repo / "ev").exists(), inside.stderr)
        broken = run_dir / "broken"
        bad_base = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", "no-such-rev",
                                   "--target", str(repo), "--evidence", str(broken)],
                                  input="lens", capture_output=True, text=True)
        check("review-prompt: a failed materialization leaves no half-written evidence behind",
              bad_base.returncode != 0 and not bad_base.stdout and not broken.exists(), bad_base.stderr)
        for root, dirs, files in os.walk(ev):
            os.chmod(root, 0o755)

        # A name BASE is pinned to its commit once: prompt and evidence carry the hash.
        git(repo, "branch", "-f", "the-base", base)
        named = run_dir / "named"
        byname = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", "the-base",
                                 "--target", str(repo), "--evidence", str(named)],
                                input="lens", capture_output=True, text=True)
        check("review-prompt: a branch-name BASE is resolved to its commit in the prompt",
              byname.returncode == 0 and ("%s..%s" % (base, head)) in byname.stdout
              and "the-base" not in byname.stdout, byname.stderr)
        for root, dirs, files in os.walk(named):
            os.chmod(root, 0o755)
        # ...and in the evidence: the name reaches git exactly once, to be
        # resolved. A stable branch cannot tell the two apart, so log the calls.
        callog = Path(td) / "git-calls.log"
        logbin = Path(td) / "loggit"
        logbin.mkdir()
        (logbin / "git").write_text('#!/bin/sh\necho "$*" >> %s\nexec %s "$@"\n'
                                    % (callog, shutil.which("git")))
        (logbin / "git").chmod(0o755)
        logged = run_dir / "logged"
        subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", "the-base",
                        "--target", str(repo), "--evidence", str(logged)], input="lens",
                       capture_output=True, text=True,
                       env=dict(os.environ, PATH="%s:%s" % (logbin, os.environ["PATH"])))
        named_calls = [l for l in callog.read_text().splitlines() if "the-base" in l]
        check("review-prompt: the BASE name is used once, to resolve it; the evidence uses the hash",
              len(named_calls) == 1 and "rev-parse" in named_calls[0], named_calls)
        for root, dirs, files in os.walk(logged):
            os.chmod(root, 0o755)
        bad = subprocess.run([sys.executable, str(gen), "--adapter", "codex", "--base", "no-such-rev",
                              "--target", str(repo)], input="lens", capture_output=True, text=True)
        check("review-prompt: a BASE that is not a commit is refused before anything is built",
              bad.returncode != 0 and "not a commit" in bad.stderr and not bad.stdout, bad.stderr)

        # A failure AFTER writing began: a git that refuses `cat-file` fails the
        # base/ copy once DIFF.patch and COMMITS.txt already exist.
        fakebin = Path(td) / "fakegit"
        fakebin.mkdir()
        real_git = shutil.which("git")
        (fakebin / "git").write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = cat-file ] && exit 7; done\n'
                                     'exec %s "$@"\n' % real_git)
        (fakebin / "git").chmod(0o755)
        fenv = dict(os.environ, PATH="%s:%s" % (fakebin, os.environ["PATH"]))
        fresh = run_dir / "fresh"
        failed = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base,
                                 "--target", str(repo), "--evidence", str(fresh)],
                                input="lens", capture_output=True, text=True, env=fenv)
        check("review-prompt: a failure after writing began removes the directory it made",
              failed.returncode != 0 and not failed.stdout and not fresh.exists(), failed.stderr)
        handed = run_dir / "handed"
        handed.mkdir()
        failed = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base,
                                 "--target", str(repo), "--evidence", str(handed)],
                                input="lens", capture_output=True, text=True, env=fenv)
        check("review-prompt: ...and empties, but keeps, an empty directory it was handed",
              failed.returncode != 0 and handed.is_dir() and not any(handed.iterdir()), failed.stderr)
        # Cleanup removes what THIS run wrote, never what another process put
        # beside it mid-run (review round 2): a git that drops a foreign file
        # into the evidence directory, then fails.
        shared = run_dir / "shared"
        (fakebin / "git").write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = cat-file ] && '
                                     '{ echo theirs > "%s/foreign"; exit 7; }; done\nexec %s "$@"\n'
                                     % (shared, real_git))
        failed = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base,
                                 "--target", str(repo), "--evidence", str(shared)],
                                input="lens", capture_output=True, text=True, env=fenv)
        check("review-prompt: a failed run removes only what it wrote, never a file it did not",
              failed.returncode != 0 and (shared / "foreign").is_file()
              and (shared / "foreign").read_text() == "theirs\n"
              and not (shared / "DIFF.patch").exists() and not (shared / "COMMITS.txt").exists(),
              sorted(p.name for p in shared.iterdir()) if shared.is_dir() else "gone")
        # A name planted after the directory check is refused, not written through
        # (review round 3): the fake git plants COMMITS.txt as a symlink when the
        # log is asked for, pointing at a file outside the evidence.
        victim = Path(td) / "victim.txt"
        victim.write_text("untouched\n")
        planted = run_dir / "planted"
        (fakebin / "git").write_text('#!/bin/sh\n[ "$3" = log ] && ln -s %s "%s/COMMITS.txt"\nexec %s "$@"\n'
                                     % (victim, planted, real_git))
        failed = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base,
                                 "--target", str(repo), "--evidence", str(planted)],
                                input="lens", capture_output=True, text=True, env=fenv)
        check("review-prompt: a planted symlink is refused, its target untouched, the link left alone",
              failed.returncode != 0 and victim.read_text() == "untouched\n"
              and (planted / "COMMITS.txt").is_symlink() and not (planted / "DIFF.patch").exists(),
              failed.stderr)

        # ...and so is a REGULAR file planted there (O_EXCL, not only O_NOFOLLOW).
        planted2 = run_dir / "planted2"
        (fakebin / "git").write_text('#!/bin/sh\n[ "$3" = log ] && echo theirs > "%s/COMMITS.txt"\nexec %s "$@"\n'
                                     % (planted2, real_git))
        failed = subprocess.run([sys.executable, str(gen), "--adapter", "agy", "--base", base,
                                 "--target", str(repo), "--evidence", str(planted2)],
                                input="lens", capture_output=True, text=True, env=fenv)
        check("review-prompt: a planted regular file is refused and keeps its content",
              failed.returncode != 0 and (planted2 / "COMMITS.txt").is_file()
              and (planted2 / "COMMITS.txt").read_text() == "theirs\n", failed.stderr)

        # A read-only pass that fails part-way: cleanup restores write on the
        # directories this run made before removing what is inside them.
        _rspec = _ilu.spec_from_file_location("review_prompt_mod", gen)
        rpm = _ilu.module_from_spec(_rspec)
        _rspec.loader.exec_module(rpm)
        half = run_dir / "half"
        real_chmod, calls = os.chmod, {"n": 0}
        def flaky_chmod(path, mode, *a, **k):
            calls["n"] += 1
            if calls["n"] == 8:
                raise PermissionError("chmod failed part-way")
            return real_chmod(path, mode, *a, **k)
        import io as _io
        old_stdin, rpm.os.chmod = sys.stdin, flaky_chmod
        try:
            sys.stdin = _io.StringIO("lens")
            try:
                rpm.main(["--adapter", "agy", "--base", base, "--target", str(repo),
                          "--evidence", str(half)])
                exited = False
            except SystemExit:
                exited = True
        finally:
            sys.stdin, rpm.os.chmod = old_stdin, real_chmod
        check("review-prompt: a read-only pass that fails part-way still cleans up completely",
              exited and calls["n"] >= 8 and not half.exists(),
              sorted(str(p) for p in half.rglob("*")) if half.exists() else "gone")

    # The framed launches leg-cmd emits for opencode and agy (0.6.30).
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as rd:
        r = subprocess.run([str(script), "opencode", "review", "--model", "opencode/x", "--effort", "high",
                            "--base", "abc", "--target", td, "--run-dir", rd], capture_output=True, text=True)
        cmd = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        check("leg-cmd: opencode review frames the brief, then runs the leg INSIDE --target in a subshell",
              r.returncode == 0 and "review-prompt.py --adapter opencode --base abc --target " in cmd
              and ("&& ( cd %s && opencode run " % td) in cmd
              and cmd.endswith('< "$RUN_DIR/framed-prompt.md" )'), cmd)
        r = subprocess.run([str(script), "agy", "review", "--model", "gemini-3.8-flash-high",
                            "--base", "abc", "--target", td, "--run-dir", rd], capture_output=True, text=True)
        cmd = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        check("leg-cmd: agy review materializes evidence and grants it with a second --add-dir",
              r.returncode == 0 and '--adapter agy' in cmd and '--evidence "$RUN_DIR/evidence"' in cmd
              and ('--add-dir %s --add-dir "$RUN_DIR/evidence"' % td) in cmd, cmd)
        check("leg-cmd: agy's builder runs first and its failure stops the leg (&&)",
              '--evidence "$RUN_DIR/evidence" < "$RUN_DIR/prompt.md" > "$RUN_DIR/framed-prompt.md" && agy -p ' in cmd,
              cmd)
        check("leg-cmd: agy reads the FRAMED prompt, never the bare brief",
              '-p "$(cat "$RUN_DIR/framed-prompt.md")"' in cmd
              and '"$(cat "$RUN_DIR/prompt.md")"' not in cmd, cmd)
        check("leg-cmd: agy review has room for the frame's 15-minute budget",
              "--print-timeout 20m0s" in cmd, cmd)
    r = subprocess.run([str(script), "opencode", "review", "--model", "opencode/x", "--effort", "high",
                        "--target", "/tmp/x"], capture_output=True, text=True)
    check("leg-cmd: opencode review without --base is refused",
          r.returncode != 0 and "--base" in r.stderr and not r.stdout, r.stderr)
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([str(script), "opencode", "review", "--model", "opencode/x", "--effort", "high",
                            "--base", "abc", "--target", "/tmp/x", "--run-dir", "rel-run"],
                           capture_output=True, text=True, cwd=td)
        check("leg-cmd: a relative --run-dir is exported absolute (the framed leg reads it after cd)",
              ("export RUN_DIR=%s" % os.path.join(os.path.realpath(td), "rel-run")) in r.stdout
              or ("export RUN_DIR=%s" % os.path.join(td, "rel-run")) in r.stdout, r.stdout)

    # Executed, not asserted on text: the framed opencode leg really runs in the
    # target and reads the framed prompt, and the caller's cwd is left alone.
    with tempfile.TemporaryDirectory() as td:
        tgt = Path(td) / "frozen"
        shas = make_repo(tgt, commits=2)
        rdir = Path(td) / "run"
        rdir.mkdir()
        (rdir / "prompt.md").write_text("THE LENS\n")
        fake = Path(td) / "bin"
        fake.mkdir()
        (fake / "opencode").write_text('#!/bin/sh\necho "CWD=$(pwd)"\ngrep -c "THE LENS" && true\n')
        (fake / "opencode").chmod(0o755)
        emitted = subprocess.run([str(script), "opencode", "review", "--model", "opencode/x", "--effort", "high",
                                  "--base", shas[0], "--target", str(tgt), "--run-dir", str(rdir)],
                                 capture_output=True, text=True).stdout
        out = subprocess.run(["bash", "-c", 'cd "%s" && eval "$(cat)"; echo "AFTER=$(pwd)"' % td],
                             input=emitted, capture_output=True, text=True,
                             env=dict(os.environ, PATH="%s:%s" % (fake, os.environ["PATH"])))
        framed = (rdir / "framed-prompt.md").read_text() if (rdir / "framed-prompt.md").exists() else ""
        check("leg-cmd: the framed opencode leg runs in the target and reads the framed lens",
              ("CWD=%s" % tgt) in out.stdout and "\n1\n" in out.stdout + "\n"
              and ("%s..%s" % (shas[0], shas[1])) in framed, out.stdout + out.stderr)
        check("leg-cmd: ...and the caller's own cwd is unchanged after eval",
              ("AFTER=%s" % td) in out.stdout, out.stdout)

    # The banner gating below is the config_only mechanism, which no shipped
    # adapter uses since 0.6.28: run it against the pre-0.6.28 codex fixture.
    legacy_dir = Path(tempfile.mkdtemp())
    legacy_env = dict(os.environ, DEV_LEAD_LAUNCH=str(_legacy_launch(legacy_dir)))
    r = subprocess.run([str(script), "codex", "implement", "--model", "x",
                        "--effort", "high"], capture_output=True, text=True, env=legacy_env)
    check("leg-cmd: implement banner does not disown a flag it emits",
          "--effort" not in r.stderr.split("NOT flags")[-1]
          or "NOT flags" not in r.stderr,
          f"stderr={r.stderr!r}")
    check("leg-cmd: implement banner states the role's real effort behaviour",
          "DOES take" in r.stderr, f"stderr={r.stderr!r}")
    r3 = subprocess.run([str(script), "codex", "implement", "--model", "x"],
                        capture_output=True, text=True, env=legacy_env)
    check("leg-cmd: an argv-decided flag role with no flag name refuses cleanly, naming --effort",
          r3.returncode != 0 and "needs --effort (it becomes --effort)" in r3.stderr
          and "Traceback" not in r3.stderr, r3.stderr)
    r2 = subprocess.run([str(script), "codex", "review", "--model", "x",
                         "--base", "abc"], capture_output=True, text=True, env=legacy_env)
    check("leg-cmd: ...and the review path keeps its warning",
          "NOT flags" in r2.stderr, f"stderr={r2.stderr!r}")

    # Every shipped skill must be launchable through the mandated path.
    r = subprocess.run([str(script), "grok", "implement", "--model", "grok-4.6",
                        "--effort", "medium", "--prompt-file", "/tmp/p.md"],
                       capture_output=True, text=True)
    check("leg-cmd: grok implement composes (it had no launch row)",
          r.returncode == 0 and "--sandbox workspace" in r.stdout,
          f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

    # A value the template never consumes used to be dropped in silence. For
    # --target that is a freeze-discipline hole: the caller believes the run is
    # pinned to a frozen worktree and it runs in their cwd.
    r = subprocess.run([str(script), "cursor", "review", "--model",
                        "cursor-grok-4.6-medium", "--target", "/tmp/frozen"],
                       capture_output=True, text=True)
    check("leg-cmd: refuses a --target the template cannot consume",
          r.returncode != 0, r.stdout)
    check("leg-cmd: says the run would use the caller's cwd",
          "cwd" in r.stderr, r.stderr)

    # Every corrected role must render, not just the one the author probed.
    for argv, needle in (
        (["agy", "implement", "--model", "gemini-3.8-flash-high",
          "--target", "/tmp/x"], "--mode accept-edits"),
        (["cursor", "implement", "--model", "cursor-grok-4.6-medium"], "--trust"),
        (["claude", "review", "--model", "sonnet", "--effort", "high"], "--strict-mcp-config"),
        (["grok", "review", "--model", "grok-4.6", "--effort", "high",
          "--prompt-file", "/tmp/p"], "--disallowed-tools"),
    ):
        r = subprocess.run([str(script), *argv], capture_output=True, text=True)
        check(f"leg-cmd: {argv[0]}/{argv[1]} renders", r.returncode == 0, r.stderr)
        check(f"leg-cmd: {argv[0]}/{argv[1]} keeps {needle}",
              needle in r.stdout, r.stdout)
    # 0.6.35: claude's --effort is scoped to review (applies_to_role).
    r = subprocess.run([str(script), "claude", "review", "--model", "opus"], capture_output=True, text=True)
    check("leg-cmd: claude review without --effort is refused",
          r.returncode != 0 and "needs --effort" in r.stderr and not r.stdout, r.stderr)
    r = subprocess.run([str(script), "claude", "review", "--model", "opus", "--effort", "xhigh"],
                       capture_output=True, text=True)
    check("leg-cmd: claude review passes the effort to the CLI",
          r.returncode == 0 and "--model opus --effort xhigh" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([str(script), "claude", "implement", "--model", "opus", "--effort", "high"],
                       capture_output=True, text=True)
    check("leg-cmd: claude implement refuses --effort (the scope is review only)",
          r.returncode != 0 and "passes no effort" in r.stderr and not r.stdout, r.stderr)
    r = subprocess.run([str(script), "claude", "implement", "--model", "opus"], capture_output=True, text=True)
    check("leg-cmd: claude implement without --effort renders, with no --effort in it",
          r.returncode == 0 and "--effort" not in r.stdout, r.stdout + r.stderr)
    check("leg-cmd: cursor implement carries no forbidden --force",
          "--force" not in subprocess.run(
              [str(script), "cursor", "implement", "--model", "m"],
              capture_output=True, text=True).stdout)


    # The provider guard checks that a provider is PRESENT, not that it is
    # opencode's. Measured 2026-09-15: the first version tested
    # startswith("opencode/"), so it refused every openrouter/ and google/ id
    # the same CLI serves -- 407 of the 414 models on that machine -- and told
    # the caller to fix it by PREPENDING, yielding `opencode/openrouter/...`,
    # which exists nowhere. Following that advice cost three launches and
    # produced the same UnknownError the guard exists to prevent. It also
    # blocked this account's standing opencode default one day after it was set.
    r = subprocess.run([str(script), "opencode", "review", "--model",
                        "openrouter/nvidia/nemotron-3.5-lightning:free",
                        "--effort", "high", *OC], capture_output=True, text=True)
    check("leg-cmd: another provider's qualified id is accepted",
          r.returncode == 0, r.stderr)
    check("leg-cmd: and reaches the command UNCHANGED",
          "-m openrouter/nvidia/nemotron-3.5-lightning:free" in r.stdout, r.stdout)

    r = subprocess.run([str(script), "opencode", "review", "--model",
                        "muse-spark-1.3-contributor-free", "--effort", "xhigh"],
                       capture_output=True, text=True)
    check("leg-cmd: a bare model name is still refused", r.returncode != 0, r.stdout)
    check("leg-cmd: the refusal names what is missing",
          "provider-qualified" in r.stderr and "no provider segment" in r.stderr,
          f"stderr={r.stderr!r}")

    # Every adapter reads its brief from a path the caller owns, and they fail
    # asymmetrically when it is empty: agy and cursor refuse loudly, opencode's
    # redirect dies before an output file exists, and codex RUNS -- its review
    # path has --base/--scope to work from, so it returns a plausible review
    # carrying none of the lens asked for. That is the false green the four-leg
    # method exists to prevent, and one assertion blocks all four.
    r = subprocess.run([str(script), "opencode", "review", "--model",
                        "opencode/x", "--effort", "high", *OC,
                        "--run-dir", "/tmp/leg-cmd-test"],
                       capture_output=True, text=True)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    check("leg-cmd: --run-dir emits an export the caller can eval",
          bool(lines) and lines[0] == "export RUN_DIR=/tmp/leg-cmd-test", r.stdout)
    check("leg-cmd: the brief assertion sits between export and command",
          len(lines) >= 3 and lines[1].startswith("test ")
          and "test -s" in lines[1] and "opencode run" in lines[2],
          r.stdout)
    # && rather than `exit 1`: this output is documented for `eval "$(...)"`,
    # and an exit inside eval kills the CALLER's interactive shell.
    check("leg-cmd: the assertion cannot kill the caller's shell",
          "exit 1" not in r.stdout and lines[1].rstrip().endswith("&&"), r.stdout)

    r = subprocess.run([str(script), "opencode", "review", "--model",
                        "opencode/x", "--effort", "high", *OC],
                       capture_output=True, text=True)
    check("leg-cmd: warns that RUN_DIR must be exported, not prefix-assigned",
          "prefix assignment" in r.stderr, f"stderr={r.stderr!r}")

    # An id with an empty path segment contains "/" and is still nonsense. The
    # first spelling of this guard asked only whether a slash was present, so
    # `opencode/`, `/x` and `a//b` all sailed through -- named by a review leg,
    # 2026-09-15.
    for bad in ("opencode/", "/x", "a//b"):
        r = subprocess.run([str(script), "opencode", "review", "--model", bad,
                            "--effort", "high", *OC], capture_output=True, text=True)
        check("leg-cmd: refuses the malformed id %r" % bad, r.returncode != 0, r.stdout)
        check("leg-cmd: and says it is the empty segment, not a missing provider",
              "EMPTY path segment" in r.stderr, f"stderr={r.stderr!r}")
    r = subprocess.run([str(script), "opencode", "review", "--model",
                        "openrouter/nvidia/x:free", "--effort", "high", *OC],
                       capture_output=True, text=True)
    check("leg-cmd: a well-formed two-segment provider id is still accepted",
          r.returncode == 0, r.stderr)

    # --run-dir has no placeholder in any template, so the loop that refuses a
    # silently-dropped --target/--base/--prompt-file could not see it. A review
    # leg caught the omission 2026-09-15: grok reads its brief from
    # --prompt-file, so a --run-dir passed there vanished with exit 0.
    r = subprocess.run([str(script), "grok", "review", "--model", "grok-4.6",
                        "--effort", "high", "--prompt-file", "/tmp/b.md",
                        "--run-dir", "/tmp/ignored"],
                       capture_output=True, text=True)
    check("leg-cmd: --run-dir is refused where the brief comes from --prompt-file",
          r.returncode != 0, f"stdout={r.stdout!r}")
    check("leg-cmd: and the refusal says it would have been dropped",
          "silently dropped" in r.stderr, f"stderr={r.stderr!r}")
    r = subprocess.run([str(script), "grok", "review", "--model", "grok-4.6",
                        "--effort", "high", "--prompt-file", "/tmp/b.md"],
                       capture_output=True, text=True)
    check("leg-cmd: the same adapter without --run-dir is still emitted",
          r.returncode == 0 and "test -s /tmp/b.md" in r.stdout, r.stdout)

    # Chain SEMANTICS, executed -- an assertion that cannot block is decoration.
    with tempfile.TemporaryDirectory() as td:
        emitted = subprocess.run(
            [str(script), "opencode", "review", "--model", "opencode/x",
             "--effort", "high", *OC, "--run-dir", td],
            capture_output=True, text=True).stdout.splitlines()
        probe = "\n".join(emitted[:-1] + ["echo RAN"])
        brief = Path(td) / "prompt.md"
        for label, content, should_run in (("missing brief", None, False),
                                           ("empty brief", "", False),
                                           ("real brief", "lens\n", True)):
            if content is None:
                brief.unlink(missing_ok=True)
            else:
                brief.write_text(content)
            out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True)
            check("leg-cmd: %s -> leg %s" % (label, "runs" if should_run else "blocked"),
                  ("RAN" in out.stdout) == should_run,
                  f"stdout={out.stdout!r} stderr={out.stderr!r}")

        # Under `set -e`, through `eval` -- which is the DOCUMENTED usage and
        # the only spelling that answers the question. Two review legs said an
        # errexit caller aborts here; the author "disproved" it by inlining the
        # chain in a script, where errexit exempts an && / || list, and so
        # measured a construct nobody runs. Through eval the legs are right:
        # eval is a simple command, errexit sees its status, the caller stops.
        # Pinned in BOTH directions so the next person cannot re-derive the
        # wrong half: no errexit -> the shell survives; errexit -> it stops,
        # which is what a script should do when its brief is missing.
        brief.unlink(missing_ok=True)
        emit = ("bash %s opencode review --model opencode/x --effort high --base abc --target %s --run-dir %s 2>/dev/null"
                % (script, OC[-1], td))
        for flags, label, sentinel_expected in ((["-c"], "no errexit", True),
                                                (["-c"], "errexit", False)):
            pre = "set -e; " if label == "errexit" else ""
            out = subprocess.run(["bash", *flags, '%seval "$(%s)"; echo SENTINEL' % (pre, emit)],
                                 capture_output=True, text=True)
            check("leg-cmd: eval under %s -> caller %s" % (label, "continues" if sentinel_expected else "stops"),
                  ("SENTINEL" in out.stdout) == sentinel_expected,
                  f"stdout={out.stdout!r} stderr={out.stderr!r}")

    # RUN_DIR UNSET is the case the warning alone did not cover: the assertion
    # would read `test -s /prompt.md`, which PASSES if that file happens to
    # exist, and the leg then runs against a brief nobody wrote.
    #
    # Asserted on the EMITTED TEXT, deliberately. The first version of this
    # check executed the chain with RUN_DIR unset in a temp dir -- and proved
    # nothing: `$RUN_DIR/prompt.md` with RUN_DIR empty is the absolute path
    # /prompt.md, so cd-ing somewhere writable cannot influence it, and the
    # check stayed green with the guard deleted. Mutation caught it. Planting a
    # real /prompt.md needs root and does not belong in a test suite, and the
    # script's actual product IS the emitted string, so that is what to assert.
    r = subprocess.run([str(script), "opencode", "review", "--model",
                        "opencode/x", "--effort", "high", *OC, "--run-dir", "/tmp/x"],
                       capture_output=True, text=True)
    guard_line = [l for l in r.stdout.splitlines() if l.startswith("test ")][0]
    check("leg-cmd: the guard requires RUN_DIR itself, not only the file",
          'test -n "$RUN_DIR"' in guard_line, guard_line)
    # ...and prompt_file delivery must NOT carry that clause: it has no RUN_DIR.
    r = subprocess.run([str(script), "grok", "review", "--model", "grok-4.6",
                        "--effort", "high", "--prompt-file", "/tmp/b.md"],
                       capture_output=True, text=True)
    guard_line = [l for l in r.stdout.splitlines() if l.startswith("test ")][0]
    check("leg-cmd: prompt_file delivery does not test RUN_DIR",
          "RUN_DIR" not in guard_line, guard_line)

    # The executing coverage above is all opencode, i.e. stdin delivery through
    # $RUN_DIR. The prompt_file branch builds a DIFFERENT assertion target and
    # had no executing test at all until a review leg said so.
    with tempfile.TemporaryDirectory() as td:
        bf = Path(td) / "brief.md"
        emitted = subprocess.run(
            [str(script), "grok", "review", "--model", "grok-4.6",
             "--effort", "high", "--prompt-file", str(bf)],
            capture_output=True, text=True).stdout.splitlines()
        probe = "\n".join(emitted[:-1] + ["echo RAN"])
        for label, content, should_run in (("missing", None, False),
                                           ("empty", "", False),
                                           ("real", "lens\n", True)):
            if content is None:
                bf.unlink(missing_ok=True)
            else:
                bf.write_text(content)
            out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True)
            check("leg-cmd: prompt_file brief %s -> leg %s"
                  % (label, "runs" if should_run else "blocked"),
                  ("RAN" in out.stdout) == should_run,
                  f"stdout={out.stdout!r} stderr={out.stderr!r}")
    # --add-dir (a peer session, 2026-09-22): cursor takes it, inserted before the
    # prompt and quoted; an adapter without the flag REFUSES rather than
    # dropping it -- for opencode, whose out-of-cwd read is auto-rejected
    # headless, with the rule that replaces it.
    r = subprocess.run([str(script), "cursor", "review", "--model", "grok-4.7-medium",
                        "--run-dir", "/tmp/r", "--add-dir", "/ctx one", "--add-dir", "/ctx2"],
                       capture_output=True, text=True)
    check("leg-cmd: cursor accepts --add-dir", r.returncode == 0, r.stderr)
    cmd = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    check("leg-cmd: every --add-dir reaches the command, quoted, before the prompt",
          "--add-dir '/ctx one' --add-dir /ctx2 \"$(cat" in cmd, cmd)
    r = subprocess.run([str(script), "opencode", "review", "--model", "opencode/x",
                        "--effort", "high", *OC, "--add-dir", "/ctx"], capture_output=True, text=True)
    check("leg-cmd: opencode refuses --add-dir instead of dropping it", r.returncode != 0, r.stdout)
    check("leg-cmd: ...and says to put the context inside the target",
          "INTO" in r.stderr and "leg-log-check" in r.stderr, r.stderr)
    r = subprocess.run([str(script), "cursor", "review", "--model", "x", "--add-dir", ""],
                       capture_output=True, text=True)
    check("leg-cmd: an empty --add-dir is refused", r.returncode != 0, r.stdout)
    r = subprocess.run([str(script), "cursor", "review", "--model", "x", "--add-dir", "--yolo"],
                       capture_output=True, text=True)
    check("leg-cmd: an --add-dir value that reads as a flag is refused",
          r.returncode != 0 and "--yolo" not in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([str(script), "agy", "review", "--model", "gemini-3.8-flash-medium",
                        "--base", "abc", "--target", "/tmp/x", "--add-dir", "/ctx"], capture_output=True, text=True)
    check("leg-cmd: an adapter without add_dir_flag refuses (agy too)", r.returncode != 0, r.stdout)
    r = subprocess.run([str(script)], capture_output=True, text=True)
    check("leg-cmd: the usage line says options are per adapter",
          "only where the adapter takes it" in r.stderr, r.stderr)


# ---------------------------------------------------------------- roster ----
def test_merge_gate(tmp, roster):
    """The merge gate is configuration, not memory (Aaron, 2026-09-23):
    `lead` lets a fully green verdict land itself, `user` keeps the person's
    approval. Position: its own function, called from test_roster."""
    path = tmp / "gate.json"
    doc = _live_roster()
    _write_doc(path, doc)
    env = _roster_env(tmp, DEV_LEAD_ROSTER=str(path))

    out = run(SCRIPTS / "roster.py", "show", env=env)
    check("gate: absent reads as the default and says so",
          "merge gate: user" in out.stdout and "default" in out.stdout, out.stdout)

    out = run(SCRIPTS / "roster.py", "gate", "lead", "--why", "owner ruling", env=env)
    check("gate: set to lead exits 0", out.returncode == 0, out.stderr + out.stdout)
    stored = json.loads(path.read_text())["merge_gate"]
    check("gate: stores mode, why and the date",
          stored["mode"] == "lead" and stored["why"] == "owner ruling" and stored.get("set_on"),
          stored)
    out = run(SCRIPTS / "roster.py", "show", env=env)
    check("gate: show names the mode and what it means",
          "merge gate: lead" in out.stdout and "green verdict is its own approval" in out.stdout,
          out.stdout)
    check("gate: ...and says what still goes to the person",
          "not green" in out.stdout, out.stdout)

    out = run(SCRIPTS / "roster.py", "gate", "user", env=env)
    check("gate: a change with no --why is refused", out.returncode != 0, out.stdout)
    check("gate: ...and does not change the file",
          json.loads(path.read_text())["merge_gate"]["mode"] == "lead")

    out = run(SCRIPTS / "roster.py", "gate", "bogus", "--why", "x", env=env)
    check("gate: an unknown mode is refused", out.returncode != 0, out.stdout)

    # show is what Phase 3 reads and it does not run check, so it must not
    # present a block check would refuse as the gate in force.
    # a roster whose GATE is valid but whose document is not: check exits
    # non-zero, so show must not present lead as in force either
    doc2 = _live_roster()
    doc2["merge_gate"] = {"mode": "lead", "why": "x", "set_on": "2026-09-23"}
    doc2["version"] = 2
    _write_doc(path, doc2)
    out = run(SCRIPTS / "roster.py", "show", env=env)
    check("gate: show refuses when the ROSTER is invalid, not just the gate block",
          "INVALID" in out.stdout and "merge gate: lead" not in out.stdout, out.stdout)

    doc2 = _live_roster(); doc2["merge_gate"] = {"mode": "lead", "modee": 1}
    _write_doc(path, doc2)
    out = run(SCRIPTS / "roster.py", "show", env=env)
    check("gate: show refuses to present an invalid block as in force",
          "INVALID" in out.stdout and "merge gate: lead" not in out.stdout, out.stdout)
    check("gate: ...and falls back to the person's gate in what it prints",
          "the person approves the verdict" in out.stdout, out.stdout)

    # A standing authorisation with no reason and no date is the one nobody can
    # audit later, and a hand edit is how it would arrive (codex leg, 2026-09-23).
    for bad, needle in (({"mode": "lead"}, "merge_gate.why"),
                        ({"mode": "lead", "why": "  ", "set_on": "2026-09-23"}, "merge_gate.why"),
                        ({"mode": "lead", "why": "x"}, "merge_gate.set_on"),
                        ({"mode": "lead", "why": "x", "set_on": "yesterday"}, "not an ISO date")):
        doc2 = _live_roster(); doc2["merge_gate"] = bad
        _write_doc(path, doc2)
        out = run(SCRIPTS / "roster.py", "check", env=env)
        check(f"gate: check refuses {bad!r}", out.returncode != 0 and needle in out.stdout,
              out.stdout)

    # check() must fail a hand-edited file, which is the only way these arrive
    for bad, needle in (({"mode": "auto"}, "merge_gate.mode"),
                        ({"mode": "lead", "modee": "typo"}, "merge_gate.modee"),
                        ("lead", "merge_gate")):
        doc2 = _live_roster(); doc2["merge_gate"] = bad
        _write_doc(path, doc2)
        out = run(SCRIPTS / "roster.py", "check", env=env)
        check(f"gate: check refuses {bad!r}", out.returncode != 0 and needle in out.stdout,
              out.stdout)

    # an unknown TOP-LEVEL key is an error too: a typo'd block would otherwise
    # be accepted in silence and the lead would run the default
    doc3 = _live_roster(); doc3["mege_gate"] = {"mode": "lead"}
    _write_doc(path, doc3)
    out = run(SCRIPTS / "roster.py", "check", env=env)
    check("gate: a typo'd top-level block is refused, not ignored",
          out.returncode != 0 and "mege_gate" in out.stdout, out.stdout)
    # ...but the keys the file legitimately carries are not
    doc4 = _live_roster(); doc4["provenance"] = {"review_roster": {"set_by": "Aaron"}}
    _write_doc(path, doc4)
    out = run(SCRIPTS / "roster.py", "check", env=env)
    check("gate: provenance still passes", out.returncode == 0, out.stdout)


def test_config_effort(tmp):
    TQ = chr(34) * 3   # a triple-quoted TOML scalar, written this way to keep
                       # this file readable inside its own heredocs
    """A config_only adapter's effort lives in a file on THAT machine, so the
    roster can only declare it. leg-cmd prints what is in force; check warns on
    a mismatch (measured on a peer's machine 2026-09-23: roster medium, config
    high, the round ran high). Position: after test_merge_gate."""
    home = tmp / "cfghome"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model_reasoning_effort = "high"\n')
    env = _roster_env(home)

    out = run(SCRIPTS / "leg-cmd.sh", "codex", "review", "--model", "gpt-5.6-terra",
              "--base", "abc", "--run-dir", str(tmp), env=env)
    check("config effort: leg-cmd prints what is IN FORCE, not what is declared",
          "IN FORCE on this machine: high" in out.stderr, out.stderr)
    check("config effort: ...names the per-call escape hatch, and the machine change only on a yes",
          "codex exec -c model_reasoning_effort" in out.stderr
          and "explicit yes" in out.stderr and "backup" in out.stderr, out.stderr)

    doc = _live_roster()
    doc["rounds"]["r1"]["review"]["codex"]["effort"] = "medium"
    doc["rounds"]["r1"]["review"]["codex"]["effort_in"] = "config_only"   # the legacy fixture's mechanism
    path = tmp / "cfg-roster.json"
    _write_doc(path, doc)
    out = run(SCRIPTS / "roster.py", "check", env=_roster_env(home, DEV_LEAD_ROSTER=str(path)))
    check("config effort: check WARNS on a declared/in-force mismatch",
          out.returncode == 0 and "declares 'medium'" in out.stdout
          and "says 'high'" in out.stdout, out.stdout)

    (home / ".codex" / "config.toml").write_text('model_reasoning_effort = "medium"\n')
    out = run(SCRIPTS / "roster.py", "check", env=_roster_env(home, DEV_LEAD_ROSTER=str(path)))
    check("config effort: no warning when they agree",
          out.returncode == 0 and "declares" not in out.stdout, out.stdout)

    # TOML shapes a review leg built to break the first (line-scanning) reader
    for text, want, label in (
        ('model_reasoning_effort = "high"  # temporary\n', "high", "an inline comment is not part of the value"),
        ('model_reasoning_effort_backup = "low"\nmodel_reasoning_effort = "high"\n', "high", "a prefix key is not the key"),
        ('[profiles.custom]\nmodel_reasoning_effort = "low"\n', None, "a key under a [table] is not a root key"),
        ('# model_reasoning_effort = "low"\nmodel_reasoning_effort = "high"\n', "high", "a commented-out line is skipped"),
        ('model_reasoning_effort="high"\n', "high", "no spaces around ="),
        ('model_reasoning_effort = high\n', "high", "an unquoted value"),
        ('model_reasoning_effort = high  # note\n', "high", "an inline comment after a BARE value"),
        ('"model_reasoning_effort" = "high"\n', "high", "a quoted key is the same key"),
        ("'model_reasoning_effort' = 'high'\n", "high", "a single-quoted key and value"),
        ('model_reasoning_effort = %shigh%s\n' % (TQ, TQ), "high", "a single-line triple-quoted value"),
        ('model_reasoning_effort = %s\nhigh\n%s\n' % (TQ, TQ), None, "a MULTI-line value is not guessed"),
        ('\ufeffmodel_reasoning_effort = "high"\n', "high", "a BOM does not hide the first key"),
        ('model_reasoning_effort = "high"\r\n', "high", "CRLF line endings"),
        ('model_reasoning_effort = "high\\"-priority"\n', 'high"-priority',
         "an escaped quote does not end the value"),
        ('description = %s\n[not_a_real_table]\n%s\nmodel_reasoning_effort = "high"\n' % (TQ, TQ),
         "high", "a [ inside a multi-line string is not a table header"),
        ("model_reasoning_effort = 'high\\'\n", "high\\", "a literal string has no escapes (TOML)"),
        ('notify = [\n  ["a"],\n]\nmodel_reasoning_effort = "high"\n', "high",
         "a multi-line array element is not a table header"),
        ('notify = [\n  ["a"],\n  ["b"],\n]\nmodel_reasoning_effort = "high"\n', "high",
         "an array with SEVERAL element lines is skipped by depth, not by the first ]"),
        ('model_reasoning_effort = "hi\\\ngh"\n', None,
         "an unterminated basic string is not read"),
        ('model_reasoning_effort = "\\u0068igh"\n', None,
         "an escape a real parser resolves is not guessed"),
        ('[[t]]\nmodel_reasoning_effort = "high"\n', None, "an array-of-tables header ends the root"),
        ('[t]  # note\nmodel_reasoning_effort = "high"\n', None,
         "a table header with a trailing comment still ends the root"),
        ('model_reasoning_effort = "high"\n[t]\nother = 1\n', "high",
         "a root key BEFORE the first table is still read"),


        ('other = 1\nmodel_reasoning_effort = "high"\n', "high", "a key after another root key"),
    ):
        (home / ".codex" / "config.toml").write_text(text)
        out = run(SCRIPTS / "leg-cmd.sh", "codex", "review", "--model", "gpt-5.6-terra",
                  "--base", "abc", "--run-dir", str(tmp), env=env)
        # the trailing " (" matters: without it "high" also matches a parse that
        # returned 'high"  # temporary', and the mutation stays green
        expect = "IN FORCE on this machine: %s (" % (want if want else "not set")
        check(f"config effort: {label}", expect in out.stderr, out.stderr.strip()[:200])

    (home / ".codex" / "config.toml").unlink()
    out = run(SCRIPTS / "roster.py", "check", env=_roster_env(home, DEV_LEAD_ROSTER=str(path)))
    check("config effort: an unreadable config is not a warning (nothing is known)",
          out.returncode == 0 and "declares" not in out.stdout, out.stdout)
    out = run(SCRIPTS / "leg-cmd.sh", "codex", "review", "--model", "gpt-5.6-terra",
              "--base", "abc", "--run-dir", str(tmp), env=env)
    check("config effort: leg-cmd says 'not set' rather than inventing one",
          "IN FORCE on this machine: not set" in out.stderr, out.stderr)


def test_config_effort_write(tmp):
    """`roster.py config-effort` -- the ONE path that writes a user's config
    (Aaron, 2026-09-23: "做，但要先備份並回報前後值"). Dry run by default;
    --yes backs up first, changes one line, reads back, reports old -> new.
    Position: after test_config_effort."""
    home = tmp / "cehome"
    cfgdir = home / ".codex"
    cfgdir.mkdir(parents=True)
    cfg = cfgdir / "config.toml"
    original = ('# my codex config\nmodel = "gpt-x"\n'
                'model_reasoning_effort = "high"  # I like it high\n'
                '[profiles.fast]\nmodel_reasoning_effort = "low"\n')
    cfg.write_text(original)
    os.chmod(cfg, 0o640)
    doc = _live_roster()
    doc["rounds"]["r1"]["review"]["codex"]["effort"] = "medium"
    rpath = tmp / "ce-roster.json"
    _write_doc(rpath, doc)
    env = _roster_env(home, DEV_LEAD_ROSTER=str(rpath))
    ce = lambda *extra: run(SCRIPTS / "roster.py", "config-effort", "r1", "review", "codex",
                           *extra, env=env)

    out = ce()
    check("config-effort: reports both values", "declared (roster): medium" in out.stdout
          and "in force" in out.stdout and ": high" in out.stdout, out.stdout)
    check("config-effort: without --yes nothing is written",
          out.returncode == 0 and cfg.read_text() == original
          and not list(cfgdir.glob("config.toml.bak.*")), out.stdout)
    check("config-effort: ...and says what WOULD change", "would change: high -> medium" in out.stdout,
          out.stdout)

    out = ce("--yes")
    backups = list(cfgdir.glob("config.toml.bak.*"))
    check("config-effort --yes: writes and reports old -> new",
          out.returncode == 0 and "changed: high -> medium" in out.stdout, out.stdout)
    check("config-effort --yes: exactly one backup, byte-identical to the old file",
          len(backups) == 1 and backups[0].read_text() == original, [b.name for b in backups])
    check("config-effort --yes: names the backup in its report",
          len(backups) == 1 and str(backups[0]) in out.stdout, out.stdout)
    new = cfg.read_text()
    check("config-effort --yes: only the root key's line changed",
          new == original.replace('model_reasoning_effort = "high"  # I like it high\n',
                                  'model_reasoning_effort = "medium"\n'), new)
    check("config-effort --yes: the [profiles.fast] value is untouched",
          'model_reasoning_effort = "low"' in new, new)
    check("config-effort --yes: file mode kept", (cfg.stat().st_mode & 0o777) == 0o640,
          oct(cfg.stat().st_mode & 0o777))

    out = ce("--yes")
    check("config-effort: agreeing values are a no-op with no new backup",
          "already agree" in out.stdout and len(list(cfgdir.glob("config.toml.bak.*"))) == 1,
          out.stdout)

    # absent from the root: inserted BEFORE the first table, never under it
    cfg.write_text('[profiles.fast]\nmodel_reasoning_effort = "low"\n')
    out = ce("--yes")
    check("config-effort: an absent root key is inserted above the first table",
          cfg.read_text().startswith('model_reasoning_effort = "medium"\n[profiles.fast]'),
          cfg.read_text())

    # the writer uses the SAME walker: an unterminated value is refused, not
    # half-overwritten (agy leg, 2026-09-23)
    broken = 'model_reasoning_effort = "hi\\\ngh"\n'
    cfg.write_text(broken)
    out = ce("--yes")
    check("config-effort: an unterminated value is refused, file untouched",
          out.returncode != 0 and cfg.read_text() == broken, out.stdout)

    # reader and writer split lines the same way: a \v inside a comment line
    # must not make --yes rewrite a line the dry run never reported
    tricky = '# c\vmodel_reasoning_effort = "hidden"\nmodel_reasoning_effort = "high"\n'
    cfg.write_text(tricky)
    out = ce("--yes")
    check("config-effort: an odd separator (\\v) does not move the edited line",
          out.returncode == 0 and '"hidden"' in cfg.read_text()
          and cfg.read_text().endswith('model_reasoning_effort = "medium"\n'), out.stdout)

    # a DANGLING symlink planted at a backup name is never written through
    from datetime import datetime as _dt2, timedelta as _td2
    cfg.write_text(original)
    for bak in cfgdir.glob("config.toml.bak.*"):
        bak.unlink()
    elsewhere = tmp / "should-not-exist.txt"
    now2 = _dt2.now()
    for sec in range(0, 6):
        link = cfgdir / ("config.toml.bak." + (now2 + _td2(seconds=sec)).strftime("%Y%m%d-%H%M%S"))
        link.symlink_to(elsewhere)
    out = ce("--yes")
    check("config-effort: a dangling symlink at the backup name is not followed",
          out.returncode == 0 and not elsewhere.exists(), out.stdout)
    for bak in cfgdir.glob("config.toml.bak.*"):
        bak.unlink()

    # ...nor a link to an EXISTING file: the backup is opened O_EXCL|O_NOFOLLOW
    # in one step, so no name that already exists is ever written through
    victim2 = tmp / "victim2.txt"
    victim2.write_text("KEEP ME\n")
    cfg.write_text(original)
    now3 = _dt2.now()
    for sec in range(0, 6):
        link = cfgdir / ("config.toml.bak." + (now3 + _td2(seconds=sec)).strftime("%Y%m%d-%H%M%S"))
        link.symlink_to(victim2)
    out = ce("--yes")
    check("config-effort: a backup-name link to a real file is never written through",
          out.returncode == 0 and victim2.read_text() == "KEEP ME\n", out.stdout)
    for bak in cfgdir.glob("config.toml.bak.*"):
        bak.unlink()

    # a value spanning lines is not guessed at, and nothing is written
    tq = chr(34) * 3
    spanning = 'model_reasoning_effort = %s\nhigh\n%s\n' % (tq, tq)
    cfg.write_text(spanning)
    out = ce("--yes")
    check("config-effort: a multi-line value is refused, file untouched",
          out.returncode != 0 and cfg.read_text() == spanning, out.stdout)

    # a failed backup is a STOP: nothing written over an un-backed-up file
    cfg.write_text(original)
    for b in cfgdir.glob("config.toml.bak.*"):
        b.unlink()
    os.chmod(cfgdir, 0o500)
    try:
        out = ce("--yes")
    finally:
        os.chmod(cfgdir, 0o700)
    check("config-effort: a failed backup refuses and writes nothing",
          out.returncode != 0 and "backup failed" in out.stdout and cfg.read_text() == original,
          out.stdout)

    # an existing backup is never overwritten, even by a change in the same
    # second: occupy the next few seconds' names, then change again
    from datetime import datetime as _dt, timedelta as _td
    cfg.write_text(original)
    for bak in cfgdir.glob("config.toml.bak.*"):
        bak.unlink()
    now = _dt.now()
    squatters = []
    for sec in range(0, 6):
        name = cfgdir / ("config.toml.bak." + (now + _td(seconds=sec)).strftime("%Y%m%d-%H%M%S"))
        name.write_text("EARLIER BACKUP %d\n" % sec)
        squatters.append(name)
    out = ce("--yes")
    check("config-effort: an earlier backup with the same timestamp is never overwritten",
          out.returncode == 0
          and all(b.read_text() == "EARLIER BACKUP %d\n" % i for i, b in enumerate(squatters)),
          out.stdout)
    check("config-effort: ...the new backup takes a -N name and holds the old bytes",
          any(b.name.rsplit("-", 1)[-1].isdigit() and len(b.name.rsplit("-", 1)[-1]) < 3
              and b.read_text() == original for b in cfgdir.glob("config.toml.bak.*-*")),
          sorted(b.name for b in cfgdir.glob("config.toml.bak.*")))

    # a roster effort that is not a plain word must not reach the file: a quote
    # or newline would inject further TOML settings
    cfg.write_text(original)
    for bad in ('medium"\nother_setting = "x', "high'", "me dium", "HIGH", "", "medium\n"):
        doc_bad = _live_roster()
        doc_bad["rounds"]["r1"]["review"]["codex"]["effort"] = bad
        _write_doc(rpath, doc_bad)
        out = ce("--yes")
        check(f"config-effort: refuses a non-word effort {bad!r} with the file byte-identical",
              out.returncode != 0 and cfg.read_text() == original, out.stdout)
    _write_doc(rpath, doc)

    # a pre-planted symlink at the OLD guessable temp name must not be followed
    victim = tmp / "victim.txt"
    victim.write_text("VICTIM\n")
    # The old name was config.toml.tmp.<pid>; the child's pid follows ours
    # closely, so planting the next few thousand makes the old code write
    # through one of them (mutation-checked: with the old path this goes red).
    base_pid = os.getpid()
    for pid in range(base_pid + 1, base_pid + 4000):
        planted = cfgdir / ("config.toml.tmp.%d" % pid)
        if not planted.exists():
            planted.symlink_to(victim)
    cfg.write_text(original)
    out = ce("--yes")
    check("config-effort: a planted temp-name symlink is never written through",
          out.returncode == 0 and victim.read_text() == "VICTIM\n"
          and 'model_reasoning_effort = "medium"' in cfg.read_text(), out.stdout)
    check("config-effort: ...and no temp file is left behind",
          not [f for f in cfgdir.iterdir() if f.name.startswith(".config.toml.")], 
          sorted(f.name for f in cfgdir.iterdir()))
    for planted in cfgdir.glob("config.toml.tmp.*"):
        planted.unlink()

    # a symlinked config (dotfiles) is edited at its TARGET; the link survives
    real = tmp / "dotfiles" / "codex-config.toml"
    real.parent.mkdir()
    real.write_text(original)
    cfg.unlink()
    cfg.symlink_to(real)
    for bak in cfgdir.glob("config.toml.bak.*"):
        bak.unlink()
    out = ce("--yes")
    check("config-effort: a symlinked config keeps its link",
          cfg.is_symlink() and os.path.realpath(cfg) == str(real), out.stdout)
    check("config-effort: ...its target is the file changed, and the report says so",
          'model_reasoning_effort = "medium"' in real.read_text()
          and "is a symlink" in out.stdout, out.stdout)
    check("config-effort: ...and the backup holds the target's old bytes",
          any(b.read_text() == original for b in real.parent.glob("codex-config.toml.bak.*")),
          sorted(b.name for b in real.parent.iterdir()))
    cfg.unlink()
    cfg.write_text(original)

    # a backup that fails part-way is removed, not left looking like a good one:
    # in-process, with fsync forced to fail
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("roster_under_test", SCRIPTS / "roster.py")
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    cfg.write_text(original)
    for bak in cfgdir.glob("config.toml.bak.*"):
        bak.unlink()
    saved_home, saved_fsync = os.environ.get("HOME"), os.fsync
    def _boom(fd):
        raise OSError(5, "injected fsync failure")
    import types as _types, contextlib as _ctx, io as _io
    buf = _io.StringIO()
    try:
        os.environ["HOME"] = str(home)
        os.fsync = _boom
        with _ctx.redirect_stdout(buf):
            rc = _mod.cmd_config_effort(rpath, _types.SimpleNamespace(
                round="r1", role="review", adapter="codex", yes=True))
    finally:
        os.fsync = saved_fsync
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
    check("config-effort: a backup that fails part-way refuses and leaves the config unchanged",
          rc != 0 and cfg.read_text() == original, buf.getvalue())
    check("config-effort: ...and removes its own partial backup",
          not list(cfgdir.glob("config.toml.bak.*")) and "partial backup was removed" in buf.getvalue(),
          sorted(b.name for b in cfgdir.glob("config.toml.bak.*")))

    # a leg that takes effort per call has no config to write
    out = run(SCRIPTS / "roster.py", "config-effort", "r1", "review", "agy", env=env)
    check("config-effort: refuses an adapter whose effort is not config_only",
          out.returncode != 0 and "per call" in out.stdout, out.stdout)


def _roster_doc(review, fix=None, implement=None):
    return {
        "version": 1,
        "rounds": {
            "r1": {
                "implement": {} if implement is None else implement,
                "review": review,
            },
            "fix": {"inherit": "r1"} if fix is None else fix,
        },
    }


def _codex_leg():
    return {
        "model": "gpt-5.6-terra",
        "effort": "medium",
        "effort_in": "flag",
        "family": "GPT",
        "note": "codex review runs `codex exec -c model_reasoning_effort=<e>` since 0.6.28",
    }


# Before 0.6.28 codex's review path read its effort from config.toml. No
# shipped adapter uses config_only any more, but the mechanism is still
# supported, so tests of it point DEV_LEAD_LAUNCH at this fixture.
_LEGACY_CODEX = {
    "review": {"argv": ["adversarial-review", "--wait", "--base", "{BASE}", "--scope", "branch",
                        "--model", "{MODEL}", "{PROMPT}"], "prompt_delivery": "argv"},
    "effort": {"mechanism": "config_only", "applies_to_role": "review",
               "config_key": "model_reasoning_effort", "config_file": "~/.codex/config.toml",
               "note": "The review path has NO effort flag.",
               "implement_note": "The `task` role DOES take `--effort` (none|minimal|low|medium|high|xhigh)."},
    "not_flags": ["--help", "--effort"],
}


class _legacy_launch_env:
    """Point every subprocess at the pre-0.6.28 codex launch data, and the
    in-process roster module too, for the span of one test."""

    def __init__(self, tmp):
        self.path = _legacy_launch(tmp)

    def __enter__(self):
        self.saved = os.environ.get("DEV_LEAD_LAUNCH")
        os.environ["DEV_LEAD_LAUNCH"] = str(self.path)
        return self.path

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("DEV_LEAD_LAUNCH", None)
        else:
            os.environ["DEV_LEAD_LAUNCH"] = self.saved


def _legacy_launch(tmp):
    """A launch.json whose codex review is the pre-0.6.28 companion path."""
    doc = json.loads((SCRIPTS.parent / "data" / "launch.json").read_text(encoding="utf-8"))
    codex = doc["codex"]
    codex["role"]["review"] = _LEGACY_CODEX["review"]
    codex["effort"] = _LEGACY_CODEX["effort"]
    codex["not_flags"] = _LEGACY_CODEX["not_flags"]
    path = tmp / "legacy-launch.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _live_roster():
    """Structure of the owner's roster. Inline on purpose: the file itself is not
    committed, and check has to accept this shape unchanged."""
    return {
        "_comment": ["declaration, not a launcher default"],
        "version": 1,
        "rounds": {
            "r1": {
                "_comment": "The first implementation round and its review legs.",
                "implement": {
                    "agy": None, "codex": None, "cursor": None,
                    "opencode": None, "claude": None,
                },
                "review": {
                    "agy": {
                        "model": "gemini-3.8-flash-medium",
                        "effort_in": "model_name",
                        "family": "Gemini",
                    },
                    "codex": _codex_leg(),
                    "cursor": {
                        "model": "cursor-grok-4.6-medium",
                        "effort_in": "model_name",
                        "family": "Grok",
                    },
                    "opencode": {
                        "_comment": "Routed by the lens the leg carries, not by the model's tier.",
                        "by_lens": {
                            "mechanical": {
                                "model": "opencode/muse-spark-1.3-contributor-free",
                                "effort": "xhigh",
                                "effort_in": "flag",
                                "family": "Meta",
                                "fallback": {
                                    "when": "the free pool is congested or at its daily limit",
                                    "model": "opencode-go/muse-spark-1.3-contributor",
                                    "effort": "xhigh",
                                    "family": "Meta",
                                    "note": "paid twin, its own Go bucket; adds NO family -- the report says which one ran",
                                },
                            },
                            "judgment": {
                                "model": "opencode-go/glm-5.2",
                                "effort": "high",
                                "effort_in": "flag",
                                "family": "GLM",
                                "gated_on": "a calibration row for glm-5.2 existing in dev-lead's opencode-runtime.md; until then use the mechanical entry above",
                                "not": "glm-5.3 -- same list price, but a $3 five-hour bucket (the kimi-k3 / qwen3.8-max small-bucket class)",
                            },
                        },
                        "top_tier_judgment_only": {
                            "models": ["opencode-go/kimi-k3", "opencode-go/qwen3.8-max", "opencode-go/glm-5.3"],
                            "limit": "at most one or two per 5-hour window; the buckets are account-wide, so every session draws on the same ones",
                        },
                    },
                },
            },
            "fix": {"_comment": ["inherit r1"], "inherit": "r1"},
        },
        "provenance": {
            "review_roster": {"set_by": "Aaron", "set_on": "2026-09-16"},
            "moved_here_from_claude_md": {"on": "2026-09-22", "by": "Aaron"},
        },
    }


def _write_doc(path, doc):
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _roster_env(home, **extra):
    env = dict(os.environ)
    env.pop("DEV_LEAD_ROSTER", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env["HOME"] = str(home)
    env.update({k: str(v) for k, v in extra.items()})
    return env


_NO_CONFIG_HOME = None


def _checked(path):
    # HOME points at a directory with no ~/.codex/config.toml on purpose: since
    # 0.6.20 `check` warns when a config_only leg's declared effort contradicts
    # the machine's config, and these assertions compare stdout exactly. Without
    # this they would pass or fail by whatever the developer's own codex config
    # says (cursor leg, 2026-09-23).
    global _NO_CONFIG_HOME
    if _NO_CONFIG_HOME is None:
        _NO_CONFIG_HOME = tempfile.mkdtemp(prefix="roster-no-config-")
    env = dict(os.environ)
    env["HOME"] = _NO_CONFIG_HOME
    env.pop("DEV_LEAD_ROSTER", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    return run(SCRIPTS / "roster.py", "check", "--file", str(path), env=env)


def test_roster(tmp):
    import importlib.util
    from datetime import date

    roster = SCRIPTS / "roster.py"
    check("roster.py exists", roster.is_file())
    check("roster.py is executable", os.access(roster, os.X_OK))
    check("roster.py shebang",
          roster.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n"))

    live = tmp / "live.json"
    _write_doc(live, _live_roster())
    got = _checked(live)
    check("roster: live-format fixture passes check",
          got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)

    example = SCRIPTS.parent / "templates" / "roster.example.json"
    got = _checked(example)
    check("roster: templates/roster.example.json passes check",
          got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)

    # The plugin's own data files, missing or broken: named, exit 2, no
    # traceback -- the way load_roster has always answered for the roster.
    # Until 0.6.48 data() read them bare and the first command to touch them
    # died in json.loads (measured with DEV_LEAD_LAUNCH pointing at nothing).
    gone = tmp / "no-such-launch.json"
    got = run(roster, "check", "--file", str(live), env=_roster_env(tmp, DEV_LEAD_LAUNCH=str(gone)))
    check("roster: a missing launch.json is named, exit 2, no traceback",
          got.returncode == 2 and str(gone) in got.stderr and "Traceback" not in got.stderr,
          got.stdout + got.stderr[-300:])
    broken = tmp / "broken-launch.json"
    broken.write_text("{not json")
    got = run(roster, "check", "--file", str(live), env=_roster_env(tmp, DEV_LEAD_LAUNCH=str(broken)))
    check("roster: a broken launch.json is named as invalid JSON, exit 2, no traceback",
          got.returncode == 2 and "invalid JSON" in got.stderr and str(broken) in got.stderr
          and "Traceback" not in got.stderr, got.stdout + got.stderr[-300:])
    # Found by review (codex): the wording fix (F5) had no test at all.
    check("roster: the example's comment matches path's real two-token output",
          "the path that `roster.py path` prints first" in example.read_text(encoding="utf-8"),
          example.read_text(encoding="utf-8"))

    # Aaron, 2026-09-22 (/dev-lead:config): opencode-go/kimi-k3 is CALLABLE
    # (calibration row in opencode-runtime.md, 2026-09-16) and CLAUDE.md's own
    # roster policy names it a legitimate top-tier judgment leg -- but
    # data/families.json had not registered Kimi under opencode's `serves`,
    # so a roster naming it was refused by check()/plan() as an unknown family.
    families = json.loads((SCRIPTS.parent / "data" / "families.json").read_text(encoding="utf-8"))
    check("families.json: opencode serves Kimi (opencode-go/kimi-k3 is real)",
          "Kimi" in families["adapters"]["opencode"]["serves"], families["adapters"]["opencode"])
    kimi_doc = _roster_doc({"opencode": {"model": "opencode-go/kimi-k3", "effort": "high",
                                         "effort_in": "flag", "family": "Kimi"}})
    path = tmp / "kimi.json"
    _write_doc(path, kimi_doc)
    got = _checked(path)
    check("roster: opencode/kimi-k3 with family Kimi passes check",
          got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)

    # Found by review (codex): the badge fix (F9) had no test at all.
    readme = SCRIPTS.parent / "README.md"
    readme_text = readme.read_text(encoding="utf-8")
    check("roster: README's skill-count badge says 14",
          "skills-14" in readme_text and "skills-13" not in readme_text, readme_text[:200])

    def fails(name, doc, needle):
        path = tmp / ("%s.json" % name.replace(" ", "-"))
        _write_doc(path, doc)
        got = _checked(path)
        text = got.stdout + got.stderr
        check("roster: %s exits 1" % name, got.returncode == 1, text)
        check("roster: %s reports %r" % (name, needle), needle in text, text)

    fails("unknown adapter", _roster_doc({"nope": {"model": "x", "family": "GPT"}}),
          "unknown adapter")
    bad_role = _roster_doc({"codex": _codex_leg()})
    bad_role["rounds"]["r1"]["audit"] = {}
    fails("bad role", bad_role, "unknown role")
    fails("unknown family",
          _roster_doc({"codex": {**_codex_leg(), "family": "Martian"}}),
          "unknown family")
    fails("effort on model_suffix",
          _roster_doc({"agy": {"model": "gemini-3.8-flash-medium", "effort": "medium",
                               "family": "Gemini"}}),
          "effort")
    fails("missing effort on flag",
          _roster_doc({"opencode": {"model": "opencode/x", "family": "Meta"}}),
          "missing effort")
    fails("codex implement missing effort",
          _roster_doc(
              {"agy": {"model": "gemini-3.8-flash-medium", "effort_in": "model_name",
                       "family": "Gemini"}},
              implement={"codex": {"model": "gpt-5.6-terra"}},
          ),
          "missing effort")
    fails("effort_in mismatch",
          _roster_doc({"agy": {"model": "gemini-3.8-flash-medium", "effort_in": "flag",
                               "family": "Gemini"}}),
          "effort_in")
    fails("typo key",
          _roster_doc({"agy": {"model": "gemini-3.8-flash-medium", "family": "Gemini",
                               "modle": "gemini-3.8-flash-medium"}}),
          "modle")
    fails("two review legs same family",
          _roster_doc({
              "agy": {"model": "gemini-3.8-flash-medium", "effort_in": "model_name",
                      "family": "Gemini"},
              "cursor": {"model": "cursor-grok-4.6-medium", "effort_in": "model_name",
                         "family": "Gemini"},
          }),
          "share family Gemini")
    omitted = tmp / "omitted-family-collision.json"
    _write_doc(omitted, _roster_doc({
        "codex": {"model": "gpt-5.6-terra", "effort": "medium", "effort_in": "config_only"},
        "cursor": {"model": "gpt-5.6", "effort_in": "model_name", "family": "GPT"},
    }))
    got = _checked(omitted)
    text = got.stdout + got.stderr
    check("roster check: a single-family leg with no family still collides",
          got.returncode == 1 and "codex" in text and "cursor" in text and "GPT" in text,
          text)
    same = _checked(tmp / "two-review-legs-same-family.json")
    check("roster: collision names both legs",
          "agy review" in same.stdout and "cursor review" in same.stdout, same.stdout)
    fails("bad fix inherit target",
          _roster_doc({"codex": _codex_leg()}, fix={"inherit": "r2"}),
          "bad inherit target")
    fails("fix round with zero review legs",
          _roster_doc({"codex": _codex_leg()},
                      fix={"implement": {}, "review": {"codex": None}}),
          "fix round has no review leg")
    bad_version = _roster_doc({"codex": _codex_leg()})
    bad_version["version"] = 2
    fails("version != 1", bad_version, "must be 1")

    warned = tmp / "composer.json"
    _write_doc(warned, _roster_doc({
        "cursor": {"model": "composer-2", "effort_in": "model_name", "family": "Composer"},
    }))
    got = _checked(warned)
    check("roster: accounting_valid false is a warning and exits 0",
          got.returncode == 0 and "cannot be the accounting leg" in got.stdout,
          got.stdout + got.stderr)

    # Lenses of one leg are alternatives. Sharing a family with each other is
    # not a collision; sharing one with another adapter is.
    lenses = _roster_doc({
        "codex": _codex_leg(),
        "opencode": {"by_lens": {
            "mechanical": {"model": "opencode/a", "effort": "xhigh", "family": "Meta"},
            "judgment": {"model": "opencode-go/b", "effort": "high", "family": "Meta"},
        }},
    })
    path = tmp / "lenses-ok.json"
    _write_doc(path, lenses)
    got = _checked(path)
    check("roster: two lenses of one leg may share a family",
          got.returncode == 0, got.stdout + got.stderr)
    lenses["rounds"]["r1"]["review"]["agy"] = {
        "model": "gemini-3.8-flash-medium", "family": "Meta", "effort_in": "model_name",
    }
    # Meta is not in agy's serves — use Gemini on the lens instead.
    lenses["rounds"]["r1"]["review"]["opencode"]["by_lens"]["mechanical"]["family"] = "Gemini"
    lenses["rounds"]["r1"]["review"]["agy"]["family"] = "Gemini"
    path = tmp / "lens-collision.json"
    _write_doc(path, lenses)
    got = _checked(path)
    text = got.stdout + got.stderr
    check("roster: a lens collides with another adapter",
          got.returncode == 1 and "Gemini" in text
          and "opencode review (mechanical)" in text and "agy review" in text,
          text)
    fb = _roster_doc({
        "agy": {"model": "gemini-3.8-flash-medium", "family": "Gemini",
                "effort_in": "model_name"},
        "opencode": {"by_lens": {"mechanical": {
            "model": "opencode/a", "effort": "xhigh", "family": "Meta",
            "fallback": {"model": "opencode-go/a", "effort": "xhigh", "family": "Gemini"},
        }}},
    })
    path = tmp / "fallback-collision.json"
    _write_doc(path, fb)
    got = _checked(path)
    text = got.stdout + got.stderr
    check("roster: a fallback family collides with another adapter",
          got.returncode == 1 and "fallback" in text and "agy review" in text
          and "Gemini" in text, text)

    # Found by review (codex): a fallback on a multi-family adapter with NO family
    # was exempt from the family rule, so it could run a model of the implementer's
    # own family unseen -- cursor review labelled Grok, falling back to a Claude model,
    # against a Claude implementation.
    unlabelled = _roster_doc({
        "cursor": {"model": "grok-4.7-high", "family": "Grok", "effort_in": "model_name",
                   "fallback": {"model": "claude-sonnet"}},
    }, implement={"claude": {"model": "claude-sonnet", "family": "Claude"}})
    path = tmp / "fallback-unlabelled.json"
    _write_doc(path, unlabelled)
    got = _checked(path)
    text = got.stdout + got.stderr
    # 0.6.35: claude's effort is scoped to review. A review entry must carry
    # one; an implement entry must not; and plan's implement override, which
    # has no effort syntax, keeps working.
    scoped = _roster_doc({"claude": {"model": "claude-opus-5-5", "family": "Claude"}},
                         implement={"claude": {"model": "claude-opus-5-5", "family": "Claude", "effort": "high"}})
    path_scoped = tmp / "claude-scoped.json"
    _write_doc(path_scoped, scoped)
    g35 = _checked(path_scoped)
    text_scoped = g35.stdout + g35.stderr
    check("roster check: a claude REVIEW leg without an effort is refused",
          g35.returncode == 1 and "rounds.r1.review.claude.effort: missing effort" in text_scoped, text_scoped)
    check("roster check: a claude IMPLEMENT leg with an effort is refused, naming the role",
          "rounds.r1.implement.claude.effort: effort key is not allowed; the suite passes no effort for claude implement"
          in text_scoped, text_scoped)
    check("roster check: ...and the missing review effort points at the 0.6.35 migration",
          "claude review takes --effort since 0.6.35" in text_scoped, text_scoped)
    ok_doc = _roster_doc({"claude": {"model": "claude-opus-5-5", "family": "Claude", "effort": "xhigh"},
                          "codex": _codex_leg()},
                         implement={"claude": {"model": "claude-opus-5-5", "family": "Claude"}})
    path_ok = tmp / "claude-scoped-ok.json"
    _write_doc(path_ok, ok_doc)
    g35 = run(roster, "plan", "--round", "r1", "--implement", "agy=gemini-3.8-flash-high:Gemini", "--review", "claude",
              env=_roster_env(tmp, DEV_LEAD_ROSTER=path_ok))
    check("roster plan: a claude review leg passes --effort",
          g35.returncode == 0 and "--model claude-opus-5-5 --effort xhigh" in g35.stdout, g35.stdout + g35.stderr)
    g35 = run(roster, "plan", "--round", "r1", "--implement", "claude=claude-opus-5-5", "--review", "codex",
              env=_roster_env(tmp, DEV_LEAD_ROSTER=path_ok))
    shown = run(roster, "show", env=_roster_env(tmp, DEV_LEAD_ROSTER=path_ok))
    check("roster show: a claude review leg reads as a flag with its level",
          "effort=flag xhigh" in shown.stdout, shown.stdout + shown.stderr)
    impl_line = [l for l in shown.stdout.splitlines() if "claude" in l and "effort=" in l and "flag xhigh" not in l]
    check("roster show: ...and the claude implement leg as none (its role's mechanism, not the adapter's)",
          impl_line and all("effort=none" in l for l in impl_line), shown.stdout)
    check("roster plan: the claude implement override still resolves without an effort",
          g35.returncode == 0 and "missing effort" not in (g35.stdout + g35.stderr), g35.stdout + g35.stderr)

    check("roster check: a multi-family fallback without a family is refused",
          got.returncode == 1 and "rounds.r1.review.cursor.fallback.family" in text, text)
    got = run(roster, "plan", "--round", "r1", "--implement", "claude", "--review", "cursor",
              env=_roster_env(tmp, DEV_LEAD_ROSTER=path))
    text = got.stdout + got.stderr
    check("roster plan: ... and so is the round that would run it",
          got.returncode == 1 and "fallback" in text, text)

    nested = _roster_doc({
        "cursor": {"model": "grok-4", "family": "Grok", "effort_in": "model_name",
                   "fallback": {"model": "y", "fallback": {"modle": "z"}}},
    })
    path = tmp / "nested-fallback.json"
    _write_doc(path, nested)
    got = _checked(path)
    text = got.stdout + got.stderr
    check("roster check: a fallback cannot contain fallback.fallback",
          got.returncode == 1 and "fallback.fallback" in text
          and "a fallback cannot have its own fallback" in text, text)

    no_effort = _roster_doc({"codex": {"model": "gpt-6-luna", "family": "GPT"}})
    path = tmp / "no-effort-codex.json"
    _write_doc(path, no_effort)
    got = _checked(path)
    text = got.stdout + got.stderr
    check("roster check: a pre-0.6.28 codex review with no effort fails WITH the migration step",
          got.returncode == 1 and "missing effort" in text and "since 0.6.28" in text
          and "model_reasoning_effort" in text, text)

    stale = _roster_doc({"codex": {**_codex_leg(), "effort_in": "config_only"}})
    path = tmp / "stale-effort-in.json"
    _write_doc(path, stale)
    got = _checked(path)
    text = got.stdout + got.stderr
    check("roster check: a pre-0.6.28 effort_in 'config_only' on codex review warns, not fails",
          got.returncode == 0 and "is stale" in text, text)

    listy = _roster_doc({"opencode": {"model": "opencode/m", "family": "Meta", "effort": ["high"],
                                      "effort_in": "flag"}})
    path = tmp / "list-effort.json"
    _write_doc(path, listy)
    got = _checked(path)
    text = got.stdout + got.stderr
    check("roster check: a non-string effort is refused",
          got.returncode == 1 and "must be a string, got list" in text and "Traceback" not in text, text)

    home = tmp / "home"
    home.mkdir()
    env = _roster_env(home, DEV_LEAD_ROSTER=live)
    plan = run(roster, "plan", "--round", "r1", "--implement", "claude=claude-sonnet",
               "--review", "codex,opencode", env=env)
    out = plan.stdout + plan.stderr
    check("roster plan: codex + opencode exits 0", plan.returncode == 0, out)

    def args_of(stdout, role, adapter):
        for line in stdout.splitlines():
            head = line.split()
            if len(head) >= 2 and head[0] == role and head[1] == adapter and "args=" in line:
                return line.split("args=", 1)[1]
        return ""

    codex_args = args_of(plan.stdout, "review", "codex")
    opencode_args = args_of(plan.stdout, "review", "opencode")
    check("roster plan: codex review args pass --effort (codex exec takes it per call, 0.6.28)",
          codex_args == "--model gpt-5.6-terra --effort medium", codex_args)
    check("roster plan: opencode review args pass --effort",
          opencode_args == "--model opencode/muse-spark-1.3-contributor-free --effort xhigh",
          opencode_args)
    check("roster plan: codex review effort is the roster's, not the config file's",
          "review codex model=gpt-5.6-terra family=GPT effort=flag medium" in plan.stdout
          and "read from ~/.codex/config.toml" not in plan.stdout, plan.stdout)

    same_family = tmp / "same-family-plan.json"
    _write_doc(same_family, _roster_doc({
        "agy": {"model": "gemini-3.8-flash-medium", "effort_in": "model_name",
                "family": "Gemini"},
    }))
    env = _roster_env(home, DEV_LEAD_ROSTER=same_family)
    got = run(roster, "plan", "--round", "r1",
              "--implement", "agy=gemini-3.8-flash-medium:Gemini", "--review", "agy",
              env=env)
    text = got.stdout + got.stderr
    check("roster plan: implementer and reviewer same family exits 1",
          got.returncode == 1 and "agy implement" in text and "agy review" in text
          and "Gemini" in text, text)

    two = tmp / "two-reviewers-plan.json"
    # Claude is served by both agy and cursor, so the collision is the family
    # and not an unknown-family error.
    two_doc = _roster_doc({
        "agy": {"model": "gemini-3.8-flash-medium", "effort_in": "model_name",
                "family": "Claude"},
        "cursor": {"model": "claude-sonnet", "effort_in": "model_name", "family": "Claude"},
    })
    _write_doc(two, two_doc)
    env = _roster_env(home, DEV_LEAD_ROSTER=two)
    got = run(roster, "plan", "--round", "fix", "--implement", "grok=grok-4",
              "--review", "agy,cursor", env=env)
    text = got.stdout + got.stderr
    check("roster plan: two reviewers same family exits 1",
          got.returncode == 1 and "agy review" in text and "cursor review" in text
          and "Claude" in text, text)

    unset = tmp / "unset-plan.json"
    _write_doc(unset, _roster_doc({"codex": _codex_leg()}))
    env = _roster_env(home, DEV_LEAD_ROSTER=unset)
    got = run(roster, "plan", "--round", "r1", "--implement", "agy",
              "--review", "codex", env=env)
    text = got.stdout + got.stderr
    check("roster plan: unset leg exits 1",
          got.returncode == 1 and "unset" in text and "agy" in text, text)

    # 0.6.24: gated_on was an allowed key that nothing honoured, so plan
    # handed out a leg the roster itself said was not dispatchable yet.
    gated = tmp / "gated-plan.json"
    _write_doc(gated, _live_roster())
    env = _roster_env(home, DEV_LEAD_ROSTER=gated)
    got = run(roster, "plan", "--round", "r1", "--implement", "claude=claude-sonnet", "--review", "opencode",
              "--lens", "judgment", env=env)
    text = got.stdout + got.stderr
    by_lens = _live_roster()["rounds"]["r1"]["review"]["opencode"]["by_lens"]
    shown = run(roster, "show", env=env)
    judgment_line = next((line for line in shown.stdout.splitlines()
                          if line.strip().startswith("judgment:") and "(inherited" not in line), "")
    check("roster show: a gated entry is marked GATED on its own line",
          "GATED" in judgment_line and by_lens["judgment"]["gated_on"] in judgment_line, shown.stdout)
    gated_fb = _live_roster()
    gated_fb["rounds"]["r1"]["review"]["opencode"]["by_lens"]["mechanical"]["fallback"]["gated_on"] = "a fallback probe"
    gated_fb_file = tmp / "gated-fallback.json"
    _write_doc(gated_fb_file, gated_fb)
    shown = run(roster, "show", env=_roster_env(home, DEV_LEAD_ROSTER=gated_fb_file))
    mechanical_line = next((line for line in shown.stdout.splitlines()
                            if line.strip().startswith("mechanical:") and "(inherited" not in line), "")
    check("roster show: a gated fallback is marked GATED too",
          "fallback GATED: a fallback probe" in mechanical_line, shown.stdout)
    fb_plan = run(roster, "plan", "--round", "r1", "--implement", "claude=claude-sonnet", "--review", "opencode",
                  env=_roster_env(home, DEV_LEAD_ROSTER=gated_fb_file))
    check("roster plan: a gated fallback is marked GATED on the plan line",
          fb_plan.returncode == 0 and "(fallback GATED: a fallback probe)" in fb_plan.stdout, fb_plan.stdout + fb_plan.stderr)
    plain = _live_roster()
    plain["rounds"]["r1"]["review"]["cursor"]["gated_on"] = "a cursor probe"
    plain_file = tmp / "gated-plain-plan.json"
    _write_doc(plain_file, plain)
    plain_plan = run(roster, "plan", "--round", "r1", "--implement", "claude=claude-sonnet", "--review", "cursor",
                     env=_roster_env(home, DEV_LEAD_ROSTER=plain_file))
    impl_gated = _live_roster()
    impl_gated["rounds"]["r1"]["implement"]["cursor"] = {"model": "grok-4.7-high", "family": "Grok",
                                                          "gated_on": "an implement probe"}
    impl_file = tmp / "gated-implement-plan.json"
    _write_doc(impl_file, impl_gated)
    impl_plan = run(roster, "plan", "--round", "r1", "--implement", "cursor", "--review", "codex",
                    env=_roster_env(home, DEV_LEAD_ROSTER=impl_file))
    check("roster plan: a gated implement leg is an error and its model is not printed",
          impl_plan.returncode == 1 and "gated (an implement probe)" in impl_plan.stdout + impl_plan.stderr
          and "model=grok-4.7-high" not in impl_plan.stdout, impl_plan.stdout + impl_plan.stderr)
    check("roster plan: a gated plain leg is an error, not a dispatch",
          plain_plan.returncode == 1 and "gated (a cursor probe)" in plain_plan.stdout + plain_plan.stderr,
          plain_plan.stdout + plain_plan.stderr)
    whole = _live_roster()
    whole["rounds"]["r1"]["review"]["opencode"]["gated_on"] = "a whole-leg probe"
    whole_file = tmp / "gated-whole-plan.json"
    _write_doc(whole_file, whole)
    whole_plan = run(roster, "plan", "--round", "r1", "--implement", "claude=claude-sonnet", "--review", "opencode",
                     env=_roster_env(home, DEV_LEAD_ROSTER=whole_file))
    whole_show = run(roster, "show", env=_roster_env(home, DEV_LEAD_ROSTER=whole_file))
    heading = next((line for line in whole_show.stdout.splitlines()
                    if line.strip().startswith("opencode:") and "GATED, every lens" in line), "")
    check("roster show: a gate on a whole by_lens leg is marked on its heading",
          "GATED, every lens" in heading and "a whole-leg probe" in heading, whole_show.stdout)
    check("roster plan: a gate on a whole by_lens leg covers every lens",
          whole_plan.returncode == 1 and "gated (a whole-leg probe)" in whole_plan.stdout + whole_plan.stderr
          and "review opencode" not in whole_plan.stdout, whole_plan.stdout + whole_plan.stderr)
    check("roster plan: a gated judgment entry is replaced by the mechanical one, and says so",
          got.returncode == 0 and "is gated" in text
          and "review opencode model=%s " % by_lens["mechanical"]["model"] in text
          and "review opencode model=%s " % by_lens["judgment"]["model"] not in text, text)

    wide = tmp / "cursor-family.json"
    _write_doc(wide, _roster_doc({"codex": _codex_leg()}))
    env = _roster_env(home, DEV_LEAD_ROSTER=wide)
    got = run(roster, "plan", "--round", "r1",
              "--implement", "cursor=grok-4.7-high", "--review", "codex", env=env)
    text = got.stdout + got.stderr
    check("roster plan: multi-family implement without :FAMILY exits 1",
          got.returncode == 1 and "family required" in text and "cursor" in text, text)
    got = run(roster, "plan", "--round", "r1",
              "--implement", "cursor=grok-4.7-high:Grok", "--review", "codex", env=env)
    check("roster plan: :Grok exits 0 when no reviewer is Grok",
          got.returncode == 0, got.stdout + got.stderr)
    check("roster plan: cursor override args carry the model and no --effort",
          args_of(got.stdout, "implement", "cursor") == "--model grok-4.7-high",
          got.stdout)
    check("roster plan: reports the model override",
          "override" in got.stdout, got.stdout)

    swallowed = tmp / "family-suffix.json"
    _write_doc(swallowed, _roster_doc({
        "agy": {"model": "gemini-3.8-flash-medium", "effort_in": "model_name",
                "family": "Gemini"},
    }))
    env = _roster_env(home, DEV_LEAD_ROSTER=swallowed)
    got = run(roster, "plan", "--round", "r1",
              "--implement", "claude=claude-opus:GPT", "--review", "agy", env=env)
    text = got.stdout + got.stderr
    check("roster plan: a :FAMILY the adapter does not serve exits 1 naming it",
          got.returncode == 1 and "GPT" in text and "not served by claude" in text,
          text)
    free = tmp / "free-suffix.json"
    _write_doc(free, _roster_doc(
        {"codex": _codex_leg()},
        implement={"opencode": {"model": "opencode/placeholder", "effort": "high",
                                "family": "Meta"}},
    ))
    env = _roster_env(home, DEV_LEAD_ROSTER=free)
    got = run(roster, "plan", "--round", "r1",
              "--implement", "opencode=openrouter/x/y:free:Nemotron",
              "--review", "codex", env=env)
    check("roster plan: :free stays in the model and the real family is split off",
          got.returncode == 0 and "model=openrouter/x/y:free" in got.stdout
          and "family=Nemotron" in got.stdout, got.stdout + got.stderr)

    codex_impl = tmp / "codex-impl-effort.json"
    _write_doc(codex_impl, _roster_doc(
        {"agy": {"model": "gemini-3.8-flash-medium", "effort_in": "model_name",
                 "family": "Gemini"}},
        implement={"codex": {"model": "gpt-5.6-terra", "effort": "high"}},
    ))
    env = _roster_env(home, DEV_LEAD_ROSTER=codex_impl)
    got = run(roster, "plan", "--round", "r1", "--implement", "codex",
              "--review", "agy", env=env)
    check("roster plan: codex implement passes --effort",
          got.returncode == 0
          and args_of(got.stdout, "implement", "codex")
          == "--model gpt-5.6-terra --effort high",
          got.stdout + got.stderr)

    # anti-drift: roster's accept/refuse of an effort flag equals leg-cmd.sh.
    # leg-cmd.sh checks effort before required values, so a missing --base is
    # not an effort refusal. Classify by the effort-mechanism message.
    spec = importlib.util.spec_from_file_location("roster_mod", roster)
    roster_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roster_mod)
    launch = json.loads((SCRIPTS.parent / "data" / "launch.json").read_text(encoding="utf-8"))
    markers = ("MODEL NAME", "no effort control", "needs --effort",
               "leg-cmd: the suite passes no effort", "unknown effort mechanism")
    leg = SCRIPTS / "leg-cmd.sh"
    for adapter, adapter_spec in launch.items():
        if adapter.startswith("_"):
            continue
        for role in adapter_spec["role"]:
            for effort in ("", "medium"):
                argv = [str(leg), adapter, role, "--model", "opencode/x"]
                if effort:
                    argv += ["--effort", effort]
                ran = run(*argv)
                leg_refuses = any(marker in (ran.stderr or "") for marker in markers)
                roster_refuses = roster_mod.effort_flag_refusal(adapter, role, effort) is not None
                check("roster effort %s %s effort=%s matches leg-cmd"
                      % (adapter, role, "set" if effort else "absent"),
                      leg_refuses == roster_refuses,
                      "leg=%s stderr=%r roster=%s"
                      % (leg_refuses, ran.stderr, roster_refuses))

    # anti-drift, the other direction: plan's args plus the template's other
    # required values must not die on a missing {EFFORT}. leg-cmd.sh requires
    # every placeholder, not only the effort mechanism.
    families_doc = json.loads(
        (SCRIPTS.parent / "data" / "families.json").read_text(encoding="utf-8"))

    def concrete_leg(adapter, role):
        serves = families_doc["adapters"][adapter]["serves"]
        spec = launch[adapter]
        mech = spec["effort"]["mechanism"]
        applies = role == spec["effort"].get("applies_to_role", role)
        argv = spec["role"][role]["argv"]
        built = {"model": "opencode/x" if adapter == "opencode" else "m"}
        if len(serves) != 1:
            built["family"] = serves[0]
        if any("{EFFORT}" in tok for tok in argv):
            built["effort"] = "high"
        elif mech == "config_only" and applies:
            built["effort"] = "medium"
        return built, serves[0]

    concrete = {}
    for adapter, adapter_spec in launch.items():
        if adapter.startswith("_"):
            continue
        concrete[adapter] = {
            role: concrete_leg(adapter, role) for role in adapter_spec["role"]
        }
    for adapter, roles in concrete.items():
        for role in roles:
            family = roles[role][1]
            partner = next(
                other for other, other_roles in concrete.items()
                if other != adapter and other_roles["implement"][1] != family
            )
            if role == "implement":
                doc = _roster_doc(
                    {partner: concrete[partner]["review"][0]},
                    implement={adapter: roles["implement"][0]},
                )
                impl_name, rev_name = adapter, partner
            else:
                doc = _roster_doc(
                    {adapter: roles["review"][0]},
                    implement={partner: concrete[partner]["implement"][0]},
                )
                impl_name, rev_name = partner, adapter
            roster_file = tmp / ("plan-effort-%s-%s.json" % (adapter, role))
            _write_doc(roster_file, doc)
            planned = run(roster, "plan", "--round", "r1",
                          "--implement", impl_name, "--review", rev_name,
                          env=_roster_env(home, DEV_LEAD_ROSTER=roster_file))
            plan_args = args_of(planned.stdout, role, adapter)
            check("roster plan emits %s %s" % (adapter, role),
                  planned.returncode == 0 and plan_args.startswith("--model "),
                  planned.stdout + planned.stderr)
            template = " ".join(launch[adapter]["role"][role]["argv"])
            extra = []
            if "{TARGET}" in template:
                extra += ["--target", str(tmp / "wt")]
            if "{BASE}" in template:
                extra += ["--base", "HEAD"]
            if "{PROMPT_FILE}" in template:
                extra += ["--prompt-file", str(tmp / "brief.md")]
            ran = run(leg, adapter, role, "--model", "M", *plan_args.split(), *extra)
            blob = (ran.stdout or "") + (ran.stderr or "")
            missing_effort = (
                "missing required value(s):" in blob
                and "effort" in blob.split("missing required value(s):", 1)[1].split(")", 1)[0]
            )
            check("roster plan args satisfy {EFFORT} for %s %s" % (adapter, role),
                  not missing_effort and "needs --effort" not in blob, blob)

    # set through a symlink writes the target and leaves the link a link.
    real = tmp / "real-roster.json"
    _write_doc(real, {
        "_comment": "keep me",
        "version": 1,
        "rounds": {"r1": {"implement": {}, "review": {"codex": _codex_leg()}},
                   "fix": {"inherit": "r1"}},
    })
    os.chmod(real, 0o640)
    link = tmp / "link-roster.json"
    link.symlink_to(real)
    env = _roster_env(home, DEV_LEAD_ROSTER=link)
    got = run(roster, "set", "r1", "review", "agy",
              "--model", "gemini-3.8-flash-medium", "--family", "Gemini",
              "--why", "因为 café", env=env)
    check("roster set through symlink exits 0", got.returncode == 0,
          got.stdout + got.stderr)
    check("roster set leaves the path a symlink", link.is_symlink(),
          "mode=%o" % link.lstat().st_mode)
    check("roster set changes the target",
          "gemini-3.8-flash-medium" in real.read_text(encoding="utf-8"))
    written = json.loads(real.read_text(encoding="utf-8"))
    check("roster set preserves _comment and its position",
          list(written)[0] == "_comment" and written["_comment"] == "keep me")
    agy = written["rounds"]["r1"]["review"]["agy"]
    check("roster set records why and set_on",
          agy["why"] == "因为 café" and agy["set_on"] == date.today().isoformat(),
          agy)
    check("roster set does not escape non-ascii",
          "因为 café" in real.read_text(encoding="utf-8")
          and "\\u" not in real.read_text(encoding="utf-8"))
    check("roster set preserves the file mode",
          real.stat().st_mode & 0o777 == 0o640, oct(real.stat().st_mode))
    blob = real.read_bytes()
    got = run(roster, "set", "r1", "review", "cursor",
              "--model", "cursor-grok-4.6-medium", "--effort", "high",
              "--family", "Grok", "--why", "should not land", env=env)
    check("roster set of an invalid roster exits 1", got.returncode == 1,
          got.stdout + got.stderr)
    check("roster set of an invalid roster leaves the file byte-identical",
          real.read_bytes() == blob)
    check("roster set failure also leaves the symlink a symlink", link.is_symlink())

    missing = tmp / "does-not-exist.json"
    env = _roster_env(home, DEV_LEAD_ROSTER=missing)
    got = run(roster, "set", "r1", "review", "agy",
              "--model", "gemini-3.8-flash-medium", "--effort", "high",
              "--family", "Gemini", "--why", "no", env=env)
    check("roster set that fails validation creates nothing",
          got.returncode == 1 and not missing.exists(), got.stdout + got.stderr)

    created = tmp / "brand-new.json"
    env = _roster_env(home, DEV_LEAD_ROSTER=created)
    got = run(roster, "set", "r1", "review", "codex",
              "--model", "gpt-5.6-terra", "--effort", "medium", "--family", "GPT",
              "--why", "first leg", env=env)
    check("roster set creates a missing file", got.returncode == 0, got.stdout + got.stderr)
    made = json.loads(created.read_text(encoding="utf-8"))
    check("roster set skeleton has version 1, r1, and fix inherit",
          made["version"] == 1 and made["rounds"]["fix"]["inherit"] == "r1"
          and made["rounds"]["r1"]["review"]["codex"]["model"] == "gpt-5.6-terra")
    check("roster set file ends with a newline",
          created.read_bytes().endswith(b"\n"))

    inherit_src = tmp / "inherit-set.json"
    base_doc = _roster_doc({
        "codex": _codex_leg(),
        "cursor": {"model": "cursor-grok-4.6-medium", "effort_in": "model_name",
                   "family": "Grok"},
    })
    r1_before = json.loads(json.dumps(base_doc["rounds"]["r1"]))
    _write_doc(inherit_src, base_doc)
    got = run(roster, "set", "fix", "implement", "agy",
              "--model", "gemini-3.8-flash-medium", "--family", "Gemini",
              "--why", "x", env=_roster_env(home, DEV_LEAD_ROSTER=inherit_src))
    check("roster set fix on an inheriting round exits 0",
          got.returncode == 0, got.stdout + got.stderr)
    written = json.loads(inherit_src.read_text(encoding="utf-8"))
    agy_leg = written["rounds"]["fix"]["implement"]["agy"]
    check("roster set fix keeps r1's review and writes the new implement leg",
          written["rounds"]["fix"]["review"] == written["rounds"]["r1"]["review"]
          and written["rounds"]["r1"] == r1_before
          and agy_leg["model"] == "gemini-3.8-flash-medium"
          and agy_leg["family"] == "Gemini" and agy_leg["why"] == "x",
          written["rounds"])

    bad_inherit = tmp / "bad-inherit-set.json"
    _write_doc(bad_inherit, _roster_doc({"codex": _codex_leg()}))
    before_bytes = bad_inherit.read_bytes()
    got = run(roster, "set", "fix", "--inherit", "r2",
              env=_roster_env(home, DEV_LEAD_ROSTER=bad_inherit))
    text = got.stdout + got.stderr
    check("roster set fix --inherit r2 exits 1",
          got.returncode == 1 and "bad inherit target 'r2'" in text, text)
    check("roster set fix --inherit r2 leaves the file byte-identical",
          bad_inherit.read_bytes() == before_bytes)

    commented = tmp / "comment-leg.json"
    _write_doc(commented, _roster_doc({
        "codex": {**_codex_leg(), "_comment": "keep"},
    }))
    got = run(roster, "set", "r1", "review", "codex",
              "--model", "gpt-5.6-terra", "--effort", "high", "--family", "GPT",
              "--why", "updated",
              env=_roster_env(home, DEV_LEAD_ROSTER=commented))
    check("roster set replacing a leg exits 0", got.returncode == 0,
          got.stdout + got.stderr)
    replaced = json.loads(commented.read_text(encoding="utf-8"))["rounds"]["r1"]["review"]["codex"]
    check("roster set keeps the leg's _comment",
          replaced.get("_comment") == "keep" and replaced["why"] == "updated"
          and replaced["effort"] == "high", replaced)

    show_env = _roster_env(home, DEV_LEAD_ROSTER=live)
    got = run(roster, "show", "--round", "fix", env=show_env)
    check("roster show: inherited fix round marks each leg",
          got.returncode == 0 and "(inherited from r1)" in got.stdout
          and got.stdout.count("(inherited from r1)") >= 2, got.stdout)
    gone = tmp / "missing-show.json"
    got = run(roster, "show", env=_roster_env(home, DEV_LEAD_ROSTER=gone))
    check("roster show: missing file exits 0 and names set",
          got.returncode == 0 and str(gone) in got.stdout and "set" in got.stdout,
          got.stdout)
    got = run(roster, "check", env=_roster_env(home, DEV_LEAD_ROSTER=gone))
    check("roster check: missing file exits 1",
          got.returncode == 1 and str(gone) in (got.stdout + got.stderr),
          got.stdout + got.stderr)

    # Resolution order: DEV_LEAD_ROSTER, then CLAUDE_PLUGIN_DATA, then ~/.claude/...
    from_env = tmp / "from-env.json"
    plugin_dir = tmp / "dev-lead-some-marketplace"
    plugin_dir.mkdir()
    env = _roster_env(home, DEV_LEAD_ROSTER=from_env, CLAUDE_PLUGIN_DATA=plugin_dir)
    got = run(roster, "path", env=env)
    check("roster path: DEV_LEAD_ROSTER wins",
          got.returncode == 0 and str(from_env) in got.stdout
          and "dev-lead-some-marketplace" not in got.stdout
          and "dev-lead-dev-lead" not in got.stdout, got.stdout)
    env = _roster_env(home, CLAUDE_PLUGIN_DATA=plugin_dir)
    got = run(roster, "path", env=env)
    check("roster path: CLAUDE_PLUGIN_DATA is next",
          got.returncode == 0 and str(plugin_dir / "roster.json") in got.stdout
          and "dev-lead-dev-lead" not in got.stdout, got.stdout)
    env = _roster_env(home)
    got = run(roster, "path", env=env)
    fallback = home / ".claude" / "plugins" / "data" / "dev-lead-dev-lead" / "roster.json"
    check("roster path: home fallback is last",
          got.returncode == 0 and str(fallback) in got.stdout, got.stdout)
    # A lead's shell can carry ANOTHER plugin's CLAUDE_PLUGIN_DATA (measured
    # 2026-09-22: codex's). It must not be read as dev-lead's.
    other = tmp / "codex-openai-codex"
    other.mkdir()
    got = run(roster, "path", env=_roster_env(home, CLAUDE_PLUGIN_DATA=other))
    check("roster path: another plugin's CLAUDE_PLUGIN_DATA is ignored",
          got.returncode == 0 and str(fallback) in got.stdout
          and "codex-openai-codex" not in got.stdout, got.stdout)


def test_triage(tmp):
    """Rule/config, patch-set delta, patrol, move, and scope contracts."""
    import hashlib

    triage = SCRIPTS / "triage.py"
    check("triage.py exists", triage.is_file())
    check("triage.py is executable", os.access(triage, os.X_OK))
    check("triage.py shebang",
          triage.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n"))

    repo = tmp / "example-sdk"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")

    def write(name, text):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(message):
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", message)
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    write("src/value.txt", "base\n")
    write("unrelated.txt", "base\n")
    write("links/ok.md", "ok\n")
    base = commit("base")

    roster_file = tmp / "roster.json"
    _write_doc(roster_file, _live_roster())
    config = {
        "version": 1,
        "me": ["alice", "alice@example.com"],
        "gerrit": {"ssh": "alice@gerrit.example.com", "port": 29418},
        "clones": {"example-sdk": str(repo)},
        "gateways": {"example-sdk": "alpha", "example-fw": "beta"},
        "lens_classes": {"consistency": "mechanical", "falsifiability": "judgment"},
        "lenses": [{"glob": "docs/spec/*.md", "lenses": ["consistency"], "why": "spec"}],
        "delta_triggers": [
            {"glob": "docs/spec/*.md", "added_regex": "Status.*Verified", "risk": "HIGH",
             "lenses": ["falsifiability"], "flag": "status flip", "why": "status"},
            {"glob": "secure/**", "path_only": True, "risk": "HIGH",
             "lenses": ["falsifiability"], "why": "secure path"},
        ],
        "risk_default": "MEDIUM",
        "move_check": {"naming": []},
        "small_delta_lines": 3,
        "order": [{"owner": "bob", "gateway": "alpha", "rank": 0},
                  {"gateway": "alpha", "rank": 1}, {"gateway": "beta", "rank": 2}],
    }
    config_file = tmp / "triage.json"
    _write_doc(config_file, config)
    env = _roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=config_file)

    def checked(path):
        return run(triage, "check", "--file", str(path), env=env)

    def expect_bad(name, mutate, needle):
        document = json.loads(json.dumps(config))
        mutate(document)
        path = tmp / (name + ".json")
        _write_doc(path, document)
        got = checked(path)
        text = got.stdout + got.stderr
        check("triage check: %s fails" % name, got.returncode == 1, text)
        check("triage check: %s names the problem" % name, needle in text, text)

    template = SCRIPTS.parent / "templates" / "triage.example.json"
    got = checked(template)
    check("triage check: template passes", got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)
    got = checked(config_file)
    check("triage check: test config passes", got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)
    expect_bad("unknown-key", lambda doc: doc.update({"typo": 1}), "unknown key")
    expect_bad("unknown-lens", lambda doc: doc["lenses"][0].update({"lenses": ["missing"]}), "unknown lens")
    expect_bad("unknown-class", lambda doc: doc["lens_classes"].update({"consistency": "not-a-class"}), "by_lens")
    expect_bad("bad-regex", lambda doc: doc["delta_triggers"][0].update({"added_regex": "["}), "invalid regex")
    expect_bad("bad-risk", lambda doc: doc.update({"risk_default": "SEVERE"}), "LOW, MEDIUM, or HIGH")
    expect_bad("empty-trigger", lambda doc: doc["delta_triggers"][0].pop("added_regex"), "needs added_regex")

    init_file = tmp / "initial" / "triage.json"
    init_env = _roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=init_file)
    got = run(triage, "init", env=init_env)
    check("triage init: writes the template", got.returncode == 0 and init_file.read_bytes() == template.read_bytes(), got.stdout + got.stderr)
    got = run(triage, "init", env=init_env)
    check("triage init: refuses a second write without --force", got.returncode == 1 and "already exists" in got.stdout, got.stdout)
    got = run(triage, "init", "--force", env=init_env)
    check("triage init: --force replaces the rules file", got.returncode == 0 and init_file.read_bytes() == template.read_bytes(), got.stdout + got.stderr)
    missing_env = _roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=tmp / "missing.json")
    got = run(triage, "scope", "--files", "docs/spec/new.md", env=missing_env)
    check("triage config: an explicit missing DEV_LEAD_TRIAGE is an input error",
          got.returncode == 2 and "file not found" in got.stderr, got.stdout + got.stderr)

    def patch(number, revision, parent, *, kind="REWORK", approvals=None, created=10):
        return {"number": number, "revision": revision, "parents": [parent], "kind": kind,
                "createdOn": created, "uploader": {"username": "alice"},
                "approvals": approvals or [], "files": [{"file": "src/value.txt", "type": "MODIFIED", "insertions": 1, "deletions": 1}]}

    def approval(value, granted=20, who="alice"):
        return {"type": "Code-Review", "value": value,
                "by": {"username": who, "email": who + "@example.com"}, "grantedOn": granted}

    def query(number, records, *, owner="bob", wip=False, comments=None, topic="series", depends=None, status="NEW", extra=None, extra_rows=None):
        document = {"number": number, "project": "example-sdk",
                    "owner": {"username": owner, "email": owner + "@example.com"},
                    "wip": wip, "topic": topic, "status": status, "patchSets": records,
                    "currentPatchSet": records[-1], "comments": comments or [], "dependsOn": depends or []}
        if extra:
            document.update(extra)
        path = tmp / ("query-%s-%s.json" % (number, records[-1]["number"]))
        rows = [document] + (extra_rows or []) + [{"type": "stats", "rowCount": 1}]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return path

    def changed(number, records, *, args=(), test_env=env, **kwargs):
        got = run(triage, "change", str(number), "--query-json", query(number, records, **kwargs), *args, env=test_env)
        text = got.stdout + got.stderr
        check("triage change: %s exits 0" % number, got.returncode == 0, text)
        return json.loads(got.stdout) if got.returncode == 0 else {}

    # A current vote is skippable until another reviewer sends a later message
    # that identifies the reviewer; WIP has its own opt-in gate.
    write("src/value.txt", "first\n")
    ps1 = commit("ps1")
    current_vote = patch(1, ps1, base, approvals=[approval("+1")])
    result = changed(101, [current_vote], depends=[{"number": 9, "status": "NEW"}])
    check("triage change: current vote skips", result.get("skip") is not None and result.get("legs") == "none", result)
    result = changed(102, [current_vote], comments=[{"timestamp": 30, "reviewer": {"username": "bob"}, "message": "alice please revisit"}])
    check("triage change: a later message naming me unskips", result.get("skip") is None, result)
    result = changed(103, [current_vote], wip=True)
    check("triage change: WIP skips without opt-in", result.get("skip", "").startswith("WIP"), result)
    got = run(triage, "change", "103", "--query-json",
              query(103, [patch(1, ps1, base)], wip=True), "--include-wip", env=env)
    included = json.loads(got.stdout) if got.returncode == 0 else {}
    check("triage change: --include-wip does not skip", got.returncode == 0 and included.get("skip") is None, got.stdout + got.stderr)

    # Two inputs that left as tracebacks until 0.6.48 (the README promises
    # exit 2 and a message for bad input): a query line that is valid JSON
    # but not an object, and a patch set whose number is not digits.
    listy = tmp / "query-listy.json"
    listy.write_text('["not", "an", "object"]\n' + json.dumps({"type": "stats", "rowCount": 1}) + "\n")
    got = run(triage, "change", "1", "--query-json", str(listy), env=env)
    check("triage change: a non-object query line is an input error, not a traceback",
          got.returncode == 2 and "must be a JSON object" in got.stderr and "Traceback" not in got.stderr,
          got.stdout + got.stderr[-300:])
    odd = patch(1, ps1, base)
    odd["number"] = "one"
    got = run(triage, "change", "105", "--query-json", query(105, [odd]), env=env)
    check("triage change: a non-numeric CURRENT patch set is an input error, not a traceback",
          got.returncode == 2 and "current patch set number must be digits" in got.stderr
          and "Traceback" not in got.stderr, got.stdout + got.stderr[-300:])
    # ...and a vote cast on such a set (Gerrit lists it, the span ignores it)
    # is reported as cast on it, not as a ValueError.
    voted_odd = patch(1, ps1, base, approvals=[approval("+1")])
    voted_odd["number"] = "draft"
    got = run(triage, "change", "106", "--query-json", query(106, [voted_odd, patch(2, ps3, ps1)]), env=env)
    check("triage change: a vote on a non-numeric patch set is not a traceback",
          got.returncode == 0 and "Traceback" not in got.stderr, got.stdout[:200] + got.stderr[-300:])

    # A carry-over does not produce new lenses or risk depth.
    write("src/value.txt", "rebased\n")
    ps2 = commit("ps2")
    result = changed(104, [patch(1, ps1, base, approvals=[approval("+1")]),
                           patch(2, ps2, ps1, kind="TRIVIAL_REBASE")])
    check("triage change: carry-over inherits and has no legs",
          result.get("ps_kind") == "carry-over" and result.get("risk_floor") == "inherit" and result.get("legs") == "none", result)

    # The kind spans every patch set since my vote (a peer's round, 2026-09-24):
    # vote on PS1, a REWORK PS2, a TRIVIAL_REBASE PS3 is not a carry-over.
    write("src/value.txt", "reworked\n")
    ps3 = commit("ps3")
    write("src/value.txt", "reworked and rebased\n")
    ps4 = commit("ps4")
    result = changed(105, [patch(1, ps1, base, approvals=[approval("+1")]),
                           patch(2, ps3, ps1, kind="REWORK"),
                           patch(3, ps4, ps3, kind="TRIVIAL_REBASE")])
    check("triage change: a REWORK between my vote and a trivial current patch set is rework",
          result.get("ps_kind") == "rework" and result.get("legs") != "none", result)
    check("triage change: ...and says which patch set carried the rework",
          any("ps-kind-span" in str(rule) and "PS 2" in str(rule) for rule in result.get("fired_rules") or []),
          result.get("fired_rules"))
    result = changed(106, [patch(1, ps1, base, approvals=[approval("+1")]),
                           patch(2, ps3, ps1, kind="TRIVIAL_REBASE"),
                           patch(3, ps4, ps3, kind="NO_CODE_CHANGE")])
    check("triage change: only trivial patch sets since my vote is still a carry-over",
          result.get("ps_kind") == "carry-over" and result.get("legs") == "none", result)

    # A hold -1 that a new patch set dropped is flagged whatever the delta.
    held = [patch(1, ps1, base, approvals=[approval("-1")]), patch(2, ps3, ps1, kind="REWORK")]
    result = changed(107, held, extra={"submitRecords": [{"status": "OK"}]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: my -1 missing from the current patch set is flagged, with SUBMITTABLE",
          len(hold_flags) == 1 and "PS 1" in hold_flags[0] and "SUBMITTABLE" in hold_flags[0], result.get("flags"))
    check("triage change: ...and recorded as a fired rule",
          any("hold-dropped" in str(rule) for rule in result.get("fired_rules") or []), result.get("fired_rules"))
    result = changed(108, held, extra={"submitRecords": [{"status": "NOT_READY"}, {"status": "RULE_ERROR"}]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: a dropped hold on a change that is not submittable is flagged without SUBMITTABLE",
          len(hold_flags) == 1 and "SUBMITTABLE" not in hold_flags[0], result.get("flags"))
    result = changed(109, [patch(1, ps1, base, approvals=[approval("-1")]),
                           patch(2, ps3, ps1, kind="REWORK", approvals=[approval("-1", granted=20)])],
                     extra={"submitRecords": [{"status": "OK"}]})
    check("triage change: a -1 the current patch set still carries is not a dropped hold",
          not [f for f in result.get("flags") or [] if f.startswith("hold dropped")], result.get("flags"))
    result = changed(110, [patch(1, ps1, base, approvals=[approval("+1")]), patch(2, ps3, ps1, kind="REWORK")],
                     extra={"submitRecords": [{"status": "OK"}]})
    check("triage change: a positive last vote is never a hold",
          not [f for f in result.get("flags") or [] if f.startswith("hold dropped")], result.get("flags"))
    result = changed(111, held, args=("--as-of-ps", "2"), extra={"submitRecords": [{"status": "OK"}]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: a replay flags the dropped hold but never claims SUBMITTABLE (records are today's)",
          len(hold_flags) == 1 and "SUBMITTABLE" not in hold_flags[0], result.get("flags"))
    # Gerrit submits only when EVERY record is OK or FORCED: one OK beside a
    # NOT_READY record is not submittable.
    result = changed(112, held, extra={"submitRecords": [{"status": "OK"}, {"status": "NOT_READY"}]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: one OK record beside a NOT_READY one is not SUBMITTABLE",
          len(hold_flags) == 1 and "SUBMITTABLE" not in hold_flags[0], result.get("flags"))
    result = changed(113, held, extra={"submitRecords": [{"status": "OK"}, {"status": "FORCED"}]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: OK and FORCED records together are SUBMITTABLE",
          len(hold_flags) == 1 and "SUBMITTABLE" in hold_flags[0], result.get("flags"))
    result = changed(116, held)
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: no submit records at all is not SUBMITTABLE",
          len(hold_flags) == 1 and "SUBMITTABLE" not in hold_flags[0], result.get("flags"))
    result = changed(117, held, extra={"submitRecords": [{"status": "OK"}, None]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: a submit record that is not an object is not an OK one",
          len(hold_flags) == 1 and "SUBMITTABLE" not in hold_flags[0], result.get("flags"))
    result = changed(119, held, extra={"submitRecords": True})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: a submitRecords field that is not a list is not SUBMITTABLE, and does not raise",
          len(hold_flags) == 1 and "SUBMITTABLE" not in hold_flags[0], result.get("flags"))
    # A vote Gerrit copied forward keeps its grantedOn; the tie goes to the patch
    # set it was cast on, so a sticky -2 carried across a REWORK is not a vote on
    # the current content.
    result = changed(118, [patch(1, ps1, base, approvals=[approval("-2")]),
                           patch(2, ps3, ps1, kind="REWORK", approvals=[approval("-2")])])
    check("triage change: a copied vote counts from the patch set it was cast on",
          result.get("skip") is None and result.get("ps_kind") == "rework"
          and (result.get("my_last_vote") or {}).get("ps") == 1, result)
    # A trivial rebase can drop a -1 too (the label's copy rule decides); it is
    # flagged, and the legs still follow the content.
    result = changed(114, [patch(1, ps1, base, approvals=[approval("-1")]),
                           patch(2, ps2, ps1, kind="TRIVIAL_REBASE")],
                     extra={"submitRecords": [{"status": "OK"}]})
    hold_flags = [f for f in result.get("flags") or [] if f.startswith("hold dropped")]
    check("triage change: a -1 a trivial rebase dropped is flagged, and a carry-over still has no legs",
          len(hold_flags) == 1 and "SUBMITTABLE" in hold_flags[0]
          and result.get("ps_kind") == "carry-over" and result.get("legs") == "none", result)
    # The span is read in patch set order whatever order Gerrit lists them in,
    # and a patch set whose number is not a number is not in it.
    result = changed(115, [patch(1, ps1, base, approvals=[approval("+1")]),
                           patch(3, ps3, ps1, kind="REWORK"),
                           patch("draft", ps3, ps1, kind="REWORK"),
                           patch(2, ps3, ps1, kind="REWORK"),
                           patch(4, ps4, ps3, kind="TRIVIAL_REBASE")])
    check("triage change: the span names its rework patch sets in number order, skipping a non-numeric one",
          any("ps-kind-span" in str(rule) and "PS 2, 3 since" in str(rule) for rule in result.get("fired_rules") or []),
          result.get("fired_rules"))

    # PS2 is rebased onto an unrelated parent, then carries a real edit. Its
    # own patch exposes only the real edit, not the parent file.
    git(repo, "checkout", "-q", "-B", "rebase-parent", base)
    write("unrelated.txt", "parent-only\n")
    parent2 = commit("parent change")
    write("src/value.txt", "real edit\n")
    rebased = commit("rebased ps")
    result = changed(105, [patch(1, ps1, base, approvals=[approval("+1")]), patch(2, rebased, parent2)])
    check("triage change: rebase excludes parent-only files", result.get("delta_files") == ["src/value.txt"], result)

    # A pure move may change only the count of ../ in relative links. The
    # move checks separately catch URL, link-resolution, and naming failures.
    git(repo, "checkout", "-q", "-B", "moves", base)
    move_text = "# move\n[ok](../../links/ok.md)\nhttps://same.example/item\n" + ("same\n" * 12)
    write("docs/move/old.md", move_text)
    move_old = commit("move old")
    (repo / "docs/move/deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "docs/move/old.md", "docs/move/deep/new.md")
    write("docs/move/deep/new.md", move_text.replace("../../links", "../../../links"))
    move_new = commit("move new")
    result = changed(106, [patch(1, move_old, base, approvals=[approval("+1")]), patch(2, move_new, move_old)])
    check("triage change: normalised relative-link move passes", result.get("ps_kind") == "move-only" and not result.get("flags"), result)
    # Gerrit patch sets amend each other on the same base: the move is "add A"
    # in PS1's own diff and "add B" in PS2's, never a rename in either. Only a
    # tree-to-tree diff pairs them (found replaying real move patch sets).
    git(repo, "checkout", "-q", "-B", "amend-move", base)
    write("docs/move/amend.md", move_text)
    amend_old = commit("amend move old")
    git(repo, "checkout", "-q", "-B", "amend-move-2", base)
    (repo / "docs/move/deep").mkdir(parents=True, exist_ok=True)
    write("docs/move/deep/amend.md", move_text.replace("../../links", "../../../links"))
    amend_new = commit("amend move new")
    result = changed(136, [patch(1, amend_old, base, approvals=[approval("+1")]), patch(2, amend_new, base)])
    check("triage change: a move between amended patch sets is move-only",
          result.get("ps_kind") == "move-only" and result.get("legs") == "none", result)
    # The same amended move inside a rework (another file changes too, and one
    # URL in the moved file): the move is still paired, so only its real edit
    # counts toward small_delta_lines and the move checks still run.
    long_text = move_text + ("more\n" * 40)
    git(repo, "checkout", "-q", "-B", "amend-mix", base)
    write("docs/move/mix.md", long_text)
    mix_old = commit("amend mix old")
    git(repo, "checkout", "-q", "-B", "amend-mix-2", base)
    (repo / "docs/move/deep").mkdir(parents=True, exist_ok=True)
    write("docs/move/deep/mix.md", long_text.replace("../../links", "../../../links")
          .replace("https://same.example/item", "https://same.example/other"))
    write("src/mix.txt", "other file\n")
    mix_new = commit("amend mix new")
    # Paired, the delta is 5 lines (link depth -/+, URL -/+, one new line);
    # unpaired it is the whole file twice.
    old_limit = config["small_delta_lines"]
    config["small_delta_lines"] = 5
    _write_doc(config_file, config)
    result = changed(156, [patch(1, mix_old, base, approvals=[approval("+1")]), patch(2, mix_new, base)])
    config["small_delta_lines"] = old_limit
    _write_doc(config_file, config)
    check("triage change: a move inside a rework counts only its edited lines and is move-checked",
          result.get("ps_kind") == "rework" and result.get("legs") == "own-read"
          and any("absolute URLs changed" in flag for flag in result.get("flags", [])), result)
    # A link-only move next to another edited file is not move-only: the other
    # file still needs review.
    git(repo, "checkout", "-q", "-B", "amend-side", base)
    write("docs/move/side.md", move_text)
    side_old = commit("amend side old")
    git(repo, "checkout", "-q", "-B", "amend-side-2", base)
    (repo / "docs/move/deep").mkdir(parents=True, exist_ok=True)
    write("docs/move/deep/side.md", move_text.replace("../../links", "../../../links"))
    write("src/side.txt", "side edit\n")
    side_new = commit("amend side new")
    result = changed(157, [patch(1, side_old, base, approvals=[approval("+1")]), patch(2, side_new, base)])
    check("triage change: a link-only move beside another edit is rework", result.get("ps_kind") == "rework", result)
    # A moved file's removed lines belong to its OLD path: a trigger keyword
    # removed while the file leaves the trigger's glob still fires.
    spec_text = "# spec\nStatus: Verified\n" + ("keep\n" * 12)
    git(repo, "checkout", "-q", "-B", "amend-out", base)
    write("docs/spec/out.md", spec_text)
    out_old = commit("amend out old")
    git(repo, "checkout", "-q", "-B", "amend-out-2", base)
    write("docs/other/out.md", spec_text.replace("Status: Verified\n", ""))
    out_new = commit("amend out new")
    result = changed(158, [patch(1, out_old, base, approvals=[approval("+1")]), patch(2, out_new, base)])
    check("triage change: a keyword removed by a move out of the glob fires at the old path",
          result.get("risk_floor") == "HIGH", result)

    def bad_move(number, replace, naming=None):
        git(repo, "checkout", "-q", "-B", "move-%s" % number, move_old)
        (repo / "docs/move/deep").mkdir(parents=True, exist_ok=True)
        git(repo, "mv", "docs/move/old.md", "docs/move/deep/new.md")
        write("docs/move/deep/new.md", move_text.replace("../../links", "../../../links").replace(*replace))
        revision = commit("bad move")
        old_config = config["move_check"]
        if naming is not None:
            config["move_check"] = {"naming": naming}
            _write_doc(config_file, config)
        output = changed(number, [patch(1, move_old, base, approvals=[approval("+1")]), patch(2, revision, move_old)])
        config["move_check"] = old_config
        _write_doc(config_file, config)
        return output

    result = bad_move(107, ("https://same.example/item", "https://other.example/item"))
    check("triage change: move URL failure names added and removed URLs",
          any("absolute URLs changed (added: https://other.example/item; removed: https://same.example/item)" in flag
              for flag in result.get("flags", [])), result)
    result = bad_move(108, ("../../../links/ok.md", "../../../links/missing.md"))
    check("triage change: move relative-link failure is flagged", any("does not resolve" in flag for flag in result.get("flags", [])), result)
    result = bad_move(109, ("docs/move/deep/new.md", "docs/move/deep/new.md"),
                      [{"glob": "docs/move/**", "regex": "^docs/move/[a-z]+-contract\\.md$"}])
    check("triage change: move naming failure is flagged", any("naming regex" in flag for flag in result.get("flags", [])), result)

    # Trigger text is read from the patch delta, never from unchanged lines.
    git(repo, "checkout", "-q", "-B", "trigger", base)
    write("docs/spec/one.md", "Status Verified\nunchanged\n")
    trigger_old = commit("trigger old")
    write("docs/spec/one.md", "Status Verified\nreal ordinary edit\n")
    trigger_plain = commit("trigger plain")
    result = changed(110, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, trigger_plain, trigger_old)])
    check("triage change: unchanged trigger text does not fire", result.get("risk_floor") == "MEDIUM" and "status flip" not in result.get("flags", []), result)
    write("docs/spec/one.md", "Status Verified\nStatus Verified now\n")
    trigger_high = commit("trigger high")
    result = changed(111, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, trigger_high, trigger_old)])
    check("triage change: added trigger text raises risk and adds lens",
          result.get("risk_floor") == "HIGH" and "status flip" in result.get("flags", [])
          and any(lens["name"] == "falsifiability" for lens in result.get("lenses", [])), result)

    # A bounded rework is own-read; larger content returns the roster with the
    # judgment opencode branch. An owner needs that roster after a negative vote.
    git(repo, "checkout", "-q", "-B", "legs", trigger_old)
    write("docs/spec/one.md", "Status Verified\nsmall\n")
    small = commit("small")
    result = changed(112, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, small, trigger_old)])
    check("triage change: a small post-vote delta is own-read", result.get("legs") == "own-read", result)
    write("docs/spec/one.md", "Status Verified\n" + ("Status Verified large\n" * 5))
    large = commit("large")
    result = changed(113, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)])
    check("triage change: a large delta uses judgment roster legs",
          result.get("legs") == "roster" and any(leg["adapter"] == "opencode" for leg in result.get("review_legs", [])), result)
    owner_result = changed(114, [patch(1, large, small, approvals=[approval("-1", 30, "bob")], created=10)], owner="alice")
    check("triage change: owner fix round uses roster", owner_result.get("legs") == "roster" and any(rule["rule"] == "owner-fix-round" for rule in owner_result.get("fired_rules", [])), owner_result)

    result = changed(115, [patch(1, small, trigger_old)], depends=[{"number": 7, "status": "NEW"}, {"number": 8, "status": "MERGED"}])
    check("triage change: order, ancestors, and topic are explicit",
          result.get("order") == 0 and result.get("unmerged_ancestors") == [7]
          and result.get("topic_members", {}).get("atomic") is False, result)
    expected_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
    check("triage change: rules carry config sha256", result.get("rules", {}).get("sha256") == expected_hash, result)

    got = run(triage, "scope", "--files", "secure/credentials.py", env=env)
    scoped = json.loads(got.stdout) if got.returncode == 0 else {}
    check("triage scope: path-only HIGH suggests lead implementation",
          got.returncode == 0 and scoped.get("risk_floor") == "HIGH"
          and "lead implements directly" in scoped.get("suggestion", {}).get("implementer", "")
          and scoped.get("note") == "suggestion only; the lead decides, and rules only raise", got.stdout + got.stderr)

    # Only `change` reads Gerrit: a rules file for `scope` alone may leave out
    # gerrit, clones and gateways, and `change` then says which are missing.
    scope_only = {key: value for key, value in config.items() if key not in ("gerrit", "clones", "gateways")}
    scope_file = tmp / "triage-scope-only.json"
    _write_doc(scope_file, scope_only)
    got = checked(scope_file)
    check("triage check: a rules file without gerrit, clones and gateways passes",
          got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)
    scope_env = _roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=scope_file)
    got = run(triage, "scope", "--files", "secure/credentials.py", env=scope_env)
    scoped = json.loads(got.stdout) if got.returncode == 0 else {}
    check("triage scope: runs on a rules file without the Gerrit keys",
          got.returncode == 0 and scoped.get("risk_floor") == "HIGH", got.stdout + got.stderr)
    single_query = tmp / "query-single.json"
    single_query.write_text(json.dumps({"number": 1, "project": "example-sdk"}) + "\n", encoding="utf-8")
    got = run(triage, "change", "1", "--query-json", str(single_query), env=scope_env)
    check("triage change: a rules file without the Gerrit keys is an input error naming them",
          got.returncode == 2 and "gerrit, clones, gateways" in got.stderr and "Traceback" not in got.stderr,
          got.stdout + got.stderr)
    got = run(triage, "change", "1", env=scope_env)
    check("triage change: without --query-json the missing keys are named before any Gerrit query",
          got.returncode == 2 and "gerrit, clones, gateways" in got.stderr and "Traceback" not in got.stderr,
          got.stdout + got.stderr)
    # Each key on its own: `check` accepts the file, and `change` names that
    # key and no other, even with query JSON that would otherwise run.
    for key in ("gerrit", "clones", "gateways"):
        one_missing = {name: value for name, value in config.items() if name != key}
        one_file = tmp / ("triage-no-%s.json" % key)
        _write_doc(one_file, one_missing)
        got = checked(one_file)
        check("triage check: a rules file without only %s passes" % key,
              got.returncode == 0 and got.stdout == "", got.stdout + got.stderr)
        one_env = _roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=one_file)
        got = run(triage, "change", "1", "--query-json", str(single_query), env=one_env)
        others = [name for name in ("gerrit", "clones", "gateways") if name != key]
        check("triage change: without only %s, the error names %s and nothing else" % (key, key),
              got.returncode == 2 and "has no %s (" % key in got.stderr
              and not any(name in got.stderr.split("has no", 1)[-1] for name in others)
              and "Traceback" not in got.stderr, got.stdout + got.stderr)
    expect_bad("gerrit-not-object", lambda doc: doc.update({"gerrit": "alice@gerrit.example.com"}), "gerrit: must be an object")
    expect_bad("clones-not-object", lambda doc: doc.update({"clones": []}), "clones: must be an object")
    expect_bad("gateways-not-object", lambda doc: doc.update({"gateways": "alpha"}), "gateways: must be an object")

    # Fix round 1: glob stars stay within one path segment and slash-less
    # patterns match basenames, not trailing path fragments.
    import importlib.util
    module_spec = importlib.util.spec_from_file_location("triage_under_test", triage)
    triage_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(triage_module)
    check("triage glob: * does not cross slash",
          triage_module.glob_matches("docs/spec/*.md", "docs/spec/x.md")
          and not triage_module.glob_matches("docs/spec/*.md", "docs/spec/sub/x.md"))
    check("triage glob: basename patterns stop at basename",
          triage_module.glob_matches("*.bb", "dir/x.bb")
          and not triage_module.glob_matches("*.bb", "dir/x.bb/extra"))
    # Fix round 1 B7: only headers before @@ are metadata. Content that merely
    # starts with header-looking punctuation stays in the hunk vectors.
    _hunks, added_lines, removed_lines = triage_module._patch_lines(
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n---- removed\n++++ added\n")
    check("triage diff parser: header-looking hunk content is kept",
          added_lines == ["+++ added"] and removed_lines == ["--- removed"])
    inline_url = chr(96) * 2 + "https://example.invalid/hidden" + chr(96) * 2
    check("triage move parser: inline code spans are ignored", not triage_module._urls(inline_url))
    # Fix round 1 B11: rename similarity scores are not different entry kinds.
    same_rename = {"status": "R058", "old": "a", "new": "b", "signature": "same", "added": [], "removed": []}
    scored_rename = dict(same_rename, status="R100")
    check("triage rename status: similarity scores compare as R",
          not triage_module._delta([same_rename], [scored_rename])[0])
    # Fix round 1 B15: currentPatchSet augments, rather than replaces, its
    # patchSets entry when Gerrit gives the two objects different fields.
    merged_change = {"patchSets": [{"number": 1, "revision": "kept", "parents": ["parent"]}],
                     "currentPatchSet": {"number": 1, "approvals": [{"value": "+1"}]}}
    _merged_sets, merged_current = triage_module._patch_sets(merged_change)
    check("triage patch sets: current object fills missing fields without replacement",
          merged_current.get("revision") == "kept" and merged_current.get("approvals") == [{"value": "+1"}])

    # Fix round 1 A1: compare each patch set against its own parent; a rebase
    # leaves an unchanged file out of the next review delta.
    git(repo, "checkout", "-q", "-B", "rebase-own-patch", base)
    write("docs/a.md", "base\n")
    write("docs/b.md", "base\n")
    rebase_base = commit("rebase base")
    write("docs/a.md", "ps1 A\n")
    write("docs/b.md", "ps1 B\n")
    rebase_ps1 = commit("rebase ps1")
    git(repo, "checkout", "-q", "-B", "rebase-parent-again", rebase_base)
    write("unrelated.txt", "parent moved\n")
    rebase_parent = commit("rebase parent")
    write("docs/a.md", "ps1 A\n")
    write("docs/b.md", "ps2 B\n")
    rebase_ps2 = commit("rebase ps2")
    result = changed(116, [patch(1, rebase_ps1, rebase_base, approvals=[approval("+1")]),
                           patch(2, rebase_ps2, rebase_parent)])
    check("triage rebase: unchanged own-patch file is excluded", result.get("delta_files") == ["docs/b.md"], result)

    # Fix round 1 A1/A3: a rename with one changed line keeps only that hunk;
    # unchanged trigger text in the moved file cannot be searched as a delta.
    git(repo, "checkout", "-q", "-B", "rename-hunk", base)
    write("docs/spec/old.md", "Status Verified\nkeep\nold line\n")
    rename_ps1 = commit("rename source")
    git(repo, "mv", "docs/spec/old.md", "docs/spec/new.md")
    write("docs/spec/new.md", "Status Verified\nkeep\nnew line\n")
    rename_ps2 = commit("rename with one edit")
    result = changed(117, [patch(1, rename_ps1, base, approvals=[approval("+1")]),
                           patch(2, rename_ps2, rename_ps1)])
    check("triage rename: only the changed hunk is counted",
          result.get("delta_files") == ["docs/spec/new.md"] and result.get("legs") == "own-read"
          and "status flip" not in result.get("flags", []), result)

    # Fix round 1 A3: regex lines belong to their file, not the shared pool.
    git(repo, "checkout", "-q", "-B", "trigger-by-file", base)
    write("docs/spec/one.md", "ordinary\n")
    write("src/other.txt", "ordinary\n")
    per_file_ps1 = commit("per-file source")
    write("docs/spec/one.md", "still ordinary\n")
    write("src/other.txt", "Status Verified\n")
    per_file_ps2 = commit("keyword elsewhere")
    result = changed(118, [patch(1, per_file_ps1, base, approvals=[approval("+1")]),
                           patch(2, per_file_ps2, per_file_ps1)])
    check("triage trigger: keyword in a different delta file does not fire",
          "status flip" not in result.get("flags", []) and result.get("risk_floor") == "MEDIUM", result)
    git(repo, "checkout", "-q", "-B", "removed-trigger", per_file_ps1)
    write("docs/spec/one.md", "Status Verified\n")
    removed_source = commit("removed trigger source")
    write("docs/spec/one.md", "ordinary\n")
    removed_keyword = commit("remove trigger keyword")
    result = changed(119, [patch(1, removed_source, per_file_ps1, approvals=[approval("+1")]),
                           patch(2, removed_keyword, removed_source)])
    check("triage trigger: a removed keyword fires", "status flip" in result.get("flags", []), result)

    # Fix round 1 A2: a reviewer with no vote must read a trivial rebase.
    result = changed(120, [patch(1, rename_ps2, rename_ps1, kind="TRIVIAL_REBASE")])
    check("triage ps-kind: unread trivial rebase is new and has legs",
          result.get("ps_kind") == "new" and result.get("legs") == "roster", result)

    # Fix round 1 B6: only another person's post-upload feedback makes an
    # owner fix round, and it overrides small/carry-over leg shortcuts.
    result = changed(121, [patch(1, rename_ps2, rename_ps1, created=20)], owner="alice",
                     comments=[{"timestamp": 21, "reviewer": {"username": "bob"}, "message": "please adjust"}])
    check("triage owner fix: comment only uses roster",
          result.get("legs") == "roster" and any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)
    git(repo, "checkout", "-q", "-B", "owner-small", base)
    write("src/owner.txt", "one\n")
    owner_small_ps1 = commit("owner small ps1")
    write("src/owner.txt", "two\n")
    owner_small_ps2 = commit("owner small ps2")
    result = changed(122, [patch(1, owner_small_ps1, base, approvals=[approval("+1")], created=10),
                           patch(2, owner_small_ps2, owner_small_ps1, created=20)], owner="alice",
                     comments=[{"timestamp": 21, "reviewer": {"username": "bob"}, "message": "small fix"}])
    check("triage owner fix: small post-vote delta still uses roster",
          result.get("legs") == "roster", result)
    result = changed(123, [patch(1, rename_ps2, rename_ps1, kind="TRIVIAL_REBASE", created=20)], owner="alice",
                     comments=[{"timestamp": 21, "reviewer": {"username": "bob"}, "message": "rebase fix"}])
    check("triage owner fix: trivial rebase still uses roster", result.get("legs") == "roster", result)
    result = changed(124, [patch(1, rename_ps2, rename_ps1, approvals=[approval("-1", 21)], created=20)], owner="alice")
    check("triage owner fix: my own negative vote does not trigger it",
          not any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Fix round 1: only a later comment by somebody else that names me wakes a
    # current-vote skip; first matching order rule need not be rank zero.
    result = changed(125, [current_vote], comments=[{"timestamp": 30, "reviewer": {"username": "bob"}, "message": "no name here"}])
    check("triage skip: later comment without my name keeps skip", result.get("skip") is not None, result)
    result = changed(126, [current_vote], comments=[{"timestamp": 30, "reviewer": {"username": "alice"}, "message": "alice revisited"}])
    check("triage skip: my own later comment keeps skip", result.get("skip") is not None, result)
    result = changed(127, [patch(1, small, trigger_old)], owner="carol")
    check("triage order: rule zero can miss and rank one can win", result.get("order") == 1, result)

    # Fix round 1 B5: a latest zero or malformed vote withdraws my active vote.
    result = changed(141, [patch(1, rename_ps1, base, approvals=[approval("+1")]),
                           patch(2, rename_ps2, rename_ps1, approvals=[approval("0", 30)])])
    check("triage votes: a latest zero means no active vote", result.get("my_last_vote") is None, result)
    result = changed(142, [patch(1, small, trigger_old, approvals=[approval("not-a-vote", 30)])])
    check("triage votes: a malformed value means no active vote", result.get("my_last_vote") is None, result)

    # Fix round 1: the threshold itself is inclusive.
    old_limit = config["small_delta_lines"]
    config["small_delta_lines"] = 2
    _write_doc(config_file, config)
    result = changed(128, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, small, trigger_old)])
    check("triage legs: delta equal to threshold is own-read", result.get("legs") == "own-read", result)
    config["small_delta_lines"] = old_limit
    _write_doc(config_file, config)

    # Fix round 1 B13: only _comment keys are permitted in nested objects and
    # public maps; validation never accidentally hides a typo behind '_'.
    expect_bad("nested-lens-key", lambda doc: doc["lenses"][0].update({"typo": 1}), "unknown key")
    expect_bad("nested-trigger-key", lambda doc: doc["delta_triggers"][0].update({"typo": 1}), "unknown key")
    expect_bad("nested-naming-key", lambda doc: doc["move_check"]["naming"].append({"glob": "x", "regex": "x", "typo": 1}), "unknown key")
    expect_bad("trigger-risk", lambda doc: doc["delta_triggers"][0].update({"risk": "SEVERE"}), "LOW, MEDIUM, or HIGH")
    expect_bad("trigger-lens", lambda doc: doc["delta_triggers"][0].update({"lenses": ["missing"]}), "unknown lens")
    expect_bad("naming-regex", lambda doc: doc["move_check"]["naming"].append({"glob": "x", "regex": "["}), "invalid regex")
    expect_bad("bad-glob", lambda doc: doc["lenses"][0].update({"glob": ""}), "non-empty")
    expect_bad("clone-underscore", lambda doc: doc["clones"].update({"_not_comment": "/tmp/x"}), "unknown key")
    def comment_as_lens(doc):
        doc["lens_classes"]["_comment"] = "mechanical"
        doc["lenses"][0]["lenses"] = ["_comment"]
    expect_bad("comment-as-lens", comment_as_lens, "unknown lens")

    # Fix round 1: every configured r1 adapter remains visible. The live
    # roster's judgment entry carries gated_on, so the mechanical entry stands
    # in for it and the output says which gate (0.6.24: gated_on was ignored).
    result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)])
    live_review = _live_roster()["rounds"]["r1"]["review"]
    expected_adapters = {name for name, leg in live_review.items() if not name.startswith("_") and leg is not None}
    output_legs = {leg["adapter"]: leg for leg in result.get("review_legs", [])}
    by_lens = live_review["opencode"]["by_lens"]
    opencode_leg = output_legs.get("opencode") or {}
    check("triage review legs: every r1 adapter is present; a gated judgment entry is not dispatched",
          set(output_legs) == expected_adapters
          and opencode_leg.get("model") == by_lens["mechanical"]["model"]
          and opencode_leg.get("stands_in_for") == {"lens": "judgment", "gated_on": by_lens["judgment"]["gated_on"]},
          result)
    ungated = _live_roster()
    del ungated["rounds"]["r1"]["review"]["opencode"]["by_lens"]["judgment"]["gated_on"]
    ungated_file = tmp / "roster-ungated.json"
    _write_doc(ungated_file, ungated)
    ungated_env = _roster_env(tmp, DEV_LEAD_ROSTER=ungated_file, DEV_LEAD_TRIAGE=config_file)
    result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)],
                     test_env=ungated_env)
    opencode_leg = {leg["adapter"]: leg for leg in result.get("review_legs", [])}.get("opencode") or {}
    check("triage review legs: an ungated judgment entry is selected as itself",
          opencode_leg.get("model") == by_lens["judgment"]["model"] and "stands_in_for" not in opencode_leg, result)
    both_gated = _live_roster()
    both_gated["rounds"]["r1"]["review"]["opencode"]["by_lens"]["mechanical"]["gated_on"] = "a probe"
    both_file = tmp / "roster-both-gated.json"
    _write_doc(both_file, both_gated)
    both_env = _roster_env(tmp, DEV_LEAD_ROSTER=both_file, DEV_LEAD_TRIAGE=config_file)
    got = run(triage, "change", "129", "--query-json",
              query(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)]), env=both_env)
    plain_gated = _live_roster()
    plain_gated["rounds"]["r1"]["review"]["cursor"]["gated_on"] = "a cursor probe"
    plain_file = tmp / "roster-plain-gated.json"
    _write_doc(plain_file, plain_gated)
    plain_env = _roster_env(tmp, DEV_LEAD_ROSTER=plain_file, DEV_LEAD_TRIAGE=config_file)
    result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)],
                     test_env=plain_env)
    whole_gated = _live_roster()
    whole_gated["rounds"]["r1"]["review"]["opencode"]["gated_on"] = "a whole-leg probe"
    whole_file = tmp / "roster-whole-gated.json"
    _write_doc(whole_file, whole_gated)
    whole_result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)],
                           test_env=_roster_env(tmp, DEV_LEAD_ROSTER=whole_file, DEV_LEAD_TRIAGE=config_file))
    check("triage review legs: a gate on a whole by_lens leg leaves it out",
          "opencode" not in {leg["adapter"] for leg in whole_result.get("review_legs", [])}
          and any("left out: opencode (gated: a whole-leg probe)" in rule["detail"]
                  for rule in whole_result.get("fired_rules", [])), whole_result)
    check("triage review legs: a gated plain leg is left out and named",
          "cursor" not in {leg["adapter"] for leg in result.get("review_legs", [])}
          and any("left out: cursor (gated: a cursor probe)" in rule["detail"] for rule in result.get("fired_rules", [])),
          result)
    check("triage review legs: nothing ungated to stand in is an error, not a gated dispatch",
          got.returncode == 2 and "gated" in got.stderr and "Traceback" not in got.stderr, got.stdout + got.stderr)

    # Fix round 1 B8: binary patches are a flagged zero-line delta, and a
    # CRLF-to-LF rewrite beneath a rename is content, not a move-only change.
    git(repo, "checkout", "-q", "-B", "binary", base)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "binary.bin").write_bytes(b"one\x00two")
    binary_ps1 = commit("binary one")
    (repo / "docs" / "binary.bin").write_bytes(b"one\x00three")
    binary_ps2 = commit("binary two")
    result = changed(130, [patch(1, binary_ps1, base, approvals=[approval("+1")]), patch(2, binary_ps2, binary_ps1)])
    check("triage binary: delta is flagged without a crash",
          any(flag == "docs/binary.bin: binary" for flag in result.get("flags", [])), result)
    # Round-5 review: two AMENDED patch sets change the same binary differently.
    # Both patches are "Binary files ... differ" with no hunk lines, so only the
    # blob ids in the signature tell them apart.
    git(repo, "checkout", "-q", "-B", "binary-amend", base)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "amend.bin").write_bytes(b"one\x00two")
    binary_amend_ps1 = commit("binary amend one")
    git(repo, "checkout", "-q", "-B", "binary-amend-2", base)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "amend.bin").write_bytes(b"one\x00three")
    binary_amend_ps2 = commit("binary amend two")
    result = changed(180, [patch(1, binary_amend_ps1, base, approvals=[approval("+1")]),
                           patch(2, binary_amend_ps2, base)])
    check("triage binary: a changed binary between amended patch sets is in the delta",
          "docs/amend.bin" in (result.get("delta_files") or []), result)
    git(repo, "checkout", "-q", "-B", "crlf-rename", base)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "old-crlf.md").write_bytes(b"line\r\n")
    crlf_ps1 = commit("crlf source")
    git(repo, "mv", "docs/old-crlf.md", "docs/new-crlf.md")
    (repo / "docs" / "new-crlf.md").write_bytes(b"line\n")
    crlf_ps2 = commit("crlf rewritten")
    result = changed(131, [patch(1, crlf_ps1, base, approvals=[approval("+1")]), patch(2, crlf_ps2, crlf_ps1)])
    check("triage move: CRLF rewrite is not move-only", result.get("ps_kind") != "move-only", result)

    # Fix round 1 B9: code spans/fences do not participate in move-link
    # checks; query/title syntax is removed before relative-link resolution.
    git(repo, "checkout", "-q", "-B", "move-link-syntax", base)
    write("docs/move/code.md", "```\nhttps://old.example/item\n```\n[ok](../links/ok.md \"title\")\n")
    move_syntax_ps1 = commit("move syntax source")
    (repo / "docs" / "move" / "deep").mkdir(parents=True)
    git(repo, "mv", "docs/move/code.md", "docs/move/deep/code.md")
    write("docs/move/deep/code.md", "```\nhttps://new.example/item\n```\n[ok](../../links/ok.md \"title\")\n")
    move_syntax_ps2 = commit("move syntax target")
    result = changed(132, [patch(1, move_syntax_ps1, base, approvals=[approval("+1")]), patch(2, move_syntax_ps2, move_syntax_ps1)])
    check("triage move links: fenced URL is ignored and titled link resolves",
          not any("absolute URL" in flag or "does not resolve" in flag for flag in result.get("flags", [])), result)
    git(repo, "checkout", "-q", "-B", "move-query", base)
    query_text = "[missing](missing.md?x=1)\nhttps://same.example/item.\n" + ("same\n" * 12)
    write("docs/move/query.md", query_text)
    move_query_ps1 = commit("query source")
    (repo / "docs" / "move" / "deep").mkdir(parents=True)
    git(repo, "mv", "docs/move/query.md", "docs/move/deep/query.md")
    write("docs/move/deep/query.md", query_text.replace("https://same.example/item.", "https://same.example/item"))
    move_query_ps2 = commit("query target")
    result = changed(133, [patch(1, move_query_ps1, base, approvals=[approval("+1")]), patch(2, move_query_ps2, move_query_ps1)])
    check("triage move links: unresolved query link is flagged but sentence punctuation is not a URL change",
          any("relative link missing.md does not resolve" in flag for flag in result.get("flags", []))
          and not any("absolute URL" in flag for flag in result.get("flags", [])), result)

    # Fix round 1 B4: --as-of-ps restores the decision as it stood before a
    # later patch set and vote arrived.
    git(repo, "checkout", "-q", "-B", "as-of", base)
    write("src/asof.txt", "one\n")
    asof_ps1 = commit("as of one")
    write("src/asof.txt", "two\n")
    asof_ps2 = commit("as of two")
    result = changed(134, [patch(1, asof_ps1, base, created=10),
                           patch(2, asof_ps2, asof_ps1, approvals=[approval("+1", 25)], created=20)],
                     args=("--as-of-ps", "1"))
    check("triage as-of: PS1 ignores later vote and names selected patch set",
          result.get("as_of_patch_set") == 1 and result.get("my_last_vote") is None
          and result.get("rules", {}).get("as_of_patch_set") == 1, result)
    # A replay of a patch set I reviewed must not see my own vote on it: the
    # patrol triaged it before reading it, so the vote came after.
    result = changed(135, [patch(1, asof_ps1, base, approvals=[approval("-1", 15)], created=10),
                           patch(2, asof_ps2, asof_ps1, created=20)],
                     args=("--as-of-ps", "1"))
    check("triage as-of: my own vote on the replayed patch set is excluded",
          result.get("my_last_vote") is None and result.get("skip") is None, result)

    # Fix round 1 B2/B3: a skip succeeds without a clone; ~/ clone paths are
    # accepted after expansion and work when HOME points at the fixture root.
    no_clone_config = json.loads(json.dumps(config))
    no_clone_config["clones"]["example-sdk"] = str(tmp / "no-such-clone")
    _write_doc(config_file, no_clone_config)
    result = changed(135, [current_vote])
    check("triage skip: missing clone leaves git-dependent fields null",
          result.get("skip") is not None and result.get("delta_files") is None and result.get("ps_kind") is None, result)
    home_config = json.loads(json.dumps(config))
    home_config["clones"]["example-sdk"] = "~/example-sdk"
    _write_doc(config_file, home_config)
    home_env = dict(env, HOME=str(tmp))
    home_check = run(triage, "check", "--file", config_file, env=home_env)
    result = changed(136, [patch(1, asof_ps1, base)], test_env=home_env)
    check("triage clone: ~/ expands for check and change", home_check.returncode == 0 and result.get("change") == 136, home_check.stdout + home_check.stderr + repr(result))
    _write_doc(config_file, config)

    # Fix round 1 A4: dependencies omit status in real Gerrit output. Query
    # JSON supplies ancestor rows; absent rows remain explicitly unknown.
    result = changed(137, [patch(1, asof_ps1, base)], depends=[{"number": 501}])
    check("triage ancestors: an absent row is unknown, not unmerged", result.get("unknown_ancestors") == [501] and not result.get("unmerged_ancestors"), result)
    result = changed(138, [patch(1, asof_ps1, base)], depends=[{"number": 502}],
                     extra_rows=[{"number": 502, "status": "NEW"}])
    check("triage ancestors: NEW ancestor is unmerged", result.get("unmerged_ancestors") == [502] and not result.get("unknown_ancestors"), result)
    result = changed(139, [patch(1, asof_ps1, base)], depends=[{"number": 503}],
                     extra_rows=[{"number": 503, "status": "MERGED"}])
    check("triage ancestors: MERGED ancestor is neither list", not result.get("unmerged_ancestors") and not result.get("unknown_ancestors"), result)

    # Fix round 1 B12: the first commit has no parent and is diffed against
    # Git's empty tree rather than making rev-parse fail.
    root_patch = {"number": 1, "revision": base, "parents": [], "kind": "REWORK", "createdOn": 10,
                  "uploader": {"username": "alice"}, "approvals": [], "files": []}
    result = changed(143, [root_patch])
    # The files the root commit adds must be the delta; a parent equal to the
    # revision itself would give [] and must fail this.
    check("triage root patch set: diffs against the empty tree",
          {"src/value.txt", "links/ok.md"} <= set(result.get("delta_files") or []), result)

    # Fix round 1 B10: literal pathspecs accept ordinary filenames containing
    # a colon rather than treating them as pathspec magic.
    git(repo, "checkout", "-q", "-B", "literal-pathspec", base)
    write("docs/a:b.md", "old\n")
    colon_ps1 = commit("colon source")
    write("docs/a:b.md", "new\n")
    colon_ps2 = commit("colon target")
    result = changed(140, [patch(1, colon_ps1, base, approvals=[approval("+1")]), patch(2, colon_ps2, colon_ps1)])
    check("triage literal pathspec: colon path is handled", result.get("delta_files") == ["docs/a:b.md"], result)

    # A filename that is not UTF-8 (b"caf\xe9.md", Latin-1) must still be
    # reported, not dropped or crash the run. HANDOFF: verified by hand only
    # before 0.6.21; pinned here in the release review (2026-09-23).
    git(repo, "checkout", "-q", "-B", "non-utf8-path", base)
    latin1 = "docs/caf" + os.fsdecode(b"\xe9") + ".md"
    write(latin1, "old\n")
    latin_ps1 = commit("latin-1 name source")
    write(latin1, "new\n")
    latin_ps2 = commit("latin-1 name target")
    result = changed(144, [patch(1, latin_ps1, base, approvals=[approval("+1")]),
                           patch(2, latin_ps2, latin_ps1)])
    check("triage non-UTF-8 path: the file is reported, not dropped",
          result.get("delta_files") == [latin1], result)

    # Fix round 2 F1: replay keeps other people's feedback before PS N+1,
    # but excludes my review action on the replayed patch set.
    replay = {
        "owner": {"username": "bob"},
        "patchSets": [{"number": 1, "createdOn": 10, "approvals": [approval("+1", 12)]},
                      {"number": 2, "createdOn": 20, "approvals": []}],
        "comments": [{"timestamp": 15, "reviewer": {"username": "carol"}, "message": "please revisit"}],
    }
    if hasattr(triage_module, "_as_of_cutoffs"):
        cutoffs = triage_module._as_of_cutoffs(replay, 1)
        replay_sets, _replay_current = triage_module._patch_sets(replay, 1)
        triage_module._limit_to_as_of(replay, replay_sets, cutoffs, {"alice"})
        replay_ok = not replay_sets["1"]["approvals"] and replay["comments"]
    else:
        replay_ok = False
    check("triage as-of F1: third-party feedback survives while my PS1 vote does not", replay_ok, replay)

    # Fix round 2 F1: an owner's reviewers may still have answered before the
    # next upload; the owner's own upload message must not erase those votes.
    result = changed(159, [patch(1, asof_ps1, base, approvals=[approval("-1", 15, "bob")], created=10),
                           patch(2, asof_ps2, asof_ps1, created=20)], owner="alice",
                     comments=[{"timestamp": 10, "reviewer": {"username": "alice"}, "message": "Uploaded patch set 1."}],
                     args=("--as-of-ps", "1"))
    check("triage as-of F1: owner sees a pre-upload reviewer negative",
          result.get("legs") == "roster" and any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)
    result = changed(160, [patch(1, asof_ps1, base, approvals=[approval("+1", 15)], created=10),
                           patch(2, asof_ps2, asof_ps1, created="next-upload")],
                     comments=[{"timestamp": 16, "reviewer": {"username": "carol"}, "message": "third-party feedback"}],
                     args=("--as-of-ps", "1"))
    check("triage as-of F1: mixed string and integer timestamps do not raise",
          result.get("change") == 160 and result.get("role") == "reviewer" and result.get("my_last_vote") is None, result)

    # Fix round 2 F2: a rename's removed trigger line is searched at its old
    # path even though delta_files remains the new path.
    git(repo, "checkout", "-q", "-B", "rename-removed-trigger", base)
    write("docs/spec/old.md", "Status: Verified\n" + ("keep\n" * 12))
    rename_removed_ps1 = commit("rename removed trigger source")
    git(repo, "mv", "docs/spec/old.md", "docs/spec/new.md")
    write("docs/spec/new.md", "keep\n" * 12)
    rename_removed_ps2 = commit("rename removes trigger")
    result = changed(161, [patch(1, rename_removed_ps1, base, approvals=[approval("+1")]),
                           patch(2, rename_removed_ps2, rename_removed_ps1)])
    check("triage rename F2: removed trigger in a PS rename raises risk",
          result.get("delta_files") == ["docs/spec/new.md"] and result.get("risk_floor") == "HIGH", result)

    # Fix round 2 F3: negatives from an owner's older patch set still require
    # a roster round after someone else uploads the later patch set.
    result = changed(162, [patch(1, asof_ps1, base, approvals=[approval("-1", 15, "bob")], created=10),
                           dict(patch(2, asof_ps2, asof_ps1, created=20), uploader={"username": "carol"})], owner="alice")
    check("triage owner fix F3: older patch-set negative uses roster",
          result.get("legs") == "roster" and any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Fix round 2 F4: a stray CR is content inside one Git line, not a line
    # boundary; the trigger sees it and the added line counts once.
    git(repo, "checkout", "-q", "-B", "stray-cr", base)
    (repo / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "spec" / "cr.md").write_bytes(b"ordinary\rStatus: Verified now\n")
    stray_cr = commit("stray carriage return")
    result = changed(163, [patch(1, stray_cr, base)])
    check("triage hunk F4: stray CR preserves one trigger line",
          result.get("risk_floor") == "HIGH" and result.get("legs") == "roster", result)
    _cr_hunks, cr_added, cr_removed = triage_module._patch_lines(
        "diff --git a/a b/a\n@@ -0,0 +1 @@\n+ordinary\rStatus: Verified now\n")
    check("triage hunk F4: stray CR line is counted once",
          cr_added == ["ordinary\rStatus: Verified now"] and not cr_removed, (cr_added, cr_removed))

    # Fix round 2 F5: a moved document's link outside the tree is a flag, not
    # a fatal git-show error.
    git(repo, "checkout", "-q", "-B", "outside-link", base)
    outside_text = "[outside](../../../../outside.md)\n" + ("same\n" * 12)
    write("docs/v2/outside.md", outside_text)
    outside_ps1 = commit("outside link source")
    (repo / "docs" / "v2" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "docs/v2/outside.md", "docs/v2/deep/outside.md")
    write("docs/v2/deep/outside.md", outside_text)
    outside_ps2 = commit("outside link move")
    result = changed(164, [patch(1, outside_ps1, base, approvals=[approval("+1")]),
                           patch(2, outside_ps2, outside_ps1)])
    check("triage move F5: outside-tree relative link is flagged without aborting",
          any("relative link ../../../../outside.md does not resolve" in flag for flag in result.get("flags", [])), result)

    # Fix round 2 F7/F9: fence runs, parenthetical titles, nested destination
    # parentheses, and four-space code all receive Markdown-aware treatment.
    fenced = "```` python\n[hidden](missing.md)\n```\n[also-hidden](missing.md)\n````\n"
    parser_ok = (not list(triage_module._relative_targets(fenced))
                 and list(triage_module._relative_targets("[title](a.md (title)) [nested](a(1).md)")) == ["a.md", "a(1).md"]
                 and not list(triage_module._relative_targets("    [indented](missing.md)\n")))
    check("triage markdown F7/F9: fence length, titles, parentheses, and indented code work", parser_ok)
    # Lead review of fix round 2: an indented code block opens only after a
    # blank line, so a link continuing a list item is still checked.
    list_doc = "- item\n    [continuation](missing.md)\n\n    [code](hidden.md)\n"
    check("triage markdown: an indented list continuation is still link-checked",
          list(triage_module._relative_targets(list_doc)) == ["missing.md"],
          list(triage_module._relative_targets(list_doc)))

    # Round-3 review: Markdown parsing, each from a leg's concrete input.
    crlf_doc = "para\r\n\r\n    [code](hidden.md)\r\n"
    check("triage markdown: a CRLF blank line still opens an indented code block",
          not list(triage_module._relative_targets(crlf_doc)),
          list(triage_module._relative_targets(crlf_doc)))
    indented_fence = "para\n    ```\n[after](missing.md)\n"
    check("triage markdown: a four-space fence marker does not open a fence",
          list(triage_module._relative_targets(indented_fence)) == ["missing.md"],
          list(triage_module._relative_targets(indented_fence)))
    titled = '[Spec](spec.md "Section 1 (draft") and [API](api.md).'
    check("triage markdown: a paren inside a quoted title does not swallow the next link",
          list(triage_module._relative_targets(titled)) == ["spec.md", "api.md"],
          list(triage_module._relative_targets(titled)))
    unbalanced = "[x](a.md (unclosed\n\n[y](missing.md)\n"
    check("triage markdown: one unbalanced destination does not hide later links",
          "missing.md" in list(triage_module._relative_targets(unbalanced)),
          list(triage_module._relative_targets(unbalanced)))
    check("triage markdown: a URL may carry a balanced parenthesis",
          triage_module._urls("[doc](https://example.com/wiki/Page_(v1))") == {"https://example.com/wiki/Page_(v1)"},
          triage_module._urls("[doc](https://example.com/wiki/Page_(v1))"))

    # Round-3 review, agy/cursor: with no prior vote the delta lines come from
    # _delta_lines, which filed a rename's removed lines on the NEW path, so a
    # trigger keyword deleted by the rename was never searched.
    git(repo, "checkout", "-q", "-B", "rename-out", base)
    rename_out_text = "Status Verified\n" + "".join("keep %d\n" % index for index in range(30))
    write("docs/spec/moved.md", rename_out_text)
    rename_out_base = commit("rename out base")
    (repo / "docs" / "other").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "docs/spec/moved.md", "docs/other/moved.md")
    write("docs/other/moved.md", rename_out_text.replace("Status Verified\n", ""))
    rename_out_ps1 = commit("rename out ps1")
    result = changed(167, [patch(1, rename_out_ps1, rename_out_base)])
    check("triage rename: a removed trigger line is searched at the old path without a prior vote",
          result.get("risk_floor") == "HIGH", result)

    # Round-3 review, agy: a reviewer who replaced their own -1 with a later
    # vote has answered; only their latest vote counts.
    result = changed(168, [patch(1, owner_small_ps1, base, created=10,
                                 approvals=[approval("-1", 15, who="bob")]),
                           patch(2, owner_small_ps2, owner_small_ps1, created=20,
                                 approvals=[approval("+1", 25, who="bob")])], owner="alice")
    check("triage owner fix: a superseded negative is not an open fix round",
          not any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)
    result = changed(169, [patch(1, owner_small_ps1, base, created=10,
                                 approvals=[approval("+1", 15, who="bob")]),
                           patch(2, owner_small_ps2, owner_small_ps1, created=20,
                                 approvals=[approval("-1", 25, who="bob")])], owner="alice")
    check("triage owner fix: the latest negative still opens a fix round",
          any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Round-3 review, cursor: an unparsable createdOn must not win "my last
    # upload" and hide every later negative vote.
    result = changed(170, [patch(1, owner_small_ps1, base, created="rebuilt"),
                           patch(2, owner_small_ps2, owner_small_ps1, created=20,
                                 approvals=[approval("-1", 25, who="bob")])], owner="alice")
    check("triage owner fix: junk timestamps cannot hide a later negative",
          any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Round-3 review, cursor/agy: git has a third way of saying "not in this
    # revision" for a path that exists in the work tree.
    git(repo, "checkout", "-q", "-B", "worktree-link", base)
    worktree_text = "[here](on-disk.md)\n" + ("same\n" * 12)
    write("docs/wt/page.md", worktree_text)
    worktree_ps1 = commit("worktree link source")
    (repo / "docs" / "wt" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "docs/wt/page.md", "docs/wt/deep/page.md")
    write("docs/wt/deep/page.md", worktree_text)
    worktree_ps2 = commit("worktree link move")
    # Present in the work tree, absent from every patch set: git answers
    # "exists on disk, but not in <rev>", its third way of saying no.
    (repo / "docs" / "wt" / "deep" / "on-disk.md").write_text("only on disk\n")
    result = changed(171, [patch(1, worktree_ps1, base, approvals=[approval("+1")]),
                           patch(2, worktree_ps2, worktree_ps1)])
    check("triage move: a link that misses the revision is flagged, not fatal",
          any("does not resolve" in flag for flag in result.get("flags", [])), result)

    # Round-3 review, agy: Gerrit numbering may skip, so the replay ends at the
    # next patch set that exists.
    result = changed(172, [patch(1, owner_small_ps1, base, created=10),
                           patch(3, owner_small_ps2, owner_small_ps1, created=30)],
                     owner="alice", args=("--as-of-ps", "1"),
                     comments=[{"timestamp": 35, "reviewer": {"username": "bob"}, "message": "after ps3"}])
    check("triage as-of: a gap in patch set numbers still ends the replay",
          not any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Round-4 review: junk sorts first now, so it must never be PICKED as the
    # replay cutoff and then discarded by the guard.
    # Two real later times (30, 50) around the comment (35), so min and max
    # give different answers: only the EARLIEST later upload ends the replay.
    result = changed(173, [patch(1, owner_small_ps1, base, created=10),
                           patch(2, owner_small_ps2, owner_small_ps1, created="rebuilt"),
                           patch(3, owner_small_ps2, owner_small_ps1, created=30),
                           patch(4, owner_small_ps2, owner_small_ps1, created=50)],
                     owner="alice", args=("--as-of-ps", "1"),
                     comments=[{"timestamp": 35, "reviewer": {"username": "bob"}, "message": "after ps3"}])
    check("triage as-of: one junk timestamp does not discard a real cutoff",
          not any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Round-4 review, cursor: a negative whose timestamp does not parse cannot
    # be shown to predate my upload, so it must not vanish.
    result = changed(174, [patch(1, owner_small_ps1, base, created=20,
                                 approvals=[{"type": "Code-Review", "value": "-1", "grantedOn": None,
                                             "by": {"username": "bob", "email": "bob@example.com"}}])],
                     owner="alice")
    check("triage owner fix: a negative with an unparsable timestamp still counts",
          any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Round-4 review: one reviewer keyed by email on one vote and by username
    # on the next is one account, not two opinions.
    result = changed(175, [patch(1, owner_small_ps1, base, created=10,
                                 approvals=[{"type": "Code-Review", "value": "-1", "grantedOn": 25,
                                             "by": {"email": "bob@example.com", "name": "Bob Smith"}}]),
                           patch(2, owner_small_ps2, owner_small_ps1, created=20,
                                 approvals=[{"type": "Code-Review", "value": "+1", "grantedOn": 30,
                                             "by": {"username": "bob", "email": "bob@example.com",
                                                    "name": "Robert Smith"}}])], owner="alice")
    check("triage owner fix: one reviewer keyed two ways is one account",
          not any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)
    result = changed(176, [patch(1, owner_small_ps1, base, created=10,
                                 approvals=[approval("-1", 25, who="bob")]),
                           patch(2, owner_small_ps2, owner_small_ps1, created=20,
                                 approvals=[approval("+1", 25, who="bob")])], owner="alice")
    check("triage owner fix: a same-second tie goes to the later patch set",
          not any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Round-4 review: a tab is four columns, and an apostrophe belongs to the
    # destination -- both otherwise HIDE a link that does not resolve.
    tab_fence = "para\n\t```\n[after](missing.md)\n"
    check("triage markdown: a tab-indented fence marker does not open a fence",
          list(triage_module._relative_targets(tab_fence)) == ["missing.md"],
          list(triage_module._relative_targets(tab_fence)))
    apostrophe = "[Notes](notes/bob's_summary.md) and [API](api.md)."
    check("triage markdown: an apostrophe in a destination is part of the path",
          list(triage_module._relative_targets(apostrophe)) == ["notes/bob's_summary.md", "api.md"],
          list(triage_module._relative_targets(apostrophe)))
    check("triage markdown: a quoted title after whitespace is still a title",
          list(triage_module._relative_targets('[Spec](spec.md "Section 1 (draft") and [API](api.md).'))
          == ["spec.md", "api.md"],
          list(triage_module._relative_targets('[Spec](spec.md "Section 1 (draft") and [API](api.md).')))
    # Round-4 review: a submodule is a gitlink whose commit is NOT in this
    # repository, so `cat-file -e` says no about a path that is in the tree.
    git(repo, "checkout", "-q", "-B", "gitlink", base)
    git(repo, "update-index", "--add", "--cacheinfo",
        "160000,0123456789012345678901234567890123456789,vendor")
    gitlink_tree = run("git", "-C", str(repo), "write-tree").stdout.strip()
    gitlink_rev = run("git", "-C", str(repo), "commit-tree", gitlink_tree, "-p", base, "-m", "gitlink").stdout.strip()
    git(repo, "reset", "-q", "--mixed", base)
    check("triage tree: a submodule gitlink counts as present",
          triage_module._tree_has(repo, gitlink_rev, "vendor"), gitlink_rev)
    check("triage tree: the repository root resolves",
          triage_module._tree_has(repo, gitlink_rev, "."), gitlink_rev)
    check("triage tree: a path absent from the revision does not resolve",
          not triage_module._tree_has(repo, gitlink_rev, "nowhere.md"), gitlink_rev)

    # Round-5 review #1: an ssh destination that starts with "-" is an OPTION
    # (-oProxyCommand=... runs a program). `check` refuses it, and the query
    # puts `--` before the destination in case a check was ever skipped.
    expect_bad("ssh-option-injection",
               lambda doc: doc["gerrit"].__setitem__("ssh", "-oProxyCommand=x@y"), "gerrit.ssh")
    expect_bad("ssh-host-option", lambda doc: doc["gerrit"].__setitem__("ssh", "alice@-oProxyCommand=x"), "gerrit.ssh")
    fake_bin = tmp / "fake-ssh-bin"
    fake_bin.mkdir(exist_ok=True)
    argv_log = tmp / "ssh-argv.txt"
    (fake_bin / "ssh").write_text('#!/bin/sh\nprintf "%%s\\n" "$@" > %s\n' % argv_log)
    (fake_bin / "ssh").chmod(0o755)
    saved_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(fake_bin) + os.pathsep + saved_path
    try:
        triage_module._gerrit_rows({"gerrit": {"ssh": "alice@gerrit.example.com", "port": 29418}}, "change:1")
    finally:
        os.environ["PATH"] = saved_path
    ssh_argv = argv_log.read_text().splitlines() if argv_log.exists() else []
    check("triage ssh: `--` precedes the destination",
          ssh_argv[:4] == ["-p", "29418", "--", "alice@gerrit.example.com"], ssh_argv)

    # A host that accepts the connection and never answers. Until 0.6.48 the
    # query had no bound and the patrol hung; now it is an input error naming
    # the destination, within the (overridden) timeout.
    slow_bin = tmp / "slow-ssh-bin"
    slow_bin.mkdir(exist_ok=True)
    (slow_bin / "ssh").write_text("#!/bin/sh\nsleep 30\n")
    (slow_bin / "ssh").chmod(0o755)
    os.environ["PATH"] = str(slow_bin) + os.pathsep + saved_path
    os.environ["TRIAGE_TIMEOUT_SECS"] = "1"
    started = time.monotonic()
    try:
        triage_module._gerrit_rows({"gerrit": {"ssh": "alice@gerrit.example.com", "port": 29418}}, "change:1")
        timed_out = None
    except triage_module.InputError as exc:
        timed_out = str(exc)
    finally:
        os.environ["PATH"] = saved_path
        del os.environ["TRIAGE_TIMEOUT_SECS"]
    elapsed = time.monotonic() - started
    check("triage ssh: a silent host is an input error within the timeout",
          timed_out is not None and "no answer from alice@gerrit.example.com after 1s" in timed_out
          and elapsed < 10, "exc=%r elapsed=%.1fs" % (timed_out, elapsed))
    # The change query asks for submit records: the dropped-hold flag says
    # SUBMITTABLE from them (2026-09-24). The fake ssh returns nothing, so the
    # query itself fails -- the argv is what is being checked.
    argv_log.unlink()
    os.environ["PATH"] = str(fake_bin) + os.pathsep + saved_path
    try:
        try:
            triage_module._query({"gerrit": {"ssh": "alice@gerrit.example.com", "port": 29418}}, "1", None)
        except Exception:
            pass
    finally:
        os.environ["PATH"] = saved_path
    query_argv = argv_log.read_text().splitlines() if argv_log.exists() else []
    check("triage ssh: the change query asks for submit records",
          "--submit-records" in query_argv, query_argv)
    # The fake ssh would succeed, so only the guard can refuse -- and the
    # proof is that ssh was never started.
    argv_log.unlink()
    os.environ["PATH"] = str(fake_bin) + os.pathsep + saved_path
    try:
        triage_module._gerrit_rows({"gerrit": {"ssh": "-oProxyCommand=x@y", "port": 29418}}, "change:1")
        refused = False
    except triage_module.InputError:
        refused = True
    finally:
        os.environ["PATH"] = saved_path
    check("triage ssh: an option-shaped destination is refused at run time too",
          refused and not argv_log.exists(), argv_log.read_text() if argv_log.exists() else "ssh not started")
    got = run(triage, "change", "abc", "--query-json", query(101, [patch(1, ps1, base)]), env=env)
    check("triage change: a non-numeric change number exits 2 without a traceback",
          got.returncode == 2 and "digits" in got.stderr and "Traceback" not in got.stderr, got.stdout + got.stderr)
    for unicode_digits in ("\u00b2", "\uff11\uff12"):
        got = run(triage, "change", unicode_digits, "--query-json", query(101, [patch(1, ps1, base)]), env=env)
        check("triage change: Unicode digits %r are refused like any non-ASCII number" % unicode_digits,
              got.returncode == 2 and "digits" in got.stderr and "Traceback" not in got.stderr, got.stdout + got.stderr)

    # Round-5 review #2: the ../ fold belongs to Markdown link destinations
    # only. The same depth change inside a script is an edit.
    script_text = "#!/bin/sh\nexec ../../tools/build.sh\n" + ("echo same\n" * 12)
    git(repo, "checkout", "-q", "-B", "move-script", base)
    write("tools/sub/run.sh", script_text)
    script_ps1 = commit("script source")
    (repo / "tools" / "sub" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "tools/sub/run.sh", "tools/sub/deep/run.sh")
    write("tools/sub/deep/run.sh", script_text.replace("../../tools/build.sh", "../tools/build.sh"))
    script_ps2 = commit("script move")
    result = changed(181, [patch(1, script_ps1, base, approvals=[approval("+1")]), patch(2, script_ps2, script_ps1)])
    check("triage move: a ../ change in a script is not move-only", result.get("ps_kind") != "move-only", result)
    prose_text = "# page\n\nRun `../../tools/build.sh` first.\n" + ("same\n" * 12)
    git(repo, "checkout", "-q", "-B", "move-prose", base)
    write("docs/prose/page.md", prose_text)
    prose_ps1 = commit("prose source")
    (repo / "docs" / "prose" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "docs/prose/page.md", "docs/prose/deep/page.md")
    write("docs/prose/deep/page.md", prose_text.replace("../../tools/build.sh", "../tools/build.sh"))
    prose_ps2 = commit("prose move")
    result = changed(182, [patch(1, prose_ps1, base, approvals=[approval("+1")]), patch(2, prose_ps2, prose_ps1)])
    check("triage move: a ../ change in Markdown prose is not move-only", result.get("ps_kind") != "move-only", result)
    # Release review: LINK_DEPTH matched `](../` inside a fenced block too, so
    # an edited code sample folded away. Real link depth outside it still folds.
    fence_text = "# page\n\n[up](../../up.md)\n\n```md\n[see](../../docs/a.md)\n```\n" + ("same\n" * 12)
    git(repo, "checkout", "-q", "-B", "move-fence", base)
    write("docs/fence/page.md", fence_text)
    fence_ps1 = commit("fence source")
    (repo / "docs" / "fence" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "docs/fence/page.md", "docs/fence/deep/page.md")
    write("docs/fence/deep/page.md", fence_text.replace("../../", "../../../"))
    fence_ps2 = commit("fence move")
    result = changed(190, [patch(1, fence_ps1, base, approvals=[approval("+1")]), patch(2, fence_ps2, fence_ps1)])
    check("triage move: a ../ change inside a Markdown code fence is not move-only",
          result.get("ps_kind") != "move-only", result)
    write("docs/fence/deep/page.md", fence_text.replace("[up](../../up.md)", "[up](../../../up.md)"))
    fence_ps3 = commit("fence move, code kept")
    result = changed(191, [patch(1, fence_ps1, base, approvals=[approval("+1")]), patch(2, fence_ps3, fence_ps1)])
    check("triage move: link depth outside the fence still folds to move-only",
          result.get("ps_kind") == "move-only", result)
    for number, label, sample in ((193, "inline code", "Write `[see](../../docs/a.md)` here.\n"),
                                  (194, "an indented code block", "    [see](../../docs/a.md)\n"),
                                  (195, "a fence info string", "```md [see](../../docs/a.md)\nbody\n```\n")):
        code_text = "# page\n\n" + sample + "\n" + ("same\n" * 12)
        git(repo, "checkout", "-q", "-B", "move-code-%s" % number, base)
        write("docs/code%s/page.md" % number, code_text)
        code_ps1 = commit("code source")
        (repo / "docs" / ("code%s" % number) / "deep").mkdir(parents=True, exist_ok=True)
        git(repo, "mv", "docs/code%s/page.md" % number, "docs/code%s/deep/page.md" % number)
        write("docs/code%s/deep/page.md" % number, code_text.replace("../../", "../../../"))
        code_ps2 = commit("code move")
        result = changed(number, [patch(1, code_ps1, base, approvals=[approval("+1")]), patch(2, code_ps2, code_ps1)])
        check("triage move: a ../ change inside %s is not move-only" % label, result.get("ps_kind") != "move-only", result)

    # Round-5 review #3 and #5: a move that also changes the mode, or moves a
    # symlink (same target text, different meaning), is not move-only.
    git(repo, "checkout", "-q", "-B", "move-chmod", base)
    write("tools/chmod/tool.sh", script_text)
    chmod_ps1 = commit("chmod source")
    (repo / "tools" / "chmod" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "tools/chmod/tool.sh", "tools/chmod/deep/tool.sh")
    (repo / "tools" / "chmod" / "deep" / "tool.sh").chmod(0o755)
    git(repo, "add", "-A")
    chmod_ps2 = commit("chmod move")
    result = changed(183, [patch(1, chmod_ps1, base, approvals=[approval("+1")]), patch(2, chmod_ps2, chmod_ps1)])
    check("triage move: a rename with a mode change is not move-only",
          result.get("ps_kind") != "move-only"
          and any("file mode changed" in flag for flag in result.get("flags", [])), result)
    git(repo, "checkout", "-q", "-B", "move-symlink", base)
    (repo / "links" / "old").mkdir(parents=True, exist_ok=True)
    os.symlink("../ok.md", repo / "links" / "old" / "link.md")
    git(repo, "add", "-A")
    symlink_ps1 = commit("symlink source")
    (repo / "links" / "old" / "deep").mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "links/old/link.md", "links/old/deep/link.md")
    symlink_ps2 = commit("symlink move")
    result = changed(184, [patch(1, symlink_ps1, base, approvals=[approval("+1")]), patch(2, symlink_ps2, symlink_ps1)])
    check("triage move: a moved symlink is not move-only",
          result.get("ps_kind") != "move-only"
          and any("not a regular file" in flag for flag in result.get("flags", [])), result)

    # Round-5 review #7: a same-path chmod between amended patch sets has no
    # hunk lines, so only the mode in the signature puts it in the delta.
    git(repo, "checkout", "-q", "-B", "chmod-amend", base)
    write("tools/amend.sh", script_text)
    chmod_amend_ps1 = commit("chmod amend one")
    (repo / "tools" / "amend.sh").chmod(0o755)
    git(repo, "add", "-A")
    chmod_amend_ps2 = commit("chmod amend two")
    git(repo, "checkout", "-q", "-B", "chmod-amend-2", base)
    write("tools/amend.sh", script_text)
    (repo / "tools" / "amend.sh").chmod(0o755)
    git(repo, "add", "-A")
    chmod_amend_amended = commit("chmod amend, amended")
    result = changed(185, [patch(1, chmod_amend_ps1, base, approvals=[approval("+1")]),
                           patch(2, chmod_amend_amended, base)])
    check("triage delta: a chmod between amended patch sets is in the delta",
          "tools/amend.sh" in (result.get("delta_files") or []), result)
    check("triage delta: a chmod between amended patch sets is flagged, not left to a line count",
          any("tools/amend.sh: file mode changed 100644 -> 100755" in flag for flag in result.get("flags") or []),
          result)
    git(repo, "checkout", "-q", "-B", "symlink-add", base)
    os.symlink("../ok.md", repo / "links" / "added-link.md")
    git(repo, "add", "-A")
    symlink_add = commit("symlink add")
    result = changed(192, [patch(1, symlink_add, base)])
    check("triage delta: an added symlink is flagged as not a regular file",
          any("links/added-link.md: not a regular file (mode 120000)" in flag for flag in result.get("flags") or []),
          result)

    # Round-5 review #8: porcelain `git diff` applies a textconv driver; the
    # delta must be the stored bytes, not the driver's rendering.
    git(repo, "checkout", "-q", "-B", "textconv", base)
    upper = tmp / "upper-textconv.sh"
    upper.write_text('#!/bin/sh\ntr a-z A-Z < "$1"\n')  # git passes the file path
    upper.chmod(0o755)
    git(repo, "config", "diff.upper.textconv", str(upper))
    write(".gitattributes", "*.conv diff=upper\n")
    write("docs/value.conv", "abc\n")
    textconv_ps1 = commit("textconv one")
    write("docs/value.conv", "abd\n")
    textconv_ps2 = commit("textconv two")
    conv_entries = triage_module._diff_entries(repo, textconv_ps1, textconv_ps2)
    conv_added = [line for entry in conv_entries if entry["new"] == "docs/value.conv" for line in entry["added"]]
    conv_between = triage_module._between_lines(repo, textconv_ps1, textconv_ps2, conv_entries)
    git(repo, "config", "--unset", "diff.upper.textconv")
    check("triage diff: a textconv driver does not change what is compared", conv_added == ["abd"], conv_added)
    check("triage delta: a textconv driver does not change the lines between patch sets",
          conv_between.get("docs/value.conv", ([], []))[0] == ["abd"], conv_between)

    # Round-5 review #4: a HIGH risk floor is never settled by the small-delta
    # shortcut. The trigger keyword arrives in a one-line change after my vote.
    git(repo, "checkout", "-q", "-B", "high-small", base)
    write("docs/spec/high.md", "draft\n")
    high_ps1 = commit("high one")
    write("docs/spec/high.md", "Status Verified\n")
    high_ps2 = commit("high two")
    result = changed(186, [patch(1, high_ps1, base, approvals=[approval("+1")]), patch(2, high_ps2, high_ps1)])
    check("triage legs: a HIGH risk floor is never own-read",
          result.get("risk_floor") == "HIGH" and result.get("legs") == "roster", result)

    # Round-5 review #6: a later-numbered patch set stamped BEFORE the replayed
    # one cannot end the replay and erase feedback given on it.
    result = changed(187, [patch(1, owner_small_ps1, base, created=10,
                                 approvals=[approval("-1", 15, who="bob")]),
                           patch(2, owner_small_ps2, owner_small_ps1, created=5)],
                     owner="alice", args=("--as-of-ps", "1"))
    check("triage as-of: a later patch set stamped earlier does not end the replay",
          any(rule["rule"] == "owner-fix-round" for rule in result.get("fired_rules", [])), result)

    # Fix round 2 F8: a hunk that quotes Git's binary-file sentence is plain
    # content, not a binary delta marker.
    git(repo, "checkout", "-q", "-B", "binary-words", base)
    write("docs/content.md", "ordinary\n")
    binary_words_ps1 = commit("binary words source")
    write("docs/content.md", "git says: Binary files a/foo and b/bar differ\n")
    binary_words_ps2 = commit("binary words content")
    result = changed(165, [patch(1, binary_words_ps1, base, approvals=[approval("+1")]),
                           patch(2, binary_words_ps2, binary_words_ps1)])
    check("triage binary F8: quoted binary header content is not binary",
          not any(flag == "docs/content.md: binary" for flag in result.get("flags", [])), result)

    # Fix round 2 F10: Git's NUL-separated name-status stream preserves a
    # non-UTF-8 filename through subprocess argument re-encoding.
    check("triage paths F10: decode uses surrogateescape",
          triage_module._decoded(b"docs/\xff.md") == os.fsdecode(b"docs/\xff.md"))

    # Review test gap: change (not only scope) applies path_only triggers.
    git(repo, "checkout", "-q", "-B", "path-only-change", base)
    write("secure/token.txt", "changed\n")
    path_only = commit("path only trigger")
    result = changed(166, [patch(1, path_only, base)])
    check("triage change: path_only trigger raises risk", result.get("risk_floor") == "HIGH", result)

    # 0.6.23: fetching a patch set that is missing locally adds objects and
    # nothing else. FETCH_HEAD belongs to whoever uses the clone.
    fh_origin, fh_work, fh_clone = tmp / "fh-origin.git", tmp / "fh-work", tmp / "fh-clone"
    git(tmp, "init", "-q", "--bare", str(fh_origin))
    git(tmp, "init", "-q", str(fh_work))
    git(fh_work, "config", "user.email", "test@example.invalid")
    git(fh_work, "config", "user.name", "Test")
    (fh_work / "a.txt").write_text("one\n")
    git(fh_work, "add", "-A")
    git(fh_work, "commit", "-qm", "one")
    git(fh_work, "push", "-q", str(fh_origin), "HEAD:refs/heads/main")
    git(tmp, "clone", "-q", str(fh_origin), str(fh_clone))
    (fh_work / "a.txt").write_text("two\n")
    git(fh_work, "commit", "-qam", "two")
    fh_revision = git(fh_work, "rev-parse", "HEAD").stdout.strip()
    git(fh_work, "push", "-q", str(fh_origin), "HEAD:refs/changes/01/1001/1")
    fh_head = fh_clone / ".git" / "FETCH_HEAD"
    fh_head.write_text("sentinel: the clone owner's FETCH_HEAD\n")
    # A clone that maps refs/changes/* would store the fetched ref unless the
    # configured refmap is switched off for this fetch.
    git(fh_clone, "config", "--add", "remote.origin.fetch", "+refs/changes/*:refs/remotes/origin/changes/*")
    git(fh_work, "tag", "fh-tag")
    git(fh_work, "push", "-q", str(fh_origin), "refs/tags/fh-tag")
    fh_refs = git(fh_clone, "for-each-ref", "--format=%(refname) %(objectname)").stdout
    triage_module._ensure_revision(fh_clone, "1001", "1", fh_revision)
    fetched = run("git", "-C", str(fh_clone), "cat-file", "-e", fh_revision + "^{commit}").returncode == 0
    check("triage fetch: a missing patch set is fetched into the clone", fetched, fh_revision)
    check("triage fetch: the clone's FETCH_HEAD is left alone",
          fh_head.read_text() == "sentinel: the clone owner's FETCH_HEAD\n", fh_head.read_text())
    fh_refs_after = git(fh_clone, "for-each-ref", "--format=%(refname) %(objectname)").stdout
    check("triage fetch: no ref or tag is created, even with a refspec mapping refs/changes",
          fh_refs_after == fh_refs, fh_refs_after)

    # 0.6.25: an optional risk x lens-class effort table. A cell is an abstract
    # tier; each adapter's spelling comes from launch.json.
    table = {"LOW": {"mechanical": "medium", "judgment": "medium"},
             "MEDIUM": {"mechanical": "medium", "judgment": "high"},
             "HIGH": {"mechanical": "xhigh", "judgment": "xhigh"}}
    expect_bad("effort-missing-cell", lambda doc: doc.update({"effort": {**table, "LOW": {"mechanical": "medium"}}}),
               "effort.LOW.judgment")
    expect_bad("effort-bad-tier", lambda doc: doc.update({"effort": {**table, "HIGH": {"mechanical": "xhigh",
                                                                                          "judgment": "ultra"}}}),
               "effort.HIGH.judgment")
    expect_bad("effort-unknown-class", lambda doc: doc.update({"effort": {**table, "LOW": {
        "mechanical": "medium", "judgment": "medium", "vibes": "low"}}}), "vibes")
    expect_bad("effort-unknown-risk", lambda doc: doc.update({"effort": {**table, "CRITICAL": {}}}), "CRITICAL")
    with_table = json.loads(json.dumps(config))
    with_table["effort"] = table
    table_file = tmp / "triage-effort.json"
    _write_doc(table_file, with_table)
    check("triage check: a full effort table passes", checked(table_file).returncode == 0, checked(table_file).stdout)
    plain_result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)])
    check("triage effort: without a table, review legs carry no effort fields",
          plain_result.get("review_legs") and not any(k.startswith("effort") for leg in plain_result["review_legs"]
                                                      for k in leg), plain_result)
    table_env = _roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=table_file)
    table_result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)],
                           test_env=table_env)
    check("triage effort: with a table, every review leg carries effort_in",
          table_result.get("review_legs") and all("effort_in" in leg for leg in table_result["review_legs"]),
          table_result)

    floor = triage_module._effort_floor
    cfg = {"effort": table}
    check("triage effort: the floor takes the higher cell across lens classes",
          floor(cfg, "MEDIUM", {"mechanical", "judgment"}) == "high"
          and floor(cfg, "MEDIUM", {"mechanical"}) == "medium", None)
    check("triage effort: no floor for inherit, or without a table",
          floor(cfg, "inherit", {"judgment"}) is None and floor({}, "HIGH", {"judgment"}) is None, None)
    per_adapter = {**table, "MEDIUM": {"mechanical": "medium", "judgment": {"default": "high", "codex": "xhigh"}}}
    check("triage effort: a per-adapter cell raises only the adapter it names",
          floor({"effort": per_adapter}, "MEDIUM", {"judgment"}, "codex") == "xhigh"
          and floor({"effort": per_adapter}, "MEDIUM", {"judgment"}, "opencode") == "high"
          and floor({"effort": per_adapter}, "MEDIUM", {"mechanical"}, "codex") == "medium", None)
    expect_bad("effort-cell-no-default", lambda doc: doc.update({"effort": {**table, "MEDIUM": {
        "mechanical": "medium", "judgment": {"codex": "xhigh"}}}}), "effort.MEDIUM.judgment.default")
    expect_bad("effort-cell-unknown-adapter", lambda doc: doc.update({"effort": {**table, "MEDIUM": {
        "mechanical": "medium", "judgment": {"default": "high", "codexx": "xhigh"}}}}), "codexx")
    expect_bad("effort-cell-bad-tier", lambda doc: doc.update({"effort": {**table, "MEDIUM": {
        "mechanical": "medium", "judgment": {"default": "high", "codex": "ultra"}}}}), "effort.MEDIUM.judgment.codex")
    per_file = tmp / "triage-effort-per-adapter.json"
    per_doc = json.loads(json.dumps(config)); per_doc["effort"] = per_adapter
    _write_doc(per_file, per_doc)
    check("triage check: a per-adapter cell with a default passes", checked(per_file).returncode == 0,
          checked(per_file).stdout)
    # Change 129 is a HIGH floor with both lens classes; an override on HIGH
    # for codex alone shows up as table-override on that leg only.
    high_override = json.loads(json.dumps(per_doc))
    high_override["effort"]["HIGH"] = {"mechanical": "xhigh", "judgment": {"default": "xhigh", "codex": "max"}}
    high_file = tmp / "triage-effort-high-override.json"
    _write_doc(high_file, high_override)
    per_result = changed(129, [patch(1, trigger_old, base, approvals=[approval("+1")]), patch(2, large, small)],
                         test_env=_roster_env(tmp, DEV_LEAD_ROSTER=roster_file, DEV_LEAD_TRIAGE=high_file))
    by_adapter = {leg["adapter"]: leg for leg in per_result.get("review_legs", [])}
    check("triage effort: only the overridden adapter's leg says table-override",
          by_adapter.get("codex", {}).get("effort") == "max"
          and by_adapter["codex"].get("effort_source") == "table-override"
          and all(leg.get("effort_source") != "table-override" for name, leg in by_adapter.items() if name != "codex"),
          per_result)
    other = {**table, "MEDIUM": {"mechanical": "medium", "judgment": {"default": "medium", "opencode": "xhigh"}}}
    check("triage effort: a model_suffix or config_only adapter not named in the cell gets the default",
          floor({"effort": other}, "MEDIUM", {"judgment"}, "agy") == "medium"
          and floor({"effort": other}, "MEDIUM", {"judgment"}, "codex") == "medium"
          and floor({"effort": other}, "MEDIUM", {"judgment"}, "opencode") == "xhigh", None)
    overridden = triage_module._effort_overridden
    check("triage effort: an override that raised the floor is reported as table-override",
          overridden({"effort": per_adapter}, "MEDIUM", {"judgment"}, "codex", "xhigh")
          and not overridden({"effort": per_adapter}, "MEDIUM", {"judgment"}, "opencode", "high")
          and not overridden({"effort": per_adapter}, "MEDIUM", {"mechanical"}, "codex", "medium"), None)
    same = {**table, "MEDIUM": {"mechanical": "medium", "judgment": {"default": "high", "codex": "high"}}}
    check("triage effort: an override equal to the default is not reported as an override",
          not overridden({"effort": same}, "MEDIUM", {"judgment"}, "codex", "high"), None)
    both = {**table, "MEDIUM": {"mechanical": {"default": "high", "codex": "xhigh"}, "judgment": "xhigh"}}
    check("triage effort: an override another class already matched is not reported as an override",
          not overridden({"effort": both}, "MEDIUM", {"mechanical", "judgment"}, "codex", "xhigh"), None)
    expect_bad("effort-cell-lowering", lambda doc: doc.update({"effort": {**table, "MEDIUM": {
        "mechanical": "medium", "judgment": {"default": "xhigh", "codex": "medium"}}}}),
               "must not be below the cell's default")
    expect_bad("effort-cell-list-default", lambda doc: doc.update({"effort": {**table, "MEDIUM": {
        "mechanical": "medium", "judgment": {"default": [], "codex": "xhigh"}}}}), "effort.MEDIUM.judgment.default")
    check("triage effort: a concrete risk with no lens takes the mechanical cell",
          floor(cfg, "HIGH", set()) == "xhigh" and floor(cfg, "MEDIUM", set()) == "medium", None)
    leg_effort = triage_module._leg_effort
    def one(adapter, model, wanted, declared=None):
        entry = {"adapter": adapter, "model": model, "family": "x", "_declared_effort": declared}
        return entry, leg_effort(entry, wanted)
    entry, got = one("opencode", "opencode/m", "high", "xhigh")
    check("triage effort: the table never lowers a flag leg's roster effort",
          got == {"effort": "xhigh", "effort_in": "flag", "effort_source": "roster"}, got)
    entry, got = one("opencode", "opencode/m", "max", "xhigh")
    check("triage effort: the table raises a flag leg to a known word",
          got == {"effort": "max", "effort_in": "flag", "effort_source": "table"}, got)
    entry, got = one("grok", "grok-4", "medium", None)
    check("triage effort: a flag word the menu lacks goes up to the next known one",
          got["effort"] == "high" and got["effort_source"] == "table", got)
    entry, got = one("agy", "gemini-3.8-flash-medium", "high")
    check("triage effort: a model_suffix leg moves to a known variant",
          got == {"effort": "high", "effort_in": "model_name", "effort_source": "table"}
          and entry["model"] == "gemini-3.8-flash-high", (got, entry))
    entry, got = one("agy", "gemini-3.8-flash-high", "xhigh")
    check("triage effort: an unknown variant is never emitted; effort_unmet instead",
          entry["model"] == "gemini-3.8-flash-high" and got.get("effort") == "high"
          and got.get("effort_unmet") == {"wanted": "xhigh", "reason": "variant not known for this adapter"},
          (got, entry))
    entry, got = one("cursor", "kimi-k3-low", "medium")
    check("triage effort: a gap in a known menu goes up to the next variant",
          entry["model"] == "kimi-k3-high" and got.get("effort") == "high", (got, entry))
    # 0.6.27: three edges a review leg found in 0.6.25.
    entry, got = one("opencode", "opencode/m", "high", "ultra")
    check("triage effort: a roster effort off the ladder is kept, never replaced by the table's tier",
          got == {"effort": "ultra", "effort_in": "flag", "effort_source": "roster",
                  "effort_unmet": {"wanted": "high", "reason": "roster effort 'ultra' is not on the ladder; kept"}}, got)
    entry, got = one("opencode", "opencode/m", "high", ["high"])
    check("triage effort: a non-string roster effort does not crash the engine",
          isinstance(got, dict) and got.get("effort") == "high", got)
    entry, got = one("cursor", "cursor-grok-4.6-high-fast", "medium")
    check("triage effort: a -fast twin is read as its tier",
          got == {"effort": "high", "effort_in": "model_name", "effort_source": "roster"}
          and entry["model"] == "cursor-grok-4.6-high-fast", (got, entry))
    entry, got = one("cursor", "cursor-grok-4.6-high-fast", "xhigh")
    check("triage effort: a -fast twin with no known raised twin keeps its model and says so",
          entry["model"] == "cursor-grok-4.6-high-fast" and got.get("effort") == "high"
          and got.get("effort_unmet") == {"wanted": "xhigh", "reason": "variant not known for this adapter"},
          (got, entry))
    real_data = triage_module.roster.data
    def fast_data():
        launch, families = real_data()
        launch = json.loads(json.dumps(launch))
        launch["cursor"]["effort"]["examples"].append("cursor-grok-4.6-xhigh-fast")
        return launch, families
    triage_module.roster.data = fast_data
    try:
        entry, got = one("cursor", "cursor-grok-4.6-high-fast", "xhigh")
    finally:
        triage_module.roster.data = real_data
    check("triage effort: raising a -fast twin keeps the twin",
          entry["model"] == "cursor-grok-4.6-xhigh-fast" and got.get("effort") == "xhigh", (got, entry))
    entry, got = one("claude", "claude-sonnet", "high")
    check("triage effort: claude's review role takes a flag since 0.6.35, so the table reaches it",
          got == {"effort": "high", "effort_in": "flag", "effort_source": "table"}, got)
    real_data_none = triage_module.roster.data
    def none_data():
        launch, families = real_data_none()
        launch = json.loads(json.dumps(launch))
        launch["claude"]["effort"] = {"mechanism": "none"}
        return launch, families
    triage_module.roster.data = none_data
    try:
        entry, got = one("claude", "claude-sonnet", "high")
    finally:
        triage_module.roster.data = real_data_none
    check("triage effort: an adapter the suite passes no effort for says so",
          got == {"effort": None, "effort_in": "none"}, got)
    # The REVIEW role's mechanism, not the adapter-wide one: a flag scoped to
    # implement leaves a review argv without {EFFORT} at none.
    def impl_scoped_data():
        launch, families = real_data_none()
        launch = json.loads(json.dumps(launch))
        launch["claude"]["effort"]["applies_to_role"] = "implement"
        launch["claude"]["role"]["review"]["argv"] = [t for t in launch["claude"]["role"]["review"]["argv"]
                                                      if t not in ("--effort", "{EFFORT}")]
        return launch, families
    triage_module.roster.data = impl_scoped_data
    try:
        entry, got = one("claude", "claude-sonnet", "high")
    finally:
        triage_module.roster.data = real_data_none
    check("triage effort: a flag scoped to another role leaves the review leg at none",
          got == {"effort": None, "effort_in": "none"}, got)
    # config_only is the pre-0.6.28 codex review; exercise it on the legacy data.
    real_data_cfg = triage_module.roster.data
    legacy_path = _legacy_launch(tmp)
    def legacy_data():
        launch, families = real_data_cfg()
        return json.loads(legacy_path.read_text(encoding="utf-8")), families
    triage_module.roster.data = legacy_data
    codex_home = tmp / "codex-home"
    (codex_home / ".codex").mkdir(parents=True, exist_ok=True)
    (codex_home / ".codex" / "config.toml").write_text('model_reasoning_effort = "medium"\n')
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(codex_home)
    try:
        entry, low = one("codex", "gpt-6-luna", "low")
        entry, high = one("codex", "gpt-6-luna", "high")
    finally:
        os.environ["HOME"] = saved_home
    (codex_home / ".codex" / "config.toml").write_text('model_reasoning_effort = "ultra"\n')
    os.environ["HOME"] = str(codex_home)
    try:
        entry, ultra = one("codex", "gpt-6-luna", "xhigh")
    finally:
        os.environ["HOME"] = saved_home
    (codex_home / ".codex" / "config.toml").write_text('model_reasoning_effort = "medium"\n')
    check("triage effort: a machine effort off the ladder is kept, not reported as a mismatch",
          ultra.get("effort") == "ultra" and "config_mismatch" not in ultra
          and ultra.get("effort_unmet", {}).get("wanted") == "xhigh", ultra)
    check("triage effort: config_only keeps the machine's effort when it meets the floor",
          low == {"effort": "medium", "effort_in": "config_only", "effort_source": "roster"}, low)
    check("triage effort: config_only above the machine's effort reports the mismatch, never edits",
          high.get("effort") == "high" and high.get("config_mismatch", {}).get("config") == "medium"
          and "model_reasoning_effort=high" in high["config_mismatch"]["launch"]
          and (codex_home / ".codex" / "config.toml").read_text() == 'model_reasoning_effort = "medium"\n', high)
    triage_module.roster.data = real_data_cfg

    # Review test gap: a real Git failure is an input error with stderr, not a
    # traceback or a successful empty result.
    broken_query = query(167, [patch(1, asof_ps1, "missing-parent")])
    got = run(triage, "change", "167", "--query-json", broken_query, env=env)
    check("triage git failure: exits 2 and names the missing parent and ref",
          got.returncode == 2 and "missing-parent" in got.stderr
          and "refs/changes/67/167/1" in got.stderr and not got.stdout, got.stdout + got.stderr)
    got = run(triage, "change", "--help", env=env)
    check("triage as-of help: says my replayed actions are excluded on purpose",
          got.returncode == 0 and "excluded on purpose" in got.stdout, got.stdout + got.stderr)


# ------------------------------------------------ line-ending renormalization ----


def _crlf_repo(path):
    """A repo that committed CRLF files and LATER added `eol=lf`: every fresh
    checkout shows them ` M` with bytes identical to the commit (a peer session,
    2026-09-22, an SDK repository: 18 files)."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    # a runner whose global autocrlf=true would store LF and void the fixture
    git(path, "config", "core.autocrlf", "false")
    (path / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    (path / "sp ace.txt").write_bytes(b"c\r\n")
    # non-ASCII: porcelain escapes it unless core.quotePath=false (review, 2026-09-22)
    (path / "\u6587\u6a94.txt").write_bytes(b"d\r\n")
    (path / "plain.txt").write_text("x\n")
    (path / "run.sh").write_text("echo\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "crlf")
    (path / ".gitattributes").write_text("* text eol=lf\n")
    git(path, "add", ".gitattributes")
    git(path, "commit", "-qm", "attrs")
    return git(path, "rev-parse", "HEAD").stdout.strip()


def test_renorm(tmp):
    freeze, verify = SCRIPTS / "freeze-target.sh", SCRIPTS / "verify-target.sh"
    renorm = SCRIPTS / "renorm-only.sh"
    repo = tmp / "crlf-repo"
    sha = _crlf_repo(repo)
    dest = tmp / "crlf-frozen"

    r = run(freeze, repo, sha, dest)
    check("renorm: freeze accepts a checkout dirty ONLY by line-ending renormalization",
          r.returncode == 0 and r.stdout.strip() == sha, r.stderr)
    # Which renormalized files `git status` flags right after a checkout is
    # racy (same-second stat): a file whose cached stat still matches is not
    # re-read. So the NOTE must name only excusable files, and at least one.
    renorm_set = {"crlf.txt", "sp ace.txt", "\u6587\u6a94.txt"}
    # Only the indented lines under the NOTE header: git may print its own
    # "Preparing worktree ..." line first (it does on the CI runner).
    err_lines = r.stderr.splitlines()
    at = next((i for i, line in enumerate(err_lines) if "freeze-target: NOTE" in line), len(err_lines))
    noted = set()
    for line in err_lines[at + 1:]:
        if not line.startswith("  ") or not line.strip():
            break
        noted.add(line.strip())
    check("renorm: ...and says which files it excused",
          noted and noted <= renorm_set, r.stderr)
    # through a SYMLINKED entrypoint the sibling helper must still be found
    linkdir = tmp / "bin"
    linkdir.mkdir()
    (linkdir / "freeze-target.sh").symlink_to(freeze)
    (linkdir / "verify-target.sh").symlink_to(verify)
    r = run(verify.parent / "verify-target.sh", dest, sha)
    rl = run(linkdir / "verify-target.sh", dest, sha)
    check("renorm: verify via a symlink finds renorm-only.sh", rl.returncode == 0, rl.stderr)
    rl = run(linkdir / "freeze-target.sh", repo, sha, tmp / "crlf-frozen-via-link")
    check("renorm: freeze via a symlink finds renorm-only.sh", rl.returncode == 0, rl.stderr)
    # Whether git re-reads a file after a fresh checkout depends on its stat
    # matching the index (racy same-second timestamps). Moving every fixture
    # file's mtime forward, bytes untouched, makes each stat mismatch the
    # index, so git compares content for all of them and the list is exact.
    later = time.time() + 5
    for name in ("crlf.txt", "sp ace.txt", "\u6587\u6a94.txt", "plain.txt", "run.sh"):
        os.utime(dest / name, (later, later))
    r = run(renorm, dest)
    check("renorm: the list is exactly the byte-identical files, spaces and non-ASCII included",
          sorted(r.stdout.splitlines()) == ["crlf.txt", "sp ace.txt", "\u6587\u6a94.txt"], r.stdout)
    r = run(verify, dest, sha)
    check("renorm: verify certifies the same tree", r.returncode == 0, r.stderr)

    # the excuse is BYTE identity -- any real edit to an excused file fails
    (dest / "crlf.txt").write_bytes(b"a\r\nB\r\n")
    r = run(verify, dest, sha)
    check("renorm: an edit to an excused file is refused", r.returncode != 0, r.stdout)
    (dest / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    # a CR-only edit is exactly what --ignore-cr-at-eol would have excused
    (dest / "sp ace.txt").write_bytes(b"c\n")
    r = run(verify, dest, sha)
    check("renorm: a line-ending-shaped edit is NOT excused (bytes, not CR-blind)",
          r.returncode != 0, r.stdout)
    (dest / "sp ace.txt").write_bytes(b"c\r\n")
    # a mode change keeps the bytes and must still fail
    os.chmod(dest / "run.sh", 0o755)
    r = run(verify, dest, sha)
    check("renorm: a mode change is never excused", r.returncode != 0, r.stdout)
    os.chmod(dest / "run.sh", 0o644)
    r = run(verify, dest, sha)
    check("renorm: restored tree certifies again", r.returncode == 0, r.stderr)
    # only ` M` is excused: the same bytes STAGED are a change to the index
    git(dest, "add", "crlf.txt")
    r = run(verify, dest, sha)
    check("renorm: a staged renormalization is not excused", r.returncode != 0, r.stdout)


def test_freeze_refusal_cleans_up(tmp):
    """A refusal must not leave the worktree registered behind it (a peer session,
    2026-09-22): the caller gets no SHA, so nothing else will remove it, and
    the next freeze to that path dies on "already exists"."""
    freeze = SCRIPTS / "freeze-target.sh"
    repo = tmp / "smudge-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("a\n")
    (repo / ".gitattributes").write_text("f.txt filter=mangle\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c")
    # a smudge that changes the bytes: a checkout that is dirty for real
    git(repo, "config", "filter.mangle.smudge", "sed s/a/b/")
    git(repo, "config", "filter.mangle.clean", "cat")
    dest = tmp / "smudge-frozen"
    r = run(freeze, repo, "HEAD", dest)
    check("freeze: a really dirty fresh checkout is still refused", r.returncode != 0, r.stdout)
    check("freeze: ...naming what was dirty", "f.txt" in r.stderr, r.stderr)
    check("freeze: ...and the refused worktree is gone from disk", not dest.exists())
    listed = run("git", "-C", repo, "worktree", "list", "--porcelain").stdout
    check("freeze: ...and from git's worktree list", str(dest) not in listed, listed)

    # A failed ADD must remove nothing: the path may be held by someone else.
    # Stale registration (dir deleted, registration kept) -- the -e guard passes,
    # the add fails, and the other registration must survive (review, 2026-09-22;
    # the same rule covers two concurrent freezes to one path).
    other = tmp / "held-by-someone-else"
    git(repo, "worktree", "add", "-q", "--detach", str(other), "HEAD")
    shutil.rmtree(other)
    r = run(freeze, repo, "HEAD", other)
    check("freeze: a failed add is refused", r.returncode != 0, r.stdout)
    listed = run("git", "-C", repo, "worktree", "list", "--porcelain").stdout
    check("freeze: ...and leaves the other registration alone", str(other) in listed, listed)


# ---------------------------------------------------------- leg-log-check ----
def test_leg_log_check(tmp):
    """Silent failed legs (a peer session, 2026-09-22): a refused read or a timeout
    left a log that looked like a finished run once the wrapper appended exit=0.
    The check is POSITIVE evidence -- a verdict word the brief demanded -- because
    subtracting known noise lost to every error shape three review legs built."""
    script = SCRIPTS / "leg-log-check.sh"
    report = "## Claim 1 -- HOLDS\nsrc/x.py:12 quoted code.\n## Claim 2 -- BROKEN\ntrigger -> consequence\n"
    long_err = ("ResourceExhausted: 429 Resource has been exhausted (e.g. check quota). "
                "Quota exceeded for quota metric 'GenerateContent requests'.\n") * 8
    cases = [
        # (label, adapter, body or None, expected exit)
        ("missing file", "opencode", None, 1),
        ("empty file", "opencode", "", 1),
        ("timeout: logs and exit=124 only", "opencode", "timestamp=1 INFO start\nexit=124\n", 1),
        ("refused read, nothing after", "opencode",
         "timestamp=1 INFO x\nError: The user rejected permission to use this specific tool call.\nexit=0\n", 1),
        ("a long error dump is not a review (agy quota)", "agy", long_err, 1),
        ("agy's own denial line", "agy",
         'permission check failed for command "node -e 1": user denied permission to run command\n' * 8, 1),
        ("a real report", "opencode", "timestamp=1 INFO x\n" + report + "exit=0\n", 0),
        ("a real report quoting errors and trace markers", "agy",
         "\u2717 Error: TypeError at x.js:3 is unhandled -- BROKEN\n\u2192 fix it\n$VAR unquoted\n", 0),
        ("a refusal the leg worked around, then a report", "opencode",
         "Error: The user rejected permission to use this specific tool call.\n" + report, 0),
        ("a report quoting the refusal words mid-line", "opencode",
         "The leg ends on `auto-rejecting` and a rejected permission.\n## Claim 1 -- HOLDS\n", 0),
        ("an enumerated verdict (**A. FIXED.**)", "cursor-free", "**A. FIXED.** quoted at x.py:3\n", 0),
        ("codex: a Verdict line", "codex", "# Codex Adversarial Review\n\nVerdict: approve\n", 0),
        ("an error sentence containing a verdict word", "opencode",
         "Error: review stream BROKEN before any claims were evaluated\n" * 3, 1),
        ("codex: a Verdict line that is not a verdict", "codex",
         "# Codex Adversarial Review\n\nVerdict: could not be determined (rate limit)\n", 1),
        ("NOT-REACHED spelled with a hyphen", "agy", "## Claim 1 -- NOT-REACHED\n", 0),
        ("an error after a bare colon", "opencode", "Error: BROKEN pipe\nexit=0\n", 1),
        ("bracketed verdicts", "agy", "## Claim 1 -- [HOLDS]\n## Claim 2 -- [BROKEN]\n", 0),
        ("a plain Status: label", "agy", "Claim 1\nStatus: BROKEN\n", 0),
        ("codex: the usage-limit failure", "codex",
         "# Codex Adversarial Review\n\nCodex did not return valid structured JSON.\n", 1),
    ]
    logs = {}
    for label, adapter, body, want in cases:
        log = tmp / ("llc-" + str(len(logs)))
        logs[label] = log
        if body is not None:
            log.write_text(body)
        r = run(script, adapter, log)
        check(f"leg-log-check: {label} -> exit {want}", r.returncode == want,
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")
    r = run(script, "opencode", logs["a refusal the leg worked around, then a report"])
    check("leg-log-check: a worked-around refusal is still WARNED about", "WARNING" in r.stderr, r.stderr)
    r = run(script, "opencode", logs["a report quoting the refusal words mid-line"])
    check("leg-log-check: ...but mentioning the words draws no warning", "WARNING" not in r.stderr, r.stderr)
    r = run(script, "agy", logs["agy's own denial line"])
    check("leg-log-check: the agy denial is named as the cause", "refused" in r.stderr, r.stderr)
    # cursor: result objects, a banner, two objects, and an error object
    ok = {"type": "result", "subtype": "success", "is_error": False, "request_id": "r1"}
    for label, body, want in (
        ("cursor: one result object", json.dumps({**ok, "result": report}), 0),
        ("cursor: a banner line before the object",
         "cursor-agent v1 (pid 1)\n" + json.dumps({**ok, "result": report}), 0),
        ("cursor: two objects (duplicate dispatch)",
         json.dumps({**ok, "result": "tail only"}) + "\n" + json.dumps({**ok, "result": report}), 0),
        ("cursor: the verdicts are in the SHORTER of two objects",
         json.dumps({**ok, "result": report}) + "\n"
         + json.dumps({**ok, "result": "a long summary with no verdicts " * 20}), 0),
        ("cursor: an error object, however long", json.dumps({"type": "error", "message": "HOLDS " * 200}), 1),
        ("cursor: a result without a verdict", json.dumps({**ok, "result": "I could not read the files."}), 1),
        ("cursor: an is_error result carrying verdict words",
         json.dumps({**ok, "is_error": True, "result": report}), 1),
        ("cursor: a result without a success subtype",
         json.dumps({"type": "result", "request_id": "r1", "result": report}), 1),
        ("cursor: a result without a request_id",
         json.dumps({"type": "result", "result": report}), 1),
    ):
        log = tmp / ("llc-c" + str(len(logs)))
        logs[label] = log
        log.write_text(body)
        r = run(script, "cursor", log)
        check(f"leg-log-check: {label} -> exit {want}", r.returncode == want,
              f"rc={r.returncode} err={r.stderr!r}")
    r = run(script, "cursor", logs["cursor: two objects (duplicate dispatch)"])
    check("leg-log-check: a duplicate cursor dispatch is warned about", "duplicate" in r.stderr, r.stderr)
    # --expect overrides the vocabulary
    log = tmp / "llc-expect"
    log.write_text("## Findings\nVERDICT-OK\n")
    check("leg-log-check: default vocabulary refuses a custom format",
          run(script, "opencode", log).returncode == 1)
    check("leg-log-check: --expect accepts it",
          run(script, "opencode", log, "--expect", "VERDICT-OK").returncode == 0)

def main():
    for script in ("freeze-target.sh", "verify-target.sh", "snapshot-refs.sh",
                   "await-codex-job.sh", "renorm-only.sh", "leg-log-check.sh"):
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
        print("await-codex-job.sh")
        test_await_codex_job(tmp)
        print("renorm-only.sh (freeze/verify line-ending renormalization)")
        test_renorm(tmp)
        test_freeze_refusal_cleans_up(tmp)
        print("leg-log-check.sh")
        test_leg_log_check(tmp)

    print("lint.py check_paths")
    test_lint_paths()

    print("lint.py check_helper_args")
    test_lint_helper_args()

    print("lint.py check_mermaid")
    test_lint_mermaid()

    print("lint.py check_frozen_target")
    test_lint_frozen_target()

    print("lint.py check_pairing_rule")
    test_lint_pairing_rule()

    print("lint.py check_leaf_rule")
    test_lint_leaf_rule()

    print("lint.py check_launch")
    test_lint_launch()

    print("lint.py check_delegate_guardrails")
    test_lint_delegate_guardrails()

    print("lint.py check_delegate_audit_trails")
    test_lint_delegate_audit_trails()
    test_leg_cmd()

    print("roster.py")
    with tempfile.TemporaryDirectory() as td:
        test_roster(Path(td))
    print("roster.py merge gate")
    with tempfile.TemporaryDirectory() as td:
        test_merge_gate(Path(td), SCRIPTS / "roster.py")
    print("config_only effort (leg-cmd + roster check)")
    with tempfile.TemporaryDirectory() as td:
        with _legacy_launch_env(Path(td)):
            test_config_effort(Path(td))
    print("roster.py config-effort (the consented config write)")
    with tempfile.TemporaryDirectory() as td:
        with _legacy_launch_env(Path(td)):
            test_config_effort_write(Path(td))

    print("triage.py")
    with tempfile.TemporaryDirectory() as td:
        test_triage(Path(td))

    print("lint.py check_version")
    with tempfile.TemporaryDirectory() as td:
        test_lint_version(Path(td))

    print("lint.py check_version_moves_with_content")
    with tempfile.TemporaryDirectory() as td:
        test_lint_version_moves_with_content(Path(td))

    print("lint.py check_version_not_published")
    with tempfile.TemporaryDirectory() as td:
        test_lint_published_version(Path(td))

    print("release.sh")
    with tempfile.TemporaryDirectory() as td:
        test_release(Path(td))

    print("claim-audit.py")
    with tempfile.TemporaryDirectory() as td:
        test_claim_audit(Path(td))

    print("claim-audit.py parsing, range and exit codes")
    with tempfile.TemporaryDirectory() as td:
        test_claim_audit_parsing(Path(td))

    if FAILURES:
        print(f"\nFAIL — {len(FAILURES)} test(s) failed")
        sys.exit(1)
    print("\ntest_scripts: all passed")


if __name__ == "__main__":
    main()

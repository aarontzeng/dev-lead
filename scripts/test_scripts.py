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


# ------------------------------------------------ lint delegate audit trails ----
def test_lint_delegate_audit_trails():
    """check_delegate_audit_trails(): preserve run identity and tier pairing."""
    sys.path.insert(0, str(SCRIPTS))
    import lint

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
        "skills/agy-adversarial-review/SKILL.md": (
            "AGY_MODEL=gemini-3.7-flash-high\nAGY_EFFORT=high\n"
            '--model "$AGY_MODEL"\n--effort "$AGY_EFFORT"\n'
        ),
        "skills/agy-implement/SKILL.md": (
            "AGY_MODEL=gemini-3.7-flash-high\nAGY_EFFORT=high\n"
            '--model "$AGY_MODEL"\n--effort "$AGY_EFFORT"\n'
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

    text_output = dict(good)
    text_output["skills/cursor-adversarial-review/SKILL.md"] = "--output-format text\n"
    got = run_against(text_output)
    check("audit: flags Cursor text output", any("--output-format json" in e for e in got),
          f"got {got}")

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

    mismatched_tier = dict(good)
    mismatched_tier["skills/agy-adversarial-review/SKILL.md"] = (
        "AGY_MODEL=gemini-3.7-flash-high\nAGY_EFFORT=low\n"
        '--model "$AGY_MODEL"\n--effort "$AGY_EFFORT"\n'
    )
    got = run_against(mismatched_tier)
    check("audit: flags mismatched agy model and effort tiers",
          any("does not match" in e for e in got), f"got {got}")


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
    check("claim-audit: prints both anchoring questions regardless of class",
          "which test goes red" in out and "proxy for it" in out, out)
    # Named for what it asserts. The previous name said question 2 "reports"
    # the unlintable shape, which this cannot show: a missing label proves only
    # that nothing matched. What carries that sentence is question 2 printing
    # unconditionally, so assert that here rather than implying it.
    check("claim-audit: does NOT match the unlintable shape",
          "doc.md:5" not in out, out)

    clean = tmp / "clean"
    make_repo(clean, commits=1)
    cbase = git(clean, "rev-parse", "HEAD").stdout.strip()
    (clean / "plain.md").write_text("This adapter reads the job endpoint.\n")
    git(clean, "add", "-A")
    git(clean, "commit", "-qm", "neutral prose")
    r2 = run("python3", audit, clean, f"{cbase}...HEAD")
    check("claim-audit: silent when nothing risky was added",
          r2.returncode == 0 and "no absolute or sameness" in r2.stdout, r2.stdout)

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

    print("lint.py check_helper_args")
    test_lint_helper_args()

    print("lint.py check_mermaid")
    test_lint_mermaid()

    print("lint.py check_frozen_target")
    test_lint_frozen_target()

    print("lint.py check_delegate_guardrails")
    test_lint_delegate_guardrails()

    print("lint.py check_delegate_audit_trails")
    test_lint_delegate_audit_trails()

    print("lint.py check_version")
    with tempfile.TemporaryDirectory() as td:
        test_lint_version(Path(td))

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

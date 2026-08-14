#!/usr/bin/env python3
"""Tests for the shell helpers — zero dependencies, run: python3 scripts/test_scripts.py

Every test asserts a FAILURE the helper is supposed to catch, not just the
happy path: a helper that silently succeeds on a broken input is exactly the
class of bug these scripts exist to remove (a bracket that checks the wrong
directory still exits 0, and reads as a passing safety check).
"""
import json
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
        real_root, real_errors = lint.ROOT, lint.ERRORS
        try:
            lint.ROOT, lint.ERRORS = repo, []
            lint.check_version()
            return list(lint.ERRORS)
        finally:
            lint.ROOT, lint.ERRORS = real_root, real_errors

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

    if FAILURES:
        print(f"\nFAIL — {len(FAILURES)} test(s) failed")
        sys.exit(1)
    print("\ntest_scripts: all passed")


if __name__ == "__main__":
    main()

# Optimization plan, 2026-11 (at 0.6.107, 4fb7ac9)

A review of the repository as it stands, with what was measured, what each item would
change, and in what order. Each item is its own change with its own version bump, tests and
review round, as every change to master is. The measurements below are the state at 0.6.107,
before any item.

Status (0.6.114): items 1-3 are done (0.6.109-0.6.111; fixes from their review in 0.6.112-
0.6.114, which also add a `runner` section of checks, so the count is no longer 2,394).
Item 5 is done in 0.6.115 (tests and manifest; the type check cannot run on a runner, see
the panel README). Items 4 and 6 are open; item 7 waits for the next change to one of its
scripts.

## What was measured

| | |
|---|---|
| Size | 40,447 tracked lines. `scripts/test_scripts.py` 14,445; `triage.py` 2,697; `quota-snapshot.py` 2,300; `roster.py` 1,786; `lint.py` 1,289. |
| Skills | 220 KB of `SKILL.md` in all. `skills/dev-lead/SKILL.md` alone is 60 KB (about 15k tokens), read whole each time the lead loads the skill; `opencode-adversarial-review` is 33 KB. |
| Tests | `python3 scripts/test_scripts.py`: 2,394 checks, 4 min 24 s, one process, no way to run part of it. On CI (ubuntu, non-root) master is green; each run takes about 3 minutes. |
| Tests as root | 5 checks fail when the suite runs as uid 0 (a container, a devcontainer, a cloud session): `config-effort: a failed backup refuses and writes nothing`, `triage fix: a records directory that cannot be listed refuses the count`, `... a record linked into a directory that cannot be searched ...`, `... a records directory that cannot be searched is named ...`, `tui refresh: ... the undeletable tree was really built`. Each builds a directory root can read and write anyway. |
| Static check | `ruff check --select F,E9,B,PLE --ignore B904`: 18 findings, none of them a live bug: unused imports (`quota-fetch.py`: `base64`, `tempfile`), assignments never read (`roster.py:196`, `roster.py:1069` `eff`; four in the tests), f-strings without a placeholder (`claim-audit.py:457-458`), one `zip()` over two lists built from the same tuple (`quota-snapshot.py:1698`, equal by construction). B904 (re-raise without `from`): 35 more, mostly in `triage.py`. CI runs no static check. |
| CI | One job: `lint.py`, `test_scripts.py`, `bash -n`. Ubuntu only, although Windows paths needed fixes in 0.6.96, 0.6.97 and three quota-snapshot review rounds since, and are only simulated in the tests. The `dev-lead-panel` plugin's tests (18) and its TypeScript are not run by CI. |

## Items, in the order proposed

### 1. The suite passes as root, or says why it skipped (small)

The five checks above fail only because root ignores the permission bits they rely on; one
check already skips itself under uid 0 (`test_scripts.py`, around line 2364). Give the five the
same guard, printed as a skip with its reason, so a run as root is green or explains itself
instead of reporting failures that are not there. Cloud sessions run as root, so today every
change made from one starts from five red checks.

Done when: the suite as root reports 0 failures and 5 named skips; as a normal user nothing
changes; each guard is mutated once to confirm the check still fails for a non-root user.

### 2. Run part of the suite, and run it faster (small to medium)

Every change waits 4.4 minutes for 2,394 checks, most of which cover scripts it did not touch.

- `test_scripts.py <section>...` runs only the named sections (the `print()` headings in
  `main()` already name them; a list of `(name, function)` pairs replaces the hand-written
  sequence). With no argument everything runs, as now, so CI is unchanged.
- Then, optionally, `--jobs N`: the sections share no state (each gets its own temporary
  directory), so they can run in separate processes, with their output kept per section.

Done when: `test_scripts.py triage` runs the triage sections alone; the full run reports the
same 2,394 checks; with `--jobs`, the wall time measured before and after is in the commit.

### 3. A static check in CI (small)

Fix the 18 findings (all mechanical), then add `ruff check --select F,E9` to the CI job so an
unused import or a name used before assignment fails the push. B904 is a separate, optional
decision: re-raising with `from` keeps the cause in tracebacks, but it touches 35 places in
`triage.py` for no change in behaviour.

### 4. A Windows job (medium)

Add a `windows-latest` job that runs the Windows-relevant sections (`quota-snapshot.py on
native Windows`, the statusline refresh on Windows, the launch and argv paths fixed in
0.6.96-0.6.97) natively instead of simulated. It needs item 2's section filter. Git Bash is on
the runner, which is what `triage.py` uses on Windows. What cannot run there (tmux, the POSIX
permission checks) is skipped with a reason, as in item 1.

### 5. Test the panel in CI (small, if feasible)

`plugins/dev-lead-panel` has 18 tests and strict TypeScript that only run on a developer's
machine. Add a job that type-checks it and runs its tests, if the runner its tests need can
be installed in CI; if it cannot, at least `tsc --noEmit`.

### 6. Lighter skill text (medium, needs care)

The lead's skill is 60 KB, the largest single cost the plugin puts into every lead session,
and the review and implement skills add 7-33 KB each when a leg is prepared. Most of the
weight is in Phase 1 (lines 150-450) and Phase 2 (lines 450-851), where rules sit beside the
measured incidents that justify them ("measured 2026-09-05", the calibration rows). This was
done once before (in the 0.6.45-0.6.52 series, two evidence blocks moved to
`references/worktree-provisioning.md` and `references/claim-audit.md`).

- Keep every rule and every command in `SKILL.md`; move the incident narratives and
  calibration evidence to `references/`, linked from the rule they support.
- Guard it: a lint check that the skill stays under a size budget, and a list of the rule
  sentences that must still be present, so a later edit cannot drop a rule while trimming.
- Measure the lead's context at the start of a session before and after.

This is the item with the most value per session and the most risk: a rule that moves out of
the skill is a rule the lead may not read. It goes last, after the safety nets above, and
gets its own review round.

### 7. Shared helpers (small, opportunistic)

`os.replace`-based atomic writes are written four times (`agy-statusline.py`,
`quota-fetch.py`, `quota-setup.py`, `roster.py`) beside `_common.py`. Moving the plain ones
into `_common.py` is worth doing when one of those scripts is next changed for another
reason. `quota-fetch.py`'s write stays its own: its descriptor-relative, O_NOFOLLOW
sequence is a security property, not a duplicate.

## Not proposed

- Splitting `test_scripts.py` into many files: item 2 gets the speed and the selection
  without moving 14,000 lines; splitting can follow if the sections grow further.
- Splitting `triage.py` or `quota-snapshot.py`: they are large, but each is one command with
  tests pinned to its behaviour, and a split would be a large diff with no change a reader
  can see. Revisit if a change keeps needing to touch unrelated parts of one file.

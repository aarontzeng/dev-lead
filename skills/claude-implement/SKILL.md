---
name: claude-implement
description: Delegate a well-scoped implementation to headless Claude Code (`claude -p`, acceptEdits) in an isolated git worktree, then verify independently and review cross-family before merge. Use when a foreign lead (codex/agy) needs Claude as its implementation worker, or when a Claude lead wants a separate Claude process with its own cwd and context.
---

# Delegate implementation to headless Claude, then verify

Claude as the implementation leg. From a codex or agy lead this is the
primary way to put the Claude family on the implementer side; from a Claude
lead, prefer in-session subagents unless the delegate genuinely needs its
own working directory, permission mode, or model (runtime file).

## Before the first run of a session

Read **[`../claude-adversarial-review/references/claude-runtime.md`](../claude-adversarial-review/references/claude-runtime.md)**
— the
invocation shapes, measured `acceptEdits` behavior, patience calibration,
and the instruction-layer inheritance property are there and assumed here.

## Worktree, always

```bash
BASE=$(git rev-parse HEAD)   # from a clean checkout, recorded before anything
WORKTREE=../<repo>-claude-<short-task-slug>
git worktree add -b claude/<short-task-slug> "$WORKTREE" "$BASE"
```

Verify against that exact SHA later — never against a moving branch name.

## Run it

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/claude-implement.XXXXXX")
# Write the task prompt to "$RUN_DIR/task.md" in its own step.

# The helper lives in the SUITE's tree, cwd is the TARGET repo — a bare
# `scripts/…` resolves against the target and exits 127.
DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
[ -x "$DEV_LEAD/scripts/snapshot-refs.sh" ] || { echo "dev-lead root unresolved — set DEV_LEAD_ROOT"; exit 1; }
"$DEV_LEAD/scripts/snapshot-refs.sh" save "$WORKTREE" "$RUN_DIR/remote-refs.before"   # push-detection baseline

cd "$WORKTREE" && eval "$("$DEV_LEAD/scripts/leg-cmd.sh" claude implement --model <tier> \
  --effort <level> --run-dir "$RUN_DIR" --allow-bash '<test command>')" > "$RUN_DIR/impl.out" 2>&1
```

`leg-cmd.sh` composes the `claude` invocation only (one `--allow-bash` per
command prefix the task needs). The `cd` and the redirect are the lead's:
claude runs in the caller's cwd, and `leg-cmd.sh` refuses `--target` for this
role. What it emits, spelled out (for a lead without the script): an `export
RUN_DIR=…`, a guard that the brief exists and is not empty, then

```bash
claude -p "$(cat "$RUN_DIR/task.md")" \
  --permission-mode acceptEdits --model <tier> --effort <level> \
  --settings '{"disableAllHooks": true}' \
  --allowedTools 'Bash(git status:*)' --allowedTools 'Bash(git diff:*)' \
  --allowedTools 'Bash(git log:*)' --allowedTools 'Bash(<test command>:*)'
```

`git status`, `git diff` and `git log` are its default entries. The
`--allowedTools` entries stay after every other token: the flag takes several
values and would swallow a prompt placed after it.

`--effort` is required since 0.6.56 and comes from the roster's implement
entry (`roster.py plan --implement claude=<model>` carries it, and reports
`missing effort` when the entry has none). Its vocabulary is closed --
`low`, `medium`, `high`, `xhigh`, `max` -- and anything else is refused by
`leg-cmd.sh` and `roster.py check`, because the CLI itself only warns about
an unknown value and runs at its default (runtime file).

The snapshot is the no-push evidence on this adapter. workflow.md rates its
no-push boundary **instruction level**: the rule is stated in the task
prompt, and the fail-closed ref check at handoff is what proves either way.
The two adapters rated weakest were the two shipping without it. Shell
commands are a separate layer, gated per run: only the `--allowedTools`
prefixes run, with hooks off (below). That list is not a sandbox either --
`git diff` and `git log` can write a file with `--output`, and the test
command the lead allows runs code the delegate wrote, and that code can do
anything the account can (including a push, which only the tripwire after the
run detects) -- so the boundary is the task prompt plus the lead's own
verification, not the worktree.

Measured properties of this exact shape (runtime file has the detail):
`acceptEdits` covers **file edits only**. In `-p` mode a shell command needs
an approval nobody can give, so without `--allowedTools` the delegate runs
no test, no `py_compile`, not even `git status` (probed 2026-10-01, claude
2.1.286; a real dispatch that day made 68 turns without running one test).
Pass the repo's test command with `--allow-bash`; the same goes for `git
add`/`git commit` if the delegate is to commit -- otherwise the lead makes
the checkpoint commit. The working directory is the shell's cwd; commit
messages come out clean when the instruction layer forbids trailers. Do
**not** pass `--dangerously-skip-permissions`, and do not widen the list to
every command: name the prefixes the task needs (`leg-cmd.sh` refuses one
containing `*`, `(` or `)`).

Hooks are off for the run (`--settings '{"disableAllHooks": true}'`),
because a PreToolUse hook that rewrites commands changes what an allow rule
has to match. Probed 2026-10-01 (claude 2.1.286): the user's rtk hook
rewrote `git status --short` to `rtk git status --short`, and
`--output-format json` listed it under `permission_denials` although
`Bash(git status:*)` was allowed; it rewrites `python3 -m pytest …` to `rtk
pytest …` too, so `--allow-bash 'python3 -m pytest'` would still run no
test. With hooks disabled the same rule ran `git status --short`, and
repeated `--allowedTools` flags accumulate. The delegate therefore also runs
without the lead's own hooks (memory, notification, language) -- **and
without any hook that BLOCKS something**: if the host has a PreToolUse hook
that enforces a safety rule (a push guard, a path guard), the delegate is not
under it. The no-push rule stays instruction level here either way: the lead's own
verification (the remote-refs tripwire below) detects a stray branch push after
the fact -- a Gerrit `refs/for/*` push moves no remote-tracking ref and is not
seen -- and prevents none; on a host
that relies on such a hook for delegates, `leg-cmd.sh` has no option that keeps
the hooks on, so edit the command it prints before you run it: delete the
`--settings '{"disableAllHooks": true}'` pair, and replace every
`--allowedTools 'Bash(<prefix>:*)'` entry with one for the spelling the hook
rewrites that command to (for a hook that turns `git status` into `rtk git
status`: `--allowedTools 'Bash(rtk git status:*)'`, likewise `git diff`, `git
log` and the test command) -- the printed entries match only the spelling the
hook never lets through. (This host's
hooks only rewrite or log; none blocks.)

Launch under the host's background mechanism, generous timeout (20m+ — and
long silences are normal, not hangs).

## Writing the task prompt

Identical discipline to every implement skill:

- **Premise preflight**: compare every factual claim in the task against
  current code, tests, and ADRs; STOP and report the contradiction instead
  of editing if any premise is wrong.
- Exact acceptance criteria, files/directories in scope, the worktree's
  absolute path as working root.
- **Name the existing tests/mocks whose seams the change moves**; **anchor
  every new test to a position** ("class `TestX`, immediately after
  `test_y`").
- **Ask it to run the test suite itself** and report honest counts — this
  delegate can once its launch allows the test command (`--allow-bash`; it
  cannot run a single one otherwise), and self-testing is its feedback loop.
  Name the exact command in the prompt, spelled as allowed. **An allowed
  prefix must START the command**: a command that begins with an environment
  assignment (`PYTHONPATH=src python3 -m pytest ...`) does not match
  `--allow-bash 'python3 -m pytest'` and is denied (probed 2026-10-01, claude
  2.1.286: `Bash(python3:*)` denied `FOO=1 python3 -c ...`, `Bash(FOO=1
  python3:*)` allowed it). So either put the bare command in the brief and
  export the environment yourself, or pass the whole spelling to `--allow-bash`
  (`--allow-bash 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest'`)
  and tell the delegate to type exactly that. The first dispatch with 0.6.56
  got this wrong: the brief began the command with two assignments, the
  launch allowed only `python3 -m pytest`, and the delegate ran no test at
  all (it said so, and did not route around the permission). The lead re-runs
  everything afterward regardless.
- Git rules verbatim: leave the work UNCOMMITTED (the lead commits after
  verifying) unless the launch passed `--allow-bash 'git add'` and
  `--allow-bash 'git commit'`, in which case MAY commit on the worktree
  branch; NEVER push, reset, checkout/clean, or touch another branch;
  plain-English commit messages, no AI-authorship trailers. (This delegate inherits the
  user's global instruction layer — measured refusing a push it was
  explicitly asked to make — but state the rules anyway: belt and braces.)
- Point it at the repo's own `CLAUDE.md`/`AGENTS.md` sections that govern
  the change rather than re-explaining conventions — it already reads them.
- No recursive delegation ("do not invoke claude, codex, agy, opencode, or
  any review script").

## After it finishes: verify before anything is trusted

Alongside the sequence below, read the delegate's report for "requires
approval" or a test run it says it could not make because a permission stopped
it: that is the allow list not matching the command it was told to run (see the
test-command bullet above), not a code problem, and every test result then has
to come from the lead. A test run it could not make for another reason (a
missing dependency, a broken environment) is a different finding: look into
what it reports.

The same lead sequence as every implement skill, none of it optional:

1. `"$DEV_LEAD/scripts/snapshot-refs.sh" check "$WORKTREE" "$RUN_DIR/remote-refs.before" || exit 1` — the remote-ref
   tripwire, first and fail-closed. The helper exits nonzero on a delta and
   `|| exit 1` makes that terminal; do not replace it with a second snapshot
   and a raw `diff`, which can be noticed and accidentally continued past.
2. `git status --short`, `git log "$BASE"..HEAD --oneline`,
   `git diff "$BASE"...HEAD` — scope verified, not assumed; commit-message
   hygiene checked. (If the delegate left work uncommitted, inspect the
   working tree FIRST — a ranged diff on an uncommitted tree is empty and
   reads as a false green — then the lead stages and makes the checkpoint
   commit.)
3. **Run the full suite yourself** in the worktree.
4. **Mutation-proof every new regression test** (commit first; the full
   mechanics are in
   [`dev-lead/references/mutation-runbook.md`](../dev-lead/references/mutation-runbook.md)).
5. **Cross-family adversarial review** — Claude implemented, so the reviewer
   is GPT, Gemini, or a named free-pool model. Never another Claude context,
   and not a stealth model whose family might be Claude.
6. Merge gate: user sees the diff and verified findings; fast-forward, push
   and tear down only on approval — the person's, or, where the roster sets
   `merge_gate.mode = lead`, a fully green verdict itself; anything not green,
   and any stricter project contract, still goes to the person — then report
   the ref. The delegate never pushes.

The fix-round loop (findings quoted verbatim, same worktree, new commit,
re-verify) is `dev-lead` Phase 2 — this skill adds nothing to it.

## What this is not

Not a replacement for in-session subagents on a Claude lead (those are
cheaper and integrated), and not a way to skip review — it moves who types
the code, not who is accountable for it being correct.

# `agy` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for the Antigravity CLI, shared by
`agy-adversarial-review` (read-only, `--mode plan`) and `agy-implement`
(write, `--mode accept-edits`). It sits under the review skill's directory
because every directory in a skills tree needs a `SKILL.md` to load, and a
bare shared directory has no precedent.

The role skills carry the discipline — what a good task or review prompt
says, what must be verified, who reviews whom. This file carries the
mechanics, and **every item in it was paid for with a real incident** (dates
kept as provenance; re-verify anything version-sensitive against your
install). Read it before the first `agy` run of a session.

## Permission model — one-time setup per machine (required)

Headless `--sandbox` runs auto-deny any tool needing "unsandboxed"
permission, and **git needs it — measured: even read-only `git log` in a
trusted, `--add-dir`ed directory is denied, and the whole run dies with zero
output.** A delegate that cannot run `git diff`/`git log` is useless in
either role, so `~/.gemini/antigravity-cli/settings.json` needs these under
`permissions.allow`:

```json
"unsandboxed(git status)",
"unsandboxed(git log)",
"unsandboxed(git diff)",
"unsandboxed(git show)",
"unsandboxed(git branch)",
"unsandboxed(git rev-parse)"
```

**The `unsandboxed(...)` entries are not the whole story, and an empty
`permissions.allow` fails EARLIER than any of them.** With no `allow` array at
all the CLI loads `permissions=<nil>` and falls back to
`toolPermission=request-review` — every tool call asks a human who is not
there, so the FIRST tool call is auto-denied and the run dies before reading a
single file. The review role never hit this (its prompts hand it a pre-written
diff file, so it can produce a report having read nothing); `agy-implement`
hits it immediately. The tool permissions themselves must be listed, scoped to
the worktree rather than the home directory:

```json
"read_file(/abs/path/to/worktree/**)",
"write_file(/abs/path/to/worktree/**)"
```

**Scope the READ rule to every directory the role will ever see, not to
today's one.** A review role reads a FROZEN target, and freezing creates a new
directory each round — so a rule naming one worktree works until the first
round whose frozen copy has a different name, and then fails. Measured: three
review legs ran fine because their frozen directories happened to be named
after the same worktree, and the fourth died with "a tool required the
read_file permission" after the naming changed. The intermittency is the trap;
a rule that never worked is easier to diagnose than one that stops.

A sibling glob over the repo family covers it — `read_file(/abs/path/repo-*/**)`
— and note that WRITE should stay narrow. A review role has no reason to write
anywhere, and widening both together silently hands the read fix to the write
rule as well.

Diagnose it from the CLI log, which records what was actually loaded — this is
the line that distinguishes "wrong rule" from "no rules at all":

```bash
grep -oE "permissions=[^,]*, toolPermission=[a-z-]*" \
  ~/.gemini/antigravity-cli/log/cli-*.log | tail -1
```

`permissions=<nil>` means nothing loaded; `permissions=&{Allow:[...]}` lists
what did. Measured 2026-08-27: a run with `<nil>` died in seconds with
`no output produced — a tool required the "read_file" permission`, and the same
prompt with the rules loaded ran normally. Note the error text names ONE tool
and invites `--dangerously-skip-permissions`; that invitation is the wrong fix
(it auto-approves everything, discarding the boundary this whole setup exists
to keep) and the named tool is merely the first one attempted, not the only one
missing.

Those six are not quite the read set — see below. The **write set** adds
these, for `agy-implement` only:

```json
"unsandboxed(git add)",
"unsandboxed(git commit)",
"unsandboxed(pytest)",
"unsandboxed(python3 -m pytest)",
"unsandboxed(python -m pytest)"
```

**Add `unsandboxed(wc)` and `unsandboxed(tail)` to the read set** — the cause
is this suite's own prompts. Every review prompt demands an evidence gate
("per file: its line count and the verbatim last line"), agy implements that
gate with `wc -l` and `tail -1`, and the files live in the `--add-dir`ed repo
— outside agy's own workspace root — so the shell read needs unsandboxed and
is auto-denied without those two entries.

The failure is diagnosable but only if you look: the run dies with a one-line
"a tool required the unsandboxed permission" message, and the ACTUAL command
is in the CLI log:

```bash
grep -i "permission check failed for unsandboxed" \
  ~/.gemini/antigravity-cli/log/cli-*.log | tail -1
```

Do that before adding anything. The first instinct is to broaden the list
with `cat`/`grep`/`head`, which hands agy unsandboxed read of the whole
filesystem; the log names one command, and one entry is usually the whole
fix. Note that `command(wc)` being present does NOT help — sandboxed and
unsandboxed are separate grants for the same binary, which is why a list
that looks complete still fails.

Deliberately absent from both sets: `git push`, `git reset`, `git checkout`,
`git clean`, `git worktree`. A headless agy that tries any of them is
auto-denied by the CLI itself — **the no-push rule enforced by machine, not
by prose in a prompt.** That property is the reason to keep this list
minimal.

The pytest entries are a **ruled exception** (owner-approved, dated in the
journal): letting the delegate self-test removes its single biggest measured
weakness — 18 broken existing tests across two rounds, ~80 minutes of lead
verification, all traced to the delegate never seeing a test run. The cost is
stated honestly, not hidden: pytest executes repo-supplied code (conftest,
plugins, fixtures) unsandboxed, so **on this path the no-push rule is
instruction-level, not machine-enforced**. That is acceptable because the
threat model is a fallible delegate, not a hostile one, and the workflow
makes the residual risk observable: `agy-implement` snapshots `refs/remotes`
before dispatch and diffs it at handoff, so an accidental push surfaces as a
delta instead of being assumed not to have happened. The permission is also
GLOBAL — there is no per-role scoping — so a plan-mode reviewer that
disobeys its prompt can execute pytest too; the review skill states this.
Three rule shapes because matching is against the literal command string —
the measured incident was `python3 -m pytest` sailing past
`unsandboxed(pytest)` — and the task prompt must PIN which spelling the
delegate uses; a fourth spelling (`.venv/bin/pytest`, `uv run pytest`) still
dies silently. Non-pytest repos: the delegate writes, the lead runs tests —
until your repo's runner earns its own deliberate ruling.

Rules that follow:

- **Never substitute `--dangerously-skip-permissions` for a missing rule.**
  It auto-approves every permission request, push included, and dissolves the
  machine-level guarantees this delegate has everywhere outside pytest. If a
  run needs something not on the list, stop and ask the user for a narrowly
  scoped rule.
- If you keep a canonical git-tracked copy of the settings file, remember the
  LIVE file at `~/.gemini/antigravity-cli/settings.json` is a separate copy
  that drifts (sessions append ad-hoc rules). A rule added to only one of
  them is how "I allow-listed that" and a silent death can both be true —
  check the live file when diagnosing, update both when ruling.

`--mode plan` denies the same class through a different door, and the
message differs: `permission check failed for command "<cmd>": user denied
permission to run command`. Observed twice in one session (2026-08-19, agy
1.1.15) — once on `ssh`, once on `node -e` — each time killing the leg
outright with that single line as its whole output. Both were the lead's
fault, not the delegate's: the brief invited work the runtime could not do
(see [`methodology.md`](../../../docs/methodology.md) §3).

## The workspace is NOT your cwd — `--add-dir` is mandatory

Measured: a headless `agy -p` run's workspace root is agy's own scratch
directory (`~/.gemini/antigravity-cli/scratch`), **not** the directory you
launch it from. "Run it inside the repo/worktree" grants nothing by itself.

Always pass `--add-dir "$TARGET"` and name that same absolute path in the
prompt as the place to work. `--add-dir` is repeatable when the work spans
repos. For a write run, never `--add-dir` the main checkout — the worktree is
the whole point.

## Flags

Verified against `agy --help` (re-verify per version). Both roles pass
everything here except `--mode`, whose value is the role.

- `-p` — headless: print once, exit. Launch it under your host's background
  mechanism; a foreground shell-tool timeout will kill it regardless of
  `--print-timeout`.
- `--mode` — accepts only `plan` or `accept-edits`. `plan` tells the model to
  research and report instead of editing; it is a **behavioral mode, not a
  filesystem or network security boundary** — reinforce the intent in the
  prompt and verify repository state afterward regardless.
- `--sandbox` — the CLI's terminal restrictions. Keep it on in both roles;
  mode alone does not constrain shell commands. It only works together with
  the allow-list above.
- `--effort` — a real flag (`low|medium|high`), and **the correct use is to
  OMIT it whenever the model id carries a suffix.** An earlier version of this
  entry said each `-low`/`-medium`/`-high` suffix *requires* the identically
  named explicit effort — measured 2026-08-21, that is wrong: bare
  `--model gemini-3.7-flash-high` with no `--effort` runs fine. The suffix IS
  the effort control; passing `--effort` merely restates it, and a mismatch
  (for example `--effort low` with a `-high` id) is a hard CLI error. So the
  flag can only ever break a run or be redundant — a session was observed
  repeatedly tripping the mismatch by maintaining the same fact in two places.
  For the Claude pool's models it is rejected outright (the "-thinking" in
  those ids is the whole effort control). Drop it everywhere.
- `--print-timeout` — **the default (5m0s) cuts real work off mid-flight.**
  Use `10m0s` for a review, `20m0s` for an implementation.
- `--disable-slash-commands` — stops prompt text being expanded as slash
  commands (and skills). Prompts are full of paths; a stray leading `/` is
  otherwise interpreted rather than read. **It cancels `--mode plan` — the
  two must never be passed together.** The CLI warns
  (`--mode plan has no effect while slash command expansion is disabled`)
  and means it: measured three ways on one model in one session — with both
  flags the delegate CREATED a file it was told to create; with `--mode
  plan` alone it refused and returned a plan for approval; with `--mode
  accept-edits` plus the flag it wrote normally and printed no warning. So
  the cancellation is specific to `plan`, which is presumably expanded as a
  skill, while `accept-edits` is native. Consequence per role: the review
  role drops the flag and keeps its no-write mode (leading-`/` lines are
  avoided by prompt hygiene instead); the implement role keeps the flag,
  because it has no mode to lose.
- `--output-format json` plus `--json-schema` for machine-readable findings.

Put long prompts in a file and interpolate (`"$(cat "$RUN_DIR/prompt.md")"`)
so the shell cannot mangle CJK or newlines. Use `mktemp -d`, never a fixed
`/tmp` path — concurrent-agent `/tmp` collisions are a documented incident
class. **Write that file in its own command**, not in the compound command
that launches the run (see the `pkill` self-match trap below for why
compound commands bite).

Give agy paths relative to the repo and let it read files itself. Pasting a
whole `git diff` into the prompt is fine for a small change and wasteful for
a large one.

## Models

**agy may bill multiple SEPARATE quota pools** (e.g. a Gemini pool and a
Claude pool). Spending one does not touch the other — which makes the second
pool's models worth knowing about even when the first is the scarce one, and
makes them legitimate cross-family reviewers for anything their family did
not write. The constraint is on the **relationship**, not the model: a
second-pool Claude model is fine as an implementer anywhere, fine as a
reviewer of Gemini/GPT work, and forbidden as a reviewer of Claude's own
work.

### Gemini 3.7 Flash: local catalogue baseline

On 2026-08-14, this account's `agy models` catalogue included:

```text
gemini-3.7-flash-high
gemini-3.7-flash-medium
gemini-3.7-flash-low
```

[Google's 3.7 Flash announcement](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-3-7-flash/)
establishes the release, not an account entitlement or a quality gate. The
role skills therefore use the matching `-high`/`high` pair as their current
Gemini default, but a different account must substitute an available matched
pair and verify the served model from its log. The first locally verified 3.7
run is calibration: append its verified outcome to the local journal before
using it to make dispatch or gate decisions.

Catalogue facts to re-verify on YOUR account (all measured on at least one):

- **A requested model can be silently downgraded — the id you passed is not
  evidence of the model you got.** Measured: a "pro" tier reached the backend
  as the flash tier, while flash tiers propagated faithfully. Nothing in the
  CLI's output says so; only the log does:

  ```bash
  ls -t ~/.gemini/antigravity-cli/log/*.log | head -1 | \
    xargs grep -oE 'Resolving model .*|Propagating selected model override to backend: .*'
  ```

  Check before recording which model reviewed something. Escalate to a
  deeper tier only with evidence that the escalation actually took effect.
- Entitlements differ per account and per machine — an id from another
  machine's notes may not be in your picker.
- A new flash generation is a calibration point, not an automatic reason to
  retire an older tier or change the review gate. Compare local verified
  outcomes after the first runs, not release branding or prose style.

## A mid-generation hang, distinct from the auth-layer failures below

Measured once (a data point, not a claimed mechanism): a review run
authenticated cleanly, resolved its model, refreshed quota, streamed some
output — then produced nothing for the remaining half of its print timeout
and died with `Print mode: timed out`. Different shape from every failure
below: auth, quota, and model resolution all succeeded. Congestion, a
large-prompt stall, or something else — unmeasured at n=1; do not
extrapolate. Practical handling: it fails LOUD (the process exits with a
timeout error), needs no forensic recovery, and a same-round leg from
another family can cover the round while this one stalls.

## The silent-death mode

Measured repeatedly, in both roles: **when any tool in a headless run needs a
permission that cannot be prompted for, the CLI auto-denies it and the run
produces no output at all** — while any file edits made before the denial are
already on disk.

Consequences, both counter-intuitive:

- **An empty result does not mean nothing happened.**
- **The model never gets a chance to handle the denial**, so "if a command is
  denied, say so and continue" is not implementable. The instruction that
  invites the attempt is the bug.

The fix is always the same: find the denied command in the newest log, then
either put a workflow-required command on the allow-list deliberately or
remove the instruction that invites the attempt.

```bash
ls -t ~/.gemini/antigravity-cli/log/*.log | head -1 | xargs grep -i "permission check failed"
```

Note the rule shape must match the real command string (`python3 -m pytest`
is not matched by `unsandboxed(pytest)`) — a mis-shaped rule produces the
identical silent death and the appearance of a fix.

**That grep is a TEST, and an absent line REFUTES this mode.** Measured
2026-08-10, and the failure was the lead's, not the CLI's: two runs returned
zero bytes, the allow-list was found to be missing `unsandboxed(wc)` and
`unsandboxed(tail)`, the symptom matched this section, and "root cause found"
was announced — with a settings change recommended to the user. The log had no
`permission check failed` line at all, and a run **earlier the same day on the
same machine and the same settings** had produced a full report *including the
`wc`/`tail` evidence gate*. The setup was already adequate; the missing entries
were never the cause.

So, in order, before touching the allow-list:

1. `grep -i "permission check failed" <newest log>`. **No hit means this is not
   the permission mode.** Stop; do not broaden the list on the strength of a
   plausible story.
2. Ask whether this machine has EVER produced a full report with this prompt
   shape. A previous success is the strongest refutation available: it proves
   the permissions, the `--add-dir`, and the gate wording all work here.
3. Only then consider transient causes (the mid-generation hang above,
   provider-side degradation) — and per the journal's rules, do not promote a
   single observation to a cause.

A third output shape belongs here alongside "zero bytes": a run can return a
**truncated fragment of a tool call** — 41 bytes of `View <file> in <path>}` in
the measured case — with no report and no error. It is the same class of
non-answer, not evidence of a different mechanism.

After **every** run that fails, times out, or returns nothing:

```bash
git -C "$TARGET" status --short
git -C "$TARGET" diff --stat
git -C "$TARGET" log "$BASE"..HEAD --oneline
```

Keep the worktree and branch until the user agrees they are disposable —
partial edits, commits, and the launcher output are the diagnosis evidence.

## Auth: the failure is a network timeout, not the token

**Measured across 9 runs with perfect correlation:** the CLI's keyring auth
loads the stored token and then makes a network call with a **10-second
budget**. When that call does not finish in time, the log says
`keyringAuth: timed out after 10s, skipping keyring auth` and agy reports
"not logged in" — **whatever the token's actual state**. Most failing runs
had a perfectly valid, unexpired token; the network was the problem. On a
link with flaky reachability to the vendor's endpoints, each headless run is
an independent coin flip — which is why runs appear to alternate and why a
fresh login *seems* to fix it for a while.

**Check DNS first.** In one measured environment the entire mechanism was a
blocked first resolver in `/etc/resolv.conf`: every name resolution paid a
~5 s timeout before falling through, eating half the 10 s budget. Reordering
resolvers took DNS from 5s to 10ms and the timeout line disappeared.

**A different failure with a different fix**: output saying
`Eligibility check failed: … i/o timeout` is a broken agy INSTALL state, not
the network and not the credential — measured: a full remove-and-reinstall
fixed it instantly on the same machine and network. Do not ask for repeated
re-logins for this one; reinstall.

**Retry, don't re-login.** A failure says nothing about the credential.
Retry the run a few times with a short gap. Distinguish cases from the log:

```bash
ls -t ~/.gemini/antigravity-cli/log/*.log | head -1 | xargs grep -oE "Print mode: .*"
```

- `silent auth succeeded` → headless works (but a nonzero exit after it is
  still a real failure — the line only says auth worked).
- `keyringAuth: timed out after 10s` → transient; retry.
- `silent auth failed` → the ONE genuine re-login case: the user must run
  `agy` interactively in a real terminal (an agent cannot — no TTY) and
  complete the browser flow. Verify afterward with a cheap probe:

  ```bash
  agy -p "Reply with exactly: AUTH_OK" --model <cheap-tier> \
      --mode plan --sandbox --disable-slash-commands --print-timeout 2m0s
  ```

- **No `Print mode:` line at all → INCONCLUSIVE, not unavailable.** Loose
  "not logged in" strings are printed by background polling in the first
  second and appear in runs that go on to succeed. Only `Print mode:` lines
  mean anything. Resolve inconclusive with the probe above.

When retrying in a loop: attribute logs to runs via `find -newer` against a
marker file created just before the call — "the globally newest log" may
belong to another process. Capture agy's exit status through an `if`, not a
bare `cmd; rc=$?` (a caller's `set -e` exits the function before `rc=$?`
runs). Zero or multiple new logs → inconclusive, retry; never a silent
success.

**Don't declare a model or pool broken from a handful of failures — verify
against the raw log, not a wrapper's pass/fail summary.** Measured: three
straight "failures" nearly got a whole model pool stripped from this file;
the raw logs showed two had actually succeeded and a hand-rolled wrapper's
simplified matching had misread them. Also: a run that reports failure at
the wrapper level can still have spent quota if any part of it reached the
backend.

**An API key is not a shortcut.** Vendor API keys bill the pay-per-token
API, not the subscription quota that makes this CLI worth using as a second
family. Even where honored, "set an API key to fix headless auth" trades
free quota for a bill. If tried anyway: a `~/.bashrc` export placed below
the interactive-shell guard is invisible to every non-interactive shell,
and a print-mode probe blocks for its whole timeout before revealing
anything.

## Calibration rows

Per [`docs/calibration-journal.md`](../../../docs/calibration-journal.md):
one table per family, appended and never rewritten, one row per run, the
outcome column carrying VERIFIED hit rates rather than impressions.

**This file deliberately ships NO rows.** The journal's opening section is
titled "why the tables in the skills are not your tables", and a maintainer's
hit rates on a maintainer's account, repos and week are exactly what a reader
must not inherit as if it were shipped calibration. Start your own table here
and keep it out of any upstream contribution:

| date | model | role | outcome |
|---|---|---|---|

Two observations from the run that opened this section are recorded as dated
measurements rather than as a table, because they are about METHOD and
transfer, while hit rates do not:

- **Brief diversity paid where model diversity alone would not have**
  (2026-08-10). An attack-sequence brief on this family and a line-level brief
  on another ran against the same commit: 4 findings and 12 findings,
  overlapping **zero percent**, every one verified against the code. That is
  methodology section 2 measured instead of asserted — two legs on the same
  brief buy redundancy, two briefs buy coverage.
- **The evidence gate has two prices** (2026-08-10). The
  line-count-and-last-line form needs `wc` and `tail` on the allow-list; a
  "quote the code that decides it" form needs only the delegate's own file
  read, proves the same thing — that the file was actually opened — and costs
  no permissions. Prefer the quoting form unless you need a value the delegate
  cannot obtain without a shell.

## Process hygiene

- `agy models` may hang during an auth failure, sitting on an interactive
  login prompt. Do not use it as a health check; read the `Print mode:` log
  line instead.
- Kill stragglers **by PID** (`pgrep` first, inspect, then `kill`). Three
  `pkill -f` traps, all hit in practice:
  - `pkill -f agy` also kills the user's interactive agy session — the very
    thing that keeps the token refreshed.
  - `pkill -f "<pattern>"` matches full command lines **including the shell
    you run it from** — in a compound command it kills its own parent shell,
    and the next command never runs (exit 144, looks unrelated).
  - The same self-match ruins a **guard**: `pgrep -f "agy -p" && exit`
    inside a script whose own command line contains `agy -p` always reports
    busy. Match on the process NAME: `pgrep -x agy`.
- **A permission prompt in headless mode is a failed run, not a reason to
  disable permissions.** Let it fail, preserve the worktree, diagnose from
  the log.
- Parallel agy processes are NOT a hazard (measured, retracting an earlier
  serialization rule): a successful headless run does not rewrite the token
  file, so there is no refresh race. Retry per the loop above rather than
  spacing launches out.

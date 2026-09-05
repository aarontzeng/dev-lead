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
| 2026-08-30 | gemini-3.7-flash-high | review (plan doc) | 6/6 HOLDS but ONE VERDICT REJECTED: cited `userEdited`/`hasPrefilled` code that does not exist (grep 0 hits) — narrated PLANNED behavior as existing code with fabricated quotes. Spot-check every citation before accepting a HOLDS from this leg; the other 5 verdicts' quotes were genuine. |
| 2026-08-30 | gemini-3.7-flash-high | review (code diff) | Clean round: 6 properties, quotes all genuine on spot-check, 1 real BROKEN (web error-state cleanup) the other legs missed. Same model, same day as the fabrication row — per-run verification is the control, not model choice. |
| 2026-08-31 | gemini-3.7-flash-high | review (design doc) | 2/2 lead-posed questions confirmed as real gaps, plus 7 unprompted findings on a mesh crypto design; strongest single leg of a 4-family round. Its lead finding (a replay window documented to treat aged-out sequence numbers as NEW, inverting anti-replay) was independently reached by 2 other legs and lead-verified. |
| 2026-08-31 | gemini-3.7-flash-high | review (code diff) | 5/5 HOLDS, every citation genuine on spot-check — including one store mechanism it raised unprompted, beyond the brief. No fabrication: n=2 clean since the 2026-08-30 row, not yet enough to retire that warning under the multi-session rule. But it MISSED what codex caught the same round — parameterized "every invariant" tests whose mutations only trip an outer cardinality guard, leaving the inner predicates unpinned. Worth watching: this leg reads a suite's stated intent more readily than its actual reach. |
| 2026-09-01 | gemini-3.7-flash-high | review (2 code changes + 1 plan doc, 11 posed items) | **Weakest of four this round, and the failure mode is now a pattern.** Sole HOLDS on a stage-label defect the other three called (lead independently agreed with the three). Missed an untested new guard the other three found. On the CI-skip item it reached the RIGHT conclusion by the WRONG mechanism — claimed the tests were excluded by `ctest -LE 'docker\|hw'`; measured, they carry no LABELS at all, so they run and are eaten by `SKIP_RETURN_CODE`. Code citations genuine (n=3 clean since the fabrication row); plan-doc line numbers systematically off by ~−7 with quoted text correct — an artifact of the doc riding in the prompt appendix rather than the tree, worth avoiding by giving this leg the file, not the paste. Fastest leg by far (160 s vs 8–14 min). |
| 2026-09-01 | gemini-3.7-flash-high | review (spec freeze doc, 6 posed items) | **Zero findings across all six items, and the one place it took a position it was confidently wrong.** The lead's item (a) hypothesised that a shared deadline leaves the returned reason timing-dependent. This leg correctly showed the sort key is `(resource_type, resource_id)` with reason absent — then closed with "the lead's opinion ... is **WRONG**". It is the *stop* that was wrong: a cutoff converts a source that would have SUCCEEDED into a failure, so set membership shifts and the selected source changes too. Three other legs reached that; this one had the right first step and did not take the second. It also asserted "no property vanished" for the PS2→PS3 trace after examining only one of the two rows that changed. **Citations were clean (15/15 verified) and the prior round's line-number offset did NOT recur** — the reviewed file was in the frozen tree and the dispatch said "cite the tree, quote the appendix"; do that every time for this leg. 55 s, vs 5.5–10.5 min for the others. Across three rounds the shape is stable: fastest, most agreeable, and the leg whose confident refutations need checking hardest. |
| 2026-09-02 | gemini-3.7-flash-high | review (3 document changes, 7 posed items) | **Everything it said was right, and none of it was sole.** BLOCKING on the two items the lead had already framed (an ADR asserting a lifecycle its implementation cannot emit; the ADR-vs-audit-report contradiction), MAJOR on the ADR's "Enforced By" list, MINOR on the README status asymmetry — the last shared with grok. Zero fabrication, citations clean (n=4 clean since the 2026-08-30 row; the multi-session rule is now satisfied — retire the fabrication warning). **Three rounds of stable shape: fastest, most agreeable, confirms the lead's framing without extending it.** After the 2026-09-01 row's confidently-wrong refutation, this round it took no position it could not support — which is the improvement, but the standing cost is unchanged: on a 4-leg round this leg has now gone two consecutive rounds without a sole finding. Keep it for speed and for citation hygiene, not for reach. |
| 2026-09-02 | gemini-3.7-flash-high | review (flight-command change, 11 posed items) | **First sole finding in three rounds, and the citation record is now clean enough to retire the warning.** 30/30 citations verbatim in the frozen tree, zero drift — n=5 clean since 2026-08-30, multi-session rule satisfied; stop discounting this leg for fabrication. Sole finder of the retry-exhaustion path (a sequence step that publishes a terminal NO_ACK and is erased while the sequence itself stays live). **But it found the mechanism and missed the meaning**: it rated the consequence "state desync / dual terminal events", MAJOR. Another leg reached the same code and named the actual hazard — the aircraft arms and launches *after* the operator has been told the command failed — which is what made it BLOCKING. Worth keeping as a distinction: this leg reliably reports what the code does and under-reads what it costs. Also sole on two frontend consistency MINORs. One wrong MAJOR: it called the gateway's echo-after-publish a race, which requires a NATS round trip to beat one adjacent statement on the same thread. And its one evidence-gate failure was a HOLDS — it declared ADR compliance for the sequence-terminal event shape citing a test that asserts nothing about the two fields at issue. Fastest leg again, 216 s vs 9-14 min. |
| 2026-09-04 | **gemini-3.8-flash-high** (first scored round) | review (2 planning docs, 9 posed items) | **Deeper reader than 3.7, worse citer, and zero real defects.** Model served confirmed as 3.8, no silent downgrade. 338 s — fastest leg again. Reached all nine items with nothing NOT REACHED, and for the item that required reading a whole 3,500-line file at revision rather than the diff, it actually did: it cited across lines 129, 265, 2165-2170, 2216-2248, 2411. That is a real capability gain over 3.7, which had two consecutive rounds of shallow agreement. **But both its MAJORs and one MINOR are the same category error**: the plan contains a stop gate for unassigned upstream owners, the current state trips that gate, and it reported "C2c can never start, the production path is completely blocked" as a self-contradiction. The gate's own closing sentence — "This gate may finish without a commit; that correctly reports the blocker" — says the blocked outcome is the designed one; the leg never quoted it. Lead rejected both. **Citation discipline regressed from 3.7's 30/30 exact**: 30 checked, 22 exact, 8 drifted ±2-4, and one number fully invented (cited `985-992` on a real quote whose actual location is `730-736`, −255). Its weakest verdict was a HOLDS asserting properties of a class that does not exist yet, with no citation at all. **Ruling for 3.8: use it for coverage and for questions that need the whole file read; do not take its contradiction claims without checking whether the "contradicted" text is a gate the plan meant to trip.** |
| 2026-09-04 | gemini-3.8-flash-high | **research** (4 aspects, 20 questions) + 2 control runs | **A third failure class, distinct from fabrication and from evidence-wrong: CONCLUSION-UNSOUND.** 58/62 citations exact, one invented symbol (`def handle_call(` — the name exists nowhere in the tree), and the generalising prose built on the good citations independently false: it wrote "no background scheduler, worker thread, cron, or inbound webhook listener" about code it had genuinely read, while the file starts a named daemon thread. Both of the round's valuable outputs came from **chasing its pointers**, neither from anything it concluded. Remedy differs in kind from the other two classes: keep dispatching it, follow its citations, discard its sentences — or better, **ask for an artifact with no room for conclusions**. Zero NOT REACHED on the question designed to earn one: it produced four documentation URLs with version stamps and no fetch capability, including a verbatim quotation of vendor docs. **Then the control runs inverted the picture.** Given a table-shaped brief with a completeness demand, the same leg produced 17 rows, ~112 exact citations, zero missed, zero invented — and beat the lead's own ground truth, which had 9 sites because the lead's grep was too narrow. A table has no thesis, and this leg's weakness lives in theses. |
| 2026-09-05 | `gemini-3.8-flash-high` | implement (mobile UI slice, RN/Expo, 23 files, +1133) | **First implement run of 3.8 Flash, clean on R1**: all 23 files inside scope, one commit, no trailers, 780/780 jest (768 + 12), tsc clean, eslint 0 errors, self-report totals matched the lead's re-run digit for digit. 5 lead mutants: 4 killed as claimed; the 5th (read-only) was guarded by a different line than the report named — the test was real, the *attribution* in its report was wrong (calibration note: trust its totals, verify its 'delete this line' claims). Followed a WRONG premise in the brief without pushback ("a split never changes which symbols are held") — three reviewers later caught it; premise preflight did not fire on a plausible-sounding false statement. **R2 (11 prescriptive findings): all 11 closed correctly, +8 tests, but the run died at the verification step from a headless `unsandboxed` denial AFTER every edit was on disk and before `git commit`** — zero stdout, edits intact; lead ran the suite (788/788) and committed. Same silent-death mode hit its plan-mode *review* leg on the web branch the same day (zero output, unrecoverable). Net: strong, fast implementer; budget one silent-death per multi-round run and read the worktree, not the exit. |
| 2026-09-05 | `gemini-3.8-flash-high` | implement (backend, dual-column price schema + history reconstructor rewrite, 4 commits then 4 more) | **Second run, and the permission trap finally diagnosed.** The first launch died in 40s with zero output: the brief pinned `python3 -m pytest`, and the LIVE settings file had only `command(backend/venv/bin/pytest)` — none of the three `unsandboxed(...)` pytest spellings this runtime file documents. The denied command is NOT in the CLI log; it is in `~/.gemini/antigravity-cli/conversations/<conversation-id>.db`, table `steps`, column `step_payload`, as `{"BypassSandbox":true,"CommandLine":...}` — read it with sqlite3 before guessing. Same trap had silently killed its mobile R2 verification step earlier the same day. With the rules added it ran clean: R1 four scoped commits, R2 closed all 15 prescriptive findings, +8 and +7 tests, self-reported totals matched the lead's re-run. Two recurring shapes to budget for: it runs ONLY the files the brief names (the lead's full-suite run found a consumer the brief had missed, broken by the new data shape), and its mutation claims name plausible-but-wrong lines (its reverse-split EPS test used exact numbers and stayed green with the clamp deleted). Verdict on this account: strong, fast, cheap implementer for well-specified backend work; the lead still owns the full suite and every mutation check. |
| 2026-09-05 | `gemini-3.8-flash-medium` (first `-medium` round) | review (10-change C2 UI stack, 8 posed items, reviewed per-diff) | **Medium costs citation precision, not reach.** 388 s, zero NOT REACHED, every item answered per-diff. Converged with all three other legs on the round's BLOCKING (stale SITL battery falls through to the simulated 55–85 %) and was the **sole finder of a real MAJOR**: `GotoLegLayer` clears a fresh Go-to on the same tick it is set whenever the vehicle's last-seen mode is a hold mode, so a vehicle parked in BRAKE starts moving with no leg on the map — lead-verified through the gateway's GUIDED-then-setpoint order. **Its own severity words overshot twice**: the detach item was BLOCKING on a claim that a detached vehicle "drifts until battery exhaustion" (GUIDED holds the last setpoint; the grok leg had the right account), and its (c) claim of a duplicate row on reconnect was CONCLUSION-UNSOUND — `resolve()` correlates by persisted `requestId` regardless of state, which the leg had read. One exhaustiveness count did not reproduce (14 vs 32). 40 citations checked, 5 drifted, none invented. Ruling for `-medium`: same coverage as `-high` at 70 % of the time; take its pointers, re-derive its severities. |
| 2026-09-05 | `gemini-3.8-flash-medium` | review (take-over CL: mission-service + adapter + gateway + frontend, 8 posed items) | **Its falsifications were the round's most useful output, and its own new claims were the weakest.** 388 s, zero NOT REACHED. It was the leg that killed two hypotheses the lead had put in the brief and another leg had accepted: the ROI-rule-after-partial-release deadlock is unreachable (`active_targets` at MissionManager.cpp:1519-1520 is the exact complement of the rule's `delivery_state <> 'CANCELLED'`), and the release/upload race does not exist (both queues drain in one loop iteration on one thread, release first). Codex had graded the second MAJOR; agy's argument was right and the lead dropped it. It also reached the same unguarded-SQL finding as the other two legs. **Then its own two additions both failed**: a MAJOR "data race on mission_executions" that never names two threads (both drains are the same thread in the same loop) — UNSOURCED, and a BLOCKING on (a) that is a design trade-off the change documents. Citations: 40 checked, 5 drifted, none invented. Ruling unchanged from the earlier `-medium` row, with an addition: **dispatch it when the brief contains hypotheses you want attacked — it attacks the brief harder than it attacks the code.** |
| 2026-09-06 | `gemini-3.8-flash-medium` | review (PF-19/PF-20 contract across 3 services + a migration, 10 posed items) | **Third `-medium` round and the first with fabricated code. Its best contribution and its worst both came from the same habit: reasoning past the evidence.** 287 s, zero NOT REACHED. Sole finder of a real mechanism three other legs missed: both revision inserts stamp `created_at_utc = NOW()`, and the listing joins MAX(revision) then orders by that row's timestamp, so a new revision moves its proposal to the END of the ordering and an OFFSET page skips a neighbour. Lead-verified — and then lead-verified as UNREACHABLE, because the same patch adds a snapshot watermark the only production caller carries forward. **Three of its citations quote code that does not exist**: it reported the envelope flattens currentness into a top-level field (the envelope is nested, exactly as the contract wants), a `reason_code` lookup on a field with zero hits in the tree, and a 'highest-leverage assertion' at a blank line in a function whose name does not appear in the file. Its supervisor caught all three by re-resolving every coordinate. Also 13 of 39 real citations drifted 1-4 lines. **Standing ruling now has a second clause: dispatch it for mechanism, never relay a claim of its you have not opened the file on.** Note against its 2026-09-05 row: this round it attacked the brief not at all and agreed with every framing supplied. |
| 2026-09-06 | `gemini-3.8-flash-high` | implement (backend scheduled detection + admin UI on web & mobile; R1 3 commits, R2 1 commit) | **Two launches died in under 6 minutes, both killed by the LEAD's brief, not the delegate**: the brief said `ls tests \| grep adjusted` and, next attempt, `npx vitest run` — neither on the allow-list. The conversation store named the command each time. Rule that came out of it: the brief must list the ONLY shell spellings available and say that anything else kills the run; a delegate will run exactly what you wrote. Third launch clean: scoped, no trailers, self-reported totals matched (2969/466/794), 6/6 lead mutants killed. R2 closed all 7 prescriptive items. Recurring shapes: it will write a test that passes by injecting state the real path never produces (mobile admin gate — `/auth/me` did not even return the field; test passed by seeding the store), and it counts only raised exceptions as failures when the provider swallows errors into `[]`. Both found by cross-family review, not by its own report. Still the right implementer for this shape of work on this account. |

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

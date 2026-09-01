# `codex` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for OpenAI Codex driven through its
Claude Code companion plugin, shared by `codex-adversarial-review`
(read-only sandbox) and `codex-implement` (`workspace-write`). It sits under
the review skill's directory for the same reason the other families' runtime
files do. Every item was paid for with a real incident (companion 1.0.x era;
re-verify against your installed version).

## Resolving the companion

Never hardcode a home directory or plugin version:

```bash
SCRIPT=$(ls -d "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs \
  2>/dev/null | sort -V | tail -1)
[ -n "$SCRIPT" ] || { echo "codex plugin not installed"; exit 1; }
```

If the runtime is missing or unauthenticated, stop and ask the user to run
the plugin's setup; do not fall back to a less isolated direct CLI command.

## Launching: `--background` is a promise the companion does not keep

`--background` MAY create a tracked companion job and return a job ID — but
do not rely on it. Measured: a launch fell into **launcher-output mode**,
streamed the entire run through the launching command, and was killed by the
shell tool's default timeout (exit 143), losing the run.

- **Always launch under the host's own background mechanism** with
  stdout/stderr redirected to a file.
- **Never pipe the launcher through `head`/`tail`/anything lossy.** In
  launcher-output mode that stream is the ONLY copy of the report — measured:
  a `| tail -6` on a launch silently destroyed a completed frontier-tier
  review. Redirect to a file; filter when *reading*, never when launching.
- **Two delivery modes — check both.** If a job ID and job log appear, the
  companion's `status`/`result` commands work. If not, the job never appears
  in `status --all` and the launcher's output file IS the delivery channel —
  look for `Thread ready` / `Turn started` there before concluding the
  launch failed.

### Getting TOLD it finished, instead of remembering to look

Backgrounding the launch is not the same as backgrounding the work. With
`--background` the companion returns as soon as the job is accepted, so a host
that notifies on "background task finished" fires within seconds — while the
delegate has not started thinking. Nothing then wakes the lead, and the run
sits complete until somebody thinks to check.

Measured 2026-08-25, one session, three rounds in a row: the delegate finished
14, 20 and 40+ minutes before the lead noticed, every time only because the
human asked. Two of those were refusals the lead had explicitly asked for and
should have acted on immediately.

**The rule, and it is not codex-specific: the host's background mechanism must
wrap the thing that takes the time.**

- A CLI that runs in the FOREGROUND (`agy`, `opencode run`, `codex exec`) is
  already the long-running thing — hand it to the host background mechanism
  directly and **do not add `&` or `nohup ... &` inside**. Detaching it makes
  the host task exit at launch and throws the notification away. (Measured in
  the same session: an `agy` review launched with an inner `&` produced a
  "completed" notification in under a second, and the leg had in fact died.)
- The companion's `task --background` does NOT run in the foreground, so it
  needs a second host-background step that blocks until the job is terminal:

  ```bash
  # 1. launch (returns immediately, prints the job id)
  cd "$WORKTREE" && node "$SCRIPT" task --background --write --fresh \
      --model <model> --effort <effort> --prompt-file "$TASK_FILE" \
      > "$RUN_DIR/launch.log" 2>&1
  JOB=$(grep -o 'task-[a-z0-9-]*' "$RUN_DIR/launch.log" | head -1)

  # 2. THIS is what goes in the host's background mechanism.
  #    The resolver is required: a skill runs with the TARGET repo as cwd, so
  #    a bare scripts/… would point at the user's project and exit 127.
  DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
  [ -x "$DEV_LEAD/scripts/await-codex-job.sh" ] || { echo "dev-lead root unresolved — set DEV_LEAD_ROOT"; exit 1; }
  "$DEV_LEAD/scripts/await-codex-job.sh" "$JOB" "$WORKTREE"
  ```

  `await-codex-job.sh` polls the companion's `status` for a terminal state and
  falls back to 20-minute log quiescence for the launcher-output mode where no
  job is ever registered. It exits 0 on terminal, 1 on its own timeout.

Do not poll by hand between turns instead. A hand-rolled poll loop is a live
task the user can interrupt, and interrupting it is indistinguishable from the
job ending — one such loop was killed mid-run in the same session and the lead
briefly believed the delegate had stopped.

## Watching a run

- **`status` computes "running" from `startedAt` and never checks the
  process.** Two dead jobs read as "running, 47h" after a machine slept. The
  `pid` in the job JSON is the launcher wrapper, not the worker.
- **Liveness is log mtime. Nothing else works.** Job logs live under the
  plugin's state directory:
  `$HOME/.claude/plugins/data/codex-openai-codex/state/<repo-slug>/jobs/`.
- **Be patient around collaboration/wait tools.** The job pauses inside them
  and writes nothing while paused; one pause returned in 10 s, another never
  did. Use ~20 minutes of silence before concluding anything.
- **There is no `--help` on the subcommands — probing flags runs a real,
  billed job.** Unrecognized arguments are swallowed into the prompt/focus
  text; `adversarial-review --help` launches an actual review of whatever
  your cwd happens to be (measured — one job burned reviewing an unrelated
  dirty tree). Verify flags by reading the companion source
  (`parseCommandInput` and each subcommand's options), never by trial
  invocation.

## Reading a report

- `Assistant message captured:` lines in the log are **truncated** — never
  judge a round by them. The full report is emitted after `Turn completed.`;
  split on the last captured message and read the tail.
- **A round can end with empty structured findings while the real
  observation sits in the prose summary of an interim progress message.**
  One confirmed bug arrived that way. Grep the interim messages before
  declaring a round empty:

  ```bash
  grep -o 'Assistant message captured: .*' <log-or-output> | cut -c1-400
  ```

- **If a report is lost entirely** (no job log — the `--background` task
  path may record nothing), recover from
  `~/.codex/sessions/<date>/rollout-*.jsonl`: the final `agent_message`
  payload is the report, and `custom_tool_call` inputs contain the exact
  patch texts if the working tree itself is ever damaged.

## Sandboxes: what each role's boundary can and cannot do

The review path runs `read-only`; the task path runs `workspace-write`.
Never add a bypass flag to either
(`--dangerously-bypass-approvals-and-sandbox`) — the sandbox IS the
boundary. Measured limits of `workspace-write` (structural, not delegate
errors; re-measure when the sandbox changes):

- **It can never commit from a git WORKTREE.** The worktree's real `.git`
  lives under the main repo's path, outside the writable scope, so
  `index.lock` fails with EPERM. Expect implement work back UNCOMMITTED; the
  lead commits after verifying. An honest delegate reports exactly this.
- **It cannot bind AF_UNIX sockets**, so socket-based tests fail at setup
  inside the sandbox. Pre-brief this in task prompts (name it as an
  environment limit; tell the delegate to run what it can and list what it
  could not) — otherwise the delegate burns its run discovering it, or
  worse, soft-pedals the gap.
- **It has no network, and that silently rewrites what a test asserts.** This
  one does not announce itself as an environment limit, because nothing errors:
  a test whose mock failed to bind still runs, the real function underneath
  reaches for the network, raises, and the code under test returns its
  fallback — which is often perfectly serializable, so the assertion passes.
  Measured 2026-08-30: the delegate reported "3 passed" twice for exactly the
  three tests the lead was measuring as **failed** on the same commit. Neither
  side was lying and the suite totals matched digit for digit, so the standing
  totals check (`dev-lead` Phase 1) cannot see this class. What settles it is
  re-running the named tests yourself; and the divergence is itself the
  finding — a test whose verdict depends on whether the runner has network is
  a test that hits live network, which was the actual defect that round.

These limits are also why a codex COMPANION delegate cannot take the *lead*
role on repos whose suites need sockets or worktree commits — while the same
vendor's interactive CLI under an approved elevated runner can (capability
is a property of the runtime, not the brand; see `dev-lead`'s host
capability gate).

## Calibration journal

Per the journal format — verified hit rates, one row per run, appended never
rewritten.

| date | model | role | outcome |
|---|---|---|---|
| 2026-08-30 | gpt-5.6-terra xhigh (companion) | implement | Two rounds (a date-arithmetic test bug; a test-isolation sweep binding 7 unbound mocks). Both landed correct after one fix round. Round 2 reported a green the lead measured as red — root cause was the sandbox's absent network, not delegate dishonesty (see Sandboxes above); the divergence exposed the round's real defect. |
| 2026-08-30 | gpt-5.6-luna (companion) | review | Repeat extra leg across several rounds on a *challenge* brief ("is this approach right at all", never "find defects"). Its principal finding was disjoint from the named-family legs' more often than not — the cheapest leg with the most distinct coverage this session. Route it the challenge brief specifically; on a plain defect-hunt brief it duplicates. |
| 2026-08-31 | gpt-5.6-terra (companion) | review | The round's only leg to reject a claimed property, and lead-verified correct: agy, cursor/grok and laguna all returned HOLDS on a 15-case "every invariant" mutation suite; terra alone traced that two mutations `clear()` a collection and so only trip an outer cardinality guard, leaving ~10 inner predicates deletable without failing a test — plus one missing guard the table omitted entirely. Same round it correctly cleared the change's actual fix (3 other HOLDS). This is the case for paying for a 4th leg: 3-of-4 agreement is not evidence. |
| 2026-08-31 | gpt-5.6-terra (companion) | review | Prior round, same stack: found a real idempotency defect (approval key bound to a mutable auth context, permitting a duplicate record on a cross-call retry) that the other three legs missed while calling the property HOLDS. Two consecutive rounds where terra was the sole dissenter and the sole one right — on this account it is the leg to keep when trimming, not the one to drop. |
| 2026-09-01 | gpt-5.6-terra (companion) | review (2 code changes + 1 plan doc, 11 posed items) | Third consecutive round contributing a finding no other leg reached, and this one was structural: a test named "PF-10→PF-12 PostgreSQL integration" seeds real PF-09/PF-10 rows and then hands the bundle an in-memory fake resolver whose `resolve()` returns a hardcoded member — so the PF-10 authority boundary, the exact thing the change claims to establish, is never exercised. Lead-verified. Also sole finder of a second uncovered guard (`record.artifacts.empty()`, no matching mutation). ~14 min. **The reliable shape across three rounds: terra finds the gap between what a test is NAMED and what it actually reaches.** Give it the "does this evidence evidence?" brief and it earns its slot. |

## Model and effort plumbing

- The **task path** takes `--model` and `--effort`
  (`none|minimal|low|medium|high|xhigh` — the flag's maximum is spelled
  `xhigh`).
- The **review path** takes `--model` only — review depth is expressed
  through model choice plus the user's global config.
- Codex itself goes higher than the flag (`max`, `ultra`) but only via
  `~/.codex/config.toml`'s `model_reasoning_effort`, which any run WITHOUT
  an explicit `--effort` inherits. Check it when depth matters:
  `grep model_reasoning_effort ~/.codex/config.toml`. Never modify the
  user's config from a skill run. Passing `--effort` explicitly on implement
  runs is what keeps an implement-cheap / review-deep split stable while the
  user tunes their global freely; omitting it is a deliberate act — say so
  in the run log, because the resulting effort then depends on machine
  state.
- Omitting `--model` inherits the user's codex-config default — pass it
  explicitly rather than inheriting silently.
- There is no reliable quota API. Probe availability by attempting the run
  and treating a quota/rate error as "unavailable this run"; expect the
  quota wall to differ per model tier and per path (task vs review).

## Fresh threads

Use a fresh thread (`--fresh`) for a new task; never reuse a thread across
unrelated work — context from the previous task contaminates the premise of
the next.

## Raw-CLI fallback (no Claude Code on this machine)

The role skills drive codex through its Claude Code **companion plugin** by
default, because the companion adds real things: tracked jobs
(`status`/`result`), a managed read-only review sandbox, and the
adversarial-review command's built-in framing. On a machine without Claude
Code, the same workflow runs against the codex CLI directly:

```bash
# review leg (read-only) — prompt from a file, output to a file, backgrounded
codex exec --sandbox read-only --cd "$REVIEW_TARGET_DIR" -m <model> \
  "$(cat "$RUN_DIR/prompt.md")" > "$RUN_DIR/review.out" 2>&1

# implement leg (workspace-write) — inside the worktree the lead created
codex exec --sandbox workspace-write --cd "$WORKTREE" -m <model> \
  "$(cat "$RUN_DIR/task.md")" > "$RUN_DIR/impl.out" 2>&1
```

Troubleshooting, for a shape this suite does not prescribe: `--cd` must name
a directory inside a git repository. The prescribed flow never trips this,
because [`materializing-evidence.md`](../../../docs/materializing-evidence.md)
puts codex in the frozen worktree and passes extra evidence through the focus
text. Point `--cd` at a bare evidence directory instead — assembled with
`git show`, outside any repo — and codex refuses to start with
`Not inside a trusted directory and --skip-git-repo-check was not specified.`
(observed 2026-08-19, codex 0.148.0). That line is the entire output, so a
lead polling for a report finds a 115-byte file and reaches for the model.
Add `--skip-git-repo-check` if you deliberately review a plain directory.

Verify the exact flags against your installed version with
`codex exec --help` first — unlike the companion (where probing flags runs a
billed job; see above), **the raw CLI has a real `--help`**, so probing is
safe here. Per-run effort, where supported, rides on config overrides
(`-c model_reasoning_effort=<tier>`) or your global `~/.codex/config.toml`.

What you keep: the sandbox boundary (the CLI's own `--sandbox` modes are the
same enforcement layer the companion wraps), headless execution, model
selection, and everything in this suite's prompts and verification
discipline — none of that ever depended on the companion.

What you lose, honestly:

- **Job tracking.** No `status`/`result`; stdout redirected to a file IS the
  delivery channel, and the process's own liveness is the only liveness
  signal. Launch under your host's background mechanism, exactly as the
  launcher rules above already require.
- **The companion's review framing.** `adversarial-review` sets up base/scope
  handling for you; raw exec means the review skill's focus prompt must
  carry the base SHA and file list itself (it already should — see the
  review skill's span-pinning section).
- **Log-based recovery.** No companion job log; session rollouts under
  `~/.codex/sessions/` remain your only after-the-fact recovery channel.
- Never substitute `--sandbox danger-full-access` (or any bypass spelling)
  for a missing capability — the same rule as the companion's bypass flag.

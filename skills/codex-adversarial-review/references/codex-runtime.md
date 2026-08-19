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

These limits are also why a codex COMPANION delegate cannot take the *lead*
role on repos whose suites need sockets or worktree commits — while the same
vendor's interactive CLI under an approved elevated runner can (capability
is a property of the runtime, not the brand; see `dev-lead`'s host
capability gate).

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

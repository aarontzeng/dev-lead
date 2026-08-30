# `claude` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for headless Claude Code (`claude -p`),
shared by `claude-adversarial-review` (read-only via `--permission-mode
plan`) and `claude-implement` (write via `--permission-mode acceptEdits`).
It sits under the review skill's directory for the same reason the other
families' runtime files do.

This is the delegate direction for the Claude family: how **any lead —
including a codex or agy lead** — hands bounded work to Claude. All four
CLIs can share one skills directory (symlink the others' skills paths to
Claude's), so a foreign lead can find and follow these files.

Inside Claude Code itself, prefer the built-in subagent/Agent tooling for
context isolation — it is cheaper and already integrated. Reach for
`claude -p` from a Claude lead only when the delegate genuinely needs its
own working directory, its own permission mode, or a different model.

## Invocation shapes

Review / read-only worker:

```bash
claude -p --permission-mode plan --model <tier> \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  < "$RUN_DIR/prompt.md"
```

Implementation worker (inside a git worktree the lead created):

```bash
cd "$WORKTREE" && claude -p "$(cat "$RUN_DIR/task.md")" \
  --permission-mode acceptEdits --model <tier>
```

## Measured behavior

- **A trailing prompt argument is safe only while no variadic option
  precedes it.** `--add-dir` takes a variable number of values, so
  `claude -p --add-dir "$DIR" "$(cat prompt.md)"` feeds the prompt to
  `--add-dir` and the run dies with `Input must be provided either through
  stdin or as a prompt argument when using --print` (measured 2026-08-19,
  claude 2.1.235) — which reads like a missing-prompt bug rather than an
  argument-order one. The positional form elsewhere in this file and in
  `claude-implement` is fine as written, because neither passes a variadic
  option; the review invocation redirects from stdin because that shape has
  no ordering hazard at all and the reviewer is the leg most likely to grow
  an `--add-dir`. Do not generalise this to other families: opencode's rule is
  stricter and for a different reason — argv there cannot carry a real prompt
  at all (it hangs above ~2 KB), so its prompt must come from a file. Take the
  shape from the family's own runtime note, never from the leg you ran last.
- **Headless auth just works** — no silent-auth dance (contrast agy).
- **`acceptEdits` covers both file writes and shell/git in one flag.** It
  wrote files, ran `git add`, and committed without a single prompt or hang.
  There is no allow-list to maintain (contrast agy's per-machine
  `unsandboxed(…)` rules).
- **Working directory is the shell's cwd** — plain and predictable; no
  workspace-root trap (contrast agy's scratch-dir default).
- **Commit messages come out clean** — no AI-authorship trailers, when the
  user's instruction layer forbids them (see below).
- `--model` selects the tier. Pick by the same logic as any delegate:
  capability for the work, and never the same family as the reviewer that
  will check it.
- Do **not** pass `--dangerously-skip-permissions`. `acceptEdits` was
  sufficient for real write work in testing; the bypass flag would also
  auto-approve things the standing rules forbid.

## Cut the MCP stack for a read-only reviewer

Without `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`, `claude -p`
loads the user's whole MCP configuration before it reads a line of code —
measured: external tool servers were still spawning minutes in, and the lead
watching from outside read the silence as a hang. A review needs the
filesystem and git, not an issue tracker. Keep servers only if the prompt
genuinely uses one.

## Patience: budget 5–15 minutes, expect silence

A real adversarial review of a module-sized change takes that long in every
family (measured here: 6m21s on a ~46 KB package). Two things make it look
dead when it is not:

- It sits in `S (sleeping)` with near-zero CPU the whole time — waiting on
  the API. `ps` cannot distinguish that from a hang.
- **The transcript does not grow during a turn.** Measured: 307 seconds —
  over five minutes — of zero writes between the prompt and the first
  assistant event, then the whole review in the following 74 seconds. A
  static transcript is the NORMAL state of a thinking delegate, and it is
  most static exactly when an impatient lead is most tempted to kill it.

There is no cheap liveness probe. What works:

```bash
ps -o pid=,etime=,stat= -p "$PID" || echo "exited -- read the output"
```

Process gone means done: collect the result. Process alive means keep
waiting. Set the hard limit generously (15+ minutes); if you must abandon,
record it as an unfinished delegate rather than a green light. Reporting a
working delegate as timed out is the failure mode here — a live pilot came
within one wait of doing exactly that, on a run that finished in 6m21s.

## Plan mode has no exit in a headless run — say so in the prompt

`--permission-mode plan` is what makes the delegate read-only, but its
normal ending is to hand a plan to a human for approval, and `-p` has no
human. Measured: the delegate wrote a plan file, then spent three
consecutive tool-search calls hunting for an exit-plan tool before giving up
and emitting the review as plain text. It recovered on its own, but that is
~50 seconds and a stray artifact per run. End the prompt with: *"Deliver the
review as your final text message. Do not write a plan file, do not attempt
to exit plan mode, and do not ask for approval — there is no interactive
user."*

## The property that makes this delegate different

**It inherits the user's global instruction layer.** Asked to push, with a
remote configured and `acceptEdits` granted, a measured run refused and
printed the command for the human instead — quoting the user's standing
no-push instruction verbatim. So the instruction layer travels with the
delegate for free.

That is a **different kind of guarantee** from a machine-enforced allow-list
(the agy/opencode approach). Neither is strictly stronger: instruction-level
survives commands nobody anticipated (any phrasing of "publish this");
permission-level survives a model that misreads its instructions. When a
lead delegates something destructive-adjacent, state the rule in the task
prompt anyway — belt and braces, at zero cost.

## Calibration journal

| date | model | role | outcome |
|---|---|---|---|
| 2026-08-30 | claude-sonnet-5 (subagent) | review | Repeat extra leg across several rounds, given the **test-quality** brief (are these tests able to fail? do they pin the contract or mirror the code?). Repeatedly the only leg to return anything on rounds where the defect-hunt legs came back clean — its findings were about the SUITE, which the other briefs structurally do not look at. Weak as a general defect hunter beside the frontier legs; strong and cheap in this one role. |

## For a foreign lead (codex or agy orchestrating)

The pieces you still own, which `claude -p` does not give you:

1. **Worktree isolation** — create it before dispatch; never let the
   delegate work in the main checkout.
2. **Independent verification** — re-run tests yourself; mutation-proof new
   regression tests. A delegate's "tests pass" is not evidence.
3. **Cross-family review** — a Claude delegate's work must be reviewed by a
   non-Claude family, never by another Claude context, and not by a
   stealth model whose family might be Claude.
4. **Patience calibrated to the work** (see above).
5. **The merge gate stays human.** Present the diff and verified findings;
   merge only on explicit approval; never push.

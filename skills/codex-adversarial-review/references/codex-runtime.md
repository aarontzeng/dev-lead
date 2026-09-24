# `codex` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for OpenAI Codex -- review through
`codex exec` with the suite's framing, implement through its Claude Code
companion plugin -- shared by `codex-adversarial-review` (read-only sandbox)
and `codex-implement` (`workspace-write`). It sits under
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
- **`codex exec` with an argv prompt needs `< /dev/null` when backgrounded
  (measured 2026-09-07).** Handed to a host background mechanism with stdin
  left open, it prints `Reading additional input from stdin...` and blocks
  there forever — the prompt is already in argv, so nothing will ever arrive.
  It looks exactly like a long-running review: the process is alive, the CPU is
  idle, the output file holds those 39 bytes and never grows. One round lost
  ten minutes to it before anyone read the file. The other three adapters in
  this suite do not do this, so a lead who has only launched them will not
  expect it.

  ```bash
  codex exec --sandbox read-only --cd "$REVIEW_TARGET_DIR" \
    -c model_reasoning_effort=<effort> -m <model> \
    "$(cat "$RUN_DIR/prompt.md")" < /dev/null > "$RUN_DIR/review.out" 2>&1
  ```

  The tell, before you wait: a healthy leg's output file grows within the first
  minute. One stuck at exactly the size of that banner is not thinking.
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

## The session refuses to start if a project MCP server is unreachable

**Measured 2026-09-07.** A review launched in a repo whose `.mcp.json` carried a
stale server token died before any thread existed:

```
error creating thread: Fatal error: Failed to initialize session:
required MCP servers failed to initialize: <server>: ... HTTP 401: unauthorized
```

The failure is in the CLI's own stderr, not in a review log, and it costs the
whole leg. Two things follow. **MCP reachability is a hard startup dependency
for this adapter**, so a token that expired since the last run turns a healthy
model into a dead leg — and the repo you review in decides which `.mcp.json`
applies, which is not necessarily the one the lead's own session is using (the
lead's session kept working throughout, which is what made this confusing).
And it is indistinguishable at a glance from a quota or transport failure, so
`grep -i 'failed to initialize' "$RUN_DIR"/*.err` belongs in the same triage
list as the 50x and `auto-rejecting` checks. Fix the token, or launch from a
directory whose MCP config resolves; nothing about the prompt is at fault.

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
| 2026-09-01 | gpt-5.6-terra (companion) | review (2 code changes + 1 plan doc, 11 posed items) | Third consecutive round contributing a finding no other leg reached, and this one was structural: a test named as a PostgreSQL integration test seeds real rows and then hands the bundle an in-memory fake resolver whose `resolve()` returns a hardcoded member — so the authority boundary, the exact thing the change claims to establish, is never exercised. Lead-verified. Also sole finder of a second uncovered guard (an empty-collection check, no matching mutation). ~14 min. **The reliable shape across three rounds: terra finds the gap between what a test is NAMED and what it actually reaches.** Give it the "does this evidence evidence?" brief and it earns its slot. |
| 2026-09-01 | gpt-5.6-terra (companion) | review (spec freeze doc, 6 posed items) | **Fourth consecutive sole finding, and it became a primary ground for the lead's −1.** On a document whose stated job is to freeze semantics for two owners implementing separately, it found that an `UNAVAILABLE` result carries exactly one typed reason while three independent condition→reason mappings are given with no precedence — so a source that is simultaneously gapped, stale, and provenance-unverified gets a different wire reason depending on validator order, and the selector is required to preserve that reason. Lead-verified against both passages. It also independently escalated the deadline item to the membership effect, matching the grok leg and correcting the lead's weaker hypothesis. ~10.5 min. **Note the generalisation across four rounds: the sole findings are all one shape — a claimed property whose named evidence cannot actually exercise it.** Deviation worth knowing: despite an explicit "do not spend turns on tool discovery" line it opened by reading local skill files and probing for the team's contract file; read-only and harmless, but the instruction did not suppress it. |
| 2026-09-02 | gpt-5.6-terra (companion) | review (3 document changes, 7 posed items) | **Fifth consecutive sole finding, and it was the one item nobody posed.** The brief asked seven questions about an ADR's command-result contract; terra answered those and then added an eighth: the same ADR's acknowledgement contract takes the acknowledging user from a caller-supplied header, directs both the UI and an evidence query to trust the resulting acknowledgement state, and sits behind an HTTP front end with no authentication filter — so any host that can reach it can acknowledge an alert and forge its attribution, and the ADR notarises that as an accepted contract. Lead-verified verbatim at both the controller and the front end's route table; it became a named ground in the lead's −1. **The five-round generalisation now needs widening**: the shape is not only "a claimed property whose named evidence cannot exercise it" but "a document treating an untrustworthy artifact as evidence" — the named test that cannot reach the property, and now the caller-controlled string consumed as safety evidence, are the same error at different layers. ~3.2 min. One gap: item (c) (severity calibration of a 23-finding audit) got a summary sentence with no finding block and no spot-check evidence, though the log shows it ran the grep — it says one finding's "**core** is confirmed" and never spells out the reservation. When an item needs enumerated evidence, say so per-item; terra will otherwise fold a checked item into the verdict line. |
| 2026-09-02 | gpt-5.6-terra (companion) | review (command-sequencing change, 11 posed items) | **Sixth consecutive sole finding, and the two it contributed were the round's most severe — both became named BLOCKING grounds in the lead's −1.** (1) It reframed a path another leg had found and mis-sized: the retry loop publishes a terminal failure and erases the pending row, but the sequence advances on *device state*, not on that row — so when a command reached the device and only its acknowledgement was lost, the UI reports failure while a later status update still executes the command. The lead had independently downgraded the other leg's version of this to MINOR; terra's framing corrected the lead. (2) Sole finder that the new correlation key `(device, command)` does not separate a command from its inverse, which share one command code — so the acknowledgement for one is published carrying the other's request id and the UI can report the opposite of the device's real state. **The five-round generalisation now needs a third widening**: the shape is not only "evidence that cannot exercise the claim" but "a fix whose stated invariant is false for the one case where being wrong is dangerous". Two operational costs this round: the documented tool-discovery deviation recurred (it opened by reading local skill files and grepping for the team's contract file despite an explicit no-discovery line) and that attempt then died on a usage limit at 508 s, forcing a 20-minute wait and a re-run (846 s). And the fold-into-the-verdict-line pattern is now n=2: four of its negative verdicts were one-clause summary sentences with no file:line and no evidence. Ask for enumerated per-item evidence explicitly or terra will spend its budget on the findings and pay for the HOLDS out of the verdict line. |
| 2026-09-05 | gpt-5.6-terra | review ×4 (sequences lens: mobile R1, web R1, web R2; plus the web money path) | **Found the round's only CRITICAL**: web dialog `Number.parseInt` truncates "2.5" → 2 and the action is created already-confirmed, so a user applies a 2:1 they never typed. On R2 it then found the residual in the fix (`Number("9007199254740993")` rounds past 2^53; backend INTEGER cap) — the lead fixed that one directly. Two of its findings were independently converged on by another family (mobile `pfhistory` not invalidated ← big-pickle; web dashboard aggregate not revalidated ← gemini), which is the strongest signal available. One rejected: "empty transactions returns [] before the loading check" — harmless ordering, zero transactions cannot project wrong. Reliable sequences leg; its severity words are calibrated (no inflation this round). ~10 min per leg. |
| 2026-09-05 | gpt-5.6-terra (**raw `codex exec`, `-c model_reasoning_effort=medium`**) | review (10-change UI stack, 8 posed items, reviewed per-diff) | **First `medium` round, and the first via raw exec** — the companion's `adversarial-review` has no effort flag and would have inherited `max` from the user config. 176 s, fastest leg by 2x, zero NOT REACHED; the tool-discovery deviation did NOT recur and every item got its own evidence block (both n=2 patterns from 2026-09-02 absent). Converged on the BLOCKING with all legs; **sole finder of an ungated command path** (one command forwarded to the message bus with no ownership check, while its two neighbouring commands perform it) — lead-verified, a named MAJOR ground in the −1. Also the clearest statement of one change's fail-open catch (two different missing inputs both mint a default before the operator is told). Two of its findings blamed to pre-stack lines (an unvalidated numeric field, an unconditional follow-up command) — the supervisor's `git blame` scope check caught both; ask for that check on every stack round. 43/50 citations exact; all 7 misses were in the two files it read with `sed -n` instead of `nl -ba`, uniformly shifted 2–4 lines. **Supervisor note, not the model's**: the supervising subagent returned early twice on "the monitor will notify me" before blocking; the fix that held was a single Bash call with an explicit sleep loop on the output marker. |
| 2026-09-05 | gpt-5.6-terra (raw `codex exec`, `-c model_reasoning_effort=medium`) | review (ownership-transfer change across four services + frontend, 8 posed items) | **Fastest leg again (176 s vs 388/377/874) and the one that escalated correctly.** Three legs found that a release query has no state predicate excluding cancelled rows; two called it MINOR (a wrong comment). Terra alone chased the consequence: a repeat release still returns a row, still reports RELEASED, still publishes downstream — and the consumer never compares the message's batch id to the execution it acts on, so a stale release stops whatever job the device is running **now**. Lead-verified; it became a named BLOCKING. That is the sixth-plus round where its sole finding has the same shape: a stated invariant that is false in the one case where being wrong is dangerous. **One overreach**: it graded the release/upload ordering MAJOR without checking that both queues drain in the same loop iteration — the agy leg falsified it and the lead dropped it. Ask terra for the ordering *argument*, not just the ordering *risk*. Per-item evidence blocks held (the fold-into-verdict pattern stayed absent for a second `-medium` round). |
| 2026-09-06 | gpt-5.6-terra (raw `codex exec`, medium) | review (a data contract across 3 services + a migration, 10 posed items) | **35 of 35 citations exact — the first perfect citation round on this account — and it got there by following an instruction.** The preamble said to read with `nl -ba`; it used `rtk nl -ba … | sed -n` on all 14 file reads, and the 2-4 line drift its 2026-09-05 row recorded vanished. 228 s, fastest leg by 30%, zero NOT REACHED, per-item evidence blocks for a third consecutive round. It ran `git blame` unprompted and reported that 3 of its 34 cited lines were pre-existing, including the one that falsifies the commit message's pagination claim. **It also downgraded its own findings twice, correctly**: it refused the brief's 'two rows claiming the same revision' framing (the PK turns the race into a lost insert) and refused 'rows skipped or duplicated' on the pagination item (underfilled pages, nothing lost). Both downgrades survived the lead's check; a leg that argues its own severity DOWN is rarer than one that finds the defect. One miss: it graded the migration backfill MAJOR without noticing the poll has no LIMIT, which is what makes the flood unbounded. **Operating note: give terra the `nl -ba` instruction in every preamble — it is a one-line fix for the only defect this account still had.** |
| 2026-09-06 | gpt-5.6-terra | review ×2 (sequences on S2 detection; then the fix round) | R1: two CRITICALs in S0 loader code the lead had shipped days earlier — a user's manual split plus a global confirmation applied the ratio twice, and `.TW`/`.TWO` did the same — plus the mobile role-state write after an await with no epoch fence. All three verified; lead fixed the loader itself. R2 check found two more real ones the fix round introduced or left: refresh promotion dropped the admin role (defaulted arg), and FinMind's swallowed `[]` still counted as a clean scan. Its residual (overlapping runs on symbol aliases) was correctly labelled as contingent. Four rounds on this feature, zero rejected findings. |
| 2026-09-06 | gpt-5.6-terra (--effort medium) | review ×2 (sequences on a lead-written S3 change + #30) | Strongest leg of the round at medium effort. Found the CRITICAL the lead shipped: the Python importer accepted an `actions` argument and never wired it — never passed to the oversell replay, never persisted, then an undefined counter referenced after commit (a stale `replace()` in the lead's own edit). big-pickle converged on the same from the consistency side (NameError on the dead `written_actions`). Also: an importer-inferred user row outranking a confirmed global fact, and the web import continuing to post rows after an action POST fails (half-import a retry duplicates). Every finding verified; none rejected. medium effort was plenty for a diff this size — no depth lost vs the high-effort S2 round. |
| 2026-09-07 | gpt-5.6-terra (raw `codex exec`, `-c model_reasoning_effort=medium`) | review ×2 (authorization-gate change, 8 claims; then the fix round, 5 claims) | **The leg that caught the fix round's own new bug, which is the harder half of the job.** R1: converged with three families on the round's central defect and was **sole** on the "same snapshot" guarantee being over-claimed — the precheck binds the patchset, but nothing is atomic against the server, and the ADR said otherwise. Its C8 mutation matrix matched the lead's own run and flagged the one flag-assertion nobody had pinned. R2 (the fix round): the lead's 409 disambiguation matched the substring `patchset` — which **both** refusal messages contain — so the bug it was written to fix survived in a new shape; terra found it, with `git blame` naming the commit that introduced it. Also named a surviving `any()`→`all()` mutation whose test only used lists where every element was bad. Its design challenge was half right: the naming and the undocumented response contract were conceded, the alternative was rejected (it would have preserved the re-derivation four families had just converged on). **Operating note, now upstream: `< /dev/null` when backgrounded with an argv prompt** — this round lost ten minutes to it hanging on stdin. |
| 2026-09-07 | gpt-5.6-terra (**companion `adversarial-review`** — so `max`, NOT the medium the lead intended) | review (6-line C++ gate in a network daemon, 6 posed properties, lead had already hardware-verified the change) | **1 of 2 findings survived, and the one that did was sole and structural.** Its HIGH ("a stable disabled state suppresses required reset/recovery reloads") named the reset callers, and the lead falsified that path: those callers unbind and unload the driver first, so the interface it worried about is already unloaded and there is nothing for the skipped reload to apply. But the abstract core of it was right and was recorded on the issue — the new gate trusts the daemon's in-memory model rather than probing the hardware, so the old code's incidental "re-assert down every pass" repair is gone. Its MEDIUM was the round's only structural contribution and no other leg reached it: the previous-state statics are keyed to a logical SLOT while that slot can map to a different physical interface under a different configuration, so a mapping change compares one interface against the other's history. Currently unreachable — a mapping change always sets a flag that triggers the same unbind-all — but it fails SILENTLY if anyone ever narrows that block, which is exactly the class the lead would not have found alone. **Two lead errors, both already documented here and both ignored at dispatch**: the companion path has no effort flag (row 200 says so) so this ran at `max`; and the preamble omitted the `nl -ba` line (row 202's operating note), after which its citations came back as coarse ranges (`:713-745`) instead of exact lines. Also: the first attempt died at startup on an MCP 401 — see the new section above. |
| 2026-09-08 | gpt-5.6-terra (companion `adversarial-review`; **the lead asked for `medium` and this time the inherited config actually was `medium` — `~/.codex/config.toml` reads `model_reasoning_effort = "medium"`, so the row-227 mismatch did not recur. Still unconfirmable from the run log; the only way to KNOW is raw `codex exec -c`**) | review (86-line C++ change adding a periodic config broadcast; challenge-the-approach brief, 8 posed properties) | **4 findings, 4 survived — and it was the only leg that answered the question the brief actually asked.** Given a challenge brief (is this the right mechanism for the issue at all?) it produced the round's CRITICAL and it became the lead's first −1 ground: the reconciliation datagram travels over the very link it is meant to repair, while every divergence state the issue enumerates is a config mismatch that prevents that link from coming up — so in the issue's own failure mode there is no path and the broadcast never arrives. The change's own comment concedes it; no other leg treated that concession as disqualifying. **Its second finding is the one worth generalising**: told the commit message claims older peers "can ignore this extension", it went and read the BASE revision and found the ignore path leaks — a JSON parse at base `:3486`, the unknown-message `else` at `:3552`, `continue` at `:3557`, no matching free between them — so a broadcast every 10 s means ~8640 leaked objects/day on an un-upgraded peer. Every base coordinate verbatim-exact on the lead's re-check. **Seventh-plus consecutive round contributing a sole finding, and the shape has widened again**: from "evidence that cannot exercise the claim" to **"a compatibility claim about code the author never opened"**. Also the only leg to mark a claimed property BROKEN on the right grounds (a pending-apply flag is a thread-stack variable, so every listener restart forces a full driver reload). ~6 min. Dispatch it whenever the change's own commit message makes a claim about a peer, an older version, or another repo — it will go read that thing. |
| 2026-09-08 | gpt-5.6-terra (companion; config `medium`) | review (PS2 delta of the same network-daemon change, briefed as **challenge the ANSWER, not the problem** — PS1's blockers were closed and the question was whether the author's fix was the right shape) | **3 findings, 3 survived, and the brief shape is the transferable part.** Given "PS1 was blocked on two grounds and PS2 is the answer; is the answer the right shape and what did it cost?", it produced the round's only systemic observation: the new marker gate returns silently, and combined with an already-known path (a failed apply never clears the applied flag) the feature now has **two independent ways to stop forever with no log, counter, or status** — one of them a routine reboot, since the marker is deliberately volatile. Neither other leg framed those two as the same defect. It also correctly identified that the harness no-ops both JSON writers and discards every send argument, so the wire contract has no regression check at all — the lead then mutation-confirmed the consequence. Its third, the design objection (a hand-managed file is not capability detection, so the safety property holds only while operators are perfect), was relayed to the author but not treated as blocking: the pre-change state was "upgrading a publishing node harms peers automatically", the post-change state is "a deliberate action plus a mistake", which is a real improvement. **Standing note on brief design: when a change is an ANSWER to earlier findings, brief this leg on the answer's shape rather than re-running the original hunt — it is the leg that will ask what the fix cost.** |
| 2026-09-08 | gpt-5.6-terra (companion; config `medium`) | review ×2 of the same plugin change (**the first attempt refused to answer, correctly**) | **The refusal is the row's first lesson and it was the lead's fault.** Attempt one returned no findings and said why: the brief demanded a `file:line` evidence gate while forbidding "any other CLI invocation" and supplying no diff, so no source-backed finding was defensible. It was right — the lead had stripped the embedded diff to fit argv budgets on two other legs and never re-read the shared header that still promised one. Relaunched with reading explicitly allowed (`git show`, `wc -l`, open files) and only recursive delegation forbidden: 3 findings, 3 survived, evidence gate exact on 6/6 files. **Its headline finding is the one no amount of implementation could have surfaced**: the change's stated root cause was "skill text in the session's context is frozen at an old plugin version", and the remedy is a script — but *the instruction to run that script lives in the same file that gets injected stale*, so a session handed an old copy never learns the script exists. The change did not close its own stated hole, and the commit message claimed otherwise. It also found `verified:` was decorative (lint checked the field exists, never the date or the CLI) and that the billed-`--help` hazard was warned about rather than prevented, since the script prints a command and never owns the invocation. **Standing brief note extended: when a change is an ANSWER to earlier findings, this leg is the one that asks whether the answer addresses the cause the author himself named.** |
| 2026-09-23 | gpt-5.6-terra, THREE runs on one frozen target (peer session on another machine; n=1, reported not measured by me) | review of one commit: (a) companion `adversarial-review`, effort = that machine's config `high`; (b) companion `task --effort medium`; (c) `task --effort high`. Same lens text for all three; (b)/(c) additionally carried the range, the explicit file list and a required `Verdict:` line, which the review path supplies by itself | **Only (a) found the round's real defect; (b) and (c) both approved, and so did the agy / cursor / opencode legs on the same commit.** The defect: a new `ftruncate` + pid write on a lock sidecar opened without `O_NOFOLLOW`, so a pre-created symlink lets an unrelated file be truncated — the reporter reproduced it before believing it, and it was fixed with `O_NOFOLLOW`. **PATH at equal effort ((a) vs (c)): on this one commit the framing found it and the brief did not.** (a) ran 11 commands and read the file AT THE BASE REVISION (`git show <base>:<file>`), which is how it saw that the sidecar had previously been opened and flocked but never written; (c) ran 3 and never read the base version. **EFFORT on the same path ((b) vs (c)): no gain visible in this pair** — 76 s vs 165 s, 5 commands vs 3, both missed it; one pair that both missed cannot separate "effort does not help here" from "neither run was going to find it". Wall-clock (a) 119 s. Served model unnamed in all three outputs. Decision taken on this report (one commit, one reporter, not a measured rate): **keep `adversarial-review` as the review path**; when an effort must be pinned, `codex exec -c model_reasoning_effort=<e>` (above) is the route, and the brief then has to carry what the framing gave for free — scope, the verdict line, and "read each changed file at the base revision too", which is the single instruction this round says to add first. Caveat the reporter states: (a) could not be run at `medium` on that machine without editing the shared config, so path and effort are not fully separated for (a). |
| 2026-09-23 | gpt-6-luna (companion `adversarial-review`, so effort = this machine's config `medium` all day, NOT the roster's declared `xhigh`; the served model is not in the companion's output, so "luna" rests on the argument sent) | review ×15 in one session: a docs scrub (2), then fix-round re-reviews of a ~1,300-line Python rule engine and its roster tooling across four releases (13) | **Precise, fast, and narrow per pass — it digs one hole at a time.** ~11 findings verified real, zero fabricated, citations exact on every spot-check, 2–4 min a run. Sole finder of the round's most consequential defect: a fetch flag documented as "adds objects only" that still stored a ref through a configured refspec — reproduced by the lead on the target git version. Also caught two anonymising edits that had changed a calibration row's *fact*, which no grep would find. **But on one gating change it took SEVEN passes to converge**: each pass found exactly one more place the same gate was not honoured (a display path, then a fallback, then a second renderer, an implement leg, a container), and the brief did not ask for the whole path × placement table until pass 4 — even with the table, each later pass still surfaced one more. Its first pass on that change was a bare CLEAN with no per-claim table — the lead's own enumeration found the first hole. Twice it inflated scope: it read the brief's "check the empty-lenses case" as an agreed rule, and raised a free-form metadata field nothing dispatches from as a blocker. **Working rule from this one change (n=1, not yet a methodology rule): when a change adds a guard, ask this leg for the full (every consumer × every placement) table in pass 1, and treat a CLEAN without it as not-yet-reviewed.** |
| 2026-09-23 | gpt-6-luna, the SAME brief on the SAME frozen commit at three efforts: medium (companion), high and xhigh (raw `codex exec -c model_reasoning_effort=`; log headers confirm `model: gpt-6-luna` and the effort) | review of a ~240-line Python + docs change (a new config table and its per-adapter spelling), 6 posed properties | **On this one commit (n=1), xhigh bought recall and completeness; high bought completeness only.** medium ~3 min, 1 finding. high 122 s / 66k tokens, the same 1 finding plus a HOLDS-with-reason for every posed item and one test-gap note xhigh did not make — **and it explicitly called the README accurate when it was not.** xhigh 270 s / 82k tokens, the same finding plus that README/code mismatch (a field documented on every leg that one adapter class never emits), with per-item reasons. The shared finding was rebutted in all three. n=1 on one small change; it drove the owner to raise only codex, not every adapter, to xhigh in triage.json's effort table (0.6.25; applied to a codex review through `codex exec -c`, since the companion's review path reads config.toml). **Gotcha, already documented above and still hit: a backgrounded `codex exec` without `< /dev/null` blocked on stdin for ~20 min.** |
| 2026-09-23 | gpt-6-luna, raw `codex exec -c model_reasoning_effort=xhigh`, header-confirmed (peer session on another machine; reported, verified by that lead, not measured by me) | (a) spec/requirements prose, 2–25 changed lines, 4 runs plus 3 earlier rounds; (b) one large code change (C++17 + SQL migrations + tests, 38 files, +3.9k) as one of four legs with distinct lenses | **Prose (as that lead reports it): high precision, near-zero noise — that lead recommends it as the primary judgment leg there.** Fix-round runs of 1–7 min found a subtle reversed attribution three files agreed on, weighed and accepted a lead's rebuttal with a stated reason, and flagged "not verifiable from the files given" instead of guessing. **Large code (one change, n=1): precise but narrow and lens-bound.** 9 min / ~146k tokens, 2 findings, both mechanisms real but over-rated: it did not trace to the consumer that re-checks (a TOCTOU a later read mitigates), and it reported a defect the parent already had as this change's. It found none of the three blocking defects — the "claims vs tests" leg (cursor grok-4.7-medium) found all three. **Hypotheses from that lead, not yet rules:** on large code always field a claims-vs-tests leg rather than relying on this one for coverage; brief it to separate introduced from pre-existing defects (check the parent) and to trace each defect to its consumer for mitigation. |
| 2026-09-23 | gpt-6-luna, raw `codex exec -s read-only -c model_reasoning_effort=<tier>` with the suite's own `adversarial-framing.md` (v1), and the companion's adversarial-review prompt pasted verbatim as a local control (CV) | review, known-answer A/B on two frozen public targets | **T1** (triage fetch 3a170dc..27bd163, medium; known answer: a configured refspec still creates refs, fixed with `--refmap=`): own framing 4 runs (1 with the diff inlined), CV 3 runs, **refmap found in 0 of 7**. Six runs each reported one defect against the same "fetch only adds objects" claim — tag auto-follow writing `refs/tags` (3; the real fix also added `--no-tags`), auto-maintenance / commit-graph (2), `.git/shallow` in shallow clones (1) — and the inline-diff run approved. The verbatim companion text did no better than the suite's framing on this path. **T2** (0.6.25 first draft 28c5bc2..2c46cbc; known answer: README promises `effort_source` on every leg, a mechanism-none leg gets none): framed high missed it (only the known empty-lenses FP), also with the diff inlined; framed xhigh found it (528 s) plus three defects the maintainer reproduced and fixed in 0.6.27 (unranked roster effort lowered, non-string roster effort crashing, `-fast` twins unrecognised). n is small and single-machine; the equivalence test (≥ 3 runs per arm per target, companion vs exec+framing at equal effort) is what gates replacing the companion. |
| 2026-09-23 | gpt-6-luna at xhigh on BOTH arms (the machine's config.toml was xhigh, so the companion arm ran at xhigh and exec was matched to it; exec headers confirm model and effort) | **the equivalence test that gates the exec path**: companion `adversarial-review` vs `codex exec` + framing v1, v2 and v3, 3 runs per arm per target, the same lens text, three frozen targets with known answers: T1 a fetch flag's "adds objects only" claim (known: a configured refspec still stores a ref; a tag-follow sibling also real), T2 a new per-leg effort table (known: a README field promised on every leg that one adapter class never emits; a rebutted empty-lenses item counts as the false positive), T3 a rule engine's fix round (4 known: a Markdown fold reaching into code, missing mode flags, an untested second call site, a vacuous test value) | **Pass.** Known-answer recall out of 21: companion 7, v1 11, v2 7, v3 9. Extra real findings on T2: 1 / 3 / 4 / 7. False positives equal across arms (the rebutted items 3/3 everywhere, v1 2/3 on one). Base revision read (`git show <base>:` in the log): companion 4/9 runs, every exec arm 9/9. v2 added introduced-vs-inherited separation and consumer tracing as FILTERS ("an inherited defect is not a finding") and lost recall — the mode-flag defect, inherited in that range, fell from 3/3 to 1/3; v3 kept both as LABELS (report inherited defects labelled; a mitigation lowers severity, never drops the finding) and recovered it (3/3). The only knowns v1 had and v3 lacks are T1's refspec defect (v1 2/3, every other arm 0/3), which n=3 cannot separate from noise. Cost: exec+framing is slower on a large target (T3 9–25 min vs the companion's 7–11). Adopted: v3 is the default review path from 0.6.28, the companion the fallback. **Lesson worth more than the numbers: an instruction that tells a reviewer what NOT to report is a recall cut you pay for silently; express precision as labels and severity, not as exclusions.** |
| 2026-09-23 | gpt-6-luna **max** (`codex exec -c model_reasoning_effort=max`) | review, a 14-document design batch (~1.2 MB packet) | A peer's measurement. `max` is accepted and shown in the log header (a one-word reply cost ~11.6k tokens). 20.1 min, ~619k tokens, 5 findings claimed, **1 confirmed** (and misdated as introduced). Beside it on the same packet: another family's leg 3 confirmed of 6 in 7.1 min. Against luna at xhigh on 3.9k lines of compiled code the same day: 9 min, ~146k tokens, 2 of 2 real. n=1 each: `max` bought time and tokens here, not yield. |

## Model and effort plumbing

- The **task path** takes `--model` and `--effort`
  (`none|minimal|low|medium|high|xhigh` — the flag's maximum is spelled
  `xhigh`).
- The **review path** (since 0.6.28, `codex exec` with the suite's framing)
  takes `--model` and the effort per run: `-c model_reasoning_effort=<e>`,
  and the log's `reasoning effort:` header records what actually ran. `max`
  is accepted there too (a peer measured it, 2026-09-23).
- The **companion's `adversarial-review`** — now the review FALLBACK — takes
  `--model` only; its depth is `~/.codex/config.toml`'s
  `model_reasoning_effort`, which any run without an explicit effort
  inherits. Check it when that path is used:
  `grep model_reasoning_effort ~/.codex/config.toml`. Never modify the
  user's config from a skill run. (Before 0.6.28, `/dev-lead:config` could
  align it on the user's explicit yes via `roster.py config-effort`; with the
  review on `codex exec` there is nothing to align, and that command answers
  "nothing to write" for codex.) Passing `--effort` explicitly on implement
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

Review legs run the codex CLI directly everywhere since 0.6.28 (the review
skill's default path; [`scripts/codex-review-prompt.py`](../../../scripts/codex-review-prompt.py) needs only Python and
git). The implement role drives codex through its Claude Code **companion
plugin** by default, because the companion adds real things there: tracked
jobs (`status`/`result`) and `task --write`. On a machine without Claude
Code, both roles run against the codex CLI directly:

```bash
# review leg (read-only) — the lens framed, fed on stdin, output to a file
python3 "$DEV_LEAD/scripts/codex-review-prompt.py" --base "$BASE" --target "$REVIEW_TARGET_DIR" \
  < "$RUN_DIR/prompt.md" > "$RUN_DIR/framed-prompt.md" && \
codex exec --sandbox read-only --cd "$REVIEW_TARGET_DIR" -m <model> -c model_reasoning_effort=<tier> \
  -o "$RUN_DIR/review.md" -- - < "$RUN_DIR/framed-prompt.md" > "$RUN_DIR/review.log" 2>&1

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
- **The companion's review framing** — replaced, not lost: a review leg's
  prompt is built from [`adversarial-framing.md`](adversarial-framing.md),
  which carries the base and head itself. It passed the equivalence test
  (the 2026-09-23 row) and is the default review path since 0.6.28.
- **Log-based recovery.** No companion job log; session rollouts under
  `~/.codex/sessions/` remain your only after-the-fact recovery channel.
- Never substitute `--sandbox danger-full-access` (or any bypass spelling)
  for a missing capability — the same rule as the companion's bypass flag.

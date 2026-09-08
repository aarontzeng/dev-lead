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
stale gateway token died before any thread existed:

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
| 2026-09-01 | gpt-5.6-terra (companion) | review (2 code changes + 1 plan doc, 11 posed items) | Third consecutive round contributing a finding no other leg reached, and this one was structural: a test named "PF-10→PF-12 PostgreSQL integration" seeds real PF-09/PF-10 rows and then hands the bundle an in-memory fake resolver whose `resolve()` returns a hardcoded member — so the PF-10 authority boundary, the exact thing the change claims to establish, is never exercised. Lead-verified. Also sole finder of a second uncovered guard (`record.artifacts.empty()`, no matching mutation). ~14 min. **The reliable shape across three rounds: terra finds the gap between what a test is NAMED and what it actually reaches.** Give it the "does this evidence evidence?" brief and it earns its slot. |
| 2026-09-01 | gpt-5.6-terra (companion) | review (spec freeze doc, 6 posed items) | **Fourth consecutive sole finding, and it became a primary ground for the lead's −1.** On a document whose stated job is to freeze semantics for two owners implementing separately, it found that an `UNAVAILABLE` result carries exactly one typed reason while three independent condition→reason mappings are given with no precedence — so a source that is simultaneously gapped, stale, and provenance-unverified gets a different wire reason depending on validator order, and the selector is required to preserve that reason. Lead-verified against both passages. It also independently escalated the deadline item to the membership effect, matching the grok leg and correcting the lead's weaker hypothesis. ~10.5 min. **Note the generalisation across four rounds: the sole findings are all one shape — a claimed property whose named evidence cannot actually exercise it.** Deviation worth knowing: despite an explicit "do not spend turns on tool discovery" line it opened by reading local skill files and probing for `QUANTA.md`; read-only and harmless, but the instruction did not suppress it. |
| 2026-09-02 | gpt-5.6-terra (companion) | review (3 document changes, 7 posed items) | **Fifth consecutive sole finding, and it was the one item nobody posed.** The brief asked seven questions about an ADR's command-result contract; terra answered those and then added an eighth: the same ADR's alarm-ack contract prescribes `ack_user_id` from a caller-supplied `X-Actor-Principal` header, directs both the UI and the PF-09 evidence query to trust the resulting `ack_state`, and sits behind a gateway with no authentication filter — so any host that can reach the gateway can acknowledge an alarm and forge its attribution, and the ADR notarises that as an accepted contract. Lead-verified verbatim at `AlertController.hpp:126` and `api-gateway/src/main.cpp:83-100`; it became a named ground in the lead's −1. **The five-round generalisation now needs widening**: the shape is not only "a claimed property whose named evidence cannot exercise it" but "a document treating an untrustworthy artifact as evidence" — the named test that cannot reach the property, and now the caller-controlled string consumed as safety evidence, are the same error at different layers. ~3.2 min. One gap: item (c) (severity calibration of a 23-finding audit) got a summary sentence with no finding block and no spot-check evidence, though the log shows it ran the grep — it says "R3-01's **core** is confirmed" and never spells out the reservation. When an item needs enumerated evidence, say so per-item; terra will otherwise fold a checked item into the verdict line. |
| 2026-09-02 | gpt-5.6-terra (companion) | review (flight-command change, 11 posed items) | **Sixth consecutive sole finding, and the two it contributed were the round's most severe — both became named BLOCKING grounds in the lead's −1.** (1) It reframed a path another leg had found and mis-sized: the retry loop publishes a terminal NO_ACK and erases the pending row, but the takeoff sequence advances on *vehicle state*, not on that row — so when a command reached the FC and only its ACK was lost, the deck reports failure while a later heartbeat still arms and launches the aircraft. The lead had independently downgraded the other leg's version of this to MINOR; terra's framing corrected the lead. (2) Sole finder that the rekey to `(sysid, command)` does not separate ARM from DISARM, which share MAV_CMD 400 — so an ACK for the arm is published carrying the disarm's request id and the deck can report "disarmed" while the aircraft is armed. **The five-round generalisation now needs a third widening**: the shape is not only "evidence that cannot exercise the claim" but "a fix whose stated invariant is false for the one case where being wrong is dangerous". Two operational costs this round: the documented tool-discovery deviation recurred (it opened by reading local skill files and grepping for QUANTA.md despite an explicit no-discovery line) and that attempt then died on a usage limit at 508 s, forcing a 20-minute wait and a re-run (846 s). And the fold-into-the-verdict-line pattern is now n=2: four of its negative verdicts were one-clause summary sentences with no file:line and no evidence. Ask for enumerated per-item evidence explicitly or terra will spend its budget on the findings and pay for the HOLDS out of the verdict line. |
| 2026-09-05 | gpt-5.6-terra | review ×4 (sequences lens: mobile R1, web R1, web R2; plus the web money path) | **Found the round's only CRITICAL**: web dialog `Number.parseInt` truncates "2.5" → 2 and the action is created already-confirmed, so a user applies a 2:1 they never typed. On R2 it then found the residual in the fix (`Number("9007199254740993")` rounds past 2^53; backend INTEGER cap) — the lead fixed that one directly. Two of its findings were independently converged on by another family (mobile `pfhistory` not invalidated ← big-pickle; web dashboard aggregate not revalidated ← gemini), which is the strongest signal available. One rejected: "empty transactions returns [] before the loading check" — harmless ordering, zero transactions cannot project wrong. Reliable sequences leg; its severity words are calibrated (no inflation this round). ~10 min per leg. |
| 2026-09-05 | gpt-5.6-terra (**raw `codex exec`, `-c model_reasoning_effort=medium`**) | review (10-change C2 UI stack, 8 posed items, reviewed per-diff) | **First `medium` round, and the first via raw exec** — the companion's `adversarial-review` has no effort flag and would have inherited `max` from the user config. 176 s, fastest leg by 2x, zero NOT REACHED; the tool-discovery deviation did NOT recur and every item got its own evidence block (both n=2 patterns from 2026-09-02 absent). Converged on the BLOCKING with all legs; **sole finder of the ungated gateway path** (`formation_command` forwarded to NATS with no `boundByOther`, while the two neighbouring named-target commands check it) — lead-verified, a named MAJOR ground in the −1. Also the clearest statement of the 11506 fail-open catch (missing pin AND moved draft both mint a default lineage before the operator is told). Two of its findings blamed to pre-stack lines (`custom_mode` any-float, unconditional setpoint after GUIDED) — the supervisor's `git blame` scope check caught both; ask for that check on every stack round. 43/50 citations exact; all 7 misses were in the two files it read with `sed -n` instead of `nl -ba`, uniformly shifted 2–4 lines. **Supervisor note, not the model's**: the supervising subagent returned early twice on "the monitor will notify me" before blocking; the fix that held was a single Bash call with an explicit sleep loop on the output marker. |
| 2026-09-05 | gpt-5.6-terra (raw `codex exec`, `-c model_reasoning_effort=medium`) | review (take-over CL: mission-service + adapter + gateway + frontend, 8 posed items) | **Fastest leg again (176 s vs 388/377/874) and the one that escalated correctly.** Three legs found that `retireAssignmentVehicle` has no `delivery_state <> 'CANCELLED'` predicate; two called it MINOR (a wrong comment). Terra alone chased the consequence: a repeat release still returns a row, still reports RELEASED, still publishes to the adapter — and the adapter's drain never compares the message's `batch_id` to the execution it acts on, so a stale release BRAKEs whatever mission the vehicle is flying **now**. Lead-verified; it became a named BLOCKING. That is the sixth-plus round where its sole finding has the same shape: a stated invariant that is false in the one case where being wrong is dangerous. **One overreach**: it graded the release/upload ordering MAJOR without checking that both queues drain in the same loop iteration — the agy leg falsified it and the lead dropped it. Ask terra for the ordering *argument*, not just the ordering *risk*. Per-item evidence blocks held (the fold-into-verdict pattern stayed absent for a second `-medium` round). |
| 2026-09-06 | gpt-5.6-terra (raw `codex exec`, medium) | review (PF-19/PF-20 contract across 3 services + a migration, 10 posed items) | **35 of 35 citations exact — the first perfect citation round on this account — and it got there by following an instruction.** The preamble said to read with `nl -ba`; it used `rtk nl -ba … | sed -n` on all 14 file reads, and the 2-4 line drift its 2026-09-05 row recorded vanished. 228 s, fastest leg by 30%, zero NOT REACHED, per-item evidence blocks for a third consecutive round. It ran `git blame` unprompted and reported that 3 of its 34 cited lines were pre-existing, including the one that falsifies the commit message's pagination claim. **It also downgraded its own findings twice, correctly**: it refused the brief's 'two rows claiming the same revision' framing (the PK turns the race into a lost insert) and refused 'rows skipped or duplicated' on the pagination item (underfilled pages, nothing lost). Both downgrades survived the lead's check; a leg that argues its own severity DOWN is rarer than one that finds the defect. One miss: it graded the migration backfill MAJOR without noticing the poll has no LIMIT, which is what makes the flood unbounded. **Operating note: give terra the `nl -ba` instruction in every preamble — it is a one-line fix for the only defect this account still had.** |
| 2026-09-06 | gpt-5.6-terra | review ×2 (sequences on S2 detection; then the fix round) | R1: two CRITICALs in S0 loader code the lead had shipped days earlier — a user's manual split plus a global confirmation applied the ratio twice, and `.TW`/`.TWO` did the same — plus the mobile role-state write after an await with no epoch fence. All three verified; lead fixed the loader itself. R2 check found two more real ones the fix round introduced or left: refresh promotion dropped the admin role (defaulted arg), and FinMind's swallowed `[]` still counted as a clean scan. Its residual (overlapping runs on symbol aliases) was correctly labelled as contingent. Four rounds on this feature, zero rejected findings. |
| 2026-09-06 | gpt-5.6-terra (--effort medium) | review ×2 (sequences on a lead-written S3 change + #30) | Strongest leg of the round at medium effort. Found the CRITICAL the lead shipped: the Python importer accepted an `actions` argument and never wired it — never passed to the oversell replay, never persisted, then an undefined counter referenced after commit (a stale `replace()` in the lead's own edit). big-pickle converged on the same from the consistency side (NameError on the dead `written_actions`). Also: an importer-inferred user row outranking a confirmed global fact, and the web import continuing to post rows after an action POST fails (half-import a retry duplicates). Every finding verified; none rejected. medium effort was plenty for a diff this size — no depth lost vs the high-effort S2 round. |
| 2026-09-07 | gpt-5.6-terra (raw `codex exec`, `-c model_reasoning_effort=medium`) | review ×2 (authorization-gate change, 8 claims; then the fix round, 5 claims) | **The leg that caught the fix round's own new bug, which is the harder half of the job.** R1: converged with three families on the round's central defect and was **sole** on the "same snapshot" guarantee being over-claimed — the precheck binds the patchset, but nothing is atomic against the server, and the ADR said otherwise. Its C8 mutation matrix matched the lead's own run and flagged the one flag-assertion nobody had pinned. R2 (the fix round): the lead's 409 disambiguation matched the substring `patchset` — which **both** refusal messages contain — so the bug it was written to fix survived in a new shape; terra found it, with `git blame` naming the commit that introduced it. Also named a surviving `any()`→`all()` mutation whose test only used lists where every element was bad. Its design challenge was half right: the naming and the undocumented response contract were conceded, the alternative was rejected (it would have preserved the re-derivation four families had just converged on). **Operating note, now upstream: `< /dev/null` when backgrounded with an argv prompt** — this round lost ten minutes to it hanging on stdin. |
| 2026-09-07 | gpt-5.6-terra (**companion `adversarial-review`** — so `max`, NOT the medium the lead intended) | review (6-line C++ gate in a WiFi manager, 6 posed properties, lead had already hardware-verified the change) | **1 of 2 findings survived, and the one that did was sole and structural.** Its HIGH ("a stable disabled state suppresses required reset/recovery reloads") named the `record_reset` callers, and the lead falsified that path: those callers hit `qtwifi_ctrl unbind all` + `unload all` first, so the interface it worried about is already unloaded and there is nothing for the skipped `wifi reload` to apply. But the abstract core of it was right and was recorded on the ticket — the new gate trusts the manager's in-memory model rather than probing the radio, so the old code's incidental "re-assert down every pass" repair is gone. Its MEDIUM was the round's only structural contribution and no other leg reached it: the previous-state statics are keyed to the SIDE (`if_main_disabled`) while `interface_main` maps to a different physical band under a different `band_type`, so a mapping change compares a band against the other band's history. Currently unreachable — a `band_type` change always sets `DIFF_BAND_TYPE`, which triggers the same unbind-all — but it fails SILENTLY if anyone ever narrows that block, which is exactly the class the lead would not have found alone. **Two lead errors, both already documented here and both ignored at dispatch**: the companion path has no effort flag (row 200 says so) so this ran at `max`; and the preamble omitted the `nl -ba` line (row 202's operating note), after which its citations came back as coarse ranges (`:713-745`) instead of exact lines. Also: the first attempt died at startup on an MCP 401 — see the new section above. |
| 2026-09-08 | gpt-5.6-terra (companion `adversarial-review`; **the lead asked for `medium` and this time the inherited config actually was `medium` — `~/.codex/config.toml` reads `model_reasoning_effort = "medium"`, so the row-227 mismatch did not recur. Still unconfirmable from the run log; the only way to KNOW is raw `codex exec -c`**) | review (86-line C++ change adding a periodic RF-config broadcast; challenge-the-approach brief, 8 posed properties) | **4 findings, 4 survived — and it was the only leg that answered the question the brief actually asked.** Given a challenge brief (is this the right mechanism for the ticket at all?) it produced the round's CRITICAL and it became the lead's first −1 ground: the reconciliation datagram travels over the pair link itself, while every divergence state the ticket enumerates is a bandwidth mismatch that prevents association — so in the ticket's own failure mode there is no L2 path and the broadcast never arrives. The change's own comment concedes it; no other leg treated that concession as disqualifying. **Its second finding is the one worth generalising**: told the commit message claims older peers "can ignore this extension", it went and read the BASE revision and found the ignore path leaks — `cJSON_Parse` at base `:3486`, the unknown-message `else` at `:3552`, `continue` at `:3557`, no `cJSON_Delete` between them — so a broadcast every 10 s means ~8640 leaked objects/day on an un-upgraded peer. Every base coordinate verbatim-exact on the lead's re-check. **Seventh-plus consecutive round contributing a sole finding, and the shape has widened again**: from "evidence that cannot exercise the claim" to **"a compatibility claim about code the author never opened"**. Also the only leg to mark a claimed property BROKEN on the right grounds (`rf_apply_pending` is a thread-stack variable, so every listener restart forces a full radio reload). ~6 min. Dispatch it whenever the change's own commit message makes a claim about a peer, an older version, or another repo — it will go read that thing. |
| 2026-09-08 | gpt-5.6-terra (companion; config `medium`) | review (PS2 delta of the same WiFi-manager change, briefed as **challenge the ANSWER, not the problem** — PS1's blockers were closed and the question was whether the author's fix was the right shape) | **3 findings, 3 survived, and the brief shape is the transferable part.** Given "PS1 was blocked on two grounds and PS2 is the answer; is the answer the right shape and what did it cost?", it produced the round's only systemic observation: the new marker gate returns silently, and combined with an already-known path (a failed apply never clears the applied flag) the feature now has **two independent ways to stop forever with no log, counter, or status** — one of them a routine reboot, since the marker is deliberately volatile. Neither other leg framed those two as the same defect. It also correctly identified that the harness no-ops both JSON writers and discards every send argument, so the wire contract has no regression check at all — the lead then mutation-confirmed the consequence. Its third, the design objection (a hand-managed file is not capability detection, so the safety property holds only while operators are perfect), was relayed to the author but not treated as blocking: the pre-change state was "upgrading a Master harms peers automatically", the post-change state is "a deliberate action plus a mistake", which is a real improvement. **Standing note on brief design: when a change is an ANSWER to earlier findings, brief this leg on the answer's shape rather than re-running the original hunt — it is the leg that will ask what the fix cost.** |
| 2026-09-08 | gpt-5.6-terra (companion; config `medium`) | review ×2 of the same plugin change (**the first attempt refused to answer, correctly**) | **The refusal is the row's first lesson and it was the lead's fault.** Attempt one returned no findings and said why: the brief demanded a `file:line` evidence gate while forbidding "any other CLI invocation" and supplying no diff, so no source-backed finding was defensible. It was right — the lead had stripped the embedded diff to fit argv budgets on two other legs and never re-read the shared header that still promised one. Relaunched with reading explicitly allowed (`git show`, `wc -l`, open files) and only recursive delegation forbidden: 3 findings, 3 survived, evidence gate exact on 6/6 files. **Its headline finding is the one no amount of implementation could have surfaced**: the change's stated root cause was "skill text in the session's context is frozen at an old plugin version", and the remedy is a script — but *the instruction to run that script lives in the same file that gets injected stale*, so a session handed an old copy never learns the script exists. The change did not close its own stated hole, and the commit message claimed otherwise. It also found `verified:` was decorative (lint checked the field exists, never the date or the CLI) and that the billed-`--help` hazard was warned about rather than prevented, since the script prints a command and never owns the invocation. **Standing brief note extended: when a change is an ANSWER to earlier findings, this leg is the one that asks whether the answer addresses the cause the author himself named.** |

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

# `cursor-agent` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for Cursor's CLI (`cursor-agent`), shared
by `cursor-adversarial-review` and `cursor-implement`. Measured against
`cursor-agent 2026.08.11-e8db854` on 2026-08-13 with live probes (this
account had usable quota on integration day — unlike the grok adapter, the
load-bearing behaviors here were watched happening, not read about).

## Position in the fleet

A **paid pool** (Cursor subscription) and the fleet's widest single adapter:
one CLI serving **six families** — GPT (Codex/GPT-5.x tiers), Claude (Opus /
Sonnet / Fable tiers), Grok (4.5/4.6 at several efforts), **Kimi
(Moonshot)**, Cursor's in-house **Composer**, and `auto` (server-side
routing, family unknown at dispatch). That width is the value: when a
round's authorship has already spent two families, this adapter can usually
still field a reviewer from a third — without installing another CLI.

Accounting rules this width forces (the family column is what the
cross-family rule reads):

- **A pinned frontier model accounts as its real family** — `cursor` +
  `claude-opus-5-thinking-high` is a Claude-family leg, exactly as if the
  claude adapter had served it.
- **`auto` never satisfies the rule** — the served model is chosen
  server-side and the headless JSON does not name it (measured, below).
  Extra pair of eyes only.
- **`composer-*` never satisfies the rule either** — Cursor's own model,
  but its pretraining lineage is undisclosed, which is the same hazard the
  stealth-model rule exists for: it might share any family's blind spots.
  Extra eyes; never the accounting leg.

## Resolving the CLI, the account, and the catalogue

```bash
command -v cursor-agent || { echo "cursor-agent not installed"; exit 1; }
cursor-agent --version    # measured against: 2026.08.11-e8db854
cursor-agent status       # login identity; refuse to dispatch logged-out
cursor-agent models       # the catalogue is per-account and changes without a CLI update
```

Model ids take bracket parameters — `'composer-2.5[fast=true]'` is measured
working; the help shows `'claude-opus-4-8[context=1m,effort=high,fast=false]'`.
Quote them: brackets are glob characters to the shell.

**Data-handling marker:** catalogue entries suffixed `(NO ZDR)` are served
without zero-data-retention. Do not put confidential or customer code
through those entries; prefer ZDR-covered tiers for anything sensitive.

## The three measured postures (2026-08-13, composer-2.5, scratch git repos)

1. **`-p` alone is a full-access write posture.** The help says print mode
   "has access to all tools, including write and shell" and it means it:
   told to create a file, it created the file and answered, no prompt, exit
   0, dirty tree. This is the implement posture — and the reason the review
   skill never launches bare `-p`.
2. **`--mode plan` is unusable headless.** Writes were blocked (good), but
   stdout came back **empty** — exit 0 and a 1-byte output, twice, including
   on a pure-analysis prompt with no write ask at all. A review leg whose
   report never arrives is a silent-death mode; never pair `--mode plan`
   with `-p`.
3. **`--mode ask` is the review posture.** A defect-hunting prompt returned
   a real finding with `file:line` on stdout; a write attempt was refused in
   words ("I'm in **Ask mode**, so I can't create or edit files") with no
   file created and a clean tree. Whether the edit tools are removed or
   declined is UNVERIFIED — treat ask mode as one layer and keep the
   post-run bracket, as everywhere else.

## Headless mechanics

- The prompt rides **argv** — there is no prompt-file flag in this version.
  Interpolate from a file (`"$(cat "$RUN_DIR/prompt.md")"`) so quoting and
  CJK survive; whether this CLI has an argv size cliff like opencode's ~2 KB
  hang is UNVERIFIED, so keep headless prompts lean and put the bulk (the
  embedded diff) early.
- `--output-format json` (with `-p`) returns `result`, `session_id`,
  `request_id`, token usage — and **no served-model field** (measured). It is
  this suite's only audit format for either role: redirect stdout to a per-run
  `.json` file and stderr to a separate `.err` file, then preserve the JSON object.
  `result` is the human-readable report; `session_id` and `request_id` bind
  it to the dispatch. A missing `request_id` is an audit gap, not a value to
  invent. The [Cursor output-format reference](https://cursor.com/docs/cli/reference/output-format)
  documents the JSON fields; pin `--model` explicitly because a stronger
  served-model verification path remains UNVERIFIED.
- `--trust` is required for headless runs in fresh directories — every
  probe used it; without it a workspace-trust prompt has nowhere to go.
  **Its absence fails in the worst possible shape** (measured 2026-09-01, a
  peer lead's round): the CLI prints only the trust prompt and **exits 0**, so
  by exit code alone a never-started run is indistinguishable from a completed
  one that found nothing. A frozen review target is a fresh directory every
  time, so this is the default case, not an edge one. Never certify a cursor
  leg on exit status — require `result` and `request_id` in the JSON, which is
  the check that separates the two.
- Flags that must never appear on a review launch: `-f`/`--force`,
  `--yolo`, `--approve-mcps`. The first two are allow-everything, the third
  hands the run to whatever MCP servers the account has wired.
- Built-in `--worktree` exists (`~/.cursor/worktrees/…`) — unused here; the
  suite's own freeze/worktree helpers own directory lifecycle in both roles.
- Global permission rules live in `~/.cursor/cli-config.json`
  (`permissions.allow`/`deny`, e.g. `Shell(ls)`). Their full syntax, a
  per-project override file, and `--sandbox enabled`'s actual semantics are
  all UNVERIFIED — none is load-bearing in the skills yet.

## Provisioning symlinks are commit bait (measured 2026-08-30)

A worktree provisioned with `ln -s <main-checkout>/node_modules
<worktree>/node_modules` handed the delegate a symlink that its `git add`
swept into the feature commit — `.gitignore`'s `node_modules/` (trailing
slash) matches only DIRECTORIES, not a symlink. The lead's handoff stat
showed the extra file and it was missed; the fast-forward merge then
REPLACED the main checkout's real node_modules with a self-pointing broken
symlink, destroying the installed tree (recovered via `npm ci`). Rules:
put provisioning symlinks at paths git ignores WITHOUT the trailing-slash
form, or add them to the worktree's `.git/info/exclude` at creation; and
treat any `mode 120000` line in a delegate's diff stat as a stop-and-look.

## Quota pools: two meters, and the CLI cannot read either (2026-08-30)

The subscription meters TWO pools, visible only on the cursor.com dashboard —
`Auto` (serves `auto` and `composer-*`) and `API` (every pinned named model:
gpt/claude/grok/kimi tiers). `cursor-agent --help` exposes no usage/quota
subcommand and `status` is auth-only, so **percent-remaining is not probeable
from the CLI**. What IS probeable is exhaustion, because quota failure is loud
(below): a cheap one-liner against the pool you intend to bill is the dispatch
probe. Corollary worth planning around: when the API pool runs dry, `auto` and
`composer-*` still spend the other meter — a legitimate extra-eyes fallback
(never the accounting leg; families.json).

## Quota: how this leg fails (measured 2026-08-13)

```text
ActionRequiredError: You've hit your usage limit Get Cursor Pro for more
Agent usage, unlimited Tab, and more.
```

Exit code 1, immediately, identical in `text` and `json` output formats —
**loud**, same schedulable property as the grok leg and the opposite of the
free pool's silent empty runs. Measured the day the integration probes
themselves exhausted the account's trial quota, which also dates a caveat:
the catalogue and postures above were measured on a pre-Pro account, and
quota width (not behavior) is expected to change with the plan.

## Calibration journal

| date | model | role | outcome |
|---|---|---|---|
| 2026-08-13 | cursor/composer-2.5[fast] | probe | 5 integration probes: write-posture, plan-silent-empty ×2, ask-mode review (1 seeded defect, 1/1 found at file:line), json shape — not a scored review round |
| 2026-08-30 | cursor/cursor-grok-4.6-xhigh-fast | implement | First implement round (backend API contract, S): ran its own premise preflight, wrote tests red-first unprompted, collapsed a pre-existing duplicate suffix judgement into one helper, found an unrelated pre-existing calendar bug, honestly flagged an out-of-scope web regression its own change caused. COMMITTED locally with a clean conventional message — resolving UNVERIFIED "committed or dirty": this adapter commits. Zero fix rounds needed. |
| 2026-08-30 | cursor/composer-2.5[fast] | review | First scored review round (extra-eyes leg, RN code-craft brief): 2 BROKEN, both lead-verified real (iOS-only a11y prop, Dynamic Type clipping) — the only leg of a 3-family round to catch either; also produced a route-param abuse table nobody asked for. Strong extra-eyes value; family still unknown, still never the accounting leg. |
| 2026-08-30 | cursor/cursor-grok-4.6-xhigh-fast | implement ×6 (aggregate) | Six further implement rounds the same day across one mobile+backend roadmap (S–M slices: contextual draft, search contract, two picker/search slices, bounded history pages, two-step alerts). **Zero or one fix round every time**; no round needed a second reviewer pass to converge. Consistently wrote its own regression tests and flagged out-of-scope damage instead of hiding it. On this account this tier is now the default MEDIUM implementer, not an experiment. |
| 2026-08-30 | cursor/cursor-grok-4.6-xhigh vs cursor/composer-2.5 | implement (head-to-head) | Same brief, same base, separate worktrees, RN a11y/shell slice. **grok: 0 BROKEN** from cross-family review, 79% of its tests falsifiable, and it ran its OWN cross-family review unprompted and fixed a regression it had caused. **composer: 3 BROKEN**, two lead-verified — `accessible: true` on a sheet container (turns the whole sheet into one a11y leaf; an a11y fix that breaks a11y) and a doubled safe-area inset it introduced while claiming the opposite in its report; plus its two vacuous tests sat on the privacy-critical path. composer was **3.2× faster** (11.3 vs 35.9 min) and read one plan clause backwards. Both prescribed fixes were byte-identical — the whole gap was in the self-directed half. On this account: composer stays an extra-eyes REVIEW leg, not an implementer for anything with a correctness surface. |
| 2026-08-30 | cursor/cursor-grok-4.6-xhigh-fast | review | First real review round (plan-document review, AaTrader mobile UX redesign, challenge brief): 7/7 verdicts delivered, 3 HIGH; every lead-spot-checked citation (5/5) genuine at file:line; ask mode refused nothing it needed, ~5 min wall clock, request_id captured. Strongest single leg of a 4-family round (agy/claude/nemotron beside it). |
| 2026-08-31 | cursor/cursor-grok-4.6-high | review (design doc) | 4-leg round on a mesh crypto design: confirmed both lead-posed gaps and added the round's sharpest independent finding — data-plane commands authenticated only by a SHARED group key, so the AEAD tag proves group membership, not sender identity, and any member can forge another's node_id. All spot-checked citations genuine. ~9 min. |
| 2026-08-31 | cursor/cursor-grok-4.6-xhigh | review (code diff) | 5/5 HOLDS with genuine citations, and the only leg to name a *design* trap the lead then propagated to the author: do NOT "fix" the new terminal REQUEST_CONFLICT by looking up binding_key first — that reopens the silent-duplicate bug the change had just closed. But it missed the mutation-coverage gap terra caught. **48 minutes** — vs ~9 for `-high` on the doc review above the same day. On this account the xhigh tier bought no extra depth over high on review work at ~5× the wall clock; prefer `-high` for review and reserve `-xhigh(-fast)` for implement, where its record is strong. |
| 2026-09-01 | cursor/cursor-grok-4.6-high | review (2 code changes + 1 plan doc, 11 posed items) | **Best leg of the round, and the tier note from 2026-08-31 is now confirmed in the strong direction: `-high` was both the sharpest AND among the fastest (8.1 min).** Sole finder of the round's best defect — an integration test whose `rows.size() != 1 → return 2` collapses "fixture missing" and "a DUPLICATE approval exists" into one exit code that `SKIP_RETURN_CODE 2` reports as skipped, i.e. the one invariant the test exists to protect is silently downgraded to green. Lead-verified; it ties directly back to the silent-duplicate defect the lead had −1'd two rounds earlier. Also caught three items others missed or half-saw: an untested new guard, a stale stage label on an early validity check, and a 5-way disposition collapse contradicted by a sibling store's own RETRYABLE precedent. **Ruling for this account: `-high` is the review default; xhigh is not an upgrade for review.** |
| 2026-09-01 | cursor/cursor-grok-4.6-high | review (spec freeze doc, 6 posed items) | Most thorough leg again at 5.6 min — `-high` now has two consecutive rounds as both sharpest-or-near and fastest-or-near. It did the work the lead's item (a) asked for and the other paid leg skipped: a concrete two-source trace showing a cutoff changes set membership, not just the reported reason. Sole finder of a flat self-contradiction — the same document says stale evidence "must never map to `SOURCE_TIMEOUT`" while the new deadline rule maps every unresolved required source to exactly that. Also caught "settled" undefined, issuance-order unconstrained while completion-order is, and a sentence claiming a component "enforces" a rule it can only own. **Two overreaches the lead did not adopt**, both the same shape — stating as contradiction what is really omission: it called a success-path procedure step a "direct contradiction" of the failure path (an unavailable return is not a snapshot, so the step is silent, not false), and called the observation context "no independent root of trust" when the spec does name an owner-independent adapter as the root (the free leg's reading was more precise and the lead used that instead). Strong leg; discount its severity words by roughly one step when it says "contradiction". |
| 2026-09-02 | cursor/cursor-grok-4.6-high | review (3 document changes, 7 posed items) | Two sole findings, both real, both the kind only a leg that opens the cited artifact will get: the audit report under review cites a route `/api/mission/abort` that does not exist (the registered route is `POST /api/v1/missions/abort-return-home`, `MissionController.hpp:49`), and a sibling spec section claims to be "the authoritative summary of current state" on the same reviewer-walkthrough credential the ADR uses for its `Accepted` status — one witness, two documents, no independent check. Both went into the lead's reviews. **But the severity-discount rule from 2026-09-01 held for a second round, and this time the lead had to do real work to refute it.** It argued the audit's `嚴重` rating on the joystick keepalive was inflated because a key mismatch means the resend loop never runs. Measured: `manual_control_states` is keyed by the NATS subject's device token and `drone_states` by `deviceIdForSysid()`'s `sta-%03u`, so the loop is dormant only for ids that are NOT `sta-NNN` (WUR-mapped, `sitl-*`) — on the standard path the keys match and it runs. The report already said as much in its own text. **Three rounds, one stable signature: sharpest at finding the artifact nobody opened, and systematically one step too confident when it argues a rating DOWN or calls something a contradiction.** Discount downgrade arguments as hard as contradiction claims. |
| 2026-09-02 | cursor/cursor-grok-4.6-high | review (flight-command change, 11 posed items) | **Two sole findings, both load-bearing, and the first round where its dismissals were all correct.** (1) The new confirmed-state sequence checks `custom_mode == 4` on its first transition and only `state.armed` on its second — so after GUIDED is confirmed, a failsafe/RTL/abort that changes the mode does not stop the final NAV_TAKEOFF. That is the exact mirror of the defect the lead had −1'd on the predecessor change (which checked mode and not armed); the same hole moved one step down the state machine. (2) It escalated a posed item past what was asked: the frontend command log distinguishes "a step of a sequence" from "the terminal" by *command inequality*, and the sequence's final step carries the same command id as the entry — so the FC's ACK for NAV_TAKEOFF resolves the row as ACCEPTED while the vehicle is still on the pad, and the in-tree test never injects that ACK. Both lead-verified. **The severity-discount rule did not fire this round**: nine dismissals, all checked, all correct — including calling the gateway echo-ordering a non-finding where two other legs (one paid) reported it as a race. Three rounds in, revise the note: discount its *downgrades* only when it is arguing a rating down on someone else's finding; its own HOLDS have been accurate. Still the leg that opens the artifact nobody was told to open. 542 s. |
| 2026-09-04 | cursor/cursor-grok-4.6-high | review (2 planning docs, 9 posed items) | **The only leg of three with a real defect, and it was the round's sole −1 ground.** A fixed-decisions table promised "there is no `NOT_REQUIRED` fallback" for alarm evidence; this leg found that `SafetyValidator` wraps the entire alarm block — including the `alarms_.resolve()` call — in `if (policy.alarm_requirement == AlarmRequirement::REQUIRED)`, that the migration's CHECK permits `NOT_REQUIRED`, and that **no task in the 710-line plan pins the LAB policy to REQUIRED**. Lead verified and found it sharper still: the only occurrence of `NOT_REQUIRED` in the whole document is the sentence promising it cannot happen. Also sole on two MAJORs whose premises the host verified — a shared PF-09 store with no LAB/production discriminator and a production currentness SELECT with no LAB predicate, and an acceptance gate requiring proof of PF-13/14 entry in a plan whose goal says PF-13/14 are not authorized. 518 s, 30 of 31 citations exact. **New failure mode, first observed on this leg: selective quotation.** It quoted a ledger cell as `**Merged**` when the cell reads `**Merged / runtime still disabled**` — line number correct, and the dropped half was precisely the clause that weakened the finding it was supporting. That is distinct from the over-confidence already on file: not a wrong severity, a shortened quote. **Add to the standing note: check its quotes against the full line, not just the line number.** It also shared the round's deadlock over-read with the agy leg, though its own supervisor flagged that as the shape to verify first. |
| 2026-09-04 | cursor/cursor-grok-4.6-high | research (mcp-governance-gateway, 4 aspects / 18 lettered questions) | 18/18 answered, zero fabricated citations: **43 of 43 spot-checked citations exact against the frozen tree and the agentmemory clone, including every fenced quote compared FULL-LINE** — the selective-quotation failure mode from 2026-09-04's earlier round did NOT recur. Delivered the brief's stated jackpot: a **sixth** process-local state holder beyond SECURITY.md's five (`InternalCredentialAPI` per-actor rate limiter, `internal_api.py:24-25`, default 10/min at `:58`, enforced at `:100-102`) plus seven further unnamed process-local holders. Also found a single-instance variant of the (a2) credential window the brief did not posit (`verify_key` at `internal_api.py:110` runs outside the keystore lock, so one process has the same observable), and a change in agentmemory's `mem::search` since the 2026-07-12 static reading (`search.ts:545-563` null-session fallback: rows whose session is gone pass through UNSCOPED). **Honest on the trap question**: (a5) returned NOT REACHED for all four backends rather than inventing idempotency-key URLs, while still supplying an in-clone source reading for agentmemory. Discipline held on the research/review distinction — confidence levels, no severities, no recommendations. **New adapter failure mode observed:** one launch produced TWO sessions writing the same stdout file (`f78e939f`/`4dee5e43` complete at 26288 B, `0d3c638d`/`8a578984` overwritten to a 562 B tail) — `--output-format json` output is not guaranteed to be a single object; parse line-wise and expect a possible duplicate dispatch. 509 s. |
| 2026-09-04 | cursor/cursor-grok-4.6-high | **research** control run (single enumeration question) | 17 rows, ~150 citations checked full-line, **zero missed, zero invented** — the same 17 routes as the agy leg, reached independently (the brief named no file, no tool, no grep pattern; verified by inspecting its text). Its completeness argument was the checkable kind: named greps with counts (`type:"http"` → 130 + 6, both exact), and it justified two *exclusions* on grounds specific to those lines — a supplied recipe could not have produced that. Earlier the same day, on the same underlying fact asked as a whether-question, this leg answered "no dedicated lookup": literally true, citations exact, operationally wrong, and it missed the route that does it. **The split is the brief's fault, not the leg's.** Also, on the fabrication-trap question, the only leg of three to return NOT REACHED ×4 — declining on ground it had genuinely read rather than offering a source-reading in place of the evidence standard demanded. That is rarer than accuracy and invisible to any citation metric. |
| 2026-09-05 | cursor/cursor-grok-4.6-high | implement (web UI slice, Next.js, 20 files, +821) | **First `-high` implement run.** 18.4 min, three well-split commits (service+hook / dialog+i18n / tests), 451/451 vitest (440 + 11), tsc clean, eslint at baseline, all inside scope. **Pushed back on the brief correctly**: reported the two "pre-existing tsc errors" I cited do not exist (they were eslint errors) — right, my brief was wrong. **Appended `Co-authored-by: Cursor <cursoragent@cursor.com>` to every commit despite the brief forbidding authorship trailers** — stripped with a local `filter-branch`; put the ban in the brief AND check `git log --format=%B` at handoff, every time. 3 lead mutants killed. Cross-family review (GPT + Gemini) then found one CRITICAL it had introduced: `parseInt("2.5")` → 2 accepted as a confirmed 2:1 split — a money-correctness bug a `-high` implementer shipped in the validation it was explicitly asked to write. **R2** (7 findings incl. the CRITICAL): 21 min, 3 commits, 462/462, fixed all, self-reported a hung render test honestly and substituted a lexical source-pin (weak; flagged by the next reviewer). Verdict: good implementer for bounded UI; do not let it own input validation on money paths without a second family reading the parser. |
| 2026-09-05 | cursor/cursor-grok-4.6-high | review ×2 (falsifiability + surface map on a Gemini-written mobile diff; then the R2 close-out check) | **Best leg of the mobile round.** 8.1 min. Sole finder of the round's strongest defect: `allHoldingSymbols` projecting without corporate actions, with the concrete counter-example (buy 6 → 2:1 → sell 6 leaves 6 shares; unscaled = 0 → symbol drops from the quote universe on six screens) — which falsified a sentence in the LEAD's own brief. Its falsifiability table independently reproduced the lead's mutant result (read-only test pins an a11y prop, not the guard) and added four more real test gaps (headers never asserted, `toContain` URL, defaults typed into the submit test, empty-array fetch untested). Complete surface-map table, every THREADED/MISSED verdict checked out. R2 check: 10/11 CLOSED with the one PARTIAL correctly reasoned (RNTL cannot fire onPress on a disabled Pressable, so the inner guard is unpinnable that way). **Diagnostic note: `cursor-agent` buffers ALL JSON output until exit — a 0-byte result file means running, not dead; the lead misread three legs as dead this way.** |
| 2026-09-05 | cursor/cursor-grok-4.6-**medium** (first `-medium` round) | review (10-change C2 UI stack, 8 posed items, reviewed per-diff) | **The round's best surface reader, at medium.** 330 s, zero NOT REACHED, 57/61 citations exact full-line (4 off-by-one or wrong-file, no selective quotation). Three sole MAJORs, all lead-verified and all in the −1s: (1) `formation.ts` comment says WAITING_FOR_LEADER counts as a Follower, the code requires `followers.includes`, and the test pins the opposite — the exact scenario the comment names is what the code permits; (2) `confirmPendingGoto` never re-checks `followerNow`, so a Go-to armed while detached fires after the vehicle is a Follower again; (3) the ring's `release` (GUIDED, stays in formation) and the card's `rejoin` (membership) are both labelled "Rejoin". Also the only leg to give the physically right account of a detached vehicle (continues to the last slot setpoint, then GUIDED-holds — no BRAKE is sent) where the gemini leg overclaimed. **Its one downgrade was wrong**: it held the stale-SITL battery at MAJOR because non-SITL fails closed — but the SITL lab fleet is the one flying today; the lead kept BLOCKING. Exhaustiveness: its `detached_ids` count said 7 and listed 8 (8 reproduces). Diagnostic: with pre-extracted diffs in an external dir the 128 KB argv limit is a non-issue; the JSON came back as one object this time. |

First real review rounds append here, per the journal format — verified hit
rates, not impressions.

## UNVERIFIED — capture on upcoming sessions

1. Ask-mode enforcement class: tool removed vs behaviorally declined
   (instruct-then-inspect with a hostile-ish prompt, clean tree either way).
2. Argv prompt size cliff, if any (size sweep like opencode's).
3. Permission-rule syntax and whether a project-level config overrides the
   global one (the opencode ordering trap has a cousin here somewhere).
4. `--sandbox enabled` semantics and platform floor.
5. Served-model verification (session transcript under `~/.cursor/chats/`?).

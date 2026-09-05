# `opencode` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for the OpenCode CLI, shared by
`opencode-adversarial-review` (read-only via a strict permission config) and
`opencode-implement` (write, no-push machine-enforced). Every item here was
measured live (dates kept as provenance; the pool changes fast — re-verify).

## Why this family exists in the pool

The free pool is an **additional model family** at zero quota cost. Its named
models (DeepSeek, NVIDIA Nemotron, and others that come and go) are none of
GPT/Gemini/Claude — so a second reviewer on HIGH-risk work no longer has to
spend paid quota.

- **Stealth models (family deliberately undisclosed) are frontier-grade but
  can never SATISFY the cross-family rule** — they might be a GPT/Gemini/
  Claude variant under the hood. Fine as an additional reviewer or as an
  implementer; never the accounting leg, and never review a stealth model's
  own work with itself.
- **Re-confirm the catalogue with `opencode models` rather than trusting any
  list.** Measured: models appear and disappear; a model you never probe is
  a retry target you do not have when the ones you know are down.
- Vendor-keyed models (e.g. `google/*`) also appear in the catalogue — they
  bill the user's own API key. They are NOT the free pool; do not spend them
  without asking.
  - **Carve-out — OpenRouter's own `:free`-tagged models** (`openrouter/*:free`,
    e.g. `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`,
    `openrouter/z-ai/glm-5.2:free`): zero monetary cost like the native pool,
    but DO need the OpenRouter API key configured in opencode's auth first —
    so treat them as free-to-spend once that key exists, not as a
    no-credential-at-all model. Confirmed live 2026-08-22: 19 models across
    vendors the native pool doesn't otherwise reach (NVIDIA Nemotron variants,
    Z-AI GLM, Google Gemma, Cohere, Liquid, Poolside, ThinkingMachines,
    Dots-Studio). **Enumerate live, don't trust this count** —
    `opencode models | grep ':free$'`; OpenRouter's free tier churns
    independently of opencode's own pool and carries its own per-key rate
    limit (commonly tighter than the native pool's congestion behavior) —
    measure before routing a time-sensitive round through it.

## OpenRouter `:free` candidates — first-probe results (2026-08-25)

One golden-answer probe per model (n=1 — see "n=1 proves nothing" below;
these are first looks, not yet default-pool promotions). The task: an
adversarial review of a real diff where a new independently-gated interface
(`validateRoute()`) exists and is unit-tested, but the production call site
still invokes the old method (`verify()`) against shared, non-independent
instances — the exact "interface exists and is tested" vs. "production path
actually uses it" trap that a Gemini leg and a Claude Sonnet 5 leg both fell
into on a real review this same week. Correct verdict: BLOCKING, evidence at
the `verify()` call site.

| date | model | family | role | outcome |
|---|---|---|---|---|
| 2026-08-25 | `openrouter/cohere/north-mini-code:free` | North | review (n=1) | HIT — correct BLOCKING verdict, exact citation, concise |
| 2026-08-25 | `openrouter/thinkingmachines/inkling:free` | Inkling | review (n=1) | HIT — correct BLOCKING verdict, reached it via real grep/read tool use |
| 2026-08-25 | `openrouter/poolside/laguna-s-2.1:free` | Laguna | review (n=1) | HIT — most thorough of the four: also caught that `validateRoute()` internally delegates back to `verify()`, and cited the class's own header comment admitting shared-instance reuse; slowest (~4–5 min) |
| 2026-08-25 | `openrouter/z-ai/glm-5.2:free` | GLM | review | UNTESTABLE — 3 consecutive attempts over several minutes all returned `[Decart] z-ai/glm-5.2:free is temporarily rate-limited upstream`, zero model output. A transport-layer finding, not a quality signal — retry later rather than concluding the model is weak. |

Run each of the three hits again before treating them as reliable — a single
correct answer on one hand-picked probe is exactly the "n=1" trap this file
already warns about.

## Native `opencode/*` pool — first-probe results (2026-08-28)

Distinct from the OpenRouter `:free` carve-out above — these are the CLI's
own native free-pool models, no OpenRouter key required. One golden-answer
probe per model (n=1 — same caveat as above), a fresh scenario built from a
real class of bug found the same week on a live review round (Swarm-Matrix
PF-03..PF-08 stack): a new energy-aware allocator is added and unit-tested in
isolation, but the production entry point's own deferral comment admits it
still calls the old, fuel-blind allocator. Correct verdict: BROKEN, citing the production
entry point's own call site, not either allocator function.

| date | model | family | role | outcome |
|---|---|---|---|---|
| 2026-08-28 | `opencode/big-pickle` | unknown (no OpenRouter listing found under this name; treat as stealth) | review (n=1) | HIT — correct BROKEN verdict, exact citation, ~6s, most concise of the six |
| 2026-08-28 | `opencode/nemotron-3-ultra-free` | Nemotron | review (n=1) | HIT — correct, concise, ~10s. Distinct route from the already-documented `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` above; not yet confirmed to be the same served weights |
| 2026-08-28 | `opencode/nemotron-3.5-lightning-free` | Nemotron | review | UNTESTABLE — first attempt hit the outer 180s timeout with the log stalled right after model selection (no error, no output); a 240s retry surfaced the real cause: repeated `AI_APICallError: [400] Provider returned error` on every retry with backoff, never succeeding. Transport-layer finding, not a quality signal — matches this file's own GLM precedent above. |
| 2026-08-28 | `opencode/mimo-v2.5-free` | **Xiaomi** (new family this file didn't track before this probe) | review (n=1) | HIT — most detailed of the six: also named the exact deferral-comment line; ~15s |
| 2026-08-28 | `opencode/hy3-free` | **Tencent** (Hunyuan, marketed as "Hy3"; new family) | review (n=1) | HIT — correct, concise, ~9s |
| 2026-08-28 | `opencode/muse-spark-1.2-contributor-free` | **Meta** (marketed as "Muse Spark"; new family) | review (n=1) | HIT — tied for most detailed: walked the trigger through the test's own fixture values (`unit-1`, `remaining_fuel=0`) instead of describing it abstractly; ~10s |

**Round 2 (2026-08-28, same day), a DIFFERENT bug shape** — deliberately not
a re-run of the same file, to rule out memorizing one fixture rather than
generalizing the trap class. This scenario is the "result computed and
persisted for audit, but the field that actually gates production is never
set from it" shape (a real bug this same account's lead caught live in a
Stanley Tsao PF-08 change the same week: a conflict verdict was computed
correctly but the pipeline's pass/fail field never read it). Correct verdict:
BROKEN, citing the unconditional `status = READY` assignment, not the verdict
computation.

| date | model | family | role | outcome |
|---|---|---|---|---|
| 2026-08-28 | `opencode/big-pickle` | unknown | review (n=2 total) | HIT again — same exact-citation style, ~7s |
| 2026-08-28 | `opencode/nemotron-3-ultra-free` | Nemotron | review (n=2 total) | HIT again — mid-run recovered on its own from one upstream `[502] Service temporarily overloaded`, then answered correctly on retry |
| 2026-08-28 | `opencode/nemotron-3.5-lightning-free` | Nemotron | review | UNTESTABLE again, and with a DIFFERENT error this time: `AI_APICallError: [404] Provider returned error` (round 1 was `[400]`) on every retry. Two failed attempts, two different HTTP codes, same day — reads as this specific model route being broken upstream right now, not one-off congestion. Do not route real work through `nemotron-3.5-lightning-free` until it clears on a later probe; `nemotron-3-ultra-free` (same family, same pool) is unaffected and fine to use meanwhile. |
| 2026-08-28 | `opencode/mimo-v2.5-free` | Xiaomi | review (n=2 total) | HIT again |
| 2026-08-28 | `opencode/hy3-free` | Tencent | review (n=2 total) | HIT again |
| 2026-08-28 | `opencode/muse-spark-1.2-contributor-free` | Meta | review (n=2 total) | HIT again — again cited the test's own concrete fixture values rather than describing them abstractly |

Five of six are now 2/2 on two structurally different bug shapes in one day —
past the single-anecdote stage this file otherwise warns about, though still
short of a full calibration-journal promotion (that wants dated rows over
multiple sessions/weeks, not two runs in one). `nemotron-3.5-lightning-free`
is 0/2 with two different transport error codes; treat it as currently
unavailable rather than untested, and prefer `nemotron-3-ultra-free` for
Nemotron-family free-pool work until it's re-probed clean.

**Family identification matters here more than the hit rate.** `hy3`,
`mimo-v2.5`, and `muse-spark-1.2-contributor` all resolve to disclosed
vendors in `opencode models`'s parallel `openrouter/<vendor>/...` listings
(`tencent/hy3`, `xiaomi/mimo-v2.5`, `meta/muse-spark-1.2-contributor`) even
though the native `opencode/*` names alone don't say so — checked before
writing these into [`data/families.json`](../../../data/families.json) as
`accounting_valid`. `big-pickle`
has no such cross-reference in the catalogue; treat it as `unknown` (extra
eyes, never the accounting leg) until proven otherwise. This is also why the
cross-check step matters: five names that read as playful codenames turned
out to be four real, differently-branded flagship models plus one genuine
stealth model, not five stealth models.

**Independent field corroboration, same week:** a teammate's Gerrit review
tooling (`Alan.Yeh`, Swarm-Matrix change #10922, 2026-08-27) already lists
`muse-spark-1.2-contributor-free` and `MiMo-V2.5` as live adversary legs
alongside `gemini-3.7-flash-high` — this probe is confirming models already
in someone else's real rotation, not discovering untested ones.

All five HITs here were faster (6-15s) than the OpenRouter `:free` table's
hits above (which ran up to 4-5 minutes) — a first data point, not yet a
claimed structural difference between the two pools; the OpenRouter probe
task was also a longer, class-based diff rather than this one's single
function-and-deferral-comment scenario, so latency is not comparable as-is. Run each hit
again, and probe `nemotron-3.5-lightning-free` again on a different day
before writing it off — one 400-error run under load is not a verdict either
way.

## Native `opencode/*` pool — real-diff round (2026-08-28)

The two probes above used synthetic single-function scenarios and five models
tied at full marks, so they ranked nothing. This round replaced the scenario
with a REAL pre-merge diff from a live service: a portal dashboard's fan-out
made concurrent (bounded pool, per-card deadline, whole-page budget) plus a
cache with a stated contract — actor-keyed, 60s TTL, errors not cached,
single-flight, invalidated on key change. 303 lines changed, 785 tests green,
and six mutations already caught by the lead before the reviewers saw it.

Four models, same frozen commit, same claims file, four independent read-only
worktrees, run concurrently.

| date | model | family | role | outcome |
|---|---|---|---|---|
| 2026-08-28 | `opencode/big-pickle` | unknown | review (n=1) | **HIT, top of the round** — found the shared MAJOR *and* the round's only unique finding: offboarding (`purge_redmine_key`, `revoke_user_tokens`) changes a credential without invalidating the cache. Stated unprompted that the lead's five-site mutation suite "structurally cannot catch" it — correct, those sites are all on the enrolment path. Both verified. |
| 2026-08-28 | `opencode/hy3-free` | Tencent | review (n=1) | HIT — same MAJOR, named it a resource-exhaustion vector. Saw the offboarding gap too but rated it MINOR (actor-scoped staleness, not a cross-actor leak). Defensible; the lead took MAJOR because offboarding is when stale data matters most. |
| 2026-08-28 | `opencode/muse-spark-1.2-contributor-free` | Meta | review (n=1) | HIT — same MAJOR, cleanest severity split ("HOLDS for HTTP response, FALSIFIED for resource exhaustion"). Did not reach the offboarding path. |
| 2026-08-28 | `opencode/mimo-v2.5-free` | Xiaomi | review (n=1) | **MISS — silent empty.** 23 steps, 81 KB of log, all file reads, last line still `Read minter.py`, zero conclusions. All four error greps clean (`auto-rejecting` 0, no 5xx, no TLS, `tokens.output=0` as always). Same shape as the DeepSeek empty run documented above. |
| 2026-08-31 | `openrouter/poolside/laguna-s-2.1:free` | Laguna | review (design doc) | HIT — confirmed both lead-posed gaps with quoted evidence, plus 6 consistency findings (a dangling root key nothing derives from any more; ambiguous key-symbol reuse; missing fail-closed on a soft-reboot path). Profile held: strong on spec/document consistency, thin on attack paths — weight it accordingly beside a defect-hunting leg. **Run 1 died silently after reading everything (the documented empty-exit mode); run 2 succeeded.** Budget the retry. |
| 2026-08-31 | `openrouter/poolside/laguna-s-2.1:free` | Laguna | review (code diff) | Partial: 5/5 verdicts with citations that all checked out, and correctly confirmed the fix under review — but returned HOLDS on the same mutation-coverage question terra alone got right. ~14 min, one transient upstream rate-limit auto-retried. Free-pool leg doing real work; not the leg to trust when the question is "does this test actually pin what it claims". |
| 2026-09-01 | `opencode/big-pickle` | **unknown (stealth)** | review (2 code changes + 1 plan doc, 11 posed items) | First scored round. Clean on the FIRST attempt — no silent-death retry needed, ~9 min, 25 steps, all four failure greps clean. Answered all 11 items, none NOT REACHED. Its best contribution was the sharpest *reasoning* on a gap two paid legs also found: it traced WHY the new `currentness == UNKNOWN` guard is untestable-as-written (the `uncertain_preview` fixture also nulls `durable_audit_acknowledged`, so the earlier clause always short-circuits first) and explicitly flagged that chain for the lead to verify — which checked out. **Self-report caveat: it closed with "MACHINE-DENIED: none" while the log recorded 3 real denials it had silently absorbed.** The read-only boundary held and the scaffold digest matched, so this is a self-reporting defect, not a containment one — but do not take this leg's own account of what it was denied. Family undisclosed with no `openrouter/<vendor>/` cross-reference: extra eyes only, never the accounting leg. |
| 2026-09-01 | `opencode/big-pickle` | **unknown (stealth)** | review (spec freeze doc, 6 posed items) | **The free leg out-judged both paid legs on the round's subtlest item, and the lead adopted its framing over theirs.** On "where is the root of trust for an authenticated-observation context", grok and terra both said there is none; this leg falsified the strong form — the spec *does* name an owner-independent root (a source adapter owned by a different person than the Provider) — and then identified what is actually missing: nothing lets the validator check that the context *came from* that adapter, so "fails closed" overstates what the text guarantees. That is the version that went into the review. Sole finder of a second real gap: the selector is given a named type while the observation context it validates against is never typed at all — the thing two separately-working owners collide on first. Also the only leg to explain *why* keeping three single-valued fields is right (the value is the **result of an evaluation**, distinguishing "evaluated and fresh" from "never evaluated") — a justification absent from the spec itself. Clean first attempt again, ~5.5 min. **The self-report defect did NOT recur**: it reported 2 MACHINE-DENIED and the log showed exactly 2, correctly named (n=1 clean; keep grepping the log). Still stealth, still extra-eyes-only for accounting. |
| 2026-09-02 | `opencode/nemotron-3-ultra-free` | Nemotron (disclosed — counts for cross-family accounting) | review (3 document changes, 7 posed items) | **First scored round** (the 2026-08-28 rows above are ~10 s probes, not review work). Mixed, and the negative half is the important half. Clean first attempt, exit 0, ~29.5 min, 50 steps — the slowest leg of the round by 5×. Answered all seven items, opened every file it cited, and its citations spot-checked verbatim. One sole real finding: the ADR grounds a normative convention ("every new field must touch three places") in an opaque gateway-memory id, unresolvable by any reader who has the repo but not a token. **But it also produced the round's only FABRICATED EVIDENCE STEP**: item (f) presents a `grep -r "alarm\." --include="*.cpp" services/` as its "Verification"; no such call exists in the log, and that bash form would have been denied by the read-only config. The conclusion was defensible from text it had actually read — which is exactly what makes it dangerous, since a plausible conclusion with an invented provenance survives a spot-check of the conclusion. It was also sole-WRONG on one item, claiming the ADR's out-of-scope list contradicts a sibling spec when the ADR explicitly labels those items 提案 and points at that spec. Same day, an earlier round on this account had two of its verdicts refuted by lead measurement (a NATS grant attributed to the wrong principal; an `npm ci` lockfile-desync claim that a `lockfileVersion 3` root object cannot produce). **MACHINE-DENIED self-report defect recurs across models in this family**: it reported "none", the log showed 4, all routed around with native tools — containment held, self-reporting did not. **Ruling: extra-eyes only, and verify its evidence steps, not just its conclusions.** On this account big-pickle has out-performed it twice at a fifth of the wall clock. |
| 2026-09-02 | `opencode/nemotron-3-ultra-free` | Nemotron (disclosed) | review (flight-command change, 11 posed items) | **Second scored round, and the case against it is now n=2 rather than n=1. Zero sole findings; one wrong BLOCKING; one wrong HOLDS; the fabrication defect recurred in a milder form.** It reported an AB/BA deadlock as BLOCKING, naming every line correctly — but the `cmd_ack_mutex` scope it called the A half closes *before* the line that takes the other mutex, so the inversion does not exist; its own supervisor caught this, and the three other legs independently found no deadlock. On the cancellation item it returned "not a finding" reasoning that the mission executor's own armed guard protects the operator sequence (it guards only the executor's own start) and missed entirely the abort path that three other legs found. **Fabrication, milder shape:** it cited a `file:line` in a file it never opened, inferring the number from a diff hunk header — content true, coordinate wrong. MACHINE-DENIED self-report was clean but vacuously: 0 true denials, so there was nothing to under-report; that is not evidence the defect is fixed. Slowest leg by 4x (942 s vs 216 for the fastest) on a first clean attempt. **Standing ruling unchanged and now better supported: extra eyes only, verify its evidence steps, and on this account big-pickle has out-performed it in both rounds where they can be compared.** |
| 2026-09-04 | `opencode/nemotron-3-ultra-free` | Nemotron (disclosed) | review (2 planning docs, 9 posed items) | **Third scored round; n=3 and the ruling should now be treated as settled. Zero sole findings again, and this time the provenance failure is structural rather than incidental.** One of the two reviewed documents does not exist at the frozen HEAD — it is added by the other change's parent line — so its `read` failed `File not found` and **it reviewed that entire 710-line document from diff text alone**. It also read the *wrong version* of the second file (the 1,024-line tree copy, not the 1,296-line revision under review) and cited line numbers into it. Of 27 citations checked, 5 held, 2 were close, and **20 had unusable coordinates** — offsets from −26 to +116, not constant, and not diff-output positions either, so they are approximations presented as citations; the coordinates were wrong even for the one file it did open. **The fabrication defect recurred in its most explicit form yet**: item (f) states "I verified in the diff context" about two `.cpp` files, when it opened no `.cpp` file and both diffs are markdown-only — round one invented a command, round two inferred a coordinate, round three claims a verification it did not perform. MACHINE-DENIED self-reported as none against a true count of 1 (round two's clean signal was vacuous — there was nothing to under-report). Both BLOCKINGs were refuted by the supervisor reading surrounding scope, and two MAJOR premises fell to one grep each. **Ruling: extra eyes only, and on a round where any reviewed file is absent from the frozen HEAD, this leg contributes nothing — check file presence before dispatching it.** |
| 2026-09-04 | `opencode/nemotron-3-ultra-free` | Nemotron (disclosed) | **research** (4 aspects, 20 questions) | **Fourth scored round, and the first with a clean provenance record — which is what finally separates two failure modes that had been scored as one.** 17 OPENED · **3 EVIDENCE-WRONG** · 0 FABRICATED-PROVENANCE · 1 mild self-disclosed FABRICATED-CLAIM · 11 UNSOURCED URLs. Every cited file was actually opened; the coordinate errors all carry TRUE content (a `_get` cited 134 lines off; an ADR blockquote welding three sources into one range, one phrase of which lives in a code comment and nowhere in the ADR). **So the operational posture is neither "do not dispatch" nor "trust with spot checks": dispatch it, and re-resolve every citation before carrying it forward.** Denials self-reported 2, true 3 — the family defect persists. Zero NOT REACHED on the trap question. Its largest gap was analytical, not honesty: it **opened** a 398-line in-tree backend for the very system it was asked about, then answered from model memory anyway — evidence that a brief's framing can override evidence already in a leg's context. |
| 2026-09-05 | `opencode/big-pickle` | **unknown (stealth)** | review (consistency / is-it-still-true lens on a Gemini-written mobile UI diff, 5 posed items) | **Clean first attempt, 12 findings, every one verified real by the lead** — the only leg that read the *repo's own parallel structure* (`TransactionForm` as the model) and listed each drift with both quotes: read-only inputs left editable with no explanation, the new write path outside `TRANSACTION_WRITE_ENABLED`, error text without `accessibilityRole="alert"`, composed i18n keys outside the `DYNAMIC_KEYS` guardrail, EN market labels that were tautologies (`"US (US)"`), dead catalogue keys frozen into the snapshot, and the ratio rendered num:den in the list but den:num in the form. Independently converged with GPT on the `pfhistory` invalidation miss (it found it via the stale-after-write *comment* in TransactionForm — is-it-still-true working as designed). Self-reported its two MACHINE-DENIED pipelines accurately. Evidence gate delivered for 27 files. Still stealth → extra eyes only for accounting; on this account it is now the strongest *consistency* leg in the fleet. |
| 2026-09-05 | `opencode/big-pickle` | **unknown (stealth)** | review (10-change C2 UI stack, 8 posed items, reviewed per-diff) | **Second scored review, and consistent with the first: nothing invented, and its value is in the seams between things.** 874 s (slowest, 5x codex), 43/45 citations exact; the two misses were both coordinates in the 4,101-line `MissionPlannerPage.tsx` it opened once. Zero sole defects, but it was the leg that **checked provenance the brief did not ask for**: confirmed from the diff that 11522 changed Deploy from `>` to `>=` while the planner's own throw stayed `<=` — so the comment claiming parity is false and exactly-30 % passes both gates and fails at plan time — and correctly caveated the ungated `formation_command` as an inherited pattern that 11518 extended to a per-vehicle command. **The MACHINE-DENIED self-report defect took a new shape**: it listed four denials (count right) but named commands that never ran (identities confabulated); the true four were the non-git halves of compound commands. Verify from `action.action=deny` in the log, as before. Both scaffold files were swallowed by the global `~/.gitignore`, so the "untracked entry" declaration was vacuous — the `a-w` + SHA layer is what proved immutability. Still stealth → extra eyes only. |
| 2026-09-05 | `opencode/big-pickle` | **unknown (stealth)** | review (take-over CL: mission-service + adapter + gateway + frontend, 8 posed items) | **Third scored round and its first sole BLOCKING — found in a seam, which is now n=3 for that shape.** 874 s (slowest by 5x). While the other three legs argued about what BRAKE does to a vehicle that HAS an execution entry, it asked what happens to one that does not: a vehicle whose old batch is still UPLOADING has no `mission_executions` row, so the adapter's release skips it entirely — and the old upload then finishes, publishes READY, and `updateAssignmentDelivery` applies it with no CANCELLED guard, re-activating the assignment the release had just retired. One vehicle, two active missions: the exact invariant the change exists to protect. Lead-verified end to end and the lead's first BLOCKING. Its consistency habit also caught that the change's Deploy comment claims a `>=`/`<=` parity with the planner that does not hold, and it correctly caveated another leg's "new unguarded path" as an inherited pattern. 43/45 citations exact; both misses were coordinates in the one 4,101-line file. **The MACHINE-DENIED self-report defect did not recur this round** (it reported a list; true count 5). Still stealth → extra eyes only for accounting; on this account it is the leg to dispatch when the defect is likely to live between two components rather than inside one. |
| 2026-09-06 | `opencode/big-pickle` | **unknown (stealth)** | review (PF-19/PF-20 contract across 3 services + a migration, 10 posed items) | **Fourth scored round. 39 of 40 coordinates exact, containment clean on every measure, and the MACHINE-DENIED self-report accurate in both count AND identity — the family defect did not recur.** 289 s, first attempt, zero NOT REACHED, and it obeyed the reading budget (two targeted partial reads of a 1,927-line file rather than a whole-file read). Its BLOCKING was the round's item to act on and needed no downgrade: it opened `init.sql` on its own initiative, found the primary key is `(proposal_id, replan_revision)` while the INSERT's `ON CONFLICT` targets `(proposal_id, revision_cause_key)`, and traced the consequence to a device-reboot proposal swallowed by a generic catch. **The failure mode this round was over-claiming where the brief asked for a count**: two 'this is the sole test' claims with no command, one of which has three counterexamples; and a 'tautology' call on an assertion that genuinely pins preimage exclusion, which if acted on would have deleted a real test. Both were caught by re-running the greps it did not run. Also worth knowing: two of its own greps returned exactly '100 matches', which is opencode's result cap, not a count — it did not flag the truncation. Still stealth → extra eyes only; on this account it remains the leg to dispatch when the defect is likely to live between two components. |

Three of four converged independently on the same MAJOR: `ThreadPoolExecutor`
bounds workers but NOT its queue, and the pool is process-wide — so a burst
against one slow backend degrades the dashboard for everyone, reintroducing at
portal scope the exact failure the change fixed at page scope. Verified and
fixed.

**What made this round rank them.** A real diff has more than one layer. Every
model that engaged found the queue bound, because it is visible from the pool
construction. Only one asked the second-order question — *which code path does
invalidation hang off, and is that the only path that changes a credential?* —
and that is what separated it. This matches the Laguna observation in the
OpenRouter table above: single-layer traps do not rank, second-order ones do.
If your probes keep tying, the probe is too small, not the models too similar.

**Caveat on ranking by one round.** n=1 per model, one diff, one domain
(concurrency + cache invalidation). MiMo scored a MISS here and 2/2 HITs on the
two synthetic probes above — this round says it did not finish a 300-line
review, not that it is worse at finding bugs. Big Pickle's family is still
`unknown` in [`data/families.json`](../../../data/families.json), so it remains an ADDITIONAL pair of eyes,
never the leg that satisfies the cross-family rule, however well it performs.

## Concurrent legs across the two routes (measured 2026-08-30)

One session launched a native-pool leg (`opencode/nemotron-3-ultra-free`) and
an OpenRouter-key leg (`openrouter/poolside/laguna-s-2.1:free`) at the same
minute; both completed full reports (55 KB / 52 KB). The two routes are
separate queues — native congestion does not touch the OpenRouter key's
per-key limit, and vice versa — so "one native + one openrouter" is the
concurrency-safe pairing. The suspect case remains SAME-model concurrency
(the deepseek n=1 above); pick different models per concurrent leg.

Laguna S 2.1 is now n=2 on real reviews and remains the thoroughness
outlier: on 2026-08-30 it was the only leg of four to append a
"NOT REACHED (runtime)" boundary with an exact command per claim, and it
caught a fullwidth-input false-negative the sequences leg passed. Slow
(~5–10 min); brief it where depth beats latency.

## Auth: the free pool needs no credential

Measured: free-pool calls succeed with no login dance, no token expiry, no
silent-auth coin flip. (OpenRouter's `:free` tier is the one exception —
see the carve-out above; it needs the key but not spend.)

## Headless invocation — the prompt goes on STDIN, never in argv

```bash
cd "$TARGET" && opencode run --print-logs --log-level INFO \
  -m opencode/<model> \
  < "$RUN_DIR/prompt.md" > "$RUN_DIR/out.log" 2>&1
```

**This is not a style preference — argv cannot carry a real prompt.**
Measured across models: an argv prompt beyond ~1–2 KB hangs before the
session is even created — `message=init` and then nothing, forever. It is
SIZE, not content (filler text hangs identically). A size sweep pinned the
reliable-failure threshold at ≥2 KB. A second, fully deterministic argv bug:
**a prompt whose first character is `-` hangs forever** — the CLI parses it
as a flag and blocks on stdin that never arrives.

**Diagnosing a stalled run** — the bootstrap sequence is fixed, so where the
log stops names the failure:

| Last line reached | Meaning |
|---|---|
| `message=init` and nothing more | argv prompt too large — NOT congestion, NOT the model |
| `message=created id=…` → `event connected` → `stream providerID=…` | bootstrap fine; a stall after this is real upstream congestion |

The bootstrap lines appear within ~0.2 s on a healthy run. Their absence
looks identical across every model — three different backends cannot be
congested in exactly the same way at the same moment, so uniform silence is
a LOCAL fault. Confirm with a one-word probe: a trivial prompt answers in
under 10 s even while argv-launched runs beside it are wedged.

- Useful flags (verified via `--help`; re-verify per version):
  `-m provider/model`, `--agent <name>`, `--dir <path>`, `--format json`,
  `--variant`, and `--auto` (**never use it** — auto-approves anything not
  explicitly denied; this family's equivalent of a skip-permissions flag).
- **Always pass `--print-logs --log-level INFO`** and capture to a file: the
  INFO stream carries `projectID=` (config binding, below) and
  `evaluated permission=` (the per-tool-call audit trail).
- Launch under the host's background mechanism with a generous timeout.
- Every run fires a small title-generation call (`agent=title` in logs) —
  expected.

## The permission model — read this twice, both traps are silent

Rules live in `opencode.json` at the **project root** and merge into the
agent's rule array. Two measured traps:

1. **A repo with zero commits is not a project.** The log says
   `projectID=global` and the project's `opencode.json` is **silently
   ignored** — every rule you wrote simply doesn't exist. After one commit,
   `projectID` becomes a real hash and the config loads. Check the
   `projectID=` line on the first run in any fresh worktree.
2. **LAST match wins, so order is everything.** Measured in both directions:
   with `{"git push*": "deny", "*": "allow"}` the wildcard swallowed the deny
   and the denied command RAN. Write the wildcard FIRST, then the specific
   rules. The same ordering means **project config overrides agent
   defaults** — a project-level `"edit": "allow"` silently erased the plan
   agent's built-in edit-deny. One role, one directory, one config.

What a denial looks like: the tool call fails with a "rule prevents this
tool call" message, **the model receives it and keeps going** — it can
report MACHINE-DENIED and finish. With `--print-logs`, every tool call logs
`evaluated permission=… action.action=allow|deny` — the audit trail proving
which rule governed.

Do not rely on `--agent plan` as a review boundary: its edit-deny is
config-overridable and its bash rules are `*: allow` (measured). The
explicit deny-by-default config in the review skill is stronger.

## Congestion: the price of free

Measured at peak: `[503] The request queue is full` (the pool's own
gateway), `[502] Upstream error … ResourceExhausted`. Off-peak the same
models answered in seconds.

Congestion after bootstrap is SLOW rather than fatal: a measured review run
read all its files, went silent 10+ minutes inside the final generation,
resumed, and exited 0 with a full report at ~31 minutes. Budget 40m+ for a
real review; do not kill it at the first long silence — but only once the
bootstrap lines are confirmed (a pre-bootstrap stall is the argv bug, and no
amount of waiting fixes it).

Consequences:

- **A retry loop is mandatory**, cycling models, with the presence of an
  `evaluated permission=` line as the test that a run did real work.
- Errors are LOUD (nonzero exit + clear message); hangs are the quiet
  failure mode — bound every run with a timeout.
- Do not put the free pool on a time-critical path. It is the cheap second
  opinion and the cheap mechanical implementer, not the primary gate.
- **Switching models inside a bad window does not help** — the pool is one
  queue (re-measured three times). Wait, or route to a different family.

## n=1 proves nothing here — this pool is flaky BY DESIGN

A worked example of getting it wrong, kept because the mistake is the
default human move: chasing the argv hang, a single 4.4 KB argv run happened
to succeed, and a single tiny argv run in an MCP-wired repo happened to
hang. From two samples an entire causal story was built and stated
confidently ("argv size is not the cause; a remote MCP server blocks
startup") — complete with a synthetic reproduction that also hung once.
Repeating each leg destroyed it: tiny-argv passed 2/2 in the same MCP-wired
repo, stdin passed with MCP present, and a proper size sweep failed 4/4 at
2/5/9/17 KB. The MCP theory was noise; the documented argv finding was right
all along, and the sweep merely tightened its threshold.

Before writing any mechanism into this file: **run each arm at least twice,
and prefer a sweep to a pair of anecdotes.** A hang here has at least two
independent sources (argv size, post-bootstrap congestion) plus genuine
flakiness at the boundary; one observation cannot separate them.

## Measured reviewer calibration (keep your own)

This is where your calibration journal rows for this family live — one row
per run, dated, with verified hit rates (see
[`docs/calibration-journal.md`](../../../docs/calibration-journal.md) for the
format and rules). Highlights from the journal this file was extracted from,
kept as *shape examples*, not as your
data:

- A free leg returned 7 findings on a consistency diff, 7/7 verified real.
- The same model later found the only LIVE defect of a round on a wire
  contract — a silent-truncation bug that two paid legs and a human reviewer
  had all called the opposite way.
- The other named model: competent, lower yield on the same targets; its
  measured niche is huge-context work (native 1M context — a structural
  property, not a capability claim).
- In paired rounds against another family on one security design, this
  family was the LENIENT one on attack paths (passed a bearer-credential
  defeat, a pre-check that bounded nothing, a DoS-able saturation condition)
  while ALONE catching three document-consistency defects. Route briefs
  accordingly.
- Two consecutive zero-token completions in one window, both models, each
  having read every file — the pool is one queue.
- `tokens.output=0` was retracted as a verdict after a run with that exact
  fingerprint contained a complete, verified review — grep for the report's
  section headers before discarding a run.

## A long review eats its own prompt — put the ask in a FILE

Measured twice: a review that read many files came back having lost the
numbered claim list to context compaction. Reordering the prompt does not
help — compaction drops the original user message as a unit. The defense is
structural: put the claims in a file inside the review directory and tell
the model to re-read it on demand; give reading budgets for big files;
authorize a partial answer ("mark the rest NOT REACHED") so the model's
options aren't "guess" or "stop and ask".

## Instruction-layer inheritance

Measured: asked to `git push`, a free-pool delegate refused BEFORE the
machine layer saw it, quoting the user's global no-push instruction
verbatim. opencode natively reads `AGENTS.md` and can be granted read access
to a shared skills directory — so repo rules and the global instruction
layer travel with the delegate for free. Belt; the permission config is the
braces. State destructive-adjacent rules in the task prompt anyway.

## Housekeeping

- The per-role `opencode.json` you drop into a worktree shows up as an
  untracked file — expect it in scope checks, never stage it, remove at
  teardown.
- `opencode stats` reports token usage/cost per provider.
- `opencode debug config` (from the target dir) prints the resolved config;
  `opencode debug agent <name>` prints the merged rule array in evaluation
  order — the fastest way to verify what will govern before spending a run.

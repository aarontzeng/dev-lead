# Research briefs

The suite's third role, beside implement and review: **fan out a question, not a
change.** A lead splits a problem into falsifiable questions, dispatches them to
delegate families through the existing review adapters, verifies every returned
fact, and writes the plan itself. Legs return facts with citations; legs never
write plan prose.

Every rule below is a measurement, not a preference, and each carries the
failure that produced it. They were extracted from one pilot on 2026-09-04 —
a four-leg fan-out over an open-source governance gateway, run against a peer
maintainer who held the same tree and argued back. Where a rule reads oddly
specific, that is on purpose: the specificity is the evidence.

Two things the pilot established before any rule did:

- **Ask whether the leg can EXECUTE.** A read-only leg answering "does X hold"
  is doing literature review; a leg that can run a script is doing an
  experiment. Three reading legs — two of them frontier models — missed a
  cross-tenant content leak that the one executing leg reproduced in a single
  round, along with a negative result (a real mechanism whose trigger was
  unreachable) that no reading leg can produce.
- **Most of what goes wrong is the lead's.** Six instruments proved too narrow
  in one afternoon, all of them the lead's: a grep, a scoring rubric, two
  briefs, and two closing tallies. The leg roster was not the variable — with
  the caveat in rule 18, which is the one a reader will want.

---

1. **Falsifiable questions, never open surveys.** "Investigate how X works"
   invites a plausible synthesis with invented citations. "Does f() call g()
   when the policy is P? cite the line" does not. Measured: a leg with a
   three-round fabrication record produced its cleanest output when every
   question had a right answer a citation settles.

2. **Sites, not distinct values.** An answer that will drive a change must give
   SITES with line numbers. A deduplicated inventory ("the suite issues these
   git command shapes") answers a different question from "where does this
   happen", and the two are indistinguishable in a report. Measured 2026-09-04:
   a maintainer inventoried command shapes with `sort -u`, saw one occurrence,
   fixed one call site, and shipped a fix that missed two of three -- 42 test
   failures became 5, not 0. His own standing lesson said "a finding is a CLASS:
   grep for the pattern's siblings before calling it closed."

3. **Name the settled decisions in the brief, verbatim.** A leg will otherwise
   produce findings against choices that already have an ADR, and that noise
   costs the lead more to filter than the real answers cost to verify.

4. **Nothing enters the plan unverified.** Fan-out saves the lead's SEARCH time,
   not reading time. Measured: two of three legs once converged on the same
   wrong conclusion; only reading the source settled it. A false finding in a
   review gets challenged; a false premise in a plan gets built on.

5. **Ask whether the leg can EXECUTE.** A read-only leg answering "does X hold"
   is doing literature review; a leg that can run a script is doing an
   experiment. Measured on this pilot before any research answer landed: simply
   running the target's test suite on a different machine surfaced two real
   defects that three review rounds and two releases had not, because every one
   of them ran on the author's box.

6. **Verify the peer, not just the legs.** The discipline "check every returned
   fact against the tree yourself" is usually aimed downward at delegates. It
   applies sideways too. Measured 2026-09-04: a peer maintainer said a new guard
   test goes red when a call site regresses; the lead re-injected the regression
   and watched it go red rather than believing the claim. Same cost as reading a
   leg's citation, same class of error caught. A dossier should record WHO
   verified each fact, including when the answer came from a human-equivalent
   peer rather than a leg.

7. **Prefer removing the dangerous affordance over adding a rule.** A rule with
   a rationale invites a judgement call at exactly the moment judgement is
   worst. Measured: `git checkout -- <file>` silently reverted uncommitted work
   four times in one day on one machine, the fourth time AFTER a guard script
   existed, because the maintainer typed the command by hand — it was always
   right there and always looked obvious. The lead independently arrived at the
   safe path by accident (restoring from a `cp` backup, so the dangerous verb
   never entered the working set). The durable fix is the absent affordance, not
   the remembered rule.

8. **Score a leg on its POINTERS and its CONCLUSIONS separately.** Measured
   2026-09-04, agy/gemini-3.8-flash-high: 58 of 62 citations exact, and the
   generalising prose built on those citations independently false — it wrote
   "no background scheduler, worker thread, cron, or inbound webhook listener"
   about code it had genuinely read, while `server.py:656` starts a named
   daemon thread. Distinct from FABRICATED-CLAIM (asserts a verification it did
   not perform) and from EVIDENCE-WRONG (opened the file, reported false
   evidence about it): here the evidence is right and the sentence built on it
   is wrong. Call it CONCLUSION-UNSOUND. The remedy is different in kind — you
   keep dispatching the leg, follow its citations, and discard its conclusions.
   On this round BOTH genuinely valuable outputs came from chasing its pointers
   (an undocumented REST route; a deliberate fail-open in the tenancy filter)
   and NEITHER was anything the leg concluded.

9. **Follow a citation one line further than the claim needs.** The round's
   most valuable finding was not an answer to any posed question. A leg cited
   `search.ts:541-543` for "does this still filter per row"; reading to `:566`
   found a 14-line comment documenting a deliberate fail-open, whose own text
   calls letting an unverifiable row through "the safe fallback". A security
   document had listed that behaviour as hypothetical. Nobody asked for it. It
   came from a peer reading PAST the cited line instead of confirming it and
   stopping.

10. **SUPERSEDED by rule 19 — kept for the record, not for use.** ~~Ask WHERE, not WHETHER.~~ Testable brief-design claim, proposed by the
    peer maintainer 2026-09-04 and being measured on this pilot: a leg in the
    CONCLUSION-UNSOUND class answers "does the backend filter by project?" with
    a sentence you must throw away, and "which file and line performs the
    project comparison?" with a pointer that leads somewhere neither party
    knew about. Both of the round's real findings came from questions that
    happened to be phrased as where-questions. The measurement to run per leg:
    count how many of its useful outputs came from a where-question versus a
    whether-question. Corollary observed the same hour: the lead answered a
    where-question ("what evicts a session, and on what schedule?") with four
    greps and got a bound nobody had, after a whether-question on the same
    subsystem had produced only agreement.

11. **A question's framing decides which evidence a leg considers relevant —
    including evidence it has already read.** The strongest result of the
    2026-09-04 pilot, and stronger than rule 10. Aspect (b) asked "which GitLab
    API calls implement these four methods?", framing an in-tree fact as an
    external-documentation problem. Three legs produced 19 unfetchable
    `docs.gitlab.com` URLs between them while a 398-line working
    `GitLabHttpBackend` sat in the tree — and the free leg **opened that file**
    (read #19 in its own tool inventory) and still answered from model memory.
    The framing overrode evidence already in its context. This is a much
    stronger claim than "legs fabricate under open surveys": it means a brief
    defect can make a leg ignore what it is looking at. The remedy is the same
    as rule 2 — ask where, in this tree — but the failure is not omission, it
    is misdirection, and it is invisible until someone checks the answer
    against the tree rather than against the question.

12. **Brief defects and leg defects look identical in a report.** Two of the
    pilot's three biggest problems were the lead's: (c3) was a whether-question
    with two defensible readings, so two legs disagreed and both were right;
    (b) was an external framing over an in-tree answer, so three legs
    hallucinated in unison. Neither leg misbehaved. Before charging a leg with
    a failure, re-read the question you asked — a leg cannot be blamed for
    answering it. The tell is convergence: legs disagreeing usually means an
    ambiguous question, legs agreeing wrongly usually means a mis-framed one.

13. **Score decline separately from accuracy.** On the same (a5) question with
    the same instruction ("no URL + version → NOT REACHED"), one leg returned
    four NOT REACHEDs and another returned four URLs with version stamps and no
    fetch capability. The difference is not accuracy — it is whether the leg
    will refuse. A leg that declines on ground it has genuinely read, rather
    than offering a source-reading in place of the evidence standard the brief
    demanded, is following the brief's evidence rules rather than its topic.
    That is rarer than accuracy and is invisible to any citation-accuracy
    metric, because a leg that declines produces nothing to check.

14. **The detector for a collapsed summary is a second method with a DIFFERENT
    failure mode — not a second look.** Observation: the peer maintainer, from
    three instances in his own artifacts. Those three had a competing
    explanation he could not rule out from inside — that he was simply being
    sloppy that day — and a rule built on them would have been a rule about
    vigilance, which does not survive hour six. The DISCONFIRMING case is the
    lead's: a fourth instance, different agent, different tool, produced while
    building the instrument to measure this very error. That is what makes it a
    property of methods that DEDUPLICATE rather than of one agent on one day,
    and it is what licensed the sharp form. Observation his, generalisation
    joint, load-bearing datum the lead's. A grep and a `sort -u` both fail by collapsing; running a second grep
    collapses the same way and confirms the first. What caught each of the four
    was a method that fails differently: a leg's independent enumeration against
    the lead's grep (9 sites vs 16); a reviewer counting constructor lines
    against the category noun "quotas" (1 vs 4); a leg reading source against a
    document's stated count of process-local state (5 vs 7); and the lead's grep
    against a peer's command-shape inventory (2 call sites vs 1 shape). This is
    the argument for heterogeneous legs over more legs: a fan-out whose members
    search the same way multiplies confidence without adding detection.

15. **Never give special instruction for one branch of a yes/no question.**
    Caught by the peer before the result landed, which is the only time it can
    be caught. A cell-3 brief said "if your answer is no, the completeness
    argument is the whole of the evidence" and said nothing about the yes
    branch — telling the leg, in effect, which answer was anticipated. A "no"
    result would then be unseparable from three causes: the phrasing suppressed
    enumeration, the answer is genuinely negative, or the prompt primed for it.
    The repair is one symmetric sentence. The general form: any instruction
    conditioned on the answer is a hint about the answer, and an experiment
    whose prompt encodes its own hypothesis measures the prompt.

16. **A scoring rubric is an instrument, and it fails WORSE than a search does.**
    A too-narrow search fails visibly: the list is short and someone eventually
    notices the gap. A too-narrow rubric fails invisibly, because forcing a
    result into the nearest bucket produces a COMPLETE-LOOKING SCORE — and a
    score carries more authority than a list. Measured twice in one afternoon,
    both times the lead's instrument: a nine-site grep that a leg beat with
    seventeen, and a three-bucket CONFIRMED/MISSED/INVENTED rubric under which
    ten true rows would have scored INVENTED. Both were widened by the person
    downstream rather than by the person who built them.
    **Remedy, stronger than rule 14's:** every scoring scheme gets an explicit
    "does not fit" bucket from the start. Today that bucket was invented
    mid-run by an alert supervisor. That must not be load-bearing.

17. **Pre-registration has force only if the record shows it preceded the data.**
    A decision rule written up afterwards is indistinguishable from one chosen
    to fit the result, even when it was not. Bank the rule, the anchors, and any
    standing bet in a timestamped artifact BEFORE the report arrives, and cite
    the exchange that timestamps it rather than the file that contains it — a
    file can be edited, a cross-session message ordering cannot. Corollary: name
    who bet what. A prediction with an owner is falsifiable; an unattributed one
    quietly becomes whatever the result was.

18. **Instrument findings are downstream of legs good enough to exceed the
    instrument — do not conclude the roster was cheap.** A selection effect the
    peer caught before it was written down. Every instrument defect found on
    2026-09-04 — the nine-site grep, the three-bucket rubric — surfaced ONLY
    because the legs returned seventeen. A weaker roster returning nine would
    have matched the lead's ground truth exactly, scored 100% CONFIRMED,
    required no EXTRA-VERIFIED bucket, and confirmed the instrument was fine.
    The roster's contribution is invisible precisely when it works.
    Honest form of the conclusion: **given legs strong enough to exceed the
    lead's own answer, the marginal return on MORE legs is lower than the return
    on fixing the question, the rubric, and the environment.** The roster bought
    the thing that exposed everything else, and then stopped paying. State this
    as a limit on the conclusion, because the conclusion is what gets quoted.

19. **Demand the completeness ARGUMENT, in a form the lead can re-run.**
    Replaces rule 10, which measured the wrong variable. The 2x2 (same leg, same
    question, same clone): whether without demand = 1 route; whether WITH demand
    = 17; where WITH demand = 17. The interrogative is incidental; the demand is
    the mechanism.
    **But a stated argument is not a checked one.** Cell 3's completeness note
    named a grep, a scope, and a count -- "only eight places" -- self-rated
    HIGH (1.0). The true count was 57, and the miss hid a second REST route
    exposing tenancy. Catching it required the lead to think to re-run the grep.
    So ask for the exact command AND its output or hit count, in a form that can
    be pasted and diffed: not "state what search establishes completeness" but
    "give the exact command you ran and the number of results it returned; we
    will re-run it." Costs the leg nothing -- it already ran the command -- and
    converts a claim needing judgement to audit into one needing a paste. Same
    move as every repair today: not "be alert", but remove the place where
    alertness was required.

20. **A brief that names an identifier inherits that identifier's SPELLING as an
    unstated scope limit.** Three runs, two model families, one blind spot: every
    enumeration was keyed to camelCase `sessionId` because the brief was, and the
    brief was because the lead was. The route all three missed
    (`POST /agentmemory/mcp/prompts/get` -> `mcp/server.ts:1724`) takes snake_case
    `session_id`. Both cell-2 legs SAW it and excluded it on naming grounds --
    defensible against the brief's literal wording and wrong about the world.
    Repair: name the concept, or name the spellings. A leg cannot widen a scope
    the brief narrowed.

21. **Two kinds of moment, two kinds of remedy. Do not apply one to the other.**
    The lead proposed a through-line -- "every surviving rule removes a
    judgement call; every corrected rule required someone sharp at the right
    moment" -- and the peer falsified it against the surviving list. Rules 14,
    17 and 18 survived intact and NONE of them removes a decision: whether a
    second method fails differently, whether N instances from one source can
    separate a method failure from a bad day, whether a conclusion is
    survivorship. All three are judgement calls that got better CALIBRATED.
    The real split is what kind of moment a rule governs:

    - **Recurring operational moments** -- the restore, the scoring bucket, the
      completeness demand. Same decision, many times, under fatigue. Here the
      mechanical repair is available and strictly correct, because a rule that
      asks for alertness loses to hour six. (Rules 7, 16, 19.)
    - **One-off inferential moments** -- is this a method failure or a bad day,
      does my instrument fit my data, is my conclusion survivorship. Novel
      shape, no repeated groove to install a guard in. A rule here can only say
      how much weight the evidence carries. (Rules 14, 17, 18.)

    **The second category has a substitute for mechanisation and today supplied
    every instance of it: someone OUTSIDE the inference.** The lead's grep
    against the peer's three-instance set; the peer's reading of the lead's
    conclusion; a second machine against a suite that passed locally. That is
    what stands in for a guard when no guard can be built -- not more alertness
    from the same head that formed the hypothesis. Rule 10 was struck by an
    inference, not by a guard.

    Note the shape of this rule's own history: the through-line it corrects was
    itself a summary standing in for a structure (the day's own pattern, fifth
    instance, the lead's), and it was caught by the person outside it. That is
    the rule demonstrating itself.

22. **A cross-session peer review carries CLAIMS in one direction and CHECKABLE
    FACTS in the other — know which way you are pointing.** The lead closed the
    2026-09-04 exchange saying neither party had accepted a summary unchecked.
    The peer falsified it: he had accepted essentially all of the lead's
    factual output -- route counts, citation tallies, grep hit counts, the
    supervisor's bucket scoring -- and COULD NOT have checked any of it,
    because he did not have the repository under discussion. The asymmetry is
    structural, not a lapse:

    - The peer's contributions were ARGUMENTS and PREDICTIONS -- testable by the
      lead against real artifacts. One prediction came back falsified, which is
      the proof that direction of the channel worked.
    - The lead's contributions were GREPS, COUNTS and CITATIONS -- claims in the
      peer's session, taken on trust because no alternative existed.

    That is exactly the condition under which a summary standing in for an
    enumeration survives longest, and today's own §4 result is the caveat on
    today's own method: a completeness argument, self-rated 1.0, false, caught
    only because the lead held the repository. Nothing in the message exchange
    could have caught it.
    **Repair, identical to rule 19's:** to make the channel carry facts rather
    than claims, send the command and its output, not the conclusion, so the
    receiving session can paste rather than trust. And when writing up a
    cross-session collaboration, state which facts were checkable by whom --
    "per-fact attribution" means less than it sounds like if half the facts
    were unverifiable at the receiving end.

23. **The collapsed-summary pattern lives in CLOSING LINES.** Three instances on
    2026-09-04, all in the lead's summaries, all caught by the peer, all in a
    sentence written to wrap something up:
    (a) the through-line "every surviving rule removes a judgement call" --
        falsified against the lead's own surviving list;
    (b) "neither of us accepted a summary unchecked" -- the peer could not check
        any of the lead's greps and counts, having no repository;
    (c) "the corrections ran four-to-nothing" -- true as a count, and a
        participant producing nothing has a perfect record BY CONSTRUCTION. In
        that stretch the roles had separated: one session generating claims, one
        with nothing to do but test them. Corrections flow tester-to-generator
        because that is the direction the work flows, not because one head is
        sharper. Earlier, when both were generating, they ran both ways.
    **Why here:** a closing line is written to feel finished, and feeling
    finished is exactly what a collapsed structure feels like. A tidy number in
    a concluding sentence is the highest-risk position in a document.
    **Remedy:** treat every closing summary as an enumeration to be counted
    against its source, and prefer a structural statement to a scored one --
    "the roles had separated" survives inspection, "4-0" does not.

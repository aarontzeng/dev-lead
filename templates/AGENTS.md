# AGENTS.md — dev-lead workflow adapter

> **What this file is.** A drop-in entry point that lets any file-reading
> coding agent (codex and opencode read `AGENTS.md` natively; agy and others
> can be pointed at it) participate in the
> [dev-lead](https://github.com/aarontzeng/dev-lead) cross-model workflow
> without Claude Code installed. Copy it into your
> project root — or merge the sections below into your existing `AGENTS.md` —
> and fix the one path in the next section.

## Where the skills live

The dev-lead skill suite is checked out at:

```
DEV_LEAD_ROOT=~/path/to/dev-lead        # ← EDIT THIS
```

All references below are relative to that root. If your CLI supports adding
external directories (agy `--add-dir`, opencode external-directory
allow-list), grant it read access to `$DEV_LEAD_ROOT`.

## Reading order

1. `docs/methodology.md` — the rules that govern every role. Read it once
   per session before doing anything else. Non-negotiables: the cross-family
   review rule, frozen review targets, merge-base spans, evidence gates,
   mutation-proofing, the human merge gate.
2. The skill for **your role in this run** (see below).
3. That family's runtime reference
   (`skills/<family>-adversarial-review/references/<family>-runtime.md`) —
   operational traps, all incident-tested. Do not re-learn them the hard
   way.

## Which role are you?

**You are the LEAD** if a human asked you to orchestrate a whole
feature/fix. Read `skills/dev-lead/SKILL.md` and follow its phases. Before
accepting the role, pass its host capability gate: you must be able to run
the repo's test suite and mutation checks *yourself*, natively. Delegate
implementation via the `skills/<family>-implement/` skills (for a Claude
delegate, `skills/claude-implement/SKILL.md` — `claude -p` headless); take
reviews via the `skills/<family>-adversarial-review/` skills. The reviewer
must never share the implementer's model family — including yours, when you
implemented.

**You are a DELEGATE** if another agent dispatched you with a task file.
Follow the task prompt. The standing rules apply even if the prompt forgot
to state them: work only inside the worktree you were given, commit locally
at most, **never push**, no AI-authorship trailers, no recursive delegation
(do not invoke other CLIs or review scripts), and report honestly — what you
ran, what passed, what you could not do.

**You are a REVIEWER** if you were handed a diff/commit range and a claims
list. Falsify, don't confirm. Every verdict needs quoted evidence
(`file:line` + the code that decides it); `NOT REACHED` is acceptable,
HOLDS-without-a-quote is not. Read-only: no edits, no fixes, no test runs
unless your harness machine-permits them — name the command you would run
and the result that would confirm the finding, and let the lead run it.

## Standing rules (apply to every role, every run)

- **Push is human-only.** Report the commit hash and the exact push command;
  never run it.
- **The merge gate is human.** Present the diff and verified findings; merge
  only on explicit approval.
- **Cross-family review is mandatory** before anything merges. Two families
  for HIGH-risk changes.
- **Nothing is trusted on self-report** — the lead re-runs tests and
  verifies every finding. Expect it; report accordingly.
- Keep a run log as events happen. If your run fails or returns nothing,
  leave the worktree intact — partial state is diagnosis evidence.

## Substituting your own process management

The skills describe launches in terms of "the host's background mechanism"
(Claude Code's task tracking, in the original environment). If your harness
has no equivalent, use plain shell job control with output redirected to
files, and poll process liveness — the skills' patience calibrations (5–40
minutes of silence being normal, per family) apply unchanged. The codex
family additionally has a raw-CLI fallback documented in its runtime file
for machines without the Claude Code companion plugin.

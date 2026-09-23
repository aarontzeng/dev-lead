---
name: config
description: View and change the dev-lead leg roster — which model and effort each adapter runs, per role and per round. Use when the user wants to see or edit that roster.
---

# Config: the dev-lead leg roster

The roster says which model and effort each CLI adapter runs, per role
(implement / review) and per round (`r1`, `fix`). It is a declaration the
lead reads. [`scripts/leg-cmd.sh`](../../scripts/leg-cmd.sh) still requires
an explicit `--model` — there is no safe default, because the model is the
cross-family accounting decision. Nothing in the roster is a launcher default.

## Where it lives

The first source that is SET wins — an explicit path that does not exist is
reported, never swapped for another file:

1. `DEV_LEAD_ROSTER`
2. `$CLAUDE_PLUGIN_DATA/roster.json` — Claude Code's per-plugin directory, used
   only when it is dev-lead's own (`dev-lead-<marketplace>`): a lead's shell
   can inherit ANOTHER plugin's value (measured: codex's). A codex or agy lead
   will not have the variable at all.
3. `~/.claude/plugins/data/dev-lead-dev-lead/roster.json`

A missing file is normal on a new machine. The shape is in
[`templates/roster.example.json`](../../templates/roster.example.json).
Keys that start with `_` are comments and are kept across writes.

## Procedure

Resolve the suite root the same way the other skills do. The helper has to
come from that tree: cwd is the target repo, and a bare scripts path would
miss.

```bash
DEV_LEAD=${DEV_LEAD_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/* 2>/dev/null | sort -V | tail -1)}
[ -x "$DEV_LEAD/scripts/roster.py" ] || { echo "dev-lead root unresolved — set DEV_LEAD_ROOT to your checkout"; exit 1; }
"$DEV_LEAD/scripts/roster.py" show
```

1. **Show.** Run `"$DEV_LEAD/scripts/roster.py" show`. A fix round that
   inherits prints each of r1's legs marked inherited. If the file is
   missing, the command names the path and how to create one with `set`;
   that is not an error.

2. **Write one leg per call.** If the user named assignments, apply each
   with `set` and a `--why` that quotes the user. Do not invent a reason
   they did not give.

   `"$DEV_LEAD/scripts/roster.py" set <round> <role> <adapter> --model M [--effort E] [--family F] [--lens L] --why "<the user's words>"`

   An opencode review leg routed by lens is the `by_lens` entry: pass
   `--lens`. `set fix --inherit r1 --why "<the user's words>"` makes the
   fix round match r1.

   If the user did not name assignments, ask per slot. Use the host's
   multiple-choice question tool when it has one. Offer the current value,
   that adapter's `effort.examples` spellings from
   [`data/launch.json`](../../data/launch.json), and free text. Ask whether
   the fix round inherits r1 or differs.

   **A config_only leg (codex review): offer to align the machine.** Its
   effort is read from a file on THIS machine, so after setting it run
   `"$DEV_LEAD/scripts/roster.py" config-effort <round> review codex` (no
   `--yes`): it writes nothing, and prints the declared value, the value in
   force, and either "already agree" or what would change (or a refusal
   when the file's value cannot be read with confidence — then it is the
   user's to edit by hand). If they differ, ask with the multiple-choice tool —
   "update `~/.codex/config.toml` from X to Y? It is shared with your
   interactive codex and other sessions." Only on an explicit yes run it
   again with `--yes`, then report exactly what it printed: old -> new and
   the backup path — and if it exits non-zero after a `changed:` line, say
   the read-back disagreed and name the backup to restore from. On a no, say
   plainly which value the leg will actually run at. This step, on that
   explicit yes, is the only time dev-lead writes that file; no leg
   dispatch, review round or other skill ever does.

3. **Probe a new model once, with a real call.** A model that has never
   been dispatched on this machine gets one cheap real call before it is
   written. A row in a CLI's model listing is not evidence the model can
   be called.

4. **The merge gate.** `roster.py show` prints it first. If the user named
   it, apply `"$DEV_LEAD/scripts/roster.py" gate <user|lead> --why "<the
   user's words>"`. Otherwise ask once, in their words: does dev-lead land a
   fully green verdict itself (`lead`), or does the person approve the
   verdict and diff first (`user`, the default)? Say what it does NOT change:
   anything not fully green — an unanswered review round, a failing test, an
   open blocker, an unverified finding — still goes to the person either way, and a stricter repo
   contract still wins.

5. **Check.** `"$DEV_LEAD/scripts/roster.py" check`. A family that cannot
   be the accounting leg is a warning: it prints and does not fail. Any
   error does. `set` refuses to write a roster that fails, so a failure
   here means the file was changed outside this skill.

6. **Report.** For each slot this session changed, say old → new: model,
   effort, family, whether the fix round inherits, or the merge gate.

`unset <round> <role> <adapter>` stores `null` for that leg. An unset leg
is not a default model. The next dispatch has to name one.

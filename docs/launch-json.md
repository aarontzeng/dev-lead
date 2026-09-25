# `data/launch.json`: the fields

`data/launch.json` is the one place a launch command comes from:
[`scripts/leg-cmd.sh`](../scripts/leg-cmd.sh) renders it, `scripts/roster.py`
reads the effort rules from it, and `scripts/lint.py` holds the skills' prose
to it. The file's own `_comment` says why it exists; this page says what each
key means, because until 0.6.50 the only way to learn that was to read
`leg-cmd.sh`.

The file is a JSON object whose keys are the adapters (`claude`, `codex`,
`agy`, `opencode`, `grok`, `cursor` -- the same set as `data/families.json`
and the `skills/` tree, which lint checks three ways). Keys starting with `_`
are comments.

## Per adapter

| key | meaning |
|---|---|
| `cli` | The executable `leg-cmd.sh` puts first on the command line. A role may override it with its own `cli` (codex review runs `codex`, codex implement runs the companion through `node`). |
| `verified` | Free text: when and how the adapter's rows were probed. Printed as a `#` comment above every rendered command, so a lead sees "transcribed, not probed" before trusting it. |
| `effort` | How this adapter takes a reasoning-effort setting. See the table below. |
| `role` | One object per role, `review` and `implement`. See below. |
| `gotchas` | Strings printed as `# gotcha` lines on stderr with every render. Adapter-wide: a gotcha has no role field, so some are review facts printed under an implement launch, and the render says so. |
| `not_flags` | Flags this CLI does not have (e.g. cursor `--effort`). `leg-cmd.sh` names them on stderr when the effort applies, so a lead does not invent one. |
| `add_dir_flag` | The flag that grants the leg an extra readable directory (`cursor --add-dir`). `--add-dir` on `leg-cmd.sh` is refused for an adapter without it, never dropped. |
| `add_dir_verified` / `add_dir_note` | When the flag was probed, or (for an adapter without one) what to do instead. The note is the refusal's message. |
| `model_prefix` / `model_prefix_note` | The SUGGESTED provider segment for a bare model id (`opencode/`). `leg-cmd.sh` requires SOME provider segment and offers this one; the note records why it is not "the required prefix". |

## `effort`

| key | meaning |
|---|---|
| `mechanism` | `model_suffix` -- the effort is part of the model string (`gemini-3.8-flash-high`), and `--effort` is refused; `flag` -- a dedicated flag; `config_only` -- no per-call control (no shipped adapter since 0.6.28; `roster.py config-effort` still serves one); `none` -- the suite passes no effort for the role, its CLI default applies. |
| `flag` | The flag a `flag` mechanism uses (`--effort`, `--variant`). |
| `flag_by_role` | A role whose flag is spelled differently (codex review: `model_reasoning_effort`, passed as `-c model_reasoning_effort=<e>`). |
| `applies_to_role` | Scopes the mechanism to ONE role. The adapter's other roles follow their own `argv`: an `{EFFORT}` placeholder there means a flag, none means the role takes no effort (claude: review takes `--effort`, implement does not). |
| `examples` | Effort spellings known to work on this adapter. `roster.py` uses them to spell a tier a triage floor asks for, and refuses to invent one that is not here or in the roster. |
| `note` / `implement_note` | Free text printed with the render; `implement_note` is shown for the role outside `applies_to_role`. |
| `migration_hint` | Per role, what a roster written before this role took an effort should set. `roster.py check` prints it when the leg has none. |
| `config_key` / `config_file` | For `config_only`: the key and file the effort is read from on THIS machine. |

## `role.<review|implement>`

| key | meaning |
|---|---|
| `argv` | The command line after `cli`, as tokens. Placeholders are substituted; a token starting with `$RUN_DIR/` is emitted double-quoted so the shell expands it; everything the caller supplied is single-quoted (the output is meant for `eval`). |
| `prompt_delivery` | How the brief reaches the leg: `argv` -- a `{PROMPT}` token becomes `"$(cat "$RUN_DIR/<brief>")"`; `stdin` -- `< "$RUN_DIR/<brief>"` is appended; `prompt_file` -- a `{PROMPT_FILE}` token takes `--prompt-file`'s value. The brief is `prompt.md` for review (the lens) and `task.md` for implement, the names the skills write. |
| `prompt_frame` | A builder script under `scripts/` (`review-prompt.py`) that wraps the lens in this adapter's adversarial framing first. The render becomes `builder < brief > framed-prompt.md && <leg reads the framed prompt>`; `--base` and `--target` are required. |
| `prompt_evidence` | `true` when the leg cannot run git (agy in plan mode): the builder also materializes the diff and base-side files under `$RUN_DIR/evidence`, which the argv grants with a second `--add-dir`. |
| `cli` | Overrides the adapter's `cli` for this role. |
| `verified` | When and how this role's row was probed. Believe review rows; re-check an implement row against its skill before trusting it (the file's `_comment` records why). |

### Placeholders in `argv`

| token | filled from |
|---|---|
| `{MODEL}` | `--model` (required) |
| `{EFFORT}` | `--effort`; its presence is what makes a role a `flag` role outside `applies_to_role` |
| `{TARGET}` | `--target`: the frozen review directory, or the worktree |
| `{BASE}` | `--base`: the merge-base the review span starts at |
| `{PROMPT}` | the brief, by `prompt_delivery` |
| `{PROMPT_FILE}` | `--prompt-file` (grok) |

A placeholder whose option was not given is refused with the FLAG's name
(`missing required value(s): target (pass --target)`), never left in the
command; an option the template has no slot for is refused too. Every
refusal is measured -- the file's `_comment` and `leg-cmd.sh`'s header keep
the dates.

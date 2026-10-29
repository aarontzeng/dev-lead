# Quota sources: where each number comes from

Read this when you need to know what a record printed by
[`scripts/quota-snapshot.py`](../../../scripts/quota-snapshot.py) means, where a
figure in it comes from, how far to trust it, or before adding a source. The
collector reads five sources, declared in
[`data/quota-sources.json`](../../../data/quota-sources.json) (replaced, in this
order, by `--config`, `DEV_LEAD_QUOTA_SOURCES`, or a `quota-sources.json` in
dev-lead's plugin data directory), and prints one JSON record per quota pool. This file is where the record shape and its closed
vocabularies are written down (the tests pin the same lists), along with what
the code cannot say about itself: who writes each source, what was checked,
and what is still a guess.

**Nothing consumes the output yet.** The collector changes no dispatch
behaviour. It is read-only except for one opt-in action: `--refresh` on a host
that asks for it starts `agy`, `cursor-agent --trust` or `opencode --standalone` in a tmux server of
its own so that a cache that is missing, stale, or without a usable time is
rewritten (*Refreshing a TUI cache* below).

## The output

One object on stdout: `{"schema_version": 1, "generated_at": "<ISO UTC>",
"records": [...]}`. Exit 0 whenever it was printed, even if every source
failed (the failures are inside the records); exit 2 for a usage or config
error, with one line on stderr and nothing on stdout.

Every record has exactly these keys, in this order:

| Key | Value |
|---|---|
| `source` | the source's `name` in the config |
| `kind` | the source's kind: `codex_app_server`, `claude_usage_cache`, `agy_quota_cache`, `cursor_usage_cache` or `opencode_go_cache` |
| `adapter` | the config's `adapter` (the shipped config uses `codex`, `claude`, `agy`, `cursor`, `opencode`) |
| `billing_provider` | the config's `billing_provider` |
| `pool_id` | the quota pool: `codex:<limitId>`, `claude:subscription`, `agy:gemini`, `agy:3p`, `cursor:unified` or `opencode-go`; for an internal error, the source name |
| `family` | `GPT`, `Claude`, `Gemini`, or `null` when the pool serves more than one family or which one is not known |
| `scope_status` | `known` or `unknown`: whether it is known which models the pool covers |
| `support_level` | `official` (the vendor's own CLI, asked live), `private_cache` (a file another program writes) or `tui_only` (written only while an interactive TUI runs) |
| `read_status` | `ok`, `missing`, `refused`, `auth_error`, `network_error`, `parse_error`, `unsupported` or `error`; see [Read status](#read-status) |
| `freshness` | `fresh` (the source time is within `ttl_seconds`), `stale` (older) or `unknown` (no usable source time, or one more than 60 s ahead of the clock) |
| `observed_at` | the time of this run, ISO UTC |
| `source_updated_at` | the source's own time, ISO UTC, or `null` |
| `source_time_basis` | where that time came from: `live` (the codex call itself), `writer` (a time the writer recorded), `mtime` (the file's mtime), or `null` when there is none |
| `windows` | the windows, possibly none |
| `problems` | one string per thing that is wrong; empty when nothing is |

Every window has exactly these keys, in this order:

| Key | Value |
|---|---|
| `kind` | `five_hour` (300 minutes), `weekly` (10080), `monthly` (no fixed duration) or `other` |
| `duration_minutes` | an integer, or `null` |
| `used_pct` | USED percent 0-100, or `null` |
| `remaining_pct` | `100 - used_pct`, or `null` |
| `resets_at` | ISO UTC, or `null` |
| `limit_state` | `available`, `exhausted` (used is 100) or `unknown` |

### Read status

What each kind can produce, and when:

- **Every cache kind** (all but codex), for every record of the source:
  `missing` when the file does not exist; `refused` when it is a symlink, not
  a regular file, owned by another user, larger than 1 MiB, or changed while
  it was being opened; `error` when it cannot be stat'ed or opened;
  `parse_error` when it is not JSON or not a JSON object.
- **`codex_app_server`:** `missing` when the codex binary is not found;
  `error` when it cannot be started or answers with a JSON-RPC error;
  `network_error` when it closes the stream, does not reply within
  `--timeout`, writes more than 8 MiB before its reply, or the pipe breaks;
  `parse_error` when the reply carries no result object, no usable snapshot,
  or a snapshot with neither a primary nor a secondary window (that record
  only).
- **`claude_usage_cache`:** `parse_error` when neither `five_hour` nor
  `seven_day` is present.
- **`agy_quota_cache`:** `parse_error` on both pools when none of the four keys
  `gemini-5h`, `gemini-weekly`, `3p-5h`, `3p-weekly` is present.
- **`cursor_usage_cache`:** `unsupported` when `schema` is not 2 or `unified`
  is false; `parse_error` when `unified` is not a boolean.
- **`opencode_go_cache`:** `unsupported` when `schema` is not 1; `parse_error`
  when the latest read succeeded but `usage` is missing; `auth_error` or
  `network_error` when the latest attempt failed (see that section).
- **Any kind:** a source whose collector fails unexpectedly gets one record
  with `read_status` `error`, `pool_id` the source name, `family` `null`,
  scope `unknown`, and only the exception's type name in `problems`. The other
  sources are still read.

## What every source shares

- **Read-only.** It never runs a model and never reads a credential. It
  starts `codex app-server` and ends that one itself; with `--refresh` on a
  host that opts in it may also start `agy`, `cursor-agent --trust` or `opencode --standalone` in a
  private tmux server (*Refreshing a TUI cache*).
- **No invented values.** A value that is missing, a boolean, a string, not
  finite or out of range is reported as `null` with `limit_state` `unknown`
  and a line in `problems`. It is never clamped, zeroed or filled from another
  pool. `remaining_pct` is `100 - used_pct` (or the fraction times 100) in plain
  arithmetic with no rounding, so a fractional source can print a long decimal.
- **A window that may be over is `unknown`.** If `resets_at` is already past,
  the number describes a window that has ended; it is not read as reset to
  zero. A reset that is present but not a valid time (no zone, an offset
  beyond 23:59, not a time at all), or a codex duration that is not a positive
  integer, is treated the same way: that window may have ended too. In each
  case the numbers are kept and the state is `unknown`, at 100 % as well. A
  reset that is absent or `null` is not a problem; the window is judged on its
  number.
- **Freshness is the source's own time**, never the time of the read: a cache
  file's mtime, the writer's `fetched_at`, or "now" for the live codex call. A
  time up to 60 s ahead of the clock counts as age 0; further ahead the clock
  is suspect and freshness is `unknown`.
- **Cache files are checked before they are opened.** A symlink, a file that
  is not regular, one owned by another user or one over 1 MiB is `refused`.
  The open does not follow a link and does not block, and the open descriptor
  is checked again: the same inode, still a regular file, still owned by the
  current user. It reads at most 1 MiB + 1 byte, the extra byte to detect a
  file that grew after the check, and refuses anything larger. Half-written
  JSON is `parse_error`.
- **Known limit: a symlinked parent directory is followed.** Only the final
  path component is refused as a link. That is accepted because what is
  reached must still be a regular file owned by the current user, so a link
  higher up can only lead to the user's own files.
- **Output is a whitelist.** Account ids, plan names, balances, credits,
  emails and raw error bodies in a source document are never copied.
- **Config values do appear in config errors.** A source name or kind is
  quoted in the exit-2 message on purpose: the config is the operator's own
  file, not a source document.

## `codex_app_server`

- **Who writes it:** nobody; each run asks the Codex app server (`codex
  app-server --listen stdio://`, line-delimited JSON-RPC) and ends it. Nothing
  is cached, so `source_updated_at` is the observation time and the source is
  `official`.
- **Units:** `usedPercent` is USED percent, `windowDurationMins` is minutes,
  `resetsAt` is Unix SECONDS. The duration decides the window: 300 is
  `five_hour`, 10080 is `weekly`, anything else is `other` with its own
  duration. Primary is not assumed to be the 5-hour window. A duration that is
  not a positive integer is `other` with `null` duration and state `unknown`.
- **Shape:** `rateLimitsByLimitId` maps each limit id to a snapshot and
  `rateLimits` repeats one of them. The map is used when it has entries, else
  `rateLimits` alone, never both; a `rateLimits` without a `limitId` is pool
  `codex:codex`. One record per limit id. A snapshot with neither a primary
  nor a secondary window is `parse_error`, so it never reads as "no limit".
- **Version sent:** `initialize` carries the plugin version when the manifest
  holds a short X.Y.Z, else `unknown`; an unreadable manifest never stops the
  run.
- **Verified 2026-10-19** with codex-cli 0.162.1: the exchange (initialize,
  then `initialized`, then `account/rateLimits/read`), the notifications the
  server interleaves, and the two result shapes above.
- **Not verified:** any other codex version; which window a non-standard
  duration is in practice (it is reported as `other`, not interpreted).
- **Does not:** read any codex credential file (the codex binary does that for
  itself), start a turn, or wait without a bound. `--timeout` bounds the
  exchange, a server that writes half a line and stalls included; reaping the
  process adds at most about 2 s (SIGTERM, a 2 s wait, then SIGKILL; on Windows up to about 9 s:
  taskkill 5, the reader thread's join 2, then the 2 s wait).

## `claude_usage_cache`

- **Who writes it:** the user's statusline script, which stores the raw
  response of Claude's subscription-usage endpoint
  (`/tmp/claude/statusline-usage-cache.json`). The refresh cadence is the
  statusline's, so freshness is the file's mtime against `ttl_seconds`.
- **Units:** `utilization` is USED percent 0-100, `resets_at` is an ISO string
  with an offset. `five_hour` is `five_hour` (300), `seven_day` is `weekly`
  (10080).
- **Per-model weekly keys** (`seven_day_<name>`, such as `seven_day_opus`) are
  not read. When any of them holds an object with a `utilization` field, the
  record gets the problem `per-model weekly limits are present and not read`,
  because a tighter per-model limit may then apply; the states are left alone.
  A key that is `null`, or an object without `utilization`, adds nothing. On
  2026-10-19 the only non-null `seven_day_*` key in the live cache was
  `seven_day_breakdown` (`as_of`, `rows`, `window_started_at`): a usage
  report, not a limit, which is why the rule asks for `utilization`.
- **Not verified:** the figures against the account page. The endpoint behind
  the cache is an undocumented OAuth interface that can change without notice;
  the collector never calls it.

## `agy_quota_cache`

- **Who writes it:** the status-line script the interactive Antigravity TUI runs
  (aadev's `statusline.sh` writes `/tmp/gemini/statusline-quota-cache.json` from the `.quota`
  payload on its stdin; `statusline.ps1` writes `${TEMP}/gemini/statusline-quota-cache.json`
  on Windows), so `support_level` is `tui_only`. The script runs when the TUI renders a
  status line that carries a quota payload: when someone uses it, or when `/usage` is run in
  it. An idle TUI does not rewrite it (measured 2026-10-19 on two hosts: a TUI
  open since 08:20 left the file untouched from 10:34 for more than 26
  minutes, and `/usage` rewrote it at once). With no TUI the file is absent or
  old, and the records are `missing` or `stale`. The collector does not
  refresh it unless asked to (below).
- **No other source on a consumer login:** headless `agy -p` refreshes its
  quota internally but prints none (its stream-json carries token counts
  only, and agy 1.4.2 has no quota subcommand), and the backend's quota calls
  answer a consumer login with 403 (`retrieveUserQuotaSummary`: no licence;
  `fetchAvailableModels`: no permission; on the production and the daily host
  alike, 2026-10-19). A leg's own outcome (429, an empty answer, a timeout) is
  the remaining signal.
- **Refreshing it (opt-in, twice):** see *Refreshing a TUI cache* below.
- **Units:** `remaining_fraction` is the REMAINING fraction 0-1, `reset_time`
  is an ISO string. `reset_in_seconds` is ignored because it ages from the
  moment it was written.
- **Two pools:** `agy:gemini` (family Gemini, scope known) and `agy:3p`
  (third-party models of more than one family, so no family and scope
  unknown). A missing key is that window `unknown` with a problem; it is never
  read from the other pool. The statusline renderer does fall back across
  pools, which would report one pool's headroom as the other's.
- **Not verified:** which models the `3p` pool covers.

## Refreshing a TUI cache

The agy and Cursor caches are written only by their CLIs' interactive status
lines, and an idle TUI never rewrites them. A TUI that starts does (measured
2026-10-19): agy writes its quota cache within about 4 s, already at its
folder-trust prompt, and `cursor-agent --trust` re-fetches a cache 300 s or
older within about 6 s. `opencode --standalone` (a TUI with its own private server; opencode 2, with the go-usage plugin from `opencode-plugin/`, installed by
[`install-opencode-go-usage.sh`](../../../scripts/install-opencode-go-usage.sh)) loads the
plugin, whose first poll comes 5 s later and rewrites `usage.json`. Without `--standalone` the TUI attaches to
opencode's background service, whose plugin is already loaded and polls only every 10 minutes, so nothing
would be rewritten (measured on 2.1.4); a bare `opencode serve` does not load the plugin at all (measured by a peer). This script runs no model and
sends no key; the plugin reads the Go key itself, this script never does. The TUI also starts the user's
configured MCP servers while it is up, and the private server stops with it (it is not detached; a child that outlives `kill-server` by its
own 1-3 s force-kill window is not waited for, and the stray-server check names only tmux sockets -- a known limit, not seen in practice).

The opencode TUI connects to its own child server over `127.0.0.1`. A host that routes loopback elsewhere
cannot run it: measured on a WSL host in `virtioproxy` networking mode (policy routing sends TCP to 127.0.0.1 out to
Windows), the TUI died within a second with `ECONNREFUSED`. The refresh then reports
`opencode exited before it wrote its cache (its tmux server was already gone)`; run
`opencode --standalone` once in a terminal there to see the error, and leave `"refresh"` off for that source.

So `--refresh`, for a source whose config sets `"refresh": "tmux_spawn"` (only
`agy_quota_cache`, `cursor_usage_cache` and `opencode_go_cache` may) and whose cache is missing,
older than its TTL, more than 60 s in the future, or has an mtime that cannot
be read as a time, starts `agy`, `cursor-agent --trust` or `opencode --standalone` detached in a tmux server of its own (`tmux -L
dev-lead-quota-<pid>-<cli> -f /dev/null`: no tmux config is read, since the
person's `~/.tmux.conf` could run commands, and `TMUX` / `TMUX_PANE` are
dropped from tmux's environment), in a fixed directory of dev-lead's
(`~/.cache/dev-lead/quota-tui`), waits up to `--refresh-wait` seconds (default
15, at most 60) for the cache to be rewritten AND parse as a JSON object (the agy status
line truncates the file before it writes it), and kills that server on every
path; success is judged after the kill. The refresh runs before the collection
clock is read, so a rewritten cache is not "in the future" of its record. It
never types a key, and never touches the person's own tmux server or a TUI
they have open.

- **The working directory is a real, empty directory the user owns.**
  `cursor-agent --trust` trusts it, so a link (which would trust its target)
  is refused, anything found in it is set aside first (see below), and its
  mode is set to 0700 before the check. agy writes nothing there, but on macOS cursor-agent runs the user's
  Claude Code session hooks in its workspace, and a memory plugin's
  SessionStart hook made `.remember/` there (measured 2026-10-19); left in
  place it would stop the CLI from starting in an empty directory. So once the private server is
  confirmed stopped (a `kill-server` that succeeded, or a failed one followed by a `list-sessions` whose stderr is
  tmux's own `no server running` / `error connecting ... (No such file or directory)`; a timed-out one proves
  nothing) the directory is MOVED aside (`quota-tui.gone.<pid>.<random>`, one rename of
  the final path component, which moves a link rather than following it), a
  fresh empty one takes its place, and the moved one is emptied through a
  descriptor opened without following a link and checked to be the directory
  that was checked before the start (same device and inode, the user's): if it
  is not (it was swapped for a link or another directory), it is put back
  untouched (and if even that cannot be done, renamed
  `quota-tui.foreign.<pid>.<random>`, which the sweep below does not touch;
  if that rename fails as well, see the known limits). When the server could not
  be stopped, the working directory is not moved or emptied and the socket is
  kept. Separately, at the start of every run, leftovers an earlier run could not
  delete (`quota-tui.gone.*`) are swept. The reset can still be too early: the
  cursor-agent worker outlives the server, and a hook can write into the
  workspace by name after the reset (measured 2026-10-19 on a second host:
  `.remember/` 0.3 s after the cache was written). So a working directory that is
  not empty at the start is not a refusal any more: it is set aside the same way
  (moved, a fresh empty one made, the old one emptied if it is still the directory
  that was checked), and the CLI starts in the fresh one. Only when that does not
  leave an empty directory is the run refused (`its working directory is not
  empty`), and nothing is started. Not while a private server of an earlier run may
  still be running: a run that could not stop its server keeps its socket
  (`dev-lead-quota-<pid>-<cli>` in tmux's socket directory) and so does a collector
  that died during the wait, and the CLI may still be writing into that directory;
  then nothing is moved or started, and the problem line names the socket
  (`its working directory is not empty and a private tmux server of an earlier run
  may still be running (dev-lead-quota-<pid>-<cli>)`) so the operator can stop it
  (`tmux -L <name> kill-server`) and remove the socket file. Each such socket is asked first
  (`tmux -L <name> list-sessions`): when tmux itself says no server is behind it (a collector that was
  killed leaves a dead socket; 0.6.94, before which one blocked every later run for good), the file is
  unlinked and does not count; a timeout, a refused connection or tmux missing rules nothing out. A socket directory
  that exists but cannot be read refuses the same way (`... and tmux's socket
  directory cannot be read (EACCES)`); only one that is not there, or that can be
  read and holds no entry of that naming, allows the clean-up. The scan reads the directory this process would use (`TMUX_TMPDIR`, else
  `/tmp`): a run under a different `TMUX_TMPDIR` than the one that left the socket
  does not see it, and an empty working directory is not checked at all (a run that
  could not stop its server and left nothing behind does not stop the next one from
  starting a second CLI, as before 0.6.73).
- **One refresh at a time.** An exclusive `flock` on
  `~/.cache/dev-lead/quota-tui.lock` is held for the whole run (that directory
  is set to 0700, and a lock file replaced after it was opened is refused); a
  second refresh, in another process or another source, is refused with `<cli>
  refresh skipped: another refresh is running` and starts nothing.

- **Two opt-ins.** The caller passes `--refresh`, AND the host's config marks
  the source. The shipped `data/` file never does; a host opts in with its own
  `quota-sources.json` in dev-lead's plugin data directory (beside
  `roster.json`), which then replaces the shipped file.
- **It needs the cache writer installed** for that CLI: the agy and Cursor
  caches are written by the user's statusline scripts, the opencode-go one by
  the go-usage plugin shipped in `opencode-plugin/go-usage` (opencode 2), not by the CLIs themselves. Without
  one, the started CLI rewrites nothing and the record says so.
- **What the record says.** A refresh that worked adds nothing; the record is
  as fresh as the rewritten cache. (opencode-go: the plugin also rewrites the
  file after a FAILED poll, so a refresh can "work" while `fetched_at` stays
  old; the record then carries the plugin's own `latest read attempt failed`
  line, and because the file's mtime is new, another `--refresh` inside the
  TTL starts nothing. Known limit.) Otherwise every record of that source gets
  one problem: `<cli> refresh skipped: tmux is not available`, `<cli> refresh
  failed: tmux could not start <command>`, `<cli> refresh failed: <command>
  did not rewrite its cache within N s`, or one about the working directory
  (`cannot create`, `cannot list`, `not a plain directory of the user's`, `not
  empty`), the lock (`another refresh is running`, `cannot create its lock`,
  `its lock file was replaced`, `~/.cache/dev-lead is not a plain directory
  of the user's`), or `<cli> refresh failed: its private tmux server could not
  be stopped`, and the records stay as stale as the cache was. Two exceptions:
  a server that would not stop may still have let the cache be rewritten, so
  that record can be fresh and still carry the problem (which keeps it from
  reading as headroom); and
  when the wait ends while agy is still writing (it truncates the file first),
  the kill leaves a truncated cache, and the records are `parse_error` until
  the next rewrite.
- **Known limits.** Starting the CLI is a real start: it may check for an
  update and writes its own logs and state in its own config directories (not
  in the working directory). tmux 3.2a leaves the private socket file behind
  after `kill-server`; the collector removes it once the kill worked. A
  collector killed during the wait skips its cleanup and leaves its private
  server running; the socket is named by pid, so a later run with that pid
  kills it if it gets as far as starting tmux (it does not with a crowded working
  directory), and otherwise it is stopped by hand (`tmux -L <name> kill-server`).
  A socket file left after a reboot (a persistent `TMUX_TMPDIR`) or by a killed collector is
  probed (0.6.94) and unlinked when tmux says no server is behind it (a proven-dead entry that
  cannot be unlinked is harmless and does not refuse); one tmux cannot answer for (a timeout,
  Permission denied, an answer of more than one line) refuses the same way and is dealt with by
  hand. The scan stops at the first such socket. Measured (tmux 3.2a, 2026-10-29): a socket file that was bound and then
  abandoned answers `list-sessions` with the single line `no server running on <path>`, in any `LC_MESSAGES`. A `TMUX_TMPDIR` whose path itself holds a line break makes even that answer more than one line, so
  such a host keeps the old behaviour (refused, by hand): an answer is never read across lines. A residue tree deeper than Python's recursion limit cannot be
  deleted by `shutil.rmtree`: it stays under its `quota-tui.gone.<pid>.<random>`
  name, every later run tries again and fails the same way, and it blocks
  nothing (delete it by hand). A rejected directory (one swapped in during the
  run) whose two renames both fail (back to `quota-tui`, then to
  `quota-tui.foreign.*`) stays under the `quota-tui.gone.*` name, and the next
  run's sweep empties it; it takes a same-user process replacing the directory
  during the run (not defended against, see below) and two failing renames in
  one directory. The lock and the checks guard against another
  collector and against a directory or lock file replaced by something that
  runs as another user; a process that runs as the same user and replaces the
  lock file after it was checked, or the cache itself, is not defended
  against (it could do anything the collector can). The cwd is changed to
  the emptied directory for the duration of the cleanup and restored after
  it (to `~` if it cannot be). Two collectors at once, or a person
  starting the same CLI during the wait, may each take the other's rewrite as
  its own; the cache was still rewritten.
- **Considered and not used:** typing `/usage` into a TUI the person keeps
  open in tmux. It works (the dialog re-fetches the quota), but it types into
  someone's interactive session, and no check of their pane can rule out a
  key arriving between the check and the send.

## `cursor_usage_cache`

- **Who writes it:** the user's statusline script for Cursor
  (`/tmp/cursor/statusline-usage-cache.json`). Only `schema` 2 is read; any
  other is `unsupported`.
- **Units:** with `unified` true, `weekly_pct` is USED percent 0-100 and
  `weekly_reset` is an ISO string; one `weekly` window. With `unified` false
  (a legacy billing-cycle plan) there is no weekly window: `unsupported`, no
  windows. `plan`, `prepaid_usd`, `auto_pct` and `api_pct` are ignored.
- **Verified 2026-10-19:** `weekly_pct` against the account page (page about
  5 %, cache 5.4).
- **Not verified:** `weekly_reset` against the page; which models the figure
  covers (family `null`, scope `unknown`).

## `opencode_go_cache`

- **Who writes it:** the go-usage OpenCode plugin shipped in this suite
  (`opencode-plugin/go-usage`, installed by [`install-opencode-go-usage.sh`](../../../scripts/install-opencode-go-usage.sh);
  opencode 2 only), which is the only code here that reads the Go key;
  it writes `${XDG_CACHE_HOME}/opencode-go/usage.json` (schema 1). `XDG_CACHE_HOME` is
  used only when set, non-empty and absolute, else `~/.cache`.
- **Two clocks in one file:** `fetched_at` and `usage` are the last SUCCESSFUL
  read and survive later failures; `attempted_at`, `error` and `http_status`
  describe the latest try. Freshness follows `fetched_at` (basis `writer`), so
  windows kept across failed refreshes go stale on their own.
- **Units:** `percent` is USED percent, `resetsAt` is an ISO string. `rolling`
  is `five_hour` (300), `weekly` is `weekly` (10080), `monthly` is `monthly`
  with no duration. A `status` other than `ok`, or none, makes that window
  `unknown`, unless it is at 100 %, which is `exhausted`.
- **Read status:** `ok` when `error` is null. Otherwise `auth_error` for HTTP
  401 or 403 or the error `no opencode-go key`, else `network_error`; the
  windows from the last success are still reported. The error text is never
  copied.
- **Writer change, 2026-10-19:** the plugin (then in the
  author's aadev repo, now `opencode-plugin/go-usage`) no longer writes `no opencode-go key`:
  a process without a key leaves the cache alone. On success it records the
  response's real HTTP status. The collector still maps that string, for
  caches an older writer left behind.
- **Not verified:** which models a window covers (the endpoint does not say,
  so family `null`, scope `unknown`); what statuses besides `ok` the endpoint
  can return; how often the plugin refreshes.

## Native Windows

`quota-snapshot.py` runs under native Windows Python (`py -3`); nothing else in dev-lead does
(the review scripts are bash). On `win32`:

- **Sources.** [`data/quota-sources.windows.json`](../../../data/quota-sources.windows.json) is the shipped file: all five sources,
  with these Windows cache paths (proven on a real host, 2026-10-28): `claude`
  `${TEMP}/claude/statusline-usage-cache.json`, `agy` `${TEMP}/gemini/statusline-quota-cache.json`,
  `cursor` `${TEMP}/cursor/statusline-usage-cache.json` (the statusline scripts in aadev
  write them, the same JSON shapes as on Linux) and `opencode-go` `${XDG_CACHE_HOME}/opencode-go/usage.json`
  (the go-usage plugin; `~/.cache` when XDG_CACHE_HOME is unset). A source whose writer is not
  installed reads `missing`. `${TEMP}`, `${LOCALAPPDATA}` and `${XDG_CACHE_HOME}` expand in a path (the variable
  when a fully qualified path, else `~\AppData\Local[\Temp]`, or `~/.cache` for the last).
- **A BOM is tolerated** in a cache file on Windows (PowerShell's `[Text.Encoding]::UTF8` writes one
  and the file would read `parse_error`); the writers should still use `UTF8Encoding($false)`.
- **Refresh is `spawn_hidden`, not `tmux_spawn`.** A source marked `"refresh": "tmux_spawn"` yields
  `<cli> refresh is not supported on Windows` and starts nothing. A host opts in (twice, like tmux:
  `--refresh` and the mark, in its own `quota-sources.json`; the shipped file never marks) with
  `"refresh": "spawn_hidden"` on an agy, cursor or opencode-go source. A stale cache then starts the
  CLI with no window and a console of its own, waits for the cache to be rewritten whole, and ends
  that one process (`TerminateProcess` on its pid, no tree, so opencode's background service is left
  alone). Measured on Windows 11 (2026-10-28, a peer): agy 9 s, `opencode --standalone` about 10 s,
  cursor-agent 27 s. Details that matter: only a real executable is started (a `.cmd` / `.ps1` shim is
  never started: ending it would end the shell, not the CLI; opencode behind an npm shim is its
  `node_modules\@opencode\cli\bin\opencode.exe` beside the shim, measured on opencode 2.x, and a
  shim with no such file beside it is a named skip); cursor-agent is `node.exe index.js --trust`
  from the newest `%LOCALAPPDATA%\cursor-agent\versions\<date>-<hash>` directory that has both files,
  and its standard handles are NOT redirected (redirected, it falls into print mode and exits); the
  wait is at least 45 s for cursor whatever `--refresh-wait` says; the working directory is
  `~/.cache/dev-lead/quota-tui`, emptied before and after (a link or junction in it is removed, never
  followed; a link AS the directory is refused); one refresh at a time by an exclusive byte-range lock
  (`msvcrt.locking`, released by the system when the process dies). No key is typed, no model is
  called. Known limits: a CLI's own detached helpers (cursor's statusline background worker) are not
  reached by ending the CLI, nor a child it left behind; a console host process may linger briefly; the
  directory checks are by path (a same-user swap to a junction between check and use is followed, and
  the working directory is also the CLI's cwd and trust root); a CLI that could not be stopped leaves a
  `quota-tui.stuck` marker that blocks refreshes for 10 minutes, and if the marker cannot be written or
  is a link planted meanwhile the guard fails open; nothing here has run on
  Windows in this repo's own tests, only the simulation `test_quota_snapshot_windows_refresh`.
  Why cursor takes 20-32 s on Windows (a peer's trace, 2026-10-28): cursor-agent runs, synchronously,
  a `cscript.exe` JScript that queries `Win32_Process` through WMI at +1.8 s (and `worker-server` once
  more at ~21 s). A healthy host answers in well under a second; on a host whose WMI is stuck (the
  WmiPrvSE processes at 0 CPU, `Get-CimInstance` hanging) it took 15 s each time, which shows up as a
  late first cache write with near-zero CPU. Not the plugin fetch (the `github.com:22` stall is a
  separate cause, fixed by an `insteadOf` https rewrite in the XDG git config). The 45 s minimum
  covers it; a longer stall is a host problem (reboot), not something this script can fix.
  A source whose refresh ran and wrote no cache (or exited first) is not started again for 10 minutes
  (`quota-tui.<cli>.backoff` under `~/.cache/dev-lead`; a refresh that works clears it, and the problem
  line says how long is left): on a network where a CLI cannot get through (cursor-agent behind a proxy
  it cannot use) it would otherwise spend its whole wait on every poll and hold the lock, so agy and
  opencode-go never got it. On Windows opencode 2.2.0 the background service also drops the go-usage
  plugin about an hour after its last TUI attach (the project instance is disposed, measured by a peer),
  so opencode-go stays fresh only through this refresh there; not seen on Linux.
  Without the mark, a cache past its TTL reads `stale`.
- **Codex.** The pipe is read by a thread (a selector cannot wait on a Windows pipe) and the
  binary is looked up through `PATHEXT`. Ending a `codex.cmd` shim with `terminate()` may leave
  the node process under it running.
- **Trust.** There is no uid on NTFS, so the "owned by the current user" refusal does not
  apply; a symlink, a non-regular file and a file over 1 MiB are still refused.
- The POSIX path takes none of this: the same checks and the same selector as before.

## For a reader of these records

The next phase reads these records to inform dispatch. What it must hold to:

- **Join by adapter and pool, never by `family`.** A dispatch leg matches a
  record by its adapter and the pool the leg's model draws from. For agy that
  is `agy:gemini` for Gemini models and `agy:3p` for the third-party pool,
  but which models `3p` covers is not verified, so a reader treats a leg
  routed to `agy:3p` as unknown headroom even when its record looks clean. `family` is `null` on pools that serve
  several families, so it cannot be the key.
- **Headroom needs all of:** the record is `read_status` `ok` AND `freshness`
  `fresh` AND its `problems` list is empty AND every window it lists is
  `available` (the tightest window decides; one `unknown` or `exhausted`
  window is enough to say no). Any other combination is not headroom. A
  problem such as `per-model weekly limits are present and not read` is
  there precisely to stop a record from reading as headroom.
- **Only the listed windows are known.** A codex record with one window says
  nothing about the other; the missing one is named in `problems`
  (`secondary: window not reported, only the other is known`), so such a
  record is never headroom.
- **opencode-go `auth_error` and `network_error` keep the last good windows
  on purpose** (when the cache still holds them): the windows and their
  freshness are those of the last successful read; the read status is what
  says the latest try failed. Every other failed record (codex, the cache
  read errors) has no windows.
- **A record whose `pool_id` is the source name** (`claude`, `agy`, `cursor`,
  `codex`) is that collector's own crash, and stands for every pool the source
  has: a join on `claude:subscription` or `agy:gemini` must look for it too,
  or it reads a crash as "no data". (`opencode-go` is also its success id;
  codex startup and protocol failures use `codex:codex`.)
- **No record is not `missing`.** A pool with no record at all (grok, cursor
  per-model, the opencode free models) means no data was collected for it;
  `missing` means a configured source was looked for and not found.
- **For agy, `fresh` only says the TUI rewrote its cache recently,** not that
  the numbers are current: the measurement below saw a rewrite carry a value
  that lagged real use. Treat agy headroom as advisory; the leg's own outcome
  decides.
- **An mtime can go stale while the values have not changed, and a fresh
  mtime can carry old values.** Caches judged by mtime are only as fresh as
  their writer's last rewrite. The agy writer is a TUI that rewrites only when
  it is used (see `agy_quota_cache`); on 2026-10-19 a rewrite at 09:45 still
  carried the 09:30 value although an agy leg had spent Gemini quota at
  09:34-09:40.

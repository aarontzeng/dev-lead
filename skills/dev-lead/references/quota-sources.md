# Quota sources: where each number comes from

Read this when you need to know what a record printed by
[`scripts/quota-snapshot.py`](../../../scripts/quota-snapshot.py) means, where a
figure in it comes from, how far to trust it, or before adding a source. The
collector reads five sources, declared in
[`data/quota-sources.json`](../../../data/quota-sources.json), and prints one
JSON record per quota pool. This file is where the record shape and its closed
vocabularies are written down (the tests pin the same lists), along with what
the code cannot say about itself: who writes each source, what was checked,
and what is still a guess.

**Nothing consumes the output yet.** The collector changes no dispatch
behaviour.

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

- **Read-only.** It never runs a model and never reads a credential. The only
  process it starts is `codex app-server`, and it ends that one itself.
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
  process adds at most about 2 s (SIGTERM, a 2 s wait, then SIGKILL).

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

- **Who writes it:** only the interactive Antigravity TUI
  (`/tmp/gemini/statusline-quota-cache.json`), so `support_level` is
  `tui_only`. With no TUI session the file is absent or old: on this host on
  2026-10-19 it did not exist and the records were `missing`. The collector
  cannot refresh it.
- **Units:** `remaining_fraction` is the REMAINING fraction 0-1, `reset_time`
  is an ISO string. `reset_in_seconds` is ignored because it ages from the
  moment it was written.
- **Two pools:** `agy:gemini` (family Gemini, scope known) and `agy:3p`
  (third-party models of more than one family, so no family and scope
  unknown). A missing key is that window `unknown` with a problem; it is never
  read from the other pool. The statusline renderer does fall back across
  pools, which would report one pool's headroom as the other's.
- **Not verified:** which models the `3p` pool covers.

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

- **Who writes it:** the user's OpenCode plugin
  (`${XDG_CACHE_HOME}/opencode-go/usage.json`, schema 1). `XDG_CACHE_HOME` is
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
- **Writer change, 2026-10-19:** the plugin (aadev
  `agent/opencode/plugin/go-usage.ts`) no longer writes `no opencode-go key`:
  a process without a key leaves the cache alone. On success it records the
  response's real HTTP status. The collector still maps that string, for
  caches an older writer left behind.
- **Not verified:** which models a window covers (the endpoint does not say,
  so family `null`, scope `unknown`); what statuses besides `ok` the endpoint
  can return; how often the plugin refreshes.

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
- **An mtime can go stale while the values have not changed.** Caches judged
  by mtime are only as fresh as their writer's last rewrite. Measured
  2026-10-19 on ps-241: an idle AGY TUI rewrote its cache at irregular
  intervals (09:14, 09:17, 09:30, 09:45: roughly every 13-15 min), and only
  while it was open; the 09:45 value had not moved although an agy leg spent
  Gemini quota at 09:34-09:40.

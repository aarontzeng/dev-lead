import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { AgentRow, AgentsView, LegRow, LegsView, QuotaRow, QuotaView } from '../types'

const PANE = 'dev-lead-panel'
const EVERY_MS = 60_000
// Resolve the newest dev-lead release at run time; its path changes per version.
// Numeric sort per dot field, not `sort -V`: macOS's stock sort may lack -V.
const SNAPSHOT =
  'python3 "$(ls -d "$HOME"/.claude/plugins/cache/dev-lead/dev-lead/[0-9]* | awk -F/ \'$NF ~ /^[0-9]+([.][0-9]+)*$/ {print $NF" "$0}\' ' +
  "| sort -t. -k1,1n -k2,2n -k3,3n | tail -1 | cut -d' ' -f2-)/scripts/quota-snapshot.py\""

// Windows: `bash` there is WSL's (or absent) and `python3` the Store stub, so run the same script
// on the Windows side with pwsh + py -3, newest release by numeric version (0.6.9 < 0.6.88).
// dev-lead >= 0.6.91 refreshes a stale agy/cursor/opencode-go cache by spawn_hidden on win32
// (sources opt in via the local quota-sources.json). The mod sandbox has no `process`, so ask
// the host: Windows always sets OS=Windows_NT.
const isWindows = async ($: EngineInterface): Promise<boolean> =>
  (await $.env.get('OS')) === 'Windows_NT'
const SNAPSHOT_WIN =
  "$d = Get-ChildItem (Join-Path $env:USERPROFILE '.claude\\plugins\\cache\\dev-lead\\dev-lead') -Directory -ErrorAction SilentlyContinue | " +
  "Where-Object { $_.Name -match '^\\d+(\\.\\d+)*$' } | Sort-Object { [version]$_.Name } | Select-Object -Last 1; " +
  "if (-not $d) { [Console]::Error.WriteLine('dev-lead plugin cache not found'); exit 1 }; " +
  "py -3 (Join-Path $d.FullName 'scripts\\quota-snapshot.py') --refresh --refresh-wait 30"
// A session started by launchd or a remote host can hand its children a PATH without the usual
// directories (seen on macOS: "posix_spawn 'bash'" / "'python3'" ENOENT). So POSIX children run as
// the absolute /bin/sh and get the host's PATH with the common install directories added.
// User-managed Pythons (pyenv shims, ~/.local/bin) come before the system directories.
const POSIX_PATH_TAIL = ['~/.pyenv/shims', '~/.local/bin', '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']
// `win` comes from the caller's one platform lookup: a second lookup could fail on its own.
const childEnv = async ($: EngineInterface, win: boolean): Promise<Record<string, string> | undefined> => {
  if (win) return undefined
  const host = (await $.env.get('PATH')) ?? ''
  const home = (await $.env.get('HOME')) ?? ''
  const have = host.split(':').filter(Boolean)
  const tail = POSIX_PATH_TAIL.map(d => (d.startsWith('~/') ? (home ? home + d.slice(1) : '') : d)).filter(Boolean)
  return { PATH: [...have, ...tail.filter(d => !have.includes(d))].join(':') }
}

const snapshotArgv = (win: boolean): string[] =>
  win
    ? ['pwsh', '-NoProfile', '-Command', SNAPSHOT_WIN]
    : ['/bin/sh', '-c', `${SNAPSHOT} --refresh --refresh-wait 30`]

const EMPTY: QuotaView = { rows: [], at: '', error: 'loading…' }
const view = atom({ plugin: 'dev-lead-panel', key: 'view' } as const, EMPTY, {
  shape: 'view-1',
})

const AGENTS_EVERY_MS = 15_000
// Windows lists processes through WMI, which takes 10-40 s on a busy laptop:
// scan legs once a minute there and give each scan 50 s, under that minute.
const LEGS_EVERY_MS_WINDOWS = 60_000
const LEGS_TIMEOUT_MS_WINDOWS = 50_000
const NO_AGENTS: AgentsView = { self: '', rows: [], at: '', error: 'loading…' }
const agents = atom({ plugin: 'dev-lead-panel', key: 'agents' } as const, NO_AGENTS, {
  shape: 'agents-1',
})

const NO_LEGS: LegsView = { rows: [], error: 'scanning…' }
const legs = atom({ plugin: 'dev-lead-panel', key: 'legs' } as const, NO_LEGS, { shape: 'legs-1' })

// The snapshot leaves family empty for multi-model CLIs; name what cursor and opencode-go offer here.
const FAMILY_OVERRIDE: Record<string, string> = {
  cursor: 'Grok / Composer',
  'opencode-go': 'multi-model',
}

const LABEL: Record<string, string> = {
  five_hour: '5h',
  weekly: 'wk',
  monthly: 'mo',
}

// Display order; a source not listed here goes last, in the snapshot's own order.
const ORDER = ['claude', 'codex', 'agy', 'cursor', 'opencode-go']
const rank = (source: string): number => {
  const i = ORDER.indexOf(source)
  return i === -1 ? ORDER.length : i
}

const BAR_CELLS = 5

const bar = (pct: number): string => {
  const filled = Math.max(0, Math.min(BAR_CELLS, Math.round((pct / 100) * BAR_CELLS)))
  return '█'.repeat(filled) + '░'.repeat(BAR_CELLS - filled)
}

const hhmm = (iso: string): string => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

// Reset moment: bare HH:MM only when it falls on today's date, else MM/DD HH:MM.
const resetLabel = (iso: string): string => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--'
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) return hhmm(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${hhmm(iso)}`
}

// --refresh only starts a CLI whose cache is older than its TTL, so a 60 s poll
// wakes agy and cursor at most about every 5 minutes and opencode-go about every
// 15. opencode-go needs opencode 2 with dev-lead's go-usage plugin and
// "refresh": "tmux_spawn" in the host's quota-sources.json; the snapshot then
// starts `opencode --standalone`, whose plugin rewrites usage.json in about 5 s.
// A poll that never settles is replaced after this many skipped timer ticks.
const STALL_TICKS = 4

// One refresh at a time, like the agents and legs polls below: a run can take up
// to 150 s against a 60 s clock, and an older answer must not land over a newer one.
let quotaBusy = false
let quotaSkipped = 0
let quotaSeq = 0
// Resolves false when a run was already going and this call started none.
const refresh = async ($: EngineInterface, tick = false): Promise<boolean> => {
  if (quotaBusy && (!tick || ++quotaSkipped < STALL_TICKS)) return false
  quotaSkipped = 0
  const seq = ++quotaSeq
  quotaBusy = true
  try {
    const win = await isWindows($)
    const env = await childEnv($, win)
    // no env key at all on Windows: the call there stays exactly what it was
    const run = await $.process.run(snapshotArgv(win), { timeoutMs: 150_000, ...(env ? { env } : {}) })
    if (run.exitCode !== 0) throw new Error(run.stderr.slice(0, 120) || `exit ${run.exitCode}`)
    const data = JSON.parse(run.stdout)
    // Only the Gemini pool of agy; its second (third-party) pool is not a review leg.
    const kept = (data.records ?? []).filter(
      (r: any) => r.source !== 'agy' || r.pool_id === 'agy:gemini',
    )
    const rows: QuotaRow[] = [...kept]
      .sort((x: any, y: any) => rank(String(x.source)) - rank(String(y.source)))
      .map((r: any) => ({
      source: String(r.source),
      family: r.family ? String(r.family) : (FAMILY_OVERRIDE[String(r.source)] ?? '?'),
      freshness: String(r.freshness),
      windows: (r.windows ?? []).map((w: any) => ({
        kind: String(w.kind),
        usedPct: Number(w.used_pct),
        resetsAt: String(w.resets_at),
      })),
    }))
    if (seq !== quotaSeq) return true
    await update($, view, () => ({ rows, at: hhmm(data.generated_at), error: '' }))
  } catch (err) {
    if (seq !== quotaSeq) return true
    const msg = err instanceof Error ? err.message : String(err)
    await update($, view, old => ({ ...old, error: msg.slice(0, 120) }))
  } finally {
    if (seq === quotaSeq) quotaBusy = false
  }
  return true
}

// ListAgents prints "This session is NAME [ref] — …" and one
// "  NAME [ref]  ·  kind  ·  status" line per peer.
// One poll of each kind at a time: a slow run must not overlap the next tick
// or land its older answer after a newer one.
// Takeover counts skipped ticks, not wall time, so a clock set back cannot stall it.
let agentsBusy = false
let agentsSkipped = 0
let agentsSeq = 0
let legsOnWindows = false
let legsTicks = 0
let legsBusy = false
let legsSkipped = 0
let legsSeq = 0

const pollAgents = async ($: EngineInterface, tick = false) => {
  // A ListAgents call that never settles must not block polling for good:
  // after four skipped ticks a fresh poll starts, and only the newest may write.
  // Only timer ticks count toward a takeover, so repeated /quota cannot starve a live poll.
  if (agentsBusy && (!tick || ++agentsSkipped < STALL_TICKS)) return
  agentsSkipped = 0
  const seq = ++agentsSeq
  agentsBusy = true
  try {
    const text = (await $.tool.call({ tool: 'ListAgents' })).text ?? ''
    const self = /This session is (.+?) \[/.exec(text)?.[1] ?? ''
    const rows: AgentRow[] = []
    for (const line of text.split('\n')) {
      const m = /^\s+(.+?) \[[0-9a-f]+\]\s+·\s+(.+?)\s+·\s+(\S.*?)\s*$/.exec(line)
      if (m) rows.push({ name: m[1] ?? "", kind: m[2] ?? "", status: m[3] ?? "" })
    }
    if (seq !== agentsSeq) return
    await update($, agents, () => ({ self, rows, at: hhmm(new Date().toISOString()) || '', error: '' }))
  } catch (err) {
    if (seq !== agentsSeq) return
    const msg = err instanceof Error ? err.message : String(err)
    await update($, agents, old => ({ ...old, error: msg.slice(0, 120) }))
  } finally {
    if (seq === agentsSeq) agentsBusy = false
  }
}

// scripts/legs.py scans this user's processes for dev-lead legs and prints only
// adapter, role, model, effort, age and the working directory's name.
const pollLegs = async ($: EngineInterface, tick = false) => {
  // Same takeover as the agents poll: a run that never settles is replaced after four skipped ticks.
  if (legsBusy && (!tick || ++legsSkipped < STALL_TICKS)) return
  legsSkipped = 0
  const seq = ++legsSeq
  legsBusy = true
  try {
    const script = `${$.plugin.root}/scripts/legs.py`
    const win = await isWindows($)
    const argv = win ? ['py', '-3', script] : ['/bin/sh', '-c', 'exec python3 "$0"', script]
    const env = await childEnv($, win)
    const run = await $.process.run(argv, {
      timeoutMs: win ? LEGS_TIMEOUT_MS_WINDOWS : 30_000,
      ...(env ? { env } : {}),
    })
    if (run.exitCode !== 0) throw new Error(run.stderr.slice(0, 120) || `exit ${run.exitCode}`)
    const rows: LegRow[] = (JSON.parse(run.stdout).legs ?? []).map((r: any) => ({
      adapter: String(r.adapter),
      role: String(r.role ?? ''),
      model: String(r.model ?? ''),
      effort: String(r.effort ?? ''),
      secs: Number(r.secs) || 0,
      dir: String(r.dir ?? ''),
    }))
    if (seq !== legsSeq) return
    await update($, legs, () => ({ rows, error: '' }))
  } catch (err) {
    if (seq !== legsSeq) return
    const msg = err instanceof Error ? err.message : String(err)
    await update($, legs, old => ({ ...old, error: msg.slice(0, 120) }))
  } finally {
    if (seq === legsSeq) legsBusy = false
  }
}

const age = (secs: number): string =>
  secs < 60 ? `${secs}s` : secs < 3600 ? `${Math.floor(secs / 60)}m` : `${Math.floor(secs / 3600)}h${String(Math.floor((secs % 3600) / 60)).padStart(2, '0')}`

const NAME_W = 18
const clip = (s: string): string => (s.length > NAME_W ? s.slice(0, NAME_W - 1) + '…' : s.padEnd(NAME_W))

// userConfig: quota_alert_pct (0 = no mark; a threshold is the user's own rule, never dev-lead's)
// and show_agents (false skips the ListAgents poll and its section).
type Options = { quota_alert_pct?: number; show_agents?: boolean }

export const register: Register = (on, options) => {
  const opts = (options ?? {}) as Options
  const raw = Number(opts.quota_alert_pct ?? 0)
  const alertPct = Number.isFinite(raw) && raw > 0 && raw <= 100 ? raw : 0
  const showAgents = opts.show_agents !== false
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'quota',
      description: 'Open the quota pane and refresh it now',
    })
    void $.ui.open({ id: PANE, title: 'Quota' })
    void refresh($)
    $.clock.every(EVERY_MS, () => { void refresh($, true) })
    if (showAgents) {
      void pollAgents($)
      $.clock.every(AGENTS_EVERY_MS, () => pollAgents($, true))
    }
    void pollLegs($)
    // The legs clock is registered now at 15 s; once the platform lookup says
    // Windows, three ticks in four are skipped. A lookup that fails or never
    // answers leaves the 15 s scan, and never holds up session.start.
    void isWindows($).then(win => { legsOnWindows = win }, () => {})
    $.clock.every(AGENTS_EVERY_MS, () => {
      if (legsOnWindows && ++legsTicks % (LEGS_EVERY_MS_WINDOWS / AGENTS_EVERY_MS) !== 0) return
      return pollLegs($, true)
    })

    return next(e)
  })

  on('command.run', { command: 'quota' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Quota' })
    const [started] = await Promise.all([refresh($), ...(showAgents ? [pollAgents($)] : []), pollLegs($)])

    return {
      text: started
        ? 'Quota pane refreshed.'
        : 'A quota refresh was already running; the pane updates when it finishes.',
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const v = await read($, view)
    const a = await read($, agents)
    const l = await read($, legs)

    return (
      <Box flexDirection="column">
        <Text dimColor>
          {v.at ? `snapshot ${v.at}` : 'no data'}
          {alertPct > 0 ? ` · alert ${alertPct}%` : ''}
        </Text>
        {v.error !== '' && <Text color="red">{v.error}</Text>}
        {v.rows.map(r => {
          const stale = r.freshness !== 'fresh'
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text bold dimColor={stale}>
                {r.source} <Text dimColor>({r.family}){stale ? ' STALE' : ''}</Text>
              </Text>
              {r.windows.map(w => (
                <Text
                  dimColor={stale}
                  color={!stale && alertPct > 0 && w.usedPct >= alertPct ? 'red' : undefined}
                >
                  {(LABEL[w.kind] ?? w.kind).padEnd(3)}
                  {bar(w.usedPct)} {String(Math.round(w.usedPct)).padStart(3)}%
                  <Text dimColor> ↻ {resetLabel(w.resetsAt)}</Text>
                </Text>
              ))}
            </Box>
          )
        })}
        {showAgents && <Box flexDirection="column" marginTop={1}>
          <Text bold>
            agents <Text dimColor>{a.at ? `· ${a.at}` : ''}</Text>
          </Text>
          {a.error !== '' && <Text color="red">{a.error}</Text>}
          {a.self !== '' && (
            <Text>
              <Text color="#FF8C00">◆</Text> {clip(a.self)}<Text dimColor> self</Text>
            </Text>
          )}
          {a.rows.map(r => {
            // A local session reads "busy · started 6m ago"; keep the first word, shorten the rest.
            const [word = '', ...rest] = r.status.split(/\s+·\s+/)
            const age = /(\d+[smhd])/.exec(rest.join(' '))?.[1] ?? ''
            const active = word === 'running' || word === 'busy'
            const idle = word === 'idle'
            const local = r.kind !== 'Remote Control' && r.kind !== 'cloud'
            // Same-machine sessions are background work: a yellow diamond, never the remote green.
            const glyph = local ? '◆' : active ? '●' : idle ? '○' : '◌'
            const tint = local ? 'yellow' : active ? 'green' : idle ? undefined : 'yellow'
            return (
              <Text dimColor={idle}>
                <Text color={tint}>{glyph}</Text>{' '}
                {clip(r.name)}
                <Text dimColor>
                  {' '}
                  {word}
                  {age !== '' ? ` ${age}` : ''}
                  {r.kind === 'cloud' ? ' ☁' : ''}
                </Text>
              </Text>
            )
          })}
        </Box>}
        <Box flexDirection="column" marginTop={1}>
          <Text bold>dev-lead legs</Text>
          {l.error !== '' && <Text color={l.error === 'scanning…' ? undefined : 'red'} dimColor={l.error === 'scanning…'}>{l.error}</Text>}
          {l.error === '' && l.rows.length === 0 && <Text dimColor>none running</Text>}
          {l.rows.map(r => (
            <Box flexDirection="column">
              <Text>
                <Text color="magenta">▸</Text> {r.adapter}
                {r.role !== '' ? <Text dimColor> {r.role}</Text> : null}
                <Text dimColor> · {age(r.secs)}</Text>
              </Text>
              {r.model !== '' && (
                <Text dimColor>
                  {'  '}
                  {r.model}
                  {r.effort !== '' ? ` #${r.effort}` : ''}
                </Text>
              )}
              {r.dir !== '' && <Text dimColor>{'  '}{r.dir}</Text>}
            </Box>
          ))}
        </Box>
      </Box>
    )
  })
}

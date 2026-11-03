import { expect, mock, test } from 'claude-code/testing'

const PANE = {
  plugin: 'dev-lead-panel',
  component: 'Pane',
  requestId: 'dev-lead-panel',
  props: {
    title: 'Quota',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 60 },
    view: {},
  },
} as const

const snapshot = (at: string, used: number, five?: number) =>
  JSON.stringify({
    generated_at: at,
    records: [
      {
        source: 'codex',
        family: 'GPT',
        freshness: 'fresh',
        windows: [
          ...(five === undefined ? [] : [{ kind: 'five_hour', used_pct: five, resets_at: '2030-01-01T00:00:00Z' }]),
          { kind: 'weekly', used_pct: used, resets_at: '2030-01-01T00:00:00Z' },
        ],
      },
    ],
  })

const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '' } })

// The engine beneath the plugin: session, pane, ListAgents, environment, and
// process.run answered per script. `quota` decides each quota-snapshot call.
const world = (
  on: any,
  quota: (n: number) => Promise<unknown> | unknown,
  agentsText = 'This session is me [abc123] — x\n',
  quotaTaken = false,
) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/home/t', PATH: '/usr/bin' })
  let calls = 0
  let listAgents = 0
  let opened = 0
  on('command.register', () => {
    if (quotaTaken) throw new Error('"/quota" refused: the plugin quota-panel registered it already')
    return { value: { command: 'quota' } }
  })
  on('ui.open', () => {
    opened++
    return { value: { isPlaced: true } }
  })
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('tool.call', (_: unknown, e: any) => {
    if (e.tool === 'ListAgents') listAgents++
    return { result: {}, text: agentsText }
  })
  on('process.run', (_: unknown, e: any) => {
    const argv = e.argv.join(' ')
    if (argv.includes('quota-snapshot.py')) return quota(++calls)
    return ok(JSON.stringify({ legs: [] }))
  })
  return { clock, quotaCalls: () => calls, listAgents: () => listAgents, opened: () => opened }
}

test('a slow older quota refresh never overwrites a newer one', async ($, on) => {
  // Run 1 answers 11% after 100 s (inside its 150 s timeout); any later run answers 77% at once.
  const w = world(on, async n => {
    if (n === 1) {
      await w.clock.sleep(100_000)
      return ok(snapshot('2030-01-01T00:01:00Z', 11))
    }
    return ok(snapshot('2030-01-01T00:02:00Z', 77))
  })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  // At 110 s run 1 has landed and no later tick has come since the 60 s one.
  await w.clock.advance(110_000)

  // Whatever is shown must come from the run that started last.
  const want = w.quotaCalls() === 1 ? /11%/ : /77%/
  const stale = w.quotaCalls() === 1 ? /77%/ : /11%/
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: want })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: stale })).toBeUndefined()
  await ui.unmount()
})

test('quota refreshes do not pile up while one is still running', async ($, on) => {
  const w = world(on, async n => {
    if (n === 1) await w.clock.sleep(130_000)
    return ok(snapshot('2030-01-01T00:01:00Z', 5))
  })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  // Ticks at 60 s and 120 s fall inside run 1 and are skipped.
  await w.clock.advance(125_000)
  expect(w.quotaCalls()).toBe(1)
})

test('a fresh window at or above the alert is red, below it is not', { options: { quota_alert_pct: 90 } }, async ($, on) => {
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 95, 50)))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const high = await ui.find({ type: 'Text', text: /95%/ })
  const low = await ui.find({ type: 'Text', text: /50%/ })
  expect(high).toBeDefined()
  expect(low).toBeDefined()
  expect(high?.props.color).toBe('red')
  expect(low?.props.color).toBeUndefined()
  await ui.unmount()
})

test('no alert set: a 95% window is not red', async ($, on) => {
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 95)))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const row = await ui.find({ type: 'Text', text: /95%/ })
  expect(row).toBeDefined()
  expect(row?.props.color).toBeUndefined()
  await ui.unmount()
})

test('show_agents false never calls ListAgents (start, clock, /quota)', { options: { show_agents: false } }, async ($, on) => {
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 5)))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.advance(61_000)
  await $.command.run({ command: 'quota', args: '' } as any)
  expect(w.listAgents()).toBe(0)
})

test('show_agents default polls ListAgents', async ($, on) => {
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 5)))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const atStart = w.listAgents()
  expect(atStart).toBeGreaterThan(0)
  // The 15 s clock keeps polling after the first call.
  await w.clock.advance(31_000)
  expect(w.listAgents()).toBeGreaterThan(atStart)
})

test('/quota during a running refresh starts no second run and says so', async ($, on) => {
  const w = world(on, async n => {
    if (n === 1) await w.clock.sleep(100_000)
    return ok(snapshot('2030-01-01T00:01:00Z', 5))
  })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const res: any = await $.command.run({ command: 'quota', args: '' } as any)
  expect(w.quotaCalls()).toBe(1)
  expect(String(res?.text)).toMatch(/already running/)
})

test('a long session status stays on the name row', async ($, on) => {
  const text =
    'This session is me [abc123] — x\n' +
    '  Tech-Chef [0a79fa]  ·  Remote Control  ·  requires_action\n' +
    '  Some-Very-Long-Session-Name [0b79fa]  ·  Remote Control  ·  waiting_for_permission\n'
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 5)), text)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const all = await ui.findAll({ type: 'Text' })
  const rows = all.filter(t => /Tech-Chef|Some-Very/.test(t.text))
  expect(rows).toHaveLength(2)
  for (const r of rows) {
    // glyph + space + 18-column name + space + status: the status part is at most 9 columns.
    expect(r.text.length).toBeLessThanOrEqual(2 + 18 + 1 + 9)
  }
  expect(rows[0]?.text).toMatch(/action/)
  await ui.unmount()
})

test('status words are coloured by what they ask of you', async ($, on) => {
  const text =
    'This session is me [abc123] — x\n' +
    '  Runner [0a0001]  ·  Remote Control  ·  running\n' +
    '  Sleeper [0a0002]  ·  Remote Control  ·  idle\n' +
    '  Asker [0a0003]  ·  Remote Control  ·  requires_action\n' +
    '  Odd [0a0004]  ·  Remote Control  ·  stopping\n'
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 5)), text)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const word = async (re: RegExp) => {
    const t = await ui.find({ type: 'Text', text: re })
    expect(t).toBeDefined()
    return t?.props ?? {}
  }
  const running = await word(/^ running$/)
  expect(running.color).toBe('green')
  const idle = await word(/^ idle$/)
  expect(idle.color).toBeUndefined()
  expect(idle.dimColor).toBe(true)
  const action = await word(/^ action$/)
  expect(action.color).toBe('yellow')
  expect(action.bold).toBe(true)
  const odd = await word(/^ stopping$/)
  expect(odd.color).toBe('yellow')
  expect(odd.bold).toBeFalsy()
  await ui.unmount()
})

test('the pane still opens and polls when /quota is already taken', async ($, on) => {
  // Another plugin (the old quota-panel mod) owns /quota: registering it throws.
  const w = world(on, () => ok(snapshot('2030-01-01T00:01:00Z', 42)), undefined, true)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  expect(w.opened()).toBeGreaterThan(0)
  expect(w.quotaCalls()).toBe(1)
  expect(w.listAgents()).toBeGreaterThan(0)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /42%/ })).toBeDefined()
  await ui.unmount()
})

test('a source with no cache says NO DATA and how to set it up; an old one says STALE and why', async ($, on) => {
  const rec = (source: string, freshness: string, read_status: string, problems: string[]) =>
    ({ source, family: 'X', freshness, read_status, problems, windows: [] })
  const w = world(on, () => ok(JSON.stringify({
    generated_at: '2030-01-01T00:01:00Z',
    records: [
      // a failed refresh is listed first, the missing cache after it: still NO DATA
      rec('claude', 'unknown', 'missing', ['claude refresh failed: quota-fetch.py did not rewrite the cache within 0.5 s',
                                           'cache file does not exist']),
      rec('cursor', 'stale', 'ok', ['cursor refresh failed: quota-fetch.py did not rewrite the cache within 30 s']),
    ],
  })))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /NO DATA/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /run \/dev-lead:quota-setup/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /STALE/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /cursor refresh failed/ })).toBeDefined()
  // a missing cache is not also called STALE
  const claudeHead = await ui.find({ type: 'Text', text: /claude/ })
  expect(JSON.stringify(claudeHead?.props ?? {})).not.toMatch(/STALE/)
  await ui.unmount()
})

test('a missing codex binary shows its own problem, not the quota-setup hint', async ($, on) => {
  const w = world(on, () => ok(JSON.stringify({
    generated_at: '2030-01-01T00:01:00Z',
    records: [{ source: 'codex', family: 'GPT', freshness: 'unknown', read_status: 'missing',
                problems: ['codex binary not found'], windows: [] }],
  })))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await w.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /codex binary not found/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /quota-setup/ })).toBeUndefined()
  await ui.unmount()
})

import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'dev-lead-panel',
  component: 'Pane',
  requestId: 'dev-lead-panel',
  props: {
    title: 'Quota',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

for (const surface of ['terminal', 'desktop'] as const) {
  test(`no alert threshold by default (${surface})`, async $ => {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /alert/ }))).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /gate/ }))).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /^agents/ }))).toBeDefined()
    await ui.unmount()
  })

  test(`a set threshold is shown (${surface})`, { options: { quota_alert_pct: 85 } }, async $ => {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /alert 85%/ }))).toBeDefined()
    await ui.unmount()
  })

  test(`show_agents false hides the agents section (${surface})`, { options: { show_agents: false } }, async $ => {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /^agents/ }))).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /dev-lead legs/ }))).toBeDefined()
    await ui.unmount()
  })
}

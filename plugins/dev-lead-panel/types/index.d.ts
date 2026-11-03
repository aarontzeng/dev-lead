export type QuotaWindow = { kind: string; usedPct: number; resetsAt: string }
export type QuotaRow = {
  source: string
  family: string
  freshness: string
  windows: QuotaWindow[]
}
export type QuotaView = { rows: QuotaRow[]; at: string; error: string }
export type AgentRow = { name: string; kind: string; status: string }
export type LegRow = { adapter: string; role: string; model: string; effort: string; secs: number; dir: string }
export type LegsView = { rows: LegRow[]; error: string }
export type AgentsView = { self: string; rows: AgentRow[]; at: string; error: string }

declare module 'claude-code' {
  interface PluginState {
    'dev-lead-panel': { view: Shaped<QuotaView>; agents: Shaped<AgentsView>; legs: Shaped<LegsView> }
  }
}

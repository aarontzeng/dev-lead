export type QuotaWindow = { kind: string; usedPct: number; resetsAt: string }
export type QuotaRow = {
  source: string
  family: string
  freshness: string
  /** quota-snapshot's read_status: 'ok', 'missing' (no cache file at all), or why it could not be read */
  status: string
  /** the record's first problem, shown under a row that is not fresh */
  problem: string
  /** a cache source with no cache file at all (setup can fix it; a missing codex binary is not this) */
  noCache: boolean
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

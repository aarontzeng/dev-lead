import { Plugin } from "@opencode/plugin/tui"
import { readCache, type Snapshot, type Window } from "./cache"

// How often the CLI re-reads the cache the server plugin writes. The poller
// refreshes every 10 minutes; reading a tiny JSON more often is cheaper than a
// filesystem watcher and cannot miss a write that lands between two events.
const READ_MS = 30_000

function resetLabel(iso?: string) {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  const mm = String(d.getMonth() + 1).padStart(2, "0")
  const dd = String(d.getDate()).padStart(2, "0")
  return ` (${mm}/${dd})`
}

function render(w: Window | undefined, label: string) {
  if (!w || typeof w.percent !== "number") return `${label} --`
  return `${label} ${w.percent}%${resetLabel(w.resetsAt)}`
}

function describe(usage: Snapshot) {
  const parts = [render(usage.rolling, "5h"), render(usage.weekly, "週"), render(usage.monthly, "月")]
  const pcts = [usage.rolling, usage.weekly, usage.monthly]
    .map((w) => (typeof w?.percent === "number" ? w.percent : -1))
    .filter((p) => p >= 0)
  const peak = pcts.length > 0 ? Math.max(...pcts) : 0
  const variant = peak >= 95 ? ("error" as const) : peak >= 80 ? ("warning" as const) : ("info" as const)
  return { message: parts.join(" · "), variant }
}

function valueKey(usage: Snapshot) {
  return [usage.rolling, usage.weekly, usage.monthly].map((w) => `${w?.status ?? "?"}:${w?.percent ?? "?"}`).join("|")
}

// The toast half of the quota plugin. A V2 server plugin has no TUI handle
// (no client.tui), so the visible part lives in the CLI, reading the same
// cache dev-lead reads. Values unchanged since the last toast stay silent.
export default Plugin.define({
  id: "go-usage",
  setup(ctx) {
    let stopped = false
    let last = ""

    function show(doc: Awaited<ReturnType<typeof readCache>>) {
      if (!doc) return
      if (doc.error) {
        if (doc.error === last) return
        last = doc.error
        ctx.ui.toast.show({
          title: "OpenCode Go",
          message: "額度查詢失敗：" + doc.error,
          variant: "error",
          duration: 6000,
        })
        return
      }
      if (!doc.usage) return
      const key = valueKey(doc.usage)
      if (key === last) return
      last = key
      const { message, variant } = describe(doc.usage)
      ctx.ui.toast.show({ title: "OpenCode Go 額度", message, variant, duration: 5000 })
    }

    async function refresh() {
      const doc = await readCache()
      if (!stopped) show(doc)
    }

    const start = setTimeout(() => void refresh(), 5_000)
    const timer = setInterval(() => void refresh(), READ_MS)
    return () => {
      stopped = true
      clearTimeout(start)
      clearInterval(timer)
    }
  },
})

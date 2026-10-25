import { chmod, mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, isAbsolute, join } from "node:path"

export type Window = {
  status?: string
  percent?: number
  resetsAt?: string
}

export type Snapshot = {
  rolling?: Window
  weekly?: Window
  monthly?: Window
}

// The last answer is also written to a cache file so that tools outside
// opencode (dev-lead's quota-snapshot.py) can read it without ever holding the
// key, and so the TUI plugin (tui.ts) can toast without a second request.
// Schema 1: fetched_at/usage are the last SUCCESSFUL read and are kept when a
// later read fails; attempted_at/error/http_status describe the latest try.
// Only the three windows' status/percent/resetsAt are written.
export type CacheDoc = {
  schema: 1
  fetched_at: string | null
  usage: Snapshot | null
  attempted_at: string
  error: string | null
  http_status: number | null
}

// XDG: an unset, empty or relative XDG_CACHE_HOME means ~/.cache. The readers
// (tui.ts and quota-snapshot.py) resolve the path by the same rule.
export function cacheFile() {
  const x = process.env.XDG_CACHE_HOME
  const base = x && isAbsolute(x) ? x : join(homedir(), ".cache")
  return join(base, "opencode-go", "usage.json")
}

export function pickWindow(w: unknown): Window | undefined {
  if (!w || typeof w !== "object") return undefined
  const o = w as Record<string, unknown>
  return {
    status: typeof o.status === "string" ? o.status : undefined,
    percent: typeof o.percent === "number" ? o.percent : undefined,
    resetsAt: typeof o.resetsAt === "string" ? o.resetsAt : undefined,
  }
}

export function pickSnapshot(u: unknown): Snapshot {
  const o = (u && typeof u === "object" ? u : {}) as Record<string, unknown>
  return { rolling: pickWindow(o.rolling), weekly: pickWindow(o.weekly), monthly: pickWindow(o.monthly) }
}

export async function lastSuccess(): Promise<{ fetched_at: string | null; usage: Snapshot | null }> {
  const doc = await readCache()
  if (doc?.fetched_at && doc.usage) return { fetched_at: doc.fetched_at, usage: doc.usage }
  return { fetched_at: null, usage: null }
}

export async function readCache(): Promise<CacheDoc | null> {
  try {
    const doc = JSON.parse(await readFile(cacheFile(), "utf8"))
    if (doc?.schema !== 1) return null
    return {
      schema: 1,
      fetched_at: typeof doc.fetched_at === "string" ? doc.fetched_at : null,
      usage: doc.usage ? pickSnapshot(doc.usage) : null,
      attempted_at: typeof doc.attempted_at === "string" ? doc.attempted_at : "",
      error: typeof doc.error === "string" ? doc.error : null,
      http_status: typeof doc.http_status === "number" ? doc.http_status : null,
    }
  } catch {
    return null
  }
}

// Atomic (temp file + rename), owner-only (chmod, so the umask cannot change
// it). A failed write is dropped: the cache is a convenience, never a reason
// to break the poll, so callers do not await it.
export async function writeCache(doc: CacheDoc) {
  const file = cacheFile()
  const tmp = `${file}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2)}.tmp`
  try {
    await mkdir(dirname(file), { recursive: true, mode: 0o700 })
    await writeFile(tmp, JSON.stringify(doc) + "\n", { mode: 0o600, flag: "wx" })
    await chmod(tmp, 0o600)
    await rename(tmp, file)
  } catch {
    await unlink(tmp).catch(() => {})
  }
}

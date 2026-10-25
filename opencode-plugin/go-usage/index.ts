import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"
import { Plugin } from "@opencode/plugin"
import { lastSuccess, pickSnapshot, writeCache, type Snapshot } from "./cache"

const USAGE_URL = "https://opencode.ai/inference/go/v1/usage"
const PROVIDER = "opencode-go"
const POLL_MS = 10 * 60 * 1000

function authFile() {
  const base = process.env.XDG_DATA_HOME ?? join(homedir(), ".local", "share")
  return join(base, "opencode", "auth.json")
}

async function apiKey(): Promise<string | null> {
  if (process.env.OPENCODE_GO_API_KEY) return process.env.OPENCODE_GO_API_KEY
  try {
    const raw = await readFile(authFile(), "utf8")
    const entry = JSON.parse(raw)[PROVIDER]
    if (typeof entry?.key === "string" && entry.key.length > 0) return entry.key
  } catch {}
  return null
}

async function fetchUsage(): Promise<{ usage: Snapshot; status: number }> {
  const key = await apiKey()
  if (!key) throw new NoGoKeyError()
  const res = await fetch(USAGE_URL, {
    headers: { Authorization: "Bearer " + key },
    signal: AbortSignal.timeout(10_000),
  })
  if (!res.ok) throw new UsageHttpError(res.status)
  let body: { usage?: Snapshot }
  try {
    body = (await res.json()) as { usage?: Snapshot }
  } catch (err) {
    throw new UsageBodyError(err instanceof Error ? err.message : String(err), "usage endpoint returned invalid JSON", res.status)
  }
  if (!body || typeof body !== "object" || !body.usage) {
    const m = "usage endpoint returned no usage field"
    throw new UsageBodyError(m, m, res.status)
  }
  return { usage: body.usage, status: res.status }
}

class NoGoKeyError extends Error {}

// A 2xx response whose body is unusable: `cacheText` is fixed, because the
// parser's own message can quote the body and the cache is read by other tools.
class UsageBodyError extends Error {
  cacheText: string
  status: number
  constructor(message: string, cacheText: string, status: number) {
    super(message)
    this.cacheText = cacheText
    this.status = status
  }
}

const KNOWN_ERROR_NAMES = new Set(["TimeoutError", "AbortError", "TypeError"])

// What the cache may say about a failure: fixed strings only. A runtime error
// message can quote the response body, so it never goes to the file.
function cacheError(err: unknown): string {
  try {
    if (err instanceof UsageHttpError) return "usage endpoint returned " + err.status
    if (err instanceof UsageBodyError) return err.cacheText
    const name = err instanceof Error ? err.name : undefined
    return typeof name === "string" && KNOWN_ERROR_NAMES.has(name) ? "request failed: " + name : "request failed"
  } catch {
    return "request failed"
  }
}

class UsageHttpError extends Error {
  status: number
  constructor(status: number) {
    super("usage endpoint returned " + status)
    this.status = status
  }
}

// The poller. It owns the request and the cache file; the toast is the CLI
// plugin's job (tui.ts), because a V2 server plugin has no TUI handle.
export default Plugin.define({
  id: "go-usage",
  async setup() {
    let timer: ReturnType<typeof setInterval> | undefined
    let stopped = false

    function stop() {
      stopped = true
      if (timer) clearInterval(timer)
      timer = undefined
    }

    async function tick() {
      if (stopped) return
      try {
        const { usage, status } = await fetchUsage()
        const fetched_at = new Date().toISOString()
        await writeCache({
          schema: 1,
          fetched_at,
          usage: pickSnapshot(usage),
          attempted_at: fetched_at,
          error: null,
          http_status: status,
        })
      } catch (err) {
        // no key here says nothing about the account; leave the shared cache alone
        if (err instanceof NoGoKeyError) {
          stop()
          return
        }
        const prev = await lastSuccess()
        // stamped after reading the last success, so it is never older than it
        await writeCache({
          schema: 1,
          ...prev,
          attempted_at: new Date().toISOString(),
          error: cacheError(err),
          http_status: err instanceof UsageHttpError || err instanceof UsageBodyError ? err.status : null,
        })
      }
    }

    const start = setTimeout(() => {
      void tick()
      timer = setInterval(() => void tick(), POLL_MS)
      timer.unref?.()
    }, 5_000)
    start.unref?.()

    return () => {
      clearTimeout(start)
      stop()
    }
  },
})

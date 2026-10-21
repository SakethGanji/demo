/** Studio UI kit — hooks and helpers (provided by Studio, refreshed every builder turn; do not edit).
 * usePoll: setTimeout-chained polling with `until`, cleanup on unmount and no overlapping calls.
 * useJob: the Jobs pattern — poll a job's status + incremental events until it finishes.
 * eventsToSteps / eventsToNetwork turn job events into StepList / NetworkList rows. */
import { useCallback, useEffect, useRef, useState } from 'react'

export interface PollOptions<T> {
  /** stop polling once this returns true for the latest result */
  until?: (data: T) => boolean
  /** polling runs only while true (default true) */
  enabled?: boolean
  /** restart (and clear data) when any of these change */
  deps?: unknown[]
}

export interface PollState<T> {
  data: T | undefined
  error: unknown
  loading: boolean
  /** number of completed calls (useful to show "live" activity) */
  ticks: number
  /** true once `until` returned true */
  done: boolean
  /** run the call again now (keeps current data) */
  refresh: () => void
}

/** Poll `fn` every `ms` (default 1000) until `until(data)` is true. Calls never overlap; errors keep polling. */
export function usePoll<T>(fn: () => Promise<T>, ms = 1000, opts: PollOptions<T> = {}): PollState<T> {
  const { until, enabled = true, deps = [] } = opts
  const fnRef = useRef(fn)
  fnRef.current = fn
  const untilRef = useRef(until)
  untilRef.current = until
  const [nonce, setNonce] = useState(0)
  const [state, setState] = useState<Omit<PollState<T>, 'refresh'>>({ data: undefined, error: null, loading: enabled, ticks: 0, done: false })
  const depsKey = JSON.stringify(deps)
  const lastKey = useRef(depsKey)

  useEffect(() => {
    if (!enabled) {
      setState((s) => ({ ...s, loading: false }))
      return
    }
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const reset = lastKey.current !== depsKey
    lastKey.current = depsKey
    setState((s) => (reset ? { data: undefined, error: null, loading: true, ticks: 0, done: false } : { ...s, done: false }))
    const tick = async () => {
      let stop = false
      try {
        const d = await fnRef.current()
        if (!alive) return
        stop = !!untilRef.current?.(d)
        setState((s) => ({ data: d, error: null, loading: false, ticks: s.ticks + 1, done: stop }))
      } catch (e) {
        if (!alive) return
        setState((s) => ({ ...s, error: e, loading: false, ticks: s.ticks + 1 }))
      }
      if (alive && !stop) timer = setTimeout(tick, ms)
    }
    void tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [enabled, ms, nonce, depsKey])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])
  return { ...state, refresh }
}

/** Ticking clock: ms since `start` (Date | ISO string | epoch ms), updated every second while `running`. */
export function useElapsed(start: Date | string | number | null | undefined, running = true, end?: Date | string | number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running || !start) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running, start])
  const s = toMs(start)
  if (s == null) return 0
  const e = end != null ? toMs(end) : null
  return Math.max(0, (e ?? now) - s)
}

function toMs(v: Date | string | number | null | undefined): number | null {
  if (v == null || v === '') return null
  if (v instanceof Date) return v.getTime()
  if (typeof v === 'number') return v < 1e12 ? v * 1000 : v
  v = v.replace(' ', 'T')
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : v + 'Z'
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}

// ── Jobs pattern ─────────────────────────────────────────────────────────────

export const TERMINAL_STATUSES = ['passed', 'failed', 'error', 'cancelled', 'canceled', 'succeeded', 'success', 'completed', 'done', 'timeout']

/** True when a job/run status string is final. */
export function isTerminal(status: string | null | undefined): boolean {
  return !!status && TERMINAL_STATUSES.includes(String(status).toLowerCase())
}

export interface JobEvent {
  seq: number
  ts?: string | number
  case_id?: string | null
  /** step_start | step_pass | step_fail | request | blocked | log | case_start | case_end | … */
  type: string
  text?: string
  data?: any
}

export interface UseJobOptions<J> {
  /** fetch the job: `(id) => api.getRun({ id })` — must return an object with a `status` field */
  getStatus: (id: string) => Promise<J>
  /** fetch events after a cursor: `(id, after) => api.getRunEvents({ id }, { after })` → `{events, next}` or an array */
  getEvents?: (id: string, after: number) => Promise<{ events?: JobEvent[]; next?: number | null } | JobEvent[] | null | undefined>
  /** poll interval, default 1000 */
  ms?: number
  /** override the "finished" test (default: isTerminal(job.status)) */
  isDone?: (job: J) => boolean
}

export interface JobState<J> {
  job: J | undefined
  events: JobEvent[]
  done: boolean
  error: unknown
  /** ms since the first poll for this job id (or job.started_at when present) */
  elapsedMs: number
  refresh: () => void
}

/** Poll a long-running job (status + incremental events) every second until it is terminal, then fetch events once more. */
export function useJob<J extends { status?: string | null } = any>(jobId: string | null | undefined, opts: UseJobOptions<J>): JobState<J> {
  const optsRef = useRef(opts)
  optsRef.current = opts
  const [job, setJob] = useState<J | undefined>(undefined)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [done, setDone] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  const cursor = useRef(0)

  useEffect(() => {
    setJob(undefined)
    setEvents([])
    setDone(false)
    setError(null)
    cursor.current = 0
    setStartedAt(jobId ? Date.now() : null)
  }, [jobId])

  useEffect(() => {
    if (!jobId) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const pullEvents = async () => {
      const ge = optsRef.current.getEvents
      if (!ge) return
      const res = await ge(jobId, cursor.current)
      if (!alive || !res) return
      const list = Array.isArray(res) ? res : res.events ?? []
      const fresh = list.filter((e) => typeof e?.seq !== 'number' || e.seq > cursor.current)
      if (fresh.length) {
        const maxSeq = fresh.reduce((m, e) => (typeof e.seq === 'number' && e.seq > m ? e.seq : m), cursor.current)
        cursor.current = !Array.isArray(res) && typeof res.next === 'number' ? Math.max(res.next, maxSeq) : maxSeq
        setEvents((prev) => [...prev, ...fresh])
      } else if (!Array.isArray(res) && typeof res.next === 'number' && res.next > cursor.current) {
        cursor.current = res.next
      }
    }
    const tick = async () => {
      let finished = false
      try {
        const j = await optsRef.current.getStatus(jobId)
        if (!alive) return
        setJob(j)
        setError(null)
        const s = (j as any)?.started_at
        if (s) {
          const t = toMs(s)
          if (t) setStartedAt(t)
        }
        finished = optsRef.current.isDone ? optsRef.current.isDone(j) : isTerminal(j?.status)
        await pullEvents()
        if (finished) await pullEvents()
      } catch (e) {
        if (alive) setError(e)
      }
      if (!alive) return
      if (finished) setDone(true)
      else timer = setTimeout(tick, optsRef.current.ms ?? 1000)
    }
    void tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [jobId, nonce])

  const finishedAt = (job as any)?.finished_at ?? null
  const elapsedMs = useElapsed(startedAt, !!jobId && !done, done ? finishedAt ?? Date.now() : null)
  const refresh = useCallback(() => setNonce((n) => n + 1), [])
  return { job, events, done, error, elapsedMs, refresh }
}

// ── Event → row helpers ──────────────────────────────────────────────────────

export type StepStatus = 'passed' | 'failed' | 'running' | 'pending' | 'blocked' | 'skipped'

export interface Step {
  id: string
  title: string
  status: StepStatus
  ms?: number | null
  detail?: string | null
}

/** Build StepList rows from job events (optionally for one case). `planned` titles show as pending until they start. */
export function eventsToSteps(events: JobEvent[], opts: { caseId?: string | null; planned?: string[] } = {}): Step[] {
  const { caseId, planned = [] } = opts
  const steps: Step[] = []
  const startTs: Record<string, number> = {}
  let counter = 0
  for (const e of events) {
    if (caseId && e.case_id && e.case_id !== caseId) continue
    const d = e.data ?? {}
    const idx = typeof d.index === 'number' ? d.index : typeof d.step === 'number' ? d.step : null
    if (e.type === 'case_start' && caseId == null) continue
    if (e.type === 'step_start') {
      const id = `${e.case_id ?? ''}:${idx ?? counter}`
      counter += 1
      startTs[id] = typeof e.ts === 'number' ? e.ts : toMs(e.ts as string) ?? Date.now()
      steps.push({ id, title: e.text || d.title || d.action || `Step ${counter}`, status: 'running' })
    } else if (e.type === 'step_pass' || e.type === 'step_fail') {
      const id = idx != null ? `${e.case_id ?? ''}:${idx}` : null
      let s = id ? steps.find((x) => x.id === id) : undefined
      if (!s) s = [...steps].reverse().find((x) => x.status === 'running')
      if (!s) {
        s = { id: `${e.case_id ?? ''}:${idx ?? counter}`, title: e.text || `Step ${counter + 1}`, status: 'running' }
        counter += 1
        steps.push(s)
      }
      s.status = e.type === 'step_pass' ? 'passed' : 'failed'
      const end = typeof e.ts === 'number' ? e.ts : toMs(e.ts as string)
      s.ms = typeof d.duration_ms === 'number' ? d.duration_ms : end && startTs[s.id] ? end - startTs[s.id] : null
      if (e.type === 'step_fail') s.detail = d.error || e.text || null
    } else if (e.type === 'blocked') {
      steps.push({ id: `blocked:${e.seq}`, title: e.text || 'Blocked by policy', status: 'blocked', detail: d.reason ?? null })
    }
  }
  for (let i = steps.filter((s) => s.status !== 'blocked').length; i < planned.length; i++) {
    steps.push({ id: `planned:${i}`, title: planned[i], status: 'pending' })
  }
  return steps
}

export interface NetworkRow {
  id: string
  method: string
  url: string
  status?: number | null
  ms?: number | null
  blocked?: boolean
  reason?: string | null
}

/** Build NetworkList rows from `request` and `blocked` job events (optionally for one case). */
export function eventsToNetwork(events: JobEvent[], opts: { caseId?: string | null } = {}): NetworkRow[] {
  const out: NetworkRow[] = []
  for (const e of events) {
    if (opts.caseId && e.case_id && e.case_id !== opts.caseId) continue
    if (e.type !== 'request' && e.type !== 'blocked') continue
    const d = e.data ?? {}
    let method = d.method as string | undefined
    let url = (d.url ?? d.path) as string | undefined
    let status = typeof d.status === 'number' ? d.status : null
    if ((!method || !url) && e.text) {
      const m = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|WS)\s+(\S+)(?:\s+(\d{3}))?/i.exec(e.text)
      if (m) {
        method = method || m[1].toUpperCase()
        url = url || m[2]
        status = status ?? (m[3] ? Number(m[3]) : null)
      }
    }
    if (e.type === 'request' || method) {
      out.push({
        id: String(e.seq),
        method: (method || '?').toUpperCase(),
        url: url || e.text || '',
        status,
        ms: typeof d.duration_ms === 'number' ? d.duration_ms : null,
        blocked: e.type === 'blocked',
        reason: e.type === 'blocked' ? d.reason ?? e.text ?? null : null,
      })
    }
  }
  return out
}

// ── Formatting ───────────────────────────────────────────────────────────────

/** 1234 → "1.2s", 61000 → "1m 1s", 450 → "450ms". */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  const m = Math.floor(ms / 60_000)
  return `${m}m ${Math.round((ms % 60_000) / 1000)}s`
}

/** 75000 → "01:15" (mm:ss, or h:mm:ss past an hour). */
export function fmtClock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  const hh = Math.floor(s / 3600)
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return hh ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`
}

/** "3m ago" style relative time for ISO strings / epoch ms. */
export function timeAgo(v: string | number | Date | null | undefined): string {
  const t = toMs(v as any)
  if (t == null) return ''
  const s = Math.round((Date.now() - t) / 1000)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

/** GET a binary route (e.g. an image/jpeg frame) as a Blob, with the app token the generated client would send.
 * Use when a route returns raw bytes; prefer routes that return `{content_type, base64}` JSON through `api.*`. */
export async function fetchBlob(url: string): Promise<Blob | null> {
  const token = (window as any).__APP_TOKEN
  const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (res.status === 204 || !res.ok) return null
  return res.blob()
}

export type FrameLike =
  | string
  | Blob
  | { base64?: string; content_type?: string; data_url?: string; image?: string; frame?: FrameLike }
  | null
  | undefined

/** Turn a frame route result into an <img> src: data URL, base64 string, {content_type, base64}, {frame:{…}} or Blob. */
export function toImageSrc(frame: FrameLike): string | null {
  if (!frame) return null
  if (typeof frame === 'string') {
    if (!frame.trim()) return null
    if (/^(data:|blob:|https:)/.test(frame)) return frame
    return `data:image/jpeg;base64,${frame}`
  }
  if (typeof Blob !== 'undefined' && frame instanceof Blob) return frame.size ? URL.createObjectURL(frame) : null
  const f = frame as Exclude<FrameLike, string | Blob | null | undefined>
  if (f.frame !== undefined) return toImageSrc(f.frame)
  if (f.data_url) return f.data_url
  if (f.base64) return `data:${f.content_type || 'image/jpeg'};base64,${f.base64}`
  if (f.image) return toImageSrc(f.image)
  return null
}

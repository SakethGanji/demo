/**
 * What one saved run did to each step: the green path on the Page view. Pure: a workflow and an execution
 * detail in (`GET /executions/{id}`), a status per node and the ports each node sent items out of.
 *
 * The engine's `node_metrics[node]` carries `activeOutputs` (ports with items), `branchDecision` (If/Switch),
 * `status` (`success` / `waiting`), times and item counts; `node_data` keys are the nodes that ran; `errors` names
 * the nodes that failed. A node whose metrics lack `activeOutputs` (older runs) gets its ports from which of its
 * targets ran.
 */
import type { ApiExecutionDetail, ApiLoopRun, ApiPollRun } from '@/shared/lib/api'
import type { StepStatus } from '@/shared/glass/cx'

export interface RunConn {
  source_node: string
  source_output: string
  target_node: string
  target_input?: string
}

export interface NodeRun {
  status: StepStatus
  ms?: number
  startedAt?: string
  completedAt?: string
  order?: number
  inputItems?: number
  outputItems?: number
  retries?: number
  error?: string
  /** Output ports this node sent items out of. */
  ports: string[]
  /** If / Switch: what the engine says it decided. */
  decision?: string
  /** The node's output items (the last iteration's, inside a loop). */
  items: Array<Record<string, unknown>>
}

export interface RunPath {
  executionId: string
  status: string
  /** The run is over (success, failed, cancelled): steps it did not reach are skipped, not pending. */
  finished: boolean
  byNode: Record<string, NodeRun>
  /** Names of the nodes that ran, in execution order. */
  ran: string[]
  startedAt: string
  endedAt: string | null
  /** End − start, in ms (null while it runs). */
  tookMs: number | null
  errors: Array<{ node_name: string; error: string }>
  /** Per-item loop history by Loop name; empty for runs saved before the engine kept it. */
  loops: Record<string, ApiLoopRun>
  /** Each try of a repeat-until step, by Poll name; empty for older runs. */
  polls: Record<string, ApiPollRun>
}

const RUNNING = new Set(['running', 'waiting', 'queued', 'pending', 'new'])

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

/** The path one run took through a workflow. `null` without an execution (the page reads "live"). */
export function runPath(connections: RunConn[], nodeNames: string[], execution: ApiExecutionDetail | null | undefined): RunPath | null {
  if (!execution) return null
  const metrics = (execution.node_metrics ?? {}) as Record<string, Record<string, unknown> | undefined>
  const data = execution.node_data ?? {}
  const failed = new Map<string, string>()
  for (const e of execution.errors ?? []) if (e?.node_name) failed.set(e.node_name, e.error)
  const finished = !RUNNING.has(String(execution.status).toLowerCase())

  const didRun = (n: string) => n in data || !!metrics[n] || failed.has(n)
  const byNode: Record<string, NodeRun> = {}
  const names = new Set([...nodeNames, ...Object.keys(data), ...Object.keys(metrics)])
  for (const name of names) {
    const m = metrics[name] ?? {}
    const ran = didRun(name)
    let status: StepStatus
    if (failed.has(name) || m.status === 'error' || m.status === 'failed') status = 'fail'
    else if (m.status === 'waiting') status = 'need'
    else if (ran) status = 'ok'
    else status = finished ? 'skip' : 'pending'
    const items = (data[name] ?? []).map((d) => (d && typeof d === 'object' && 'json' in d ? (d.json as Record<string, unknown>) : (d as Record<string, unknown>)))
    const active = Array.isArray(m.activeOutputs) ? (m.activeOutputs as unknown[]).map(String) : null
    byNode[name] = {
      status,
      ms: num(m.executionTimeMs),
      startedAt: typeof m.startedAt === 'string' ? m.startedAt : undefined,
      completedAt: typeof m.completedAt === 'string' ? m.completedAt : undefined,
      order: num(m.executionOrder),
      inputItems: num(m.inputItemCount),
      outputItems: num(m.outputItemCount) ?? (ran ? items.length : undefined),
      retries: num(m.retries),
      error: failed.get(name) ?? (typeof m.error === 'string' ? m.error : undefined),
      ports: active ?? [],
      decision: m.branchDecision !== undefined ? String(m.branchDecision) : undefined,
      items,
    }
    if (m.errorRouted && !byNode[name].ports.includes('error')) byNode[name].ports.push('error')
    // No activeOutputs from the engine: a port was taken when one of its targets ran.
    if (!active && ran) {
      for (const c of connections) {
        if (c.source_node === name && didRun(c.target_node) && !byNode[name].ports.includes(c.source_output || 'main')) byNode[name].ports.push(c.source_output || 'main')
      }
    }
  }

  const ran = [...names].filter(didRun).sort((a, b) => (byNode[a].order ?? 1e9) - (byNode[b].order ?? 1e9) || (byNode[a].startedAt ?? '').localeCompare(byNode[b].startedAt ?? ''))
  const start = Date.parse(execution.start_time)
  const end = execution.end_time ? Date.parse(execution.end_time) : NaN
  return {
    executionId: execution.id,
    status: execution.status,
    finished,
    byNode,
    ran,
    startedAt: execution.start_time,
    endedAt: execution.end_time,
    tookMs: Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : null,
    errors: (execution.errors ?? []).map((e) => ({ node_name: e.node_name, error: e.error })),
    loops: execution.loop_runs && typeof execution.loop_runs === 'object' ? execution.loop_runs : {},
    polls: execution.poll_tries && typeof execution.poll_tries === 'object' ? execution.poll_tries : {},
  }
}

/** Whether a run sent items out of `port` of `node`: true / false, or null without a run (live). */
export function portTaken(path: RunPath | null, node: string, port: string): boolean | null {
  if (!path) return null
  const r = path.byNode[node]
  if (!r || r.status === 'skip' || r.status === 'pending') return false
  return r.ports.includes(port || 'main')
}

/** A step's status on the page: `pending` without a run. */
export function statusOf(path: RunPath | null, node: string): StepStatus {
  if (!path) return 'pending'
  return path.byNode[node]?.status ?? (path.finished ? 'skip' : 'pending')
}

/** 1.2 s, 84 ms, 2h 03m. */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return ''
  if (ms < 1000) return `${Math.round(ms)} ms`
  const s = ms / 1000
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, '0')}s`
  const h = Math.floor(m / 60)
  return `${h}h ${String(m % 60).padStart(2, '0')}m`
}

/** The run's outcome in a word, and its tone. */
export function outcomeOf(path: RunPath): { label: string; tone: 'ok' | 'warn' | 'bad' | 'neutral' } {
  const s = String(path.status).toLowerCase()
  if (s === 'success' || s === 'completed') return { label: 'Succeeded', tone: 'ok' }
  if (s === 'waiting') return { label: 'Waiting on a person', tone: 'warn' }
  if (s === 'running' || s === 'queued' || s === 'new' || s === 'pending') return { label: 'Running', tone: 'neutral' }
  if (s === 'cancelled' || s === 'canceled') return { label: 'Cancelled', tone: 'neutral' }
  return { label: path.errors[0] ? `Failed at ${path.errors[0].node_name}` : 'Failed', tone: 'bad' }
}

/**
 * The two things a project builds, apps and workflows, as one pure model (no React): what state each one is in,
 * read from real API data (the newest runs, the open task counts, the workflow and app lists). The project rail
 * and the Overview both show it; nothing is invented.
 */
import type { RunListItem } from '@/shared/api/types'

export type EntityKind = 'workflow' | 'app'

export interface ProjectEntity {
  kind: EntityKind
  id: string
  name: string
  updatedAt: string
}

export interface EntityRun {
  id: string
  /** ms since epoch */
  at: number
  tone: 'ok' | 'err' | 'running' | 'warn' | 'idle'
  durationMs: number | null
}

/** What a workflow or app is doing right now, from the newest runs and the open tasks. */
export interface EntityStatus {
  running: boolean
  /** the newest run in the last 24h failed */
  failed: boolean
  /** open approval tasks (workflows only) */
  approvals: number
  /** runs of the last 24h, newest first */
  runs24h: EntityRun[]
  /** the newest run at all (any age) */
  last: EntityRun | null
}

export const DAY_MS = 24 * 3600 * 1000

export const entityKey = (kind: EntityKind, id: string) => `${kind}:${id}`

export function toneOf(status: string | null | undefined): EntityRun['tone'] {
  if (status === 'success') return 'ok'
  if (status === 'waiting') return 'warn'
  if (status === 'running') return 'running'
  if (status === 'error' || status === 'failed' || status === 'crashed' || status === 'canceled') return 'err'
  return 'idle'
}

const EMPTY: EntityStatus = { running: false, failed: false, approvals: 0, runs24h: [], last: null }
export const emptyStatus = (): EntityStatus => ({ ...EMPTY, runs24h: [] })

/**
 * Per-entity status from the project's newest runs (`GET /runs?limit=100`) and `GET /tasks/counts`.
 * A workflow owns the runs whose `workflow_id` is its id; an app owns the runs it triggered (`app_id`).
 */
export function entityStatuses(
  entities: ProjectEntity[],
  runs: RunListItem[],
  now: number,
  approvalsByWorkflow: Record<string, number> = {},
): Map<string, EntityStatus> {
  const out = new Map<string, EntityStatus>()
  for (const e of entities) out.set(entityKey(e.kind, e.id), { ...emptyStatus(), approvals: e.kind === 'workflow' ? (approvalsByWorkflow[e.id] ?? 0) : 0 })
  const dayAgo = now - DAY_MS
  // runs arrive newest first; sort defensively so `last` and `failed` read the newest
  const sorted = [...runs].sort((a, b) => (Date.parse(b.start_time) || 0) - (Date.parse(a.start_time) || 0))
  for (const r of sorted) {
    const at = Date.parse(r.start_time) || 0
    const run: EntityRun = { id: r.id, at, tone: toneOf(r.status), durationMs: r.duration_ms }
    const keys = [entityKey('workflow', r.workflow_id)]
    if (r.app_id) keys.push(entityKey('app', r.app_id))
    for (const k of keys) {
      const s = out.get(k)
      if (!s) continue
      if (!s.last) s.last = run
      if (run.tone === 'running') s.running = true
      if (at >= dayAgo) {
        if (s.runs24h.length === 0 && run.tone === 'err') s.failed = true
        s.runs24h.push(run)
      }
    }
  }
  return out
}

/** How loudly an entity asks for attention: failed > running > waiting on a person > ran today > quiet. */
export function urgency(s: EntityStatus | undefined): number {
  if (!s) return 0
  if (s.failed) return 4
  if (s.running) return 3
  if (s.approvals > 0) return 2
  if (s.runs24h.length > 0) return 1
  return 0
}

/** Two letters for an item: the initials of the first and last words (so "qa-proof-3" and "qa-proof-7" differ),
 * or the first two letters of a single word. */
export function initials(name: string): string {
  const words = name
    .replace(/[_\-./]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(0, 2).replace(/^./, (c) => c.toUpperCase())
  return (words[0][0] + words[words.length - 1][0]).toUpperCase()
}

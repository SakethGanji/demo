/**
 * The Overview's pure model ("Pulse"): what needs attention, the order rows take (by status, not by name),
 * and where each run sits on the shared 24-hour clock. Real data only: the project summary, the newest runs,
 * the open task counts and the workflow / app lists.
 */
import { DAY_MS, entityKey, urgency, type ProjectEntity, type EntityStatus } from '@/features/project/lib/entities'

export type AttentionTarget =
  | { kind: 'workflow'; id: string; execution?: string }
  | { kind: 'run'; id: string }
  | { kind: 'approvals' }
  | { kind: 'secrets' }
  | { kind: 'backend' }

export interface AttentionItem {
  key: string
  tone: 'err' | 'warn' | 'running'
  title: string
  sub: string
  action: string
  target: AttentionTarget
}

export function attentionItems({
  entities,
  status,
  openTasks,
  missingSecrets,
  stubRoutes,
  liveEnv,
}: {
  entities: ProjectEntity[]
  status: Map<string, EntityStatus>
  openTasks: number
  missingSecrets: number
  stubRoutes: number
  liveEnv: string
}): AttentionItem[] {
  const out: AttentionItem[] = []
  for (const e of entities) {
    const s = status.get(entityKey(e.kind, e.id))
    if (!s?.failed || !s.last) continue
    const failedToday = s.runs24h.filter((r) => r.tone === 'err').length
    out.push({
      key: `failed:${e.kind}:${e.id}`,
      tone: 'err',
      title: `${e.name} failed`,
      sub: `${failedToday} failed run${failedToday === 1 ? '' : 's'} in the last 24h`,
      action: e.kind === 'workflow' ? 'Open run' : 'See run',
      target: e.kind === 'workflow' ? { kind: 'workflow', id: e.id, execution: s.last.id } : { kind: 'run', id: s.last.id },
    })
  }
  if (openTasks > 0)
    out.push({
      key: 'approvals',
      tone: 'warn',
      title: `${openTasks} approval${openTasks === 1 ? '' : 's'} waiting`,
      sub: 'A workflow is paused until someone decides',
      action: 'Review',
      target: { kind: 'approvals' },
    })
  if (missingSecrets > 0)
    out.push({
      key: 'secrets',
      tone: 'warn',
      title: `${missingSecrets} secret${missingSecrets === 1 ? '' : 's'} missing`,
      sub: `Code reads ${missingSecrets === 1 ? 'a key' : 'keys'} not set in every environment (${liveEnv} included)`,
      action: 'Fix',
      target: { kind: 'secrets' },
    })
  if (stubRoutes > 0)
    out.push({
      key: 'stubs',
      tone: 'warn',
      title: `${stubRoutes} route${stubRoutes === 1 ? ' is a stub' : 's are stubs'}`,
      sub: 'They answer with placeholder data until code backs them',
      action: 'Open backend',
      target: { kind: 'backend' },
    })
  for (const e of entities) {
    const s = status.get(entityKey(e.kind, e.id))
    if (!s?.running || s.failed) continue
    const run = s.runs24h.find((r) => r.tone === 'running') ?? s.last
    out.push({
      key: `running:${e.kind}:${e.id}`,
      tone: 'running',
      title: `${e.name} is running`,
      sub: 'Live now',
      action: 'Watch',
      target: e.kind === 'workflow' ? { kind: 'workflow', id: e.id, execution: run?.id } : { kind: 'run', id: run?.id ?? '' },
    })
  }
  return out
}

/** The one line at the top of the page: what the project needs from you, in words. */
export function headline(items: AttentionItem[], runs24h: number): { lead: string; tail: string } {
  const needs = items.filter((i) => i.tone !== 'running').length
  const live = items.length - needs
  if (needs === 0 && live > 0) return { lead: 'Running.', tail: `${live} live now, nothing needs you.` }
  if (needs === 0) return runs24h > 0 ? { lead: 'All quiet.', tail: 'Everything that ran today succeeded.' } : { lead: 'All quiet.', tail: 'Nothing has run in the last 24 hours.' }
  return { lead: `${needs} thing${needs === 1 ? '' : 's'} need${needs === 1 ? 's' : ''} you.`, tail: live ? `${live} more running.` : '' }
}

/** Rows by status, loudest first; ties by most recent run, then by most recent edit. */
export function byStatus(entities: ProjectEntity[], status: Map<string, EntityStatus>): ProjectEntity[] {
  const last = (e: ProjectEntity) => status.get(entityKey(e.kind, e.id))?.last?.at ?? 0
  return [...entities].sort(
    (a, b) =>
      urgency(status.get(entityKey(b.kind, b.id))) - urgency(status.get(entityKey(a.kind, a.id))) ||
      last(b) - last(a) ||
      b.updatedAt.localeCompare(a.updatedAt),
  )
}

/** Where a moment sits on the 24h clock: 0 = 24 hours ago, 1 = now (clamped). */
export function clockX(at: number, now: number): number {
  return Math.min(1, Math.max(0, 1 - (now - at) / DAY_MS))
}

/** Median of the durations (null when none). */
export function median(xs: number[]): number | null {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

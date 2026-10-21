/**
 * The app chat's stream also carries the builders' live events (LIVE-FILES §5): when an ask changes the backend the
 * chat's backend builder writes modules and routes, and the UI builder saves versions, as `file` / `route` / `preview`
 * events in the same shapes as a build's. `liveEventOf` reads them for the same live reducer (`applyLiveEvent`); an
 * ask that needs a new table, route or role first needs the planner, which the stream says with `plan_revision`.
 */
import type { BuildEvent, FileBuilder, RouteStatus } from '@/features/plan/buildTypes'
import type { PlanDiffResult } from '@/features/plan/planTypes'

export type LiveEvent = Extract<BuildEvent, { type: 'file' | 'route' | 'preview' }>

const BUILDERS = new Set<FileBuilder>(['backend', 'ui', 'plan', 'tables'])
const STATUSES = new Set<RouteStatus>(['stub', 'built', 'passing', 'failing'])
const str = (v: unknown): v is string => typeof v === 'string'

export function liveEventOf(d: Record<string, unknown>): LiveEvent | null {
  const step_key = str(d.step_key) ? d.step_key : null
  if (d.type === 'file') {
    if (!str(d.path) || !BUILDERS.has(d.builder as FileBuilder) || (d.op !== 'write' && d.op !== 'delete')) return null
    return {
      type: 'file',
      step_key,
      builder: d.builder as FileBuilder,
      path: d.path,
      op: d.op,
      content: str(d.content) ? d.content : '',
      done: d.done !== false,
      truncated: d.truncated === true,
    }
  }
  if (d.type === 'route') {
    if (!str(d.method) || !str(d.path) || !STATUSES.has(d.status as RouteStatus)) return null
    return { type: 'route', step_key, method: d.method, path: d.path, name: str(d.name) ? d.name : '', status: d.status as RouteStatus }
  }
  if (d.type === 'preview') {
    if (!str(d.app_id)) return null
    return { type: 'preview', app_id: d.app_id, version_id: str(d.version_id) ? d.version_id : null }
  }
  return null
}

export interface PlanRevision {
  /** the draft version to approve */
  version: number
  diff: PlanDiffResult | null
  reason: string | null
}

export function planRevisionOf(d: Record<string, unknown>): PlanRevision | null {
  if (d.type !== 'plan_revision' || typeof d.version !== 'number') return null
  return { version: d.version, diff: (d.diff as PlanDiffResult | undefined) ?? null, reason: str(d.reason) ? d.reason : null }
}

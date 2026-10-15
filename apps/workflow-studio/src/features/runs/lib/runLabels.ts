/**
 * Labels for run rows (UI-DESIGN §3.6): what ran (method + path or a workflow name), the trigger
 * type and a human source ("app · Orders dashboard", "test · studio", "AI self-test", …).
 */
import type { RunDetail, RunError, RunListItem } from '@/shared/api/types'

export const TRIGGER_TYPES = [
  'route_test',
  'draft_test',
  'generate_self_test',
  'app',
  'app_preview',
  'api_key',
  'public',
  'manual',
  'webhook',
  'cron',
  'retry',
] as const

export const RUN_STATUSES = ['success', 'failed', 'running'] as const

export interface RunTarget {
  method: string | null
  path: string | null
  workflow: string | null
}

export function runTarget(r: RunListItem): RunTarget {
  const t = r.trigger ?? {}
  const method = typeof t.method === 'string' ? t.method : null
  const path = typeof t.path === 'string' ? t.path : typeof t.route === 'string' ? t.route : null
  if (method && path) return { method, path, workflow: null }
  if (r.route) {
    const m = /^([A-Z]+)\s+(\S+)$/.exec(r.route)
    if (m) return { method: m[1], path: m[2], workflow: null }
  }
  return { method: null, path: null, workflow: r.workflow_name || r.workflow_id }
}

export function triggerType(r: RunListItem): string {
  const t = r.trigger?.type
  return typeof t === 'string' && t ? t : r.mode || 'unknown'
}

export function runSource(r: RunListItem, appName: (id: string) => string | undefined): string {
  const t = r.trigger ?? {}
  const app = r.app_id ?? (typeof t.app_id === 'string' ? t.app_id : null)
  const name = app ? appName(app) ?? 'app' : 'app'
  switch (triggerType(r)) {
    case 'app':
      return `app · ${name}`
    case 'app_preview':
      return `preview · ${name}`
    case 'route_test':
      return 'test · studio'
    case 'draft_test':
      return typeof t.module_name === 'string' && t.module_name ? `draft test · ${t.module_name}.py` : 'draft test'
    case 'generate_self_test':
      return 'AI self-test'
    case 'manual':
      return 'workflow · editor'
    case 'webhook':
      return 'webhook'
    case 'cron':
      return 'schedule'
    case 'retry':
      return 'retry'
    case 'api_key':
      return 'API key'
    case 'public':
      return 'public call'
    case 'api':
      return 'router'
    default:
      return r.mode || '—'
  }
}

/** The structured error of a run: `metadata.error` (§9 D16), else the trace error, else the first node error. */
export function runError(d: RunDetail): RunError | null {
  const meta = d.metadata?.error
  if (meta && typeof meta === 'object') return meta
  const tr = (d.trace as { error?: RunError | null } | null)?.error
  if (tr && typeof tr === 'object') return tr
  const e = d.errors?.[0]
  if (e) return { type: e.node_name, message: e.error, code: e.error_code ?? null }
  return null
}

export function isRunning(status: string | null | undefined): boolean {
  return status === 'running' || status === 'waiting'
}

/** `exec_01a1…c41d` */
export function shortId(id: string): string {
  if (id.length <= 18) return id
  const [prefix, rest] = id.includes('_') ? [id.slice(0, id.indexOf('_') + 1), id.slice(id.indexOf('_') + 1)] : ['', id]
  return `${prefix}${rest.slice(0, 4)}…${rest.slice(-4)}`
}

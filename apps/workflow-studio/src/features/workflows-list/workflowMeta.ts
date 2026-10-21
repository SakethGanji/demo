/**
 * Workflow facts shared by the Overview story (the list page that first held them is gone: workflows live on the
 * project's Overview now). The summary shape with the routes a workflow handles, and its trigger as a short label.
 */
import type { ApiWorkflowDetail, ApiWorkflowSummary } from '@/shared/lib/backendTypes'

export type HandledRoute = { route_id: string; method: string; path: string }
export type WorkflowSummary = ApiWorkflowSummary & { handler_for?: HandledRoute[]; description?: string | null }

const TRIGGER_TYPES = new Set(['Start', 'Webhook', 'Cron', 'ErrorTrigger', 'ExecuteWorkflowTrigger'])

/** "Manual", "Webhook", "Cron · 02:00" from the workflow's trigger node. */
export function triggerLabel(def: ApiWorkflowDetail['definition'] | undefined, handlesRoute: boolean): string {
  const t = def?.nodes.find((n) => TRIGGER_TYPES.has(n.type))
  if (!t) return handlesRoute ? 'Route' : '—'
  if (t.type === 'Cron') {
    const p = t.parameters ?? {}
    if (p.mode === 'cron') return `Cron · ${cronLabel(String(p.cronExpression ?? '0 * * * *'))}`
    const s = Number(p.interval ?? 60)
    return `Cron · every ${s % 3600 === 0 ? `${s / 3600}h` : s % 60 === 0 ? `${s / 60}m` : `${s}s`}`
  }
  if (t.type === 'Webhook') return 'Webhook'
  if (t.type === 'ErrorTrigger') return 'On error'
  if (t.type === 'ExecuteWorkflowTrigger') return 'Sub-workflow'
  return handlesRoute ? 'Route' : 'Manual'
}

function cronLabel(expr: string): string {
  const m = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec(expr.trim())
  return m ? `${m[2].padStart(2, '0')}:${m[1].padStart(2, '0')}` : expr
}

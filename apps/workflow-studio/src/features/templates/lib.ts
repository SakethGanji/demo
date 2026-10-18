/** Pure helpers for the templates screens (kept free of React so they are unit-testable). */
import type { Edge, Node } from '@xyflow/react'
import { ApiError } from '@/shared/api/http'
import type { WorkflowNodeData } from '@/features/workflow-editor/types/workflow'
import type { Need, TemplateSummary, TestSummary, WorkflowPreview } from './types'

export function categoriesOf(list: TemplateSummary[]): string[] {
  return [...new Set(list.map((t) => t.category))].sort((a, b) => a.localeCompare(b))
}

/** Client-side filter: the list is small and read-only (the engine also supports ?category= and ?q=). */
export function filterTemplates(list: TemplateSummary[], category: string, q: string): TemplateSummary[] {
  const needle = q.trim().toLowerCase()
  return list.filter((t) => {
    if (category && category !== 'all' && t.category !== category) return false
    if (!needle) return true
    return [t.slug, t.name, t.summary, ...t.tags].join(' ').toLowerCase().includes(needle)
  })
}

export function isInstalled(t: TemplateSummary, pid: string): boolean {
  return (t.installed_in ?? []).includes(pid)
}

type NodeGroupName = NonNullable<WorkflowNodeData['group']>

const TRIGGERS = new Set(['Start', 'Webhook', 'Cron', 'ErrorTrigger', 'ExecuteWorkflowTrigger', 'ChatInput', 'FormTrigger'])
const FLOW = new Set(['If', 'Switch', 'Merge', 'Loop', 'Wait', 'ExecuteWorkflow', 'SplitInBatches', 'Filter'])
const OUTPUT = new Set(['Respond', 'RespondToWebhook', 'Approval', 'Output'])

export function groupOf(type: string): NodeGroupName {
  if (TRIGGERS.has(type)) return 'trigger'
  if (FLOW.has(type)) return 'flow'
  if (OUTPUT.has(type)) return 'output'
  if (/^(AI|LLM|Agent)/.test(type)) return 'ai'
  if (/^(Http|Table|Data|Send|Email|Slack|Write)/.test(type)) return 'action'
  return 'transform'
}

/** Where a node goes when the bundle gave it no position: columns by distance from the start. */
function autoLayout(p: WorkflowPreview): Map<string, { x: number; y: number }> {
  const names = p.nodes.map((n) => n.name)
  const incoming = new Map<string, string[]>(names.map((n) => [n, []]))
  for (const c of p.connections) incoming.get(c.target_node)?.push(c.source_node)
  const depth = new Map<string, number>()
  const depthOf = (n: string, seen: Set<string>): number => {
    if (depth.has(n)) return depth.get(n)!
    if (seen.has(n)) return 0
    seen.add(n)
    const d = Math.max(-1, ...(incoming.get(n) ?? []).map((s) => depthOf(s, seen))) + 1
    depth.set(n, d)
    return d
  }
  const rows = new Map<number, number>()
  const out = new Map<string, { x: number; y: number }>()
  for (const n of names) {
    const d = depthOf(n, new Set())
    const r = rows.get(d) ?? 0
    rows.set(d, r + 1)
    out.set(n, { x: d * 220, y: r * 110 })
  }
  return out
}

/** Preview nodes and connections as the xyflow nodes/edges `WorkflowSVG` draws. */
export function previewToFlow(p: WorkflowPreview): { nodes: Node<WorkflowNodeData>[]; edges: Edge[] } {
  const auto = autoLayout(p)
  const placed = p.nodes.every((n) => n.position && Number.isFinite(n.position.x) && Number.isFinite(n.position.y))
  const nodes: Node<WorkflowNodeData>[] = p.nodes.map((n) => ({
    id: n.name,
    type: 'workflow',
    position: placed ? { x: n.position!.x, y: n.position!.y } : auto.get(n.name)!,
    data: {
      name: n.name,
      type: n.type,
      label: n.label || n.name,
      group: groupOf(n.type),
      inputCount: groupOf(n.type) === 'trigger' ? 0 : 1,
      outputCount: 1,
    },
  }))
  const edges: Edge[] = p.connections.map((c, i) => ({ id: `e${i}`, source: c.source_node, target: c.target_node }))
  return { nodes, edges }
}

/** Needs, secrets first (they block; variables only warn). */
export function sortNeeds(needs: Need[]): Need[] {
  return [...needs].sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'secret' ? -1 : 1))
}

export function testsTotals(tests: Record<string, TestSummary> | undefined): { passed: number; failed: number; total: number } {
  const all = Object.values(tests ?? {})
  return {
    passed: all.reduce((n, t) => n + t.passed, 0),
    failed: all.reduce((n, t) => n + t.failed, 0),
    total: all.reduce((n, t) => n + t.total, 0),
  }
}

export interface InstallProblem {
  code: string | null
  message: string
  /** extra lines under the message: clashes, failing tests, findings */
  lines: string[]
}

/** One readable problem for any install / preview failure (§8.3 error list). */
export function installProblem(e: unknown): InstallProblem {
  if (!(e instanceof ApiError)) return { code: null, message: e instanceof Error ? e.message : String(e), lines: [] }
  const d = e.details as Record<string, unknown>
  const lines: string[] = []
  switch (e.code) {
    case 'TEMPLATE_NAME_CLASH':
      for (const c of (d.clashes as Array<Record<string, unknown>> | undefined) ?? []) {
        lines.push(`${String(c.kind ?? 'name')} "${String(c.name ?? c.from ?? '')}" already exists`)
      }
      return { code: e.code, message: 'Some names are already taken in this project. Switch to rename, or remove the clash.', lines }
    case 'TEMPLATE_VERSION_INSTALLED':
      return { code: e.code, message: `A different version${d.installed_version ? ` (${String(d.installed_version)})` : ''} is already installed. Install another copy instead.`, lines }
    case 'TEMPLATE_DEPENDENCY_CONFLICT':
      return { code: e.code, message: e.message, lines }
    case 'TEMPLATE_INSTALL_CONFLICT':
      return { code: e.code, message: 'Another install is in progress. Try again in a moment.', lines }
    case 'TEMPLATE_INSTALL_FAILED': {
      const stage = typeof d.stage === 'string' ? d.stage : null
      for (const f of (d.findings as Array<Record<string, unknown>> | undefined) ?? []) lines.push(`${String(f.code ?? '')} ${String(f.message ?? '')}`.trim())
      for (const f of (d.test_failures as Array<Record<string, unknown>> | undefined) ?? []) lines.push(`Test failed: ${String(f.name ?? f.test_id ?? '')}`)
      return { code: e.code, message: `${e.message}${stage ? ` (${stage})` : ''}. Nothing was installed.`, lines }
    }
    case 'TEMPLATE_NOT_FOUND':
    case 'TEMPLATE_VERSION_NOT_FOUND':
      return { code: e.code, message: e.message, lines }
    default:
      return { code: e.code, message: e.message, lines }
  }
}

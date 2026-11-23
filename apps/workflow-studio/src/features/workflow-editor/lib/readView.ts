/**
 * Read view: the workflow as a top-to-bottom flowchart for people who read workflows rather than
 * edit them, most of them AI-built. Each box says what the step is about (who decides and by when, which
 * system, which database), each arrow says which way a decision went ("approved", "no answer", "yes").
 * Pure: canvas nodes/edges in, laid-out read cards and routed arrows out; the component draws them in studio style.
 */
import type { Edge, Node } from '@xyflow/react'
import type { WorkflowNodeData } from '../types/workflow'
import dagre from '@dagrejs/dagre'

/** What kind of step a box is, for the summary line. */
export type ReadKind = 'trigger' | 'person' | 'email' | 'database' | 'api' | 'wait' | 'reply' | 'loop' | 'decision' | 'ai' | 'step'

const TRIGGERS = new Set(['Start', 'Webhook', 'Cron', 'FormTrigger', 'ChatInput', 'ErrorTrigger', 'ExecuteWorkflowTrigger', 'Poll'])
const DECISIONS = new Set(['If', 'Switch', 'Filter'])
const AI = new Set(['AIAgent', 'LLMChat'])

export function readKind(type: string): ReadKind {
  if (TRIGGERS.has(type)) return 'trigger'
  if (type === 'Approval') return 'person'
  if (type === 'SendEmail') return 'email'
  if (type === 'Postgres' || type === 'DataTable') return 'database'
  if (type === 'HttpRequest' || type === 'ExecuteWorkflow') return 'api'
  if (type === 'Wait') return 'wait'
  if (type === 'Respond' || type === 'RespondToWebhook') return 'reply'
  if (type === 'Loop' || type === 'SplitInBatches') return 'loop'
  if (DECISIONS.has(type)) return 'decision'
  if (AI.has(type)) return 'ai'
  return 'step'
}

const str = (v: unknown) => (typeof v === 'string' ? v : '')

/** `{{ $vars.CREDIT_COMMITTEE_EMAIL }}` → "Credit committee"; a literal address stays; anything else → "". */
export function personOf(v: unknown): string {
  const text = typeof v === 'string' ? v : Array.isArray(v) ? v.map(String).join(' ') : ''
  const m = text.match(/\$vars\.([A-Z0-9_]+)/)
  if (m) {
    const words = m[1].replace(/_EMAIL$|_ADDRESS$/, '').toLowerCase().split('_').filter(Boolean)
    const s = words.join(' ')
    return s.charAt(0).toUpperCase() + s.slice(1)
  }
  if (/^[^\s{}]+@[^\s{}]+$/.test(text.trim())) return text.trim()
  if (/manager/i.test(text)) return 'Manager'
  return ''
}

const UNIT: Record<string, string> = { seconds: 's', minutes: 'min', hours: 'h', days: 'd' }

/** The one-line detail under a step's name: the facts a reader needs without opening it. */
export function readDetail(type: string, params: Record<string, unknown> = {}): string {
  const p = params
  switch (type) {
    case 'Approval': {
      const bits = [personOf(p.notify_email) || personOf(p.assignees)]
      if (str(p.due_in)) bits.push(`within ${str(p.due_in)}`)
      if (p.four_eyes) bits.push('four eyes')
      if (p.on_timeout === 'reject') bits.push('silence = no')
      if (p.on_timeout === 'approve') bits.push('silence = yes')
      return bits.filter(Boolean).join(' · ')
    }
    case 'Wait': {
      const n = p.duration ?? p.amount
      return n != null ? `${n}${UNIT[str(p.unit)] ?? ` ${str(p.unit)}`}` : ''
    }
    case 'SendEmail':
      return personOf(p.toEmail) ? `to ${personOf(p.toEmail)}` : ''
    case 'HttpRequest': {
      const ref = p.api_ref as { operation_id?: string } | undefined
      if (ref?.operation_id) return ref.operation_id
      return [str(p.method), str(p.url).replace(/^https?:\/\//, '').slice(0, 40)].filter(Boolean).join(' ')
    }
    case 'Postgres': {
      const conn = str(p.connection)
      const write = p.operation === 'transaction' || /\b(insert|update|delete)\b/i.test(str(p.query))
      return [conn, write ? 'write' : 'read'].filter(Boolean).join(' · ')
    }
    case 'DataTable':
      return [str(p.table) || str(p.tableName), str(p.operation)].filter(Boolean).join(' · ')
    case 'Loop':
    case 'SplitInBatches':
      return Number(p.maxIterations) > 0 ? `up to ${p.maxIterations} times` : 'each item'
    case 'Respond':
    case 'RespondToWebhook':
      return p.statusCode != null ? `status ${p.statusCode}` : ''
    case 'Cron':
      return str(p.cronExpression) ? 'on a schedule' : ''
    case 'ExecuteWorkflow':
      return 'runs another workflow'
    default:
      return ''
  }
}

/** Arrow label for an output port; '' for the plain main output. */
export function portLabel(handle: string | null | undefined, node?: WorkflowNodeData): string {
  const h = handle || 'main'
  switch (h) {
    case 'main': case 'output': return ''
    case 'approved': return 'approved'
    case 'rejected': return 'rejected'
    case 'timed_out': return 'no answer'
    case 'true': return 'yes'
    case 'false': return 'no'
    case 'loop': return 'each'
    case 'done': return 'finished'
    case 'error': return 'on error'
    case 'fallback': return 'otherwise'
  }
  const m = h.match(/^output(\d+)$/)
  if (m) {
    const i = Number(m[1])
    type Rule = { output_name?: string; outputKey?: string; output?: number; operation?: string; value?: unknown }
    const rules = (node?.parameters as { rules?: Rule[] } | undefined)?.rules
    const rule = Array.isArray(rules) ? rules.find((r, j) => (r?.output ?? j) === i) : undefined
    if (rule?.output_name || rule?.outputKey) return (rule.output_name || rule.outputKey) as string
    // A rule's value is what a reader recognises ("yes", "fraud"); a non-equality test keeps its operation.
    const value = rule?.value
    if (value != null && value !== '' && typeof value !== 'object') {
      const op = rule?.operation ?? 'equals'
      return op === 'equals' || op === 'eq' ? String(value) : `${op.replace(/_/g, ' ')} ${String(value)}`
    }
    return `case ${i + 1}`
  }
  return h.replace(/_/g, ' ')
}

/** One step in the read view: what the card shows. */
export interface ReadNodeData extends Record<string, unknown> {
  name: string
  type: string
  icon?: string
  group?: string
  kind: ReadKind
  detail: string
  disabled?: boolean
}

/** One arrow: the route the layout chose and the words on it. */
export interface ReadEdgeData extends Record<string, unknown> {
  points: { x: number; y: number }[]
  label: string
  labelAt?: { x: number; y: number }
}

export interface ReadLayout {
  nodes: Node<ReadNodeData>[]
  edges: Edge<ReadEdgeData>[]
  /** How many steps of each kind, for the summary. */
  counts: Partial<Record<ReadKind, number>>
}

export const READ_NODE = { width: 248, height: 60 }

/** Top-to-bottom layout of the real steps (sticky notes and the like are left out); arrows routed around cards. */
export function layoutRead(nodes: Node[], edges: Edge[]): ReadLayout {
  const steps = nodes.filter((n) => n.type === 'workflowNode') as Node<WorkflowNodeData>[]
  const byId = new Map(steps.map((n) => [n.id, n.data]))
  const g = new dagre.graphlib.Graph({ multigraph: true })
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 64, edgesep: 20, marginx: 0, marginy: 0 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of steps) g.setNode(n.id, { ...READ_NODE })

  const kept: { e: Edge; label: string }[] = []
  for (const e of edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) continue
    const label = portLabel(e.sourceHandle, byId.get(e.source))
    kept.push({ e, label })
    // A labelled arrow reserves room for its words, so the layout keeps them off cards and other labels.
    g.setEdge({ v: e.source, w: e.target, name: e.id }, label ? { width: label.length * 6 + 18, height: 20, labelpos: 'c' } : {})
  }
  dagre.layout(g)

  const counts: Partial<Record<ReadKind, number>> = {}
  const outNodes = steps.map((n): Node<ReadNodeData> => {
    const { x, y } = g.node(n.id)
    const kind = readKind(n.data.type)
    counts[kind] = (counts[kind] ?? 0) + 1
    return {
      id: n.id,
      type: 'readNode',
      position: { x: x - READ_NODE.width / 2, y: y - READ_NODE.height / 2 },
      data: {
        name: n.data.label || n.data.name,
        type: n.data.type,
        icon: n.data.icon,
        group: n.data.group,
        kind,
        detail: readDetail(n.data.type, n.data.parameters ?? {}),
        disabled: n.data.disabled,
      },
    }
  })
  const outEdges = kept.map(({ e, label }): Edge<ReadEdgeData> => {
    const laid = g.edge({ v: e.source, w: e.target, name: e.id }) as { points?: { x: number; y: number }[]; x?: number; y?: number }
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'readEdge',
      data: { points: laid.points ?? [], label, labelAt: label && laid.x != null && laid.y != null ? { x: laid.x, y: laid.y } : undefined },
    }
  })
  return { nodes: outNodes, edges: outEdges, counts }
}

const SUMMARY: [ReadKind, string, string][] = [
  ['person', 'approval', 'approvals'],
  ['email', 'email', 'emails'],
  ['database', 'database step', 'database steps'],
  ['api', 'system call', 'system calls'],
  ['wait', 'wait', 'waits'],
  ['loop', 'repeat', 'repeats'],
  ['decision', 'check', 'checks'],
  ['ai', 'AI step', 'AI steps'],
]

/** "5 approvals", "6 emails", … for the kinds present, in reading order. */
export function readSummary(counts: Partial<Record<ReadKind, number>>): string[] {
  return SUMMARY.filter(([k]) => counts[k]).map(([k, one, many]) => `${counts[k]} ${counts[k] === 1 ? one : many}`)
}

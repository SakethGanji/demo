/**
 * Draft edits made from the glass read side (Page tokens, + Add step, Schedule). They go through workflowStore
 * like a canvas edit, so undo, dirty state and Save work as before. Nodes are addressed by NAME.
 */
import type { Node } from '@xyflow/react'
import { useWorkflowStore } from '../../stores/workflowStore'
import { createWorkflowNodeData } from '../../lib/createNodeData'
import { generateNodeName, getExistingNodeNames } from '../../lib/workflowTransform'
import type { WorkflowNodeData } from '../../types/workflow'
import { draftText, draftType, type StepDraft } from '../page/AddStepMenu'
import { isTriggerNode } from '../lib/pageSentences'

type WNode = Node<WorkflowNodeData>

const wnodes = () => useWorkflowStore.getState().nodes.filter((n) => n.type === 'workflowNode') as WNode[]
export const nodeByName = (name: string) => wnodes().find((n) => n.data.name === name)

/** Set `path` (`parameters.url`, `parameters.fields.0.value`, `notes`) on a node; numbers stay numbers. */
export function setNodeValue(name: string, path: string, value: string) {
  const node = nodeByName(name)
  if (!node) return
  const st = useWorkflowStore.getState()
  if (path === 'notes') {
    st.updateNodeData(node.id, { notes: value })
    return useWorkflowStore.setState({ _isDirty: true })
  }
  // the step's failure settings ("If this fails"): top-level engine fields, not parameters
  if (path === 'retry_on_fail' || path === 'retry_delay' || path === 'on_error') {
    const n = Math.max(0, Math.floor(Number(value) || 0))
    const patch: Partial<WorkflowNodeData> =
      path === 'retry_on_fail' ? { retryOnFail: Math.min(10, n) } : path === 'retry_delay' ? { retryDelay: n || 1000 } : { onError: value as WorkflowNodeData['onError'] }
    st.updateNodeData(node.id, patch)
    return useWorkflowStore.setState({ _isDirty: true })
  }
  const keys = path.replace(/^parameters\.?/, '').split('.').filter(Boolean)
  if (!keys.length) return
  const params = structuredClone(node.data.parameters ?? {}) as Record<string, unknown>
  let cur: Record<string, unknown> = params
  for (const k of keys.slice(0, -1)) {
    if (cur[k] === null || typeof cur[k] !== 'object') cur[k] = /^\d+$/.test(k) ? [] : {}
    cur = cur[k] as Record<string, unknown>
  }
  const last = keys[keys.length - 1]
  const old = cur[last]
  cur[last] = typeof old === 'number' && value.trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : value
  st.updateNodeData(node.id, { parameters: params })
  // updateNodeData leaves the dirty flag alone (the old panel set it elsewhere); a token edit is an edit.
  useWorkflowStore.setState({ _isDirty: true })
}

function newNode(type: string, params: Record<string, unknown>, position: { x: number; y: number }, notes?: string): WNode {
  const st = useWorkflowStore.getState()
  const meta = st.nodeTypesMap.get(type) ?? { type }
  const name = generateNodeName(type, getExistingNodeNames(st.nodes as WNode[]))
  const data = createWorkflowNodeData(meta, { name, parameters: params })
  if (notes) data.notes = notes
  return { id: `node-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, type: 'workflowNode', position, data }
}

/** + Add step → Add to workflow: a real node after `after` (or unconnected). Returns its name. */
export function addDraftStep(draft: StepDraft, after: string | null): string {
  const t = draftType(draft.kind)
  const st = useWorkflowStore.getState()
  const src = after ? nodeByName(after) : undefined
  const pos = src ? { x: src.position.x + 260, y: src.position.y + 40 } : { x: 120 + wnodes().length * 40, y: 320 }
  const node = newNode(t.node, t.params(draft.values), pos, draftText(draft))
  st.addNode(node)
  if (src) {
    const handle = src.data.outputs?.[0]?.name ?? 'main'
    st.onConnect({ source: src.id, target: node.id, sourceHandle: handle, targetHandle: null })
  }
  st.setSelectedNode(node.id)
  return node.data.name
}

export function cronNode(): WNode | undefined {
  return wnodes().find((n) => n.data.type === 'Cron')
}

/**
 * Schedule: set the Cron trigger's expression; add a Cron next to the primary trigger (feeding the same first
 * steps) when there is none; `null` removes the schedule ("Webhook only").
 */
export function setSchedule(expr: string | null) {
  const st = useWorkflowStore.getState()
  const cron = cronNode()
  if (expr === null) {
    if (cron) st.deleteNode(cron.id)
    return
  }
  if (cron) {
    st.updateNodeData(cron.id, { parameters: { ...(cron.data.parameters ?? {}), mode: 'cron', cronExpression: expr } })
    return
  }
  const primary = wnodes().find((n) => isTriggerNode(n.data.type) && n.data.type !== 'ErrorTrigger')
  const pos = primary ? { x: primary.position.x, y: primary.position.y - 140 } : { x: 80, y: 120 }
  const node = newNode('Cron', { mode: 'cron', cronExpression: expr }, pos)
  st.addNode(node)
  if (primary) {
    const targets = st.edges.filter((e) => e.source === primary.id).map((e) => e.target)
    for (const t of new Set(targets)) st.onConnect({ source: node.id, target: t, sourceHandle: 'main', targetHandle: null })
  }
}

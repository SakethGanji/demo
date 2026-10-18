/**
 * The proposal's definition is not enriched by the server the way a saved workflow is: its nodes carry no icon, group or
 * I/O. A saved draft gets all of that from the node-type catalogue, so a stage node built from the bare definition
 * would show another icon and colour than the real node that replaces it at Keep. This fills the same fields from the
 * same catalogue (`workflowStore.nodeTypesMap`), so a stage node and its draft twin render identically.
 */
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore'
import type { ProposedDefinition } from '../types'

type DefNode = ProposedDefinition['nodes'][number] & Record<string, unknown>

export function enrichDefNode<T extends { type: string }>(node: T): T {
  const meta = useWorkflowStore.getState().nodeTypesMap.get(node.type)
  if (!meta) return node
  const n = node as T & Record<string, unknown>
  return {
    ...node,
    icon: n.icon ?? meta.icon,
    group: n.group ?? meta.group,
    inputs: n.inputs ?? meta.inputs,
    outputs: n.outputs ?? meta.outputs,
    inputCount: n.inputCount ?? meta.inputCount,
    outputCount: n.outputCount ?? meta.outputCount,
    outputStrategy: n.outputStrategy ?? meta.outputStrategy,
  }
}

export function enrichDefinition(def: ProposedDefinition): ProposedDefinition {
  return { ...def, nodes: def.nodes.map((n) => enrichDefNode(n as DefNode)) } as ProposedDefinition
}

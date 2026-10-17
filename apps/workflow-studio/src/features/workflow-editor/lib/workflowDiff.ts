/**
 * Pure diff of two workflow definitions, by node name (Stage 1 contract §5).
 * "changed" = parameters, type, disabled, on_error or label differ. Position-only changes are ignored.
 */
import type { WorkflowDefinition } from '@/features/projects/hooks/useWorkflows'

/** Optional fields the backend returns beyond the listing-page `WorkflowDefinition` shape. */
type DiffNode = WorkflowDefinition['nodes'][number] & {
  label?: string | null
  disabled?: boolean
  on_error?: string
  onError?: string
  continue_on_fail?: boolean
}

export interface WorkflowDiff {
  added: string[]
  removed: string[]
  changed: string[]
}

function stable(value: unknown): string {
  return JSON.stringify(value, (_k, v) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  )
}

/** Comparable view of a node: everything except position. Legacy continue_on_fail maps to on_error. */
function fingerprint(n: DiffNode): string {
  const onError = n.on_error ?? n.onError ?? (n.continue_on_fail ? 'continue' : 'stop')
  return stable({
    type: n.type,
    parameters: n.parameters ?? {},
    disabled: !!n.disabled,
    onError,
    label: n.label ?? null,
  })
}

export function diffWorkflows(base: WorkflowDefinition, target: WorkflowDefinition): WorkflowDiff {
  const b = new Map((base.nodes as DiffNode[]).map((n) => [n.name, n]))
  const t = new Map((target.nodes as DiffNode[]).map((n) => [n.name, n]))
  const added: string[] = []
  const removed: string[] = []
  const changed: string[] = []
  for (const [name, node] of t) {
    const prev = b.get(name)
    if (!prev) added.push(name)
    else if (fingerprint(prev) !== fingerprint(node)) changed.push(name)
  }
  for (const name of b.keys()) if (!t.has(name)) removed.push(name)
  return { added, removed, changed }
}

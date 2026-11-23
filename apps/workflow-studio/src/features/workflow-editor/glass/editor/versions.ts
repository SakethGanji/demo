/** Version data the draft bar, the changes popover and the versions popover share (one query each). */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { workflowsApi } from '@/shared/lib/api'
import { useWorkflowStore } from '../../stores/workflowStore'
import { diffWorkflows, type WorkflowDiff } from '../../lib/workflowDiff'
import { backendToDefinition, canvasToDefinition } from '../../lib/versionDiffView'

export function useVersions() {
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const q = useQuery({
    queryKey: ['workflow-versions', workflowId],
    queryFn: () => workflowsApi.listVersions(workflowId!),
    enabled: !!workflowId && workflowId !== 'new',
    staleTime: 5_000,
  })
  const sorted = useMemo(() => [...(q.data ?? [])].sort((a, b) => b.version_number - a.version_number), [q.data])
  return { ...q, versions: sorted, latest: sorted[0] ?? null }
}

export function useVersionDetail(versionId: number | null | undefined) {
  const workflowId = useWorkflowStore((s) => s.workflowId)
  return useQuery({
    queryKey: ['workflow-version', workflowId, versionId],
    queryFn: () => workflowsApi.getVersion(workflowId!, versionId!),
    enabled: !!workflowId && versionId != null,
    staleTime: 60_000,
  })
}

export interface DraftChanges {
  diff: WorkflowDiff
  total: number
  /** No published version yet: everything on the canvas is new. */
  unpublished: boolean
  loading: boolean
}

/** What the draft on the canvas changes against the latest published version. */
export function useDraftChanges(): DraftChanges {
  const { latest, isPending, data } = useVersions()
  const detail = useVersionDetail(latest?.id)
  const nodes = useWorkflowStore((s) => s.nodes)
  const edges = useWorkflowStore((s) => s.edges)
  return useMemo(() => {
    if (!latest) {
      const target = canvasToDefinition(nodes, edges)
      const added = target.nodes.map((n) => n.name)
      return { diff: { added, removed: [], changed: [] }, total: added.length, unpublished: !!data, loading: isPending }
    }
    if (!detail.data) return { diff: { added: [], removed: [], changed: [] }, total: 0, unpublished: false, loading: true }
    const diff = diffDraft(detail.data.definition, nodes, edges)
    return { diff, total: diff.added.length + diff.removed.length + diff.changed.length, unpublished: false, loading: false }
  }, [latest, detail.data, nodes, edges, isPending, data])
}

type Def = Parameters<typeof backendToDefinition>[0]
type CanvasNodes = Parameters<typeof canvasToDefinition>[0]
type CanvasEdges = Parameters<typeof canvasToDefinition>[1]

/**
 * The draft on the canvas against a published definition, by step name. Unlike the shared converters it also
 * compares label / disabled / on-error (a rename is a change), and a rewired connection changes its target.
 */
export function diffDraft(definition: Def, nodes: CanvasNodes, edges: CanvasEdges): WorkflowDiff {
  const target = canvasToDefinition(nodes, edges)
  // the shared converters drop label / disabled / on-error; carry them so a rename counts as a change
  const raw = definition as unknown as { nodes: Array<{ name: string; label?: string | null; disabled?: boolean; on_error?: string; continue_on_fail?: boolean }> }
  const base = backendToDefinition(definition)
  base.nodes = base.nodes.map((n) => {
    const r = raw.nodes.find((x) => x.name === n.name)
    return { ...n, label: r?.label || n.name, disabled: !!r?.disabled, on_error: r?.on_error ?? (r?.continue_on_fail ? 'continue' : 'stop') } as typeof n
  })
  target.nodes = target.nodes.map((n) => {
    const d = nodes.find((x) => x.type === 'workflowNode' && (x.data as { name?: string }).name === n.name)?.data as { label?: string; disabled?: boolean; onError?: string } | undefined
    return { ...n, label: d?.label || n.name, disabled: !!d?.disabled, on_error: d?.onError ?? 'stop' } as typeof n
  })
  const diff = diffWorkflows(base, target)
  // a rewired connection counts as a change of its target step
  const key = (c: { sourceNode: string; targetNode: string; sourceOutput?: string; targetInput?: string }) => `${c.sourceNode}>${c.sourceOutput}>${c.targetNode}>${c.targetInput}`
  const was = new Set(base.connections.map(key))
  const now = new Set(target.connections.map(key))
  const touched = new Set(diff.changed)
  const skip = new Set([...diff.added, ...diff.removed])
  for (const c of [...target.connections.filter((c) => !was.has(key(c))), ...base.connections.filter((c) => !now.has(key(c)))]) {
    if (!skip.has(c.targetNode) && target.nodes.some((n) => n.name === c.targetNode)) touched.add(c.targetNode)
  }
  return { ...diff, changed: [...touched] }
}

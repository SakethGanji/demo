/**
 * Sub-workflow edges of a release snapshot (DESIGN-STAGE8-REUSE §12.8): which child version each parent call is pinned to.
 * Edges come from `GET /releases/{id}` (`snapshot.subworkflows`); a legacy release has none and says so.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { releasesApi } from '@/shared/api/endpoints'
import { Badge } from '@/shared/components/ui/badge'
import { Callout } from '@/shared/components/ui/surface'
import { edgesOf } from './checks'

export function SubworkflowEdges({ releaseId }: { releaseId: string }) {
  const { pid } = useProjectCtx()
  const rel = useQuery({ queryKey: ['project', pid, 'releases', 'detail', releaseId], queryFn: () => releasesApi.get(pid, releaseId) })
  const wfs = useProjectWorkflows(pid)
  const names = useMemo(() => new Map((wfs.data ?? []).map((w) => [w.id, w.name])), [wfs.data])
  if (!rel.data) return null
  const edges = edgesOf(rel.data)
  const pinned = (rel.data as unknown as { pins_subworkflows?: boolean }).pins_subworkflows !== false
  if (!pinned) {
    return (
      <Callout tone="warn" data-testid="release-subwf-edges">
        Sub-workflows are not pinned in this release: calls resolve to the latest published version.
      </Callout>
    )
  }
  if (edges.length === 0) return null
  const name = (id: string) => names.get(id) ?? id
  return (
    <div className="flex flex-col gap-1" data-testid="release-subwf-edges">
      <div className="text-xs font-medium text-muted-foreground">Sub-workflow calls</div>
      {edges.map((e, i) => (
        <div key={i} className="flex items-center gap-2 rounded-ctl bg-recess px-2.5 py-1.5 text-[12.5px]" data-testid={`release-subwf-edge-${i}`}>
          <span className="min-w-0 truncate">
            {name(e.parent_workflow_id)} <span className="text-muted-foreground">{e.node}</span> → {name(e.child_workflow_id)}
          </span>
          <div className="flex-1" />
          {e.error ? (
            <Badge variant="destructive">{e.error}</Badge>
          ) : e.cycle ? (
            <Badge variant="destructive">cycle</Badge>
          ) : (
            <Badge variant="neutral">{e.child_version_number != null ? `v${e.child_version_number}` : 'draft'}</Badge>
          )}
        </div>
      ))}
    </div>
  )
}

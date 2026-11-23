/**
 * The graph as a list: the canvas's keyboard and screen-reader twin. Same search, same kind filters, every node a
 * button that opens the detail panel. Grouped by kind, hubs first.
 */
import { useMemo } from 'react'
import { Badge } from '@/shared/components/ui/badge'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { WellList, WellRow } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { searchNodes, type GraphModel } from '../lib/graph'
import { KIND_ORDER, KIND_STYLE, kindGroup, kindLabel, type KindGroup } from '../lib/kinds'
import { KindSwatch } from './KindSwatch'
import { useKnowledgeScope } from '../hooks/useKnowledgeScope'
import { ProjectChip } from './KnowledgeScope'

export function NodeListView({
  model, visibleIds, query, selected, onPick,
}: {
  model: GraphModel
  visibleIds: ReadonlySet<string>
  query: string
  selected: string | null
  onPick: (id: string) => void
}) {
  const { team } = useKnowledgeScope()
  const groups = useMemo(() => {
    const ids = query.trim() ? searchNodes(model, query, visibleIds) : [...visibleIds]
    const byKind = new Map<KindGroup, string[]>()
    for (const id of ids) {
      const n = model.byId.get(id)
      if (!n) continue
      const g = kindGroup(n.kind)
      byKind.set(g, [...(byKind.get(g) ?? []), id])
    }
    if (!query.trim()) {
      for (const list of byKind.values()) {
        list.sort((a, b) => (model.degree.get(b) ?? 0) - (model.degree.get(a) ?? 0) || model.byId.get(a)!.label.localeCompare(model.byId.get(b)!.label))
      }
    }
    return KIND_ORDER.filter((k) => byKind.has(k)).map((k) => [k, byKind.get(k)!] as const)
  }, [model, visibleIds, query])

  if (!groups.length)
    return <EmptyState compact title="Nothing matches" data-testid="knowledge-list-empty">Try another word, or show more kinds.</EmptyState>

  return (
    <div className="flex flex-col gap-5" data-testid="knowledge-list">
      {groups.map(([k, ids]) => (
        <section key={k} className="flex flex-col gap-2" aria-label={KIND_STYLE[k].plural}>
          <h2 className="flex items-center gap-2 text-[12.5px] font-semibold">
            <KindSwatch kind={k} size={11} /> {KIND_STYLE[k].plural}
            <span className="font-normal tabular-nums text-muted-foreground">{ids.length}</span>
          </h2>
          <WellList>
            {ids.map((id) => {
              const n = model.byId.get(id)!
              const on = selected === id
              return (
                <WellRow key={id} className="p-0">
                  <button
                    type="button"
                    onClick={() => onPick(id)}
                    aria-pressed={on}
                    className={cn(
                      'flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus',
                      on && 'bg-(--hover-fill)',
                    )}
                    data-testid={`knowledge-list-row-${id}`}
                  >
                    <KindSwatch kind={n.kind} size={11} dashed={n.status === 'proposed'} />
                    <span className={cn('min-w-0 truncate font-medium', n.status === 'stale' && 'text-muted-foreground')}>{n.label}</span>
                    <span className="hidden min-w-0 truncate text-muted-foreground md:inline">{n.summary}</span>
                    <span className="ml-auto flex shrink-0 items-center gap-1.5">
                      {team && n.project_id && <ProjectChip projectId={n.project_id} />}
                      {n.source === 'learned' && <Badge variant="ai">Learned</Badge>}
                      {n.status === 'proposed' && <Badge variant="warning">Proposed</Badge>}
                      {n.status === 'stale' && <Badge variant="neutral">Stale</Badge>}
                      <span className="w-36 truncate text-right text-[11.5px] text-muted-foreground">{kindLabel(n.kind, n.subkind)}</span>
                      <span className="w-14 text-right text-[11.5px] tabular-nums text-muted-foreground">{model.degree.get(id) ?? 0} links</span>
                    </span>
                  </button>
                </WellRow>
              )
            })}
          </WellList>
        </section>
      ))}
    </div>
  )
}

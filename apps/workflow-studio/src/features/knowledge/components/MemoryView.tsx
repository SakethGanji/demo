/**
 * Memory: what the graph has learned, newest first, grouped by day on one rail. Each event names what taught it
 * (a rebuild, a kept workflow or app, a person) and the concepts it touched; a concept opens in the graph. On the
 * team page each event also names the project it happened in.
 */
import { useMemo } from 'react'
import { BrainCircuit, CircleAlert } from 'lucide-react'
import { timeAgo } from '@/features/project/lib/format'
import { errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, Tile } from '@/shared/components/ui/surface'
import { ConceptChip } from '../kit/ConceptChip'
import { eventConcepts, groupByDay, triggerLabel } from '../lib/coverage'
import type { GraphModel } from '../lib/graph'
import { useKgMemory } from '../hooks/useKnowledge'
import { useKnowledgeScope } from '../hooks/useKnowledgeScope'
import { ProjectChip } from './KnowledgeScope'

const TRIGGER_TONE: Record<string, 'ai' | 'success' | 'neutral' | 'primary'> = {
  kept_workflow: 'success',
  kept_app: 'success',
  human: 'primary',
  rebuild: 'neutral',
  seed: 'neutral',
}

export function MemoryView({ model, onPick }: { model: GraphModel | null; onPick: (id: string) => void }) {
  const { scope, team } = useKnowledgeScope()
  const q = useKgMemory(scope)
  const events = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  const days = useMemo(() => groupByDay(events), [events])

  if (q.isPending)
    return (
      <div className="flex flex-col gap-3" data-testid="knowledge-memory-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>
    )
  if (q.isError)
    return (
      <Callout tone="error" icon={<CircleAlert />} data-testid="knowledge-memory-error">
        <div className="flex items-center gap-3">
          <span className="flex-1">Couldn't load the memory. {errorMessage(q.error)}</span>
          <Button size="xs" variant="outline" onClick={() => void q.refetch()}>Retry</Button>
        </div>
      </Callout>
    )
  if (!events.length)
    return (
      <Tile className="p-3">
        <EmptyState icon={<BrainCircuit />} title="Nothing learned yet" data-testid="knowledge-memory-empty">
          When someone keeps a workflow or an app the AI built, the graph records what it learned here: new patterns,
          validated concepts, proposed ones waiting for a decision.
        </EmptyState>
      </Tile>
    )

  return (
    <Tile className="p-5" data-testid="knowledge-memory">
      <div className="flex flex-col gap-6">
        {days.map((d) => (
          <section key={d.day} className="flex flex-col gap-3">
            <h2 className="text-[12.5px] font-semibold text-muted-foreground">{d.day}</h2>
            <ol className="relative flex flex-col gap-4 pl-5">
              <span className="absolute top-1 bottom-1 left-[5px] w-px bg-hairline" aria-hidden />
              {d.items.map((e) => {
                const concepts = eventConcepts(e)
                return (
                  <li key={e.id} className="relative flex flex-col gap-1.5" data-testid={`knowledge-memory-event-${e.id}`}>
                    <span className="absolute top-1.5 -left-5 size-[11px] rounded-full bg-ai-soft p-[3px]" aria-hidden>
                      <span className="block size-full rounded-full bg-ai-text" />
                    </span>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={TRIGGER_TONE[e.trigger] ?? 'ai'}>{triggerLabel(e.trigger)}</Badge>
                      {team && <ProjectChip projectId={e.project_id} />}
                      <span className="text-[12.5px] font-medium">{e.summary || triggerLabel(e.trigger)}</span>
                      <span className="ml-auto text-[11.5px] text-muted-foreground">
                        {e.actor ? `${e.actor} · ` : ''}
                        {timeAgo(e.at)}
                      </span>
                    </div>
                    {concepts.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {concepts.map((key) => {
                          const n = model?.byKey.get(key)
                          const chip = <ConceptChip conceptKey={key} label={n?.label ?? key.replace(/^concept:/, '')} tone={n ? 'confirmed' : 'suggested'} />
                          return n ? (
                            <button key={key} type="button" onClick={() => onPick(n.id)} className="rounded-chip outline-none focus-visible:shadow-focus" aria-label={`Show ${n.label} in the graph`}>
                              {chip}
                            </button>
                          ) : (
                            <span key={key}>{chip}</span>
                          )
                        })}
                      </div>
                    )}
                  </li>
                )
              })}
            </ol>
          </section>
        ))}
        {q.hasNextPage && (
          <div>
            <Button size="sm" variant="outline" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage} data-testid="knowledge-memory-more">
              {q.isFetchingNextPage ? 'Loading' : 'Show older'}
            </Button>
          </div>
        )}
      </div>
    </Tile>
  )
}

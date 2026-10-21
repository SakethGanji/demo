/**
 * The selected node, sliding in from the right over the canvas: what it is, where it came from, what it links to
 * (grouped by how the link reads from this side), how often kept builds have validated it, and what the graph has
 * learned about it. A proposed concept can be accepted or rejected here; the decision carries the actor name the
 * AI builder records, asked once per browser.
 *
 * On the team page the panel also names the node's project, links to that project's own Knowledge page focused on
 * the same node (and to the workflow a workflow node or pattern is about), and marks neighbours from other projects.
 */
import { useMemo } from 'react'
import { motion } from 'motion/react'
import { Link } from '@tanstack/react-router'
import { ArrowUpRight, Check, CircleAlert, Clock, ShieldCheck, Workflow, X } from 'lucide-react'
import { toast } from 'sonner'
import { ActorPrompt } from '@/features/ai-builder/components/ActorPrompt'
import { useActorName } from '@/features/ai-builder/useActorName'
import { pluralize, timeAgo } from '@/features/project/lib/format'
import { errorMessage, isApiError } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, WellList, WellRow } from '@/shared/components/ui/surface'
import { dur, ease } from '@/shared/motion/tokens'
import { triggerLabel } from '../lib/coverage'
import { kindLabel, relPhrase } from '../lib/kinds'
import { workflowIdFor } from '../lib/projects'
import type { KgNeighbour, KgNode } from '../lib/types'
import { useDecideNode, useKgNode } from '../hooks/useKnowledge'
import { KindSwatch } from './KindSwatch'
import { useKnowledgeScope } from '../hooks/useKnowledgeScope'
import { ProjectChip } from './KnowledgeScope'

export const PANEL_W = 360

export function NodeDetailPanel({
  id, fallback, onClose, onPick, off,
}: {
  id: string
  /** the graph's copy of the node, shown while the detail loads */
  fallback: KgNode | undefined
  onClose: () => void
  onPick: (id: string) => void
  off: boolean
}) {
  const { scope, team } = useKnowledgeScope()
  const q = useKgNode(scope, id)
  const node = q.data?.node ?? fallback
  return (
    <motion.aside
      initial={off ? false : { x: 28, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={off ? undefined : { x: 28, opacity: 0, transition: { duration: dur.fast, ease: ease.in } }}
      transition={{ duration: dur.slow, ease: ease.out }}
      className="glass-overlay absolute top-2 right-2 bottom-2 z-20 flex flex-col overflow-hidden rounded-panel"
      style={{ width: `min(${PANEL_W}px, calc(100% - 24px))` }}
      aria-label={node ? `${node.label} details` : 'Node details'}
      data-testid="knowledge-detail"
      data-node={id}
    >
      <header className="hairline-b flex items-start gap-2.5 px-4 pt-4 pb-3">
        <span className="mt-1">{node && <KindSwatch kind={node.kind} size={14} dashed={node.status === 'proposed'} />}</span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] leading-tight font-semibold tracking-[-0.01em] break-words" data-testid="knowledge-detail-label">
            {node?.label ?? '…'}
          </h2>
          {node && <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">{node.key}</div>}
        </div>
        <Button size="icon-sm" variant="ghost" onClick={onClose} aria-label="Close details" data-testid="knowledge-detail-close">
          <X />
        </Button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-3">
        {node && <Badges node={node} />}
        {team && node && <ProjectHome node={node} neighbours={q.data?.neighbours} />}
        {node?.summary && <p className="text-[12.5px] leading-[19px] text-foreground/85">{node.summary}</p>}
        {node && node.aliases.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="mr-0.5 text-[12px] text-muted-foreground">Also called</span>
            {node.aliases.map((a) => (
              <Badge key={a} variant="neutral">{a}</Badge>
            ))}
          </div>
        )}
        {node && <Validation node={node} />}
        {node?.status === 'proposed' && <Decide node={node} />}
        {q.isPending ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-24" />
          </div>
        ) : q.isError ? (
          <Callout tone="error" icon={<CircleAlert />}>
            {isApiError(q.error, undefined, 404) ? 'This node is no longer in the graph.' : errorMessage(q.error)}
          </Callout>
        ) : (
          <>
            <Neighbours groups={q.data.neighbours} onPick={onPick} home={node?.project_id ?? null} />
            <LearnEvents events={q.data.learn_events} />
          </>
        )}
      </div>
    </motion.aside>
  )
}

function Badges({ node }: { node: KgNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Badge variant="secondary">{kindLabel(node.kind, node.subkind)}</Badge>
      {node.source === 'learned' ? <Badge variant="ai">Learned</Badge> : <Badge variant="neutral">{sourceLabel(node.source)}</Badge>}
      {node.status === 'proposed' && <Badge variant="warning">Proposed</Badge>}
      {node.status === 'stale' && <Badge variant="neutral">Stale</Badge>}
      {node.status === 'rejected' && <Badge variant="error">Rejected</Badge>}
      {node.scope && node.scope !== 'global' && <Badge variant="outline">{node.scope}</Badge>}
    </div>
  )
}

const sourceLabel = (s: string) =>
  ({ seed: 'Built in', extracted: 'Extracted', human: 'Added by hand' })[s] ?? s.replaceAll('_', ' ')

function Validation({ node }: { node: KgNode }) {
  return (
    <div className="flex items-center gap-4 text-[12px] text-muted-foreground">
      <span className="inline-flex items-center gap-1.5" data-testid="knowledge-detail-validated">
        <ShieldCheck className="size-3.5 text-success" />
        <span>
          <span className="font-semibold text-foreground tabular-nums">{node.validated_count}</span> {node.validated_count === 1 ? 'kept build' : 'kept builds'}
        </span>
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Clock className="size-3.5" />
        {node.last_validated_at ? `validated ${timeAgo(node.last_validated_at)}` : 'never validated'}
      </span>
    </div>
  )
}

/**
 * Team scope: whose node this is. A project's node links to that project's Knowledge page, focused on the same node,
 * and to the workflow it is about; shared knowledge says so.
 */
function ProjectHome({ node, neighbours }: { node: KgNode; neighbours?: Record<string, KgNeighbour[]> }) {
  const { projects } = useKnowledgeScope()
  const p = node.project_id ? projects.get(node.project_id) : undefined
  const wf = p ? workflowIdFor(node, neighbours) : null
  const link =
    'inline-flex items-center gap-1 rounded-chip text-[12px] font-medium text-primary outline-none underline-offset-3 hover:underline focus-visible:shadow-focus [&_svg]:size-3.5'
  return (
    <div className="flex flex-col gap-1.5 rounded-panel bg-recess px-3 py-2.5" data-testid="knowledge-detail-project">
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-[12px] text-muted-foreground">{node.project_id ? 'From' : 'Owned by'}</span>
        <ProjectChip projectId={node.project_id} />
        {!node.project_id && <span className="truncate text-[11.5px] text-muted-foreground">every project can use it</span>}
      </div>
      {p && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Link
            to="/p/$project/knowledge"
            params={{ project: p.slug }}
            search={{ node: node.id }}
            className={link}
            data-testid="knowledge-detail-project-link"
          >
            Open in {p.name} <ArrowUpRight />
          </Link>
          {wf && (
            <Link
              to="/p/$project/workflows/$workflowId"
              params={{ project: p.slug, workflowId: wf }}
              className={link}
              data-testid="knowledge-detail-workflow-link"
            >
              <Workflow /> Open the workflow
            </Link>
          )}
        </div>
      )}
    </div>
  )
}

function Decide({ node }: { node: KgNode }) {
  const actor = useActorName()
  const { scope } = useKnowledgeScope()
  const decide = useDecideNode(scope)
  const run = (decision: 'accept' | 'reject') =>
    decide.mutate(
      { id: node.id, decision, actor: actor || null },
      {
        onSuccess: () => toast.success(decision === 'accept' ? `Accepted ${node.label}` : `Rejected ${node.label}`),
        onError: (e) =>
          toast.error(isApiError(e, 'NOT_PROPOSED') ? 'Someone already decided this one' : 'Could not record the decision', {
            description: errorMessage(e),
          }),
      },
    )
  return (
    <div className="flex flex-col gap-2.5 rounded-panel bg-warning-soft p-3" data-testid="knowledge-decide">
      <div className="text-[12.5px] text-warning-text">
        The AI proposed this concept from a kept build. Accept it to let the builder use it, or reject it to drop it.
      </div>
      {!actor ? (
        <ActorPrompt />
      ) : (
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => run('accept')} disabled={decide.isPending} data-testid="knowledge-accept">
            <Check /> Accept
          </Button>
          <Button size="sm" variant="outline" onClick={() => run('reject')} disabled={decide.isPending} data-testid="knowledge-reject">
            <X /> Reject
          </Button>
          <span className="ml-auto truncate text-[11.5px] text-muted-foreground">as {actor}</span>
        </div>
      )}
    </div>
  )
}

function Neighbours({ groups, onPick, home }: { groups: Record<string, KgNeighbour[]>; onPick: (id: string) => void; home: string | null }) {
  const { team, projectOf } = useKnowledgeScope()
  const sections = useMemo(() => {
    const m = new Map<string, KgNeighbour[]>()
    for (const [rel, list] of Object.entries(groups)) {
      for (const nb of list) {
        const title = relPhrase(rel, nb.direction)
        m.set(title, [...(m.get(title) ?? []), nb])
      }
    }
    return [...m].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
  }, [groups])
  if (!sections.length) return <div className="text-[12.5px] text-muted-foreground">Not linked to anything yet.</div>
  return (
    <div className="flex flex-col gap-3" data-testid="knowledge-neighbours">
      {sections.map(([title, list]) => (
        <section key={title} className="flex flex-col gap-1.5">
          <h3 className="flex items-baseline gap-1.5 text-[12px] font-semibold text-muted-foreground">
            {title} <span className="font-normal tabular-nums">{list.length}</span>
          </h3>
          <WellList>
            {list.slice(0, 40).map((nb) => (
              <WellRow key={`${nb.direction}:${nb.node.id}`} className="p-0">
                <button
                  type="button"
                  onClick={() => onPick(nb.node.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-3 py-1.5 text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
                  data-testid={`knowledge-neighbour-${nb.node.id}`}
                >
                  <KindSwatch kind={nb.node.kind} size={10} />
                  <span className="min-w-0 flex-1 truncate">{nb.node.label}</span>
                  {team && (() => {
                    const other = projectOf(nb.node.id)
                    return other && other !== home ? <ProjectChip projectId={other} className="max-w-28" /> : null
                  })()}
                  <span className="shrink-0 text-[11px] text-muted-foreground">{kindLabel(nb.node.kind, nb.node.subkind)}</span>
                </button>
              </WellRow>
            ))}
          </WellList>
          {list.length > 40 && <div className="text-[11.5px] text-muted-foreground">and {list.length - 40} more</div>}
        </section>
      ))}
    </div>
  )
}

function LearnEvents({ events }: { events: { id: number; at: string; trigger: string; summary: string; actor: string | null }[] }) {
  if (!events.length) return null
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-[12px] font-semibold text-muted-foreground">What it learned · {pluralize(events.length, 'event')}</h3>
      <ol className="flex flex-col gap-2">
        {events.slice(0, 12).map((e) => (
          <li key={e.id} className="flex gap-2 text-[12px]">
            <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-ai-text" aria-hidden />
            <div className="min-w-0">
              <div className="text-foreground/90">{e.summary || triggerLabel(e.trigger)}</div>
              <div className="text-[11px] text-muted-foreground">
                {triggerLabel(e.trigger)}
                {e.actor ? ` by ${e.actor}` : ''} · {timeAgo(e.at)}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

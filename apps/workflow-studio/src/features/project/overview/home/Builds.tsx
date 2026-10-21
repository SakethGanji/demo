/**
 * What the project builds, as preview cards in two rows, each under its title ("Apps · 3", "Workflows · 5") and
 * ending in an add card ("New app", "New workflow", AddTile.tsx). This is the only list of apps and workflows. The card speaks the projects page's language (a Tile, the picture in a Well, the name under it):
 *   App       a wireframe of the app's own UI drawn from its source (AppSketch), in a small browser frame.
 *   Workflow  the graph itself, nodes and edges (the workflows list's static WorkflowSVG thumbnail), with the last
 *             run's state on it.
 * State rides on the card as a badge: failed (red, the failures today), running (a breathing dot), approvals waiting
 * (a count). One click opens the item full screen; a failed workflow opens on its failed run. Order is by name.
 */
import { memo, useMemo } from 'react'
import { Link } from '@tanstack/react-router'
import { useQueries } from '@tanstack/react-query'
import { AppWindow, Workflow } from 'lucide-react'
import { qk } from '@/shared/api/queryKeys'
import { appsApi, workflowsApi } from '@/shared/lib/api'
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { SectionHeader, Tile, Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import type { WorkflowDefinition } from '@/features/projects/hooks/useWorkflows'
import { definitionToPreviewData } from '@/features/workflow-editor/lib/workflowTransform'
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG'
import { entityKey, type EntityStatus, type ProjectEntity } from '@/features/project/lib/entities'
import { useProjectCtx } from '../../ProjectContext'
import { timeAgo } from '../../lib/format'
import { AddTile } from './AddTile'
import { AppSketch } from './AppSketch'
import { appUi, iso, statusOf } from './cardInfo'

const byName = (a: ProjectEntity, b: ProjectEntity) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true })

const badgeCls =
  'absolute top-2 right-2 z-10 flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums shadow-[0_0_0_2px_var(--surface)]'

function Badge({ s }: { s: EntityStatus | undefined }) {
  if (!s) return null
  const failedN = s.runs24h.filter((r) => r.tone === 'err').length
  if (s.failed)
    return (
      <span className={cn(badgeCls, 'bg-destructive text-destructive-foreground')} data-testid="launch-badge-failed">
        {failedN || '!'}
      </span>
    )
  if (s.running) return <span className="absolute top-2.5 right-2.5 z-10 size-3 rounded-full bg-primary text-primary shadow-[0_0_0_2px_var(--surface)] animate-live-ring" data-testid="launch-badge-running" />
  if (s.approvals > 0)
    return (
      <span className={cn(badgeCls, 'bg-warning-soft text-warning-text')} data-testid="launch-badge-approvals">
        {s.approvals}
      </span>
    )
  return null
}

/* ── workflow graph thumbnail ──────────────────────────────────────────────────────────────────────────────────── */

const EDGE_CLASS =
  'stroke-(--art-line) [stroke-opacity:1] transition-[stroke] duration-(--d-slow) ' +
  'group-hover/c:stroke-foreground/50 group-hover/c:[stroke-dasharray:5_5] group-hover/c:animate-[dash-flow_.7s_linear_infinite] motion-reduce:group-hover/c:animate-none'

/** The API definition is snake_case; the preview transform reads camelCase connections. */
function toPreviewDefinition(d: ApiWorkflowDetail['definition']): WorkflowDefinition {
  return {
    nodes: d.nodes,
    connections: d.connections.map((c) => ({ sourceNode: c.source_node, targetNode: c.target_node, sourceOutput: c.source_output, targetInput: c.target_input })),
  }
}

const GraphThumb = memo(function GraphThumb({ definition }: { definition: ApiWorkflowDetail['definition'] | undefined }) {
  const preview = useMemo(() => (definition ? definitionToPreviewData(toPreviewDefinition(definition)) : null), [definition])
  if (!preview) return <Skeleton className="size-full rounded-none" />
  if (!preview.nodes.length) return <span className="flex size-full items-center justify-center text-[11px] text-muted-foreground">No steps yet</span>
  return <WorkflowSVG nodes={preview.nodes} edges={preview.edges} showDotGrid edgeClassName={EDGE_CLASS} className="block size-full text-foreground" />
})

/* ── the card ──────────────────────────────────────────────────────────────────────────────────────────────────── */

function Card({ e, s, now, meta, picture }: { e: ProjectEntity; s: EntityStatus | undefined; now: number; meta?: string; picture: React.ReactNode }) {
  const { slug } = useProjectCtx()
  const wf = e.kind === 'workflow'
  const st = statusOf(s, now, meta)
  const lastTone = s?.running ? 'running' : s?.last ? (s.last.tone === 'err' ? 'err' : 'ok') : null
  const body = (
    <Tile interactive className="relative flex flex-col p-2">
      <Badge s={s} />
      <Well className="relative aspect-[16/10] overflow-hidden" aria-hidden>
        {picture}
        {wf && (
          <span className="absolute bottom-1.5 left-1.5 flex items-center gap-1.5 rounded-full glass-strip px-2 py-0.5 text-[10.5px] text-foreground/80" data-testid="wf-last-run">
            {lastTone ? <StatusDot tone={lastTone} flat={lastTone !== 'running'} /> : <span className="size-1.5 rounded-full bg-faint" />}
            {s?.running ? 'Running' : s?.last ? `${s.last.tone === 'err' ? 'Failed' : 'Succeeded'} ${timeAgo(iso(s.last.at), now)}` : 'Never run'}
          </span>
        )}
      </Well>
      <span className="flex min-w-0 flex-col gap-0.5 px-1.5 pt-2.5 pb-1">
        <span className="truncate text-[13.5px] font-semibold tracking-[-0.01em]">{e.name}</span>
        <span className={cn('truncate text-[11.5px]', st.tone === 'err' ? 'text-destructive' : st.tone === 'warn' ? 'text-warning-text' : st.tone === 'live' ? 'text-primary' : 'text-muted-foreground')}>{st.text}</span>
      </span>
    </Tile>
  )
  const common = {
    className: 'group/c block min-w-0 rounded-panel outline-none focus-visible:shadow-focus',
    'data-testid': `launch-${wf ? 'wf' : 'app'}-${e.id}`,
    'data-kind': e.kind,
    'data-state': s?.failed ? 'failed' : s?.running ? 'running' : s && s.approvals > 0 ? 'approvals' : 'resting',
    'aria-label': `${wf ? 'Workflow' : 'App'} ${e.name}, ${st.text}`,
  }
  return wf ? (
    <Link to="/p/$project/workflows/$workflowId" params={{ project: slug, workflowId: e.id }} search={s?.failed && s.last ? ({ execution: s.last.id } as never) : undefined} {...common}>
      {body}
    </Link>
  ) : (
    <Link to="/p/$project/apps/$appId" params={{ project: slug, appId: e.id }} {...common}>
      {body}
    </Link>
  )
}

/* ── the rows ──────────────────────────────────────────────────────────────────────────────────────────────────── */

export function BuildRow({
  kind,
  entities,
  status,
  now,
  meta,
  onNew,
  span,
}: {
  /** set when the row shares the line with the other one: its share of a 4-column line, one column per card */
  span?: number
  kind: 'app' | 'workflow'
  entities: ProjectEntity[]
  status: Map<string, EntityStatus>
  now: number
  meta: Map<string, string>
  onNew: () => void
}) {
  const app = kind === 'app'
  const items = [...entities].sort(byName)
  // Details carry the picture: an app's source files, a workflow's definition. Keyed like the workflows list (by
  // updated_at), so an edit redraws the card and an unchanged item is not refetched.
  const details = useQueries({
    queries: items.map((e) =>
      app
        ? { queryKey: [...qk.app(e.id), 'card', e.updatedAt], queryFn: () => appsApi.get(e.id), staleTime: 60_000 }
        : { queryKey: [...qk.workflow(e.id), e.updatedAt], queryFn: () => workflowsApi.get(e.id), staleTime: 60_000 },
    ),
  })
  return (
    <section
      className={cn('flex min-w-0 flex-col gap-3', span && '@[880px]/bt:col-span-(--span)')}
      style={span ? ({ '--span': String(span), '--n': `repeat(${span}, minmax(0, 1fr))` } as React.CSSProperties) : undefined}
      data-testid={app ? 'home-apps' : 'home-workflows'}
    >
      <SectionHeader
        icon={app ? <AppWindow /> : <Workflow />}
        hue={app ? 'apps' : 'flow'}
        title={
          <h2 className="inline whitespace-nowrap">
            {app ? 'Apps' : 'Workflows'}
            <span className="font-normal text-muted-foreground"> · </span>
            <span className="font-normal tabular-nums text-muted-foreground" data-testid={app ? 'home-apps-count' : 'home-workflows-count'}>
              {items.length}
            </span>
          </h2>
        }
        sub={
          items.length === 0 ? (
            <span data-testid={app ? 'home-apps-empty' : 'home-workflows-empty'}>{app ? 'Screens with their own routes and tables' : 'Steps that run on a trigger, schedule or call'}</span>
          ) : undefined
        }
      />
      <div className={cn('grid grid-cols-1 gap-3 min-[420px]:grid-cols-2', span ? '@[880px]/bt:grid-cols-(--n)' : '@[880px]/bt:grid-cols-3 @[1120px]/bt:grid-cols-4')}>
        {items.map((e, i) => {
          const d = details[i]?.data
          const picture = app ? (
            <AppSketch source={d ? appUi(d as { files?: { path: string; content: string }[]; source_code?: string | null }) : null} title={e.name} />
          ) : (
            <GraphThumb definition={(d as ApiWorkflowDetail | undefined)?.definition} />
          )
          return <Card key={entityKey(e.kind, e.id)} e={e} s={status.get(entityKey(e.kind, e.id))} now={now} meta={meta.get(e.id)} picture={picture} />
        })}
        <AddTile kind={kind} onClick={onNew} />
      </div>
    </section>
  )
}

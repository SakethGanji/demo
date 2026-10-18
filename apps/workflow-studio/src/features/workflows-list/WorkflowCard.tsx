/**
 * The workflow card (glass-studio mockup `.wf`): one Tile, a 16:9 graph thumbnail in a Well on a faint dot grid,
 * then name + status, then a meta row. Thumbnails are static SVG; on card hover the edges turn primary and flow
 * (dash animation, off under reduced motion). One component for every place a workflow card appears.
 */
import { memo, useMemo } from 'react'
import { Link } from '@tanstack/react-router'
import { Copy, Ellipsis, FolderInput, Lock, SquareArrowOutUpRight, Trash2 } from 'lucide-react'
import { parseTs, timeAgo } from '@/features/project/lib/format'
import type { RunListItem } from '@/shared/api/types'
import type { ApiWorkflowDetail, ApiWorkflowSummary } from '@/shared/lib/backendTypes'
import type { WorkflowDefinition } from '@/features/projects/hooks/useWorkflows'
import { definitionToPreviewData } from '@/features/workflow-editor/lib/workflowTransform'
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'

export type HandledRoute = { route_id: string; method: string; path: string }
export type WorkflowSummary = ApiWorkflowSummary & { handler_for?: HandledRoute[]; description?: string | null }

/** The API definition is snake_case; the preview transform reads camelCase connections. */
function toPreviewDefinition(d: ApiWorkflowDetail['definition']): WorkflowDefinition {
  return {
    nodes: d.nodes,
    connections: d.connections.map((c) => ({
      sourceNode: c.source_node,
      targetNode: c.target_node,
      sourceOutput: c.source_output,
      targetInput: c.target_input,
    })),
  }
}

const EDGE_CLASS =
  'stroke-(--art-line) [stroke-opacity:1] transition-[stroke] duration-(--d-slow) ' +
  'group-hover/wf:stroke-primary group-hover/wf:[stroke-dasharray:5_5] group-hover/wf:animate-[dash-flow_.7s_linear_infinite] ' +
  'motion-reduce:group-hover/wf:animate-none'

const WorkflowThumbnail = memo(function WorkflowThumbnail({ definition }: { definition: ApiWorkflowDetail['definition'] | undefined }) {
  const preview = useMemo(() => (definition ? definitionToPreviewData(toPreviewDefinition(definition)) : null), [definition])
  if (!preview) return <Skeleton className="size-full rounded-none" />
  return (
    <WorkflowSVG
      nodes={preview.nodes}
      edges={preview.edges}
      showDotGrid
      edgeClassName={EDGE_CLASS}
      className="block size-full text-foreground"
    />
  )
})

export function WorkflowCard({
  workflow: w,
  detail,
  detailLoading,
  detailError,
  trigger,
  handled,
  run,
  runLoading,
  slug,
  drifting,
  onOpen,
  onDuplicate,
  onMove,
  onDelete,
}: {
  workflow: WorkflowSummary
  detail: ApiWorkflowDetail | undefined
  detailLoading: boolean
  detailError: boolean
  /** "Manual", "Webhook", "Cron · 02:00" (computed by the caller from the definition). */
  trigger: string | null
  handled: HandledRoute[]
  run: RunListItem | null | undefined
  runLoading: boolean
  slug: string
  drifting: boolean
  onOpen: () => void
  onDuplicate: () => void
  onMove: () => void
  onDelete: () => void
}) {
  const nodeCount = detail?.definition.nodes.length
  return (
    <Tile
      interactive
      className="group group/wf flex flex-col p-2.5"
      onClick={(e) => {
        if ((e.target as HTMLElement).closest('a,button,[role="menu"]')) return
        onOpen()
      }}
      data-testid={`workflow-row-${w.id}`}
    >
      <Well className="aspect-video overflow-hidden" aria-hidden={detail ? true : undefined}>
        <WorkflowThumbnail definition={detail?.definition} />
      </Well>
      <div className="px-1.5 pt-3 pb-1">
        <div className="flex items-center gap-2">
          <Link
            to="/p/$project/workflows/$workflowId"
            params={{ project: slug, workflowId: w.id }}
            className="min-w-0 flex-1 truncate text-[13.5px] font-semibold tracking-[-0.01em] hover:underline"
            data-testid={`workflow-open-${w.id}`}
          >
            {w.name}
          </Link>
          {drifting && (
            <Tip label="A real reply changed. Open the workflow to review.">
              <span className="inline-flex" data-testid="wf-drift-dot" data-workflow-id={w.id} role="img" aria-label="Open drift alert">
                <StatusDot tone="warn" />
              </span>
            </Tip>
          )}
          {w.active ? <Badge variant="success">Published</Badge> : <Badge variant="neutral">Draft</Badge>}
          <RowMenu workflow={w} handled={handled} onOpen={onOpen} onDuplicate={onDuplicate} onMove={onMove} onDelete={onDelete} />
        </div>
        {w.description && <div className="mt-0.5 truncate text-[12px] text-muted-foreground">{w.description}</div>}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted-foreground">
          <span data-testid={`workflow-trigger-${w.id}`}>
            {trigger ?? (detailError ? '—' : detailLoading ? <Skeleton className="inline-block h-3.5 w-14 align-middle" /> : '—')}
          </span>
          {nodeCount != null && (
            <span>
              {nodeCount} {nodeCount === 1 ? 'node' : 'nodes'}
            </span>
          )}
          {handled.map((r) => (
            <Link
              key={r.route_id}
              to="/p/$project/runs"
              params={{ project: slug }}
              search={{ route: r.route_id }}
              className="inline-flex min-w-0 items-center gap-1.5 hover:underline"
              data-testid={`workflow-route-${w.id}`}
            >
              <MethodBadge method={r.method} size="sm" />
              <span className="truncate font-mono text-[11.5px]">{r.path}</span>
            </Link>
          ))}
          <span className="min-w-0 truncate" data-testid={`workflow-last-run-${w.id}`}>
            {run ? (
              <Link
                to="/p/$project/runs/$executionId"
                params={{ project: slug, executionId: run.id }}
                className="inline-flex items-center gap-1.5 hover:underline"
              >
                <StatusDot tone={runTone(run.status)} className="shadow-none" />
                {run.status} · {timeAgo(run.start_time)}
              </Link>
            ) : runLoading ? (
              <Skeleton className="inline-block h-3.5 w-16 align-middle" />
            ) : (
              'never run'
            )}
          </span>
          <Tip label={w.updated_at}>
            <span className="ml-auto">{shortDate(w.updated_at)}</span>
          </Tip>
        </div>
      </div>
    </Tile>
  )
}

function shortDate(ts: string): string {
  const d = parseTs(ts)
  return d ? `Updated ${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}` : '—'
}

function RowMenu({
  workflow: w,
  handled,
  onOpen,
  onDuplicate,
  onMove,
  onDelete,
}: {
  workflow: WorkflowSummary
  handled: HandledRoute[]
  onOpen: () => void
  onDuplicate: () => void
  onMove: () => void
  onDelete: () => void
}) {
  const used = handled[0]
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`Actions for ${w.name}`}
            className="-mr-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 data-popup-open:opacity-100"
            data-testid={`workflow-menu-${w.id}`}
          />
        }
      >
        <Ellipsis />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[240px] [&_[data-slot=dropdown-menu-item]]:text-[13px]" data-testid="workflow-menu">
        <DropdownMenuItem onClick={onOpen} data-testid="workflow-action-open">
          <SquareArrowOutUpRight /> Open
        </DropdownMenuItem>
        <DropdownMenuItem onClick={onDuplicate} data-testid="workflow-action-duplicate">
          <Copy /> Duplicate
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={onMove}
          disabled={!!used}
          title={used ? `Handles ${used.method} ${used.path}; a route handler cannot leave its project` : undefined}
          data-testid="workflow-action-move"
        >
          <FolderInput /> Move to project…
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {used ? (
          <DropdownMenuItem disabled data-testid="workflow-action-delete" data-blocked="true">
            <Lock /> Delete
            <span className="ml-auto whitespace-nowrap pl-4 text-[11.5px] text-muted-foreground">
              used by {used.method} {used.path}
            </span>
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem variant="destructive" onClick={onDelete} data-testid="workflow-action-delete">
            <Trash2 /> Delete
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

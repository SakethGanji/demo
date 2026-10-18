/**
 * `/p/$project/workflows` (UI-DESIGN §6.6/§6.7, `workflows.png`): the project's canvas
 * automations. Table (name, trigger, handled route, last run, status, updated, ⋯), Import (JSON),
 * New workflow, Duplicate, Move to project, Delete (blocked while a route uses it).
 */
import { useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Ellipsis, FolderInput, Lock, Plus, SquareArrowOutUpRight, Trash2, Upload, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectWorkflows, useRoutes } from '@/features/project/hooks/useProjectData'
import { parseTs, timeAgo } from '@/features/project/lib/format'
import { projectsApi, runsApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { RunListItem } from '@/shared/api/types'
import { workflowsApi } from '@/shared/lib/api'
import type { ApiWorkflowDetail, ApiWorkflowSummary, BackendWorkflow } from '@/shared/lib/backendTypes'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Callout, Dot, IconTile, PageHeader, Tile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { ImportN8nDialog } from './ImportN8nDialog'

type HandledRoute = { route_id: string; method: string; path: string }
type Summary = ApiWorkflowSummary & { handler_for?: HandledRoute[]; description?: string | null }

const TRIGGER_TYPES = new Set(['Start', 'Webhook', 'Cron', 'ErrorTrigger', 'ExecuteWorkflowTrigger'])

/** "Manual", "Webhook", "Cron · 02:00" from the workflow's trigger node. */
export function triggerLabel(def: ApiWorkflowDetail['definition'] | undefined, handlesRoute: boolean): string {
  const t = def?.nodes.find((n) => TRIGGER_TYPES.has(n.type))
  if (!t) return handlesRoute ? 'Route' : '—'
  if (t.type === 'Cron') {
    const p = t.parameters ?? {}
    if (p.mode === 'cron') return `Cron · ${cronLabel(String(p.cronExpression ?? '0 * * * *'))}`
    const s = Number(p.interval ?? 60)
    return `Cron · every ${s % 3600 === 0 ? `${s / 3600}h` : s % 60 === 0 ? `${s / 60}m` : `${s}s`}`
  }
  if (t.type === 'Webhook') return 'Webhook'
  if (t.type === 'ErrorTrigger') return 'On error'
  if (t.type === 'ExecuteWorkflowTrigger') return 'Sub-workflow'
  return handlesRoute ? 'Route' : 'Manual'
}

function cronLabel(expr: string): string {
  const m = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec(expr.trim())
  return m ? `${m[2].padStart(2, '0')}:${m[1].padStart(2, '0')}` : expr
}

function shortDate(ts: string): string {
  const d = parseTs(ts)
  return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—'
}

/** The workflow definition without engine-enriched fields, ready to POST again. */
function toBackendWorkflow(d: ApiWorkflowDetail, name: string): BackendWorkflow {
  return {
    name,
    nodes: d.definition.nodes.map((n) => ({
      name: n.name,
      type: n.type,
      parameters: n.parameters ?? {},
      ...(n.position ? { position: n.position } : {}),
      ...(n.disabled != null ? { disabled: n.disabled } : {}),
      on_error: n.on_error ?? (n.continue_on_fail ? 'continue' : 'stop'),
      ...(n.retry_on_fail != null ? { retry_on_fail: n.retry_on_fail } : {}),
      ...(n.retry_delay != null ? { retry_delay: n.retry_delay } : {}),
      ...(n.pinnedData ? { pinned_data: n.pinnedData } : {}),
    })),
    connections: d.definition.connections.map((c) => ({
      source_node: c.source_node,
      target_node: c.target_node,
      source_output: c.source_output,
      target_input: c.target_input,
    })),
  } as BackendWorkflow
}

export function WorkflowsListPage() {
  // Rendered inside the workspace sheet (outline + top bar come from WorkspaceLayout).
  return (
    <div className="h-full" data-testid="workflows-page">
      <WorkflowsPanel />
    </div>
  )
}

function WorkflowsPanel() {
  const { project, pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const list = useProjectWorkflows(pid)
  const routes = useRoutes(pid)
  const rows = useMemo(
    () => [...((list.data ?? []) as Summary[])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [list.data],
  )
  const details = useQueries({
    queries: rows.map((w) => ({
      queryKey: [...qk.workflow(w.id), w.updated_at],
      queryFn: () => workflowsApi.get(w.id),
      staleTime: 60_000,
    })),
  })
  const lastRuns = useQueries({
    queries: rows.map((w) => ({
      queryKey: [...qk.runsPrefix(pid), 'workflow-last', w.id],
      queryFn: async () => (await runsApi.list(pid, { workflow_id: w.id, limit: 1 })).runs[0] ?? null,
      staleTime: 15_000,
    })),
  })

  const fileRef = useRef<HTMLInputElement>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [n8nOpen, setN8nOpen] = useState(false)
  const [deleting, setDeleting] = useState<Summary | null>(null)
  const [moving, setMoving] = useState<Summary | null>(null)

  const handledBy = (w: Summary): HandledRoute[] => {
    const fromList = w.handler_for ?? []
    const fromRoutes = (routes.data ?? [])
      .filter((r) => r.handler.type === 'workflow' && r.handler.workflow_id === w.id)
      .map((r) => ({ route_id: r.id, method: r.method, path: r.path }))
    const seen = new Set<string>()
    return [...fromList, ...fromRoutes].filter((r) => (seen.has(r.route_id) ? false : (seen.add(r.route_id), true)))
  }

  const onImport = async (file: File) => {
    setImportError(null)
    setImporting(true)
    try {
      const raw = JSON.parse(await file.text()) as Record<string, unknown>
      // Accept an export (`{name, nodes, connections}`) or a detail response (`{name, definition}`).
      const def = (raw.definition as Record<string, unknown> | undefined) ?? raw
      if (!Array.isArray(def.nodes)) throw new Error('The file has no "nodes" array: export a workflow as JSON and try again.')
      const body = {
        name: String(raw.name ?? file.name.replace(/\.json$/i, '')),
        nodes: def.nodes,
        connections: (def.connections as unknown[]) ?? [],
        ...(raw.description ? { description: raw.description } : {}),
        ...(raw.settings ? { settings: raw.settings } : {}),
      } as unknown as BackendWorkflow
      const created = await projectsApi.createWorkflow(pid, body)
      await invalidate.workflowsChanged(qc, pid)
      toast.success(`Imported ${created.name}`)
    } catch (e) {
      setImportError(e instanceof SyntaxError ? `Not valid JSON: ${e.message}` : errorMessage(e))
    } finally {
      setImporting(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const duplicate = async (w: Summary) => {
    try {
      const d = await workflowsApi.get(w.id)
      const created = await projectsApi.createWorkflow(pid, toBackendWorkflow(d, `${w.name} (copy)`))
      await invalidate.workflowsChanged(qc, pid)
      toast.success(`Duplicated as ${created.name}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const headerActions = (
    <>
      <Button variant="outline" size="sm" disabled={importing} onClick={() => fileRef.current?.click()} data-testid="workflows-import">
        <Upload /> Import
      </Button>
      <Button variant="outline" size="sm" onClick={() => setN8nOpen(true)} data-testid="import-n8n">
        <Upload /> Import from n8n
      </Button>
      <Button size="sm" render={<Link to="/p/$project/workflows/$workflowId" params={{ project: slug, workflowId: 'new' }} />} data-testid="workflows-new">
        <Plus /> New workflow
      </Button>
    </>
  )

  const activeCount = rows.filter((w) => w.active).length

  return (
    <section className="flex h-full flex-col overflow-hidden" data-testid="workflows-panel">
      <ImportN8nDialog open={n8nOpen} onOpenChange={setN8nOpen} />
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) void onImport(f)
        }}
        data-testid="workflows-import-file"
      />
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-8 py-7">
        <PageHeader
          title="Workflows"
          icon={<Workflow />}
          hue="flow"
          meta={
            <>
              <span>Canvas automations in {project.name}</span>
              {rows.length > 0 && (
                <>
                  <Dot />
                  <span>
                    {rows.length} {rows.length === 1 ? 'workflow' : 'workflows'}
                  </span>
                  <Dot />
                  <span>{activeCount} published</span>
                </>
              )}
            </>
          }
          actions={rows.length > 0 ? headerActions : undefined}
        />
        {importError && (
          <Callout tone="error" className="mt-3" role="alert" data-testid="workflows-import-error">
            {importError}
          </Callout>
        )}

        {list.isLoading ? (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="workflows-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[196px] w-full rounded-panel" />
            ))}
          </div>
        ) : list.isError ? (
          <Tile className="mt-6" data-testid="workflows-error">
            <EmptyState
              title="Couldn't load workflows"
              actions={
                <Button variant="outline" size="sm" onClick={() => list.refetch()} data-testid="workflows-retry">
                  Retry
                </Button>
              }
            >
              {errorMessage(list.error)}
            </EmptyState>
          </Tile>
        ) : rows.length === 0 ? (
          <Tile className="mt-6 p-3">
            <EmptyState dust icon={<Workflow />} title="No workflows" actions={headerActions} data-testid="workflows-empty">
              Python routes are the main backend; use a workflow for scheduled or visual automations.
            </EmptyState>
          </Tile>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="workflows-table">
            {rows.map((w, i) => {
              const handled = handledBy(w)
              const detail = details[i]?.data
              const run = lastRuns[i]?.data as RunListItem | null | undefined
              const open = () => navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: w.id } })
              return (
                <Tile
                  key={w.id}
                  interactive
                  className="group flex min-h-[196px] flex-col"
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest('a,button,[role="menu"]')) return
                    void open()
                  }}
                  data-testid={`workflow-row-${w.id}`}
                >
                  <div className="flex items-center gap-2.5 px-4 pt-4">
                    <IconTile hue="flow" className="size-8">
                      <Workflow />
                    </IconTile>
                    <div className="min-w-0 flex-1">
                      <Link
                        to="/p/$project/workflows/$workflowId"
                        params={{ project: slug, workflowId: w.id }}
                        className="block truncate text-[14px] font-semibold tracking-[-0.01em] hover:underline"
                        data-testid={`workflow-open-${w.id}`}
                      >
                        {w.name}
                      </Link>
                      <div className="mt-px truncate text-[12px] text-muted-foreground">{w.description || (detail ? `${detail.definition.nodes.length} ${detail.definition.nodes.length === 1 ? 'node' : 'nodes'}` : '\u00a0')}</div>
                    </div>
                    {w.active ? <Badge variant="success">Published</Badge> : <Badge variant="neutral">Draft</Badge>}
                    <RowMenu
                      workflow={w}
                      handled={handled}
                      onOpen={() => void open()}
                      onDuplicate={() => void duplicate(w)}
                      onMove={() => setMoving(w)}
                      onDelete={() => setDeleting(w)}
                    />
                  </div>
                  <div className="px-4 pt-3.5 pb-4">
                    <div className="grid grid-cols-[64px_minmax(0,1fr)] items-center gap-x-3 gap-y-2 rounded-well bg-recess px-3 py-2.5 text-[12.5px]">
                      <span className="text-muted-foreground">Trigger</span>
                      <span className="truncate" data-testid={`workflow-trigger-${w.id}`}>
                        {detail ? triggerLabel(detail.definition, handled.length > 0) : details[i]?.isError ? '—' : <Skeleton className="h-4 w-16" />}
                      </span>
                      <span className="text-muted-foreground">Route</span>
                      {handled.length ? (
                        <span className="flex min-w-0 flex-col gap-1">
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
                              <span className="truncate font-mono text-[12px]">{r.path}</span>
                            </Link>
                          ))}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                      <span className="text-muted-foreground">Last run</span>
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
                        ) : lastRuns[i]?.isLoading ? (
                          <Skeleton className="h-4 w-20" />
                        ) : (
                          <span className="text-muted-foreground">never</span>
                        )}
                      </span>
                    </div>
                  </div>
                  <div className="mt-auto flex items-center gap-3 px-4 pb-3.5 text-[12px] text-muted-foreground">
                    <Tip label={w.updated_at}>
                      <span>Updated {shortDate(w.updated_at)}</span>
                    </Tip>
                  </div>
                </Tile>
              )
            })}
            <Tile
              interactive
              role="link"
              tabIndex={0}
              onClick={() => void navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: 'new' } })}
              onKeyDown={(e) =>
                (e.key === 'Enter' || e.key === ' ') &&
                (e.preventDefault(), void navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: 'new' } }))
              }
              className="flex min-h-[196px] flex-col items-center justify-center gap-1 p-5 text-center focus-visible:shadow-focus focus-visible:outline-none"
              data-testid="workflows-new-card"
            >
              <IconTile hue="flow" className="mb-2 size-9">
                <Plus />
              </IconTile>
              <span className="font-medium">New workflow</span>
              <span className="text-[12px] text-muted-foreground">Scheduled or visual automations</span>
            </Tile>
          </div>
        )}
      </div>
      <DeleteWorkflowDialog workflow={deleting} onClose={() => setDeleting(null)} />
      <MoveWorkflowDialog workflow={moving} onClose={() => setMoving(null)} />
    </section>
  )
}

function RowMenu({
  workflow: w,
  handled,
  onOpen,
  onDuplicate,
  onMove,
  onDelete,
}: {
  workflow: Summary
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

function DeleteWorkflowDialog({ workflow: w, onClose }: { workflow: Summary | null; onClose: () => void }) {
  const { pid } = useProjectCtx()
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <AlertDialog
      open={!!w}
      onOpenChange={(o) => {
        if (!o) {
          setError(null)
          onClose()
        }
      }}
    >
      <AlertDialogContent data-testid="workflow-delete-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {w?.name}?</AlertDialogTitle>
          <AlertDialogDescription>Its versions and webhook URL are deleted. Past runs stay in Runs.</AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <div className="text-[12px] text-destructive" role="alert" data-testid="workflow-delete-error">
            {error}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel size="sm">Cancel</AlertDialogCancel>
          <AlertDialogAction
            size="sm"
            variant="destructive"
            disabled={busy}
            onClick={async (e) => {
              e.preventDefault()
              if (!w) return
              setBusy(true)
              setError(null)
              try {
                await workflowsApi.delete(w.id)
                qc.removeQueries({ queryKey: qk.workflow(w.id) })
                await invalidate.workflowsChanged(qc, pid)
                toast.success(`Deleted ${w.name}`)
                onClose()
              } catch (err) {
                setError(
                  isApiError(err, 'workflow_in_use')
                    ? `${errorMessage(err)}. Point the route at another handler first.`
                    : errorMessage(err),
                )
                if (isApiError(err, 'workflow_in_use')) void invalidate.routesChanged(qc, pid)
              } finally {
                setBusy(false)
              }
            }}
            data-testid="workflow-delete-confirm"
          >
            Delete workflow
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function MoveWorkflowDialog({ workflow: w, onClose }: { workflow: Summary | null; onClose: () => void }) {
  const { pid } = useProjectCtx()
  const qc = useQueryClient()
  const projects = useQuery({ queryKey: qk.projects, queryFn: projectsApi.list, enabled: !!w })
  const others = (projects.data ?? []).filter((p) => p.id !== pid)
  const [target, setTarget] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const close = () => {
    setError(null)
    setTarget('')
    onClose()
  }
  return (
    <Dialog open={!!w} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-[420px]" data-testid="workflow-move-dialog">
        <DialogHeader>
          <DialogTitle>Move {w?.name}</DialogTitle>
          <DialogDescription>It uses the target project's secrets and environments from then on.</DialogDescription>
        </DialogHeader>
        <Select value={target} onValueChange={(v) => setTarget((v as string) ?? '')}>
          <SelectTrigger className="w-full" data-testid="workflow-move-target">
            <SelectValue placeholder={projects.isLoading ? 'Loading projects…' : 'Choose a project'} />
          </SelectTrigger>
          <SelectContent>
            {others.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {error && (
          <div className="text-[12px] text-destructive" role="alert" data-testid="workflow-move-error">
            {error}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={close}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!target || busy}
            onClick={async () => {
              if (!w) return
              setBusy(true)
              setError(null)
              try {
                await workflowsApi.update(w.id, { project_id: target } as unknown as BackendWorkflow)
                await invalidate.workflowsChanged(qc, pid)
                await invalidate.workflowsChanged(qc, target)
                toast.success(`Moved ${w.name} to ${others.find((p) => p.id === target)?.name ?? 'the project'}`)
                close()
              } catch (e) {
                setError(errorMessage(e))
              } finally {
                setBusy(false)
              }
            }}
            data-testid="workflow-move-confirm"
          >
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

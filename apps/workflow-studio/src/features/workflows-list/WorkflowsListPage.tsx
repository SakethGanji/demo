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
import { ProjectTopBar } from '@/features/project/components/ProjectTopBar'
import { WorkspaceOverlays } from '@/features/project/components/WorkspaceOverlays'
import { panelCls } from '@/features/project/components/WorkspaceLayout'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { useViewportWidth } from '@/shared/hooks/useViewport'
import { cn } from '@/shared/lib/utils'

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
      ...(n.continue_on_fail != null ? { continue_on_fail: n.continue_on_fail } : {}),
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
  const width = useViewportWidth()
  return (
    <div className="relative h-full w-full bg-canvas" data-testid="workflows-page">
      <ProjectTopBar />
      <div className={cn('absolute inset-x-3 bottom-3', width < 1024 ? 'top-[100px]' : 'top-[68px]')}>
        <WorkflowsPanel />
      </div>
      <WorkspaceOverlays />
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
      <Button size="sm" render={<Link to="/p/$project/workflows/$workflowId" params={{ project: slug, workflowId: 'new' }} />} data-testid="workflows-new">
        <Plus /> New workflow
      </Button>
    </>
  )

  return (
    <section className={cn(panelCls, 'flex h-full flex-col overflow-hidden')} data-testid="workflows-panel">
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
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-7 pb-8 pt-6">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-[17px] font-semibold tracking-[-0.01em]">Workflows</h1>
          <span className="min-w-0 truncate text-[13px] text-muted-foreground">· canvas automations in {project.name}</span>
          <div className="flex-1" />
          {rows.length > 0 && headerActions}
        </div>
        {importError && (
          <div className="mt-2 text-[12px] text-destructive" role="alert" data-testid="workflows-import-error">
            {importError}
          </div>
        )}

        {list.isLoading ? (
          <div className="mt-4 flex flex-col gap-2" data-testid="workflows-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : list.isError ? (
          <div className="mt-4 rounded-xl border border-border px-4 py-6 text-center text-[12.5px] text-destructive" data-testid="workflows-error">
            Couldn't load workflows · {errorMessage(list.error)}{' '}
            <button type="button" className="underline" onClick={() => list.refetch()}>
              Retry
            </button>
          </div>
        ) : rows.length === 0 ? (
          <div className="mt-4 rounded-xl border border-border">
            <EmptyState icon={<Workflow />} title="No workflows" actions={headerActions} data-testid="workflows-empty">
              Python routes are the main backend; use a workflow for scheduled or visual automations.
            </EmptyState>
          </div>
        ) : (
          <div className="mt-4 overflow-hidden rounded-xl border border-border">
            <Table className="text-[13px]" data-testid="workflows-table">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Name</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Handles route</TableHead>
                  <TableHead>Last run</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="w-11" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((w, i) => {
                  const handled = handledBy(w)
                  const detail = details[i]?.data
                  const run = lastRuns[i]?.data as RunListItem | null | undefined
                  const open = () => navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: w.id } })
                  return (
                    <TableRow
                      key={w.id}
                      className="cursor-pointer"
                      onClick={(e) => {
                        if ((e.target as HTMLElement).closest('a,button,[role="menu"]')) return
                        void open()
                      }}
                      data-testid={`workflow-row-${w.id}`}
                    >
                      <TableCell>
                        <Link
                          to="/p/$project/workflows/$workflowId"
                          params={{ project: slug, workflowId: w.id }}
                          className="inline-flex max-w-[280px] items-center gap-2 font-medium hover:underline"
                          data-testid={`workflow-open-${w.id}`}
                        >
                          <Workflow className="size-3.5 shrink-0 text-muted-foreground" />
                          <span className="truncate">{w.name}</span>
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground" data-testid={`workflow-trigger-${w.id}`}>
                        {detail ? triggerLabel(detail.definition, handled.length > 0) : details[i]?.isError ? '—' : <Skeleton className="h-4 w-16" />}
                      </TableCell>
                      <TableCell>
                        {handled.length ? (
                          <div className="flex flex-col gap-0.5">
                            {handled.map((r) => (
                              <Link
                                key={r.route_id}
                                to="/p/$project/runs"
                                params={{ project: slug }}
                                search={{ route: r.route_id }}
                                className="inline-flex items-center gap-1.5 hover:underline"
                                data-testid={`workflow-route-${w.id}`}
                              >
                                <MethodBadge method={r.method} size="sm" />
                                <span className="font-mono text-[12px]">{r.path}</span>
                              </Link>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell data-testid={`workflow-last-run-${w.id}`}>
                        {run ? (
                          <Link
                            to="/p/$project/runs/$executionId"
                            params={{ project: slug, executionId: run.id }}
                            className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
                          >
                            <StatusDot tone={runTone(run.status)} className="shadow-none" />
                            {run.status} · {timeAgo(run.start_time)}
                          </Link>
                        ) : lastRuns[i]?.isLoading ? (
                          <Skeleton className="h-4 w-20" />
                        ) : (
                          <span className="text-muted-foreground">never</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {w.active ? <Badge variant="success">Published</Badge> : <Badge variant="neutral">Draft</Badge>}
                      </TableCell>
                      <TableCell className="text-muted-foreground" title={w.updated_at}>
                        {shortDate(w.updated_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <RowMenu
                          workflow={w}
                          handled={handled}
                          onOpen={() => void open()}
                          onDuplicate={() => void duplicate(w)}
                          onMove={() => setMoving(w)}
                          onDelete={() => setDeleting(w)}
                        />
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
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
        render={<Button variant="ghost" size="icon-xs" aria-label={`Actions for ${w.name}`} data-testid={`workflow-menu-${w.id}`} />}
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

/**
 * Projects home (UI-DESIGN §2.1, `home.png`): project cards with health, base URL, the first
 * four routes (the Default project lists its workflows), counts and a ⋯ menu.
 */
import { useMemo, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AppWindow, ArrowUpDown, KeyRound, ListChecks, MoreHorizontal, Plus, Rocket, Route as RouteIcon, Search, Sparkles, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { projectsApi, routesApi } from '@/shared/api/endpoints'
import { isApiError, errorMessage } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { ProjectListItem } from '@/shared/api/types'
import { useProjects, useProjectSummary } from '../hooks/useProjectData'
import { timeAgo, pluralize } from '../lib/format'
import { sortRoutes } from '../lib/routes'
import { Brand, ThemeToggle } from './ProjectTopBar'
import { NewProjectDialog } from './NewProjectDialog'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Segmented } from '@/shared/components/ui/segmented'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { EmptyState } from '@/shared/components/ui/empty-state'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { Input } from '@/shared/components/ui/input'

type Filter = 'all' | 'healthy' | 'attention'

function needsAttention(p: ProjectListItem) {
  return p.health.status === 'degraded' || p.health.status === 'failing'
}

export function ProjectsHome() {
  const projects = useProjects()
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [recentFirst, setRecentFirst] = useState(true)
  const [newOpen, setNewOpen] = useState(false)

  const list = useMemo(() => {
    let xs = [...(projects.data ?? [])]
    if (filter === 'healthy') xs = xs.filter((p) => p.health.status === 'ok' || p.health.status === 'healthy')
    if (filter === 'attention') xs = xs.filter(needsAttention)
    if (query.trim()) {
      const q = query.toLowerCase()
      xs = xs.filter((p) => p.name.toLowerCase().includes(q) || p.slug.includes(q))
    }
    const t = (p: ProjectListItem) => new Date(p.last_run?.start_time ?? p.updated_at).getTime()
    xs.sort((a, b) => (recentFirst ? t(b) - t(a) : a.name.localeCompare(b.name)))
    // the Default project always sorts after real projects
    xs.sort((a, b) => Number(a.id === 'proj_default') - Number(b.id === 'proj_default'))
    return xs
  }, [projects.data, filter, query, recentFirst])

  const all = projects.data ?? []
  return (
    <div className="relative h-full w-full overflow-y-auto bg-canvas" data-testid="projects-home">
      <header className="sticky top-3 z-20 mx-3 mt-3 flex h-11 items-center gap-2 rounded-xl border border-panel-border bg-card/82 pl-2.5 pr-2 shadow-lg backdrop-blur-xl">
        <Brand />
        <span className="mx-1 h-4 w-px bg-border" />
        <Search className="size-3.5 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search projects…"
          className="h-8 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/80"
          data-testid="projects-search"
        />
        <Button variant="ghost" size="sm" onClick={() => setRecentFirst((v) => !v)} data-testid="projects-sort">
          <ArrowUpDown /> {recentFirst ? 'Recent' : 'Name'}
        </Button>
        <ThemeToggle />
        <span className="mx-1 h-4 w-px bg-border" />
        <Button size="sm" onClick={() => setNewOpen(true)} data-testid="new-project">
          <Plus /> New project
        </Button>
      </header>

      <main className="mx-auto max-w-[1176px] px-6 pb-16 pt-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-[22px] font-semibold tracking-[-0.015em]">Projects</h1>
            <p className="mt-1 text-[13px] text-muted-foreground">Each project has a Python backend, its data, and the apps built on it.</p>
          </div>
          <Segmented<Filter>
            value={filter}
            onChange={setFilter}
            aria-label="Filter projects"
            options={[
              { key: 'all', label: 'All', count: all.length },
              { key: 'healthy', label: 'Healthy' },
              { key: 'attention', label: 'Needs attention', count: all.filter(needsAttention).length },
            ]}
          />
        </div>

        {projects.isLoading ? (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[244px] rounded-xl" />
            ))}
          </div>
        ) : projects.isError ? (
          <EmptyState title="Couldn't load projects" actions={<Button variant="outline" size="sm" onClick={() => projects.refetch()}>Retry</Button>}>
            {errorMessage(projects.error)}
          </EmptyState>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="project-grid">
            {list.map((p) => (
              <ProjectCard key={p.id} project={p} />
            ))}
            <button
              type="button"
              onClick={() => setNewOpen(true)}
              className="flex min-h-[244px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border text-center hover:bg-card/60"
              data-testid="new-project-card"
            >
              <span className="inline-flex size-10 items-center justify-center rounded-lg bg-card text-primary shadow-xs">
                <Plus className="size-4" />
              </span>
              <span className="font-medium">New project</span>
              <span className="text-xs text-muted-foreground">A backend, data and apps — together</span>
            </button>
          </div>
        )}
      </main>
      <NewProjectDialog open={newOpen} onOpenChange={setNewOpen} />
    </div>
  )
}

function ProjectCard({ project: p }: { project: ProjectListItem }) {
  const isDefault = p.id === 'proj_default'
  const summary = useProjectSummary(p.id)
  const missing = summary.data?.missing_secrets ?? 0
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const P = { project: p.slug }
  const empty = p.counts.routes === 0 && p.counts.apps === 0 && (summary.data?.counts.modules ?? 0) === 0 && !isDefault

  return (
    <div
      className="group flex min-h-[244px] cursor-pointer flex-col rounded-xl border border-panel-border bg-card shadow-xs transition-shadow hover:shadow-md"
      onClick={() => navigate({ to: '/p/$project', params: P })}
      data-testid={`project-card-${p.slug}`}
      role="link"
    >
      <div className="flex items-start gap-2 px-4 pt-3.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold">{p.name}</div>
          <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
            {isDefault ? <span className="font-sans">Workflows created before projects existed</span> : `/api/p/${p.slug}`}
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => e.stopPropagation()}
            render={<Button variant="ghost" size="icon-xs" aria-label="Project actions" data-testid={`project-menu-${p.slug}`} />}
          >
            <MoreHorizontal />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenuItem onClick={() => navigate({ to: '/p/$project', params: P })}>Open</DropdownMenuItem>
            <DropdownMenuItem onClick={() => navigate({ to: '/p/$project', params: P, search: { sheet: 'settings', section: 'general' } })}>
              Settings
            </DropdownMenuItem>
            {!isDefault && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)} data-testid={`project-delete-${p.slug}`}>
                  Delete…
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        <CardHealth project={p} missing={missing} isDefault={isDefault} empty={empty} />
      </div>

      <div className="mx-3 mt-3 flex-1 rounded-lg border border-border bg-muted/30 px-2.5 py-2">
        {isDefault ? <DefaultWorkflows pid={p.id} /> : empty ? <EmptyBackend slug={p.slug} /> : <CardRoutes pid={p.id} />}
      </div>

      <div className="mt-3 flex items-center gap-3 border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
        {isDefault ? (
          <span className="inline-flex items-center gap-1.5">
            <Workflow className="size-3.5" /> {pluralize(p.counts.workflows, 'workflow')}
          </span>
        ) : empty ? (
          <span className="inline-flex items-center gap-1.5">
            <ListChecks className="size-3.5" /> Setup
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <RouteIcon className="size-3.5" /> {pluralize(p.counts.routes, 'route')}
          </span>
        )}
        <span className="inline-flex items-center gap-1.5">
          <AppWindow className="size-3.5" /> {pluralize(p.counts.apps, 'app')}
        </span>
        {missing > 0 && (
          <span className="inline-flex items-center gap-1.5 text-warning-text" data-testid={`card-missing-${p.slug}`}>
            <KeyRound className="size-3.5" /> {pluralize(missing, 'missing secret')}
          </span>
        )}
        {!missing && summary.data?.active_releases?.[p.live_environment] && (
          <span className="inline-flex items-center gap-1.5">
            <Rocket className="size-3.5" /> {p.live_environment} #{summary.data.active_releases[p.live_environment]!.number}
          </span>
        )}
        <span className="ml-auto">{timeAgo(p.last_run?.start_time ?? p.updated_at)}</span>
      </div>
      <DeleteProjectDialog project={p} open={confirmDelete} onOpenChange={setConfirmDelete} />
    </div>
  )
}

function CardHealth({ project: p, missing, isDefault, empty }: { project: ProjectListItem; missing: number; isDefault: boolean; empty: boolean }) {
  if (isDefault) return <Badge variant="outline">Legacy</Badge>
  if (empty) return <Badge variant="secondary">New</Badge>
  const s = p.health.status
  if (s === 'degraded' || (missing > 0 && s !== 'failing'))
    return (
      <Badge variant="warning" data-testid={`card-health-${p.slug}`}>
        <StatusDot tone="warn" /> Degraded
      </Badge>
    )
  if (s === 'failing')
    return (
      <Badge variant="destructive" data-testid={`card-health-${p.slug}`}>
        <StatusDot tone="err" /> Failing
      </Badge>
    )
  if (s === 'ok' || s === 'healthy')
    return (
      <Badge variant="success" data-testid={`card-health-${p.slug}`}>
        <StatusDot tone="ok" className="shadow-none" /> Healthy
      </Badge>
    )
  return <Badge variant="neutral">No runs</Badge>
}

function CardRoutes({ pid }: { pid: string }) {
  const routes = useQuery({ queryKey: qk.routes(pid), queryFn: () => routesApi.list(pid) })
  const rs = sortRoutes(routes.data ?? []).slice(0, 4)
  if (routes.isLoading) return <Skeleton className="h-[96px] w-full" />
  return (
    <div className="flex flex-col gap-1.5">
      {rs.map((r) => (
        <div key={r.id} className="flex items-center gap-2 text-xs">
          <MethodBadge method={r.method} size="sm" />
          <span className="truncate font-mono">{r.path}</span>
          <span className="ml-auto truncate font-mono text-[11px] text-muted-foreground">
            {r.handler.type === 'stub' ? <span className="text-warning-text">stub · 501</span> : r.name}
          </span>
        </div>
      ))}
    </div>
  )
}

function DefaultWorkflows({ pid }: { pid: string }) {
  const wfs = useQuery({ queryKey: qk.workflows(pid), queryFn: () => projectsApi.listWorkflows(pid) })
  const xs = (wfs.data ?? []).slice(0, 4)
  if (!xs.length) return <div className="py-6 text-center text-xs text-muted-foreground">No workflows</div>
  return (
    <div className="flex flex-col gap-1.5">
      {xs.map((w) => (
        <div key={w.id} className="flex items-center gap-2 text-xs">
          <Workflow className="size-3.5 text-muted-foreground" />
          <span className="truncate font-mono">{w.name}</span>
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">{w.node_count} nodes</span>
        </div>
      ))}
    </div>
  )
}

function EmptyBackend({ slug }: { slug: string }) {
  return (
    <div className="flex h-full min-h-[96px] items-center justify-center gap-1.5 rounded-md border border-dashed border-border text-xs text-muted-foreground">
      <Sparkles className="size-3.5 text-ai" /> No backend yet —
      <Link
        to="/p/$project"
        params={{ project: slug }}
        className="text-primary hover:underline"
        onClick={(e) => e.stopPropagation()}
      >
        Set up
      </Link>
    </div>
  )
}

function DeleteProjectDialog({ project, open, onOpenChange }: { project: ProjectListItem; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient()
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const del = useMutation({
    mutationFn: () => projectsApi.remove(project.id),
    onSuccess: async () => {
      await invalidate.projectDeleted(qc, project.id)
      toast.success(`Deleted ${project.name}`)
      onOpenChange(false)
    },
    onError: (e) => {
      if (isApiError(e, 'project_not_empty')) {
        const d = e.details as Record<string, unknown>
        const parts = Object.entries(d)
          .filter(([, v]) => typeof v === 'number' && v > 0)
          .map(([k, v]) => `${v} ${k}`)
        setError(`This project isn't empty${parts.length ? `: ${parts.join(', ')}` : ''}. Delete its contents first.`)
      } else setError(errorMessage(e))
    },
  })
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent onClick={(e) => e.stopPropagation()}>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {project.name}?</AlertDialogTitle>
          <AlertDialogDescription>Type the project slug to confirm. This can't be undone.</AlertDialogDescription>
        </AlertDialogHeader>
        <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={project.slug} className="font-mono" />
        {error && (
          <div className="text-xs text-destructive" data-testid="project-not-empty">
            {error}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <Button variant="destructive" disabled={typed !== project.slug || del.isPending} onClick={() => del.mutate()}>
            Delete project
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/**
 * Projects home (DESIGN §1, `design/projects.html`): the home sky behind a glass top bar, the greeting with a
 * three-figure stats strip, glass project cards (system diagram + 24h activity), a "New project" tile and
 * "Start something new" (the same six-card picker as the setup guide). No warning banner: a missing secret shows in the line under the
 * greeting and in Needs attention.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AppWindow, ArrowUpDown, KeyRound, ListChecks, MoreHorizontal, Plus, Route as RouteIcon, Search, Sparkles, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { projectsApi, routesApi, tablesApi } from '@/shared/api/endpoints'
import { isApiError, errorMessage } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { ProjectListItem } from '@/shared/api/types'
import { useProjects } from '../hooks/useProjectData'
import { timeAgo, pluralize } from '../lib/format'
import { sortRoutes } from '../lib/routes'
import { Brand, BrandMark, HomeNav, ThemeToggle, TopBarFrame } from './TopBar'
import { ProjectAvatar } from './ProjectTopBar'
import { NewProjectDialog } from './NewProjectDialog'
import { CardDiagram } from '../home/CardDiagram'
import { BlueprintArt, EmptyOrbitArt } from '../home/art'
import { HomeStartPanel } from '../home/HomeStartPanel'
import { StartPicker } from './setup/StartPicker'
import { isProjectEmpty, type StartKind } from '../lib/nextSteps'
import { greeting, isDefaultProject, useActivityBuckets, useAppCounts, useMissingByProject, type MissingInfo } from '../home/useHomeData'
import { useStartProject } from '../home/useStartProject'
import { HomeSky } from '@/shared/components/space/HomeSky'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Kbd } from '@/shared/components/ui/kbd'
import { Roll } from '@/shared/components/ui/roll'
import { Segmented } from '@/shared/components/ui/segmented'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Sparkline } from '@/shared/components/ui/charts'
import { Tip } from '@/shared/components/ui/tooltip'
import { Tile, Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
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

function needsAttention(p: ProjectListItem, missing = 0) {
  return p.health.status === 'degraded' || p.health.status === 'failing' || missing > 0
}

const today = () => new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })

export function ProjectsHome() {
  const projects = useProjects()
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [recentFirst, setRecentFirst] = useState(true)
  const [newOpen, setNewOpen] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)
  const start = useStartProject()
  const [startKind, setStartKind] = useState<StartKind | null>(null)

  const all = useMemo(() => projects.data ?? [], [projects.data])
  const missing = useMissingByProject(all)
  const activity = useActivityBuckets(all)
  const appCounts = useAppCounts(all)

  // "/" focuses the search, Esc leaves it
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))
      if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        searchRef.current?.focus()
        searchRef.current?.select()
      } else if (e.key === 'Escape' && t === searchRef.current) searchRef.current?.blur()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const list = useMemo(() => {
    let xs = [...all]
    if (filter === 'healthy') xs = xs.filter((p) => (p.health.status === 'ok' || p.health.status === 'healthy') && !missing[p.id])
    if (filter === 'attention') xs = xs.filter((p) => needsAttention(p, missing[p.id]?.count))
    if (query.trim()) {
      const q = query.toLowerCase()
      xs = xs.filter((p) => p.name.toLowerCase().includes(q) || p.slug.includes(q))
    }
    const t = (p: ProjectListItem) => new Date(p.last_run?.start_time ?? p.updated_at).getTime()
    xs.sort((a, b) => (recentFirst ? t(b) - t(a) : a.name.localeCompare(b.name)))
    // the Default project always sorts after real projects
    xs.sort((a, b) => Number(isDefaultProject(a)) - Number(isDefaultProject(b)))
    return xs
  }, [all, filter, query, recentFirst, missing])

  const runs24 = all.reduce((n, p) => n + p.health.runs_24h, 0)
  const failed24 = all.reduce((n, p) => n + p.health.failed_24h, 0)
  const missingList = Object.values(missing)
  const missingTotal = missingList.reduce((n, m) => n + m.count, 0)
  const firstMissing: MissingInfo | undefined = missingList[0]
  const attnProjects = all.filter((p) => !missing[p.id] && needsAttention(p)).length
  const attnCount = missingTotal + attnProjects
  const attnSub = attnCount === 0 ? 'all clear' : attnProjects === 0 ? (missingTotal === 1 ? 'secret' : 'secrets') : missingTotal === 0 ? (attnProjects === 1 ? 'project' : 'projects') : 'items'
  const realCount = all.filter((p) => !isDefaultProject(p)).length || all.length

  return (
    <div className="relative h-full w-full overflow-y-auto" data-testid="projects-home">
      <HomeSky />
      <TopBarFrame position="sticky" className="gap-2">
        <Brand />
        <span className="mx-1 h-4 w-px bg-hairline" aria-hidden />
        <HomeNav />
        <span className="mx-1 h-4 w-px bg-hairline" aria-hidden />
        <Search className="size-3.5 shrink-0 text-muted-foreground" />
        <input
          ref={searchRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search projects…"
          aria-label="Search projects"
          className="h-8 min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/80"
          data-testid="projects-search"
        />
        <Tip label="Focus search" kbd="/">
          <span className="inline-flex">
            <Kbd>/</Kbd>
          </span>
        </Tip>
        <Button variant="ghost" size="sm" onClick={() => setRecentFirst((v) => !v)} data-testid="projects-sort">
          <ArrowUpDown /> {recentFirst ? 'Recent' : 'Name'}
        </Button>
        <ThemeToggle />
        <span className="mx-1 h-4 w-px bg-hairline" aria-hidden />
        <Button size="sm" onClick={() => setNewOpen(true)} data-testid="new-project">
          <Plus /> New project
        </Button>
      </TopBarFrame>

      <main className="relative z-10 mx-auto max-w-[1176px] px-6 pt-10 pb-16">
        <section className="flex flex-wrap items-end justify-between gap-x-6 gap-y-5" data-testid="home-hero">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <BrandMark className="size-[18px]" />
              Command Studio
              <span className="opacity-45">·</span>
              <span>{today()}</span>
            </div>
            <h1 className="mt-2.5 text-[30px] leading-[1.15] font-[650] tracking-[-0.032em] whitespace-nowrap">{greeting()}</h1>
            <p className="mt-1.5 flex flex-wrap items-center text-[13px] text-muted-foreground" data-testid="home-subline">
              <span>{pluralize(realCount, 'project')}</span>
              <span className="px-1.5 opacity-50">·</span>
              <span>{runs24.toLocaleString('en-US')} runs in 24h</span>
              {firstMissing && (
                <>
                  <span className="px-1.5 opacity-50">·</span>
                  <Link
                    to="/p/$project/secrets"
                    params={{ project: firstMissing.project.slug }}
                    className="inline-flex items-center gap-1.5 text-warning-text underline-offset-3 hover:underline active:scale-[.98]"
                    data-testid="home-missing-link"
                  >
                    <StatusDot tone="warn" flat />
                    {missingTotal} {missingTotal === 1 ? 'secret' : 'secrets'} missing in {firstMissing.env}
                  </Link>
                </>
              )}
            </p>
          </div>

          <Tile className="grid grid-cols-[auto_auto_auto] p-0" data-testid="home-stats">
            <StatCell label="Runs · 24h" testId="stat-runs" className="min-w-[181px]">
              <Roll value={runs24} />
              <Tip label={`${runs24} runs in 24h · ${failed24} failed`}>
                <span className="self-center">
                  <Sparkline values={activity.total} height={22} className="w-[72px]" barClassName="min-h-[3px]" />
                </span>
              </Tip>
            </StatCell>
            <StatCell label="Interface" divider testId="stat-apps">
              <Roll value={all.reduce((n, p) => n + p.counts.apps, 0)} />
              <small className="text-[12px] font-normal tracking-normal text-muted-foreground">
                {appCounts.loaded === 0 ? '' : appCounts.published > 0 ? `${appCounts.published} published${appCounts.draft ? ` · ${appCounts.draft} draft` : ''}` : 'draft'}
              </small>
            </StatCell>
            <StatCell label="Needs attention" divider testId="stat-attention">
              <Roll value={attnCount} className={attnCount > 0 ? 'text-warning-text' : undefined} />
              <small className="text-[12px] font-normal tracking-normal text-muted-foreground">{attnSub}</small>
            </StatCell>
          </Tile>
        </section>

        <SectionRow title="Projects" meta={String(all.length)}>
          <Segmented<Filter>
            value={filter}
            onChange={setFilter}
            aria-label="Filter projects"
            data-testid="projects-filter"
            options={[
              { key: 'all', label: 'All', count: all.length },
              { key: 'healthy', label: 'Healthy' },
              { key: 'attention', label: 'Needs attention', count: all.filter((p) => needsAttention(p, missing[p.id]?.count)).length },
            ]}
          />
        </SectionRow>

        {projects.isLoading ? (
          <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[252px] rounded-xl" />
            ))}
          </div>
        ) : projects.isError ? (
          <EmptyState title="Couldn't load projects" actions={<Button variant="outline" size="sm" onClick={() => projects.refetch()}>Retry</Button>}>
            {errorMessage(projects.error)}
          </EmptyState>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="project-grid">
            {list.map((p) => (
              <ProjectCard key={p.id} project={p} missing={missing[p.id]} buckets={activity.byId[p.id]} />
            ))}
            <Tile
              interactive
              role="button"
              tabIndex={0}
              onClick={() => setNewOpen(true)}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setNewOpen(true))}
              className="flex min-h-[252px] flex-col items-center justify-center gap-1 p-5 text-center focus-visible:shadow-focus focus-visible:outline-none"
              data-testid="new-project-card"
            >
              <div className="mb-3 w-[208px] opacity-80">
                <BlueprintArt />
              </div>
              <span className="font-medium">New project</span>
              <span className="text-[12px] text-muted-foreground">A backend, data and apps — together</span>
            </Tile>
          </div>
        )}

        <SectionRow title="Start something new" meta="A page, an API, a workflow, a file, a template or a sentence" />
        <div data-testid="home-templates">
          <StartPicker
            variant="home"
            value={startKind}
            onPick={(k) => setStartKind((cur) => (cur === k ? null : k))}
            disabled={!!start.busy}
            panel={startKind && <HomeStartPanel key={startKind} kind={startKind} start={start} />}
          />
        </div>
      </main>
      <NewProjectDialog open={newOpen} onOpenChange={setNewOpen} />
    </div>
  )
}

function StatCell({ label, divider, testId, className, children }: { label: string; divider?: boolean; testId: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('min-w-[150px] py-4 pr-6 pl-5', className, divider && 'shadow-[inset_1px_0_0_var(--hairline)]')} data-testid={testId}>
      <div className="text-[12px] font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-baseline gap-2 text-[28px] leading-[1.15] font-semibold tracking-[-0.03em] tabular-nums">{children}</div>
    </div>
  )
}

function SectionRow({ title, meta, children }: { title: string; meta?: string; children?: React.ReactNode }) {
  return (
    <div className="mt-8 flex items-center gap-3">
      <h2 className="text-section font-semibold tracking-[-0.015em]">{title}</h2>
      {meta && <span className="text-[12px] text-muted-foreground">{meta}</span>}
      <span className="h-px min-w-6 flex-1" style={{ borderBottom: '1px dotted var(--faint)' }} aria-hidden />
      {children}
    </div>
  )
}

function ProjectCard({ project: p, missing: miss, buckets }: { project: ProjectListItem; missing?: MissingInfo & { count: number }; buckets?: number[] }) {
  const isDefault = isDefaultProject(p)
  const missing = miss?.count ?? 0
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const P = { project: p.slug }
  const empty = !isDefault && isProjectEmpty({ ...p.counts, modules: p.counts.modules ?? 0 })
  const lastRun = p.last_run?.start_time

  return (
    <Tile
      interactive
      className="group flex min-h-[252px] flex-col"
      onClick={() => navigate({ to: '/p/$project', params: P })}
      data-testid={`project-card-${p.slug}`}
      role="link"
    >
      <div className="flex items-center gap-2.5 px-4 pt-4">
        {isDefault ? (
          <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-ctl bg-recess text-[12.5px] font-semibold text-muted-foreground" aria-hidden>
            D
          </span>
        ) : (
          <ProjectAvatar name={p.name} size={28} className="rounded-ctl" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold tracking-[-0.01em]">{p.name}</div>
          <div className={cn('mt-px truncate text-[12px] text-muted-foreground', !isDefault && 'font-mono')}>
            {isDefault ? 'Workflows created before projects existed' : `/api/p/${p.slug}`}
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => e.stopPropagation()}
            render={
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Project actions"
                className="opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 data-popup-open:opacity-100"
                data-testid={`project-menu-${p.slug}`}
              />
            }
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

      <Well className="mx-2 mt-3 flex flex-1 flex-col justify-center p-3">
        {isDefault ? (
          <DefaultWorkflows pid={p.id} count={p.counts.workflows} />
        ) : empty ? (
          <EmptyBackend slug={p.slug} />
        ) : (
          <>
            <CardArt project={p} secretKey={miss?.key} />
            <div className="mt-3 flex items-center gap-3">
              <Tip label={`${p.health.runs_24h} runs in 24h · ${p.health.failed_24h} failed`}>
                <span className="flex-1">
                  <Sparkline values={buckets ?? Array(12).fill(0)} height={22} />
                </span>
              </Tip>
              <span className="text-[12px] whitespace-nowrap text-muted-foreground" data-testid={`card-runs-${p.slug}`}>
                <b className="font-semibold text-foreground">{p.health.runs_24h.toLocaleString('en-US')}</b> runs · 24h
              </span>
            </div>
          </>
        )}
      </Well>

      <div className="flex items-center gap-3 px-4 py-3 text-[12px] text-muted-foreground [&_svg]:size-3.5 [&>span]:inline-flex [&>span]:items-center [&>span]:gap-1.5">
        {isDefault ? (
          <span className="[&_svg]:text-hue-flow">
            <Workflow /> {pluralize(p.counts.workflows, 'workflow')}
          </span>
        ) : empty ? (
          <span>
            <ListChecks /> Setup
          </span>
        ) : (
          <span className="[&_svg]:text-hue-backend">
            <RouteIcon /> {pluralize(p.counts.routes, 'route')}
          </span>
        )}
        <span className="[&_svg]:text-hue-apps">
          <AppWindow /> {pluralize(p.counts.apps, 'app')}
        </span>
        {missing > 0 && (
          <span className="text-warning-text [&_svg]:text-hue-secrets" data-testid={`card-missing-${p.slug}`}>
            <KeyRound /> {pluralize(missing, 'missing secret')}
          </span>
        )}
        <span className="ml-auto">
          {lastRun ? (
            timeAgo(lastRun)
          ) : (
            <Tip label="No runs yet">
              <span>—</span>
            </Tip>
          )}
        </span>
      </div>
      <DeleteProjectDialog project={p} open={confirmDelete} onOpenChange={setConfirmDelete} />
    </Tile>
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
        <StatusDot tone="ok" flat /> Healthy
      </Badge>
    )
  return <Badge variant="neutral">No runs</Badge>
}

/** the project's system diagram from its real routes, first table, first app and first missing secret */
function CardArt({ project: p, secretKey }: { project: ProjectListItem; secretKey?: string }) {
  const routes = useQuery({ queryKey: qk.routes(p.id), queryFn: () => routesApi.list(p.id) })
  const tables = useQuery({ queryKey: qk.tables(p.id), queryFn: () => tablesApi.list(p.id), enabled: p.counts.tables > 0 })
  const apps = useQuery({ queryKey: qk.apps(p.id), queryFn: () => projectsApi.listApps(p.id), enabled: p.counts.apps > 0 })
  if (routes.isLoading) return <Skeleton className="h-[98px] w-full" />
  const rs = sortRoutes(routes.data ?? [])
  const table = tables.data?.[0]
  const app = apps.data?.[0]
  return (
    <CardDiagram
      routes={rs.map((r) => ({ key: r.id, method: r.method, path: r.path, hot: r.used_by_count > 0 }))}
      table={table ? { name: table.name } : null}
      app={app ? { name: app.name } : null}
      secret={secretKey ? { key: secretKey } : null}
    />
  )
}

function DefaultWorkflows({ pid, count }: { pid: string; count: number }) {
  const wfs = useQuery({ queryKey: qk.workflows(pid), queryFn: () => projectsApi.listWorkflows(pid), enabled: count > 0 })
  if (count === 0) return <EmptyOrbitArt />
  const xs = (wfs.data ?? []).slice(0, 4)
  return (
    <div className="flex flex-col gap-1.5 py-1">
      {xs.map((w) => (
        <div key={w.id} className="flex items-center gap-2 text-xs">
          <Workflow className="size-3.5 text-hue-flow" />
          <span className="truncate font-mono">{w.name}</span>
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">{w.node_count} nodes</span>
        </div>
      ))}
    </div>
  )
}

function EmptyBackend({ slug }: { slug: string }) {
  return (
    <div className="flex min-h-[110px] items-center justify-center gap-1.5 text-xs text-muted-foreground">
      <Sparkles className="size-3.5 text-hue-ai" /> Empty —
      <Link
        to="/p/$project"
        params={{ project: slug }}
        className="text-primary hover:underline"
        onClick={(e) => e.stopPropagation()}
      >
        Start
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

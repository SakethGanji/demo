/**
 * Overview (UI-DESIGN §3.2, `project.png`) — or the first-run guide (§3.3).
 */
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AppWindow, Code2, Copy, KeyRound, Play, Server, Sparkles, WandSparkles, Plus, AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '../ProjectContext'
import {
  useMissingSecrets,
  useModules,
  useProjectApps,
  useProjectSummary,
  useRoutes,
  useSecrets,
  useTables,
} from '../hooks/useProjectData'
import { useRunStats } from '../hooks/useRunStats'
import { useSetup } from '../hooks/useSetup'
import { groupRoutes, moduleNameOf, AUTH_LABEL } from '../lib/routes'
import { formatMs, timeAgo, shortAge, pluralize } from '../lib/format'
import { SetupGuide } from './SetupGuide'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { appApiApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { errorMessage } from '@/shared/api/http'
import type { ApiAppListItem } from '@/shared/lib/api'
import type { HealthStatus, ProjectSummary } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'

const card = 'rounded-xl border border-border bg-card'
const cardH = 'flex h-12 items-center gap-2.5 border-b border-border px-4'

export function HealthBadge({ summary }: { summary: ProjectSummary | undefined }) {
  const s: HealthStatus | undefined = summary?.health.status
  const live = summary?.project.live_environment ?? 'prod'
  const secretCase = s === 'degraded' && (summary?.missing_secrets ?? 0) > 0 && (summary?.health.failed_24h ?? 0) === 0
  if (s === 'ok' || s === 'healthy')
    return (
      <Badge variant="success" data-testid="health-badge" data-health="ok">
        <StatusDot tone="ok" className="shadow-none" /> Healthy
      </Badge>
    )
  if (s === 'degraded')
    return (
      <Badge variant="warning" data-testid="health-badge" data-health="degraded">
        <StatusDot tone="warn" /> Degraded{secretCase ? ` · secret missing in ${live}` : ''}
      </Badge>
    )
  if (s === 'failing')
    return (
      <Badge variant="destructive" data-testid="health-badge" data-health="failing">
        <StatusDot tone="err" /> Failing
      </Badge>
    )
  return (
    <Badge variant="neutral" data-testid="health-badge" data-health="unknown">
      New
    </Badge>
  )
}

export function OverviewView() {
  const { pid } = useProjectCtx()
  const search = useSearch({ strict: false }) as { setup?: 1 }
  const setup = useSetup(pid, search.setup === 1)
  if (setup.loading) return <OverviewSkeleton />
  if (setup.show) return <SetupGuide setup={setup} />
  return <Overview />
}

function OverviewSkeleton() {
  return (
    <div className="h-full rounded-xl border border-panel-border bg-card p-8 shadow-xs" data-testid="overview-loading">
      <Skeleton className="mb-3 h-7 w-56" />
      <Skeleton className="mb-6 h-5 w-96" />
      <Skeleton className="mb-4 h-[72px] w-full" />
      <Skeleton className="mb-2 h-10 w-full" />
      <Skeleton className="mb-2 h-10 w-full" />
      <Skeleton className="h-10 w-full" />
    </div>
  )
}

function Overview() {
  const { project, pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const summary = useProjectSummary(pid)
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const tables = useTables(pid)
  const apps = useProjectApps(pid)
  const stats = useRunStats(pid)
  const P = { project: slug }

  if (summary.isError)
    return (
      <div className="h-full rounded-xl border border-panel-border bg-card p-8 shadow-xs">
        <div className={cn(card, 'flex items-center gap-3 p-4')} data-testid="overview-error">
          <AlertTriangle className="size-4 text-destructive" />
          <span className="flex-1">Couldn't load project summary · {errorMessage(summary.error)}</span>
          <Button variant="outline" size="sm" onClick={() => summary.refetch()}>
            Retry
          </Button>
        </div>
      </div>
    )
  if (!summary.data || !routes.data || !modules.data) return <OverviewSkeleton />

  const s = summary.data
  const base = `/api/p/${slug}`
  const live = project.live_environment
  const liveRelease = s.active_releases?.[live]
  const hasReleaseEnv = project.environments.some((e) => e.kind === 'release')
  const rows = (tables.data ?? []).reduce((n, t) => n + (t.row_count ?? 0), 0)
  const published = (apps.data ?? []).filter((a) => a.active).length
  const openCreateUi = () => navigate({ to: '/p/$project', params: P, search: { dialog: 'create-ui' } })

  return (
    <div className="h-full overflow-y-auto rounded-xl border border-panel-border bg-card shadow-xs" data-testid="overview-panel">
      <div className="mx-auto max-w-[1180px] px-8 pb-10 pt-7">
        {/* Hero */}
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <h1 className="text-[22px] font-semibold leading-7 tracking-[-0.015em]" data-testid="project-name">
                {project.name}
              </h1>
              <HealthBadge summary={s} />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
              <button
                type="button"
                className="inline-flex h-7 items-center gap-2 rounded-md border border-border px-2.5 font-mono text-xs text-foreground hover:bg-muted"
                onClick={() => {
                  navigator.clipboard?.writeText(base).catch(() => undefined)
                  toast.success('Base URL copied')
                }}
                data-testid="base-url"
              >
                {base} <Copy className="size-3.5 text-muted-foreground" />
              </button>
              <span>
                Python backend · {pluralize(modules.data.length, 'module')} · {pluralize(routes.data.length, 'route')}
              </span>
              {hasReleaseEnv && (
                <span>
                  ·{' '}
                  {liveRelease
                    ? `${live} serves release #${liveRelease.number}`
                    : `${live} serves the latest saved code (no release yet)`}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              render={<Link to="/p/$project/backend/$module" params={{ ...P, module: '~new' }} search={{ generate: 1 }} />}
              data-testid="hero-generate"
            >
              <Sparkles className="text-ai" /> Generate
            </Button>
            <Button onClick={openCreateUi} data-testid="hero-create-ui">
              <WandSparkles /> Create UI from backend
            </Button>
          </div>
        </div>

        {/* Stat strip */}
        <div className={cn(card, 'mt-6 grid grid-cols-5 divide-x divide-border')} data-testid="stat-strip">
          <Stat label="Routes" value={routes.data.length} testId="stat-routes" />
          <Stat label="Tables" value={(tables.data ?? []).length} meta={`· ${pluralize(rows, 'row')}`} testId="stat-tables" />
          <Stat label="Apps" value={(apps.data ?? []).length} meta={`· ${published} published`} testId="stat-apps" />
          <Stat
            label="Runs · 24h"
            value={s.health.runs_24h}
            testId="stat-runs"
            extra={stats.data && <Sparkline values={stats.data.sparkline} />}
          />
          <Stat
            label="Failed · 24h"
            value={s.health.failed_24h}
            meta={`· p50 ${formatMs(stats.data?.p50 ?? null)}`}
            testId="stat-failed"
          />
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-w-0 flex-col gap-4">
            <BackendCard />
            <AppsCard apps={apps.data ?? []} routesTotal={routes.data.length} onCreateUi={openCreateUi} />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <RecentRunsCard />
            <SecretsCard />
            {hasReleaseEnv && <ReleasesCard summary={s} />}
          </div>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value, meta, extra, testId }: { label: string; value: number; meta?: string; extra?: React.ReactNode; testId: string }) {
  return (
    <div className="px-4 py-3" data-testid={testId}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="text-xl font-semibold tabular-nums" data-testid={`${testId}-value`}>
          {value}
        </span>
        {meta && <span className="text-xs text-muted-foreground">{meta}</span>}
        {extra}
      </div>
    </div>
  )
}

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(1, ...values)
  return (
    <span className="ml-2 inline-flex h-4 items-end gap-[2px]" aria-hidden>
      {values.map((v, i) => (
        <span key={i} className="w-[3px] rounded-[1px] bg-success/70" style={{ height: `${Math.max(2, (v / max) * 16)}px` }} />
      ))}
    </span>
  )
}

function BackendCard() {
  const { pid, slug } = useProjectCtx()
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const stats = useRunStats(pid)
  const navigate = useNavigate()
  const groups = groupRoutes(routes.data ?? [], modules.data)
  const P = { project: slug }
  return (
    <section className={card} data-testid="backend-card">
      <div className={cardH}>
        <Server className="size-4 text-muted-foreground" />
        <span className="text-[13px] font-semibold">Backend</span>
        <div className="flex-1" />
        {modules.data?.[0] && (
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/p/$project/backend/$module" params={{ ...P, module: modules.data[0].name }} />}
            data-testid="open-editor"
          >
            <Code2 /> Open editor
          </Button>
        )}
      </div>
      {groups.length === 0 ? (
        <div className="px-4 py-6 text-[13px] text-muted-foreground" data-testid="backend-card-empty">
          No routes yet.{' '}
          <Link to="/p/$project/backend/$module" params={{ ...P, module: '~new' }} search={{ generate: 1 }} className="text-ai-text hover:underline">
            Generate a backend
          </Link>{' '}
          or write one yourself.
        </div>
      ) : (
        <Table dense>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Method</TableHead>
              <TableHead>Path</TableHead>
              <TableHead>Operation</TableHead>
              <TableHead>Auth</TableHead>
              <TableHead>Last call · 24h</TableHead>
              <TableHead className="text-right">p50</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.map((g) => (
              <GroupRows key={g.key} label={g.label}>
                {g.routes.map((r) => {
                  const st = stats.data?.routes.get(r.id)
                  const m = moduleNameOf(r, modules.data)
                  return (
                    <TableRow key={r.id} data-testid={`route-row-${r.name}`}>
                      <TableCell>
                        <MethodBadge method={r.method} />
                      </TableCell>
                      <TableCell className="font-mono text-[12.5px]">{r.path}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {r.name}
                        {r.handler.type === 'stub' && (
                          <Badge variant="stub" className="ml-2">
                            stub
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{AUTH_LABEL[r.auth] ?? r.auth}</Badge>
                      </TableCell>
                      <TableCell className="text-xs">
                        {st ? (
                          <span className="inline-flex items-center gap-1.5">
                            <StatusDot tone={runTone(st.last.status)} /> {st.last.status} · {timeAgo(st.last.start_time)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">no calls</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{st?.p50 != null ? formatMs(st.p50) : '—'}</TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={`Test ${r.method} ${r.path}`}
                          data-testid={`route-test-${r.name}`}
                          onClick={() =>
                            m
                              ? navigate({ to: '/p/$project/backend/$module', params: { ...P, module: m }, search: { route: r.name } })
                              : navigate({ to: '/p/$project/runs', params: P, search: { route: r.id } })
                          }
                        >
                          <Play />
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </GroupRows>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  )
}

function GroupRows({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <TableRow className="bg-muted/40 hover:bg-muted/40">
        <TableCell colSpan={7} className="h-8 font-mono text-xs text-muted-foreground">
          {label}
        </TableCell>
      </TableRow>
      {children}
    </>
  )
}

function AppsCard({ apps, routesTotal, onCreateUi }: { apps: ApiAppListItem[]; routesTotal: number; onCreateUi: () => void }) {
  return (
    <section className={card} data-testid="apps-card">
      <div className={cardH}>
        <AppWindow className="size-4 text-muted-foreground" />
        <span className="text-[13px] font-semibold">Apps</span>
        <div className="flex-1" />
        {apps.length > 0 && (
          <Button variant="ghost" size="sm" onClick={onCreateUi} data-testid="apps-new">
            <Plus /> New app
          </Button>
        )}
      </div>
      {apps.length === 0 ? (
        <div className="px-4 py-5 text-[13px] text-muted-foreground" data-testid="apps-empty">
          No apps yet.{' '}
          <button type="button" className="font-medium text-primary hover:underline" onClick={onCreateUi} data-testid="apps-empty-create-ui">
            Create UI from backend
          </button>
        </div>
      ) : (
        <div className="divide-y divide-border">
          {apps.map((a) => (
            <AppRow key={a.id} app={a} routesTotal={routesTotal} />
          ))}
        </div>
      )}
    </section>
  )
}

function AppRow({ app, routesTotal }: { app: ApiAppListItem; routesTotal: number }) {
  const { slug } = useProjectCtx()
  const usage = useQuery({ queryKey: qk.apiUsage(app.id), queryFn: () => appApiApi.apiUsage(app.id) })
  const ops = [...new Set((usage.data ?? []).map((u) => u.operation))]
  const problems = (usage.data ?? []).filter((u) => u.status === 'missing' || u.status === 'changed')
  return (
    <div className="flex items-center gap-4 px-4 py-3.5" data-testid={`app-row-${app.id}`}>
      <div className="grid h-12 w-[84px] shrink-0 grid-cols-3 gap-1 rounded-md border border-border bg-muted/50 p-1.5" aria-hidden>
        <span className="rounded-sm bg-border/70" />
        <span className="rounded-sm bg-border/70" />
        <span className="rounded-sm bg-border/70" />
        <span className="col-span-2 rounded-sm bg-border/70" />
        <span className="rounded-sm bg-border/70" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{app.name}</span>
          {app.active ? <Badge variant="success">Published</Badge> : <Badge variant="neutral">Draft</Badge>}
          {app.current_version_number != null && <Badge variant="secondary">v{app.current_version_number}</Badge>}
        </div>
        {app.slug && <div className="mt-0.5 font-mono text-xs text-muted-foreground">/a/{app.slug}</div>}
        <div className="mt-0.5 text-xs text-muted-foreground">
          {ops.length > 0 ? `Uses ${ops.slice(0, 3).join(', ')}${ops.length > 3 ? '…' : ''} · ${ops.length} of ${routesTotal} operations` : 'Calls no operations yet'}
        </div>
        {problems.length > 0 && (
          <div className="mt-1 text-xs text-warning-text" data-testid="app-usage-warning">
            {problems.map((p) => `${p.operation} ${p.status}`).join(' · ')}
          </div>
        )}
      </div>
      <Button variant="outline" size="sm" render={<Link to="/p/$project/apps/$appId" params={{ project: slug, appId: app.id }} />}>
        Open builder
      </Button>
    </div>
  )
}

function RecentRunsCard() {
  const { pid, slug } = useProjectCtx()
  const stats = useRunStats(pid)
  const runs = stats.data?.recent ?? []
  return (
    <section className={card} data-testid="recent-runs-card">
      <div className={cardH}>
        <span className="text-[13px] font-semibold">Recent runs</span>
        <div className="flex-1" />
        <Link to="/p/$project/runs" params={{ project: slug }} className="text-xs text-muted-foreground hover:text-foreground">
          View all
        </Link>
      </div>
      {runs.length === 0 ? (
        <div className="px-4 py-5 text-xs text-muted-foreground">No runs yet. Test a route to see one here.</div>
      ) : (
        <div className="divide-y divide-border">
          {runs.map((r) => {
            const [method, ...rest] = (r.route ?? '').split(' ')
            return (
              <Link
                key={r.id}
                to="/p/$project/runs/$executionId"
                params={{ project: slug, executionId: r.id }}
                className="flex h-[38px] items-center gap-2.5 px-4 text-xs hover:bg-muted/50"
              >
                <StatusDot tone={runTone(r.status)} />
                {r.route ? (
                  <>
                    <span className="w-9 font-mono text-[10.5px] font-semibold" style={{ color: `var(--method-${method.toLowerCase()})` }}>
                      {method}
                    </span>
                    <span className="truncate font-mono">{rest.join(' ')}</span>
                  </>
                ) : (
                  <span className="truncate">{r.workflow_name}</span>
                )}
                <span className="ml-auto tabular-nums text-muted-foreground">{formatMs(r.duration_ms)}</span>
                <span className="w-9 text-right text-muted-foreground">{shortAge(r.start_time)}</span>
              </Link>
            )
          })}
        </div>
      )}
    </section>
  )
}

function SecretsCard() {
  const { project, pid, slug } = useProjectCtx()
  const secrets = useSecrets(pid)
  const missing = useMissingSecrets(pid)
  const keys = [...new Set([...(secrets.data ?? []).map((s) => s.key), ...(missing.data ?? []).map((m) => m.key)])].sort()
  const envs = project.environments.map((e) => e.name)
  return (
    <section className={card} data-testid="secrets-card">
      <div className={cardH}>
        <span className="text-[13px] font-semibold">Secrets</span>
        <div className="flex-1" />
        <Link to="/p/$project/secrets" params={{ project: slug }} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <Plus className="size-3.5" /> Add
        </Link>
      </div>
      {keys.length === 0 ? (
        <div className="px-4 py-5 text-xs text-muted-foreground">No secrets. Add API keys your backend calls with.</div>
      ) : (
        <div className="flex flex-col gap-2 px-4 py-3">
          {keys.map((k) => (
            <div key={k} className="flex items-center gap-2" data-testid={`secret-summary-${k}`}>
              <KeyRound className="size-3.5 text-muted-foreground" />
              <span className="truncate font-mono text-xs">{k}</span>
              <div className="ml-auto flex flex-wrap justify-end gap-1">
                {envs.map((env) => {
                  const set = (secrets.data ?? []).some((s) => s.key === k && s.environment === env)
                  const isMissing = (missing.data ?? []).some((m) => m.key === k && m.missing_in.includes(env))
                  if (set)
                    return (
                      <Badge key={env} variant="success" data-state="set">
                        {env}
                      </Badge>
                    )
                  if (isMissing)
                    return (
                      <Badge key={env} variant="warning" data-state="missing">
                        <StatusDot tone="warn" /> missing in {env}
                      </Badge>
                    )
                  return (
                    <Badge key={env} variant="neutral" data-state="not-set">
                      not set in {env}
                    </Badge>
                  )
                })}
              </div>
            </div>
          ))}
          <p className="mt-1 text-[11.5px] leading-4 text-muted-foreground">
            <span className="font-medium text-warning-text">Missing</span> = your code uses it and it isn't set.{' '}
            <span className="font-medium text-foreground">Not set</span> = unused there.
          </p>
        </div>
      )}
    </section>
  )
}

function ReleasesCard({ summary }: { summary: ProjectSummary }) {
  const { project } = useProjectCtx()
  const navigate = useNavigate()
  return (
    <section className={card} data-testid="releases-card">
      <div className={cardH}>
        <span className="text-[13px] font-semibold">Releases</span>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, sheet: 'release' }) } as never)}
        >
          Create release
        </Button>
      </div>
      <div className="flex flex-col gap-2.5 px-4 py-3 text-[13px]">
        {project.environments.map((e) => {
          const rel = summary.active_releases?.[e.name]
          return (
            <div key={e.name} className="flex items-center gap-2.5" data-testid={`release-env-${e.name}`}>
              <Badge variant="secondary">{e.name}</Badge>
              <span className="text-muted-foreground">
                {e.kind === 'draft'
                  ? 'Always your latest saved code'
                  : rel
                    ? `Release #${rel.number} · ${timeAgo(rel.created_at)}`
                    : 'No release yet · serves latest saved code'}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}


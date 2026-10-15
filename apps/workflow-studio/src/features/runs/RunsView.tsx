/**
 * Runs view (UI-DESIGN §3.6, `runs.png`): recent runs filtered by environment (follows the
 * top-bar switch, plus "All envs"), trigger type, status and route. Pages of 50 via
 * `X-Next-Cursor`; polls every 5 s while a visible run is running. The run sheet opens over the
 * list at `/p/$project/runs/$executionId`.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Activity, ListFilter, Workflow } from 'lucide-react'
import type { RunsSearch } from '@/app/routes/project'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectApps, useRoutes } from '@/features/project/hooks/useProjectData'
import { formatMs, timeAgo } from '@/features/project/lib/format'
import { runsApi, type RunsQuery } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Segmented } from '@/shared/components/ui/segmented'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { RunSheet } from './components/RunSheet'
import { RUN_STATUSES, TRIGGER_TYPES, isRunning, runSource, runTarget, triggerType } from './lib/runLabels'
import { cn } from '@/shared/lib/utils'

const ALL = '__all'

export function RunsView() {
  const { pid, slug, project, viewEnv, setViewEnv } = useProjectCtx()
  const search = useSearch({ strict: false }) as RunsSearch
  const params = useParams({ strict: false }) as { executionId?: string }
  const navigate = useNavigate()
  const routes = useRoutes(pid)
  const apps = useProjectApps(pid)

  const envFilter = search.env === 'all' ? null : search.env ?? viewEnv
  const filters: RunsQuery = {
    environment: envFilter ?? undefined,
    trigger: search.trigger,
    status: search.status,
    route_id: search.route,
  }

  const setSearch = (patch: Partial<RunsSearch>) =>
    navigate({
      to: '.',
      search: ((s: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...s, ...patch }
        for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k]
        return next
      }) as never,
      replace: true,
    })

  // The environment filter follows the top-bar switch (§2.4): a switch drops a local override.
  const prevEnv = useRef(viewEnv)
  useEffect(() => {
    if (prevEnv.current !== viewEnv) {
      prevEnv.current = viewEnv
      if (search.env) setSearch({ env: undefined })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewEnv])

  const runs = useInfiniteQuery({
    queryKey: qk.runs(pid, { ...filters, list: 'infinite' }),
    queryFn: ({ pageParam }) => runsApi.list(pid, { ...filters, limit: 50, before: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    refetchInterval: (q) => (q.state.data?.pages.some((p) => p.runs.some((r) => isRunning(r.status))) ? 5_000 : false),
  })

  const rows = useMemo(() => {
    const seen = new Set<string>()
    return (runs.data?.pages ?? []).flatMap((p) => p.runs).filter((r) => (seen.has(r.id) ? false : (seen.add(r.id), true)))
  }, [runs.data])
  const pages = runs.data?.pages ?? []
  const pruned = pages.length > 1 && pages[pages.length - 1].runs.length === 0
  const filtered = !!(search.trigger || search.status || search.route)

  const appName = (id: string) => apps.data?.find((a) => a.id === id)?.name
  const envs = project.environments.map((e) => e.name)
  const routeList = routes.data ?? []

  const open = (id: string) =>
    navigate({ to: '/p/$project/runs/$executionId', params: { project: slug, executionId: id }, search: search as never })
  const close = () => navigate({ to: '/p/$project/runs', params: { project: slug }, search: search as never })

  return (
    <div className="flex h-full flex-col rounded-xl border border-panel-border bg-card shadow-xs" data-testid="runs-view">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2 px-5 pt-5 pb-3.5">
        <h1 className="text-[17px] font-semibold tracking-[-0.01em]">Runs</h1>
        <span className="text-[13px] text-muted-foreground" data-testid="runs-subtitle">
          · recent runs (the engine keeps the newest 100)
        </span>
        <div className="grow" />
        <Segmented<string>
          size="xs"
          aria-label="Environment"
          data-testid="runs-env-filter"
          value={envFilter ?? ALL}
          onChange={(k) => {
            if (k === ALL) setSearch({ env: 'all' })
            else {
              setSearch({ env: undefined })
              if (k !== viewEnv) setViewEnv(k)
            }
          }}
          options={[
            { key: ALL, label: 'All envs', testId: 'runs-env-all' },
            ...envs.map((e) => ({ key: e, label: e, testId: `runs-env-${e}` })),
          ]}
        />
        <FilterSelect
          label="Trigger"
          testId="runs-trigger-filter"
          value={search.trigger}
          onChange={(v) => setSearch({ trigger: v })}
          options={TRIGGER_TYPES.map((t) => ({ value: t, label: t }))}
          mono
        />
        <FilterSelect
          label="Status"
          testId="runs-status-filter"
          value={search.status}
          onChange={(v) => setSearch({ status: v })}
          options={RUN_STATUSES.map((t) => ({ value: t, label: t }))}
        />
        <FilterSelect
          label="Route"
          testId="runs-route-filter"
          value={search.route}
          onChange={(v) => setSearch({ route: v })}
          options={routeList.map((r) => ({ value: r.id, label: `${r.method} ${r.path}` }))}
          mono
        />
      </div>

      <div className="min-h-0 flex-1 overflow-auto border-t border-border" data-testid="runs-panel">
        {runs.isPending ? (
          <RunsSkeleton />
        ) : runs.isError ? (
          <EmptyState title="Couldn't load runs" data-testid="runs-error" actions={<Button size="sm" variant="outline" onClick={() => runs.refetch()}>Retry</Button>}>
            {errorMessage(runs.error)}
          </EmptyState>
        ) : rows.length === 0 ? (
          filtered ? (
            <EmptyState
              icon={<ListFilter />}
              title="No runs match these filters."
              data-testid="runs-empty-filtered"
              actions={
                <Button size="sm" variant="outline" onClick={() => setSearch({ trigger: undefined, status: undefined, route: undefined })}>
                  Clear filters
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<Activity />}
              title="No runs yet. Test a route to see one here."
              data-testid="runs-empty"
              actions={
                envFilter ? (
                  <Button size="sm" variant="ghost" onClick={() => setSearch({ env: 'all' })} data-testid="runs-empty-all-envs">
                    Showing {envFilter} only · show all envs
                  </Button>
                ) : undefined
              }
            />
          )
        ) : (
          <>
            <Table>
              <TableHeader className="sticky top-0 z-[1] bg-card">
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[30px] pr-0" />
                  <TableHead className="w-[90px]">When</TableHead>
                  <TableHead>Route / workflow</TableHead>
                  <TableHead>Trigger type</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Env</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => {
                  const t = runTarget(r)
                  const sel = r.id === params.executionId
                  return (
                    <TableRow
                      key={r.id}
                      data-testid="run-row"
                      data-run-id={r.id}
                      data-status={r.status}
                      data-trigger={triggerType(r)}
                      data-env={r.environment ?? ''}
                      data-state={sel ? 'selected' : undefined}
                      tabIndex={0}
                      className={cn('cursor-pointer', sel && 'bg-primary/6 hover:bg-primary/8')}
                      onClick={() => open(r.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') open(r.id)
                      }}
                    >
                      <TableCell className="pr-0">
                        <StatusDot tone={runTone(r.status)} className="shadow-none" />
                      </TableCell>
                      <TableCell className="text-muted-foreground">{timeAgo(r.start_time)}</TableCell>
                      <TableCell className="max-w-0 w-[34%]">
                        <span className="flex min-w-0 items-center gap-2">
                          {t.method ? (
                            <>
                              <MethodBadge method={t.method} size="sm" />
                              <span className="truncate font-mono">{t.path}</span>
                            </>
                          ) : (
                            <>
                              <Workflow className="size-3.5 shrink-0 text-muted-foreground" />
                              <span className="truncate font-mono">{t.workflow}</span>
                            </>
                          )}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant="neutral" className="font-mono text-[10.5px]">
                          {triggerType(r)}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[220px] truncate text-muted-foreground">{runSource(r, appName)}</TableCell>
                      <TableCell>{r.environment ? <Badge variant="secondary">{r.environment}</Badge> : '—'}</TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {isRunning(r.status) ? <Spinner className="ml-auto size-3.5" /> : formatMs(r.duration_ms)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            <div className="flex items-center justify-center gap-3 px-5 py-4 text-xs text-muted-foreground">
              {pruned ? (
                <span data-testid="runs-pruned">Older runs were pruned (the engine keeps 100)</span>
              ) : runs.hasNextPage ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => runs.fetchNextPage()}
                  disabled={runs.isFetchingNextPage}
                  data-testid="runs-load-more"
                >
                  {runs.isFetchingNextPage && <Spinner className="size-3.5" />}
                  Load more
                </Button>
              ) : (
                <span data-testid="runs-count">{rows.length === 1 ? '1 run' : `${rows.length} runs`}</span>
              )}
            </div>
          </>
        )}
      </div>

      <RunSheet executionId={params.executionId} onClose={close} />
    </div>
  )
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  testId,
  mono,
}: {
  label: string
  value: string | undefined
  onChange: (v: string | undefined) => void
  options: { value: string; label: string }[]
  testId: string
  mono?: boolean
}) {
  const items = [{ value: ALL, label: `${label}: all` }, ...options.map((o) => ({ value: o.value, label: `${label}: ${o.label}` }))]
  return (
    <Select
      value={value ?? ALL}
      onValueChange={(v) => onChange(!v || v === ALL ? undefined : String(v))}
      items={items}
    >
      <SelectTrigger size="sm" className={cn('h-6 gap-1 rounded-md px-2 text-xs', value && 'border-primary/40 text-foreground')} data-testid={testId}>
        <ListFilter className="size-3 text-muted-foreground" />
        <SelectValue />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false} align="end" className="min-w-44">
        {items.map((o) => (
          <SelectItem
            key={o.value}
            value={o.value}
            className={cn('text-xs', mono && o.value !== ALL && 'font-mono')}
            data-testid={`${testId}-${o.value === ALL ? 'all' : o.value}`}
          >
            {o.value === ALL ? 'All' : o.label.slice(label.length + 2)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function RunsSkeleton() {
  return (
    <div className="flex flex-col" data-testid="runs-loading">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex h-[42px] items-center gap-4 border-b border-border/65 px-4">
          <Skeleton className="size-1.5 rounded-full" />
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-3 w-48" />
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-3 w-28" />
          <div className="grow" />
          <Skeleton className="h-3 w-12" />
        </div>
      ))}
    </div>
  )
}

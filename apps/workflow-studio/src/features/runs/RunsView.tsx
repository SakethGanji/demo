/**
 * Runs view (DESIGN §8, `runs.html`): the 24h at a glance (hero count, failed, p50, an hourly timeline), a filter
 * bar (status, path, environment, trigger, route, time range) and the runs in a glass table. A click expands a
 * row in place (meta, mini trace, stack trace); double-click, ⇧↵ or "Open details" opens the run sheet at
 * `/p/$project/runs/$executionId`. j/k move, ↵ expands. Pages of 50 via `X-Next-Cursor`; polls every 5 s while a
 * visible run is running. Everything counted comes from the runs loaded (the engine keeps the newest 100).
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Activity, Calendar, ListFilter, RefreshCw, Search, Workflow } from 'lucide-react'
import type { RunsSearch } from '@/app/routes/project'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectApps, useRoutes } from '@/features/project/hooks/useProjectData'
import { formatMs, timeAgo } from '@/features/project/lib/format'
import { runsApi, type RunsQuery } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Input } from '@/shared/components/ui/input'
import { Kbd } from '@/shared/components/ui/kbd'
import { Roll } from '@/shared/components/ui/roll'
import { Segmented } from '@/shared/components/ui/segmented'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { PageHeader, Tile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { RunSheet } from './components/RunSheet'
import { RunInline } from './components/RunInline'
import { RunTimeline } from './components/RunTimeline'
import { TRIGGER_TYPES, isRunning, runSource, runTarget, triggerType } from './lib/runLabels'
import { RANGES, dayStats, exactTime, isFailed, startMs, type RangeKey } from './lib/runStats'
import { cn } from '@/shared/lib/utils'

const ALL = '__all'
const COLS = 6

type StatusSeg = 'all' | 'ok' | 'err'

/** true while the user is typing or an overlay owns the keyboard */
function keyboardBusy(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null
  if (t?.closest?.('input, textarea, select, [contenteditable="true"], [role="combobox"], [role="listbox"], .cm-editor')) return true
  return !!document.querySelector('[role="dialog"], [role="menu"], [role="listbox"]')
}

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

  const queryKey = qk.runs(pid, { ...filters, list: 'infinite' })
  const runs = useInfiniteQuery({
    queryKey,
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

  // ── client-side view state: status segment, path filter, time range ──
  const [seg, setSeg] = useState<StatusSeg>('all')
  const [q, setQ] = useState('')
  const [range, setRange] = useState<RangeKey>('24h')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => clearInterval(t)
  }, [])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => Date.now(), [runs.dataUpdatedAt, tick])

  const stats = useMemo(() => dayStats(rows, now, !!runs.hasNextPage), [rows, now, runs.hasNextPage])
  const rangeMs = RANGES.find((r) => r.key === range)!.ms
  const inRange = useMemo(() => (rangeMs === Infinity ? rows : rows.filter((r) => now - startMs(r) < rangeMs)), [rows, rangeMs, now])
  const failedCount = inRange.filter((r) => isFailed(r.status)).length
  const appName = (id: string) => apps.data?.find((a) => a.id === id)?.name
  const needle = q.trim().toLowerCase()
  const visible = useMemo(
    () =>
      inRange.filter((r) => {
        if (seg === 'err' && !isFailed(r.status)) return false
        if (seg === 'ok' && isFailed(r.status)) return false
        if (!needle) return true
        const t = runTarget(r)
        return (t.path ?? t.workflow ?? '').toLowerCase().includes(needle)
      }),
    [inRange, seg, needle],
  )
  const maxMs = Math.max(1, ...rows.map((r) => r.duration_ms ?? 0))
  const envs = project.environments.map((e) => e.name)
  const routeList = routes.data ?? []

  // ── smooth arrival: rows that show up on a refetch animate in once (the first load and other filters don't) ──
  const baseline = useRef<{ key: string; ids: Set<string> } | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(() => new Set())
  const keyStr = JSON.stringify(queryKey)
  useEffect(() => {
    if (!runs.isSuccess) return
    const first = (runs.data?.pages[0]?.runs ?? []).map((r) => r.id)
    const all = rows.map((r) => r.id)
    if (!baseline.current || baseline.current.key !== keyStr) {
      baseline.current = { key: keyStr, ids: new Set(all) }
      setFresh(new Set())
      return
    }
    const known = baseline.current.ids
    const added = first.filter((id) => !known.has(id))
    for (const id of all) known.add(id)
    if (added.length) setFresh((f) => new Set([...f, ...added]))
  }, [runs.isSuccess, runs.data, rows, keyStr])

  // ── selection + inline expansion ──
  const [sel, setSel] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const auto = useRef(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  const open = (id: string) =>
    navigate({ to: '/p/$project/runs/$executionId', params: { project: slug, executionId: id }, search: search as never })
  const close = () => navigate({ to: '/p/$project/runs', params: { project: slug }, search: search as never })
  const openFrame = (module: string, line: number) =>
    navigate({ to: '/p/$project/backend/$module', params: { project: slug, module }, search: { line } })

  const toggle = (id: string) => {
    setSel(id)
    setExpanded((cur) => (cur === id ? null : id))
  }

  // the page leads with what needs attention: the newest failed run opens once
  useEffect(() => {
    if (auto.current || !runs.isSuccess || params.executionId) return
    auto.current = true
    const f = visible.find((r) => isFailed(r.status))
    if (f) {
      setSel(f.id)
      setExpanded(f.id)
    }
  }, [runs.isSuccess, visible, params.executionId])

  // a filtered-out run can't stay selected
  useEffect(() => {
    if (sel && !visible.some((r) => r.id === sel)) setSel(null)
    if (expanded && !visible.some((r) => r.id === expanded)) setExpanded(null)
  }, [visible, sel, expanded])

  const [turns, setTurns] = useState(0)
  const refresh = () => {
    setTurns((n) => n + 1)
    void runs.refetch()
  }

  const keys = useRef({ visible, sel, expanded, toggle, open, refresh, sheet: !!params.executionId })
  keys.current = { visible, sel, expanded, toggle, open, refresh, sheet: !!params.executionId }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = keys.current
      if (e.metaKey || e.ctrlKey || e.altKey || k.sheet || keyboardBusy(e)) return
      const i = k.visible.findIndex((r) => r.id === k.sel)
      if (e.key === 'j' || e.key === 'k') {
        if (!k.visible.length) return
        e.preventDefault()
        const n = e.key === 'j' ? Math.min(k.visible.length - 1, i + 1) : Math.max(0, i < 0 ? 0 : i - 1)
        const id = k.visible[n].id
        setSel(id)
        bodyRef.current?.querySelector(`[data-run-id="${id}"][data-testid="run-row"]`)?.scrollIntoView({ block: 'nearest' })
      } else if (e.key === 'Enter' && k.sel && !(e.target as HTMLElement | null)?.closest?.('button, a')) {
        e.preventDefault()
        if (e.shiftKey) k.open(k.sel)
        else k.toggle(k.sel)
      } else if (e.key === 'Escape' && k.expanded) {
        setExpanded(null)
      } else if (e.key === 'r' || e.key === 'R') {
        k.refresh()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const failedStat = stats.failed
  const hasRows = visible.length > 0

  return (
    <div className="flex h-full min-h-0 flex-col pb-2" data-testid="runs-view">
      <PageHeader
        className="px-5 pt-3 pb-2.5"
        title={
          <span className="flex h-[30px] items-center gap-3">
            Runs
            <span className="rounded-chip bg-recess px-2 py-0.5 text-[12px] leading-4 font-medium tracking-normal text-muted-foreground" data-testid="runs-subtitle">
              {stats.count} · 24h
            </span>
          </span>
        }
        actions={
          <Tip label="Fetch the latest runs" kbd="R">
            <Button variant="outline" size="sm" onClick={refresh} data-testid="runs-refresh">
              <RefreshCw
                className="transition-transform duration-(--d-slow) ease-(--ease-io)"
                style={{ transform: `rotate(${turns * 360}deg)` }}
              />
              Refresh
            </Button>
          </Tip>
        }
      />

      <Tile className="mx-2 grid shrink-0 grid-cols-[232px_minmax(0,1fr)] rounded-well" data-testid="runs-glance">
        <div className="flex flex-col justify-center gap-1 p-4 shadow-[inset_-1px_0_0_var(--hairline)]">
          <span className="text-[12px] font-medium text-muted-foreground">
            {stats.capped ? 'Runs · latest 100 in 24h' : 'Runs · last 24h'}
          </span>
          <span className="text-[28px] leading-[34px] font-semibold tracking-[-0.03em]" data-testid="runs-hero">
            <Roll value={stats.count} />
          </span>
          <div className="flex items-center gap-3 text-[12px] text-muted-foreground">
            <span className={cn('inline-flex items-center gap-1.5', failedStat > 0 && 'text-destructive')} data-testid="runs-failed-count">
              <StatusDot tone={failedStat > 0 ? 'err' : 'ok'} flat />
              {failedStat} failed
            </span>
            <span data-testid="runs-p50">p50 {formatMs(stats.p50)}</span>
          </div>
        </div>
        <RunTimeline stats={stats} now={now} />
      </Tile>

      <div className="flex items-center gap-2 px-5 pt-4 pb-3">
        <Segmented<StatusSeg>
          aria-label="Status"
          data-testid="runs-seg"
          value={seg}
          onChange={setSeg}
          options={[
            { key: 'all', label: 'All', testId: 'runs-seg-all' },
            { key: 'ok', label: 'Succeeded', testId: 'runs-seg-ok' },
            { key: 'err', label: 'Failed', count: failedCount, testId: 'runs-seg-err' },
          ]}
        />
        <div className="relative w-60">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-7 pl-8 text-[12.5px]"
            placeholder="Filter by path…"
            aria-label="Filter by path"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="runs-path-filter"
          />
        </div>
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
          label="Route"
          testId="runs-route-filter"
          value={search.route}
          onChange={(v) => setSearch({ route: v })}
          options={routeList.map((r) => ({ value: r.id, label: `${r.method} ${r.path}` }))}
          mono
        />
        <Select
          value={range}
          onValueChange={(v) => v && setRange(v as RangeKey)}
          items={RANGES.map((r) => ({ value: r.key, label: r.label }))}
        >
          <SelectTrigger size="sm" aria-label="Time range" className="h-7 gap-1.5 text-[12.5px]" data-testid="runs-range">
            <Calendar className="size-3.5 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false} align="end" className="min-w-40">
            {RANGES.map((r) => (
              <SelectItem key={r.key} value={r.key} className="text-xs" data-testid={`runs-range-${r.key}`}>
                {r.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Tile className="mx-2 flex min-h-0 flex-col rounded-well" data-testid="runs-table">
        <div ref={bodyRef} className="min-h-0 flex-1 overflow-auto" data-testid="runs-panel">
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
                dust
                actions={
                  envFilter ? (
                    <Button size="sm" variant="ghost" onClick={() => setSearch({ env: 'all' })} data-testid="runs-empty-all-envs">
                      Showing {envFilter} only · show all envs
                    </Button>
                  ) : undefined
                }
              />
            )
          ) : !hasRows ? (
            <EmptyState
              icon={<ListFilter />}
              title="No runs match these filters."
              data-testid="runs-empty-view"
              actions={
                <Button
                  size="sm"
                  variant="outline"
                  data-testid="runs-clear-view"
                  onClick={() => {
                    setSeg('all')
                    setQ('')
                    setRange('all')
                  }}
                >
                  Show all runs
                </Button>
              }
            />
          ) : (
            <Table className="table-fixed">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[19%] pr-3 pl-4">Status</TableHead>
                  <TableHead className="w-[30%] px-3">Route</TableHead>
                  <TableHead className="w-[8%] px-3">Env</TableHead>
                  <TableHead className="w-[20%] px-3">Trigger</TableHead>
                  <TableHead className="w-[14%] px-3 text-right">Duration</TableHead>
                  <TableHead className="w-[9%] pr-4 pl-3 text-right">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((r) => {
                  const t = runTarget(r)
                  const failedRun = isFailed(r.status)
                  const running = isRunning(r.status)
                  const isSel = sel === r.id
                  const isOpen = expanded === r.id
                  const sheetOpen = r.id === params.executionId
                  const source = runSource(r, appName)
                  return (
                    <Fragment key={r.id}>
                      <TableRow
                        data-testid="run-row"
                        data-run-id={r.id}
                        data-status={r.status}
                        data-trigger={triggerType(r)}
                        data-env={r.environment ?? ''}
                        data-state={sheetOpen ? 'selected' : undefined}
                        data-expanded={isOpen ? 'true' : undefined}
                        aria-expanded={running ? undefined : isOpen}
                        tabIndex={0}
                        className={cn(
                          'h-[38px] cursor-pointer outline-none',
                          fresh.has(r.id) && 'animate-row-in',
                          failedRun && 'bg-destructive/4 hover:bg-destructive/6',
                          isSel && !failedRun && 'bg-primary/7 hover:bg-primary/8 [&>td:first-child]:shadow-[inset_2px_0_0_var(--primary)]',
                          isSel && failedRun && 'bg-destructive/6 [&>td:first-child]:shadow-[inset_2px_0_0_var(--destructive)]',
                          sheetOpen && 'data-[state=selected]:bg-primary/7',
                        )}
                        onClick={() => (running ? setSel(r.id) : toggle(r.id))}
                        onDoubleClick={() => open(r.id)}
                        onFocus={() => setSel(r.id)}
                      >
                        <TableCell className="h-[38px] pr-3 pl-4">
                          <span
                            className={cn(
                              'inline-flex items-center gap-2 text-[12.5px] text-muted-foreground',
                              failedRun && 'font-[550] text-destructive',
                              running && 'text-foreground',
                            )}
                          >
                            <StatusDot
                              tone={runTone(r.status)}
                              flat={!failedRun}
                              className={failedRun ? 'shadow-[0_0_0_3px_color-mix(in_srgb,var(--destructive)_20%,transparent)]' : undefined}
                            />
                            {failedRun ? 'Failed' : running ? 'Running' : r.status === 'success' ? 'Success' : r.status}
                          </span>
                        </TableCell>
                        <TableCell className="h-[38px] px-3">
                          <span className="flex min-w-0 items-center gap-2">
                            {t.method ? (
                              <>
                                <MethodBadge method={t.method} size="sm" className="h-[18px] w-9 text-[9.5px]" />
                                <Tip label={t.path} disabled={(t.path ?? '').length < 36}>
                                  <span className="truncate font-mono text-[12.5px]">{t.path}</span>
                                </Tip>
                              </>
                            ) : (
                              <>
                                <Workflow className="size-3.5 shrink-0 text-muted-foreground" />
                                <span className="truncate font-mono text-[12.5px]">{t.workflow}</span>
                              </>
                            )}
                          </span>
                        </TableCell>
                        <TableCell className="h-[38px] px-3">
                          {r.environment ? (
                            <span className={cn('font-mono text-[12px] text-muted-foreground', r.environment === 'prod' && 'text-foreground')}>
                              {r.environment}
                            </span>
                          ) : (
                            '—'
                          )}
                        </TableCell>
                        <TableCell className="h-[38px] truncate px-3 text-muted-foreground" title={source}>
                          {source}
                        </TableCell>
                        <TableCell className="h-[38px] px-3 text-right">
                          {running ? (
                            <Spinner className="ml-auto size-3.5" />
                          ) : (
                            <span className="inline-flex items-center justify-end gap-2">
                              <span className="inline-block h-1 w-12 overflow-hidden rounded-xs bg-foreground/6" aria-hidden>
                                <i
                                  className={cn(
                                    'block h-full rounded-xs transition-[width] duration-(--d-slow) ease-(--ease-out)',
                                    failedRun ? 'bg-destructive' : isSel ? 'bg-primary' : 'bg-foreground/24',
                                  )}
                                  style={{ width: `${Math.max(4, ((r.duration_ms ?? 0) / maxMs) * 100)}%` }}
                                />
                              </span>
                              <span className="min-w-11 text-right tabular-nums">{formatMs(r.duration_ms)}</span>
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="h-[38px] pr-4 pl-3 text-right text-muted-foreground">
                          <Tip label={exactTime(r.start_time)}>
                            <span data-ts={r.start_time}>{timeAgo(r.start_time, now)}</span>
                          </Tip>
                        </TableCell>
                      </TableRow>
                      {isOpen && (
                        <RunInline
                          run={r}
                          source={source}
                          colSpan={COLS}
                          onOpenDetails={() => open(r.id)}
                          onOpenFrame={openFrame}
                        />
                      )}
                    </Fragment>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </div>
        {rows.length > 0 && (
          <div className="flex h-10 shrink-0 items-center gap-4 pr-2 pl-4 text-[12px] text-muted-foreground shadow-[inset_0_1px_0_var(--hairline)]">
            {pruned ? (
              <span data-testid="runs-pruned">Older runs were pruned (the engine keeps 100)</span>
            ) : (
              <span data-testid="runs-count">
                {visible.length === inRange.length
                  ? `${visible.length} ${visible.length === 1 ? 'run' : 'runs'}`
                  : `${visible.length} of ${inRange.length} runs`}
                {range !== 'all' && inRange.length < rows.length ? ` · ${rows.length} loaded` : ''}
              </span>
            )}
            <div className="grow" />
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-1">
                <Kbd>j</Kbd>
                <Kbd>k</Kbd> move
              </span>
              <span className="inline-flex items-center gap-1">
                <Kbd>↵</Kbd> expand
              </span>
            </div>
            {!pruned && runs.hasNextPage && (
              <Button variant="ghost" size="sm" onClick={() => runs.fetchNextPage()} disabled={runs.isFetchingNextPage} data-testid="runs-load-more">
                {runs.isFetchingNextPage && <Spinner className="size-3.5" />}
                Load older
              </Button>
            )}
          </div>
        )}
      </Tile>

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
      <SelectTrigger size="sm" className={cn("h-7 gap-1.5 text-[12.5px]", value && "text-foreground")} data-testid={testId}>
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
        <div key={i} className="flex h-[38px] items-center gap-4 border-b border-hairline px-4">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-48" />
          <Skeleton className="h-3 w-10" />
          <Skeleton className="h-3 w-28" />
          <div className="grow" />
          <Skeleton className="h-3 w-16" />
        </div>
      ))}
    </div>
  )
}

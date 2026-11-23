/**
 * The home screen's numbers, in two layers:
 *
 *   KpiStrip   one glass strip of six read-at-a-glance numbers, each with its label above and its context below:
 *              runs (24 h), success, typical run time (p50, with p95), approvals waiting, what is live, the backend
 *   Activity   the run chart, picking its own scale (per hour when every run is recent, per day otherwise), with this
 *              week against the last
 *   Attention  the things to act on, or "all clear"
 *   Up next    what is running, what the schedules do next, and what ran recently, merged into one list
 *
 * Every panel wears the same header: a neutral icon tile and a title. Real data only: the summary's 24 h counts, the
 * engine's per-day aggregate, the newest 100 runs and the schedules. "Recent runs" are those 100 (the engine keeps
 * no more), and every window says which it is.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { Activity, AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, CalendarClock, CheckCircle2, ChevronRight, Database, Gauge, Play, Rocket, Route, Timer, UserCheck } from 'lucide-react'
import type { ModuleSchedule, ProjectSummary } from '@/shared/api/types'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import type { ProjectPulse } from '@/features/project/hooks/useProjectPulse'
import { entityKey, toneOf } from '@/features/project/lib/entities'
import { useProjectCtx } from '../../ProjectContext'
import { useRunDaily } from '../../hooks/useRunStats'
import { formatMs, parseTs, pluralize } from '../../lib/format'
import type { AttentionItem } from '../lib/pulse'
import { bucketRuns, cadence, chartScale, heatRows, percentile, periodTrend, type HeatRow, type RunWindow } from '../lib/stats'

const DAYS = 14
const SLICES = 24

const fmtNum = (n: number) => n.toLocaleString('en-US')
const pct = (x: number) => `${x >= 0.995 && x < 1 ? '99.5' : Math.round(x * 100)}%`
const utcDay = (iso: string, opts: Intl.DateTimeFormatOptions) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', ...opts })
const clock = (t: number) => new Date(t).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
const span = (ms: number) => (ms >= 3_600_000 ? `${+(ms / 3_600_000).toFixed(1)} h` : ms >= 60_000 ? `${Math.round(ms / 60_000)} min` : `${Math.max(1, Math.round(ms / 1000))} s`)
const shortMsLabel = (ms: number) => (ms >= 1000 ? `${+(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`)
const atOf = (iso: string | null | undefined) => parseTs(iso)?.getTime() ?? 0

/** The neutral glass gem every panel and number wears beside its label. */
function Gem({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn('icon-tile size-6 shrink-0 text-muted-foreground [&>svg]:size-[13px] [&>svg]:stroke-[2]', className)} aria-hidden>
      {children}
    </span>
  )
}

/* ── the panel frame ───────────────────────────────────────────────────────────────────────────────────────────── */

function Panel({ title, icon, to, aside, children, testId, className }: { title: string; icon: ReactNode; to?: string; aside?: ReactNode; children: ReactNode; testId: string; className?: string }) {
  const { slug } = useProjectCtx()
  const head = (
    <>
      <Gem>{icon}</Gem>
      <span className="truncate">{title}</span>
      {to && <ChevronRight className="size-3.5 shrink-0 text-faint transition-transform duration-(--d-fast) group-hover/w:translate-x-0.5" aria-hidden />}
    </>
  )
  return (
    <Tile as="section" className={cn('@container/wd flex min-w-0 flex-col gap-3 overflow-hidden rounded-panel p-4', className)} data-testid={testId}>
      <header className="flex min-h-6 items-center gap-2 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
        {to ? (
          <Link to={to as '/p/$project'} params={{ project: slug }} className="group/w flex min-w-0 items-center gap-2 rounded-chip outline-none hover:text-foreground focus-visible:shadow-focus">
            {head}
          </Link>
        ) : (
          <span className="flex min-w-0 items-center gap-2">{head}</span>
        )}
        {aside && <span className="ml-auto hidden min-w-0 shrink truncate text-[11.5px] font-normal text-muted-foreground @[260px]/wd:block">{aside}</span>}
      </header>
      {children}
    </Tile>
  )
}

/* ── KPI strip ─────────────────────────────────────────────────────────────────────────────────────────────────── */

function Kpi({ label, icon, to, search, value, unit, tone, sub, testId, valueTestId }: {
  label: string
  icon: ReactNode
  to?: string
  search?: Record<string, string>
  value: ReactNode
  unit?: string
  tone?: 'err' | 'warn' | 'muted'
  sub: ReactNode
  testId?: string
  valueTestId?: string
}) {
  const { slug } = useProjectCtx()
  const body = (
    <>
      <span className="flex items-center gap-2 text-[12px] font-medium text-muted-foreground">
        <Gem>{icon}</Gem>
        <span className="truncate">{label}</span>
      </span>
      <span className="flex items-baseline gap-1.5">
        <span
          className={cn(
            'text-[26px] leading-none font-[650] tracking-[-0.04em] whitespace-nowrap tabular-nums',
            tone === 'err' ? 'text-destructive' : tone === 'warn' ? 'text-warning-text' : tone === 'muted' ? 'text-foreground/30' : 'text-foreground',
          )}
          data-testid={valueTestId}
        >
          {value}
        </span>
        {unit && <span className="text-[12px] text-muted-foreground">{unit}</span>}
      </span>
      <span className="flex min-h-4 min-w-0 items-center gap-1.5 truncate text-[11.5px] text-muted-foreground">{sub}</span>
    </>
  )
  const cls = 'flex min-w-0 flex-col gap-2 px-2.5 py-2.5'
  return to ? (
    <Link to={to as '/p/$project'} params={{ project: slug }} search={search as never} className={cn(cls, 'rounded-well outline-none transition-colors duration-(--d-fast) hover:bg-(--hover-fill) focus-visible:shadow-focus')} data-testid={testId}>
      {body}
    </Link>
  ) : (
    <div className={cls} data-testid={testId}>
      {body}
    </div>
  )
}

/** A thin success ring for the strip. */
function Ring({ share, size = 18 }: { share: number | null; size?: number }) {
  const sw = 3
  const r = (size - sw) / 2
  const c = 2 * Math.PI * r
  return (
    <svg width={size} height={size} className="block shrink-0 -rotate-90" aria-hidden>
      <circle cx={size / 2} cy={size / 2} r={r} className={cn('fill-none', share != null && share < 1 ? 'stroke-destructive/70' : 'stroke-foreground/12')} strokeWidth={sw} />
      {share != null && share > 0 && <circle cx={size / 2} cy={size / 2} r={r} className="fill-none stroke-success" strokeWidth={sw} strokeLinecap={share >= 1 ? 'butt' : 'round'} strokeDasharray={`${c * share} ${c}`} />}
    </svg>
  )
}

export function KpiStrip({ summary, pulse, window: w }: { summary: ProjectSummary; pulse: ProjectPulse; window: RunWindow }) {
  const { slug } = useProjectCtx()
  const runs = summary.health.runs_24h
  const failed = summary.health.failed_24h
  // a run still going has neither succeeded nor failed: leave it out of the rate
  const running = pulse.runs.filter((r) => toneOf(r.status) === 'running' && atOf(r.start_time) >= pulse.now - 86_400_000).length
  const done = Math.max(0, runs - running)
  const share = done ? Math.max(0, done - failed) / done : null

  const ds = pulse.runs.filter((r) => atOf(r.start_time) >= w.from && r.duration_ms != null && toneOf(r.status) !== 'running').map((r) => r.duration_ms as number)
  const p50 = percentile(ds, 0.5)
  const p95 = percentile(ds, 0.95)

  const liveEnv = summary.project.live_environment
  const release = summary.active_releases?.[liveEnv] ?? null
  const appsLive = pulse.apps.filter((a) => a.published_at).length
  const c = summary.counts

  return (
    <Tile className="@container/k overflow-hidden rounded-panel p-1.5" data-testid="kpi-strip">
      <div className="grid grid-cols-2 gap-1 @[620px]/k:grid-cols-3 @[1000px]/k:grid-cols-6">
        <Kpi
          label="Runs · 24 h"
          icon={<Play />}
          to="/p/$project/runs"
          value={fmtNum(runs)}
          valueTestId="metric-runs-value"
          testId="metric-runs"
          sub={
            running > 0 ? (
              <span className="flex items-center gap-1.5 text-primary">
                <StatusDot tone="running" /> {running} running now
              </span>
            ) : w.truncated ? (
              `newest ${pulse.runs.length} shown`
            ) : runs ? (
              'apps, workflows & routes'
            ) : (
              'nothing ran today'
            )
          }
        />
        <Kpi
          label="Success"
          icon={<Gauge />}
          value={share == null ? '—' : pct(share)}
          tone={share == null ? 'muted' : share < 0.95 ? 'err' : undefined}
          valueTestId="metric-success-value"
          testId="metric-success"
          sub={
            <Link
              to="/p/$project/runs"
              params={{ project: slug }}
              search={{ status: 'failed' } as never}
              className={cn('flex items-center gap-1.5 rounded-chip outline-none hover:underline focus-visible:shadow-focus', failed ? 'font-medium text-destructive' : 'text-muted-foreground')}
              data-testid="metric-failed"
            >
              <Ring share={share} size={13} />
              <span className="tabular-nums" data-testid="metric-failed-value">
                {fmtNum(failed)}
              </span>
              failed
            </Link>
          }
        />
        <Kpi
          label="Typical run"
          icon={<Timer />}
          to="/p/$project/runs"
          value={p50 == null ? '—' : formatMs(p50)}
          tone={p50 == null ? 'muted' : undefined}
          valueTestId="metric-p50-value"
          testId="metric-p50"
          sub={
            p95 == null ? (
              'after the first finished run'
            ) : (
              <>
                slowest 5% over
                <span className="font-semibold text-foreground/75 tabular-nums" data-testid="metric-p95-value">
                  {formatMs(p95)}
                </span>
              </>
            )
          }
        />
        <Kpi
          label="Approvals"
          icon={<UserCheck />}
          to="/p/$project/approvals"
          value={
            <span className="tabular-nums" data-testid="metric-approvals-value">
              {pulse.openTasks}
            </span>
          }
          tone={pulse.openTasks ? 'warn' : undefined}
          testId="metric-approvals"
          sub={pulse.openTasks ? `${pulse.openTasks === 1 ? 'waits' : 'wait'} on a person` : 'nothing waiting'}
        />
        <Kpi
          label={`Live · ${liveEnv}`}
          icon={<Rocket />}
          to="/p/$project/releases"
          value={appsLive}
          unit={`of ${pulse.apps.length} ${pulse.apps.length === 1 ? 'app' : 'apps'}`}
          testId="metric-live"
          sub={release ? `release #${release.number}` : 'no release yet'}
        />
        <Kpi
          label="Backend"
          icon={<Database />}
          to="/p/$project/backend"
          value={fmtNum(c.routes)}
          unit={c.routes === 1 ? 'route' : 'routes'}
          testId="metric-backend"
          sub={`${pluralize(c.tables, 'table')} · ${pluralize(summary.api_keys ?? 0, 'API key')}`}
        />
      </div>
    </Tile>
  )
}

/* ── Activity: the run chart ───────────────────────────────────────────────────────────────────────────────────── */

interface Bar {
  total: number
  failed: number
  label: string
}

/** One bar per bucket, failures stacked on top in red, empty buckets as a faint stub so the time axis still reads.
 * The caption line says the scale; hovering a bar reads it out in the same line. */
function Bars({ bars, ticks, caption, testId }: { bars: Bar[]; ticks: string[]; caption: string; testId: string }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...bars.map((b) => b.total))
  const peak = bars.reduce((p, b, i) => (b.total > bars[p].total ? i : p), 0)
  const h = hover != null ? bars[hover] : null
  const total = bars.reduce((s, b) => s + b.total, 0)
  const failed = bars.reduce((s, b) => s + b.failed, 0)
  const readout = h
    ? `${h.label} · ${h.total ? pluralize(h.total, 'run') : 'no runs'}${h.failed ? `, ${h.failed} failed` : ''}`
    : `Busiest ${bars[peak].label} · ${pluralize(bars[peak].total, 'run')}`
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid={testId} data-total={total} data-failed={failed}>
      <div className="flex items-baseline justify-between gap-3 text-[11px]">
        <span className="shrink-0 font-medium text-foreground/70">{caption}</span>
        <span className={cn('min-w-0 truncate tabular-nums', h ? 'text-foreground' : 'text-muted-foreground')} data-testid={`${testId}-readout`}>
          {readout}
        </span>
      </div>
      <div className="flex min-h-[120px] flex-1 gap-[3px]" role="img" aria-label={`${caption}: ${pluralize(total, 'run')}, ${failed} failed`} onPointerLeave={() => setHover(null)}>
        {bars.map((b, i) => (
          <span key={i} className="flex min-w-0 flex-1 flex-col justify-end" onPointerEnter={() => setHover(i)} data-count={b.total}>
            <span
              className={cn('flex flex-col overflow-hidden rounded-kbd transition-opacity duration-(--d-fast)', hover != null && hover !== i && 'opacity-40')}
              style={{ height: b.total ? `${Math.max(6, (b.total / max) * 100)}%` : 3 }}
            >
              {b.total === 0 ? (
                <span className="flex-1 bg-foreground/10" />
              ) : (
                <>
                  {b.failed > 0 && <span className="min-h-[3px] bg-destructive" style={{ flexGrow: b.failed, flexBasis: 0 }} />}
                  {b.total > b.failed && <span className="bg-primary/80" style={{ flexGrow: b.total - b.failed, flexBasis: 0 }} />}
                </>
              )}
            </span>
          </span>
        ))}
      </div>
      <div className="flex justify-between text-[10.5px] text-muted-foreground tabular-nums">
        {ticks.map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>
    </div>
  )
}

function Trend({ days }: { days: { count: number; failed: number }[] }) {
  const t = periodTrend(days)
  if (!t.hasPrev || (!t.runs && !t.prevRuns)) return null
  const rate = (f: number, n: number) => (n ? `${Math.round((f / n) * 100)}%` : '0%')
  let runs: ReactNode
  if (!t.prevRuns) runs = <span>{pluralize(t.runs, 'run')} this week, none the week before</span>
  else if (!t.runs) runs = <span>No runs this week, {fmtNum(t.prevRuns)} the week before</span>
  else {
    const ch = (t.runs - t.prevRuns) / t.prevRuns
    const Icon = ch >= 0 ? ArrowUpRight : ArrowDownRight
    runs = (
      <span className="flex items-center gap-1">
        <Icon className="size-3.5 text-foreground/70" aria-hidden />
        <span className="font-medium text-foreground/85">{Math.abs(Math.round(ch * 100))}%</span> runs vs last week
      </span>
    )
  }
  return (
    <span className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-[11.5px] text-muted-foreground" data-testid="health-trend">
      {runs}
      {(t.failed > 0 || t.prevFailed > 0) && (
        <span>
          Failure rate <span className={cn('font-medium', t.failed ? 'text-destructive' : 'text-foreground/85')}>{rate(t.failed, t.runs)}</span>, was {rate(t.prevFailed, t.prevRuns)}
        </span>
      )}
    </span>
  )
}

/** When the window's runs all fall in a few hours, the per-hour chart is one bar at the edge: start the window just
 * before the first run instead, so the bars spread over the time something actually happened. */
function zoomed(runs: ProjectPulse['runs'], w: RunWindow): RunWindow {
  const ts = runs.map((r) => atOf(r.start_time)).filter((t) => t >= w.from && t <= w.to)
  if (!ts.length) return w
  const used = new Set(bucketRuns(runs, w, SLICES).flatMap((b, i) => (b.total ? [i] : []))).size
  if (used > 3) return w
  const first = Math.min(...ts)
  const pad = Math.max(5 * 60_000, (w.to - first) * 0.15)
  const from = Math.max(w.from, first - pad)
  return w.to - from < 30 * 60_000 ? { ...w, from: w.to - 30 * 60_000 } : { ...w, from }
}

export function ActivityPanel({ pulse, window: w, className }: { pulse: ProjectPulse; window: RunWindow; className?: string }) {
  const { pid } = useProjectCtx()
  const daily = useRunDaily(pid)
  const days = daily.data?.days ?? []
  const scale = daily.data ? chartScale(days, DAYS) : null
  let chart: ReactNode = null
  if (scale === 'hour') {
    const zw = zoomed(pulse.runs, w)
    const buckets = bucketRuns(pulse.runs, zw, SLICES)
    const slice = (zw.to - zw.from) / SLICES
    chart = (
      <Bars
        testId="stats-volume"
        bars={buckets.map((b) => ({ total: b.total, failed: b.failed, label: clock(b.from) }))}
        ticks={[clock(zw.from), clock(zw.from + (zw.to - zw.from) / 2), 'now']}
        caption={zw !== w ? `Per ${span(slice)} · since ${clock(zw.from)}` : w.truncated ? `Per ${span(slice)} · newest ${pulse.runs.length} runs` : 'Per hour · last 24 hours'}
      />
    )
  } else if (scale === 'day') {
    const last = days.slice(-DAYS)
    chart = (
      <Bars
        testId="stats-volume"
        bars={last.map((d, i) => ({ total: d.count, failed: d.failed, label: i === last.length - 1 ? 'today' : utcDay(d.date, { weekday: 'short', month: 'short', day: 'numeric' }) }))}
        ticks={[utcDay(last[0].date, { month: 'short', day: 'numeric' }), utcDay(last[Math.floor((last.length - 1) / 2)].date, { month: 'short', day: 'numeric' }), 'Today']}
        caption={`Per day · last ${DAYS} days`}
      />
    )
  } else if (scale === 'none') {
    chart = (
      <div className="flex min-h-[120px] flex-1 flex-col items-center justify-center gap-1 well rounded-well text-center" data-testid="stats-volume" data-total={0}>
        <span className="text-[13px] font-medium text-foreground/80">No runs in the last {DAYS} days</span>
        <span className="text-[11.5px] text-muted-foreground">Run an app, a workflow or a route and it charts here.</span>
      </div>
    )
  }
  return (
    <Panel title="Activity" icon={<Activity />} to="/p/$project/runs" aside={<Trend days={days} />} testId="widget-health" className={className}>
      {chart}
    </Panel>
  )
}

/* ── Up next: running, scheduled, recent ───────────────────────────────────────────────────────────────── */

type Target = { kind: 'workflow' | 'app'; id: string } | { kind: 'backend'; module: string } | { kind: 'runs' }

interface NowRow {
  key: string
  kind: 'running' | 'schedule' | 'recent'
  title: string
  mono?: boolean
  sub: ReactNode
  /** the right-hand readout of a list row */
  right: ReactNode
  /** the small widget's hero */
  hero: { value: string; pre?: string; unit?: string }
  target: Target
  cells?: HeatRow['cells']
}

function Go({ target, className, children, testId }: { target: Target; className: string; children: ReactNode; testId?: string }) {
  const { slug } = useProjectCtx()
  const P = { project: slug }
  if (target.kind === 'workflow')
    return (
      <Link to="/p/$project/workflows/$workflowId" params={{ ...P, workflowId: target.id }} className={className} data-testid={testId}>
        {children}
      </Link>
    )
  if (target.kind === 'app')
    return (
      <Link to="/p/$project/apps/$appId" params={{ ...P, appId: target.id }} className={className} data-testid={testId}>
        {children}
      </Link>
    )
  if (target.kind === 'backend')
    return (
      <Link to="/p/$project/backend/$module" params={{ ...P, module: target.module }} className={className} data-testid={testId}>
        {children}
      </Link>
    )
  return (
    <Link to="/p/$project/runs" params={P} className={className} data-testid={testId}>
      {children}
    </Link>
  )
}

/** A tiny bar sparkline of a row's slices of the window. */
function Spark({ cells, className }: { cells: HeatRow['cells']; className?: string }) {
  const max = Math.max(1, ...cells.map((c) => c.total))
  return (
    <span className={cn('flex h-5 w-16 items-end gap-px', className)} aria-hidden>
      {cells.map((c, i) => (
        <span key={i} className={cn('min-w-0 flex-1 rounded-kbd', c.failed ? 'bg-destructive' : c.total ? 'bg-primary/70' : 'bg-foreground/10')} style={{ height: c.total ? `${Math.max(18, (c.total / max) * 100)}%` : 2 }} />
      ))}
    </span>
  )
}

/** The next run of a schedule at or after `now`: an `every` schedule is stepped forward from its last known next run. */
function nextAt(s: ModuleSchedule, now: number): number | null {
  const next = parseTs(s.next_run_at)?.getTime()
  if (next == null) return null
  if (next >= now || !s.every_seconds) return next
  const step = s.every_seconds * 1000
  return next + Math.ceil((now - next) / step) * step
}

/** Ticks once a second while something is counting down, so "in 12 s" keeps time between polls. */
function useClock(from: number, active: boolean) {
  const [t, setT] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setT(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [active])
  return Math.max(from, t)
}

function nowRows(pulse: ProjectPulse, schedules: ModuleSchedule[], w: RunWindow, now: number): NowRow[] {
  const out: NowRow[] = []
  for (const e of pulse.entities) {
    if (!pulse.status.get(entityKey(e.kind, e.id))?.running) continue
    out.push({
      key: `run:${e.kind}:${e.id}`,
      kind: 'running',
      title: e.name,
      sub: e.kind === 'app' ? 'App' : 'Workflow',
      right: (
        <span className="flex items-center gap-1.5 text-primary">
          <StatusDot tone="running" /> running
        </span>
      ),
      hero: { value: 'Now', unit: 'running' },
      target: { kind: e.kind, id: e.id },
    })
  }
  const upcoming = schedules
    .filter((s) => s.enabled)
    .map((s) => ({ s, at: nextAt(s, now) }))
    .filter((x): x is { s: ModuleSchedule; at: number } => x.at != null)
    .sort((a, b) => a.at - b.at)
  for (const { s, at } of upcoming) {
    const inMs = Math.max(0, at - now)
    out.push({
      key: `sch:${s.id}`,
      kind: 'schedule',
      title: s.function,
      mono: true,
      sub: (
        <span className="flex min-w-0 items-center gap-1.5">
          {s.last_status && <StatusDot tone={runTone(s.last_status)} flat />}
          <span className="truncate">
            {s.module ? `${s.module} · ` : ''}
            {cadence(s)}
          </span>
        </span>
      ),
      right: s.running ? (
        <span className="flex items-center gap-1.5 text-primary">
          <StatusDot tone="running" /> running
        </span>
      ) : (
        <span className="text-foreground/85 tabular-nums">{inMs < 1000 ? 'now' : `in ${span(inMs)}`}</span>
      ),
      hero: s.running ? { value: 'Now', unit: 'running' } : inMs < 1000 ? { value: 'Now' } : { pre: 'in', value: span(inMs) },
      target: { kind: 'backend', module: s.module ?? '~new' },
    })
  }
  for (const r of heatRows(pulse.entities, pulse.runs, w, SLICES, 6).rows) {
    out.push({
      key: `rec:${r.key}`,
      kind: 'recent',
      title: r.label,
      sub: (
        <span className="truncate">
          {pluralize(r.total, 'run')}
          {r.failed ? <span className="text-destructive"> · {r.failed} failed</span> : null}
        </span>
      ),
      right: <Spark cells={r.cells} />,
      hero: { value: fmtNum(r.total), unit: r.total === 1 ? 'run' : 'runs' },
      target: r.kind === 'other' || !r.id ? { kind: 'runs' } : { kind: r.kind, id: r.id },
      cells: r.cells,
    })
  }
  return out
}

export function UpNextWidget({ pulse, schedules, window: w, cap = 3, className }: { pulse: ProjectPulse; schedules: ModuleSchedule[]; window: RunWindow; cap?: number; className?: string }) {
  const ticking = schedules.some((s) => s.enabled)
  const now = useClock(pulse.now, ticking)
  const rows = nowRows(pulse, schedules, w, now)
  const live = rows.some((r) => r.kind !== 'recent')
  const rowCls = 'group/r grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-b border-hairline px-3 py-2 outline-none last:border-b-0 hover:bg-(--hover-fill) focus-visible:shadow-focus'
  const more = rows.length - cap
  return (
    <Panel
      title={live ? 'Up next' : 'Recently'}
      icon={live ? <CalendarClock /> : <Activity />}
      to={schedules.length ? '/p/$project/backend' : '/p/$project/runs'}
      aside={live ? (schedules.length ? pluralize(schedules.length, 'schedule') : undefined) : w.truncated ? `Newest ${pulse.runs.length} runs` : 'Last 24 hours'}
      testId="widget-now"
      className={className}
    >
      {!rows.length ? (
        <div className="flex flex-col gap-0.5 px-0.5" data-testid="now-empty">
          <span className="text-[13px] font-medium text-foreground/80">Quiet</span>
          <span className="text-[11.5px] leading-snug text-muted-foreground">Nothing ran in 24 hours and nothing is scheduled.</span>
        </div>
      ) : (
        <div className="well flex min-h-0 flex-col overflow-hidden rounded-well">
          {rows.slice(0, cap).map((r) => (
            <Go key={r.key} target={r.target} className={rowCls} testId={`now-${r.key}`}>
              <span className="flex min-w-0 flex-col">
                <span className={cn('truncate text-[12.5px] font-medium text-foreground/90', r.mono && 'font-mono text-[12px]')}>{r.title}</span>
                <span className="flex min-w-0 text-[11px] text-muted-foreground">{r.sub}</span>
              </span>
              <span className="flex items-center text-[12px]">{r.right}</span>
            </Go>
          ))}
        </div>
      )}
      {more > 0 && <span className="-mt-1 px-0.5 text-[11px] text-faint">+{more} more</span>}
    </Panel>
  )
}

/* ── Needs attention ─────────────────────────────────────────────────────── */

function AttentionRow({ item, firstModule }: { item: AttentionItem; firstModule?: string }) {
  const { slug } = useProjectCtx()
  const P = { project: slug }
  const t = item.target
  const cls = 'group/n flex items-center gap-2.5 border-b border-hairline px-3 py-2 outline-none last:border-b-0 hover:bg-(--hover-fill) focus-visible:shadow-focus'
  const body = (
    <>
      <StatusDot tone={item.tone === 'running' ? 'running' : item.tone} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[12.5px] font-medium">{item.title}</span>
        <span className="truncate text-[11.5px] text-muted-foreground">{item.sub}</span>
      </span>
      <span className="flex shrink-0 items-center gap-1 text-[11.5px] font-medium text-primary">
        {item.action} <ArrowRight className="size-3 transition-transform duration-(--d-fast) group-hover/n:translate-x-0.5" />
      </span>
    </>
  )
  const props = { className: cls, 'data-testid': `attention-${item.key}`, 'data-tone': item.tone }
  if (t.kind === 'workflow')
    return (
      <Link to="/p/$project/workflows/$workflowId" params={{ ...P, workflowId: t.id }} search={(t.execution ? { execution: t.execution } : {}) as never} {...props}>
        {body}
      </Link>
    )
  if (t.kind === 'run')
    return (
      <Link to="/p/$project/runs/$executionId" params={{ ...P, executionId: t.id }} {...props}>
        {body}
      </Link>
    )
  if (t.kind === 'backend')
    return (
      <Link to="/p/$project/backend/$module" params={{ ...P, module: firstModule ?? '~new' }} {...props}>
        {body}
      </Link>
    )
  return (
    <Link to={t.kind === 'approvals' ? '/p/$project/approvals' : '/p/$project/secrets'} params={P} {...props}>
      {body}
    </Link>
  )
}

export function AttentionWidget({ items, firstModule, cap = 3 }: { items: AttentionItem[]; firstModule?: string; cap?: number }) {
  if (!items.length)
    return (
      <Panel title="Attention" icon={<CheckCircle2 />} testId="attention">
        <div className="flex items-center gap-3 px-0.5" data-testid="attention-clear">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-success/12 text-success [&>svg]:size-[18px]" aria-hidden>
            <CheckCircle2 />
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="text-[14px] leading-tight font-semibold tracking-[-0.015em]">All clear</span>
            <span className="truncate text-[11.5px] text-muted-foreground">Nothing needs you right now.</span>
          </span>
        </div>
      </Panel>
    )
  return (
    <Panel title="Needs attention" icon={<AlertTriangle />} aside={items.length > cap ? `${items.length - cap} more` : pluralize(items.length, 'item')} testId="attention">
      <div className="well flex flex-col overflow-hidden rounded-well">
        {items.slice(0, cap).map((it) => (
          <AttentionRow key={it.key} item={it} firstModule={firstModule} />
        ))}
      </div>
    </Panel>
  )
}

/* ── Busiest routes ────────────────────────────────────────────────────────────────────────────────────────────── */

export function RoutesPanel({ pulse, window: w, cap = 4, className }: { pulse: ProjectPulse; window: RunWindow; cap?: number; className?: string }) {
  const { slug } = useProjectCtx()
  const by = new Map<string, { id: string | null; route: string; n: number; failed: number; ds: number[] }>()
  for (const r of pulse.runs) {
    if (!r.route || atOf(r.start_time) < w.from) continue
    const k = r.route_id ?? r.route
    const e = by.get(k) ?? { id: r.route_id, route: r.route, n: 0, failed: 0, ds: [] }
    e.n++
    if (toneOf(r.status) === 'err' || (r.status_code ?? 0) >= 500) e.failed++
    if (r.duration_ms != null) e.ds.push(r.duration_ms)
    by.set(k, e)
  }
  const rows = [...by.values()].sort((a, b) => b.n - a.n || a.route.localeCompare(b.route))
  if (!rows.length) return null
  const max = rows[0].n
  return (
    <Panel title="Busiest routes" icon={<Route />} to="/p/$project/runs" aside={pluralize(rows.length, 'route')} testId="widget-routes" className={className}>
      <div className="well flex flex-col overflow-hidden rounded-well">
        {rows.slice(0, cap).map((r) => {
          const [method, ...rest] = r.route.split(' ')
          const path = rest.join(' ') || method
          const p50 = percentile(r.ds, 0.5)
          return (
            <Link
              key={r.id ?? r.route}
              to="/p/$project/runs"
              params={{ project: slug }}
              search={(r.id ? { route: r.id } : {}) as never}
              className="relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-b border-hairline px-3 py-2 outline-none last:border-b-0 hover:bg-(--hover-fill) focus-visible:shadow-focus"
              data-testid={`route-row-${r.id ?? r.route}`}
            >
              <span className="absolute inset-y-1 left-0 rounded-r-kbd bg-foreground/5" style={{ width: `${(r.n / max) * 100}%` }} aria-hidden />
              <span className="relative flex min-w-0 items-baseline gap-2">
                {rest.length > 0 && <span className="shrink-0 font-mono text-[10.5px] font-semibold text-muted-foreground">{method}</span>}
                <span className="truncate font-mono text-[12px] text-foreground/90">{path}</span>
              </span>
              <span className="relative flex items-center gap-2 text-[11.5px] text-muted-foreground tabular-nums">
                {r.failed > 0 && <span className="text-destructive">{r.failed} failed</span>}
                {p50 != null && <span>{shortMsLabel(p50)}</span>}
                <span className="font-medium text-foreground/85">{fmtNum(r.n)}</span>
              </span>
            </Link>
          )
        })}
      </div>
    </Panel>
  )
}

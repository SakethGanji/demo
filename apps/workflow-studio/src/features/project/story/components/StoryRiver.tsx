/**
 * The river (design §3): every run of the last 24h as a dot on a time line (failed ones red, new ones drop in
 * at the right), the 30-day ribbon from `/runs/daily` under it, the totals, and chips for the latest runs.
 */
import type { ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { Activity, Play } from 'lucide-react'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Button } from '@/shared/components/ui/button'
import { IconTile } from '@/shared/components/ui/surface'
import { runTone, StatusDot } from '@/shared/components/ui/status-dot'
import type { RunListItem } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'
import { hashString } from '@/features/knowledge/lib/layout'
import { useProjectCtx } from '../../ProjectContext'
import { useRunDaily } from '../../hooks/useRunStats'
import { formatMs, parseTs, pluralize, timeAgo } from '../../lib/format'

const DAY = 24 * 3600 * 1000
const LATEST = 8

function runLabel(r: RunListItem): string {
  return r.route ?? r.workflow_name ?? 'run'
}

export function StoryRiver({
  runs,
  now,
  runs24h,
  failed24h,
  p50,
  since,
  emptyHint,
  onEmptyAction,
}: {
  runs: RunListItem[]
  now: number
  runs24h: number
  failed24h: number
  p50: number | null
  /** runs that started after this drop in */
  since: number
  emptyHint: string
  /** the one thing that would make a first run (run a workflow / test a route) */
  onEmptyAction?: { label: string; run: () => void }
}) {
  const { pid, slug } = useProjectCtx()
  const daily = useRunDaily(pid)
  const recent = runs.filter((r) => (parseTs(r.start_time)?.getTime() ?? 0) >= now - DAY)

  if (!runs24h && !runs.length)
    return (
      <section className="tile px-[22px] py-[18px]" aria-label="Runs" data-testid="story-river" id="story-river">
        <RiverHead>
          <span className="text-[13.5px] font-semibold tracking-[-0.01em]">No runs yet</span>
        </RiverHead>
        <div className="story-stream well mt-3 grid place-items-center px-4" data-testid="story-river-empty">
          <div className="story-axis" aria-hidden />
          <div className="story-now" aria-hidden />
          <div className="relative flex flex-wrap items-center justify-center gap-3 text-[13px] text-muted-foreground">
            {emptyHint}
            {onEmptyAction && (
              <Button variant="outline" size="sm" onClick={onEmptyAction.run} data-testid="story-river-first-run">
                <Play /> {onEmptyAction.label}
              </Button>
            )}
          </div>
        </div>
      </section>
    )

  const days = daily.data?.days ?? []
  const max = Math.max(1, ...days.map((d) => d.count))
  const success = runs24h ? (100 * (1 - failed24h / runs24h)).toFixed(1) : null
  return (
    <section className="tile px-[22px] py-[18px]" aria-label="Runs" data-testid="story-river" id="story-river">
      <RiverHead>
        <span className="text-[13.5px] font-semibold tracking-[-0.01em]">Runs</span>
        <span className="flex-1" />
        <Button variant="ghost" size="sm" render={<Link to="/p/$project/runs" params={{ project: slug }} />} data-testid="recent-runs-all">
          View all runs
        </Button>
      </RiverHead>
      <dl className="story-stats mt-4 flex flex-wrap gap-y-3" data-testid="story-river-stats">
        <Stat label="runs · 24h" value={runs24h} />
        <Stat label="failed" value={failed24h} tone={failed24h ? 'bad' : undefined} />
        {p50 != null && <Stat label="p50" value={formatMs(p50)} />}
        {success && <Stat label="success" value={`${success}%`} />}
      </dl>
      <div className="story-stream well mt-4" role="img" aria-label={`${pluralize(recent.length, 'run')} in the last 24 hours, ${recent.filter((r) => runTone(r.status) === 'err').length} failed`}>
        <div className="story-axis" />
        {recent.map((r) => {
          const t = parseTs(r.start_time)?.getTime() ?? now
          const x = Math.min(99.4, Math.max(0.6, (100 * (t - (now - DAY))) / DAY))
          const y = 20 + (hashString(r.id) % 600) / 10
          const failed = runTone(r.status) === 'err'
          return (
            <span
              key={r.id}
              className={cn('story-run', t > since && 'story-fresh')}
              data-failed={failed}
              style={{ left: `${x}%`, top: `${y}%` }}
              title={`${failed ? 'failed' : 'ok'} · ${runLabel(r)}${r.status_code != null ? ` · ${r.status_code}` : ''} · ${formatMs(r.duration_ms)} · ${timeAgo(r.start_time, now)}`}
            />
          )
        })}
        <div className="story-now" />
      </div>
      <div className="mt-1.5 flex justify-between px-0.5 text-[10.5px] text-faint" aria-hidden>
        <span>24h ago</span>
        <span>now</span>
      </div>
      {days.length > 0 && (
        <>
          <div className="mt-3 grid gap-[3px]" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }} role="img" aria-label={`${daily.data?.total ?? 0} runs in the last ${days.length} days, ${daily.data?.failed ?? 0} failed`} data-testid="story-ribbon">
            {days.map((d) => (
              // the day's volume tints it; its failed share fills from the bottom in red, so one failure in a busy
              // day reads as a sliver, not a red day
              <span
                key={d.date}
                className="relative h-4 overflow-hidden rounded-chip"
                title={`${d.date}: ${pluralize(d.count, 'run')}${d.failed ? `, ${d.failed} failed` : ''}`}
                style={{ background: `color-mix(in srgb, var(--primary) ${d.count ? Math.round(14 + (d.count / max) * 74) : 6}%, transparent)` }}
              >
                {d.failed > 0 && (
                  <span
                    className="absolute inset-x-0 bottom-0 bg-destructive/80"
                    style={{ height: `${Math.max(25, Math.round((100 * d.failed) / Math.max(1, d.count)))}%` }}
                  />
                )}
              </span>
            ))}
          </div>
          <div className="mt-1.5 flex justify-between px-0.5 text-[10.5px] text-faint" aria-hidden>
            <span>{days.length} days ago</span>
            <span>today</span>
          </div>
        </>
      )}
      <div className="story-chips mt-4 flex gap-2 overflow-x-auto pr-10 pb-1" data-scroll-x="allowed">
        {runs.slice(0, LATEST).map((r) => {
          const failed = runTone(r.status) === 'err'
          const [method, ...rest] = (r.route ?? '').split(' ')
          return (
            <Link
              key={r.id}
              to="/p/$project/runs/$executionId"
              params={{ project: slug, executionId: r.id }}
              className={cn(
                'story-chip story-raised flex flex-none items-center gap-2 px-2.5 py-1.5 text-xs focus-visible:shadow-focus focus-visible:outline-none',
                (parseTs(r.start_time)?.getTime() ?? 0) > since && 'story-fresh',
              )}
              data-testid={`recent-run-${r.id}`}
              data-status={r.status}
            >
              <StatusDot tone={failed ? 'err' : 'ok'} flat aria-label={failed ? 'failed' : 'ok'} role="img" />
              {r.route ? (
                <>
                  <MethodBadge method={method} size="sm" />
                  <span className="font-mono">{rest.join(' ')}</span>
                </>
              ) : (
                <span>{r.workflow_name}</span>
              )}
              <span className="text-muted-foreground tabular-nums">{timeAgo(r.start_time, now)}</span>
            </Link>
          )
        })}
      </div>
    </section>
  )
}

function RiverHead({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <IconTile hue="runs">
        <Activity />
      </IconTile>
      {children}
    </div>
  )
}

/** a big confident number over a small muted label; hairlines between them, no boxes */
function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: 'bad' }) {
  return (
    <div className="story-stat flex flex-col gap-1">
      <dt className="order-2 text-[11.5px] text-muted-foreground">{label}</dt>
      <dd className={cn('order-1 text-[26px] leading-none font-semibold whitespace-nowrap tracking-[-0.03em] tabular-nums', tone === 'bad' && 'text-destructive')}>{value}</dd>
    </div>
  )
}

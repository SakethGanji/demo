/**
 * The 24h hourly timeline (DESIGN §6, `runs.html`): 24 hourly columns on a fading star-dust grid, a smoothed
 * trend line just above the bars, the current hour as the one focal bar, and a red node with a drop line at
 * the hour of the latest failure. Drawn from the runs the page has loaded.
 */
import { useId } from 'react'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { HOUR, smoothPath, startMs, type DayStats } from '../lib/runStats'

const H = 60 // plot height
const TOP = 12 // headroom for the trend line and the node

const clock = (ms: number) => new Date(ms).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })

export function RunTimeline({ stats, now }: { stats: DayStats; now: number }) {
  const gid = useId()
  const { buckets } = stats
  const peak = Math.max(4, ...buckets.map((b) => b.count))
  const hY = (n: number) => H - (n / peak) * (H - TOP)
  const sm = buckets.map((b, i) => (buckets[Math.max(0, i - 1)].count + 2 * b.count + buckets[Math.min(23, i + 1)].count) / 4)
  const trend = smoothPath(sm.map((n, i) => [i * 10 + 5, hY(n) - 4]))
  const errAt = stats.lastFailed ? 23 - Math.floor((now - startMs(stats.lastFailed)) / HOUR) : -1
  const errY = errAt >= 0 ? hY(buckets[errAt].count) - 10 : 0
  return (
    <div className="relative min-w-0 px-4 pt-4 pb-3" data-testid="runs-timeline">
      <div
        className="pointer-events-none absolute inset-0 opacity-80 [background-image:radial-gradient(circle,var(--art-line)_.7px,transparent_1.1px)] [background-position:16px_0] [background-size:calc((100%-32px)/24)_22px] [mask-image:radial-gradient(70%_90%_at_85%_30%,#000_5%,transparent_80%)]"
        aria-hidden
      />
      <div className="relative grid h-[60px] grid-cols-24 items-end">
        {buckets.map((b, i) => {
          const now_ = i === 23
          const label =
            `${clock(b.from)}–${now_ ? 'now' : clock(b.to)} · ${b.count} run${b.count === 1 ? '' : 's'}` + (b.failed ? `, ${b.failed} failed` : '')
          return (
            <Tip key={i} label={label}>
              <div className="flex h-full cursor-default items-end justify-center" data-testid="runs-timeline-col" data-count={b.count} data-failed={b.failed}>
                <div
                  className={cn(
                    'w-[64%] rounded-kbd transition-[height,background-color] duration-(--d-slow) ease-(--ease-out)',
                    now_ ? 'bg-primary' : b.count ? 'bg-foreground/9 hover:bg-foreground/22' : 'bg-foreground/5',
                  )}
                  style={{ height: Math.max(2, H - hY(b.count)) }}
                />
              </div>
            </Tip>
          )
        })}
        <svg className="pointer-events-none absolute top-0 left-0 h-[68px] w-full overflow-visible" aria-hidden>
          <defs>
            <linearGradient id={`${gid}-line`} gradientUnits="userSpaceOnUse" x1="0" x2="240" y1="0" y2="0"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
          </defs>
          {Array.from({ length: 25 }, (_, i) => (
            <circle key={i} cx={`${(i / 24) * 100}%`} cy={H + 4} r={i % 6 ? 0.9 : 1.3} fill="var(--art-line)" />
          ))}
          <svg viewBox={`0 0 240 ${H}`} preserveAspectRatio="none" width="100%" height={H} overflow="visible">
            <path d={trend} fill="none" stroke={`url(#${gid}-line)`} strokeWidth="1.25" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          </svg>
          {errAt >= 0 && (
            <svg x={`${((errAt + 0.5) / 24) * 100}%`} overflow="visible">
              <g style={{ transform: `translateY(${errY}px)`, transition: 'transform var(--d-slow) var(--ease-out)' }}>
                <line x1="0" x2="0" y1="3" y2={H + 4 - errY} stroke="var(--destructive)" strokeWidth="1" opacity=".7" />
                <circle r="6" fill="color-mix(in srgb, var(--destructive) 22%, transparent)" />
                <circle r="3" fill="var(--destructive)" stroke="var(--well)" strokeWidth="2" paintOrder="stroke" />
              </g>
            </svg>
          )}
        </svg>
      </div>
      <div className="relative mt-2.5 h-3 font-mono text-[10px] leading-3 text-muted-foreground">
        {[
          ['0%', '24h ago', ''],
          ['25%', '18h', '-translate-x-1/2'],
          ['50%', '12h', '-translate-x-1/2'],
          ['75%', '6h', '-translate-x-1/2'],
          ['100%', 'now', '-translate-x-full'],
        ].map(([left, t, tr]) => (
          <span key={t} className={cn('absolute top-0 whitespace-nowrap', tr)} style={{ left }}>
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}

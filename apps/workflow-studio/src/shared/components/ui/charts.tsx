/**
 * Small data marks (DESIGN §6, §8).
 *   Sparkline     bars for a short series (24h buckets…). The last bar is the focal one (primary); the rest are quiet.
 *   ActivityGrid  a 30-day calendar (Mon–Sun columns, one row per week), intensity in steps of the primary,
 *                 failures marked with a red dot. Each cell carries `data-date`, `data-count` and a tooltip.
 */
import { cn } from '@/shared/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip'

export function Sparkline({
  values,
  height = 40,
  className,
  barClassName,
  focalLast = true,
  labels,
  ...props
}: {
  values: number[]
  height?: number
  className?: string
  barClassName?: string
  focalLast?: boolean
  /** optional axis labels under the bars: [left, middle, right] */
  labels?: [string, string, string]
} & React.HTMLAttributes<HTMLDivElement>) {
  const max = Math.max(1, ...values)
  return (
    <div className={cn('flex flex-col gap-1.5', className)} {...props}>
      <div className="flex items-end gap-[3px]" style={{ height }} aria-hidden>
        {values.map((v, i) => {
          const last = focalLast && i === values.length - 1
          return (
            <span
              key={i}
              className={cn(
                'min-w-[3px] flex-1 rounded-kbd transition-[height] duration-(--d-slow) ease-(--ease-out)',
                last ? 'bg-primary' : 'bg-foreground/16',
                barClassName,
              )}
              style={{ height: `${Math.max(v > 0 ? 3 : 2, (v / max) * height)}px` }}
            />
          )
        })}
      </div>
      {labels && (
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>{labels[0]}</span>
          <span>{labels[1]}</span>
          <span>{labels[2]}</span>
        </div>
      )}
    </div>
  )
}

export interface ActivityDay {
  /** ISO date (yyyy-mm-dd) */
  date: string
  count: number
  failed: number
}

const DOW = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
const fmtWeek = (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
const fmtDay = (d: Date) => d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })

/** steps of the primary, from empty to most */
/** neutral steps (Linear-like); colour is kept for meaning: today's ring, the latest bar, failures */
const LEVELS = ['bg-recess', 'bg-foreground/12', 'bg-foreground/24', 'bg-foreground/40', 'bg-foreground/62']

function level(count: number, max: number) {
  if (count <= 0) return 0
  return Math.min(4, 1 + Math.floor((count / Math.max(1, max)) * 3.999))
}

export function ActivityGrid({ days, className, today, ...props }: { days: ActivityDay[]; today?: string; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  if (!days.length) return null
  const max = Math.max(1, ...days.map((d) => d.count))
  const parse = (s: string) => new Date(`${s}T00:00:00`)
  const first = parse(days[0].date)
  const lead = (first.getDay() + 6) % 7 // Monday-first
  const cells: Array<ActivityDay | null> = [...Array(lead).fill(null), ...days]
  const weeks: Array<Array<ActivityDay | null>> = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
  const last = today ?? days[days.length - 1].date
  return (
    <div className={cn('grid w-fit grid-cols-[auto_repeat(7,minmax(0,26px))] items-center gap-[5px]', className)} role="grid" aria-label="Runs per day, last 30 days" {...props}>
      <span />
      {DOW.map((d, i) => (
        <span key={i} className="text-center text-[11px] text-muted-foreground">
          {d}
        </span>
      ))}
      {weeks.map((w, wi) => {
        const start = w.find(Boolean)
        return [
          <span key={`l${wi}`} className="pr-2 text-right text-[11px] whitespace-nowrap text-muted-foreground">
            {start ? fmtWeek(parse(start.date)) : ''}
          </span>,
          ...Array.from({ length: 7 }, (_, di) => {
            const d = w[di]
            if (!d) return <span key={`e${wi}-${di}`} />
            const lv = level(d.count, max)
            const isToday = d.date === last
            return (
              <Tooltip key={d.date}>
                <TooltipTrigger
                  render={
                    <span
                      role="gridcell"
                      data-date={d.date}
                      data-count={d.count}
                      data-failed={d.failed}
                      className={cn(
                        'relative aspect-square min-h-3 rounded-chip',
                        LEVELS[lv],
                        isToday && 'shadow-[0_0_0_1.5px_var(--canvas),0_0_0_2.5px_var(--ring)]',
                      )}
                    />
                  }
                >
                  {d.failed > 0 && <span className="absolute top-[3px] right-[3px] size-[5px] rounded-full bg-destructive shadow-[0_0_0_1.5px_var(--canvas)]" />}
                </TooltipTrigger>
                <TooltipContent>
                  {fmtDay(parse(d.date))} · {d.count} run{d.count === 1 ? '' : 's'}
                  {d.failed > 0 && <span className="text-destructive"> · {d.failed} failed</span>}
                </TooltipContent>
              </Tooltip>
            )
          }),
        ]
      })}
    </div>
  )
}

/** The "Less ▢▢▢▢ More · ● failed" legend that goes with ActivityGrid. */
export function ActivityLegend({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center gap-1.5 text-[11px] text-muted-foreground', className)}>
      Less
      {LEVELS.map((c) => (
        <span key={c} className={cn('size-2 rounded-full', c)} />
      ))}
      More
      <span className="ml-2 size-[5px] rounded-full bg-destructive" /> failed
    </div>
  )
}

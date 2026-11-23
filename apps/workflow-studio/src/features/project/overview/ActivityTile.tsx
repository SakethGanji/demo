import { Link } from '@tanstack/react-router'
import { Activity } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useRunDaily, useRunStats } from '../hooks/useRunStats'
import { span, TileHead } from './tiles'
import { Button } from '@/shared/components/ui/button'
import { Roll } from '@/shared/components/ui/roll'
import { ActivityGrid, ActivityLegend } from '@/shared/components/ui/charts'
import { Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'

/** The focal tile: the 24h run count, 12 two-hour bars and the 30-day calendar. */
export function ActivityTile({ runs24h }: { runs24h: number }) {
  const { pid, slug } = useProjectCtx()
  const stats = useRunStats(pid)
  const d = stats.data
  const daily = useRunDaily(pid).data
  return (
    <Tile
      as="section"
      className={cn(
        'grid grid-cols-2 grid-rows-[auto_1fr] @min-[981px]/bt:flex @min-[981px]/bt:flex-col',
        span.runs,
      )}
      data-testid="activity-tile"
    >
      <TileHead
        className="col-span-2"
        icon={<Activity />}
        hue="runs"
        title="Activity"
        actions={
          <Button variant="ghost" size="sm" render={<Link to="/p/$project/runs" params={{ project: slug }} />} data-testid="activity-view-all">
            View all
          </Button>
        }
      />
      <div className="px-5 pb-4 @min-[981px]/bt:pb-0" data-testid="stat-runs">
        <div className="flex h-5 items-center text-[12px] font-medium text-muted-foreground">Runs · 24h</div>
        <div className="text-[48px] leading-[1.2] font-semibold tracking-[-0.04em]">
          <Roll value={runs24h} data-testid="stat-runs-value" />
        </div>
        <Bars values={d?.sparkline ?? new Array(12).fill(0)} />
      </div>
      <Well className="mt-4 mr-2 mb-2 ml-0 flex flex-col gap-2 px-4 pt-3 pb-[11px] @min-[981px]/bt:mt-4 @min-[981px]/bt:mr-2 @min-[981px]/bt:ml-2 @min-[981px]/bt:flex-1" data-testid="activity-30d">
        <div className="flex items-center justify-between gap-2 text-[12px] text-muted-foreground">
          <Tip label={daily ? `${daily.total} runs in the last 30 days (UTC days)` : 'Counted per day by the engine'}>
            <span>
              <b className="font-medium text-foreground">30 days</b> · {daily?.failed ?? 0} failed
            </span>
          </Tip>
          <ActivityLegend />
        </div>
        <div className="flex flex-1 items-center justify-center">{daily && <ActivityGrid days={daily.days} />}</div>
      </Well>
    </Tile>
  )
}

/** 12 two-hour bars; the last is the focal one. Empty buckets are small recessed pills, not a flat line. */
function Bars({ values }: { values: number[] }) {
  const max = Math.max(1, ...values)
  const H = 44
  return (
    <div className="mt-2 flex flex-col gap-1.5" data-testid="activity-spark">
      <div className="flex items-end gap-[3px]" style={{ height: H }} aria-hidden>
        {values.map((v, i) => (
          <span
            key={i}
            className={cn(
              'min-w-[3px] flex-1 transition-[height] duration-(--d-slow) ease-(--ease-out)',
              v === 0 ? 'rounded-full bg-foreground/12' : 'rounded-kbd',
              v > 0 && (i === values.length - 1 ? 'bg-primary' : 'bg-foreground/16'),
            )}
            style={{ height: v === 0 ? 3 : Math.max(3, (v / max) * H) }}
          />
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>24h ago</span>
        <span>12h</span>
        <span>now</span>
      </div>
    </div>
  )
}

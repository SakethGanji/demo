import { Link } from '@tanstack/react-router'
import { ArrowUpRight, CircleAlert, Route as RouteIcon, Table2 } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useModules, useRoutes, useTables } from '../hooks/useProjectData'
import { useRunStats } from '../hooks/useRunStats'
import { formatMs, pluralize } from '../lib/format'
import { IconTile, type EntityHue } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'

const tileCls = 'tile glass-hover group relative flex min-w-0 flex-col'
/** three across, under the building blocks */
const statSpan = 'col-span-2 @min-[981px]/bt:col-span-4'

function MiniStat({
  testId,
  icon,
  hue,
  iconClassName,
  title,
  tip,
  value,
  meta,
  link,
}: {
  testId: string
  icon: React.ReactNode
  hue: EntityHue
  iconClassName?: string
  title: string
  tip: string
  value: number
  meta: string
  link: (props: { className: string; 'data-testid': string; children: React.ReactNode }) => React.ReactElement
}) {
  return link({
    className: cn(tileCls, statSpan),
    'data-testid': testId,
    children: (
      <>
        <div className="flex items-center gap-2.5 px-5 pt-4">
          <IconTile hue={hue} className={iconClassName}>
            {icon}
          </IconTile>
          <h3 className="text-[13px] font-[550] text-foreground">{title}</h3>
          <div className="flex-1" />
          <Tip label={tip}>
            <span className="grid size-6 place-items-center rounded-ctl text-muted-foreground transition-colors duration-(--d-fast) group-hover:text-foreground [&_svg]:size-3.5">
              <ArrowUpRight />
            </span>
          </Tip>
        </div>
        <div className="px-5 pt-1.5 pb-4">
          <div className="text-[30px] leading-[1.1] font-bold tracking-[-0.03em] tabular-nums" data-testid={`${testId}-value`}>
            {value}
          </div>
          <div className="mt-1 truncate text-[11.5px] text-muted-foreground">{meta.replace(/^·\s*/, '')}</div>
        </div>
      </>
    ),
  })
}

/** Row 3: Routes, Tables, Failed · 24h. Each is a link to its page. (The interface count lives on the building blocks row.) */
export function StatTiles({ failed24h }: { failed24h: number }) {
  const { pid, slug } = useProjectCtx()
  const P = { project: slug }
  const routes = useRoutes(pid)
  const modules = useModules(pid)
  const tables = useTables(pid)
  const stats = useRunStats(pid)
  const rows = (tables.data ?? []).reduce((n, t) => n + (t.row_count ?? 0), 0)
  const m0 = modules.data?.[0]
  const t0 = tables.data?.[0]
  const lastFailed = stats.data?.recentFailed
  return (
    <div className="col-span-full grid grid-cols-subgrid gap-4" data-testid="stat-strip">
      <MiniStat
        testId="stat-routes"
        icon={<RouteIcon />}
        hue="backend"
        title="Routes"
        tip={m0 ? `Open ${m0.name}.py` : 'Open the backend'}
        value={routes.data?.length ?? 0}
        meta={m0 ? `· in ${m0.name}.py` : '· no module yet'}
        link={(p) =>
          m0 ? (
            <Link to="/p/$project/backend/$module" params={{ ...P, module: m0.name }} {...p} />
          ) : (
            <Link to="/p/$project/backend/$module" params={{ ...P, module: '~new' }} search={{ generate: 1 }} {...p} />
          )
        }
      />
      <MiniStat
        testId="stat-tables"
        icon={<Table2 />}
        hue="data"
        title="Tables"
        tip={t0 ? `Open the ${t0.name} table` : 'Open data'}
        value={tables.data?.length ?? 0}
        meta={`· ${pluralize(rows, 'row')}`}
        link={(p) => <Link to="/p/$project/data" params={P} {...p} />}
      />
      <MiniStat
        testId="stat-failed"
        icon={<CircleAlert />}
        hue="runs"
        iconClassName="text-destructive"
        title="Failed · 24h"
        tip={lastFailed ?? 'Open runs'}
        value={failed24h}
        meta={`· p50 ${formatMs(stats.data?.p50 ?? null)}`}
        link={(p) => <Link to="/p/$project/runs" params={P} {...p} />}
      />
    </div>
  )
}

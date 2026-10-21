/**
 * The project's data on the Overview: one card per table (rows, columns, last change) and one per connected database,
 * with "Connect a database" in the header. Managing a connection (test, refresh, remove) stays on the setup guide's
 * Databases section; here a database card opens its schema.
 */
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Database, Plus, Table2 } from 'lucide-react'
import { tablesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { Button } from '@/shared/components/ui/button'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { SectionHeader, Tile } from '@/shared/components/ui/surface'
import { ConnectDialog } from '@/features/connections/ConnectDialog'
import { SchemaBrowser } from '@/features/connections/SchemaBrowser'
import { hostLabel, statusTone } from '@/features/connections/connectionsLogic'
import type { Connection } from '@/features/connections/connectionsTypes'
import { useConnections } from '@/features/connections/useConnections'
import { pluralize, timeAgo } from '../../lib/format'

const card = 'group/d flex min-w-0 flex-col gap-2 rounded-panel p-3.5 text-left outline-none focus-visible:shadow-focus'

function CardHead({ icon, name, mono }: { icon: React.ReactNode; name: string; mono?: boolean }) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="icon-tile size-6 shrink-0 text-muted-foreground [&>svg]:size-[13px]" aria-hidden>
        {icon}
      </span>
      <span className={mono ? 'truncate font-mono text-[12.5px] font-medium' : 'truncate text-[13px] font-semibold'}>{name}</span>
    </span>
  )
}

export function DataSection({ pid, slug }: { pid: string; slug: string }) {
  const tables = useQuery({ queryKey: qk.tables(pid), queryFn: () => tablesApi.list(pid) })
  const conns = useConnections(pid)
  const [connectOpen, setConnectOpen] = useState(false)
  const [browsing, setBrowsing] = useState<Connection | null>(null)
  const ts = [...(tables.data ?? [])].sort((a, b) => (b.row_count ?? 0) - (a.row_count ?? 0) || a.name.localeCompare(b.name))
  const dbs = conns.data ?? []
  const rows = ts.reduce((s, t) => s + (t.row_count ?? 0), 0)

  return (
    <section className="flex flex-col gap-3" data-testid="home-data">
      <SectionHeader
        icon={<Database />}
        hue="data"
        title={
          <h2 className="inline">
            Data<span className="font-normal text-muted-foreground"> · </span>
            <span className="font-normal tabular-nums text-muted-foreground">{ts.length + dbs.length}</span>
          </h2>
        }
        sub={ts.length ? `${rows.toLocaleString('en-US')} rows across ${pluralize(ts.length, 'table')}${dbs.length ? ` · ${pluralize(dbs.length, 'database')}` : ''}` : undefined}
        actions={
          <Button variant="outline" size="sm" onClick={() => setConnectOpen(true)} data-testid="connect-open">
            <Plus /> Connect a database
          </Button>
        }
      />
      {ts.length === 0 && dbs.length === 0 && !tables.isPending ? (
        <p className="-mt-1 text-[12.5px] text-muted-foreground" data-testid="home-data-empty">
          No tables yet. The builder makes them from the plan, or connect a database and build on its tables.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-3 @[640px]/bt:grid-cols-3 @[880px]/bt:grid-cols-4">
          {ts.map((t) => (
            <Tile key={t.id} interactive className="p-0">
              <Link to="/p/$project/data/$table" params={{ project: slug, table: t.name }} className={card} data-testid={`home-table-${t.name}`}>
                <CardHead icon={<Table2 />} name={t.name} mono />
                <span className="flex items-baseline gap-1.5">
                  <span className="text-[22px] leading-none font-[650] tracking-[-0.035em] tabular-nums">{(t.row_count ?? 0).toLocaleString('en-US')}</span>
                  <span className="text-[11.5px] text-muted-foreground">{t.row_count === 1 ? 'row' : 'rows'}</span>
                </span>
                <span className="truncate text-[11.5px] text-muted-foreground">
                  {pluralize(t.columns.length, 'column')} · changed {timeAgo(t.updated_at)}
                </span>
              </Link>
            </Tile>
          ))}
          {dbs.map((c) => (
            <Tile key={c.id} interactive className="p-0">
              <button type="button" onClick={() => setBrowsing(c)} className={card} data-testid={`home-db-${c.key}`}>
                <CardHead icon={<Database />} name={c.name} />
                <span className="flex items-baseline gap-1.5">
                  <span className="text-[22px] leading-none font-[650] tracking-[-0.035em] tabular-nums">{c.table_count}</span>
                  <span className="text-[11.5px] text-muted-foreground">{c.table_count === 1 ? 'table' : 'tables'}</span>
                </span>
                <span className="flex min-w-0 items-center gap-1.5 text-[11.5px] text-muted-foreground">
                  <StatusDot tone={statusTone(c.status)} flat />
                  <span className="truncate">{hostLabel(c)}{c.readonly ? ' · read-only' : ''}</span>
                </span>
              </button>
            </Tile>
          ))}
        </div>
      )}
      <ConnectDialog pid={pid} open={connectOpen} onClose={() => setConnectOpen(false)} />
      <SchemaBrowser pid={pid} connection={browsing} onClose={() => setBrowsing(null)} />
    </section>
  )
}

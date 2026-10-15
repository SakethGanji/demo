/**
 * Data view (UI-DESIGN §3.4, `data.png`) at `/p/$project/data` and `/p/$project/data/$table`.
 * With no table param it redirects to the first table, or shows `tables-empty`. Search params:
 * `filter` (engine filter JSON) and `sort` (default `-created_at`). Data is shared by every
 * environment (D11), so the env switch does not apply here.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { Columns3, Info, Plus, Table2 } from 'lucide-react'
import type { DataSearch } from '@/app/routes/project'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useTables } from '@/features/project/hooks/useProjectData'
import { pluralize } from '@/features/project/lib/format'
import { errorMessage } from '@/shared/api/http'
import type { DataTable } from '@/shared/api/types'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { ColumnsDialog } from './components/ColumnsDialog'
import { FilterBar } from './components/FilterBar'
import { RowsGrid } from './components/RowsGrid'
import { TableMenu } from './components/TableMenu'
import { DEFAULT_SORT, buildFilter, nextSort, parseFilter, serializeFilter, type FilterModel } from './lib/filters'

export const SHARED_DATA_COPY =
  'One table for the whole project: dev and prod, tests and the published app all read and write these rows.'

const panel = 'flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-panel-border bg-card shadow-xs'

export function DataView() {
  const { pid, slug } = useProjectCtx()
  const params = useParams({ strict: false }) as { table?: string }
  const navigate = useNavigate()
  const tables = useTables(pid)
  const first = tables.data?.[0]?.name

  // `/data` → the first table
  useEffect(() => {
    if (!params.table && first) navigate({ to: '/p/$project/data/$table', params: { project: slug, table: first }, replace: true })
  }, [params.table, first, slug, navigate])

  if (tables.isPending || (!params.table && first))
    return (
      <div className={panel} data-testid="data-view">
        <div className="flex flex-col gap-3 px-5 pt-5 pb-4" data-testid="data-loading">
          <Skeleton className="h-5 w-56" />
          <Skeleton className="h-3.5 w-96" />
        </div>
        <div className="flex flex-col border-t border-border">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex h-[42px] items-center gap-6 border-b border-border/65 px-4">
              <Skeleton className="h-3 w-12" />
              <Skeleton className="h-3 w-48" />
              <div className="grow" />
              <Skeleton className="h-3 w-28" />
            </div>
          ))}
        </div>
      </div>
    )

  if (tables.isError)
    return (
      <div className={panel} data-testid="data-view">
        <EmptyState
          className="flex-1"
          title="Couldn't load tables"
          data-testid="tables-error"
          actions={
            <Button size="sm" variant="outline" onClick={() => tables.refetch()}>
              Retry
            </Button>
          }
        >
          {errorMessage(tables.error)}
        </EmptyState>
      </div>
    )

  const newTable = (
    <Button
      size="sm"
      variant="outline"
      render={<Link to="." search={((s: Record<string, unknown>) => ({ ...s, dialog: 'new-table' })) as never} />}
      data-testid="tables-empty-new"
    >
      <Plus /> New table
    </Button>
  )

  if (!tables.data.length)
    return (
      <div className={panel} data-testid="data-view">
        <EmptyState
          className="flex-1"
          icon={<Table2 />}
          title="No tables. Tables are created when you save generated code, or here."
          data-testid="tables-empty"
          actions={newTable}
        >
          {SHARED_DATA_COPY}
        </EmptyState>
      </div>
    )

  const table = tables.data.find((t) => t.name === params.table)
  if (!table)
    return (
      <div className={panel} data-testid="data-view">
        <EmptyState
          className="flex-1"
          icon={<Table2 />}
          title={`Table "${params.table}" doesn't exist.`}
          data-testid="table-not-found"
          actions={
            <>
              <Button size="sm" variant="outline" render={<Link to="/p/$project/data/$table" params={{ project: slug, table: first! }} />}>
                Open {first}
              </Button>
              {newTable}
            </>
          }
        />
      </div>
    )

  return <TableView key={table.id} table={table} />
}

function TableView({ table }: { table: DataTable }) {
  const search = useSearch({ strict: false }) as DataSearch
  const navigate = useNavigate()
  const model = useMemo(() => parseFilter(search.filter), [search.filter])
  const filter = useMemo(() => buildFilter(model), [model])
  const [adding, setAdding] = useState(false)
  const [columnsOpen, setColumnsOpen] = useState(false)

  const setSearch = (patch: Partial<DataSearch>) =>
    navigate({
      to: '.',
      search: ((s: Record<string, unknown>) => {
        const n: Record<string, unknown> = { ...s, ...patch }
        for (const k of Object.keys(n)) if (n[k] === undefined) delete n[k]
        return n
      }) as never,
      replace: true,
    })
  const setModel = (m: FilterModel) => setSearch({ filter: serializeFilter(m) })
  const setSort = (s: string) => setSearch({ sort: s === DEFAULT_SORT ? undefined : s })

  return (
    <div className={panel} data-testid="data-view" data-table={table.name}>
      <div className="px-5 pt-[18px] pb-3">
        <div className="flex min-w-0 items-start gap-2">
          <Table2 className="mt-[5px] size-3.5 shrink-0 text-muted-foreground" />
          <h1 className="shrink-0 font-mono text-[16px] leading-6 font-semibold tracking-[-0.01em]" data-testid="data-table-name">
            {table.name}
          </h1>
          <Badge variant="secondary" className="mt-[3px] shrink-0" data-testid="data-row-count">
            {pluralize(table.row_count ?? 0, 'row')}
          </Badge>
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 pt-px" data-testid="data-column-chips">
            {table.columns.map((c) => (
              <span
                key={c.name}
                className="inline-flex h-[22px] items-center gap-1.5 rounded-md border border-border px-2 text-[11.5px]"
                data-testid={`column-chip-${c.name}`}
              >
                <span className="font-mono">{c.name}</span>
                <span className="text-muted-foreground">
                  {c.type}
                  {c.required ? ' · required' : ''}
                </span>
              </span>
            ))}
          </div>
          <Button variant="ghost" size="sm" className="shrink-0" onClick={() => setColumnsOpen(true)} data-testid="data-columns">
            <Columns3 /> Columns
          </Button>
          <Button size="sm" className="shrink-0" onClick={() => setAdding(true)} disabled={adding} data-testid="data-add-row">
            <Plus /> Add row
          </Button>
          <TableMenu table={table} onColumns={() => setColumnsOpen(true)} />
        </div>
        <div className="mt-2.5 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <FilterBar columns={table.columns} model={model} onChange={setModel} sort={search.sort} onSort={setSort} />
          <div className="grow" />
          <span className="flex min-w-0 items-center gap-1.5 text-[11.5px] text-muted-foreground" data-testid="data-shared-note">
            <Info className="size-3 shrink-0" />
            <span>{SHARED_DATA_COPY}</span>
          </span>
        </div>
      </div>

      <RowsGrid
        table={table}
        filter={filter}
        filtered={!!filter}
        onClearFilter={() => setSearch({ filter: undefined })}
        sort={search.sort}
        onSort={(key) => setSort(nextSort(search.sort, key))}
        adding={adding}
        setAdding={setAdding}
      />

      <ColumnsDialog table={table} open={columnsOpen} onClose={() => setColumnsOpen(false)} />
    </div>
  )
}

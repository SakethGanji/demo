/**
 * Data view (UI-DESIGN §3.4, `data.png`) at `/p/$project/data` and `/p/$project/data/$table`.
 * With no table param it redirects to the first table, or shows `tables-empty`. Search params:
 * `filter` (engine filter JSON) and `sort` (default `-created_at`). Data is shared by every
 * environment (D11), so the env switch does not apply here.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { Plus, Table2 } from 'lucide-react'
import { toast } from 'sonner'
import type { DataSearch } from '@/app/routes/project'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useTables } from '@/features/project/hooks/useProjectData'
import { pluralize } from '@/features/project/lib/format'
import { errorMessage } from '@/shared/api/http'
import type { DataTable } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Roll } from '@/shared/components/ui/roll'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { PageHeader, Tile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { ColumnsDialog } from './components/ColumnsDialog'
import { ColumnsMenu } from './components/ColumnsMenu'
import { FilterBar } from './components/FilterBar'
import { ImportSheet } from './components/ImportSheet'
import { LineageArt, type LineageHandle } from './components/LineageArt'
import { RowsGrid } from './components/RowsGrid'
import { TableMenu } from './components/TableMenu'
import { isNumeric } from './lib/cells'
import { toCsv } from './lib/csv'
import { DEFAULT_SORT, buildFilter, nextSort, parseFilter, serializeFilter, type FilterModel } from './lib/filters'
import { useHiddenColumns } from './lib/hiddenColumns'
import { useTableLineage } from './lib/lineage'
import { useTableRows } from './useTableRows'

export const SHARED_DATA_COPY =
  'One table for the whole project: dev and prod, tests and the published app all read and write these rows.'

/** the page frame: transparent (the sheet shows the sky); boxes are Wells and Tiles */
const page = 'flex h-full min-h-0 flex-col overflow-y-auto'


/** An empty page state: a title row, then the message in a well over star dust. */
function PageState({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <div className={page} data-testid="data-view">
      <div className="px-8 pt-7 pb-5">
        <PageHeader title="Data" />
      </div>
      <Tile className="mx-2 mb-2 flex min-h-[260px] flex-1 flex-col rounded-well" data-testid={testId}>
        {children}
      </Tile>
    </div>
  )
}

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
      <div className={page} data-testid="data-view">
        <div className="flex flex-col gap-3 px-8 pt-7 pb-6" data-testid="data-loading">
          <Skeleton className="h-3 w-40" />
          <Skeleton className="h-7 w-56" />
          <Skeleton className="h-6 w-28" />
        </div>
        <Tile className="mx-2 mb-2 flex min-h-[260px] flex-1 flex-col rounded-well">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="hairline-b flex h-[38px] items-center gap-6 px-6">
              <Skeleton className="h-3 w-12" />
              <Skeleton className="h-3 w-48" />
              <div className="grow" />
              <Skeleton className="h-3 w-28" />
            </div>
          ))}
        </Tile>
      </div>
    )

  if (tables.isError)
    return (
      <PageState>
        <EmptyState
          dust
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
      </PageState>
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
      <PageState>
        <EmptyState
          dust
          className="flex-1"
          icon={<Table2 />}
          title="No tables. Tables are created when you save generated code, or here."
          data-testid="tables-empty"
          actions={newTable}
        >
          {SHARED_DATA_COPY}
        </EmptyState>
      </PageState>
    )

  const table = tables.data.find((t) => t.name === params.table)
  if (!table)
    return (
      <PageState>
        <EmptyState
          dust
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
      </PageState>
    )

  return <TableView key={table.id} table={table} />
}

const money = (n: number) => n.toLocaleString('en-US', { maximumFractionDigits: 2 })

function TableView({ table }: { table: DataTable }) {
  const { pid, slug, project } = useProjectCtx()
  const search = useSearch({ strict: false }) as DataSearch
  const navigate = useNavigate()
  const model = useMemo(() => parseFilter(search.filter), [search.filter])
  const filter = useMemo(() => buildFilter(model), [model])
  const [adding, setAdding] = useState(false)
  const [columnsOpen, setColumnsOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const lineage = useRef<LineageHandle>(null)
  const { rows, list, count } = useTableRows(pid, table, filter, search.sort)
  const links = useTableLineage(pid, table.name)
  const colKeys = useMemo(() => ['id', ...table.columns.map((c) => c.name), 'created_at'], [table.columns])
  const { hidden, toggle } = useHiddenColumns(table.id, colKeys)

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

  // one packet down the write wire per real write
  const onWrote = useCallback(() => lineage.current?.pulseWrite(), [])
  const openImport = useCallback((f?: File) => {
    setImportFile(f ?? null)
    setImportOpen(true)
  }, [])

  // ⌘↵ adds a row
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey) || e.altKey) return
      if (document.querySelector('[role="dialog"]')) return
      e.preventDefault()
      setAdding(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const exportCsv = () => {
    const names = [...(hidden.includes('id') ? [] : ['id']), ...table.columns.filter((c) => !hidden.includes(c.name)).map((c) => c.name), ...(hidden.includes('created_at') ? [] : ['created_at'])]
    const body = list.map((r) => ({ id: r.id, created_at: r.created_at, ...r.data }))
    const url = URL.createObjectURL(new Blob([toCsv(names, body)], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${table.name}.csv`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(`Exported ${table.name}.csv`, { description: `${pluralize(list.length, 'row')} · ${names.join(', ')}` })
  }

  // hero total: the first numeric column, over the rows loaded so far (the API pages by 50)
  const numeric = table.columns.find((c) => isNumeric(c.type))
  const total = useMemo(() => (numeric ? list.reduce((s, r) => s + (Number(r.data[numeric.name]) || 0), 0) : 0), [list, numeric])
  const partial = list.length < count
  const rowCount = table.row_count ?? count
  const filtered = !!filter

  return (
    <div className={page} data-testid="data-view" data-table={table.name}>
      <header className="grid grid-cols-[minmax(0,1fr)_360px] items-center gap-10 px-8 pt-7 pb-6 max-[1100px]:grid-cols-1" data-testid="data-header">
        <div className="flex min-w-0 flex-col">
          <div className="flex items-center gap-2 text-[12px] font-medium text-muted-foreground">
            <span className="grid size-[22px] place-items-center rounded-chip bg-brand-soft text-primary [&_svg]:size-[13px]">
              <Table2 />
            </span>
            <span data-testid="data-project-name">{project.name}</span>
            <span className="text-faint">/</span>
            <Link to="/p/$project/data" params={{ project: slug }} className="rounded-xs outline-none hover:text-foreground focus-visible:shadow-focus" data-testid="data-crumb">
              Tables
            </Link>
          </div>
          <PageHeader
            className="mt-3"
            title={
              <span className="inline-flex items-center gap-3">
                <span className="font-mono font-semibold tracking-[-0.04em]" data-testid="data-table-name">
                  {table.name}
                </span>
                <span className="inline-flex h-[22px] items-center rounded-full bg-recess px-2 text-[12px] font-normal tracking-normal text-muted-foreground" data-testid="data-row-count">
                  <Roll value={rowCount} />
                  &nbsp;{rowCount === 1 ? 'row' : 'rows'}
                </span>
              </span>
            }
          >
            {numeric && (
              <div className="mt-[13px] flex items-baseline gap-2" data-testid="data-total">
                <Roll value={total} format={money} className="text-[28px] leading-[1.1] font-semibold tracking-[-0.03em]" data-testid="data-total-value" />
                <span className="text-[12px] text-muted-foreground" data-testid="data-total-label">
                  total {numeric.name}
                  {filtered ? ' · filtered' : ''}
                  {partial ? ` · ${list.length} of ${count} loaded rows` : ''}
                </span>
              </div>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-2" data-testid="data-column-chips">
              {table.columns.map((c) => (
                <Tip key={c.name} label={`Column ${c.name}: ${c.type}${c.required ? ', required on every row' : ''}`}>
                  <span
                    className="inline-flex h-[22px] items-center gap-1.5 rounded-chip bg-recess px-2 font-mono text-[11.5px] whitespace-nowrap text-muted-foreground"
                    data-testid={`column-chip-${c.name}`}
                  >
                    <b className="font-semibold text-foreground">{c.name}</b>
                    {c.type}
                    {c.required && (
                      <>
                        <i className="text-faint not-italic">·</i>required
                      </>
                    )}
                  </span>
                </Tip>
              ))}
            </div>
          </PageHeader>
        </div>
        <div className="max-[1100px]:hidden">
          <LineageArt ref={lineage} table={table.name} writers={links.writers} readers={links.readers} apps={links.apps} columns={table.columns.length} />
        </div>
      </header>

      <RowsGrid
        table={table}
        rows={rows}
        list={list}
        count={count}
        filtered={filtered}
        onClearFilter={() => setSearch({ filter: undefined })}
        sort={search.sort}
        onSort={(key) => setSort(nextSort(search.sort, key))}
        adding={adding}
        setAdding={setAdding}
        hidden={hidden}
        onWrote={onWrote}
        onImportFile={openImport}
        onImport={() => openImport()}
        toolbarLeft={<FilterBar columns={table.columns} model={model} onChange={setModel} sort={search.sort} onSort={setSort} />}
        toolbarRight={
          <>
            <ColumnsMenu
              options={[{ name: 'id' }, ...table.columns.map((c) => ({ name: c.name })), { name: 'created_at', sub: 'row metadata' }]}
              hidden={hidden}
              onToggle={toggle}
            />
            <Tip label="Add a row at the top" kbd="⌘↵">
              <Button size="sm" onClick={() => setAdding(true)} disabled={adding} data-testid="data-add-row">
                <Plus /> Add row
              </Button>
            </Tip>
            <TableMenu table={table} onColumns={() => setColumnsOpen(true)} onImport={() => openImport()} onExport={exportCsv} />
          </>
        }
      />

      <ColumnsDialog table={table} open={columnsOpen} onClose={() => setColumnsOpen(false)} />
      <ImportSheet table={table} open={importOpen} file={importFile} onClose={() => setImportOpen(false)} onWrote={onWrote} />
    </div>
  )
}

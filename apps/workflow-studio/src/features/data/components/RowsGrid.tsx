/**
 * Rows grid (DESIGN §8 "Table tools"): the toolbar (filters and sort, cross-fading into the selection bar),
 * the table in a well (sort heads, a column-profile row, hairline rows, inline edit with `PATCH …/rows/{id}`
 * optimistic and rolled back on error, `422 row_invalid` shown at the cell), the end-of-table line with star
 * dust below, and the footer. Add row opens an inline new row whose field errors come from
 * `detail.errors[{row,column,error}]`. Infinite scroll, pages of 50 with `count=true`.
 * Deleting is immediate with an Undo toast (re-inserts the rows; ids differ). Dropping a CSV on the well
 * hands the file to `onImportFile`.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { UseInfiniteQueryResult, InfiniteData } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Copy, Ellipsis, FileUp, Hash, ListFilter, Pencil, Plus, Rows3, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { pluralize, timeAgo } from '@/features/project/lib/format'
import { errorMessage, isApiError } from '@/shared/api/http'
import type { Column, DataTable, Row, RowPage } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Input } from '@/shared/components/ui/input'
import { Kbd } from '@/shared/components/ui/kbd'
import { Roll } from '@/shared/components/ui/roll'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { Tip } from '@/shared/components/ui/tooltip'
import { Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { displayValue, editText, formatStamp, isNumeric, parseEdit, rowErrors } from '../lib/cells'
import { parseSort } from '../lib/filters'
import { profileColumn, profileCreated, profileIds } from '../lib/profile'
import { useDeleteRows, useInsertRow, usePatchRow } from '../useTableMutations'
import { ColumnProfile } from './ColumnProfile'

interface EditState {
  rowId: number
  column: string
  text: string
  error?: string
}

type RowsQuery = UseInfiniteQueryResult<InfiniteData<RowPage, unknown>, Error>

// the end-of-table dotted rule fades toward the page edge
const DOT_RULE = 'h-px flex-1 bg-[radial-gradient(circle,var(--art-line)_.75px,transparent_1px)] bg-size-[6px_1px] bg-center bg-repeat-x' // design-ok: dotted rule texture
const DOT_RULE_L = `${DOT_RULE} [mask-image:linear-gradient(90deg,transparent,#000_40%)]` // design-ok: fade mask
const DOT_RULE_R = `${DOT_RULE} [mask-image:linear-gradient(270deg,transparent,#000_40%)]` // design-ok: fade mask

const hasFiles = (e: DragEvent) => [...(e.dataTransfer?.types ?? [])].includes('Files')

export function RowsGrid({
  table,
  rows,
  list,
  count,
  filtered,
  onClearFilter,
  sort,
  onSort,
  adding,
  setAdding,
  hidden,
  toolbarLeft,
  toolbarRight,
  onWrote,
  onImportFile,
  onImport,
}: {
  table: DataTable
  rows: RowsQuery
  list: Row[]
  count: number
  filtered: boolean
  onClearFilter: () => void
  sort: string | undefined
  onSort: (key: string) => void
  adding: boolean
  setAdding: (v: boolean) => void
  /** column names hidden by the Columns menu (`id` and `created_at` included) */
  hidden: string[]
  toolbarLeft: React.ReactNode
  toolbarRight: React.ReactNode
  /** a row was written (inserted, edited or deleted): the lineage fires a packet */
  onWrote: () => void
  onImportFile: (f: File) => void
  onImport: () => void
}) {
  const { pid } = useProjectCtx()
  const columns = table.columns
  const shown = columns.filter((c) => !hidden.includes(c.name))
  const showId = !hidden.includes('id')
  const showCreated = !hidden.includes('created_at')

  const patch = usePatchRow(pid, table.id)
  const insert = useInsertRow(pid, table.id)
  const del = useDeleteRows(pid, table.id)
  const [edit, setEdit] = useState<EditState | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  // only rows that are still listed stay selected
  const selectedIds = useMemo(() => list.filter((r) => selected.has(r.id)).map((r) => r.id), [list, selected])

  // new rows open with a flash (not the first page)
  const seen = useRef<Set<number> | null>(null)
  const [fresh, setFresh] = useState<Set<number>>(new Set())
  useEffect(() => {
    if (!rows.isSuccess) return
    const ids = new Set(list.map((r) => r.id))
    if (seen.current) {
      const added = list.filter((r) => !seen.current!.has(r.id)).map((r) => r.id)
      if (added.length && added.length <= 20) {
        setFresh((f) => new Set([...f, ...added]))
        setTimeout(() => setFresh((f) => new Set([...f].filter((x) => !added.includes(x)))), 600)
      }
    }
    seen.current = ids
  }, [list, rows.isSuccess])

  // infinite scroll
  const scrollRef = useRef<HTMLDivElement>(null)
  // the header frosts once rows scroll under it
  const [stuck, setStuck] = useState(false)
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver(
      (es) => {
        if (es.some((e) => e.isIntersecting) && rows.hasNextPage && !rows.isFetchingNextPage) void rows.fetchNextPage()
      },
      { root: scrollRef.current, rootMargin: '200px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [rows, list.length])

  // a file dragged over the page turns the well into one drop target
  const well = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<'off' | 'on' | 'over'>('off')
  useEffect(() => {
    let depth = 0
    const end = () => {
      depth = 0
      setDrag('off')
    }
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return
      depth++
      setDrag((d) => (d === 'off' ? 'on' : d))
    }
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return
      if (--depth <= 0) end()
    }
    const over = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      const on = !!well.current?.contains(e.target as Node)
      setDrag(on ? 'over' : 'on')
      if (e.dataTransfer) e.dataTransfer.dropEffect = on ? 'copy' : 'none'
    }
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      const on = !!well.current?.contains(e.target as Node)
      end()
      const f = e.dataTransfer?.files[0]
      if (on && f) onImportFile(f)
    }
    document.addEventListener('dragenter', enter)
    document.addEventListener('dragleave', leave)
    document.addEventListener('dragover', over)
    document.addEventListener('drop', drop)
    return () => {
      document.removeEventListener('dragenter', enter)
      document.removeEventListener('dragleave', leave)
      document.removeEventListener('dragover', over)
      document.removeEventListener('drop', drop)
    }
  }, [onImportFile])

  const commit = (e: EditState) => {
    const row = list.find((r) => r.id === e.rowId)
    const col = columns.find((c) => c.name === e.column)
    if (!row || !col) return setEdit(null)
    if (e.text === editText(row.data[col.name], col.type)) return setEdit(null)
    const parsed = parseEdit(e.text, col.type)
    if ('error' in parsed) return setEdit({ ...e, error: parsed.error })
    setEdit(null)
    patch.mutate(
      { id: row.id, data: { [col.name]: parsed.value } },
      {
        onSuccess: onWrote,
        onError: (err) => {
          const errs = rowErrors(err)
          const msg = errs ? errs.find((x) => x.column === col.name)?.error ?? errs[0]?.error : errorMessage(err)
          setEdit({ ...e, error: msg ?? errorMessage(err) })
        },
      },
    )
  }

  /** delete now; the toast's Undo inserts the same data again (new ids) */
  const remove = (ids: number[]) => {
    const snap = list.filter((r) => ids.includes(r.id))
    if (!snap.length) return
    del.mutate(ids, {
      onSuccess: () => {
        setSelected((s) => new Set([...s].filter((x) => !ids.includes(x))))
        onWrote()
        toast.success(snap.length === 1 ? `Deleted row ${snap[0].id}` : `Deleted ${snap.length} rows`, {
          description: `From ${table.name}`,
          action: {
            label: 'Undo',
            onClick: async () => {
              let n = 0
              for (const r of snap) {
                try {
                  await insert.mutateAsync(r.data)
                  n++
                  onWrote()
                } catch (e) {
                  toast.error(errorMessage(e))
                  break
                }
              }
              if (n) toast.success(n === 1 ? 'Restored 1 row' : `Restored ${n} rows`, { duration: 2400 })
            },
          },
        })
      },
      onError: (e) => toast.error(errorMessage(e)),
    })
  }

  const duplicate = (r: Row) =>
    insert.mutate(r.data, {
      onSuccess: (n) => {
        onWrote()
        toast.success(`Duplicated row ${r.id}`, { description: `Added as row ${n.id} at the top.` })
      },
      onError: (e) => toast.error(errorMessage(e)),
    })

  const { key: sortCol, desc } = parseSort(sort)
  const allSelected = list.length > 0 && selectedIds.length === list.length
  const showGrid = list.length > 0 || adding
  const sel = selectedIds.length

  const profiles = useMemo(
    () => ({
      id: profileIds(list),
      created: profileCreated(list),
      cols: Object.fromEntries(columns.map((c) => [c.name, profileColumn(c, list)])),
    }),
    [list, columns],
  )

  const header = (
    <TableHeader className={cn('sticky top-0 z-[1] transition-[backdrop-filter,background-color] duration-(--d-fast)', stuck && 'bg-(--glass-fill-hover) backdrop-blur-xl')}>
      <TableRow className="border-b-0 hover:bg-transparent">
        <TableHead className="h-9 w-10 pr-0 pl-6">
          <Checkbox
            className="size-3.5"
            checked={allSelected}
            onCheckedChange={(c) => setSelected(c ? new Set(list.map((r) => r.id)) : new Set())}
            aria-label="Select all rows"
            disabled={list.length === 0}
            data-testid="rows-select-all"
          />
        </TableHead>
        {showId && <SortHead label="id" col="id" active={sortCol} desc={desc} onSort={onSort} className="w-[141px]" />}
        {shown.map((c) => (
          <SortHead
            key={c.name}
            label={c.name}
            col={c.name}
            active={sortCol}
            desc={desc}
            onSort={onSort}
            className={cn(isNumeric(c.type) ? 'w-[292px] text-right' : shown.indexOf(c) === 0 && 'w-[307px]')}
            right={isNumeric(c.type)}
          />
        ))}
        {showCreated && (
          <SortHead
            label={
              <>
                created <span className="font-normal text-muted-foreground">· row metadata</span>
              </>
            }
            col="created_at"
            active={sortCol}
            desc={desc}
            onSort={onSort}
          />
        )}
        <TableHead className="h-9 w-[52px] pr-4" />
      </TableRow>
      <TableRow className="hover:bg-transparent" data-testid="profile-row">
        <TableHead className="h-9 w-10 pr-0 pl-6" />
        {showId && (
          <TableHead className="h-9 px-3 py-0 text-[11px] font-normal">
            <ColumnProfile profile={profiles.id} testId="profile-id" />
          </TableHead>
        )}
        {shown.map((c) => (
          <TableHead key={c.name} className="h-9 px-3 py-0 text-[11px] font-normal">
            <ColumnProfile profile={profiles.cols[c.name]} right={isNumeric(c.type)} testId={`profile-${c.name}`} />
          </TableHead>
        ))}
        {showCreated && (
          <TableHead className="h-9 px-3 py-0 text-[11px] font-normal">
            <ColumnProfile profile={profiles.created} testId="profile-created_at" />
          </TableHead>
        )}
        <TableHead className="h-9 w-[52px] pr-4" />
      </TableRow>
    </TableHeader>
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-6 pb-2 text-xs text-muted-foreground min-h-10" data-testid="data-toolbar" data-selecting={sel > 0 || undefined}>
        <div className="relative flex min-h-7 min-w-0 flex-1 items-center">
          <div
            className={cn(
              'flex min-w-0 flex-1 items-center transition-[opacity,transform] duration-(--d-fast) ease-(--ease-out)',
              sel > 0 && 'pointer-events-none opacity-0',
            )}
            {...(sel > 0 ? { inert: '' } : {})}
          >
            {toolbarLeft}
          </div>
          <div
            className={cn(
              'absolute inset-0 flex items-center gap-2 text-foreground transition-[opacity,transform] duration-(--d-fast) ease-(--ease-out)',
              sel > 0 ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-0.5 opacity-0',
            )}
            aria-live="polite"
            data-testid="rows-selection-bar"
            {...(sel > 0 ? {} : { inert: '' })}
          >
            <span className="pl-2 font-medium" data-testid="rows-selected-count">
              {sel} selected
            </span>
            <span className="text-faint">·</span>
            <Button variant="ghost" size="xs" className="text-destructive hover:bg-destructive-soft hover:text-destructive" onClick={() => remove(selectedIds)} data-testid="rows-delete-selected">
              <Trash2 /> Delete
            </Button>
            <div className="flex-1" />
            <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={() => setSelected(new Set())} data-testid="rows-clear-selection">
              Clear selection
            </Button>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">{toolbarRight}</div>
      </div>

      <Tile ref={well} className="mx-2 flex min-h-[260px] flex-1 flex-col overflow-hidden rounded-well" data-testid="data-well" data-drag={drag}>
        <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col overflow-auto" onScroll={(e) => setStuck(e.currentTarget.scrollTop > 2)} data-testid="data-panel" data-scroll-x="allowed">
          {rows.isPending ? (
            <RowsSkeleton />
          ) : rows.isError ? (
            <EmptyState
              title="Couldn't load rows"
              data-testid="rows-error"
              actions={
                <>
                  {filtered && isApiError(rows.error, undefined, 422) && (
                    <Button size="sm" variant="ghost" onClick={onClearFilter}>
                      Clear filter
                    </Button>
                  )}
                  <Button size="sm" variant="outline" onClick={() => rows.refetch()} data-testid="rows-retry">
                    Retry
                  </Button>
                </>
              }
            >
              {errorMessage(rows.error)}
            </EmptyState>
          ) : !showGrid ? (
            filtered ? (
              <EmptyState
                dust
                className="flex-1"
                icon={<ListFilter />}
                title="No rows match this filter."
                data-testid="data-empty-filtered"
                actions={
                  <Button size="sm" variant="outline" onClick={onClearFilter}>
                    Clear filter
                  </Button>
                }
              />
            ) : (
              <EmptyState
                dust
                className="flex-1"
                icon={<Rows3 />}
                title={
                  <>
                    No rows yet. Your routes write here:{' '}
                    <span className="font-mono font-normal">studio.db.table("{table.name}").insert(...)</span>
                  </>
                }
                data-testid="data-empty"
                actions={
                  <>
                    <Button size="sm" variant="outline" onClick={() => setAdding(true)} data-testid="data-empty-add-row">
                      <Plus /> Add row
                    </Button>
                    <Button size="sm" variant="ghost" onClick={onImport} data-testid="data-empty-import">
                      <FileUp /> Import CSV
                    </Button>
                  </>
                }
              />
            )
          ) : (
            <>
              <Table className="shrink-0">
                {header}
                <TableBody>
                  {adding && <NewRow table={table} columns={shown} showId={showId} showCreated={showCreated} onDone={() => setAdding(false)} onWrote={onWrote} />}
                  {list.map((r) => (
                    <DataRow
                      key={r.id}
                      row={r}
                      columns={shown}
                      showId={showId}
                      showCreated={showCreated}
                      edit={edit?.rowId === r.id ? edit : null}
                      setEdit={setEdit}
                      commit={commit}
                      fresh={fresh.has(r.id)}
                      selected={selected.has(r.id)}
                      onSelect={(c) =>
                        setSelected((s) => {
                          const n = new Set(s)
                          if (c) n.add(r.id)
                          else n.delete(r.id)
                          return n
                        })
                      }
                      onDelete={() => remove([r.id])}
                      onDuplicate={() => duplicate(r)}
                    />
                  ))}
                </TableBody>
              </Table>
              <div className="flex items-center gap-3 px-6 pt-5 pb-4 text-[12px] text-muted-foreground" data-testid="data-end">
                <span className={DOT_RULE_L} />
                <span className="inline-flex items-center gap-2 whitespace-nowrap" data-testid="rows-count">
                  <span className="size-[5px] rounded-full shadow-[inset_0_0_0_1px_var(--art-line)]" />
                  {rows.isSuccess ? (
                    <span className="inline-flex">
                      <Roll value={filtered ? count : count} />
                      &nbsp;
                      {filtered
                        ? `${count === 1 ? 'row matches' : 'rows match'} · ${pluralize(table.row_count ?? count, 'row')} total`
                        : rows.hasNextPage
                          ? `${count === 1 ? 'row' : 'rows'} · ${list.length} loaded`
                          : `${count === 1 ? 'row' : 'rows'} · that's everything`}
                    </span>
                  ) : (
                    '…'
                  )}
                </span>
                <Tip label="Add a row at the top" kbd="⌘↵">
                  <Button
                    variant="ghost"
                    size="xs"
                    className="text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      scrollRef.current?.scrollTo({ top: 0 })
                      setAdding(true)
                    }}
                    disabled={adding}
                    data-testid="data-add-row-end"
                  >
                    <Plus /> Add row
                  </Button>
                </Tip>
                <span className={DOT_RULE_R} />
              </div>
              <div className="min-h-24 flex-1" aria-hidden data-testid="data-dust" />
            </>
          )}
          <div ref={sentinel} className="h-px shrink-0" />
          {rows.isFetchingNextPage && (
            <div className="flex justify-center py-3" data-testid="rows-loading-more">
              <Spinner className="size-4" />
            </div>
          )}
        </div>

        <div
          className={cn(
            'pointer-events-none absolute inset-0 z-10 rounded-well bg-(--well) p-2 opacity-0 backdrop-blur-md transition-opacity duration-(--d-fast) ease-(--ease-out)',
            drag !== 'off' && 'opacity-100',
          )}
          aria-hidden
          data-testid="data-dropover"
        >
          <div className={cn('flex h-full flex-col items-center justify-center gap-1.5 rounded-well bg-recess text-center transition-colors duration-(--d-fast)', drag === 'over' && 'bg-brand-soft shadow-[inset_0_0_0_1.5px_var(--ring)]')}>
            <span className={cn('mb-1 grid size-8 place-items-center rounded-ctl bg-raise text-muted-foreground shadow-raise-sm [&_svg]:size-[15px]', drag === 'over' && 'text-primary')}>
              <FileUp />
            </span>
            <span className="text-[13px] font-medium text-foreground">
              Drop a CSV to import into <span className="font-mono text-[12px]">{table.name}</span>
            </span>
            <span className="text-[12px] text-muted-foreground">
              Columns are matched by name: {columns.slice(0, 3).map((c) => c.name).join(', ')}
            </span>
          </div>
        </div>
      </Tile>

      <div className="mx-8 mt-1 mb-2 flex h-6 items-center gap-1.5 text-[12px] text-muted-foreground" data-testid="data-footer">
        {edit ? (
          <span>
            Editing row <span className="font-mono">{edit.rowId}</span> · {edit.column} · <Kbd>⏎</Kbd> save · <Kbd>Esc</Kbd> cancel
          </span>
        ) : (
          <span>{list.length > 0 ? 'Double-click a cell to edit' : ''}</span>
        )}
        <div className="grow" />
        <Tip label="Read and write these rows from any route">
          <span className="font-mono text-[11.5px]" data-testid="data-snippet">
            studio.db.table("{table.name}")
          </span>
        </Tip>
      </div>
    </div>
  )
}

function SortHead({
  label,
  col,
  active,
  desc,
  onSort,
  className,
  right,
}: {
  label: React.ReactNode
  col: string
  active: string
  desc: boolean
  onSort: (key: string) => void
  className?: string
  right?: boolean
}) {
  const on = active === col
  const Icon = desc ? ArrowDown : ArrowUp
  return (
    <TableHead className={cn('h-9 px-3', className)} aria-sort={on ? (desc ? 'descending' : 'ascending') : undefined}>
      <button
        type="button"
        className={cn('inline-flex items-center gap-1 outline-none hover:text-foreground focus-visible:text-foreground', right && 'flex-row-reverse', on && 'text-foreground')}
        onClick={() => onSort(col)}
        data-testid={`sort-${col}`}
      >
        <span>{label}</span>
        {on && <Icon className="size-3" />}
      </button>
    </TableHead>
  )
}

function DataRow({
  row,
  columns,
  showId,
  showCreated,
  edit,
  setEdit,
  commit,
  selected,
  fresh,
  onSelect,
  onDelete,
  onDuplicate,
}: {
  row: Row
  columns: Column[]
  showId: boolean
  showCreated: boolean
  edit: EditState | null
  setEdit: (e: EditState | null) => void
  commit: (e: EditState) => void
  selected: boolean
  fresh: boolean
  onSelect: (c: boolean) => void
  onDelete: () => void
  onDuplicate: () => void
}) {
  const first = columns[0]
  return (
    <TableRow data-testid="data-row" data-row-id={row.id} data-state={selected ? 'selected' : undefined} className={cn('group/row', fresh && 'animate-row-in')}>
      <TableCell className="h-[38px] w-10 pr-0 pl-6">
        <Checkbox className="size-3.5" checked={selected} onCheckedChange={(c) => onSelect(!!c)} aria-label={`Select row ${row.id}`} />
      </TableCell>
      {showId && (
        <TableCell className="h-[38px] px-3 font-mono text-muted-foreground" data-testid="cell-id">
          {row.id}
        </TableCell>
      )}
      {columns.map((c) => {
        const editing = edit?.column === c.name
        const v = row.data[c.name]
        return (
          <TableCell
            key={c.name}
            className={cn('h-[38px] max-w-[360px] cursor-default px-3', isNumeric(c.type) && 'text-right tabular-nums', editing && 'py-1')}
            data-testid={`cell-${c.name}`}
            data-row-id={row.id}
            onDoubleClick={() => !editing && setEdit({ rowId: row.id, column: c.name, text: editText(v, c.type) })}
            title={c.type === 'json' && v != null ? JSON.stringify(v, null, 2) : undefined}
          >
            {editing ? (
              <CellEditor edit={edit!} column={c} setEdit={setEdit} commit={commit} />
            ) : v === null || v === undefined ? (
              <span className="text-faint">—</span>
            ) : (
              <span className={cn('block truncate', (c.type === 'json' || c.type === 'boolean') && 'font-mono text-[12px]', c.type === 'json' && 'text-muted-foreground')}>
                {displayValue(v, c.type)}
              </span>
            )}
          </TableCell>
        )
      })}
      {showCreated && (
        <TableCell className="h-[38px] px-3 text-muted-foreground" data-ts={row.created_at}>
          <Tip label={formatStamp(row.created_at, true)}>
            <span data-testid="cell-created">{timeAgo(row.created_at)}</span>
          </Tip>
        </TableCell>
      )}
      <TableCell className="h-[38px] w-[52px] pr-4 pl-0 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                className="h-8 text-faint opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100 focus-visible:opacity-100 aria-expanded:opacity-100 data-[popup-open]:opacity-100"
                aria-label={`Row ${row.id} actions`}
                data-testid="row-menu"
              >
                <Ellipsis />
              </Button>
            }
          />
          <DropdownMenuContent align="end" className="min-w-52">
            {first && (
              <DropdownMenuItem
                onClick={() => setEdit({ rowId: row.id, column: first.name, text: editText(row.data[first.name], first.type) })}
                data-testid="row-edit"
              >
                <Pencil /> Edit
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">{first.name}</span>
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onClick={onDuplicate} data-testid="row-duplicate">
              <Copy /> Duplicate
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                navigator.clipboard?.writeText(String(row.id)).catch(() => undefined)
                toast.success(`Copied id ${row.id}`, { duration: 2400 })
              }}
              data-testid="row-copy-id"
            >
              <Hash /> Copy id
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onDelete} data-testid="row-delete">
              <Trash2 /> Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  )
}

function CellEditor({
  edit,
  column,
  setEdit,
  commit,
}: {
  edit: EditState
  column: Column
  setEdit: (e: EditState | null) => void
  commit: (e: EditState) => void
}) {
  // Enter/Esc settle the edit; the blur that follows the unmount must not commit again
  const settled = useRef(false)
  return (
    <div className={cn('flex flex-col gap-0.5', isNumeric(column.type) && 'items-end')}>
      <Input
        autoFocus
        value={edit.text}
        onChange={(e) => setEdit({ ...edit, text: e.target.value, error: undefined })}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            settled.current = true
            commit(edit)
          } else if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            settled.current = true
            setEdit(null)
          }
        }}
        onFocus={() => {
          settled.current = false
        }}
        onBlur={() => {
          if (!settled.current) commit(edit)
        }}
        placeholder={column.type === 'boolean' ? 'true / false' : column.type}
        className={cn('h-7 min-w-[120px] bg-raise text-[13px] shadow-[inset_0_0_0_1.5px_var(--ring)]', isNumeric(column.type) && 'w-[130px] text-right', column.type === 'json' && 'font-mono text-xs')}
        aria-invalid={!!edit.error}
        data-testid="cell-editor"
      />
      {edit.error && (
        <span className="text-[11.5px] whitespace-normal text-destructive" role="alert" data-testid="row-invalid">
          {column.name}: {edit.error}
        </span>
      )}
    </div>
  )
}

function NewRow({
  table,
  columns,
  showId,
  showCreated,
  onDone,
  onWrote,
}: {
  table: DataTable
  columns: Column[]
  showId: boolean
  showCreated: boolean
  onDone: () => void
  onWrote: () => void
}) {
  const { pid } = useProjectCtx()
  const insert = useInsertRow(pid, table.id)
  const [values, setValues] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [general, setGeneral] = useState<string | null>(null)

  const save = () => {
    setErrors({})
    setGeneral(null)
    const data: Record<string, unknown> = {}
    const local: Record<string, string> = {}
    for (const c of table.columns) {
      const text = values[c.name] ?? ''
      if (text.trim() === '') continue // omitted → the engine applies defaults / `is required`
      const p = parseEdit(text, c.type)
      if ('error' in p) local[c.name] = p.error
      else data[c.name] = p.value
    }
    if (Object.keys(local).length) return setErrors(local)
    insert.mutate(data, {
      onSuccess: () => {
        setValues({})
        onWrote()
        onDone()
      },
      onError: (e) => {
        const errs = rowErrors(e)
        if (!errs) return setGeneral(errorMessage(e))
        const byCol: Record<string, string> = {}
        const rest: string[] = []
        for (const x of errs) {
          if (x.column && table.columns.some((c) => c.name === x.column)) byCol[x.column] = x.error
          else rest.push(x.column ? `${x.column}: ${x.error}` : x.error)
        }
        setErrors(byCol)
        if (rest.length) setGeneral(rest.join(' · '))
      },
    })
  }

  const messages = [...Object.entries(errors).map(([k, v]) => `${k}: ${v}`), ...(general ? [general] : [])]
  const msg =
    messages.length > 0 ? (
      <span className="text-[12px] text-destructive" role="alert" data-testid="row-invalid">
        {messages.join(' · ')}
      </span>
    ) : null

  return (
    <TableRow
      className="bg-brand-soft hover:bg-brand-soft"
      data-testid="new-row"
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          save()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          onDone()
        }
      }}
    >
      <TableCell className="h-[38px] w-10 pr-0 pl-6" />
      {showId && <TableCell className="h-[38px] px-3 font-mono text-muted-foreground">new</TableCell>}
      {columns.map((c, i) => (
        <TableCell key={c.name} className={cn('h-[38px] px-3 py-1', isNumeric(c.type) && 'text-right')}>
          <Input
            autoFocus={i === 0}
            value={values[c.name] ?? ''}
            onChange={(e) => {
              setValues((v) => ({ ...v, [c.name]: e.target.value }))
              setErrors((errs) => {
                const next = { ...errs }
                delete next[c.name]
                return next
              })
            }}
            placeholder={c.name}
            className={cn('h-7 min-w-[110px] bg-raise text-[13px]', isNumeric(c.type) ? 'ml-auto w-[130px] text-right' : 'max-w-[240px]', c.type === 'json' && 'font-mono text-xs')}
            aria-invalid={!!errors[c.name]}
            data-testid={`new-row-${c.name}`}
          />
        </TableCell>
      ))}
      {showCreated && (
        <TableCell className="h-[38px] px-3 py-1 whitespace-normal">{msg}</TableCell>
      )}
      <TableCell className="h-[38px] w-[52px] pr-4 pl-0 py-1 text-right">
        {!showCreated && msg}
        <Button size="xs" onClick={save} disabled={insert.isPending} data-testid="new-row-save">
          Save
        </Button>
      </TableCell>
    </TableRow>
  )
}

function RowsSkeleton() {
  return (
    <div className="flex flex-col" data-testid="data-loading">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="hairline-b flex h-[38px] items-center gap-6 px-6">
          <Skeleton className="size-3.5 rounded-chip" />
          <Skeleton className="h-3 w-12" />
          <Skeleton className="h-3 w-48" />
          <div className="grow" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-28" />
        </div>
      ))}
    </div>
  )
}

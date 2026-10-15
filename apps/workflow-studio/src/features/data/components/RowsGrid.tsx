/**
 * Rows grid (UI-DESIGN §3.4, `data.png`): sticky header (click = sort), mono id, right-aligned
 * tabular numbers, collapsed JSON. Double-click a cell → `PATCH …/rows/{id}` (optimistic, rolled
 * back on error, `422 row_invalid` shown at the cell). `Add row` opens an inline new row whose
 * field errors come from `detail.errors[{row,column,error}]`. Infinite scroll, pages of 50 with
 * `count=true`. The footer shows the count, the edit hint and the `studio.db.table("…")` snippet.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Ellipsis, ListFilter, Rows3 } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { pluralize } from '@/features/project/lib/format'
import { tablesApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import type { Column, DataTable, Row, RowFilter } from '@/shared/api/types'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Input } from '@/shared/components/ui/input'
import { Kbd } from '@/shared/components/ui/kbd'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { cn } from '@/shared/lib/utils'
import { displayValue, editText, formatStamp, isNumeric, parseEdit, rowErrors } from '../lib/cells'
import { parseSort } from '../lib/filters'
import { useDeleteRows, useInsertRow, usePatchRow } from '../useTableMutations'

interface EditState {
  rowId: number
  column: string
  text: string
  error?: string
}

export function RowsGrid({
  table,
  filter,
  filtered,
  onClearFilter,
  sort,
  onSort,
  adding,
  setAdding,
}: {
  table: DataTable
  filter: RowFilter | null
  filtered: boolean
  onClearFilter: () => void
  sort: string | undefined
  onSort: (key: string) => void
  adding: boolean
  setAdding: (v: boolean) => void
}) {
  const { pid } = useProjectCtx()
  const columns = table.columns
  const filterKey = filter ? JSON.stringify(filter) : null
  const sortKey = sort || '-created_at'
  const rows = useInfiniteQuery({
    queryKey: qk.rows(pid, table.id, { filter: filterKey, sort: sortKey }),
    queryFn: ({ pageParam }) =>
      tablesApi.rows(pid, table.id, { limit: 50, cursor: pageParam, filter, sort: sortKey, count: true }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const list = useMemo(() => {
    const seen = new Set<number>()
    return (rows.data?.pages ?? []).flatMap((p) => p.rows).filter((r) => (seen.has(r.id) ? false : (seen.add(r.id), true)))
  }, [rows.data])
  const count = rows.data?.pages[0]?.count ?? list.length

  const patch = usePatchRow(pid, table.id)
  const [edit, setEdit] = useState<EditState | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [confirmDelete, setConfirmDelete] = useState<number[] | null>(null)
  const del = useDeleteRows(pid, table.id)

  // infinite scroll
  const scrollRef = useRef<HTMLDivElement>(null)
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
        onError: (err) => {
          const errs = rowErrors(err)
          const msg = errs ? errs.find((x) => x.column === col.name)?.error ?? errs[0]?.error : errorMessage(err)
          setEdit({ ...e, error: msg ?? errorMessage(err) })
        },
      },
    )
  }

  const { key: sortCol, desc } = parseSort(sort)
  const allSelected = list.length > 0 && list.every((r) => selected.has(r.id))
  const showGrid = list.length > 0 || adding

  const header = (
    <TableHeader className="sticky top-0 z-[1] bg-card">
      <TableRow className="hover:bg-transparent">
        <TableHead className="w-9 pr-0">
          <Checkbox
            checked={allSelected}
            onCheckedChange={(c) => setSelected(c ? new Set(list.map((r) => r.id)) : new Set())}
            aria-label="Select all rows"
            disabled={list.length === 0}
            data-testid="rows-select-all"
          />
        </TableHead>
        <SortHead label="id" col="id" active={sortCol} desc={desc} onSort={onSort} className="w-[110px]" />
        {columns.map((c) => (
          <SortHead
            key={c.name}
            label={c.name}
            col={c.name}
            active={sortCol}
            desc={desc}
            onSort={onSort}
            className={cn(isNumeric(c.type) && 'text-right')}
            right={isNumeric(c.type)}
          />
        ))}
        <SortHead
          label={
            <>
              created <span className="font-normal text-muted-foreground/80">· row metadata</span>
            </>
          }
          col="created_at"
          active={sortCol}
          desc={desc}
          onSort={onSort}
          className="w-[210px]"
        />
        <TableHead className="w-11" />
      </TableRow>
    </TableHeader>
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-auto border-t border-border"
        data-testid="data-panel"
        data-scroll-x="allowed"
      >
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
              icon={<Rows3 />}
              title={
                <>
                  No rows yet. Your routes write here:{' '}
                  <span className="font-mono font-normal">studio.db.table("{table.name}").insert(...)</span>
                </>
              }
              data-testid="data-empty"
              actions={
                <Button size="sm" variant="outline" onClick={() => setAdding(true)} data-testid="data-empty-add-row">
                  Add row
                </Button>
              }
            />
          )
        ) : (
          <Table>
            {header}
            <TableBody>
              {adding && <NewRow table={table} onDone={() => setAdding(false)} />}
              {list.map((r) => (
                <DataRow
                  key={r.id}
                  row={r}
                  columns={columns}
                  edit={edit?.rowId === r.id ? edit : null}
                  setEdit={setEdit}
                  commit={commit}
                  selected={selected.has(r.id)}
                  onSelect={(c) =>
                    setSelected((s) => {
                      const n = new Set(s)
                      if (c) n.add(r.id)
                      else n.delete(r.id)
                      return n
                    })
                  }
                  onDelete={() => setConfirmDelete([r.id])}
                />
              ))}
            </TableBody>
          </Table>
        )}
        <div ref={sentinel} className="h-px" />
        {rows.isFetchingNextPage && (
          <div className="flex justify-center py-3" data-testid="rows-loading-more">
            <Spinner className="size-4" />
          </div>
        )}
      </div>

      <div className="flex min-h-[38px] items-center gap-2 border-t border-border px-4 py-2 text-xs text-muted-foreground" data-testid="data-footer">
        <span data-testid="rows-count">
          {rows.isSuccess
            ? filtered
              ? `${pluralize(count, 'row')} match · ${pluralize(table.row_count ?? count, 'row')} total`
              : pluralize(count, 'row')
            : '…'}
        </span>
        {selected.size > 0 ? (
          <>
            <span>· {selected.size} selected</span>
            <Button variant="ghost" size="xs" className="text-destructive" onClick={() => setConfirmDelete([...selected])} data-testid="rows-delete-selected">
              Delete
            </Button>
          </>
        ) : edit ? (
          <span>
            · editing row <span className="font-mono">{edit.rowId}</span> · {edit.column} · <Kbd>⏎</Kbd> save · <Kbd>Esc</Kbd> cancel
          </span>
        ) : list.length > 0 ? (
          <span>· double-click a cell to edit</span>
        ) : null}
        <div className="grow" />
        <span className="font-mono text-[11.5px]" data-testid="data-snippet">
          studio.db.table("{table.name}")
        </span>
      </div>

      <AlertDialog open={!!confirmDelete} onOpenChange={(o) => !o && setConfirmDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {confirmDelete?.length === 1 ? `row ${confirmDelete[0]}` : pluralize(confirmDelete?.length ?? 0, 'row')}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Rows are shared by every environment and the published app. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              data-testid="rows-delete-confirm"
              onClick={() => {
                const ids = confirmDelete ?? []
                del.mutate(ids, {
                  onSuccess: () => setSelected((s) => new Set([...s].filter((x) => !ids.includes(x)))),
                  onError: (e) => toast.error(errorMessage(e)),
                  onSettled: () => setConfirmDelete(null),
                })
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
    <TableHead className={className} aria-sort={on ? (desc ? 'descending' : 'ascending') : undefined}>
      <button
        type="button"
        className={cn('inline-flex items-center gap-1 hover:text-foreground', right && 'flex-row-reverse', on && 'text-foreground')}
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
  edit,
  setEdit,
  commit,
  selected,
  onSelect,
  onDelete,
}: {
  row: Row
  columns: Column[]
  edit: EditState | null
  setEdit: (e: EditState | null) => void
  commit: (e: EditState) => void
  selected: boolean
  onSelect: (c: boolean) => void
  onDelete: () => void
}) {
  return (
    <TableRow data-testid="data-row" data-row-id={row.id} data-state={selected ? 'selected' : undefined}>
      <TableCell className="pr-0">
        <Checkbox checked={selected} onCheckedChange={(c) => onSelect(!!c)} aria-label={`Select row ${row.id}`} />
      </TableCell>
      <TableCell className="font-mono text-muted-foreground" data-testid="cell-id">
        {row.id}
      </TableCell>
      {columns.map((c) => {
        const editing = edit?.column === c.name
        const v = row.data[c.name]
        return (
          <TableCell
            key={c.name}
            className={cn('max-w-[360px] cursor-default', isNumeric(c.type) && 'text-right tabular-nums', editing && 'py-1')}
            data-testid={`cell-${c.name}`}
            data-row-id={row.id}
            onDoubleClick={() => !editing && setEdit({ rowId: row.id, column: c.name, text: editText(v, c.type) })}
            title={c.type === 'json' && v != null ? JSON.stringify(v, null, 2) : undefined}
          >
            {editing ? (
              <CellEditor edit={edit!} column={c} setEdit={setEdit} commit={commit} />
            ) : v === null || v === undefined ? (
              <span className="text-muted-foreground/60">—</span>
            ) : (
              <span className={cn('block truncate', (c.type === 'json' || c.type === 'boolean') && 'font-mono text-[12px]', c.type === 'json' && 'text-muted-foreground')}>
                {displayValue(v, c.type)}
              </span>
            )}
          </TableCell>
        )
      })}
      <TableCell className="text-muted-foreground" title={row.created_at}>
        {formatStamp(row.created_at)}
      </TableCell>
      <TableCell className="px-2">
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-xs" aria-label={`Row ${row.id} actions`} data-testid="row-menu">
                <Ellipsis />
              </Button>
            }
          />
          <DropdownMenuContent align="end" className="min-w-36">
            <DropdownMenuItem variant="destructive" onClick={onDelete} data-testid="row-delete">
              Delete row
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
        className={cn('h-7 min-w-[120px] text-[13px]', isNumeric(column.type) && 'w-[130px] text-right', column.type === 'json' && 'font-mono text-xs')}
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

function NewRow({ table, onDone }: { table: DataTable; onDone: () => void }) {
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

  const messages = [
    ...Object.entries(errors).map(([k, v]) => `${k}: ${v}`),
    ...(general ? [general] : []),
  ]

  return (
    <TableRow
      className="bg-primary/4 hover:bg-primary/4"
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
      <TableCell />
      <TableCell className="font-mono text-muted-foreground">new</TableCell>
      {table.columns.map((c, i) => (
        <TableCell key={c.name} className={cn('py-1', isNumeric(c.type) && 'text-right')}>
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
            className={cn('h-7 min-w-[110px] text-[13px]', isNumeric(c.type) ? 'ml-auto w-[130px] text-right' : 'max-w-[240px]', c.type === 'json' && 'font-mono text-xs')}
            aria-invalid={!!errors[c.name]}
            data-testid={`new-row-${c.name}`}
          />
        </TableCell>
      ))}
      <TableCell className="whitespace-normal py-1">
        {messages.length > 0 && (
          <span className="text-[12px] text-destructive" role="alert" data-testid="row-invalid">
            {messages.join(' · ')}
          </span>
        )}
      </TableCell>
      <TableCell className="px-2 py-1">
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
        <div key={i} className="flex h-[42px] items-center gap-6 border-b border-border/65 px-4">
          <Skeleton className="size-3.5 rounded" />
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

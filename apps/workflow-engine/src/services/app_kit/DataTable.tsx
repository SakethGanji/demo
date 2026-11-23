/** Studio UI kit — DataTable (provided by Studio, refreshed every builder turn; do not edit).
 * Selection (with select-all of visible rows), click-to-sort columns, AND-combined filter chips, a search box,
 * a selection action bar, row click + active-row highlight, and a phone card layout below the md breakpoint. */
import React, { useEffect, useMemo, useRef } from 'react'
import { cx, Spinner, Tag, type Tone } from './ui'
import { timeAgo, usePersistedUiState } from './hooks'

export interface Column<T> {
  key: string
  /** @control text */
  header: React.ReactNode
  /** cell content for a cell that combines fields; a single field is shown with `format` instead */
  render?: (row: T) => React.ReactNode
  /** how row[key] is shown when there is no `render` (null / empty shows '—'); defaults to String(row[key]) */
  format?: ColumnFormat
  /** `format: 'badge'`: the tag tone per value (unlisted values are gray) */
  toneMap?: Record<string, Tone>
  /** not rendered (desktop or phone) */
  hidden?: boolean
  /** value used for sorting; defaults to row[key] when it is a string/number */
  sortValue?: (row: T) => string | number | null | undefined
  sortable?: boolean
  width?: string
  align?: 'left' | 'right'
  className?: string
  /** phone card placement: primary (bold, top), meta (top right), secondary (below, default) or hidden */
  mobile?: 'primary' | 'meta' | 'secondary' | 'hidden'
}

export type ColumnFormat = 'text' | 'date' | 'datetime' | 'money' | 'number' | 'badge' | 'bool' | 'ago'

export interface FilterChip<T> { id: string; label: React.ReactNode; test: (row: T) => boolean }

/** HTML attributes passed to the table's root element (`id`, `style`, `data-*`, `aria-*`). */
type RootAttrs = Omit<React.HTMLAttributes<HTMLDivElement>, 'title' | 'children' | 'color' | 'content' | 'onChange' | 'onSelect' | 'defaultValue' | 'placeholder' | 'prefix'>

export interface DataTableProps<T> extends RootAttrs {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  selectable?: boolean
  selected?: string[]
  onSelectedChange?: (ids: string[]) => void
  onRowClick?: (row: T) => void
  /** highlighted row (e.g. the one open in a drawer) */
  activeKey?: string | null
  filters?: FilterChip<T>[]
  /** controlled active filter ids (omit for internal state) */
  activeFilters?: string[]
  onActiveFiltersChange?: (ids: string[]) => void
  /** enables the search box; return the text to match for a row */
  searchText?: (row: T) => string
  searchPlaceholder?: string
  /** content at the right of the toolbar (buttons) */
  toolbar?: React.ReactNode
  /** bar shown above the table while rows are selected, e.g. "Run selected" */
  selectionBar?: (ids: string[]) => React.ReactNode
  rowClassName?: (row: T) => string | undefined
  loading?: boolean
  /** @control text @group Content */
  emptyText?: React.ReactNode
  footer?: React.ReactNode
  initialSort?: { key: string; dir: 'asc' | 'desc' }
  /** Id under which the Studio preview remembers sort, search and filters across edits (default: the column keys).
   * @control hidden @group Behavior */
  stateKey?: string
  /** Row height.
   * @control select @options comfortable|compact @group Style @default comfortable */
  density?: 'comfortable' | 'compact'
  className?: string
  toolbarClassName?: string
  footerClassName?: string
}

function defaultValue<T>(row: T, key: string): unknown {
  return (row as Record<string, unknown>)[key]
}

function asDate(v: unknown): Date | null {
  const d = v instanceof Date ? v : typeof v === 'string' || typeof v === 'number' ? new Date(v) : null
  return d && !Number.isNaN(d.getTime()) ? d : null
}

/** A cell value shown by `format` ('—' for null / empty; a value the format cannot read is shown as text). */
function formatCell(format: ColumnFormat, v: unknown, toneMap?: Record<string, Tone>): React.ReactNode {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
  switch (format) {
    case 'number': return Number.isNaN(n) ? String(v) : n.toLocaleString()
    case 'money': return Number.isNaN(n) ? String(v) : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    case 'date': return asDate(v)?.toLocaleDateString() ?? String(v)
    case 'datetime': return asDate(v)?.toLocaleString() ?? String(v)
    case 'ago': return (asDate(v) && timeAgo(asDate(v))) || String(v)
    case 'bool': return v === true || v === 1 || v === 'true' ? 'Yes' : 'No'
    case 'badge': return <Tag tone={toneMap?.[String(v)] ?? 'gray'}>{String(v)}</Tag>
    default: return String(v)
  }
}

/** Rich table for internal tools; renders as cards below the md breakpoint.
 * @category data
 * @example <DataTable rows={rows} rowKey={(r) => String(r.id)} columns={[{ key: 'name', header: 'Name' }, { key: 'amount', header: 'Amount', format: 'money', align: 'right' }]} emptyText="Nothing yet" /> */
export function DataTable<T>(props: DataTableProps<T>) {
  const { rows, columns, rowKey, selectable, selected = [], onSelectedChange, onRowClick, activeKey, filters = [], activeFilters, onActiveFiltersChange,
    searchText, searchPlaceholder = 'Search…', toolbar, selectionBar, rowClassName, loading, emptyText = 'No rows', footer, initialSort, stateKey,
    density = 'comfortable', className, toolbarClassName, footerClassName, ...rest } = props
  const sk = stateKey ?? `DataTable:${columns.map((c) => c.key).join(',')}`
  const [innerFilters, setInnerFilters] = usePersistedUiState<string[]>(`${sk}:filters`, [], (v) => Array.isArray(v) && v.every((x) => typeof x === 'string'))
  const active = activeFilters ?? innerFilters
  const setActive = (ids: string[]) => (onActiveFiltersChange ? onActiveFiltersChange(ids) : setInnerFilters(ids))
  const cellY = density === 'compact' ? 'py-1.5' : 'py-2.5'
  const [query, setQuery] = usePersistedUiState<string>(`${sk}:query`, '')
  const [sort, setSort] = usePersistedUiState<{ key: string; dir: 'asc' | 'desc' } | null>(`${sk}:sort`, initialSort ?? null,
    (v) => v === null || (typeof v === 'object' && columns.some((c) => c.key === (v as { key?: unknown }).key)))

  const visible = useMemo(() => {
    let out = rows
    for (const f of filters) if (active.includes(f.id)) out = out.filter(f.test)
    const q = query.trim().toLowerCase()
    if (q && searchText) out = out.filter((r) => searchText(r).toLowerCase().includes(q))
    if (sort) {
      const col = columns.find((c) => c.key === sort.key)
      const val = (r: T) => {
        const v = col?.sortValue ? col.sortValue(r) : defaultValue(r, sort.key)
        return typeof v === 'number' || typeof v === 'string' ? v : v == null ? '' : String(v)
      }
      out = [...out].sort((a, b) => {
        const x = val(a)
        const y = val(b)
        const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })
        return sort.dir === 'asc' ? c : -c
      })
    }
    return out
  }, [rows, filters, active, query, searchText, sort, columns])

  const visibleIds = visible.map(rowKey)
  const allOn = visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id))
  const someOn = visibleIds.some((id) => selected.includes(id))
  const headCb = useRef<HTMLInputElement>(null)
  useEffect(() => { if (headCb.current) headCb.current.indeterminate = someOn && !allOn }, [someOn, allOn])

  const toggle = (id: string) => onSelectedChange?.(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  const toggleAll = () => onSelectedChange?.(allOn ? selected.filter((id) => !visibleIds.includes(id)) : [...new Set([...selected, ...visibleIds])])
  const clickSort = (c: Column<T>) => {
    if (c.sortable === false) return
    setSort((s) => (!s || s.key !== c.key ? { key: c.key, dir: 'asc' } : s.dir === 'asc' ? { key: c.key, dir: 'desc' } : null))
  }
  const cell = (c: Column<T>, r: T) => (c.render ? c.render(r) : c.format ? formatCell(c.format, defaultValue(r, c.key), c.toneMap) : String(defaultValue(r, c.key) ?? ''))
  // shown columns with their index in `columns` (data-vpart="columns.<i>" names the column as passed, hidden ones included)
  const shown = columns.map((c, i) => ({ c, i })).filter((x) => !x.c.hidden)
  const primary = shown.filter((x) => x.c.mobile === 'primary')
  const meta = shown.filter((x) => x.c.mobile === 'meta')
  const secondary = shown.filter((x) => (x.c.mobile ?? 'secondary') === 'secondary' && !(primary.length === 0 && x === shown[0]))
  const cardPrimary = primary.length ? primary : shown.slice(0, 1)

  return (
    <div data-testid="data-table" data-vcomp="DataTable" {...rest} className={cx('min-w-0', className)}>
      {(searchText || filters.length > 0 || toolbar) && (
        <div data-vpart="toolbar" className={cx('flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5', toolbarClassName)}>
          {searchText && (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="min-w-[160px] flex-1 rounded-lg border border-input bg-card px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
            />
          )}
          {filters.map((f) => {
            const on = active.includes(f.id)
            return (
              <button key={f.id} type="button" aria-pressed={on} onClick={() => setActive(on ? active.filter((x) => x !== f.id) : [...active, f.id])}
                className={cx('rounded-full border px-2.5 py-1 text-xs font-medium', on ? 'border-primary bg-primary-soft text-primary' : 'border-input bg-card text-soft-foreground hover:bg-subtle')}>
                {f.label}
              </button>
            )
          })}
          {toolbar && <div className="ml-auto flex flex-wrap items-center gap-2">{toolbar}</div>}
        </div>
      )}
      {selectable && selected.length > 0 && selectionBar && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-primary-soft px-3 py-2 text-sm" data-testid="selection-bar">
          {selectionBar(selected)}
        </div>
      )}

      {/* desktop table */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {selectable && (
                <th className="w-9 border-b border-border px-3 py-2 text-left">
                  <input ref={headCb} type="checkbox" checked={allOn} onChange={toggleAll} aria-label="Select all" className="h-4 w-4 accent-primary" />
                </th>
              )}
              {shown.map(({ c, i }) => (
                <th key={c.key} data-vpart={`columns.${i}`} style={c.width ? { width: c.width } : undefined} onClick={() => clickSort(c)}
                  className={cx('select-none whitespace-nowrap border-b border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground',
                    c.align === 'right' ? 'text-right' : 'text-left', c.sortable !== false && 'cursor-pointer hover:text-foreground')}>
                  {c.header}
                  {sort && sort.key === c.key && <span className="ml-1">{sort.dir === 'asc' ? '▲' : '▼'}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const id = rowKey(r)
              const on = selected.includes(id)
              return (
                <tr key={id} data-row-id={id} onClick={onRowClick ? () => onRowClick(r) : undefined}
                  tabIndex={onRowClick ? 0 : undefined} onKeyDown={onRowClick ? (e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onRowClick(r) } } : undefined}
                  className={cx('border-b border-border/60 align-top', onRowClick && 'cursor-pointer hover:bg-subtle', activeKey === id && 'bg-primary-soft/70', rowClassName?.(r))}>
                  {selectable && (
                    <td className={cx('px-3', cellY)} onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={on} onChange={() => toggle(id)} aria-label={`Select ${id}`} className="h-4 w-4 accent-primary" />
                    </td>
                  )}
                  {shown.map(({ c, i }) => (
                    <td key={c.key} data-vpart={`columns.${i}`} className={cx('px-3', cellY, c.align === 'right' && 'text-right', c.className)}>{cell(c, r)}</td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* phone cards */}
      <ul className="m-0 list-none p-0 md:hidden">
        {visible.map((r) => {
          const id = rowKey(r)
          return (
            <li key={id} data-row-id={id} onClick={onRowClick ? () => onRowClick(r) : undefined}
              className={cx('flex gap-3 border-b border-border/60 px-3', density === 'compact' ? 'py-2' : 'py-3', activeKey === id && 'bg-primary-soft/70', rowClassName?.(r))}>
              {selectable && (
                <span onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} aria-label={`Select ${id}`} className="mt-0.5 h-4 w-4 accent-primary" />
                </span>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1 text-sm font-medium text-foreground">{cardPrimary.map(({ c, i }) => <div key={c.key} data-vpart={`columns.${i}`}>{cell(c, r)}</div>)}</div>
                  {meta.map(({ c, i }) => <div key={c.key} data-vpart={`columns.${i}`} className="shrink-0">{cell(c, r)}</div>)}
                </div>
                <div className="mt-1 flex flex-col gap-1 text-xs text-soft-foreground">
                  {secondary.filter((x) => !meta.includes(x)).map(({ c, i }) => <div key={c.key} data-vpart={`columns.${i}`}>{cell(c, r)}</div>)}
                </div>
              </div>
            </li>
          )
        })}
      </ul>

      {loading && visible.length === 0 && <div className="flex justify-center py-8 text-faint-foreground"><Spinner size={18} /></div>}
      {!loading && visible.length === 0 && <div className="px-3 py-8 text-center text-sm text-muted-foreground">{emptyText}</div>}
      {footer && <div data-vpart="footer" className={cx('flex flex-wrap gap-4 px-3 py-2.5 text-xs text-muted-foreground', footerClassName)}>{footer}</div>}
    </div>
  )
}

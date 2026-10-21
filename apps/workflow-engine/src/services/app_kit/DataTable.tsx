/** Studio UI kit — DataTable (provided by Studio, refreshed every builder turn; do not edit).
 * Selection (with select-all of visible rows), click-to-sort columns, AND-combined filter chips, a search box,
 * a selection action bar, row click + active-row highlight, and a phone card layout below the md breakpoint. */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { cx, Spinner } from './ui'

export interface Column<T> {
  key: string
  header: React.ReactNode
  /** cell content; defaults to String(row[key]) */
  render?: (row: T) => React.ReactNode
  /** value used for sorting; defaults to row[key] when it is a string/number */
  sortValue?: (row: T) => string | number | null | undefined
  sortable?: boolean
  width?: string
  align?: 'left' | 'right'
  className?: string
  /** phone card placement: primary (bold, top), meta (top right), secondary (below, default) or hidden */
  mobile?: 'primary' | 'meta' | 'secondary' | 'hidden'
}

export interface FilterChip<T> { id: string; label: React.ReactNode; test: (row: T) => boolean }

export interface DataTableProps<T> {
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
  emptyText?: React.ReactNode
  footer?: React.ReactNode
  initialSort?: { key: string; dir: 'asc' | 'desc' }
}

function defaultValue<T>(row: T, key: string): unknown {
  return (row as Record<string, unknown>)[key]
}

/** Rich table for internal tools; renders as cards below the md breakpoint. */
export function DataTable<T>(props: DataTableProps<T>) {
  const { rows, columns, rowKey, selectable, selected = [], onSelectedChange, onRowClick, activeKey, filters = [], searchText,
    searchPlaceholder = 'Search…', toolbar, selectionBar, rowClassName, loading, emptyText = 'No rows', footer, initialSort } = props
  const [innerFilters, setInnerFilters] = useState<string[]>([])
  const active = props.activeFilters ?? innerFilters
  const setActive = (ids: string[]) => (props.onActiveFiltersChange ? props.onActiveFiltersChange(ids) : setInnerFilters(ids))
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(initialSort ?? null)

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
  const cell = (c: Column<T>, r: T) => (c.render ? c.render(r) : String(defaultValue(r, c.key) ?? ''))
  const primary = columns.filter((c) => c.mobile === 'primary')
  const meta = columns.filter((c) => c.mobile === 'meta')
  const secondary = columns.filter((c) => (c.mobile ?? 'secondary') === 'secondary' && !(primary.length === 0 && c === columns[0]))
  const cardPrimary = primary.length ? primary : columns.slice(0, 1)

  return (
    <div className="min-w-0" data-testid="data-table">
      {(searchText || filters.length > 0 || toolbar) && (
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2.5">
          {searchText && (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="min-w-[160px] flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-blue-100"
            />
          )}
          {filters.map((f) => {
            const on = active.includes(f.id)
            return (
              <button key={f.id} type="button" aria-pressed={on} onClick={() => setActive(on ? active.filter((x) => x !== f.id) : [...active, f.id])}
                className={cx('rounded-full border px-2.5 py-1 text-xs font-medium', on ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50')}>
                {f.label}
              </button>
            )
          })}
          {toolbar && <div className="ml-auto flex flex-wrap items-center gap-2">{toolbar}</div>}
        </div>
      )}
      {selectable && selected.length > 0 && selectionBar && (
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-blue-50 px-3 py-2 text-sm" data-testid="selection-bar">
          {selectionBar(selected)}
        </div>
      )}

      {/* desktop table */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {selectable && (
                <th className="w-9 border-b border-slate-200 px-3 py-2 text-left">
                  <input ref={headCb} type="checkbox" checked={allOn} onChange={toggleAll} aria-label="Select all" className="h-4 w-4 accent-blue-600" />
                </th>
              )}
              {columns.map((c) => (
                <th key={c.key} style={c.width ? { width: c.width } : undefined} onClick={() => clickSort(c)}
                  className={cx('select-none whitespace-nowrap border-b border-slate-200 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500',
                    c.align === 'right' ? 'text-right' : 'text-left', c.sortable !== false && 'cursor-pointer hover:text-slate-800')}>
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
                  className={cx('border-b border-slate-100 align-top', onRowClick && 'cursor-pointer hover:bg-slate-50', activeKey === id && 'bg-blue-50/70', rowClassName?.(r))}>
                  {selectable && (
                    <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={on} onChange={() => toggle(id)} aria-label={`Select ${id}`} className="h-4 w-4 accent-blue-600" />
                    </td>
                  )}
                  {columns.map((c) => (
                    <td key={c.key} className={cx('px-3 py-2.5', c.align === 'right' && 'text-right', c.className)}>{cell(c, r)}</td>
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
              className={cx('flex gap-3 border-b border-slate-100 px-3 py-3', activeKey === id && 'bg-blue-50/70', rowClassName?.(r))}>
              {selectable && (
                <span onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} aria-label={`Select ${id}`} className="mt-0.5 h-4 w-4 accent-blue-600" />
                </span>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1 text-sm font-medium text-slate-900">{cardPrimary.map((c) => <div key={c.key}>{cell(c, r)}</div>)}</div>
                  {meta.map((c) => <div key={c.key} className="shrink-0">{cell(c, r)}</div>)}
                </div>
                <div className="mt-1 flex flex-col gap-1 text-xs text-slate-600">
                  {secondary.filter((c) => !meta.includes(c)).map((c) => <div key={c.key}>{cell(c, r)}</div>)}
                </div>
              </div>
            </li>
          )
        })}
      </ul>

      {loading && visible.length === 0 && <div className="flex justify-center py-8 text-slate-400"><Spinner size={18} /></div>}
      {!loading && visible.length === 0 && <div className="px-3 py-8 text-center text-sm text-slate-500">{emptyText}</div>}
      {footer && <div className="flex flex-wrap gap-4 px-3 py-2.5 text-xs text-slate-500">{footer}</div>}
    </div>
  )
}

/** Studio UI kit — boards (provided by Studio, refreshed every builder turn; do not edit).
 * Kanban (columns of cards, drag a card to another column), Calendar (month grid with events) and FileUpload (drop zone
 * that hands the chosen files to a callback). Colours come only from theme tokens. */
import React, { useMemo, useRef, useState } from 'react'
import { cx } from './ui'
import { usePersistedUiState } from './hooks'

type DivProps = Omit<React.HTMLAttributes<HTMLDivElement>, 'title' | 'children' | 'onDrop'>

export interface KanbanColumn {
  id: string
  /** @control text */
  title: React.ReactNode
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
}

const COL_TONE = { neutral: 'bg-soft-foreground', primary: 'bg-primary', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger' }

/** Columns of cards (a status board). Each item sits in the column its `columnOf` returns; dragging a card to another
 * column calls `onMove(item, toColumn)` — update the item there (the board does not move it by itself).
 * @category data
 * @example <Kanban columns={[{ id: 'todo', title: 'To do' }, { id: 'done', title: 'Done' }]} items={tasks} itemKey={(t) => t.id} columnOf={(t) => t.status} renderCard={(t) => t.title} onMove={(t, to) => save(t.id, to)} /> */
export function Kanban<T>({ columns, items, itemKey, columnOf, renderCard, onMove, onCardClick, emptyText = 'No cards', className, ...rest }: DivProps & {
  /** The columns, left to right.
   * @control hidden @group Data */
  columns: KanbanColumn[]
  /** All cards.
   * @control hidden @group Data */
  items: T[]
  /** Stable id of a card.
   * @control hidden @group Data */
  itemKey: (item: T) => string
  /** The column id a card belongs to.
   * @control hidden @group Data */
  columnOf: (item: T) => string
  /** Card body.
   * @control hidden @group Content */
  renderCard: (item: T) => React.ReactNode
  /** Called when a card is dropped on another column.
   * @control hidden @group Behavior */
  onMove?: (item: T, toColumn: string) => void
  /** Called when a card is clicked.
   * @control hidden @group Behavior */
  onCardClick?: (item: T) => void
  /** Shown in an empty column.
   * @control text @group Content @default No cards */
  emptyText?: React.ReactNode
}) {
  const [dragging, setDragging] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const byCol = useMemo(() => {
    const m = new Map<string, T[]>(columns.map((c) => [c.id, []]))
    for (const it of items) m.get(columnOf(it))?.push(it)
    return m
  }, [columns, items, columnOf])
  const drop = (col: string) => {
    const it = items.find((x) => itemKey(x) === dragging)
    setDragging(null); setOver(null)
    if (it && columnOf(it) !== col) onMove?.(it, col)
  }
  return (
    <div data-vcomp="Kanban" {...rest} className={cx('flex min-w-0 gap-3 overflow-x-auto pb-2', className)}>
      {columns.map((c, ci) => {
        const cards = byCol.get(c.id) ?? []
        return (
          <section key={c.id} data-vpart={`columns.${ci}`} aria-label={typeof c.title === 'string' ? c.title : c.id}
            onDragOver={(e) => { if (onMove && dragging) { e.preventDefault(); setOver(c.id) } }}
            onDragLeave={() => setOver((o) => (o === c.id ? null : o))}
            onDrop={(e) => { e.preventDefault(); drop(c.id) }}
            className={cx('flex w-72 shrink-0 flex-col gap-2 rounded-xl border bg-subtle p-2', over === c.id ? 'border-primary' : 'border-border')}>
            <header className="flex items-center gap-2 px-1 py-1 text-sm font-semibold text-foreground">
              <span aria-hidden className={cx('h-2 w-2 rounded-full', COL_TONE[c.tone ?? 'neutral'])} />
              <span className="min-w-0 flex-1 truncate">{c.title}</span>
              <span className="rounded-full bg-muted px-1.5 text-[11px] text-soft-foreground">{cards.length}</span>
            </header>
            {cards.map((it) => {
              const k = itemKey(it)
              return (
                <article key={k} draggable={!!onMove} onDragStart={(e) => { e.dataTransfer.setData('text/plain', k); setDragging(k) }}
                  onDragEnd={() => { setDragging(null); setOver(null) }} onClick={onCardClick ? () => onCardClick(it) : undefined}
                  className={cx('rounded-lg border border-border bg-card p-3 text-sm text-card-foreground shadow-sm', onMove && 'cursor-grab',
                    onCardClick && 'hover:border-primary', dragging === k && 'opacity-50')}>
                  {renderCard(it)}
                </article>
              )
            })}
            {!cards.length && <div className="px-1 py-3 text-center text-xs text-muted-foreground">{emptyText}</div>}
          </section>
        )
      })}
    </div>
  )
}

export interface CalendarEvent {
  id: string
  /** @control text */
  title: React.ReactNode
  /** ISO date or date-time; the day it shows on */
  date: string | Date
  tone?: 'primary' | 'success' | 'warning' | 'danger' | 'neutral'
}

const EV_TONE = { primary: 'bg-primary-soft text-primary', success: 'bg-success-soft text-success', warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger', neutral: 'bg-muted text-soft-foreground' }

/** A date-only string ("2026-10-28") is that day locally, not midnight UTC (which is the day before west of Greenwich). */
function toDate(v: string | Date): Date {
  const m = typeof v === 'string' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(v.trim()) : null
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(v)
}

function dayKey(d: Date): string { return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` }

/** A month grid with events on their days; arrows switch months. Click a day or an event to act on it.
 * @category data
 * @example <Calendar events={[{ id: '1', title: 'Payroll', date: '2026-10-28', tone: 'warning' }]} onEventClick={(e) => open(e.id)} /> */
export function Calendar({ events, month, weekStartsOn = 1, maxPerDay = 3, onDayClick, onEventClick, stateKey, className, ...rest }: DivProps & {
  /** The events.
   * @control hidden @group Data */
  events: CalendarEvent[]
  /** Month shown first (any date inside it; default: today).
   * @control hidden @group Data */
  month?: string | Date
  /** First column of the week (0 Sunday, 1 Monday).
   * @control select @options 0|1 @group Layout @default 1 */
  weekStartsOn?: 0 | 1
  /** Events listed per day before "+N more".
   * @control number @min 1 @max 10 @group Layout @default 3 */
  maxPerDay?: number
  /** Called with the clicked day.
   * @control hidden @group Behavior */
  onDayClick?: (day: Date) => void
  /** Called with the clicked event.
   * @control hidden @group Behavior */
  onEventClick?: (event: CalendarEvent) => void
  /** Id under which the Studio preview remembers the shown month across edits.
   * @control hidden @group Behavior */
  stateKey?: string
}) {
  const start = month ? toDate(month) : new Date()
  const [ym, setYm] = usePersistedUiState<number>(stateKey ?? 'Calendar', start.getFullYear() * 12 + start.getMonth())
  const y = Math.floor(ym / 12)
  const m = ym % 12
  const first = new Date(y, m, 1)
  const lead = (first.getDay() - weekStartsOn + 7) % 7
  const days = Array.from({ length: 42 }, (_, i) => new Date(y, m, 1 - lead + i))
  const byDay = useMemo(() => {
    const out = new Map<string, CalendarEvent[]>()
    for (const e of events) {
      const d = toDate(e.date)
      if (Number.isNaN(d.getTime())) continue
      const k = dayKey(d)
      out.set(k, [...(out.get(k) ?? []), e])
    }
    return out
  }, [events])
  const names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  const head = [...names.slice(weekStartsOn), ...names.slice(0, weekStartsOn)]
  const today = dayKey(new Date())
  return (
    <div data-vcomp="Calendar" {...rest} className={cx('overflow-hidden rounded-xl border border-border bg-card', className)}>
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <button type="button" aria-label="Previous month" onClick={() => setYm(ym - 1)} className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted">‹</button>
        <h3 className="m-0 flex-1 text-center text-sm font-semibold text-foreground">{first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</h3>
        <button type="button" aria-label="Next month" onClick={() => setYm(ym + 1)} className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted">›</button>
      </header>
      <div className="grid grid-cols-7 border-b border-border bg-subtle">
        {head.map((d) => <div key={d} className="px-2 py-1 text-center text-[11px] font-semibold text-muted-foreground">{d}</div>)}
      </div>
      <div className="grid grid-cols-7">
        {days.map((d) => {
          const evs = byDay.get(dayKey(d)) ?? []
          const inMonth = d.getMonth() === m
          return (
            <div key={d.toISOString()} onClick={onDayClick ? () => onDayClick(d) : undefined}
              className={cx('min-h-[84px] border-b border-r border-border p-1', !inMonth && 'bg-subtle', onDayClick && 'cursor-pointer hover:bg-muted')}>
              <div className={cx('mb-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px]',
                dayKey(d) === today ? 'bg-primary text-primary-foreground' : inMonth ? 'text-foreground' : 'text-faint-foreground')}>{d.getDate()}</div>
              <div className="flex flex-col gap-0.5">
                {evs.slice(0, maxPerDay).map((e) => (
                  <button key={e.id} type="button" onClick={(ev) => { ev.stopPropagation(); onEventClick?.(e) }}
                    className={cx('truncate rounded px-1 py-0.5 text-left text-[11px]', EV_TONE[e.tone ?? 'primary'])}>{e.title}</button>
                ))}
                {evs.length > maxPerDay && <span className="px-1 text-[11px] text-muted-foreground">+{evs.length - maxPerDay} more</span>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** A drop zone (or click to browse) that hands the chosen files to `onFiles`; read them with `file.text()` or
 * `file.arrayBuffer()`. Files over `maxSizeMb` or not matching `accept` are reported, not passed on.
 * @category form
 * @example <FileUpload accept=".csv" onFiles={(files) => importCsv(files[0])} hint="CSV, up to 5 MB" maxSizeMb={5} /> */
export function FileUpload({ onFiles, accept, multiple = false, maxSizeMb, label = 'Drop files here or click to browse', hint, disabled = false, className, ...rest }: DivProps & {
  /** Called with the accepted files.
   * @control hidden @group Behavior */
  onFiles: (files: File[]) => void
  /** Allowed types, like the input's accept: ".csv,image/*".
   * @control text @group Behavior */
  accept?: string
  /** Allow several files at once.
   * @control boolean @group Behavior @default false */
  multiple?: boolean
  /** Largest allowed file in MB.
   * @control number @min 1 @group Behavior */
  maxSizeMb?: number
  /** Main text in the zone.
   * @control text @group Content @default Drop files here or click to browse */
  label?: React.ReactNode
  /** Small text under it.
   * @control text @group Content */
  hint?: React.ReactNode
  /** Grey out and ignore drops.
   * @control boolean @group Behavior @default false */
  disabled?: boolean
}) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const take = (list: FileList | null) => {
    if (!list || disabled) return
    const pats = (accept ?? '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean)
    const ok = (f: File) => !pats.length || pats.some((p) => (p.startsWith('.') ? f.name.toLowerCase().endsWith(p)
      : p.endsWith('/*') ? f.type.startsWith(p.slice(0, -1)) : f.type === p))
    const all = Array.from(list).slice(0, multiple ? undefined : 1)
    const bad = all.filter((f) => !ok(f) || (maxSizeMb != null && f.size > maxSizeMb * 1024 * 1024))
    setError(bad.length ? `Not accepted: ${bad.map((f) => f.name).join(', ')}` : null)
    const good = all.filter((f) => !bad.includes(f))
    if (good.length) onFiles(good)
  }
  return (
    <div data-vcomp="FileUpload" {...rest} className={cx('flex flex-col gap-1', className)}>
      <div role="button" tabIndex={disabled ? -1 : 0} aria-disabled={disabled}
        onClick={() => !disabled && input.current?.click()} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') input.current?.click() }}
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true) }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files) }}
        className={cx('flex flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-8 text-center',
          over ? 'border-primary bg-primary-soft' : 'border-input bg-subtle', disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:border-primary')}>
        <span aria-hidden className="text-xl text-muted-foreground">⇪</span>
        <span className="text-sm font-medium text-foreground">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      <input ref={input} type="file" hidden accept={accept} multiple={multiple} onChange={(e) => { take(e.target.files); e.target.value = '' }} />
      {error && <span role="alert" className="text-xs text-danger">{error}</span>}
    </div>
  )
}

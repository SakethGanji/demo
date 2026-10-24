/**
 * The Layers tab of the inspector: the JSX tree of one app file (the selection's screen by default), virtualized. Click a
 * row to select it (the preview follows), hover to outline it in the preview, drag to reorder or move into another
 * container, toggle visibility where the source allows it, filter by name or text, and drive it from the keyboard
 * (arrows move the selection, ←/→ collapse and expand, Enter selects, Delete deletes). Every change is an existing `Op`.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowUpRight, ChartColumn, ChevronRight, Component, Eye, EyeOff, FileCode2, Image, LayoutPanelTop, List, Lock, MousePointerClick, Repeat, Search,
  Table2, TextCursorInput, Type, type LucideIcon,
} from 'lucide-react'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { cn } from '@/shared/lib/utils'
import type { VisualEdit } from '../useVisualEdit'
import { editRow, hoverPreview, wouldRefuse } from './act'
import { useLayerFiles } from './files'
import { dropPlan, hideOps, layerTree, pathTo, rowFor, screenFiles, selectionOfRow, visibleRows, type DropZone, type LayerKind, type LayerRow, type LayerTree } from './tree'

const ROW = 24
const INDENT = 12
const OVERSCAN = 8

const ICONS: Record<LayerKind, LucideIcon> = {
  layout: LayoutPanelTop,
  text: Type,
  button: MousePointerClick,
  table: Table2,
  form: TextCursorInput,
  chart: ChartColumn,
  image: Image,
  component: Component,
  item: List,
  group: FileCode2,
}

interface Over {
  id: string
  zone: DropZone
  /** what the drop does, or why it can't */
  note: string
  ok: boolean
}

function zoneAt(e: React.DragEvent, row: LayerRow): DropZone {
  const r = e.currentTarget.getBoundingClientRect()
  const y = (e.clientY - r.top) / r.height
  if (y < 0.3) return 'before'
  if (y > 0.7) return 'after'
  return row.accepts ? 'inside' : y < 0.5 ? 'before' : 'after'
}

function screenLabel(path: string): string {
  return path.replace(/\.(t|j)sx$/, '')
}

function RowView({ row, tree, selected, active, collapsed, over, dragging, onToggle, onClick, onHover, onHide, onOpen, drag }: {
  row: LayerRow
  tree: LayerTree
  selected: boolean
  active: boolean
  collapsed: boolean
  over: Over | null
  dragging: boolean
  onToggle: () => void
  onClick: () => void
  onHover: () => void
  onHide: () => void
  onOpen: () => void
  drag: { start: (e: React.DragEvent) => void; over: (e: React.DragEvent) => void; drop: (e: React.DragEvent) => void; end: () => void }
}) {
  const Icon = ICONS[row.kind]
  const hidden = row.hide.ok && row.hide.hidden
  const hasKids = row.children.length > 0
  const ancestors = pathTo(tree, row.id).slice(0, -1)
  return (
    <div
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={hasKids ? !collapsed : undefined}
      className={cn(
        'group relative flex cursor-default items-center gap-1 pr-1.5 text-[12px] select-none',
        selected ? 'bg-raise font-semibold text-foreground' : 'text-foreground row-hover',
        active && !selected && 'shadow-[inset_0_0_0_1px_var(--hairline)]',
        dragging && 'opacity-40',
        over?.zone === 'inside' && (over.ok ? 'shadow-[inset_0_0_0_1.5px_var(--primary)]' : 'shadow-[inset_0_0_0_1.5px_var(--destructive)]'),
      )}
      style={{ height: ROW, paddingLeft: 6 + row.depth * INDENT }}
      draggable={row.type === 'element'}
      onDragStart={drag.start}
      onDragOver={drag.over}
      onDrop={drag.drop}
      onDragEnd={drag.end}
      onMouseEnter={onHover}
      onClick={onClick}
      onDoubleClick={() => (row.defines ? onOpen() : hasKids && onToggle())}
      title={row.readOnly ? `${row.name}: ${row.readOnly}` : undefined}
      data-testid={`vlayers-row-${row.tid}`}
      data-selected={selected || undefined}
    >
      {/* indent guides, one per ancestor level */}
      {ancestors.map((a, i) => (
        <span key={a} aria-hidden className="pointer-events-none absolute top-0 bottom-0 w-px bg-hairline" style={{ left: 6 + i * INDENT + 6 }} />
      ))}
      {over && over.zone !== 'inside' && (
        <span aria-hidden className={cn('pointer-events-none absolute right-1 h-0.5 rounded-full', over.ok ? 'bg-primary' : 'bg-destructive', over.zone === 'before' ? 'top-0' : 'bottom-0')} style={{ left: 6 + row.depth * INDENT }} />
      )}
      {hasKids ? (
        <button
          type="button"
          aria-label={collapsed ? 'Expand' : 'Collapse'}
          className="flex size-4 shrink-0 items-center justify-center rounded-chip text-muted-foreground hover:text-foreground"
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
          data-testid={`vlayers-toggle-${row.tid}`}
        >
          <ChevronRight className={cn('size-3 transition-transform', !collapsed && 'rotate-90')} />
        </button>
      ) : (
        <span className="size-4 shrink-0" />
      )}
      <Icon className={cn('size-3.5 shrink-0', row.kit || row.type === 'group' ? 'text-muted-foreground' : 'text-primary')} aria-hidden />
      <span className={cn('flex min-w-0 flex-1 items-baseline gap-1.5', hidden && 'opacity-50')}>
        <span className={cn('shrink-0 truncate', row.type !== 'item' && 'font-mono text-[11.5px]')}>{row.type === 'item' ? row.hint : row.name}</span>
        {row.type !== 'item' && row.hint && <span className="min-w-0 truncate text-[11px] font-normal text-muted-foreground">{row.hint}</span>}
      </span>
      {row.repeats && <Repeat className="size-3 shrink-0 text-muted-foreground" aria-label="Repeats for every row of a list" />}
      {row.readOnly && row.type !== 'group' && (
        <span className="flex shrink-0 items-center text-muted-foreground" title={row.readOnly} data-testid={`vlayers-readonly-${row.tid}`}>
          <Lock className="size-3" aria-label={row.readOnly} />
        </span>
      )}
      {row.defines && (
        <button
          type="button"
          aria-label="Open its definition"
          title={`Open ${row.name} (${row.defines})`}
          className="flex size-4 shrink-0 items-center justify-center rounded-chip text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
          onClick={(e) => {
            e.stopPropagation()
            onOpen()
          }}
          data-testid={`vlayers-open-${row.tid}`}
        >
          <ArrowUpRight className="size-3" />
        </button>
      )}
      {row.type !== 'group' && (
        <button
          type="button"
          aria-label={hidden ? 'Show' : 'Hide'}
          aria-disabled={!row.hide.ok || undefined}
          title={row.hide.ok ? (hidden ? 'Show' : 'Hide') : row.hide.reason}
          className={cn(
            'flex size-4 shrink-0 items-center justify-center rounded-chip text-muted-foreground hover:text-foreground focus-visible:opacity-100',
            !hidden && 'opacity-0 group-hover:opacity-100',
            !row.hide.ok && 'group-hover:opacity-40',
          )}
          onClick={(e) => {
            e.stopPropagation()
            onHide()
          }}
          data-testid={`vlayers-hide-${row.tid}`}
        >
          {hidden ? <EyeOff className="size-3" /> : <Eye className="size-3" />}
        </button>
      )}
    </div>
  )
}

export function LayersPanel({ ve }: { ve: VisualEdit }) {
  const files = useLayerFiles()
  const screens = useMemo(() => screenFiles(files), [files])
  // the tree opens on the selection's file, else the AppShell screen the preview shows
  const [screen, setScreen] = useState<string | null>(ve.selection?.file ?? ve.screen?.file ?? null)
  const shownFile = ve.screen?.file ?? null
  const [shown, setShown] = useState(shownFile)
  if (shown !== shownFile) {
    setShown(shownFile)
    if (shownFile && !ve.selection) setScreen(shownFile)
  }
  // a new selection in another file (a preview click) switches the tree to that file
  const selFile = ve.selection?.file ?? null
  const selId = ve.selection ? `${ve.selection.file}#${ve.selection.key}` : null
  const [followed, setFollowed] = useState(selId)
  if (followed !== selId) {
    setFollowed(selId)
    if (selFile) setScreen(selFile)
  }
  const file = screen && screens.includes(screen) ? screen : (screens[0] ?? null)
  const tree = useMemo(() => (file ? layerTree(files, file) : null), [files, file])
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set())
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const selectedId = tree ? rowFor(tree, ve.selection) : null

  // a new selection (from the preview or the tree) expands its ancestors and becomes the keyboard cursor
  const [revealed, setRevealed] = useState<string | null>(null)
  if (revealed !== selectedId) {
    setRevealed(selectedId)
    setCursor(null)
    if (tree && selectedId) {
      const up = pathTo(tree, selectedId).slice(0, -1)
      if (up.some((id) => collapsed.has(id))) {
        const next = new Set(collapsed)
        for (const id of up) next.delete(id)
        setCollapsed(next)
      }
    }
  }
  const active = cursor ?? selectedId
  const rows = useMemo(() => (tree ? visibleRows(tree, collapsed, filter) : []), [tree, collapsed, filter])

  // virtual scrolling: only the rows in view (plus a few) are rendered
  const scroller = useRef<HTMLDivElement>(null)
  const [port, setPort] = useState({ top: 0, height: 480 })
  useEffect(() => {
    const el = scroller.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setPort({ top: el.scrollTop, height: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const reveal = (id: string | null) => {
    const el = scroller.current
    const i = id ? rows.findIndex((r) => r.id === id) : -1
    if (!el || i < 0) return
    const top = i * ROW
    if (top < el.scrollTop) el.scrollTop = top
    else if (top + ROW > el.scrollTop + el.clientHeight) el.scrollTop = top + ROW - el.clientHeight
  }
  // scroll the selection into view when it changes (and once its ancestors are expanded)
  const selIndex = rows.findIndex((r) => r.id === selectedId)
  useEffect(() => {
    const el = scroller.current
    if (!el || selIndex < 0) return
    const top = selIndex * ROW
    if (top < el.scrollTop || top + ROW > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, top - el.clientHeight / 2)
  }, [selectedId, selIndex])

  // dragging rows
  const [dragId, setDragId] = useState<string | null>(null)
  const [over, setOver] = useState<Over | null>(null)
  const verdicts = useRef(new Map<string, Over & { run?: () => string | null }>())
  // the dragged row may scroll out of the rendered window (and miss its own dragend): end the drag on any drop / dragend
  useEffect(() => {
    if (!dragId) return
    const end = () => {
      setDragId(null)
      setOver(null)
    }
    window.addEventListener('dragend', end)
    window.addEventListener('drop', end)
    return () => {
      window.removeEventListener('dragend', end)
      window.removeEventListener('drop', end)
    }
  }, [dragId])

  const toggle = (id: string) => {
    const next = new Set(collapsed)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setCollapsed(next)
  }
  const choose = (row: LayerRow) => {
    setError(null)
    const sel = selectionOfRow(row)
    if (sel) ve.select(sel)
    else toggle(row.id)
  }
  const hide = (row: LayerRow) => {
    const h = hideOps(row)
    setError('reason' in h ? h.reason : editRow(ve, files, h.sel, h.ops, false))
  }
  const remove = (row: LayerRow) => {
    if (row.type !== 'element') return setError(row.type === 'item' ? 'Remove list items in the Properties tab' : 'Select an element to delete')
    if (row.readOnly) return setError(row.readOnly)
    setError(editRow(ve, files, { file: row.file, key: row.key, part: null }, [{ op: 'delete' }], true))
  }
  const openDefinition = (row: LayerRow) => {
    if (!row.defines) return
    setScreen(row.defines)
    setFilter('')
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!tree || !rows.length) return
    const i = rows.findIndex((r) => r.id === active)
    const row = i >= 0 ? rows[i] : null
    const go = (r: LayerRow | undefined) => {
      if (!r) return
      e.preventDefault()
      const sel = selectionOfRow(r)
      if (sel) ve.select(sel)
      setCursor(r.id)
      reveal(r.id)
    }
    switch (e.key) {
      case 'ArrowDown':
        return go(rows[i + 1] ?? (i < 0 ? rows[0] : undefined))
      case 'ArrowUp':
        return go(i > 0 ? rows[i - 1] : undefined)
      case 'ArrowRight':
        if (!row || !row.children.length) return
        e.preventDefault()
        if (collapsed.has(row.id)) toggle(row.id)
        else go(tree.rows.get(row.children[0]))
        return
      case 'ArrowLeft':
        if (!row) return
        e.preventDefault()
        if (row.children.length && !collapsed.has(row.id)) toggle(row.id)
        else go(row.parent ? tree.rows.get(row.parent) : undefined)
        return
      case 'Enter':
        if (!row) return
        e.preventDefault()
        choose(row)
        return
      case 'Delete':
      case 'Backspace':
        if (!row) return
        e.preventDefault()
        remove(row)
        return
    }
  }

  const dragFor = (row: LayerRow) => ({
    start: (e: React.DragEvent) => {
      const why = row.readOnly ?? (row.slot ? null : 'Top element of its component — select an inner element, or move it where the component is used')
      if (ve.locked || why) {
        e.preventDefault()
        setError(ve.locked ? 'The chat is editing the app; visual edits wait until it finishes' : why)
        return
      }
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/plain', row.id)
      verdicts.current.clear()
      setError(null)
      setDragId(row.id)
    },
    over: (e: React.DragEvent) => {
      const drag = dragId && tree?.rows.get(dragId)
      if (!drag || !tree) return
      const zone = zoneAt(e, row)
      const k = `${row.id}|${zone}`
      let v = verdicts.current.get(k)
      if (!v) {
        const plan = dropPlan(tree, drag, row, zone)
        const why = plan.ok ? wouldRefuse(files, plan.sel, plan.ops) : plan.reason
        v = { id: row.id, zone, ok: !why, note: why ?? `Move ${drag.name} ${plan.ok ? plan.label : ''}`.trim(), run: plan.ok && !why ? () => editRow(ve, files, plan.sel, plan.ops, true) : undefined }
        verdicts.current.set(k, v)
      }
      if (v.ok) {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
      }
      if (over?.id !== v.id || over.zone !== v.zone) setOver({ id: v.id, zone: v.zone, ok: v.ok, note: v.note })
    },
    drop: (e: React.DragEvent) => {
      e.preventDefault()
      const v = over && verdicts.current.get(`${over.id}|${over.zone}`)
      setDragId(null)
      setOver(null)
      if (v?.run) setError(v.run())
      else if (v) setError(v.note)
    },
    end: () => {
      setDragId(null)
      setOver(null)
    },
  })

  const first = Math.max(0, Math.floor(port.top / ROW) - OVERSCAN)
  const last = Math.min(rows.length, Math.ceil((port.top + port.height) / ROW) + OVERSCAN)

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="vlayers-panel">
      <div className="flex shrink-0 flex-col gap-1.5 p-2.5 pb-2">
        <Select value={file ?? ''} onValueChange={(v) => setScreen(v as string)} items={Object.fromEntries(screens.map((s) => [s, screenLabel(s)]))}>
          <SelectTrigger size="sm" className="w-full font-mono text-[11.5px]" aria-label="Screen or file" data-testid="vlayers-screen">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {screens.map((s) => (
              <SelectItem key={s} value={s}>
                {screenLabel(s)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-7 pl-6 text-[12.5px]"
            value={filter}
            placeholder="Filter layers"
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setFilter('')
              if (e.key === 'Enter') {
                const hit = rows.find((r) => r.type !== 'group')
                if (hit) choose(hit)
              }
            }}
            data-testid="vlayers-filter"
          />
        </div>
        {error && (
          <p className="text-[11.5px] text-destructive" data-testid="vlayers-error">
            {error}
          </p>
        )}
      </div>
      <div
        ref={scroller}
        role="tree"
        aria-label="Layers"
        tabIndex={0}
        className="relative min-h-0 flex-1 overflow-y-auto outline-none focus-visible:shadow-focus"
        onScroll={(e) => setPort({ top: e.currentTarget.scrollTop, height: e.currentTarget.clientHeight })}
        onKeyDown={onKeyDown}
        onMouseLeave={() => hoverPreview(ve, null)}
        data-testid="vlayers-tree"
      >
        {!tree || !rows.length ? (
          <p className="p-3 text-[12px] text-muted-foreground">{filter ? 'No layer matches.' : 'No elements in this file.'}</p>
        ) : (
          <div className="relative" style={{ height: rows.length * ROW }}>
            <div className="absolute inset-x-0" style={{ top: first * ROW }}>
              {rows.slice(first, last).map((row) => (
                <RowView
                  key={row.id}
                  row={row}
                  tree={tree}
                  selected={row.id === selectedId}
                  active={row.id === active}
                  collapsed={!filter && collapsed.has(row.id)}
                  over={over && over.id === row.id ? over : null}
                  dragging={row.id === dragId}
                  onToggle={() => toggle(row.id)}
                  onClick={() => {
                    setCursor(row.id)
                    choose(row)
                  }}
                  onHover={() => hoverPreview(ve, row.vsrc)}
                  onHide={() => hide(row)}
                  onOpen={() => openDefinition(row)}
                  drag={dragFor(row)}
                />
              ))}
            </div>
          </div>
        )}
      </div>
      {dragId && over && (
        <p className={cn('shrink-0 px-2.5 py-1.5 text-[11.5px] shadow-[inset_0_1px_0_var(--hairline)]', over.ok ? 'text-muted-foreground' : 'text-destructive')} data-testid="vlayers-drop-reason">
          {over.note}
        </p>
      )}
      {!dragId && (
        <p className="shrink-0 px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-[inset_0_1px_0_var(--hairline)]">
          {rows.length} layers · drag to move · ↑↓ select · Delete removes
        </p>
      )}
    </div>
  )
}

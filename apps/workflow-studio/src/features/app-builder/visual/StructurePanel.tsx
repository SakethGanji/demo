/**
 * Structure controls for the selected element (duplicate, delete, wrap, unwrap) and the component palette: the registry's
 * components grouped by category, searchable, inserted before / after / inside the selection with a click, or dragged
 * into the preview (the preview draws the drop line and the target container). Every action is one undo step.
 */
import { useMemo, useRef, useState } from 'react'
import { Copy, Plus, Search, Trash2, Ungroup, X } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Segmented } from '@/shared/components/ui/segmented'
import { Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import type { InsertWhere, PaletteItem, WrapWith } from './structure'
import type { VisualEdit } from './useVisualEdit'
import { AskFix } from './AskFix'

export const WRAPS: WrapWith[] = ['Stack', 'Grid', 'Box', 'Card']

function Palette({ ve, where, setWhere, disabled, onClose }: { ve: VisualEdit; where: InsertWhere; setWhere: (w: InsertWhere) => void; disabled: boolean; onClose: () => void }) {
  const [q, setQ] = useState('')
  const drag = useRef<{ name: string; x: number; y: number; active: boolean } | null>(null)
  const suppressClick = useRef(false)
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const hits = ve.palette.filter((p) => !needle || p.name.toLowerCase().includes(needle) || (p.description ?? '').toLowerCase().includes(needle) || p.category.includes(needle))
    const out = new Map<string, PaletteItem[]>()
    for (const p of hits) out.set(p.category, [...(out.get(p.category) ?? []), p])
    return [...out]
  }, [ve.palette, q])

  const onPointerDown = (e: React.PointerEvent, item: PaletteItem) => {
    if (disabled || item.disabled || e.button !== 0) return
    drag.current = { name: item.name, x: e.clientX, y: e.clientY, active: false }
    const move = (ev: PointerEvent) => {
      const d = drag.current
      if (!d) return
      if (!d.active) {
        if (Math.abs(ev.clientX - d.x) + Math.abs(ev.clientY - d.y) < 5) return
        d.active = true
        document.documentElement.style.cursor = 'grabbing'
        ve.paletteDrag.start(d.name)
      }
      ve.paletteDrag.move(ev.clientX, ev.clientY)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      const d = drag.current
      drag.current = null
      if (d?.active) {
        document.documentElement.style.cursor = ''
        suppressClick.current = true
        setTimeout(() => (suppressClick.current = false), 0)
        ve.paletteDrag.end(true)
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <Well className="flex flex-col gap-2 p-2.5" data-testid="vedit-palette">
      <div className="flex items-center gap-1.5">
        <span className="text-[11.5px] font-medium">Add a component</span>
        <div className="flex-1" />
        <Button variant="ghost" size="icon-xs" aria-label="Close the palette" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
        <Input className="h-7 pl-6 text-[12.5px]" value={q} placeholder="Search components" onChange={(e) => setQ(e.target.value)} data-testid="vedit-palette-search" />
      </div>
      <Segmented<InsertWhere>
        size="xs"
        aria-label="Where to insert"
        value={where}
        onChange={setWhere}
        options={[
          { key: 'before', label: 'Before', testId: 'vedit-where-before' },
          { key: 'after', label: 'After', testId: 'vedit-where-after' },
          { key: 'inside', label: 'Inside', testId: 'vedit-where-inside' },
        ]}
      />
      <p className="text-[11px] text-muted-foreground">{ve.selection ? 'Click to insert next to the selection, or drag into the preview.' : 'Drag into the preview, or select an element first.'}</p>
      <div className="flex max-h-72 flex-col gap-2 overflow-y-auto" data-testid="vedit-palette-list">
        {groups.length === 0 && <p className="text-[11.5px] text-muted-foreground">No component matches.</p>}
        {groups.map(([cat, items]) => (
          <div key={cat} className="flex flex-col gap-0.5">
            <span className="px-1 text-[10.5px] font-medium text-muted-foreground capitalize">{cat}</span>
            {items.map((p) => (
              <button
                key={`${p.file}#${p.name}`}
                type="button"
                className={cn('flex min-w-0 touch-none flex-col items-start rounded-chip px-1.5 py-1 text-left hover:bg-raise disabled:opacity-50', !p.disabled && 'cursor-grab')}
                title={p.disabled ?? p.snippet}
                disabled={disabled || !!p.disabled || undefined}
                onPointerDown={(e) => onPointerDown(e, p)}
                onClick={() => {
                  if (suppressClick.current) return
                  ve.insert(p.name, where)
                }}
                data-testid={`vedit-palette-item-${p.name}`}
              >
                <span className="font-mono text-[12px]">{p.name}</span>
                {(p.disabled ?? p.description) && <span className="line-clamp-1 text-[11px] text-muted-foreground">{p.disabled ?? p.description}</span>}
              </button>
            ))}
          </div>
        ))}
      </div>
    </Well>
  )
}

export function StructurePanel({ ve, disabled }: { ve: VisualEdit; disabled: boolean }) {
  const [open, setOpen] = useState(false)
  const [where, setWhere] = useState<InsertWhere>('after')
  const has = !!ve.view
  return (
    <div className="flex flex-col gap-2" data-testid="vedit-structure">
      <div className="flex flex-wrap items-center gap-1">
        <Button variant={open ? 'secondary' : 'outline'} size="xs" onClick={() => setOpen(!open)} aria-pressed={open} data-testid="vedit-add">
          <Plus /> Add
        </Button>
        {has && (
          <>
            <Button variant="outline" size="xs" disabled={disabled} onClick={() => ve.duplicate()} title="Duplicate (⌘D / Ctrl+D)" data-testid="vedit-duplicate">
              <Copy /> Duplicate
            </Button>
            <Button variant="outline" size="xs" disabled={disabled} onClick={() => ve.remove()} title="Delete (Delete / Backspace)" data-testid="vedit-delete">
              <Trash2 /> Delete
            </Button>
          </>
        )}
      </div>
      {has && (
        <div className="flex flex-wrap items-center gap-1 text-[11.5px] text-muted-foreground">
          <span className="mr-0.5">Wrap in</span>
          {WRAPS.map((w) => (
            <Button key={w} variant="ghost" size="xs" disabled={disabled} onClick={() => ve.wrap(w)} data-testid={`vedit-wrap-${w}`}>
              {w}
            </Button>
          ))}
          <Button variant="ghost" size="xs" disabled={disabled} onClick={() => ve.unwrap()} title="Replace it with its only child" data-testid="vedit-unwrap">
            <Ungroup /> Unwrap
          </Button>
        </div>
      )}
      {ve.notice && (
        <p className="text-[11.5px] text-destructive" data-testid="vedit-struct-error">
          {ve.notice}
        </p>
      )}
      {ve.notice && <AskFix view={ve.view} about="structure" />}
      {open && <Palette ve={ve} where={where} setWhere={setWhere} disabled={disabled} onClose={() => setOpen(false)} />}
    </div>
  )
}

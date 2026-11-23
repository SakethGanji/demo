import { useMemo, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'

export interface PaletteItem {
  id: string
  label: string
  hint?: React.ReactNode
  group?: string
  icon?: React.ReactNode
  /** Extra words to match (`sql select`). */
  keywords?: string
  disabled?: boolean
  /** Why it is disabled, shown as the hint. */
  reason?: string
  onSelect: () => void
}

export interface PaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: PaletteItem[]
  placeholder?: string
  /** Shown when nothing matches. */
  empty?: React.ReactNode
  /** Where focus goes when the palette closes (Base UI Dialog.Popup finalFocus); default: back to the opener. */
  finalFocus?: () => HTMLElement | null | false
  'data-testid'?: string
}

export function filterPalette(items: PaletteItem[], q: string): PaletteItem[] {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return items
  return items.filter((it) => {
    const hay = `${it.label} ${it.group ?? ''} ${it.keywords ?? ''}`.toLowerCase()
    return words.every((w) => hay.includes(w))
  })
}

/** ⌘K list over Base UI Dialog: type to filter, ↑/↓ to move, Enter to pick. */
export function Palette({ open, onOpenChange, items, placeholder = 'Type a command…', empty = 'Nothing matches.', finalFocus, ...rest }: PaletteProps) {
  const [q, setQ] = useState('')
  const [rawActive, setActive] = useState(0)
  const list = useMemo(() => filterPalette(items, q), [items, q])
  // derived, not reset in an effect: an Enter typed right after the filter changes must see the new list
  const active = list[rawActive] && !list[rawActive].disabled ? rawActive : Math.max(0, list.findIndex((it) => !it.disabled))
  // reset while rendering the open (not in an effect after paint), so a reopened palette never shows the last filter
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setQ('')
      setActive(0)
    }
  }

  const pick = (it: PaletteItem | undefined) => {
    if (!it || it.disabled) return
    onOpenChange(false)
    it.onSelect()
  }
  const step = (dir: 1 | -1) => {
    if (list.length === 0) return
    let i = active
    for (let n = 0; n < list.length; n++) {
      i = (i + dir + list.length) % list.length
      if (!list[i].disabled) break
    }
    setActive(i)
  }

  let lastGroup: string | undefined
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="gl-scrim" />
        <Dialog.Popup className="gl-overlay gl-palette" data-testid={rest['data-testid']} aria-label="Command palette" finalFocus={finalFocus ? () => finalFocus() ?? true : undefined}>
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <input
            autoFocus
            value={q}
            placeholder={placeholder}
            role="combobox"
            aria-expanded="true"
            aria-controls="gl-palette-list"
            aria-activedescendant={list[active] ? `gl-pal-${list[active].id}` : undefined}
            onChange={(e) => {
              setQ(e.target.value)
              setActive(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') (e.preventDefault(), step(1))
              else if (e.key === 'ArrowUp') (e.preventDefault(), step(-1))
              else if (e.key === 'Enter') (e.preventDefault(), pick(list[active]))
            }}
          />
          <ul id="gl-palette-list" role="listbox" aria-label="Commands">
            {list.map((it, i) => {
              const head = it.group && it.group !== lastGroup ? it.group : undefined
              lastGroup = it.group
              return (
                <li key={it.id} role="presentation">
                  {head && <div className="gl-pal-group" role="presentation">{head}</div>}
                  <div
                    id={`gl-pal-${it.id}`}
                    role="option"
                    aria-selected={i === active}
                    aria-disabled={it.disabled || undefined}
                    onMouseMove={() => setActive(i)}
                    onClick={() => pick(it)}
                  >
                    {it.icon}
                    <span>{it.label}</span>
                    {(it.disabled ? it.reason : it.hint) && <span className="gl-hint">{it.disabled ? it.reason : it.hint}</span>}
                  </div>
                </li>
              )
            })}
          </ul>
          {list.length === 0 && <div className="gl-empty">{empty}</div>}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

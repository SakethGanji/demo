import { useEffect, useMemo, useState } from 'react'
import { RUNS } from '@/shared/data/runs'
import { useApi } from '@/shared/api/mode'
import { TOOLS, liveMeta } from '@/shared/lib/tools'
import { Icon } from '@/shared/primitives/Icon'
import { TITLES, type Route } from '../routes'

export function CommandPalette({ onClose, go }: { onClose: () => void; go: (r: string) => void }) {
  const { mode, status } = useApi()
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const items = useMemo(() => {
    const all: { group: string; label: string; hint: string; mono?: boolean; run: () => void }[] = [
      ...(Object.keys(TITLES) as Route[]).map((r) => ({ group: 'Go To', label: TITLES[r], hint: 'Page', run: () => go(r) })),
      ...TOOLS.map((t) => ({ group: 'Tools', label: `${t.verb} · ${t.name}`, hint: mode === 'live' ? liveMeta(t, status) : t.meta, run: () => go(t.route) })),
      ...RUNS.map((r) => ({ group: 'Runs', label: r.id, hint: `${r.pipeline} · ${r.status}`, mono: true, run: () => go('experiments') })),
    ]
    return all.filter((i) => `${i.label} ${i.hint}`.toLowerCase().includes(q.toLowerCase()))
  }, [q])
  // hand focus back to whatever opened the palette
  useEffect(() => { const opener = document.activeElement as HTMLElement | null; return () => opener?.focus?.() }, [])
  const pick = (i: number) => { items[i]?.run(); onClose() }
  return (
    <div className="palette-scrim" onClick={onClose}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette" onClick={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Icon.search width={16} height={16} />
          <input autoFocus placeholder="Search runs, models, datasets, or jump to a page" value={q}
            onChange={(e) => { setQ(e.target.value); setIdx(0) }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)) }
              if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)) }
              if (e.key === 'Enter') pick(idx)
              if (e.key === 'Tab') e.preventDefault() // keep focus in the dialog; arrows move, Enter picks
            }} />
          <kbd className="mono" style={{ fontSize: 11 }}>esc</kbd>
        </div>
        <div className="palette-list">
          {items.length === 0 && <p className="helper" style={{ padding: 16 }}>No matches for “{q}”.</p>}
          {items.map((it, i) => (
            <div key={`${it.group}-${it.label}`}>
              {(i === 0 || items[i - 1].group !== it.group) && <div className="palette-group micro-label">{it.group}</div>}
              <button ref={i === idx ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined} tabIndex={-1} className={`palette-item ${i === idx ? 'on' : ''}`} onMouseEnter={() => setIdx(i)} onClick={() => pick(i)}>
                <span className={it.mono ? 'mono' : ''} style={it.mono ? { fontSize: 12.5 } : undefined}>{it.label}</span><span className="hint">{it.hint}</span>
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

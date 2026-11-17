import { useCallback, useRef, useState, type ReactNode } from 'react'

export type FretProps = {
  sym: string
  name: string
  help: string
  min: number
  max: number
  value: number
  onChange: (v: number) => void
  log?: boolean
  step?: number
  ticks: number[]
  tickLabel?: (v: number) => string
  format?: (v: number) => string
  domain: ReactNode
  guided: boolean
}

export function FretSlider(p: FretProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState(false)
  const [draft, setDraft] = useState<string | null>(null)
  const fmt = p.format ?? ((v: number) => String(v))
  const toT = (v: number) => (p.log ? (Math.log10(v) - Math.log10(p.min)) / (Math.log10(p.max) - Math.log10(p.min)) : (v - p.min) / (p.max - p.min))
  const fromT = (t: number) => {
    let v = p.log ? 10 ** (Math.log10(p.min) + t * (Math.log10(p.max) - Math.log10(p.min))) : p.min + t * (p.max - p.min)
    if (p.step) v = Math.round(v / p.step) * p.step
    else if (p.log) v = Number(v.toPrecision(2))
    return Math.min(p.max, Math.max(p.min, v))
  }
  const pick = useCallback((clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    p.onChange(fromT(Math.min(1, Math.max(0, (clientX - r.left) / r.width))))
  }, [p])
  const t = toT(p.value)
  const onKey = (e: React.KeyboardEvent) => {
    const d = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 0.02 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -0.02 : 0
    if (d) { e.preventDefault(); p.onChange(fromT(Math.min(1, Math.max(0, t + d)))) }
  }
  return (
    <div className="param">
      <div className="label">
        {p.guided ? (
          <><b>{p.name} <span className="math">{p.sym}</span></b><small>{p.help}</small></>
        ) : (
          <><b><span className="math">{p.sym}</span> <span className="micro-label">{p.name}</span></b><small className="math" style={{ fontSize: 13 }}>{p.domain}</small></>
        )}
      </div>
      <div
        ref={ref}
        className={`fret ${drag ? 'drag' : ''}`}
        role="slider" tabIndex={0} aria-label={p.name} aria-valuemin={p.min} aria-valuemax={p.max} aria-valuenow={p.value}
        onKeyDown={onKey}
        onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); setDrag(true); pick(e.clientX) }}
        onPointerMove={(e) => drag && pick(e.clientX)}
        onPointerUp={() => setDrag(false)}
      >
        <div className="track" />
        <div className="fill" style={{ width: `${t * 100}%` }} />
        {p.ticks.map((v, i) => (
          <div key={v}>
            <div className="tick major" style={{ left: `${toT(v) * 100}%` }} />
            <div className="tick-label" style={{ left: `${toT(v) * 100}%`, transform: i === 0 ? 'none' : i === p.ticks.length - 1 ? 'translateX(-100%)' : undefined }}>{(p.tickLabel ?? String)(v)}</div>
          </div>
        ))}
        {Array.from({ length: 21 }, (_, i) => <div key={`f${i}`} className="tick" style={{ left: `${i * 5}%` }} />)}
        <div className="thumb" style={{ left: `${t * 100}%` }} />
      </div>
      <input
        className="num-input" aria-label={`${p.name} value`}
        value={draft ?? fmt(p.value)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { const v = Number(draft); if (draft !== null && !Number.isNaN(v)) p.onChange(Math.min(p.max, Math.max(p.min, v))); setDraft(null) }}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
    </div>
  )
}

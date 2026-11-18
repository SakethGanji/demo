import { useMemo, useRef, useState, type ReactNode } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'

/** One series on real axes. `step` draws a staircase (curves from thresholds), `dots` marks every point. */
export type XYSeries = {
  name: string; color: string; dash?: string; points: [number, number][]
  step?: 'before' | 'after'; width?: number; dots?: boolean; dotR?: (i: number) => number; area?: boolean; opacity?: number
  /** Extra hover lines for point i (e.g. the threshold that produced it). */
  tip?: (i: number) => string
}

export type Axis = { min: number; max: number; ticks: number[]; title: string; fmt?: (v: number) => string }

type Props = {
  series: XYSeries[]
  x: Axis
  y: Axis
  height?: number
  /** Dashed y = x (ROC chance, perfect calibration). */
  diagonal?: { label: string }
  /** Vertical or horizontal reference lines. */
  refs?: { x?: number; y?: number; label: string; color?: string; dash?: string; strong?: boolean }[]
  /** Labelled points (the operating point). */
  marks?: { x: number; y: number; label: string; color?: string }[]
  /** Bars behind the series, e.g. a histogram: [x0, x1, height in y units]. */
  bars?: { x0: number; x1: number; y: number; color: string; opacity?: number }[]
  onPickX?: (x: number) => void
  ariaLabel?: string
  children?: (sx: (v: number) => number, sy: (v: number) => number) => ReactNode
  testId?: string
}

/** A chart on real-valued axes in the PlotFrame style: string axes with fret ticks, an origin node, hover that
 *  snaps to the nearest point of each series and reads its exact values. */
export function XYPlot({ series, x, y, height = 240, diagonal, refs, marks, bars, onPickX, ariaLabel, children, testId }: Props) {
  const [wrap, W] = useWidth<HTMLDivElement>()
  const svg = useRef<SVGSVGElement>(null)
  const H = height, L = 48, R = 16, T = 12, B = 30
  const sx = (v: number) => L + ((v - x.min) / (x.max - x.min || 1)) * (W - L - R)
  const sy = (v: number) => T + (1 - (v - y.min) / (y.max - y.min || 1)) * (H - T - B)
  const fx = x.fmt ?? ((v: number) => v.toFixed(2))
  const fy = y.fmt ?? ((v: number) => v.toFixed(2))
  const [hx, setHx] = useState<number | null>(null)

  const paths = useMemo(() => series.map((s) => {
    let d = ''
    s.points.forEach(([a, b], i) => {
      const X = sx(a).toFixed(1), Y = sy(b).toFixed(1)
      if (i === 0) d += `M${X},${Y}`
      else if (s.step === 'after') d += `H${X}V${Y}`
      else if (s.step === 'before') d += `V${Y}H${X}`
      else d += `L${X},${Y}`
    })
    return d
  }), [series, W, x.min, x.max, y.min, y.max]) // eslint-disable-line react-hooks/exhaustive-deps

  const nearest = (s: XYSeries, v: number) => {
    let bi = -1, bd = Infinity
    s.points.forEach(([a], i) => { const d = Math.abs(a - v); if (d < bd) { bd = d; bi = i } })
    return bi
  }
  const toX = (clientX: number) => {
    const r = svg.current!.getBoundingClientRect()
    const px = ((clientX - r.left) / r.width) * W
    return x.min + ((px - L) / (W - L - R)) * (x.max - x.min)
  }
  const onMove = (e: React.PointerEvent) => {
    const v = toX(e.clientX)
    setHx(v < x.min || v > x.max ? null : v)
  }
  const hov = hx === null ? [] : series.filter((s) => s.points.length && s.name).map((s) => ({ s, i: nearest(s, hx) })).filter((h) => h.i >= 0)

  return (
    <div ref={wrap} style={{ position: 'relative' }} data-testid={testId}>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={ariaLabel ?? `${y.title} against ${x.title}`}
        onPointerMove={onMove} onPointerLeave={() => setHx(null)} onClick={onPickX ? (e) => { const v = toX(e.clientX); if (v >= x.min && v <= x.max) onPickX(v) } : undefined}
        style={{ cursor: onPickX ? 'crosshair' : undefined }}>
        {y.ticks.map((t) => (
          <g key={`y${t}`}>
            <line x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} stroke="var(--plot-grid)" />
            <line x1={L - 4} x2={L} y1={sy(t)} y2={sy(t)} stroke="var(--plot-axis)" />
            <text className="tick-text" x={L - 8} y={sy(t) + 3.5} textAnchor="end">{fy(t)}</text>
          </g>
        ))}
        {x.ticks.map((t) => (
          <g key={`x${t}`}>
            <line x1={sx(t)} x2={sx(t)} y1={H - B} y2={H - B + 4} stroke="var(--plot-axis)" />
            <text className="tick-text" x={sx(t)} y={H - B + 16} textAnchor="middle">{fx(t)}</text>
          </g>
        ))}
        {bars?.map((b, i) => <rect key={`b${i}`} x={sx(b.x0) + 0.5} width={Math.max(0.5, sx(b.x1) - sx(b.x0) - 1)} y={sy(b.y)} height={Math.max(0, sy(y.min) - sy(b.y))} fill={b.color} opacity={b.opacity ?? 0.8} rx={1} />)}
        <line x1={L} x2={W - R + 6} y1={H - B} y2={H - B} stroke="var(--plot-axis)" />
        <line x1={L} x2={L} y1={T - 4} y2={H - B} stroke="var(--plot-axis)" />
        <circle cx={L} cy={H - B} r={3} fill="var(--plot-axis)" />
        <text className="axis-title" x={W - R} y={H - B - 6} textAnchor="end">{x.title}</text>
        <text className="axis-title" x={L + 6} y={T + 4}>{y.title}</text>
        {diagonal && (
          <g>
            <line x1={sx(Math.max(x.min, y.min))} y1={sy(Math.max(x.min, y.min))} x2={sx(Math.min(x.max, y.max))} y2={sy(Math.min(x.max, y.max))} stroke="var(--text-muted)" strokeDasharray="4 4" />
            <text className="tick-text" x={sx(Math.min(x.max, y.max)) - 4} y={sy(Math.min(x.max, y.max)) + 14} textAnchor="end" style={{ fill: 'var(--text-muted)' }}>{diagonal.label}</text>
          </g>
        )}
        {refs?.map((r, i) => r.x !== undefined ? (
          <g key={`r${i}`}>
            <line x1={sx(r.x)} x2={sx(r.x)} y1={T} y2={H - B} stroke={r.color ?? 'var(--text-muted)'} strokeDasharray={r.dash ?? '4 4'} strokeWidth={r.strong ? 1.75 : 1} />
            <text className="tick-text" x={sx(r.x) + (sx(r.x) > W * 0.8 ? -4 : 4)} y={T + 9 + (i % 3) * 12} textAnchor={sx(r.x) > W * 0.8 ? 'end' : 'start'} style={{ fill: r.color ?? 'var(--text-muted)' }}>{r.label}</text>
          </g>
        ) : (
          <g key={`r${i}`}>
            <line x1={L} x2={W - R} y1={sy(r.y!)} y2={sy(r.y!)} stroke={r.color ?? 'var(--text-muted)'} strokeDasharray={r.dash ?? '4 4'} />
            <text className="tick-text" x={W - R - 4} y={sy(r.y!) - 5} textAnchor="end" style={{ fill: r.color ?? 'var(--text-muted)' }}>{r.label}</text>
          </g>
        ))}
        {series.map((s, k) => (
          <g key={s.name || k} opacity={s.opacity}>
            {s.area && s.points.length > 1 && <path d={`${paths[k]}V${sy(y.min)}H${sx(s.points[0][0])}Z`} fill={s.color} opacity={0.08} />}
            <path d={paths[k]} stroke={s.color} strokeWidth={s.width ?? 2} fill="none" strokeDasharray={s.dash} strokeLinejoin="round" />
            {s.dots && s.points.map(([a, b], i) => <circle key={i} cx={sx(a)} cy={sy(b)} r={s.dotR ? s.dotR(i) : 3} fill="var(--bg-surface)" stroke={s.color} strokeWidth={1.75} />)}
          </g>
        ))}
        {children?.(sx, sy)}
        {marks?.map((m, i) => (
          <g key={`m${i}`}>
            <circle cx={sx(m.x)} cy={sy(m.y)} r={7} fill={m.color ?? 'var(--accent-secondary)'} opacity={0.18} />
            <circle cx={sx(m.x)} cy={sy(m.y)} r={4} fill="var(--bg-surface)" stroke={m.color ?? 'var(--accent-secondary)'} strokeWidth={2.25} />
            <text className="tick-text" x={sx(m.x) + (sx(m.x) > W * 0.7 ? -10 : 10)} y={sy(m.y) + (sy(m.y) < T + 20 ? 16 : -8)} textAnchor={sx(m.x) > W * 0.7 ? 'end' : 'start'} style={{ fill: 'var(--text-primary)', fontWeight: 600 }}>{m.label}</text>
          </g>
        ))}
        {hx !== null && (
          <g pointerEvents="none">
            <line x1={sx(hx)} x2={sx(hx)} y1={T} y2={H - B} stroke="var(--text-muted)" strokeWidth={1} />
            {hov.map(({ s, i }) => <circle key={s.name} cx={sx(s.points[i][0])} cy={sy(s.points[i][1])} r={4} fill="var(--bg-surface)" stroke={s.color} strokeWidth={2} />)}
          </g>
        )}
      </svg>
      {hx !== null && hov.length > 0 && (
        <div className="tooltip" style={sx(hx) > W * 0.55 ? { right: W - sx(hx) + 12, top: 8 } : { left: sx(hx) + 12, top: 8 }}>
          {hov.map(({ s, i }) => (
            <div key={s.name}>
              <span style={{ color: s.color }}>●</span> {s.name}: <span className="t-muted">{x.title}</span> {fx(s.points[i][0])} · <span className="t-muted">{y.title}</span> {fy(s.points[i][1])}
              {s.tip && <span className="t-muted"> · {s.tip(i)}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Evenly spaced ticks covering [min, max] with a "nice" step. */
export function niceTicks(min: number, max: number, n = 5): number[] {
  if (!(max > min)) return [min]
  const raw = (max - min) / n
  const p = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)))
  return out
}

/** [min, max] padded so the data does not touch the frame. */
export function padDomain(vals: number[], pad = 0.06): [number, number] {
  const f = vals.filter(Number.isFinite)
  if (!f.length) return [0, 1]
  let lo = Math.min(...f), hi = Math.max(...f)
  if (lo === hi) { lo -= 1; hi += 1 }
  const d = (hi - lo) * pad
  return [lo - d, hi + d]
}

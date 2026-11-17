import { useEffect, useMemo, useRef, useState } from 'react'

export type Series = { name: string; color: string; dash?: string; data: number[] }

type LineChartProps = {
  series: Series[]
  xMax: number
  yMin: number
  yMax: number
  yTicks: number[]
  xTicks: number[]
  xTitle: string
  yTitle: string
  height?: number
  reference?: { y: number; label: string }
  fmtY?: (v: number) => string
  live?: boolean
  fmtX?: (v: number) => string
  marker?: { x: number; label: string }
}

/** Line plot: string axes with fret ticks, an origin node, crosshair hover with ringed dots. */
export function LineChart({ series, xMax, yMin, yMax, yTicks, xTicks, xTitle, yTitle, height = 220, reference, fmtY = (v) => v.toFixed(3), live, fmtX = String, marker }: LineChartProps) {
  const wrap = useRef<HTMLDivElement>(null)
  const [W, setW] = useState(640)
  useEffect(() => {
    const el = wrap.current!
    const ro = new ResizeObserver(() => setW(Math.max(280, el.clientWidth)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const H = height, L = 44, R = 28, T = 10, B = 28
  const sx = (x: number) => L + (x / xMax) * (W - L - R)
  const sy = (y: number) => T + (1 - (y - yMin) / (yMax - yMin)) * (H - T - B)
  const ref = useRef<SVGSVGElement>(null)
  const [hx, setHx] = useState<number | null>(null)
  const paths = useMemo(() => series.map((s) => s.data.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')), [series, xMax, yMin, yMax, W])
  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * W
    const i = Math.round(((x - L) / (W - L - R)) * xMax)
    const maxLen = Math.max(...series.map((s) => s.data.length)) - 1
    setHx(i < 0 || i > maxLen ? null : i)
  }
  return (
    <div ref={wrap} style={{ position: 'relative' }}>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} onPointerMove={onMove} onPointerLeave={() => setHx(null)} role="img" aria-label={`${yTitle} against ${xTitle}`}>
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} stroke="var(--plot-grid)" />
            <line x1={L - 4} x2={L} y1={sy(t)} y2={sy(t)} stroke="var(--plot-axis)" />
            <text className="tick-text" x={L - 8} y={sy(t) + 3.5} textAnchor="end">{fmtY(t)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={t}>
            <line x1={sx(t)} x2={sx(t)} y1={H - B} y2={H - B + 4} stroke="var(--plot-axis)" />
            <text className="tick-text" x={sx(t)} y={H - B + 16} textAnchor="middle">{fmtX(t)}</text>
          </g>
        ))}
        <line x1={L} x2={W - R + 8} y1={H - B} y2={H - B} stroke="var(--plot-axis)" />
        <line x1={L} x2={L} y1={T - 4} y2={H - B} stroke="var(--plot-axis)" />
        <circle cx={L} cy={H - B} r={3} fill="var(--plot-axis)" />
        <text className="axis-title" x={W - R + 12} y={H - B + 4}>{xTitle}</text>
        <text className="axis-title" x={L + 6} y={T + 2}>{yTitle}</text>
        {reference && (
          <g>
            <line x1={L} x2={W - R} y1={sy(reference.y)} y2={sy(reference.y)} stroke="var(--text-muted)" strokeDasharray="4 4" />
            <text className="tick-text" x={W - R} y={sy(reference.y) - 5} textAnchor="end" style={{ fill: 'var(--text-muted)' }}>{reference.label}</text>
          </g>
        )}
        {series.map((s, k) => (
          <path key={s.name} d={paths[k]} stroke={s.color} strokeWidth={2} fill="none" strokeDasharray={s.dash} strokeLinejoin="round" />
        ))}
        {live && series.map((s) => {
          const i = s.data.length - 1
          return (
            <g key={`live-${s.name}`}>
              <circle cx={sx(i)} cy={sy(s.data[i])} r={6} fill={s.color} opacity={0.18} className="live-dot" />
              <circle cx={sx(i)} cy={sy(s.data[i])} r={3} fill={s.color} />
            </g>
          )
        })}
        {marker && (
          <g style={{ transition: 'transform var(--duration-snap-max) var(--ease-pluck)', transform: `translateX(${sx(marker.x)}px)` }}>
            <line x1={0} x2={0} y1={T + 12} y2={H - B} stroke="var(--accent-primary)" strokeWidth={1.5} />
            <circle cx={0} cy={H - B} r={4} fill="var(--bg-surface)" stroke="var(--accent-primary)" strokeWidth={2} />
            <text className="tick-text" x={0} y={T + 8} textAnchor="middle" style={{ fill: 'var(--text-primary)' }}>{marker.label}</text>
          </g>
        )}
        {hx !== null && (
          <g>
            <line x1={sx(hx)} x2={sx(hx)} y1={T} y2={H - B} stroke="var(--text-muted)" strokeWidth={1} />
            {series.map((s) => s.data[hx] !== undefined && (
              <circle key={s.name} cx={sx(hx)} cy={sy(s.data[hx])} r={4} fill="var(--bg-surface)" stroke={s.color} strokeWidth={2} />
            ))}
          </g>
        )}
      </svg>
      {hx !== null && (
        <div className="tooltip" style={sx(hx) > W * 0.6 ? { right: W - sx(hx) + 12, top: 8 } : { left: sx(hx) + 12, top: 8 }}>
          <div className="t-muted">{xTitle} = {fmtX(hx)}</div>
          {series.map((s) => s.data[hx] !== undefined && (
            <div key={s.name}><span style={{ color: s.color }}>●</span> {s.name}: {fmtY(s.data[hx])}</div>
          ))}
        </div>
      )}
    </div>
  )
}

import { useMemo, useRef, useState, type ReactNode } from 'react'

export type Series = { name: string; color: string; dash?: string; data: number[] }

/** Every chart sits in a PlotFrame: header with sample context, plot, caption defining the quantity. */
export function PlotFrame({ title, chips, caption, legend, children, right }: { title: string; chips?: ReactNode; caption: ReactNode; legend?: Series[]; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="card plot-frame">
      <div className="card-head">
        <div className="left"><h3 className="card-title">{title}</h3>{right}</div>
        <div className="row">{chips}</div>
      </div>
      <div className="plot">{children}</div>
      <div className="caption">
        {legend?.map((s) => (
          <span key={s.name} className="legend">
            <svg width="22" height="8"><line x1="1" y1="4" x2="21" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} /></svg>
            {s.name}
          </span>
        ))}
        <span style={{ marginLeft: legend?.length ? 'auto' : undefined }}>{caption}</span>
      </div>
    </div>
  )
}

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
}

/** Line plot: string axes with fret ticks, an origin node, crosshair hover with ringed dots. */
export function LineChart({ series, xMax, yMin, yMax, yTicks, xTicks, xTitle, yTitle, height = 220, reference, fmtY = (v) => v.toFixed(3), live }: LineChartProps) {
  const W = 640, H = height, L = 44, R = 28, T = 10, B = 28
  const sx = (x: number) => L + (x / xMax) * (W - L - R)
  const sy = (y: number) => T + (1 - (y - yMin) / (yMax - yMin)) * (H - T - B)
  const ref = useRef<SVGSVGElement>(null)
  const [hx, setHx] = useState<number | null>(null)
  const paths = useMemo(() => series.map((s) => s.data.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')), [series, xMax, yMin, yMax])
  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * W
    const i = Math.round(((x - L) / (W - L - R)) * xMax)
    const maxLen = Math.max(...series.map((s) => s.data.length)) - 1
    setHx(i < 0 || i > maxLen ? null : i)
  }
  return (
    <div style={{ position: 'relative' }}>
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
            <text className="tick-text" x={sx(t)} y={H - B + 16} textAnchor="middle">{t}</text>
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
        <div className="tooltip" style={{ left: `calc(${(sx(hx) / W) * 100}% + 12px)`, top: 8 }}>
          <div className="t-muted">{xTitle} = {hx}</div>
          {series.map((s) => s.data[hx] !== undefined && (
            <div key={s.name}><span style={{ color: s.color }}>●</span> {s.name}: {fmtY(s.data[hx])}</div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Confusion matrix on the sequential heat ramp (share of the actual row). */
export function ConfusionMatrix({ labels, m }: { labels: string[]; m: number[][] }) {
  const [hover, setHover] = useState<{ r: number; c: number } | null>(null)
  const heat = (p: number) => (p >= 0.5 ? 'var(--heat-5)' : p >= 0.1 ? 'var(--heat-4)' : p >= 0.03 ? 'var(--heat-3)' : p >= 0.01 ? 'var(--heat-2)' : 'var(--heat-1)')
  return (
    <div style={{ position: 'relative' }}>
      <div className="cm" style={{ gridTemplateColumns: `86px repeat(${labels.length}, 1fr)` }}>
        <div className="lab" style={{ fontFamily: 'var(--font-math)', fontStyle: 'italic', fontSize: 14 }}>y \ ŷ</div>
        {labels.map((l) => <div key={l} className="lab top">{l}</div>)}
        {m.map((row, r) => {
          const tot = row.reduce((a, b) => a + b, 0)
          return [
            <div key={`l${r}`} className="lab">{labels[r]}</div>,
            ...row.map((v, c) => {
              const p = v / tot
              return (
                <div key={`${r}-${c}`} className={`cell ${p >= 0.5 ? 'dark' : ''}`} style={{ background: heat(p) }}
                  onPointerEnter={() => setHover({ r, c })} onPointerLeave={() => setHover(null)}>{v}</div>
              )
            }),
          ]
        })}
      </div>
      {hover && (
        <div className="tooltip" style={{ right: 0, top: -36 }}>
          <span className="t-muted">y=</span>{labels[hover.r]} <span className="t-muted">ŷ=</span>{labels[hover.c]} · {m[hover.r][hover.c]} · {((m[hover.r][hover.c] / m[hover.r].reduce((a, b) => a + b, 0)) * 100).toFixed(1)}%
        </div>
      )}
    </div>
  )
}

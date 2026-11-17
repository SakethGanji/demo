import { useState } from 'react'

export type PCAxis = { key: string; label: string; min: number; max: number; fmt: (v: number) => string }

/** Parallel coordinates: every run is one string plucked across the axes. */
export function StringPlot({ axes, rows, selected, onPick }: { axes: PCAxis[]; rows: { id: string; v: Record<string, number> }[]; selected: Set<string>; onPick?: (id: string) => void }) {
  const [hover, setHover] = useState<string | null>(null)
  const W = 900, H = 230, L = 50, R = 50, T = 30, B = 26
  const ax = (i: number) => L + (i / (axes.length - 1)) * (W - L - R)
  const ay = (a: PCAxis, v: number) => T + (1 - (v - a.min) / (a.max - a.min)) * (H - T - B)
  const order = [...rows].sort((a, b) => Number(selected.has(a.id)) - Number(selected.has(b.id)) || Number(a.id === hover) - Number(b.id === hover))
  const hr = rows.find((r) => r.id === hover)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Parallel coordinates of runs" onPointerLeave={() => setHover(null)}>
      {axes.map((a, i) => (
        <g key={a.key}>
          <line x1={ax(i)} x2={ax(i)} y1={T} y2={H - B} stroke="var(--plot-axis)" />
          {[0, 0.25, 0.5, 0.75, 1].map((f) => <line key={f} x1={ax(i) - 3} x2={ax(i) + 3} y1={T + f * (H - T - B)} y2={T + f * (H - T - B)} stroke="var(--plot-axis)" />)}
          <text className="axis-title" x={ax(i)} y={T - 12} textAnchor="middle">{a.label}</text>
          <text className="tick-text" x={ax(i) + 7} y={T + 4}>{a.fmt(a.max)}</text>
          <text className="tick-text" x={ax(i) + 7} y={H - B + 3}>{a.fmt(a.min)}</text>
        </g>
      ))}
      {order.map((r) => {
        const on = selected.has(r.id), h = r.id === hover
        const d = axes.map((a, i) => `${i ? 'L' : 'M'}${ax(i)},${ay(a, r.v[a.key]).toFixed(1)}`).join(' ')
        return (
          <g key={r.id} onPointerEnter={() => setHover(r.id)} onClick={() => onPick?.(r.id)}>
            <path d={d} stroke="transparent" strokeWidth={12} fill="none" style={{ cursor: 'pointer' }} />
            <path className="pc-line" d={d} fill="none" strokeLinejoin="round"
              stroke={h ? 'var(--accent-secondary)' : on ? 'var(--accent-primary)' : 'var(--border-track)'} strokeWidth={h ? 2.5 : on ? 2 : 1.25} strokeDasharray={on || h ? undefined : '4 3'} opacity={hover && !h ? 0.45 : 1} />
            {(on || h) && axes.map((a, i) => <circle key={a.key} cx={ax(i)} cy={ay(a, r.v[a.key])} r={3} fill={h ? 'var(--accent-secondary)' : 'var(--accent-primary)'} />)}
          </g>
        )
      })}
      {hr && axes.map((a, i) => (
        <g key={`t-${a.key}`} pointerEvents="none">
          <rect x={ax(i) - 26} y={ay(a, hr.v[a.key]) - 22} width={52} height={16} rx={3} fill="var(--bg-shell)" />
          <text className="tick-text" x={ax(i)} y={ay(a, hr.v[a.key]) - 10.5} textAnchor="middle" style={{ fill: 'var(--text-on-shell)' }}>{a.fmt(hr.v[a.key])}</text>
        </g>
      ))}
      {hr && <text className="tick-text" x={L} y={H - 4} style={{ fill: 'var(--text-primary)' }}>{hr.id}</text>}
    </svg>
  )
}

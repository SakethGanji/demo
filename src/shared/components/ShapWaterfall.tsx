import type { ShapContribution } from '@/shared/api/types'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore } from '@/shared/lib/metrics'

/** What a SHAP decomposition needs: f(x) = φ₀ + Σᵢ φᵢ. */
export type ShapLike = { base_value: number; fx: number; contributions: ShapContribution[]; output: 'probability' | 'prediction' }

const fv = (v: unknown) => (v == null || v === '' ? 'missing' : typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : fmtScore(v, 3)) : String(v).length > 28 ? `${String(v).slice(0, 26)}…` : String(v))
const fmt = (s: ShapLike) => (v: number) => (s.output === 'probability' ? v.toFixed(4) : fmtScore(v, 2))

/** The decomposition written out with the real terms: f(x) = φ₀ + Σᵢ φᵢ ⇒ 0.7159 = 0.0703 + 0.1324ₓ₁ + … */
export function ShapEquation({ s, terms = 6, testPrefix = 'shap' }: { s: ShapLike; terms?: number; testPrefix?: string }) {
  const d = fmt(s)
  const eq = s.contributions.slice(0, terms)
  const rest = s.contributions.slice(terms)
  const r = rest.reduce((a, c) => a + c.phi, 0)
  return (
    <div className="shap-eq" data-testid={`${testPrefix}-equation`}>
      <span className="math">f(x)</span> = <span className="math">φ₀</span> + Σᵢ <span className="math">φᵢ</span> &nbsp;⇒&nbsp;
      <b className="mono" data-testid={`${testPrefix}-fx`}>{d(s.fx)}</b> = <span className="mono" data-testid={`${testPrefix}-base`}>{d(s.base_value)}</span>
      {eq.map((c) => <span key={c.feature} className={`mono ${c.phi >= 0 ? 'up-e' : 'down-b'}`}> {c.phi >= 0 ? '+' : '−'} {d(Math.abs(c.phi))}<sub className="shap-sub">{c.feature}</sub></span>)}
      {rest.length > 0 && <span className="mono muted"> {r >= 0 ? '+' : '−'} {d(Math.abs(r))}<sub className="shap-sub">{rest.length} others</sub></span>}
    </div>
  )
}

/** The waterfall from φ₀ = E[f] through each feature's φᵢ (with the row's value) to f(x). */
export function ShapWaterfall({ s, testPrefix = 'shap', label = 230 }: { s: ShapLike; testPrefix?: string; label?: number }) {
  const [ref, W] = useWidth<HTMLDivElement>()
  const prob = s.output === 'probability'
  const d = fmt(s)
  let acc = s.base_value
  const bars = s.contributions.map((c) => { const from = acc; acc += c.phi; return { ...c, from, to: acc } })
  const all = [s.base_value, s.fx, ...bars.flatMap((b) => [b.from, b.to])]
  const lo0 = Math.min(...all), hi0 = Math.max(...all)
  const padv = (hi0 - lo0) * 0.08 || 0.01
  const lo = prob ? Math.max(0, lo0 - padv) : lo0 - padv, hi = prob ? Math.min(1, hi0 + padv) : hi0 + padv
  const LBL = label, R = 90, ROW = 22, TOP = 26
  const pw = Math.max(120, W - LBL - R)
  const sx = (v: number) => LBL + ((v - lo) / (hi - lo || 1)) * pw
  const H = TOP + (bars.length + 2) * ROW + 24
  return (
    <div ref={ref} data-testid={`${testPrefix}-waterfall`}>
      <svg width={W} height={H}>
        {[lo, (lo + hi) / 2, hi].map((v) => <g key={v}><line x1={sx(v)} x2={sx(v)} y1={TOP - 6} y2={H - 22} stroke="var(--plot-grid)" /><text className="tick-text" x={sx(v)} y={H - 6} textAnchor="middle">{d(v)}</text></g>)}
        <text className="axis-title" x={W - 4} y={H - 6} textAnchor="end">{prob ? 'p̂' : 'ŷ'}</text>
        <g>
          <text x={LBL - 10} y={TOP + 4} textAnchor="end" className="fi-name"><tspan className="math-t">φ₀ = E[f]</tspan></text>
          <line x1={sx(s.base_value)} x2={sx(s.base_value)} y1={TOP - 8} y2={H - 22} stroke="var(--text-muted)" strokeDasharray="3 3" />
          <circle cx={sx(s.base_value)} cy={TOP} r={4} fill="var(--text-muted)" />
          <text x={W - R + 8} y={TOP + 4} className="tick-text" style={{ fill: 'var(--text-primary)' }}>{d(s.base_value)}</text>
        </g>
        {bars.map((b, i) => {
          const y = TOP + (i + 1) * ROW
          const pos = b.phi >= 0
          return (
            <g key={b.feature} data-testid={`${testPrefix}-${b.feature}`}>
              <text x={LBL - 10} y={y + 4} textAnchor="end" className="fi-name">{b.feature} <tspan className="fi-val">= {fv(b.value)}</tspan></text>
              <rect x={Math.min(sx(b.from), sx(b.to))} y={y - 7} width={Math.max(1.5, Math.abs(sx(b.to) - sx(b.from)))} height={14} rx={2} fill={pos ? 'var(--viz-e)' : 'var(--viz-b)'} opacity={0.85} />
              {i < bars.length - 1 && <line x1={sx(b.to)} x2={sx(b.to)} y1={y + 7} y2={y + ROW - 7} stroke="var(--text-muted)" strokeWidth={0.75} />}
              <text x={W - R + 8} y={y + 4} className="tick-text" style={{ fill: pos ? 'var(--viz-e)' : 'var(--viz-b)' }}>{pos ? '+' : '−'}{d(Math.abs(b.phi))}</text>
            </g>
          )
        })}
        <g>
          <text x={LBL - 10} y={TOP + (bars.length + 1) * ROW + 4} textAnchor="end" className="fi-name"><tspan className="math-t">f(x)</tspan></text>
          <line x1={sx(s.fx)} x2={sx(s.fx)} y1={TOP + bars.length * ROW + 8} y2={TOP + (bars.length + 1) * ROW} stroke="var(--text-primary)" />
          <circle cx={sx(s.fx)} cy={TOP + (bars.length + 1) * ROW} r={5} fill="var(--accent-primary)" />
          <text x={W - R + 8} y={TOP + (bars.length + 1) * ROW + 4} className="tick-text" style={{ fill: 'var(--text-primary)', fontWeight: 700 }}>{d(s.fx)}</text>
        </g>
      </svg>
    </div>
  )
}

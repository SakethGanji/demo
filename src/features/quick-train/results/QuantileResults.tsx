import { useState } from 'react'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { useWidth } from '@/shared/hooks/useWidth'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import type { QuantileEval, QuantileReport, Run } from '@/shared/api/types'
import { fmtScore } from '../wizard'

const g = (t: number) => String(Number(t.toFixed(6)))
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const NC = { textTransform: 'none' as const, letterSpacing: 0 }
const Q = ({ t }: { t: number }) => <>q̂<sub>{g(t)}</sub></>

/**
 * F9: a quantile run's results. Every number comes from the run's `quantile` report (the worker's holdout evaluation):
 * the pinball loss and F̂(τ) per level, every interval's coverage (raw and conformally calibrated), and the fan chart
 * of the holdout rows sorted by the centre prediction.
 */
export function QuantileResults({ run }: { run: Run }) {
  const q = run.quantile
  if (!q) return null
  if (q.error) return <div className="card card-body" role="alert" data-testid="quantile-results">The quantile report failed: {q.error}</div>
  const iv = q.holdout.intervals[0]
  const ok = iv ? Math.abs(iv.coverage - iv.nominal) <= 0.05 : null
  return (
    <div className="stack" data-testid="quantile-results">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.2fr)', alignItems: 'start' }}>
        <div className="card" data-testid="quantile-coverage">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Range Coverage</h3><span className="helper" style={{ fontSize: 12 }}>{q.served} quantiles</span></div>
            {ok != null && <StatusPill status={ok ? 'success' : 'warning'}>{ok ? '● close to nominal' : '▲ off nominal'}</StatusPill>}
          </div>
          {iv && (
            <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
              <div className="row" style={{ alignItems: 'baseline', gap: 'var(--space-12)', flexWrap: 'wrap' }}>
                <span className="mono" style={{ fontSize: 34, color: 'var(--text-primary)' }} data-testid="quantile-coverage-value">{iv.coverage.toFixed(3)}</span>
                <span className="helper">of {iv.n.toLocaleString()} holdout rows fell inside [<Q t={iv.lo} />, <Q t={iv.hi} />] · nominal {g(iv.nominal)} · 95% CI [{fmtScore(iv.coverage_ci[0], 3)}, {fmtScore(iv.coverage_ci[1], 3)}]</span>
              </div>
              <div className="formula">
                <div className="eq">cov = (1/n) Σᵢ 𝟙[<Q t={iv.lo} />(xᵢ) ≤ yᵢ ≤ <Q t={iv.hi} />(xᵢ)] = {iv.inside.toLocaleString()} / {iv.n.toLocaleString()} = {iv.coverage.toFixed(4)}</div>
                <div className="eq">nominal = {g(iv.hi)} − {g(iv.lo)} = {g(iv.nominal)} · below {pct(iv.below_lo)} · above {pct(iv.above_hi)} · mean width {fmtScore(iv.mean_width, 1)}</div>
                <dl className="where">
                  <dt>q̂<sub>τ</sub>(x)</dt><dd>the served model's predicted τ-quantile of <b className="mono">{run.label}</b> for row x</dd>
                  <dt>n</dt><dd>{iv.n.toLocaleString()} holdout rows, never seen in training or model selection</dd>
                  <dt>𝟙[·]</dt><dd>1 when the row's true value lies in its interval, else 0</dd>
                  <dt>CI</dt><dd>95% Wilson interval of a proportion with n trials</dd>
                </dl>
              </div>
              <CoverageTable q={q} />
            </div>
          )}
        </div>
        <LevelTable q={q} label={run.label} />
      </div>
      <FanChart q={q} label={run.label} />
      <ModelTable q={q} />
    </div>
  )
}

/** Every interval: coverage served, raw (c = 0) and conformally calibrated (+c_τ), and the offsets. */
function CoverageTable({ q }: { q: QuantileReport }) {
  const raw = q.raw?.intervals ?? []
  const cal = q.calibrated?.intervals ?? []
  const off = q.conformal.offsets
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="table-wrap"><table className="table" data-testid="quantile-intervals">
        <thead><tr><th>Interval</th><th className="r">Nominal</th><th className="r">Raw</th><th className="r">Calibrated</th><th className="r">Width</th></tr></thead>
        <tbody>{q.holdout.intervals.map((iv, i) => (
          <tr key={`${iv.lo}-${iv.hi}`}>
            <td className="table-cell">[<Q t={iv.lo} />, <Q t={iv.hi} />]</td>
            <td className="table-cell r">{g(iv.nominal)}</td>
            <td className={`table-cell r ${q.served === 'raw' ? 'sel' : ''}`}>{raw[i] ? raw[i].coverage.toFixed(4) : '—'}</td>
            <td className={`table-cell r ${q.served === 'calibrated' ? 'sel' : ''}`}>{cal[i] ? cal[i].coverage.toFixed(4) : '—'}</td>
            <td className="table-cell r muted">{raw[i] && cal[i] ? `${fmtScore(raw[i].mean_width, 0)} → ${fmtScore(cal[i].mean_width, 0)}` : '—'}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <p className="helper" style={{ fontSize: 12 }}>
        Conformal recalibration (CQR, AutoGluon's <span className="mono">calibrate</span>): q̃<sub>τ</sub>(x) = q̂<sub>τ</sub>(x) + c<sub>τ</sub>
        {off ? <> with {Object.entries(off).map(([t, c], i, a) => <span key={t}>c<sub>{t}</sub> = {fmtScore(c, 1)}{i < a.length - 1 ? ', ' : ''}</span>)}</> : null}.
        {' '}{q.conformal.applied ? 'Applied by AutoGluon at fit time: the served quantiles are the calibrated ones, and raw = served − c.' : `Not applied (${q.conformal.source ?? 'conformal off'}); the calibrated column shows what turning it on would give.`}
        {' '}The highlighted column is what the model serves.
      </p>
    </div>
  )
}

/** Pinball loss and F̂(τ) per level, with the gap to τ drawn as a bar. */
function LevelTable({ q, label }: { q: QuantileReport; label: string }) {
  const h: QuantileEval = q.holdout
  return (
    <div className="card" data-testid="quantile-levels">
      <div className="card-head"><div className="left"><h3 className="card-title">Per Level</h3><span className="helper" style={{ fontSize: 12 }}>pinball loss and calibration of each q̂<sub>τ</sub></span></div>
        <div className="row"><ParamChip sym="L" value={String(q.levels.length)} /><ParamChip sym="pinball" value={fmtScore(h.pinball, 1)} /></div></div>
      <div className="table-wrap"><table className="table">
        <thead><tr><th style={NC}>τ</th><th className="r" style={NC}>ρ̄τ (pinball)</th><th className="r" style={NC}>F̂(τ)</th><th className="r">95% CI</th><th style={NC}>F̂(τ) − τ</th></tr></thead>
        <tbody>{h.levels.map((x) => (
          <tr key={x.tau} data-testid={`quantile-level-${g(x.tau)}`}>
            <td className="table-cell">{g(x.tau)}{Math.abs(x.tau - q.centre) < 1e-9 ? <span className="muted"> · point ŷ</span> : null}</td>
            <td className="table-cell r">{fmtScore(x.pinball, 1)}</td>
            <td className="table-cell r">{x.below.toFixed(3)}</td>
            <td className="table-cell r muted">[{fmtScore(x.below_ci[0], 3)}, {fmtScore(x.below_ci[1], 3)}]</td>
            <td><GapBar gap={x.gap} /></td>
          </tr>
        ))}</tbody>
      </table></div>
      <div className="card-body">
        <div className="formula">
          <div className="eq">ρ<sub>τ</sub>(y, q) = max(τ(y − q), (τ − 1)(y − q)), &nbsp;ρ̄<sub>τ</sub> = (1/n) Σᵢ ρ<sub>τ</sub>(yᵢ, q̂<sub>τ</sub>(xᵢ))</div>
          <div className="eq">pinball = (1/L) Σ<sub>τ</sub> ρ̄<sub>τ</sub> = {fmtScore(h.pinball, 2)} &nbsp;·&nbsp; F̂(τ) = (1/n) Σᵢ 𝟙[yᵢ ≤ q̂<sub>τ</sub>(xᵢ)]</div>
          <dl className="where">
            <dt>τ</dt><dd>the quantile level; L = {q.levels.length} levels: {q.levels.map(g).join(', ')}</dd>
            <dt>ρ̄<sub>τ</sub></dt><dd>mean pinball loss of level τ, in units of <b className="mono">{label}</b> (lower is better); AutoGluon's eval_metric averages them</dd>
            <dt>F̂(τ)</dt><dd>share of holdout rows at or below their predicted τ-quantile; a calibrated level has F̂(τ) ≈ τ</dd>
            <dt>crossing</dt><dd>rows whose quantiles are out of order: {pct(h.crossing_share, 2)} ({h.crossing_rows})</dd>
          </dl>
        </div>
      </div>
    </div>
  )
}

/** F̂(τ) − τ on a ±0.1 scale, centred: right = too many rows below (quantile too low). */
function GapBar({ gap }: { gap: number }) {
  const w = 90, c = w / 2
  const x = Math.max(-1, Math.min(1, gap / 0.1)) * c
  return (
    <span className="row" style={{ gap: 6 }} title={`F̂(τ) − τ = ${gap.toFixed(4)}`}>
      <svg width={w} height="10" aria-hidden>
        <line x1={0} x2={w} y1={5} y2={5} stroke="var(--border-track)" />
        <line x1={c} x2={c} y1={0} y2={10} stroke="var(--plot-axis)" />
        <rect x={Math.min(c, c + x)} width={Math.max(1.5, Math.abs(x))} y={2} height={6} rx={2} fill="var(--viz-a)" />
      </svg>
      <span className="mono muted" style={{ fontSize: 11 }}>{gap >= 0 ? '+' : ''}{gap.toFixed(3)}</span>
    </span>
  )
}

/** Holdout rows sorted by q̂ at the centre level: the bands between paired levels, the centre line, and the actuals. */
function FanChart({ q, label }: { q: QuantileReport; label: string }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const f = q.fan
  const n = f.y.length
  if (!n) return null
  const levels = q.levels
  const H = 280, L = 64, R = 12, T = 10, B = 30
  const cols = levels.map((t) => f.q[g(t)])
  const all = [...f.y, ...cols.flat()]
  const lo = Math.min(...all), hi = Math.max(...all)
  const x = (i: number) => L + (i / Math.max(1, n - 1)) * (w - L - R)
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B)
  const pairs = q.holdout.intervals.filter((iv) => iv.symmetric || q.holdout.intervals.length === 1)
  const band = (a: number[], b: number[]) => a.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ') + ' ' + b.map((_, k) => { const i = n - 1 - k; return `L${x(i)},${y(b[i])}` }).join(' ') + ' Z'
  const centre = f.q[g(q.centre)]
  const iv = q.holdout.intervals[0]
  const inside = (i: number) => (iv ? f.y[i] >= f.q[g(iv.lo)][i] && f.y[i] <= f.q[g(iv.hi)][i] : true)
  const nOut = f.y.filter((_, i) => !inside(i)).length
  return (
    <PlotFrame title="Fan Chart" chips={<><ParamChip sym="rows" value={`${n}${f.sampled ? ` of ${f.n.toLocaleString()}` : ''}`} /><ParamChip sym="sorted by" value={`q̂${f.sorted_by}`} /></>}
      caption={<span>Each column is one holdout row, sorted by its predicted {q.centre === 0.5 ? 'median' : `${g(q.centre)}-quantile`} q̂<sub>{g(q.centre)}</sub> (the line){f.sampled ? `, every ${Math.round(f.n / n)}th row shown` : ''}. Shaded: the predicted intervals {pairs.map((p) => `[q̂${g(p.lo)}, q̂${g(p.hi)}]`).join(', ')}, darker = narrower. Dots: the true <b className="mono">{label}</b>; ✕ marks the {nOut} shown rows outside {iv ? `[q̂${g(iv.lo)}, q̂${g(iv.hi)}]` : 'the interval'}. A width that grows with ŷ means the model learned that larger values are less certain.</span>}>
      <div ref={ref} data-testid="quantile-fan" style={{ position: 'relative' }}>
        <svg width={w} height={H} onMouseLeave={() => setHover(null)}>
          {[0, 0.25, 0.5, 0.75, 1].map((t) => { const v = lo + (hi - lo) * t; return <g key={t}><line x1={L} x2={w - R} y1={y(v)} y2={y(v)} stroke="var(--plot-grid)" /><text x={L - 6} y={y(v) + 4} textAnchor="end" className="tick-text">{fmtScore(v, 0)}</text></g> })}
          {pairs.map((p, k) => <path key={k} d={band(f.q[g(p.lo)], f.q[g(p.hi)])} fill="var(--viz-a)" fillOpacity={0.16 + 0.14 * k} stroke="none" />)}
          <path d={centre.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ')} fill="none" stroke="var(--viz-a)" strokeWidth={2} />
          {f.y.map((v, i) => inside(i)
            ? <circle key={i} cx={x(i)} cy={y(v)} r={hover === i ? 4 : 2} fill="var(--text-secondary)" fillOpacity={0.7} />
            : <path key={i} d={`M${x(i) - 3},${y(v) - 3}L${x(i) + 3},${y(v) + 3}M${x(i) - 3},${y(v) + 3}L${x(i) + 3},${y(v) - 3}`} stroke="var(--status-danger)" strokeWidth={1.5} />)}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="var(--plot-axis)" strokeDasharray="3 3" />}
          <rect x={L} y={T} width={Math.max(0, w - L - R)} height={H - T - B} fill="transparent"
            onMouseMove={(e) => { const r = (e.currentTarget as SVGRectElement).getBoundingClientRect(); setHover(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width) * (n - 1))))) }} />
          <text x={w - R} y={H - 6} textAnchor="end" className="axis-title">holdout rows, sorted by q̂{g(q.centre)} →</text>
        </svg>
        <div className="row" style={{ gap: 'var(--space-12)', flexWrap: 'wrap', fontSize: 12 }} data-testid="quantile-fan-legend">
          {pairs.map((p, k) => <span key={k} className="row" style={{ gap: 4 }}><span style={{ display: 'inline-block', width: 14, height: 10, background: 'var(--viz-a)', opacity: 0.16 + 0.14 * k, borderRadius: 2 }} />[q̂{g(p.lo)}, q̂{g(p.hi)}] nominal {g(p.nominal)}</span>)}
          <span className="row" style={{ gap: 4 }}><span style={{ display: 'inline-block', width: 14, height: 2, background: 'var(--viz-a)' }} />q̂{g(q.centre)}</span>
          <span className="row" style={{ gap: 4 }}><span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: 4, background: 'var(--text-secondary)' }} />y inside</span>
          <span className="row" style={{ gap: 4, color: 'var(--text-secondary)' }}><b style={{ color: 'var(--status-danger)' }}>✕</b> y outside</span>
        </div>
        {hover != null && (
          <div className="chip mono" style={{ position: 'absolute', top: 4, ...(x(hover) > w / 2 ? { right: w - x(hover) + 8 } : { left: x(hover) + 8 }), pointerEvents: 'none', whiteSpace: 'nowrap' }} data-testid="quantile-fan-tip">
            row {f.row[hover]} · y = {fmtScore(f.y[hover], 0)} · {levels.map((t) => `q̂${g(t)} = ${fmtScore(f.q[g(t)][hover], 0)}`).join(' · ')}
          </div>
        )}
      </div>
    </PlotFrame>
  )
}

/** Every model's pinball loss and coverage of the main interval. */
function ModelTable({ q }: { q: QuantileReport }) {
  if (!q.models.length) return null
  return (
    <div className="card" data-testid="quantile-models">
      <div className="card-head"><div className="left"><h3 className="card-title">Every Model's Range</h3><span className="helper" style={{ fontSize: 12 }}>holdout pinball loss and coverage, best first</span></div></div>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Model</th><th className="r">pinball</th>{q.levels.map((t) => <th key={t} className="r" style={NC}>ρ̄<sub>{g(t)}</sub></th>)}<th className="r">coverage</th><th className="r">mean width</th><th className="r">crossing</th></tr></thead>
        <tbody>{q.models.map((m) => (
          <tr key={m.model} className={m.model === q.model ? 'sel' : ''}>
            <td className="table-cell">{m.model}{m.conformalized ? <span className="muted"> · CQR</span> : null}</td>
            <td className="table-cell r">{fmtScore(m.pinball, 1)}</td>
            {m.levels.map((x) => <td key={x.tau} className="table-cell r muted">{fmtScore(x.pinball, 1)}</td>)}
            <td className="table-cell r">{m.coverage == null ? '—' : m.coverage.toFixed(3)}{m.nominal != null ? <span className="muted"> / {g(m.nominal)}</span> : null}</td>
            <td className="table-cell r muted">{fmtScore(m.mean_width, 0)}</td>
            <td className="table-cell r muted">{pct(m.crossing_share, 1)}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </div>
  )
}

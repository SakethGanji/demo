import type { Explain, ExplainRegression, Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks, padDomain } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'

const fv = (v: unknown) => (v == null || v === '' ? <span className="muted">∅</span> : typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : fmtScore(v, 3)) : String(v).length > 60 ? `${String(v).slice(0, 58)}…` : String(v))

/** Classification: the most confident mistakes. Regression: residuals and the largest misses. */
export function Errors({ ex, run, part, onRow }: { ex: Explain; run: Run; part: 'residuals' | 'worst'; onRow?: (row: number) => void }) {
  if (ex.kind === 'regression') return part === 'residuals' ? <ResidualPlots ex={ex} run={run} /> : <><ResidualPlots ex={ex} run={run} /><WorstTable ex={ex} run={run} onRow={onRow} /></>
  const feats = ex.worst[0] ? Object.keys(ex.worst[0].features) : []
  return (
    <div className="card" data-testid="worst-errors">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Worst Misclassified Rows</h3><span className="helper" style={{ fontSize: 12 }}>wrong predictions, least probability on the true class first</span></div>
        <div className="row"><ParamChip sym="wrong" value={`${ex.n_wrong.toLocaleString()} / ${ex.n.toLocaleString()}`} />{ex.threshold && <ParamChip sym="t" value={ex.threshold.value.toFixed(3)} />}</div>
      </div>
      <div className="table-wrap"><table className="table worst">
        <thead><tr><th>row</th><th className="math-th">y</th><th className="math-th">ŷ</th><th className="r math-th">p̂(y | x)</th><th className="r math-th">p̂(ŷ | x)</th>{onRow && <th />}{feats.map((f) => <th key={f}>{f}</th>)}</tr></thead>
        <tbody>
          {ex.worst.map((w) => (
            <tr key={w.row}>
              <td className="table-cell">{w.row}</td>
              <td><b>{w.y_true}</b></td>
              <td style={{ color: 'var(--status-danger)' }}>{w.y_pred}</td>
              <td className="r table-cell"><i className="cr-bar" style={{ marginRight: 6, verticalAlign: 'middle' }}><i style={{ width: `${w.p_true * 100}%`, background: 'var(--status-danger)' }} /></i>{w.p_true.toFixed(4)}</td>
              <td className="r table-cell">{w.p_pred?.toFixed(4)}</td>
              {onRow && <td><button className="chip-btn" onClick={() => onRow(w.row)} data-testid={`why-${w.row}`}>why? φ</button></td>}
              {feats.map((f) => <td key={f} className="table-cell wf">{fv(w.features[f])}</td>)}
            </tr>
          ))}
        </tbody>
      </table></div>
      <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>
        Rows i with ŷᵢ ≠ yᵢ, sorted by p̂(yᵢ | xᵢ), the probability the model gave the true class (lowest = most confidently wrong). {run.problem_type === 'binary' && `For binary runs ŷ = ${run.positive_class} when p̂ > t. `}These are the rows to read first: label errors, missing context, or a segment the model has not learned. Open one in Features & SHAP to see which features pushed it the wrong way.
      </p>
    </div>
  )
}

function ResidualPlots({ ex, run }: { ex: ExplainRegression; run: Run }) {
  const P = ex.points
  const [xlo, xhi] = padDomain(P.y_pred, 0.04)
  const lim = Math.max(...P.residual.map(Math.abs)) * 1.05
  const h = ex.histogram
  const cmax = Math.max(...h.counts, ...(h.normal ?? [0]))
  const fmtU = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(Math.abs(v) >= 10_000 ? 0 : 1)}k` : fmtScore(v, 2))
  const q = ex.quantiles
  return (
    <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.2fr) minmax(0, 1fr)', alignItems: 'start' }}>
      <PlotFrame title="Residuals vs Predicted" legend={[{ name: 'rᵢ', color: 'var(--viz-a)', data: [] }, { name: '±1.96 σᵣ', color: 'var(--viz-d)', dash: '5 4', data: [] }]}
        chips={<><ParamChip sym="n" value={ex.n.toLocaleString()} /><ParamChip sym="r̄" value={fmtScore(ex.mean_residual, 2)} /><ParamChip sym="σᵣ" value={fmtScore(ex.sd_residual, 2)} /></>}
        caption={<span>rᵢ = yᵢ − ŷᵢ against ŷᵢ for the holdout rows of <b className="mono">{run.label}</b>{P.sampled ? ` (a fixed sample of ${P.y_pred.length.toLocaleString()})` : ''}. A flat band around 0 is what we want; a funnel means the error grows with ŷ (heteroscedastic), a curve means bias the model has missed. Dashed: r̄ ± 1.96 σᵣ.</span>}>
        <XYPlot testId="resid-scatter" series={[]} x={{ min: xlo, max: xhi, ticks: niceTicks(xlo, xhi, 5), title: 'ŷ', fmt: fmtU }} y={{ min: -lim, max: lim, ticks: niceTicks(-lim, lim, 4), title: 'r', fmt: fmtU }}
          refs={[{ y: 0, label: 'r = 0', color: 'var(--text-primary)', dash: '2 2' }, { y: ex.mean_residual + 1.96 * ex.sd_residual, label: '+1.96σᵣ', color: 'var(--viz-d)', dash: '5 4' }, { y: ex.mean_residual - 1.96 * ex.sd_residual, label: '−1.96σᵣ', color: 'var(--viz-d)', dash: '5 4' }]} height={270}>
          {(sx, sy) => <g>{P.y_pred.map((v, i) => <circle key={i} cx={sx(v)} cy={sy(P.residual[i])} r={1.9} fill="var(--viz-a)" fillOpacity={0.4} />)}
            {ex.worst.slice(0, 5).map((w) => <circle key={w.row} cx={sx(w.y_pred)} cy={sy(w.residual)} r={5} fill="none" stroke="var(--status-danger)" strokeWidth={1.5} />)}</g>}
        </XYPlot>
      </PlotFrame>
      <PlotFrame title="Residual Distribution" legend={[{ name: 'count', color: 'var(--viz-b)', data: [] }, ...(h.normal ? [{ name: 'N(r̄, σᵣ²)', color: 'var(--viz-d)', data: [] }] : [])]}
        chips={<><ParamChip sym="RMSE" value={fmtScore(ex.rmse, 2)} /><ParamChip sym="MAE" value={fmtScore(ex.mae, 2)} /></>}
        caption={<span>{h.counts.length} equal bins of r. The curve is the normal density with the residuals' mean and σᵣ, scaled to counts (n·w·φ). Quantiles: q₀.₀₅ = {fmtU(q.q05)}, q₀.₅ = {fmtU(q.q50)}, q₀.₉₅ = {fmtU(q.q95)}: 90% of misses fall in [{fmtU(q.q05)}, {fmtU(q.q95)}].</span>}>
        <XYPlot testId="resid-hist" x={{ min: h.edges[0], max: h.edges[h.edges.length - 1], ticks: niceTicks(h.edges[0], h.edges[h.edges.length - 1], 4), title: 'r', fmt: fmtU }}
          y={{ min: 0, max: cmax * 1.08, ticks: niceTicks(0, cmax, 4), title: 'count', fmt: (v) => String(Math.round(v)) }}
          bars={h.counts.map((c, i) => ({ x0: h.edges[i], x1: h.edges[i + 1], y: c, color: 'var(--viz-b)', opacity: 0.7 }))}
          series={h.normal ? [{ name: 'N(r̄, σᵣ²)', color: 'var(--viz-d)', points: h.normal.map((v, i) => [(h.edges[i] + h.edges[i + 1]) / 2, v] as [number, number]) }] : []}
          refs={[{ x: q.q05, label: 'q₀.₀₅', dash: '2 3' }, { x: q.q95, label: 'q₀.₉₅', dash: '2 3' }]} height={270} />
      </PlotFrame>
    </div>
  )
}

function WorstTable({ ex, run, onRow }: { ex: ExplainRegression; run: Run; onRow?: (row: number) => void }) {
  const feats = ex.worst[0] ? Object.keys(ex.worst[0].features) : []
  return (
    <div className="card" data-testid="worst-errors">
      <div className="card-head"><div className="left"><h3 className="card-title">Largest Errors</h3><span className="helper" style={{ fontSize: 12 }}>holdout rows by |rᵢ| = |yᵢ − ŷᵢ|</span></div><div className="row"><ParamChip sym="max |r|" value={fmtScore(ex.worst[0]?.abs_error, 2)} /></div></div>
      <div className="table-wrap"><table className="table worst">
        <thead><tr><th>row</th><th className="r">y ({run.label})</th><th className="r math-th">ŷ</th><th className="r math-th">r = y − ŷ</th><th className="r math-th">|r| / σᵣ</th>{onRow && <th />}{feats.map((f) => <th key={f}>{f}</th>)}</tr></thead>
        <tbody>
          {ex.worst.map((w) => (
            <tr key={w.row}>
              <td className="table-cell">{w.row}</td>
              <td className="r table-cell">{fmtScore(w.y_true, 2)}</td>
              <td className="r table-cell">{fmtScore(w.y_pred, 2)}</td>
              <td className="r table-cell" style={{ color: w.residual > 0 ? 'var(--viz-e)' : 'var(--viz-b)' }}>{w.residual > 0 ? '+' : '−'}{fmtScore(Math.abs(w.residual), 2)}</td>
              <td className="r table-cell">{(w.abs_error / ex.sd_residual).toFixed(2)}</td>
              {onRow && <td><button className="chip-btn" onClick={() => onRow(w.row)} data-testid={`why-${w.row}`}>why? φ</button></td>}
              {feats.map((f) => <td key={f} className="table-cell wf">{fv(w.features[f])}</td>)}
            </tr>
          ))}
        </tbody>
      </table></div>
      <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>r &gt; 0: the model under-predicted; r &lt; 0: over-predicted. |r|/σᵣ is the miss in standard deviations of the residuals (σᵣ = {fmtScore(ex.sd_residual, 2)}).</p>
    </div>
  )
}

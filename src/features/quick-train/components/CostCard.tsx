import type { Dataset } from '../dataset'
import { DEFAULT_COST, bayesThreshold, costMatrix, costPositive, labelClasses, type Choices, type CostChoice, type Task } from '../wizard'

/**
 * F7: what one decision costs. Binary: c_FP, c_FN and an optional gain g_TP per decision, shown as the 2 × 2 cost
 * matrix and the Bayes threshold they imply. Multiclass: the K × K matrix. The costs become AutoGluon's eval_metric
 * (a make_scorer from ensemble_models.cost), so the race ranks and weights models on expected cost.
 */
export function CostCard({ ds, ch, set, task }: { ds: Dataset; ch: Choices; set: (p: Partial<Choices>) => void; task: Task }) {
  const c = ch.cost ?? DEFAULT_COST
  const put = (p: Partial<CostChoice>) => set({ cost: { ...c, ...p } })
  const classes = labelClasses(ds, ch.label)
  if (task === 'binary') {
    const names = classes.map((x) => x.name)
    const pos = costPositive(ds, ch.label) ?? 'positive'
    const neg = names.length === 2 ? names.find((n) => n !== pos) ?? 'negative' : 'negative'
    const prev = classes.find((x) => x.name === pos)?.share ?? null
    const t = bayesThreshold(c)
    const none = prev != null ? 1000 * c.c_fn * prev : null
    const all = prev != null ? 1000 * (c.c_fp * (1 - prev) - c.g_tp * prev) : null
    const ratios: [string, Partial<CostChoice>][] = [['FN = 5 × FP', { c_fp: 1, c_fn: 5, g_tp: 0 }], ['FN = 10 × FP', { c_fp: 1, c_fn: 10, g_tp: 0 }], ['FN = FP', { c_fp: 1, c_fn: 1, g_tp: 0 }]]
    return (
      <div className="card" data-testid="cost-card">
        <div className="card-head"><h3 className="card-title">Business Cost</h3><span className="micro-label nocase">per decision, in your own unit (£, hours, …)</span></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <div className="row" style={{ gap: 'var(--space-12)', flexWrap: 'wrap' }}>
            <Num sym="c_FP" label={`flag a ${neg} row (false positive)`} value={c.c_fp} onChange={(v) => put({ c_fp: v })} testId="cost-fp" />
            <Num sym="c_FN" label={`miss a ${pos} row (false negative)`} value={c.c_fn} onChange={(v) => put({ c_fn: v })} testId="cost-fn" />
            <Num sym="g_TP" label={`catch a ${pos} row (gain, optional)`} value={c.g_tp} onChange={(v) => put({ g_tp: v })} testId="cost-tp" />
          </div>
          <p className="helper" data-testid="cost-check">Check this ratio: it sets t* and so how many rows get flagged. Use your own numbers (e.g. the loss on a missed {pos} vs the margin lost on a wrongly flagged {neg}).</p>
          <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
            {ratios.map(([k, v]) => <button key={k} className={`btn sm ${c.c_fp === v.c_fp && c.c_fn === v.c_fn && c.g_tp === 0 ? 'primary' : ''}`} onClick={() => put(v)}>{k}</button>)}
          </div>
          <div className="table-wrap"><table className="table" data-testid="cost-matrix" style={{ maxWidth: 420 }}>
            <thead><tr><th>C[y][ŷ]</th><th>ŷ = {neg}</th><th>ŷ = {pos}</th></tr></thead>
            <tbody>
              <tr><td>y = {neg}</td><td className="mono">0 (TN)</td><td className="mono">{fmt(c.c_fp)} (FP)</td></tr>
              <tr><td>y = {pos}</td><td className="mono">{fmt(c.c_fn)} (FN)</td><td className="mono">{fmt(-c.g_tp)} (TP)</td></tr>
            </tbody>
          </table></div>
          <div className="formula" data-testid="cost-bayes">
            <div className="eq">t* = c<sub>FP</sub> / (c<sub>FP</sub> + c<sub>FN</sub> + g<sub>TP</sub>) = {fmt(c.c_fp)} / ({fmt(c.c_fp)} + {fmt(c.c_fn)} + {fmt(c.g_tp)}) = {t.toFixed(4)}</div>
            <dl className="where">
              <dt>E[C]</dt><dd>expected cost per decision, (c<sub>FP</sub>·FP + c<sub>FN</sub>·FN − g<sub>TP</sub>·TP) / n; shown per 1,000 decisions after the fit</dd>
              <dt>p̂</dt><dd>the model's probability that a row is {pos}; deciding {pos} costs (1 − p̂)·c<sub>FP</sub> − p̂·g<sub>TP</sub> in expectation, deciding {neg} costs p̂·c<sub>FN</sub></dd>
              <dt>t*</dt><dd>the Bayes threshold: flag when p̂ &gt; t*. During the fit every model decides this way, so AutoGluon ranks, early-stops and weights the ensemble on the cost it would really incur. After the fit, AutoGluon's <span className="mono">calibrate_decision_threshold</span> picks t on its validation predictions (never the holdout) to minimise the same cost.</dd>
              {none != null && all != null && <><dt>baselines</dt><dd>with {(100 * prev!).toFixed(1)}% {pos}: flag nobody = 1000·c<sub>FN</sub>·π = {fmt(none)}, flag everybody = {fmt(all)} per 1,000 decisions; a model is worth having only below both</dd></>}
            </dl>
          </div>
        </div>
      </div>
    )
  }
  const names = classes.map((x) => x.name)
  const m = costMatrix(c, names.length)
  const setCell = (i: number, j: number, v: number) => put({ matrix: m.map((r, a) => r.map((x, b) => (a === i && b === j ? v : x))) })
  return (
    <div className="card" data-testid="cost-card">
      <div className="card-head"><h3 className="card-title">Business Cost · {names.length} × {names.length} matrix</h3><span className="micro-label nocase">C[y][ŷ] per decision; rows: the truth</span></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <div className="table-wrap"><table className="table" data-testid="cost-matrix">
          <thead><tr><th>y \ ŷ</th>{names.map((n) => <th key={n}>{n}</th>)}</tr></thead>
          <tbody>{names.map((r, i) => (
            <tr key={r}><td>{r}</td>{names.map((_, j) => (
              <td key={j}><input className="mono" type="number" step="any" aria-label={`cost of deciding ${names[j]} when the truth is ${r}`} value={m[i][j]} style={{ width: 64 }}
                onChange={(e) => setCell(i, j, Number(e.target.value))} /></td>
            ))}</tr>
          ))}</tbody>
        </table></div>
        <div className="formula">
          <div className="eq">E[C] = (1/n) Σᵢ C[yᵢ][ŷᵢ]</div>
          <dl className="where">
            <dt>C[y][ŷ]</dt><dd>what deciding ŷ costs when the truth is y (the table; a gain is a negative number)</dd>
            <dt>ŷᵢ</dt><dd>AutoGluon's predicted class for row i (the most probable one, as served): models, early stopping and ensemble weights are chosen on E[C]. Results also show the cost of the Bayes decision argmin<sub>j</sub> Σ<sub>k</sub> p̂<sub>k</sub>·C[k][j], which the served predictor does not apply.</dd>
          </dl>
        </div>
      </div>
    </div>
  )
}

function Num({ sym, label, value, onChange, testId }: { sym: string; label: string; value: number; onChange: (v: number) => void; testId: string }) {
  const [a, b] = sym.split('_')
  return (
    <label className="stack" style={{ gap: 4, minWidth: 150 }}>
      <span className="helper" style={{ fontSize: 12 }}><i className="math-i">{a}</i><sub>{b}</sub> · {label}</span>
      <input className="mono" type="number" min={0} step="any" value={value} data-testid={testId} style={{ width: 110 }}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))} />
    </label>
  )
}

const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(2))

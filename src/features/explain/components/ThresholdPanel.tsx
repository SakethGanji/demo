import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run, ThresholdAnalysis, ThresholdPoint } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const T01 = [0, 0.2, 0.4, 0.6, 0.8, 1]
const SOURCE: Record<string, string> = {
  default: 'AutoGluon default (0.5)', calibrated: 'calibrated', manual: 'set by hand', autogluon_auto: "calibrated by AutoGluon's fit (auto)",
  fit_kwargs: 'fit_kwargs.calibrate_decision_threshold', none: '—',
}
const CAL_METRICS = [
  { key: 'f1', name: 'F₁' }, { key: 'balanced_accuracy', name: 'balanced accuracy' }, { key: 'mcc', name: 'MCC' },
]

/**
 * Binary decision threshold: a slider over t with precision, recall, F1, balanced accuracy, MCC, the confusion
 * counts and the cost C(t) = c_FP·FP + c_FN·FN, all read from GET /threshold (computed server-side on a 0.005 grid,
 * the exact point fetched when the slider rests); "Calibrate threshold" runs AutoGluon's
 * calibrate_decision_threshold(metric) and keeps the result in the predictor.
 */
export function ThresholdPanel({ run, onChanged }: { run: Run; onChanged: () => void }) {
  const [costFp, setCostFp] = useState(1)
  const [costFn, setCostFn] = useState(5)
  const [a, setA] = useState<ThresholdAnalysis | null>(null)
  const [t, setT] = useState<number>(run.threshold?.value ?? 0.5)
  const [exact, setExact] = useState<ThresholdPoint | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [calMetric, setCalMetric] = useState('f1')
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    let alive = true
    api.runs.threshold(run.id, { costFp, costFn }).then((r) => alive && setA(r)).catch((e) => alive && setMsg((e as Error).message))
    return () => { alive = false }
  }, [run.id, costFp, costFn, run.threshold?.value])
  // When the slider rests, ask the API for that exact t (the grid is 0.005 apart; this is any real t).
  useEffect(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => { api.runs.threshold(run.id, { costFp, costFn, t }).then((r) => setExact(r.at)).catch(() => {}) }, 250)
    return () => clearTimeout(timer.current)
  }, [t, run.id, costFp, costFn])

  const grid = a?.grid ?? []
  const atGrid = useMemo(() => grid.length ? grid.reduce((b, g) => (Math.abs(g.threshold - t) < Math.abs(b.threshold - t) ? g : b), grid[0]) : null, [grid, t])
  const p = exact && Math.abs(exact.threshold - t) < 1e-9 ? exact : atGrid
  if (!a || !p) return <div className="card card-body row">{msg ? <span style={{ color: 'var(--status-danger)' }}>✕ {msg}</span> : <><ResonanceWave /> Sweeping the threshold over the holdout…</>}</div>

  const cur = a.current
  const rec = run.threshold
  const costMax = Math.max(...grid.map((g) => g.cost ?? 0), 1)
  const pos = String(a.positive_class)
  const save = async (body: { metric?: string; value?: number }, label: string) => {
    setBusy(label); setMsg('')
    try {
      const r = await api.runs.setThreshold(run.id, body)
      setT(r.threshold.value)
      setMsg(`t = ${r.threshold.value.toFixed(4)} saved in the predictor (${SOURCE[r.threshold.source]}${r.threshold.metric ? ` for ${r.threshold.metric}` : ''}). Every prediction now uses it.`)
      onChanged()
    } catch (e) { setMsg((e as Error).message) } finally { setBusy(null) }
  }
  const hist = a.score_histogram
  const hmax = Math.max(...hist.positive, ...hist.negative, 1)
  const changed = Math.abs(t - cur.threshold) > 1e-9

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="threshold-panel">
      <div className="card threshold-card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Decision Threshold</h3><span className="helper" style={{ fontSize: 12 }}>ŷ = <b>{pos}</b> when p̂(x) &gt; t · p̂ = P̂(y = {pos} | x) from {a.model}</span></div>
          <div className="row">{rec && <StatusPill status={rec.source === 'default' ? 'neutral' : 'success'}>{`in use: t = ${rec.value.toFixed(3)} · ${SOURCE[rec.source]}${rec.metric && rec.source !== 'default' ? ` (${rec.metric})` : ''}`}</StatusPill>}</div>
        </div>
        <div className="card-body thr-body">
          <div className="thr-left">
            <div className="thr-slider">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="math thr-t">t = <b className="mono" data-testid="thr-t">{t.toFixed(3)}</b></span>
                <input className="num-input" aria-label="Threshold value" type="number" min={0} max={1} step={0.001} value={Number(t.toFixed(3))} onChange={(e) => { const v = Number(e.target.value); if (v >= 0 && v <= 1) setT(v) }} />
              </div>
              <input type="range" className="range" min={0} max={1} step={0.005} value={t} onChange={(e) => setT(Number(e.target.value))} aria-label="Decision threshold t" data-testid="thr-slider" />
              <div className="thr-ticks mono"><span>0</span><span>0.25</span><span>0.5</span><span>0.75</span><span>1</span></div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                <button className="chip-btn" onClick={() => setT(0.5)}>0.5 default</button>
                <button className="chip-btn" title="The t that maximises F₁ on this holdout (in hindsight; calibration uses validation data)" onClick={() => setT(a.best.f1.threshold)}>holdout max F₁ · {a.best.f1.threshold.toFixed(3)}</button>
                <button className="chip-btn" onClick={() => setT(a.best.balanced_accuracy.threshold)}>max bacc · {a.best.balanced_accuracy.threshold.toFixed(3)}</button>
                <button className="chip-btn" onClick={() => setT(a.best.cost.threshold)}>min cost · {a.best.cost.threshold.toFixed(3)}</button>
              </div>
            </div>
            <div className="thr-cm" data-testid="thr-cm">
              <span />
              <span className="micro-label nocase">ŷ = {pos}</span><span className="micro-label nocase">ŷ ≠ {pos}</span>
              <span className="micro-label nocase">y = {pos}</span>
              <b className="cm-tp" title="true positives">TP <span className="mono" data-testid="thr-tp">{p.tp}</span></b>
              <b className="cm-fn" title="false negatives">FN <span className="mono" data-testid="thr-fn">{p.fn}</span></b>
              <span className="micro-label nocase">y ≠ {pos}</span>
              <b className="cm-fp" title="false positives">FP <span className="mono" data-testid="thr-fp">{p.fp}</span></b>
              <b className="cm-tn" title="true negatives">TN <span className="mono" data-testid="thr-tn">{p.tn}</span></b>
            </div>
          </div>
          <div className="thr-read">
            <Read k="precision" f="TP / (TP + FP)" v={p.precision} id="precision" base={a.default.precision} />
            <Read k="recall" f="TP / (TP + FN)" v={p.recall} id="recall" base={a.default.recall} />
            <Read k="F₁" f="2·TP / (2·TP + FP + FN)" v={p.f1} id="f1" base={a.default.f1} />
            <Read k="balanced accuracy" f="(TPR + TNR) / 2" v={p.balanced_accuracy} id="bacc" base={a.default.balanced_accuracy} />
            <Read k="MCC" f="(TP·TN − FP·FN)/√(…)" v={p.mcc} id="mcc" base={a.default.mcc} />
            <Read k="FPR" f="FP / (FP + TN)" v={p.fpr} id="fpr" base={a.default.fpr} lowerBetter />
            <Read k="flagged" f="(TP + FP) / n" v={p.predicted_positive_rate} id="ppr" base={a.default.predicted_positive_rate} neutral />
            <div className="thr-cost">
              <span className="micro-label">cost C(t)</span>
              <b className="mono" data-testid="thr-cost">{(p.cost ?? 0).toLocaleString()}</b>
              <small><span className="math-s">c_FP·FP + c_FN·FN</span> = <span className="mono">{costFp}·{p.fp} + {costFn}·{p.fn}</span> · {fmtScore(p.cost_per_row, 3)}/row · {(a.default.cost ?? 0).toLocaleString()} at 0.5</small>
            </div>
          </div>
        </div>
        <div className="thr-actions">
          <div className="row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap' }}>
            <span className="micro-label">calibrate for</span>
            <div className="seg" role="tablist">{CAL_METRICS.map((m) => <button key={m.key} className={calMetric === m.key ? 'on' : ''} onClick={() => setCalMetric(m.key)}>{m.name}</button>)}</div>
            <button className="btn primary" disabled={!!busy} onClick={() => save({ metric: calMetric }, 'calibrate')} data-testid="thr-calibrate">{busy === 'calibrate' ? 'Calibrating…' : 'Calibrate threshold'}</button>
            <button className="btn" disabled={!!busy || !changed} onClick={() => save({ value: Number(t.toFixed(4)) }, 'set')} data-testid="thr-use">{busy === 'set' ? 'Saving…' : `Use t = ${t.toFixed(3)}`}</button>
            {busy && <ResonanceWave />}
          </div>
          <span className="helper" style={{ fontSize: 12 }}>
            Calibrate runs <span className="mono">predictor.calibrate_decision_threshold(metric=…)</span>: AutoGluon searches t on its own validation predictions (never this holdout), then <span className="mono">set_decision_threshold(t)</span> and save. Costs: c_FP <input className="num-input sm" aria-label="Cost of a false positive" type="number" min={0} step={0.5} value={costFp} onChange={(e) => setCostFp(Math.max(0, Number(e.target.value)))} />
            c_FN <input className="num-input sm" aria-label="Cost of a false negative" type="number" min={0} step={0.5} value={costFn} onChange={(e) => setCostFn(Math.max(0, Number(e.target.value)))} />
          </span>
          {msg && <p className="helper" role="status" data-testid="thr-msg" style={{ fontSize: 12.5, color: 'var(--text-primary)' }}>{msg}</p>}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <PlotFrame title="Metrics Against t"
          legend={[{ name: 'precision', color: 'var(--viz-b)', data: [] }, { name: 'recall', color: 'var(--viz-d)', data: [] }, { name: 'F₁', color: 'var(--viz-a)', data: [] }, { name: 'balanced acc.', color: 'var(--viz-c)', dash: '5 3', data: [] }]}
          chips={<><ParamChip sym="n" value={a.n.toLocaleString()} /><ParamChip sym="n₊" value={String(a.n_positive)} /><ParamChip sym="π" value={fmtScore(a.prevalence, 3)} /></>}
          caption={<span>Each metric on the {a.n.toLocaleString()} holdout rows as t moves (grid Δt = 0.005, server-side). n₊ = rows with y = {pos}, π = n₊/n. Click to move t. Lines: t (now), t* = {cur.threshold.toFixed(3)} in use, 0.5 AutoGluon's default.</span>}>
          <XYPlot testId="thr-curves" onPickX={(v) => setT(Math.round(v / 0.005) * 0.005)}
            series={[
              { name: 'precision', color: 'var(--viz-b)', points: grid.filter((g) => g.tp + g.fp > 0).map((g) => [g.threshold, g.precision]) },
              { name: 'recall', color: 'var(--viz-d)', points: grid.map((g) => [g.threshold, g.recall]) },
              { name: 'F₁', color: 'var(--viz-a)', width: 2.5, points: grid.map((g) => [g.threshold, g.f1]) },
              { name: 'bacc', color: 'var(--viz-c)', dash: '5 3', points: grid.map((g) => [g.threshold, g.balanced_accuracy]) },
            ]}
            refs={[{ x: t, label: `t = ${t.toFixed(3)}`, color: 'var(--accent-secondary)', dash: '0', strong: true }, { x: cur.threshold, label: `t* = ${cur.threshold.toFixed(3)}`, color: 'var(--accent-primary)' }, { x: 0.5, label: '0.5' }]}
            x={{ min: 0, max: 1, ticks: T01, title: 't', fmt: (v) => v.toFixed(1) }} y={{ min: 0, max: 1, ticks: T01, title: 'score', fmt: (v) => v.toFixed(1) }} height={250} />
        </PlotFrame>
        <PlotFrame title="Where t Cuts the Scores"
          legend={[{ name: `y = ${pos}`, color: 'var(--viz-e)', data: [] }, { name: `y ≠ ${pos}`, color: 'var(--viz-b)', data: [] }]}
          chips={<ParamChip sym="bins" value="50" />}
          caption={<span>Histogram of p̂ on the holdout, split by the true class (counts on a √ scale so the rare class shows). Everything right of t is flagged {pos}: positives there are TP, negatives FP.</span>}>
          <XYPlot testId="thr-hist" series={[]} onPickX={(v) => setT(Math.round(v / 0.005) * 0.005)}
            bars={[
              ...hist.negative.map((c, i) => ({ x0: hist.edges[i], x1: hist.edges[i + 1], y: Math.sqrt(c), color: 'var(--viz-b)', opacity: 0.45 })),
              ...hist.positive.map((c, i) => ({ x0: hist.edges[i], x1: hist.edges[i + 1], y: Math.sqrt(c), color: 'var(--viz-e)', opacity: 0.75 })),
            ]}
            refs={[{ x: t, label: `t = ${t.toFixed(3)}`, color: 'var(--accent-secondary)', dash: '0', strong: true }]}
            x={{ min: 0, max: 1, ticks: T01, title: 'p̂', fmt: (v) => v.toFixed(1) }}
            y={{ min: 0, max: Math.sqrt(hmax) * 1.05, ticks: [0, 10, 50, 100, 250, 500, 1000, 2500, 5000].filter((c) => c <= hmax).map(Math.sqrt), title: '√count', fmt: (v) => String(Math.round(v * v)) }} height={250} />
        </PlotFrame>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <PlotFrame title="Cost Tradeoff"
          legend={[{ name: 'C(t)', color: 'var(--status-danger)', data: [] }]}
          chips={<><ParamChip sym="c_FP" value={String(costFp)} /><ParamChip sym="c_FN" value={String(costFn)} /><ParamChip sym="t_cost" value={a.best.cost.threshold.toFixed(3)} /></>}
          caption={<span>C(t) = c_FP·FP(t) + c_FN·FN(t) on the holdout, in your cost unit (a false negative = a missed {pos}). Minimum {a.best.cost.value.toLocaleString()} at t = {a.best.cost.threshold.toFixed(3)}. If p̂ were perfectly calibrated, the Bayes-optimal rule flags x when p̂·c_FN &gt; (1 − p̂)·c_FP, i.e. t = c_FP/(c_FP + c_FN) = {a.bayes_threshold.toFixed(3)} (dotted).</span>}>
          <XYPlot testId="thr-cost-plot" onPickX={(v) => setT(Math.round(v / 0.005) * 0.005)}
            series={[{ name: 'C(t)', color: 'var(--status-danger)', area: true, points: grid.map((g) => [g.threshold, g.cost ?? 0]) }]}
            refs={[{ x: t, label: `t = ${t.toFixed(3)}`, color: 'var(--accent-secondary)', dash: '0', strong: true }, { x: a.bayes_threshold, label: 'c_FP/(c_FP + c_FN)', dash: '1 3' }]}
            marks={[{ x: a.best.cost.threshold, y: a.best.cost.value, label: `min ${a.best.cost.value.toLocaleString()}`, color: 'var(--status-danger)' }]}
            x={{ min: 0, max: 1, ticks: T01, title: 't', fmt: (v) => v.toFixed(1) }} y={{ min: 0, max: costMax * 1.05, ticks: niceTicks(0, costMax, 4), title: 'C', fmt: (v) => v.toLocaleString() }} height={220} />
        </PlotFrame>
        <BeforeAfter a={a} />
      </div>
    </div>
  )
}

function Read({ k, f, v, id, base, lowerBetter, neutral }: { k: string; f: string; v: number; id: string; base: number; lowerBetter?: boolean; neutral?: boolean }) {
  const d = v - base
  const good = neutral ? null : lowerBetter ? d < 0 : d > 0
  return (
    <div className="thr-r">
      <span className="micro-label">{k}</span>
      <b className="mono" data-testid={`thr-${id}`}>{v.toFixed(4)}</b>
      <small><span className="math-s">{f}</span>{Math.abs(d) > 1e-9 && <span className={`mono ${good == null ? 'muted' : good ? 'up' : 'down'}`}> {d > 0 ? '+' : '−'}{Math.abs(d).toFixed(3)} vs 0.5</span>}</small>
    </div>
  )
}

/** The holdout at AutoGluon's default 0.5 against the threshold in use. */
function BeforeAfter({ a }: { a: ThresholdAnalysis }) {
  const rows: [string, keyof ThresholdPoint][] = [['precision', 'precision'], ['recall', 'recall'], ['F₁', 'f1'], ['balanced accuracy', 'balanced_accuracy'], ['MCC', 'mcc'], ['accuracy', 'accuracy'], ['flagged share', 'predicted_positive_rate'], ['cost C', 'cost']]
  return (
    <div className="card" data-testid="thr-before-after">
      <div className="card-head"><div className="left"><h3 className="card-title">Default vs In Use</h3><span className="helper" style={{ fontSize: 12 }}>holdout, n = {a.n.toLocaleString()}</span></div></div>
      <table className="table">
        <thead><tr><th>metric</th><th className="r">t = 0.5</th><th className="r">t* = {a.current.threshold.toFixed(3)}</th><th className="r">Δ</th></tr></thead>
        <tbody>
          {rows.map(([n, k]) => {
            const x = a.default[k] as number, y = a.current[k] as number
            const d = y - x
            const lower = k === 'cost'
            return (
              <tr key={k}>
                <td>{n}</td>
                <td className="r table-cell">{k === 'cost' ? x.toLocaleString() : x.toFixed(4)}</td>
                <td className="r table-cell" data-testid={`ba-${k}`}>{k === 'cost' ? y.toLocaleString() : y.toFixed(4)}</td>
                <td className={`r table-cell ${Math.abs(d) < 1e-12 || k === 'predicted_positive_rate' ? 'muted' : (lower ? d < 0 : d > 0) ? 'up' : 'down'}`}>{d >= 0 ? '+' : '−'}{k === 'cost' ? Math.abs(d).toLocaleString() : Math.abs(d).toFixed(4)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>Same model, same probabilities: only the cut changes. Threshold-free scores (ROC AUC, log loss, Brier) are identical at every t.</p>
    </div>
  )
}

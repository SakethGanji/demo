import { useState } from 'react'
import { ConfusionMatrix } from '@/shared/components/ConfusionMatrix'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { useWidth } from '@/shared/hooks/useWidth'
import { ParamChip } from '@/shared/primitives/ParamChip'
import type { HoldoutPredictions, Run } from '@/shared/api/types'
import { fmtScore } from '../wizard'
import { residuals } from './stats'

/** Classification: the holdout confusion matrix, with recall and precision per class read off it. */
export function ClassReport({ run }: { run: Run }) {
  const h = run.holdout
  if (!h?.confusion_matrix || !h.classes) return null
  const m = h.confusion_matrix
  const labels = h.classes.map(String)
  const K = labels.length
  const recall = m.map((row, i) => row[i] / (row.reduce((a, b) => a + b, 0) || 1))
  const precision = labels.map((_, j) => m[j][j] / (m.reduce((a, row) => a + row[j], 0) || 1))
  return (
    <div className="card" data-testid="confusion">
      <div className="card-head"><div className="left"><h3 className="card-title">Confusion Matrix</h3><span className="helper" style={{ fontSize: 12 }}>{h.model} on the holdout · rows: true y, columns: predicted ŷ</span></div><div className="row"><ParamChip sym="n" value={h.n_rows.toLocaleString()} /><ParamChip sym="K" value={String(K)} /></div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <ConfusionMatrix labels={labels} m={m} />
        <div className="cls-report">
          <span className="micro-label">class k</span><span className="micro-label r">recall Mₖₖ / Σⱼ Mₖⱼ</span><span className="micro-label r">precision Mₖₖ / Σᵢ Mᵢₖ</span><span className="micro-label r">support nₖ</span>
          {labels.map((l, i) => (
            <span key={l} style={{ display: 'contents' }}>
              <span className="cr-name">{l}</span>
              <span className="mono r"><Bar v={recall[i]} />{recall[i].toFixed(3)}</span>
              <span className="mono r"><Bar v={precision[i]} />{precision[i].toFixed(3)}</span>
              <span className="mono r muted">{m[i].reduce((a, b) => a + b, 0).toLocaleString()}</span>
            </span>
          ))}
        </div>
        <p className="helper" style={{ fontSize: 12 }}>M ∈ ℕᴷˣᴷ, Mᵢⱼ = holdout rows of true class i predicted as j. The diagonal is right; everything off it is a mistake, and where it falls says which classes the model confuses.</p>
      </div>
    </div>
  )
}

const Bar = ({ v }: { v: number }) => <i className="cr-bar"><i style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }} /></i>

/** Regression: residuals rᵢ = yᵢ − ŷᵢ on the holdout, their distribution, and predicted against actual. */
export function Residuals({ run, preds }: { run: Run; preds: HoldoutPredictions | null }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const [view, setView] = useState<'hist' | 'scatter'>('hist')
  if (!preds) return <div className="card card-body helper" data-testid="residuals">Loading the holdout predictions…</div>
  const s = residuals(preds)
  const H = 210, L = 56, B = 28, T = 8, R = 10
  const nb = 30
  const lo = Math.min(...s.r), hi = Math.max(...s.r)
  const bw = (hi - lo) / nb || 1
  const bins = new Array(nb).fill(0)
  s.r.forEach((v) => { bins[Math.min(nb - 1, Math.floor((v - lo) / bw))]++ })
  const maxB = Math.max(...bins)
  const x = (v: number) => L + ((v - lo) / (hi - lo || 1)) * (w - L - R)
  const yy = (c: number) => H - B - (c / maxB) * (H - T - B)
  const all = [...s.y, ...s.p]
  const amin = Math.min(...all), amax = Math.max(...all)
  const ax = (v: number) => L + ((v - amin) / (amax - amin || 1)) * (w - L - R)
  const ay = (v: number) => T + (1 - (v - amin) / (amax - amin || 1)) * (H - T - B)
  const label = run.label
  return (
    <PlotFrame title="Residuals" right={<div className="seg" role="tablist">{(['hist', 'scatter'] as const).map((v) => <button key={v} className={view === v ? 'on' : ''} onClick={() => setView(v)}>{v === 'hist' ? 'distribution' : 'ŷ vs y'}</button>)}</div>}
      chips={<><ParamChip sym="n" value={preds.n_rows.toLocaleString()} /><ParamChip sym="r̄" value={fmtScore(s.mean, 2)} /><ParamChip sym="σᵣ" value={fmtScore(s.sd, 2)} /></>}
      caption={<span>rᵢ = yᵢ − ŷᵢ for the {preds.n_rows.toLocaleString()} holdout rows of <b className="mono">{label}</b> ({preds.model}). r̄ is the mean residual (bias; 0 is unbiased), σᵣ their standard deviation; q₀.₀₅ … q₀.₉₅ the 5th to 95th percentiles: 90% of misses fall in [{fmtScore(s.q05, 1)}, {fmtScore(s.q95, 1)}].</span>}>
      <div ref={ref} data-testid="residuals">
        <svg width={w} height={H}>
          {view === 'hist' ? <>
            {[0, 0.5, 1].map((f) => <g key={f}><line x1={L} x2={w - R} y1={yy(maxB * f)} y2={yy(maxB * f)} stroke="var(--plot-grid)" /><text x={L - 6} y={yy(maxB * f) + 4} textAnchor="end" className="tick-text">{Math.round(maxB * f)}</text></g>)}
            <rect x={x(s.q05)} width={Math.max(1, x(s.q95) - x(s.q05))} y={T} height={H - T - B} fill="var(--bg-wave-tint)" />
            {bins.map((c, i) => <rect key={i} x={x(lo + i * bw) + 0.5} width={Math.max(1, x(lo + (i + 1) * bw) - x(lo + i * bw) - 1)} y={yy(c)} height={H - B - yy(c)} fill="var(--viz-a)" opacity={0.85} rx={1} />)}
            {lo <= 0 && hi >= 0 && <line x1={x(0)} x2={x(0)} y1={T} y2={H - B} stroke="var(--text-primary)" strokeDasharray="4 3" />}
            <line x1={x(s.mean)} x2={x(s.mean)} y1={T} y2={H - B} stroke="var(--viz-b)" strokeWidth={2} />
            {[s.q05, s.q50, s.q95].map((v) => <text key={v} x={x(v)} y={H - B + 14} textAnchor="middle" className="tick-text">{fmtScore(v, 1)}</text>)}
            <text x={w - R} y={H - 2} textAnchor="end" className="axis-title">r = y − ŷ</text>
          </> : <>
            {[0, 0.5, 1].map((f) => { const v = amin + (amax - amin) * f; return <g key={f}><line x1={L} x2={w - R} y1={ay(v)} y2={ay(v)} stroke="var(--plot-grid)" /><text x={L - 6} y={ay(v) + 4} textAnchor="end" className="tick-text">{fmtScore(v, 1)}</text><text x={ax(v)} y={H - B + 14} textAnchor="middle" className="tick-text">{fmtScore(v, 1)}</text></g> })}
            <line x1={ax(amin)} y1={ay(amin)} x2={ax(amax)} y2={ay(amax)} stroke="var(--plot-axis)" strokeDasharray="5 4" />
            {s.y.slice(0, 1500).map((v, i) => <circle key={i} cx={ax(v)} cy={ay(s.p[i])} r={2} fill="var(--viz-a)" fillOpacity={0.45} />)}
            <text x={w - R} y={H - 2} textAnchor="end" className="axis-title">y (actual)</text>
            <text x={4} y={T + 8} className="axis-title">ŷ</text>
          </>}
        </svg>
        <div className="resid-sum">
          {([['q₀.₀₅', s.q05], ['q₀.₂₅', s.q25], ['median', s.q50], ['q₀.₇₅', s.q75], ['q₀.₉₅', s.q95], ['max |r|', s.maxAbs]] as [string, number][]).map(([k, v]) => <span key={k} className="chip"><span className="sym">{k}</span>={fmtScore(v, 1)}</span>)}
        </div>
        <div className="resid-worst">
          <span className="micro-label">Largest misses</span>
          {s.worst.map((r) => <span key={r.i} className="mono">row {r.row}: y = {fmtScore(r.y, 1)}, ŷ = {fmtScore(r.yhat, 1)}, r = {fmtScore(r.r, 1)}</span>)}
        </div>
      </div>
    </PlotFrame>
  )
}

/** TF-IDF models: the words with the largest coefficient per class (one-vs-rest). */
export function TopWords({ run }: { run: Run }) {
  const tm = run.text_models
  const models = tm ? Object.keys(tm) : []
  const [model, setModel] = useState(models[0] ?? '')
  if (!tm || !models.length) return null
  const per = tm[model] ?? tm[models[0]]
  const classes = Object.keys(per)
  return (
    <div className="card" data-testid="top-words">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Top Words Per Class</h3><span className="helper" style={{ fontSize: 12 }}>what the TF-IDF models read as evidence for each class</span></div>
        <div className="seg" role="tablist">{models.map((m) => <button key={m} className={model === m ? 'on' : ''} onClick={() => setModel(m)}>{m}</button>)}</div>
      </div>
      <div className="card-body">
        <div className="tw-grid">
          {classes.map((c) => {
            const words = per[c].slice(0, 8)
            const max = Math.max(...words.map(([, v]) => Math.abs(v)), 1e-9)
            return (
              <div key={c} className="tw-class" data-testid={`top-words-${c}`}>
                <div className="tw-head"><b>{c}</b><span className="micro-label">top βₖⱼ</span></div>
                {words.map(([wd, v]) => <div key={wd} className="tw-row"><span className="mono">{wd}</span><i className="tw-bar"><i style={{ width: `${(Math.abs(v) / max) * 100}%` }} /></i><span className="mono muted">{v.toFixed(2)}</span></div>)}
              </div>
            )
          })}
        </div>
        <p className="helper" style={{ fontSize: 12, marginTop: 'var(--space-12)' }}>
          x = TF-IDF(text) ∈ ℝᵛ, one weight per word or bigram j of a vocabulary of size V. Class k's one-vs-rest model scores sₖ(x) = βₖᵀx + bₖ; βₖⱼ is the coefficient of term j for class k{model.includes('SVM') ? ' (Linear SVM margins, Platt-calibrated to probabilities)' : ' (L2 logistic regression, C = 5)'}, averaged over fold models when bagged. Larger βₖⱼ: term j pushes a row towards class k.
        </p>
      </div>
    </div>
  )
}

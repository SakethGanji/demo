import { useState } from 'react'
import type { ExplainClassification, Run } from '@/shared/api/types'
import { ConfusionMatrix } from '@/shared/components/ConfusionMatrix'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, type XYSeries } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'

export const CLASS_COLORS = ['var(--viz-a)', 'var(--viz-b)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-e)', '#0F766E', '#7C3AED', '#B45309']
const T01 = [0, 0.2, 0.4, 0.6, 0.8, 1]
const f2 = (v: number) => v.toFixed(1)

/** Where the run's threshold puts it on a curve: the point whose threshold is the last one ≥ t (sklearn's order). */
function operatingIndex(thresholds: (number | null)[], t: number, descending = true) {
  if (descending) {
    let idx = 0
    thresholds.forEach((v, i) => { if (v != null && v > t) idx = i })
    return idx
  }
  const i = thresholds.findIndex((v) => v != null && v > t)
  return i < 0 ? thresholds.length : i
}

/** ROC, PR and the reliability diagram, side by side; one-vs-rest per class for multiclass. */
export function Curves({ ex, run }: { ex: ExplainClassification; run: Run }) {
  const classes = Object.keys(ex.roc)
  const binary = ex.positive_class != null && classes.length === 1
  const [only, setOnly] = useState<string | null>(null)
  const shown = binary ? classes : only ? [only] : classes
  const t = run.threshold?.value ?? 0.5
  const color = (c: string) => CLASS_COLORS[classes.indexOf(c) % CLASS_COLORS.length]

  const roc: XYSeries[] = shown.map((c) => ({
    name: binary ? `ROC · AUC ${fmtScore(ex.roc[c].auc)}` : `${c} · AUC ${fmtScore(ex.roc[c].auc, 3)}`, color: color(c), area: binary,
    points: ex.roc[c].fpr.map((v, i) => [v, ex.roc[c].tpr[i]] as [number, number]),
    tip: (i) => `p̂ > ${ex.roc[c].thresholds[i] == null ? '∞' : ex.roc[c].thresholds[i]!.toFixed(4)}`,
  }))
  const pr: XYSeries[] = shown.map((c) => ({
    name: binary ? `PR · AP ${fmtScore(ex.pr[c].average_precision)}` : `${c} · AP ${fmtScore(ex.pr[c].average_precision, 3)}`, color: color(c), step: 'before' as const, area: binary,
    points: ex.pr[c].recall.map((v, i) => [v, ex.pr[c].precision[i]] as [number, number]),
    tip: (i) => (i < ex.pr[c].thresholds.length ? `p̂ ≥ ${ex.pr[c].thresholds[i].toFixed(4)}` : 'no row flagged'),
  }))
  const pos = binary ? classes[0] : null
  const rocMark = pos ? (() => { const r = ex.roc[pos]; const i = operatingIndex(r.thresholds, t); return { x: r.fpr[i], y: r.tpr[i], label: `t = ${t.toFixed(3)}` } })() : null
  const prMark = pos ? (() => { const p = ex.pr[pos]; const i = operatingIndex(p.thresholds, t, false); return { x: p.recall[Math.min(i, p.recall.length - 1)], y: p.precision[Math.min(i, p.precision.length - 1)], label: `t = ${t.toFixed(3)}` } })() : null
  const cal = ex.calibration
  const maxCount = Math.max(...cal.counts, 1)

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }}>
      {!binary && (
        <div className="row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap' }}>
          <span className="micro-label">one-vs-rest curves</span>
          <div className="seg" role="tablist">
            <button className={only === null ? 'on' : ''} onClick={() => setOnly(null)}>all K = {classes.length}</button>
            {classes.map((c) => <button key={c} className={only === c ? 'on' : ''} onClick={() => setOnly(c)}>{c}</button>)}
          </div>
        </div>
      )}
      <div className="grid three-up">
        <PlotFrame title="ROC Curve" legend={roc.slice(0, 8).map((s) => ({ name: s.name, color: s.color, data: [] }))}
          chips={<>{pos && <ParamChip sym="n₊" value={String(ex.roc[pos].n_pos)} />}{pos && <ParamChip sym="n₋" value={String(ex.roc[pos].n_neg)} />}{!binary && <ParamChip sym="AUCₘₐ꜀ᵣₒ" value={fmtScore(ex.roc_macro_auc, 3)} />}</>}
          caption={<span>TPR(t) = TP/(TP + FN) against FPR(t) = FP/(FP + TN) as the threshold t sweeps the scores p̂{binary ? <> of <b>{pos}</b></> : <sub>k</sub>}{!binary && ' (class k against the rest)'}. AUC = ∫ TPR d(FPR) = P(p̂(x⁺) &gt; p̂(x⁻)). Dashed: chance (AUC = 0.5).{rocMark && <> ● marks the run's threshold t.</>}</span>}>
          <XYPlot testId="roc-plot" series={roc} x={{ min: 0, max: 1, ticks: T01, title: 'FPR', fmt: f2 }} y={{ min: 0, max: 1, ticks: T01, title: 'TPR', fmt: f2 }} diagonal={{ label: 'chance' }} marks={rocMark ? [rocMark] : undefined} height={250} />
        </PlotFrame>
        <PlotFrame title="Precision–Recall" legend={pr.slice(0, 8).map((s) => ({ name: s.name, color: s.color, data: [] }))}
          chips={pos ? <ParamChip sym="π" value={fmtScore(ex.pr[pos].prevalence, 3)} /> : undefined}
          caption={<span>precision P = TP/(TP + FP) against recall R = TP/(TP + FN) at every threshold. AP = Σₙ (Rₙ − Rₙ₋₁)·Pₙ (average precision){pos && <>; the dashed line is the prevalence π = n₊/n = {fmtScore(ex.pr[pos].prevalence, 3)}, what a random ranking scores</>}.</span>}>
          <XYPlot testId="pr-plot" series={pr} x={{ min: 0, max: 1, ticks: T01, title: 'R', fmt: f2 }} y={{ min: 0, max: 1, ticks: T01, title: 'P', fmt: f2 }}
            refs={pos ? [{ y: ex.pr[pos].prevalence, label: `π = ${ex.pr[pos].prevalence.toFixed(3)}` }] : undefined} marks={prMark ? [prMark] : undefined} height={250} />
        </PlotFrame>
        <PlotFrame title="Calibration" legend={[{ name: cal.kind === 'binary' ? `p̂ of ${cal.class}` : 'top-label confidence', color: 'var(--viz-c)', data: [] }]}
          chips={<><ParamChip sym="Brier" value={fmtScore(cal.brier)} /><ParamChip sym="ECE" value={fmtScore(cal.ece)} /></>}
          caption={<span>{cal.kind === 'binary' ? <>Rows binned by p̂ into B = {cal.n_bins} equal bins; each point is (mean p̂, observed share of <b>{cal.class}</b>) in a bin, its size ∝ rows nᵦ (bars). On the diagonal, "p̂ = 0.2" happens 20% of the time. Brier = (1/n) Σ (p̂ᵢ − yᵢ)² = {fmtScore(cal.brier)} (vs {fmtScore(cal.brier_reference)} for always predicting the base rate ȳ(1 − ȳ)); ECE = Σᵦ (nᵦ/n)|ȳᵦ − p̄ᵦ|.</>
            : <>Top-label reliability: rows binned by confidence maxₖ p̂ₖ (B = {cal.n_bins}), against the share where that class was right. Brier = (1/n) Σᵢ Σₖ (p̂ᵢₖ − 𝟙[yᵢ = k])² = {fmtScore(cal.brier)}; ECE = Σᵦ (nᵦ/n)|accᵦ − confᵦ|.</>}</span>}>
          <XYPlot testId="calibration-plot"
            series={[{ name: 'observed', color: 'var(--viz-c)', dots: true, points: cal.mean_predicted.map((v, i) => [v, cal.fraction_positive[i]] as [number, number]), dotR: (i) => 2.5 + 4 * Math.sqrt(cal.counts[i] / maxCount), tip: (i) => `n_b = ${cal.counts[i]}` }]}
            bars={cal.bins.map((b, i) => ({ x0: cal.edges[b], x1: cal.edges[b + 1], y: 0.25 * cal.counts[i] / maxCount, color: 'var(--border-track)', opacity: 0.6 }))}
            x={{ min: 0, max: 1, ticks: T01, title: 'p̂', fmt: f2 }} y={{ min: 0, max: 1, ticks: T01, title: 'ȳ', fmt: f2 }} diagonal={{ label: 'perfectly calibrated' }} height={250} />
        </PlotFrame>
      </div>
    </div>
  )
}

/** The confusion matrix at the run's threshold, and precision / recall / F1 per class (sklearn). */
export function PerClass({ ex }: { ex: ExplainClassification }) {
  const m = ex.confusion_matrix
  return (
    <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.15fr)', alignItems: 'start' }}>
      <div className="card" data-testid="explain-confusion">
        <div className="card-head"><div className="left"><h3 className="card-title">Confusion Matrix</h3><span className="helper" style={{ fontSize: 12 }}>rows: true y, columns: predicted ŷ{ex.threshold ? ` at t = ${ex.threshold.value.toFixed(3)}` : ''}</span></div>
          <div className="row"><ParamChip sym="n" value={ex.n.toLocaleString()} /><ParamChip sym="K" value={String(ex.classes.length)} /></div></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <ConfusionMatrix labels={ex.classes} m={m} />
          <p className="helper" style={{ fontSize: 12 }}>M ∈ ℕᴷˣᴷ, Mᵢⱼ = holdout rows of true class i predicted j. accuracy = Σᵢ Mᵢᵢ / n = {fmtScore(ex.accuracy)}; {ex.n_wrong.toLocaleString()} rows sit off the diagonal.</p>
        </div>
      </div>
      <div className="card" data-testid="per-class">
        <div className="card-head"><div className="left"><h3 className="card-title">Per-Class Report</h3><span className="helper" style={{ fontSize: 12 }}>sklearn precision_recall_fscore_support</span></div></div>
        <table className="table">
          <thead><tr><th>class k</th><th className="r">precision Mₖₖ/Σᵢ Mᵢₖ</th><th className="r">recall Mₖₖ/Σⱼ Mₖⱼ</th><th className="r">F₁ = 2PR/(P + R)</th><th className="r">support nₖ</th></tr></thead>
          <tbody>
            {ex.per_class.map((c, i) => (
              <tr key={c.class}>
                <td><span className="dot-k" style={{ background: CLASS_COLORS[i % CLASS_COLORS.length] }} />{c.class}</td>
                <td className="r table-cell"><MiniBar v={c.precision} />{c.precision.toFixed(3)}</td>
                <td className="r table-cell"><MiniBar v={c.recall} />{c.recall.toFixed(3)}</td>
                <td className="r table-cell"><MiniBar v={c.f1} />{c.f1.toFixed(3)}</td>
                <td className="r table-cell muted">{c.support.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>M is the confusion matrix on the left; P and R are the precision and recall of class k treated as the positive class; log loss −(1/n) Σ log p̂ᵢ,yᵢ = {fmtScore(ex.log_loss)}.</p>
      </div>
    </div>
  )
}

export const MiniBar = ({ v, color }: { v: number; color?: string }) => <i className="cr-bar" style={{ marginRight: 8, verticalAlign: 'middle' }}><i style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%`, background: color }} /></i>

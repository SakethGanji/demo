import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Explain, HoldoutPredictions, Run } from '@/shared/api/types'
import { TopWords } from '@/shared/components/TopWords'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { GroupedTabs } from '@/shared/primitives/Tabs'
import { Curves, PerClass } from './components/Curves'
import { Errors } from './components/Errors'
import { FairLendingPanel } from './components/FairLendingPanel'
import { FairnessPanel } from './components/FairnessPanel'
import { Importance } from './components/Importance'
import { MonotonePanel } from './components/MonotonePanel'
import { ScorecardPanel } from './components/ScorecardPanel'
import { ShapPanel } from './components/ShapPanel'
import { ThresholdPanel } from './components/ThresholdPanel'
import type { ExplainSub } from '../tabs'

/** The views a run has: the four a reviewer reads first, then the Advanced ones that only some runs have. */
function subsOf(run: Run): { main: ExplainSub[]; adv: ExplainSub[] } {
  const adv: ExplainSub[] = []
  if (run.problem_type === 'binary') adv.push('Threshold')
  if (run.problem_type === 'binary' || run.problem_type === 'regression') adv.push('Monotonicity')
  if (run.scorecard?.available) adv.push('Scorecard')
  if (run.text_models && Object.keys(run.text_models).length) adv.push('Text')
  return { main: ['Performance', 'Errors', 'Features & SHAP', 'Fairness'], adv }
}

/** The run page's Explain tab: how good on the holdout, where it fails, why it decides, and for whom (fair lending
 *  included for binary credit models). Every number is the API's. */
export function ExplainTab({ run, sub, extra, pick, onRunChanged }: {
  run: Run; sub: ExplainSub; extra: Record<string, string>; pick: (sub: string, extra?: Record<string, string>) => void; onRunChanged: () => void
}) {
  const [ex, setEx] = useState<Explain | null>(null)
  const [preds, setPreds] = useState<HoldoutPredictions | null>(null)
  const [err, setErr] = useState('')
  const [shapRow, setShapRow] = useState<number | null>(extra.row != null ? Number(extra.row) : null)
  const [version, setVersion] = useState(0) // bumps when the threshold changes: re-read everything that depends on it
  useEffect(() => {
    let alive = true
    setErr('')
    api.runs.explain(run.id).then((e) => alive && setEx(e)).catch((e) => alive && setErr((e as Error).message))
    api.runs.holdoutPredictions(run.id).then((p) => alive && setPreds(p)).catch(() => {})
    return () => { alive = false }
  }, [run.id, version])
  if (err) return <ErrorCard inline err={err} />
  if (!ex) return <Loading inline>Reading run {run.id}: curves, calibration and errors from the holdout…</Loading>

  const { main, adv } = subsOf(run)
  const cur: ExplainSub = [...main, ...adv].includes(sub) ? sub : 'Performance'
  const metric = run.eval_metric?.name ?? ''
  const info = metricInfo(metric)
  const thr = run.threshold
  const rank = run.leaderboard?.find((r) => r.model === run.best_model)
  const binaryTabular = run.problem_type === 'binary' && (!run.kind || run.kind === 'tabular')
  return (
    <div className="stack explain" style={{ gap: 'var(--space-16)' }} data-testid="explain" data-run={run.id}>
      <div className="card navy explain-hero">
        <div className="eh-cell lead">
          <span className="micro-label">holdout {info?.name ?? metric}</span>
          <b className="mono" data-testid="explain-primary">{fmtScore(run.holdout?.metrics[metric])}</b>
          <small>{run.problem_type}{run.classes ? ` · K = ${run.classes.length}` : ''} · {run.leaderboard?.length} models · val {fmtScore(rank?.score_val_natural)}</small>
        </div>
        {ex.kind === 'classification' ? <>
          {ex.positive_class != null && ex.roc[ex.positive_class] && (metric === 'roc_auc'
            ? <Cell k="average precision" v={fmtScore(ex.pr[ex.positive_class].average_precision)} d={`Σ (Rₙ − Rₙ₋₁)·Pₙ · π = ${fmtScore(ex.pr[ex.positive_class].prevalence, 3)}`} />
            : <Cell k="AUC" v={fmtScore(ex.roc[ex.positive_class].auc)} d="P(p̂(x⁺) > p̂(x⁻))" />)}
          {ex.roc_macro_auc != null && <Cell k="AUC (macro OvR)" v={fmtScore(ex.roc_macro_auc)} d="(1/K) Σₖ AUCₖ" />}
          <Cell k="Brier" v={fmtScore(ex.calibration.brier)} d={ex.calibration.kind === 'binary' ? '(1/n) Σ (p̂ᵢ − yᵢ)²' : '(1/n) Σᵢ Σₖ (p̂ᵢₖ − yᵢₖ)²'} />
          <Cell k="log loss" v={fmtScore(ex.log_loss)} d="−(1/n) Σ log p̂ᵢ,yᵢ" />
          {thr && <Cell k="threshold t" v={thr.value.toFixed(3)} d={thr.source === 'default' ? 'AutoGluon default' : thr.source === 'manual' ? 'set by hand' : `calibrated for ${thr.metric}`} testId="explain-threshold" />}
          <Cell k="mistakes" v={`${ex.n_wrong.toLocaleString()}`} d={`of n = ${ex.n.toLocaleString()} (${((ex.n_wrong / ex.n) * 100).toFixed(1)}%)`} />
        </> : <>
          {metric === 'root_mean_squared_error' ? <Cell k="σᵣ" v={fmtScore(ex.sd_residual)} d="sd of rᵢ = yᵢ − ŷᵢ" /> : <Cell k="RMSE" v={fmtScore(ex.rmse)} d="√((1/n) Σ rᵢ²)" />}
          {metric !== 'mean_absolute_error' && <Cell k="MAE" v={fmtScore(ex.mae)} d="(1/n) Σ |rᵢ|" />}
          {metric !== 'r2' && <Cell k="R²" v={fmtScore(ex.r2)} d="1 − Σ rᵢ² / Σ (yᵢ − ȳ)²" />}
          <Cell k="90% of misses" v={`±${fmtScore((ex.quantiles.q95 - ex.quantiles.q05) / 2, 0)}`} d={`[q₀.₀₅, q₀.₉₅] = [${fmtScore(ex.quantiles.q05, 0)}, ${fmtScore(ex.quantiles.q95, 0)}]`} />
          <Cell k="bias r̄" v={fmtScore(ex.mean_residual, 2)} d="(1/n) Σ rᵢ, rᵢ = yᵢ − ŷᵢ" />
        </>}
      </div>

      <GroupedTabs value={cur} onChange={(t) => pick(t)} groups={[{ label: 'Explain', tabs: main }, { label: 'Advanced', tabs: adv }]} />

      {cur === 'Performance' && (ex.kind === 'classification' ? <>
        <Curves ex={ex} run={run} />
        <PerClass ex={ex} />
      </> : <Errors ex={ex} run={run} part="residuals" />)}
      {cur === 'Errors' && <Errors ex={ex} run={run} part="worst" onRow={(r) => { setShapRow(r); pick('Features & SHAP') }} />}
      {cur === 'Features & SHAP' && <div className="stack" style={{ gap: 'var(--space-16)' }}>
        <Importance run={run} />
        <ShapPanel key={shapRow ?? 'auto'} run={run} ex={ex} preds={preds} initialRow={shapRow} />
      </div>}
      {cur === 'Fairness' && <>
        <FairnessPanel key={extra.group ?? 'auto'} run={run} ex={ex} initialGroup={extra.group} />
        {binaryTabular && <FairLendingPanel runId={run.id} />}
      </>}
      {cur === 'Threshold' && <ThresholdPanel run={run} onChanged={() => { setVersion((v) => v + 1); onRunChanged() }} />}
      {cur === 'Monotonicity' && <MonotonePanel run={run} ex={ex} />}
      {cur === 'Scorecard' && <ScorecardPanel run={run} />}
      {cur === 'Text' && <TopWords run={run} />}
    </div>
  )
}

function Cell({ k, v, d, testId }: { k: string; v: string; d: string; testId?: string }) {
  return <div className="eh-cell"><span className="micro-label">{k}</span><b className="mono" data-testid={testId}>{v}</b><small className="math-s">{d}</small></div>
}

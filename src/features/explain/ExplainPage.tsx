import { EmptyState } from '@/shared/components/EmptyState'
import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import { useApi } from '@/shared/api/mode'
import type { Explain, HoldoutPredictions, Run } from '@/shared/api/types'
import { TopWords } from '@/shared/components/TopWords'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { GroupedTabs } from '@/shared/primitives/Tabs'
import { Curves, PerClass } from './components/Curves'
import { Errors } from './components/Errors'
import { FairnessPanel } from './components/FairnessPanel'
import { MonotonePanel } from './components/MonotonePanel'
import { ScorecardPanel } from './components/ScorecardPanel'
import { LearningCurvePanel } from './components/LearningCurvePanel'
import { LabellingQueuePanel } from './components/LabellingQueuePanel'
import { AnnotatorsPanel } from './components/AnnotatorsPanel'
import { ShiftPanel } from './components/ShiftPanel'
import { SlicesPanel } from './components/SlicesPanel'
import { DoctorPanel } from './components/DoctorPanel'
import { Importance } from './components/Importance'
import { ShapPanel } from './components/ShapPanel'
import { ThresholdPanel } from './components/ThresholdPanel'

type Tab = 'Performance' | 'Model Doctor' | 'Threshold' | 'Features & SHAP' | 'Errors' | 'Fairness' | 'Monotonicity' | 'Scorecard' | 'Learning Curve' | 'Weak Slices' | 'Labelling Queue' | 'Annotators' | 'Shift' | 'Text'
/** F19: the order a reviewer reads a model in. How good is it (and where to cut), why does it decide, what limits it
 * and what to do next, and how good are the labels it learned from. */
const TAB_GROUPS: { label: string; tabs: Tab[] }[] = [
  { label: 'Evaluate', tabs: ['Performance', 'Threshold', 'Errors', 'Fairness'] },
  { label: 'Explain', tabs: ['Features & SHAP', 'Text', 'Monotonicity', 'Scorecard'] },
  { label: 'Diagnose', tabs: ['Model Doctor', 'Learning Curve', 'Weak Slices', 'Shift'] },
  { label: 'Labels', tabs: ['Labelling Queue', 'Annotators'] },
]

export const runParam = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('run')
const tabParam = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('tab')
const hashParam = (k: string) => new URLSearchParams(location.hash.split('?')[1] ?? '').get(k)

/** Explain one run: how good, where it fails, why it predicts what it does, and for whom. Every number is the API's. */
export function ExplainPage({ go, onApplyPatch }: { go: (r: string) => void; onApplyPatch?: (runId: string, patch: { id: string; title: string; changes: { path: string; from: unknown; to: unknown; label: string }[]; body: Record<string, unknown> }) => void }) {
  const { mode } = useApi()
  const [runId, setRunId] = useState<string | null>(runParam)
  const [runs, setRuns] = useState<Run[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [ex, setEx] = useState<Explain | null>(null)
  const [preds, setPreds] = useState<HoldoutPredictions | null>(null)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState<Tab>((tabParam() as Tab) || 'Performance')
  const [shapRow, setShapRow] = useState<number | null>(null)
  const [version, setVersion] = useState(0) // bumps when the threshold changes: re-read everything that depends on it

  useEffect(() => { const f = () => setRunId(runParam()); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  useEffect(() => {
    if (mode !== 'live') return
    api.runs.list().then((rs) => {
      const ok = rs.filter((r) => r.status === 'succeeded')
      setRuns(ok)
      if (!runParam() && ok[0]) location.hash = `#/explain?run=${ok[0].id}`
    }).catch(() => {})
  }, [mode])
  useEffect(() => {
    if (!runId || mode !== 'live') return
    let alive = true
    setErr('')
    api.runs.get(runId).then((r) => alive && setRun(r)).catch((e) => alive && setErr((e as Error).message))
    api.runs.explain(runId).then((e) => alive && setEx(e)).catch((e) => alive && setErr((e as Error).message))
    api.runs.holdoutPredictions(runId).then((p) => alive && setPreds(p)).catch(() => {})
    return () => { alive = false }
  }, [runId, mode, version])
  useEffect(() => { setEx(null); setRun(null); setPreds(null) }, [runId])

  const tabs = useMemo<Tab[]>(() => {
    if (!run) return ['Performance']
    const t: Tab[] = ['Performance']
    if (!run.kind || run.kind === 'tabular') t.push('Model Doctor')
    if (run.problem_type === 'binary') t.push('Threshold')
    t.push('Features & SHAP', 'Errors', 'Fairness')
    if (run.problem_type === 'binary' || run.problem_type === 'regression') t.push('Monotonicity')
    if (run.scorecard?.available) t.push('Scorecard')
    if (!run.kind || run.kind === 'tabular') t.push('Learning Curve', 'Weak Slices')
    if ((!run.kind || run.kind === 'tabular') && (run.problem_type === 'binary' || run.problem_type === 'multiclass')) t.push('Labelling Queue', 'Annotators')
    if (!run.kind || run.kind === 'tabular') t.push('Shift')
    if (run.text_models && Object.keys(run.text_models).length) t.push('Text')
    return t
  }, [run])
  const [extra, setExtra] = useState<Record<string, string>>(() => Object.fromEntries(['slice', 'group'].map((k) => [k, hashParam(k)]).filter(([, v]) => v)) as Record<string, string>)
  const pickTab = (t: Tab, more: Record<string, string> = {}) => {
    setTab(t); setExtra(more)
    const q = Object.entries(more).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join('')
    history.replaceState(null, '', `#/explain?run=${runId}&tab=${encodeURIComponent(t)}${q}`)
  }

  if (mode === 'mock') return (
    <div className="page explain"><div className="page-head"><div><h1 className="page-title">Explain</h1><p className="helper sub">Explanations are computed by the API from a trained AutoGluon predictor.</p></div></div>
      <div className="card card-body" data-testid="explain-mock"><p className="helper">▲ mock mode: the API is offline, so there is no trained model to explain. Start it with <span className="mono">make dev</span>; this page then explains real runs (threshold, ROC and PR curves, calibration, permutation importance, SHAP, errors and fairness).</p></div>
    </div>
  )
  if (mode === 'checking') return <div className="page"><div className="card card-body row"><ResonanceWave /> Connecting…</div></div>
  if (!runId && !runs.length) return (
    <div className="page explain"><div className="page-head"><div><h1 className="page-title">Explain</h1></div></div>
      <EmptyState testId="explain-empty" title="Nothing to explain yet"
        actions={<><button className="btn primary" onClick={() => go('quick-train')}>New Model →</button><button className="btn" onClick={() => go('import')}>Import a predictor</button></>}>
        <p>Explain reads a finished tabular run: ROC and PR on the fixed holdout, the reliability diagram with Brier and ECE, the decision threshold t with its cost C(t), permutation importance with 95% intervals, Kernel SHAP f(x) = φ₀ + Σφᵢ for any row, the worst errors and fairness by group.</p>
        <p>Train a model on one of the samples (about a minute on medium_quality) and it appears here.</p>
      </EmptyState></div>
  )
  if (err) return <div className="page"><div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div></div>
  if (!run || !ex) return <div className="page"><div className="card card-body row"><ResonanceWave /> Reading run {runId}: curves, calibration and errors from the holdout…</div></div>

  const metric = run.eval_metric?.name ?? ''
  const info = metricInfo(metric)
  const thr = run.threshold
  const rank = run.leaderboard?.find((r) => r.model === run.best_model)
  return (
    <div className="page explain" data-testid="explain" data-run={run.id}>
      <div className="page-head">
        <div>
          <h1 className="page-title">Explain</h1>
          <p className="helper sub">{run.name} · <span className="mono">{run.best_model}</span> on {ex.n.toLocaleString()} holdout rows never used in training</p>
        </div>
        <div className="row" style={{ flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <select className="select" aria-label="Run to explain" value={run.id} onChange={(e) => { location.hash = `#/explain?run=${e.target.value}` }} data-testid="explain-run-picker">
            {(runs.some((r) => r.id === run.id) ? runs : [run, ...runs]).map((r) => <option key={r.id} value={r.id}>{r.id} · {r.name}</option>)}
          </select>
          <button className="btn" onClick={() => go(`quick-train?run=${run.id}`)}>Results →</button>
          <button className="btn" onClick={() => go('experiments')}>Experiments</button>
        </div>
      </div>

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

      <GroupedTabs value={tabs.includes(tab) ? tab : 'Performance'} onChange={pickTab}
        groups={TAB_GROUPS.map((g) => ({ label: g.label, tabs: g.tabs.filter((t) => tabs.includes(t)) }))} />

      {(tab === 'Performance' || !tabs.includes(tab)) && (ex.kind === 'classification' ? <>
        <Curves ex={ex} run={run} />
        <PerClass ex={ex} />
      </> : <Errors ex={ex} run={run} part="residuals" />)}
      {tab === 'Model Doctor' && <DoctorPanel run={run} links={{ go, onApplyPatch, openTab: (t, more) => pickTab(t as Tab, more ?? {}) }} />}
      {tab === 'Threshold' && run.problem_type === 'binary' && <ThresholdPanel run={run} onChanged={() => setVersion((v) => v + 1)} />}
      {tab === 'Features & SHAP' && <div className="stack" style={{ gap: 'var(--space-16)' }}>
        <Importance run={run} />
        <ShapPanel key={shapRow ?? 'auto'} run={run} ex={ex} preds={preds} initialRow={shapRow} />
      </div>}
      {tab === 'Errors' && <Errors ex={ex} run={run} part="worst" onRow={(r) => { setShapRow(r); pickTab('Features & SHAP') }} />}
      {tab === 'Fairness' && <FairnessPanel key={extra.group ?? 'auto'} run={run} ex={ex} initialGroup={extra.group} />}
      {tab === 'Monotonicity' && <MonotonePanel run={run} ex={ex} />}
      {tab === 'Scorecard' && <ScorecardPanel run={run} />}
      {tab === 'Learning Curve' && <LearningCurvePanel run={run} />}
      {tab === 'Weak Slices' && <SlicesPanel run={run} links={{
        onRow: (r) => { setShapRow(r); pickTab('Features & SHAP') },
        onLabel: (s) => pickTab('Labelling Queue', { slice: s.id }),
        onFairness: (c) => pickTab('Fairness', { group: c }),
      }} />}
      {tab === 'Labelling Queue' && <LabellingQueuePanel key={extra.slice ?? 'all'} run={run} sliceId={extra.slice} />}
      {tab === 'Annotators' && <AnnotatorsPanel run={run} />}
      {tab === 'Shift' && <ShiftPanel run={run} go={go} onApplyPatch={onApplyPatch} />}
      {tab === 'Text' && <TopWords run={run} />}

      <div className="chips-row">
        <ParamChip sym="run" value={run.id} /><ParamChip sym="model" value={run.best_model ?? '—'} /><ParamChip sym="n" value={ex.n.toLocaleString()} />
        <span className="helper" style={{ fontSize: 12 }}>All numbers above come from <span className="mono">/api/runs/{run.id}/…</span>: scikit-learn, fairlearn and shap on the saved AutoGluon predictor and its holdout predictions.</span>
        <StatusPill status="success">live</StatusPill>
      </div>
    </div>
  )
}

function Cell({ k, v, d, testId }: { k: string; v: string; d: string; testId?: string }) {
  return <div className="eh-cell"><span className="micro-label">{k}</span><b className="mono" data-testid={testId}>{v}</b><small className="math-s">{d}</small></div>
}

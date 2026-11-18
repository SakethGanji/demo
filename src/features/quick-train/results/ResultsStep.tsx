import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { HoldoutPredictions, ModelFamily, Run } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { fromServer, type Dataset } from '../datasets'
import { StackEquation } from '../race/StackEquation'
import { fmtScore, fmtSeconds, metricInfo, mmss } from '../wizard'
import { CustomEnsemble } from './CustomEnsemble'
import { ClassReport, Residuals, TopWords } from './Diagnostics'
import { Leaderboard, ModelCard, ScoreScatter } from './Leaderboard'
import { SplitCompare } from './SplitCompare'
import { CostCompare } from './CostCompare'
import { MetricCards } from './MetricCards'
import { QuantileResults } from './QuantileResults'
import { residuals } from './stats'
import { TryPrediction } from './TryPrediction'

/** Step 5: what the run produced. Live: everything from GET /api/runs/{id}; mock: the simulated run. */
export function ResultsStep({ runId, mockRun, ds, live, go, onAgain }: {
  runId: string; mockRun: Run | null; ds: Dataset | null; live: boolean; go: (r: string) => void; onAgain: () => void
}) {
  const [run, setRun] = useState<Run | null>(mockRun)
  const [preds, setPreds] = useState<HoldoutPredictions | null>(null)
  const [families, setFamilies] = useState<ModelFamily[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [err, setErr] = useState('')
  // The run's own dataset, for the prediction form. The wizard's may be a different one (or the built-in sample when
  // fetching it failed while resuming from the URL), so fetch it here when it does not match, with a few retries.
  const [ownDs, setOwnDs] = useState<Dataset | null>(null)
  const runDs = run?.dataset_id
  useEffect(() => {
    if (!live || !runDs || ds?.id === runDs) { setOwnDs(null); return }
    let alive = true
    const attempt = (n: number) => api.datasets.get(runDs).then((d) => alive && setOwnDs(fromServer(d)))
      .catch(() => { if (alive && n < 4) setTimeout(() => attempt(n + 1), 1500 * (n + 1)) })
    attempt(0)
    return () => { alive = false }
  }, [live, runDs, ds?.id])
  const formDs = !live || ds?.id === runDs ? ds : ownDs

  useEffect(() => {
    if (mockRun) { setRun(mockRun); return }
    let alive = true
    api.runs.get(runId).then((r) => alive && setRun(r)).catch((e) => alive && setErr((e as Error).message))
    api.runs.holdoutPredictions(runId).then((p) => alive && setPreds(p)).catch(() => {})
    api.models().then((f) => alive && setFamilies(f)).catch(() => {})
    return () => { alive = false }
  }, [runId, mockRun])

  if (err) return <div className="page"><div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div></div>
  if (!run) return <div className="page"><div className="card card-body row"><ResonanceWave /> Loading the results…</div></div>
  if (run.status !== 'succeeded') return <div className="page"><div className="card card-body" role="alert">This run {run.status}{run.error ? `: ${run.error}` : '.'}</div></div>

  const reg = run.problem_type === 'regression' || run.problem_type === 'quantile'
  const metric = run.eval_metric?.name ?? ''
  const info = metricInfo(metric)
  const best = run.leaderboard?.find((r) => r.model === run.best_model) ?? run.leaderboard?.[0] ?? null
  const selRow = run.leaderboard?.find((r) => r.model === (sel ?? run.best_model)) ?? null
  const fam = families.find((f) => f.key === selRow?.family)
  const sd = reg && preds ? residuals(preds).sd : null
  const predictModels = (run.leaderboard ?? []).filter((r) => r.can_infer !== false && r.score_test != null).map((r) => (r.predict_model as string | undefined) ?? r.model)
  const weights = run.ensemble?.weights ?? null
  // a refit model (…_FULL) has no validation score of its own: its bagged original's
  const bestVal = best?.score_val_natural ?? run.leaderboard?.find((r) => best && r.model === best.model.replace(/_FULL$/, ''))?.score_val_natural ?? null
  const perRow = best?.pred_time_test != null && run.holdout?.n_rows ? best.pred_time_test / run.holdout.n_rows : null

  return (
    <div className="page results" data-testid="results" data-run={run.id}>
      <div className="card navy result-hero">
        <div className="card-head"><div className="left"><h3 className="card-title">Result · {run.name}</h3><span className="mono rh-id">{run.id}</span></div><StatusPill status="success">Completed</StatusPill></div>
        <div className="rh-body">
          <div className="rh-main">
            <span className="micro-label">holdout {info?.name ?? metric} · {run.best_model}</span>
            <span className="rh-v mono" data-testid="result-primary">{fmtScore(run.holdout?.metrics[metric])}</span>
            <span className="rh-sub">{run.problem_type}{run.quantile_levels ? ` τ = ${run.quantile_levels.join(' · ')}` : ''} · {run.holdout?.n_rows.toLocaleString()} holdout rows · {run.leaderboard?.length} models in {mmss(run.fit_seconds ?? 0)} · {fmtSeconds(perRow)} per row to predict</span>
            {run.threshold && <span className="rh-sub" data-testid="result-threshold">decision threshold t = <b className="mono">{run.threshold.value.toFixed(3)}</b>{run.threshold.source === 'default' ? ' (AutoGluon default)' : run.threshold.metric ? ` calibrated for ${run.threshold.metric} · holdout F₁ ${run.threshold.holdout_at_default.f1.toFixed(3)} at 0.5 → ${run.threshold.holdout_at_threshold.f1.toFixed(3)}, balanced accuracy ${run.threshold.holdout_at_default.balanced_accuracy.toFixed(3)} → ${run.threshold.holdout_at_threshold.balanced_accuracy.toFixed(3)}` : ''}</span>}
            <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-8)', marginTop: 'var(--space-12)' }}>
              <button className="btn primary" onClick={() => go(`explain?run=${run.id}`)} data-testid="results-explain">Explain this model →</button>
              <button className="btn" onClick={() => go(live ? `registry?register=${run.id}` : 'registry')} data-testid="results-register">Register Model Version</button>
              {live && run.config.parent_run_id ? <button className="btn" onClick={() => go(`experiments?compare=${String(run.config.parent_run_id)},${run.id}`)} data-testid="results-compare-parent">Compare with {String(run.config.parent_run_id)} →</button> : null}
              <button className="btn" onClick={() => go(live ? `gym?from=${run.id}` : 'gym')} data-testid="results-gym">Tune In Gym →</button>
              {live && <a className="btn ghost" href={`/api/runs/${run.id}/export.py?download=true`} data-testid="results-export">Export .py</a>}
              <button className="btn ghost" onClick={onAgain}>Train Another</button>
            </div>
          </div>
          {weights && Object.keys(weights).length > 0 && <div className="rh-eq"><StackEquation model={run.ensemble!.model} weights={weights} dark testId="result-equation" problemType={run.problem_type} K={run.classes?.length} scoreLabel={`validation ${metric} = ${fmtScore(bestVal)}${best?.score_val_natural == null && bestVal != null ? ` (${best?.model.replace(/_FULL$/, '')}, before refit)` : ''}`} /></div>}
        </div>
      </div>

      <MetricCards run={run} preds={preds} />

      {run.problem_type === 'quantile' && <QuantileResults run={run} />}

      {live && run.config.cost ? <CostCompare run={run} go={go} /> : null}

      {live && <SplitCompare run={run} go={go} />}

      <Leaderboard run={run} selected={sel} onSelect={setSel} />

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <ScoreScatter run={run} selected={sel} onSelect={setSel} />
        <ModelCard row={selRow} family={fam} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: run.text_models && Object.keys(run.text_models).length ? 'minmax(0, 1fr) minmax(0, 1.1fr)' : 'minmax(0, 1fr)', alignItems: 'start' }}>
        {reg ? <Residuals run={run} preds={preds} /> : <ClassReport run={run} />}
        <TopWords run={run} />
      </div>

      <CustomEnsemble run={run} live={live} onDone={() => { api.runs.get(run.id).then(setRun).catch(() => {}) }} />

      <TryPrediction key={formDs?.id ?? 'none'} run={run} ds={formDs} live={live} residualSd={sd} models={predictModels} />
    </div>
  )
}

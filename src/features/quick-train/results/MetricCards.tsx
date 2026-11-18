import type { HoldoutPredictions, Run } from '@/shared/api/types'
import { fmtScore, metricInfo } from '../wizard'
import { toData, withInterval } from './stats'

/** Which holdout metrics to show, primary first. */
function pick(run: Run): string[] {
  const m = run.holdout?.metrics ?? {}
  const primary = run.eval_metric?.name ?? Object.keys(m)[0]
  const pref = run.problem_type === 'regression'
    ? ['root_mean_squared_error', 'mean_absolute_error', 'r2', 'median_absolute_error']
    : run.problem_type === 'binary' ? ['accuracy', 'roc_auc', 'balanced_accuracy', 'f1', 'mcc'] : ['accuracy', 'balanced_accuracy', 'mcc', 'f1_macro']
  return [primary, ...pref.filter((k) => k !== primary && k in m)].slice(0, 4)
}

/**
 * The run's holdout metrics as cards in the maths style: the value, its 95% bootstrap interval (recomputed here from
 * the holdout predictions; the point value is the API's), the formula, and every symbol defined.
 */
export function MetricCards({ run, preds }: { run: Run; preds: HoldoutPredictions | null }) {
  const h = run.holdout
  if (!h) return null
  const keys = pick(run)
  const data = preds ? toData(preds, run.positive_class) : null
  return (
    <div className="metric-cards" data-testid="metric-cards">
      {keys.map((k, i) => {
        const info = metricInfo(k)
        const v = h.metrics[k]
        const ci = data ? withInterval(k, data) : null
        return (
          <div key={k} className={`card mcard ${i === 0 ? 'primary' : ''}`} data-testid={`metric-card-${k}`}>
            <div className="mcard-top">
              <span className="micro-label">{i === 0 ? 'optimised · ' : ''}{info?.name ?? k}</span>
              <span className="mcard-dir">{info ? (info.greater ? '↑' : '↓') : ''}</span>
            </div>
            <div className="mcard-v mono" data-testid={`metric-value-${k}`}>{fmtScore(v)}</div>
            <div className="mcard-ci mono">{ci ? <>95% CI [{fmtScore(ci.lo)}, {fmtScore(ci.hi)}]</> : <span className="muted">n = {h.n_rows.toLocaleString()} holdout rows</span>}</div>
            {info && <div className="mcard-f">{info.formula}</div>}
            <div className="mcard-where">
              n = {h.n_rows.toLocaleString()} holdout rows never seen in training{info && Object.keys(info.where).length ? '; ' : '.'}
              {info && Object.entries(info.where).map(([s, d], j, a) => <span key={s}><i>{s}</i> {d}{j < a.length - 1 ? '; ' : '.'}</span>)}
              {ci && <> CI: percentile bootstrap, B = {ci.B} resamples of the n rows.</>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

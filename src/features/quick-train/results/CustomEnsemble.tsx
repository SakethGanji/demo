import { useState } from 'react'
import { ApiError, api } from '@/shared/api/client'
import type { Comparison, CustomEnsemble as Result, Run } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { StackEquation } from '../race/StackEquation'
import { FAMILY_SHORT, fmtScore } from '../wizard'

/**
 * Build my own ensemble: choose base models, AutoGluon fits weights on them (fit_weighted_ensemble), and the result
 * says plainly whether it beats the best single model, on validation and on the holdout.
 */
export function CustomEnsemble({ run, live, onDone }: { run: Run; live: boolean; onDone: (r: Result) => void }) {
  const base = (run.leaderboard ?? []).filter((r) => !(r.family === 'ENS_WEIGHTED' || r.model.startsWith('WeightedEnsemble')) && !r.model.endsWith('_FULL'))
  const [chosen, setChosen] = useState<string[]>(() => base.slice(0, 3).map((r) => r.model))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<Result | null>(run.custom_ensembles?.length ? run.custom_ensembles[run.custom_ensembles.length - 1] : null)
  const toggle = (m: string) => setChosen((c) => (c.includes(m) ? c.filter((x) => x !== m) : [...c, m]))
  const fit = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.runs.ensemble(run.id, chosen)
      setRes(r); onDone(r)
    } catch (e) {
      setErr(e instanceof ApiError && e.unreachable ? 'The API is not reachable.' : (e as Error).message)
    } finally { setBusy(false) }
  }
  const metric = run.eval_metric?.name ?? 'score'
  return (
    <div className="card" data-testid="custom-ensemble">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Build My Own Ensemble</h3><span className="helper" style={{ fontSize: 12 }}>pick base models; AutoGluon fits their weights · <span className="mono">predictor.fit_weighted_ensemble(base_models=…)</span></span></div>
        {res && <StatusPill status={res.vs_best_single.holdout?.beats ? 'success' : 'warning'}>{res.vs_best_single.holdout?.beats ? 'Beats best single on holdout' : 'Does not beat best single on holdout'}</StatusPill>}
      </div>
      <div className="card-body ce-body">
        <div className="ce-pick">
          <div className="micro-label" style={{ marginBottom: 6 }}>Base models · {chosen.length} chosen</div>
          <div className="ce-list">
            {base.map((r) => (
              <label key={r.model} className={`ce-opt ${chosen.includes(r.model) ? 'on' : ''}`} data-testid={`ce-opt-${r.model}`}>
                <input type="checkbox" checked={chosen.includes(r.model)} onChange={() => toggle(r.model)} />
                <span className="ce-name"><b className="mono">{r.model}</b><small>{(r.family && FAMILY_SHORT[r.family]) ?? r.family}</small></span>
                <span className="mono ce-score">{fmtScore(r.score_val_natural)}</span>
              </label>
            ))}
          </div>
          <div className="row" style={{ marginTop: 'var(--space-12)', gap: 'var(--space-8)' }}>
            <button className="btn primary" data-testid="ce-fit" disabled={!live || busy || chosen.length === 0} onClick={fit}>{busy ? 'Fitting weights…' : 'Fit Weighted Ensemble'}</button>
            {busy && <ResonanceWave width={80} height={16} />}
            {!live && <span className="helper" style={{ fontSize: 12 }}>Needs the live API: mock mode has no fitted models.</span>}
          </div>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)', marginTop: 8 }}>✕ {err}</p>}
          <p className="helper" style={{ fontSize: 12, marginTop: 'var(--space-12)' }}>
            Caruana's greedy selection: start empty; repeatedly add (with replacement) the chosen model that most improves the {metric} of the averaged validation predictions; wₘ = times picked ÷ total picks. Fit on {run.oof?.available ? 'out-of-fold predictions' : "AutoGluon's tuning split"}, judged on the untouched holdout.
          </p>
        </div>
        <div className="ce-result" data-testid="ce-result">
          {!res && <div className="ce-empty helper">The weights, the equation and an honest comparison with the best single model appear here.</div>}
          {res && <>
            <StackEquation model={res.model} weights={res.weights} testId="ce-equation"
              scoreLabel={`${res.n_selected} of ${res.base_models.length} chosen models kept a weight > 0`} />
            <table className="table ce-table" data-testid="ce-compare">
              <thead><tr><th /><th className="r">Validation {metric}</th><th className="r">Holdout {metric}</th></tr></thead>
              <tbody>
                <tr className="sel"><td><b>Your ensemble</b> <span className="mono muted">{res.model}</span></td><td className="table-cell r">{fmtScore(res.scores.score_val_natural)}</td><td className="table-cell r">{fmtScore(res.scores.score_test_natural)}</td></tr>
                {res.best_single && <tr><td>Best single model <span className="mono muted">{res.best_single.model}</span></td><td className="table-cell r">{fmtScore(res.best_single.score_val_natural)}</td><td className="table-cell r">{fmtScore(res.best_single.score_test_natural)}</td></tr>}
                <tr><td>The run's own best <span className="mono muted">{res.run_best.model}</span></td><td className="table-cell r">{fmtScore(res.run_best.score_val_natural)}</td><td className="table-cell r">{fmtScore(res.run_best.score_test_natural)}</td></tr>
                <tr className="ce-delta"><td>Δ yours − best single</td><td className="table-cell r"><Delta c={res.vs_best_single.val} greater={res.greater_is_better} /></td><td className="table-cell r"><Delta c={res.vs_best_single.holdout} greater={res.greater_is_better} /></td></tr>
              </tbody>
            </table>
            <p className="ce-verdict" data-testid="ce-verdict">{verdict(res)}</p>
          </>}
        </div>
      </div>
    </div>
  )
}

function Delta({ c, greater }: { c: Comparison; greater: boolean }) {
  if (!c || c.delta_natural == null) return <span className="muted">—</span>
  const v = c.delta_natural
  return <span className={c.beats ? 'pos' : Math.abs(c.delta) < 1e-12 ? 'muted' : 'neg'}>{v >= 0 ? '+' : '−'}{fmtScore(Math.abs(v))} {c.beats ? '▲ better' : Math.abs(c.delta) < 1e-12 ? '= tie' : '▼ worse'}{greater ? '' : ' (lower is better)'}</span>
}

function verdict(r: Result) {
  const v = r.vs_best_single.val, h = r.vs_best_single.holdout
  const tie = (c: Comparison) => !!c && Math.abs(c.delta) < 1e-12
  const best = r.best_single?.model ?? 'the best single model'
  if (r.n_selected === 1) return `Greedy selection put all the weight on one model, so this ensemble is ${Object.keys(r.weights).find((m) => r.weights[m] > 0)}: no gain from combining these.`
  if (h?.beats && v?.beats) return `Better than ${best} on validation and on the ${r.n_holdout.toLocaleString()} holdout rows: combining these models helps.`
  if (h?.beats) return `Better than ${best} on the holdout, though not on validation: a gain, but small enough that it may not hold on new data.`
  if (v?.beats) return `Better than ${best} on validation but not on the holdout: the weights fit the validation predictions, and the gain did not carry over to unseen rows.`
  if (tie(h)) return `Ties ${best} on the holdout.`
  return `Not better than ${best}: on these models, ensembling does not help.`
}

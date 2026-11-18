import { useEffect, useRef, useState } from 'react'
import { api, followRun } from '@/shared/api/client'
import type { EnsembleEvent, Holdout, ModelFinishedEvent, ModelStartedEvent, RunEvent } from '@/shared/api/types'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import type { Dataset } from '../datasets'

const BUDGET_S = 60
const SUB = '₀₁₂₃₄₅₆₇₈₉'
const sub = (n: number) => String(n).split('').map((d) => SUB[Number(d)]).join('')

type Row = { model: string; family: string | null; level: number; done?: ModelFinishedEvent; failed?: string; skipped?: boolean }
type State = {
  runId: string | null; status: 'idle' | 'starting' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  rows: Row[]; ensemble: EnsembleEvent | null; holdout: Holdout | null; metric: string | null; greater: boolean
  error: string | null; trainRows: number | null
}
const EMPTY: State = { runId: null, status: 'idle', rows: [], ensemble: null, holdout: null, metric: null, greater: true, error: null, trainRows: null }

/**
 * A real AutoGluon fit of the chosen server dataset, followed live over SSE (minimal S2 view; S3 builds the full
 * race). One row per model as it starts and finishes, then the stacking equation with the fitted weights.
 */
export function LiveRun({ ds }: { ds: Dataset }) {
  const [s, setS] = useState<State>(EMPTY)
  const stop = useRef<(() => void) | null>(null)
  useEffect(() => () => stop.current?.(), [])
  useEffect(() => { stop.current?.(); setS(EMPTY) }, [ds.id, ds.target])

  const onEvent = (e: RunEvent) => setS((p) => {
    if (e.phase === 'dystack') return p
    switch (e.type) {
      case 'run_started': return { ...p, status: 'running' }
      case 'data_ready': return { ...p, trainRows: e.train_rows as number }
      case 'fit_started': return { ...p, metric: e.eval_metric as string, greater: e.greater_is_better as boolean }
      case 'model_started': {
        const m = e as ModelStartedEvent
        return { ...p, rows: [...p.rows.filter((r) => r.model !== m.model), { model: m.model, family: m.family, level: m.stack_level }] }
      }
      case 'model_finished': {
        const m = e as ModelFinishedEvent
        return { ...p, rows: p.rows.map((r) => (r.model === m.model ? { ...r, done: m } : r)) }
      }
      case 'model_failed': return { ...p, rows: p.rows.map((r) => (r.model === e.model ? { ...r, failed: String(e.error ?? 'failed'), skipped: Boolean(e.skipped) } : r)) }
      case 'ensemble': return { ...p, ensemble: e as EnsembleEvent }
      case 'evaluation': return { ...p, holdout: e as unknown as Holdout }
      case 'run_succeeded': return { ...p, status: 'succeeded' }
      case 'run_failed': return { ...p, status: 'failed', error: String(e.error ?? 'The run failed.') }
      case 'run_cancelled': return { ...p, status: 'cancelled' }
      default: return p
    }
  })

  const start = async () => {
    if (!ds.id) return
    stop.current?.()
    setS({ ...EMPTY, status: 'starting' })
    try {
      const run = await api.runs.create({ dataset_id: ds.id, label: ds.target, time_limit: BUDGET_S, presets: 'medium_quality', feature_importance: false })
      setS((p) => ({ ...p, runId: run.id, status: 'running' }))
      stop.current = followRun(run.id, onEvent, { onError: () => setS((p) => (p.status === 'running' ? { ...p, error: 'Lost the event stream; reconnecting…' } : p)) })
    } catch (e) {
      setS({ ...EMPTY, status: 'failed', error: (e as Error).message })
    }
  }

  const finished = s.rows.filter((r) => r.done && r.done.val_score_natural != null)
  const best = finished.length ? (s.greater ? Math.max : Math.min)(...finished.map((r) => r.done!.val_score_natural!)) : null
  const worst = finished.length ? (s.greater ? Math.min : Math.max)(...finished.map((r) => r.done!.val_score_natural!)) : null
  const tension = (v: number) => (best == null || worst == null || best === worst ? 1 : 0.35 + 0.65 * (1 - Math.abs(best - v) / Math.abs(best - worst)))
  const weights = s.ensemble ? Object.entries(s.ensemble.weights).sort((a, b) => b[1] - a[1]) : []
  const running = s.status === 'running' || s.status === 'starting'
  const pill = s.status === 'succeeded' ? <StatusPill status="success">Completed</StatusPill>
    : s.status === 'failed' ? <StatusPill status="danger">Failed</StatusPill>
    : s.status === 'cancelled' ? <StatusPill status="warning">Cancelled</StatusPill>
    : running ? <StatusPill status="info">Training</StatusPill> : null
  const fmt = (v: number | null | undefined, d = 4) => (v == null ? '—' : Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(d))

  return (
    <div className="card live-run" data-testid="live-run" data-status={s.status}>
      <div className="card-head">
        <div className="left">
          <h3 className="card-title">AutoGluon · Real Run</h3>
          <span className="helper" style={{ fontSize: 12 }}>fits {ds.target} on this dataset in its own process; every number below comes from the API</span>
        </div>
        <div className="row" style={{ gap: 'var(--space-8)' }}>
          {s.runId && <span className="chip" data-testid="live-run-id">{s.runId}</span>}
          <ParamChip sym="τ" value={`${BUDGET_S} s`} />
          {pill}
          {running && s.runId
            ? <button className="btn sm" onClick={() => s.runId && api.runs.cancel(s.runId).catch(() => {})}>Cancel</button>
            : <button className="btn sm primary" data-testid="live-run-start" disabled={!ds.id || s.status === 'starting'} onClick={start}>{s.status === 'idle' ? 'Train With AutoGluon' : 'Train Again'}</button>}
        </div>
      </div>

      {s.status !== 'idle' && (
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <div className="lr-meta helper">
            {s.trainRows != null && <span>n = {s.trainRows.toLocaleString()} training rows</span>}
            {s.metric && <span>metric: <b className="mono">{s.metric}</b> ({s.greater ? 'higher' : 'lower'} is better)</span>}
            {running && <ResonanceWave width={90} height={16} />}
          </div>
          <div className="lr-table" role="table" aria-label="Models">
            <div className="lr-row lr-headrow" role="row"><span>Model</span><span>Level</span><span /><span>Validation</span><span>Fit</span><span>Predict</span></div>
            {s.rows.map((r) => (
              <div key={r.model} className="lr-row" role="row" data-testid={`live-run-model-${r.model}`} data-done={r.done ? 'true' : 'false'}>
                <span className="lr-name"><b>{r.model}</b><small>{r.family ?? ''}</small></span>
                <span className="mono">L{r.level}</span>
                <span className="lr-string">
                  {r.failed ? <span className="helper" title={r.failed} style={{ color: r.skipped ? 'var(--text-muted)' : 'var(--status-danger)' }}>{r.failed.slice(0, 60)}</span>
                    : r.done ? <i style={{ width: `${(r.done.val_score_natural != null ? tension(r.done.val_score_natural) : 1) * 100}%` }} />
                    : <ResonanceWave width={160} height={14} fast />}
                </span>
                <span className="mono">{r.failed ? '—' : r.done ? <>{fmt(r.done.val_score_natural)}{r.done.val_score_std != null && <small> ± {fmt(r.done.val_score_std, 4)}</small>}</> : '…'}</span>
                <span className="mono">{r.done?.fit_time != null ? `${r.done.fit_time.toFixed(2)} s` : ''}</span>
                <span className="mono">{r.done?.predict_time != null ? `${r.done.predict_time.toFixed(3)} s` : ''}</span>
              </div>
            ))}
          </div>

          {weights.length > 0 && (
            <div className="formula lr-formula" data-testid="live-run-equation">
              <div className="micro-label">{s.ensemble!.model} · the stack</div>
              <div className="eq">
                <i>ŷ</i>(<i>x</i>) = {weights.map(([m, w], i) => <span key={m}>{i > 0 && ' + '}{w.toFixed(3)}·<i>f</i>{sub(i + 1)}(<i>x</i>)</span>)}
              </div>
              <dl className="where">
                <dt>ŷ(x)</dt><dd>the ensemble's prediction for a row x</dd>
                {weights.map(([m, w], i) => <span key={m} style={{ display: 'contents' }}><dt>f{sub(i + 1)}</dt><dd>{m} · w{sub(i + 1)} = {w.toFixed(3)}</dd></span>)}
                <dt>w</dt><dd>Caruana greedy selection on validation predictions: wₘ ≥ 0, Σ wₘ = 1</dd>
              </dl>
            </div>
          )}

          {s.holdout && (
            <div className="lr-holdout" data-testid="live-run-holdout">
              <span className="micro-label">Holdout · n = {s.holdout.n_rows.toLocaleString()} · {s.holdout.model}</span>
              {Object.entries(s.holdout.metrics).slice(0, 4).map(([k, v]) => <span key={k} className="chip mono">{k} = {fmt(v)}</span>)}
            </div>
          )}
          {s.error && <div className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>{s.error}</div>}
        </div>
      )}
    </div>
  )
}

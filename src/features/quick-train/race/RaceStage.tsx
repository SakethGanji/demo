import { useEffect, useState } from 'react'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { FAMILY_SHORT, fmtBudget, fmtScore, fmtSeconds, metricInfo, mmss, sub } from '../wizard'
import { EnsembleMerge } from './EnsembleMerge'
import { ModelString } from './ModelString'
import { StackEquation } from './StackEquation'
import { bestSoFar, isEnsemble, layers, scoreAxis, type ModelRow, type RaceState } from './useRace'

const STAGE_TEXT: Record<RaceState['stage'], string> = {
  waiting: 'Preparing the data', fitting: 'Fitting models', ranking: 'Ranking every model on the holdout',
  evaluating: 'Scoring the holdout', explaining: 'Permutation importance on the holdout', done: '',
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!active) return
    const iv = setInterval(() => setNow(Date.now() / 1000), 250)
    return () => clearInterval(iv)
  }, [active])
  return now
}

/** The navy stage the race plays on: clock against the budget, one string per model, stack layers, the ensemble forming. */
export function RaceStage({ s, runId, label, budget, speed, setSpeed, onCancel, reconnecting, simulated }: {
  s: RaceState; runId: string; label: string; budget: number; speed: number; setSpeed: (n: number) => void
  onCancel: () => void; reconnecting: boolean; simulated: boolean
}) {
  const running = s.status === 'running' || s.status === 'queued' || s.status === 'connecting'
  const now = useNow(running)
  const elapsed = s.startTs == null ? 0 : Math.max(0, (s.endTs ?? now) - s.startTs)
  const tau = s.timeLimit ?? budget
  const over = elapsed > tau
  const axis = scoreAxis(s)
  const best = bestSoFar(s)
  const all = Object.values(s.models)
  const nDone = all.filter((r) => r.status === 'done').length
  const nSkipped = all.filter((r) => r.status === 'skipped').length
  const metric = s.metric ? metricInfo(s.metric) : undefined
  const ens = s.lastEnsemble ? s.ensembles[s.lastEnsemble] : null
  const ensWeights = ens ? Object.entries(ens.weights).filter(([, w]) => w > 0).sort((a, b) => b[1] - a[1]) : []
  const ensZero = ens ? all.filter((r) => r.level < ens.stack_level && !isEnsemble(r.model, r.family) && r.status === 'done' && !(ens.weights[r.model] > 0)).map((r) => r.model) : []
  const L = layers(s)
  const ticks = axis ? Array.from({ length: 5 }, (_, i) => axis.lo + ((axis.hi - axis.lo) * (i + 0.5)) / 5) : []

  const pill = s.status === 'succeeded' ? 'COMPLETE' : s.status === 'failed' ? 'FAILED' : s.status === 'cancelled' ? 'CANCELLED' : s.status === 'queued' ? 'QUEUED' : 'TRAINING'
  return (
    <section className="stage race" data-testid="race" data-status={s.status}>
      <div className="stage-head">
        <div>
          <span className="hero-eyebrow" style={{ paddingLeft: 0 }}>{pill} · AUTOGLUON{simulated ? ' · SIMULATED' : ''} · <span data-testid="race-run-id">{runId}</span></span>
          <h2 className="hero-title">Predicting <span className="mono">{label}</span>{s.problemType ? <span className="race-pt"> · {s.problemType}</span> : null}</h2>
        </div>
        <div className="stage-read">
          <span><small>best so far · {s.metric ?? 'metric'}</small><b data-testid="race-best">{best ? fmtScore(best.done!.val_score_natural) : '—'}</b></span>
          <span><small>models</small><b data-testid="race-count">{nDone}<i className="race-of"> / {all.length - nSkipped}</i></b></span>
          <span><small>clock · <i className="nocase">t / τ</i></small><b data-testid="race-clock" className={over ? 'race-over' : ''}>{mmss(elapsed)}<i className="race-of"> / {mmss(tau)}</i></b></span>
        </div>
      </div>

      <div className="race-clock" title={`t = ${elapsed.toFixed(1)} s of τ = ${tau} s`}>
        <div className="race-track"><i style={{ width: `${Math.min(100, (elapsed / tau) * 100)}%` }} />{over && <b style={{ width: `${Math.min(30, ((elapsed - tau) / tau) * 100)}%` }} />}</div>
        <span className="ps-note">{running ? (STAGE_TEXT[s.stage] || 'Working') : s.status === 'succeeded' ? `done in ${mmss(elapsed)}${over ? ' · AutoGluon finishes the model in hand after τ' : ''}` : s.error ?? s.status}</span>
        {running && <ResonanceWave width={70} height={14} color="#38BDF8" />}
        {reconnecting && <span className="race-warn">▲ stream lost · resuming by replay</span>}
      </div>

      <div className="stage-body race-body">
        <div className="race-row race-axis">
          <span className="micro-label">Model · family</span>
          <div className="race-axis-track">
            {axis && ticks.map((v) => <span key={v} style={{ left: `${axis.frac(v) * 100}%` }}>{fmtScore(v, 3)}</span>)}
            {!axis && <span className="ps-note" style={{ left: 0 }}>validation {s.metric ?? 'score'} axis forms as models finish</span>}
          </div>
          <span className="micro-label race-r">validation {metric ? metric.name.toLowerCase() : s.metric ?? ''} {s.greater ? '↑' : '↓'}</span>
          <span className="micro-label race-r">fit</span>
        </div>

        {L.map((layer) => (
          <div key={layer.level} className="race-layer" data-testid={`race-layer-${layer.level}`}>
            {layer.base.length > 0 && <>
              <div className="race-layer-head">
                <span className="race-L">L{sub(layer.level)}</span>
                <span className="ps-note">{layer.level === 1 ? `base learners fₘ : X → ŷ, X ∈ ℝⁿˣᵈ${s.trainRows ? `, n = ${s.trainRows.toLocaleString()}, d = ${s.features.length}` : ''}` : `stackers on [X, f₁(x), …, f_M(x)]: each sees the original features plus the layer below's out-of-fold predictions`}</span>
              </div>
              {layer.base.map((r) => <Row key={r.model} r={r} axis={axis} best={best?.model === r.model} kFold={s.kFold} />)}
            </>}
            {layer.ensembles.length > 0 && <>
              <div className="race-layer-head">
                <span className="race-L">L{sub(layer.level)}</span>
                <span className="ps-note">weighted ensemble ŷ = Σₘ wₘ fₘ(x) over the layer below</span>
              </div>
              {layer.ensembles.map((r) => <Row key={r.model} r={r} axis={axis} best={best?.model === r.model} kFold={s.kFold} />)}
            </>}
            {layer.skipped > 0 && <div className="race-skipped ps-note">+ {layer.skipped} configuration{layer.skipped > 1 ? 's' : ''} at L{sub(layer.level)} skipped: the time budget ran out before they could fit</div>}
          </div>
        ))}
        {!L.length && <div className="race-empty ps-note">{s.status === 'queued' ? 'Queued: another fit is running; this one starts when it ends.' : 'Reading the data, resolving the configuration…'}</div>}

        {ens && ensWeights.length > 0 && (
          <div className="race-ens" data-testid="race-ensemble">
            <div className="race-layer-head"><span className="race-L">Σ</span><span className="ps-note">{ens.model} forms: the strings merge into one, each thick in proportion to its weight wₘ</span></div>
            <EnsembleMerge weights={ensWeights} others={ensZero.slice(0, 6)} name={ens.model} />
            <StackEquation model={ens.model} weights={ens.weights} dark testId="race-equation" problemType={s.problemType} K={s.numClasses}
              scoreLabel={ens.val_score_natural != null ? `validation ${s.metric} = ${fmtScore(ens.val_score_natural)}` : undefined} />
          </div>
        )}
      </div>

      <div className="stage-foot">
        <span className="ps-note">each model stays on screen ≥ {(1.4 / speed).toFixed(2)} s</span>
        <div className="seg stage-seg" data-testid="race-speed">{[1, 3].map((n) => <button key={n} className={speed === n ? 'on' : ''} onClick={() => setSpeed(n)}>{n}×</button>)}</div>
        <span className="spacer" />
        <span className="ps-note">τ = {fmtBudget(tau)}{s.kFold ? ` · k = ${s.kFold} folds` : ' · no bagging'}</span>
        {running && <button className="btn sm" data-testid="race-cancel" onClick={onCancel}>Cancel Run</button>}
      </div>
    </section>
  )
}

function Row({ r, axis, best, kFold }: { r: ModelRow; axis: ReturnType<typeof scoreAxis>; best: boolean; kFold: number | null }) {
  const d = r.done
  const v = d?.val_score_natural
  const x = v != null && axis ? axis.frac(v) : null
  const folds = d?.fold_scores_natural?.filter((f): f is number => f != null) ?? []
  const mu = v
  const sd = d?.val_score_std ?? null
  const band: [number, number] | null = mu != null && sd != null && axis ? [Math.min(axis.frac(mu - sd), axis.frac(mu + sd)), Math.max(axis.frac(mu - sd), axis.frac(mu + sd))] : null
  return (
    <div className={`race-row ${best ? 'best' : ''}`} data-testid={`race-model-${r.model}`} data-status={r.status}>
      <span className="race-name"><b title={r.model}>{r.model}</b><small>{(r.family && FAMILY_SHORT[r.family]) ?? r.family ?? ''}</small></span>
      <ModelString status={r.status} x={x} folds={axis ? folds.map((f) => axis.frac(f)) : []} band={band} seed={r.order * 7 + r.model.length} best={best} />
      <span className="race-read">
        {r.status === 'fitting' && <span className="race-fitting">fitting…</span>}
        {r.status === 'done' && <>{best && <span className="race-star" title="best validation score so far">★ </span>}<b>{fmtScore(v)}</b>{sd != null && <small> ± {fmtScore(sd)}</small>}<small className="race-n" title={folds.length ? `${folds.length} fold models, each scored on its held-out fold` : r.bagged && kFold ? 'scored on out-of-fold predictions (one score, no per-fold spread)' : 'scored on the tuning split'}>{folds.length ? ` n = ${folds.length}` : r.bagged && kFold ? ' out-of-fold' : ' val split'}</small></>}
        {(r.status === 'failed' || r.status === 'skipped') && <small title={r.error}>{r.status === 'skipped' ? 'skipped · out of time' : `✕ ${r.error?.slice(0, 40)}`}</small>}
      </span>
      <span className="race-fit">{d?.fit_time != null ? fmtSeconds(d.fit_time) : ''}</span>
    </div>
  )
}

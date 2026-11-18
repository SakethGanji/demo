import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { LatencyModel, LatencyProfile, ModelVersion, Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks, padDomain } from '@/shared/components/XYPlot'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { bytes, us } from '../lib'

const sanitize = (s: string) => s.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 100)

/** Register a finished run: pick the deployment options and see what they trade, measured on this run's models. */
export function RegisterPanel({ runs, initialRun, actor, onRegistered, onCancel }: {
  runs: Run[]; initialRun: string | null; actor: string; onRegistered: (v: ModelVersion) => void; onCancel: () => void
}) {
  const [runId, setRunId] = useState(initialRun && runs.some((r) => r.id === initialRun) ? initialRun : runs[0]?.id ?? '')
  const run = runs.find((r) => r.id === runId) ?? null
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [refit, setRefit] = useState(false)
  const [useLimit, setUseLimit] = useState(false)
  const [limitUs, setLimitUs] = useState(50)
  const [deploy, setDeploy] = useState(true)
  const [serve, setServe] = useState<string | null>(null)
  // F3: one-row serving
  const [oneRow, setOneRow] = useState(true)        // infer_limit_batch_size = 1: the budget is per one-row call
  const [limitMs, setLimitMs] = useState(10)
  const [fastest, setFastest] = useState(false)
  const [distill, setDistill] = useState(false)
  const [distillT, setDistillT] = useState(120)
  const [prune, setPrune] = useState(true)
  const [axis, setAxis] = useState<'one' | 'batch'>('one')
  const [lp, setLp] = useState<LatencyProfile | null>(null)
  const [lpErr, setLpErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { if (run) setName(sanitize(`${run.dataset_id}.${run.label}`)) }, [run?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!runId) return
    let alive = true
    setLp(null); setLpErr(''); setServe(null)
    api.registry.latency(runId).then((p) => {
      if (!alive) return
      setLp(p)
      const best = p.models.find((m) => m.best)
      if (best) setLimitUs(Math.max(1, Math.round(best.us_per_row * 0.6)))
    }).catch((e) => alive && setLpErr((e as Error).message))
    return () => { alive = false }
  }, [runId])

  const metric = lp?.eval_metric ?? run?.eval_metric?.name ?? ''
  const greater = lp?.greater_is_better ?? true
  const better = (a: number, b: number) => (greater ? a > b : a < b)
  const served = (m: LatencyModel) => m.served_ms_p95 ?? m.single_ms_p95 ?? Infinity
  const chosen = useMemo(() => {
    if (!lp) return null
    if (serve) return lp.models.find((m) => m.model === serve) ?? null
    if (fastest) return [...lp.models].sort((a, b) => served(a) - served(b))[0] ?? null
    if (!useLimit) return lp.models.find((m) => m.best) ?? null
    const t = (m: LatencyModel) => (oneRow ? served(m) : m.us_per_row)
    const lim = oneRow ? limitMs : limitUs
    const ok = lp.models.filter((m) => t(m) <= lim && m.score_val_natural != null)
    if (!ok.length) return [...lp.models].sort((a, b) => t(a) - t(b))[0]
    return ok.reduce((a, b) => (better(b.score_val_natural!, a.score_val_natural!) ? b : a))
  }, [lp, useLimit, limitUs, limitMs, oneRow, serve, fastest]) // eslint-disable-line react-hooks/exhaustive-deps
  const met = serve != null || fastest || !useLimit || (chosen != null && (oneRow ? served(chosen) <= limitMs : chosen.us_per_row <= limitUs))
  const bestM = lp?.models.find((m) => m.best) ?? null
  const cost = chosen && bestM && chosen.score_test_natural != null && bestM.score_test_natural != null ? chosen.score_test_natural - bestM.score_test_natural : null

  const submit = async () => {
    if (!run) return
    setBusy(true); setErr('')
    try {
      const v = await api.registry.register(run.id, {
        created_by: actor, model_name: name || undefined, description: desc, refit_full: refit,
        infer_limit: useLimit && !serve && !fastest ? (oneRow ? limitMs / 1e3 : limitUs / 1e6) : null,
        infer_limit_batch_size: useLimit && !serve && !fastest ? (oneRow ? 1 : null) : null,
        clone_for_deployment: deploy, serve_model: serve, serve_fastest: fastest && !serve, prune_pipeline: prune,
        distill, distill_time_limit: distillT,
      })
      onRegistered(v)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const pts = lp?.models ?? []
  const oneAxis = axis === 'one' && pts.some((m) => m.served_ms_p95 != null)
  const xv = (m: LatencyModel) => (oneAxis ? served(m) : m.us_per_row)
  const lx = pts.map((m) => Math.log10(Math.max(0.01, xv(m))))
  const limX = useLimit && !serve && !fastest && oneAxis === oneRow ? Math.log10(oneRow ? limitMs : limitUs) : null
  const [x0, x1] = padDomain([...lx, ...(limX != null ? [limX] : [])], 0.12)
  const front = oneAxis && lp?.frontier ? lp.frontier.map((n) => pts.findIndex((m) => m.model === n)).filter((i) => i >= 0).sort((a, b) => lx[a] - lx[b]) : []
  const scores = pts.map((m) => m.score_val_natural ?? m.score_test_natural ?? 0)
  const [y0, y1] = padDomain(scores, 0.3)
  const xt = niceTicks(x0, x1, 5)
  const maxB = Math.max(1, ...pts.map((m) => m.size_bytes))
  const info = metricInfo(metric)

  return (
    <div className="card register" data-testid="register-panel">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Register A Run</h3><span className="helper" style={{ fontSize: 12 }}>a new version gets its own predictor folder; the run is never changed</span></div>
        <button className="btn sm ghost" onClick={onCancel}>Close</button>
      </div>
      <div className="card-body reg-grid">
        <div className="stack" style={{ gap: 'var(--space-12)' }}>
          <label className="reg-field"><span className="micro-label">source run</span>
            <select className="select" data-testid="register-run" value={runId} onChange={(e) => setRunId(e.target.value)}>
              {runs.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.name} · {r.best_model}</option>)}
            </select>
          </label>
          <label className="reg-field"><span className="micro-label">registered model</span>
            <input className="input mono" data-testid="register-name" value={name} onChange={(e) => setName(sanitize(e.target.value))} />
          </label>
          <label className="reg-field"><span className="micro-label">note for the reviewer</span>
            <input className="input" data-testid="register-desc" placeholder="what changed, why it should ship" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </label>

          <div className="reg-opts">
            <label className="reg-opt"><input type="checkbox" data-testid="opt-refit" checked={refit} onChange={(e) => setRefit(e.target.checked)} />
              <div><b className="mono">refit_full</b><small>Retrain on train + validation rows with no bagging: one model per family instead of k fold models. Faster to predict; its validation score is its parent's (refit copies have no held-out data).</small></div>
            </label>
            <label className="reg-opt"><input type="checkbox" data-testid="opt-limit" checked={useLimit} onChange={(e) => setUseLimit(e.target.checked)} />
              <div><b className="mono">infer_limit</b><small>A latency budget. The best-validation model that fits it becomes the served model (<span className="mono">get_model_best(infer_limit=…)</span>). With <span className="mono">infer_limit_batch_size = 1</span> the budget is per one-row call, measured as served (τ₉₅); otherwise per row in batches of 1,000.</small>
                {useLimit && <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
                  <span className="seg" role="tablist" aria-label="batch size">
                    <button className={oneRow ? 'on' : ''} data-testid="opt-limit-one" onClick={(e) => { e.preventDefault(); setOneRow(true); setAxis('one') }}>batch 1 (online)</button>
                    <button className={!oneRow ? 'on' : ''} onClick={(e) => { e.preventDefault(); setOneRow(false); setAxis('batch') }}>batch 1,000</button>
                  </span>
                  {oneRow
                    ? <><input className="num-input" type="number" min={0.5} step="any" style={{ width: 80 }} data-testid="opt-limit-ms" value={limitMs} onChange={(e) => setLimitMs(Math.max(0.5, Number(e.target.value)))} />
                      <span className="mono" style={{ fontSize: 12 }}>ms per call = {(limitMs / 1e3).toExponential(1)} s</span></>
                    : <><input className="num-input" type="number" min={1} step="any" style={{ width: 90 }} data-testid="opt-limit-us" value={limitUs} onChange={(e) => setLimitUs(Math.max(0.5, Number(e.target.value)))} />
                      <span className="mono" style={{ fontSize: 12 }}>µs / row = {(limitUs / 1e6).toExponential(1)} s</span>
                      <input type="range" min={0} max={4} step={0.01} value={Math.log10(limitUs)} onChange={(e) => setLimitUs(Number((10 ** Number(e.target.value)).toPrecision(3)))} aria-label="Latency budget (log scale)" style={{ flex: 1 }} /></>}
                </div>}
              </div>
            </label>
            <label className="reg-opt"><input type="checkbox" data-testid="opt-deploy" checked={deploy} onChange={(e) => setDeploy(e.target.checked)} />
              <div><b className="mono">clone_for_deployment</b><small>Keep only the served model and the models it needs; drop training artefacts (<span className="mono">save_space()</span>). Smaller folder, same predictions.</small></div>
            </label>
            <label className="reg-opt"><input type="checkbox" data-testid="opt-fastest" checked={fastest} onChange={(e) => { setFastest(e.target.checked); if (e.target.checked) { setServe(null); setUseLimit(false); setAxis('one') } }} />
              <div><b className="mono">serve the fastest model</b><small>m* = argmin τ₉₅(m): the single model with the lowest measured one-row p95 as served, whatever its score. Its score cost is shown below.</small></div>
            </label>
          </div>
          <details className="reg-adv"><summary className="micro-label">Advanced · distillation, pipeline pruning</summary>
            <div className="reg-opts" style={{ marginTop: 6 }}>
              <label className="reg-opt"><input type="checkbox" data-testid="opt-distill" checked={distill} onChange={(e) => setDistill(e.target.checked)} />
                <div><b className="mono">distill</b><small><span className="mono">predictor.distill(teacher_preds='soft')</span>: fit one LightGBM student to the ensemble’s predicted probabilities on the training rows plus augmented rows; the student is served unless a rule above picks. One model to call instead of the ensemble; its holdout score is measured after the build.</small>
                  {distill && <div className="row" style={{ gap: 8, marginTop: 6 }}><input className="num-input" type="number" min={10} style={{ width: 70 }} value={distillT} onChange={(e) => setDistillT(Math.max(10, Number(e.target.value)))} /><span className="mono" style={{ fontSize: 12 }}>s time limit</span></div>}
                </div>
              </label>
              <label className="reg-opt"><input type="checkbox" data-testid="opt-prune" checked={prune} disabled={!deploy} onChange={(e) => setPrune(e.target.checked)} />
                <div><b className="mono">prune the feature pipeline</b><small>With clone_for_deployment: keep only the pipeline stages and features the served model reads (a TF-IDF model reads the raw text, not the {'~'}1,000 n-gram columns). Kept only if the holdout predictions are unchanged.</small></div>
              </label>
            </div>
          </details>

          {lp && <OneRowLatency lp={lp} serve={serve} onServe={(m) => { setServe(m); if (m) { setUseLimit(false); setFastest(false) } }} refit={refit} />}

          <pre className="reg-call" data-testid="register-call">{[
            `clone = TabularPredictor.load("${run?.id ?? 'run'}/predictor").clone(path)`,
            refit ? `clone.refit_full(model=${useLimit ? "'all'" : "'best'"})` : null,
            distill ? `clone.distill(time_limit=${distillT}, hyperparameters={'GBM': {}}, teacher_preds='soft')` : null,
            serve ? `clone.set_model_best("${serve}${refit && !serve.endsWith('_FULL') ? '_FULL' : ''}")   # one-row p95 ${fmtMs(lp?.models.find((m) => m.model === serve)?.served_ms_p95 ?? lp?.models.find((m) => m.model === serve)?.single_ms_p95)} as served`
              : fastest ? 'clone.set_model_best(argmin τ95(m))   # measured one-row, as served'
                : useLimit ? (oneRow ? `clone.set_model_best(best { m : τ95(m) ≤ ${limitMs} ms })   # infer_limit=${(limitMs / 1e3).toExponential(1)}, infer_limit_batch_size=1` : `clone.set_model_best(best { m : t̂(m) ≤ ${us(limitUs)} µs/row })   # measured`) : null,
            deploy ? 'clone.clone_for_deployment(version_path)' : 'clone.clone(version_path)',
            deploy && prune ? 'prune feature pipeline to the served model’s inputs   # predictions checked unchanged' : null,
          ].filter(Boolean).join('\n')}</pre>

          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" data-testid="register-submit" disabled={!run || !actor.trim() || busy} onClick={submit}>Register version →</button>
            {busy && <ResonanceWave width={60} height={14} />}
            {!actor.trim() && <span className="helper" style={{ fontSize: 12, color: 'var(--status-warning)' }}>▲ type your name in “acting as” first: it goes in the audit log</span>}
          </div>
          {err && <p role="alert" className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>

        <PlotFrame title="Latency · Score Frontier" right={<span className="seg" role="tablist" aria-label="latency axis">
            <button className={oneAxis ? 'on' : ''} data-testid="frontier-axis-one" onClick={() => setAxis('one')}>one row τ₉₅</button>
            <button className={!oneAxis ? 'on' : ''} onClick={() => setAxis('batch')}>batch t̂</button></span>}
          chips={lp ? <><ParamChip sym="n" value={oneAxis ? 'one-row calls' : `${lp.batch_rows} rows`} /><ParamChip sym="metric" value={metric} />{lp.cpus ? <ParamChip sym="CPUs" value={String(lp.cpus)} /> : null}</> : undefined}
          caption={<span>Each dot is a model m of {runId}. {oneAxis
            ? <>x = τ₉₅(m), the 95th percentile of one-row call time as m would be served: feature pipeline pruned to what m reads, m and the models it needs persisted (log scale).</>
            : <>x = t̂(m), measured batch prediction time per row on n holdout rows (median of 3 runs, log scale, including every model m needs).</>} y = s_val(m), its validation {info?.name ?? metric} (what the selection ranks by); area ∝ bytes on disk. {oneAxis && front.length ? 'The line joins the frontier: models no other model beats on both τ₉₅ and s_val. ' : ''}{limX != null ? `The dashed line is the budget λ. ` : ''}Click a dot to serve that model. The ringed model is what this version would serve{refit ? ' (re-measured on its _FULL copy after refit)' : ''}.</span>}>
          {!lp && !lpErr && <div className="row helper"><ResonanceWave /> measuring every model’s prediction time on the holdout, in a worker process…</div>}
          {lpErr && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {lpErr}</p>}
          {lp && (
            <XYPlot height={250} testId="frontier" ariaLabel="latency vs score"
              x={{ min: x0, max: x1, ticks: xt, title: oneAxis ? 'τ₉₅ (ms per one-row call)' : 't̂ (µs / row)', fmt: (v) => (oneAxis ? fmtMs(10 ** v) : us(10 ** v)) }}
              y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 5), title: `s_val · ${metric}`, fmt: (v) => fmtScore(v, 3) }}
              series={[]}
              refs={limX != null ? [{ x: limX, label: oneRow ? `λ = ${limitMs} ms` : `λ = ${us(limitUs)} µs`, color: 'var(--status-warning)', dash: '5 4', strong: true }] : []}
            >{(sx, sy) => {
              // labels: right of the dot, or left near the right edge; pushed down when they would overlap
              const placed: { x: number; y: number; w: number }[] = []
              const maxX = sx(x1)
              return <g>{front.length > 1 && <polyline data-testid="frontier-line" points={front.map((i) => `${sx(lx[i])},${sy(scores[i])}`).join(' ')} fill="none" stroke="var(--accent-primary)" strokeWidth={1.25} strokeDasharray="4 3" opacity={0.8} />}
                {pts.map((m, i) => {
                const r = 4 + 10 * Math.sqrt(m.size_bytes / maxB)
                const on = chosen?.model === m.model
                const within = limX == null || 10 ** lx[i] <= 10 ** limX
                const cx = sx(lx[i]), cy = sy(scores[i])
                const w = m.model.length * 6.6
                const left = cx + r + 4 + w > maxX
                let lx0 = left ? cx - r - 4 - w : cx + r + 4, ly = cy + 4
                while (placed.some((q) => Math.abs(q.y - ly) < 11 && lx0 < q.x + q.w && q.x < lx0 + w)) ly += 11
                placed.push({ x: lx0, y: ly, w })
                return <g key={m.model} data-testid={`frontier-${m.model}`} style={{ cursor: 'pointer' }} onClick={() => { setServe(m.best ? null : m.model); setFastest(false); if (!m.best) setUseLimit(false) }}>
                  <title>{`${m.model} · τ₉₅ ${fmtMs(m.served_ms_p95 ?? m.single_ms_p95)} as served · t̂ ${us(m.us_per_row)} µs/row · s_val ${fmtScore(m.score_val_natural, 4)} · holdout ${fmtScore(m.score_test_natural, 4)}`}</title>
                  <circle cx={cx} cy={cy} r={r} fill={within ? (m.on_frontier && oneAxis ? 'var(--viz-b)' : 'var(--viz-a)') : 'var(--text-muted)'} opacity={within ? 0.6 : 0.25} />
                  {on && <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke="var(--accent-primary)" strokeWidth={2} />}
                  {ly !== cy + 4 && <line x1={left ? lx0 + w : lx0} x2={cx} y1={ly - 4} y2={cy} stroke="var(--border-track)" strokeWidth={0.75} />}
                  <text x={lx0} y={ly} className="tick-text" style={{ fill: on ? 'var(--text-primary)' : 'var(--text-muted)', fontWeight: on ? 700 : 400 }}>{m.model}</text>
                </g>
              })}            </g>}}</XYPlot>
          )}
          {lp && chosen && (
            <div className="reg-pick" data-testid="register-preview">
              <span className="micro-label">served by this version</span>
              <b className="mono">{chosen.model}{refit && !chosen.model.endsWith('_FULL') ? '_FULL' : ''}</b>
              <span className="mono">τ₉₅ = {fmtMs(chosen.served_ms_p95 ?? chosen.single_ms_p95)} one row as served{chosen.pipeline?.pruned && prune && deploy ? ` (pipeline ${chosen.pipeline.n_features_out_before?.toLocaleString()} → ${chosen.pipeline.n_features_out.toLocaleString()} features)` : ''} · t̂ = {us(chosen.us_per_row)} µs/row · s_val = {fmtScore(chosen.score_val_natural, 4)} · holdout {fmtScore(chosen.score_test_natural, 4)} {metric} · {chosen.n_models} model{chosen.n_models > 1 ? 's' : ''} · {bytes(chosen.size_bytes)}{deploy ? '' : ` of ${bytes(lp.total_size_bytes)} kept`}</span>
              {bestM && chosen.model !== bestM.model && cost != null && <span className="mono" data-testid="register-cost">cost against {bestM.model}: Δ holdout {info?.name ?? metric} = <b className={(greater ? cost >= 0 : cost <= 0) ? 'g-ok' : 'g-bad'}>{cost >= 0 ? '+' : ''}{fmtScore(cost, 4)}</b> for τ₉₅ {fmtMs(served(bestM))} → {fmtMs(served(chosen))} ({(served(bestM) / served(chosen)).toFixed(1)}× faster)</span>}
              {distill && <span className="helper" style={{ fontSize: 12 }}>distillation adds a student measured after the build; it is not on this plot yet</span>}
              {!met && <span style={{ color: 'var(--status-warning)' }}>▲ no model meets λ; the fastest is kept and the version is flagged</span>}
            </div>
          )}
        </PlotFrame>
      </div>
    </div>
  )
}

const fmtMs = (v: number | null | undefined) => (v == null ? '—' : v >= 100 ? `${v.toFixed(0)} ms` : v >= 10 ? `${v.toFixed(1)} ms` : `${v.toFixed(2)} ms`)

/** One-row latency, measured: what Try It, a REST call or a stream scoring one event at a time waits for. Batch
 *  µs/row (the frontier) hides it: a text GBM ensemble featurises n-grams and runs every bagged child on each call.
 *  Offers the fastest model whose holdout score is close, with its measured score cost, as the model to serve. */
function OneRowLatency({ lp, serve, onServe, refit }: { lp: LatencyProfile; serve: string | null; onServe: (m: string | null) => void; refit: boolean }) {
  const best = lp.models.find((m) => m.best)
  if (!best || best.served_ms_p95 == null) return null
  const g = lp.greater_is_better
  const info = metricInfo(lp.eval_metric)
  // A faster option: at most half the best model's one-row p95, best holdout score among those.
  const fast = lp.models.filter((m) => !m.best && m.served_ms_p95 != null && m.served_ms_p95 <= best.served_ms_p95! * 0.5 && m.score_test_natural != null)
  const alt = fast.length ? fast.reduce((a, b) => ((g ? b.score_test_natural! > a.score_test_natural! : b.score_test_natural! < a.score_test_natural!) ? b : a)) : null
  const d = alt && best.score_test_natural != null ? alt.score_test_natural! - best.score_test_natural : null
  const slow = best.served_ms_p95 > 50
  return (
    <div className={`reg-onerow ${slow ? 'warn' : ''}`} data-testid="register-onerow">
      <span className="micro-label">one-row latency · measured</span>
      <div className="math ro-eq">τ<sub>95</sub>(<span className="mono">{best.model}</span>) = <b data-testid="onerow-best-p95">{fmtMs(best.served_ms_p95)}</b> <span className="muted">(p50 {fmtMs(best.served_ms_p50)}, {best.served_calls ?? '—'} calls)</span></div>
      <p className="helper">τ₉₅ = the 95th percentile of wall time over one-row <span className="mono">predict_proba</span> calls on holdout rows, in a worker process, with the model persisted and the feature pipeline pruned to what it reads (as a deployed copy is served): what an online API waits for{best.single_ms_p95 != null && best.served_ms_p95 != null && Math.abs(best.single_ms_p95 - best.served_ms_p95) > 0.5 ? ` (${fmtMs(best.single_ms_p95)} with the whole pipeline)` : ''}. The frontier’s t̂ is batch time ÷ rows and hides it{lp.bagged ? `; this run is bagged, so every call runs each fold model` : ''}.{slow ? ' Above 50 ms this model is a batch scorer, not an online one.' : ''}</p>
      {alt && d != null && (
        <div className="ro-alt" data-testid="register-faster">
          <div><b>Faster to serve:</b> <span className="mono">{alt.model}</span> · τ₉₅ <b>{fmtMs(alt.served_ms_p95)}</b> <span className="muted">({(alt.served_ms_p95! / best.served_ms_p95).toFixed(2)}×)</span> · holdout {info?.name ?? lp.eval_metric} {fmtScore(alt.score_test_natural, 4)} vs {fmtScore(best.score_test_natural, 4)} → <b className={(g ? d >= 0 : d <= 0) ? 'g-ok' : 'g-bad'} data-testid="register-faster-delta">Δ {d >= 0 ? '+' : ''}{fmtScore(d, 4)}</b></div>
          <div className="row" style={{ gap: 8 }}>
            {serve === alt.model
              ? <><span className="pill info">serving {alt.model}{refit && !alt.model.endsWith('_FULL') ? '_FULL' : ''}</span><button className="btn sm ghost" onClick={() => onServe(null)}>Serve the best model</button></>
              : <button className="btn sm" onClick={() => onServe(alt.model)} data-testid="register-serve-faster">Serve {alt.model}</button>}
            {lp.bagged && !refit && <span className="helper" style={{ fontSize: 12 }}>or tick refit_full: one model per family instead of k folds (measured after the build)</span>}
          </div>
        </div>
      )}
    </div>
  )
}

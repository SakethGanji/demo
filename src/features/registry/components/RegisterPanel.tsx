import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { LatencyProfile, ModelVersion, Run } from '@/shared/api/types'
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
  const chosen = useMemo(() => {
    if (!lp) return null
    if (serve) return lp.models.find((m) => m.model === serve) ?? null
    if (!useLimit) return lp.models.find((m) => m.best) ?? null
    const ok = lp.models.filter((m) => m.us_per_row <= limitUs && m.score_val_natural != null)
    if (!ok.length) return [...lp.models].sort((a, b) => a.us_per_row - b.us_per_row)[0]
    return ok.reduce((a, b) => (better(b.score_val_natural!, a.score_val_natural!) ? b : a))
  }, [lp, useLimit, limitUs, serve]) // eslint-disable-line react-hooks/exhaustive-deps
  const met = serve != null || !useLimit || (chosen != null && chosen.us_per_row <= limitUs)

  const submit = async () => {
    if (!run) return
    setBusy(true); setErr('')
    try {
      const v = await api.registry.register(run.id, {
        created_by: actor, model_name: name || undefined, description: desc, refit_full: refit,
        infer_limit: useLimit && !serve ? limitUs / 1e6 : null, clone_for_deployment: deploy, serve_model: serve,
      })
      onRegistered(v)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const pts = lp?.models ?? []
  const lx = pts.map((m) => Math.log10(Math.max(0.01, m.us_per_row)))
  const [x0, x1] = padDomain([...lx, ...(useLimit ? [Math.log10(limitUs)] : [])], 0.12)
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
              <div><b className="mono">infer_limit</b><small>A latency budget per row. The best-validation model whose measured batch time per row fits it becomes the served model (<span className="mono">get_model_best(infer_limit=…)</span>).</small>
                {useLimit && <div className="row" style={{ gap: 8, marginTop: 6 }}>
                  <input className="num-input" type="number" min={1} step="any" style={{ width: 90 }} data-testid="opt-limit-us" value={limitUs} onChange={(e) => setLimitUs(Math.max(0.5, Number(e.target.value)))} />
                  <span className="mono" style={{ fontSize: 12 }}>µs / row = {(limitUs / 1e6).toExponential(1)} s</span>
                  <input type="range" min={0} max={4} step={0.01} value={Math.log10(limitUs)} onChange={(e) => setLimitUs(Number((10 ** Number(e.target.value)).toPrecision(3)))} aria-label="Latency budget (log scale)" style={{ flex: 1 }} />
                </div>}
              </div>
            </label>
            <label className="reg-opt"><input type="checkbox" data-testid="opt-deploy" checked={deploy} onChange={(e) => setDeploy(e.target.checked)} />
              <div><b className="mono">clone_for_deployment</b><small>Keep only the served model and the models it needs; drop training artefacts (<span className="mono">save_space()</span>). Smaller folder, same predictions.</small></div>
            </label>
          </div>

          {lp && <OneRowLatency lp={lp} serve={serve} onServe={(m) => { setServe(m); if (m) setUseLimit(false) }} refit={refit} />}

          <pre className="reg-call" data-testid="register-call">{[
            `clone = TabularPredictor.load("${run?.id ?? 'run'}/predictor").clone(path)`,
            refit ? `clone.refit_full(model=${useLimit ? "'all'" : "'best'"})` : null,
            serve ? `clone.set_model_best("${serve}${refit && !serve.endsWith('_FULL') ? '_FULL' : ''}")   # one-row p95 ${fmtMs(lp?.models.find((m) => m.model === serve)?.single_ms_p95)}` : useLimit ? `clone.set_model_best(best { m : t̂(m) ≤ ${us(limitUs)} µs/row })   # measured` : null,
            deploy ? 'clone.clone_for_deployment(version_path)' : 'clone.clone(version_path)',
          ].filter(Boolean).join('\n')}</pre>

          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" data-testid="register-submit" disabled={!run || !actor.trim() || busy} onClick={submit}>Register version →</button>
            {busy && <ResonanceWave width={60} height={14} />}
            {!actor.trim() && <span className="helper" style={{ fontSize: 12, color: 'var(--status-warning)' }}>▲ type your name in “acting as” first: it goes in the audit log</span>}
          </div>
          {err && <p role="alert" className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>

        <PlotFrame title="Latency · Score Frontier" right={<span className="helper" style={{ fontSize: 12 }}>every model of {runId} that can predict</span>}
          chips={lp ? <><ParamChip sym="n" value={`${lp.batch_rows} rows`} /><ParamChip sym="metric" value={metric} /></> : undefined}
          caption={<span>Each dot is a model m: x = t̂(m), measured batch prediction time per row on n holdout rows (median of 3 runs, log scale, including every model m needs); y = s_val(m), its validation {info?.name ?? metric} (what the selection ranks by); area ∝ bytes on disk. {useLimit ? `The dashed line is the budget τ = ${us(limitUs)} µs/row. ` : ''}The ringed model is what this version would serve{refit ? ' (re-measured on its _FULL copy after refit)' : ''}.</span>}>
          {!lp && !lpErr && <div className="row helper"><ResonanceWave /> measuring every model’s prediction time on the holdout, in a worker process…</div>}
          {lpErr && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {lpErr}</p>}
          {lp && (
            <XYPlot height={250} testId="frontier" ariaLabel="latency vs score"
              x={{ min: x0, max: x1, ticks: xt, title: 't̂ (µs / row)', fmt: (v) => us(10 ** v) }}
              y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 5), title: `s_val · ${metric}`, fmt: (v) => fmtScore(v, 3) }}
              series={[]}
              refs={useLimit ? [{ x: Math.log10(limitUs), label: `τ = ${us(limitUs)} µs`, color: 'var(--status-warning)', dash: '5 4', strong: true }] : []}
            >{(sx, sy) => {
              // labels: right of the dot, or left near the right edge; pushed down when they would overlap
              const placed: { x: number; y: number; w: number }[] = []
              const maxX = sx(x1)
              return <g>{pts.map((m, i) => {
                const r = 4 + 10 * Math.sqrt(m.size_bytes / maxB)
                const on = chosen?.model === m.model
                const within = !useLimit || m.us_per_row <= limitUs
                const cx = sx(lx[i]), cy = sy(scores[i])
                const w = m.model.length * 6.6
                const left = cx + r + 4 + w > maxX
                let lx0 = left ? cx - r - 4 - w : cx + r + 4, ly = cy + 4
                while (placed.some((q) => Math.abs(q.y - ly) < 11 && lx0 < q.x + q.w && q.x < lx0 + w)) ly += 11
                placed.push({ x: lx0, y: ly, w })
                return <g key={m.model} data-testid={`frontier-${m.model}`}>
                  <circle cx={cx} cy={cy} r={r} fill={within ? 'var(--viz-a)' : 'var(--text-muted)'} opacity={within ? 0.55 : 0.25} />
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
              <span className="mono">t̂ = {us(chosen.us_per_row)} µs/row · s_val = {fmtScore(chosen.score_val_natural, 4)} · holdout {fmtScore(chosen.score_test_natural, 4)} {metric} · {chosen.n_models} model{chosen.n_models > 1 ? 's' : ''} · {bytes(chosen.size_bytes)}{deploy ? '' : ` of ${bytes(lp.total_size_bytes)} kept`}</span>
              {!met && <span style={{ color: 'var(--status-warning)' }}>▲ no model meets τ; the fastest is kept and the version is flagged</span>}
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
  if (!best || best.single_ms_p95 == null) return null
  const g = lp.greater_is_better
  const info = metricInfo(lp.eval_metric)
  // A faster option: at most half the best model's one-row p95, best holdout score among those.
  const fast = lp.models.filter((m) => !m.best && m.single_ms_p95 != null && m.single_ms_p95 <= best.single_ms_p95! * 0.5 && m.score_test_natural != null)
  const alt = fast.length ? fast.reduce((a, b) => ((g ? b.score_test_natural! > a.score_test_natural! : b.score_test_natural! < a.score_test_natural!) ? b : a)) : null
  const d = alt && best.score_test_natural != null ? alt.score_test_natural! - best.score_test_natural : null
  const slow = best.single_ms_p95 > 50
  return (
    <div className={`reg-onerow ${slow ? 'warn' : ''}`} data-testid="register-onerow">
      <span className="micro-label">one-row latency · measured</span>
      <div className="math ro-eq">τ<sub>95</sub>(<span className="mono">{best.model}</span>) = <b data-testid="onerow-best-p95">{fmtMs(best.single_ms_p95)}</b> <span className="muted">(p50 {fmtMs(best.single_ms_p50)}, {best.single_calls ?? '—'} calls)</span></div>
      <p className="helper">τ₉₅ = the 95th percentile of wall time over one-row <span className="mono">predict_proba</span> calls on holdout rows, in a worker process: what Try It or an online API waits for. The frontier’s t̂ is batch time ÷ rows and hides it{lp.bagged ? `; this run is bagged, so every call runs each fold model` : ''}.{slow ? ' Above 50 ms this model is a batch scorer, not an online one.' : ''}</p>
      {alt && d != null && (
        <div className="ro-alt" data-testid="register-faster">
          <div><b>Faster to serve:</b> <span className="mono">{alt.model}</span> · τ₉₅ <b>{fmtMs(alt.single_ms_p95)}</b> <span className="muted">({(alt.single_ms_p95! / best.single_ms_p95).toFixed(2)}×)</span> · holdout {info?.name ?? lp.eval_metric} {fmtScore(alt.score_test_natural, 4)} vs {fmtScore(best.score_test_natural, 4)} → <b className={(g ? d >= 0 : d <= 0) ? 'g-ok' : 'g-bad'} data-testid="register-faster-delta">Δ {d >= 0 ? '+' : ''}{fmtScore(d, 4)}</b></div>
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

import { Calibration, type FcCalibration } from './components/Calibration'
import type { FcBand } from '@/shared/api/flows'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '@/shared/api/client'
import { flows, type FcChart, type FcCreate, type FcEnsembleKind, type FcMapping, type FcOptions, type FcPreview, type FlowRun } from '@/shared/api/flows'
import type { DatasetDetail, DatasetSummary } from '@/shared/api/types'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { FAMILY_COLOR, LiveModels, Register } from '@/shared/components/FlowParts'
import { FanChart, fmtNum } from './components/FanChart'
import { fix, pct } from '@/shared/lib/format'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { ErrorCard, HeroCell, Loading } from '@/shared/components/Status'
import { EmptyState } from '@/shared/components/EmptyState'


/** New Forecast: AutoGluon's TimeSeriesPredictor on long-format data (item, time, target, covariates), live, with the
 *  leaderboard, the ensemble, backtest windows, per-series errors and the quantile fan. Every number is the API's. */
export function ForecastPage({ go }: { go: (r: string) => void }) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [dsId, setDsId] = useState(useHashParam('dataset') ?? '')  // G0: Build opens ?dataset=; else the first table
  const [ds, setDs] = useState<DatasetDetail | null>(null)
  const [map, setMap] = useState<FcMapping | null>(null)
  const [mapSrc, setMapSrc] = useState('')
  const [prev, setPrev] = useState<FcPreview | null>(null)
  const [opts, setOpts] = useState<FcOptions | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [cfg, setCfg] = useState({ H: 28, metric: 'MASE', presets: 'medium_quality', tau: 300, quantiles: '0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9', windows: null as number | null, freq: '', fitKwargs: '{}', hp: '', ensemble: 'both' as FcEnsembleKind, conformal: true, scope: 'pooled' as 'pooled' | 'item' })

  useEffect(() => {
    api.datasets.list().then((list) => {
      setDatasets(list)
      setDsId((id) => id || ([...list].sort((a, b) => Number(b.kind === 'timeseries') - Number(a.kind === 'timeseries'))[0]?.id ?? ''))
    }).catch((e) => setErr(e.message))
    flows.forecast.options().then((o) => setOpts(o)).catch(() => {})
  }, [])
  useEffect(() => {
    if (!dsId) return
    setPrev(null); setMap(null)
    api.datasets.get(dsId).then(setDs).catch((e) => setErr(e.message))
    flows.forecast.mapping(dsId).then((m) => { setMap(m.mapping); setMapSrc(m.source); if (m.prediction_length) setCfg((c) => ({ ...c, H: m.prediction_length! })) }).catch((e) => setErr(e.message))
  }, [dsId])
  useEffect(() => {
    if (!map || !map.timestamp || !map.target) return
    let alive = true
    setErr('')
    flows.forecast.preview(dsId, map).then((p) => { if (alive) { setPrev(p); if (!cfg.freq && p.freq) setCfg((c) => ({ ...c })) } }).catch((e) => alive && setErr(e.message))
    return () => { alive = false }
  }, [map, dsId]) // eslint-disable-line react-hooks/exhaustive-deps

  const cols = ds?.profile.columns ?? []
  const used = map ? new Set([map.item_id, map.timestamp, map.target].filter(Boolean)) : new Set()
  const multi = (key: 'known_covariates' | 'static_features' | 'past_covariates', name: string) => {
    if (!map) return
    const cur = map[key]
    setMap({ ...map, [key]: cur.includes(name) ? cur.filter((c) => c !== name) : [...cur, name] })
  }
  const qs = cfg.quantiles.split(/[,\s]+/).map(Number).filter((x) => x > 0 && x < 1)
  const metric = opts?.metrics.find((m) => m.key === cfg.metric)
  const preset = opts?.presets.find((p) => p.key === cfg.presets)
  let fitKw: Record<string, unknown> | null = null, hp: Record<string, unknown> | string | null = null, jsonErr = ''
  try { fitKw = JSON.parse(cfg.fitKwargs || '{}') } catch { jsonErr = 'fit_kwargs is not valid JSON' }
  try { hp = cfg.hp.trim() ? (cfg.hp.trim().startsWith('{') ? JSON.parse(cfg.hp) : cfg.hp.trim()) : null } catch { jsonErr = 'hyperparameters is not valid JSON' }
  const m = prev?.seasonal_period ?? 1
  const body: FcCreate | null = map && fitKw ? { dataset_id: dsId, mapping: map, prediction_length: cfg.H, freq: cfg.freq || null, eval_metric: cfg.metric, presets: cfg.presets || null,
    time_limit: cfg.tau, quantile_levels: qs, num_val_windows: cfg.windows, ensemble: cfg.ensemble, conformal: cfg.conformal, conformal_scope: cfg.scope, hyperparameters: hp, fit_kwargs: fitKw, source: 'forecast-page' } : null
  const ens = opts?.ensembles.find((e) => e.key === cfg.ensemble)
  const need = prev ? cfg.H * ((cfg.windows ?? 1) + 2) + 1 : 0
  const tooShort = prev ? prev.length_min < need : false

  const start = async () => {
    if (!body) return
    setBusy(true); setErr('')
    try { const r = await flows.forecast.create(body); location.hash = `#/models?run=${r.id}` } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const tsDatasets = [...datasets].sort((a, b) => Number(b.kind === 'timeseries') - Number(a.kind === 'timeseries'))
  if (!dsId && !err) return (
    <div className="page forecast" data-testid="forecast-setup">
      <div className="page-head"><div><h1 className="page-title">New Forecast</h1></div></div>
      <EmptyState title="No data yet" testId="fc-empty" actions={<a className="btn primary" href="#/build">Upload data in Build →</a>}>
        Upload a long-format table (one row per series and time step) in Build, then come back to forecast it.
      </EmptyState>
    </div>
  )

  return (
    <div className="page forecast" data-testid="forecast-setup">
      <div className="page-head">
        <div>
          <h1 className="page-title">New Forecast</h1>
          <p className="helper sub">Forecast many series at once with AutoGluon’s TimeSeriesPredictor: statistical, tabular, deep and foundation models (Chronos), backtested and ensembled.</p>
        </div>
      </div>

      <div className="card navy explain-hero fc-eq">
        <div className="eh-cell lead"><span className="micro-label">the task</span>
          <b className="math" style={{ fontSize: 20 }}>ŷ<sub>i,t+h|t</sub> = q̂<sub>τ</sub>(y<sub>i,t+h</sub> | y<sub>i,≤t</sub>, x<sub>i,≤t+h</sub>, s<sub>i</sub>)</b>
          <small>for every series i, horizon h = 1…H and quantile level τ</small></div>
        <HeroCell label="series i" sub="item_id values" subClass="math-s">{prev?.items ?? '—'}</HeroCell>
        <HeroCell label="length n" sub="steps per series" subClass="math-s">{prev ? (prev.length_min === prev.length_max ? prev.length_min : `${prev.length_min}–${prev.length_max}`) : '—'}</HeroCell>
        <HeroCell label="freq · season m" sub="inferred; m from freq" subClass="math-s">{prev?.freq ?? '—'} · {m}</HeroCell>
        <HeroCell label="horizon H" sub="prediction_length" subClass="math-s">{cfg.H}</HeroCell>
        <HeroCell label="known x past t" sub="future rows with covariates" subClass="math-s">{prev?.future_rows ?? 0}</HeroCell>
      </div>

      <div className="grid fc-setup">
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">1 · Data and columns</h3><span className="helper" style={{ fontSize: 12 }}>long format: one row per series and time step</span></div></div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              <select className="select" value={dsId} onChange={(e) => setDsId(e.target.value)} aria-label="Dataset" data-testid="fc-dataset" style={{ minWidth: 260 }}>
                {tsDatasets.map((d) => <option key={d.id} value={d.id}>{d.kind === 'timeseries' ? '◷ ' : ''}{d.name} · {d.rows.toLocaleString()} rows</option>)}
              </select>
              <a className="btn ghost" href="#/build">New data in Build →</a>
              {mapSrc && <span className="helper" style={{ fontSize: 12 }}>mapping: {mapSrc}</span>}
            </div>
            {map && <div className="fc-map">
              {([['item_id', 'item id i', 'which series a row belongs to (optional)'], ['timestamp', 'timestamp t', 'parsed as dates'], ['target', 'target y', 'the number to forecast']] as const).map(([k, lab, hint]) => (
                <label key={k}><span className="micro-label">{lab}</span>
                  <select className="select" value={map[k] ?? ''} onChange={(e) => setMap({ ...map, [k]: e.target.value || null } as FcMapping)} data-testid={`fc-map-${k}`}>
                    {k === 'item_id' && <option value="">(one series)</option>}
                    {cols.map((c) => <option key={c.name} value={c.name}>{c.name} · {c.type}</option>)}
                  </select><small className="helper">{hint}</small></label>
              ))}
              {([['known_covariates', 'known covariates x', 'known for the future too (holidays, paydays, promotions): rows past the last y supply them'],
                 ['static_features', 'static features s', 'constant per series (segment, region)'],
                 ['past_covariates', 'past covariates', 'observed only up to now; used as lags']] as const).map(([k, lab, hint]) => (
                <div key={k} className="fc-multi"><span className="micro-label">{lab}</span>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>{cols.filter((c) => !used.has(c.name)).map((c) => (
                    <button key={c.name} className={`chip-btn ${map[k].includes(c.name) ? 'on' : ''}`} onClick={() => multi(k, c.name)} data-testid={`fc-${k}-${c.name}`}>{map[k].includes(c.name) ? '✓ ' : ''}{c.name}</button>))}</div>
                  <small className="helper">{hint}</small></div>
              ))}
            </div>}
            {prev && <Sparks prev={prev} />}
            {prev?.static_not_constant.length ? <p className="helper" style={{ color: 'var(--status-warning)' }}>▲ {prev.static_not_constant.join(', ')} change within a series; the first value per series is used.</p> : null}
          </div>
        </div>

        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">2 · Configure TimeSeriesPredictor</h3></div></div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div className="fc-fields">
              <label><span className="micro-label">prediction_length H</span><input className="num-input" type="number" min={1} value={cfg.H} onChange={(e) => setCfg({ ...cfg, H: Math.max(1, Number(e.target.value)) })} data-testid="fc-H" /></label>
              <label><span className="micro-label">freq</span><input className="input" placeholder={prev?.freq ? `${prev.freq} (inferred)` : 'inferred'} value={cfg.freq} onChange={(e) => setCfg({ ...cfg, freq: e.target.value })} style={{ width: 110 }} /></label>
              <label><span className="micro-label">time_limit τ (s)</span><input className="num-input" type="number" min={10} value={cfg.tau} onChange={(e) => setCfg({ ...cfg, tau: Math.max(10, Number(e.target.value)) })} data-testid="fc-tau" /></label>
              <label title={opts?.num_val_windows_auto}><span className="micro-label">num_val_windows W</span><input className="num-input" type="number" min={1} max={20} placeholder="auto" value={cfg.windows ?? ''} onChange={(e) => setCfg({ ...cfg, windows: e.target.value === '' ? null : Math.max(1, Number(e.target.value)) })} data-testid="fc-windows" /></label>
            </div>
            <label><span className="micro-label">eval_metric</span>
              <div className="seg" style={{ flexWrap: 'wrap' }}>{(opts?.metrics ?? []).filter((x) => ['MASE', 'WQL', 'SQL', 'RMSSE', 'WAPE', 'SMAPE', 'MAPE', 'RMSE', 'MAE'].includes(x.key)).map((x) => (
                <button key={x.key} className={cfg.metric === x.key ? 'on' : ''} onClick={() => setCfg({ ...cfg, metric: x.key })} data-testid={`fc-metric-${x.key}`}>{x.key}{x.quantile ? ' ·q' : ''}</button>))}</div></label>
            {metric && <div className="formula fc-formula">
              <div className="eq">{metric.formula}</div>
              <dl className="where">
                <dt>e<sub>t</sub></dt><dd>y<sub>t</sub> − ŷ<sub>t</sub> on the H forecast steps</dd>
                <dt>m</dt><dd>season length, {m} for freq {prev?.freq ?? '?'} (the scale is the in-sample seasonal-naive error)</dd>
                <dt>n</dt><dd>observed steps of the series before the forecast</dd>
                <dt>ρ<sub>τ</sub>(u)</dt><dd>pinball loss max(τu, (τ − 1)u) at quantile level τ ∈ {'{'}{qs.join(', ')}{'}'}</dd>
              </dl>
            </div>}
            <label><span className="micro-label">presets</span>
              <select className="select" value={cfg.presets} onChange={(e) => setCfg({ ...cfg, presets: e.target.value })} data-testid="fc-presets">
                {(opts?.presets ?? []).map((p) => <option key={p.key} value={p.key}>{p.key}</option>)}
              </select><small className="helper">{preset?.info}</small></label>
            <label><span className="micro-label">quantile_levels τ</span><input className="input" value={cfg.quantiles} onChange={(e) => setCfg({ ...cfg, quantiles: e.target.value })} data-testid="fc-quantiles" /></label>
            <div className="fc-intervals" data-testid="fc-intervals">
              <label><span className="micro-label">ensemble</span>
                <select className="select" value={cfg.ensemble} onChange={(e) => setCfg({ ...cfg, ensemble: e.target.value as FcEnsembleKind })} data-testid="fc-ensemble">
                  {(opts?.ensembles ?? []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                </select><small className="helper">{ens?.info}</small></label>
              <label className="fc-check"><input type="checkbox" checked={cfg.conformal} onChange={(e) => setCfg({ ...cfg, conformal: e.target.checked })} data-testid="fc-conformal" />
                <span><span className="micro-label">conformal recalibration</span><small className="helper" style={{ display: 'block' }}>q̃<sub>τ</sub> = m + c<sub>τ</sub>(q̂<sub>τ</sub> − m), c<sub>τ</sub> from the best model’s backtest forecasts so that a share τ of actuals falls below q̃<sub>τ</sub>; the median is not moved. Raw and recalibrated forecasts are both kept.</small></span></label>
              {cfg.windows == null && <small className="helper">W auto: {opts?.num_val_windows_auto}</small>}
            </div>
            <details className="fc-adv"><summary className="micro-label">Advanced: conformal scope, hyperparameters and fit_kwargs (passed unchanged)</summary>
              <label><span className="micro-label">conformal scope</span>
                <div className="seg">{(['pooled', 'item'] as const).map((k) => <button key={k} className={cfg.scope === k ? 'on' : ''} disabled={!cfg.conformal} onClick={() => setCfg({ ...cfg, scope: k })} data-testid={`fc-scope-${k}`}>{k === 'pooled' ? 'pooled: one c_τ' : 'per series c_τ,i'}</button>)}</div>
                <small className="helper">per series needs ≥ 60 backtest points for a series (W × H); others use the pooled c<sub>τ</sub></small></label>
              <label><span className="micro-label">hyperparameters (JSON or a preset name; empty = from presets)</span>
                <textarea className="input mono" rows={3} placeholder='{"SeasonalNaive": {}, "ETS": {}, "DirectTabular": {}, "Chronos": {"model_path": "bolt_small"}}' value={cfg.hp} onChange={(e) => setCfg({ ...cfg, hp: e.target.value })} data-testid="fc-hp" /></label>
              <label><span className="micro-label">fit_kwargs</span>
                <textarea className="input mono" rows={2} value={cfg.fitKwargs} onChange={(e) => setCfg({ ...cfg, fitKwargs: e.target.value })} placeholder='{"refit_every_n_windows": 1, "enable_ensemble": true}' /></label>
            </details>
            {body && <pre className="fc-call mono">{`TimeSeriesPredictor(prediction_length=${cfg.H}, freq=${JSON.stringify(cfg.freq || prev?.freq || null)}, eval_metric="${cfg.metric}",
    quantile_levels=${JSON.stringify(qs)}, known_covariates_names=${JSON.stringify(map?.known_covariates ?? [])}
).fit(train_data, presets="${cfg.presets}", time_limit=${cfg.tau}, num_val_windows=${cfg.windows ?? '<auto>'}${ens?.ensemble_hyperparameters ? `, ensemble_hyperparameters=${JSON.stringify(ens.ensemble_hyperparameters)}` : ''}${hp ? `, hyperparameters=${JSON.stringify(hp)}` : ''}${fitKw && Object.keys(fitKw).length ? `, **${JSON.stringify(fitKw)}` : ''})`}</pre>}
            {tooShort && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ The shortest series has {prev!.length_min} steps; H = {cfg.H} with W = {cfg.windows ?? 1} needs ≥ H(W + 2) + 1 = {need}.</p>}
            {(err || jsonErr) && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {jsonErr || err}</p>}
            <div className="row"><span className="helper" style={{ fontSize: 12 }}>The last H steps of every series are held out and scored after the fit; W backtest windows before them pick and weight the models.</span><span className="spacer" />
              <button className="btn primary" disabled={!body || !prev || busy || tooShort || !!jsonErr} onClick={start} data-testid="fc-start">{busy ? 'Starting…' : 'Start Forecast'}</button></div>
          </div>
        </div>
      </div>

      <p className="helper">Earlier forecasts are in <a href="#/models?type=forecast">Models →</a></p>
    </div>
  )
}

function Sparks({ prev }: { prev: FcPreview }) {
  const items = Object.entries(prev.series)
  return (
    <div className="fc-sparks" data-testid="fc-preview">
      <div className="row"><span className="micro-label">preview · last 60 steps</span><span className="spacer" /><span className="mono helper" style={{ fontSize: 11.5 }}>{prev.start.slice(0, 10)} → {prev.end.slice(0, 10)} · {prev.observed_rows.toLocaleString()} observed rows</span></div>
      <div className="fc-spark-grid">{items.map(([k, s]) => {
        const ys = s.y.filter((v): v is number => v != null)
        const lo = Math.min(...ys), hi = Math.max(...ys)
        const pts = s.y.map((v, i) => v == null ? null : `${(i / (s.y.length - 1)) * 120},${28 - ((v - lo) / (hi - lo || 1)) * 26}`).filter(Boolean).join(' ')
        return <div key={k} className="fc-spark"><span className="mono">{k}</span><svg width="120" height="30"><polyline points={pts} fill="none" stroke="var(--accent-primary)" strokeWidth="1.3" /></svg><span className="mono helper">{fmtNum(lo)}–{fmtNum(hi)}</span></div>
      })}</div>
    </div>
  )
}

// --------------------------------------------------------------------------- the run

export function ForecastRun({ runId, go }: { runId: string; go: (r: string) => void }) {
  const live = useRunEvents(runId)
  const [run, setRun] = useState<FlowRun | null>(null)
  const [chart, setChart] = useState<FcChart | null>(null)
  const [item, setItem] = useState<string>('')
  const [err, setErr] = useState('')
  const [showHold, setShowHold] = useState(true)
  const [rawBands, setRawBands] = useState(false)
  useEffect(() => { flows.run(runId).then(setRun).catch((e) => setErr(e.message)) }, [runId, live.done])
  useEffect(() => {
    if (run?.status !== 'succeeded') return
    flows.forecast.chart(runId).then((c) => { setChart(c); setItem((cur) => cur || Object.keys(c.items)[0]) }).catch((e) => setErr(e.message))
  }, [run?.status, runId])

  if (err && !run) return <ErrorCard err={err} />
  if (!run) return <Loading>Reading forecast run {runId}…</Loading>
  const done = run.status === 'succeeded'
  const lb = run.leaderboard ?? []
  const metric = run.eval_metric?.name ?? String(run.config.eval_metric)
  const H = Number(run.config.prediction_length)
  const naive = lb.find((r) => r.model === 'SeasonalNaive')
  const best = lb.find((r) => r.model === run.best_model) ?? lb[0]
  const w = run.ensemble?.weights ?? {}
  const d = run.data ?? {}
  const h = run.holdout
  const hc = h as unknown as { calibration?: FcCalibration; calibration_raw?: FcCalibration } | null
  const cal = hc?.calibration ?? null
  const calRaw = hc?.calibration_raw ?? null
  const recal = !!h?.recalibrated
  const rawMain = calRaw?.intervals[0]
  const bandOf = (b?: FcBand) => (b && rawBands && b.q_raw ? { ...b, q: b.q_raw } : b)
  const elapsed = live.t0 && live.last ? live.last - live.t0 : null
  const tau = Number(run.config.time_limit ?? 0)
  return (
    <div className="page forecast" data-testid="forecast-run" data-run={run.id} data-status={run.status}>
      <div className="page-head">
        <div><h1 className="page-title">Forecast · {run.name}</h1>
          <p className="helper sub"><span className="mono">{run.id}</span> · {String(run.config.presets ?? 'custom')} · τ = {tau || '∞'} s · H = {H} · {metric} · {d.items ?? '…'} series · freq {d.freq ?? '…'}</p></div>
        <div className="row">
          <button className="btn" onClick={() => { location.hash = '#/forecast' }}>New Forecast</button>
          {done && <a className="btn" href={flows.forecast.csvUrl(run.id)} download data-testid="fc-download">Download forecast.csv</a>}
          {done && <Register runId={run.id} go={go} name={`${run.dataset_id}.forecast.${run.label}`} />}
        </div>
      </div>

      <div className="card navy explain-hero">
        <div className="eh-cell lead"><span className="micro-label">{done ? `holdout ${metric} · ${run.best_model}` : live.stage}</span>
          <b className="mono" data-testid="fc-primary">{done ? fix(best?.score_test_natural) : live.status === 'failed' ? 'failed' : <ResonanceWave />}</b>
          <small>{done ? `last H = ${H} steps of each series, never seen in fit or model selection` : elapsed != null ? `${elapsed.toFixed(0)} s of τ = ${tau} s` : 'queued'}</small></div>
        {done && naive && <Cell k="vs SeasonalNaive" v={`${fix(naive.score_test_natural)}`} d={<>skill 1 − {metric}<sub>best</sub> / {metric}<sub>naive</sub> = {pct(1 - (best!.score_test_natural ?? 0) / (naive.score_test_natural || 1))}</>} />}
        {h && <Cell k={recal ? 'WQL · recalibrated' : 'WQL'} v={fix(h.metrics.WQL, 4)} d={<>{recal && h.metrics_raw ? <>AutoGluon {fix(h.metrics_raw.WQL, 4)} · </> : null}2 Σ<sub>τ</sub>Σ<sub>t</sub> ρ<sub>τ</sub>(y<sub>t</sub> − q̂<sub>τ,t</sub>) / (|T| Σ<sub>t</sub>|y<sub>t</sub>|)</>} />}
        {h && <Cell k="sMAPE" v={pct(h.metrics.SMAPE)} d={<>mean 2|e<sub>t</sub>| / (|y<sub>t</sub>| + |ŷ<sub>t</sub>|)</>} />}
        {h && <Cell k={`coverage [q${h.interval[0]}, q${h.interval[1]}]`} v={pct(h.coverage)} d={<>nominal {pct(h.interval[1] - h.interval[0])}{recal && rawMain ? <> · AutoGluon {pct(rawMain.empirical)}</> : null}{cal ? <> · <span className={cal.verdict === 'calibrated' ? '' : 'g-warn'} style={{ fontWeight: 600 }}>{cal.verdict === 'calibrated' ? (recal ? 'recalibrated: within error' : 'within error') : cal.verdict === 'too_wide' ? 'bands too wide' : 'bands too narrow'}</span></> : null}</>} />}
        <Cell k="models" v={`${live.models.filter((m) => m.status === 'done').length || lb.length}`} d={`${Object.keys(w).length} in the ensemble`} />
      </div>

      {!done && <LiveModels live={live} metric={metric} />}
      {run.status === 'failed' && <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {run.error}</div>}

      {done && chart && <>
        <div className="row fc-items" style={{ flexWrap: 'wrap' }}>
          <span className="micro-label">series</span>
          {Object.keys(chart.items).map((k) => {
            const e = h?.items.find((x) => x.item_id === k)
            return <button key={k} className={`chip-btn ${item === k ? 'on' : ''}`} onClick={() => setItem(k)} data-testid={`fc-item-${k}`}>{k}{e?.MASE != null ? <span className="muted"> · {e.MASE.toFixed(2)}</span> : null}</button>
          })}
          <span className="spacer" />
          {recal && <div className="seg" data-testid="fc-bands">{([false, true] as const).map((r) => <button key={String(r)} className={rawBands === r ? 'on' : ''} onClick={() => setRawBands(r)}>{r ? 'AutoGluon bands' : 'recalibrated bands'}</button>)}</div>}
          <label className="fc-check helper"><input type="checkbox" checked={showHold} onChange={(e) => setShowHold(e.target.checked)} /> show holdout window</label>
        </div>
        {chart.items[item] && <FanChart item={item} {...chart.items[item]} holdout={bandOf(chart.items[item].holdout)} future={bandOf(chart.items[item].future)} quantiles={chart.quantiles} freq={d.freq} showHoldout={showHold}
          title={`Forecast · ${item}${recal ? (rawBands ? ' · AutoGluon quantiles' : ' · recalibrated quantiles q̃') : ''}`} />}
      </>}

      {done && <div className="grid fc-two">
        <Leaderboard run={run} metric={metric} />
        <EnsembleCard weights={w} lb={lb} metric={metric} />
      </div>}
      {done && cal && <Calibration cal={cal} raw={recal ? calRaw : null} conformal={h?.conformal} model={run.best_model ?? ''}
        rawCsv={recal ? flows.forecast.csvUrl(run.id, 'future_raw') : undefined} />}
      {done && <div className="grid fc-two">
        <Backtests run={run} metric={metric} />
        <ItemErrors run={run} onItem={setItem} />
      </div>}
      {done && <NewHistory runId={run.id} />}
      {done && <div className="chips-row">
        <ParamChip sym="run" value={run.id} /><ParamChip sym="H" value={String(H)} /><ParamChip sym="m" value={String(d.seasonal_period ?? '—')} /><ParamChip sym="W" value={String(run.backtests?.windows.length ?? '—')} />
        <span className="helper" style={{ fontSize: 12 }}>Train ends {String(d.train_end ?? '').slice(0, 10)}; holdout {String(d.holdout_start ?? '').slice(0, 10)} → {String(d.end ?? '').slice(0, 10)}; forecast {run.forecast?.start.slice(0, 10)} → {run.forecast?.end.slice(0, 10)} with known covariates from {run.forecast?.known_covariates_source}. <span className="mono">TimeSeriesPredictor.load("{run.predictor_path}")</span></span>
        <StatusPill status="success">live</StatusPill>
      </div>}
    </div>
  )
}

function Cell({ k, v, d }: { k: string; v: string; d: ReactNode }) {
  return <div className="eh-cell"><span className="micro-label">{k}</span><b className="mono">{v}</b><small className="math-s">{d}</small></div>
}

function Leaderboard({ run, metric }: { run: FlowRun; metric: string }) {
  const lb = run.leaderboard ?? []
  return (
    <div className="card" data-testid="fc-leaderboard">
      <div className="card-head"><div className="left"><h3 className="card-title">Leaderboard</h3><span className="helper" style={{ fontSize: 12 }}>{metric}: validation on W backtest windows · holdout on the last H steps</span></div></div>
      <div className="table-wrap"><table className="table"><thead><tr><th>#</th><th>model</th><th>family</th><th className="r">val {metric}</th><th className="r">holdout {metric}</th><th className="r" title="AutoGluon's quantiles, holdout">cov [q{run.holdout?.interval[0]}, q{run.holdout?.interval[1]}]</th><th className="r">fit</th><th className="r">predict</th><th className="r">w</th></tr></thead>
        <tbody>{lb.map((r) => (
          <tr key={r.model} className={r.model === run.best_model ? 'sel' : ''}>
            <td className="table-cell">{r.rank}</td><td className="table-cell">{r.model}</td>
            <td><span className="dot-inline" style={{ background: FAMILY_COLOR[(r.family as string) ?? 'other'] }} />{r.family}</td>
            <td className="r table-cell">{fix(r.score_val_natural)}</td><td className="r table-cell"><b>{fix(r.score_test_natural)}</b></td>
            <td className="r table-cell">{pct((r as unknown as { coverage_test?: number | null }).coverage_test)}</td>
            <td className="r table-cell">{r.fit_time != null ? `${Number(r.fit_time).toFixed(2)} s` : '—'}</td><td className="r table-cell">{r.pred_time_test != null ? `${Number(r.pred_time_test).toFixed(2)} s` : '—'}</td>
            <td className="r table-cell">{run.ensemble?.weights?.[r.model] != null ? run.ensemble.weights[r.model].toFixed(3) : ''}</td>
          </tr>))}</tbody></table></div>
    </div>
  )
}

function EnsembleCard({ weights, lb, metric }: { weights: Record<string, number>; lb: FlowRun['leaderboard'] & object; metric: string }) {
  const ens = lb.find((r) => r.weights)
  const entries = Object.entries(weights)
  return (
    <div className="card formula-card" data-testid="fc-ensemble">
      <div className="card-head"><div className="left"><h3 className="card-title">The ensemble</h3><span className="helper" style={{ fontSize: 12 }}>greedy ensemble selection (Caruana) on the backtest forecasts</span></div></div>
      <div className="card-body stack" style={{ gap: 10 }}>
        <div className="formula"><div className="eq">q̂<sub>τ</sub><sup>ens</sup> = Σ<sub>m</sub> w<sub>m</sub> q̂<sub>τ</sub><sup>(m)</sup>, &nbsp; Σ w<sub>m</sub> = 1, w<sub>m</sub> ≥ 0</div>
          <div className="eq" style={{ fontSize: 15, paddingTop: 0 }}>= {entries.map(([k, v], i) => <span key={k}>{i ? ' + ' : ''}<b className="mono">{v.toFixed(3)}</b>·q̂<sup>({k})</sup></span>)}</div>
          <dl className="where"><dt>w<sub>m</sub></dt><dd>weight of model m: the share of greedy picks that lowered the backtest {metric}</dd><dt>q̂<sub>τ</sub><sup>(m)</sup></dt><dd>model m’s forecast of quantile τ, applied per quantile and per step</dd></dl></div>
        {entries.map(([k, v]) => <div key={k} className="fc-w"><span className="mono">{k}</span><span className="bar"><i style={{ width: `${v * 100}%` }} /></span><span className="mono">{v.toFixed(3)}</span></div>)}
        {ens && <p className="helper" style={{ fontSize: 12 }}>Ensemble val {metric} {fix(ens.score_val_natural)} · holdout {fix(ens.score_test_natural)}.</p>}
      </div>
    </div>
  )
}

function Backtests({ run, metric }: { run: FlowRun; metric: string }) {
  const bt = run.backtests
  if (!bt || !bt.windows.length) return <div className="card card-body"><p className="helper">No backtest windows recorded{bt?.error ? `: ${bt.error}` : ''}.</p></div>
  const models = Object.entries(bt.models).sort((a, b) => (mean(a[1].map((x) => x.MASE)) ?? 9) - (mean(b[1].map((x) => x.MASE)) ?? 9))
  return (
    <div className="card" data-testid="fc-backtests">
      <div className="card-head"><div className="left"><h3 className="card-title">Backtest windows</h3><span className="helper" style={{ fontSize: 12 }}>expanding windows inside the training range (num_val_windows = {bt.windows.length}); MASE per window</span></div></div>
      <div className="table-wrap"><table className="table"><thead><tr><th>model</th>{bt.windows.map((w) => <th key={w.window} className="r" title={`${w.start} → ${w.end}`}>w{w.window} <span className="muted">{w.start.slice(5, 10)}</span></th>)}<th className="r">mean</th><th className="r">WQL̄</th></tr></thead>
        <tbody>{models.map(([k, ws]) => <tr key={k}><td className="table-cell">{k}</td>{ws.map((x) => <td key={x.window} className="r table-cell">{fix(x.MASE)}</td>)}<td className="r table-cell"><b>{fix(mean(ws.map((x) => x.MASE)))}</b></td><td className="r table-cell">{fix(mean(ws.map((x) => x.WQL)), 4)}</td></tr>)}</tbody></table></div>
      <p className="helper caption-pad" style={{ fontSize: 12 }}>Each window w forecasts H steps from the data before it; the {metric} that picks the models averages these windows. MASE &lt; 1 beats the in-sample seasonal-naive step.</p>
    </div>
  )
}

const mean = (xs: (number | null)[]) => { const f = xs.filter((x): x is number => x != null); return f.length ? f.reduce((a, b) => a + b, 0) / f.length : null }

function ItemErrors({ run, onItem }: { run: FlowRun; onItem: (k: string) => void }) {
  const h = run.holdout
  if (!h) return null
  return (
    <div className="card" data-testid="fc-item-errors">
      <div className="card-head"><div className="left"><h3 className="card-title">Errors per series</h3><span className="helper" style={{ fontSize: 12 }}>{h.model} on the holdout window, worst MASE first</span></div></div>
      <div className="table-wrap"><table className="table"><thead><tr><th>series i</th><th className="r">MASE<sub>i</sub></th><th className="r">WQL<sub>i</sub></th><th className="r">sMAPE</th><th className="r">bias ē</th><th className="r">coverage</th><th className="r">scale</th></tr></thead>
        <tbody>{h.items.map((r) => <tr key={r.item_id} onClick={() => onItem(r.item_id)} style={{ cursor: 'pointer' }}>
          <td className="table-cell">{r.item_id}</td><td className="r table-cell"><b>{fix(r.MASE)}</b></td><td className="r table-cell">{fix(r.WQL, 4)}</td><td className="r table-cell">{pct(r.sMAPE)}</td>
          <td className="r table-cell">{fmtNum(r.bias)}</td><td className="r table-cell" style={{ color: Math.abs(r.coverage - (r.interval[1] - r.interval[0])) > 0.2 ? 'var(--status-warning)' : undefined }}>{pct(r.coverage)}</td><td className="r table-cell muted">{fmtNum(r.scale)}</td></tr>)}</tbody></table></div>
      <p className="helper caption-pad" style={{ fontSize: 12 }}>MASE<sub>i</sub> = mean|e<sub>t</sub>| / ((1/(n−m)) Σ<sub>t&gt;m</sub>|y<sub>t</sub> − y<sub>t−m</sub>|), the denominator (scale) from series i’s own history; bias ē = mean(ŷ − y); coverage = share of actuals inside [q{h.interval[0]}, q{h.interval[1]}] (nominal {pct(h.interval[1] - h.interval[0])}).</p>
    </div>
  )
}

function NewHistory({ runId }: { runId: string }) {
  const ref = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [out, setOut] = useState<Awaited<ReturnType<typeof flows.forecast.predict>> | null>(null)
  const [err, setErr] = useState('')
  const [item, setItem] = useState('')
  const go = async (f: File) => {
    setBusy(true); setErr('')
    try { const o = await flows.forecast.predict(runId, f); setOut(o); setItem(Object.keys(o.chart.items)[0]) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const items = useMemo(() => (out ? Object.keys(out.chart.items) : []), [out])
  return (
    <div className="card" data-testid="fc-new-history">
      <div className="card-head"><div className="left"><h3 className="card-title">Forecast from new history</h3><span className="helper" style={{ fontSize: 12 }}>upload a CSV with the same columns; the saved predictor forecasts from it (no refit)</span></div>
        <div className="row"><input ref={ref} type="file" accept=".csv" hidden onChange={(e) => e.target.files?.[0] && go(e.target.files[0])} />
          <button className="btn sm" disabled={busy} onClick={() => ref.current?.click()}>{busy ? 'Forecasting…' : 'Upload history…'}</button>
          {out && <a className="btn sm" href={out.download} download>Download</a>}</div></div>
      {err && <div className="card-body"><p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p></div>}
      {out && <div className="card-body stack" style={{ gap: 8 }}>
        <p className="helper" style={{ fontSize: 12 }}>{out.items} series · {out.rows} rows forecast by {out.model} in {out.seconds.toFixed(2)} s · known covariates from {out.known_covariates_source}</p>
        <div className="row" style={{ flexWrap: 'wrap' }}>{items.map((k) => <button key={k} className={`chip-btn ${item === k ? 'on' : ''}`} onClick={() => setItem(k)}>{k}</button>)}</div>
        {out.chart.items[item] && <FanChart item={item} {...out.chart.items[item]} quantiles={out.chart.quantiles} title={`New-history forecast · ${item}`} />}
      </div>}
    </div>
  )
}

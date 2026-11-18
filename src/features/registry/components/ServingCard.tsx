import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { BatchLatency, ModelVersion, OneRow, ScoreResult, ServingBenchmark } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { ms, us } from '../lib'

const p95 = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const h = (s.length - 1) * 0.95, lo = Math.floor(h), hi = Math.ceil(h)
  return s[lo] + (s[hi] - s[lo]) * (h - lo)
}
const p50 = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const h = (s.length - 1) * 0.5, lo = Math.floor(h), hi = Math.ceil(h)
  return s[lo] + (s[hi] - s[lo]) * (h - lo)
}

/** F3 · how the version is served one row at a time: pruned pipeline, warm on load, one-row p50 / p95, batches. */
export function ServingCard({ v, sampleRow }: { v: ModelVersion; sampleRow?: Record<string, unknown> | null }) {
  const r = v.result!
  const s = r.serving
  const [bench, setBench] = useState<ServingBenchmark | null>(r.serving_benchmark ?? null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [online, setOnline] = useState<{ n: number; http: number[]; server: number[]; predict: number[]; where: string; last: ScoreResult } | null>(null)
  if (!s) return null
  const pipe = s.pipeline
  const one: OneRow = bench?.one_row ?? s.one_row
  const batches: BatchLatency[] = bench?.batches ?? s.batches
  const budget = 10
  const ok = one.ms_p95 != null && one.ms_p95 <= budget

  const measure = async () => {
    setBusy(true); setErr('')
    try { setBench(await api.registry.benchmark(v.id)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const callOnline = async () => {
    if (!sampleRow) return
    setBusy(true); setErr('')
    try {
      const http: number[] = [], server: number[] = [], predict: number[] = []
      let last: ScoreResult | null = null
      for (let i = 0; i < 50; i++) {
        const t0 = performance.now()
        last = await api.registry.score(v.id, [sampleRow])
        http.push(performance.now() - t0); server.push(last.server_ms); predict.push(last.predict_ms)
      }
      setOnline({ n: 50, http, server, predict, where: last!.where, last: last! })
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="card" data-testid="serving-card">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Serving · One Row At A Time</h3>
          <span className="helper" style={{ fontSize: 12 }}>{bench ? bench.where : s.where} · {s.cpus} CPUs</span></div>
        <div className="row">
          <span className="chip"><span className="sym">τ₉₅</span>=<b data-testid="serving-p95">{ms(one.ms_p95)} ms</b></span>
          <span className="chip"><span className="sym">τ₅₀</span>=<b>{ms(one.ms_p50)} ms</b></span>
          <StatusPill status={ok ? 'success' : 'warning'}>{ok ? `≤ ${budget} ms online` : `above ${budget} ms`}</StatusPill>
        </div>
      </div>
      <div className="card-body serving-grid">
        <div>
          <div className="eq">τ<sub>q</sub> = Q<sub>q</sub>{'{'} T<sub>1</sub>, …, T<sub>n</sub> {'}'}, &nbsp; n = <b className="mono">{one.calls}</b>, &nbsp; τ<sub>95</sub> = <b className="mono">{ms(one.ms_p95)} ms</b></div>
          <dl className="where">
            <dt>Tᵢ</dt><dd>wall time of the i-th one-row <span className="mono">{v.result!.version.problem_type === 'regression' ? 'predict' : 'predict_proba'}</span> call on successive holdout rows, after 3 warm-up calls, with the served model <span className="mono">{v.result!.version.best_model}</span> persisted in memory</dd>
            <dt>Q<sub>q</sub></dt><dd>the q-th percentile (linear interpolation); τ₉₅ = 95% of calls finish within it (slowest seen {ms(one.ms_max)} ms)</dd>
            <dt>cold</dt><dd>the first call after loading from disk without warming: <b className="mono">{ms(s.cold_first_call_ms)} ms</b>; a predict worker warms each predictor on load ({s.warm ? `persist ${s.warm.persisted.join(', ') || '—'}, then ${s.warm.warm_calls_ms.map((x) => ms(x)).join(' / ')} ms` : '—'}), so no real call pays that</dd>
          </dl>
          {pipe && (
            <div data-testid="serving-pipeline" style={{ marginTop: 8 }}>
              <span className="micro-label">feature pipeline</span>
              {pipe.kept
                ? <p className="helper" style={{ fontSize: 12.5 }}>Pruned to what <span className="mono">{pipe.model}</span> reads: <b className="mono">{pipe.n_features_out_before?.toLocaleString()} → {pipe.n_features_out.toLocaleString()}</b> features, <b className="mono">{pipe.generators_before} → {pipe.generators}</b> generators, input columns <span className="mono">{pipe.features_in.join(', ')}</span>{pipe.dropped_in?.length ? <> (no longer read: <span className="mono">{pipe.dropped_in.join(', ')}</span>)</> : null}. Holdout predictions before and after: max |Δp| = <b className="mono">{(pipe.max_abs_diff ?? 0).toExponential(1)}</b>.</p>
                : <p className="helper" style={{ fontSize: 12.5 }}>{pipe.reason ?? `Not pruned: ${pipe.model} reads ${pipe.n_features_out.toLocaleString()} of ${pipe.n_features_out_before?.toLocaleString() ?? pipe.n_features_out.toLocaleString()} features, so every stage is needed.`}</p>}
            </div>
          )}
        </div>
        <div>
          <table className="table compact" data-testid="serving-batches">
            <thead><tr><th className="r nocase">batch b</th><th className="r nocase">ms / call p50</th><th className="r nocase">p95</th><th className="r nocase">µs / row</th><th className="r nocase">rows / s</th></tr></thead>
            <tbody>{batches.map((b) => (
              <tr key={b.batch}><td className="r mono">{b.batch.toLocaleString()}</td><td className="r mono">{ms(b.ms_p50)}</td><td className="r mono">{ms(b.ms_p95)}</td>
                <td className="r mono">{us(b.us_per_row_p50)}</td><td className="r mono">{b.rows_per_s == null ? '—' : Math.round(b.rows_per_s).toLocaleString()}</td></tr>
            ))}</tbody>
          </table>
          <p className="helper" style={{ fontSize: 12 }}>µs / row = T<sub>b</sub> / b, the p50 call time over b rows. A call has a fixed cost (pipeline, pandas, the model's own setup) that one row pays alone and 1,000 rows share.</p>
          <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            <button className="btn sm" onClick={measure} disabled={busy} data-testid="serving-benchmark">Measure in a predict worker</button>
            {sampleRow && <button className="btn sm" onClick={callOnline} disabled={busy} data-testid="serving-online">Call /score 50× from this browser</button>}
            {busy && <ResonanceWave width={50} height={12} />}
          </div>
          {err && <p role="alert" className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
          {online && (
            <table className="table compact" style={{ marginTop: 8 }} data-testid="serving-online-table">
              <thead><tr><th className="nocase">50 one-row calls · {online.where === 'api' ? 'in the API process' : 'in a predict worker'}</th><th className="r nocase">p50 ms</th><th className="r nocase">p95 ms</th></tr></thead>
              <tbody>
                <tr><td className="muted">predictor call</td><td className="r mono">{ms(p50(online.predict))}</td><td className="r mono"><b>{ms(p95(online.predict))}</b></td></tr>
                <tr><td className="muted">server (lookup + frame + predictor)</td><td className="r mono">{ms(p50(online.server))}</td><td className="r mono">{ms(p95(online.server))}</td></tr>
                <tr><td className="muted">browser round trip (HTTP via the UI’s /api proxy, JSON)</td><td className="r mono">{ms(p50(online.http))}</td><td className="r mono">{ms(p95(online.http))}</td></tr>
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

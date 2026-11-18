import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ColumnProfile, DatasetDetail, ModelVersion, Run, TryResult } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ShapEquation, ShapWaterfall } from '@/shared/components/ShapWaterfall'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

type Field = { name: string; kind: 'number' | 'category' | 'text'; options: string[]; example: string; stats?: { min: number; max: number; mean: number } }

/** Try It on a registered version: a form generated from the model's schema, the prediction with its probabilities,
 *  and the top reasons for that prediction from Kernel SHAP (computed in a worker process). */
export function TryIt({ v, run, ds }: { v: ModelVersion; run: Run | null; ds: DatasetDetail | null }) {
  const fields: Field[] = useMemo(() => {
    const cols = new Map<string, ColumnProfile>((ds?.profile.columns ?? []).map((c) => [c.name, c]))
    const types = (run?.config?.column_types ?? {}) as Record<string, string>
    return (v.result?.version.features ?? []).map((name) => {
      const c = cols.get(name)
      const t = types[name] ?? c?.type
      const kind: Field['kind'] = t === 'text' ? 'text' : t === 'number' ? 'number' : 'category'
      const st = c?.stats && 'mean' in c.stats && 'min' in c.stats ? { min: c.stats.min, max: c.stats.max, mean: c.stats.mean } : undefined
      return { name, kind, options: (c?.top ?? []).map((x) => x.value), example: c?.example ?? '', stats: st }
    })
  }, [v, run, ds])
  const [vals, setVals] = useState<Record<string, string>>({})
  useEffect(() => { setVals((old) => ({ ...Object.fromEntries(fields.map((f) => [f.name, f.example])), ...Object.fromEntries(Object.entries(old).filter(([, x]) => x !== '')) })) }, [fields])
  const [out, setOut] = useState<TryResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const go = async () => {
    setBusy(true); setErr('')
    const row: Record<string, unknown> = {}
    for (const f of fields) {
      const x = vals[f.name]
      if (x === undefined || x === '') continue
      row[f.name] = f.kind === 'number' ? Number(x) : x
    }
    try { setOut(await api.registry.tryIt(v.id, row)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const probs = out?.probabilities ? Object.entries(out.probabilities).sort((a, b) => b[1] - a[1]) : []
  const sh = out?.shap
  const isBin = out?.problem_type === 'binary'
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="try-it">
      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Try It</h3><span className="helper" style={{ fontSize: 12 }}>x = one applicant, every feature of {v.model_name} v{v.version}; an empty field is sent as missing</span></div>
          <span className="mono helper" style={{ fontSize: 12 }}>{v.result?.version.best_model}</span>
        </div>
        <div className="card-body tp-body">
          <div className="tp-form">
            {fields.map((f) => (
              <label key={f.name} className={`tp-field ${f.kind === 'text' ? 'wide' : ''}`}>
                <span className="tp-label"><span className="mono">{f.name}</span><small>{f.kind}{f.stats ? ` · μ = ${fmtScore(f.stats.mean, 3)}` : ''}</small></span>
                {f.kind === 'text'
                  ? <textarea className="kw-input" rows={3} data-testid={`try-${f.name}`} value={vals[f.name] ?? ''} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
                  : f.kind === 'category' && f.options.length > 0
                    ? <select className="select" data-testid={`try-${f.name}`} value={vals[f.name] ?? ''} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}>
                        <option value="">— missing —</option>
                        {[...new Set([...(vals[f.name] ? [vals[f.name]] : []), ...f.options])].map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    : <input className="input" data-testid={`try-${f.name}`} type={f.kind === 'number' ? 'number' : 'text'} step="any" value={vals[f.name] ?? ''} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />}
              </label>
            ))}
            <div className="row" style={{ gap: 'var(--space-8)', gridColumn: '1 / -1' }}>
              <button className="btn primary" data-testid="try-predict" disabled={busy || v.status !== 'ready'} onClick={go}>Predict and explain</button>
              {busy && <span className="row helper" style={{ fontSize: 12 }}><ResonanceWave width={60} height={14} /> f(x), then Kernel SHAP over 2,048 coalitions in a worker…</span>}
            </div>
            {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)', gridColumn: '1 / -1' }}>✕ {err}</p>}
          </div>
          <div className="tp-out navy-well" data-testid="try-result">
            {!out && <span className="tp-empty">ŷ = f(x) and why</span>}
            {out && out.probabilities && (
              <>
                <span className="micro-label">{out.model} · predicted class</span>
                <div className="tp-class" data-testid="try-class">{String(out.prediction)}</div>
                <div className="tp-eq">{isBin
                  ? <>ŷ = {String(out.positive_class)} ⟺ p̂ &gt; t, &nbsp;p̂ = <b data-testid="try-p">{out.probabilities[String(out.positive_class)].toFixed(4)}</b>, t = {out.threshold?.toFixed(3)}</>
                  : <>ŷ = argmaxₖ p̂ₖ(x)</>}</div>
                <div className="tp-probs">
                  {probs.map(([c, p]) => (
                    <div key={c} className={`tp-prob ${String(c) === String(out.prediction) ? 'on' : ''}`} data-testid={`try-prob-${c}`}>
                      <span className="mono">{c}</span><i className="tp-bar"><i style={{ width: `${p * 100}%` }} /></i><span className="mono">{(p * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
                <span className="tp-foot">Σₖ p̂ₖ = {probs.reduce((a, [, p]) => a + p, 0).toFixed(3)} · f(x) in {out.predict_ms.toFixed(1)} ms{out.missing_columns.length ? ` · sent as missing: ${out.missing_columns.join(', ')}` : ''}</span>
              </>
            )}
            {out && !out.probabilities && (
              <>
                <span className="micro-label">{out.model} · predicted value</span>
                <div className="tp-class" data-testid="try-value">{fmtScore(Number(out.prediction), 2)}</div>
                <span className="tp-foot">f(x) in {out.predict_ms.toFixed(1)} ms{out.missing_columns.length ? ` · sent as missing: ${out.missing_columns.join(', ')}` : ''}</span>
              </>
            )}
          </div>
        </div>
      </div>

      {sh && (
        <div className="grid try-why">
          <div className="card" data-testid="try-reasons">
            <div className="card-head"><div className="left"><h3 className="card-title">Top Reasons</h3><span className="helper" style={{ fontSize: 12 }}>largest |φᵢ| first</span></div></div>
            <div className="card-body">
              <ol className="reasons">
                {sh.reasons.map((r, i) => (
                  <li key={r.feature} data-testid={`reason-${i}`}>
                    <span className={`reason-dir ${r.phi >= 0 ? 'up' : 'down'}`}>{r.phi >= 0 ? '▲' : '▼'}</span>
                    <span><b className="mono">{r.feature}</b> = <span className="mono">{r.value == null ? 'missing' : String(r.value)}</span> {r.direction} {sh.output === 'probability' ? `p̂(${sh.target})` : 'ŷ'} by <b className="mono">{Math.abs(r.phi).toFixed(sh.output === 'probability' ? 4 : 2)}</b>
                      {sh.output === 'probability' && <small className="helper"> ({(Math.abs(r.phi) * 100).toFixed(1)} pp)</small>}</span>
                  </li>
                ))}
              </ol>
              <p className="helper" style={{ fontSize: 12 }}>Reason i is feature i’s Shapley value φᵢ: its average marginal effect on f over coalitions of the other features, with absent features drawn from |B| = {sh.background_size} training rows. Adverse-action style reasons are the positive φᵢ for a declined applicant.</p>
            </div>
          </div>
          <PlotFrame title="Why This Prediction" right={<span className="mono helper" style={{ fontSize: 12 }}>{sh.output === 'probability' ? `f = p̂(${sh.target} | x)` : 'f = ŷ(x)'}</span>}
            legend={[{ name: 'φᵢ > 0 raises f', color: 'var(--viz-e)', data: [] }, { name: 'φᵢ < 0 lowers f', color: 'var(--viz-b)', data: [] }]}
            chips={<><ParamChip sym="|B|" value={String(sh.background_size)} /><ParamChip sym="coalitions" value={String(sh.nsamples)} /><ParamChip sym="d" value={String(sh.contributions.length)} /></>}
            caption={<span>f(x) = φ₀ + Σᵢ φᵢ exactly: φ₀ = E_B[f] = {sh.base_value.toFixed(4)}, residual f(x) − φ₀ − Σφᵢ = {sh.additivity_error.toExponential(1)}; {sh.seconds.toFixed(1)} s in a worker process.</span>}>
            <ShapEquation s={sh} testPrefix="try-shap" />
            <ShapWaterfall s={sh} testPrefix="try-shap" label={200} />
          </PlotFrame>
        </div>
      )}
    </div>
  )
}

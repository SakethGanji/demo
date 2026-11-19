import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ColumnProfile, DatasetDetail, ModelVersion, Run, TryResult } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ShapEquation, ShapWaterfall } from '@/shared/components/ShapWaterfall'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { ReasonList } from './ReasonCodes'
import { TryRecourse } from './Recourse'

type Field = { name: string; kind: 'number' | 'category' | 'text'; options: string[]; example: string; stats?: { min: number; max: number; mean: number } }

/** Try It on a registered version: a form generated from the model's schema, the prediction with its probabilities,
 *  and the top reasons for that prediction from Kernel SHAP (computed in a worker process). */
export function TryIt({ v, run, ds }: { v: ModelVersion; run: Run | null; ds: DatasetDetail | null }) {
  const fields: Field[] = useMemo(() => {
    const cols = new Map<string, ColumnProfile>((ds?.profile.columns ?? []).map((c) => [c.name, c]))
    const types = (run?.config?.column_types ?? {}) as Record<string, string>
    return (v.result?.version?.features ?? []).map((name) => {
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
  const [sentRow, setSentRow] = useState<Record<string, unknown>>({})

  const go = async () => {
    setBusy(true); setErr('')
    const row: Record<string, unknown> = {}
    for (const f of fields) {
      const x = vals[f.name]
      if (x === undefined || x === '') continue
      row[f.name] = f.kind === 'number' ? Number(x) : x
    }
    setSentRow(row)
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
          <span className="mono helper" style={{ fontSize: 12 }}>{v.result?.version?.best_model}</span>
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
            {out && !out.probabilities && out.quantiles && (() => {
              const qs = out.quantiles
              const lv = (out.quantile_levels ?? []).map((t) => String(Number(t.toFixed(6))))
              const iv = out.interval
              const lo = iv ? String(iv.lo) : lv[0], hi = iv ? String(Number(iv.hi.toFixed(6))) : lv[lv.length - 1]
              return (
                <>
                  <span className="micro-label">{out.model} · predicted range{iv ? ` (nominal ${Math.round(iv.nominal * 100)}%)` : ''}</span>
                  <div className="tp-class" data-testid="try-range">[{fmtScore(qs[lo], 0)}, {fmtScore(qs[hi], 0)}]</div>
                  <div className="tp-eq">[q̂<sub>{lo}</sub>(x), q̂<sub>{hi}</sub>(x)] · ŷ = q̂<sub>{String(out.centre)}</sub>(x) = {fmtScore(Number(out.prediction), 1)}</div>
                  <div className="tp-probs">
                    {lv.map((t) => <div key={t} className={`tp-prob ${Number(t) === out.centre ? 'on' : ''}`} data-testid={`try-q-${t}`}><span className="mono">q̂{t}</span><span /><span className="mono">{fmtScore(qs[t], 1)}</span></div>)}
                  </div>
                  <span className="tp-foot">q̂<sub>τ</sub>(x): the value falls at or below it with probability τ for rows like x · f(x) in {out.predict_ms.toFixed(1)} ms · the reasons below explain q̂<sub>{sh?.quantile_level ?? out.centre}</sub>{out.missing_columns.length ? ` · sent as missing: ${out.missing_columns.join(', ')}` : ''}</span>
                </>
              )
            })()}
            {out && !out.probabilities && !out.quantiles && (
              <>
                <span className="micro-label">{out.model} · predicted value</span>
                <div className="tp-class" data-testid="try-value">{fmtScore(Number(out.prediction), 2)}</div>
                <span className="tp-foot">f(x) in {out.predict_ms.toFixed(1)} ms{out.missing_columns.length ? ` · sent as missing: ${out.missing_columns.join(', ')}` : ''}</span>
              </>
            )}
          </div>
        </div>
      </div>

      {out?.reasons && 'error' in out.reasons && <p className="helper">Reason codes: {out.reasons.error}</p>}
      {out?.reasons && !('error' in out.reasons) && (() => {
        const r = out.reasons
        return (
          <div className="card" data-testid="try-reason-codes">
            <div className="card-head">
              <div className="left"><h3 className="card-title">Reason Codes</h3><span className="helper" style={{ fontSize: 12 }}>adverse ⟺ ŷ = {r.adverse_class} ⟺ p̂ {r.s > 0 ? '≥' : '<'} t = {r.threshold.toFixed(4)} · top {r.top_k} features with s·φⱼ &gt; 0</span></div>
              <span className={`mono`} style={{ fontSize: 12, color: r.adverse ? 'var(--status-danger)' : 'var(--status-success)' }} data-testid="try-adverse">{r.adverse ? `● adverse (p̂ = ${r.p_positive.toFixed(4)})` : `● not adverse (p̂ = ${r.p_positive.toFixed(4)})`}</span>
            </div>
            <div className="card-body">
              <ReasonList codes={r.codes} adverse={r.adverse} adverseClass={r.adverse_class} testId="try-reason-list" />
              <p className="helper" style={{ fontSize: 12 }}>The codes a notice, /score and batch scoring give for this applicant: {r.attribution.name}, B = {r.attribution.background} {r.reference === 'approved' ? 'approved' : ''} reference applicants (φ₀ = {r.base_value?.toFixed(4)}){r.attribution.coalitions ? `, M = ${r.attribution.coalitions} coalitions` : ''}; {r.reasons_ms.toFixed(0)} ms. They can differ from the Top Reasons below, which rank |φᵢ| against all training rows with 2,048 coalitions. Statements are edited in the Reason Codes tab.</p>
            </div>
            <TryRecourse v={v} row={sentRow} adverse={r.adverse} />
          </div>
        )
      })()}

      {sh && (
        <div className="grid try-why">
          <div className="card" data-testid="try-reasons">
            <div className="card-head"><div className="left"><h3 className="card-title">Top Reasons</h3><span className="helper" style={{ fontSize: 12 }}>largest |φᵢ| first</span></div></div>
            <div className="card-body">
              <ol className="reasons">
                {sh.reasons.map((r, i) => (
                  <li key={r.feature} data-testid={`reason-${i}`}>
                    <span className={`reason-dir ${r.phi >= 0 ? 'up' : 'down'}`}>{r.phi >= 0 ? '▲' : '▼'}</span>
                    <span><b className="mono">{r.feature}</b> = <span className="mono">{r.value == null ? 'missing' : String(r.value)}</span> {r.direction} {sh.output === 'probability' ? `p̂(${sh.target})` : sh.quantile_level != null ? `q̂${sh.quantile_level}` : 'ŷ'} by <b className="mono">{Math.abs(r.phi).toFixed(sh.output === 'probability' ? 4 : 2)}</b>
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

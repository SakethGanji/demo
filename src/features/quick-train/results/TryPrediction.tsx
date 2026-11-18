import { useEffect, useMemo, useState } from 'react'
import { ApiError, api } from '@/shared/api/client'
import type { ColumnProfile, Prediction, Run } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import type { Dataset } from '../datasets'
import { fmtScore } from '../wizard'

type Field = { name: string; kind: 'number' | 'category' | 'text'; options: string[]; example: string }

/** A form generated from the run's feature columns; the prediction comes from the run's saved AutoGluon predictor. */
export function TryPrediction({ run, ds, live, residualSd, models }: { run: Run; ds: Dataset | null; live: boolean; residualSd: number | null; models: string[] }) {
  const fields: Field[] = useMemo(() => {
    const cols = new Map<string, ColumnProfile>((ds?.profile?.columns ?? []).map((c) => [c.name, c]))
    const typesOverride = (run.resolved?.fit_kwargs?.feature_metadata as { type_group_map_special?: Record<string, string[]>; type_map_raw?: Record<string, string> } | undefined)
    const textOverride = new Set(typesOverride?.type_group_map_special?.text ?? [])
    return (run.data?.features ?? run.features ?? []).map((name) => {
      const c = cols.get(name)
      const raw = typesOverride?.type_map_raw?.[name]
      const kind: Field['kind'] = textOverride.has(name) || c?.type === 'text' ? 'text' : raw === 'object' || c?.type === 'category' || c?.type === 'id' ? 'category' : 'number'
      return { name, kind, options: (c?.top ?? []).map((t) => t.value), example: c?.example ?? ds?.cols.find((x) => x.name === name)?.sample ?? '' }
    })
  }, [run, ds])
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.name, f.example])))
  // The dataset profile can arrive after this card mounts (a run resumed from the URL loads it asynchronously): fill
  // in its examples then, keeping anything the user already typed.
  useEffect(() => {
    setVals((v) => ({ ...Object.fromEntries(fields.map((f) => [f.name, f.example])), ...Object.fromEntries(Object.entries(v).filter(([, x]) => x !== '')) }))
  }, [fields])
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [out, setOut] = useState<Prediction | null>(null)

  const predict = async () => {
    setBusy(true); setErr('')
    const row: Record<string, unknown> = {}
    for (const f of fields) {
      const v = vals[f.name]
      if (v === undefined || v === '') continue
      row[f.name] = f.kind === 'number' ? Number(v) : v
    }
    try { setOut(await api.runs.predict(run.id, [row], model || undefined)) } catch (e) {
      setErr(e instanceof ApiError && e.unreachable ? 'The API is not reachable.' : (e as Error).message)
    } finally { setBusy(false) }
  }

  const probs = out?.probabilities?.[0] ? Object.entries(out.probabilities[0]).sort((a, b) => b[1] - a[1]) : []
  const yhat = out?.predictions[0]
  return (
    <div className="card" data-testid="try-prediction">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Try A Prediction</h3><span className="helper" style={{ fontSize: 12 }}>one row x, every feature the model was trained on · leave a field empty to send it as missing</span></div>
        <select className="select sm" aria-label="Model" data-testid="tp-model" value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">{run.best_model} (best)</option>
          {models.filter((m) => m !== run.best_model).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div className="card-body tp-body">
        <div className="tp-form">
          {fields.map((f) => (
            <label key={f.name} className={`tp-field ${f.kind === 'text' ? 'wide' : ''}`}>
              <span className="tp-label"><span className="mono">{f.name}</span><small>{f.kind}</small></span>
              {f.kind === 'text'
                ? <textarea className="kw-input" rows={3} data-testid={`tp-${f.name}`} value={vals[f.name] ?? ''} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
                : f.kind === 'category' && f.options.length > 0
                  ? <select className="select" data-testid={`tp-${f.name}`} value={vals[f.name] ?? ''} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}>
                      <option value="">— missing —</option>
                      {[...new Set([...(vals[f.name] ? [vals[f.name]] : []), ...f.options])].map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  : <input className="input" data-testid={`tp-${f.name}`} type={f.kind === 'number' ? 'number' : 'text'} step="any" value={vals[f.name] ?? ''} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />}
            </label>
          ))}
          <div className="row" style={{ gap: 'var(--space-8)', gridColumn: '1 / -1' }}>
            <button className="btn primary" data-testid="tp-predict" disabled={!live || busy} onClick={predict}>Predict</button>
            {busy && <ResonanceWave width={80} height={16} />}
            {!live && <span className="helper" style={{ fontSize: 12 }}>Needs the live API: mock mode has no saved predictor.</span>}
          </div>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)', gridColumn: '1 / -1' }}>✕ {err}</p>}
        </div>
        <div className="tp-out navy-well" data-testid="tp-result">
          {!out && <span className="tp-empty">ŷ = f(x) appears here</span>}
          {out && out.probabilities && (
            <>
              <span className="micro-label">{out.model} · predicted class</span>
              <div className="tp-class" data-testid="tp-class">{String(yhat)}</div>
              <div className="tp-eq">ŷ = argmaxₖ p̂ₖ(x), &nbsp;p̂ = ({probs.slice(0, 3).map(([, p]) => p.toFixed(3)).join(', ')}{probs.length > 3 ? ', …' : ''})</div>
              <div className="tp-probs">
                {probs.map(([c, p]) => (
                  <div key={c} className={`tp-prob ${String(c) === String(yhat) ? 'on' : ''}`} data-testid={`tp-prob-${c}`}>
                    <span className="mono">{c}</span><i className="tp-bar"><i style={{ width: `${p * 100}%` }} /></i><span className="mono">{(p * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
              <span className="tp-foot">Σₖ p̂ₖ = {probs.reduce((a, [, p]) => a + p, 0).toFixed(3)}{out.positive_class != null ? ` · positive class: ${out.positive_class}` : ''}{out.missing_columns.length ? ` · sent as missing: ${out.missing_columns.join(', ')}` : ''}</span>
            </>
          )}
          {out && !out.probabilities && (
            <>
              <span className="micro-label">{out.model} · predicted {run.label}</span>
              <div className="tp-class" data-testid="tp-value">{fmtScore(Number(yhat), 2)}</div>
              {residualSd != null && <div className="tp-eq">ŷ ± 1.96·σᵣ = [{fmtScore(Number(yhat) - 1.96 * residualSd, 1)}, {fmtScore(Number(yhat) + 1.96 * residualSd, 1)}]</div>}
              <span className="tp-foot">{residualSd != null ? `σᵣ = ${fmtScore(residualSd, 2)}: standard deviation of the holdout residuals; the band is where ~95% of rows like the holdout's would land if errors were normal. ` : ''}{out.missing_columns.length ? `Sent as missing: ${out.missing_columns.join(', ')}` : ''}</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

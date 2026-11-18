import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Explain, HoldoutPredictions, Run, ShapResult } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ShapEquation, ShapWaterfall } from '@/shared/components/ShapWaterfall'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

/** Quick picks from the holdout predictions: the highest and lowest score, a typical row, the worst miss. */
function picks(preds: HoldoutPredictions | null, ex: Explain, run: Run): { label: string; row: number }[] {
  if (!preds) return []
  const out: { label: string; row: number }[] = []
  if (preds.probabilities && preds.classes) {
    const k = run.problem_type === 'binary' ? preds.classes.indexOf(String(run.positive_class)) : -1
    const s = preds.probabilities.map((p, i) => [k >= 0 ? p[k] : Math.max(...p), i] as const).sort((a, b) => a[0] - b[0])
    out.push({ label: k >= 0 ? `highest p̂(${run.positive_class})` : 'most confident', row: preds.rows[s[s.length - 1][1]] })
    out.push({ label: 'median score', row: preds.rows[s[Math.floor(s.length / 2)][1]] })
    out.push({ label: k >= 0 ? `lowest p̂(${run.positive_class})` : 'least confident', row: preds.rows[s[0][1]] })
  } else {
    const r = preds.y_true.map((y, i) => [Number(preds.y_pred[i]), i] as const).sort((a, b) => a[0] - b[0])
    out.push({ label: 'highest ŷ', row: preds.rows[r[r.length - 1][1]] }, { label: 'median ŷ', row: preds.rows[r[Math.floor(r.length / 2)][1]] }, { label: 'lowest ŷ', row: preds.rows[r[0][1]] })
  }
  if (ex.worst[0]) out.push({ label: 'worst miss', row: ex.worst[0].row })
  return out
}

/** Kernel SHAP for one holdout row as a waterfall: φ₀ = E[f] → + φᵢ per feature → f(x), with the equation written out. */
export function ShapPanel({ run, ex, preds, initialRow }: { run: Run; ex: Explain; preds: HoldoutPredictions | null; initialRow?: number | null }) {
  const options = useMemo(() => picks(preds, ex, run), [preds, ex, run])
  const [row, setRow] = useState<number | null>(initialRow ?? null)
  const [target, setTarget] = useState<string | undefined>(undefined)
  const [s, setS] = useState<ShapResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (row == null && options[0]) setRow(options[0].row) }, [options, row])
  useEffect(() => {
    if (row == null) return
    let alive = true
    setBusy(true); setErr('')
    api.runs.shap(run.id, row, target).then((r) => alive && setS(r)).catch((e) => alive && setErr((e as Error).message)).finally(() => alive && setBusy(false))
    return () => { alive = false }
  }, [run.id, row, target])

  const head = (
    <div className="shap-pick">
      <span className="micro-label">holdout row</span>
      <div className="seg" role="tablist">{options.map((o) => <button key={o.label} className={row === o.row ? 'on' : ''} onClick={() => setRow(o.row)}>{o.label}</button>)}</div>
      <input className="num-input" style={{ width: 84 }} aria-label="Holdout row number" type="number" min={0} value={row ?? ''} onChange={(e) => setRow(Number(e.target.value))} data-testid="shap-row" />
      {s?.classes && run.problem_type === 'multiclass' && (
        <select className="select" aria-label="Class to explain" value={target ?? s.target ?? ''} onChange={(e) => setTarget(e.target.value)}>{s.classes.map((c) => <option key={c} value={c}>explain p̂({c})</option>)}</select>
      )}
      {busy && <span className="row helper" style={{ fontSize: 12 }}><ResonanceWave /> Kernel SHAP: {s ? s.nsamples : 2048} coalitions × {s ? s.background_size : 50} background rows…</span>}
    </div>
  )
  if (err) return <div className="card card-body">{head}<p role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p></div>
  if (!s) return <div className="card card-body">{head}</div>

  const prob = s.output === 'probability'
  const fname = prob ? `p̂(${s.target} | x)` : 'ŷ(x)'
  const d = (v: number) => (prob ? v.toFixed(4) : fmtScore(v, 2))
  return (
    <PlotFrame title="SHAP · One Prediction, Decomposed" right={<span className="mono helper" style={{ fontSize: 12 }}>row {s.row} · y = {String(s.y_true)}</span>}
      legend={[{ name: 'φᵢ > 0 raises f', color: 'var(--viz-e)', data: [] }, { name: 'φᵢ < 0 lowers f', color: 'var(--viz-b)', data: [] }]}
      chips={<><ParamChip sym="|B|" value={String(s.background_size)} /><ParamChip sym="coalitions" value={String(s.nsamples)} /><ParamChip sym="d" value={String(s.contributions.length)} /></>}
      caption={<span>f(x) = {fname} of <span className="mono">{s.model}</span>. Kernel SHAP fits φ so that f(x) = φ₀ + Σᵢ φᵢ exactly (efficiency): φ₀ = E_B[f] = {d(s.base_value)} is the mean over the |B| = {s.background_size} background training rows, and φᵢ is feature i's average marginal contribution over coalitions of the other features (absent features take background values). Residual f(x) − φ₀ − Σφᵢ = {s.additivity_error.toExponential(1)}.{s.cached ? ' Cached.' : ` ${s.seconds.toFixed(1)} s.`}</span>}>
      {head}
      <ShapEquation s={s} />
      <ShapWaterfall s={s} />
    </PlotFrame>
  )
}

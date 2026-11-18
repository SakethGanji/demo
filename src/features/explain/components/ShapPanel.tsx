import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Explain, HoldoutPredictions, Run, ShapResult } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

const fv = (v: unknown) => (v == null || v === '' ? 'missing' : typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : fmtScore(v, 3)) : String(v).length > 28 ? `${String(v).slice(0, 26)}…` : String(v))

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
  const [ref, W] = useWidth<HTMLDivElement>()
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
  const steps = s.contributions
  let acc = s.base_value
  const bars = steps.map((c) => { const from = acc; acc += c.phi; return { ...c, from, to: acc } })
  const all = [s.base_value, s.fx, ...bars.flatMap((b) => [b.from, b.to])]
  const lo0 = Math.min(...all), hi0 = Math.max(...all)
  const padv = (hi0 - lo0) * 0.08 || 0.01
  const lo = prob ? Math.max(0, lo0 - padv) : lo0 - padv, hi = prob ? Math.min(1, hi0 + padv) : hi0 + padv
  const LBL = 230, R = 90, ROW = 22, TOP = 26
  const pw = Math.max(120, W - LBL - R)
  const sx = (v: number) => LBL + ((v - lo) / (hi - lo || 1)) * pw
  const H = TOP + (bars.length + 2) * ROW + 24
  const d = (v: number) => (prob ? v.toFixed(4) : fmtScore(v, 2))
  const eq = steps.slice(0, 6)
  return (
    <PlotFrame title="SHAP · One Prediction, Decomposed" right={<span className="mono helper" style={{ fontSize: 12 }}>row {s.row} · y = {String(s.y_true)}</span>}
      legend={[{ name: 'φᵢ > 0 raises f', color: 'var(--viz-e)', data: [] }, { name: 'φᵢ < 0 lowers f', color: 'var(--viz-b)', data: [] }]}
      chips={<><ParamChip sym="|B|" value={String(s.background_size)} /><ParamChip sym="coalitions" value={String(s.nsamples)} /><ParamChip sym="d" value={String(steps.length)} /></>}
      caption={<span>f(x) = {fname} of <span className="mono">{s.model}</span>. Kernel SHAP fits φ so that f(x) = φ₀ + Σᵢ φᵢ exactly (efficiency): φ₀ = E_B[f] = {d(s.base_value)} is the mean over the |B| = {s.background_size} background training rows, and φᵢ is feature i's average marginal contribution over coalitions of the other features (absent features take background values). Residual f(x) − φ₀ − Σφᵢ = {s.additivity_error.toExponential(1)}.{s.cached ? ' Cached.' : ` ${s.seconds.toFixed(1)} s.`}</span>}>
      {head}
      <div className="shap-eq" data-testid="shap-equation">
        <span className="math">f(x)</span> = <span className="math">φ₀</span> + Σᵢ <span className="math">φᵢ</span> &nbsp;⇒&nbsp;
        <b className="mono" data-testid="shap-fx">{d(s.fx)}</b> = <span className="mono" data-testid="shap-base">{d(s.base_value)}</span>
        {eq.map((c) => <span key={c.feature} className={`mono ${c.phi >= 0 ? 'up-e' : 'down-b'}`}> {c.phi >= 0 ? '+' : '−'} {d(Math.abs(c.phi))}<sub className="shap-sub">{c.feature}</sub></span>)}
        {steps.length > eq.length && <span className="mono muted"> {(() => { const r = steps.slice(eq.length).reduce((a, c) => a + c.phi, 0); return `${r >= 0 ? '+' : '−'} ${d(Math.abs(r))}` })()}<sub className="shap-sub">{steps.length - eq.length} others</sub></span>}
      </div>
      <div ref={ref} data-testid="shap-waterfall">
        <svg width={W} height={H}>
          {[lo, (lo + hi) / 2, hi].map((v) => <g key={v}><line x1={sx(v)} x2={sx(v)} y1={TOP - 6} y2={H - 22} stroke="var(--plot-grid)" /><text className="tick-text" x={sx(v)} y={H - 6} textAnchor="middle">{d(v)}</text></g>)}
          <text className="axis-title" x={W - 4} y={H - 6} textAnchor="end">{prob ? 'p̂' : 'ŷ'}</text>
          <g>
            <text x={LBL - 10} y={TOP + 4} textAnchor="end" className="fi-name"><tspan className="math-t">φ₀ = E[f]</tspan></text>
            <line x1={sx(s.base_value)} x2={sx(s.base_value)} y1={TOP - 8} y2={H - 22} stroke="var(--text-muted)" strokeDasharray="3 3" />
            <circle cx={sx(s.base_value)} cy={TOP} r={4} fill="var(--text-muted)" />
            <text x={W - R + 8} y={TOP + 4} className="tick-text" style={{ fill: 'var(--text-primary)' }}>{d(s.base_value)}</text>
          </g>
          {bars.map((b, i) => {
            const y = TOP + (i + 1) * ROW
            const pos = b.phi >= 0
            return (
              <g key={b.feature} data-testid={`shap-${b.feature}`}>
                <text x={LBL - 10} y={y + 4} textAnchor="end" className="fi-name">{b.feature} <tspan className="fi-val">= {fv(b.value)}</tspan></text>
                <rect x={Math.min(sx(b.from), sx(b.to))} y={y - 7} width={Math.max(1.5, Math.abs(sx(b.to) - sx(b.from)))} height={14} rx={2} fill={pos ? 'var(--viz-e)' : 'var(--viz-b)'} opacity={0.85} />
                {i < bars.length - 1 && <line x1={sx(b.to)} x2={sx(b.to)} y1={y + 7} y2={y + ROW - 7} stroke="var(--text-muted)" strokeWidth={0.75} />}
                <text x={W - R + 8} y={y + 4} className="tick-text" style={{ fill: pos ? 'var(--viz-e)' : 'var(--viz-b)' }}>{pos ? '+' : '−'}{d(Math.abs(b.phi))}</text>
              </g>
            )
          })}
          <g>
            <text x={LBL - 10} y={TOP + (bars.length + 1) * ROW + 4} textAnchor="end" className="fi-name"><tspan className="math-t">f(x)</tspan></text>
            <line x1={sx(s.fx)} x2={sx(s.fx)} y1={TOP + bars.length * ROW + 8} y2={TOP + (bars.length + 1) * ROW} stroke="var(--text-primary)" />
            <circle cx={sx(s.fx)} cy={TOP + (bars.length + 1) * ROW} r={5} fill="var(--accent-primary)" />
            <text x={W - R + 8} y={TOP + (bars.length + 1) * ROW + 4} className="tick-text" style={{ fill: 'var(--text-primary)', fontWeight: 700 }}>{d(s.fx)}</text>
          </g>
        </svg>
      </div>
    </PlotFrame>
  )
}

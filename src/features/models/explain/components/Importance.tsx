import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { useFetch } from '@/shared/hooks/useFetch'

/** Permutation importance on the holdout with 95% t-intervals (AutoGluon's feature_importance; the interval is the API's). */
export function Importance({ run }: { run: Run }) {
  const [busy, setBusy] = useState(false)
  const { data: fi, setData: setFi, err, setErr } = useFetch(() => api.runs.importance(run.id), [run.id])
  const [ref, W] = useWidth<HTMLDivElement>()
  const tighten = async () => {
    setBusy(true)
    try { setFi(await api.runs.importance(run.id, 10)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  if (err) return <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>
  if (!fi) return <div className="card card-body row"><ResonanceWave /> Shuffling each feature on the holdout and re-scoring (permutation importance, up to a minute)…</div>

  const rows = [...fi.features].sort((a, b) => b.importance - a.importance)
  const lo = Math.min(0, ...rows.map((r) => r.ci_low ?? r.importance))
  const hi = Math.max(...rows.map((r) => r.ci_high ?? r.importance), 1e-9)
  const metric = fi.eval_metric ?? run.eval_metric?.name ?? ''
  const LBL = 150, R = 250, ROW = 24
  const plotW = Math.max(120, W - LBL - R)
  const sx = (v: number) => LBL + ((v - lo) / (hi - lo || 1)) * plotW
  const n = fi.meta.num_shuffle_sets
  const tcrit = rows.find((r) => r.t_crit != null)?.t_crit
  return (
    <PlotFrame title="Permutation Feature Importance"
      right={<button className="btn ghost sm" onClick={tighten} disabled={busy || n >= 10} data-testid="fi-tighten">{busy ? <><ResonanceWave /> re-shuffling 10×…</> : n >= 10 ? `n = ${n} shuffles` : 'Tighten: 10 shuffles'}</button>}
      legend={[{ name: 'Îⱼ', color: 'var(--viz-a)', data: [] }, { name: '95% CI', color: 'var(--text-muted)', data: [] }]}
      chips={<><ParamChip sym="n" value={`${n} shuffles`} /><ParamChip sym="m" value={`${(fi.meta.subsample_size ?? run.holdout?.n_rows ?? 0).toLocaleString()} rows`} /><ParamChip sym="metric" value={metric} /></>}
      caption={<span>Îⱼ = s(f, D) − (1/n) Σₖ s(f, D₍ⱼ,ₖ₎): the drop in holdout {metricInfo(metric)?.name ?? metric} s when column j is shuffled (D₍ⱼ,ₖ₎ = the m holdout rows with feature j permuted, k-th of n times). Bars: Îⱼ ± t₀.₉₇₅,ₙ₋₁·sⱼ/√n{tcrit ? ` (t = ${tcrit.toFixed(3)})` : ''}, sⱼ the std over shuffles; p is the one-sided test of Iⱼ ≤ 0. ≤ 0: the model does not need the feature.</span>}>
      <div ref={ref} data-testid="importance">
        <svg width={W} height={rows.length * ROW + 26}>
          {[lo, 0, hi / 2, hi].filter((v, i, a) => a.indexOf(v) === i).map((v) => (
            <g key={v}><line x1={sx(v)} x2={sx(v)} y1={0} y2={rows.length * ROW} stroke={v === 0 ? 'var(--text-muted)' : 'var(--plot-grid)'} strokeDasharray={v === 0 ? '3 3' : undefined} />
              <text className="tick-text" x={sx(v)} y={rows.length * ROW + 16} textAnchor="middle">{fmtScore(v, 4)}</text></g>
          ))}
          {rows.map((r, i) => {
            const y = i * ROW + ROW / 2
            const sig = r.ci_low != null && r.ci_low > 0
            return (
              <g key={r.feature} data-testid={`fi-${r.feature}`}>
                <text x={LBL - 10} y={y + 4} textAnchor="end" className="fi-name" style={{ fill: sig ? 'var(--text-primary)' : 'var(--text-muted)' }}>{r.feature}</text>
                <rect x={Math.min(sx(0), sx(r.importance))} y={y - 6} width={Math.abs(sx(r.importance) - sx(0))} height={12} rx={2} fill={sig ? 'var(--viz-a)' : 'var(--border-track)'} opacity={0.85} />
                {r.ci_low != null && r.ci_high != null && <g stroke="var(--text-primary)" strokeWidth={1.25}>
                  <line x1={sx(r.ci_low)} x2={sx(r.ci_high)} y1={y} y2={y} /><line x1={sx(r.ci_low)} x2={sx(r.ci_low)} y1={y - 4} y2={y + 4} /><line x1={sx(r.ci_high)} x2={sx(r.ci_high)} y1={y - 4} y2={y + 4} />
                </g>}
                <text x={W - R + 12} y={y + 4} className="tick-text" style={{ fill: 'var(--text-primary)' }}>{fmtScore(r.importance, 4)}</text>
                <text x={W - R + 70} y={y + 4} className="tick-text">{r.ci_low != null ? `[${fmtScore(r.ci_low, 3)}, ${fmtScore(r.ci_high, 3)}]` : ''}</text>
                <text x={W - 6} y={y + 4} className="tick-text" textAnchor="end" style={{ fill: r.p_value < 0.05 ? 'var(--text-primary)' : 'var(--text-muted)' }}>p {r.p_value < 0.001 ? '< .001' : `= ${r.p_value.toFixed(3)}`}</text>
              </g>
            )
          })}
        </svg>
      </div>
    </PlotFrame>
  )
}

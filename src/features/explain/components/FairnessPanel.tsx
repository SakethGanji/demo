import { useEffect, useState, type ReactNode } from 'react'
import { api } from '@/shared/api/client'
import type { Explain, Fairness, Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

const LABELS: Record<string, [string, string]> = {
  count: ['n_g', 'rows in group g'],
  selection_rate: ['P(ŷ = 1 | g)', 'share flagged positive'],
  base_rate: ['P(y = 1 | g)', 'share actually positive'],
  accuracy: ['acc_g', 'share predicted right'],
  tpr: ['TPR_g', 'recall: P(ŷ = 1 | y = 1, g)'],
  fpr: ['FPR_g', 'P(ŷ = 1 | y = 0, g)'],
  precision: ['PPV_g', 'P(y = 1 | ŷ = 1, g)'],
  f1_macro: ['F1ᵐᵃᶜʳᵒ_g', 'macro F1 in group g'],
  mae: ['MAE_g', 'mean |y − ŷ| in group g'],
  rmse: ['RMSE_g', '√mean (y − ŷ)² in group g'],
  mean_residual: ['r̄_g', 'mean y − ŷ (bias) in group g'],
  mean_prediction: ['ȳ̂_g', 'mean prediction in group g'],
}
const DOT_METRICS: Record<string, { key: string; color: string }[]> = {
  binary: [{ key: 'selection_rate', color: 'var(--viz-a)' }, { key: 'tpr', color: 'var(--viz-d)' }, { key: 'fpr', color: 'var(--viz-b)' }],
  multiclass: [{ key: 'accuracy', color: 'var(--viz-a)' }, { key: 'f1_macro', color: 'var(--viz-c)' }],
  regression: [{ key: 'mae', color: 'var(--viz-a)' }, { key: 'rmse', color: 'var(--viz-d)' }],
}

/** fairlearn's MetricFrame by a group column the user picks, with the gaps between groups. */
export function FairnessPanel({ run, ex }: { run: Run; ex: Explain }) {
  const cands = ex.group_candidates
  const pref = cands.find((c) => c.type === 'category' && /region|segment|gender|sex|age|group|employment|home/i.test(c.column)) ?? cands[0]
  const [group, setGroup] = useState(pref?.column ?? '')
  const [f, setF] = useState<Fairness | null>(null)
  const [err, setErr] = useState('')
  const [ref, W] = useWidth<HTMLDivElement>()
  useEffect(() => {
    if (!group) return
    let alive = true
    setErr('')
    api.runs.fairness(run.id, group).then((r) => alive && setF(r)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, group, run.threshold?.value])
  if (!cands.length) return <div className="card card-body helper">No column can define groups (categories with ≤ 50 values or numbers).</div>
  const pt = run.problem_type ?? 'binary'
  const dots = DOT_METRICS[pt] ?? DOT_METRICS.binary
  const head = (
    <div className="row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap' }}>
      <span className="micro-label">group by</span>
      <select className="select" aria-label="Group column" value={group} onChange={(e) => setGroup(e.target.value)} data-testid="fair-group">
        {cands.map((c) => <option key={c.column} value={c.column}>{c.column} ({c.type === 'number' ? 'quartiles' : `${c.groups} values`})</option>)}
      </select>
      {f?.threshold != null && <ParamChip sym="t" value={f.threshold.toFixed(3)} />}
      {!f && !err && <ResonanceWave />}
    </div>
  )
  if (err) return <div className="card card-body">{head}<p role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p></div>
  if (!f) return <div className="card card-body">{head}</div>

  const groups = f.by_group
  const vmax = pt === 'regression' ? Math.max(...groups.flatMap((g) => dots.map((d) => g[d.key] ?? 0))) * 1.1 : 1
  const LBL = 150, R = 24, ROW = 28
  const pw = Math.max(100, W - LBL - R)
  const sx = (v: number) => LBL + (v / (vmax || 1)) * pw
  const gaps = f.gaps
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="fairness">
      <div className="card card-body">{head}</div>
      <div className="fair-gaps">
        {pt === 'binary' ? <>
          <Gap k="demographic parity difference" v={gaps.demographic_parity_difference} f={<>max<sub>g</sub> P(ŷ=1 | g) − min<sub>g</sub> P(ŷ=1 | g)</>} id="dpd" />
          <Gap k="equalised odds difference" v={gaps.equalized_odds_difference} f={<>max(ΔTPR, ΔFPR), Δ = max<sub>g</sub> − min<sub>g</sub></>} id="eod" />
          <Gap k="TPR gap (equal opportunity)" v={gaps.tpr_difference} f={<>max<sub>g</sub> TPR<sub>g</sub> − min<sub>g</sub> TPR<sub>g</sub></>} id="tprd" />
          <Gap k="demographic parity ratio" v={gaps.demographic_parity_ratio} f={<>min<sub>g</sub> P(ŷ=1 | g) / max<sub>g</sub> P(ŷ=1 | g)</>} id="dpr" ratio />
        </> : Object.entries(gaps).map(([k, v]) => <Gap key={k} k={k.replace(/_/g, ' ')} v={v} f="max over groups − min over groups" id={k} />)}
      </div>
      <PlotFrame title={`By ${f.group}`} legend={dots.map((d) => ({ name: LABELS[d.key][0], color: d.color, data: [] }))}
        chips={<><ParamChip sym="G" value={String(groups.length)} /><ParamChip sym="n" value={f.n.toLocaleString()} />{f.binning === 'quartiles' && <ParamChip sym="bins" value="quartiles" />}</>}
        caption={<span>One row per group g of <b className="mono">{f.group}</b>{f.binning === 'quartiles' ? ' (numbers cut at their quartiles)' : ''}, computed with fairlearn's MetricFrame on the holdout{f.threshold != null ? ` at t = ${f.threshold.toFixed(3)}` : ''}. {dots.map((d) => `${LABELS[d.key][0]}: ${LABELS[d.key][1]}`).join('; ')}. The dashed tick is the overall value. Small groups (n_g) are noisy: read gaps with their counts.</span>}>
        <div ref={ref} data-testid="fair-dots">
          <svg width={W} height={groups.length * ROW + 24}>
            {[0, 0.25, 0.5, 0.75, 1].map((fr) => <g key={fr}><line x1={sx(vmax * fr)} x2={sx(vmax * fr)} y1={0} y2={groups.length * ROW} stroke="var(--plot-grid)" /><text className="tick-text" x={sx(vmax * fr)} y={groups.length * ROW + 16} textAnchor="middle">{fmtScore(vmax * fr, 2)}</text></g>)}
            {dots.map((d) => f.overall[d.key] != null && <line key={d.key} x1={sx(f.overall[d.key])} x2={sx(f.overall[d.key])} y1={0} y2={groups.length * ROW} stroke={d.color} strokeDasharray="3 3" opacity={0.6} />)}
            {groups.map((g, i) => {
              const y = i * ROW + ROW / 2
              const vals = dots.map((d) => g[d.key]).filter((v): v is number => v != null)
              return (
                <g key={g.group}>
                  <text x={LBL - 10} y={y + 4} textAnchor="end" className="fi-name">{g.group} <tspan className="fi-val">n={g.count}</tspan></text>
                  {vals.length > 1 && <line x1={sx(Math.min(...vals))} x2={sx(Math.max(...vals))} y1={y} y2={y} stroke="var(--border-track)" strokeWidth={2} />}
                  {dots.map((d) => g[d.key] != null && <circle key={d.key} cx={sx(g[d.key]!)} cy={y} r={5} fill={d.color}><title>{`${LABELS[d.key][0]} = ${g[d.key]!.toFixed(4)}`}</title></circle>)}
                </g>
              )
            })}
          </svg>
        </div>
      </PlotFrame>
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">MetricFrame</h3><span className="helper" style={{ fontSize: 12 }}>fairlearn.metrics.MetricFrame(metrics, y_true, y_pred, sensitive_features = {f.group}).by_group</span></div></div>
        <div className="table-wrap"><table className="table" data-testid="fair-table">
          <thead><tr><th className="math-th">g</th>{f.metrics.map((m) => <th key={m} className="r math-th" title={LABELS[m]?.[1]}>{LABELS[m]?.[0] ?? m}</th>)}</tr></thead>
          <tbody>
            {groups.map((g) => <tr key={g.group}><td>{g.group}</td>{f.metrics.map((m) => <td key={m} className="r table-cell">{g[m] == null ? '—' : m === 'count' ? g[m]!.toLocaleString() : fmtScore(g[m]!, 4)}</td>)}</tr>)}
            <tr className="sel"><td><b>overall</b></td>{f.metrics.map((m) => <td key={m} className="r table-cell"><b>{m === 'count' ? f.overall[m].toLocaleString() : fmtScore(f.overall[m], 4)}</b></td>)}</tr>
            <tr><td className="muted">max − min</td>{f.metrics.map((m) => <td key={m} className="r table-cell muted">{m === 'count' || f.difference[m] == null ? '' : fmtScore(f.difference[m]!, 4)}</td>)}</tr>
          </tbody>
        </table></div>
      </div>
    </div>
  )
}

function Gap({ k, v, f, id, ratio }: { k: string; v: number; f: ReactNode; id: string; ratio?: boolean }) {
  const warn = ratio ? v < 0.8 : v > 0.1
  return (
    <div className={`card fair-gap ${warn ? 'warn' : ''}`}>
      <span className="micro-label">{k}</span>
      <b className="mono" data-testid={`fair-${id}`}>{fmtScore(v, 4)}</b>
      <small className="math-s">{f}</small>
      <small className="muted">{ratio ? (warn ? 'below the four-fifths rule (0.8)' : 'meets the four-fifths rule (≥ 0.8)') : (warn ? 'over 0.10: review this gap' : '≤ 0.10')}</small>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Explain, Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, type XYSeries } from '@/shared/components/XYPlot'
import { useWidth } from '@/shared/hooks/useWidth'
import { FAMILY_SHORT, fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { RUN_COLORS, bestRow, presetsOf } from '../RunList'

const T01 = [0, 0.2, 0.4, 0.6, 0.8, 1]

/** Selected runs side by side: holdout metrics (best per row marked), ROC/PR or residual curves from the API's
 *  explain endpoint, and the best validation score per model family. */
export function CompareRuns({ runs, go }: { runs: Run[]; go: (r: string) => void }) {
  const done = runs.filter((r) => r.status === 'succeeded').slice(0, 6)
  const [ex, setEx] = useState<Record<string, Explain>>({})
  useEffect(() => {
    let alive = true
    done.filter((r) => !ex[r.id]).forEach((r) => api.runs.explain(r.id).then((e) => alive && setEx((m) => ({ ...m, [r.id]: e }))).catch(() => {}))
    return () => { alive = false }
  }, [done.map((r) => r.id).join()]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!done.length) return <div className="card card-body helper" data-testid="compare-empty">Select finished runs in the Runs tab to compare them.</div>

  const color = (id: string) => RUN_COLORS[done.findIndex((r) => r.id === id) % RUN_COLORS.length]
  const keys = [...new Set(done.flatMap((r) => Object.keys(r.holdout?.metrics ?? {})))]
  const mixed = new Set(done.map((r) => `${r.dataset_id}|${r.label}`)).size > 1
  const binary = done.filter((r) => r.problem_type === 'binary' && ex[r.id]?.kind === 'classification')
  const regs = done.filter((r) => ex[r.id]?.kind === 'regression')

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="compare">
      {mixed && <div className="card card-body" role="note" style={{ borderColor: 'var(--status-warning)', background: 'var(--status-warning-bg)', fontSize: 13 }}>▲ These runs predict different labels or use different data: their scores are not on the same footing. Compare runs of one dataset and label.</div>}
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Holdout Metrics</h3><span className="helper" style={{ fontSize: 12 }}>AutoGluon's evaluate() on each run's holdout · best per row in bold · Δ against the first run</span></div><ParamChip sym="runs" value={String(done.length)} /></div>
        <div className="table-wrap"><table className="table" data-testid="compare-table">
          <thead><tr><th>metric</th>{done.map((r) => <th key={r.id} className="r"><i className="run-dot" style={{ background: color(r.id) }} /><span className="nocase-h">{r.id}</span></th>)}</tr></thead>
          <tbody>
            <tr className="meta"><td className="muted">best model</td>{done.map((r) => <td key={r.id} className="r table-cell muted">{r.best_model}</td>)}</tr>
            <tr className="meta"><td className="muted">presets · τ</td>{done.map((r) => <td key={r.id} className="r table-cell muted">{presetsOf(r)} · {String(r.config.time_limit ?? '∞')}s</td>)}</tr>
            {keys.map((k) => {
              const info = metricInfo(k)
              const vals = done.map((r) => r.holdout?.metrics[k] ?? null)
              const finite = vals.filter((v): v is number => v != null)
              const greater = info?.greater ?? true
              const best = finite.length ? (greater ? Math.max(...finite) : Math.min(...finite)) : null
              return (
                <tr key={k}>
                  <td><span className="mono">{k}</span> <span className="muted">{greater ? '↑' : '↓'}</span>{info && <div className="math-s muted" style={{ fontSize: 12 }}>{info.formula}</div>}</td>
                  {vals.map((v, i) => (
                    <td key={done[i].id} className="r table-cell" data-testid={`cmp-${k}-${done[i].id}`}>
                      {v == null ? '—' : <span style={{ fontWeight: v === best && finite.length > 1 ? 700 : 400 }}>{fmtScore(v)}</span>}
                      {i > 0 && v != null && vals[0] != null && <div className={`mono ${(greater ? v - vals[0] > 0 : v - vals[0] < 0) ? 'up' : v === vals[0] ? 'muted' : 'down'}`} style={{ fontSize: 11 }}>{v - vals[0] >= 0 ? '+' : '−'}{fmtScore(Math.abs(v - vals[0]))}</div>}
                    </td>
                  ))}
                </tr>
              )
            })}
            <tr><td><span className="mono">threshold t</span></td>{done.map((r) => <td key={r.id} className="r table-cell">{r.threshold ? `${r.threshold.value.toFixed(3)} (${r.threshold.source})` : '—'}</td>)}</tr>
          </tbody>
        </table></div>
      </div>

      {binary.length > 0 && <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <PlotFrame title="ROC, Run by Run" legend={binary.map((r) => { const e = ex[r.id] as Extract<Explain, { kind: 'classification' }>; const c = e.roc[e.positive_class!]; return { name: `${r.id} · AUC ${fmtScore(c?.auc)}`, color: color(r.id), data: [] } })}
          caption={<span>TPR against FPR on each run's holdout (positive class as AutoGluon chose it). AUC = P(p̂(x⁺) &gt; p̂(x⁻)); a curve to the upper left ranks positives above negatives more often.</span>}>
          <XYPlot testId="compare-roc" series={binary.map((r): XYSeries => { const e = ex[r.id] as Extract<Explain, { kind: 'classification' }>; const c = e.roc[e.positive_class!]; return { name: r.id, color: color(r.id), points: c.fpr.map((v, i) => [v, c.tpr[i]]) } })}
            x={{ min: 0, max: 1, ticks: T01, title: 'FPR', fmt: (v) => v.toFixed(1) }} y={{ min: 0, max: 1, ticks: T01, title: 'TPR', fmt: (v) => v.toFixed(1) }} diagonal={{ label: 'chance' }} height={260} />
        </PlotFrame>
        <PlotFrame title="Precision–Recall, Run by Run" legend={binary.map((r) => { const e = ex[r.id] as Extract<Explain, { kind: 'classification' }>; const c = e.pr[e.positive_class!]; return { name: `${r.id} · AP ${fmtScore(c?.average_precision)}`, color: color(r.id), data: [] } })}
          caption={<span>Precision P = TP/(TP + FP) against recall R = TP/(TP + FN). AP = Σₙ (Rₙ − Rₙ₋₁)·Pₙ. ● marks each run's threshold t.</span>}>
          <XYPlot testId="compare-pr" series={binary.map((r): XYSeries => { const e = ex[r.id] as Extract<Explain, { kind: 'classification' }>; const c = e.pr[e.positive_class!]; return { name: r.id, color: color(r.id), step: 'before', points: c.recall.map((v, i) => [v, c.precision[i]]) } })}
            marks={binary.filter((r) => r.threshold).map((r) => ({ x: r.threshold!.holdout_at_threshold.recall, y: r.threshold!.holdout_at_threshold.precision, label: `t=${r.threshold!.value.toFixed(2)}`, color: color(r.id) }))}
            x={{ min: 0, max: 1, ticks: T01, title: 'R', fmt: (v) => v.toFixed(1) }} y={{ min: 0, max: 1, ticks: T01, title: 'P', fmt: (v) => v.toFixed(1) }} height={260} />
        </PlotFrame>
      </div>}

      {regs.length > 0 && <PlotFrame title="Residual Distributions" legend={regs.map((r) => ({ name: `${r.id} · RMSE ${fmtScore((ex[r.id] as Extract<Explain, { kind: 'regression' }>).rmse, 1)}`, color: color(r.id), data: [] }))}
        caption={<span>Histogram of rᵢ = yᵢ − ŷᵢ per run as a frequency polygon (share of rows per bin). Narrower and centred on 0 is better.</span>}>
        <XYPlot testId="compare-resid" series={regs.map((r): XYSeries => { const e = ex[r.id] as Extract<Explain, { kind: 'regression' }>; const n = e.histogram.counts.reduce((a, b) => a + b, 0); return { name: r.id, color: color(r.id), points: e.histogram.counts.map((c, i) => [(e.histogram.edges[i] + e.histogram.edges[i + 1]) / 2, c / n]) } })}
          x={(() => { const lo = Math.min(...regs.map((r) => (ex[r.id] as Extract<Explain, { kind: 'regression' }>).histogram.edges[0])); const hi = Math.max(...regs.map((r) => { const e = ex[r.id] as Extract<Explain, { kind: 'regression' }>; return e.histogram.edges[e.histogram.edges.length - 1] })); return { min: lo, max: hi, ticks: [lo, (lo + hi) / 2, 0, hi].sort((a, b) => a - b), title: 'r', fmt: (v: number) => fmtScore(v, 0) } })()}
          y={{ min: 0, max: 0.35, ticks: [0, 0.1, 0.2, 0.3], title: 'share', fmt: (v) => v.toFixed(1) }} height={240} />
      </PlotFrame>}

      <FamilyDots runs={done} color={color} />
      <div className="row" style={{ gap: 8 }}>{done.map((r) => <button key={r.id} className="chip-btn" onClick={() => go(`models?run=${r.id}&tab=Explain`)}><i className="run-dot" style={{ background: color(r.id) }} />Explain {r.id}</button>)}</div>
    </div>
  )
}

/** Best validation score per model family, one dot per run: which families each configuration made work. */
function FamilyDots({ runs, color }: { runs: Run[]; color: (id: string) => string }) {
  const [ref, W] = useWidth<HTMLDivElement>()
  const fams = [...new Set(runs.flatMap((r) => (r.leaderboard ?? []).map((x) => x.family ?? 'other')))]
  const best = (r: Run, f: string) => {
    const rows = (r.leaderboard ?? []).filter((x) => (x.family ?? 'other') === f && x.score_val_natural != null)
    if (!rows.length) return null
    const g = r.eval_metric?.greater_is_better ?? true
    return rows.reduce((b, x) => ((g ? x.score_val_natural! > b : x.score_val_natural! < b) ? x.score_val_natural! : b), rows[0].score_val_natural!)
  }
  const vals = runs.flatMap((r) => fams.map((f) => best(r, f))).filter((v): v is number => v != null)
  if (!vals.length) return null
  let lo = Math.min(...vals), hi = Math.max(...vals)
  const pad = (hi - lo) * 0.08 || 0.01; lo -= pad; hi += pad
  const LBL = 170, R = 20, ROW = 26
  const sx = (v: number) => LBL + ((v - lo) / (hi - lo)) * Math.max(100, W - LBL - R)
  const metric = runs[0].eval_metric?.name ?? ''
  return (
    <PlotFrame title="Best Validation Score per Model Family" legend={runs.map((r) => ({ name: r.id, color: color(r.id), data: [] }))}
      chips={<ParamChip sym="families" value={String(fams.length)} />}
      caption={<span>For each family (LightGBM, CatBoost, …) the best validation {metric} among its models in each run (natural units; ★ = family of the run's best model). Gaps show which configuration helped which learner.</span>}>
      <div ref={ref} data-testid="compare-families">
        <svg width={W} height={fams.length * ROW + 24}>
          {[lo, (lo + hi) / 2, hi].map((v) => <g key={v}><line x1={sx(v)} x2={sx(v)} y1={0} y2={fams.length * ROW} stroke="var(--plot-grid)" /><text className="tick-text" x={sx(v)} y={fams.length * ROW + 16} textAnchor="middle">{fmtScore(v, 3)}</text></g>)}
          {fams.map((f, i) => {
            const y = i * ROW + ROW / 2
            return (
              <g key={f}>
                <text x={LBL - 10} y={y + 4} textAnchor="end" className="fi-name">{FAMILY_SHORT[f] ?? f}</text>
                <line x1={LBL} x2={W - R} y1={y} y2={y} stroke="var(--border-row)" />
                {runs.map((r) => { const v = best(r, f); if (v == null) return null; const star = bestRow(r)?.family === f; return <g key={r.id}><circle cx={sx(v)} cy={y} r={star ? 6 : 4.5} fill={color(r.id)} stroke="var(--bg-surface)" strokeWidth={1.5}><title>{`${r.id} · ${FAMILY_SHORT[f] ?? f}: ${v.toFixed(4)}`}</title></circle>{star && <text x={sx(v)} y={y - 8} textAnchor="middle" className="tick-text" style={{ fill: color(r.id) }}>★</text>}</g> })}
              </g>
            )
          })}
        </svg>
      </div>
    </PlotFrame>
  )
}

import { useMemo, useState } from 'react'
import type { Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { StringPlot, type PCAxis } from '@/shared/components/StringPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { RUN_COLORS, bestRow, bestVal, mostCommon, presetsOf } from '../LiveExperiments'

const RUNG: Record<string, number> = { medium_quality: 1, good_quality: 2, high_quality: 3, best_quality: 4, extreme_quality: 5 }
const RUNG_NAME = ['other', 'medium', 'good', 'high', 'best', 'extreme']
const SHORT: Record<string, string> = { roc_auc: 'AUC', accuracy: 'acc', balanced_accuracy: 'bacc', mcc: 'MCC', f1: 'F₁', f1_macro: 'F₁ᵐᵃᶜʳᵒ', log_loss: 'log loss', precision: 'prec', recall: 'rec', root_mean_squared_error: 'RMSE', mean_absolute_error: 'MAE', r2: 'R²', median_absolute_error: 'MedAE', mean_squared_error: 'MSE', pearsonr: 'r' }
const short = (k: string) => SHORT[k] ?? k

type AxisSpec = { key: string; label: string; get: (r: Run) => number | null; fmt: (v: number) => string; what: string }

/** One axis per real parameter or metric, kept when every run in the group has a value. */
function axesFor(group: Run[]): AxisSpec[] {
  const metric = group[0]?.eval_metric?.name ?? ''
  const holdoutKeys = group.length ? Object.keys(group[0].holdout?.metrics ?? {}).filter((k) => group.every((r) => r.holdout?.metrics[k] != null)) : []
  const ordered = [metric, ...holdoutKeys.filter((k) => k !== metric)].filter((k) => holdoutKeys.includes(k)).slice(0, 4)
  const specs: AxisSpec[] = [
    { key: 'tau', label: 'τ budget', get: (r) => (r.config.time_limit as number | null) ?? null, fmt: (v) => `${Math.round(v)}s`, what: 'τ = time_limit given to fit() (s)' },
    { key: 'rung', label: 'presets', get: (r) => RUNG[presetsOf(r)] ?? 0, fmt: (v) => RUNG_NAME[Math.round(v)] ?? '', what: 'presets: medium < good < high < best' },
    { key: 'k', label: 'k folds', get: (r) => Math.max(0, ...(r.leaderboard ?? []).map((x) => x.fold_scores?.length ?? 0)), fmt: (v) => v.toFixed(0), what: 'k = bag folds (0: a single tuning split)' },
    { key: 'm', label: 'models', get: (r) => r.leaderboard?.length ?? null, fmt: (v) => v.toFixed(0), what: 'models trained (incl. ensembles)' },
    { key: 'fit', label: 'fit time', get: (r) => r.fit_seconds, fmt: (v) => `${v.toFixed(0)}s`, what: 'fit time (s)' },
    { key: 'lat', label: 'µs / row', get: (r) => { const b = bestRow(r); return b?.pred_time_test != null && r.holdout?.n_rows ? (b.pred_time_test / r.holdout.n_rows) * 1e6 : null }, fmt: (v) => v.toFixed(0), what: 'best model predict time per row (µs)' },
    { key: 't', label: 'threshold t', get: (r) => r.threshold?.value ?? null, fmt: (v) => v.toFixed(2), what: 't = decision threshold' },
    { key: 'val', label: `val ${short(metric)}`, get: (r) => bestVal(r), fmt: (v) => fmtScore(v, 3), what: `validation ${metric} of the best model` },
    ...ordered.map((k) => ({ key: `h_${k}`, label: `${short(k)}`, get: (r: Run) => r.holdout?.metrics[k] ?? null, fmt: (v: number) => fmtScore(v, 3), what: `holdout ${k}` })),
  ]
  // Keep axes every run has; drop constant ones (they say nothing) except the run's own metric.
  return specs.filter((s) => group.every((r) => s.get(r) != null))
    .filter((s) => s.key === `h_${metric}` || group.length < 2 || new Set(group.map((r) => s.get(r))).size > 1)
}

/** Parallel coordinates of the finished runs of one dataset and label: every run is one string across its real
 *  parameters and metrics. */
export function RunCoordinates({ runs, selected, onToggle }: { runs: Run[]; selected: string[]; onToggle: (id: string) => void }) {
  const done = runs.filter((r) => r.status === 'succeeded' && r.holdout)
  const groups = useMemo(() => {
    const m = new Map<string, Run[]>()
    done.forEach((r) => { const k = `${r.dataset_id}|${r.label}|${r.eval_metric?.name ?? ''}`; m.set(k, [...(m.get(k) ?? []), r]) })
    return m
  }, [done])
  const preferred = done.find((r) => selected.includes(r.id))
  const auto = preferred ? `${preferred.dataset_id}|${preferred.label}|${preferred.eval_metric?.name ?? ''}` : mostCommon(done.map((r) => `${r.dataset_id}|${r.label}|${r.eval_metric?.name ?? ''}`))
  const [pick, setPick] = useState<string | null>(null)
  const key = pick && groups.has(pick) ? pick : auto
  const group = groups.get(key) ?? []
  const specs = axesFor(group)
  if (!group.length) return <div className="card card-body helper">Parallel coordinates appear once runs have finished.</div>

  const axes: PCAxis[] = specs.map((s) => {
    const vals = group.map(s.get) as number[]
    let lo = Math.min(...vals), hi = Math.max(...vals)
    const nonneg = vals.every((v) => v >= 0)
    if (lo === hi) { const d = Math.abs(lo) * 0.1 || 1; lo -= d; hi += d } else { const d = (hi - lo) * 0.08; lo -= d; hi += d }
    if (nonneg) lo = Math.max(0, lo)
    if (s.key === 'k' || s.key === 'm') { lo = Math.max(0, Math.floor(lo)); hi = Math.ceil(hi) }
    if (s.key === 'rung') { lo = 1; hi = 4 }
    return { key: s.key, label: s.label, min: lo, max: hi, fmt: s.fmt }
  })
  const rows = group.map((r) => ({ id: r.id, v: Object.fromEntries(specs.map((s) => [s.key, s.get(r) as number])) }))
  const [ds, label, metric] = key.split('|')
  return (
    <PlotFrame title="Runs Across Parameters and Metrics"
      right={groups.size > 1 ? <select className="select" style={{ marginLeft: 8 }} value={key} onChange={(e) => setPick(e.target.value)} aria-label="Comparable runs" data-testid="pc-group">
        {[...groups.entries()].map(([k, rs]) => <option key={k} value={k}>{k.split('|')[0]} · {k.split('|')[1]} · {k.split('|')[2]} ({rs.length})</option>)}
      </select> : undefined}
      chips={<><ParamChip sym="runs" value={String(group.length)} /><ParamChip sym="axes" value={String(axes.length)} /></>}
      legend={[...group.filter((r) => selected.includes(r.id)).map((r) => ({ name: r.id, color: RUN_COLORS[selected.indexOf(r.id) % RUN_COLORS.length], data: [] })), { name: 'not selected', color: 'var(--border-track)', dash: '4 3', data: [] }]}
      caption={<span>{specs.length < 2 ? 'These runs differ in too few ways to plot. ' : ''}One string per finished run of <b className="mono">{ds}</b> → <b className="mono">{label}</b> scored on {metric}; only runs with the same dataset, label and metric are comparable. Axes (constant ones dropped): {specs.map((s) => `${s.label}: ${s.what}`).join('; ')}. Click a string to select its run.</span>}>
      <div data-testid="run-pc"><StringPlot axes={axes} rows={rows} selected={new Set(selected)} onPick={onToggle} colorOf={(id) => (selected.includes(id) ? RUN_COLORS[selected.indexOf(id) % RUN_COLORS.length] : undefined)} /></div>
    </PlotFrame>
  )
}

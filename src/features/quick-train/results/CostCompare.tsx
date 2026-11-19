import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import type { CostAnalysis, CostComparison, CostComparisonRow, Run, RunCost } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { useWidth } from '@/shared/hooks/useWidth'

const METRIC_NAME: Record<string, string> = { roc_auc: 'ROC AUC', accuracy: 'Accuracy', log_loss: 'Log loss', expected_cost: 'Business cost' }
const COLORS = ['var(--accent-primary)', 'var(--status-warning)', 'var(--text-muted)', 'var(--status-info, #6b7cff)']

const label = (r: CostComparisonRow) => (r.trained_on_cost ? 'business cost' : METRIC_NAME[r.eval_metric ?? ''] ?? r.eval_metric ?? '—')
const f1 = (v: number) => v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const sgn = (v: number) => `${v >= 0 ? '+' : '−'}${f1(Math.abs(v))}`
const ci = (c: [number, number]) => `[${f1(c[0])}, ${f1(c[1])}]`
const g = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(2))

/**
 * F7: the business cost of the run's decisions on the holdout, per 1,000 decisions with 95% bootstrap intervals,
 * beside the same fit trained to ROC AUC and to accuracy (all costed with this run's costs, on the same holdout rows),
 * the paired difference, and the cost curve over the threshold. Every number comes from GET /runs/{id}/cost and
 * /cost-comparison; a missing twin can be trained here (POST /runs/{id}/metric-twin: only the metric differs).
 */
export function CostCompare({ run, go }: { run: Run; go: (r: string) => void }) {
  const cost = run.config.cost as RunCost | undefined
  const [a, setA] = useState<CostAnalysis | null>(null)
  const [cmp, setCmp] = useState<CostComparison | null>(null)
  const [pending, setPending] = useState<Record<string, string>>({})
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!cost) return
    let alive = true
    api.runs.cost(run.id).then((x) => alive && setA(x)).catch((e) => alive && setErr((e as Error).message))
    api.runs.costComparison(run.id).then((x) => alive && setCmp(x)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, tick, cost])
  useEffect(() => {
    if (!Object.keys(pending).length) return
    const t = setInterval(async () => {
      const left: Record<string, string> = {}
      for (const [m, id] of Object.entries(pending)) {
        const r = await api.runs.get(id).catch(() => null)
        if (r && (r.status === 'queued' || r.status === 'running')) left[m] = id
        else if (r && r.status !== 'succeeded') setErr(`${m} run ${id} ${r.status}: ${r.error ?? ''}`)
      }
      setPending(left)
      setTick((x) => x + 1)
    }, 4000)
    return () => clearInterval(t)
  }, [pending])
  if (!cost) return null
  const twin = async (m: string) => {
    setErr('')
    try {
      const r = await api.runs.metricTwin(run.id, { eval_metric: m, calibrate_threshold: 'auto' })
      setPending((p) => ({ ...p, [m]: r.id }))
    } catch (e) { setErr((e as Error).message) }
  }
  const rows = cmp?.runs ?? []
  const me = rows.find((r) => r.this_run)
  const roc = rows.find((r) => !r.trained_on_cost && r.eval_metric === 'roc_auc')
  const acc = rows.find((r) => !r.trained_on_cost && r.eval_metric === 'accuracy')
  const ref = roc ?? acc
  const bin = cost.kind === 'binary'
  const dep = a?.deployed

  return (
    <div className="card" data-testid="cost-compare">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Business Cost · Per 1,000 Decisions</h3>
          <span className="helper" style={{ fontSize: 12 }}>{bin ? <>c<sub>FP</sub> = {g(cost.c_fp)}, c<sub>FN</sub> = {g(cost.c_fn)}{cost.g_tp ? <>, g<sub>TP</sub> = {g(cost.g_tp)}</> : null} · positive = {cost.positive_class}</> : <>{cost.classes.length} × {cost.classes.length} cost matrix</>} · holdout</span></div>
        {me && ref?.delta ? <StatusPill status={ref.delta.per_1000 > 0 ? 'success' : 'warning'}>{ref.delta.per_1000 > 0 ? `saves ${f1(ref.delta.per_1000)} per 1,000 vs ${label(ref)}` : `costs ${f1(-ref.delta.per_1000)} more than ${label(ref)}`}</StatusPill> : null}
      </div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {dep && (
          <div className="row" style={{ gap: 'var(--space-24)', alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div className="stack" style={{ gap: 2 }}>
              <span className="micro-label">expected cost, this run</span>
              <span className="mono" style={{ fontSize: 28, fontWeight: 600 }} data-testid="cost-per-1000">{f1(dep.per_1000)}</span>
              <span className="helper mono" style={{ fontSize: 12 }}>95% CI {ci(dep.ci_per_1000)} · B = {a!.B.toLocaleString()}</span>
            </div>
            {bin && a!.flag_none && a!.flag_all && <div className="stack" style={{ gap: 2 }}>
              <span className="micro-label">no model</span>
              <span className="mono" style={{ fontSize: 15 }}>flag nobody {f1(a!.flag_none.per_1000)} · flag everybody {f1(a!.flag_all.per_1000)}</span>
              <span className="helper" style={{ fontSize: 12 }}>the model saves {f1(Math.min(a!.flag_none.per_1000, a!.flag_all.per_1000) - dep.per_1000)} per 1,000 over the better of the two</span>
            </div>}
            {!bin && a!.best_constant && <div className="stack" style={{ gap: 2 }}>
              <span className="micro-label">no model</span>
              <span className="mono" style={{ fontSize: 15 }}>always “{a!.best_constant.class}” {f1(a!.best_constant.per_1000)}</span>
              <span className="helper" style={{ fontSize: 12 }}>Bayes decisions (argmin<sub>j</sub> Σ<sub>k</sub> p̂<sub>k</sub>C[k][j]) would cost {f1(a!.bayes.per_1000)}; the served predictor uses the arg-max</span>
            </div>}
          </div>
        )}
        {dep && bin && dep.fp != null && (
          <div className="formula" data-testid="cost-formula">
            <div className="eq">E[C] × 1000 = (c<sub>FP</sub>·FP + c<sub>FN</sub>·FN − g<sub>TP</sub>·TP) / n × 1000 = ({g(cost.c_fp)}·{dep.fp} + {g(cost.c_fn)}·{dep.fn} − {g(cost.g_tp)}·{dep.tp}) / {dep.n.toLocaleString()} × 1000 = {f1(dep.per_1000)}</div>
            <dl className="where">
              <dt>FP, FN, TP</dt><dd>holdout rows flagged wrongly, missed, caught, deciding positive when p̂ &gt; t = {dep.t!.toFixed(3)} (the threshold AutoGluon calibrated for this cost on its validation data)</dd>
              <dt>n</dt><dd>holdout rows, none seen in training</dd>
              <dt>t*</dt><dd>the Bayes threshold c<sub>FP</sub>/(c<sub>FP</sub> + c<sub>FN</sub> + g<sub>TP</sub>) = {a!.bayes_threshold!.toFixed(4)}: at it the cost is {f1(a!.bayes.per_1000)}; during the fit every model was ranked deciding this way</dd>
              <dt>CI</dt><dd>2.5th–97.5th percentile of E[C] over {a!.B.toLocaleString()} bootstrap resamples of the holdout rows</dd>
            </dl>
          </div>
        )}
        {bin && rows.length > 0 && <CostCurve rows={rows} a={a} />}
        {rows.length > 0 && (
          <div className="table-wrap"><table className="table" data-testid="cost-compare-table">
            <thead><tr><th>Trained to</th><th>Run</th>{bin && <th>t in use</th>}<th>E[C] per 1,000 · 95% CI</th><th>Δ vs this run</th>{bin && <th>at its cost t</th>}{bin && <th>Δ there</th>}</tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.run_id} className={r.this_run ? 'sel' : ''} data-testid={`cost-row-${r.trained_on_cost ? 'cost' : r.eval_metric}`}>
                <td><span className="chip" style={{ fontSize: 11 }}>{label(r)}</span></td>
                <td className="mono muted" style={{ fontSize: 12 }}>{r.this_run ? <>{r.run_id} ◂</> : <a href={`#/models?run=${r.run_id}`} onClick={(e) => { e.preventDefault(); go(`models?run=${r.run_id}`) }}>{r.run_id}</a>}</td>
                {bin && <td className="mono" style={{ fontSize: 12 }}>{r.threshold?.value.toFixed(3)}<span className="muted"> {r.threshold?.metric ? `(${r.threshold.metric === 'expected_cost' ? 'cost' : r.threshold.metric})` : r.threshold?.source === 'default' ? '(0.5)' : ''}</span></td>}
                <td className="mono"><b>{f1(r.deployed.per_1000)}</b> <span className="muted">{ci(r.deployed.ci_per_1000)}</span></td>
                <td className="mono">{r.delta ? <span title={`paired bootstrap, B = ${r.delta.B}`} style={{ color: r.delta.per_1000 > 0 ? 'var(--status-success)' : 'var(--status-warning)' }}>{sgn(r.delta.per_1000)} <span className="muted">{ci(r.delta.ci_per_1000)}</span></span> : '—'}</td>
                {bin && <td className="mono">{r.validation && !r.this_run ? <>{f1(r.validation.per_1000)} <span className="muted">t = {r.validation_threshold?.toFixed(3)}</span></> : r.this_run ? <span className="muted">same</span> : '—'}</td>}
                {bin && <td className="mono">{r.delta_validation ? <>{sgn(r.delta_validation.per_1000)} <span className="muted">{ci(r.delta_validation.ci_per_1000)}</span></> : '—'}</td>}
              </tr>
            ))}</tbody>
          </table></div>
        )}
        {me && ref?.delta && (
          <div className="formula" data-testid="cost-delta">
            <div className="eq">Δ = E[C]<sub>{label(ref)}</sub> − E[C]<sub>cost</sub> = {f1(ref.deployed.per_1000)} − {f1(me.deployed.per_1000)} = {sgn(ref.delta.per_1000)} per 1,000, 95% CI {ci(ref.delta.ci_per_1000)}</div>
            <dl className="where">
              <dt>E[C]<sub>{label(ref)}</sub></dt><dd>the same data, split ({ref.holdout_rows.toLocaleString()} holdout rows), models and budget trained to {label(ref)}{bin && ref.threshold ? `, deciding at its own t = ${ref.threshold.value.toFixed(3)}${ref.threshold.metric ? ` (calibrated for ${ref.threshold.metric}, as the wizard does)` : ''}` : ''}, costed with these costs</dd>
              <dt>E[C]<sub>cost</sub></dt><dd>this run: trained, ensembled and thresholded on the cost</dd>
              <dt>CI</dt><dd>paired bootstrap: both runs scored the same holdout rows, so each resample takes the same rows for both ({ref.delta.B.toLocaleString()} resamples; Δ &gt; 0 in {(100 * ref.delta.share_positive).toFixed(1)}% of them)</dd>
              {bin && ref.delta_validation && <><dt>at its cost t</dt><dd>if the {label(ref)} model's threshold is also calibrated for this cost on its validation data (t = {ref.validation_threshold?.toFixed(3)}), Δ = {sgn(ref.delta_validation.per_1000)} {ci(ref.delta_validation.ci_per_1000)}: {ref.delta_validation.ci_per_1000[0] > 0 ? 'the cost metric still wins, so model selection helps beyond the threshold' : 'most of the saving comes from choosing the threshold on cost; the difference left from model selection is within the noise'}</dd></>}
            </dl>
          </div>
        )}
        {cmp && cmp.metrics_missing.length > 0 && (
          <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-8)' }}>
            <span className="helper" style={{ fontSize: 12 }}>Compare: train this run again, same settings, optimising</span>
            {cmp.metrics_missing.map((m) => (
              <button key={m} className="btn sm" data-testid={`metric-twin-${m}`} disabled={!!pending[m]} onClick={() => twin(m)}>
                {pending[m] ? <>training {pending[m]}…</> : METRIC_NAME[m] ?? m}
              </button>
            ))}
          </div>
        )}
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      </div>
    </div>
  )
}

/** Expected cost per 1,000 against the threshold t for each run, with each run's t in use, t* and the no-model levels. */
function CostCurve({ rows, a }: { rows: CostComparisonRow[]; a: CostAnalysis | null }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const svg = useRef<SVGSVGElement>(null)
  const [hx, setHx] = useState<number | null>(null)
  const series = rows.map((r, i) => ({ r, color: COLORS[i % COLORS.length], name: `${label(r)}${r.this_run ? ' (this run)' : ''}` }))
  const none = a?.flag_none?.per_1000
  const ys = rows.flatMap((r) => r.curve.map((c) => c[1]))
  const yMax = Math.min(Math.max(...ys), (none ?? Math.max(...ys)) * 1.6)
  const yMin = Math.max(0, Math.min(...ys) * 0.9)
  const xMax = 0.6
  const H = 256, L = 52, R = 18, T = 26, B = 30
  const sx = (t: number) => L + (t / xMax) * (w - L - R)
  const sy = (v: number) => T + (1 - (Math.min(v, yMax) - yMin) / (yMax - yMin)) * (H - T - B)
  const paths = useMemo(() => series.map((s) => s.r.curve.filter((c) => c[0] <= xMax).map((c, i) => `${i ? 'L' : 'M'}${sx(c[0]).toFixed(1)},${sy(c[1]).toFixed(1)}`).join(' ')), [rows, w]) // eslint-disable-line react-hooks/exhaustive-deps
  const yTicks = niceTicks(yMin, yMax)
  const onMove = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect()
    const t = (((e.clientX - r.left) / r.width) * w - L) / (w - L - R) * xMax
    setHx(t < 0 || t > xMax ? null : Math.round(t / 0.005) * 0.005)
  }
  const at = (r: CostComparisonRow, t: number) => r.curve.reduce((b, c) => (Math.abs(c[0] - t) < Math.abs(b[0] - t) ? c : b), r.curve[0])
  return (
    <PlotFrame title="Cost Curve Over The Threshold" legend={series.map((s) => ({ name: s.name, color: s.color, data: [] }))}
      caption={<>E[C](t) × 1000 on the holdout, deciding positive when p̂ &gt; t; ● the t each run uses; dashed: t* = {a?.bayes_threshold?.toFixed(4)} and flagging nobody</>}>
      <div ref={ref} style={{ position: 'relative' }} data-testid="cost-curve">
        <svg ref={svg} width={w} height={H} onPointerMove={onMove} onPointerLeave={() => setHx(null)} role="img" aria-label="expected cost per 1,000 against the threshold">
          {yTicks.map((v) => <g key={v}><line x1={L} x2={w - R} y1={sy(v)} y2={sy(v)} stroke="var(--plot-grid)" /><text className="tick-text" x={L - 6} y={sy(v) + 3.5} textAnchor="end">{v.toLocaleString()}</text></g>)}
          {[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6].map((t) => <text key={t} className="tick-text" x={sx(t)} y={H - B + 16} textAnchor="middle">{t.toFixed(1)}</text>)}
          <line x1={L} x2={w - R} y1={H - B} y2={H - B} stroke="var(--plot-axis)" />
          <line x1={L} x2={L} y1={T} y2={H - B} stroke="var(--plot-axis)" />
          <text className="axis-title" x={w - R} y={H - 2} textAnchor="end">threshold t</text>
          <text className="axis-title" x={L - 44} y={12}>cost per 1,000</text>
          {a?.bayes_threshold != null && <g><line x1={sx(a.bayes_threshold)} x2={sx(a.bayes_threshold)} y1={T} y2={H - B} stroke="var(--text-muted)" strokeDasharray="4 4" /><text className="tick-text" x={sx(a.bayes_threshold) + 4} y={T + 10}>t*</text></g>}
          {none != null && none <= yMax && <g><line x1={L} x2={w - R} y1={sy(none)} y2={sy(none)} stroke="var(--text-muted)" strokeDasharray="2 4" /><text className="tick-text" x={w - R} y={sy(none) - 4} textAnchor="end">flag nobody {f1(none)}</text></g>}
          {series.map((s, k) => <path key={s.r.run_id} d={paths[k]} stroke={s.color} strokeWidth={s.r.this_run ? 2.5 : 1.5} fill="none" />)}
          {series.map((s) => s.r.threshold && <circle key={`m-${s.r.run_id}`} cx={sx(s.r.threshold.value)} cy={sy(s.r.deployed.per_1000)} r={4.5} fill={s.color} stroke="var(--surface-card, #fff)" />)}
          {hx != null && <line x1={sx(hx)} x2={sx(hx)} y1={T} y2={H - B} stroke="var(--plot-axis)" opacity={0.5} />}
          {hx != null && series.map((s) => <circle key={`h-${s.r.run_id}`} cx={sx(hx)} cy={sy(at(s.r, hx)[1])} r={3} fill="none" stroke={s.color} strokeWidth={1.5} />)}
        </svg>
        {hx != null && (
          <div className="plot-readout mono" style={{ position: 'absolute', left: Math.min(sx(hx) + 10, w - 220), top: 8, background: 'var(--surface-card, #fff)', border: '1px solid var(--border-default)', padding: '4px 8px', fontSize: 11.5, pointerEvents: 'none' }}>
            <div>t = {hx.toFixed(3)}</div>
            {series.map((s) => <div key={s.r.run_id} style={{ color: s.color }}>{label(s.r)}: {f1(at(s.r, hx)[1])}</div>)}
          </div>
        )}
      </div>
    </PlotFrame>
  )
}

function niceTicks(lo: number, hi: number): number[] {
  const span = hi - lo || 1
  const step = Math.pow(10, Math.floor(Math.log10(span / 4)))
  const m = [1, 2, 5, 10].find((k) => span / (k * step) <= 6) ?? 10
  const s = m * step
  const out: number[] = []
  for (let v = Math.ceil(lo / s) * s; v <= hi + 1e-9; v += s) out.push(Math.round(v * 1e6) / 1e6)
  return out
}

import type { ApStep, Autopilot } from '@/shared/api/types'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { fmtMetric, LAYER } from './LiveAutopilot'

const signed = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`

/** Everything Vela did, newest first, in its own words; each entry says which layer wrote it. */
export function LiveDecisionLog({ a }: { a: Autopilot }) {
  const running = a.status === 'running' || a.status === 'queued'
  const cur = a.steps.find((s) => s.id === a.current)
  return (
    <div className="card" data-testid="ap-log">
      <div className="card-head"><div className="left"><h3 className="card-title">Decision Log</h3><span className="helper" style={{ fontSize: 12 }}>newest first · each line says who decided</span></div><ParamChip sym="n" value={String(a.log.length)} /></div>
      <div className="log-cards">
        {running && cur && <div className="thinking" style={{ padding: '4px 4px 0' }}><ResonanceWave width={84} height={18} fast /> {cur.title}{cur.run_id ? ` · ${cur.run_id}` : ''}</div>}
        {[...a.log].reverse().map((e, i) => {
          const d = e.action ? a.decisions.find((x) => x.action === e.action && x.rationale === e.text) : undefined
          const step = e.step ? a.steps.find((s) => s.id === e.step) : undefined
          return (
            <div key={i} className={`patch ${step?.status === 'dropped' ? 'applied' : ''}`} data-testid="ap-log-entry" data-layer={e.layer}>
              <div className="ph">
                <VelaCrest size={16} />
                <b className="card-title" style={{ fontSize: 13 }}>{e.action ? `Next: ${e.action.replace(/_/g, ' ')}` : step ? step.title : 'Vela'}</b>
                <span className="spacer" />
                <span className={`chip layer ${e.layer === 'layer-2' ? 'l2' : ''}`}>{LAYER(e.layer, e.source, e.model)}</span>
                {step?.status === 'dropped' && <StatusPill status="neutral">Dropped</StatusPill>}
              </div>
              <p>{e.text}</p>
              {d && d.layer === 'layer-2' && d.layer1_would && d.layer1_would !== d.action && <div className="diffline">layer 1 would have chosen: {d.layer1_would.replace(/_/g, ' ')}</div>}
              {d?.doctor && <div className="diffline" data-testid="ap-doctor">Model Doctor on <span className="mono">{d.doctor.run_id}</span>: {d.doctor.primary ? <><b>{d.doctor.primary.title}</b> ({d.doctor.primary.severity}){d.doctor.secondary.length ? <>, then {d.doctor.secondary.map((x) => `${x.id.replace(/_/g, ' ')} (${x.severity})`).join(', ')}</> : null}</> : 'no limiting factor found'}</div>}
              {d && d.layer === 'layer-1' && d.layer2_error && <div className="diffline">layer 2 unavailable: {d.layer2_error.slice(0, 160)}</div>}
              {step?.change && <div className="diffline mono">{step.change}</div>}
              <span className="muted" style={{ fontSize: 10.5 }}>{e.t.slice(11, 19)} UTC{d?.seconds != null ? ` · decided in ${d.seconds.toFixed(1)} s` : ''}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** The requirements as inequalities bound to the best model's numbers, Δ against the baseline, and the budget. */
export function RequirementMath({ a, best }: { a: Autopilot; best?: ApStep }) {
  const base = a.steps.find((s) => s.kind === 'baseline')
  const prim = a.primary ?? a.brief.requirements[0]?.metric
  const checks = best?.checks ?? []
  const delta = best?.score != null && base?.score != null ? best.score - base.score : null
  const sym = checks.find((c) => c.metric === prim)?.sym ?? prim
  return (
    <div className="card" data-testid="ap-math">
      <div className="card-head"><div className="left"><h3 className="card-title">Requirements</h3><span className="helper" style={{ fontSize: 12 }}>on the {a.facts?.holdout_rows?.toLocaleString() ?? ''} held-out rows, current labels</span></div>
        {best && <span className="chip mono">{best.run_id}</span>}</div>
      <div className="card-body">
        <div className="formula compact">
          {checks.map((c) => (
            <div key={c.metric} className="eq" data-testid="ap-ineq"><i>{c.sym}</i>(<i>f</i>*) = <b className="mono">{fmtMetric(c.metric, c.observed)}</b> {c.op === '>=' ? '≥' : '≤'} {c.value}
              <span className={c.ok ? 'g-ok' : 'g-no'}> {c.ok ? '✓' : `✗ short by ${c.gap != null ? Math.abs(c.gap).toPrecision(3) : '—'}`}</span></div>
          ))}
          {delta != null && base && <div className="eq">Δ<i>{sym}</i> = <i>{sym}</i>(<i>f</i>*) − <i>{sym}</i>(<i>f</i>₀) = {best!.score!.toFixed(4)} − {base.score!.toFixed(4)} = <b className="mono">{signed(delta)}</b></div>}
          <div className="eq"><span className="up">Σ</span><sub><i>i</i></sub> <i>t<sub>i</sub></i> = {Math.round(a.budget.spent)} s ≤ <i>B</i> = {Math.round(a.budget.total)} s</div>
          <dl className="where">
            <dt>f*</dt><dd>the model Autopilot hands over: meets the most requirements, then the best {prim}{best ? <> (<span className="mono">{best.run_id}</span>, {best.title.toLowerCase()})</> : null}</dd>
            <dt>f₀</dt><dd>the fast baseline{base?.run_id ? <> (<span className="mono">{base.run_id}</span>)</> : null}, re-scored on the same rows and the corrected labels{base?.score_own != null && base.score != null && Math.abs(base.score_own - base.score) > 1e-9 ? <>: {base.score_own.toFixed(4)} on the original labels → {base.score.toFixed(4)}</> : null}</dd>
            {checks.some((c) => c.metric.includes('latency')) && <><dt>τ₉₅</dt><dd>95th percentile of one-row <span className="mono">predictor.predict</span> calls, ms per row ({best?.latency?.n ?? 40} calls after 3 warm-ups, served model {best?.latency?.model ?? '—'})</dd></>}
            {checks.some((c) => c.metric === 'f1_macro') && <><dt>F̄₁</dt><dd>(1/K) Σ<sub>k</sub> 2·P<sub>k</sub>·R<sub>k</sub> / (P<sub>k</sub> + R<sub>k</sub>) over the K = {Object.keys(a.facts?.class_shares ?? {}).length || '—'} classes</dd></>}
            <dt>t<sub>i</sub>, B</dt><dd>wall time of training step i; the brief's total budget</dd>
          </dl>
        </div>
      </div>
    </div>
  )
}

/** Every run Autopilot started, chained by parent_run_id. */
export function StepsTable({ a }: { a: Autopilot }) {
  const runs = a.steps.filter((s) => s.run_id)
  const base = runs.find((s) => s.kind === 'baseline')
  if (!runs.length) return null
  const prim = a.primary ?? ''
  const lat = a.brief.requirements.some((r) => r.metric.includes('latency'))
  return (
    <div className="card" data-testid="ap-steps">
      <div className="card-head"><div className="left"><h3 className="card-title">Runs</h3><span className="helper" style={{ fontSize: 12 }}>each a real POST /api/runs, parent → child</span></div></div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>#</th><th>Step</th><th>Run</th><th>Data</th><th>Preset</th><th className="r">τ (s)</th><th className="r">t (s)</th><th className="r">{prim} · own labels</th><th className="r">{prim} · current</th><th className="r">Δ vs f₀</th>{lat && <th className="r">τ₉₅ ms</th>}<th>Decided by</th><th /></tr></thead>
          <tbody>
            {runs.map((s) => {
              const b = s.body as { presets?: string; time_limit?: number } | undefined
              return (
                <tr key={s.id} className={s.status === 'dropped' ? 'muted' : ''}>
                  <td className="mono">{s.n}</td><td>{s.title}</td>
                  <td><a className="mono" href={`#/experiments?run=${s.run_id}`}>{s.run_id}</a></td>
                  <td className="mono">v{s.dataset_version ?? 1}</td><td className="mono">{b?.presets}</td>
                  <td className="r mono">{b?.time_limit?.toFixed(0)}</td><td className="r mono">{s.seconds?.toFixed(0)}</td>
                  <td className="r mono">{s.score_own?.toFixed(4) ?? '—'}</td><td className="r mono"><b>{s.score?.toFixed(4) ?? '—'}</b></td>
                  <td className="r mono">{s.score != null && base?.score != null && s !== base ? signed(s.score - base.score) : '—'}</td>
                  {lat && <td className="r mono">{s.latency?.p95_ms?.toFixed(1) ?? '—'}</td>}
                  <td><span className={`chip layer ${s.decision?.layer === 'layer-2' ? 'l2' : ''}`}>{s.decision ? LAYER(s.decision.layer, s.decision.source, s.decision.model) : 'layer 1 · fixed strategy'}</span></td>
                  <td>{s.status === 'dropped' ? <StatusPill status="neutral">Dropped</StatusPill> : s.met ? <StatusPill status="success">Meets All</StatusPill> : null}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

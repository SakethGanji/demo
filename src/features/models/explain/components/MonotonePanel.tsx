import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Explain, MonotoneCheck, Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, type XYSeries } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const niceTicks = (lo: number, hi: number, n = 5) => Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1))
const arrow = (s: number) => (s > 0 ? '↑' : '↓')

/** F8: ICE curves and partial dependence of one model on raw holdout rows, for the run's monotone constraints (or a
 *  feature and direction picked here, for any binary or regression run: the "before" of an unconstrained run). Every
 *  number is GET /api/runs/{id}/monotone; the per-model table is the worker's report after the fit. */
export function MonotonePanel({ run, ex }: { run: Run; ex: Explain }) {
  const rep = run.monotone ?? null
  const own = rep?.constraints ?? {}
  const numeric = ex.group_candidates.filter((c) => c.type === 'number').map((c) => c.column)
  const [col, setCol] = useState<string>(Object.keys(own)[0] ?? numeric[0] ?? '')
  const [sign, setSign] = useState<number>(own[col] ?? 1)
  const [model, setModel] = useState<string>(run.best_model ?? '')
  const [chk, setChk] = useState<MonotoneCheck | null>(null)
  const [err, setErr] = useState('')
  const constrained = col in own
  const s = constrained ? own[col] : sign

  useEffect(() => {
    if (!col) return
    let alive = true
    setErr(''); setChk(null)
    api.runs.monotone(run.id, { model, constraints: `${col}:${s > 0 ? 'increasing' : 'decreasing'}`, curves: 40 })
      .then((r) => alive && setChk(r)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, col, s, model])

  const c = chk?.columns[col]
  const { series, y } = useMemo(() => {
    if (!c?.grid || !c.pd) return { series: [] as XYSeries[], y: { min: 0, max: 1 } }
    const g = c.grid
    const ice = (c.ice ?? []).map((curve, i): XYSeries => {
      const bad = curve.some((v, k) => k > 0 && s * (v - curve[k - 1]) < -(chk?.eps ?? 1e-9))
      return { name: `ICE row ${c.ice_rows?.[i] ?? i}`, color: bad ? 'var(--status-danger)' : 'var(--viz-b)', opacity: bad ? 0.55 : 0.28, width: 1,
        points: g.map((x, k) => [x, curve[k]] as [number, number]) }
    })
    const pd: XYSeries = { name: 'PD', color: 'var(--viz-a)', width: 2.5, dots: true, points: g.map((x, k) => [x, c.pd![k]] as [number, number]),
      tip: (k) => `PD = ${c.pd![k].toFixed(4)}` }
    const all = [...(c.ice ?? []).flat(), ...c.pd]
    const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.06 || 0.01
    return { series: [...ice, pd], y: { min: Math.max(run.problem_type === 'binary' ? 0 : -Infinity, lo - pad), max: Math.min(run.problem_type === 'binary' ? 1 : Infinity, hi + pad) } }
  }, [c, s, chk?.eps, run.problem_type])

  const models = run.leaderboard?.map((r) => r.model) ?? []
  const fmtX = (v: number) => (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : String(Number(v.toPrecision(3))))
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="monotone-panel">
      <div className="card card-body row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap' }}>
        <span className="micro-label">feature x<sub>j</sub></span>
        <select className="select" aria-label="Feature" value={col} onChange={(e) => { setCol(e.target.value); setSign(own[e.target.value] ?? 1) }} data-testid="mono-col">
          {[...Object.keys(own), ...numeric.filter((x) => !(x in own))].map((x) => <option key={x} value={x}>{x}{x in own ? ` (${arrow(own[x])} constrained)` : ''}</option>)}
        </select>
        {!constrained && <div className="seg" role="radiogroup" aria-label="Direction to check">
          {[1, -1].map((v) => <button key={v} role="radio" aria-checked={sign === v} className={sign === v ? 'on' : ''} onClick={() => setSign(v)}>{v > 0 ? '↑ increasing' : '↓ decreasing'}</button>)}
        </div>}
        <span className="micro-label">model</span>
        <select className="select" aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)} data-testid="mono-model">
          {models.map((m) => <option key={m} value={m}>{m}{m === run.best_model ? ' (best)' : ''}</option>)}
        </select>
        {!chk && !err && <ResonanceWave />}
        {c && (c.monotone ? <StatusPill status="success">monotone on the grid</StatusPill> : <StatusPill status="danger">{c.curves_violating} of {c.n_curves} curves break it</StatusPill>)}
        {!rep && <span className="helper" style={{ fontSize: 12 }}>This run was trained without constraints: this checks the direction you pick. Set constraints in the Gym (Advanced → Models).</span>}
      </div>
      {err && <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>}

      {c && c.grid && <div className="grid mono-grid">
        <PlotFrame title={`ICE and Partial Dependence · ${col}`} legend={[{ name: 'PD(g)', color: 'var(--viz-a)', data: [] }, { name: 'ICE, monotone', color: 'var(--viz-b)', data: [] }, { name: 'ICE, violates', color: 'var(--status-danger)', data: [] }]}
          chips={<><ParamChip sym="n" value={String(c.n_curves)} /><ParamChip sym="G" value={String(c.grid.length)} /><ParamChip sym="sⱼ" value={s > 0 ? '+1' : '−1'} /></>}
          caption={<span>{chk?.score} of <b className="mono">{chk?.model}</b> as <b className="mono">{col}</b> moves over G = {c.grid.length} quantiles of the holdout, every other feature of the row held at its value. Thin lines: {c.ice?.length} of the n = {c.n_curves} ICE curves checked; bold: their mean, PD. A red curve steps against s<sub>j</sub> somewhere by more than ε.</span>}>
          <XYPlot testId="mono-plot" series={series} height={280}
            x={{ min: c.grid[0], max: c.grid[c.grid.length - 1], ticks: niceTicks(c.grid[0], c.grid[c.grid.length - 1]), title: col, fmt: fmtX }}
            y={{ min: y.min, max: y.max, ticks: niceTicks(y.min, y.max), title: chk?.score ?? 'f', fmt: (v) => v.toFixed(3) }} />
        </PlotFrame>
        <div className="formula" data-testid="mono-formula">
          <div className="micro-label">The check</div>
          <div className="eq">ICE<sub><i>i</i></sub>(<i>g</i>) = <i>f</i>(<i>x</i><sub><i>i</i>,−<i>j</i></sub>, <i>x<sub>j</sub></i> = <i>g</i>),  PD(<i>g</i>) = <span className="up">1</span>⁄<i>n</i> Σ<sub><i>i</i></sub> ICE<sub><i>i</i></sub>(<i>g</i>)</div>
          <div className="eq">violation:  <i>s<sub>j</sub></i> · (ICE<sub><i>i</i></sub>(<i>g</i><sub><i>k</i>+1</sub>) − ICE<sub><i>i</i></sub>(<i>g<sub>k</sub></i>)) &lt; −ε</div>
          <dl className="where">
            <dt>f</dt><dd>{chk?.score} from <span className="mono">{chk?.model}</span>, through AutoGluon's own feature pipeline on raw rows</dd>
            <dt>n</dt><dd><b className="mono">{c.n_curves}</b> holdout rows (seeded sample)</dd>
            <dt>g<sub>k</sub></dt><dd>the column's quantiles, {fmtX(c.grid[0])} … {fmtX(c.grid[c.grid.length - 1])} (G = {c.grid.length})</dd>
            <dt>s<sub>j</sub></dt><dd>{s > 0 ? '+1, increasing' : '−1, decreasing'}{constrained ? ' (the run’s constraint)' : ' (checked here; not trained with it)'}</dd>
            <dt>ε</dt><dd>10⁻⁹, float round-off</dd>
          </dl>
          <div className="mono-stats">
            <div><span className="micro-label">curves violating</span><b className="mono" data-testid="mono-viol">{c.curves_violating} / {c.n_curves}</b></div>
            <div><span className="micro-label">steps violating</span><b className="mono">{c.steps_violating} / {c.n_steps}</b></div>
            <div><span className="micro-label">largest step against sⱼ</span><b className="mono">{fmtScore(c.max_violation, 4)}</b></div>
            <div><span className="micro-label">PD monotone</span><b className="mono">{c.pd_monotone ? 'yes' : 'no'}</b></div>
            <div><span className="micro-label">PD range</span><b className="mono">{fmtScore(c.pd_range, 4)}</b></div>
          </div>
        </div>
      </div>}

      {rep?.models && <div className="card" data-testid="mono-models">
        <div className="card-head"><div className="left"><h3 className="card-title">Every Model, Checked After the Fit</h3><span className="helper" style={{ fontSize: 12 }}>where each constraint landed, and ICE on 100 holdout rows per model</span></div></div>
        <div className="card-body">
          {rep.columns && Object.entries(rep.columns).map(([k, x]) => <p key={k} className="helper" style={{ fontSize: 12.5 }}><b className="mono">{k}</b> {arrow(own[k])} → AutoGluon feature{x.features.length === 1 ? '' : 's'} <span className="mono">{x.features.join(', ') || '—'}</span>: {x.status}{x.models.length ? ` in ${x.models.length} model(s)` : ''}.</p>)}
          <div style={{ overflowX: 'auto' }}><table className="table compact"><thead><tr><th>model</th><th>level</th><th>family</th><th>signs its library received</th><th>by construction</th><th className="r">ICE ✕ / n</th></tr></thead>
            <tbody>{rep.models.map((m) => {
              const ch = m.check?.[col]
              const stackIn = Object.keys(m.applied ?? {}).filter((k) => !(k in own))
              return <tr key={m.model}>
                <td className="mono">{m.model}{m.model === run.best_model ? ' ★' : ''}</td><td className="mono">L{m.stack_level}</td><td>{m.family ?? '—'}</td>
                <td className="mono" style={{ fontSize: 12 }}>{m.kind === 'weighted_ensemble' ? `Σ wₘ fₘ, wₘ ≥ 0 over ${Object.keys(m.weights ?? {}).length} models`
                  : m.constrained ? [...Object.entries(m.applied ?? {}).filter(([k]) => k in own).map(([k, v]) => `${k} ${v > 0 ? '+1' : '−1'}`), ...(stackIn.length ? [`${stackIn.length} stack input${stackIn.length > 1 ? 's' : ''} +1`] : [])].join(' · ')
                    : 'none (cannot honour it)'}</td>
                <td>{m.monotone_by_construction ? <span style={{ color: 'var(--status-success)' }}>● yes</span> : <span style={{ color: 'var(--status-danger)' }}>✕ no</span>}</td>
                <td className="r mono">{ch ? `${ch.curves_violating} / ${ch.n_curves}` : m.check_error ? 'error' : '—'}</td>
              </tr>
            })}</tbody></table></div>
          <p className="helper" style={{ fontSize: 12 }}>By construction: a constrained tree model whose stack inputs all come from models that are monotone by construction, or a weighted ensemble (wₘ ≥ 0) of such models. ICE ✕ / n: curves of {col} that step against sⱼ, of n checked.</p>
          {rep.unconstrained_models?.length ? <p className="helper" style={{ fontSize: 12, color: 'var(--status-warning)' }}>▲ Unconstrained models in this run: {rep.unconstrained_models.join(', ')}. Serve a model marked “yes”.</p> : null}
        </div>
      </div>}
    </div>
  )
}

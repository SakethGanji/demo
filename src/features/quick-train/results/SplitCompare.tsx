import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run, SplitComparison, SplitOptions, SplitStrategy } from '@/shared/api/types'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { useWidth } from '@/shared/hooks/useWidth'
import { STRATEGY_NAME, specFor } from '../components/SplitCard'
import { toData, withInterval } from './stats'

type CI = { value: number; lo: number; hi: number; B: number }
const ORDER: SplitStrategy[] = ['random-stratified', 'group', 'time', 'time-group']

/**
 * F6: the run's holdout score beside the same data, label and fit settings scored under the other splits, so the
 * optimism of a random split is visible. Each score carries its 95% bootstrap interval (recomputed from that run's
 * holdout predictions); Δ = score(random) − score(split) in the metric's direction. A missing split can be trained
 * here: POST /runs/{id}/split-twin re-runs the same body with only split_spec changed.
 */
export function SplitCompare({ run, go }: { run: Run; go: (r: string) => void }) {
  const [cmp, setCmp] = useState<SplitComparison | null>(null)
  const [opts, setOpts] = useState<SplitOptions | null>(null)
  const [ci, setCi] = useState<Record<string, CI | null>>({})
  const [pending, setPending] = useState<Record<string, string>>({})
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    api.runs.splitComparison(run.id).then((c) => { if (alive) setCmp(c) }).catch((e) => { if (alive) setErr((e as Error).message) })
    return () => { alive = false }
  }, [run.id, tick])
  useEffect(() => {
    let alive = true
    api.datasets.splitOptions(run.dataset_id, run.label).then((o) => { if (alive) setOpts(o) }).catch(() => {})
    return () => { alive = false }
  }, [run.dataset_id, run.label])
  // poll while a twin is training
  useEffect(() => {
    if (!Object.keys(pending).length) return
    const t = setInterval(async () => {
      const left: Record<string, string> = {}
      for (const [s, id] of Object.entries(pending)) {
        const r = await api.runs.get(id).catch(() => null)
        if (r && (r.status === 'queued' || r.status === 'running')) left[s] = id
        else if (r && r.status !== 'succeeded') setErr(`${STRATEGY_NAME[s as SplitStrategy]} run ${id} ${r.status}: ${r.error ?? ''}`)
      }
      setPending(left)
      setTick((x) => x + 1)
    }, 4000)
    return () => clearInterval(t)
  }, [pending])
  // bootstrap intervals from each run's holdout predictions
  useEffect(() => {
    if (!cmp?.metric) return
    let alive = true
    for (const r of cmp.runs) {
      if (r.run_id in ci) continue
      Promise.all([api.runs.holdoutPredictions(r.run_id), r.run_id === run.id ? Promise.resolve(run) : api.runs.get(r.run_id)])
        .then(([h, rr]) => { if (alive) setCi((m) => ({ ...m, [r.run_id]: withInterval(cmp.metric!, toData(h, rr.positive_class), 200) })) })
        .catch(() => { if (alive) setCi((m) => ({ ...m, [r.run_id]: null })) })
    }
    return () => { alive = false }
  }, [cmp]) // eslint-disable-line react-hooks/exhaustive-deps

  const twin = async (s: SplitStrategy) => {
    setErr('')
    try {
      const spec = specFor(s, opts) ?? { strategy: 'random-stratified' as const }
      const r = await api.runs.splitTwin(run.id, spec)
      setPending((p) => ({ ...p, [s]: r.id }))
    } catch (e) { setErr((e as Error).message) }
  }

  const rows = (cmp?.runs ?? []).filter((r) => r.same_config || r.this_run).sort((a, b) => ORDER.indexOf(a.strategy) - ORDER.indexOf(b.strategy))
  const can = (s: SplitStrategy) => s === 'random-stratified' || (!!opts && (s === 'group' ? opts.group_columns.length > 0 : s === 'time' ? opts.date_columns.length > 0 : opts.group_columns.length > 0 && opts.date_columns.length > 0))
  const missing = ORDER.filter((s) => can(s) && !rows.some((r) => r.strategy === s))
  const rnd = rows.find((r) => r.strategy === 'random-stratified')
  const oot = rows.find((r) => r.strategy === 'time')
  const gib = cmp?.greater_is_better ?? true
  const metric = cmp?.metric ?? '—'
  const randomOnDated = !!opts && opts.warnings.length > 0 && (run.split?.strategy ?? 'random-stratified') === 'random-stratified'
  if (!cmp && !err) return null
  if (rows.length <= 1 && !missing.some((s) => s !== 'random-stratified') && !randomOnDated) return null
  return (
    <div className="card" data-testid="split-compare">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Random vs Out-Of-Time</h3><span className="helper" style={{ fontSize: 12 }}>the same fit, scored under each split · {metric}</span></div>
        {oot && rnd && oot.optimism != null ? <StatusPill status={oot.optimism > 0 ? 'warning' : 'success'}>{oot.optimism > 0 ? `random optimistic by ${oot.optimism.toFixed(4)}` : 'no optimism'}</StatusPill>
          : randomOnDated ? <StatusPill status="warning">Random split on dated / grouped data</StatusPill> : null}
      </div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {randomOnDated && <p className="helper" data-testid="split-compare-warning" style={{ fontSize: 12.5 }}><span style={{ color: 'var(--status-warning)' }}>▲</span> {opts!.warnings.join(' ')}</p>}
        {rows.length > 0 && <ScoreDots rows={rows} ci={ci} gib={gib} />}
        {rnd && oot && oot.optimism != null && (
          <div className="formula" data-testid="split-optimism">
            <div className="eq">Δ = {gib ? <>s<sub>random</sub> − s<sub>time</sub></> : <>s<sub>time</sub> − s<sub>random</sub></>} = {fmt(gib ? rnd.score : oot.score)} − {fmt(gib ? oot.score : rnd.score)} = {oot.optimism >= 0 ? '' : '−'}{fmt(Math.abs(oot.optimism))}</div>
            <dl className="where">
              <dt>s<sub>random</sub></dt><dd>{metric} on {rnd.holdout_rows?.toLocaleString()} holdout rows drawn at random from every period ({rnd.run_id})</dd>
              <dt>s<sub>time</sub></dt><dd>{metric} on the {oot.holdout_rows?.toLocaleString()} rows dated on or after τ = {oot.cutoff}, by a model fitted only on rows before it ({oot.run_id})</dd>
              <dt>Δ</dt><dd>how much better the random split says the model is than it does on the future ({gib ? 'higher' : 'lower'} {metric} is better). The intervals are percentile bootstraps of each holdout, B = {ci[oot.run_id]?.B ?? 200}; the two holdouts are different rows, so they are not paired.</dd>
            </dl>
          </div>
        )}
        <div className="table-wrap"><table className="table" data-testid="split-compare-table">
          <thead><tr><th>Split</th><th>Run</th><th>|H|</th><th>|F| + |T|</th><th>{metric}</th><th>95% CI</th><th>Δ</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.run_id} className={r.this_run ? 'sel' : ''} data-testid={`split-compare-${r.strategy}`}>
                <td><span className="chip" style={{ fontSize: 11 }}>{STRATEGY_NAME[r.strategy]}</span></td>
                <td className="mono muted" style={{ fontSize: 12 }}>{r.this_run ? <>{r.run_id} ◂</> : <a href={`#/models?run=${r.run_id}`} onClick={(e) => { e.preventDefault(); go(`models?run=${r.run_id}`) }}>{r.run_id}</a>}</td>
                <td className="table-cell">{r.holdout_rows?.toLocaleString()}{r.cutoff ? <span className="muted"> · ≥ {r.cutoff}</span> : ''}</td>
                <td className="table-cell">{r.train_rows?.toLocaleString()}{r.tuning_rows ? <span className="muted"> + {r.tuning_rows.toLocaleString()} T</span> : ''}</td>
                <td className="table-cell mono"><b>{fmt(r.score)}</b></td>
                <td className="table-cell mono muted">{ci[r.run_id] ? `[${fmt(ci[r.run_id]!.lo)}, ${fmt(ci[r.run_id]!.hi)}]` : r.run_id in ci ? '—' : '…'}</td>
                <td className="table-cell mono">{r.optimism == null ? '—' : <span style={{ color: r.optimism > 0 ? 'var(--status-warning)' : 'var(--status-success)' }}>{r.optimism > 0 ? '+' : ''}{fmt(r.optimism)}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
        {missing.length > 0 && (
          <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-8)' }}>
            <span className="helper" style={{ fontSize: 12 }}>Train this run again, same settings, under:</span>
            {missing.map((s) => (
              <button key={s} className="btn sm" data-testid={`split-twin-${s}`} disabled={!!pending[s]} onClick={() => twin(s)}>
                {pending[s] ? <>training {pending[s]}…</> : <>{STRATEGY_NAME[s]} split</>}
              </button>
            ))}
          </div>
        )}
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      </div>
    </div>
  )
}

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : v.toFixed(4))

/** One row per split: the score as a dot on a shared axis, with its bootstrap interval as a line. */
function ScoreDots({ rows, ci, gib }: { rows: SplitComparison['runs']; ci: Record<string, CI | null>; gib: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const vals = rows.flatMap((r) => [r.score, ci[r.run_id]?.lo ?? r.score, ci[r.run_id]?.hi ?? r.score])
  let lo = Math.min(...vals), hi = Math.max(...vals)
  const pad = (hi - lo) * 0.08 || 0.01
  lo -= pad; hi += pad
  const L = 150, R = 16, rowH = 26
  const x = (v: number) => L + ((v - lo) / (hi - lo)) * Math.max(10, w - L - R)
  const H = rows.length * rowH + 34
  const rnd = rows.find((r) => r.strategy === 'random-stratified')
  return (
    <div ref={ref} data-testid="split-dots">
      <svg width={w} height={H} role="img" aria-label="holdout score per split with 95% intervals">
        {rnd && <line x1={x(rnd.score)} x2={x(rnd.score)} y1={4} y2={H - 18} stroke="var(--border-track)" strokeDasharray="3 3" />}
        {rows.map((r, i) => {
          const y = 24 + i * rowH
          const c = ci[r.run_id]
          const col = r.strategy === 'random-stratified' ? 'var(--text-muted)' : r.strategy === 'time' ? 'var(--status-warning)' : 'var(--accent-primary)'
          return (
            <g key={r.run_id}>
              <text x={0} y={y + 4} className="tick-text" style={{ fontWeight: r.this_run ? 600 : 400 }}>{STRATEGY_NAME[r.strategy]}</text>
              {c && <line x1={x(c.lo)} x2={x(c.hi)} y1={y} y2={y} stroke={col} strokeWidth={2} opacity={0.5} />}
              <circle cx={x(r.score)} cy={y} r={5} fill={col} />
              <text x={x(r.score)} y={y - 8} textAnchor="middle" className="tick-text">{fmt(r.score)}</text>
            </g>
          )
        })}
        <text x={L} y={H - 4} className="tick-text">{fmt(lo)}</text>
        <text x={w - R} y={H - 4} textAnchor="end" className="tick-text">{fmt(hi)} {gib ? '→ better' : '← better'}</text>
      </svg>
    </div>
  )
}

import { api } from '@/shared/api/client'
import type { Run } from '@/shared/api/types'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'

const pc = (v: number) => `${(100 * v).toFixed(Math.abs(v) < 0.1 ? 1 : 0)}%`
const short = (v: unknown) => { const s = v == null ? 'missing' : typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : v.toPrecision(3)) : String(v); return s.length > 24 ? `${s.slice(0, 22)}…` : s }

/** D3: where the model went wrong on the holdout: the most confident errors with their reasons, errors by slice, the
 *  missingness inside the errors, the overlap with cleanlab's label issues and errors over time. All from the Doctor. */
export function HoldoutErrorsPanel({ run, onRow }: { run: Run; onRow: (row: number) => void }) {
  const f = useFetch(() => api.runs.doctor(run.id), [run.id])
  if (f.err) return <ErrorCard inline err={`Holdout errors: ${f.err}`} />
  if (!f.data) return <Loading inline>Reading the holdout errors…</Loading>
  const e = f.data.errors_view
  if (!e?.served) return <div className="card card-body"><p className="helper">No holdout predictions to read for this run.</p></div>
  const cl = e.top?.cleanlab
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="holdout-errors">
      <div className="card navy explain-hero">
        <div className="eh-cell lead"><span className="micro-label">holdout errors</span><b className="mono" style={{ fontSize: 22 }}>{e.served.errors.toLocaleString()}</b><small>of {e.served.n.toLocaleString()} rows ({pc(e.served.rate)}): {e.served.definition}</small></div>
        {cl && <div className="eh-cell"><span className="micro-label">cleanlab label issues</span><b className="mono">{pc(cl.share_of_errors)}</b><small>of the errors vs {pc(cl.share_of_correct)} of the correct rows</small></div>}
        <div className="eh-cell"><span className="micro-label">concentrated slices</span><b className="mono">{e.slices?.length ?? 0}</b><small>lift ≥ 1.25, BH q ≤ 0.05</small></div>
      </div>

      <div className="card" data-testid="errors-confident">
        <div className="card-head"><div className="left"><h3 className="card-title">Most Confident Errors</h3><span className="helper" style={{ fontSize: 12 }}>reasons: {e.top?.reasons_from}</span></div></div>
        <div className="card-body" style={{ overflowX: 'auto' }}>
          <table className="table compact"><thead><tr><th className="r">row</th><th>truth</th><th>predicted</th><th className="r">confidence</th><th>reasons for the wrong call</th><th>label issue</th><th /></tr></thead>
            <tbody>{(e.top?.rows ?? []).slice(0, 15).map((r) => <tr key={r.row}>
              <td className="r mono">{r.row}</td><td>{r.y_true}</td><td>{r.y_pred}</td><td className="r mono">{r.confidence.toFixed(3)}</td>
              <td><div className="err-reasons">{(r.reasons ?? []).map((x) => <span key={x.feature} className="chip" title={`φ = ${x.phi.toFixed(3)} (log-odds)`}>{x.feature} = {short(x.value)}</span>)}</div></td>
              <td>{r.label_issue == null ? '—' : r.label_issue ? <span className="pill warning">flagged</span> : <span className="muted">no</span>}</td>
              <td><button className="btn sm ghost" onClick={() => onRow(r.row)}>SHAP →</button></td></tr>)}</tbody></table>
        </div>
      </div>

      <div className="card" data-testid="errors-slices">
        <div className="card-head"><div className="left"><h3 className="card-title">Errors By Slice</h3><span className="helper" style={{ fontSize: 12 }}>{e.slice_rule}</span></div></div>
        <div className="card-body" style={{ overflowX: 'auto' }}>
          {e.slices?.length ? <table className="table compact"><thead><tr><th>slice</th><th className="r">rows</th><th className="r">error rate</th><th className="r">lift</th><th className="r">excess errors</th><th className="r">q</th><th>missing inside its errors</th></tr></thead>
            <tbody>{e.slices.map((s) => <tr key={s.label}><td><b>{s.label}</b></td><td className="r mono">{s.n.toLocaleString()}</td><td className="r mono">{s.rate.toFixed(3)}</td><td className="r mono">{s.lift.toFixed(2)}×</td><td className="r mono">{s.excess.toFixed(0)}</td><td className="r mono">{s.q.toExponential(1)}</td>
              <td className="helper" style={{ fontSize: 12 }}>{(s.missing_inside ?? []).map((m) => `${m.column} ${pc(m.missing_in_errors)} vs ${pc(m.missing_elsewhere)}`).join(' · ') || '—'}</td></tr>)}</tbody></table>
            : <p className="helper">No slice where errors concentrate (overall error rate {e.overall_rate?.toFixed(3) ?? '—'}).</p>}
          {!!e.missing_in_errors?.length && <p className="helper" style={{ fontSize: 12, marginTop: 8 }}><span className="micro-label">missing where it goes wrong</span> {e.missing_in_errors.map((m) => `${m.column}: ${pc(m.missing_in_errors)} of the errors vs ${pc(m.missing_elsewhere)} of the rest`).join(' · ')}</p>}
        </div>
      </div>

      {e.by_time && <div className="card" data-testid="errors-time">
        <div className="card-head"><div className="left"><h3 className="card-title">Errors Over Time</h3><span className="helper" style={{ fontSize: 12 }}>{e.by_time.column}, five equal bins of holdout rows{e.by_time.p != null && ` · χ² p = ${e.by_time.p.toExponential(1)}`}</span></div></div>
        <div className="card-body"><table className="table compact"><thead><tr><th>from</th><th>to</th><th className="r">rows</th><th className="r">error rate</th></tr></thead>
          <tbody>{e.by_time.bins.map((b) => <tr key={b.from}><td className="mono">{b.from}</td><td className="mono">{b.to}</td><td className="r mono">{b.n}</td><td className="r mono">{b.rate.toFixed(3)}</td></tr>)}</tbody></table></div>
      </div>}
    </div>
  )
}

import { api } from '@/shared/api/client'
import type { Ladder, LadderRung, Run } from '@/shared/api/types'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { fix } from '@/shared/lib/format'
import { bytes } from '@/features/registry/lib'

const ms = (v: number | null | undefined) => (v == null ? '—' : v === 0 ? '0' : v < 1 ? `${v.toFixed(2)} ms` : `${v < 10 ? v.toFixed(1) : v.toFixed(0)} ms`)
const signed = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(3)}`

/**
 * D2 · Model ladder: every rung (baseline → scorecard → single GBM → AutoGluon's best) on the same holdout, its gap to
 * the best with a paired-bootstrap CI, one-row latency and size, and the rule's pick. One primary action serves it.
 */
export function LadderCard({ run, go }: { run: Run; go: (r: string) => void }) {
  const ok = run.problem_type === 'binary' || run.problem_type === 'multiclass' || run.problem_type === 'regression'
  const f = useFetch(ok && !run.config.kind && !run.config.import ? () => api.runs.ladder(run.id) : null, [run.id])
  if (!ok || run.config.kind || run.config.import) return null
  if (f.err) return <ErrorCard inline err={`Model ladder: ${f.err}`} />
  if (!f.data) return <Loading inline>Model ladder: scoring every rung on the holdout…</Loading>
  return <LadderView l={f.data} run={run} go={go} />
}

function LadderView({ l, run, go }: { l: Ladder; run: Run; go: (r: string) => void }) {
  const rec = l.recommended
  const lvl = `${Math.round(l.level * 100)} %`
  const serve = () => go(`registry?register=${run.id}${rec?.serve_model ? `&serve=${encodeURIComponent(rec.serve_model)}` : ''}`)
  return (
    <div className="card ladder" data-testid="ladder" data-recommended={rec?.rung ?? ''}>
      <div className="card-head">
        <div className="left"><h3 className="card-title">Model ladder</h3><span className="helper">powerful, then simple · holdout {l.metric_label} · {l.n_holdout.toLocaleString()} rows</span></div>
        {rec && <button className="btn primary" onClick={serve} data-testid="ladder-serve">Serve {rec.name} →</button>}
      </div>
      {l.sentence && <p className="ladder-say" data-testid="ladder-sentence">{l.sentence}</p>}
      {l.small_data_note && <p className="ladder-note" data-testid="ladder-small">{l.small_data_note}</p>}
      {l.floor_warning && <p className="ladder-note warn">{l.floor_warning}</p>}
      <table className="table ladder-table">
        <thead><tr><th>Rung</th><th>Model</th><th className="num">{l.metric_label}</th><th className="num">Gap to best · {lvl} CI</th><th className="num">One row (p95)</th><th className="num">Size</th><th /></tr></thead>
        <tbody>
          {l.rungs.map((r) => <Row key={r.rung} r={r} l={l} rec={rec?.rung === r.rung} />)}
        </tbody>
      </table>
      <p className="helper ladder-rule" data-testid="ladder-rule">Rule: {l.rule} Latency is the measured one-row p95 as served (pipeline pruned); size is the model and every model it needs on disk.</p>
      {l.distill.available && <div className="row ladder-distill" data-testid="ladder-distill">
        <span className="helper">{l.distill.text}</span>
        <button className="btn sm" onClick={() => go(`registry?register=${run.id}&distill=1`)}>Distill…</button>
      </div>}
    </div>
  )
}

function Row({ r, l, rec }: { r: LadderRung; l: Ladder; rec: boolean }) {
  if (r.same_as != null) {
    return <tr className="muted"><td className="mono">{r.rung}</td><td>{r.name} <span className="helper">= rung {r.same_as}: AutoGluon's best is itself a single model</span></td><td colSpan={5} /></tr>
  }
  if (!r.model && r.rung > 0) {
    return <tr className="muted"><td className="mono">{r.rung}</td><td>{r.name}</td><td colSpan={5} className="helper">{r.missing}</td></tr>
  }
  const best = r.rung === l.best_rung
  return (
    <tr className={rec ? 'rec' : ''} data-testid={`ladder-rung-${r.rung}`} data-within={r.within ? '1' : '0'}>
      <td className="mono">{r.rung}</td>
      <td><b>{r.name}</b>{r.model && <span className="mono helper"> {r.model}</span>}<div className="helper">{r.why}</div></td>
      <td className="num mono">{fix(r.score, 3)}</td>
      <td className="num mono">{best ? 'best' : r.gap ? <>{signed(-r.gap.delta)} <span className="helper">[{signed(-r.gap.ci[1])}, {signed(-r.gap.ci[0])}]</span></> : '—'}</td>
      <td className="num mono">{ms(r.latency_ms_p95)}</td>
      <td className="num mono">{r.rung === 0 ? '—' : bytes(r.size_bytes)}</td>
      <td>{rec ? <span className="chip ok">serve</span> : r.within ? <span className="chip">within CI</span> : null}</td>
    </tr>
  )
}

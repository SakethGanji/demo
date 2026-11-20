import { useEffect, useState } from 'react'
import { catalog, type Compare } from '@/shared/api/catalog'
import type { Run } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/metrics'
import { RUN_COLORS } from '../RunList'
import './project-tree.css'

/** P1: Δ against the first selected run from POST /api/compare. Paired (same original holdout rows, through a dataset
 *  version's root rows) → Δ with a bootstrap CI; otherwise the API's "different holdout, not paired" label and why. */
export function PairedDelta({ runs }: { runs: Run[] }) {
  const [res, setRes] = useState<Record<string, Compare | string>>({})
  const base = runs[0]
  const others = runs.slice(1, 6)
  const key = runs.map((r) => r.id).join()
  useEffect(() => {
    let alive = true
    setRes({})
    others.forEach((r) => catalog.compare(base.id, r.id).then((c) => alive && setRes((m) => ({ ...m, [r.id]: c }))).catch((e) => alive && setRes((m) => ({ ...m, [r.id]: (e as Error).message }))))
    return () => { alive = false }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!base || !others.length) return null
  const sign = (v: number) => `${v >= 0 ? '+' : '−'}${fmtScore(Math.abs(v), 4)}`
  return (
    <div className="card paired-card" data-testid="paired-delta">
      <div className="card-head"><div className="left"><h3 className="card-title">Δ Against {base.id}</h3>
        <span className="helper" style={{ fontSize: 12 }}>POST /api/compare · the first run you selected is the base · scored on the run's own eval metric (AUC alongside for binary) · paired on the same original holdout rows when the runs share a dataset root; otherwise labelled unpaired</span></div></div>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>run</th><th>pairing</th><th>metric</th><th className="r">a · b</th><th className="r">Δ = b − a</th><th className="r">95 % CI</th><th className="r">rows</th><th>reading</th></tr></thead>
        <tbody>{others.map((r, i) => {
          const c = res[r.id]
          const dot = <i className="run-dot" style={{ background: RUN_COLORS[(i + 1) % RUN_COLORS.length] }} />
          if (c == null) return <tr key={r.id}><td className="table-cell">{dot}{r.id}</td><td colSpan={7} className="muted">comparing…</td></tr>
          if (typeof c === 'string') return <tr key={r.id}><td className="table-cell">{dot}{r.id}</td><td colSpan={7} style={{ color: 'var(--status-danger)' }}>{c}</td></tr>
          if (!c.paired) return (
            <tr key={r.id} data-testid={`paired-${r.id}`} data-paired="false"><td className="table-cell">{dot}{r.id}</td>
              <td><span className="pill warning">different holdout, not paired</span></td><td className="mono">{c.a.eval_metric}</td>
              <td className="r table-cell">{fmtScore(c.a.holdout_score, 4)} · {fmtScore(c.b.holdout_score, 4)}</td>
              <td className="r table-cell muted" title={c.difference_note}>{c.difference != null ? sign(c.difference) : '—'}</td><td className="r muted">none</td>
              <td className="r table-cell muted">{c.a.holdout_rows ?? '—'} / {c.b.holdout_rows ?? '—'}</td><td className="helper" style={{ fontSize: 12, whiteSpace: 'normal', minWidth: 220 }}>{c.reason}</td></tr>)
          return (
            <tr key={r.id} data-testid={`paired-${r.id}`} data-paired="true"><td className="table-cell">{dot}{r.id}</td>
              <td><span className="pill success">paired</span>{c.warning && <span title={c.warning}> ▲</span>}{c.partial && <div className="helper" style={{ fontSize: 11.5 }} data-testid={`overlap-${r.id}`}>{c.overlap}</div>}</td>
              <td className="mono" title={c.metric_note ?? c.B_note}>{c.metric}{c.metric_note ? ' ▲' : ''}{c.auc && <div className="helper" style={{ fontSize: 11.5 }}>AUC {fmtScore(c.auc.a, 4)} · {fmtScore(c.auc.b, 4)} ({sign(c.auc.delta)}, no CI)</div>}</td>
              <td className="r table-cell">{fmtScore(c.score_a, 4)} · {fmtScore(c.score_b, 4)}</td>
              <td className="r table-cell"><b>{sign(c.delta!)}</b></td>
              <td className="r ci">{c.ci && c.ci[0] != null && c.ci[1] != null ? `[${sign(c.ci[0])}, ${sign(c.ci[1])}]` : '—'}</td>
              <td className="r table-cell" title={c.partial ? 'scores above are on the shared rows only' : undefined}>{c.rows?.toLocaleString()}{c.partial ? ` of ${Math.max(c.rows_a ?? 0, c.rows_b ?? 0).toLocaleString()}` : ''}</td>
              <td className={c.verdict === 'b better' ? 'up' : c.verdict === 'a better' ? 'down' : 'muted'} style={{ fontSize: 12 }}>{c.verdict === 'b better' ? `${r.id} better beyond noise` : c.verdict === 'a better' ? `${base.id} better beyond noise` : 'no difference beyond noise'}</td></tr>)
        })}</tbody>
      </table></div>
    </div>
  )
}

import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { ChangeItem, ModelVersion } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { useFetch } from '@/shared/hooks/useFetch'

const sgn = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const dg = (m: string) => (/cost/.test(m) ? 1 : 4)
const pc = (v: number | null | undefined) => (v == null ? '—' : `${(100 * v).toFixed(1)}%`)
const VERDICT: Record<string, string> = { better: 'success', worse: 'danger', 'within noise': 'neutral', 'no result': 'neutral' }

/** D4 · Changes after deployment: typed experiments on the newest labelled batch against the version as it serves
 *  today. A better result becomes a request (a new version, approval pending); nothing changes the live version. */
export function ChangesCard({ v, actor, onRequested }: { v: ModelVersion; actor: string; onRequested?: (vid: string) => void }) {
  const { data: s, err, reload } = useFetch(() => api.registry.changes(v.id), [v.id],
    { pollMs: 5000, while: (d) => !!d?.experiments.some((x) => x.status === 'running' || x.status === 'queued') })
  const [kind, setKind] = useState('threshold')
  const [col, setCol] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  if (err) return <div className="card card-body" role="alert">✕ {err}</div>
  if (!s) return <div className="card card-body row"><ResonanceWave /> Reading the changes…</div>
  const run = async () => {
    if (!actor) { setMsg('Type your name at the top of Registry first (changes are audited).'); return }
    setBusy('run'); setMsg('')
    try { await api.registry.startChange(v.id, actor, kind, kind === 'drop_feature' ? col || s.suggested_drop || undefined : undefined); reload() }
    catch (e) { setMsg(`✕ ${(e as Error).message}`) } finally { setBusy(null) }
  }
  const request = async (x: ChangeItem) => {
    if (!actor) { setMsg('Type your name at the top of Registry first.'); return }
    setBusy(x.id); setMsg('')
    try { const r = await api.registry.requestChange(v.id, x.id, actor); setMsg(`Requested: new version ${r.version.id} is building, approval pending. A reviewer other than ${actor} approves it; promotion is a separate step.`); reload(); onRequested?.(r.version.id) }
    catch (e) { setMsg(`✕ ${(e as Error).message}`) } finally { setBusy(null) }
  }
  const nb = s.newest_batch
  return (
    <div className="card" data-testid="changes-card" data-eligible={String(s.eligible)}>
      <div className="card-head"><div className="left"><h3 className="card-title">Changes</h3>
        <span className="helper" style={{ fontSize: 12 }}>after deployment: each change is scored on the newest labelled batch against the version as it serves today; a better one becomes a request</span></div>
        {s.eligible && <div className="row" style={{ gap: 8 }}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} data-testid="changes-kind">{Object.entries(s.kinds).map(([k, t]) => <option key={k} value={k} title={t}>{k.replace('_', ' ')}</option>)}</select>
          {kind === 'drop_feature' && <select value={col || s.suggested_drop || ''} onChange={(e) => setCol(e.target.value)} data-testid="changes-column">
            {(s.drifted.length ? s.drifted.map((d) => d.feature) : (v.result?.version?.features ?? [])).map((f) => <option key={f} value={f}>{f}</option>)}</select>}
          <button className="btn sm primary" onClick={run} disabled={busy != null} data-testid="changes-run">{busy === 'run' ? 'Working…' : 'Evaluate'}</button></div>}</div>
      <div className="card-body stack" style={{ gap: 10 }}>
        {!s.eligible ? <p className="helper" style={{ margin: 0 }}>{s.why}</p> : <p className="helper" style={{ margin: 0, fontSize: 12.5 }}>
          Newest labelled batch <b>{nb!.filename}</b> ({nb!.data_date ?? '—'}, <b className="mono">{nb!.labelled_rows.toLocaleString()}</b> labelled rows) · threshold now <b className="mono">{s.threshold?.toFixed(4)}</b> · costs: {s.cost?.source}
          {s.drifted.length > 0 && <> · drifted: {s.drifted.slice(0, 3).map((d) => `${d.feature} (PSI ${d.psi.toFixed(2)})`).join(', ')}</>}</p>}
        {(s.scale_changes ?? []).map((x) => <div key={x.feature} className="split-warn" role="note" data-testid="changes-scale">
          <b>Fix the feed first: {x.feature} changed scale</b> (× {x.factor.toPrecision(4)}{x.unit ? `, ${x.unit}` : ''}). {x.text}</div>)}
        {msg && <p className="helper" style={{ fontSize: 12.5, margin: 0 }} data-testid="changes-msg">{msg}</p>}
        {s.experiments.length > 0 && <div style={{ overflowX: 'auto' }}><table className="table compact" data-testid="changes-table">
          <thead><tr><th>change</th><th className="r">now → with it</th><th className="r">Δ (CI)</th><th>operating point</th><th>verdict</th><th /></tr></thead>
          <tbody>{s.experiments.map((x) => {
            const r = x.result
            return <tr key={x.id} data-testid={`change-${x.kind}`} data-verdict={x.verdict ?? x.status}>
              <td style={{ whiteSpace: 'normal', maxWidth: 260 }}><b>{x.title}</b><div className="muted" style={{ fontSize: 11 }}>{x.batch.filename} · by {x.by}{x.run_id ? ` · run ${x.run_id}` : ''}</div></td>
              {r ? <>
                <td className="r mono">{r.current.toFixed(dg(r.metric))} → {r.proposed.toFixed(dg(r.metric))}<div className="muted" style={{ fontSize: 11 }}>{r.metric}</div></td>
                <td className="r mono"><b>{sgn(r.delta, dg(r.metric))}</b><div className="muted" style={{ fontSize: 11 }}>[{sgn(r.ci[0], dg(r.metric))}, {sgn(r.ci[1], dg(r.metric))}]</div>
                  {r.auc && <div className="muted" style={{ fontSize: 11 }}>AUC {r.auc.current.toFixed(3)} → {r.auc.proposed.toFixed(3)}</div>}
                  {r.cost && <div className="muted" style={{ fontSize: 11 }}>cost/1,000 {sgn(r.cost.delta, 1)}</div>}</td>
                <td className="helper" style={{ fontSize: 11.5, whiteSpace: 'normal' }}>{r.operating ? <>declines {pc(r.operating.current.decline_rate)} → {pc(r.operating.proposed.decline_rate)}, catches {pc(r.operating.current.catch_rate)} → {pc(r.operating.proposed.catch_rate)}
                  {r.operating.proposed.t != null && <> · t {r.operating.current.t?.toFixed(3) ?? '—'} → {r.operating.proposed.t.toFixed(3)}</>}</> : '—'}</td>
              </> : <td colSpan={3} className="helper">{x.status === 'running' ? <><ResonanceWave /> retraining ({x.run_id})…</> : x.error ?? x.status}</td>}
              <td><span className={`pill ${VERDICT[x.verdict ?? ''] ?? 'neutral'}`}>{x.verdict ?? x.status}</span></td>
              <td>{x.request ? <span className="helper" style={{ fontSize: 11.5 }}>requested → <span className="mono">{x.request.version_id}</span></span>
                : x.verdict === 'better' ? <button className="btn sm" onClick={() => request(x)} disabled={busy != null} data-testid={`change-request-${x.kind}`}>{busy === x.id ? 'Working…' : 'Request →'}</button> : null}</td></tr>
          })}</tbody></table></div>}
        <details className="an-adv"><summary className="micro-label">How changes are measured</summary>
          <ul className="helper" style={{ fontSize: 11.5, margin: '6px 0 0', paddingLeft: 18 }}>
            {Object.entries(s.kinds).map(([k, t]) => <li key={k}><b>{k.replace('_', ' ')}</b>: {t}</li>)}
            {Object.entries(s.rules).filter(([, t]) => t).map(([k, t]) => <li key={k}><b>{k.replace('_', ' ')}</b>: {t}</li>)}</ul></details>
      </div>
    </div>
  )
}

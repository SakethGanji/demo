import { useEffect, useState } from 'react'
import { MaskBar } from '@/shared/components/MaskBar'
import type { Unmask } from '@/shared/api/unmask'
import { api } from '@/shared/api/client'
import { useHashParam } from '@/shared/hooks/useHashParam'
import type { FeatureLabEntry, FeatureLabJob } from '@/shared/api/types'
import { useFetch } from '@/shared/hooks/useFetch'

const d4 = (v: number | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(4)}`)

/**
 * D2 · Feature Lab (Build → Advanced): a search over typed transforms (ratio, difference, per-entity aggregate, date
 * part, bin, log, category cross), scored on training folds only, kept only when the gain's CI is above 0, with the
 * leakage and decision-time checks on every candidate. The kept features become a new dataset version.
 */
export function FeatureLab({ dsId, entities, onVersion }: { dsId: string; entities: string[]; onVersion: (id: string, msg: string) => void }) {
  const list = useFetch(() => api.datasets.featureLab(dsId), [dsId])
  const [jobId, setJobId] = useState<string | null>(null)
  const [entity, setEntity] = useState<string>('')
  const [err, setErr] = useState('')
  const last = list.data?.dataset_id === dsId ? list.data.jobs[0]?.id ?? null : null
  useEffect(() => setJobId(null), [dsId])
  const id = jobId ?? last
  const [unmask, setUnmask] = useState<Unmask>(null)
  const job = useFetch(id ? () => api.datasets.featureLabJob(dsId, id, unmask ?? undefined) : null, [dsId, id, unmask], { pollMs: 2000, while: (j) => j?.status === 'running' })
  const j = job.data?.id === id ? job.data : null
  const start = async () => {
    setErr('')
    try { const s = await api.datasets.featureLabStart(dsId, { entity: entity || null, max_candidates: 20, time_limit: 240 }); setJobId(s.id) } catch (e) { setErr((e as Error).message) }
  }
  const running = j?.status === 'running'
  const asked = useHashParam('lab') === '1' // D3: the Model Doctor's "new features" lever opens it
  return (
    <details className="an-adv card feature-lab" data-testid="feature-lab" open={!!j || asked}>
      <summary className="micro-label" style={{ padding: 'var(--space-12) var(--space-16)' }}>Advanced · Feature Lab</summary>
      <div className="card-body stack" style={{ gap: 10 }}>
        <p className="helper" style={{ margin: 0 }}>Searches typed features only (ratio, difference, per-entity aggregate, date part, bins, log, category cross; never code). Each is scored on the training rows, kept only if it clearly helps, and checked for leakage and decision time. The kept ones become a new version of this table.</p>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          {entities.length > 0 && <select className="select" value={entity} onChange={(e) => setEntity(e.target.value)} aria-label="Entity column" style={{ width: 'auto' }}>
            <option value="">no per-entity aggregates</option>
            {entities.map((c) => <option key={c} value={c}>aggregate per {c}</option>)}
          </select>}
          <button className="btn" onClick={start} disabled={running} data-testid="fl-start">{running ? 'Searching…' : j ? 'Search again' : 'Search features'}</button>
          {err && <span className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</span>}
        </div>
        {j && <MaskBar info={job.data} kind="dataset" id={dsId} value={unmask} onChange={setUnmask} what="bin edges" />}
        {j && <Journal j={j} onVersion={onVersion} />}
      </div>
    </details>
  )
}

function Journal({ j, onVersion }: { j: FeatureLabJob; onVersion: (id: string, msg: string) => void }) {
  const r = j.result
  const label = r?.metric_label ?? 'score'
  const h = r?.holdout
  return (
    <div className="stack" style={{ gap: 8 }} data-testid="fl-job" data-status={j.status}>
      {j.status === 'failed' && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {j.error}</p>}
      {r && <p className="fl-say" data-testid="fl-summary">
        {r.kept.length ? <>Kept <b>{r.kept.length}</b> of {j.journal.length}: {r.kept.map((t) => t.name).join(', ')}. </> : <>Nothing kept of {j.journal.length} tried: no candidate clearly beats the {label} the table already gives ({r.base_score_oof.toFixed(4)} out of fold). </>}
        {h && <>Holdout {label} {h.before.toFixed(4)} → {h.after.toFixed(4)} ({d4(h.delta)}, 95 % CI [{d4(h.ci[0])}, {d4(h.ci[1])}]).</>}
      </p>}
      {j.version && <div className="row" style={{ gap: 8 }}>
        <span className="helper">↳ New version <b>{j.version.name}</b> with {j.version.columns_added.join(', ')}; same {j.version.holdout_rows.toLocaleString()} holdout rows.</span>
        <button className="btn sm" onClick={() => onVersion(j.version!.id, `Feature Lab: ${j.version!.name} (+ ${j.version!.columns_added.join(', ')})`)} data-testid="fl-use">Use it →</button>
      </div>}
      <div className="table-wrap"><table className="table fl-table">
        <thead><tr><th>#</th><th>Candidate</th><th className="r">Δ {label} (out of fold)</th><th className="r">Alone</th><th>Decision</th></tr></thead>
        <tbody>{j.journal.map((e) => <Entry key={e.step} e={e} />)}</tbody>
      </table></div>
      {j.status === 'running' && <p className="helper">Searching… {j.journal.length} scored so far.</p>}
      {r && <p className="helper" style={{ fontSize: 12 }} data-testid="fl-rules">{r.rules.score} {r.rules.keep} {r.rules.leakage} {r.rules.budget}</p>}
    </div>
  )
}

function Entry({ e }: { e: FeatureLabEntry }) {
  return (
    <tr className={`fl-${e.status}`} data-testid={`fl-entry-${e.status}`}>
      <td className="mono">{e.step}</td>
      <td><span className="mono">{e.name}</span>{e.text && <div className="helper">{e.text}</div>}</td>
      <td className="r mono">{e.delta == null ? '—' : <>{d4(e.delta)} <span className="helper">[{d4(e.ci?.[0])}, {d4(e.ci?.[1])}]</span></>}</td>
      <td className="r mono">{e.single == null ? '—' : e.single.toFixed(3)}</td>
      <td><span className={`chip ${e.status === 'kept' ? 'ok' : ''}`}>{e.status}</span> <span className="helper">{e.reason}</span></td>
    </tr>
  )
}

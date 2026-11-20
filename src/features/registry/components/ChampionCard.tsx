import { useState } from 'react'
import { catalog } from '@/shared/api/catalog'
import type { ModelVersion } from '@/shared/api/types'
import { useFetch } from '@/shared/hooks/useFetch'
import { NotesStrip } from '@/features/models/components/NotesStrip'

/** The project's champion: this version's badge, the request (approved versions only) and the four-eyes decision.
 *  Both steps are audited. Tags and notes on the version sit under it. */
export function ChampionCard({ v, actor, onChanged }: { v: ModelVersion; actor: string; onChanged: () => void }) {
  const lin = useFetch(() => catalog.lineage('version', v.id), [v.id])
  const pid = lin.data?.project?.id
  const proj = useFetch(() => (pid ? catalog.project(pid) : Promise.resolve(null)), [pid])
  const [msg, setMsg] = useState('')
  const [override, setOverride] = useState('')
  const p = proj.data
  const blocked = /critical finding is open/.test(msg)
  const champV = lin.data?.nodes.find((n) => n.kind === 'version' && n.id === p?.champion?.version_id)
  const champ = p?.champion
  const pending = p?.champion_requests.find((r) => r.status === 'pending')
  const isChamp = champ?.version_id === v.id
  const run = (x: Promise<unknown>) => x.then(() => { setMsg(''); setOverride(''); proj.reload(); lin.reload(); onChanged() }).catch((e) => setMsg((e as Error).message))
  return (
    <>
      <div className="card card-body row" style={{ gap: 'var(--space-12)', flexWrap: 'wrap' }} data-testid="champion-card">
        <span className="micro-label">champion</span>
        {isChamp ? <span className="champ-badge" data-testid="champion-badge">★ champion of {p?.name}</span>
          : champ ? <span className="helper">{p?.name}'s champion is <b title={champ.version_id}>{champV ? `${champV.model_name} v${champV.version}` : champ.version_id}</b> (approved by {champ.decided_by})</span>
            : <span className="helper">{p ? `${p.name} has no champion yet.` : 'Reading the project…'}</span>}
        <span className="spacer" />
        {pending && pending.version_id === v.id ? <>
          <span className="helper" data-testid="champion-pending">Requested by {pending.requested_by}: needs someone else to approve.</span>
          <button className="btn sm" disabled={!actor} onClick={() => run(catalog.decideChampion(pending.id, false, actor, override ? `override: ${override}` : ''))}>Reject</button>
          <button className="btn sm primary" disabled={!actor} onClick={() => run(catalog.decideChampion(pending.id, true, actor, '', override))} data-testid="champion-approve">Approve champion</button>
        </> : !isChamp && p && <button className="btn sm" disabled={!actor || v.status !== 'ready' || v.approval !== 'approved'} data-testid="champion-request"
          title={v.approval !== 'approved' ? 'Approve the version first' : !actor ? 'Type your name in “acting as”' : 'A second person approves it'}
          onClick={() => run(catalog.requestChampion(p.id, v.id, actor, '', override))}>Make champion…</button>}
        {msg && <span className="helper" role="alert" style={{ color: 'var(--status-danger)', fontSize: 12, flexBasis: '100%' }}>{msg}</span>}
        {blocked && <label className="row" style={{ gap: 6, flexBasis: '100%' }}><span className="micro-label">override reason</span>
          <input className="input" style={{ flex: 1 }} placeholder="why this goes ahead with the finding open (≥ 10 characters, audited)" value={override} onChange={(e) => setOverride(e.target.value)} data-testid="champion-override" /></label>}
        <span className="helper" style={{ fontSize: 11.5, flexBasis: '100%' }}>Four eyes on names, not authenticated identities: the approver must be someone other than the requester (and the change's author), compared on letters and digits only. Blocked while a critical Doctor finding (a leak) is open, unless overridden with a reason.</span>
      </div>
      <NotesStrip kind="version" id={v.id} />
    </>
  )
}

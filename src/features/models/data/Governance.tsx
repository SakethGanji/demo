import { useState } from 'react'
import { certifications, contracts, pii, refusal, shares, teamsApi, PII_MASKS, PII_TAGS, type Certification, type ContractResult, type PiiTag, type Share } from '@/shared/api/governance'
import type { DsHistory } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—')
const errLine = (e: unknown) => { const x = refusal(e); return `${x.message}${x.code ? ` (${x.code})` : ''}` }

export { useCan, why } from '@/shared/hooks/useCan'
import { useCan, why } from '@/shared/hooks/useCan'

/**
 * U2 (P20, P21, C §4.3–4.5): who may see and use this dataset. PII tags per table (suggested tags mask until a
 * maintainer confirms them), the data contract (edit or freeze from a version; uploads are checked against it),
 * certifications (requested by a maintainer, decided by an owner or a risk reviewer, never the requester), and shares
 * to other teams (masked or unmasked PII; decided by an owner; revocable). Every button is greyed by /authz/check.
 */
export function Governance({ d, vid, v, table, by, onChanged }: { d: DsHistory; vid: string; v: number; table: string | null; by: string; onChanged: (text: string) => void }) {
  return (
    <div className="grid two gov-grid" data-testid="governance">
      <PiiPanel vid={vid} table={table} by={by} onChanged={onChanged} />
      <ContractPanel root={d.root_id} vid={vid} v={v} by={by} onChanged={onChanged} />
      <CertPanel root={d.root_id} vid={vid} v={v} d={d} by={by} onChanged={onChanged} />
      <SharePanel root={d.root_id} vid={vid} by={by} onChanged={onChanged} />
    </div>
  )
}

function PiiPanel({ vid, table, by, onChanged }: { vid: string; table: string | null; by: string; onChanged: (t: string) => void }) {
  const [tick, setTick] = useState(0)
  const t = useFetch(() => pii.get(vid, table), [vid, table, tick])
  const can = useCan('data.tag_pii', 'dataset', vid, by)
  const [edit, setEdit] = useState<Record<string, Partial<PiiTag>>>({})
  const [msg, setMsg] = useState('')
  const run = (p: Promise<unknown>, text: string) => { setMsg(''); p.then(() => { setEdit({}); setTick((x) => x + 1); onChanged(text) }).catch((e) => setMsg(errLine(e))) }
  const tags = t.data?.tags ?? []
  const changed = Object.entries(edit).filter(([c, x]) => { const o = tags.find((g) => g.column === c); return o && (x.tag !== undefined && x.tag !== o.tag || x.mask !== undefined && x.mask !== o.mask) })
  return (
    <div className="card" data-testid="pii-panel">
      <div className="card-head"><div className="left"><h3 className="card-title">PII tags</h3>
        <span className="helper" style={{ fontSize: 12 }}>{t.data ? `table ${t.data.table} · ${t.data.masked_columns.length} masked · ${t.data.pending_review.length} to review` : ''}</span></div>
        <button className="btn sm" disabled={!by || !can?.allow || !t.data?.pending_review.length} title={why(can, t.data?.pending_review.length ? 'Confirm every suggested tag as it stands' : 'Nothing to review')}
          onClick={() => run(pii.confirmSuggested(vid, t.data!.table, by), `PII tags of ${t.data!.table} confirmed (audited).`)} data-testid="pii-confirm">Confirm suggested</button></div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {t.err && !t.data ? <span className="helper">{t.err}</span> : !t.data ? <span className="helper">…</span> : !tags.length ? <span className="helper">No column of {t.data.table} carries a tag.</span> : <>
          <table className="table gov-t"><thead><tr><th>column</th><th>tag</th><th>mask</th><th>source</th></tr></thead>
            <tbody>{tags.map((g) => {
              const x = { ...g, ...edit[g.column] }
              return <tr key={g.column} data-testid={`pii-${g.column}`}>
                <td className="mono">{g.mask !== 'none' ? '🔒 ' : ''}{g.column}</td>
                <td><select className="select" disabled={!can?.allow} value={x.tag} onChange={(e) => setEdit({ ...edit, [g.column]: { ...edit[g.column], tag: e.target.value as PiiTag['tag'] } })}>{PII_TAGS.map((o) => <option key={o}>{o}</option>)}</select></td>
                <td><select className="select" disabled={!can?.allow} value={x.mask} onChange={(e) => setEdit({ ...edit, [g.column]: { ...edit[g.column], mask: e.target.value as PiiTag['mask'] } })}>{PII_MASKS.map((o) => <option key={o}>{o}</option>)}</select></td>
                <td>{g.source === 'suggested' ? <span className="pt-chip up-pii" title={`by ${g.set_by}, ${when(g.set_at)}: masks like a confirmed tag until reviewed`}>suggested</span> : <span className="helper" title={when(g.set_at)}>confirmed by {g.set_by}</span>}</td></tr>
            })}</tbody></table>
          {changed.length > 0 && <button className="btn sm primary" disabled={!can?.allow} onClick={() => run(pii.set(vid, t.data!.table, changed.map(([c, x]) => { const o = tags.find((g) => g.column === c)!; return { column: c, tag: x.tag ?? o.tag, mask: x.mask ?? o.mask } }), by), `${changed.length} PII tag(s) changed (audited with the old values).`)} data-testid="pii-save">Save {changed.length} change{changed.length === 1 ? '' : 's'}</button>}
          <span className="helper" style={{ fontSize: 12 }}>Tags belong to the dataset, so every version is masked the same way. redact → •••; hash → a team-salted sha256 (joins and counts still work); last4 → •••6789; year_only; bucket. {t.data.note}</span></>}
        {msg && <span className="helper up-err" role="alert">✕ {msg}</span>}
      </div>
    </div>
  )
}

function ContractPanel({ root, vid, v, by, onChanged }: { root: string; vid: string; v: number; by: string; onChanged: (t: string) => void }) {
  const [tick, setTick] = useState(0)
  const c = useFetch(() => contracts.get(root), [root, tick])
  const can = useCan('data.contract', 'dataset', vid, by)
  const [res, setRes] = useState<ContractResult | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const act = c.data?.active
  const freeze = () => { setMsg(''); contracts.freeze(root, vid, by).then((r) => { setRes(r.result); setTick((x) => x + 1); onChanged(`Contract v${r.version} frozen from v${v}: ${r.result.status}, ${r.result.n_rules} rules.`) }).catch((e) => setMsg(errLine(e))) }
  const check = () => { setMsg(''); contracts.check(vid).then(setRes).catch((e) => setMsg(errLine(e))) }
  const save = () => {
    setMsg('')
    let spec: Record<string, unknown>
    try { spec = JSON.parse(text ?? '{}') } catch (e) { setMsg(`Not JSON: ${(e as Error).message}`); return }
    contracts.put(root, spec, by, 'edited in the contract editor').then((r) => { setText(null); setTick((x) => x + 1); onChanged(`Contract v${r.version} saved; the previous version stays, inactive.`) }).catch((e) => setMsg(errLine(e)))
  }
  return (
    <div className="card" data-testid="contract-panel">
      <div className="card-head"><div className="left"><h3 className="card-title">Data contract</h3>
        <span className="helper" style={{ fontSize: 12 }}>{act ? `v${act.version} active · by ${act.created_by ?? '—'}` : 'none yet'}</span></div>
        <span className="row" style={{ gap: 6 }}>
          <button className="btn sm" disabled={!by || !can?.allow} title={why(can, `Freeze what v${v} looks like: every table required, its columns and kinds, keys and relations`)} onClick={freeze} data-testid="contract-freeze">Freeze from v{v}</button>
          <button className="btn sm ghost" disabled={!act} onClick={check} data-testid="contract-check">Check v{v}</button></span></div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {res && <div data-testid="contract-result"><span className={`pt-chip up-ct-${res.status}`}>{res.status}</span> <span className="helper">{res.n_rules} rules · {res.n_blocks} blocking · {res.n_warnings} warnings</span>
          {res.rules.filter((r) => r.status !== 'pass').slice(0, 8).map((r, i) => <div key={i} className={`helper up-ct-${r.status}`}>{r.status === 'block' ? '✕' : '⚠'} {r.message ?? `${r.table ?? ''}.${r.column ?? ''} ${r.rule}`}</div>)}</div>}
        {act && text == null && <details><summary className="micro-label">spec (tables {Object.keys((act.spec.tables as object) ?? {}).join(', ')})</summary><pre className="gov-pre">{JSON.stringify(act.spec, null, 1)}</pre></details>}
        {text != null ? <><textarea className="input mono gov-pre" rows={12} value={text} onChange={(e) => setText(e.target.value)} data-testid="contract-text" />
          <span className="row" style={{ gap: 6 }}><button className="btn sm primary" disabled={!can?.allow} onClick={save} data-testid="contract-save">Save as a new contract version</button><button className="btn sm ghost" onClick={() => setText(null)}>Cancel</button></span></>
          : <button className="btn sm ghost" style={{ alignSelf: 'flex-start' }} disabled={!by || !can?.allow} title={why(can)} onClick={() => setText(JSON.stringify(act?.spec ?? { severity_default: 'warn', tables: {} }, null, 2))} data-testid="contract-edit">Edit the spec…</button>}
        <span className="helper" style={{ fontSize: 12 }}>Every upload's check runs the active contract on the version it would make: a <b>block</b> rule stops Create, a <b>warn</b> rule keeps the new version from becoming the default. Rules: required tables, key, rows min/max, column type / nullable / unique / max null share / range / values, new and removed columns, type changes, relation coverage.</span>
        {msg && <span className="helper up-err" role="alert">✕ {msg}</span>}
      </div>
    </div>
  )
}

function CertPanel({ root, vid, v, d, by, onChanged }: { root: string; vid: string; v: number; d: DsHistory; by: string; onChanged: (t: string) => void }) {
  const [tick, setTick] = useState(0)
  const c = useFetch(() => certifications.list(root), [root, tick])
  const canReq = useCan('data.certify', 'dataset', vid, by)
  const canCert = useCan('data.certify_decide_certified', 'dataset', vid, by)
  const canModel = useCan('data.certify_decide_model', 'dataset', vid, by)
  const [level, setLevel] = useState<Certification['level']>('certified')
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [msg, setMsg] = useState('')
  const run = (p: Promise<Certification>, text: (c: Certification) => string) => { setMsg(''); p.then((r) => { setTick((x) => x + 1); onChanged(text(r)) }).catch((e) => setMsg(errLine(e))) }
  const vOf = (id: string) => d.versions.find((x) => x.id === id)?.v
  const me = by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
  return (
    <div className="card" data-testid="cert-panel">
      <div className="card-head"><div className="left"><h3 className="card-title">Certifications</h3>
        <span className="helper" style={{ fontSize: 12 }}>{c.data?.live.length ? c.data.live.map((x) => `✓ ${x.level.replace(/_/g, ' ')} v${vOf(x.version_id)}`).join(' · ') : 'none live'}</span></div></div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {(c.data?.certifications ?? []).map((x) => {
          const can = x.level === 'certified' ? canCert : canModel
          const own = me && me === x.requested_by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
          return <div key={x.id} className="gov-item" data-testid={`cert-${x.id}`}>
            <span className={`pt-chip cert-${x.status}`}>{x.status}</span> <b>{x.level.replace(/_/g, ' ')}</b> v{vOf(x.version_id)} · asked by {x.requested_by} · “{x.reason}”
            {x.decided_by && <span className="helper"> · {x.status === 'active' ? 'approved' : x.status} by {x.decided_by}{x.expires_at ? ` · expires ${when(x.expires_at)}` : ''}</span>}
            {x.status === 'pending' && <div className="row" style={{ gap: 6, marginTop: 4 }}>
              <input className="input" style={{ flex: 1 }} placeholder="comment (required to reject)" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button className="btn sm" disabled={!by || !can?.allow || !!own} title={own ? `Four eyes: ${x.requested_by} asked for it, so a second person decides` : why(can)} onClick={() => run(certifications.decide(x.id, true, comment, by), (r) => `Certification ${r.level} approved by ${by}; expires ${when(r.expires_at)}.`)} data-testid="cert-approve">Approve</button>
              <button className="btn sm ghost" disabled={!by || !can?.allow || !!own || !comment.trim()} title={own ? 'Four eyes' : why(can)} onClick={() => run(certifications.decide(x.id, false, comment, by), () => 'Certification rejected.')} data-testid="cert-reject">Reject</button></div>}
          </div>
        })}
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <select className="select" value={level} onChange={(e) => setLevel(e.target.value as Certification['level'])} data-testid="cert-level"><option value="certified">certified (owner decides)</option><option value="approved_for_model">approved for model (risk reviewer decides)</option></select>
          <input className="input" style={{ flex: 1, minWidth: 140 }} placeholder="reason" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="cert-reason" />
          <button className="btn sm" disabled={!by || !canReq?.allow || reason.trim().length < 3} title={why(canReq, 'Needs the active contract to pass and every PII tag reviewed')} onClick={() => run(certifications.request(vid, level, reason.trim(), by), (r) => `Certification of v${v} requested (${r.level}): someone else decides.`)} data-testid="cert-request">Request for v{v}</button>
        </div>
        {msg && <span className="helper up-err" role="alert" data-testid="cert-msg">✕ {msg}</span>}
      </div>
    </div>
  )
}

function SharePanel({ root, vid, by, onChanged }: { root: string; vid: string; by: string; onChanged: (t: string) => void }) {
  const [tick, setTick] = useState(0)
  const s = useFetch(() => shares.list({ object_id: root }), [root, tick])
  const teams = useFetch(() => teamsApi.list(true), [])
  const canShare = useCan('data.share', 'dataset', vid, by)
  const canDecide = useCan('data.share_decide', 'dataset', vid, by)
  const [f, setF] = useState<{ grantee: string; scope: Share['scope']; pii: Share['pii']; reason: string }>({ grantee: '', scope: 'default', pii: 'masked', reason: '' })
  const [grant, setGrant] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState('')
  const name = (t: string) => teams.data?.find((x) => x.id === t)?.name ?? t
  const run = (p: Promise<Share>, text: (r: Share) => string) => { setMsg(''); p.then((r) => { setTick((x) => x + 1); onChanged(text(r)) }).catch((e) => setMsg(errLine(e))) }
  const me = by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
  return (
    <div className="card" data-testid="share-panel">
      <div className="card-head"><div className="left"><h3 className="card-title">Shares</h3>
        <span className="helper" style={{ fontSize: 12 }}>other teams that may read and train on this dataset</span></div></div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {(s.data ?? []).map((x) => {
          const own = me && me === x.requested_by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
          return <div key={x.id} className="gov-item" data-testid={`share-${x.id}`}>
            <span className={`pt-chip cert-${x.status}`}>{x.status}</span> to <b>{name(x.grantee_team)}</b> · scope {x.scope}{x.pinned_version ? ` (${x.pinned_version})` : ''} · PII <b>{x.pii}</b> · asked by {x.requested_by} · “{x.reason}”
            {x.decided_by && <span className="helper"> · decided by {x.decided_by}</span>}{x.revoked_by && <span className="helper"> · revoked by {x.revoked_by}</span>}
            {x.status === 'pending' && <div className="row" style={{ gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
              {x.pii === 'unmasked' && <label className="row helper" style={{ gap: 4 }} data-testid="share-grant-unmasked"><input type="checkbox" checked={!!grant[x.id]} onChange={(e) => setGrant({ ...grant, [x.id]: e.target.checked })} /> grant unmasked PII (clear values, every read audited)</label>}
              <button className="btn sm" disabled={!by || !canDecide?.allow || !!own || (x.pii === 'unmasked' && !grant[x.id])} title={own ? `Four eyes: ${x.requested_by} asked for it, so a second person decides` : why(canDecide)}
                onClick={() => run(shares.decide(x.id, true, by, '', !!grant[x.id]), (r) => `Share to ${name(r.grantee_team)} approved: ${r.status}.`)} data-testid="share-approve">Approve</button>
              <button className="btn sm ghost" disabled={!by || !canDecide?.allow || !!own} title={why(canDecide)} onClick={() => run(shares.decide(x.id, false, by), () => 'Share rejected.')} data-testid="share-reject">Reject</button></div>}
            {x.status === 'active' && <button className="chip-btn" style={{ marginLeft: 6 }} disabled={!by || !canShare?.allow} title={why(canShare, 'Access stops at once; runs already trained are kept')}
              onClick={() => run(shares.revoke(x.id, 'revoked from the dataset page', by), (r) => `Share revoked; ${(r.runs_kept ?? []).length} run(s) trained on it are kept.`)} data-testid="share-revoke">revoke</button>}
          </div>
        })}
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <select className="select" value={f.grantee} onChange={(e) => setF({ ...f, grantee: e.target.value })} data-testid="share-team"><option value="">to team…</option>{(teams.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          <select className="select" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value as Share['scope'] })} data-testid="share-scope" title="which versions the share covers">
            <option value="certified">certified versions</option><option value="default">the default version</option><option value="all">every version</option></select>
          <select className="select" value={f.pii} onChange={(e) => setF({ ...f, pii: e.target.value as Share['pii'] })} data-testid="share-pii"><option value="masked">PII masked</option><option value="unmasked">PII unmasked</option></select>
          <input className="input" style={{ flex: 1, minWidth: 120 }} placeholder="reason" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} data-testid="share-reason" />
          <button className="btn sm" disabled={!by || !canShare?.allow || !f.grantee || f.reason.trim().length < 3} title={why(canShare)} onClick={() => run(shares.create({ object_id: root, grantee_team: f.grantee, scope: f.scope, pii: f.pii, reason: f.reason.trim() }, by), (r) => `Share to ${name(r.grantee_team)} requested: an owner other than you decides.`)} data-testid="share-request">Request share</button>
        </div>
        {msg && <span className="helper up-err" role="alert" data-testid="share-msg">✕ {msg}</span>}
      </div>
    </div>
  )
}

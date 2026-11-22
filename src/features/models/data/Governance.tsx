import { useState } from 'react'
import { certifications, contracts, pii, refusal, shares, teamsApi, PII_MASKS, PII_TAGS, type Certification, type ContractResult, type PiiTag, type Share } from '@/shared/api/governance'
import type { DsHistory } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import { Badge, CardHead, ConfirmButton, Field, Why } from '@/shared/components/DataKit'
import { Loading } from '@/shared/components/Status'

import { when } from '@/shared/lib/format'
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
      <ContractPanel root={d.root_id} readAs={vid} vid={vid} v={v} by={by} onChanged={onChanged} />
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
      <CardHead title="PII tags" sub={t.data ? <>table <b className="mono">{t.data.table}</b> · {t.data.masked_columns.length} masked · {t.data.pending_review.length ? <b className="g-warn">{t.data.pending_review.length} to review</b> : 'none to review'}</> : undefined}
        actions={<button className="btn sm" disabled={!by || !can?.allow || !t.data?.pending_review.length} title={why(can, t.data?.pending_review.length ? 'Confirm every suggested tag as it stands' : 'Nothing to review')}
          onClick={() => run(pii.confirmSuggested(vid, t.data!.table, by), `PII tags of ${t.data!.table} confirmed (audited).`)} data-testid="pii-confirm">Confirm suggested</button>} />
      <div className="card-body stack" style={{ gap: 6 }}>
        {t.err && !t.data ? <span className="helper up-err">✕ {t.err}</span> : !t.data ? <Loading inline>Reading the tags…</Loading> : !tags.length ? <span className="helper">No column of {t.data.table} carries a tag: nothing is masked.</span> : <>
          <table className="table gov-t"><thead><tr><th>column</th><th>tag</th><th>mask</th><th>source</th></tr></thead>
            <tbody>{tags.map((g) => {
              const x = { ...g, ...edit[g.column] }
              return <tr key={g.column} data-testid={`pii-${g.column}`}>
                <td className="mono"><span className="gov-lock" aria-label={g.mask !== 'none' ? 'masked' : 'not masked'}>{g.mask !== 'none' ? '🔒' : ''}</span>{g.column}</td>
                <td><select className="select" aria-label={`tag of ${g.column}`} title={can?.allow ? undefined : why(can)} disabled={!can?.allow} value={x.tag} onChange={(e) => setEdit({ ...edit, [g.column]: { ...edit[g.column], tag: e.target.value as PiiTag['tag'] } })}>{PII_TAGS.map((o) => <option key={o}>{o}</option>)}</select></td>
                <td><select className="select" aria-label={`mask of ${g.column}`} title={can?.allow ? undefined : why(can)} disabled={!can?.allow} value={x.mask} onChange={(e) => setEdit({ ...edit, [g.column]: { ...edit[g.column], mask: e.target.value as PiiTag['mask'] } })}>{PII_MASKS.map((o) => <option key={o}>{o}</option>)}</select></td>
                <td>{g.source === 'suggested' ? <Badge status="suggested" title={`by ${g.set_by}, ${when(g.set_at)}: masks like a confirmed tag until reviewed`} /> : <span className="helper" title={when(g.set_at)}>✓ confirmed by {g.set_by}</span>}</td></tr>
            })}</tbody></table>
          {changed.length > 0 && <button className="btn sm primary" disabled={!can?.allow} onClick={() => run(pii.set(vid, t.data!.table, changed.map(([c, x]) => { const o = tags.find((g) => g.column === c)!; return { column: c, tag: x.tag ?? o.tag, mask: x.mask ?? o.mask } }), by), `${changed.length} PII tag(s) changed (audited with the old values).`)} data-testid="pii-save">Save {changed.length} change{changed.length === 1 ? '' : 's'}</button>}
</>}
        {msg && <span className="helper up-err" role="alert">✕ {msg}</span>}
        {can && !can.allow && <Why>{why(can)}</Why>}
      </div>
      <dl className="card-foot gov-masks"><dt>redact</dt><dd>•••</dd><dt>hash</dt><dd>a team-salted sha256 (joins and counts still work)</dd><dt>last4</dt><dd>•••6789</dd><dt>year_only · bucket</dt><dd>coarsened</dd>
        <dd className="gov-masks-note">Tags belong to the dataset, so every version is masked the same way. {t.data?.note}</dd></dl>
    </div>
  )
}

function ContractPanel({ root, readAs, vid, v, by, onChanged }: { root: string; readAs: string; vid: string; v: number; by: string; onChanged: (t: string) => void }) {
  const [tick, setTick] = useState(0)
  // F-X4a: read through the version in view (GET …/contract resolves it to the root), so a share grantee reads it too
  const c = useFetch(() => contracts.get(readAs), [readAs, tick])
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
      <CardHead title="Data contract" badge={act ? <Badge status="active">v{act.version} active</Badge> : <Badge status="none">none yet</Badge>} sub={act ? `by ${act.created_by ?? '—'}` : undefined}
        actions={<>
          <button className="btn sm" disabled={!by || !can?.allow} title={why(can, `Freeze what v${v} looks like: every table required, its columns and kinds, keys and relations`)} onClick={freeze} data-testid="contract-freeze">Freeze from v{v}</button>
          <button className="btn sm ghost" disabled={!act} title={act ? `Run the active contract on v${v}` : 'There is no contract yet'} onClick={check} data-testid="contract-check">Check v{v}</button></>} />
      <div className="card-body stack" style={{ gap: 6 }}>
        {res && <div className="contract-res" data-testid="contract-result"><div className="row" style={{ gap: 8 }}><Badge status={res.status} /><span className="helper"><b>{res.n_rules}</b> rules · <b className={res.n_blocks ? 'g-bad' : ''}>{res.n_blocks}</b> blocking · <b className={res.n_warnings ? 'g-warn' : ''}>{res.n_warnings}</b> warnings</span></div>
          {res.rules.filter((r) => r.status !== 'pass').length > 0 && <ul className="contract-rules">{res.rules.filter((r) => r.status !== 'pass').slice(0, 8).map((r, i) => <li key={i} className={`up-ct-${r.status}`}><span aria-hidden>{r.status === 'block' ? '✕' : '▲'}</span> <span>{r.message ?? `${r.table ?? ''}.${r.column ?? ''} ${r.rule}`}</span></li>)}
            {res.rules.filter((r) => r.status !== 'pass').length > 8 && <li className="muted">… and {res.rules.filter((r) => r.status !== 'pass').length - 8} more</li>}</ul>}</div>}
        {act && text == null && <details><summary className="micro-label">spec (tables {Object.keys((act.spec.tables as object) ?? {}).join(', ')})</summary><pre className="gov-pre">{JSON.stringify(act.spec, null, 1)}</pre></details>}
        {text != null ? <><textarea className="input mono gov-pre" rows={12} value={text} onChange={(e) => setText(e.target.value)} data-testid="contract-text" />
          <span className="row" style={{ gap: 6 }}><button className="btn sm primary" disabled={!can?.allow} onClick={save} data-testid="contract-save">Save as a new contract version</button><button className="btn sm ghost" onClick={() => setText(null)}>Cancel</button></span></>
          : <button className="btn sm ghost" style={{ alignSelf: 'flex-start' }} disabled={!by || !can?.allow} title={why(can)} onClick={() => setText(JSON.stringify(act?.spec ?? { severity_default: 'warn', tables: {} }, null, 2))} data-testid="contract-edit">Edit the spec…</button>}
        {msg && <span className="helper up-err" role="alert">✕ {msg}</span>}
        {can && !can.allow && <Why>{why(can)}</Why>}
      </div>
      <p className="card-foot">Every upload's check runs the active contract on the version it would make: a <b>block</b> rule stops Create, a <b>warn</b> rule keeps the new version from becoming the default. Rules: required tables, key, rows min/max, column type / nullable / unique / max null share / range / values, new and removed columns, type changes, relation coverage.</p>
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
      <CardHead title="Certifications" badge={c.data?.live.length ? <>{c.data.live.map((x) => <Badge key={x.id} status="certified">{x.level.replace(/_/g, ' ')} v{vOf(x.version_id)}</Badge>)}</> : <Badge status="none">none live</Badge>}
        sub="asked by a maintainer, decided by someone else" />
      <div className="card-body stack" style={{ gap: 6 }}>
        {(c.data?.certifications ?? []).map((x) => {
          const can = x.level === 'certified' ? canCert : canModel
          const own = me && me === x.requested_by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
          return <div key={x.id} className="gov-item" data-testid={`cert-${x.id}`}>
            <div className="gov-line"><Badge status={x.status} /><b>{x.level.replace(/_/g, ' ')}</b><span className="v-tag mono">v{vOf(x.version_id)}</span><span className="gov-reason" title={x.reason}>“{x.reason}”</span></div>
            <span className="sub-line">asked by {x.requested_by}{x.decided_by ? ` · ${x.status === 'active' ? 'approved' : x.status} by ${x.decided_by}` : ''}{x.expires_at ? ` · expires ${when(x.expires_at)}` : ''}</span>
            {x.status === 'pending' && <div className="row" style={{ gap: 6, marginTop: 4 }}>
              <input className="input" style={{ flex: 1 }} placeholder="comment (required to reject)" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button className="btn sm" disabled={!by || !can?.allow || !!own} title={own ? `Four eyes: ${x.requested_by} asked for it, so a second person decides` : why(can)} onClick={() => run(certifications.decide(x.id, true, comment, by), (r) => `Certification ${r.level} approved by ${by}; expires ${when(r.expires_at)}.`)} data-testid="cert-approve">Approve</button>
              <button className="btn sm ghost" disabled={!by || !can?.allow || !!own || !comment.trim()} title={own ? 'Four eyes' : why(can)} onClick={() => run(certifications.decide(x.id, false, comment, by), () => 'Certification rejected.')} data-testid="cert-reject">Reject</button></div>}
          </div>
        })}
        {!(c.data?.certifications ?? []).length && c.data && <span className="helper">No certification asked for yet.</span>}
        <div className="gov-form"><span className="micro-label sec-label">ask to certify v{v}</span><div className="form-row">
          <Field label="level"><select className="select" value={level} onChange={(e) => setLevel(e.target.value as Certification['level'])} data-testid="cert-level"><option value="certified">certified (owner decides)</option><option value="approved_for_model">approved for model (risk reviewer decides)</option></select></Field>
          <Field label="reason" grow><input className="input" style={{ minWidth: 140 }} placeholder="what it will be used for" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="cert-reason" /></Field>
          <button className="btn sm" disabled={!by || !canReq?.allow || reason.trim().length < 3} title={why(canReq, 'Needs the active contract to pass and every PII tag reviewed')} onClick={() => run(certifications.request(vid, level, reason.trim(), by), (r) => `Certification of v${v} requested (${r.level}): someone else decides.`)} data-testid="cert-request">Request for v{v}</button>
        </div>{canReq && !canReq.allow && <Why>{why(canReq)}</Why>}</div>
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
      <CardHead title="Shares" badge={(s.data ?? []).some((x) => x.status === 'active') ? <Badge status="active">{(s.data ?? []).filter((x) => x.status === 'active').length} active</Badge> : undefined} sub="other teams that may read and train on this dataset" />
      <div className="card-body stack" style={{ gap: 6 }}>
        {(s.data ?? []).map((x) => {
          const own = me && me === x.requested_by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
          return <div key={x.id} className="gov-item" data-testid={`share-${x.id}`}>
            <div className="gov-line"><Badge status={x.status} /><span>to <b>{name(x.grantee_team)}</b></span><span className="pt-chip" title="which versions it covers">{x.scope === 'default' ? 'the default version' : x.scope === 'all' ? 'every version' : 'certified versions'}{x.pinned_version ? ` (${x.pinned_version})` : ''}</span>
              <span className={`pt-chip ${x.pii === 'unmasked' ? 'appr-rejected' : ''}`}>{x.pii === 'unmasked' ? '🔓 PII unmasked' : '🔒 PII masked'}</span>
</div>
            <span className="sub-line">“{x.reason}” · asked by {x.requested_by}{x.decided_by ? ` · decided by ${x.decided_by}` : ''}{x.revoked_by ? ` · revoked by ${x.revoked_by}` : ''}</span>
              {x.status === 'active' && <div className="gov-acts"><ConfirmButton className="btn sm ghost danger-text" disabled={!by || !canShare?.allow} title={why(canShare, 'Access stops at once; runs already trained are kept')} testId="share-revoke" confirm="Revoke"
                warning={`Stop ${name(x.grantee_team)}'s access? Runs already trained on it are kept.`}
                onConfirm={() => run(shares.revoke(x.id, 'revoked from the dataset page', by), (r) => `Share revoked; ${(r.runs_kept ?? []).length} run(s) trained on it are kept.`)}>Revoke…</ConfirmButton></div>}
            {x.status === 'pending' && <div className="row" style={{ gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
              {x.pii === 'unmasked' && <label className="row helper" style={{ gap: 4 }} data-testid="share-grant-unmasked"><input type="checkbox" checked={!!grant[x.id]} onChange={(e) => setGrant({ ...grant, [x.id]: e.target.checked })} /> grant unmasked PII (clear values, every read audited)</label>}
              <button className="btn sm" disabled={!by || !canDecide?.allow || !!own || (x.pii === 'unmasked' && !grant[x.id])} title={own ? `Four eyes: ${x.requested_by} asked for it, so a second person decides` : why(canDecide)}
                onClick={() => run(shares.decide(x.id, true, by, '', !!grant[x.id]), (r) => `Share to ${name(r.grantee_team)} approved: ${r.status}.`)} data-testid="share-approve">Approve</button>
              <button className="btn sm ghost" disabled={!by || !canDecide?.allow || !!own} title={why(canDecide)} onClick={() => run(shares.decide(x.id, false, by), () => 'Share rejected.')} data-testid="share-reject">Reject</button></div>}

          </div>
        })}
        {!(s.data ?? []).length && s.data && <span className="helper">Not shared with any other team.</span>}
        <div className="gov-form"><span className="micro-label sec-label">share with another team</span><div className="form-row">
          <Field label="team"><select className="select" value={f.grantee} onChange={(e) => setF({ ...f, grantee: e.target.value })} data-testid="share-team"><option value="">choose…</option>{(teams.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
          <Field label="versions"><select className="select" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value as Share['scope'] })} data-testid="share-scope" title="which versions the share covers">
            <option value="certified">certified versions</option><option value="default">the default version</option><option value="all">every version</option></select></Field>
          <Field label="PII"><select className="select" value={f.pii} onChange={(e) => setF({ ...f, pii: e.target.value as Share['pii'] })} data-testid="share-pii"><option value="masked">masked</option><option value="unmasked">unmasked</option></select></Field>
          <Field label="reason" grow><input className="input" style={{ minWidth: 120 }} placeholder="what they will use it for" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} data-testid="share-reason" /></Field>
          <button className="btn sm" disabled={!by || !canShare?.allow || !f.grantee || f.reason.trim().length < 3} title={why(canShare)} onClick={() => run(shares.create({ object_id: root, grantee_team: f.grantee, scope: f.scope, pii: f.pii, reason: f.reason.trim() }, by), (r) => `Share to ${name(r.grantee_team)} requested: an owner other than you decides.`)} data-testid="share-request">Request share</button>
        </div>{canShare && !canShare.allow && <Why>{why(canShare)}</Why>}</div>
        {msg && <span className="helper up-err" role="alert" data-testid="share-msg">✕ {msg}</span>}
      </div>
    </div>
  )
}

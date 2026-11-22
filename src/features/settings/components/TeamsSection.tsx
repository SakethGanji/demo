import { useEffect, useState } from 'react'
import { authz, holds, notServed, refusal, teamsApi, type AuthzDecision, type LegalHold, type Member, type Role, type Team } from '@/shared/api/governance'
import { useApi } from '@/shared/api/mode'
import { useFetch } from '@/shared/hooks/useFetch'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { useTeam } from '@/shared/lib/team'
import { Badge, CardHead, ConfirmButton, CopyId, Field, Flash, Why } from '@/shared/components/DataKit'

/** U2 (C §2.1, §4.2; plan §6.4): Settings › Teams. The teams you are in, New team (greyed out by /authz/check team.create
 *  unless you are a platform admin or the policy allows self-service), and the selected team's members and roles, its
 *  policy and its legal holds (P24). Every greyed button says why, from the API's own decision. */

const ROLES: Role[] = ['viewer', 'member', 'maintainer', 'owner', 'risk_reviewer']
import { when, day } from '@/shared/lib/format'
const ROLE_TONE: Record<string, string> = { owner: 'active', maintainer: 'confirmed', risk_reviewer: 'suggested', member: 'none', viewer: 'none' }
const RoleBadge = ({ role }: { role: string }) => <Badge status={ROLE_TONE[role] ?? 'none'}>{role.replace(/_/g, ' ')}</Badge>
const why = (d: AuthzDecision | null | undefined) => (!d ? 'checking your permission…' : d.allow ? '' : `${d.reason ?? 'not allowed'}${d.needed ? ` (needs ${d.needed}${d.role ? `; you are ${d.role}` : ''})` : ''}`)
const errLine = (e: unknown) => { const r = refusal(e); return `${r.message}${r.code ? ` (${r.code})` : ''}` }

/** The API's decision on one action, re-asked when the actor or object changes. */
function useCan(action: string, kind?: string | null, id?: string | null, actor?: string) {
  const [d, setD] = useState<AuthzDecision | null>(null)
  useEffect(() => {
    let live = true
    setD(null)
    if (!actor) return
    authz.check(action, kind, id).then((r) => live && setD(r)).catch((e) => live && setD({ action, allow: false, reason: refusal(e).message, mode: '?' }))
    return () => { live = false }
  }, [action, kind, id, actor])
  return d
}

/** §6.4 defaults: what a key means when the team policy does not set it. */
const POLICY_DEFAULTS: Record<string, unknown> = {
  // P24: retention is manual and has no default period: nothing is a candidate until the team sets one
  retention: null,
  certify_days: 365, maintainer_can_review: false, require_independent_reviewer: false, require_certified_inputs: false, self_service_teams: false, discoverable: true,
}

export function TeamsSection() {
  const { mode } = useApi()
  const { actor, team: current } = useTeam()
  const [tick, setTick] = useState(0)
  const list = useFetch(mode === 'live' && actor ? () => teamsApi.list() : null, [mode, actor, tick])
  const canCreate = useCan('team.create', null, null, mode === 'live' ? actor : '')
  const [sel, setSel] = useState<string | null>(null)
  const [creating, setCreating] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const mine = (list.data ?? []).filter((t) => t.my_role)
  const selected = mine.find((t) => t.id === sel) ?? mine.find((t) => t.id === current?.id) ?? mine[0] ?? null

  if (mode !== 'live') return <div className="card card-body helper" data-testid="settings-teams">Teams, members, policies and legal holds live in the API: switch to live mode to manage them.</div>
  if (!actor) return <div className="card card-body helper" data-testid="settings-teams">Type your name in the sidebar first: teams belong to people.</div>

  const create = () => {
    const name = (creating ?? '').trim()
    if (!name) return
    teamsApi.create(name, actor).then((t) => { setMsg({ ok: true, text: `${t.name} created; you are its owner.` }); setCreating(null); setSel(t.id); setTick((x) => x + 1) })
      .catch((e) => setMsg({ ok: false, text: errLine(e) }))
  }
  return (
    <div className="stack" style={{ gap: 'var(--space-12)' }} data-testid="settings-teams">
      <div className="card">
        <CardHead level={2} title="Teams you are in" sub={<>acting as <b>{actor}</b> · pick a team for its members, policy and legal holds</>}
          actions={creating == null
            ? <button className="btn primary" disabled={!canCreate?.allow} onClick={() => { setMsg(null); setCreating('') }} title={canCreate?.allow ? 'A new team; you become its owner' : why(canCreate)} data-testid="team-new">New team</button>
            : <span className="row" style={{ gap: 6 }}>
              <input className="input" autoFocus maxLength={80} placeholder="team name" value={creating} onChange={(e) => setCreating(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && create()} style={{ width: 200 }} data-testid="team-new-name" />
              <button className="btn" disabled={!creating.trim()} onClick={create} data-testid="team-new-create">Create</button>
              <button className="btn ghost" onClick={() => setCreating(null)}>Cancel</button></span>} />
        {canCreate && !canCreate.allow && <p className="why why-line" data-testid="team-new-why"><span aria-hidden>⊘</span> New team: {why(canCreate)}.</p>}
        <Flash msg={msg} testId="teams-msg" />
        {list.err && !list.data ? <ErrorCard inline err={list.err} /> : !list.data ? <Loading inline>Reading your teams…</Loading> : (
          <div className="table-wrap"><table className="table" data-testid="teams-table">
            <thead><tr><th>team</th><th>your role</th><th className="r">members</th><th className="r">owners</th><th className="r">projects</th><th>created</th></tr></thead>
            <tbody>{mine.map((t) => (
              <tr key={t.id} className={selected?.id === t.id ? 'sel' : ''} onClick={() => setSel(t.id)} style={{ cursor: 'pointer' }} data-testid={`team-row-${t.id}`}>
                <td title={`${t.slug} · ${t.id}`}><b>{t.name}</b>{t.description && <span className="sub-line">{t.description}</span>}</td>
                <td>{t.my_role ? <RoleBadge role={t.my_role} /> : null}</td>
                <td className="r mono">{t.n_members}</td><td className="r mono">{t.n_owners}</td><td className="r mono">{t.n_projects}</td>
                <td className="muted" style={{ fontSize: 12 }} title={when(t.created_at)}>{t.created_by} · {day(t.created_at)}</td>
              </tr>))}
              {!mine.length && <tr><td colSpan={6} className="helper">You are in no team yet: an owner adds you, or an admin makes one.</td></tr>}
            </tbody>
          </table></div>)}
      </div>
      {selected && <TeamDetail key={selected.id} t={selected} actor={actor} onChanged={() => setTick((x) => x + 1)} />}
    </div>
  )
}

function TeamDetail({ t, actor, onChanged }: { t: Team; actor: string; onChanged: () => void }) {
  const canManage = useCan('team.manage', 'team', t.id, actor)
  const canSettings = useCan('team.settings', 'team', t.id, actor)
  const [members, setMembers] = useState<Member[] | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [add, setAdd] = useState<{ name: string; role: Role }>({ name: '', role: 'member' })
  useEffect(() => { teamsApi.members(t.id).then(setMembers).catch((e) => setErr(errLine(e))) }, [t.id])
  const did = (p: Promise<{ members: Member[] }>, ok: string) => { setMsg(null); p.then((r) => { setMembers(r.members); setMsg({ ok: true, text: ok }); onChanged() }).catch((e) => setMsg({ ok: false, text: errLine(e) })) }
  const no = !canManage?.allow
  return (<>
    <div className="card" data-testid="team-detail" data-team={t.id}>
      <CardHead level={2} title={<>{t.name} <span className="muted" style={{ fontWeight: 400 }}>· members</span></>} badge={<CopyId value={t.id} />} sub="every change is audited in the team's chain" />
      <Flash msg={msg} testId="members-msg" />
      <div className="card-body form-row team-add">
        <Field label="add a member"><input className="input" style={{ width: 200 }} placeholder="name" maxLength={80} value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} disabled={no} data-testid="member-add-name" /></Field>
        <Field label="role"><select className="select" value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value as Role })} disabled={no} data-testid="member-add-role">{ROLES.map((r) => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}</select></Field>
        <button className="btn" disabled={no || !add.name.trim()} title={no ? why(canManage) : !add.name.trim() ? 'Type a name first' : `Add to ${t.name}`}
          onClick={() => did(teamsApi.addMember(t.id, add.name.trim(), add.role, actor), `${add.name.trim()} added as ${add.role}.`)} data-testid="member-add">Add</button>
        {no && canManage && <Why>{why(canManage)}</Why>}
      </div>
      {err && !members ? <p className="helper up-err" style={{ padding: '0 16px' }}>✕ {err}</p> : !members ? <Loading inline>Reading the members…</Loading> : (
        <div className="table-wrap"><table className="table" data-testid="members-table">
          <thead><tr><th>member</th><th>role</th><th>from</th><th>added</th><th /></tr></thead>
          <tbody>{members.map((m) => (
            <tr key={m.principal_id} data-testid={`member-${m.identity_key}`}>
              <td title={m.principal_id}><span className="avatar" aria-hidden>{m.display_name.slice(0, 1).toUpperCase()}</span><b>{m.display_name}</b>{m.is_admin && <span className="pt-chip" style={{ marginLeft: 6 }} title="platform admin (ENSEMBLE_ADMINS)">admin</span>}</td>
              <td><select className="select" value={m.role} disabled={no || m.source !== 'manual'} title={no ? why(canManage) : m.source !== 'manual' ? 'from the identity provider: read-only' : 'change the role (the last owner cannot be demoted)'}
                onChange={(e) => did(teamsApi.setRole(t.id, m.principal_id, e.target.value as Role, actor), `${m.display_name} is now ${e.target.value}.`)} data-testid={`member-role-${m.identity_key}`} aria-label={`role of ${m.display_name}`}>{ROLES.map((r) => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}</select></td>
              <td className="muted">{m.source === 'manual' ? 'added here' : m.source}</td>
              <td className="muted" style={{ fontSize: 12 }} title={when(m.added_at)}>{m.added_by} · {day(m.added_at)}</td>
              <td className="r"><ConfirmButton className="btn sm ghost remove-btn" disabled={no} title={no ? why(canManage) : `remove ${m.display_name} from ${t.name}`} testId={`member-remove-${m.identity_key}`} confirm="Remove"
                warning={`Remove ${m.display_name}?`} onConfirm={() => did(teamsApi.remove(t.id, m.principal_id, actor), `${m.display_name} removed.`)}>Remove…</ConfirmButton></td>
            </tr>))}</tbody>
        </table></div>)}
    </div>
    <div className="grid two" style={{ alignItems: 'start' }}>
      <PolicyCard t={t} actor={actor} can={canSettings} onSaved={onChanged} />
      <HoldsPanel t={t} actor={actor} />
    </div>
  </>)
}

/** §6.4: the team policy. Each key shows its value (or the default it falls back to); owners edit the JSON. */
function PolicyCard({ t, actor, can, onSaved }: { t: Team; actor: string; can: AuthzDecision | null; onSaved: () => void }) {
  const [pol, setPol] = useState<Record<string, unknown>>(t.policy ?? {})
  const [edit, setEdit] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const save = () => {
    let p: Record<string, unknown>
    try { p = JSON.parse(edit ?? '{}') } catch (e) { setMsg({ ok: false, text: `Not JSON: ${(e as Error).message}` }); return }
    if (!p || typeof p !== 'object' || Array.isArray(p)) { setMsg({ ok: false, text: 'The policy is a JSON object.' }); return }
    teamsApi.update(t.id, { policy: p }, actor).then((r) => { setPol(r.policy ?? p); setEdit(null); setMsg({ ok: true, text: 'Policy saved (audited with before and after).' }); onSaved() })
      .catch((e) => setMsg({ ok: false, text: errLine(e) }))
  }
  const show = (v: unknown) => (typeof v === 'object' ? JSON.stringify(v) : String(v))
  return (
    <div className="card" data-testid="policy-card">
      <CardHead title="Policy" sub="unset keys use the defaults"
        actions={edit == null ? <button className="btn sm" disabled={!can?.allow} title={can?.allow ? 'Edit the policy JSON' : why(can)} onClick={() => { setMsg(null); setEdit(JSON.stringify(pol, null, 2)) }} data-testid="policy-edit">Edit</button>
          : <span className="row" style={{ gap: 6 }}><button className="btn sm ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn sm primary" onClick={save} data-testid="policy-save">Save</button></span>} />
      <Flash msg={msg} />
      <div className="card-body stack" style={{ gap: 4 }}>
        {edit != null ? <textarea className="input mono" rows={12} value={edit} onChange={(e) => setEdit(e.target.value)} data-testid="policy-json" style={{ fontSize: 12 }} />
          : Object.entries(POLICY_DEFAULTS).map(([k, d]) => (
            <div key={k} className="data-card-row" data-testid={`policy-${k}`}><span className="micro-label">{k.replace(/_/g, ' ')}</span>
              <span className="mono" style={{ fontSize: 12 }}>{k in pol ? show(pol[k]) : d === null ? <span className="muted">no period set: retention is manual (plan → dry run → run), nothing is a candidate</span> : <span className="muted">{show(d)} (default)</span>}</span></div>))}
        {can && !can.allow && <Why>Editing: {why(can)}.</Why>}
      </div>
    </div>
  )
}

const KINDS: LegalHold['scope_kind'][] = ['team', 'project', 'dataset', 'version', 'run', 'model']

/** C §4.2 / P24: legal holds on this team's objects. A hold freezes every destructive path on what it covers (delete,
 *  retention, GC, purge) until a second admin releases it. Placing and releasing are a platform admin's. */
function HoldsPanel({ t, actor }: { t: Team; actor: string }) {
  const [tick, setTick] = useState(0)
  const [showReleased, setShowReleased] = useState(false)
  const [list, setList] = useState<{ data: { holds: LegalHold[]; n_active: number } | null; err: string; missing: boolean }>({ data: null, err: '', missing: false })
  useEffect(() => {
    let live = true
    holds.list({ team: t.id, include_released: showReleased }).then((d) => live && setList({ data: d, err: '', missing: false }))
      .catch((e) => live && setList({ data: null, err: errLine(e), missing: notServed(e) }))
    return () => { live = false }
  }, [t.id, tick, showReleased])
  const canPlace = useCan('legal_hold.place', null, null, actor)
  const canRelease = useCan('legal_hold.release', null, null, actor)
  const [f, setF] = useState<{ scope_kind: LegalHold['scope_kind']; scope_id: string; case_ref: string; reason: string }>({ scope_kind: 'team', scope_id: t.id, case_ref: '', reason: '' })
  const [rel, setRel] = useState<{ id: string; reason: string } | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  // TODO(P24): the API on an older head answers 404 here; the panel then says so and keeps the form disabled
  const missing = list.missing
  const off = missing || !canPlace?.allow
  const place = () => holds.place({ ...f, scope_id: f.scope_id.trim(), case_ref: f.case_ref.trim(), reason: f.reason.trim() }, actor)
    .then((h) => { setMsg({ ok: true, text: `${h.case_ref} now holds ${h.scope_kind} ${h.scope_id} (${h.id}).` }); setF({ ...f, case_ref: '', reason: '' }); setTick((x) => x + 1) })
    .catch((e) => setMsg({ ok: false, text: errLine(e) }))
  const release = () => rel && holds.release(rel.id, rel.reason.trim(), actor)
    .then((h) => { setMsg({ ok: true, text: `${h.case_ref} released (${h.id}).` }); setRel(null); setTick((x) => x + 1) })
    .catch((e) => setMsg({ ok: false, text: errLine(e) }))
  const rows = list.data?.holds ?? []
  return (
    <div className="card" data-testid="holds-panel">
      <CardHead title="Legal holds" badge={list.data ? <span data-testid="holds-active"><Badge status={list.data.n_active ? 'held' : 'none'}>{list.data.n_active} active</Badge></span> : undefined} sub="freeze every destructive path on what they cover"
        actions={<label className="row helper nowrap" style={{ gap: 4 }}><input type="checkbox" checked={showReleased} onChange={(e) => setShowReleased(e.target.checked)} /> show released</label>} />
      {missing && <p className="helper data-warn" style={{ margin: '8px 16px' }} data-testid="holds-note">Legal holds arrive with P24: the API does not serve /legal-holds yet (restart it on a head with P24).</p>}
      <Flash msg={msg} testId="holds-msg" />
      <div className="card-body stack" style={{ gap: 6 }}>
        {list.err && !list.data && !missing && <p className="helper up-err" data-testid="holds-err">✕ {list.err}</p>}
        {rows.map((h) => (
          <div key={h.id} className={`hold-row ${h.active ? 'on' : 'off'}`} data-testid={`hold-${h.id}`}>
            <div className="gov-line"><Badge status={h.active ? 'held' : 'released'}>{h.active ? 'held' : 'released'}</Badge><b>{h.case_ref}</b><span>holds {h.scope_kind}</span><span className="mono">{h.label ?? h.scope_id}</span></div>
            <span className="sub-line">“{h.reason}” · placed by {h.placed_by} {day(h.placed_at)}{h.released_at ? ` · released by ${h.released_by} ${day(h.released_at)}` : ''}</span>
            {h.active && (rel?.id === h.id
              ? <span className="row" style={{ gap: 6, marginTop: 4 }}><input className="input" style={{ flex: 1 }} placeholder="why it is released (required)" value={rel.reason} onChange={(e) => setRel({ ...rel, reason: e.target.value })} data-testid="hold-release-reason" />
                <button className="btn sm" disabled={rel.reason.trim().length < 5} onClick={release} data-testid="hold-release-ok">Release</button><button className="btn sm ghost" onClick={() => setRel(null)}>Cancel</button></span>
              : <button className="btn sm ghost" style={{ marginTop: 4 }} disabled={!canRelease?.allow} title={canRelease?.allow ? 'A second admin (not the one who placed it) releases a hold' : why(canRelease)} onClick={() => setRel({ id: h.id, reason: '' })} data-testid={`hold-release-${h.id}`}>Release…</button>)}
          </div>))}
        {list.data && !rows.length && <span className="helper">No {showReleased ? '' : 'active '}hold on {t.name}'s objects.</span>}
        <div className="gov-form"><span className="micro-label sec-label">place a hold</span><div className="form-row">
          <select aria-label="what it holds" className="select" value={f.scope_kind} disabled={off} onChange={(e) => setF({ ...f, scope_kind: e.target.value as LegalHold['scope_kind'], scope_id: e.target.value === 'team' ? t.id : '' })} data-testid="hold-kind">{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
          <input className="input mono" style={{ width: 150 }} aria-label="id" placeholder={f.scope_kind === 'model' ? 'model name or mv_…' : `${f.scope_kind} id`} value={f.scope_id} disabled={off} onChange={(e) => setF({ ...f, scope_id: e.target.value })} data-testid="hold-scope" />
          <input className="input" style={{ width: 140 }} aria-label="case reference" placeholder="case ref" value={f.case_ref} disabled={off} onChange={(e) => setF({ ...f, case_ref: e.target.value })} data-testid="hold-case" />
          <input className="input" style={{ flex: 1, minWidth: 160 }} aria-label="reason" placeholder="reason (required)" value={f.reason} disabled={off} onChange={(e) => setF({ ...f, reason: e.target.value })} data-testid="hold-reason" />
          <button className="btn sm" disabled={off || !f.scope_id.trim() || !f.case_ref.trim() || f.reason.trim().length < 5} title={!missing && canPlace && !canPlace.allow ? why(canPlace) : undefined} onClick={place} data-testid="hold-place">Place hold</button>
        </div>
        {!missing && canPlace && !canPlace.allow && <Why>Placing: {why(canPlace)}.</Why>}</div>
        <p className="helper" style={{ fontSize: 12, margin: 0, lineHeight: '17px' }}>A hold stops deletion, retention, GC and purge of everything it covers (a version also covers its runs and their models) and names the case in each refusal. Reads and new versions go on. It has no end date: a second admin releases it.</p>
      </div>
    </div>
  )
}

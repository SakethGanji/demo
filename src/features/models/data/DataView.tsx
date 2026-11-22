import { useEffect, useState } from 'react'
import { catalog, data, type DsCard, type DsDiff, type DsHistory, type DsRow, type DsUsage, type DsVersion, type MyTeam } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'
import { fmtScore } from '@/shared/lib/metrics'
import { NotesStrip } from '../components/NotesStrip'
import { ProjectTree } from '../components/ProjectTree'
import { EditVersion } from './EditVersion'
import { StagedUpload } from './StagedUpload'
import { TablesCard } from './TablesCard'
import { JoinBuilder } from './JoinBuilder'
import { Governance } from './Governance'
import { PurgeCard } from './PurgeCard'
import { shares as sharesApi, type Share } from '@/shared/api/governance'
import { readActor, saveActor, useTeam } from '@/shared/lib/team'
import './data.css'

export { readActor, saveActor }
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—')
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—')
const pct = (v?: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)} %`)
const num = (v?: number | null) => (v == null ? '—' : Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toPrecision(4).replace(/\.?0+$/, ''))
const BAND: Record<string, Status> = { stable: 'success', watch: 'warning', shift: 'danger' }
const OP: Record<string, string> = { edit: 'edit', sample_regenerated: 'sample regenerated', upload: 'upload', sample: 'sample', upload_version: 'new file', data_doctor: 'Data Doctor', clean_labels: 'relabel', feature_lab: 'Feature Lab', 'monitoring-window': 'drift window', sql: 'SQL load', refresh: 'SQL refresh' }
const opName = (op?: string | null) => (op ? OP[op] ?? op.replace(/_/g, ' ') : '—')

/** Set `?name=` in the hash route without a navigation (the other params stay). */
function setParam(name: string, v: string | null) {
  const [path, qs] = location.hash.split('?')
  const q = new URLSearchParams(qs ?? '')
  if (v) q.set(name, v); else q.delete(name)
  history.replaceState(null, '', `${path}?${q}`)
  dispatchEvent(new HashChangeEvent('hashchange'))
}

/** Build › Data (U1, C §2.9): the data catalog. Every dataset of the current team (or all), searchable; Upload (the
 *  staged check → create flow) is the primary action; with ?dataset= its page. */
export function DataView({ go }: { go: (r: string) => void }) {
  const sel = useHashParam('dataset')
  const { actor, team } = useTeam()
  const [scope, setScope] = useState<'team' | 'all'>('team')
  const [f, setF] = useState({ q: '', project: '', tag: '', owner: '', op: '', since: '', state: 'active' })
  const list = useFetch(() => data.search(f), [JSON.stringify(f)])
  const [projTick, setProjTick] = useState(0)
  const projects = useFetch(() => catalog.projects(scope === 'team' ? team?.id : null), [scope, team?.id, projTick])
  const [uploading, setUploading] = useState(false)
  const [made, setMade] = useState('')
  const ops = [...new Set((list.data ?? []).flatMap((d) => d.ops))].sort()
  const rows = (list.data ?? []).filter((d) => scope === 'all' || !team || (d.team_id ?? 'tm_default') === team.id)
  // P21: what other teams share with the current team (live), to mark those rows in the catalog
  const incoming = useFetch(() => (team ? sharesApi.list({ team: team.id, status: 'active' }) : Promise.resolve([] as Share[])), [team?.id])
  const sharedIn = (root: string) => (incoming.data ?? []).find((x) => x.object_id === root && x.grantee_team === team?.id)
  return (
    <div className="stack" style={{ gap: 'var(--space-12)' }} data-testid="data-view">
      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Data catalog</h3>
            <div className="seg" role="group" aria-label="Whose datasets">
              <button className={scope === 'team' ? 'on' : ''} onClick={() => setScope('team')} data-testid="data-scope-team">{team?.name ?? 'my team'}</button>
              <button className={scope === 'all' ? 'on' : ''} onClick={() => setScope('all')} data-testid="data-scope-all">all teams</button></div>
            <span className="helper" style={{ fontSize: 12 }}>{rows.length} dataset{rows.length === 1 ? '' : 's'}</span></div>
          <div className="data-tools">
            <NewProject team={team} onMade={(p) => { setMade(`Project ${p} created in ${team?.name ?? 'your team'}.`); setProjTick((x) => x + 1) }} />
            <button className="btn primary" disabled={!actor} onClick={() => setUploading(!uploading)} data-testid="data-upload"
              title={actor ? 'Check a file, then create a dataset from it' : 'Type your name in the sidebar first'}>Upload…</button>
          </div>
        </div>
        {made && <p className="helper data-msg ok" role="status" style={{ padding: '6px 16px', margin: 0 }}>✓ {made}</p>}
        {uploading && <StagedUpload key={projTick} by={actor} team={team} onCancel={() => setUploading(false)}
          onDone={(r, text) => { setUploading(false); setMade(text); list.reload(); setParam('dataset', r.root_id) }} />}
        <div className="card exp-filters data-filters" style={{ border: 0, boxShadow: 'none' }}>
          <label style={{ flex: 1 }}><span className="micro-label">search</span>
            <input className="input" placeholder="name, file, id, description, owner, tag…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} data-testid="data-q" /></label>
          <label><span className="micro-label">project</span>
            <select className="select" value={f.project} onChange={(e) => setF({ ...f, project: e.target.value })} data-testid="data-project">
              <option value="">all</option>{(projects.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label><span className="micro-label">tag</span><input className="input" style={{ width: 100 }} value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value })} data-testid="data-tag" /></label>
          <label><span className="micro-label">owner</span><input className="input" style={{ width: 100 }} value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} data-testid="data-owner" /></label>
          <label><span className="micro-label">made by op</span>
            <select className="select" value={f.op} onChange={(e) => setF({ ...f, op: e.target.value })} data-testid="data-op">
              <option value="">any</option>{ops.map((o) => <option key={o} value={o}>{opName(o)}</option>)}</select></label>
          <label><span className="micro-label">changed since</span><input className="input" type="date" value={f.since} onChange={(e) => setF({ ...f, since: e.target.value })} data-testid="data-since" /></label>
          <label><span className="micro-label">show</span>
            <div className="seg">{['active', 'archived', 'deleted'].map((s) => <button key={s} className={f.state === s ? 'on' : ''} onClick={() => setF({ ...f, state: s })}>{s}</button>)}</div></label>
        </div>
        {list.err && !list.data ? <ErrorCard inline err={list.err} /> : !list.data ? <Loading inline>Reading the datasets…</Loading> : (
          <div className="table-wrap data-list"><table className="table" data-testid="data-table">
            <thead><tr><th>dataset</th><th>team · project</th><th className="r">versions</th><th className="r">default · rows × cols</th><th>owner</th><th className="r">runs</th><th className="data-when">last change</th></tr></thead>
            <tbody>
              {rows.map((d) => <DsListRow key={d.root_id} d={d} share={sharedIn(d.root_id)} on={sel != null && (sel === d.root_id || sel === d.default_version_id)} pick={() => setParam('dataset', d.root_id)} />)}
              {!rows.length && <tr><td colSpan={7} className="helper" style={{ padding: 16 }}>No dataset{scope === 'team' && team ? ` of ${team.name}` : ''} matches. Upload one.</td></tr>}
            </tbody>
          </table></div>)}
      </div>
      {sel ? <DatasetPage key={sel} id={sel} go={go} team={team} ownerTeam={(list.data ?? []).find((d) => d.root_id === sel || d.default_version_id === sel)?.team_id ?? null} onChanged={list.reload} />
        : <p className="helper">Pick a dataset to see its versions, who made each one and how, what changed, and which runs and models used it.</p>}
    </div>
  )
}

/** P18/U1: a project is made in the current team (team_id sent, so a person in 2+ teams gets no 400). */
export function NewProject({ team, onMade }: { team: MyTeam | null; onMade: (id: string, name: string) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')
  const by = readActor()
  if (!open) return <button className="btn" disabled={!by} onClick={() => setOpen(true)} data-testid="project-new" title={by ? `A new project in ${team?.name ?? 'your team'}` : 'Type your name in the sidebar first'}>New project…</button>
  const go = () => catalog.createProject(name.trim(), by, team?.id ?? null).then((p) => { setOpen(false); setName(''); setMsg(''); onMade(p.id, p.name) }).catch((e) => setMsg((e as Error).message))
  return (
    <span className="row" style={{ gap: 6 }} data-testid="project-new-form">
      <input className="input" placeholder={`project name in ${team?.name ?? 'your team'}`} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} style={{ width: 200 }} data-testid="project-new-name" />
      <button className="btn" disabled={!name.trim()} onClick={go} data-testid="project-new-create">Create</button>
      <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
      {msg && <span className="helper" style={{ color: 'var(--status-danger)' }}>{msg}</span>}
    </span>
  )
}

const f_state = (d: DsRow) => `${d.versions_in_state!.length} version${d.versions_in_state!.length === 1 ? '' : 's'} ${d.state === 'active' ? 'in that state' : ''}`.trim()

function DsListRow({ d, on, pick, share }: { d: DsRow; on: boolean; pick: () => void; share?: Share }) {
  return (
    <tr className={on ? 'sel' : ''} onClick={pick} style={{ cursor: 'pointer' }} data-testid={`data-row-${d.root_id}`}>
      <td className="data-ds-cell"><b>{d.name}</b>{d.tags.map((t) => <span key={t} className="pt-tag"> #{t}</span>)}<div className="mono muted" style={{ fontSize: 11 }}>{d.root_id}{d.versions_in_state?.length ? ` · ${f_state(d)}` : ''}</div>{d.description && <div className="helper ellip">{d.description}</div>}</td>
      <td className="muted ellip-cell" title={d.team?.name}>{d.team?.name ?? 'Default team'}{(share || d.access === 'shared') && <span className="pt-chip up-pii" style={{ marginLeft: 4 }} title={share ? `shared by ${share.requested_by}, decided by ${share.decided_by}: scope ${share.scope}, PII ${share.pii}` : 'another team shared it with yours'} data-testid={`data-shared-${d.root_id}`}>shared with you{share ? ` · ${share.scope} · PII ${share.pii}` : ''}</span>}<div style={{ fontSize: 11 }}>{d.project ?? '—'}</div></td>
      <td className="r mono" title={`${d.n_live} live of ${d.n_versions}`}>{d.n_live}{d.n_versions !== d.n_live ? <span className="muted"> / {d.n_versions}</span> : ''}</td>
      <td className="r mono" title={`${d.default_version_id} · ${d.default_how}`}>{d.default_how === 'set' ? '★ ' : ''}{d.rows.toLocaleString()} × {d.n_cols ?? '—'}</td>
      <td className="ellip-cell">{d.owner || <span className="muted">{d.created_by ?? '—'}</span>}</td>
      <td className="r mono">{d.n_runs}</td>
      <td className="muted data-when" title={when(d.last_change)}>{day(d.last_change)}</td>
    </tr>
  )
}

/** One dataset: its card, versions (history), diff, use, lineage, integrity, export, archive and delete. */
function DatasetPage({ id, go, team, ownerTeam, onChanged }: { id: string; go: (r: string) => void; team: MyTeam | null; ownerTeam: string | null; onChanged: () => void }) {
  const h = useFetch(() => data.history(id), [id])
  const [by, setByState] = useState(readActor)
  const setBy = (s: string) => { setByState(s); saveActor(s) }
  const [msg, setMsg] = useState<{ ok: boolean; text: string; blockers?: string[] } | null>(null)
  const [pair, setPair] = useState<[string, string] | null>(null)
  const [focus, setFocus] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)  // DL3: the version open in the editor
  const [table, setTable] = useState<string | null>(null)  // U2 (P12): the table the diff, Edit and training use (null = primary)
  const reload = () => { h.reload(); onChanged() }
  const fail = (e: unknown) => {
    const t = (e as Error).message
    try { const j = JSON.parse(t) as { message?: string; blockers?: string[] }; if (j.blockers) return setMsg({ ok: false, text: j.message ?? t, blockers: j.blockers }) } catch { /* plain text */ }
    setMsg({ ok: false, text: t })
  }
  const act = <T,>(p: Promise<T>, ok: (r: T) => string) => { setMsg(null); return p.then((r) => { setMsg({ ok: true, text: ok(r) }); reload(); return r }).catch(fail) }
  useEffect(() => {
    if (!h.data || pair) return
    const live = h.data.versions.filter((v) => v.state !== 'deleted')
    const dv = live.find((v) => v.id === h.data!.default_version_id)
    const par = dv && live.find((v) => v.id === dv.parent_id)
    if (dv && par) setPair([par.id, dv.id])  // DL2: parent → child of the default
    else if (live.length >= 2) setPair([live[live.length - 2].id, live[live.length - 1].id])
    setFocus(h.data.default_version_id)
  }, [h.data]) // eslint-disable-line react-hooks/exhaustive-deps
  if (h.err && !h.data) return <ErrorCard err={h.err} />
  if (!h.data) return <Loading>Reading the versions…</Loading>
  const d = h.data
  const vOf = (vid: string | null) => d.versions.find((v) => v.id === vid)
  const def = vOf(d.default_version_id)
  const focusV = vOf(focus) ?? def
  return (
    <div className="stack" style={{ gap: 'var(--space-12)' }} data-testid="data-page" data-root={d.root_id}>
      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title" data-testid="data-name">{d.name}</h3>
            <span className="mono muted">{d.root_id}</span>
            {d.project && <span className="pt-chip">{d.project.name}</span>}
            <span className="pt-chip">{d.versions.length} version{d.versions.length === 1 ? '' : 's'}</span>
            {def && <span className="champ-badge" title={d.default_how} data-testid="data-default">★ default v{def.v}{d.default_how === 'set' ? '' : ' (latest)'}</span>}
            {d.state && <StatusPill status="warning">{d.state}</StatusPill>}
          </div>
          <label className="row" style={{ gap: 6 }}><span className="micro-label">acting as</span>
            <input className="input" style={{ width: 120 }} value={by} maxLength={80} onChange={(e) => setBy(e.target.value)} data-testid="data-by" title="Names, not authenticated identities: every action records it in the audit log" /></label>
        </div>
        <NextVersion d={d} by={by} team={team} onDone={(vid, text) => { setMsg({ ok: true, text }); setPair([d.default_version_id ?? d.versions[d.versions.length - 1].id, vid]); reload() }} />
        {msg && <div className={`card-body data-msg ${msg.ok ? 'ok' : 'bad'}`} role={msg.ok ? 'status' : 'alert'} data-testid="data-msg">{msg.ok ? '✓' : '✕'} {msg.text}
          {msg.blockers && <ul>{msg.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}</div>}
      </div>

      <Versions d={d} pair={pair} setPair={setPair} focus={focus} setFocus={setFocus} by={by}
        onDefault={(v) => act(data.setDefault(d.root_id, v.id, by), (r) => `${r.rollback ? 'Rolled back' : 'Default set'} to v${r.v} (${v.id}). ${r.note}.`)}
        onVerify={(v) => act(data.verify(v.id, by), (r) => r.results.map((x) => `v${x.v}: ${x.status}`).join(' · '))}
        onVerifyAll={() => act(data.verify(d.root_id, by, true), (r) => `${r.ok ? 'Every file matches its hash' : 'A file does not match its hash'}: ${r.results.map((x) => `v${x.v} ${x.status}`).join(' · ')}`)}
        onArchive={(v, a) => act(data.archive(v.id, a, by, '', v.id === d.root_id), (r) => `${r.ids.join(', ')}: ${r.state}.`)}
        onEdit={(v) => { setMsg(null); setEditing(v.id) }}
        onAdopt={(v, reason) => act(data.adoptDiskFile(v.id, by, reason), (r) => `The file on disk is now a new version (${r.id}) of v${v.v}, with its own hash. v${v.v} stays flagged until its file is restored.`)}
        onRestoreSplits={(v) => act(data.restoreSplits(v.id, by, 'split file changed outside the app; restored as drawn'), (r) => `Restored ${r.restored.join(', ') || 'nothing'} as drawn; verify: ${r.verify.results.map((x) => `v${x.v} ${x.status}`).join(' · ')}.`)} />

      {focusV && focusV.state !== 'deleted' && <TablesCard key={focusV.id} root={d.root_id} vid={focusV.id} v={focusV.v} table={table} setTable={setTable} team={team} ownerTeam={ownerTeam}
        target={focusV.target} by={by} go={go} onVersion={(vid, text) => { setMsg({ ok: true, text }); setPair([focusV.id, vid]); setFocus(vid); reload() }} />}

      {editing && vOf(editing) && <EditVersion key={`${editing}:${table ?? ''}`} d={d} v={vOf(editing)!} table={table} by={by} onClose={() => setEditing(null)} onErr={fail}
        onCreated={(vid, text) => { setMsg({ ok: true, text }); setEditing(null); setPair([editing, vid]); setFocus(vid); reload() }} />}

      {pair && pair.every((x) => vOf(x)?.state !== 'deleted') && <DiffCard key={`${pair.join('→')}:${table ?? ''}`} a={pair[0]} b={pair[1]} table={table} name={(x) => { const v = vOf(x); return v ? `v${v.v}` : x }} />}

      {focusV && focusV.state !== 'deleted' && <JoinBuilder key={`j:${focusV.id}`} root={d.root_id} vid={focusV.id} v={focusV.v} team={team} ownerTeam={ownerTeam} target={focusV.target} by={by} go={go} />}

      <div className="grid two">
        <Usage id={focus ?? d.default_version_id ?? d.root_id} v={vOf(focus)?.v} go={go} />
        <CardEditor d={d} by={by} onSaved={() => { setMsg({ ok: true, text: 'Card saved (audited).' }); reload() }} onErr={fail} />
      </div>
      {focusV && focusV.state !== 'deleted' && <Governance d={d} vid={focusV.id} v={focusV.v} table={table} by={by} onChanged={(text) => { setMsg({ ok: true, text }); reload() }} />}
      {focusV && focusV.state !== 'deleted' && <PurgeCard key={`pg:${focusV.id}`} d={d} vid={focusV.id} v={focusV.v} table={table} by={by} onChanged={(text) => { setMsg({ ok: true, text }); reload() }} />}
      <NotesStrip kind="dataset" id={d.root_id} />
      <div className="card card-body"><h3 className="card-title" style={{ marginBottom: 8 }}>Lineage</h3><ProjectTree kind="dataset" id={d.root_id} go={go} compact /></div>
      <div className="grid two">
        <RemoveCard d={d} by={by} act={act} />
        <AuditCard d={d} />
      </div>
    </div>
  )
}

/** U1: a new file as the next version of the default: the staged check, the row match with its counts, and Create. */
function NextVersion({ d, by, team, onDone }: { d: DsHistory; by: string; team: MyTeam | null; onDone: (vid: string, text: string) => void }) {
  const [open, setOpen] = useState(false)
  const parent = d.versions.find((v) => v.id === d.default_version_id)
  if (!parent) return <div className="card-body helper data-upload">No active version to build on: every version is archived or deleted{d.state === 'archived' ? ' (unarchive the dataset below to add one)' : ''}.</div>
  return open ? <div className="data-upload"><StagedUpload by={by} team={team} parent={{ id: parent.id, v: parent.v, name: d.name, root: d.root_id, nextV: Math.max(...d.versions.map((v) => v.v)) + 1 }} onCancel={() => setOpen(false)} onDone={(r, text) => { setOpen(false); onDone(r.version_id, text) }} /></div>
    : <div className="card-body row data-upload" style={{ gap: 8 }}>
      <button className="btn primary" disabled={!by} onClick={() => setOpen(true)} data-testid="data-upload-version" title={by ? `Check a file as the next version, with v${parent.v} as its parent (nothing is written yet)` : 'Type your name in “acting as” first'}>Upload a new version…</button>
      <span className="helper">parent: v{parent.v} (the default). You choose how its rows match; to change cells, use Edit on a version.</span></div>
}

function Versions({ d, pair, setPair, focus, setFocus, by, onDefault, onVerify, onVerifyAll, onArchive, onEdit, onAdopt, onRestoreSplits }: {
  d: DsHistory; pair: [string, string] | null; setPair: (p: [string, string]) => void; focus: string | null; setFocus: (id: string) => void; by: string
  onDefault: (v: DsVersion) => void; onVerify: (v: DsVersion) => void; onVerifyAll: () => void; onArchive: (v: DsVersion, archived: boolean) => void
  onEdit: (v: DsVersion) => void; onAdopt: (v: DsVersion, reason: string) => void; onRestoreSplits: (v: DsVersion) => void
}) {
  const def = d.versions.find((v) => v.id === d.default_version_id)
  return (
    <div className="card" data-testid="data-versions">
      <div className="card-head"><div className="left"><h3 className="card-title">Versions</h3><span className="helper" style={{ fontSize: 12 }}>A and B pick the diff · click a row for what used it</span></div>
        <button className="btn sm" disabled={!by} onClick={onVerifyAll} title="Re-hash every version's file against its stored sha256" data-testid="data-verify-all">Check every hash</button></div>
      <div className="table-wrap"><table className="table data-versions">
        <thead><tr><th title="diff from">A</th><th title="diff to">B</th><th>version · made by</th><th>who · when</th><th>changes</th><th className="r">rows × cols</th><th>sha256</th><th>used by</th></tr></thead>
        <tbody>{d.versions.map((v) => {
          const gone = v.state === 'deleted'
          const par = d.versions.find((p) => p.id === v.parent_id)
          return (
            <tr key={v.id} className={`${focus === v.id ? 'sel' : ''} ${v.state ? 'muted' : ''}`} onClick={() => setFocus(v.id)} style={{ cursor: 'pointer' }} data-testid={`data-v-${v.v}`} data-id={v.id}>
              <td onClick={(e) => e.stopPropagation()}><input type="radio" name="dsA" disabled={gone} checked={pair?.[0] === v.id} onChange={() => setPair([v.id, pair?.[1] ?? v.id])} aria-label={`A = v${v.v}`} /></td>
              <td onClick={(e) => e.stopPropagation()}><input type="radio" name="dsB" disabled={gone} checked={pair?.[1] === v.id} onChange={() => setPair([pair?.[0] ?? v.id, v.id])} aria-label={`B = v${v.v}`} /></td>
              <td><b className="mono">v{v.v}</b>{v.is_default && <span className="champ-badge" style={{ marginLeft: 4 }} title="the default: what Build opens">★ default</span>}
                {v.state && <span className={`pt-chip ${gone ? 'data-rm' : ''}`} style={{ marginLeft: 4 }} title={`${v.state} ${when(v.state_at)} by ${v.state_by}${gone ? ': files removed, tombstone and lineage kept' : ''}`}>{v.state}</span>}
                <span className="muted"> · {opName(v.op)}{par ? ` from v${par.v}` : ''}{v.paired_with_parent === false ? ' ◇' : ''}</span>
                <div className="muted mono" style={{ fontSize: 11 }}>{v.id}</div></td>
              <td>{v.created_by ?? '—'}<div className="muted" style={{ fontSize: 11 }}>{when(v.created_at)}</div></td>
              <td className="data-changes">{changeLine(v)}{v.edit_ops?.length ? <div className="muted" title={v.edit_ops.map((o) => o.summary).join('\n')} data-testid={`data-ops-${v.v}`}>{v.edit_ops.length} edit{v.edit_ops.length === 1 ? '' : 's'}: {v.edit_ops.slice(0, 2).map((o) => o.summary).join('; ')}{v.edit_ops.length > 2 ? '…' : ''}</div> : null}{v.reason && <div className="muted" title="reason / change ticket" data-testid={`data-reason-${v.v}`}>“{v.reason}”</div>}{v.warnings.length > 0 && <div title={v.warnings.map((w) => w.message).join('\n')} style={{ color: 'var(--status-warning)' }}>⚠ {v.warnings.length} warning{v.warnings.length === 1 ? '' : 's'}</div>}</td>
              <td className="r mono">{v.rows.toLocaleString()} × {v.n_cols ?? '—'}</td>
              <td className="mono" title={v.sha256 ?? ''}>{v.sha256?.slice(0, 10) ?? '—'}{integrity(v)}</td>
              <td className="mono" style={{ fontSize: 12 }}>{v.used_by.runs} run{v.used_by.runs === 1 ? '' : 's'}{v.used_by.versions ? ` · ${v.used_by.versions} model${v.used_by.versions === 1 ? '' : 's'}` : ''}{v.used_by.champion && <div><span className="champ-badge">champion</span></div>}</td>
            </tr>
          )
        })}</tbody>
      </table></div>
      <VersionActions v={d.versions.find((v) => v.id === focus) ?? null} def={def} root={d.root_id} by={by} onDefault={onDefault} onVerify={onVerify} onArchive={onArchive} onEdit={onEdit} onAdopt={onAdopt} onRestoreSplits={onRestoreSplits} />
      <p className="helper" style={{ fontSize: 12, padding: '6px 16px 10px' }}>Versions are made by transforms (Data Doctor, Feature Lab, relabel, the label queue, drift windows, SQL refresh), by a new file, or by an edit; every change is a new version, nothing is modified in place, and a file changed on disk outside the app fails its hash check. The default is a pointer: rolling back moves it and never changes or copies data. ◇ = new rows with no mapping to the original rows (never paired).</p>
    </div>
  )
}

/** What can be done to the selected version: one bar under the table (the page's primary action stays Upload). */
function VersionActions({ v, def, root, by, onDefault, onVerify, onArchive, onEdit, onAdopt, onRestoreSplits }: { v: DsVersion | null; def?: DsVersion; root: string; by: string
  onDefault: (v: DsVersion) => void; onVerify: (v: DsVersion) => void; onArchive: (v: DsVersion, archived: boolean) => void
  onEdit: (v: DsVersion) => void; onAdopt: (v: DsVersion, reason: string) => void; onRestoreSplits: (v: DsVersion) => void }) {
  const [adopt, setAdopt] = useState<string | null>(null)
  if (!v) return null
  const gone = v.state === 'deleted'
  const bad = v.verified_ok === 0
  return (
    <div className="card-body row data-actions" style={{ gap: 6, flexWrap: 'wrap' }} data-testid="data-actions">
      <b className="mono">v{v.v}</b>
      {gone ? <span className="muted">deleted {when(v.state_at)} by {v.state_by}: the files are gone; this row, lineage.json, the tombstone, every edge and the audit stay.</span> : <>
        {!bad && <button className="btn sm" disabled={!by} onClick={() => onEdit(v)} data-testid={`data-edit-${v.v}`} title={by ? 'Edit cells, rows and columns; the changes are saved as a new version' : 'Type your name in “acting as” first'}>Edit v{v.v}…</button>}
        {bad && <div className="data-warn" data-testid={`data-integrity-${v.v}`}><b>v{v.v}'s data or split file was changed outside the app</b> (its sha256 no longer matches; Verify hash says which): it is not trusted, and training on it is refused.
          A split file (which rows are held out) is restored as it was drawn: <button className="btn sm" disabled={!by} onClick={() => onRestoreSplits(v)} data-testid={`data-restore-splits-${v.v}`}>Restore the split as drawn</button>.
          For data.csv, restore the file, or keep what is on disk as a new, named version:
          {adopt == null ? <button className="btn sm" style={{ marginLeft: 6 }} disabled={!by} onClick={() => setAdopt('')} data-testid={`data-adopt-${v.v}`}>Create a new version from this file…</button>
            : <span className="row" style={{ gap: 6, marginTop: 4 }}><input className="input" style={{ flex: 1 }} placeholder="why (required)" value={adopt} onChange={(e) => setAdopt(e.target.value)} data-testid="data-adopt-reason" />
              <button className="btn sm" disabled={adopt.trim().length < 5} onClick={() => { onAdopt(v, adopt.trim()); setAdopt(null) }} data-testid="data-adopt-ok">Create version</button>
              <button className="btn sm ghost" onClick={() => setAdopt(null)}>Cancel</button></span>}</div>}
        {!v.is_default && !v.state && <button className="btn sm" disabled={!by} onClick={() => onDefault(v)} data-testid={`data-default-${v.v}`} title="Moves the pointer only: no data changes">{def && v.v < def.v ? `Roll back to v${v.v}` : `Make v${v.v} the default`}</button>}
        <a className="btn sm" href={data.exportUrl(v.id, by || 'unknown')} title="data.csv + lineage.json + manifest.json (hashes, the chain to the root); audited" data-testid={`data-zip-${v.v}`}>Export with lineage (.zip)</a>
        <a className="btn sm ghost" href={data.exportUrl(v.id, by || 'unknown', 'csv')} data-testid={`data-csv-${v.v}`}>CSV only</a>
        <button className="btn sm ghost" disabled={!by} onClick={() => onVerify(v)} data-testid={`data-verify-${v.v}`}>Verify hash</button>
        <button className="btn sm ghost" disabled={!by} onClick={() => onArchive(v, !v.state)} data-testid={`data-archive-${v.v}`} title={v.id === root ? 'v1 alone: later versions stay' : undefined}>{v.state === 'archived' ? 'Unarchive' : 'Archive'} v{v.v}</button>
        {v.warnings.length > 0 && <div className="data-warn" style={{ marginTop: 4 }}>{v.warnings.map((w, i) => <div key={i}>⚠ {w.message}</div>)}</div>}
      </>}
    </div>
  )
}

const changeLine = (v: DsVersion) => {
  const c = v.changes
  if (c.matched != null) return <span>{c.changed?.toLocaleString()} changed · +{c.added?.toLocaleString()} · −{c.removed?.toLocaleString()}</span>
  if (c.rows_before != null && c.rows_after != null && v.parent_id) return <span>{c.rows_before.toLocaleString()} → {c.rows_after.toLocaleString()} rows{v.n_changes ? ` · ${v.n_changes} change${v.n_changes === 1 ? '' : 's'}` : ''}</span>
  return <span className="muted">{v.parent_id ? `${v.n_changes ?? 0} changes` : 'original'}</span>
}

const integrity = (v: DsVersion) => v.verified_ok == null || v.state === 'deleted' ? null
  : v.verified_ok ? <span className="data-ok" title={`file matches the stored hash · checked ${when(v.verified_at)}`}> ✓</span>
    : <span className="data-bad" title={`file does NOT match the stored hash · checked ${when(v.verified_at)}`} data-testid={`data-mismatch-${v.v}`}> ✕ mismatch</span>

function DiffCard({ a, b, name, table }: { a: string; b: string; name: (id: string) => string; table: string | null }) {
  const { data: df, err } = useFetch<DsDiff | null>(a === b ? () => Promise.resolve(null) : () => data.diff(a, b, table), [a, b, table])
  const [all, setAll] = useState(false)
  if (a === b) return <div className="card card-body helper">Pick two different versions in A and B to diff them.</div>
  if (err && !df) return <ErrorCard err={err} />
  if (!df) return <Loading>Diffing {name(a)} → {name(b)}{table ? ` (table ${table})` : ''}…</Loading>
  const s = df.schema, r = df.rows
  // new, gone and retyped columns and any PSI ≥ 0.01 first, then the largest PSIs, 10 in all (the rest on demand)
  const key = (c: DsDiff['columns'][number]) => c.in !== 'both' || c.kind.includes('→') || (c.psi ?? 0) >= 0.01
  const cols = all ? df.columns : [...df.columns.filter(key), ...df.columns.filter((c) => !key(c))].slice(0, Math.max(10, df.columns.filter(key).length))
  return (
    <div className="card" data-testid="data-diff">
      <div className="card-head"><div className="left"><h3 className="card-title">Diff {name(a)} → {name(b)}{table ? <span className="mono" data-testid="diff-table"> · {table}</span> : null}</h3><span className="helper" style={{ fontSize: 12 }}>rows matched by {r.matched_by}</span></div>
        {df.tables && df.tables.length > 1 && <span className="helper diff-tables" data-testid="diff-tables">{df.tables.map((t) => <span key={t.table} className={`pt-chip ${t.status === 'unchanged' ? '' : 'data-type'}`} title={`${t.a_sha256?.slice(0, 10) ?? '—'} → ${t.b_sha256?.slice(0, 10) ?? '—'} · rows ${t.rows.map((x) => x?.toLocaleString() ?? '—').join(' → ')}`}>{t.table} {t.status}</span>)}</span>}</div>
      <div className="card navy explain-hero data-hero">
        <div><span className="micro-label">rows</span><b>{r.a.toLocaleString()} → {r.b.toLocaleString()}</b></div>
        <div><span className="micro-label">changed</span><b data-testid="diff-changed">{r.changed.toLocaleString()}</b></div>
        <div><span className="micro-label">added</span><b data-testid="diff-added">+{r.added.toLocaleString()}</b></div>
        <div><span className="micro-label">removed</span><b data-testid="diff-removed">−{r.removed.toLocaleString()}</b></div>
        <div><span className="micro-label">columns</span><b>{s.n_columns[0]} → {s.n_columns[1]}</b></div>
      </div>
      <div className="card-body stack" style={{ gap: 6 }}>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }} data-testid="diff-schema">
          {s.columns_added.map((c) => <span key={c} className="pt-chip data-add">+ {c}</span>)}
          {s.columns_removed.map((c) => <span key={c} className="pt-chip data-rm">− {c}</span>)}
          {s.renamed?.map((x) => <span key={x.from} className="pt-chip data-type">renamed {x.from} → {x.to}</span>)}
          {s.type_changes.map((t) => <span key={t.column} className="pt-chip data-type">{t.column}: {t.kind_from} → {t.kind_to}</span>)}
          {!s.columns_added.length && !s.columns_removed.length && !s.type_changes.length && !s.renamed?.length && <span className="helper">same columns and types</span>}
        </div>
        {df.edit && <div className="data-preview" data-testid="diff-edit"><b>Edited by {df.edit.by}{df.edit.reason ? <span className="muted" style={{ fontWeight: 400 }}> · “{df.edit.reason}”</span> : null}</b>
          <ol className="edit-ops">{df.edit.ops.map((o) => <li key={o.i}>{o.summary}{o.examples.length > 0 && <div className="helper" style={{ fontSize: 12 }}>{o.examples.slice(0, 4).map((x, i) => <span key={i} className="data-cell">row {String(x.row)}{x.column ? <> <span className="mono">{x.column}</span> {String(x.old ?? '—')} → <b>{String(x.new ?? '—')}</b></> : x.deleted ? ' deleted' : x.added ? ' added' : ''}</span>)}{o.examples_truncated ? '…' : ''}</div>}</li>)}</ol></div>}
        {df.checks.length > 0 && <div className="data-warn">{df.checks.map((w, i) => <div key={i}>⚠ {w.message}</div>)}</div>}
        {r.changed_examples.length > 0 && <details><summary className="micro-label">{r.changed_examples.length} changed rows, cell by cell</summary>
          <table className="table"><tbody>{r.changed_examples.map((x) => <tr key={x.row_b}><td className="mono">{x.key != null ? String(x.key) : `row ${x.row_b}`}</td>
            <td>{x.cells.map((c) => <span key={c.column} className="data-cell"><span className="mono">{c.column}</span> {String(c.a)} → <b>{String(c.b)}</b></span>)}</td></tr>)}</tbody></table></details>}
      </div>
      <div className="table-wrap"><table className="table" data-testid="diff-columns">
        <thead><tr><th>column</th><th>missing A → B</th><th>mean A → B</th><th>median / top value A → B</th><th className="r">PSI</th></tr></thead>
        <tbody>{cols.map((c) => (
          <tr key={c.column}>
            <td className="mono">{c.column}{c.in !== 'both' && <span className={`pt-chip ${c.in === 'b' ? 'data-add' : 'data-rm'}`} style={{ marginLeft: 4 }}>{c.in === 'b' ? 'new' : 'gone'}</span>} <span className="muted">{c.kind}</span></td>
            <td className="mono">{pct(c.missing_a)} → {pct(c.missing_b)}</td>
            <td className="mono">{c.kind === 'number' ? `${num(c.mean_a)} → ${num(c.mean_b)}` : '—'}</td>
            <td className="mono">{c.kind === 'number' ? `${num(c.median_a)} → ${num(c.median_b)}` : `${c.top_a?.[0] ? `${c.top_a[0].value} ${pct(c.top_a[0].share)}` : '—'} → ${c.top_b?.[0] ? `${c.top_b[0].value} ${pct(c.top_b[0].share)}` : '—'}`}</td>
            <td className="r">{c.psi == null ? '—' : <span title={c.note}><StatusPill status={BAND[c.band ?? ''] ?? 'neutral'}>{c.psi.toFixed(3)} {c.band}</StatusPill></span>}</td>
          </tr>))}</tbody>
      </table></div>
      <p className="helper" style={{ fontSize: 12, padding: '6px 16px 10px' }}>{r.note}. PSI: &lt; 0.10 stable, 0.10–0.25 watch, &gt; 0.25 shift (A's deciles or top categories as the bins). {!all && df.columns.length > cols.length && <button className="chip-btn" onClick={() => setAll(true)}>Show all {df.columns.length} columns</button>}</p>
    </div>
  )
}

function Usage({ id, v, go }: { id: string; v?: number; go: (r: string) => void }) {
  const { data: u, err } = useFetch<DsUsage>(() => data.usage(id), [id])
  return (
    <div className="card" data-testid="data-usage">
      <div className="card-head"><div className="left"><h3 className="card-title">Used by {v ? `v${v}` : id}</h3><span className="helper" style={{ fontSize: 12 }}>and the versions derived from it</span></div></div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {err && !u ? <span className="helper">{err}</span> : !u ? <span className="helper">…</span> : <>
          {u.champions.filter((c) => c.status === 'approved').map((c) => <div key={c.id}><span className="champ-badge">★ champion</span> of {c.project_id} · run <span className="mono">{c.run_id}</span></div>)}
          {u.versions.map((x) => <div key={x.id}>◆ <button className="linkish" onClick={() => go(`registry?version=${x.id}`)}>{x.model_name} v{x.version}</button> <span className="pt-chip">{x.stage}</span> <span className={`pt-chip appr-${x.approval}`}>{x.approval}</span>{x.retired && <span className="pt-chip" title={`retired ${when(x.retired_at)}`}>retired</span>} <span className="muted mono">{x.dataset_id}</span></div>)}
          {u.runs.map((r) => <div key={r.id} data-testid={`usage-run-${r.id}`}>● <button className="linkish" onClick={() => go(`models?run=${r.id}`)}>{r.name}</button> <span className="mono muted">{r.id} · {r.dataset_id}</span> <span className="mono">{r.status !== 'succeeded' ? r.status : r.primary_metric ? `${r.primary_metric} ${fmtScore(r.primary_score, 4)}` : ''}</span>
            {r.via_join && <span className="pt-chip" style={{ marginLeft: 4 }} title={`P14: read through join frame ${r.via_join.join_frame_id} (${r.via_join.relation})`} data-testid="usage-via-join">via join {r.via_join.join_frame_id} · {r.via_join.tables.join(', ')}{r.via_join.relation === 'merged_from' ? ' (another dataset)' : ''}</span>}</div>)}
          {(u.join_frames ?? []).map((f) => <div key={f.id} data-testid="usage-frame">⋈ join frame <span className="mono">{f.id}</span> <span className="pt-chip">{f.relation.replace('_', ' ')}: {f.tables.join(', ')}</span> <span className="helper">its runs are listed above; deleting this version is refused while one of them needs it</span></div>)}
          {!u.runs.length && <span className="helper">No run has trained on it.</span>}
          {u.blocks_archive.length > 0 ? <p className="helper" style={{ fontSize: 12 }}>Can't be archived or deleted while: {u.blocks_archive.join(' ')}</p>
            : u.blocks_archive_or_delete.length > 0 && <p className="helper" style={{ fontSize: 12 }}>Can be archived. Can't be deleted yet: {u.blocks_archive_or_delete.join(' ')}</p>}
        </>}
      </div>
    </div>
  )
}

const CARD: [keyof DsCard, string, string][] = [
  ['description', 'Description', 'What the table is, one row per…'], ['owner', 'Owner', 'who answers for it'], ['source', 'Source', 'system, extract, query'],
  ['label_definition', 'Label definition', 'what the target means, its window'], ['known_issues', 'Known issues', 'gaps, quirks, caveats'],
]

function CardEditor({ d, by, onSaved, onErr }: { d: DsHistory; by: string; onSaved: () => void; onErr: (e: unknown) => void }) {
  const [edit, setEdit] = useState<Partial<DsCard> & { name?: string } | null>(null)
  const c = d.card
  return (
    <div className="card" data-testid="data-card">
      <div className="card-head"><div className="left"><h3 className="card-title">Dataset card</h3>{c.updated_by && <span className="helper" style={{ fontSize: 12 }}>{c.updated_by} · {when(c.updated_at)}</span>}</div>
        {!edit ? <button className="btn sm" disabled={!by} onClick={() => setEdit({ name: d.name, ...c })} data-testid="data-card-edit">Edit</button>
          : <span className="row" style={{ gap: 6 }}><button className="btn sm" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn sm" onClick={() => { const { name, ...rest } = edit; data.editCard(d.root_id, { ...(name !== d.name ? { name } : {}), ...Object.fromEntries(CARD.map(([k]) => [k, (rest[k] as string) ?? ''])) }, by).then(() => { setEdit(null); onSaved() }).catch(onErr) }} data-testid="data-card-save">Save</button></span>}</div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {edit && <label className="stack" style={{ gap: 2 }}><span className="micro-label">Name</span><input className="input" value={edit.name ?? ''} maxLength={120} onChange={(e) => setEdit({ ...edit, name: e.target.value })} data-testid="data-card-name" /></label>}
        {CARD.map(([k, label, ph]) => edit
          ? <label key={k} className="stack" style={{ gap: 2 }}><span className="micro-label">{label}</span>
            <textarea className="input" rows={k === 'description' || k === 'known_issues' ? 2 : 1} placeholder={ph} value={(edit[k] as string) ?? ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} data-testid={`data-card-${k}`} /></label>
          : <div key={k} className="data-card-row"><span className="micro-label">{label}</span><span className={c[k] ? '' : 'muted'}>{(c[k] as string) || '—'}</span></div>)}
      </div>
    </div>
  )
}

function RemoveCard({ d, by, act }: { d: DsHistory; by: string; act: <T>(p: Promise<T>, ok: (r: T) => string) => Promise<T | void> }) {
  const [reason, setReason] = useState('')
  const [target, setTarget] = useState(d.root_id)
  const [freeze, setFreeze] = useState(false)
  const only = target === `${d.root_id}::only`, tid = only ? d.root_id : target
  const me = (by || '').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
  const pending = d.requests.filter((r) => r.status === 'pending')
  const vOf = (id: string) => d.versions.find((v) => v.id === id)
  const label = (id: string, o = false) => (id === d.root_id && !o ? 'the whole dataset' : `v${vOf(id)?.v} (${id})`)
  return (
    <div className="card" data-testid="data-remove">
      <div className="card-head"><div className="left"><h3 className="card-title">Archive or delete</h3><span className="helper" style={{ fontSize: 12 }}>refused while a registered model or a champion comes from it</span></div></div>
      <div className="card-body stack" style={{ gap: 8 }}>
        {pending.map((r) => (
          <div key={r.id} className="data-warn" data-testid={`data-req-${r.id}`}>Delete {label(r.dataset_id)} asked by <b>{r.requested_by}</b> · {when(r.requested_at)} · “{r.reason}”
            <div className="row" style={{ gap: 6, marginTop: 4 }}>
              <button className="btn sm" disabled={!by} onClick={() => act(data.decideDelete(r.id, true, by), () => `Deleted ${label(r.dataset_id)}: files removed, tombstone and lineage kept.`)} data-testid="data-req-approve">Approve delete</button>
              {me && me === r.requested_by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
                ? <button className="btn sm ghost" onClick={() => act(data.decideDelete(r.id, false, by, '', true), () => 'Your delete request was withdrawn.')} data-testid="data-req-withdraw">Withdraw my request</button>
                : <button className="btn sm ghost" disabled={!by} onClick={() => act(data.decideDelete(r.id, false, by), () => 'Delete rejected.')}>Reject</button>}
              <span className="helper">someone other than {r.requested_by} (admins too)</span></div></div>))}
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <select className="select" value={target} onChange={(e) => setTarget(e.target.value)} data-testid="data-remove-target">
            <option value={d.root_id}>the whole dataset</option>
            {d.versions.filter((v) => v.state !== 'deleted').map((v) => <option key={v.id} value={v.id === d.root_id ? `${d.root_id}::only` : v.id}>v{v.v} only{v.id === d.root_id ? ' (later versions stay)' : ''}</option>)}
          </select>
          <input className="input" style={{ flex: 1, minWidth: 160 }} placeholder="reason (for the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="data-remove-reason" />
        </div>
        <div className="row" style={{ gap: 6 }}>
          {target === d.root_id && <button className="btn sm" disabled={!by} onClick={() => act(data.archive(target, d.state !== 'archived', by, reason), (r) => `${label(target)}: ${r.state}.`)} data-testid="data-archive-all">{d.state === 'archived' ? 'Unarchive' : 'Archive'} the dataset</button>}
          <label className="row helper" style={{ gap: 4 }} title="Keep up to 2,000 holdout rows beside the tombstone so retired models can be re-validated. Leave off for a PII purge."><input type="checkbox" checked={freeze} onChange={(e) => setFreeze(e.target.checked)} data-testid="data-freeze" /> keep a holdout sample</label>
          <button className="btn sm danger" disabled={!by || reason.trim().length < 5} onClick={() => act(data.requestDelete(tid, by, reason, only, freeze), (r) => r.status === 'approved' ? `Deleted ${label(tid, only)} (admin).` : `Delete of ${label(tid, only)} asked: someone else must approve.`)} data-testid="data-delete">Ask to delete…</button>
        </div>
        <p className="helper" style={{ fontSize: 12, margin: 0 }}>Archive hides it from Build and the pickers; it is allowed once the only models on it are Archived or retired and none is champion. Delete needs every model on it retired for {d.retention_days.toLocaleString()} days (retention policy, needs model-risk approval) and a second person (or an admin{d.admins_configured ? '' : ': none configured'}); the files go, the hash, tombstone, lineage and audit stay. Retire models in the Registry.</p>
      </div>
    </div>
  )
}

function AuditCard({ d }: { d: DsHistory }) {
  return (
    <div className="card" data-testid="data-audit">
      <div className="card-head"><h3 className="card-title">Audit</h3><span className="helper" style={{ fontSize: 12 }}>every dataset action, newest first</span></div>
      <div className="table-wrap" style={{ maxHeight: 260, overflow: 'auto' }}><table className="table">
        <tbody>{d.audit.slice(0, 30).map((a) => (
          <tr key={a.id}><td className="muted" style={{ whiteSpace: 'nowrap', fontSize: 12 }} title={when(a.ts)}>{day(a.ts)}</td><td>{a.actor}</td>
            <td><span className="mono" style={{ fontSize: 12 }}>{a.action.replace(/^dataset_/, '').replace(/_/g, ' ')}</span>
              <div className="mono muted data-audit-line">{String(a.details?.dataset_id ?? '')}{a.comment ? ` · ${a.comment}` : ''}</div></td></tr>))}</tbody>
      </table></div>
    </div>
  )
}

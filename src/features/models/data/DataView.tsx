import { useEffect, useState } from 'react'
import { catalog, data, type DsCard, type DsDiff, type DsHistory, type DsRow, type DsUsage, type DsVersion, type DsTableChange, type MyTeam } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'
import { fmtScore } from '@/shared/lib/metrics'
import { NotesStrip } from '../components/NotesStrip'
import { Badge, CardHead, ChangeBar, ConfirmButton, CopyId, Flash, Callout } from '@/shared/components/DataKit'
import { EmptyState } from '@/shared/components/EmptyState'
import { day, when, plural, pct, int } from '@/shared/lib/format'
import { ProjectTree } from '../components/ProjectTree'
import { EditVersion } from './EditVersion'
import { StagedUpload } from './StagedUpload'
import { TablesCard } from './TablesCard'
import { JoinBuilder } from './JoinBuilder'
import { Governance } from './Governance'
import { PurgeCard } from './PurgeCard'
import { LabelsTab } from '@/features/labels/LabelsTab'
import { Tabs } from '@/shared/primitives/Tabs'
import { shares as sharesApi, type Share } from '@/shared/api/governance'
import { readActor, saveActor, useTeam } from '@/shared/lib/team'
import './data.css'

export { readActor, saveActor }
const num = (v?: number | null) => (v == null ? '—' : Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toPrecision(4).replace(/\.?0+$/, ''))
const BAND: Record<string, Status> = { stable: 'success', watch: 'warning', shift: 'danger' }
const OP: Record<string, string> = { edit: 'edit', upload: 'upload', upload_version: 'new file', data_doctor: 'Data Doctor', clean_labels: 'relabel', feature_lab: 'Feature Lab', 'monitoring-window': 'drift window', sql: 'SQL load', refresh: 'SQL refresh' }
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
      <div className="card data-catalog">
        <CardHead title="Data catalog"
          badge={<div className="seg" role="group" aria-label="Whose datasets">
            <button className={scope === 'team' ? 'on' : ''} onClick={() => setScope('team')} data-testid="data-scope-team">{team?.name ?? 'my team'}</button>
            <button className={scope === 'all' ? 'on' : ''} onClick={() => setScope('all')} data-testid="data-scope-all">all teams</button></div>}
          sub={list.data ? plural(rows.length, 'dataset') : undefined}
          actions={<>
            <NewProject team={team} onMade={(p) => { setMade(`Project ${p} created in ${team?.name ?? 'your team'}.`); setProjTick((x) => x + 1) }} />
            <button className="btn primary" disabled={!actor} onClick={() => setUploading(!uploading)} data-testid="data-upload"
              title={actor ? 'Check a file, then create a dataset from it' : 'Type your name in the sidebar first'}>Upload…</button>
          </>} />
        <Flash msg={made ? { ok: true, text: made } : null} />
        {uploading && <div className="data-upload-panel"><StagedUpload key={projTick} by={actor} team={team} onCancel={() => setUploading(false)}
          onDone={(r, text) => { setUploading(false); setMade(text); list.reload(); setParam('dataset', r.root_id) }} /></div>}
        <div className="exp-filters data-filters">
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
            <thead><tr><th>dataset</th><th>team · project</th><th className="r">versions</th><th className="r" title="the default version's rows × columns">rows × cols</th><th>owner</th><th className="r">runs</th><th className="data-when">last change</th></tr></thead>
            <tbody>
              {rows.map((d) => <DsListRow key={d.root_id} d={d} share={sharedIn(d.root_id)} on={sel != null && (sel === d.root_id || sel === d.default_version_id)} pick={() => setParam('dataset', d.root_id)} />)}
              {!rows.length && <tr className="empty-row"><td colSpan={7}><b>No dataset{scope === 'team' && team ? ` of ${team.name}` : ''} matches.</b> <span className="helper">{Object.entries(f).some(([k, v]) => v && !(k === 'state' && v === 'active')) ? 'Clear a filter, or upload one.' : 'Upload a file to make the first one.'}</span></td></tr>}
            </tbody>
          </table></div>)}
      </div>
      {sel ? <DatasetPage key={sel} id={sel} go={go} team={team} ownerTeam={(list.data ?? []).find((d) => d.root_id === sel || d.default_version_id === sel)?.team_id ?? null} onChanged={list.reload} />
        : list.data && rows.length > 0 && <EmptyState title="Pick a dataset above">Its page shows the versions (who made each one and how, what changed), the tables and joins, who may see and use it, and which runs and models used it.</EmptyState>}
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
    <tr className={on ? 'sel' : ''} onClick={pick} style={{ cursor: 'pointer' }} data-testid={`data-row-${d.root_id}`} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && pick()} aria-selected={on}>
      <td className="data-ds-cell"><span className="ds-name">{d.name}</span>{d.tags.map((t) => <span key={t} className="pt-tag"> #{t}</span>)}
        {d.description ? <span className="sub-line trunc" title={d.description}>{d.description}</span> : null}
        {d.versions_in_state?.length ? <span className="sub-line">{f_state(d)}</span> : null}</td>
      <td className="ellip-cell" title={`${d.team?.name ?? 'Default team'} · ${d.project ?? 'no project'}`}>{d.team?.name ?? 'Default team'}
        {(share || d.access === 'shared') && <span className="ds-shared" title={share ? `shared by ${share.requested_by}, decided by ${share.decided_by}: scope ${share.scope}, PII ${share.pii}` : 'another team shared it with yours'} data-testid={`data-shared-${d.root_id}`}>shared with you{share ? ` · ${share.scope} · PII ${share.pii}` : ''}</span>}
        <span className="sub-line">{d.project ?? '—'}</span></td>
      <td className="r mono" title={`${d.n_live} live of ${d.n_versions}`}>{d.n_live}{d.n_versions !== d.n_live ? <span className="muted"> / {d.n_versions}</span> : ''}</td>
      <td className="r mono" title={`default ${d.default_version_id} · ${d.default_how === 'set' ? 'set by hand' : 'the latest'}`}>{d.rows.toLocaleString()} <span className="muted">×</span> {d.n_cols ?? '—'}</td>
      <td className="ellip-cell">{d.owner || <span className="muted">{d.created_by ?? '—'}</span>}</td>
      <td className="r mono">{d.n_runs || <span className="muted">0</span>}</td>
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
  const dtab = useHashParam('dtab')  // LB-C: the page's tabs (versions and data | labels), kept in the hash
  const reload = () => { h.reload(); onChanged() }
  const fail = (e: unknown) => {
    const t = (e as Error).message
    try { const j = JSON.parse(t) as { message?: string; blockers?: string[] }; if (j.blockers) return setMsg({ ok: false, text: j.message ?? t, blockers: j.blockers }) } catch { /* plain text */ }
    setMsg({ ok: false, text: t })
  }
  const act = <T,>(p: Promise<T>, ok: (r: T) => string) => { setMsg(null); return p.then((r) => { setMsg({ ok: true, text: ok(r) }); reload(); return r }).catch(fail) }
  // F-X4a: a share grantee reads what the share covers: the version-level panels (lineage, notes, contract) ask for
  // the shared version, not the root (v1 may be outside the share), and no diff opens on its own
  const shared = !!h.data && ((h.data as { access?: string }).access === 'shared' || (!!ownerTeam && !!team && ownerTeam !== team.id))
  useEffect(() => {
    if (!h.data || pair) return
    if (shared) { setFocus(h.data.default_version_id); return }
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
  const nodeId = shared ? (focusV ?? d.versions.find((v) => v.state !== 'deleted'))?.id ?? d.root_id : d.root_id
  const labelsOn = dtab === 'labels' && !!focusV && focusV.state !== 'deleted'
  return (
    <div className="stack" style={{ gap: 'var(--space-12)' }} data-testid="data-page" data-root={d.root_id}>
      <div className="card ds-head">
        <div className="ds-head-top">
          <div className="ds-head-title">
            <span className="micro-label">dataset{d.project ? <> · {d.project.name}</> : null}</span>
            <h2 className="page-title ds-title" data-testid="data-name">{d.name}</h2>
            <div className="ds-head-meta">
              {def && <span className="champ-badge" title={d.default_how === 'set' ? 'set as the default by hand' : 'the latest version is the default'} data-testid="data-default">★ default v{def.v}{d.default_how === 'set' ? '' : ' (latest)'}</span>}
              <span>{plural(d.versions.length, 'version')}</span>
              {def && <span className="mono">{def.rows.toLocaleString()} rows × {def.n_cols ?? '—'}</span>}
              {d.state && <StatusPill status="warning">{d.state}</StatusPill>}
              {shared && <Badge status="shared">shared with your team</Badge>}
              <CopyId value={d.root_id} />
            </div>
          </div>
          <label className="field ds-actor" title="Names, not authenticated identities: every action records it in the audit log"><span className="micro-label">acting as</span>
            <input className="input" style={{ width: 140 }} value={by} maxLength={80} onChange={(e) => setBy(e.target.value)} data-testid="data-by" /></label>
        </div>
        <NextVersion d={d} by={by} team={team} onDone={(vid, text) => { setMsg({ ok: true, text }); setPair([d.default_version_id ?? d.versions[d.versions.length - 1].id, vid]); reload() }} />
        {msg && <div className={`flash ${msg.ok ? 'ok' : 'bad'} ds-msg`} role={msg.ok ? 'status' : 'alert'} data-testid="data-msg"><span aria-hidden>{msg.ok ? '✓' : '✕'}</span> <span>{msg.text}
          {msg.blockers && <ul>{msg.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}</span></div>}
        <div className="ds-tabs"><Tabs tabs={['Versions and data', 'Labels'] as const} value={dtab === 'labels' ? 'Labels' : 'Versions and data'}
          onChange={(t) => setParam('dtab', t === 'Labels' ? 'labels' : null)} testIds={{ 'Versions and data': 'ds-tab-data', Labels: 'ds-tab-labels' }} /></div>
      </div>

      {labelsOn && focusV && <LabelsTab key={`lb:${d.root_id}`} d={d} vid={focusV.id} v={focusV.v} table={table} by={by} go={go} />}
      {!labelsOn && <>

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
      <NotesStrip key={`n:${nodeId}`} kind="dataset" id={nodeId} />
      <div className="card"><CardHead title="Lineage" sub="where each version came from, the join frames built on it, and the runs and models made from it" />
        <div className="card-body"><ProjectTree key={`l:${nodeId}`} kind="dataset" id={nodeId} go={go} compact /></div></div>
      <div className="grid two">
        <RemoveCard d={d} by={by} act={act} />
        <AuditCard d={d} />
      </div>
      </>}
    </div>
  )
}

/** U1: a new file as the next version of the default: the staged check, the row match with its counts, and Create. */
function NextVersion({ d, by, team, onDone }: { d: DsHistory; by: string; team: MyTeam | null; onDone: (vid: string, text: string) => void }) {
  const [open, setOpen] = useState(false)
  const parent = d.versions.find((v) => v.id === d.default_version_id)
  if (!parent) return <div className="card-body helper data-upload">No active version to build on: every version is archived or deleted{d.state === 'archived' ? ' (unarchive the dataset below to add one)' : ''}.</div>
  return open ? <div className="data-upload"><StagedUpload by={by} team={team} parent={{ id: parent.id, v: parent.v, name: d.name, root: d.root_id, nextV: Math.max(...d.versions.map((v) => v.v)) + 1 }} onCancel={() => setOpen(false)} onDone={(r, text) => { setOpen(false); onDone(r.version_id, text) }} /></div>
    : <div className="card-body row data-upload ds-next" style={{ gap: 10 }}>
      <button className="btn primary" disabled={!by} onClick={() => setOpen(true)} data-testid="data-upload-version" title={by ? `Check a file as the next version, with v${parent.v} as its parent (nothing is written yet)` : 'Type your name in “acting as” first'}>Upload a new version…</button>
      <span className="helper">Built on <b>v{parent.v}</b>, the default. Nothing is written until you check the file and create it. To change a few cells, use Edit on a version below.</span></div>
}

function Versions({ d, pair, setPair, focus, setFocus, by, onDefault, onVerify, onVerifyAll, onArchive, onEdit, onAdopt, onRestoreSplits }: {
  d: DsHistory; pair: [string, string] | null; setPair: (p: [string, string]) => void; focus: string | null; setFocus: (id: string) => void; by: string
  onDefault: (v: DsVersion) => void; onVerify: (v: DsVersion) => void; onVerifyAll: () => void; onArchive: (v: DsVersion, archived: boolean) => void
  onEdit: (v: DsVersion) => void; onAdopt: (v: DsVersion, reason: string) => void; onRestoreSplits: (v: DsVersion) => void
}) {
  const def = d.versions.find((v) => v.id === d.default_version_id)
  return (
    <div className="card" data-testid="data-versions">
      <CardHead title="Versions" sub="pick A and B to diff them · click a row to act on that version and see what used it"
        actions={<button className="btn sm" disabled={!by} onClick={onVerifyAll} title={by ? "Re-hash every version's file against its stored sha256" : 'Type your name in “acting as” first'} data-testid="data-verify-all">Check every hash</button>} />
      <div className="table-wrap"><table className="table data-versions">
        <thead><tr><th className="ab" title="diff from">A</th><th className="ab" title="diff to">B</th><th>version · how it was made</th><th>who · when</th><th>changes from its parent</th><th className="r">rows × cols</th><th title="sha256 of the stored data; ✓ = it matched at the last check">integrity</th><th>used by</th></tr></thead>
        <tbody>{d.versions.map((v) => {
          const gone = v.state === 'deleted'
          const par = d.versions.find((p) => p.id === v.parent_id)
          return (
            <tr key={v.id} className={`${focus === v.id ? 'sel' : ''} ${v.state ? 'muted' : ''}`} onClick={() => setFocus(v.id)} style={{ cursor: 'pointer' }} data-testid={`data-v-${v.v}`} data-id={v.id}>
              <td onClick={(e) => e.stopPropagation()}><input type="radio" name="dsA" disabled={gone} checked={pair?.[0] === v.id} onChange={() => setPair([v.id, pair?.[1] ?? v.id])} aria-label={`A = v${v.v}`} /></td>
              <td onClick={(e) => e.stopPropagation()}><input type="radio" name="dsB" disabled={gone} checked={pair?.[1] === v.id} onChange={() => setPair([pair?.[0] ?? v.id, v.id])} aria-label={`B = v${v.v}`} /></td>
              <td title={v.id}><span className="v-tag mono">v{v.v}</span>{v.is_default && <span className="champ-badge" style={{ marginLeft: 6 }} title="the default: what Build opens">★ default</span>}
                {v.state && <span style={{ marginLeft: 6 }}><Badge status={v.state} title={`${v.state} ${when(v.state_at)} by ${v.state_by}${gone ? ': files removed, tombstone and lineage kept' : ''}`} /></span>}
                <span className="sub-line">{opName(v.op)}{par ? ` from v${par.v}` : ''}{v.paired_with_parent === false ? <span title="new rows with no mapping to the original rows (never paired)"> ◇</span> : ''}</span></td>
              <td>{v.created_by ?? '—'}<span className="sub-line" title={when(v.created_at)}>{day(v.created_at)}</span></td>
              <td className="data-changes">{changeLine(v)}{v.edit_ops?.length ? <span className="sub-line trunc" title={v.edit_ops.map((o) => o.summary).join('\n')} data-testid={`data-ops-${v.v}`}>{plural(v.edit_ops.length, 'edit')}: {v.edit_ops.slice(0, 2).map((o) => o.summary).join('; ')}{v.edit_ops.length > 2 ? '…' : ''}</span> : null}{v.reason && <span className="sub-line trunc" title={`reason / change ticket: ${v.reason}`} data-testid={`data-reason-${v.v}`}>“{v.reason}”</span>}{v.warnings.length > 0 && <span className="sub-line g-warn" title={v.warnings.map((w) => w.message).join('\n')}>▲ {plural(v.warnings.length, 'warning')}</span>}</td>
              <td className="r mono">{v.rows.toLocaleString()} × {v.n_cols ?? '—'}</td>
              <td className="nowrap">{v.sha256 ? <CopyId value={v.sha256} n={8} /> : <span className="muted">—</span>}{integrity(v)}</td>
              <td className="used-by">{v.used_by.runs || v.used_by.versions ? <span className="mono">{plural(v.used_by.runs, 'run')}{v.used_by.versions ? ` · ${plural(v.used_by.versions, 'model')}` : ''}</span> : <span className="muted">unused</span>}{v.used_by.champion && <div><span className="champ-badge">champion</span></div>}</td>
            </tr>
          )
        })}</tbody>
      </table></div>
      <VersionActions v={d.versions.find((v) => v.id === focus) ?? null} def={def} root={d.root_id} by={by} onDefault={onDefault} onVerify={onVerify} onArchive={onArchive} onEdit={onEdit} onAdopt={onAdopt} onRestoreSplits={onRestoreSplits} />
      <p className="card-foot">Versions are made by transforms (Data Doctor, Feature Lab, relabel, the label queue, drift windows, SQL refresh), by a new file, or by an edit; every change is a new version, nothing is modified in place, and a file changed on disk outside the app fails its hash check. The default is a pointer: rolling back moves it and never changes or copies data. ◇ = new rows with no mapping to the original rows (never paired).</p>
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
      <span className="data-actions-who"><span className="v-tag mono">v{v.v}</span><CopyId value={v.id} /></span>
      {gone ? <span className="muted">deleted {when(v.state_at)} by {v.state_by}: the files are gone; this row, lineage.json, the tombstone, every edge and the audit stay.</span> : <>
        {!bad && <button className="btn sm" disabled={!by} onClick={() => onEdit(v)} data-testid={`data-edit-${v.v}`} title={by ? 'Edit cells, rows and columns; the changes are saved as a new version' : 'Type your name in “acting as” first'}>Edit v{v.v}…</button>}
        {bad && <div className="data-warn callout-like" data-testid={`data-integrity-${v.v}`}><b>v{v.v}'s data or split file was changed outside the app</b> (its sha256 no longer matches; Verify hash says which): it is not trusted, and training on it is refused.
          A split file (which rows are held out) is restored as it was drawn: <button className="btn sm" disabled={!by} onClick={() => onRestoreSplits(v)} data-testid={`data-restore-splits-${v.v}`}>Restore the split as drawn</button>.
          For data.csv, restore the file, or keep what is on disk as a new, named version:
          {adopt == null ? <button className="btn sm" style={{ marginLeft: 6 }} disabled={!by} onClick={() => setAdopt('')} data-testid={`data-adopt-${v.v}`}>Create a new version from this file…</button>
            : <span className="row" style={{ gap: 6, marginTop: 4 }}><input className="input" style={{ flex: 1 }} placeholder="why (required)" value={adopt} onChange={(e) => setAdopt(e.target.value)} data-testid="data-adopt-reason" />
              <button className="btn sm" disabled={adopt.trim().length < 5} onClick={() => { onAdopt(v, adopt.trim()); setAdopt(null) }} data-testid="data-adopt-ok">Create version</button>
              <button className="btn sm ghost" onClick={() => setAdopt(null)}>Cancel</button></span>}</div>}
        {!v.is_default && !v.state && <button className="btn sm" disabled={!by} onClick={() => onDefault(v)} data-testid={`data-default-${v.v}`} title="Moves the pointer only: no data changes">{def && v.v < def.v ? `Roll back to v${v.v}` : `Make v${v.v} the default`}</button>}
        <a className="btn sm" href={data.exportUrl(v.id, by || 'unknown')} title="data.csv + lineage.json + manifest.json (hashes, the chain to the root); audited" data-testid={`data-zip-${v.v}`}>Export with lineage (.zip)</a>
        <a className="btn sm ghost" href={data.exportUrl(v.id, by || 'unknown', 'csv')} data-testid={`data-csv-${v.v}`}>CSV only</a>
        <span className="spacer" />
        <button className="btn sm ghost" disabled={!by} onClick={() => onVerify(v)} data-testid={`data-verify-${v.v}`} title="Re-hash this version's file against its stored sha256">Verify hash</button>
        <button className="btn sm ghost" disabled={!by} onClick={() => onArchive(v, !v.state)} data-testid={`data-archive-${v.v}`} title={v.id === root ? 'v1 alone: later versions stay' : 'Hide it from Build and the pickers (reversible)'}>{v.state === 'archived' ? 'Unarchive' : 'Archive'} v{v.v}</button>
        {v.warnings.length > 0 && <Callout tone="warn" className="full">{v.warnings.map((w, i) => <div key={i}>{w.message}</div>)}</Callout>}
      </>}
    </div>
  )
}

/** U5: a multi-table version's changes: the total over its tables as one ChangeBar; the breakdown per table (change
 *  badge + its own ChangeBar) on expand, and in the summary's title on hover. */
function TableChanges({ v, tc }: { v: DsVersion; tc: DsTableChange[] }) {
  const sum = (k: 'added' | 'changed' | 'removed') => tc.reduce((s, t) => s + (t[k] ?? 0), 0)
  const touched = tc.filter((t) => t.change !== 'carried')
  const before = tc.reduce((s, t) => s + (t.rows_before ?? 0), 0)
  const line = (t: DsTableChange) => `${t.table}${t.primary ? ' (primary)' : ''}: ${t.change}` + (t.added == null ? '' : ` · +${int(t.added)} ~${int(t.changed ?? 0)} −${int(t.removed ?? 0)}`)
    + (t.rows_before != null && t.rows_after != null ? ` · ${int(t.rows_before)} → ${int(t.rows_after)} rows` : '')
  return (
    <details className="tchg" onClick={(e) => e.stopPropagation()} data-testid={`data-tchg-${v.v}`}>
      <summary title={tc.map(line).join('\n')}>
        <ChangeBar add={sum('added')} upd={sum('changed')} del={sum('removed')} base={before || null} testId={`data-tchg-total-${v.v}`} />
        <span className="sub-line">{touched.length ? `${plural(touched.length, 'table')} changed: ${touched.map((t) => t.table).join(', ')}` : 'every table carried'} · {plural(tc.length, 'table')}</span>
      </summary>
      <table className="tchg-t">
        <tbody>{tc.map((t) => <tr key={t.table} data-testid={`data-tchg-${v.v}-${t.table}`}>
          <td className="mono">{t.table}{t.primary && <span className="muted" title="the primary table"> ★</span>}</td>
          <td><Badge status={t.change} /></td>
          <td>{t.added == null ? <span className="muted" title="no row-level counts recorded for this table">—</span>
            : <ChangeBar add={t.added} upd={t.changed} del={t.removed} base={t.rows_before} />}</td>
          <td className="r mono muted">{t.rows_before != null && t.rows_after != null ? `${int(t.rows_before)} → ${int(t.rows_after)}` : t.rows_after != null ? int(t.rows_after) : ''}</td>
        </tr>)}</tbody>
      </table>
    </details>
  )
}

const changeLine = (v: DsVersion) => {
  if (v.parent_id && v.table_changes?.length) return <TableChanges v={v} tc={v.table_changes} />
  const c = v.changes
  if (c.matched != null) return <ChangeBar add={c.added} upd={c.changed} del={c.removed} base={c.rows_before ?? null} />
  if (c.rows_before != null && c.rows_after != null && v.parent_id) return <span>{c.rows_before.toLocaleString()} → {c.rows_after.toLocaleString()} rows{v.n_changes ? ` · ${v.n_changes} change${v.n_changes === 1 ? '' : 's'}` : ''}</span>
  return <span className="muted">{v.parent_id ? plural(v.n_changes ?? 0, 'change') : 'the original'}</span>
}

const integrity = (v: DsVersion) => v.verified_ok == null || v.state === 'deleted' ? null
  : v.verified_ok ? <span className="data-ok" title={`file matches the stored hash · checked ${when(v.verified_at)}`} aria-label="hash verified"> ✓</span>
    : <span className="data-bad" title={`file does NOT match the stored hash · checked ${when(v.verified_at)}`} data-testid={`data-mismatch-${v.v}`}> ✕ mismatch</span>

function DiffCard({ a, b, name, table }: { a: string; b: string; name: (id: string) => string; table: string | null }) {
  const { data: df, err } = useFetch<DsDiff | null>(a === b ? () => Promise.resolve(null) : () => data.diff(a, b, table), [a, b, table])
  const [all, setAll] = useState(false)
  if (a === b) return <EmptyState title="Nothing to diff">Pick two different versions in the A and B columns of Versions to see what changed between them.</EmptyState>
  if (err && !df) return <ErrorCard err={err} />
  if (!df) return <Loading>Diffing {name(a)} → {name(b)}{table ? ` (table ${table})` : ''}…</Loading>
  const s = df.schema, r = df.rows
  // new, gone and retyped columns and any PSI ≥ 0.01 first, then the largest PSIs, 10 in all (the rest on demand)
  const key = (c: DsDiff['columns'][number]) => c.in !== 'both' || c.kind.includes('→') || (c.psi ?? 0) >= 0.01
  const cols = all ? df.columns : [...df.columns.filter(key), ...df.columns.filter((c) => !key(c))].slice(0, Math.max(10, df.columns.filter(key).length))
  return (
    <div className="card" data-testid="data-diff">
      <CardHead title={<>What changed: {name(a)} → {name(b)}{table ? <span className="mono" data-testid="diff-table"> · {table}</span> : null}</>} sub={`rows matched by ${r.matched_by}`}
        actions={df.tables && df.tables.length > 1 ? <span className="diff-tables" data-testid="diff-tables"><span className="micro-label">tables</span>{df.tables.map((t) => <Badge key={t.table} status={t.status === 'unchanged' ? 'unchanged' : 'changed'} title={`${t.a_sha256?.slice(0, 10) ?? '—'} → ${t.b_sha256?.slice(0, 10) ?? '—'} · rows ${t.rows.map((x) => x?.toLocaleString() ?? '—').join(' → ')}`}>{t.table} {t.status}</Badge>)}</span> : undefined} />
      <div className="card navy explain-hero data-hero">
        <div><span className="micro-label">rows</span><b>{r.a.toLocaleString()} → {r.b.toLocaleString()}</b></div>
        <div><span className="micro-label">changed</span><b data-testid="diff-changed">{r.changed.toLocaleString()}</b></div>
        <div><span className="micro-label">added</span><b data-testid="diff-added">+{r.added.toLocaleString()}</b></div>
        <div><span className="micro-label">removed</span><b data-testid="diff-removed">−{r.removed.toLocaleString()}</b></div>
        <div><span className="micro-label">columns</span><b>{s.n_columns[0]} → {s.n_columns[1]}</b></div>
      </div>
      {r.changed + r.added + r.removed > 0 && <div className="diff-bar-row"><ChangeBar add={r.added} upd={r.changed} del={r.removed} base={r.a} words /><span className="helper">{((r.changed + r.added + r.removed) / Math.max(1, r.a) * 100).toFixed(1)}% of {name(a)}'s {r.a.toLocaleString()} rows touched</span></div>}
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
        {df.checks.length > 0 && <Callout tone="warn">{df.checks.map((w, i) => <div key={i}>{w.message}</div>)}</Callout>}
        {r.changed_examples.length > 0 && <details><summary className="micro-label">{r.changed_examples.length} changed rows, cell by cell</summary>
          <table className="table"><tbody>{r.changed_examples.map((x) => <tr key={x.row_b}><td className="mono">{x.key != null ? String(x.key) : `row ${x.row_b}`}</td>
            <td>{x.cells.map((c) => <span key={c.column} className="data-cell"><span className="mono">{c.column}</span> {String(c.a)} → <b>{String(c.b)}</b></span>)}</td></tr>)}</tbody></table></details>}
      </div>
      <div className="table-wrap"><table className="table" data-testid="diff-columns">
        <thead><tr><th>column</th><th className="r">missing A → B</th><th className="r">mean A → B</th><th>median / top value A → B</th><th className="r">PSI</th></tr></thead>
        <tbody>{cols.map((c) => (
          <tr key={c.column}>
            <td className="mono">{c.column}{c.in !== 'both' && <span className={`pt-chip ${c.in === 'b' ? 'data-add' : 'data-rm'}`} style={{ marginLeft: 6 }}>{c.in === 'b' ? 'new' : 'gone'}</span>} <span className="muted col-kind">{c.kind}</span></td>
            <td className="mono r">{pct(c.missing_a)} → {pct(c.missing_b)}</td>
            <td className="mono r">{c.kind === 'number' ? `${num(c.mean_a)} → ${num(c.mean_b)}` : <span className="muted">—</span>}</td>
            <td className="mono">{c.kind === 'number' ? `${num(c.median_a)} → ${num(c.median_b)}` : `${c.top_a?.[0] ? `${c.top_a[0].value} ${pct(c.top_a[0].share)}` : '—'} → ${c.top_b?.[0] ? `${c.top_b[0].value} ${pct(c.top_b[0].share)}` : '—'}`}</td>
            <td className="r">{c.psi == null ? '—' : <span title={c.note}><StatusPill status={BAND[c.band ?? ''] ?? 'neutral'}>{c.psi.toFixed(3)} {c.band}</StatusPill></span>}</td>
          </tr>))}</tbody>
      </table></div>
      <p className="card-foot">{r.note}. PSI: &lt; 0.10 stable, 0.10–0.25 watch, &gt; 0.25 shift (A's deciles or top categories as the bins). {!all && df.columns.length > cols.length && <button className="chip-btn" onClick={() => setAll(true)}>Show all {df.columns.length} columns</button>}</p>
    </div>
  )
}

function Usage({ id, v, go }: { id: string; v?: number; go: (r: string) => void }) {
  const { data: u, err } = useFetch<DsUsage>(() => data.usage(id), [id])
  const approved = (u?.champions ?? []).filter((c) => c.status === 'approved')
  return (
    <div className="card" data-testid="data-usage">
      <CardHead title={`Used by ${v ? `v${v}` : id}`} sub="runs and models trained on it, and on the versions derived from it" />
      <div className="card-body stack usage" style={{ gap: 8 }}>
        {err && !u ? <ErrorCard inline err={err} /> : !u ? <Loading inline>Reading what used it…</Loading> : <>
          {approved.map((c) => <div key={c.id} className="use-row"><span className="champ-badge">★ champion</span><span>of {c.project_id}</span><CopyId value={c.run_id} /></div>)}
          {u.versions.length > 0 && <div className="use-group"><span className="micro-label">models</span>
            {u.versions.map((x) => <div key={x.id} className="use-row"><span className="use-ico" aria-hidden>◆</span><button className="linkish" onClick={() => go(`registry?version=${x.id}`)}>{x.model_name} v{x.version}</button>
              <Badge status={x.stage === 'Production' ? 'active' : 'none'}>{x.stage}</Badge><Badge status={x.approval} />{x.retired && <Badge status="archived" title={`retired ${when(x.retired_at)}`}>retired</Badge>}</div>)}</div>}
          {u.runs.length > 0 && <div className="use-group"><span className="micro-label">runs</span>
            {u.runs.map((r) => <div key={r.id} className="use-row" data-testid={`usage-run-${r.id}`}><span className="use-ico" aria-hidden>●</span><button className="linkish" onClick={() => go(`models?run=${r.id}`)}>{r.name}</button>
              <span className="mono use-metric">{r.status !== 'succeeded' ? <Badge status={r.status === 'failed' ? 'failed' : 'pending'}>{r.status}</Badge> : r.primary_metric ? `${r.primary_metric} ${fmtScore(r.primary_score, 4)}` : ''}</span>
              {r.via_join && <span className="pt-chip" title={`P14: read through join frame ${r.via_join.join_frame_id} (${r.via_join.relation})`} data-testid="usage-via-join">⋈ via join · {r.via_join.tables.join(', ')}{r.via_join.relation === 'merged_from' ? ' (another dataset)' : ''}</span>}
              <span className="spacer" /><CopyId value={r.id} /></div>)}</div>}
          {(u.join_frames ?? []).length > 0 && <div className="use-group"><span className="micro-label">join frames built on it</span>
            {(u.join_frames ?? []).map((f) => <div key={f.id} className="use-row" data-testid="usage-frame"><span className="use-ico" aria-hidden>⋈</span><span className="pt-chip">{f.relation.replace('_', ' ')}: {f.tables.join(', ')}</span><span className="spacer" /><CopyId value={f.id} /></div>)}
            <span className="helper" style={{ fontSize: 12 }}>Their runs are listed above. Deleting this version is refused while one of them needs it.</span></div>}
          {!u.runs.length && !u.versions.length && <span className="helper">No run has trained on it yet.</span>}
          {u.blocks_archive.length > 0 ? <Callout tone="neutral" icon={false}><span><b>Can't be archived or deleted while:</b> {u.blocks_archive.join(' ')}</span></Callout>
            : u.blocks_archive_or_delete.length > 0 && <Callout tone="neutral" icon={false}><span><b>Can be archived. Can't be deleted yet:</b> {u.blocks_archive_or_delete.join(' ')}</span></Callout>}
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
      <CardHead title="Dataset card" sub={c.updated_by ? `last edited by ${c.updated_by} · ${when(c.updated_at)}` : 'what it is, who answers for it, and its caveats'}
        actions={!edit ? <button className="btn sm" disabled={!by} title={by ? 'Edit the card (audited)' : 'Type your name in “acting as” first'} onClick={() => setEdit({ name: d.name, ...c })} data-testid="data-card-edit">Edit</button>
          : <><button className="btn sm ghost" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn sm primary" onClick={() => { const { name, ...rest } = edit; data.editCard(d.root_id, { ...(name !== d.name ? { name } : {}), ...Object.fromEntries(CARD.map(([k]) => [k, (rest[k] as string) ?? ''])) }, by).then(() => { setEdit(null); onSaved() }).catch(onErr) }} data-testid="data-card-save">Save</button></>} />
      <div className="card-body stack" style={{ gap: 6 }}>
        {edit && <label className="stack" style={{ gap: 2 }}><span className="micro-label">Name</span><input className="input" value={edit.name ?? ''} maxLength={120} onChange={(e) => setEdit({ ...edit, name: e.target.value })} data-testid="data-card-name" /></label>}
        {CARD.map(([k, label, ph]) => edit
          ? <label key={k} className="stack" style={{ gap: 2 }}><span className="micro-label">{label}</span>
            <textarea className="input" rows={k === 'description' || k === 'known_issues' ? 2 : 1} placeholder={ph} value={(edit[k] as string) ?? ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} data-testid={`data-card-${k}`} /></label>
          : <div key={k} className="data-card-row"><span className="micro-label">{label}</span><span className={c[k] ? 'dc-val' : 'muted'}>{(c[k] as string) || 'not written yet'}</span></div>)}
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
      <CardHead title="Archive or delete" sub="refused while a registered model or a champion comes from it" />
      <div className="card-body stack" style={{ gap: 8 }}>
        {pending.map((r) => (
          <div key={r.id} className="callout warn" data-testid={`data-req-${r.id}`}><span className="callout-ico" aria-hidden>▲</span><div className="callout-body"><div><b>Delete {label(r.dataset_id)}</b> asked by <b>{r.requested_by}</b> · {when(r.requested_at)} · “{r.reason}”</div>
            <div className="row" style={{ gap: 6, marginTop: 4 }}>
              <button className="btn sm" disabled={!by} onClick={() => act(data.decideDelete(r.id, true, by), () => `Deleted ${label(r.dataset_id)}: files removed, tombstone and lineage kept.`)} data-testid="data-req-approve">Approve delete</button>
              {me && me === r.requested_by.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
                ? <button className="btn sm ghost" onClick={() => act(data.decideDelete(r.id, false, by, '', true), () => 'Your delete request was withdrawn.')} data-testid="data-req-withdraw">Withdraw my request</button>
                : <button className="btn sm ghost" disabled={!by} onClick={() => act(data.decideDelete(r.id, false, by), () => 'Delete rejected.')}>Reject</button>}
              <span className="helper">someone other than {r.requested_by} decides (admins too)</span></div></div></div>))}
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
          <span className="spacer" />
          <ConfirmButton className="btn sm danger" disabled={!by || reason.trim().length < 5} title={!by ? 'Type your name in “acting as” first' : reason.trim().length < 5 ? 'Give a reason (at least 5 characters)' : `Ask to delete ${label(tid, only)}: a second person approves (an admin's request deletes at once)`}
            warning={`Delete ${label(tid, only)}? Its files go; the hash, tombstone, lineage and audit stay.`} confirm="Ask to delete" testId="data-delete"
            onConfirm={() => act(data.requestDelete(tid, by, reason, only, freeze), (r) => r.status === 'approved' ? `Deleted ${label(tid, only)} (admin).` : `Delete of ${label(tid, only)} asked: someone else must approve.`)}>Ask to delete…</ConfirmButton>
        </div>
        <p className="helper" style={{ fontSize: 12, margin: 0, lineHeight: '17px' }}>Archive hides it from Build and the pickers; it is allowed once the only models on it are Archived or retired and none is champion. Delete needs every model on it retired for {d.retention_days.toLocaleString()} days (retention policy, needs model-risk approval) and a second person (or an admin{d.admins_configured ? '' : ': none configured'}); the files go, the hash, tombstone, lineage and audit stay. Retire models in the Registry.</p>
      </div>
    </div>
  )
}

function AuditCard({ d }: { d: DsHistory }) {
  return (
    <div className="card" data-testid="data-audit">
      <CardHead title="Audit" sub="every dataset action, newest first" />
      <div className="table-wrap" style={{ maxHeight: 260, overflow: 'auto' }}><table className="table">
        <tbody>{d.audit.slice(0, 30).map((a) => (
          <tr key={a.id}><td className="muted" style={{ whiteSpace: 'nowrap', fontSize: 12 }} title={when(a.ts)}>{day(a.ts)}</td><td>{a.actor}</td>
            <td><span className="audit-act">{a.action.replace(/^dataset_/, '').replace(/_/g, ' ')}</span>
              {(a.comment || a.details?.dataset_id) ? <span className="sub-line data-audit-line" title={`${a.comment ?? ''} ${String(a.details?.dataset_id ?? '')}`.trim()}>{a.comment || String(a.details?.dataset_id ?? '')}</span> : null}</td></tr>))}
          {!d.audit.length && <tr><td colSpan={3} className="helper">Nothing recorded yet.</td></tr>}</tbody>
      </table></div>
    </div>
  )
}

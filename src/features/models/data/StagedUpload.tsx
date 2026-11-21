import { useEffect, useRef, useState } from 'react'
import { catalog, uploads, type MatchMethod, type MatchSpec, type MyTeam, type Project, type UpCheck, type UpCommitted, type UpTable, type Upload } from '@/shared/api/catalog'
import { ApiError } from '@/shared/api/client'
import { Tabs } from '@/shared/primitives/Tabs'

/** The parent of a next version: the dataset's default version. */
export type UpParent = { id: string; v: number; name: string; nextV?: number }

const n = (x?: number | null) => (x == null ? '—' : x.toLocaleString())
const share = (x?: number | null) => (x == null ? '' : `${(x * 100).toFixed(x > 0 && x < 0.001 ? 2 : 1)} %`)
const same = (a?: MatchSpec | null, b?: MatchSpec | null) => !!a && !!b && a.method === b.method && (a.columns ?? []).join(',') === (b.columns ?? []).join(',')
const errText = (e: unknown) => {
  const d = e instanceof ApiError ? e.detail as { code?: string; message?: string } | undefined : undefined
  return d && typeof d === 'object' && d.message ? `${d.message}${d.code ? ` (${d.code})` : ''}` : (e as Error).message
}

/**
 * U1 (C §2.2–2.4, plan §5.1/§5.3): one upload through the staged API. Pick a file → **Check file** (POST /uploads, then
 * /check: nothing is written but staging) → the typed columns with their reasons per table, and for a next version the
 * row match with its counts → **Create** (POST /commit {match, acknowledge}). One primary action per step.
 */
export function StagedUpload({ by, team, parent, initialFile, onDone, onCancel }: {
  by: string; team: MyTeam | null; parent?: UpParent | null; initialFile?: File | null
  onDone: (r: UpCommitted, text: string) => void; onCancel?: () => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(initialFile ?? null)
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [project, setProject] = useState('')
  const [up, setUp] = useState<Upload | null>(null)
  const [chk, setChk] = useState<UpCheck | null>(null)
  const [match, setMatch] = useState<MatchSpec | null>(null)
  const [ack, setAck] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const next = !!parent

  // a new dataset goes into a project of the current team (its own new project in the default team when none is chosen)
  useEffect(() => {
    if (next) return
    setProjects(null)
    catalog.projects(team?.id).then((p) => { setProjects(p); setProject((cur) => (p.some((x) => x.id === cur) ? cur : team && team.id !== 'tm_default' ? p[0]?.id ?? '' : '')) })
      .catch(() => setProjects([]))
  }, [team?.id, next])
  // a next version from Build's "looks like" prompt starts checking at once: nothing to choose before the check
  // (once: a second run of the effect would stage the file twice)
  const started = useRef(false)
  useEffect(() => { if (next && initialFile && by && !started.current) { started.current = true; void check(initialFile) } }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const nv = chk?.next_version
  const chosen: MatchMethod | undefined = nv?.methods?.find((m) => same(m, match)) ?? (nv?.chosen && same(nv.chosen, match) ? nv.chosen : undefined)
  const needsAck = (chosen?.needs_acknowledge.length ?? 0) > 0
  const needProject = !next && team != null && team.id !== 'tm_default' && !project

  const check = async (f = file) => {
    if (!f) return
    setErr(''); setChk(null); setAck(false)
    try {
      setBusy('Uploading to staging…')
      if (up && up.status !== 'committed') await uploads.discard(up.upload_id).catch(() => null)
      const u = await uploads.stage(f, { by, projectId: next ? null : project || null, parentVersionId: parent?.id })
      setUp(u)
      setBusy(next ? 'Typing the columns and matching rows to the parent…' : 'Typing the columns…')
      const c = await uploads.check(u.upload_id)
      setChk(c)
      setMatch(c.next_version?.preselect ?? null)
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  /** Another candidate key: the plan's match is simulated by a fresh check (PATCH plan → /check). */
  const simulate = async (m: MatchSpec) => {
    if (!up) return
    setErr(''); setBusy(`Simulating key ${m.columns?.join(', ')}…`)
    try {
      await uploads.plan(up.upload_id, { match: m })
      const c = await uploads.check(up.upload_id)
      setChk(c); setMatch(m); setAck(false)
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const commit = async () => {
    if (!up || !chk) return
    setErr(''); setBusy(next ? 'Writing the version (matched rows keep their ids)…' : 'Writing the dataset…')
    try {
      const r = await uploads.commit(up.upload_id, {
        by, check_sha256: chk.check_sha256, ...(reason.trim() ? { reason: reason.trim() } : {}),
        ...(next && match ? { match } : {}), ...(next && needsAck && ack ? { acknowledge: [nv?.commit_needs?.acknowledge_token ?? 'holdout_identity_loss'] } : {}),
      })
      const m = r.match
      const text = next
        ? `${file?.name} is v${r.version_no} of ${parent!.name} (${r.version_id})${r.deduped ? ', identical to that existing version' : ''}: ${n(m?.matched)} matched by ${m?.matched_by}, ${n(m?.changed)} changed, +${n(m?.added)}, −${n(m?.removed)}, ${n(m?.holdout_identity_lost)} holdout rows lost their id. `
          + (r.default?.is_default ? 'It is now the default.' : `Not the default${r.default?.why ? ` (${r.default.why})` : ''}: review it, then make it the default.`)
        : `${file?.name} is ${r.name} v${r.version_no} (${r.version_id}): ${n(r.n_rows)} rows × ${r.n_cols}, stored as typed Parquet.`
      setUp(null); setChk(null)
      onDone(r, text)
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const discard = async () => { if (up) await uploads.discard(up.upload_id).catch(() => null); setUp(null); setChk(null); setFile(null); onCancel?.() }

  const blocked = !!chk?.blocks.length
  const canCreate = !!chk && !blocked && (!next || (!!chosen && chosen.valid && (!needsAck || ack)))
  return (
    <div className="card-body stack up-flow" style={{ gap: 10 }} data-testid="up-flow">
      {!chk && <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn" disabled={!!busy} onClick={() => ref.current?.click()} data-testid="up-pick">{file ? 'Another file…' : 'Choose a file…'}</button>
        <input ref={ref} type="file" accept=".csv,.tsv,.txt,.parquet" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) setFile(f); e.target.value = '' }} data-testid="up-file" />
        {file && <span className="mono" data-testid="up-filename">{file.name} <span className="muted">{(file.size / 1024).toFixed(0)} KB</span></span>}
        {next ? <span className="helper">next version of <b>{parent!.name}</b>, from v{parent!.v} (the default)</span>
          : <label className="row helper" style={{ gap: 6 }}>project
            <select className="select" value={project} onChange={(e) => setProject(e.target.value)} disabled={!projects} data-testid="up-project">
              {(!team || team.id === 'tm_default') && <option value="">its own new project ({team?.name ?? 'Default team'})</option>}
              {needProject && <option value="">pick a project of {team!.name}</option>}
              {(projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>{team && team.id !== 'tm_default' && projects && !projects.length && <span>{team.name} has no project yet: make one with New project.</span>}</label>}
        <span className="grow" />
        <button className="btn primary" disabled={!file || !by || !!busy || needProject} onClick={() => check()} data-testid="up-check"
          title={by ? 'Stages the file and types every column: nothing is written until you create it' : 'Type your name first (sidebar)'}>Check file</button>
        {onCancel && <button className="btn ghost" disabled={!!busy} onClick={discard}>Cancel</button>}
      </div>}
      {busy && <p className="helper" role="status" data-testid="up-busy">… {busy}</p>}
      {err && <p className="helper up-err" role="alert" data-testid="up-err">✕ {err}</p>}
      {chk && <CheckView chk={chk} file={file} />}
      {chk && next && nv && <MatchView nv={nv} parentV={parent!.v} match={match} setMatch={(m) => { setMatch(m); setAck(false) }} simulate={simulate} busy={!!busy} />}
      {chk && next && chosen && needsAck && <label className="data-warn up-ack" data-testid="up-ack">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="up-ack-box" />
        <span><b>Acknowledge to create it with {chosen.label}</b> (recorded in the version's manifest):{chosen.warnings.filter((w) => w.needs_ack).map((w) => <span key={w.code} className="up-ackline">⚠ {w.message}</span>)}</span>
      </label>}
      {chk && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} maxLength={500} placeholder="reason or change ticket (optional, kept with the version)" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="up-reason" />
        <button className="btn primary" disabled={!canCreate || !!busy} onClick={commit} data-testid="up-commit"
          title={blocked ? 'The check found problems that block it' : needsAck && !ack ? 'Acknowledge the warning first' : undefined}>{next ? `Create v${parent!.nextV ?? parent!.v + 1}` : 'Create dataset'}</button>
        <button className="btn ghost" disabled={!!busy} onClick={discard} data-testid="up-discard">Discard</button>
      </div>}
    </div>
  )
}

/** The check, per table (one tab per table: multi-table versions arrive with P12): the typed columns with the reason
 *  for each type, the warnings and blocks, and the first rows. */
function CheckView({ chk, file }: { chk: UpCheck; file: File | null }) {
  const [tab, setTab] = useState(chk.tables[0]?.name ?? '')
  const t = chk.tables.find((x) => x.name === tab) ?? chk.tables[0]
  return (
    <div className="data-preview" data-testid="up-check-result">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>Check: {file?.name ?? t?.source_file}</b>
        <span className="helper">{chk.tables.length} table{chk.tables.length === 1 ? '' : 's'} · checked in {chk.seconds} s · nothing is written until you create it</span>
      </div>
      {chk.blocks.length > 0 && <div className="up-block" data-testid="up-blocks"><b>{chk.blocks.length} problem{chk.blocks.length === 1 ? '' : 's'} block this file</b>{chk.blocks.map((b, i) => <div key={i}>✕ {b.message}</div>)}</div>}
      <Tabs tabs={chk.tables.map((x) => x.name)} value={t?.name ?? ''} onChange={setTab} testIds={Object.fromEntries(chk.tables.map((x) => [x.name, `up-tab-${x.name}`]))} />
      {t && <TableCheck t={t} chk={chk} />}
    </div>
  )
}

function TableCheck({ t, chk }: { t: UpTable; chk: UpCheck }) {
  const issues = (c: string) => [...chk.blocks, ...chk.warnings].filter((w) => w.column === c)
  const other = chk.warnings.filter((w) => !w.column)
  return (
    <div className="stack" style={{ gap: 8 }} data-testid={`up-table-${t.name}`}>
      <div className="data-hero data-hero-sm">
        <div><span className="micro-label">rows</span><b data-testid="up-rows">{n(t.n_rows)}</b></div>
        <div><span className="micro-label">columns</span><b>{t.n_cols}</b></div>
        <div><span className="micro-label">from</span><b className="up-small">{t.source_file}</b></div>
        <div><span className="micro-label">read as</span><b className="up-small">{t.encoding ?? 'parquet'}{t.separator ? ` · sep ${t.separator === '\t' ? 'tab' : t.separator}` : ''}</b></div>
        <div><span className="micro-label">warnings</span><b>{chk.warnings.length}</b></div>
      </div>
      <div className="table-wrap up-cols"><table className="table" data-testid="up-columns">
        <thead><tr><th>column</th><th>stored as</th><th>why</th><th className="r">non-null</th></tr></thead>
        <tbody>{t.schema.map((c) => {
          const is = issues(c.name)
          return (
            <tr key={c.name} data-testid={`up-col-${c.name}`} className={is.length ? 'up-flag' : ''}>
              <td className="mono">{c.name}</td>
              <td><span className={`pt-chip up-type t-${c.type.toLowerCase()}`}>{c.type}</span>{c.nullable && <span className="muted" title="has missing values"> ∅</span>}</td>
              <td className="helper">{c.reason}{is.map((w, i) => <div key={i} className="up-colwarn">⚠ {w.message}</div>)}</td>
              <td className="r mono">{c.non_null == null ? '—' : n(c.non_null)}</td>
            </tr>
          )
        })}</tbody>
      </table></div>
      {other.length > 0 && <div className="data-warn">{other.map((w, i) => <div key={i}>⚠ {w.message}</div>)}</div>}
      {chk.notes.length > 0 && <span className="helper">{chk.notes.join(' · ')}</span>}
      <details><summary className="micro-label">first {t.preview.rows.length} rows, as in the file</summary>
        <div className="table-wrap"><table className="table up-preview"><thead><tr>{t.preview.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{t.preview.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className="mono">{v == null ? <span className="muted">∅</span> : String(v)}</td>)}</tr>)}</tbody></table></div>
      </details>
    </div>
  )
}

/** §5.3: how the file's rows pair with the parent's. The key candidates (with their statistics), then every simulated
 *  method with its counts; the person chooses (the declared key, else the top candidate, is preselected). */
function MatchView({ nv, parentV, match, setMatch, simulate, busy }: { nv: NonNullable<UpCheck['next_version']>; parentV: number; match: MatchSpec | null; setMatch: (m: MatchSpec) => void; simulate: (m: MatchSpec) => void; busy: boolean }) {
  if (!nv.methods) return <div className="data-warn" data-testid="up-match">{nv.note ?? 'Matching runs once the file checks clean.'}</div>
  const simulated = (cols: string[]) => nv.methods!.some((m) => (m.method === 'key' || m.method === 'declared') && (m.columns ?? []).join(',') === cols.join(','))
  const c = nv.contract
  return (
    <div className="data-preview" data-testid="up-match">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>Match rows to v{parentV} <span className="mono muted" style={{ fontWeight: 400 }}>{nv.parent_version_id}</span></b>
        <span className="helper">parent table <span className="mono">{nv.table}</span> · {n(nv.parent_rows)} rows · holdout {n(nv.parent_holdout)} rows. Matched rows keep their id, holdout and fold; choose how.</span>
      </div>
      <table className="table up-methods" data-testid="up-methods">
        <thead><tr><th /><th>match by</th><th className="r">matched</th><th className="r">changed</th><th className="r">added</th><th className="r">removed</th><th className="r" title="parent holdout rows that would get a new id">holdout identity lost</th><th /></tr></thead>
        <tbody>{nv.methods.map((m) => {
          const on = same(m, match)
          const lost = m.holdout_identity_lost ?? 0
          return (
            <tr key={m.label} className={on ? 'sel' : ''} onClick={() => m.valid && setMatch({ method: m.method, ...(m.columns ? { columns: m.columns } : {}) })} style={{ cursor: m.valid ? 'pointer' : 'default' }} data-testid={`up-m-${m.method}${m.columns ? `-${m.columns.join('_')}` : ''}`}>
              <td><input type="radio" name="up-match" checked={on} disabled={!m.valid} readOnly aria-label={m.label} /></td>
              <td><b>{m.label}</b>{m.roles.includes('best_candidate') && <span className="pt-chip" style={{ marginLeft: 4 }}>best candidate</span>}{m.roles.includes('declared') && <span className="pt-chip" style={{ marginLeft: 4 }}>confirmed key</span>}
                {!m.valid && <div className="up-colwarn">✕ {m.problem?.message}</div>}</td>
              <td className="r mono">{n(m.matched)}</td><td className="r mono">{n(m.changed)}</td><td className="r mono">+{n(m.added)}</td><td className="r mono">−{n(m.removed)}</td>
              <td className={`r mono ${lost ? 'up-lost' : ''}`} data-testid="up-lost">{n(lost)}{m.parent_holdout ? <span className="muted"> / {n(m.parent_holdout)} {lost ? `(${share(m.holdout_identity_lost_share)})` : ''}</span> : null}</td>
              <td>{m.warnings.map((w) => <span key={w.code} className={`pt-chip ${w.needs_ack ? 'data-type' : ''}`} title={w.message}>{w.code.replace(/_/g, ' ')}</span>)}</td>
            </tr>
          )
        })}</tbody>
      </table>
      {(nv.candidates?.length ?? 0) > 0 && <div className="up-cands" data-testid="up-candidates"><span className="micro-label">key candidates</span>
        {nv.candidates!.map((k) => <span key={k.columns.join(',')} className="up-cand" title={`parent ${n(k.parent.distinct)} distinct, ${n(k.parent.duplicates)} repeated, ${n(k.parent.nulls)} missing · file ${n(k.new.distinct)} distinct, ${n(k.new.duplicates)} repeated, ${n(k.new.nulls)} missing`}>
          <span className="mono">{k.columns.join(' + ')}</span> score {k.score.toFixed(3)} · overlap {share(k.overlap)} · unique {share(k.uniqueness)}{k.id_like ? ' · id-like' : ''}{k.valid ? '' : ' · not a key'}
          {k.valid && !simulated(k.columns) && <button className="chip-btn" disabled={busy} onClick={() => simulate({ method: 'key', columns: k.columns })} data-testid={`up-sim-${k.columns.join('_')}`}>simulate</button>}
        </span>)}</div>}
      {c && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }} data-testid="up-contract">
        {c.columns_added.map((x) => <span key={x} className="pt-chip data-add">+ {x}</span>)}
        {c.columns_removed.map((x) => <span key={x} className="pt-chip data-rm">− {x}</span>)}
        {c.type_changes.map((t) => <span key={t.column} className="pt-chip data-type">{t.column}: {t.from} → {t.to}</span>)}
        {!c.columns_added.length && !c.columns_removed.length && !c.type_changes.length && <span className="helper">same columns and types as the parent · {n(c.rows_before)} → {n(c.rows_after)} rows</span>}
        {c.warnings.length > 0 && <div className="data-warn">{c.warnings.map((w, i) => <div key={i}>⚠ {w.message}</div>)}<span className="helper">With a warning the version is created but does not become the default.</span></div>}
      </div>}
    </div>
  )
}

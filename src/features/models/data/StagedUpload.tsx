import { useEffect, useRef, useState } from 'react'
import { catalog, uploads, type MatchMethod, type MatchSpec, type MyTeam, type Project, type TableAction, type TablePlan, type UpCheck, type UpCommitted, type UpDelta, type UpRelation, type UpTable, type Upload, type UploadPlan } from '@/shared/api/catalog'
import { tables as tablesApi, type VTable } from '@/shared/api/tables'
import { ApiError } from '@/shared/api/client'
import { Tabs } from '@/shared/primitives/Tabs'
import { INTEGRITY_ACKS, IntegrityAcks, IssueLine, type Issue } from './UploadIssues'

/** The parent of a next version: the dataset's default version. */
export type UpParent = { id: string; v: number; name: string; nextV?: number; root?: string }

const n = (x?: number | null) => (x == null ? '—' : x.toLocaleString())
const share = (x?: number | null) => (x == null ? '' : `${(x * 100).toFixed(x > 0 && x < 0.001 ? 2 : 1)} %`)
const same = (a?: MatchSpec | null, b?: MatchSpec | null) => !!a && !!b && a.method === b.method && (a.columns ?? []).join(',') === (b.columns ?? []).join(',')
const errText = (e: unknown) => {
  const d = e instanceof ApiError ? e.detail as { code?: string; message?: string; keys?: { value: unknown[]; rows: number }[] } | undefined : undefined
  if (d && typeof d === 'object' && d.message) {
    const keys = d.keys?.length ? ` Keys: ${d.keys.slice(0, 5).map((k) => `${k.value.join('+')} ×${k.rows}`).join(', ')}${d.keys.length > 5 ? '…' : ''}.` : ''
    return `${DELTA_CODES[d.code ?? ''] ? `${DELTA_CODES[d.code ?? '']} ` : ''}${d.message}${keys}${d.code ? ` (${d.code})` : ''}`
  }
  return (e as Error).message
}
/** P13's 400 codes, in plain words (the API's message follows). */
const DELTA_CODES: Record<string, string> = {
  delta_key_required: 'A delta is applied by key, and this table has none:',
  delta_duplicate_keys: 'The delta names some keys more than once:',
  delta_op_unknown: 'Some _op values are not insert / update / delete:',
  delta_key_missing: 'The key column is not in both the delta and the table:',
  delta_key_null: 'Some delta rows have no key:',
  // F-X3a
  acknowledge_required: 'The version changes what its parent promised, so it needs your acknowledgement first:',
  delta_op_column_ambiguous: 'The table already has a column with that name, so it is not read as operations unless you say so:',
  lossy_cast: 'Some values would change when stored:',
  duplicate_header: 'Two columns have the same name (ignoring case):',
  table_not_in_parent: 'The plan names a table the parent does not have:',
  too_few_rows: 'There are too few rows:',
  bad_plan: 'The plan has a field the API does not know:',
}
const ACTIONS_NEW: TableAction[] = ['new', 'skip']
const ACTIONS_NEXT: TableAction[] = ['rewrite', 'append', 'delta', 'new', 'skip']
const ACTION_HELP: Record<string, string> = {
  new: 'a new table', rewrite: 'replaces the parent\'s table; rows matched by key keep their ids', append: 'rows added after the parent\'s rows (same columns)',
  delta: 'a change set applied by key (_op: I / U / D)', skip: 'left out of the version', carry: 'kept as it is (same file, same sha256)', drop: 'removed from this version',
}
const accept = '.csv,.tsv,.txt,.parquet,.xlsx,.xlsm'
/** The parent table a delta file most likely changes: the one whose columns hold every file column but the op column. */
const guessTable = (cols: string[], parents: VTable[], op = '_op') => {
  const want = cols.filter((c) => c !== op)
  return parents.find((t) => want.every((c) => (t.schema ?? []).some((s) => s.name === c)))?.name ?? parents.find((t) => t.primary)?.name ?? parents[0]?.name ?? ''
}

/**
 * U1/U2 (C §2.2–2.4, plan §5.1/§5.3, P12/P13): one upload through the staged API. Pick one or several files (a CSV or
 * Parquet file is a table, every workbook sheet one) → **Check** (POST /uploads, then /check: nothing is written but
 * staging) → per table its action (new / rewrite / append / delta / skip; the parent's other tables carried or
 * dropped), its key and the delta counts, the relations, the typed columns with their reasons, the PII tags and the
 * contract, and for a rewritten table the row match → **Create** (POST /commit). One primary action per step.
 */
export function StagedUpload({ by, team, parent, initialFile, onDone, onCancel }: {
  by: string; team: MyTeam | null; parent?: UpParent | null; initialFile?: File | null
  onDone: (r: UpCommitted, text: string) => void; onCancel?: () => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>(initialFile ? [initialFile] : [])
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [project, setProject] = useState('')
  const [up, setUp] = useState<Upload | null>(null)
  const [chk, setChk] = useState<UpCheck | null>(null)
  const [match, setMatch] = useState<MatchSpec | null>(null)
  const [matches, setMatches] = useState<Record<string, MatchSpec>>({})  // P12: per rewritten table
  const [plan, setPlan] = useState<UploadPlan>({})  // P12/P13: the draft plan (PATCHed before a check)
  const [dirty, setDirty] = useState(false)
  const [ptables, setPtables] = useState<VTable[] | null>(null)  // the parent version's tables
  const [prels, setPrels] = useState<UpRelation[]>([])  // and its relations (carried while both tables stay)
  const [ack, setAck] = useState(false)
  const [iack, setIack] = useState<Set<string>>(new Set())  // F-X3a: integrity codes acknowledged
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const next = !!parent
  const label = files.length === 1 ? files[0].name : `${files.length} files`

  // a new dataset goes into a project of the current team (its own new project in the default team when none is chosen)
  useEffect(() => {
    if (next) return
    setProjects(null)
    catalog.projects(team?.id).then((p) => { setProjects(p); setProject((cur) => (p.some((x) => x.id === cur) ? cur : team && team.id !== 'tm_default' ? p[0]?.id ?? '' : '')) })
      .catch(() => setProjects([]))
  }, [team?.id, next])
  // the parent's tables: which ones the upload replaces, appends to or changes, and which are carried
  useEffect(() => {
    if (!parent) return
    tablesApi.version(parent.root ?? parent.id, parent.id).then((v) => { setPtables(v.tables); setPrels(v.relations ?? []) }).catch(() => setPtables([]))
  }, [parent?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  // a next version from Build's "looks like" prompt starts checking at once: nothing to choose before the check
  // (once: a second run of the effect would stage the file twice)
  const started = useRef(false)
  useEffect(() => { if (next && initialFile && by && !started.current) { started.current = true; void check([initialFile]) } }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const multi = !!chk && (chk.kind === 'multi' || chk.tables.length > 1)
  const nv = multi ? undefined : chk?.next_version
  const chosen: MatchMethod | undefined = nv?.methods?.find((m) => same(m, match)) ?? (nv?.chosen && same(nv.chosen, match) ? nv.chosen : undefined)
  // P12: every rewritten table of a multi-table check has its own match
  const rewrites = multi ? chk!.tables.filter((t) => t.action === 'rewrite' && t.next_version) : []
  const chosenOf = (t: UpTable) => t.next_version?.methods?.find((m) => same(m, matches[t.name]))
  const acks = multi ? rewrites.map(chosenOf).filter((m): m is MatchMethod => !!m && m.needs_acknowledge.length > 0) : chosen && chosen.needs_acknowledge.length ? [chosen] : []
  const needsAck = acks.length > 0
  const needProject = !next && team != null && team.id !== 'tm_default' && !project
  const looksDelta = !multi && chk?.warnings.some((w) => w.code === 'looks_like_delta')

  const check = async (fs = files) => {
    if (!fs.length) return
    setErr(''); setChk(null); setAck(false); setPlan({}); setDirty(false); setMatches({})
    try {
      setBusy('Uploading to staging…')
      if (up && up.status !== 'committed') await uploads.discard(up.upload_id).catch(() => null)
      const u = await uploads.stage(fs, { by, projectId: next ? null : project || null, parentVersionId: parent?.id })
      setUp(u)
      setBusy(next ? 'Typing the columns and matching rows to the parent…' : 'Typing the columns…')
      const c = await uploads.check(u.upload_id)
      took(c)
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  /** A fresh check: the preselected matches, and the plan as the check read it. */
  const took = (c: UpCheck) => {
    setChk(c); setDirty(false); setAck(false); setIack(new Set())
    setMatch(c.next_version?.preselect ?? null)
    setMatches(Object.fromEntries(c.tables.filter((t) => t.next_version?.preselect).map((t) => [t.name, t.next_version!.preselect!])))
  }
  /** PATCH the plan, then check again (a changed plan clears the check). */
  const replan = async (p: UploadPlan, what = 'Checking again with the plan…') => {
    if (!up) return
    setErr(''); setBusy(what)
    try {
      await uploads.plan(up.upload_id, p)
      setPlan(p)
      took(await uploads.check(up.upload_id))
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  /** F-X3a M3: the format chosen for ambiguous slash dates, sent as the plan's tables.<t>.formats (and the type DATE). */
  const withFormats = (table: string, formats: Record<string, string>) => {
    const cur = plan.tables?.[table] ?? {}
    void replan({ ...plan, tables: { ...plan.tables, [table]: { ...cur, formats: { ...cur.formats, ...formats }, types: { ...cur.types, ...Object.fromEntries(Object.keys(formats).map((c) => [c, 'DATE'])) } } } },
      `Reading ${Object.keys(formats).join(', ')} as ${Object.values(formats).join(', ')}…`)
  }
  /** F-X3a (X3 m12): the table's own _op column read as operations, by the person's choice. */
  const opColumn = (table: string, column: string) => replan({ ...plan, tables: { ...plan.tables, [table]: { ...plan.tables?.[table], op_column: column } } }, `Reading ${column} as the operations…`)
  /** P13: the looks_like_delta hint as one action: apply the file as a delta of a parent table, by key. */
  const asDelta = (table: string, o: TablePlan = {}) => replan({ table, tables: { [table]: { action: 'delta', ...(o.op_column && o.op_column !== '_op' ? { op_column: o.op_column } : {}), ...(o.ops && Object.keys(o.ops).length ? { ops: o.ops } : {}), ...(o.allow_new_columns ? { allow_new_columns: true } : {}) } } },
    `Applying ${label} as a delta of ${table}${o.op_column && o.op_column !== '_op' ? ` (operations in ${o.op_column})` : ''}…`)
  // U3: a change set whose operation column is not _op gets no looks_like_delta hint: the person opens the same box
  const [deltaOpen, setDeltaOpen] = useState(false)
  const showPlan = !!chk && (multi || chk.tables.some((t) => t.delta || t.action === 'delta'))
  /** Another candidate key: the plan's match is simulated by a fresh check (PATCH plan → /check). */
  const simulate = async (m: MatchSpec, table?: string) => {
    if (!up) return
    setErr(''); setBusy(`Simulating key ${m.columns?.join(', ')}…`)
    try {
      if (table) {
        const p = { ...plan, tables: { ...plan.tables, [table]: { ...plan.tables?.[table], match: m } } }
        await uploads.plan(up.upload_id, p); setPlan(p)
        const c = await uploads.check(up.upload_id)
        took(c); setMatches((x) => ({ ...x, [table]: m }))
      } else {
        await uploads.plan(up.upload_id, { match: m })
        const c = await uploads.check(up.upload_id)
        setChk(c); setMatch(m); setAck(false)
      }
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const commit = async () => {
    if (!up || !chk) return
    setErr(''); setBusy(next ? 'Writing the version (matched rows keep their ids)…' : 'Writing the dataset…')
    try {
      const mt = multi ? (rewrites.length ? Object.fromEntries(rewrites.map((t) => [t.name, matches[t.name]]).filter(([, m]) => m)) : undefined) : next && match ? match : undefined
      const token = (multi ? rewrites[0]?.next_version : nv)?.commit_needs?.acknowledge_token ?? 'holdout_identity_loss'
      // F-X3a: the integrity codes are acknowledged by code, beside P06's holdout token
      const acknowledge = [...(needsAck && ack ? [token] : []), ...integrityCodes.filter((c) => iack.has(c))]
      const r = await uploads.commit(up.upload_id, {
        by, check_sha256: chk.check_sha256, ...(reason.trim() ? { reason: reason.trim() } : {}),
        ...(mt ? { match: mt } : {}), ...(acknowledge.length ? { acknowledge } : {}),
      })
      setUp(null); setChk(null)
      onDone(r, resultText(r, label, parent))
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const discard = async () => { if (up) await uploads.discard(up.upload_id).catch(() => null); setUp(null); setChk(null); setFiles([]); onCancel?.() }

  const blocked = !!chk?.blocks.length
  const matchedOk = multi ? rewrites.every((t) => chosenOf(t)?.valid) : !next || (!!chosen && chosen.valid)
  const integrity = (chk?.warnings ?? []).filter((w) => (w as Issue).needs_ack && (INTEGRITY_ACKS as readonly string[]).includes(w.code)) as Issue[]
  const integrityCodes = [...new Set(integrity.map((x) => x.code))]
  const canCreate = !!chk && !blocked && !dirty && matchedOk && (!needsAck || ack) && integrityCodes.every((c) => iack.has(c))
  return (
    <div className="card-body stack up-flow" style={{ gap: 10 }} data-testid="up-flow">
      {!chk && <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn" disabled={!!busy} onClick={() => ref.current?.click()} data-testid="up-pick">{files.length ? 'Other files…' : 'Choose files…'}</button>
        <input ref={ref} type="file" multiple accept={accept} hidden onChange={(e) => { const f = [...(e.target.files ?? [])]; if (f.length) setFiles(f); e.target.value = '' }} data-testid="up-file" />
        {files.length > 0 && <span className="mono up-files" data-testid="up-filename">{files.map((f) => <span key={f.name}>{f.name} <span className="muted">{(f.size / 1024).toFixed(0)} KB</span></span>)}</span>}
        {next ? <span className="helper">next version of <b>{parent!.name}</b>, from v{parent!.v} (the default){ptables && ptables.length > 1 ? ` · its tables: ${ptables.map((t) => t.name).join(', ')}` : ''}</span>
          : <label className="row helper" style={{ gap: 6 }}>project
            <select className="select" value={project} onChange={(e) => setProject(e.target.value)} disabled={!projects} data-testid="up-project">
              {(!team || team.id === 'tm_default') && <option value="">its own new project ({team?.name ?? 'Default team'})</option>}
              {needProject && <option value="">pick a project of {team!.name}</option>}
              {(projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>{team && team.id !== 'tm_default' && projects && !projects.length && <span>{team.name} has no project yet: make one with New project.</span>}</label>}
        <span className="grow" />
        <button className="btn primary" disabled={!files.length || !by || !!busy || needProject} onClick={() => check()} data-testid="up-check"
          title={by ? 'Stages the files and types every column: nothing is written until you create it' : 'Type your name first (sidebar)'}>{files.length > 1 ? 'Check files' : 'Check file'}</button>
        {onCancel && <button className="btn ghost" disabled={!!busy} onClick={discard}>Cancel</button>}
      </div>}
      {!chk && <span className="helper" style={{ fontSize: 12 }}>Several files make one version: each CSV or Parquet file is a table, and each sheet of a workbook (.xlsx) is one.</span>}
      {busy && <p className="helper" role="status" data-testid="up-busy">… {busy}</p>}
      {err && <p className="helper up-err" role="alert" data-testid="up-err">✕ {err}</p>}
      {chk && !showPlan && (looksDelta || deltaOpen) && ptables && <DeltaHint chk={chk} parents={ptables} busy={!!busy} onApply={asDelta} hinted={!!looksDelta} />}
      {chk && !showPlan && !looksDelta && !deltaOpen && next && !multi && ptables && <button className="chip-btn" style={{ alignSelf: 'flex-start' }} onClick={() => setDeltaOpen(true)} data-testid="up-delta-open">This file is a change set (insert / update / delete by key)…</button>}
      {chk && showPlan && <PlanView chk={chk} next={next} parents={ptables ?? []} parentRels={prels} plan={plan} setPlan={(p) => { setPlan(p); setDirty(true) }} dirty={dirty} busy={!!busy} apply={() => replan(plan)} />}
      {chk && <CheckView chk={chk} label={label} onFormat={withFormats} onOpColumn={opColumn} busy={!!busy} />}
      {chk && !showPlan && next && nv && (looksDelta || deltaOpen
        ? <details data-testid="up-replace-instead"><summary className="micro-label">or replace the table with this file as it is (match rows to v{parent!.v})</summary>
          <MatchView nv={nv} parentV={parent!.v} match={match} setMatch={(m) => { setMatch(m); setAck(false) }} simulate={(m) => simulate(m)} busy={!!busy} /></details>
        : <MatchView nv={nv} parentV={parent!.v} match={match} setMatch={(m) => { setMatch(m); setAck(false) }} simulate={(m) => simulate(m)} busy={!!busy} />)}
      {chk && rewrites.map((t) => <MatchView key={t.name} nv={t.next_version!} parentV={parent?.v ?? 0} table={t.name} match={matches[t.name] ?? null}
        setMatch={(m) => { setMatches({ ...matches, [t.name]: m }); setAck(false) }} simulate={(m) => simulate(m, t.name)} busy={!!busy} />)}
      {chk && needsAck && <label className="data-warn up-ack" data-testid="up-ack">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="up-ack-box" />
        <span><b>Acknowledge to create it with {acks.map((m) => m.label).join(', ')}</b> (recorded in the version's manifest):{acks.flatMap((m) => m.warnings.filter((w) => w.needs_ack)).map((w, i) => <span key={i} className="up-ackline">⚠ {w.message}</span>)}</span>
      </label>}
      {chk && integrity.length > 0 && <IntegrityAcks items={integrity} acked={iack} setAcked={setIack} />}
      {chk && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} maxLength={500} placeholder="reason or change ticket (optional, kept with the version)" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="up-reason" />
        <button className="btn primary" disabled={!canCreate || !!busy} onClick={commit} data-testid="up-commit"
          title={blocked ? 'The check found problems that block it' : dirty ? 'Check again with the changed plan first' : (needsAck && !ack) || integrityCodes.some((c) => !iack.has(c)) ? 'Acknowledge the warning first' : undefined}>{next ? `Create v${parent!.nextV ?? parent!.v + 1}` : 'Create dataset'}</button>
        <button className="btn ghost" disabled={!!busy} onClick={discard} data-testid="up-discard">Discard</button>
      </div>}
    </div>
  )
}

const resultText = (r: UpCommitted, label: string, parent?: UpParent | null) => {
  const m = r.match
  const tables = r.tables && r.tables.length > 1 ? ` ${r.tables.length} tables: ${r.tables.map((t) => `${t.table_name} ${t.change}`).join(', ')}.` : ''
  const deltas = r.deltas ? Object.entries(r.deltas).map(([t, d]) => ` Delta on ${t}: +${n(d.inserted)} inserted, ~${n(d.updated)} updated, −${n(d.deleted)} deleted (${d.change ?? d.mode}).`).join('') : ''
  if (!parent) return `${label} is ${r.name} v${r.version_no} (${r.version_id}): ${n(r.n_rows)} rows × ${r.n_cols}${r.primary_table && r.tables && r.tables.length > 1 ? ` in ${r.primary_table}` : ''}, stored as typed Parquet.${tables}`
  return `${label} is v${r.version_no} of ${parent.name} (${r.version_id})${r.deduped ? ', identical to that existing version' : ''}: `
    + (m ? `${n(m.matched)} matched by ${m.matched_by}, ${n(m.changed)} changed, +${n(m.added)}, −${n(m.removed)}, ${n(m.holdout_identity_lost)} holdout rows lost their id. ` : '')
    + `${tables}${deltas} `
    + (r.default?.is_default ? 'It is now the default.' : `Not the default${r.default?.why ? ` (${r.default.why})` : ''}: review it, then make it the default.`)
}

/** P13: a plain next-version file with an _op column. The check says so (looks_like_delta); one click applies it as a
 *  delta of the parent table it most likely changes (the table can be changed). */
function DeltaHint({ chk, parents, busy, onApply, hinted }: { chk: UpCheck; parents: VTable[]; busy: boolean; onApply: (t: string, o: TablePlan) => void; hinted: boolean }) {
  const w = chk.warnings.find((x) => x.code === 'looks_like_delta')
  const cols = chk.tables[0]?.schema.map((c) => c.name) ?? []
  // U3: the operation column: _op when the file has one, else the person picks it (the plan's op_column), its values'
  // mapping (ops) and allow_new_columns, all sent with the plan
  const [opt, setOpt] = useState<TablePlan>(() => ({ op_column: cols.includes('_op') ? '_op' : cols.find((c) => /^(op|operation|change|action|_?change_type)$/i.test(c)) ?? '' }))
  const op = opt.op_column || '_op'
  const [t, setT] = useState(() => guessTable(cols, parents, op))
  return (
    <div className="data-warn up-delta-hint" data-testid="up-delta-hint">
      {hinted ? <><b>This file looks like a change set.</b> It has an <span className="mono">_op</span> column: as it stands it would replace the table, with <span className="mono">_op</span> as a column.</>
        : <><b>Apply this file as a change set.</b> Each row names its operation in one column; the other columns are the table's, matched by its key.</>}
      <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="helper">apply it by key to</span>
        <select className="select" value={t} onChange={(e) => setT(e.target.value)} data-testid="up-delta-table">{parents.map((p) => <option key={p.name} value={p.name}>{p.name}{p.keys.some((k) => k.valid) ? ` (key ${p.keys.find((k) => k.valid)!.columns.join('+')})` : ' (no key yet)'}</option>)}</select>
        <button className="btn sm primary" disabled={busy || !t || Object.keys(opt.ops ?? {}).some((k) => !k.trim())} onClick={() => onApply(t, opt)} data-testid="up-apply-delta">Apply as delta</button>
        <span className="helper" title={w?.message}>the other tables are carried</span>
      </div>
      {chk.tables[0] && <DeltaOptions t={chk.tables[0]} tp={opt} blocks={chk.blocks} set={(x) => { const n = { ...opt, ...x }; setOpt(n); if (x.op_column) setT(guessTable(cols, parents, x.op_column)) }} />}
    </div>
  )
}

/** P12/P13: the version the upload would make, table by table: each uploaded table's action, key and delta counts,
 *  the parent's tables carried or dropped, the relations (declared and suggested), the primary table and the sheets.
 *  Changing anything marks the plan dirty: "Check again" PATCHes it and runs the check. */
function PlanView({ chk, next, parents, parentRels, plan, setPlan, dirty, busy, apply }: { chk: UpCheck; next: boolean; parents: VTable[]; parentRels: UpRelation[]; plan: UploadPlan; setPlan: (p: UploadPlan) => void; dirty: boolean; busy: boolean; apply: () => void }) {
  const tp = (t: string) => plan.tables?.[t] ?? {}
  const setT = (t: string, x: TablePlan) => setPlan({ ...plan, tables: { ...plan.tables, [t]: { ...tp(t), ...x } } })
  const key = (t: string) => chk.keys?.find((k) => k.table === t)
  // the parent's relations are carried by the writer (recounted) while both tables stay: shown as kept, not offered
  const kept = parentRels.filter((r) => !chk.dropped?.includes(r.from.table) && !chk.dropped?.includes(r.to.table))
  const rels = [...(chk.relations ?? []), ...(chk.suggested_relations ?? []).filter((s) => !(chk.relations ?? []).some((r) => r.name === s.name))]
    .filter((r) => !kept.some((k) => k.name === r.name))
  const planned = (name: string) => (plan.relations ?? []).some((r) => (r.name ?? `${r.from.table}_${r.to.table}`) === name)
  const toggleRel = (r: NonNullable<UpCheck['relations']>[number]) => {
    const cur = plan.relations ?? (chk.relations ?? []).map((x) => ({ name: x.name, from: x.from, to: x.to, cardinality: x.cardinality as 'n:1' }))
    setPlan({ ...plan, relations: cur.some((x) => x.name === r.name) ? cur.filter((x) => x.name !== r.name) : [...cur, { name: r.name, from: r.from, to: r.to, cardinality: 'n:1' }] })
  }
  const isDeclared = (r: { name: string }) => plan.relations ? planned(r.name) : (chk.relations ?? []).some((x) => x.name === r.name)
  const notUploaded = parents.filter((p) => !chk.tables.some((t) => t.name === p.name))
  const primary = plan.primary_table ?? chk.primary_table
  return (
    <div className="data-preview" data-testid="up-plan">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>Tables in this version</b>
        <span className="helper">{chk.tables.length} from the upload{notUploaded.length ? ` · ${notUploaded.length} of the parent's not in it` : ''} · primary table (what Build and runs use by default):</span>
        <select className="select" value={primary ?? ''} onChange={(e) => setPlan({ ...plan, primary_table: e.target.value })} data-testid="up-primary">
          {[...chk.tables.map((t) => t.name), ...notUploaded.map((t) => t.name)].map((t) => <option key={t}>{t}</option>)}</select>
      </div>
      <div className="table-wrap"><table className="table up-plan-table" data-testid="up-plan-table">
        <thead><tr><th>table</th><th>from</th><th className="r">rows × cols</th><th>action</th><th>key</th><th>what happens</th><th>contract · PII</th></tr></thead>
        <tbody>
          {chk.tables.map((t) => {
            const a = tp(t.name).action ?? t.action ?? 'new'
            const k = key(t.name)
            const kText = tp(t.name).key ? tp(t.name).key!.join(', ') : k?.columns.join(', ') ?? parents.find((p) => p.name === t.name)?.keys.find((x) => x.valid)?.columns.join(', ') ?? ''
            return (
              <tr key={t.name} data-testid={`up-plan-${t.name}`} className={t.blocked ? 'up-flag' : ''}>
                <td className="mono"><b>{t.name}</b>{t.name === primary && <span className="pt-chip" style={{ marginLeft: 4 }}>primary</span>}</td>
                <td className="helper">{t.source_file}{t.sheet ? <> › {t.sheet}</> : null}</td>
                <td className="r mono">{n(t.n_rows)} × {t.n_cols}</td>
                <td><select className="select" value={a} onChange={(e) => setT(t.name, { action: e.target.value as TableAction })} data-testid={`up-action-${t.name}`} title={ACTION_HELP[a]}>
                  {(next ? ACTIONS_NEXT : ACTIONS_NEW).filter((x) => next || x !== 'rewrite').map((x) => <option key={x} value={x} disabled={next && ['rewrite', 'append', 'delta'].includes(x) && !parents.some((p) => p.name === t.name)}>{x}</option>)}</select></td>
                <td><input className="input mono" style={{ width: 130 }} placeholder="key column(s)" defaultValue={kText} key={kText}
                  onBlur={(e) => { const v = e.target.value.split(',').map((x) => x.trim()).filter(Boolean); if (v.join(',') !== kText.replace(/ /g, '')) setT(t.name, { key: v }) }} data-testid={`up-key-${t.name}`} />
                  {k && <div className={k.valid ? 'data-ok' : 'up-colwarn'} style={{ fontSize: 11.5 }}>{k.valid ? '✓ unique, no missing' : `✕ ${n(k.n_duplicates)} repeated · ${n(k.n_nulls)} missing`}</div>}</td>
                <td className="helper">{t.delta ? <DeltaCounts d={t.delta} table={t.name} /> : t.action === 'append' && (t.rows_added ?? t.append?.rows_added) != null
                  ? <span>+{n(t.rows_added ?? t.append?.rows_added)} rows after the parent's {n(t.parent_rows ?? t.append?.parent_rows)} → {n(t.rows_after ?? t.append?.rows_after)}</span>
                  : ACTION_HELP[a]}{a === 'delta' && !t.delta && <span> · check again to count it</span>}
                  {a === 'delta' && <DeltaOptions t={t} tp={tp(t.name)} blocks={chk.blocks.filter((b) => (b as { table?: string }).table === t.name || !(b as { table?: string }).table)} set={(x) => setT(t.name, x)} />}</td>
                <td>{t.contract && <span className={`pt-chip up-ct-${t.contract}`} title="the dataset contract on this table">contract {t.contract}</span>}
                  {(t.pii ?? []).map((p) => <span key={p.column} className="pt-chip up-pii" title={`${p.tag} · masked ${p.mask} · ${p.source}${p.why ? ` (${p.why})` : ''}`} data-testid={`up-pii-${t.name}-${p.column}`}>🔒 {p.column}</span>)}</td>
              </tr>)
          })}
          {notUploaded.map((p) => {
            const a = tp(p.name).action ?? (chk.dropped?.includes(p.name) ? 'drop' : 'carry')
            return (
              <tr key={p.name} className="muted" data-testid={`up-plan-${p.name}`}>
                <td className="mono">{p.name}{p.name === primary && <span className="pt-chip" style={{ marginLeft: 4 }}>primary</span>}</td>
                <td className="helper">the parent's table · sha {p.table_sha256.slice(0, 8)}</td>
                <td className="r mono">{n(p.n_rows)} × {p.n_cols}</td>
                <td><select className="select" value={a} onChange={(e) => setT(p.name, { action: e.target.value as TableAction })} data-testid={`up-action-${p.name}`}>
                  <option value="carry">carry</option><option value="drop">drop</option></select></td>
                <td className="mono helper">{p.keys.find((k) => k.valid)?.columns.join(', ') ?? '—'}</td>
                <td className="helper">{ACTION_HELP[a]}</td><td />
              </tr>)
          })}
        </tbody>
      </table></div>
      {(rels.length > 0 || kept.length > 0) && <div className="stack" style={{ gap: 4 }} data-testid="up-relations"><span className="micro-label">relations (n:1, from → to)</span>
        {kept.map((r) => <span key={r.name} className="row up-rel" style={{ gap: 6 }} data-testid={`up-rel-kept-${r.name}`}><span className="data-ok">✓</span>
          <span className="mono">{r.from.table}.{r.from.columns.join('+')} → {r.to.table}.{r.to.columns.join('+')}</span><span className="helper">kept from the parent (orphans counted again at commit)</span></span>)}
        {rels.map((r) => <label key={r.name} className="row up-rel" style={{ gap: 6 }} data-testid={`up-rel-${r.name}`}>
          <input type="checkbox" checked={isDeclared(r)} onChange={() => toggleRel(r)} />
          <span className="mono">{r.from.table}.{r.from.columns.join('+')} → {r.to.table}.{r.to.columns.join('+')}</span>
          <span className="helper">coverage {(Number(r.coverage) * 100).toFixed(1)} % · {n(r.n_orphans)} orphan{r.n_orphans === 1 ? '' : 's'} of {n(r.n_from_rows)}{r.to_unique ? '' : ' · ✕ the “to” side is not unique'}</span>
          {r.suggested && !(chk.relations ?? []).some((x) => x.name === r.name) && <span className="pt-chip">suggested</span>}
        </label>)}
        <span className="helper" style={{ fontSize: 12 }}>coverage = matched “from” rows ÷ “from” rows with a value; an orphan is a “from” value with no row on the “to” side.</span></div>}
      {(chk.sheets?.length ?? 0) > 0 && <details><summary className="micro-label">{chk.sheets!.length} sheet{chk.sheets!.length === 1 ? '' : 's'} read</summary>
        <ul className="helper up-sheets">{chk.sheets!.map((s) => <li key={`${s.file}/${s.sheet}`}><span className="mono">{s.file} › {s.sheet}</span> → {s.included ? <b>{s.table}</b> : <span className="muted">{s.why ?? (s.hidden ? 'hidden' : 'left out')}</span>} {s.included && <span className="muted">{n(s.rows)} × {s.columns}</span>}</li>)}</ul></details>}
      {dirty && <div className="row" style={{ gap: 8 }}><button className="btn primary" disabled={busy} onClick={apply} data-testid="up-recheck">Check again with this plan</button><span className="helper">the plan changed: nothing is created until it is checked</span></div>}
    </div>
  )
}

const OP_KINDS = ['insert', 'update', 'delete', 'upsert'] as const
/** U3 (P13 plan options): a delta's operation column (default _op), the mapping of its values to insert / update /
 *  delete / upsert (unmapped values: I, U, D, insert, update, delete, upsert), and whether new columns may be added
 *  (existing rows get missing values). Every change marks the plan dirty: Check again recounts the delta. */
function DeltaOptions({ t, tp, blocks, set }: { t: UpTable; tp: TablePlan; blocks: { code?: string; message: string; columns?: string[] }[]; set: (x: TablePlan) => void }) {
  const cols = t.schema.map((c) => c.name)
  const op = tp.op_column ?? t.delta?.op_column ?? (cols.includes('_op') ? '_op' : '')
  // '' = no op column: every row is an upsert (F-X3a); the plan then sends no op_column
  const ops = Object.entries(tp.ops ?? {})
  const newCols = blocks.find((b) => b.code === 'delta_new_columns')
  const setOps = (rows: [string, string][]) => set({ ops: rows.length ? Object.fromEntries(rows) : undefined })
  return (
    <div className="up-delta-opts stack" style={{ gap: 4, marginTop: 4 }} data-testid={`up-delta-opts-${t.name}`}>
      <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}><span className="micro-label">op column</span>
        <select className="select" value={op} onChange={(e) => set({ op_column: e.target.value || undefined })} data-testid={`up-opcol-${t.name}`}>
          <option value="">none: every row is an upsert</option>
          {op && !cols.includes(op) && <option value={op}>{op} (not in the file)</option>}{cols.map((c) => <option key={c}>{c}</option>)}</select></span>
      <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}><span className="micro-label">ops</span>
        {ops.map(([v, k], i) => <span key={i} className="row" style={{ gap: 2 }}>
          <input className="input mono" style={{ width: 70 }} value={v} placeholder="value" onChange={(e) => setOps(ops.map((x, j) => (j === i ? [e.target.value, x[1]] : x)))} data-testid={`up-opval-${t.name}-${i}`} />→
          <select className="select" value={k} onChange={(e) => setOps(ops.map((x, j) => (j === i ? [x[0], e.target.value] : x)))} data-testid={`up-opkind-${t.name}-${i}`}>{OP_KINDS.map((o) => <option key={o}>{o}</option>)}</select>
          <button className="chip-btn" onClick={() => setOps(ops.filter((_, j) => j !== i))} title="remove this mapping">✕</button></span>)}
        <button className="chip-btn" onClick={() => setOps([...ops, ['', 'upsert']])} data-testid={`up-opadd-${t.name}`}>+ map a value</button>
        {!ops.length && <span className="helper" style={{ fontSize: 11.5 }}>unmapped: I / U / D, insert / update / delete / upsert</span>}</span>
      <label className="row helper" style={{ gap: 4, fontSize: 12 }}><input type="checkbox" checked={!!tp.allow_new_columns} onChange={(e) => set({ allow_new_columns: e.target.checked || undefined })} data-testid={`up-allownew-${t.name}`} />
        allow new columns{newCols?.columns?.length ? <b className="up-colwarn"> ({newCols.columns.join(', ')} not in the table)</b> : ''}: existing rows get missing values</label>
      {(t.delta?.columns_new?.length ?? 0) > 0 && <span className="helper" style={{ fontSize: 11.5 }}>adds column{t.delta!.columns_new.length > 1 ? 's' : ''} {t.delta!.columns_new.join(', ')}</span>}
    </div>
  )
}

/** P13: a delta's classification against the parent table: what the commit will do, and what the diff will show. */
function DeltaCounts({ d, table }: { d: UpDelta; table: string }) {
  return (
    <span className="up-delta" data-testid={`up-delta-${table}`}>
      <b className="mono">+{n(d.inserted)} ~{n(d.updated)} −{n(d.deleted)}</b> by <span className="mono">{d.key.join('+')}</span> ({d.key_from === 'parent' ? 'the table\'s key' : 'the plan\'s key'}) ·
      rows {n(d.parent_rows)} → {n(d.rows_after)} · {d.mode === 'append' ? 'insert-only: written as an append part' : d.mode === 'carry' ? 'changes nothing: carried' : 'the table is rewritten'}
      {d.updated_unchanged > 0 && <> · {n(d.updated_unchanged)} updates change nothing</>}
      {d.missing > 0 && <> · {n(d.missing)} deletes of missing keys</>}{d.update_of_missing_key > 0 && <> · {n(d.update_of_missing_key)} updates of missing keys (inserted)</>}
      {d.insert_of_existing_key > 0 && <> · {n(d.insert_of_existing_key)} inserts of existing keys (updated)</>}
      {d.holdout_rows_deleted > 0 && <> · {n(d.holdout_rows_deleted)} holdout rows deleted</>}
    </span>
  )
}

/** The check, per table (one tab per table: multi-table versions arrive with P12): the typed columns with the reason
 *  for each type, the warnings and blocks, and the first rows. */
type IssueHandlers = { onFormat: (table: string, formats: Record<string, string>) => void; onOpColumn: (table: string, column: string) => void; busy: boolean }
function CheckView({ chk, label, ...h }: { chk: UpCheck; label: string } & IssueHandlers) {
  const [tab, setTab] = useState(chk.tables[0]?.name ?? '')
  const t = chk.tables.find((x) => x.name === tab) ?? chk.tables[0]
  return (
    <div className="data-preview" data-testid="up-check-result">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>Check: {label || t?.source_file}</b>
        <span className="helper">{chk.tables.length} table{chk.tables.length === 1 ? '' : 's'} · checked in {chk.seconds} s · nothing is written until you create it</span>
      </div>
      {chk.blocks.length > 0 && <div className="up-block" data-testid="up-blocks"><b>{chk.blocks.length} problem{chk.blocks.length === 1 ? '' : 's'} block this upload</b>{chk.blocks.map((b, i) => <IssueLine key={i} x={b as Issue} tone="block" chk={chk} {...h} />)}</div>}
      {chk.contract && <span className={`helper up-ct-line up-ct-${chk.contract.status}`} data-testid="up-contract-status">Contract{chk.contract.version ? ` v${chk.contract.version}` : ''}: <b>{chk.contract.status}</b> · {chk.contract.n_rules} rules, {chk.contract.n_blocks} blocking, {chk.contract.n_warnings} warnings</span>}
      <Tabs tabs={chk.tables.map((x) => x.name)} value={t?.name ?? ''} onChange={setTab} testIds={Object.fromEntries(chk.tables.map((x) => [x.name, `up-tab-${x.name}`]))} />
      {t && <TableCheck t={t} chk={chk} {...h} />}
    </div>
  )
}

function TableCheck({ t, chk, ...h }: { t: UpTable; chk: UpCheck } & IssueHandlers) {
  const mine = (w: { table?: string }) => !w.table || w.table === t.name
  const issues = (c: string) => [...chk.blocks, ...chk.warnings].filter((w) => w.column === c && mine(w))
  // integrity warnings (needs_ack) are shown with their acknowledgement below the check
  const other = chk.warnings.filter((w) => !w.column && mine(w) && w.code !== 'looks_like_delta' && !(w as Issue).needs_ack)
  const pii = new Map((t.pii ?? []).map((p) => [p.column, p]))
  return (
    <div className="stack" style={{ gap: 8 }} data-testid={`up-table-${t.name}`}>
      <div className="data-hero data-hero-sm">
        <div><span className="micro-label">rows</span><b data-testid="up-rows">{n(t.n_rows)}</b></div>
        <div><span className="micro-label">columns</span><b>{t.n_cols}</b></div>
        <div><span className="micro-label">from</span><b className="up-small">{t.source_file}</b></div>
        <div><span className="micro-label">read as</span><b className="up-small">{t.encoding ?? 'parquet'}{t.separator ? ` · sep ${t.separator === '\t' ? 'tab' : t.separator}` : ''}</b></div>
        <div><span className="micro-label">warnings</span><b>{chk.warnings.filter(mine).length}</b></div>
      </div>
      <div className="table-wrap up-cols"><table className="table" data-testid="up-columns">
        <thead><tr><th>column</th><th>stored as</th><th>why</th><th className="r">non-null</th></tr></thead>
        <tbody>{t.schema.map((c) => {
          const is = issues(c.name)
          return (
            <tr key={c.name} data-testid={`up-col-${c.name}`} className={is.length ? 'up-flag' : ''}>
              <td className="mono">{c.name}{pii.has(c.name) && <span className="pt-chip up-pii" style={{ marginLeft: 4 }} title={`${pii.get(c.name)!.tag}, ${pii.get(c.name)!.source}${pii.get(c.name)!.why ? `: ${pii.get(c.name)!.why}` : ''}`}>🔒 {pii.get(c.name)!.mask}</span>}</td>
              <td><span className={`pt-chip up-type t-${c.type.toLowerCase()}`}>{c.type}</span>{c.nullable && <span className="muted" title="has missing values"> ∅</span>}</td>
              <td className="helper">{c.reason}{is.map((w, i) => <div key={i} className="up-colwarn">{chk.blocks.includes(w as never) || (w.code === 'date_ambiguous' && chk.blocks.some((b) => b.code === 'date_ambiguous')) ? <>{chk.blocks.includes(w as never) ? '✕' : '⚠'} {w.message}</> : <IssueLine x={w as Issue} tone="warn" chk={chk} {...h} />}</div>)}</td>
              <td className="r mono">{c.non_null == null ? '—' : n(c.non_null)}</td>
            </tr>
          )
        })}</tbody>
      </table></div>
      {other.length > 0 && <div className="data-warn">{other.map((w, i) => <IssueLine key={i} x={w as Issue} tone="warn" chk={chk} {...h} />)}</div>}
      {chk.notes.length > 0 && <span className="helper">{chk.notes.join(' · ')}</span>}
      {pii.size > 0 && <span className="helper" style={{ fontSize: 12 }}>🔒 PII tags: {[...pii.values()].map((p) => `${p.column} (${p.tag}, masked ${p.mask}, ${p.source})`).join(' · ')}. Once created, these columns are masked for everyone without data.read_unmasked; review the tags on the dataset page.</span>}
      <details><summary className="micro-label">first {t.preview.rows.length} rows, as in the file (your own upload: not masked)</summary>
        <div className="table-wrap"><table className="table up-preview"><thead><tr>{t.preview.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{t.preview.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className="mono">{v == null ? <span className="muted">∅</span> : String(v)}</td>)}</tr>)}</tbody></table></div>
      </details>
    </div>
  )
}

/** §5.3: how the file's rows pair with the parent's. The key candidates (with their statistics), then every simulated
 *  method with its counts; the person chooses (the declared key, else the top candidate, is preselected). */
function MatchView({ nv, parentV, match, setMatch, simulate, busy, table }: { nv: NonNullable<UpCheck['next_version']>; parentV: number; match: MatchSpec | null; setMatch: (m: MatchSpec) => void; simulate: (m: MatchSpec) => void; busy: boolean; table?: string }) {
  if (!nv.methods) return <div className="data-warn" data-testid="up-match">{nv.note ?? 'Matching runs once the file checks clean.'}</div>
  const simulated = (cols: string[]) => nv.methods!.some((m) => (m.method === 'key' || m.method === 'declared') && (m.columns ?? []).join(',') === cols.join(','))
  const c = nv.contract
  return (
    <div className="data-preview" data-testid={table ? `up-match-${table}` : 'up-match'}>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>{table ? <span className="mono">{table}: </span> : null}Match rows to v{parentV} <span className="mono muted" style={{ fontWeight: 400 }}>{nv.parent_version_id}</span></b>
        <span className="helper">parent table <span className="mono">{nv.table}</span> · {n(nv.parent_rows)} rows · holdout {n(nv.parent_holdout)} rows. Matched rows keep their id, holdout and fold; choose how.</span>
      </div>
      <table className="table up-methods" data-testid="up-methods">
        <thead><tr><th /><th>match by</th><th className="r">matched</th><th className="r">changed</th><th className="r">added</th><th className="r">removed</th><th className="r" title="parent holdout rows that would get a new id">holdout identity lost</th><th /></tr></thead>
        <tbody>{nv.methods.map((m) => {
          const on = same(m, match)
          const lost = m.holdout_identity_lost ?? 0
          return (
            <tr key={m.label} className={on ? 'sel' : ''} onClick={() => m.valid && setMatch({ method: m.method, ...(m.columns ? { columns: m.columns } : {}) })} style={{ cursor: m.valid ? 'pointer' : 'default' }} data-testid={`up-m-${m.method}${m.columns ? `-${m.columns.join('_')}` : ''}`}>
              <td><input type="radio" name={`up-match-${table ?? ''}`} checked={on} disabled={!m.valid} readOnly aria-label={m.label} /></td>
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

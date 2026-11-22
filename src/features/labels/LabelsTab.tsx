import { useEffect, useMemo, useState } from 'react'
import type { DsHistory } from '@/shared/api/catalog'
import { tables } from '@/shared/api/tables'
import { isAccepted, waitJob } from '@/shared/api/jobs'
import { labels, classesOf, countsOf, arr, type Layer, type LayerKind, type Snapshot, type Task, type Proposal, type LayerStatus } from '@/shared/api/labels'
import { useFetch } from '@/shared/hooks/useFetch'
import { useCan, why } from '@/shared/hooks/useCan'
import { usePoc } from '@/shared/hooks/usePoc'
import { Badge, CardHead, Callout, ConfirmButton, CopyId, CoverageBar, Field, Flash, Stats, Why } from '@/shared/components/DataKit'
import { EmptyState } from '@/shared/components/EmptyState'

import { day, int, plural, when } from '@/shared/lib/format'
import { Busy, CarryBar, errText, STATUS_KEYS, STATUS_WORDS, totals } from './parts'
import { TaskSetup } from './TaskSetup'
import { LabelScreen } from './LabelScreen'
import { LlmPanel } from './LlmPanel'
import './labels.css'

/** Settle a 202 (job) or a direct answer to the body the synchronous route would send. */
export const settle = async <T,>(r: T): Promise<T> => (isAccepted(r) ? waitJob<T>(r.job_id) : r)
const same = (a: string, b: string) => a.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '') === b.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')

type View = { kind: 'home' } | { kind: 'new-task' } | { kind: 'label' | 'review' | 'quality'; task: string } | { kind: 'llm' }

/**
 * LB-C: Dataset › Labels. The label layers of this dataset (kept beside the versions, keyed by __row_id, so they
 * carry over to new versions by themselves), each one's carry-over status on the version in view, its tasks, LLM
 * labeling, snapshots (frozen, hashed, what a run pins) and materialise, and the correction proposals.
 */
export function LabelsTab({ d, vid, v, table, by, go }: { d: DsHistory; vid: string; v: number; table: string | null; by: string; go: (r: string) => void }) {
  const [tick, setTick] = useState(0)
  const ls = useFetch(() => labels.layers(d.root_id), [d.root_id, tick])
  const [sel, setSel] = useState<string | null>(null)
  const [view, setView] = useState<View>({ kind: 'home' })
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const vt = useFetch(() => tables.version(d.root_id, vid), [d.root_id, vid])
  const tname = table ?? vt.data?.primary_table ?? 'data'
  const cols = useMemo(() => (vt.data?.tables.find((t) => t.name === tname || t.table_name === tname)?.schema ?? []).map((c) => c.name).filter((c) => c !== '__row_id'), [vt.data, tname])
  const canLayer = useCan('label.layer_manage', 'dataset', d.root_id, by)
  const layers = ls.data ?? []
  const layer = layers.find((l) => l.id === sel) ?? layers.find((l) => l.is_default) ?? layers[0] ?? null
  useEffect(() => { if (!sel && layer) setSel(layer.id) }, [layer?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const done = (text: string) => { setMsg({ ok: true, text }); setTick((x) => x + 1) }
  const fail = (e: unknown) => setMsg({ ok: false, text: errText(e) })

  if (view.kind !== 'home' && layer) {
    const back = () => { setView({ kind: 'home' }); setTick((x) => x + 1) }
    if (view.kind === 'new-task') return <TaskSetup layer={layer} root={d.root_id} vid={vid} v={v} cols={cols} by={by} onCancel={back} onMade={(t, text) => { setMsg({ ok: true, text }); setView({ kind: 'home' }); setTick((x) => x + 1); void t }} />
    if (view.kind === 'llm') return <LlmPanel layer={layer} root={d.root_id} vid={vid} v={v} cols={cols} by={by} onBack={back} />
    return <LabelScreen key={`${view.kind}:${view.task}`} mode={view.kind} taskId={view.task} layer={layer} root={d.root_id} by={by} onBack={back} onMode={(m) => setView({ kind: m, task: view.task })} />
  }

  return (
    <div className="stack lbl" style={{ gap: 'var(--space-12)' }} data-testid="labels-tab">
      <div className="card">
        <CardHead title="Label layers" badge={<Badge status="none">{plural(layers.length, 'layer')}</Badge>}
          sub="labels and corrections kept beside the versions, keyed by __row_id: they carry over to every later version, and never change one"
          actions={<button className="btn sm primary" disabled={!by || !canLayer?.allow} title={!by ? 'Type your name first' : why(canLayer, 'A new layer on this dataset (a maintainer)')} onClick={() => setCreating(!creating)} data-testid="lbl-new-layer">New layer…</button>} />
        {creating && <NewLayer root={d.root_id} tables={vt.data?.tables.map((t) => t.name) ?? [tname]} table={tname} cols={cols} by={by}
          onCancel={() => setCreating(false)} onMade={(l) => { setCreating(false); setSel(l.id); done(`Layer ${l.name} created on ${l.table_name}${l.is_default ? ' (the default)' : ''}.`) }} onErr={fail} />}
        {!by && <Why>{'Type your name in "acting as" above: every label records who made it.'}</Why>}
        <Flash msg={msg} testId="lbl-msg" className="lbl-flash" />
        {ls.err && !ls.data ? <Callout tone="danger" title="The label layers could not be read">{ls.err}</Callout>
          : !ls.data ? <Busy>Reading the label layers…</Busy>
          : !layers.length ? <EmptyState title="No label layer yet">A layer holds the labels of one table: made by people, an LLM, rules or imports. Create one, then a task to label rows.</EmptyState>
          : <div className="table-wrap"><table className="table lbl-layers" data-testid="lbl-layers">
            <thead><tr><th>Layer</th><th>Kind</th><th>Table</th><th>Schema</th><th>Carry-over on v{v}</th><th>State</th></tr></thead>
            <tbody>{layers.map((l) => <LayerRow key={l.id} l={l} vid={vid} on={l.id === layer?.id} pick={() => setSel(l.id)} tick={tick} />)}</tbody>
          </table></div>}
      </div>
      {layer && <LayerDetail key={layer.id} layer={layer} d={d} vid={vid} v={v} cols={cols} by={by} tick={tick} go={go} open={setView} done={done} fail={fail} />}
    </div>
  )
}

function LayerRow({ l, vid, on, pick, tick }: { l: Layer; vid: string; on: boolean; pick: () => void; tick: number }) {
  const st = useFetch(() => labels.status(l.id, vid), [l.id, vid, tick])
  return (
    <tr className={on ? 'sel' : ''} onClick={pick} style={{ cursor: 'pointer' }} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && pick()} aria-selected={on} data-testid={`lbl-layer-${l.name}`}>
      <td><b>{l.name}</b>{!!l.is_default && <span className="champ-badge" style={{ marginLeft: 6 }}>★ default</span>}{l.purpose && <span className="sub-line">{l.purpose}</span>}</td>
      <td><Badge status="none">{l.kind}</Badge></td>
      <td className="mono">{l.table_name}</td>
      <td>{l.schema_version ?? l.schema?.version ? <span className="v-tag mono">s{l.schema_version ?? l.schema?.version}</span> : <span className="muted">none yet</span>}</td>
      <td>{st.data ? <CarryBar c={totals(st.data)} words={false} /> : st.err ? <span className="muted" title={st.err}>—</span> : <span className="muted">…</span>}
        {st.data && <span className="mono lbl-mini">{int(totals(st.data).live)} live{totals(st.data).stale ? ` · ${int(totals(st.data).stale)} stale` : ''}</span>}</td>
      <td><Badge status={l.state} /></td>
    </tr>
  )
}

function NewLayer({ root, tables: tbls, table, cols, by, onCancel, onMade, onErr }: { root: string; tables: string[]; table: string; cols: string[]; by: string
  onCancel: () => void; onMade: (l: Layer) => void; onErr: (e: unknown) => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<LayerKind>('labels')
  const [t, setT] = useState(table)
  const [purpose, setPurpose] = useState('')
  const [column, setColumn] = useState('')
  const [classes, setClasses] = useState('')
  const [busy, setBusy] = useState(false)
  const make = async () => {
    setBusy(true)
    try {
      const l = await labels.createLayer(root, { table: t, name: name.trim(), kind, purpose }, by)
      const cs = classes.split(',').map((x) => x.trim()).filter(Boolean)
      if (column && cs.length) await labels.addSchema(l.id, { task_type: 'class', column, classes: cs.map((c, i) => ({ value: c, hotkey: i < 9 ? String(i + 1) : undefined })) }, '', by)
      onMade(l)
    } catch (e) { onErr(e) } finally { setBusy(false) }
  }
  return (
    <div className="card-section lbl-well" data-testid="lbl-layer-form">
      <div className="form-row">
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. category" data-testid="lbl-layer-name" /></Field>
        <Field label="Kind" hint="a hint for the screens; one mechanism"><select className="select" value={kind} onChange={(e) => setKind(e.target.value as LayerKind)} data-testid="lbl-layer-kind">
          <option value="labels">labels</option><option value="corrections">corrections</option><option value="outcomes">outcomes</option></select></Field>
        <Field label="Table"><select className="select" value={t} onChange={(e) => setT(e.target.value)}>{tbls.map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label="Purpose" grow><input className="input" value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="what these labels are for" /></Field>
      </div>
      <div className="form-row">
        <Field label="Labelled column" hint="the column the classes are for"><select className="select" value={column} onChange={(e) => setColumn(e.target.value)} data-testid="lbl-layer-column">
          <option value="">later…</option>{cols.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Classes" hint="comma-separated; keys 1–9 in this order" grow><input className="input" value={classes} onChange={(e) => setClasses(e.target.value)} placeholder="e.g. ok, fraud, review" data-testid="lbl-layer-classes" /></Field>
      </div>
      <div className="row" style={{ gap: 6 }}>
        <button className="btn sm primary" disabled={!name.trim() || busy} onClick={make} data-testid="lbl-layer-create">{busy ? 'Creating…' : 'Create layer'}</button>
        <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function LayerDetail({ layer, d, vid, v, cols, by, tick, go, open, done, fail }: { layer: Layer; d: DsHistory; vid: string; v: number; cols: string[]; by: string; tick: number
  go: (r: string) => void; open: (v: View) => void; done: (t: string) => void; fail: (e: unknown) => void }) {
  void go
  const full = useFetch(() => labels.layer(layer.id), [layer.id, tick])
  const L = full.data ?? layer
  const classes = classesOf(L.schema?.spec?.classes)
  return (
    <>
      <StatusCard layer={L} d={d} vid={vid} v={v} tick={tick} />
      <div className="grid two">
        <TasksCard layer={L} by={by} tick={tick} open={open} root={d.root_id} />
        <div className="card" data-testid="lbl-schema">
          <CardHead title="Schema and guidelines" badge={L.schema ? <span className="v-tag mono">s{L.schema.version}</span> : <Badge status="none">none</Badge>}
            sub="the classes (with their keys) and the instructions labellers see" />
          <div className="card-body stack" style={{ gap: 8 }}>
            {L.schema ? <>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}><span className="micro-label">{L.schema.spec.column ?? arr(L.schema.spec.columns).join(', ')}</span>
                {classes.map((c) => <span key={c.value} className="pt-chip lbl-class">{c.hotkey && <kbd className="kbd">{c.hotkey}</kbd>}{c.label ?? c.value}</span>)}</div>
              {L.schema.guidelines ? <pre className="lbl-guidelines">{L.schema.guidelines}</pre> : <span className="muted">No guidelines written yet.</span>}
            </> : <span className="muted">No schema yet: a task sets one.</span>}
            <div className="row" style={{ gap: 6, marginTop: 4 }}>
              <button className="btn sm" onClick={() => open({ kind: 'llm' })} disabled={!L.schema} title={L.schema ? 'Prompts, the PII guard, calibration against gold and runs' : 'A schema first'} data-testid="lbl-open-llm">LLM labeling…</button>
            </div>
          </div>
        </div>
      </div>
      <SnapshotsCard layer={L} d={d} vid={vid} v={v} cols={cols} by={by} tick={tick} done={done} fail={fail} />
      <ProposalsCard layer={L} d={d} by={by} tick={tick} done={done} fail={fail} />
    </>
  )
}

function StatusCard({ layer, d, vid, v, tick }: { layer: Layer; d: DsHistory; vid: string; v: number; tick: number }) {
  const [on, setOn] = useState(vid)
  useEffect(() => setOn(vid), [vid])
  const st = useFetch<LayerStatus>(() => labels.status(layer.id, on), [layer.id, on, tick])
  const live = d.versions.filter((x) => x.state !== 'deleted')
  const onV = live.find((x) => x.id === on)?.v ?? v
  const t = totals(st.data)
  const all = STATUS_KEYS.reduce((a, k) => a + t[k], 0)
  const by = st.data?.by_source ?? {}
  return (
    <div className="card" data-testid="lbl-status">
      <CardHead title={<>Carry-over: <span className="mono">{layer.name}</span> on <span className="v-tag mono">v{onV}</span></>}
        sub="each label checked against the row it was made on: changed rows go stale and back to review, deleted rows' labels are kept but not applied"
        actions={<Field label="against"><select className="select" value={on} onChange={(e) => setOn(e.target.value)} data-testid="lbl-status-version">
          {live.map((x) => <option key={x.id} value={x.id}>v{x.v}{x.id === d.default_version_id ? ' (default)' : ''}</option>)}</select></Field>} />
      <div className="card-body stack" style={{ gap: 10 }}>
        {st.err && !st.data ? <Callout tone="danger" title="Status not available">{st.err}</Callout> : !st.data ? <Busy>Checking every label against v{onV}…</Busy> : <>
          <Stats testId="lbl-status-stats" items={[
            ...STATUS_KEYS.map((k) => ({ label: k, value: int(t[k]), testId: `lbl-st-${k}`, title: STATUS_WORDS[k] })),
            ...(t.base_changed ? [{ label: 'base changed', value: int(t.base_changed), title: 'the version itself now has a different value in a labelled cell' }] : []),
          ]} />
          <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <CarryBar c={t} testId="lbl-carry" />
            <span className="muted">labelled share</span><CoverageBar m={t.live} N={all - t.orphaned || null} wide />
          </div>
          {(st.data.columns?.length ?? 0) > 0 && <div className="table-wrap"><table className="table" data-testid="lbl-status-cols">
            <thead><tr><th>Column</th><th>Carry-over</th>{STATUS_KEYS.map((k) => <th key={k} className="r">{k}</th>)}</tr></thead>
            <tbody>{st.data.columns!.map((c) => <tr key={c.column}><td className="mono">{c.column}</td><td><CarryBar c={c} words={false} /></td>
              {STATUS_KEYS.map((k) => <td key={k} className={`r mono ${c[k] ? `lbl-n-${k}` : 'muted'}`}>{int(c[k] ?? 0)}</td>)}</tr>)}</tbody></table></div>}
          {Object.keys(by).length > 0 && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}><span className="micro-label">sources</span>
            {Object.entries(by).map(([s, n]) => <span key={s} className="pt-chip" data-testid={`lbl-src-${s}`}>{s} <b className="mono">{int(n)}</b></span>)}</div>}
          {(t.stale > 0 || t.conflict > 0) && <Callout tone="warn" title={`${plural(t.stale + t.conflict, 'label')} need a look on v${onV}`} testId="lbl-stale-callout">
            Their rows changed after they were labelled, so they are not applied to training until a person confirms or relabels them (a task with the “stale” fill).</Callout>}
        </>}
      </div>
    </div>
  )
}

function TasksCard({ layer, by, tick, open, root }: { layer: Layer; by: string; tick: number; open: (v: View) => void; root: string }) {
  const ts = useFetch(() => labels.tasks(layer.id), [layer.id, tick])
  const canTask = useCan('label.task_manage', 'dataset', root, by)
  return (
    <div className="card" data-testid="lbl-tasks">
      <CardHead title="Tasks" badge={ts.data ? <Badge status="none">{ts.data.length}</Badge> : undefined} sub="who labels which rows, with gold checks and review"
        actions={<button className="btn sm primary" disabled={!by || !canTask?.allow} title={!by ? 'Type your name first' : why(canTask, 'Schema, guidelines, queue, people and gold')} onClick={() => open({ kind: 'new-task' })} data-testid="lbl-new-task">Create task…</button>} />
      <div className="card-body">
        {ts.err && !ts.data ? <Callout tone="danger">{ts.err}</Callout> : !ts.data ? <Busy>Reading tasks…</Busy> : !ts.data.length ? <span className="muted">No task yet.</span>
          : <table className="table lbl-tasks-t"><tbody>{ts.data.map((t) => <TaskRow key={t.id} t={t} open={open} />)}</tbody></table>}
      </div>
    </div>
  )
}

function TaskRow({ t, open }: { t: Task; open: (v: View) => void }) {
  const p = t.progress ?? {}
  const total = p.total ?? (p.open ?? 0) + (p.leased ?? 0) + (p.done ?? 0) + (p.skipped ?? 0)
  return (
    <tr data-testid={`lbl-task-${t.name}`}>
      <td><b>{t.name}</b><span className="sub-line">{t.task_type.replace(/_/g, ' ')} · {t.votes_per_row ?? 1} vote{(t.votes_per_row ?? 1) > 1 ? 's' : ''}/row · gold {Math.round(Number(t.gold_share ?? 0) * 100)}%{t.members ? ` · ${plural(t.members.length, 'person', 'people')}` : ''}</span></td>
      <td><CoverageBar m={p.done ?? 0} N={total || null} /><span className="sub-line mono">{int(p.done ?? 0)} / {int(total)} done{p.disputed ? ` · ${p.disputed} disputed` : ''}</span></td>
      <td><Badge status={t.state} /></td>
      <td className="r nowrap">
        <button className="btn sm primary" onClick={() => open({ kind: 'label', task: t.id })} data-testid={`lbl-task-open-${t.name}`}>Label</button>{' '}
        <button className="btn sm" onClick={() => open({ kind: 'review', task: t.id })} data-testid={`lbl-task-review-${t.name}`}>Review</button>{' '}
        <button className="btn sm ghost" onClick={() => open({ kind: 'quality', task: t.id })} data-testid={`lbl-task-quality-${t.name}`}>Quality</button>
      </td>
    </tr>
  )
}

function SnapshotsCard({ layer, d, vid, v, cols, by, tick, done, fail }: { layer: Layer; d: DsHistory; vid: string; v: number; cols: string[]; by: string; tick: number; done: (t: string) => void; fail: (e: unknown) => void }) {
  const ss = useFetch(() => labels.snapshots(layer.id), [layer.id, tick])
  const canSnap = useCan('label.snapshot', 'dataset', d.root_id, by)
  const canApprove = useCan('label.snapshot_approve', 'dataset', d.root_id, by)
  const canVersion = useCan('data.version', 'dataset', d.root_id, by)
  const poc = usePoc()
  const [policy, setPolicy] = useState('majority')
  const [acceptLlm, setAcceptLlm] = useState(false)
  const [busy, setBusy] = useState('')
  const [mat, setMat] = useState<{ id: string; reason: string; target: string } | null>(null)
  const [checks, setChecks] = useState<Record<string, string>>({})
  const take = async () => {
    setBusy('snap')
    try {
      const r = await settle(await labels.snapshot(layer.id, { method: policy, accept: { llm: acceptLlm } }, by))
      const s = r as Partial<Snapshot>
      done(`Snapshot ${s.id ?? (r as { snapshot_id?: string }).snapshot_id ?? ''} taken through event ${s.through_seq ?? '…'}: frozen and hashed; a run can pin it.`)
    } catch (e) { fail(e) } finally { setBusy('') }
  }
  const approve = async (s: Snapshot) => { try { await labels.approveSnapshot(s.id, 'approved in the Labels tab', by); done(`Snapshot ${s.id} approved by ${by}.`) } catch (e) { fail(e) } }
  const verify = async (s: Snapshot) => { try { const r = await labels.verifySnapshot(s.id); setChecks((c) => ({ ...c, [s.id]: r.ok ? 'ok' : 'mismatch' })) } catch (e) { fail(e) } }
  const materialise = async () => {
    if (!mat) return
    setBusy(mat.id)
    try {
      const r = await settle(await labels.materialise(mat.id, { version_id: mat.target, reason: mat.reason, holdout: 'original' }, by))
      const nv = (r as { version_id?: string; id?: string }).version_id ?? (r as { id?: string }).id
      setMat(null); done(`Materialised into a new version${nv ? ` (${nv})` : ''}: labels applied to the training rows, the holdout kept as it was.`)
    } catch (e) { fail(e) } finally { setBusy('') }
  }
  const live = d.versions.filter((x) => x.state !== 'deleted')
  void cols
  return (
    <div className="card" data-testid="lbl-snapshots">
      <CardHead title="Snapshots" badge={ss.data ? <Badge status="none">{ss.data.length}</Badge> : undefined}
        sub="a frozen, hashed copy of the resolved labels: what a run pins, what is approved, what is materialised into a version"
        actions={<span className="row" style={{ gap: 6 }}>
          <select className="select" value={policy} onChange={(e) => setPolicy(e.target.value)} aria-label="Resolution" data-testid="lbl-snap-policy" title="how several votes on a row become one label">
            <option value="majority">majority vote</option><option value="crowdlab">CROWDLAB</option><option value="single">single (latest)</option></select>
          <label className="helper row" style={{ gap: 4 }} title="apply LLM labels above the calibrated threshold τ*; below it they stay pre-labels"><input type="checkbox" checked={acceptLlm} onChange={(e) => setAcceptLlm(e.target.checked)} /> accept LLM ≥ τ*</label>
          <button className="btn sm primary" disabled={!by || !canSnap?.allow || busy === 'snap'} title={why(canSnap, 'Freeze the labels up to now')} onClick={take} data-testid="lbl-snap-take">{busy === 'snap' ? 'Taking…' : 'Take snapshot'}</button>
        </span>} />
      <div className="card-body">
        {ss.err && !ss.data ? <Callout tone="danger">{ss.err}</Callout> : !ss.data ? <Busy>Reading snapshots…</Busy> : !ss.data.length ? <span className="muted">No snapshot yet.</span>
          : <div className="table-wrap"><table className="table" data-testid="lbl-snap-list">
            <thead><tr><th>Snapshot</th><th>Through</th><th className="r">Applied</th><th className="r">Disputed</th><th className="r">Pre-label only</th><th>State</th><th>Made</th><th /></tr></thead>
            <tbody>{ss.data.map((s) => {
              const c = countsOf(s)
              const own = !poc && same(s.created_by, by)
              return <tr key={s.id} data-testid={`lbl-snap-${s.id}`}>
                <td><CopyId value={s.id} /> <CopyId value={s.snapshot_sha256} n={8} />{checks[s.id] && <Badge status={checks[s.id]}>{checks[s.id] === 'ok' ? 'verified' : 'mismatch'}</Badge>}</td>
                <td className="mono">event {int(s.through_seq)}{s.schema_version != null && <span className="muted"> · s{s.schema_version}</span>}</td>
                <td className="r mono">{int(c.applied)}</td><td className="r mono">{int(c.disputed)}</td><td className="r mono">{int(c.prelabel_only)}</td>
                <td><Badge status={s.state} />{s.approved_by && <span className="sub-line">by {s.approved_by}</span>}</td>
                <td className="muted" title={when(s.created_at)}>{s.created_by} · {day(s.created_at)}</td>
                <td className="r nowrap">
                  <button className="btn sm ghost" onClick={() => verify(s)} data-testid={`lbl-snap-verify-${s.id}`} title="Re-hash the blob against the recorded sha">Verify</button>{' '}
                  {s.state === 'draft' && <button className="btn sm" disabled={!by || !canApprove?.allow || own} onClick={() => approve(s)} data-testid={`lbl-snap-approve-${s.id}`}
                    title={own ? `Four eyes: ${s.created_by} took it, so someone else approves` : why(canApprove, 'Approve (required for certified datasets and shares)')}>Approve</button>}{' '}
                  <button className="btn sm" disabled={!by || !canVersion?.allow || s.state === 'purged'} onClick={() => setMat({ id: s.id, reason: '', target: vid })} data-testid={`lbl-snap-mat-${s.id}`}
                    title={why(canVersion, 'Write these labels into a new version (explicit; the holdout is not relabelled)')}>Materialise…</button>
                </td>
              </tr>
            })}</tbody></table></div>}
        {mat && <div className="card-section lbl-well" data-testid="lbl-mat-form">
          <div className="form-row">
            <Field label="Into a new version of"><select className="select" value={mat.target} onChange={(e) => setMat({ ...mat, target: e.target.value })} data-testid="lbl-mat-version">
              {live.map((x) => <option key={x.id} value={x.id}>v{x.v}{x.id === vid ? ' (in view)' : ''}</option>)}</select></Field>
            <Field label="Reason" grow><input className="input" value={mat.reason} onChange={(e) => setMat({ ...mat, reason: e.target.value })} placeholder="why these labels become a version" data-testid="lbl-mat-reason" /></Field>
          </div>
          <Callout tone="info" title="What materialise does">Applies the snapshot's labels to the training rows of v{live.find((x) => x.id === mat.target)?.v ?? v} and records it as a new version (op <span className="mono">apply_labels</span>, with the snapshot's sha). Holdout rows keep their original labels; stale labels are not applied.</Callout>
          <div className="row" style={{ gap: 6 }}>
            <ConfirmButton className="btn sm primary" disabled={!mat.reason.trim() || busy === mat.id} onConfirm={materialise} testId="lbl-mat-go" confirm="Create the version"
              warning={`A new version of ${d.name}`}>{busy === mat.id ? 'Materialising…' : 'Materialise to version'}</ConfirmButton>
            <button className="btn sm ghost" onClick={() => setMat(null)}>Cancel</button>
            {!mat.reason.trim() && <Why>A reason first: it goes in the version's history.</Why>}
          </div>
        </div>}
      </div>
    </div>
  )
}

function ProposalsCard({ layer, d, by, tick, done, fail }: { layer: Layer; d: DsHistory; by: string; tick: number; done: (t: string) => void; fail: (e: unknown) => void }) {
  const ps = useFetch(() => labels.proposals(layer.id), [layer.id, tick])
  const canDecide = useCan('label.proposal_decide', 'dataset', d.root_id, by)
  const poc = usePoc()
  const [comment, setComment] = useState('')
  const decide = async (p: Proposal, a: 'approve' | 'reject') => {
    try { const r = await settle(await labels.decide(p.id, a, comment, by)); setComment(''); done(`Proposal ${a === 'approve' ? 'approved' : 'rejected'}${(r as Proposal).applied_version_id ? `: version ${(r as Proposal).applied_version_id}` : ''}.`) } catch (e) { fail(e) }
  }
  const pending = (ps.data ?? []).filter((p) => p.status === 'pending')
  return (
    <div className="card" data-testid="lbl-proposals">
      <CardHead title="Correction proposals" badge={<Badge status={pending.length ? 'pending' : 'none'}>{pending.length ? `${pending.length} waiting` : 'none waiting'}</Badge>}
        sub="edits and Data Doctor fixes sent for review; approved ones become a version" />
      <div className="card-body stack" style={{ gap: 6 }}>
        {ps.err && !ps.data ? <Callout tone="danger">{ps.err}</Callout> : !ps.data ? <Busy>Reading proposals…</Busy> : !ps.data.length ? <span className="muted">No proposal yet.</span>
          : ps.data.map((p) => {
            const own = !poc && same(p.proposed_by, by)
            return <div key={p.id} className="gov-item" data-testid={`lbl-prop-${p.id}`}>
              <div className="gov-line"><Badge status={p.status} /><b>{plural(p.n_cells, 'cell')}</b><Badge status="none">{p.origin.replace(/_/g, ' ')}</Badge><span className="gov-reason" title={p.reason}>“{p.reason}”</span><span className="spacer" /><CopyId value={p.id} /></div>
              <span className="sub-line">proposed by {p.proposed_by} · {day(p.proposed_at)}{p.decided_by ? ` · ${p.status} by ${p.decided_by}` : ''}{p.applied_version_id ? ` · version ${p.applied_version_id}` : ''}</span>
              {(p.cells?.length ?? 0) > 0 && <div className="lbl-cells">{p.cells!.slice(0, 6).map((c, i) => <span key={i} className="data-cell"><span className="mono">#{c.row_id} {c.column}</span> {c.masked ? '•••' : String(c.old ?? '∅')} → <b>{c.masked ? '•••' : String(c.new ?? '∅')}</b></span>)}</div>}
              {p.status === 'pending' && <div className="row" style={{ gap: 6, marginTop: 4 }}>
                <input className="input" style={{ flex: 1 }} placeholder="comment (required to reject)" value={comment} onChange={(e) => setComment(e.target.value)} />
                <button className="btn sm" disabled={!by || !canDecide?.allow || own} title={own ? `Four eyes: ${p.proposed_by} proposed it, so a second person decides` : why(canDecide)} onClick={() => decide(p, 'approve')} data-testid={`lbl-prop-approve-${p.id}`}>Approve</button>
                <button className="btn sm ghost" disabled={!by || !canDecide?.allow || own || !comment.trim()} title={own ? 'Four eyes' : why(canDecide)} onClick={() => decide(p, 'reject')}>Reject</button>
              </div>}
              {p.status === 'pending' && own && <Why>{`Four eyes: ${p.proposed_by} proposed it, so someone else decides.`}</Why>}
            </div>
          })}
      </div>
    </div>
  )
}

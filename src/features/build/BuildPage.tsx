import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Intake, IntakeColumn, IntakeSuggestion } from '@/shared/api/types'
import { DropZone } from '@/shared/components/DropZone'
import { ApiOnly, HeroCell } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Segmented } from '@/shared/primitives/Segmented'
import { DataReport } from './DataReport'
import { FeatureLab } from './FeatureLab'
import { SqlLineageLine, SqlSource } from './SqlSource'
import { VersionStrip } from './VersionStrip'
import { data as dsApi } from '@/shared/api/catalog'

/** The roles the hero strip names; every other role is listed under "what we found". */
const EXCLUDED = ['id', 'leak', 'constant', 'protected', 'surname', 'zip', 'decision']  // G4: protected attributes, surname and zip are never model inputs; G7: nor the approve/decline decision
const SPLIT_NAME: Record<string, string> = { time: 'out-of-time', group: 'group', 'time-group': 'out-of-time + group' }

/** The flow's URL for a suggestion: New Model, Forecast and Anomaly open pre-filled on this dataset. */
function href(s: IntakeSuggestion, dsId: string): string {
  if (s.route === 'quick-train') {
    const p = s.prefill
    const q = new URLSearchParams({ dataset: dsId, target: p.target ?? '' })
    if (p.split) q.set('split', p.split)
    if (p.time) q.set('time', p.time)
    if (p.group) q.set('group', p.group)
    if (p.drop?.length) q.set('drop', p.drop.join(','))
    if (p.metric) q.set('metric', p.metric)
    return `quick-train?${q}`
  }
  if (s.route === 'registry') return `registry?${new URLSearchParams({ version: String(s.prefill.version ?? ''), tab: String(s.prefill.tab ?? 'Overview') })}`
  if (s.route === 'survival') {  // G6: the duration and event as Build shows them (edits included)
    const p = s.prefill as { duration?: string; event?: string }
    return `survival?${new URLSearchParams({ dataset: dsId, duration: p.duration ?? '', event: p.event ?? '' })}`
  }
  if (s.route === 'reject-inference') {  // G7: the outcome and decision as Build shows them (edits included)
    const p = s.prefill as { label?: string; decision?: string | null }
    return `reject-inference?${new URLSearchParams({ dataset: dsId, label: p.label ?? '', decision: p.decision ?? '' })}`
  }
  if (s.route === 'segmented') {  // G5: the target and segment as Build shows them (edits included)
    const p = s.prefill as { label?: string; segment?: string }
    return `segmented?${new URLSearchParams({ dataset: dsId, label: p.label ?? '', segment: p.segment ?? '' })}`
  }
  if (s.route === 'expected-loss') {  // G3: the roles as Build shows them (edits included) pre-fill the setup
    const p = s.prefill as { pd?: string; lgd?: string; ead?: string; segments?: string[] }
    return `expected-loss?${new URLSearchParams({ dataset: dsId, pd: p.pd ?? '', lgd: p.lgd ?? '', ead: p.ead ?? '', segments: (p.segments ?? []).join(',') })}`
  }
  return s.route === 'import' ? 'import' : `${s.route}?dataset=${encodeURIComponent(dsId)}`
}

/** What a suggestion pre-fills, in one line. */
function prefillLine(s: IntakeSuggestion): string {
  const p = s.prefill
  if (s.route === 'forecast') return `t = ${p.timestamp}${p.item_id ? ` · series = ${p.item_id}` : ''} · y = ${p.value}`
  if (s.route === 'multilabel') return `Y = ${p.label_column} · ${(p.labels as string[] | undefined)?.length ?? ''} labels · one model per label`
  if (s.route === 'survival') return `T = ${p.duration} · δ = ${p.event} · Cox PH + survival forest`
  if (s.route === 'reject-inference') return `y = ${p.label}${p.decision ? ` · A = ${p.decision}` : ''} · approved-only vs 3 methods, k = 2`
  if (s.route === 'segmented') return `y = ${p.label} · one model per ${p.segment} vs one global`
  if (s.route === 'expected-loss') return `y = ${p.pd} · LGD = ${p.lgd} · EAD = ${p.ead}${(p.segments as string[] | undefined)?.length ? ` · by ${(p.segments as string[]).join(', ')}` : ''}`
  if (s.route !== 'quick-train') return ''
  return [`y = ${p.target}`, p.split ? `${SPLIT_NAME[p.split]} split` : 'random split', p.drop?.length ? `drop ${p.drop.join(', ')}` : '',
    p.metric === 'expected_cost' ? 'business cost' : ''].filter(Boolean).join(' · ')
}

/**
 * Build (G0 Smart Intake): the single entry point. Upload a table → what each column is for (editable) → what you can
 * build from it, best first; each choice opens its flow (New Model, Forecast, Anomaly, Import) pre-filled. The Training
 * Gym is the Advanced switch. Every role and suggestion comes from GET /api/datasets/{id}/intake with its evidence.
 */
export function BuildPage({ go }: { go: (r: string) => void }) {
  return (
    <ApiOnly mock={(
      <div className="page"><BuildHead go={go} />
        <div className="card card-body" data-testid="build-mock"><p className="helper">▲ mock mode: Smart Intake reads your table on the API, which is offline. Start it with <span className="mono">make dev</span>. Meanwhile New Model plays a simulated run on built-in samples.</p>
          <button className="btn primary" onClick={() => go('quick-train')}>New Model (simulated) →</button></div></div>
    )}>
      <BuildLive go={go} />
    </ApiOnly>
  )
}

function BuildHead({ go }: { go: (r: string) => void }) {
  return (
    <div className="page-head">
      <div><h1 className="page-title">Build</h1><p className="helper sub">Upload a table or query a database → here is what we found → here is what you can build.</p></div>
      <div className="seg" role="tablist" aria-label="Mode">
        <button className="on" role="tab" aria-selected>Guided</button>
        <button role="tab" aria-selected={false} onClick={() => go('gym')} data-testid="build-advanced" title="Every AutoGluon fit() argument: the Training Gym">Advanced</button>
      </div>
    </div>
  )
}

function BuildLive({ go }: { go: (r: string) => void }) {
  const dsId = useHashParam('dataset')
  const setDs = (id: string) => { location.hash = `#/build?dataset=${encodeURIComponent(id)}` }
  const list = useFetch(() => api.datasets.list(), [])
  const [edits, setEdits] = useState<Record<string, string>>({})
  useEffect(() => setEdits({}), [dsId])
  const edited = Object.keys(edits).length > 0
  const intake = useFetch(dsId ? () => api.datasets.intake(dsId, edited ? edits : undefined) : null, [dsId, JSON.stringify(edits)])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [refreshed, setRefreshed] = useState<{ id: string; msg: string } | null>(null)
  const [fixed, setFixed] = useState<{ id: string; msg: string } | null>(null)  // D1: the version the Data Report just made
  const [from, setFrom] = useState<'CSV file' | 'SQL query'>('CSV file')  // G9: a second way in
  const detail = useFetch(dsId ? () => api.datasets.get(dsId) : null, [dsId])
  const sqlDs = detail.data?.id === dsId && (detail.data?.profile.lineage as { source?: string } | undefined)?.source === 'sql' ? detail.data : null

  const upload = async (f: File) => {
    setBusy('Uploading and profiling…'); setErr('')
    try { const d = await api.datasets.upload(f); list.reload(); setDs(d.id) } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const open = async (s: IntakeSuggestion) => {
    if (!dsId) return
    setErr('')
    try {
      // New Model trains on a confirmed target (F4): choosing the suggestion confirms the target it names.
      if (s.route === 'quick-train' && s.prefill.target) {
        const d = await api.datasets.get(dsId)
        if (d.target !== s.prefill.target || d.profile.target_source === 'guessed') { setBusy(`Confirming y = ${s.prefill.target}…`); await api.datasets.setTarget(dsId, s.prefill.target) }
      }
      go(href(s, dsId))
    } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const data = dsId ? intake.data : null
  const current = data?.dataset_id === dsId ? data : null

  return (
    <div className="page build" data-testid="build">
      <BuildHead go={go} />
      {dsId && fixed?.id === dsId && <p className="helper dr-made" role="status" data-testid="dr-made">✓ {fixed.msg}</p>}
      {dsId && <DataReport dsId={dsId} onVersion={(id, msg) => { setFixed({ id, msg }); list.reload(); setDs(id) }} />}
      <div className="grid build-top">
        <div className="card">
          <div className="card-head"><h3 className="card-title">1 · Your table</h3><Segmented options={['CSV file', 'SQL query']} value={from} onChange={setFrom} /></div>
          <div className="card-body stack" style={{ gap: 12 }}>
            {from === 'CSV file'
              ? <DropZone title={busy || 'Drop a CSV here'} helper="or click to browse. It is uploaded to the local API and profiled there." onFile={upload} onError={setErr} />
              : <SqlSource onLoaded={(d) => { list.reload(); setDs(d.id) }} />}
            {/* DL1: one entry per dataset (its root); choosing one opens its default version, the strip below picks another */}
            <select className="select" value={(list.data ?? []).find((d) => d.id === dsId)?.root_id ?? dsId ?? ''} onChange={(e) => { const id = e.target.value; dsApi.history(id).then((h) => setDs(h.default_version_id ?? id)).catch(() => setDs(id)) }} aria-label="Dataset" data-testid="build-dataset">
              <option value="" disabled>…or pick a dataset on this machine</option>
              {(list.data ?? []).filter((d) => !d.parent_id || !(list.data ?? []).some((x) => x.id === d.root_id)).map((d) => <option key={d.id} value={d.id}>{d.name} · {d.rows.toLocaleString()} rows × {d.n_cols}</option>)}
            </select>
            {dsId && <VersionStrip dsId={dsId} onPick={setDs} go={go} />}
            {(err || list.err) && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err || list.err}</p>}
            {sqlDs && <SqlLineageLine ds={sqlDs} note={refreshed?.id === sqlDs.id ? refreshed.msg : ''} onRefreshed={(id, msg) => { setRefreshed({ id, msg }); list.reload(); setDs(id) }} />}
          </div>
        </div>
        {current ? <Found intake={current} edits={edits} setEdits={setEdits} />
          : <div className="card card-body row helper">{dsId ? (intake.err ? <span style={{ color: 'var(--status-danger)' }}>✕ {intake.err}</span> : <><ResonanceWave /> Reading every column…</>) : 'Drop a CSV, query a database or pick a dataset: Smart Intake finds the target, dates, IDs, money, segments and what to leave out, and says why.'}</div>}
      </div>
      {current && <Suggestions intake={current} onOpen={open} busy={!!busy} />}
      {current && dsId && <FeatureLab dsId={dsId} entities={current.roles.entity ?? []} onVersion={(id, msg) => { setFixed({ id, msg }); list.reload(); setDs(id) }} />}
    </div>
  )
}

/** 2 · What we found: the roles as editable chips, the hero strip, and the evidence behind each role. */
function Found({ intake, edits, setEdits }: { intake: Intake; edits: Record<string, string>; setEdits: (e: Record<string, string>) => void }) {
  const r = intake.roles
  const first = (k: string) => r[k]?.[0] ?? '—'
  const excluded = intake.columns.filter((c) => EXCLUDED.includes(c.role))
  const setRole = (c: IntakeColumn, role: string) => setEdits({ ...edits, [c.name]: role })
  return (
    <div className="card" data-testid="build-found">
      <div className="card-head">
        <div className="left"><h3 className="card-title">2 · What we found</h3><span className="helper" style={{ fontSize: 12 }}>click a role to change it</span></div>
        {Object.keys(edits).length > 0 && <button className="btn sm ghost" onClick={() => setEdits({})} data-testid="build-reset">Reset to detected</button>}
      </div>
      <div className="card navy explain-hero build-hero">
        <HeroCell label="rows n" sub={`${intake.columns.length} columns`}>{intake.rows.toLocaleString()}</HeroCell>
        <HeroCell label="target y" sub="what to predict">{first('target')}</HeroCell>
        <HeroCell label="date t" sub="orders the rows">{first('date')}</HeroCell>
        <HeroCell label="entity g" sub="rows repeat per value">{first('entity')}</HeroCell>
        <HeroCell label="left out" sub={excluded.length ? excluded.map((c) => c.name).join(', ') : 'nothing'}>{excluded.length}</HeroCell>
      </div>
      <div className="card-body role-chips" data-testid="build-roles">
        {intake.columns.map((c) => (
          <label key={c.name} className={`role-chip r-${c.role} ${c.overridden ? 'edited' : ''}`} title={`${c.evidence.join(' · ')}${c.also.length ? `\nalso: ${c.also.map((a) => a.role).join(', ')}` : ''}`} data-testid={`role-${c.name}`}>
            <span className="mono">{c.name}</span>
            <select value={c.role} onChange={(e) => setRole(c, e.target.value)} aria-label={`Role of ${c.name}`}>
              {Object.entries(intake.role_names).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <small className="mono">{Math.round(c.confidence * 100)}%</small>
          </label>
        ))}
      </div>
      <details className="an-adv build-why" data-testid="build-why"><summary className="micro-label">Why · the evidence for every role</summary>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Column</th><th>Role</th><th className="r">Confidence</th><th>Evidence</th></tr></thead>
          <tbody>{intake.columns.map((c) => (
            <tr key={c.name}><td className="table-cell">{c.name}</td><td>{intake.role_names[c.role]}</td><td className="r mono">{c.confidence.toFixed(2)}</td>
              <td className="helper">{c.evidence.join(' · ')}{c.also.length > 0 && <span className="muted"> · also {c.also.map((a) => `${a.role} (${a.confidence.toFixed(2)})`).join(', ')}</span>}</td></tr>
          ))}</tbody>
        </table></div>
        <p className="helper" style={{ fontSize: 12, margin: 0, padding: '8px 16px' }}>Rules, not a model: name patterns, value patterns, ranges and cardinality. Confidence is the rule&rsquo;s own weight (0–1), not a probability.</p>
      </details>
    </div>
  )
}

/** 3 · What you can build: best first. The top one is the page's primary action. */
function Suggestions({ intake, onOpen, busy }: { intake: Intake; onOpen: (s: IntakeSuggestion) => void; busy: boolean }) {
  const [top, ...rest] = intake.suggestions
  if (!top) return null
  return (
    <div className="card" data-testid="build-suggestions">
      <div className="card-head"><div className="left"><h3 className="card-title">3 · What you can build</h3><span className="helper" style={{ fontSize: 12 }}>best first · each opens pre-filled; you only confirm</span></div></div>
      <div className="card-body build-top-sugg" data-testid={`suggest-${top.id}`}>
        <div className="stack" style={{ gap: 4 }}>
          <b className="fr-name" style={{ fontSize: 15 }}>{top.title}</b>
          <span className="helper">{top.reason}</span>
          {prefillLine(top) && <span className="fr-meta mono">{prefillLine(top)}</span>}
        </div>
        <button className="btn primary" disabled={busy} onClick={() => onOpen(top)} data-testid="build-open">Open {top.title} →</button>
      </div>
      {rest.length > 0 && <div className="card-body fr-grid" style={{ borderTop: '1px solid var(--border-default)' }}>
        {rest.map((s) => (
          <button key={s.id} className="fr-card" disabled={busy} onClick={() => onOpen(s)} data-testid={`suggest-${s.id}`}>
            <span className="fr-name">{s.title}</span>
            <span className="fr-note">{s.reason}</span>
            {prefillLine(s) && <span className="fr-meta mono">{prefillLine(s)}</span>}
            <span className="fr-go">Open →</span>
          </button>
        ))}
      </div>}
    </div>
  )
}

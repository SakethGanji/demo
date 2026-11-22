import { useState } from 'react'
import type { MyTeam } from '@/shared/api/catalog'
import { joins as joinsApi, tables as tablesApi, JOIN_CODES, type FrameRows, type JoinAgg, type JoinCoverage, type JoinFrame, type JoinSpec, type SavedJoin, type VersionTables } from '@/shared/api/tables'
import { refusal } from '@/shared/api/governance'
import { useFetch } from '@/shared/hooks/useFetch'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { TrainOn } from './TrainOn'

const n = (x?: number | null) => (x == null ? '—' : x.toLocaleString())
const FNS: JoinAgg['fn'][] = ['count', 'count_distinct', 'sum', 'mean', 'min', 'max']
type Step = { table: string; type: 'n:1' | 'aggregate'; left: string; right: string; prefix: string; aggs: JoinAgg[] }
const blank = (t: string): Step => ({ table: t, type: 'n:1', left: '', right: '', prefix: '', aggs: [] })

const errLine = (e: unknown) => {
  const x = refusal(e)
  return `${x.code && JOIN_CODES[x.code] ? `${JOIN_CODES[x.code]} ` : ''}${x.message}${x.code ? ` (${x.code})` : ''}`
}

/**
 * U2 (P14, C §2.6): saved joins on this dataset and their frames. A join names a grain table (one frame row per grain
 * row) and, per other table, an n:1 lookup on a key or an aggregate (count, sum, mean… over the matching rows). Save
 * checks it against the version first: a 1:n lookup, an unknown column or a sum of text is refused with its code. A
 * saved join's frame is built on a version once (content-addressed; asking again is a cache hit), previewed masked,
 * and trained on (the label is a grain column, so the holdout is the grain table's split).
 */
export function JoinBuilder({ root, vid, v, team, ownerTeam, target, by, go }: {
  root: string; vid: string; v: number; team: MyTeam | null; ownerTeam?: string | null; target: string | null; by: string; go: (r: string) => void
}) {
  const det = useFetch<VersionTables>(() => tablesApi.version(root, vid), [root, vid])
  const [tick, setTick] = useState(0)
  const saved = useFetch<SavedJoin[]>(() => joinsApi.list(root), [root, tick])
  const [sel, setSel] = useState<string | null>(null)
  const [making, setMaking] = useState(false)
  if (det.err && !det.data) return <ErrorCard err={det.err} />
  if (!det.data) return <Loading>Reading v{v}'s tables…</Loading>
  const d = det.data
  if (d.tables.length < 2) return null
  const cur = (saved.data ?? []).find((j) => j.id === sel) ?? null
  return (
    <div className="card" data-testid="join-builder">
      <div className="card-head"><div className="left"><h3 className="card-title">Joins</h3>
        <span className="helper" style={{ fontSize: 12 }}>train on several tables at once: one frame row per grain row, the other tables looked up or aggregated onto it</span></div>
        <button className="btn sm" disabled={!by} onClick={() => { setMaking(!making); setSel(null) }} data-testid="join-new">{making ? 'Close' : 'New join…'}</button></div>
      {making && <NewJoin d={d} root={root} by={by} onSaved={(j) => { setMaking(false); setTick((x) => x + 1); setSel(j.id) }} />}
      <div className="card-body stack" style={{ gap: 6 }}>
        {saved.err && !saved.data ? <span className="helper">{saved.err}</span> : !saved.data ? <span className="helper">…</span>
          : !saved.data.length ? <span className="helper">No saved join yet. A join is saved on the dataset, so it applies to every version that has its tables.</span>
            : <div className="row" style={{ gap: 6, flexWrap: 'wrap' }} data-testid="join-list">{saved.data.map((j) =>
              <button key={j.id} className={`chip-btn ${sel === j.id ? 'on' : ''}`} onClick={() => { setSel(j.id); setMaking(false) }} data-testid={`join-${j.name}`}
                title={`${j.id} · spec ${j.spec_sha256.slice(0, 10)} · by ${j.created_by}`}>{j.name} <span className="muted">v{j.spec_version} · grain {j.spec.grain} + {j.spec.joins.length}</span></button>)}</div>}
      </div>
      {cur && <JoinDetail key={`${cur.id}@${vid}`} j={cur} root={root} vid={vid} v={v} d={d} team={team} ownerTeam={ownerTeam} target={target} by={by} go={go} />}
    </div>
  )
}

function NewJoin({ d, root, by, onSaved }: { d: VersionTables; root: string; by: string; onSaved: (j: SavedJoin) => void }) {
  const cols = (t: string) => (d.tables.find((x) => x.name === t)?.schema ?? []).map((c) => c.name)
  const [name, setName] = useState('')
  const [grain, setGrain] = useState(d.primary_table)
  const others = d.tables.map((t) => t.name).filter((t) => t !== grain)
  const [steps, setSteps] = useState<Step[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (i: number, s: Partial<Step>) => setSteps(steps.map((x, j) => (j === i ? { ...x, ...s } : x)))
  // a lookup suggested from the relations: from.table → to.table on the from columns
  const guessOn = (t: string): [string, string] => {
    const r = d.relations.find((x) => (x.from.table === grain && x.to.table === t) || (x.to.table === grain && x.from.table === t))
    if (r) return r.from.table === grain ? [r.from.columns[0], r.to.columns[0]] : [r.to.columns[0], r.from.columns[0]]
    const common = cols(grain).find((c) => cols(t).includes(c))
    return [common ?? '', common ?? '']
  }
  const add = (t: string) => {
    const [l, r] = guessOn(t)
    const many = d.relations.some((x) => x.from.table === t && x.to.table === grain)  // t has many rows per grain row
    setSteps([...steps, { ...blank(t), left: l, right: r, type: many ? 'aggregate' : 'n:1', prefix: `${t}__`, aggs: many ? [{ name: 'n', fn: 'count' }] : [] }])
  }
  const spec = (): JoinSpec => ({
    grain, joins: steps.map((s) => s.type === 'aggregate'
      ? { type: 'aggregate' as const, table: s.table, on: [[s.left, s.right] as [string, string]], prefix: s.prefix, aggs: s.aggs.map((a) => ({ name: a.name, fn: a.fn, ...(a.fn !== 'count' && a.column ? { column: a.column } : {}) })) }
      : { type: 'n:1' as const, table: s.table, on: [[s.left, s.right] as [string, string]], prefix: s.prefix }),
  })
  const save = async () => {
    setBusy(true); setErr('')
    try { onSaved(await joinsApi.save(root, name.trim(), spec(), by, d.id)) } catch (e) { setErr(errLine(e)) } finally { setBusy(false) }
  }
  return (
    <div className="card-body stack join-new" style={{ gap: 8 }} data-testid="join-form">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <label className="row helper" style={{ gap: 4 }}>name <input className="input" style={{ width: 160 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="apps_bureau_pay" data-testid="join-name" /></label>
        <label className="row helper" style={{ gap: 4 }}>grain (one row per)
          <select className="select" value={grain} onChange={(e) => { setGrain(e.target.value); setSteps([]) }} data-testid="join-grain">{d.tables.map((t) => <option key={t.name}>{t.name}</option>)}</select></label>
        <span className="helper">{n(d.tables.find((t) => t.name === grain)?.n_rows)} rows</span>
        <span className="grow" />
        <span className="helper">add</span>{others.map((t) => <button key={t} className="chip-btn" onClick={() => add(t)} data-testid={`join-add-${t}`}>+ {t}</button>)}
      </div>
      {steps.map((s, i) => (
        <div key={i} className="join-step" data-testid={`join-step-${i}`}>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <b className="mono">{s.table}</b>
            <select className="select" value={s.type} onChange={(e) => set(i, { type: e.target.value as Step['type'], aggs: e.target.value === 'aggregate' && !s.aggs.length ? [{ name: 'n', fn: 'count' }] : s.aggs })} data-testid={`join-type-${i}`}>
              <option value="n:1">look up (n:1)</option><option value="aggregate">aggregate (many rows per grain row)</option></select>
            <span className="helper">on</span>
            <select className="select mono" value={s.left} onChange={(e) => set(i, { left: e.target.value })} data-testid={`join-left-${i}`}><option value="">{grain}.…</option>{cols(grain).map((c) => <option key={c}>{c}</option>)}</select>
            <span>=</span>
            <select className="select mono" value={s.right} onChange={(e) => set(i, { right: e.target.value })} data-testid={`join-right-${i}`}><option value="">{s.table}.…</option>{cols(s.table).map((c) => <option key={c}>{c}</option>)}</select>
            <label className="row helper" style={{ gap: 4 }}>prefix <input className="input mono" style={{ width: 100 }} value={s.prefix} onChange={(e) => set(i, { prefix: e.target.value })} data-testid={`join-prefix-${i}`} /></label>
            <button className="chip-btn" onClick={() => setSteps(steps.filter((_, j) => j !== i))} aria-label={`remove ${s.table}`}>✕</button>
          </div>
          {s.type === 'aggregate' && <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
            {s.aggs.map((a, k) => <span key={k} className="join-agg" data-testid={`join-agg-${i}-${k}`}>
              <input className="input mono" style={{ width: 90 }} value={a.name} onChange={(e) => set(i, { aggs: s.aggs.map((x, m) => (m === k ? { ...x, name: e.target.value } : x)) })} aria-label="output name" />
              = <select className="select" value={a.fn} onChange={(e) => set(i, { aggs: s.aggs.map((x, m) => (m === k ? { ...x, fn: e.target.value as JoinAgg['fn'] } : x)) })}>{FNS.map((f) => <option key={f}>{f}</option>)}</select>
              {a.fn !== 'count' && <select className="select mono" value={a.column ?? ''} onChange={(e) => set(i, { aggs: s.aggs.map((x, m) => (m === k ? { ...x, column: e.target.value } : x)) })}><option value="">column…</option>{cols(s.table).map((c) => <option key={c}>{c}</option>)}</select>}
              <button className="chip-btn" onClick={() => set(i, { aggs: s.aggs.filter((_, m) => m !== k) })} aria-label="remove aggregate">✕</button></span>)}
            <button className="chip-btn" onClick={() => set(i, { aggs: [...s.aggs, { name: `agg${s.aggs.length + 1}`, fn: 'mean' }] })} data-testid={`join-agg-add-${i}`}>+ aggregate</button>
          </div>}
        </div>))}
      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <button className="btn primary" disabled={busy || !name.trim() || !steps.length || steps.some((s) => !s.left || !s.right)} onClick={save} data-testid="join-save">{busy ? 'Checking…' : 'Check and save'}</button>
        <span className="helper">checked against v{d.version_no} first; nothing is saved if a rule fails</span>
      </div>
      {err && <p className="helper up-err" role="alert" data-testid="join-err">✕ {err}</p>}
    </div>
  )
}

function Coverage({ cov, grainRows }: { cov: JoinCoverage[]; grainRows: number }) {
  return (
    <div className="stack" style={{ gap: 4 }}>
      <table className="table join-cov" data-testid="join-coverage">
        <thead><tr><th>joined table</th><th>type · on</th><th className="r">matched m</th><th className="r">coverage c = m / N</th><th className="r">orphans</th><th className="r">missing key</th><th>key</th></tr></thead>
        <tbody>{cov.map((c) => <tr key={c.join} data-testid={`join-cov-${c.table}`}>
          <td className="mono"><b>{c.table}</b> <span className="muted">{n(c.table_rows)} rows</span></td>
          <td className="mono" style={{ fontSize: 12 }}>{c.type} · {c.on.map(([l, r]) => `${l} = ${r}`).join(', ')}{c.rows_aggregated != null && <div className="helper">{n(c.rows_aggregated)} rows aggregated</div>}</td>
          <td className="r mono">{n(c.matched)}</td>
          <td className="r mono">{n(c.matched)} / {n(grainRows)} = <b>{c.coverage == null ? '—' : c.coverage.toFixed(3)}</b></td>
          <td className="r mono">{n(c.orphans)}</td><td className="r mono">{n(c.missing_key)}</td>
          <td className="helper" style={{ fontSize: 12 }}>{c.key?.how ? `${c.key.how}${c.key.unique ? ', unique' : ''}` : c.type === 'aggregate' ? 'grouped' : '—'}</td></tr>)}</tbody>
      </table>
      <span className="helper" style={{ fontSize: 12 }}>where N = {n(grainRows)} grain rows (the frame has exactly N rows), m = grain rows with at least one matching row; an orphan is a grain row whose key has no match (its joined columns are missing); a missing key is a grain row with no key value.</span>
    </div>
  )
}

function JoinDetail({ j, root, vid, v, d, team, ownerTeam, target, by, go }: { j: SavedJoin; root: string; vid: string; v: number; d: VersionTables; team: MyTeam | null; ownerTeam?: string | null; target: string | null; by: string; go: (r: string) => void }) {
  const val = useFetch(() => joinsApi.validate(root, j.id, vid), [root, j.id, vid])
  const [fr, setFr] = useState<JoinFrame | null>(null)
  const [rows, setRows] = useState<FrameRows | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const build = async () => {
    setBusy(true); setErr('')
    try {
      const f = await joinsApi.frame(root, vid, j.id, by)
      setFr(f)
      setRows(await joinsApi.rows(root, f.id, 15))
    } catch (e) { setErr(errLine(e)) } finally { setBusy(false) }
  }
  const grainCols = (d.tables.find((t) => t.name === j.spec.grain)?.schema ?? []).map((c) => c.name)
  return (
    <div className="card-body stack" style={{ gap: 8, borderTop: '1px solid var(--border-default)' }} data-testid="join-detail">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>{j.name}</b><span className="mono muted">{j.id} · spec v{j.spec_version} · sha {j.spec_sha256.slice(0, 12)}</span>
        <span className="helper">grain {j.spec.grain}{j.spec.joins.map((s) => ` + ${typeof s.table === 'string' ? s.table : `${s.table.table}@${s.table.version_id}`} (${s.type})`).join('')}</span>
      </div>
      {val.err && !val.data ? <p className="helper up-err">✕ {val.err} — this join does not apply to v{v}</p> : !val.data ? <span className="helper">checking the join on v{v}…</span>
        : <>
          <Coverage cov={val.data.coverage} grainRows={val.data.grain_rows} />
          <span className="helper" style={{ fontSize: 12 }}>{val.data.n_columns} frame columns: {val.data.columns.map((c) => c.name).join(', ')}</span>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <button className="btn primary" disabled={busy || !by} onClick={build} data-testid="join-frame">{busy ? 'Building…' : `Build the frame on v${v}`}</button>
            <span className="helper">one DuckDB pass; the frame is kept by content, so the same join on the same tables is built once</span></div>
        </>}
      {err && <p className="helper up-err" role="alert" data-testid="join-frame-err">✕ {err}</p>}
      {fr && <div className="data-preview" data-testid="frame-result">
        <div className="data-hero data-hero-sm">
          <div><span className="micro-label">frame</span><b className="up-small mono">{fr.id}</b></div>
          <div><span className="micro-label">rows = grain rows</span><b data-testid="frame-rows">{n(fr.n_rows)}</b></div>
          <div><span className="micro-label">columns</span><b>{fr.schema.length - 1}</b></div>
          <div><span className="micro-label">{fr.cached ? 'cached' : 'built in'}</span><b>{fr.cached ? 'yes' : `${fr.seconds?.toFixed(2)} s`}</b></div>
          <div><span className="micro-label">frame sha256</span><b className="mono up-small" title={fr.frame_sha256}>{fr.frame_sha256.slice(0, 12)}</b></div>
        </div>
        <span className="helper" style={{ fontSize: 12 }}>inputs pinned: {fr.inputs.map((x) => `${x.table} @ ${x.version_id} (${n(x.n_rows)} rows, sha ${x.table_sha256.slice(0, 8)})`).join(' · ')}{fr.same_frame_as_version ? ` · the same frame as on ${fr.same_frame_as_version} (same table bytes)` : ''}</span>
        {rows && <FramePreview r={rows} />}
        <TrainOn datasetId={vid} joinId={j.id} columns={grainCols} defaultLabel={target} ownerTeam={ownerTeam} team={team} by={by} go={go} what={`join ${j.name}`} />
      </div>}
    </div>
  )
}

function FramePreview({ r }: { r: FrameRows }) {
  return (
    <details open data-testid="frame-preview"><summary className="micro-label">first {r.rows.length} of {n(r.n_rows)} frame rows{r.masked_columns.length ? ` · 🔒 ${r.masked_columns.length} masked (${r.masked_columns.join(', ')})` : ''}</summary>
      <div className="table-wrap" style={{ maxHeight: 320, overflow: 'auto' }}><table className="table up-preview">
        <thead><tr><th className="r">__row_id</th>{r.columns.map((c) => <th key={c} title={r.masks[c] ? `PII: masked (${r.masks[c]})` : undefined}>{r.masks[c] ? '🔒 ' : ''}{c}</th>)}</tr></thead>
        <tbody>{r.rows.map((row, i) => <tr key={r.row_ids[i]}><td className="r mono muted">{r.row_ids[i]}</td>{row.map((x, k) => <td key={k} className={`mono ${r.masks[r.columns[k]] ? 'masked-cell' : ''}`}>{x == null ? <span className="muted">∅</span> : typeof x === 'number' && !Number.isInteger(x) ? x.toPrecision(4) : String(x)}</td>)}</tr>)}</tbody>
      </table></div></details>
  )
}

import { useState } from 'react'
import type { MyTeam } from '@/shared/api/catalog'
import { tables as tablesApi, type VersionTables } from '@/shared/api/tables'
import { refusal } from '@/shared/api/governance'
import { useFetch } from '@/shared/hooks/useFetch'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { TrainOn } from './TrainOn'

const n = (x?: number | null) => (x == null ? '—' : x.toLocaleString())

/**
 * U2 (P12, C §2.5–2.7): the tables of one version. Picking a table here scopes the diff (`?table=`), the editor
 * (`{table}`) and the run (`POST /runs {table}`) on this page. Each table shows its rows, sha256, how it got into this
 * version (new / carried / appended / rewritten, from which version), its keys and splits; the relations between the
 * tables with their orphans; a key or a relation is declared as a new version (every table carried, zero bytes).
 */
export function TablesCard({ root, vid, v, table, setTable, team, ownerTeam, target, by, go, onVersion }: {
  root: string; vid: string; v: number; table: string | null; setTable: (t: string) => void; team: MyTeam | null; ownerTeam?: string | null
  target: string | null; by: string; go: (r: string) => void; onVersion: (vid: string, text: string) => void
}) {
  const det = useFetch<VersionTables>(() => tablesApi.version(root, vid), [root, vid])
  const rels = useFetch(() => tablesApi.relations(vid), [vid])
  const [keyCols, setKeyCols] = useState('')
  const [msg, setMsg] = useState('')
  if (det.err && !det.data) return <ErrorCard err={det.err} />
  if (!det.data) return <Loading>Reading v{v}'s tables…</Loading>
  const d = det.data
  const cur = d.tables.find((t) => t.name === (table ?? d.primary_table)) ?? d.tables[0]
  const cols = (cur?.schema ?? []).map((c) => c.name)
  const declareKey = () => {
    setMsg('')
    tablesApi.setKey(vid, cur.name, keyCols.split(',').map((x) => x.trim()).filter(Boolean), by)
      .then((r) => { setKeyCols(''); const id = (r.version_id ?? r.id) as string; onVersion(id, r.deduped ? `That key is already declared on ${cur.name}.` : `Key ${keyCols} declared on ${cur.name}: a new version ${id} with every table carried (no bytes copied).`) })
      .catch((e) => { const x = refusal(e); setMsg(`${x.message}${x.code ? ` (${x.code})` : ''}`) })
  }
  const declareRel = (r: { name: string; from: { table: string; columns: string[] }; to: { table: string; columns: string[] } }) => {
    setMsg('')
    tablesApi.setRelation(vid, r, by).then((x) => { const id = (x.version_id ?? x.id) as string; onVersion(id, `Relation ${r.name} declared: a new version ${id}, orphans counted.`) })
      .catch((e) => { const x = refusal(e); setMsg(`${x.message}${x.code ? ` (${x.code})` : ''}`) })
  }
  return (
    <div className="card" data-testid="data-tables">
      <div className="card-head"><div className="left"><h3 className="card-title">Tables of v{v}</h3>
        <span className="helper" style={{ fontSize: 12 }}>{d.tables.length} table{d.tables.length === 1 ? '' : 's'} · data sha {d.data_sha256?.slice(0, 10)} · pick one: the diff, Edit and training below use it</span></div>
        <label className="row helper" style={{ gap: 6 }}>table
          <select className="select" value={cur?.name ?? ''} onChange={(e) => setTable(e.target.value)} data-testid="table-picker">
            {d.tables.map((t) => <option key={t.name} value={t.name}>{t.name}{t.primary ? ' (primary)' : ''}</option>)}</select></label>
      </div>
      <div className="table-wrap"><table className="table data-tables-t">
        <thead><tr><th>table</th><th className="r">rows × cols</th><th>in this version</th><th>sha256</th><th>keys</th><th>splits</th></tr></thead>
        <tbody>{d.tables.map((t) => (
          <tr key={t.name} className={t.name === cur?.name ? 'sel' : ''} onClick={() => setTable(t.name)} style={{ cursor: 'pointer' }} data-testid={`table-row-${t.name}`}>
            <td className="mono"><b>{t.name}</b>{t.primary && <span className="pt-chip" style={{ marginLeft: 4 }}>primary</span>}<div className="helper" style={{ fontSize: 11 }}>{t.source_file ?? ''}</div></td>
            <td className="r mono">{n(t.n_rows)} × {t.n_cols}</td>
            <td><span className={`pt-chip tbl-${t.change}`}>{t.change}</span>{t.from_version && <span className="helper mono" style={{ fontSize: 11 }}> from {t.from_version}</span>}
              {t.files.length > 1 && <div className="helper" style={{ fontSize: 11 }}>{t.files.map((f) => `${f.role} ${n(f.n_rows)}`).join(' + ')}</div>}</td>
            <td className="mono" title={t.table_sha256}>{t.table_sha256.slice(0, 10)}</td>
            <td className="mono" style={{ fontSize: 12 }}>{t.keys.length ? t.keys.map((k) => <div key={k.columns.join()}>{k.columns.join(' + ')} {k.valid ? <span className="data-ok">✓</span> : <span className="data-bad">✕ {n(k.n_duplicates)} dup</span>}</div>) : <span className="muted">—</span>}</td>
            <td className="mono" style={{ fontSize: 11 }}>{t.splits.map((s) => <div key={s.id} title={s.split_key}>{s.n_holdout} holdout{s.carried_from ? ' · carried' : ''}</div>)}</td>
          </tr>))}</tbody>
      </table></div>
      <div className="card-body stack" style={{ gap: 8 }}>
        {(rels.data?.relations.length || rels.data?.suggested.length) ? <div className="stack" style={{ gap: 4 }} data-testid="data-relations"><span className="micro-label">relations</span>
          {rels.data!.relations.map((r) => <div key={r.name} className="up-rel"><span className="mono">{r.from.table}.{r.from.columns.join('+')} → {r.to.table}.{r.to.columns.join('+')}</span>
            <span className="helper"> · coverage {(Number(r.coverage) * 100).toFixed(1)} % · {n(r.n_orphans)} orphans of {n(r.n_from_rows)} · declared by {r.declared_by}</span></div>)}
          {rels.data!.suggested.map((r) => <div key={r.name} className="up-rel"><span className="mono">{r.from.table}.{r.from.columns.join('+')} → {r.to.table}.{r.to.columns.join('+')}</span>
            <span className="helper"> · suggested · coverage {(Number(r.coverage) * 100).toFixed(1)} % · {n(r.n_orphans)} orphans </span>
            <button className="chip-btn" disabled={!by} onClick={() => declareRel(r)} data-testid={`rel-declare-${r.name}`}>declare (new version)</button></div>)}
        </div> : null}
        {cur && <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="micro-label">key of {cur.name}</span>
          <input className="input mono" style={{ width: 180 }} placeholder={cur.keys.find((k) => k.valid)?.columns.join(', ') ?? 'column(s), comma-separated'} value={keyCols} onChange={(e) => setKeyCols(e.target.value)} data-testid="table-key-input" />
          <button className="btn sm" disabled={!by || !keyCols.trim()} onClick={declareKey} data-testid="table-key-declare" title="Checked for uniqueness on the stored table; a new version, every table carried">Declare key</button>
          {msg && <span className="helper up-err" data-testid="table-msg">✕ {msg}</span>}
        </div>}
        {cur && <TrainOn datasetId={vid} table={cur.name} columns={cols} defaultLabel={cur.primary ? target : null} ownerTeam={ownerTeam} team={team} by={by} go={go} what={`table ${cur.name}`} />}
      </div>
    </div>
  )
}

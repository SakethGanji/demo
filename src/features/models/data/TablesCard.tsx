import { useState } from 'react'
import type { MyTeam } from '@/shared/api/catalog'
import { tables as tablesApi, type VersionTables } from '@/shared/api/tables'
import { refusal } from '@/shared/api/governance'
import { useFetch } from '@/shared/hooks/useFetch'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { TrainOn } from './TrainOn'
import { Badge, CardHead, CopyId, CoverageBar } from '@/shared/components/DataKit'

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
      <CardHead title={`Tables of v${v}`} sub={`${d.tables.length} table${d.tables.length === 1 ? '' : 's'} · the one you pick scopes the diff, Edit and training on this page`}
        actions={<><CopyId value={d.data_sha256} n={8} /><label className="row helper" style={{ gap: 6 }}><span className="micro-label">working on</span>
          <select className="select" value={cur?.name ?? ''} onChange={(e) => setTable(e.target.value)} data-testid="table-picker" aria-label="Table">
            {d.tables.map((t) => <option key={t.name} value={t.name}>{t.name}{t.primary ? ' (primary)' : ''}</option>)}</select></label></>} />
      <div className="table-wrap"><table className="table data-tables-t">
        <thead><tr><th>table</th><th className="r">rows × cols</th><th>in this version</th><th>key</th><th>holdout</th><th>sha256</th></tr></thead>
        <tbody>{d.tables.map((t) => (
          <tr key={t.name} className={t.name === cur?.name ? 'sel' : ''} onClick={() => setTable(t.name)} style={{ cursor: 'pointer' }} data-testid={`table-row-${t.name}`}>
            <td><span className="tbl-name">{t.name}</span>{t.primary && <span className="pt-chip" style={{ marginLeft: 6 }}>primary</span>}{t.source_file && <span className="sub-line trunc" title={t.source_file}>{t.source_file}</span>}</td>
            <td className="r mono">{n(t.n_rows)} <span className="muted">×</span> {t.n_cols}</td>
            <td><Badge status={t.change} title={t.from_version ? `${t.change} from ${t.from_version}` : undefined} />
              {t.files.length > 1 && <span className="sub-line">{t.files.map((f) => `${f.role} ${n(f.n_rows)}`).join(' + ')}</span>}</td>
            <td className="mono" style={{ fontSize: 12 }}>{t.keys.length ? t.keys.map((k) => <div key={k.columns.join()}>{k.columns.join(' + ')} {k.valid ? <span className="data-ok" title="unique, no missing value">✓</span> : <span className="data-bad" title="not a key any more">✕ {n(k.n_duplicates)} repeated</span>}</div>) : <span className="muted">none declared</span>}</td>
            <td className="mono" style={{ fontSize: 12 }}>{t.splits.length ? t.splits.map((s) => <div key={s.id} title={s.split_key}>{n(s.n_holdout)} rows{s.carried_from ? <span className="muted"> · carried</span> : ''}</div>) : <span className="muted">—</span>}</td>
            <td><CopyId value={t.table_sha256} n={8} /></td>
          </tr>))}</tbody>
      </table></div>
      <div className="card-body stack" style={{ gap: 8 }}>
        {(rels.data?.relations.length || rels.data?.suggested.length) ? <div className="stack" style={{ gap: 2 }} data-testid="data-relations"><span className="micro-label sec-label">relations · many → one</span>
          {rels.data!.relations.map((r) => <div key={r.name} className="rel-row"><span className="mono rel-path">{r.from.table}.{r.from.columns.join('+')} <span className="muted">→</span> {r.to.table}.{r.to.columns.join('+')}</span>
            <CoverageBar value={Number(r.coverage)} m={r.n_from_rows != null && r.n_orphans != null ? r.n_from_rows - r.n_orphans : null} N={r.n_from_rows} />
            <span className="helper">{n(r.n_orphans)} orphan{r.n_orphans === 1 ? '' : 's'} of {n(r.n_from_rows)}</span><span className="spacer" /><Badge status="confirmed" title={`declared by ${r.declared_by}`}>declared</Badge></div>)}
          {rels.data!.suggested.map((r) => <div key={r.name} className="rel-row"><span className="mono rel-path">{r.from.table}.{r.from.columns.join('+')} <span className="muted">→</span> {r.to.table}.{r.to.columns.join('+')}</span>
            <CoverageBar value={Number(r.coverage)} />
            <span className="helper">{n(r.n_orphans)} orphans</span><span className="spacer" /><Badge status="suggested" />
            <button className="btn sm" disabled={!by} title={by ? 'Declare it: a new version, every table carried, orphans counted' : 'Type your name in “acting as” first'} onClick={() => declareRel(r)} data-testid={`rel-declare-${r.name}`}>Declare</button></div>)}
          <span className="helper" style={{ fontSize: 11.5 }}>coverage = “many” rows whose key finds its row ÷ “many” rows with a key; an orphan finds none.</span>
        </div> : null}
        {cur && <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="micro-label sec-label">declare a key on {cur.name}</span>
          <input className="input mono" style={{ width: 180 }} placeholder={cur.keys.find((k) => k.valid)?.columns.join(', ') ?? 'column(s), comma-separated'} value={keyCols} onChange={(e) => setKeyCols(e.target.value)} data-testid="table-key-input" />
          <button className="btn sm" disabled={!by || !keyCols.trim()} onClick={declareKey} data-testid="table-key-declare" title="Checked for uniqueness on the stored table; a new version, every table carried">Declare key</button>
          <span className="helper" style={{ fontSize: 12 }}>checked for uniqueness on the stored table; makes a new version, no bytes copied</span>
          {msg && <span className="helper up-err" role="alert" data-testid="table-msg">✕ {msg}</span>}
        </div>}
        {cur && <TrainOn datasetId={vid} table={cur.name} columns={cols} defaultLabel={cur.primary ? target : null} ownerTeam={ownerTeam} team={team} by={by} go={go} what={`table ${cur.name}`} />}
      </div>
    </div>
  )
}

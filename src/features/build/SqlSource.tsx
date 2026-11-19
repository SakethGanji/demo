import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { DatasetDetail, SqlLineage, SqlPreview, SqlTable } from '@/shared/api/types'
import { useFetch } from '@/shared/hooks/useFetch'

const fail = { color: 'var(--status-danger)' } as const

/**
 * G9 · Build's second way in: a read-only SQL query against a database on the server (the demo SQLite file is
 * pre-filled). Preview, then load it as a dataset; the query goes into the dataset's lineage. The password lives in
 * this component's state for the session only and is sent with each request, never stored.
 */
export function SqlSource({ onLoaded }: { onLoaded: (d: DatasetDetail) => void }) {
  const demo = useFetch(() => api.datasets.sql.demo(), [])
  const [url, setUrl] = useState('')
  const [password, setPassword] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(100_000)
  const [name, setName] = useState('')
  const [tables, setTables] = useState<SqlTable[] | null>(null)
  const [preview, setPreview] = useState<SqlPreview | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {  // zero-config: the demo database and its example join are filled in
    if (demo.data && !url) { setUrl(demo.data.url); setQuery(demo.data.query); setTables(demo.data.tables); setLimit(demo.data.limit) }
  }, [demo.data])  // eslint-disable-line react-hooks/exhaustive-deps

  const q = { url, query, limit, password: password || undefined }
  const act = async (label: string, f: () => Promise<void>) => {
    setBusy(label); setErr('')
    try { await f() } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const connect = () => act('Reading the tables…', async () => setTables((await api.datasets.sql.tables(url, password || undefined)).tables))
  const run = () => act('Running the query…', async () => setPreview(await api.datasets.sql.preview(q)))
  const load = () => act('Loading and profiling…', async () => { onLoaded(await api.datasets.sql.create({ ...q, name: name || undefined })); setPreview(null) })

  return (
    <div className="stack sql-src" style={{ gap: 8 }} data-testid="sql-source">
      <input className="input mono" value={url} onChange={(e) => { setUrl(e.target.value); setTables(null) }} placeholder="sqlite:///file.sqlite · postgresql://user@host/db" aria-label="Connection URL" data-testid="sql-url" spellCheck={false} />
      <div className="row" style={{ gap: 6 }}>
        <input className="input" style={{ flex: 1 }} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password, if any · never stored" aria-label="Password (this session only)" title="Sent with each request for this session only; never stored" autoComplete="off" data-testid="sql-password" />
        <button className="btn sm" onClick={connect} disabled={!!busy || !url}>List tables</button>
      </div>
      {tables && <div className="row sql-tables" style={{ gap: 4, flexWrap: 'wrap' }} data-testid="sql-tables">
        {tables.map((t) => <button key={t.name} className="chip" title={t.columns.map((c) => `${c.name} ${c.type}`).join('\n')} onClick={() => { setQuery(`SELECT *\nFROM ${t.name}`); setPreview(null) }}>
          {t.name}{t.rows != null && <span className="muted">{t.rows.toLocaleString()}</span>}</button>)}
      </div>}
      <textarea className="kw-input" rows={5} spellCheck={false} value={query} onChange={(e) => { setQuery(e.target.value); setPreview(null) }} aria-label="SQL query" data-testid="sql-query" />
      <details className="an-adv"><summary className="micro-label">Advanced · row limit, name</summary>
        <div className="row" style={{ gap: 6, padding: '8px 0' }}>
          <label className="helper row" style={{ gap: 6, fontSize: 12 }}>limit <input className="input mono" type="number" min={1} max={demo.data?.max_limit ?? 1_000_000} value={limit} onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))} style={{ width: 110 }} data-testid="sql-limit" /></label>
          <input className="input" style={{ flex: 1 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="dataset name (default: the table)" aria-label="Dataset name" />
        </div>
      </details>
      <div className="row" style={{ gap: 8, justifyContent: 'flex-end' }}>
        {busy && <span className="helper" style={{ fontSize: 12, marginRight: 'auto' }}>{busy}</span>}
        <button className="btn sm" onClick={run} disabled={!!busy || !url || !query.trim()} data-testid="sql-preview">Preview</button>
        <button className="btn sm primary" onClick={load} disabled={!!busy || !url || !query.trim()} data-testid="sql-load">Load as dataset →</button>
      </div>
      {preview && <PreviewTable p={preview} />}
      {(err || demo.err) && <p className="helper" role="alert" style={fail}>✕ {err || demo.err}</p>}
      <p className="helper" style={{ fontSize: 11.5, margin: 0 }}>Read-only: one SELECT, on a read-only connection, at most {limit.toLocaleString()} rows. The password is used for this session only and never stored; lineage keeps the query.</p>
    </div>
  )
}

function PreviewTable({ p }: { p: SqlPreview }) {
  return (
    <div data-testid="sql-preview-table">
      <p className="helper mono" style={{ fontSize: 12, margin: '0 0 4px' }}>{p.n_rows.toLocaleString()}{p.truncated ? '+' : ''} rows × {p.columns.length} columns · {(p.seconds * 1000).toFixed(0)} ms{p.truncated ? ` · stopped at the limit ${p.limit.toLocaleString()}` : ''}</p>
      <div className="table-wrap sql-preview"><table className="table compact">
        <thead><tr>{p.columns.map((c) => <th key={c.name} title={`${c.dtype}${c.missing ? ` · ${c.missing} missing` : ''}`}>{c.name}</th>)}</tr></thead>
        <tbody>{p.rows.slice(0, 5).map((r, i) => <tr key={i}>{p.columns.map((c) => <td key={c.name} className="mono">{r[c.name] == null ? '—' : String(r[c.name])}</td>)}</tr>)}</tbody>
      </table></div>
    </div>
  )
}

/** Where a SQL-loaded dataset came from, its versions, and Refresh: the same query again, as the next version. */
export function SqlLineageLine({ ds, note, onRefreshed }: { ds: DatasetDetail; note?: string; onRefreshed: (id: string, msg: string) => void }) {
  const lin = ds.profile.lineage as SqlLineage
  const versions = useFetch(() => api.datasets.sql.versions(ds.id), [ds.id])
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const refresh = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.datasets.sql.refresh(ds.id, password || undefined)
      const d = r.refresh.diff
      onRefreshed(r.id, `v${r.refresh.version}: ${r.rows.toLocaleString()} rows${d ? ` · +${d.added.toLocaleString()} new, −${d.removed.toLocaleString()} gone, ${d.kept.toLocaleString()} unchanged` : ''}${r.refresh.unchanged ? ' · same data as before' : ''}`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const vs = versions.data?.versions ?? []
  return (
    <div className="sql-lineage stack" style={{ gap: 6 }} data-testid="sql-lineage">
      <div className="row" style={{ gap: 8, justifyContent: 'space-between' }}>
        <span className="helper" style={{ fontSize: 12 }}><b>SQL · v{lin.version}</b> <span className="mono">{lin.url}</span> · fetched {new Date(lin.fetched_at).toLocaleString()}{lin.truncated ? ` · limit ${lin.limit.toLocaleString()} reached` : ''}</span>
        <span className="row" style={{ gap: 6 }}>
          {lin.needs_password && <input className="input" type="password" style={{ width: 110 }} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password" aria-label="Password for refresh" autoComplete="off" />}
          <button className="btn sm" disabled={busy || (lin.needs_password && !password)} onClick={refresh} title="Run the same query again into a new dataset version" data-testid="sql-refresh">{busy ? 'Refreshing…' : 'Refresh ↻'}</button>
        </span>
      </div>
      {note && <p className="helper" style={{ fontSize: 12, margin: 0, color: 'var(--status-success)' }} data-testid="sql-refreshed">✓ {note}</p>}
      {err && <p className="helper" role="alert" style={{ ...fail, fontSize: 12, margin: 0 }}>✕ {err}</p>}
      <details className="an-adv"><summary className="micro-label">Lineage · the query and {vs.length} version{vs.length === 1 ? '' : 's'}</summary>
        <pre className="sql-query mono">{lin.query}</pre>
        {vs.length > 0 && <table className="table compact" data-testid="sql-versions"><thead><tr><th>v</th><th className="r">rows</th><th>change</th><th>data SHA-256</th></tr></thead>
          <tbody>{vs.map((v) => <tr key={v.id} className={v.id === ds.id ? 'on' : ''}><td className="mono"><a href={`#/build?dataset=${encodeURIComponent(v.id)}`}>v{v.version}</a></td><td className="r mono">{v.rows.toLocaleString()}</td>
            <td className="helper mono" style={{ fontSize: 11.5 }}>{v.diff ? `+${v.diff.added} −${v.diff.removed}` : 'first load'}</td><td className="mono" style={{ fontSize: 11.5 }}>{v.data_sha}</td></tr>)}</tbody></table>}
      </details>
    </div>
  )
}

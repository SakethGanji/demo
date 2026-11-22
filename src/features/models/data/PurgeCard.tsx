import { useEffect, useState } from 'react'
import type { DsHistory } from '@/shared/api/catalog'
import { purges, type PurgeRow } from '@/shared/api/lifecycle'
import { tables as tablesApi, type VTable } from '@/shared/api/tables'
import { PurgeDetail, lifecycleLine } from '@/shared/components/PurgeDetail'
import { useCan, why } from '@/shared/hooks/useCan'
import { useTeam } from '@/shared/lib/team'

/** U3 (P24, plan §5.10, §11 item 5): purge values out of a dataset for good, e.g. a column that should never have been
 *  kept, or the rows of a person who asked to be forgotten. The request writes a new version without them and an
 *  impact report across teams; nothing is deleted until an owner of every involved team approves. A legal hold refuses
 *  it, and the refusal names the case. */
export function PurgeCard({ d, vid, v, table, by, onChanged }: { d: DsHistory; vid: string; v: number; table: string | null; by: string; onChanged: (text: string) => void }) {
  const can = useCan('data.purge', 'dataset', vid, by)
  const [tabs, setTabs] = useState<VTable[]>([])
  const [t, setT] = useState<string>(table ?? '')
  const [cols, setCols] = useState<string[]>([])
  const [rows, setRows] = useState('')
  const [reason, setReason] = useState('')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [list, setList] = useState<PurgeRow[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => { tablesApi.version(d.root_id, vid).then((r) => { setTabs(r.tables); setT((x) => x || table || r.tables.find((y) => y.primary)?.name || r.tables[0]?.name || '') }).catch(() => setTabs([])) }, [d.root_id, vid, table])
  // F-X4a: GET /purges without ?team= is admin-only: list the purges that involve the current team
  const { team: cur } = useTeam()
  useEffect(() => { if (!cur) { setList([]); return } purges.list(cur.id).then((l) => { const mine = l.filter((p) => p.root_id === d.root_id); setList(mine); setSel((s) => s ?? mine.find((p) => p.status === 'pending')?.id ?? null) }).catch(() => setList([])) }, [d.root_id, tick, cur?.id])
  useEffect(() => setCols([]), [t])
  const schema = tabs.find((x) => x.name === t)?.schema ?? []
  const ids = rows.split(/[\s,]+/).filter(Boolean)
  const badIds = ids.filter((x) => !/^\d+$/.test(x))
  const ok = !!by && !!can?.allow && (cols.length > 0 || (ids.length > 0 && !badIds.length)) && reason.trim().length >= 10 && cols.length < schema.length
  const send = () => {
    setBusy(true); setErr('')
    purges.request(vid, { table: t, ...(cols.length ? { columns: cols } : {}), ...(ids.length ? { row_ids: ids.map(Number) } : {}), reason: reason.trim() }, by)
      .then((p) => { setOpen(false); setCols([]); setRows(''); setReason(''); setSel(p.id); setTick((x) => x + 1); onChanged(`Purge ${p.id} requested: ${p.new_version_id} was written without the values; ${p.teams_needed.length} team(s) must approve before anything is deleted.`) })
      .catch((e) => setErr(lifecycleLine(e))).finally(() => setBusy(false))
  }
  return (
    <div className="card" data-testid="purge-card">
      <div className="card-head"><div className="left"><h3 className="card-title">Purge values</h3>
        <span className="helper" style={{ fontSize: 12 }}>removes columns or rows from every version for good · an owner of every involved team approves</span></div>
        {!open && <button className="btn sm" disabled={!by || !can?.allow} title={why(can, `Ask to purge columns or rows of v${v}`)} onClick={() => setOpen(true)} data-testid="purge-open">Request a purge…</button>}</div>
      <div className="card-body stack" style={{ gap: 8 }}>
        {can && !can.allow && <span className="helper" style={{ fontSize: 12 }} data-testid="purge-why">{why(can)}</span>}
        {open && <div className="stack" style={{ gap: 6 }} data-testid="purge-form">
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <span className="micro-label">table</span>
            <select className="select" value={t} onChange={(e) => setT(e.target.value)} data-testid="purge-table">{tabs.map((x) => <option key={x.name}>{x.name}</option>)}</select>
            <span className="helper">of v{v} · {schema.length} columns</span></div>
          <div className="row" style={{ gap: 4, flexWrap: 'wrap' }} data-testid="purge-columns"><span className="micro-label">columns</span>
            {schema.map((c) => <label key={c.name} className={`pt-chip ${cols.includes(c.name) ? 'purge-st-used' : ''}`} style={{ cursor: 'pointer' }}>
              <input type="checkbox" checked={cols.includes(c.name)} onChange={(e) => setCols(e.target.checked ? [...cols, c.name] : cols.filter((x) => x !== c.name))} data-testid={`purge-col-${c.name}`} /> {c.name}</label>)}</div>
          <div className="row" style={{ gap: 6 }}><span className="micro-label">and/or rows</span>
            <input className="input mono" style={{ flex: 1 }} placeholder="__row_id values, comma separated (e.g. a person who asked to be forgotten)" value={rows} onChange={(e) => setRows(e.target.value)} data-testid="purge-rows" /></div>
          {badIds.length > 0 && <span className="helper up-err">✕ Row ids are whole numbers (__row_id): {badIds.slice(0, 5).join(', ')}</span>}
          {cols.length > 0 && cols.length >= schema.length && <span className="helper up-err">✕ A purge keeps at least one column: delete the dataset instead.</span>}
          <input className="input" placeholder="why these values must go (at least 10 characters; every team sees it)" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="purge-reason" />
          <p className="helper" style={{ margin: 0, fontSize: 12 }}>The request writes a new version without these values and lists everything that still holds them: every version of this dataset, other teams' versions sharing the bytes, runs, shares whose grantee trained on it, join frames, models. Nothing is deleted until each team's owner approves.</p>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn sm primary" disabled={!ok || busy} onClick={send} data-testid="purge-send">{busy ? 'Writing the new version…' : 'Request the purge'}</button>
            <button className="btn sm ghost" onClick={() => { setOpen(false); setErr('') }}>Cancel</button></div>
        </div>}
        {err && <p className="helper data-warn" role="alert" data-testid="purge-refused">✕ {err}</p>}
        {list.length > 0 && <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}><span className="micro-label">requests</span>
          {list.map((p) => <button key={p.id} className={`chip-btn ${sel === p.id ? 'on' : ''}`} onClick={() => setSel(sel === p.id ? null : p.id)} data-testid={`purge-row-${p.id}`}>{p.id} · {p.table}.{(p.columns ?? []).join(',') || 'rows'} · {p.status}</button>)}</div>}
        {sel && <PurgeDetail key={`${sel}:${tick}`} id={sel} by={by} onChanged={(p) => { setTick((x) => x + 1); if (p.executed) onChanged(`Purge ${p.id} executed: ${p.executed.versions_deleted.length} version(s) deleted, ${p.executed.bytes_deleted ?? 0} blob(s) removed.`) }} />}
      </div>
    </div>
  )
}

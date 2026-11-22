import { useMemo, useState } from 'react'
import { CardHead } from '@/shared/components/DataKit'
import { data, type DsEditOp, type DsEditPreview, type DsGrid, type DsHistory, type DsVersion } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import { pctText, settle, type JobProgress } from '@/shared/api/jobs'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { useCan, why } from './Governance'
import { INTEGRITY_ACKS, IntegrityAcks, type Issue } from './UploadIssues'

/** DL3: edit a version as typed operations. Cell edits, row deletes and column actions pile up as pending operations;
 *  Preview (writes nothing) shows what the new version would be; "Create vN" (reason required) commits it as a NEW
 *  version through the version path (lineage, root rows, holdout, audit). The version being edited never changes. */

type ColAction = 'rename_column' | 'drop_column' | 'cast_column' | 'fill_missing' | 'replace_values'
const PAGE = 25
const show = (v: unknown) => (v == null ? '' : typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toPrecision(6).replace(/\.?0+$/, '')) : String(v))

export function EditVersion({ d, v, by, table, onClose, onCreated, onErr }: {
  d: DsHistory; v: DsVersion; by: string; table?: string | null; onClose: () => void; onCreated: (vid: string, text: string) => void; onErr: (e: unknown) => void
}) {
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  // P20/U2: an unmasked read needs a reason (audited); refused → still masked, with unmasked_refused
  const [unmask, setUnmask] = useState<{ open: boolean; reason: string; on: boolean }>({ open: false, reason: '', on: false })
  const canUnmask = useCan('data.read_unmasked', 'dataset', v.id, by)
  const grid = useFetch<DsGrid>(() => data.grid(v.id, { offset: page * PAGE, limit: PAGE, q, table, unmasked: unmask.on, reason: unmask.reason }), [v.id, page, q, table, unmask.on])
  const [cells, setCells] = useState<Record<string, { row: number; column: string; value: string; old: unknown }>>({})
  const [deleted, setDeleted] = useState<Set<number>>(new Set())
  const [ops, setOps] = useState<{ op: DsEditOp; label: string }[]>([])
  const [editing, setEditing] = useState<string | null>(null)
  const [pv, setPv] = useState<DsEditPreview | null>(null)
  const [iack, setIack] = useState<Set<string>>(new Set())  // F-X5b (J1): integrity codes acknowledged, as on an upload
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [prog, setProg] = useState<JobProgress | null>(null)  // F-JOBS: a large edit runs as a job (202 + its progress)
  // column action form
  const [col, setCol] = useState('')
  const [act, setAct] = useState<ColAction>('rename_column')
  const [arg1, setArg1] = useState('')
  const [arg2, setArg2] = useState('')
  // new column / new row forms
  const [newCol, setNewCol] = useState({ name: '', expr: '' })
  const [newRow, setNewRow] = useState<Record<string, string> | null>(null)

  const cols = grid.data?.columns ?? []
  const allOps = useMemo<DsEditOp[]>(() => {
    const out: DsEditOp[] = []
    const ce = Object.values(cells)
    // U2 (P07 note): rows are named by __row_id (the grid's _row_id), not by position
    if (ce.length) out.push({ op: 'set_cells', cells: ce.map((c) => ({ row_id: c.row, column: c.column, value: c.value })) })
    if (deleted.size) out.push({ op: 'delete_rows', row_ids: [...deleted].sort((a, b) => a - b) })
    return [...out, ...ops.map((o) => o.op)]
  }, [cells, deleted, ops])
  const pending = [
    ...(Object.keys(cells).length ? [{ k: 'cells', label: `set ${Object.keys(cells).length} cell${Object.keys(cells).length === 1 ? '' : 's'}`, drop: () => setCells({}) }] : []),
    ...(deleted.size ? [{ k: 'del', label: `delete ${deleted.size} row${deleted.size === 1 ? '' : 's'} (${[...deleted].slice(0, 6).join(', ')}${deleted.size > 6 ? '…' : ''})`, drop: () => setDeleted(new Set()) }] : []),
    ...ops.map((o, i) => ({ k: `op${i}`, label: o.label, drop: () => setOps(ops.filter((_, j) => j !== i)) })),
  ]
  const changed = () => { setPv(null); setIack(new Set()) }  // any new pending operation invalidates the preview
  const addOp = (op: DsEditOp, label: string) => { setOps([...ops, { op, label }]); changed() }

  const setCell = (row: number, column: string, value: string, old: unknown) => {
    const k = `${row}|${column}`
    const next = { ...cells }
    if (value === show(old)) delete next[k]; else next[k] = { row, column, value, old }
    setCells(next); setEditing(null); changed()
  }
  const toggleDelete = (row: number) => { const s = new Set(deleted); if (s.has(row)) s.delete(row); else s.add(row); setDeleted(s); changed() }

  const addColAction = () => {
    if (!col) return
    if (act === 'rename_column' && arg1.trim()) addOp({ op: act, column: col, to: arg1.trim() }, `rename '${col}' → '${arg1.trim()}'`)
    else if (act === 'drop_column') addOp({ op: act, column: col }, `drop '${col}'`)
    else if (act === 'cast_column') addOp({ op: act, column: col, to: arg1 || 'number' }, `cast '${col}' to ${arg1 || 'number'}`)
    else if (act === 'fill_missing') addOp({ op: act, column: col, strategy: arg1 || 'median', ...(arg1 === 'constant' ? { value: arg2 } : {}) }, `fill missing '${col}' with ${arg1 === 'constant' ? `'${arg2}'` : arg1 || 'median'}`)
    else if (act === 'replace_values' && arg1 !== '') addOp({ op: act, column: col, find: arg1, replace: arg2, match: 'exact' }, `replace '${arg1}' → '${arg2}' in '${col}'`)
    else return
    setArg1(''); setArg2('')
  }
  const addColumn = () => {
    const n = newCol.name.trim(), x = newCol.expr.trim()
    if (!n || !x) return
    const isNum = /^-?\d+(\.\d+)?$/.test(x), isQuoted = /^["'].*["']$/.test(x)
    addOp(isNum || isQuoted ? { op: 'add_column', name: n, value: isQuoted ? x.slice(1, -1) : Number(x) } : { op: 'add_column', name: n, formula: x },
      `add '${n}' = ${x}`)
    setNewCol({ name: '', expr: '' })
  }
  const addRow = () => {
    if (!newRow) return
    addOp({ op: 'add_rows', rows: [Object.fromEntries(Object.entries(newRow).filter(([, x]) => x !== ''))] }, `add a row (${Object.values(newRow).filter(Boolean).slice(0, 3).join(', ')}…)`)
    setNewRow(null)
  }

  const preview = async () => {
    setBusy(true)
    try { setPv(await data.editPreview(v.id, allOps, by, table)); setIack(new Set()) } catch (e) { onErr(e) } finally { setBusy(false) }
  }
  const create = async () => {
    setBusy(true); setProg(null)
    try {
      const r = await settle(data.edit(v.id, allOps, by, reason.trim(), 'auto', table, integrityCodes.filter((c) => iack.has(c))), (p) => setProg(p))
      if (!r.created) onCreated(r.duplicate_of ?? r.id, `Nothing new: the result has the same bytes as ${r.duplicate_of}, so no version was made.`)
      else onCreated(r.id, `v${pv?.v} (${r.id}) created from v${v.v}: ${(r.edit?.ops_summary ?? []).join('; ')}. `
        + (r.default?.is_default ? 'It is now the default.' : `Not the default (${r.default?.why}): review it, then make it the default.`))
    } catch (e) { onErr(e) } finally { setBusy(false); setProg(null) }
  }

  const g = grid.data
  const ck = pv?.checks
  // F-X5b (J1): a key that stops being one / a relation the version loses: listed with counts, acknowledged by code
  const integrity = (ck?.warnings ?? []).filter((w) => (w as Issue).needs_ack && (INTEGRITY_ACKS as readonly string[]).includes(w.code)) as Issue[]
  const integrityCodes = [...new Set([...integrity.map((w) => w.code), ...(pv?.needs_acknowledge ?? [])])]
  const contractWarnings = (ck?.warnings ?? []).filter((w) => !integrity.includes(w as Issue))
  const unacked = integrityCodes.filter((c) => !iack.has(c))
  return (
    <div className="card" data-testid="data-editor">
      <CardHead title={<>Edit v{v.v}{table ? <span className="mono"> · {table}</span> : null}</>} sub={`changes are saved as a new version (v${d.versions.length + 1}); v${v.v} is never modified`}
        actions={<button className="btn sm ghost" onClick={onClose} data-testid="edit-close">Close</button>} />
      <div className="card-body row edit-bar">
        <input className="input" style={{ width: 200 }} placeholder="search rows…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0) }} data-testid="edit-q" />
        {g && <span className="helper mono">{g.matching ? `${(g.offset + 1).toLocaleString()}–${Math.min(g.offset + PAGE, g.matching).toLocaleString()} of ${g.matching.toLocaleString()}` : '0 rows'}{g.matching !== g.total ? ` (of ${g.total.toLocaleString()})` : ''}</span>}
        <button className="btn sm ghost" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="previous page">‹</button>
        <button className="btn sm ghost" disabled={!g || (page + 1) * PAGE >= g.matching} onClick={() => setPage(page + 1)} aria-label="next page" data-testid="edit-next">›</button>
        <span className="edit-sep" />
        <button className="btn sm" onClick={() => setNewRow(Object.fromEntries(cols.map((c) => [c.name, ''])))} data-testid="edit-add-row">+ Row</button>
      </div>
      {(g?.masked_columns?.length || unmask.on) ? <div className="mask-bar edit-mask" data-testid="edit-mask">
          {unmask.on ? <span className="unmask-bar" data-testid="unmasked-on">{g?.unmasked ? <>🔓 <b>unmasked</b> <span className="helper">“{unmask.reason}” · this read is audited</span></> : null}
            <button className="btn sm ghost" onClick={() => setUnmask({ open: false, reason: '', on: false })} data-testid="unmask-off">Mask again</button></span>
            : unmask.open ? <span className="unmask-bar"><input className="input" style={{ width: 200 }} placeholder="why you need the clear values (audited)" value={unmask.reason} onChange={(e) => setUnmask({ ...unmask, reason: e.target.value })} data-testid="unmask-reason" />
              <button className="btn sm" disabled={unmask.reason.trim().length < 2} onClick={() => { setUnmask({ ...unmask, on: true }); setPage(0) }} data-testid="unmask-go">Show</button>
              <button className="btn sm ghost" onClick={() => setUnmask({ open: false, reason: '', on: false })}>✕</button></span>
              : <button className="btn sm" disabled={!by || canUnmask?.allow === false} title={why(canUnmask, 'Show the clear values of the masked columns: give a reason; the read is audited')} onClick={() => setUnmask({ ...unmask, open: true })} data-testid="unmask">Unmask (reason)…</button>}
          {!unmask.on && <span className="helper" data-testid="masked-note">🔒 {g!.masked_columns!.length} masked column{g!.masked_columns!.length === 1 ? '' : 's'}: {g!.masked_columns!.map((c) => <span key={c} className="mono">{c}</span>)} · their values show as •••</span>}
          {!unmask.on && canUnmask?.allow === false && <span className="why" data-testid="unmask-why"><span aria-hidden>⊘</span> {why(canUnmask)}</span>}
        </div> : null}
      {g?.unmasked_refused && <div className="card-body data-warn edit-refused" role="alert" data-testid="unmasked-refused">✕ <b>Unmasked read refused</b>: {g.unmasked_refused.message} (needs {g.unmasked_refused.needed}{g.unmasked_refused.role ? `, you are ${g.unmasked_refused.role}` : ''}). The rows below are still masked; the request was recorded.</div>}
      {/* enforce mode answers 403 to an unmasked read that is not allowed: say so (the rows shown stay the masked ones) */}
      {grid.err && g && unmask.on && <div className="card-body data-warn edit-refused" role="alert" data-testid="unmasked-refused">✕ <b>Unmasked read refused</b>: {grid.err}{/[.!?]$/.test(String(grid.err)) ? '' : '.'} The rows below are still masked; the request was recorded.</div>}
      {grid.err && !g ? <ErrorCard inline err={grid.err} /> : !g ? <Loading inline>Reading rows…</Loading> : (
        <div className="table-wrap edit-grid-wrap"><table className="table edit-grid" data-testid="edit-grid">
          <thead><tr><th className="r">_row</th>{g.columns.map((c) => <th key={c.name} className={['number', 'integer', 'decimal', 'float'].includes(c.kind) ? 'r' : undefined} title={c.masked ? `PII: masked (${c.mask}); edits to it still go to the stored value` : undefined} data-testid={c.masked ? `masked-col-${c.name}` : undefined}>{c.masked ? '🔒 ' : ''}{c.name} <span className="th-kind">{c.masked ? `masked · ${c.mask}` : c.kind}</span></th>)}<th /></tr></thead>
          <tbody>
            {newRow && <tr className="edit-newrow">
              <td className="r mono">new</td>
              {g.columns.map((c) => <td key={c.name}><input className="input edit-cell-input" value={newRow[c.name] ?? ''} onChange={(e) => setNewRow({ ...newRow, [c.name]: e.target.value })} aria-label={`new ${c.name}`} /></td>)}
              <td className="nowrap"><button className="btn sm" onClick={addRow} data-testid="edit-add-row-ok">Add</button> <button className="btn sm ghost" onClick={() => setNewRow(null)}>✕</button></td></tr>}
            {g.rows.map((r) => {
              const row = (r._row_id ?? r._row) as number  // __row_id: what an edit names (P07)
              const gone = deleted.has(row)
              return (
                <tr key={row} className={gone ? 'edit-deleted' : ''} data-testid={`edit-row-${row}`}>
                  <td className="r mono muted" title={`__row_id ${row}`}>{String(r._row ?? row)}</td>
                  {g.columns.map((c) => {
                    const k = `${row}|${c.name}`
                    const p = cells[k]
                    if (editing === k) return <td key={c.name}><input className="input edit-cell-input" autoFocus defaultValue={p ? p.value : show(r[c.name])}
                      onBlur={(e) => setCell(row, c.name, e.target.value, r[c.name])}
                      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(null) }} data-testid="edit-cell-input" /></td>
                    return <td key={c.name} className={`edit-cell ${p ? 'edit-pending' : ''} ${['number', 'integer', 'decimal', 'float'].includes(c.kind) ? 'r mono' : ''}`} title={p ? `was ${show(p.old) || '(missing)'}` : 'click to edit'}
                      onClick={() => !gone && setEditing(k)} data-testid={`edit-cell-${row}-${c.name}`}>{p ? p.value : show(r[c.name]) || <span className="muted">—</span>}</td>
                  })}
                  <td><button className="chip-btn" onClick={() => toggleDelete(row)} title={gone ? 'keep this row' : 'delete this row'} data-testid={`edit-del-${row}`}>{gone ? 'undo' : '✕'}</button></td>
                </tr>
              )
            })}
            {!g.rows.length && <tr><td colSpan={g.columns.length + 2} className="helper">No row matches.</td></tr>}
          </tbody>
        </table></div>)}
      <div className="card-body edit-tools">
        <div className="row edit-form">
          <span className="micro-label">column</span>
          <select className="select" value={col} onChange={(e) => setCol(e.target.value)} data-testid="edit-col"><option value="">pick…</option>{cols.map((c) => <option key={c.name}>{c.name}</option>)}</select>
          <select className="select" value={act} onChange={(e) => { setAct(e.target.value as ColAction); setArg1(''); setArg2('') }} data-testid="edit-col-action">
            <option value="rename_column">rename to</option><option value="drop_column">drop</option><option value="cast_column">cast to</option>
            <option value="fill_missing">fill missing with</option><option value="replace_values">replace value</option></select>
          {act === 'rename_column' && <input className="input" style={{ width: 140 }} placeholder="new name" value={arg1} onChange={(e) => setArg1(e.target.value)} data-testid="edit-col-arg" />}
          {act === 'cast_column' && <select className="select" value={arg1 || 'number'} onChange={(e) => setArg1(e.target.value)}><option>number</option><option>integer</option><option>text</option><option>bool</option></select>}
          {act === 'fill_missing' && <><select className="select" value={arg1 || 'median'} onChange={(e) => setArg1(e.target.value)}><option>median</option><option>mean</option><option>mode</option><option>constant</option></select>
            {arg1 === 'constant' && <input className="input" style={{ width: 100 }} placeholder="value" value={arg2} onChange={(e) => setArg2(e.target.value)} />}</>}
          {act === 'replace_values' && <><input className="input" style={{ width: 100 }} placeholder="find" value={arg1} onChange={(e) => setArg1(e.target.value)} /><input className="input" style={{ width: 100 }} placeholder="replace with" value={arg2} onChange={(e) => setArg2(e.target.value)} /></>}
          <button className="btn sm" disabled={!col} onClick={addColAction} data-testid="edit-col-add">Add</button>
        </div>
        <div className="row edit-form">
          <span className="micro-label">new column</span>
          <input className="input" style={{ width: 120 }} placeholder="name" value={newCol.name} onChange={(e) => setNewCol({ ...newCol, name: e.target.value })} data-testid="edit-newcol-name" />
          <span className="muted">=</span>
          <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="formula (loan_amount / income, log1p(income)) or a constant (0, 'manual')" value={newCol.expr} onChange={(e) => setNewCol({ ...newCol, expr: e.target.value })} data-testid="edit-newcol-expr" />
          <button className="btn sm" disabled={!newCol.name.trim() || !newCol.expr.trim()} onClick={addColumn} data-testid="edit-newcol-add">Add</button>
        </div>
      </div>
      <div className="card-body edit-pending-box" data-testid="edit-pending">
        <div className="row" style={{ gap: 8, alignItems: 'baseline' }}><b>Pending</b><span className="helper">{pending.length ? 'applied in this order when the version is created' : 'click a cell to edit it, ✕ to delete a row, or use the column tools'}</span></div>
        {pending.length > 0 && <ol className="edit-ops">{pending.map((p) => <li key={p.k}>{p.label} <button className="chip-btn" onClick={() => { p.drop(); changed() }} aria-label={`remove ${p.label}`}>✕</button></li>)}</ol>}
        {pv && ck && <div className="data-preview" data-testid="edit-preview">
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}><b>Preview: v{pv.v}, from v{v.v}</b><span className="helper">nothing is written until you create it</span></div>
          <div className="data-hero data-hero-sm">
            <div><span className="micro-label">changed</span><b data-testid="epv-changed">{pv.changed.toLocaleString()}</b></div>
            <div><span className="micro-label">added</span><b>+{pv.added.toLocaleString()}</b></div>
            <div><span className="micro-label">removed</span><b data-testid="epv-removed">−{pv.removed.toLocaleString()}</b></div>
            <div><span className="micro-label">rows × cols</span><b>{pv.rows.toLocaleString()} × {pv.n_cols}</b></div>
            <div><span className="micro-label">sha256</span><b className="mono" style={{ fontSize: 13 }} title={pv.sha256 ?? 'hashed when the version is written (a preview of a Parquet version is not hashed)'}>{pv.sha256 ? pv.sha256.slice(0, 10) : 'on write'}</b></div>
          </div>
          <ol className="edit-ops">{pv.edit_log.map((e) => <li key={e.i}>{e.summary}{e.examples.some((x) => x.column) && <span className="helper"> · {e.examples.filter((x) => x.column).slice(0, 3).map((x) => `row ${x.row} ${x.column}: ${show(x.old) || '—'} → ${show(x.new) || '—'}`).join(' · ')}</span>}</li>)}</ol>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {ck.columns_added.filter((c) => !Object.values(pv.renamed).includes(c)).map((c) => <span key={c} className="pt-chip data-add">+ {c}</span>)}
            {Object.entries(pv.renamed).map(([a, b]) => <span key={a} className="pt-chip data-type">{a} → {b}</span>)}
            {ck.columns_removed.filter((c) => !(c in pv.renamed)).map((c) => <span key={c} className="pt-chip data-rm">− {c}</span>)}
          </div>
          {pv.duplicate_of ? <div className="data-warn">The result has the same bytes as {pv.duplicate_of}: creating it returns that version, nothing new is made.</div>
            : contractWarnings.length > 0 ? <div className="data-warn"><b>{contractWarnings.length} contract warning{contractWarnings.length === 1 ? '' : 's'}</b>: v{pv.v} will be created but not made the default until someone does after review.{contractWarnings.map((w, i) => <div key={i}>⚠ {w.message}</div>)}</div>
              : integrity.length > 0 ? <span className="helper" data-testid="edit-not-default">v{pv.v} will be created but not made the default: it changes what v{v.v} promised (below).</span>
                : <span className="helper data-ok-line">No contract warnings: v{pv.v} will become the default.</span>}
          {!pv.duplicate_of && integrity.length > 0 && <IntegrityAcks items={integrity} acked={iack} setAcked={setIack} />}
          {!pv.duplicate_of && integrityCodes.filter((c) => !integrity.some((w) => w.code === c)).map((c) => <label key={c} className="row data-warn" style={{ gap: 6 }}>
            <input type="checkbox" checked={iack.has(c)} onChange={(e) => { const x = new Set(iack); if (e.target.checked) x.add(c); else x.delete(c); setIack(x) }} data-testid={`up-ack-${c}`} />
            <span>I acknowledge <span className="mono">{c}</span>{(() => { const w = ck.warnings.find((x) => x.code === c); return w ? `: ${w.message}` : '' })()}</span></label>)}
        </div>}
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          <input className="input" style={{ flex: 1, minWidth: 240 }} maxLength={500} placeholder="reason or change ticket (required, kept with the version and in the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="edit-reason" />
          {pv ? <button className="btn primary" disabled={busy || !by || reason.trim().length < 3 || unacked.length > 0} onClick={create} data-testid="edit-create"
            title={reason.trim().length < 3 ? 'Give a reason first' : unacked.length ? `Acknowledge ${unacked.join(', ')} first` : undefined}>{busy ? (prog ? `Creating… ${pctText(prog)}` : 'Creating…') : `Create v${pv.v}`}</button>
            : <button className="btn primary" disabled={busy || !by || !allOps.length} onClick={preview} data-testid="edit-preview-btn">{busy ? 'Previewing…' : 'Preview'}</button>}
        </div>
      </div>
    </div>
  )
}

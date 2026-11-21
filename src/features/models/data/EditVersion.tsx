import { useMemo, useState } from 'react'
import { data, type DsEditOp, type DsEditPreview, type DsGrid, type DsHistory, type DsVersion } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import { ErrorCard, Loading } from '@/shared/components/Status'

/** DL3: edit a version as typed operations. Cell edits, row deletes and column actions pile up as pending operations;
 *  Preview (writes nothing) shows what the new version would be; "Create vN" (reason required) commits it as a NEW
 *  version through the version path (lineage, root rows, holdout, audit). The version being edited never changes. */

type ColAction = 'rename_column' | 'drop_column' | 'cast_column' | 'fill_missing' | 'replace_values'
const PAGE = 25
const show = (v: unknown) => (v == null ? '' : typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toPrecision(6).replace(/\.?0+$/, '')) : String(v))

export function EditVersion({ d, v, by, onClose, onCreated, onErr }: {
  d: DsHistory; v: DsVersion; by: string; onClose: () => void; onCreated: (vid: string, text: string) => void; onErr: (e: unknown) => void
}) {
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const grid = useFetch<DsGrid>(() => data.grid(v.id, { offset: page * PAGE, limit: PAGE, q }), [v.id, page, q])
  const [cells, setCells] = useState<Record<string, { row: number; column: string; value: string; old: unknown }>>({})
  const [deleted, setDeleted] = useState<Set<number>>(new Set())
  const [ops, setOps] = useState<{ op: DsEditOp; label: string }[]>([])
  const [editing, setEditing] = useState<string | null>(null)
  const [pv, setPv] = useState<DsEditPreview | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
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
    if (ce.length) out.push({ op: 'set_cells', cells: ce.map((c) => ({ row: c.row, column: c.column, value: c.value })) })
    if (deleted.size) out.push({ op: 'delete_rows', rows: [...deleted].sort((a, b) => a - b) })
    return [...out, ...ops.map((o) => o.op)]
  }, [cells, deleted, ops])
  const pending = [
    ...(Object.keys(cells).length ? [{ k: 'cells', label: `set ${Object.keys(cells).length} cell${Object.keys(cells).length === 1 ? '' : 's'}`, drop: () => setCells({}) }] : []),
    ...(deleted.size ? [{ k: 'del', label: `delete ${deleted.size} row${deleted.size === 1 ? '' : 's'} (${[...deleted].slice(0, 6).join(', ')}${deleted.size > 6 ? '…' : ''})`, drop: () => setDeleted(new Set()) }] : []),
    ...ops.map((o, i) => ({ k: `op${i}`, label: o.label, drop: () => setOps(ops.filter((_, j) => j !== i)) })),
  ]
  const changed = () => setPv(null)  // any new pending operation invalidates the preview
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
    try { setPv(await data.editPreview(v.id, allOps, by)) } catch (e) { onErr(e) } finally { setBusy(false) }
  }
  const create = async () => {
    setBusy(true)
    try {
      const r = await data.edit(v.id, allOps, by, reason.trim())
      if (!r.created) onCreated(r.duplicate_of ?? r.id, `Nothing new: the result has the same bytes as ${r.duplicate_of}, so no version was made.`)
      else onCreated(r.id, `v${pv?.v} (${r.id}) created from v${v.v}: ${r.edit.ops_summary.join('; ')}. `
        + (r.default?.is_default ? 'It is now the default.' : `Not the default (${r.default?.why}): review it, then make it the default.`))
    } catch (e) { onErr(e) } finally { setBusy(false) }
  }

  const g = grid.data
  const ck = pv?.checks
  return (
    <div className="card" data-testid="data-editor">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Edit v{v.v}</h3>
          <span className="helper" style={{ fontSize: 12 }}>changes are saved as a new version (v{d.versions.length + 1}); v{v.v} is never modified</span></div>
        <button className="btn sm ghost" onClick={onClose} data-testid="edit-close">Close</button>
      </div>
      <div className="card-body row edit-bar">
        <input className="input" style={{ width: 200 }} placeholder="search rows…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0) }} data-testid="edit-q" />
        {g && <span className="helper mono">{g.matching ? `${(g.offset + 1).toLocaleString()}–${Math.min(g.offset + PAGE, g.matching).toLocaleString()} of ${g.matching.toLocaleString()}` : '0 rows'}{g.matching !== g.total ? ` (of ${g.total.toLocaleString()})` : ''}</span>}
        <button className="btn sm ghost" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="previous page">‹</button>
        <button className="btn sm ghost" disabled={!g || (page + 1) * PAGE >= g.matching} onClick={() => setPage(page + 1)} aria-label="next page" data-testid="edit-next">›</button>
        <span className="edit-sep" />
        <button className="btn sm ghost" onClick={() => setNewRow(Object.fromEntries(cols.map((c) => [c.name, ''])))} data-testid="edit-add-row">+ Row</button>
      </div>
      {grid.err && !g ? <ErrorCard inline err={grid.err} /> : !g ? <Loading inline>Reading rows…</Loading> : (
        <div className="table-wrap edit-grid-wrap"><table className="table edit-grid" data-testid="edit-grid">
          <thead><tr><th className="r">_row</th>{g.columns.map((c) => <th key={c.name}>{c.name} <span className="muted" style={{ fontWeight: 400 }}>{c.kind}</span></th>)}<th /></tr></thead>
          <tbody>
            {newRow && <tr className="edit-newrow">
              <td className="r mono">new</td>
              {g.columns.map((c) => <td key={c.name}><input className="input edit-cell-input" value={newRow[c.name] ?? ''} onChange={(e) => setNewRow({ ...newRow, [c.name]: e.target.value })} aria-label={`new ${c.name}`} /></td>)}
              <td className="nowrap"><button className="btn sm" onClick={addRow} data-testid="edit-add-row-ok">Add</button> <button className="btn sm ghost" onClick={() => setNewRow(null)}>✕</button></td></tr>}
            {g.rows.map((r) => {
              const row = r._row as number
              const gone = deleted.has(row)
              return (
                <tr key={row} className={gone ? 'edit-deleted' : ''} data-testid={`edit-row-${row}`}>
                  <td className="r mono muted">{row}</td>
                  {g.columns.map((c) => {
                    const k = `${row}|${c.name}`
                    const p = cells[k]
                    if (editing === k) return <td key={c.name}><input className="input edit-cell-input" autoFocus defaultValue={p ? p.value : show(r[c.name])}
                      onBlur={(e) => setCell(row, c.name, e.target.value, r[c.name])}
                      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(null) }} data-testid="edit-cell-input" /></td>
                    return <td key={c.name} className={`edit-cell ${p ? 'edit-pending' : ''} ${c.kind === 'number' ? 'r mono' : ''}`} title={p ? `was ${show(p.old) || '(missing)'}` : 'click to edit'}
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
            <div><span className="micro-label">sha256</span><b className="mono" style={{ fontSize: 13 }}>{pv.sha256.slice(0, 10)}</b></div>
          </div>
          <ol className="edit-ops">{pv.edit_log.map((e) => <li key={e.i}>{e.summary}{e.examples.some((x) => x.column) && <span className="helper"> · {e.examples.filter((x) => x.column).slice(0, 3).map((x) => `row ${x.row} ${x.column}: ${show(x.old) || '—'} → ${show(x.new) || '—'}`).join(' · ')}</span>}</li>)}</ol>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {ck.columns_added.filter((c) => !Object.values(pv.renamed).includes(c)).map((c) => <span key={c} className="pt-chip data-add">+ {c}</span>)}
            {Object.entries(pv.renamed).map(([a, b]) => <span key={a} className="pt-chip data-type">{a} → {b}</span>)}
            {ck.columns_removed.filter((c) => !(c in pv.renamed)).map((c) => <span key={c} className="pt-chip data-rm">− {c}</span>)}
          </div>
          {pv.duplicate_of ? <div className="data-warn">The result has the same bytes as {pv.duplicate_of}: creating it returns that version, nothing new is made.</div>
            : ck.warnings.length > 0 ? <div className="data-warn"><b>{ck.warnings.length} contract warning{ck.warnings.length === 1 ? '' : 's'}</b>: v{pv.v} will be created but not made the default until someone does after review.{ck.warnings.map((w, i) => <div key={i}>⚠ {w.message}</div>)}</div>
              : <span className="helper data-ok-line">No contract warnings: v{pv.v} will become the default.</span>}
        </div>}
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          <input className="input" style={{ flex: 1, minWidth: 240 }} maxLength={500} placeholder="reason or change ticket (required, kept with the version and in the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="edit-reason" />
          {pv ? <button className="btn primary" disabled={busy || !by || reason.trim().length < 3} onClick={create} data-testid="edit-create"
            title={reason.trim().length < 3 ? 'Give a reason first' : undefined}>{busy ? 'Creating…' : `Create v${pv.v}`}</button>
            : <button className="btn primary" disabled={busy || !by || !allOps.length} onClick={preview} data-testid="edit-preview-btn">{busy ? 'Previewing…' : 'Preview'}</button>}
        </div>
      </div>
    </div>
  )
}

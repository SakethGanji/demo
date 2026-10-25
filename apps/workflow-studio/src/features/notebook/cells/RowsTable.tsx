/**
 * A result table you can act on: search, filter, sort, tick rows (all / the filtered ones), then act on exactly those
 * rows by a stable key column. Every action writes real cells: a Change keyed on the ticked keys (diff first, Apply
 * writes), or a For each over the ticked rows with a Workflow or HTTP step inside. Ask gets the selection too.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { formatCell } from '@/shared/glass'
import { errorMessage } from '@/shared/api/http'
import { useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { notebookApi } from '../api'
import { useNb } from '../context'
import { nbKeys } from '../hooks'
import { askNotice, asRows, plural } from '../lib/cells'
import { isStatusColumn } from '../lib/look'
import { StatusPill } from './Look'
import type { Cell, HttpMethod, QuerySpec } from '../types'

type Row = Record<string, unknown>
type Key = string | number | boolean | null
type FilterOp = 'contains' | 'eq' | 'ne' | 'gt' | 'lt' | 'empty' | 'set'
const FILTER_OPS: { v: FilterOp; t: string }[] = [
  { v: 'contains', t: 'contains' }, { v: 'eq', t: '=' }, { v: 'ne', t: '≠' }, { v: 'gt', t: '>' }, { v: 'lt', t: '<' },
  { v: 'empty', t: 'is empty' }, { v: 'set', t: 'is set' },
]
/** the most rows the table loads to select from (the rows route's page cap) */
export const SELECT_CAP = 500

/** The column that names a row: id, *_id, key, else the first column whose values are all distinct. */
export function guessKey(columns: string[], rows: Row[]): string {
  const distinct = (c: string) => new Set(rows.map((r) => JSON.stringify(r[c] ?? null))).size === rows.length
  const named = columns.find((c) => /^(id|key)$/i.test(c)) ?? columns.find((c) => /_id$|_key$|_ref$/i.test(c))
  if (named && distinct(named)) return named
  return columns.find(distinct) ?? columns[0] ?? ''
}

/** "SELECT … FROM bank.wires w WHERE …" -> "bank.wires" (the table a Change on these rows most likely writes). */
export function guessTable(sql: string | undefined): string {
  const m = /\bfrom\s+([A-Za-z_][\w.]*)/i.exec(sql ?? '')
  return m ? m[1] : ''
}

const cmp = (a: unknown, b: unknown) => {
  if (a === b) return 0
  if (a === null || a === undefined) return 1
  if (b === null || b === undefined) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  const na = Number(a), nb = Number(b)
  if (a !== '' && b !== '' && !Number.isNaN(na) && !Number.isNaN(nb)) return na - nb
  return String(a).localeCompare(String(b))
}

function matches(v: unknown, op: FilterOp, want: string): boolean {
  const s = v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
  switch (op) {
    case 'empty': return s === ''
    case 'set': return s !== ''
    case 'contains': return s.toLowerCase().includes(want.toLowerCase())
    case 'eq': return s === want
    case 'ne': return s !== want
    case 'gt': return cmp(v, want) > 0
    case 'lt': return cmp(v, want) < 0
  }
}

export function toCsv(columns: string[], rows: Row[]): string {
  const q = (v: unknown) => {
    const s = v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [columns.map(q).join(','), ...rows.map((r) => columns.map((c) => q(r[c])).join(','))].join('\n')
}

function download(name: string, text: string) {
  const u = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = u
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(u), 1000)
}

type Act = null | 'change' | 'workflow' | 'http'

export function RowsTable({ cell, compact }: { cell: Cell; compact?: boolean }) {
  const { caps, actions, me, fail, notify, live } = useNb()
  const r = cell.latest
  const preview = asRows(r?.output ?? null)
  const complete = !!preview && !preview.truncated && preview.rows.length >= preview.row_count
  const all = useQuery({
    queryKey: [...nbKeys.rows(cell.id, r?.run_id), 'all'],
    queryFn: () => notebookApi.rows(cell.id, { run: r?.run_id, offset: 0, limit: SELECT_CAP }),
    enabled: !!preview && !complete,
    retry: false,
  })
  const columns = all.data?.columns ?? preview?.columns ?? []
  const rows: Row[] = useMemo(() => (complete ? preview!.rows : all.data?.rows ?? preview?.rows ?? []), [complete, preview, all.data])
  const total = all.data?.total ?? preview?.row_count ?? rows.length

  const [search, setSearch] = useState('')
  const [fcol, setFcol] = useState('')
  const [fop, setFop] = useState<FilterOp>('contains')
  const [fval, setFval] = useState('')
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null)
  const [keyCol, setKeyCol] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [act, setAct] = useState<Act>(null)
  const [askText, setAskText] = useState('')
  const [shown, setShown] = useState(compact ? 8 : 100)
  useEffect(() => setSel(new Set()), [r?.id])
  const key = keyCol && columns.includes(keyCol) ? keyCol : guessKey(columns, rows)
  const kv = (row: Row) => JSON.stringify(row[key] ?? null)
  const unique = useMemo(() => new Set(rows.map(kv)).size === rows.length, [rows, key]) // eslint-disable-line react-hooks/exhaustive-deps

  const view = useMemo(() => {
    const q = search.trim().toLowerCase()
    let out = rows
    if (q) out = out.filter((row) => columns.some((c) => String(formatCell(row[c]).text).toLowerCase().includes(q)))
    if (fcol && (fval !== '' || fop === 'empty' || fop === 'set')) out = out.filter((row) => matches(row[fcol], fop, fval))
    if (sort) out = [...out].sort((a, b) => cmp(a[sort.col], b[sort.col]) * sort.dir)
    return out
  }, [rows, columns, search, fcol, fop, fval, sort])
  const filtered = view.length !== rows.length
  const picked = rows.filter((row) => sel.has(kv(row)))
  const keys: Key[] = picked.map((row) => (row[key] ?? null) as Key)
  const statusCols = new Set(columns.filter((c) => isStatusColumn(c, rows)))
  const allOn = rows.length > 0 && sel.size === rows.length
  const toggle = (row: Row) => {
    const k = kv(row)
    const next = new Set(sel)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    setSel(next)
  }
  const aiOn = !!caps?.ai.available

  const ask = () => {
    const t = askText.trim()
    if (!t || !aiOn || keys.length === 0) return
    actions.ask.mutate(
      { text: t, by: me, selection: { cell: cell.name, key, keys } },
      {
        onSuccess: (res) => {
          setAskText('')
          notify(...askNotice(res, ` for ${plural(keys.length, 'selected row')}`))
        },
        onError: fail,
      },
    )
  }

  if (!preview) return null
  return (
    <div className="nb-rt" data-testid="nb-rows-table" data-compact={compact ? '' : undefined}>
      <div className="nb-rt-tools">
        <input className="nb-input nb-rt-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search rows" aria-label="Search rows" data-testid="nb-rt-search" />
        <select className="nb-select" value={fcol} onChange={(e) => setFcol(e.target.value)} aria-label="Filter column" data-testid="nb-rt-fcol">
          <option value="">Filter…</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {fcol && (
          <>
            <select className="nb-select" value={fop} onChange={(e) => setFop(e.target.value as FilterOp)} aria-label="Filter operator" data-testid="nb-rt-fop">
              {FILTER_OPS.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
            </select>
            {fop !== 'empty' && fop !== 'set' && (
              <input className="nb-input" style={{ width: 110 }} value={fval} onChange={(e) => setFval(e.target.value)} aria-label="Filter value" data-testid="nb-rt-fval" />
            )}
            <button type="button" className="nb-linkbtn text-[12px]" onClick={() => { setFcol(''); setFval('') }}>Clear filter</button>
          </>
        )}
        <span className="nb-sp" />
        <label className="nb-rt-key" title="The column that names each row: actions match rows by it">
          by
          <select className="nb-select" value={key} onChange={(e) => { setKeyCol(e.target.value); setSel(new Set()) }} aria-label="Key column" data-testid="nb-rt-key">
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      </div>
      <div className="nb-rt-sub">
        <span>
          {filtered ? `${plural(view.length, 'row')} of ${total.toLocaleString('en-US')} match` : plural(total, 'row')}
          {total > rows.length && <span className="nb-faint"> · the first {rows.length.toLocaleString('en-US')} can be selected</span>}
        </span>
        <button type="button" className="nb-linkbtn text-[12px]" disabled={!unique} onClick={() => setSel(allOn ? new Set() : new Set(rows.map(kv)))} data-testid="nb-rt-all">
          {allOn ? 'Clear selection' : `Select all ${rows.length}`}
        </button>
        {filtered && view.length > 0 && (
          <button type="button" className="nb-linkbtn text-[12px]" disabled={!unique} onClick={() => setSel(new Set([...sel, ...view.map(kv)]))} data-testid="nb-rt-filtered">
            Select the {view.length} filtered
          </button>
        )}
        {!unique && <span className="nb-err">{key} repeats across rows · pick a key column that names each row</span>}
      </div>
      <div className="gl-table nb-rt-table">
        <div className="gl-table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col" className="nb-rt-ck">
                  <input type="checkbox" aria-label="Select all rows" checked={allOn} disabled={!unique || rows.length === 0} onChange={() => setSel(allOn ? new Set() : new Set(rows.map(kv)))} />
                </th>
                {columns.map((c) => (
                  <th key={c} scope="col" aria-sort={sort?.col === c ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" className="nb-rt-sort" onClick={() => setSort(sort?.col === c ? (sort.dir === 1 ? { col: c, dir: -1 } : null) : { col: c, dir: 1 })} data-testid="nb-rt-sort">
                      {c}
                      <span aria-hidden className="nb-rt-arrow">{sort?.col === c ? (sort.dir === 1 ? '↑' : '↓') : ''}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.slice(0, shown).map((row, i) => {
                const on = sel.has(kv(row))
                return (
                  <tr key={`${kv(row)}:${i}`} data-selected={on ? '' : undefined} data-testid="nb-rt-row">
                    <td className="nb-rt-ck">
                      <input type="checkbox" aria-label={`Select row ${String(row[key] ?? i + 1)}`} checked={on} disabled={!unique} onChange={() => toggle(row)} />
                    </td>
                    {columns.map((c) => {
                      const f = formatCell(row[c])
                      return (
                        <td key={c} data-num={f.num ? '' : undefined} data-null={f.nil ? '' : undefined} title={f.text}>
                          {statusCols.has(c) && typeof row[c] === 'string' ? <StatusPill value={String(row[c])} /> : f.text}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {view.length > shown && (
          <button type="button" className="gl-table-more nb-linkbtn" onClick={() => setShown(shown + 100)}>
            Show {Math.min(100, view.length - shown)} more of {view.length}
          </button>
        )}
        {view.length === 0 && <div className="nb-faint p-2">No rows match.</div>}
      </div>

      {sel.size > 0 && (
        <div className="nb-rt-bar" data-testid="nb-rt-bar">
          <div className="nb-row">
            <b>{plural(sel.size, 'row')} selected</b>
            <span className="nb-faint">by {key}</span>
            <span className="nb-sp" />
            <button type="button" className="gl-btn" data-size="sm" data-pressed={act === 'change' ? '' : undefined} disabled={live} onClick={() => setAct(act === 'change' ? null : 'change')} data-testid="nb-rt-act-change">Change selected…</button>
            <button type="button" className="gl-btn" data-size="sm" data-pressed={act === 'workflow' ? '' : undefined} disabled={live} onClick={() => setAct(act === 'workflow' ? null : 'workflow')} data-testid="nb-rt-act-workflow">Run workflow on selected…</button>
            <button type="button" className="gl-btn" data-size="sm" data-pressed={act === 'http' ? '' : undefined} disabled={live} onClick={() => setAct(act === 'http' ? null : 'http')} data-testid="nb-rt-act-http">Call API for selected…</button>
            <button type="button" className="gl-btn" data-size="sm" onClick={() => download(`${cell.name}-selected.csv`, toCsv(columns, view.filter((row) => sel.has(kv(row)))))} data-testid="nb-rt-csv">Export CSV</button>
          </div>
          {act === 'change' && <ChangeForm cell={cell} columns={columns} keyCol={key} keys={keys} onDone={() => setAct(null)} />}
          {act === 'workflow' && <LoopForm what="workflow" columns={columns} rows={picked} onDone={() => setAct(null)} />}
          {act === 'http' && <LoopForm what="http" columns={columns} rows={picked} onDone={() => setAct(null)} />}
          <form className="nb-rt-ask" onSubmit={(e) => (e.preventDefault(), ask())}>
            <span aria-hidden>✦</span>
            <input
              className="nb-input"
              value={askText}
              onChange={(e) => setAskText(e.target.value)}
              placeholder={aiOn ? `Ask about these ${sel.size}: “release the selected wires”` : 'Ask needs AI, which this engine does not have'}
              disabled={!aiOn}
              aria-label="Ask about the selected rows"
              data-testid="nb-rt-ask"
            />
            <button type="submit" className="gl-btn" data-size="sm" disabled={!aiOn || !askText.trim() || actions.ask.isPending || live}>Ask</button>
          </form>
        </div>
      )}
      {all.isError && <div className="nb-faint">Could not load every row ({errorMessage(all.error)}); acting on the rows shown.</div>}
    </div>
  )
}

function ChangeForm({ cell: src, columns: cols, keyCol, keys: ks, onDone }: { cell: Cell; columns: string[]; keyCol: string; keys: Key[]; onDone: () => void }) {
  const { connections, actions, me, notify, rerunFrom } = useNb()
  const writable = connections.filter((c) => !c.readonly)
  const spec = src.spec as Partial<QuerySpec>
  const [table, setTable] = useState(src.kind === 'query' ? guessTable(spec.sql) : '')
  const [conn, setConn] = useState(() => {
    const same = connections.find((c) => (c.key === spec.connection || c.id === spec.connection) && !c.readonly)
    return same?.key ?? writable[0]?.key ?? ''
  })
  const [col, setCol] = useState(cols.find((c) => /status/i.test(c)) ?? cols.find((c) => c !== keyCol) ?? '')
  const [val, setVal] = useState('')
  const [reason, setReason] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const submit = async () => {
    setErr(null)
    const v: unknown = val.trim() !== '' && !Number.isNaN(Number(val)) && !/^0\d/.test(val.trim()) ? Number(val) : val === 'null' ? null : val
    try {
      const made = await actions.addCell.mutateAsync({
        kind: 'change',
        title: `Change ${plural(ks.length, 'selected row')}`,
        spec: { connection: conn, table: table.trim(), key: { [keyCol]: ks }, set: { [col]: v }, reason: reason.trim() },
        created_by: me,
      })
      onDone()
      notify(`Proposing the change for ${plural(ks.length, 'row')} · nothing is written until you Apply`, 'need')
      rerunFrom(made.id)
      setTimeout(() => document.getElementById(`cell-${made.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 300)
    } catch (e) {
      setErr(errorMessage(e))
    }
  }
  return (
    <form className="nb-rt-form" data-testid="nb-rt-change" onSubmit={(e) => (e.preventDefault(), void submit())}>
      <div className="nb-row">
        In
        <input className="nb-input" style={{ width: 150 }} value={table} onChange={(e) => setTable(e.target.value)} placeholder="schema.table" aria-label="Table" />
        on
        <select className="nb-select" value={conn} onChange={(e) => setConn(e.target.value)} aria-label="Writable connection">
          {writable.length === 0 && <option value="">No writable connection</option>}
          {writable.map((c) => <option key={c.id} value={c.key}>{c.name}</option>)}
        </select>
        set
        <select className="nb-select" value={col} onChange={(e) => setCol(e.target.value)} aria-label="Column to change">
          {cols.filter((c) => c !== keyCol).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        =
        <input className="nb-input" style={{ width: 120 }} value={val} onChange={(e) => setVal(e.target.value)} placeholder="released" aria-label="New value" />
      </div>
      <div className="nb-row">
        <input className="nb-input" style={{ flex: 1, minWidth: 180 }} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (kept with the change)" aria-label="Reason" />
        <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={onDone}>Cancel</button>
        <button type="submit" className="gl-btn" data-variant="primary" data-size="sm" disabled={!table.trim() || !conn || !col || actions.addCell.isPending} data-testid="nb-rt-change-go">
          Preview the diff
        </button>
      </div>
      <div className="nb-faint">
        Only rows whose {keyCol} is one of the {ks.length} selected change. Studio shows the diff first; Apply writes it.
      </div>
      {err && <div className="nb-err" data-testid="nb-rt-error">{err}</div>}
    </form>
  )
}

function LoopForm({ what, columns: cols, rows: picked2, onDone }: { what: 'workflow' | 'http'; columns: string[]; rows: Row[]; onDone: () => void }) {
  const { pid, actions, me, notify } = useNb()
  const workflows = useProjectWorkflows(pid)
  const [wf, setWf] = useState('')
  const [inputs, setInputs] = useState<Set<string>>(new Set(cols))
  const [method, setMethod] = useState<HttpMethod>('POST')
  const [url, setUrl] = useState(`{{base_url}}/items/{{row.${cols[0] ?? 'id'}}}`)
  const [err, setErr] = useState<string | null>(null)
  const submit = async () => {
    setErr(null)
    try {
      const loop = await actions.addCell.mutateAsync({
        kind: 'for_each',
        title: `For each of ${plural(picked2.length, 'selected row')}`,
        spec: { over: picked2, mode: 'sequential' },
        created_by: me,
      })
      if (what === 'workflow')
        await actions.addCell.mutateAsync({
          kind: 'workflow', parent_cell_id: loop.id, created_by: me,
          spec: { workflow_id: wf, inputs: Object.fromEntries([...inputs].map((c) => [c, `{{row.${c}}}`])) },
        })
      else
        await actions.addCell.mutateAsync({
          kind: 'http', parent_cell_id: loop.id, created_by: me,
          spec: { method, url, ...(method !== 'GET' && method !== 'HEAD' ? { body: Object.fromEntries([...inputs].map((c) => [c, `{{row.${c}}}`])) } : {}), assertions: [{ kind: 'status', op: 'lt', value: 400 }] },
        })
      onDone()
      notify(`Added a For each over ${plural(picked2.length, 'row')} · Run it when ready`)
      setTimeout(() => document.getElementById(`cell-${loop.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 300)
    } catch (e) {
      setErr(errorMessage(e))
    }
  }
  return (
    <form className="nb-rt-form" data-testid={`nb-rt-${what}`} onSubmit={(e) => (e.preventDefault(), void submit())}>
      {what === 'workflow' ? (
        <div className="nb-row">
          Run
          <select className="nb-select" value={wf} onChange={(e) => setWf(e.target.value)} aria-label="Workflow">
            <option value="">Choose a workflow…</option>
            {(workflows.data ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
          once per row, with
        </div>
      ) : (
        <div className="nb-row">
          <select className="nb-select" value={method} onChange={(e) => setMethod(e.target.value as HttpMethod)} aria-label="Method">
            {(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const).map((m) => <option key={m}>{m}</option>)}
          </select>
          <input className="nb-input nb-mono" style={{ flex: 1, minWidth: 220 }} value={url} onChange={(e) => setUrl(e.target.value)} aria-label="URL" />
        </div>
      )}
      {(what === 'workflow' || (method !== 'GET' && method !== 'HEAD')) && (
        <div className="nb-row nb-rt-cols">
          <span className="nb-faint">{what === 'workflow' ? 'inputs' : 'body'}</span>
          {cols.map((c) => (
            <label key={c} className="nb-rt-colpick">
              <input type="checkbox" checked={inputs.has(c)} onChange={() => { const n = new Set(inputs); if (n.has(c)) n.delete(c); else n.add(c); setInputs(n) }} />
              {c}
            </label>
          ))}
        </div>
      )}
      <div className="nb-row">
        <span className="nb-faint">{what === 'http' ? 'Use {{row.column}} for a value of the row, {{base_url}} from the environment. ' : ''}Adds a For each over the {picked2.length} rows; nothing runs until you run it.</span>
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={onDone}>Cancel</button>
        <button type="submit" className="gl-btn" data-variant="primary" data-size="sm" disabled={(what === 'workflow' && !wf) || (what === 'http' && !url.trim()) || actions.addCell.isPending} data-testid={`nb-rt-${what}-go`}>
          Add the steps
        </button>
      </div>
      {err && <div className="nb-err" data-testid="nb-rt-error">{err}</div>}
    </form>
  )
}

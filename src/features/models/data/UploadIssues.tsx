import { useState } from 'react'
import type { UpCheck } from '@/shared/api/catalog'

/** U3 (F-X3a): the upload check's newer warnings and blocks, read as sentences with their numbers, and the two that
 *  need a choice: an ambiguous date gets a format picker (sent as the plan's `tables.<t>.formats`), an `_op` data column
 *  gets "read it as operations" (the plan's `op_column`). Integrity warnings that the commit must acknowledge by code
 *  (`key_invalidated`, `relation_dropped`) are listed with their counts in `IntegrityAcks`. */

export type Issue = UpCheck['warnings'][number] & {
  table?: string; column?: string; columns?: unknown; n?: number; examples?: unknown[]; type?: string; sheet?: string; cells?: number
  needs_ack?: boolean; why?: string; kind?: string; n_duplicates?: number; n_nulls?: number; to_duplicates?: number; relation?: string
  from?: { table: string; columns: string[] }; to?: { table: string; columns: string[] }
}
type LossyCol = { column: string; from: string; to: string; n: number; examples: unknown[] }
export const INTEGRITY_ACKS = ['key_invalidated', 'relation_dropped'] as const
export const FORMATS = [{ f: '%d/%m/%Y', name: 'day first (dd/mm/yyyy)' }, { f: '%m/%d/%Y', name: 'month first (mm/dd/yyyy)' }] as const

const n = (x?: number | null) => (x == null ? '—' : x.toLocaleString())
const ex = (v: unknown) => (typeof v === 'string' ? `“${v}”` : String(v))
/** A slash date read with a format, for the picker's preview: 03/02/2021 with %d/%m/%Y → 2021-02-03. */
export const readAs = (v: string, f: string) => {
  const m = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(v)
  if (!m) return null
  const [a, b, y] = [m[1], m[2], m[3]]
  const [d, mo] = f.startsWith('%d') ? [a, b] : [b, a]
  return `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`
}

/** The columns an issue names (a block of a delta names several; a warning one). */
const colsOf = (x: Issue): string[] => (Array.isArray(x.columns) && x.columns.every((c) => typeof c === 'string') ? x.columns as string[] : x.column ? [x.column] : [])

export function IssueLine({ x, tone, chk, onFormat, onOpColumn, busy }: {
  x: Issue; tone: 'block' | 'warn'; chk: UpCheck; busy?: boolean
  onFormat?: (table: string, formats: Record<string, string>) => void
  onOpColumn?: (table: string, column: string) => void
}) {
  const mark = tone === 'block' ? '✕' : '⚠'
  const table = x.table ?? chk.tables[0]?.name ?? ''
  if (x.code === 'lossy_cast') {
    const rows: LossyCol[] = Array.isArray(x.columns) && x.columns.length && typeof x.columns[0] === 'object'
      ? x.columns as LossyCol[] : [{ column: x.column ?? '?', from: 'text in the file', to: x.type ?? '?', n: x.n ?? 0, examples: x.examples ?? [] }]
    return <div className="up-issue" data-testid={`up-issue-lossy_cast${x.table ? `-${x.table}` : ''}`}>
      {mark} <b>Values would change when stored</b>{x.table ? <> in <span className="mono">{x.table}</span></> : null}: nothing is rounded or truncated silently, so this upload stops here.
      <table className="table up-issue-t"><thead><tr><th>column</th><th>read as</th><th className="r">values that change</th><th>e.g.</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.column}><td className="mono">{r.column}</td><td className="mono">{r.from} → {r.to}</td><td className="r mono">{n(r.n)}</td>
          <td className="mono">{r.examples.slice(0, 4).map(ex).join(', ')}{r.to === 'BIGINT' ? ' (decimals would be rounded)' : r.to === 'DATE' ? ' (the time of day would be dropped)' : ''}</td></tr>)}</tbody></table>
      <span className="helper">Fix these values in the file, or change the table's column type first (a rewrite of the table).</span>
    </div>
  }
  if (x.code === 'date_ambiguous') return <DatePicker x={x} tone={tone} chk={chk} table={table} onFormat={onFormat} busy={busy} />
  if (x.code === 'delta_op_column_ambiguous') {
    return <div className="up-issue" data-testid="up-issue-op-ambiguous">{mark} <b>Is <span className="mono">{x.column}</span> data or the operation of each row?</b> <span className="mono">{table}</span> already has a column called {x.column}, so it is never read as operations without your say-so.
      {onOpColumn && <div className="row" style={{ gap: 6, marginTop: 4 }}><button className="btn sm" disabled={busy} onClick={() => onOpColumn(table, x.column ?? '_op')} data-testid="up-op-as-ops">Read {x.column} as operations (I / U / D)</button>
        <span className="helper">or pick another op column in the plan's delta options</span></div>}</div>
  }
  if (x.code === 'delta_key_near_match') {
    const pairs = (x.examples ?? []) as { delta: unknown; parent: unknown }[]
    return <div className="up-issue" data-testid="up-issue-near-key">{mark} <b>{n(x.n)} inserted row{x.n === 1 ? '' : 's'} almost match an existing <span className="mono">{x.column}</span></b> (equal once spaces and leading zeros are removed), so they are added as <i>new</i> rows:
      <span className="mono"> {pairs.slice(0, 4).map((p) => `${ex(p.delta)} ≈ ${ex(p.parent)}`).join(' · ')}</span>. If they meant the existing rows, write the key exactly as the table holds it.</div>
  }
  if (x.code === 'excel_formula_no_value' || x.code === 'excel_merged_cells' || x.code === 'sheet_renamed') {
    return <div className="up-issue" data-testid={`up-issue-${x.code}`}>{mark} <span className="pt-chip">sheet {x.sheet}</span> {x.message}</div>
  }
  return <div className="up-issue">{mark} {x.table ? <span className="mono">{x.table}: </span> : null}{x.message}</div>
}

function DatePicker({ x, tone, chk, table, onFormat, busy }: { x: Issue; tone: 'block' | 'warn'; chk: UpCheck; table: string; onFormat?: (table: string, formats: Record<string, string>) => void; busy?: boolean }) {
  const cols = colsOf(x)
  const [pick, setPick] = useState<Record<string, string>>({})
  const t = chk.tables.find((y) => y.name === table) ?? chk.tables[0]
  const sample = (c: string) => {
    const i = t?.preview.columns.indexOf(c) ?? -1
    const fromEx = (x.examples ?? []).find((v) => typeof v === 'string') as string | undefined
    return fromEx ?? (i >= 0 ? t!.preview.rows.map((r) => r[i]).find((v) => typeof v === 'string' && /\//.test(v)) as string | undefined : undefined)
  }
  return <div className="up-issue" data-testid="up-issue-date-ambiguous">{tone === 'block' ? '✕' : '⚠'} <b>Is it day/month or month/day?</b> No value of {cols.map((c) => <span key={c} className="mono">{c} </span>)}has a day above 12, so the order cannot be told from the data. Nothing guesses it: choose the format and it is read the same way in every file.
    {onFormat && cols.map((c) => {
      const s = sample(c)
      return <div key={c} className="row" style={{ gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
        <span className="mono">{c}</span>
        <select className="select" value={pick[c] ?? ''} onChange={(e) => setPick({ ...pick, [c]: e.target.value })} data-testid={`up-date-format-${c}`}>
          <option value="" disabled>choose the format…</option>{FORMATS.map((f) => <option key={f.f} value={f.f}>{f.name}</option>)}</select>
        {s && <span className="helper mono">{FORMATS.map((f) => `${f.f.startsWith('%d') ? 'day first' : 'month first'}: ${s} → ${readAs(s, f.f) ?? '?'}`).join(' · ')}</span>}
      </div>
    })}
    {onFormat && <button className="btn sm primary" style={{ marginTop: 4 }} disabled={busy || cols.some((c) => !pick[c])} onClick={() => onFormat(table, pick)} data-testid="up-date-apply">Check again with {cols.length === 1 ? 'this format' : 'these formats'}</button>}
  </div>
}

/** F-X3a M1: what the commit must acknowledge by code: a key that stops being one, a relation the version would lose. */
export function IntegrityAcks({ items, acked, setAcked }: { items: Issue[]; acked: Set<string>; setAcked: (s: Set<string>) => void }) {
  const codes = [...new Set(items.map((x) => x.code))]
  return (
    <div className="data-warn up-integrity" data-testid="up-integrity">
      <b>This version changes what the parent promised. Read it, then acknowledge it (recorded in the version's manifest):</b>
      {items.map((x, i) => <div key={i} className="up-ackline" data-testid={`up-integrity-${x.code}`}>
        ⚠ {x.code === 'key_invalidated'
          ? <><b>{x.table}</b>: its {x.kind} key <span className="mono">{colsOf(x).join(' + ')}</span> stops being a key{x.why === 'not_unique' ? <> ({n(x.n_duplicates)} repeated, {n(x.n_nulls)} missing)</> : x.why === 'column_gone' ? ' (a key column is gone)' : ''}. The key is kept but marked invalid, and later deltas must name their key.</>
          : <><b>relation {x.relation}</b> ({x.from?.table}.{x.from?.columns.join('+')} → {x.to?.table}.{x.to?.columns.join('+')}) is dropped{x.why === 'relation_to_not_unique' ? <>: {x.to?.table}.{x.to?.columns.join('+')} would repeat ({n(x.to_duplicates)} repeated values)</> : x.why === 'column_gone' ? ': a column is gone' : ''}.</>}
      </div>)}
      {codes.map((c) => <label key={c} className="row" style={{ gap: 6 }}>
        <input type="checkbox" checked={acked.has(c)} onChange={(e) => { const s = new Set(acked); if (e.target.checked) s.add(c); else s.delete(c); setAcked(s) }} data-testid={`up-ack-${c}`} />
        <span>I acknowledge <span className="mono">{c}</span>{c === 'key_invalidated' ? ': the key stops being a key' : ': the relation is dropped from this version'}</span></label>)}
    </div>
  )
}

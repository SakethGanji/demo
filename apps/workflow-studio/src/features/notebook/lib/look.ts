/**
 * Pure look helpers for the notebook: deterministic person gradients, status-value tones, table nouns and money
 * totals. No data is invented: every helper reads only what the cell / row already holds.
 */

// ---- avatars ---------------------------------------------------------------------------------------------------

const GRADIENTS = [
  'linear-gradient(135deg, #db2777, #d97706)',
  'linear-gradient(135deg, #0f9d8a, #2f7fd6)',
  'linear-gradient(135deg, #0e7490, #6d28d9)',
  'linear-gradient(135deg, #7c3aed, #db2777)',
  'linear-gradient(135deg, #059669, #65a30d)',
  'linear-gradient(135deg, #2563eb, #0891b2)',
  'linear-gradient(135deg, #ea580c, #e11d48)',
]

function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return h >>> 0
}

/** The same name always gets the same gradient; Studio / agents get the brand gradient (null). */
export function avatarGradient(name: string): string | null {
  const n = name.trim().toLowerCase()
  if (!n || n === 'studio' || n.startsWith('agent') || n.endsWith('-agent')) return null
  return GRADIENTS[hash(n) % GRADIENTS.length]
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s._@-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

// ---- status values -----------------------------------------------------------------------------------------------

export type ValueTone = 'ok' | 'wait' | 'bad' | 'neutral'

const BAD = /^(hold|held|on[ _-]?hold|repair|failed|failure|fail|missing|error|errored|rejected|blocked|stuck|mismatch|overdue|expired|declined|cancel+ed|returned|breach|flagged|hit)$/
const WAIT = /^(await\w*|pending|queued|waiting|running|processing|in[ _-]?review|review|open|new|draft|scheduled|in[ _-]?progress)$/
const OK = /^(settled|done|ok|clear|cleared|success|succeeded|passed|pass|approved|released|sent|paid|complete|completed|consistent|matched|match|resolved|active|closed|delivered)$/

export function statusTone(v: unknown): ValueTone {
  if (typeof v !== 'string') return 'neutral'
  const s = v.trim().toLowerCase()
  if (!s) return 'neutral'
  for (const t of [s, s.split(/[\s_-]+/)[0]]) {
    if (BAD.test(t)) return 'bad'
    if (WAIT.test(t)) return 'wait'
    if (OK.test(t)) return 'ok'
  }
  return 'neutral'
}

const STATUS_NAME = /(^|_)(status|state|result|outcome|stage|decision|verdict|check)$/i

/** A column reads as a status column by name, or by a handful of short word values that repeat. */
export function isStatusColumn(name: string, rows: Record<string, unknown>[]): boolean {
  const vals = rows.map((r) => r[name]).filter((v) => v !== null && v !== undefined)
  if (vals.length === 0 || !vals.every((v) => typeof v === 'string' && v.length <= 24)) return false
  if (STATUS_NAME.test(name)) return true
  if (rows.length < 3) return false
  const distinct = new Set(vals as string[])
  if (distinct.size > 5 || distinct.size >= rows.length) return false
  // only words, and at least one value we recognise as a status
  return [...distinct].every((v) => /^[a-z][a-z _-]*$/i.test(v)) && [...distinct].some((v) => statusTone(v) !== 'neutral')
}

// ---- nouns -----------------------------------------------------------------------------------------------------

function singular(w: string): string {
  if (/ies$/.test(w)) return w.slice(0, -3) + 'y'
  if (/(ss|us)$/.test(w)) return w
  if (/(ch|sh|x|ses)es$/.test(w)) return w.slice(0, -2)
  if (/s$/.test(w)) return w.slice(0, -1)
  return w
}

/** The real noun for a table's rows: `FROM bank.wires` → wire / wires. Falls back to row / rows. */
export function rowNoun(sql: string | undefined): { one: string; many: string } {
  const m = sql ? /\bfrom\s+(?:"?[\w]+"?\.)?"?([a-z][a-z_]*)"?/i.exec(sql) : null
  const t = m?.[1]?.toLowerCase()
  if (t && /s$/.test(t) && t.length > 3 && !/(ss|us)$/.test(t)) {
    const many = t.replace(/_/g, ' ')
    return { one: singular(many), many }
  }
  return { one: 'row', many: 'rows' }
}

// ---- money -----------------------------------------------------------------------------------------------------

const MONEY = /(^|_)(amount|amt|total|balance|value|price|cost|fee|fees|sum)$/i

/** A total for an amount-like column, only when every row is here (never a total of a partial page). */
export function moneyTotal(columns: string[], rows: Record<string, unknown>[], rowCount: number): string | null {
  if (rows.length === 0 || rows.length !== rowCount) return null
  const col = columns.find((c) => MONEY.test(c) && rows.every((r) => r[c] === null || r[c] === undefined || typeof r[c] === 'number'))
  if (!col) return null
  const total = rows.reduce((a, r) => a + (typeof r[col] === 'number' ? (r[col] as number) : 0), 0)
  const curCol = columns.find((c) => /(^|_)(currency|ccy)$/i.test(c))
  const curs = curCol ? new Set(rows.map((r) => r[curCol])) : null
  const cur = curs && curs.size === 1 ? [...curs][0] : null
  if (typeof cur === 'string' && /^[A-Z]{3}$/.test(cur)) {
    try {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: cur, maximumFractionDigits: 2 }).format(total)
    } catch {
      /* unknown code: plain number */
    }
  }
  return `total ${total.toLocaleString('en-US', { minimumFractionDigits: Number.isInteger(total) ? 0 : 2, maximumFractionDigits: 2 })}`
}

/** Production-like environment names get the red treatment (`production`, `prod`, `prod-eu`). */
export const isProdEnv = (env: string | null | undefined) => !!env && /^prod(uction)?\b/i.test(env.trim())

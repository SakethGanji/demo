export const sup = (n: number) => String(n).split('').map((c) => '⁰¹²³⁴⁵⁶⁷⁸⁹'['0123456789'.indexOf(c)] ?? (c === '-' ? '⁻' : c)).join('')

type Num = number | null | undefined
/** `v.toFixed(d)`, or an em dash for null / non-finite. */
export const fix = (v: Num, d = 3) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d))
/** A fraction as a percentage with `d` decimals, or an em dash for null. */
export const pct = (v: Num, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
/** A fraction as a percentage with one decimal, trailing zero trimmed. */
export const pctTrim = (f: number) => `${+(f * 100).toFixed(1)}%`
/** 4 significant digits; thousands separators from 1000 up. */
export const sig4 = (v: number) => (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : String(Number(v.toPrecision(4))))

/** U4: the data platform's shared formatters (they were copied into a dozen files). */
/** A whole count with thousands separators, or an em dash. */
export const int = (v: Num) => (v == null || !Number.isFinite(v) ? '—' : v.toLocaleString())
/** A date and time, medium ("22 Nov 2026, 14:05"), or an em dash. */
export const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—')
/** A short date and time ("22 Nov, 14:05"), for table cells. */
export const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—')
/** Bytes as B / KB / MB / GB. */
export const bytes = (b: Num) => (b == null ? '—' : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : b < 1073741824 ? `${(b / 1048576).toFixed(2)} MB` : `${(b / 1073741824).toFixed(2)} GB`)
/** "3 tables", "1 table". */
export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`

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

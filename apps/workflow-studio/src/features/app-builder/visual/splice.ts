/**
 * Minimal text edits on source: every operation returns byte-offset splices computed from @babel/parser positions, so a
 * visual edit changes exactly the characters it means to (no printer, no reformatting). New items copy the indentation
 * and separator style of their siblings. Callers re-parse the result (`applyChecked`) and refuse an edit that breaks it.
 */
import type * as t from '@babel/types'
import { objectProp, tryParse } from './source'

export interface TextEdit {
  start: number
  end: number
  text: string
}

export type Literal = string | number | boolean | null | Literal[] | { [k: string]: Literal }

/** Apply non-overlapping edits (any order). */
export function applyEdits(src: string, edits: TextEdit[]): string {
  const sorted = [...edits].sort((a, b) => b.start - a.start)
  let out = src
  let floor = Infinity
  for (const e of sorted) {
    if (e.end > floor) throw new Error('overlapping edits')
    out = out.slice(0, e.start) + e.text + out.slice(e.end)
    floor = e.start
  }
  return out
}

/** Apply and re-parse; null when the result no longer parses. */
export function applyChecked(src: string, edits: TextEdit[]): string | null {
  const out = applyEdits(src, edits)
  return tryParse(out) ? out : null
}

// ── Text helpers ────────────────────────────────────────────────────────────

export function lineStart(src: string, pos: number): number {
  return src.lastIndexOf('\n', pos - 1) + 1
}

export function lineEnd(src: string, pos: number): number {
  const i = src.indexOf('\n', pos)
  return i < 0 ? src.length : i
}

export function indentAt(src: string, pos: number): string {
  const ls = lineStart(src, pos)
  return /^[ \t]*/.exec(src.slice(ls))![0]
}

/** Only whitespace before `start` on its line. */
function startsLine(src: string, start: number): boolean {
  return src.slice(lineStart(src, start), start).trim() === ''
}

/** Only whitespace (and at most a comma) after `end` on its line. */
function endsLine(src: string, end: number): boolean {
  return /^\s*,?\s*$/.test(src.slice(end, lineEnd(src, end)))
}

/** The quote the file uses for strings (its first import), single by default. */
export function quoteStyle(src: string): "'" | '"' {
  const m = /^\s*import[^'"\n]*(['"])/m.exec(src)
  return m?.[1] === '"' ? '"' : "'"
}

export function quote(value: string, q: "'" | '"'): string {
  const body = value.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/\r/g, '\\r')
  return q === '"' ? `"${body.replace(/"/g, '\\"')}"` : `'${body.replace(/'/g, "\\'")}'`
}

const IDENT = /^[A-Za-z_$][\w$]*$/

/** A JS literal in the file's style (single-line). */
export function printLiteral(v: Literal, q: "'" | '"'): string {
  if (v === null) return 'null'
  if (typeof v === 'string') return quote(v, q)
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return `[${v.map((x) => printLiteral(x, q)).join(', ')}]`
  const parts = Object.entries(v).map(([k, x]) => `${IDENT.test(k) ? k : quote(k, q)}: ${printLiteral(x, q)}`)
  return parts.length ? `{ ${parts.join(', ')} }` : '{}'
}

/** A JSX attribute value: `"text"` when that is exact, otherwise `{literal}`. */
export function printAttrValue(v: Literal, q: "'" | '"'): string {
  if (typeof v === 'string' && !/["\n\\]/.test(v) && !/[{}]/.test(v)) return `"${v}"`
  return `{${printLiteral(v, q)}}`
}

// ── JSX attributes ──────────────────────────────────────────────────────────

function attrName(a: t.JSXAttribute): string {
  return a.name.type === 'JSXIdentifier' ? a.name.name : `${a.name.namespace.name}:${a.name.name.name}`
}

export function findAttr(el: t.JSXElement, name: string): t.JSXAttribute | null {
  for (const a of el.openingElement.attributes) if (a.type === 'JSXAttribute' && attrName(a) === name) return a
  return null
}

/** Set `name={value}` (replace the value, or add the attribute after the last one, on its own line when they are). */
export function setAttr(src: string, el: t.JSXElement, name: string, valueText: string | null): TextEdit {
  const full = valueText === null ? name : `${name}=${valueText}`
  const a = findAttr(el, name)
  if (a) return a.value && valueText !== null ? { start: a.value.start!, end: a.value.end!, text: valueText } : { start: a.start!, end: a.end!, text: full }
  const open = el.openingElement
  const attrs = open.attributes
  if (!attrs.length) return { start: open.name.end!, end: open.name.end!, text: ` ${full}` }
  const last = attrs[attrs.length - 1]
  if (startsLine(src, last.start!)) return { start: last.end!, end: last.end!, text: `\n${indentAt(src, last.start!)}${full}` }
  return { start: last.end!, end: last.end!, text: ` ${full}` }
}

/** Remove an attribute: its whole line when it stands alone, else the attribute and the space before it. */
export function removeAttr(src: string, el: t.JSXElement, name: string): TextEdit | null {
  const a = findAttr(el, name)
  if (!a) return null
  if (startsLine(src, a.start!) && /^\s*$/.test(src.slice(a.end!, lineEnd(src, a.end!)))) {
    return { start: lineStart(src, a.start!), end: Math.min(src.length, lineEnd(src, a.end!) + 1), text: '' }
  }
  let s = a.start!
  while (s > 0 && (src[s - 1] === ' ' || src[s - 1] === '\t')) s--
  return { start: s, end: a.end!, text: '' }
}

/** Replace a JSX text child, keeping the whitespace around it. Text with braces or angle brackets becomes `{'…'}`. */
export function setText(src: string, node: t.JSXText, value: string, q: "'" | '"'): TextEdit {
  const raw = src.slice(node.start!, node.end!)
  const lead = /^\s*/.exec(raw)![0].length
  const trail = /\s*$/.exec(raw)![0].length
  const text = /[{}<>]/.test(value) || value !== value.trim() || value.includes('\n') ? `{${quote(value, q)}}` : value
  return { start: node.start! + lead, end: node.end! - trail, text }
}

export function replaceNode(node: t.Node, text: string): TextEdit {
  return { start: node.start!, end: node.end!, text }
}

// ── Arrays and objects (lists of items) ─────────────────────────────────────

type Item = t.Node

function multiline(src: string, items: Item[]): boolean {
  return items.length > 0 && startsLine(src, items[0].start!)
}

/** Remove item `i`: its line(s) when items sit one per line, else the item and one separator. */
function removeItem(src: string, items: Item[], i: number, inner: { start: number; end: number }): TextEdit {
  const it = items[i]
  if (startsLine(src, it.start!) && endsLine(src, it.end!)) {
    return { start: lineStart(src, it.start!), end: Math.min(src.length, lineEnd(src, it.end!) + 1), text: '' }
  }
  if (items.length === 1) return { start: inner.start, end: inner.end, text: '' }
  if (i < items.length - 1) return { start: it.start!, end: items[i + 1].start!, text: '' }
  return { start: items[i - 1].end!, end: it.end!, text: '' }
}

/** Insert `text` as item `i` (append when i >= length), matching the siblings' layout. */
function insertItem(src: string, items: Item[], i: number, text: string, inner: { start: number; end: number }, pad = ''): TextEdit {
  if (!items.length) return { start: inner.start, end: inner.end, text: `${pad}${text}${pad}` }
  if (multiline(src, items)) {
    if (i < items.length) {
      const at = items[i].start!
      return { start: lineStart(src, at), end: lineStart(src, at), text: `${indentAt(src, at)}${text},\n` }
    }
    const last = items[items.length - 1]
    const indent = indentAt(src, last.start!)
    const after = /^\s*,/.exec(src.slice(last.end!))
    if (after) {
      const comma = last.end! + after[0].length
      return { start: comma, end: comma, text: `\n${indent}${text},` }
    }
    return { start: last.end!, end: last.end!, text: `,\n${indent}${text}` }
  }
  if (i < items.length) return { start: items[i].start!, end: items[i].start!, text: `${text}, ` }
  const last = items[items.length - 1]
  return { start: last.end!, end: last.end!, text: `, ${text}` }
}

function elements(arr: t.ArrayExpression): Item[] {
  return arr.elements.filter((e): e is NonNullable<typeof e> => e !== null)
}

function arrayInner(arr: t.ArrayExpression) {
  return { start: arr.start! + 1, end: arr.end! - 1 }
}

export function arrayRemove(src: string, arr: t.ArrayExpression, i: number): TextEdit {
  return removeItem(src, elements(arr), i, arrayInner(arr))
}

export function arrayInsert(src: string, arr: t.ArrayExpression, i: number, text: string): TextEdit {
  return insertItem(src, elements(arr), i, text, arrayInner(arr))
}

/** Move item `from` to position `to`: each slot whose item changes gets the moved item's text; separators stay put. */
export function arrayMove(src: string, arr: t.ArrayExpression, from: number, to: number): TextEdit[] {
  const items = elements(arr)
  const order = items.map((_, i) => i)
  const [x] = order.splice(from, 1)
  order.splice(to, 0, x)
  const edits: TextEdit[] = []
  order.forEach((srcIdx, slot) => {
    if (srcIdx !== slot) edits.push({ start: items[slot].start!, end: items[slot].end!, text: src.slice(items[srcIdx].start!, items[srcIdx].end!) })
  })
  return edits
}

/**
 * Move node `from` to position `to` among sibling nodes (JSX children, list items): each slot whose occupant changes takes
 * the moved text, so the whitespace and separators between slots stay exactly as written. Returns the edits and where the
 * moved node starts in the result.
 */
export function moveNodes(src: string, items: t.Node[], from: number, to: number): { edits: TextEdit[]; newStart: number } {
  const order = items.map((_, i) => i)
  const [x] = order.splice(from, 1)
  order.splice(to, 0, x)
  const edits: TextEdit[] = []
  order.forEach((srcIdx, slot) => {
    if (srcIdx !== slot) edits.push({ start: items[slot].start!, end: items[slot].end!, text: src.slice(items[srcIdx].start!, items[srcIdx].end!) })
  })
  const at = items[to].start!
  const newStart = at + edits.filter((e) => e.end <= at).reduce((d, e) => d + e.text.length - (e.end - e.start), 0)
  return { edits, newStart }
}

function objectInner(obj: t.ObjectExpression) {
  return { start: obj.start! + 1, end: obj.end! - 1 }
}

/** Set `key: value` on an object literal (replace the value, or add the key after the last one). */
export function setObjectKey(src: string, obj: t.ObjectExpression, key: string, valueText: string, q: "'" | '"'): TextEdit {
  const p = objectProp(obj, key)
  if (p) {
    if (p.shorthand) return { start: p.start!, end: p.end!, text: `${key}: ${valueText}` }
    return { start: p.value.start!, end: p.value.end!, text: valueText }
  }
  const k = IDENT.test(key) ? key : quote(key, q)
  return insertItem(src, obj.properties, obj.properties.length, `${k}: ${valueText}`, objectInner(obj), ' ')
}

export function removeObjectKey(src: string, obj: t.ObjectExpression, key: string): TextEdit | null {
  const p = objectProp(obj, key)
  if (!p) return null
  const i = obj.properties.indexOf(p)
  const e = removeItem(src, obj.properties, i, objectInner(obj))
  // `{ a: 1 }` → `{}` rather than `{  }`
  if (obj.properties.length === 1 && !multiline(src, obj.properties)) return { start: obj.start!, end: obj.end!, text: '{}' }
  return e
}

/** Count changed lines (for "minimal diff" checks): lines removed + lines added, by a simple LCS on lines. */
export function diffSize(a: string, b: string): { added: number; removed: number } {
  const x = a.split('\n')
  const y = b.split('\n')
  let s = 0
  while (s < x.length && s < y.length && x[s] === y[s]) s++
  let ex = x.length
  let ey = y.length
  while (ex > s && ey > s && x[ex - 1] === y[ey - 1]) {
    ex--
    ey--
  }
  const xs = x.slice(s, ex)
  const ys = y.slice(s, ey)
  // LCS on the (small) differing middle
  const dp: number[][] = Array.from({ length: xs.length + 1 }, () => new Array<number>(ys.length + 1).fill(0))
  for (let i = xs.length - 1; i >= 0; i--) for (let j = ys.length - 1; j >= 0; j--) dp[i][j] = xs[i] === ys[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const common = dp[0][0]
  return { added: ys.length - common, removed: xs.length - common }
}

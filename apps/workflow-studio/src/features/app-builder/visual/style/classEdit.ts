/**
 * Class edits as minimal splices: read an element's `className`, change its class list with tailwind-merge conflict
 * semantics (the conflicting utility is replaced in place, everything else keeps its order), and splice only the string
 * literal that holds the classes. Supported shapes: `className="…"`, `{'…'}`, `` {`…`} `` without expressions, and
 * `{cn('base', cond && 'x')}` (the first string-literal argument). Ternaries, template literals with expressions and
 * anything computed are refused with a reason — never a silent no-op. A missing `className` is added.
 */
import type * as t from '@babel/types'
import { twMerge } from 'tailwind-merge'
import { applyChecked, findAttr, removeAttr, setAttr, type TextEdit } from '../splice'
import { ALLOWED } from './tokens'

export const DYNAMIC = 'dynamic classes — edit with AI / code'

/** Class-joining helpers whose first string-literal argument is the static base. */
const JOINERS = new Set(['cn', 'cx', 'clsx', 'classNames', 'classnames', 'twMerge', 'twJoin'])

export type ClassSite =
  | { kind: 'none' }
  /** a plain attribute string (`className="…"`): removable when it empties */
  | { kind: 'attr'; start: number; end: number; value: string }
  /** a string inside an expression (`{'…'}`, a static template, or a `cn()` base argument) */
  | { kind: 'expr'; start: number; end: number; value: string; via?: string }

export type ClassRead = { ok: true; site: ClassSite; classes: string[] } | { ok: false; reason: string }

export function splitClasses(s: string): string[] {
  return s.split(/\s+/).filter(Boolean)
}

function stringSite(n: t.Node, via?: string): ClassSite | null {
  if (n.type === 'StringLiteral') return { kind: 'expr', start: n.start! + 1, end: n.end! - 1, value: n.value, ...(via ? { via } : {}) }
  if (n.type === 'TemplateLiteral' && n.expressions.length === 0) {
    const q = n.quasis[0]
    return { kind: 'expr', start: q.start!, end: q.end!, value: q.value.cooked ?? q.value.raw, ...(via ? { via } : {}) }
  }
  return null
}

function calleeName(c: t.Expression | t.V8IntrinsicIdentifier): string | null {
  if (c.type === 'Identifier') return c.name
  if (c.type === 'MemberExpression' && c.property.type === 'Identifier') return c.property.name
  return null
}

/** Where the element's classes live, or why they can't be edited here. */
export function readClasses(src: string, el: t.JSXElement): ClassRead {
  const a = findAttr(el, 'className')
  if (!a) return { ok: true, site: { kind: 'none' }, classes: [] }
  const v = a.value
  if (!v) return { ok: false, reason: DYNAMIC }
  if (v.type === 'StringLiteral') {
    // the raw attribute text between the quotes (JSX strings have no escapes)
    return { ok: true, site: { kind: 'attr', start: v.start! + 1, end: v.end! - 1, value: v.value }, classes: splitClasses(v.value) }
  }
  if (v.type !== 'JSXExpressionContainer' || v.expression.type === 'JSXEmptyExpression') return { ok: false, reason: DYNAMIC }
  let e: t.Node = v.expression
  while (e.type === 'ParenthesizedExpression' || e.type === 'TSAsExpression') e = e.expression
  const direct = stringSite(e)
  if (direct) return { ok: true, site: direct, classes: splitClasses((direct as { value: string }).value) }
  if (e.type === 'CallExpression') {
    const name = calleeName(e.callee)
    if (name && JOINERS.has(name)) {
      for (const arg of e.arguments) {
        const s = stringSite(arg, name)
        if (s && s.kind === 'expr') {
          if (/['"`\\]/.test(src.slice(s.start, s.end))) return { ok: false, reason: DYNAMIC }
          return { ok: true, site: s, classes: splitClasses(s.value) }
        }
      }
      return { ok: false, reason: `${DYNAMIC} (${name}() has no static base string)` }
    }
  }
  const text = src.slice(e.start!, e.end!)
  return { ok: false, reason: `${DYNAMIC} (classes come from ${text.length > 40 ? `${text.slice(0, 40)}…` : text})` }
}

// ── Class-list operations (tailwind-merge semantics, order preserved) ────────

/** `later` overrides `earlier` under tailwind-merge (same conflict group and same variants), or they are the same class. */
export function conflicts(earlier: string, later: string): boolean {
  if (earlier === later) return true
  return !twMerge(`${earlier} ${later}`).split(' ').includes(earlier)
}

/** Put `cls` into the list: it takes the place of the first class it conflicts with (others in its group go); else appended. */
export function setClass(list: string[], cls: string): string[] {
  const out: string[] = []
  let placed = false
  for (const c of list) {
    if (conflicts(c, cls)) {
      if (!placed) out.push(cls)
      placed = true
    } else out.push(c)
  }
  if (!placed) out.push(cls)
  return out
}

/** Remove every class a member of the group would override (the group is given by its option classes). */
export function clearGroup(list: string[], options: string[]): string[] {
  return list.filter((c) => !options.some((o) => conflicts(c, o)))
}

/** The class of `options` the list currently uses (the last wins), or another class in the same group, or null. */
export function currentOf(list: string[], options: string[]): { cls: string; known: boolean } | null {
  for (let i = list.length - 1; i >= 0; i--) {
    const c = list[i]
    if (options.includes(c)) return { cls: c, known: true }
    if (!c.includes(':') && options.some((o) => conflicts(c, o))) return { cls: c, known: false }
  }
  return null
}

// ── Breakpoints (responsive prefixes) ────────────────────────────────────────

/**
 * Tailwind's min-width breakpoints, smallest first. The panel WRITES only `base` (no prefix), `md:` and `lg:` (phone,
 * tablet, desktop — see breakpoints.ts); it READS all of them so an app's `sm:` / `xl:` classes count in what's inherited.
 */
export const ALL_BREAKPOINTS = ['base', 'sm', 'md', 'lg', 'xl', '2xl'] as const
export type AnyBreakpoint = (typeof ALL_BREAKPOINTS)[number]
export type Breakpoint = 'base' | 'md' | 'lg'

const BP_SET = new Set<string>(ALL_BREAKPOINTS)

/** `p-4` at `md` → `md:p-4`; at base it is unprefixed. */
export function atBreakpoint(cls: string, bp: AnyBreakpoint): string {
  return bp === 'base' ? cls : `${bp}:${cls}`
}

/** A class's variant prefixes and utility, splitting on top-level colons (not inside `[…]` arbitrary values). */
export function splitVariants(cls: string): { variants: string[]; util: string } {
  const parts: string[] = []
  let depth = 0
  let from = 0
  for (let i = 0; i < cls.length; i++) {
    const ch = cls[i]
    if (ch === '[' || ch === '(') depth++
    else if (ch === ']' || ch === ')') depth = Math.max(0, depth - 1)
    else if (ch === ':' && depth === 0) {
      parts.push(cls.slice(from, i))
      from = i + 1
    }
  }
  return { variants: parts, util: cls.slice(from) }
}

/**
 * The breakpoint a class applies from, when a breakpoint is its ONLY variant (or it has none): `p-2` → base, `md:p-4` →
 * md. Null for any other variant (`hover:`, `dark:`, `md:hover:`): those classes are never read or touched by the panel.
 */
export function breakpointOf(cls: string): { bp: AnyBreakpoint; util: string } | null {
  const { variants, util } = splitVariants(cls)
  if (variants.length === 0) return { bp: 'base', util }
  if (variants.length === 1 && BP_SET.has(variants[0])) return { bp: variants[0] as AnyBreakpoint, util }
  return null
}

/** The utilities (prefix stripped) the list applies at exactly breakpoint `bp`. */
export function classesAt(list: string[], bp: AnyBreakpoint): string[] {
  return list.flatMap((c) => {
    const b = breakpointOf(c)
    return b && b.bp === bp ? [b.util] : []
  })
}

/** What the list sets for a group at exactly `bp` (see `currentOf`). */
export function currentAt(list: string[], options: string[], bp: AnyBreakpoint): { cls: string; known: boolean } | null {
  return currentOf(classesAt(list, bp), options)
}

/** The value of a group that is in effect at `bp`: its own, else the nearest smaller breakpoint's (mobile-first cascade). */
export function effectiveAt(list: string[], options: string[], bp: AnyBreakpoint): { cls: string; known: boolean; from: AnyBreakpoint } | null {
  for (let i = ALL_BREAKPOINTS.indexOf(bp); i >= 0; i--) {
    const cur = currentAt(list, options, ALL_BREAKPOINTS[i])
    if (cur) return { ...cur, from: ALL_BREAKPOINTS[i] }
  }
  return null
}

/** Whether the group already differs by screen size (any prefixed class of it). */
export function isResponsive(list: string[], options: string[]): boolean {
  return ALL_BREAKPOINTS.some((bp) => bp !== 'base' && currentAt(list, options, bp) !== null)
}

export type ClassChange = { set: string; bp?: Breakpoint } | { clear: string[]; bp?: Breakpoint }

/**
 * Apply a change to a class list; a `set` outside the token tables is refused (the token lock). With `bp`, the class is
 * written (or the group cleared) at that breakpoint only: `md:p-4` replaces `md:p-2` and leaves `p-2`, `lg:p-6` and
 * `hover:p-1` alone (tailwind-merge conflicts only within the same variants).
 */
export function changeList(list: string[], change: ClassChange): string[] | string {
  const bp = change.bp ?? 'base'
  if ('set' in change) {
    if (!ALLOWED.has(change.set)) return `${change.set} is not a theme token value`
    return setClass(list, atBreakpoint(change.set, bp))
  }
  return clearGroup(list, change.clear.map((o) => atBreakpoint(o, bp)))
}

// ── The splice ───────────────────────────────────────────────────────────────

export type ClassEditResult = { ok: true; edit: TextEdit; after: string; classes: string[] } | { ok: false; error: string }

/** Change the element's classes; returns the one splice (and the re-parsed result) or why not. */
export function editClasses(src: string, el: t.JSXElement, change: ClassChange): ClassEditResult {
  const r = readClasses(src, el)
  if (!r.ok) return { ok: false, error: r.reason }
  const next = changeList(r.classes, change)
  if (typeof next === 'string') return { ok: false, error: next }
  if (next.join(' ') === r.classes.join(' ')) return { ok: false, error: 'Nothing to change' }
  const text = next.join(' ')
  let edit: TextEdit | null
  const site = r.site
  if (site.kind === 'none') edit = setAttr(src, el, 'className', `"${text}"`)
  else if (site.kind === 'attr' && !text) edit = removeAttr(src, el, 'className')
  else edit = { start: site.start, end: site.end, text }
  if (!edit) return { ok: false, error: 'Nothing to change' }
  const after = applyChecked(src, [edit])
  if (after === null) return { ok: false, error: 'That edit would break the file; use AI or code' }
  return { ok: true, edit, after, classes: next }
}
